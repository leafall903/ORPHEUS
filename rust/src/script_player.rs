//! Prepared owner statescript for a live body in build 104319.

mod charge;
mod lifecycle;
mod owner_walk;
mod world;

#[cfg(test)]
mod tests;

use std::{collections::BTreeSet, io, sync::Arc};

#[cfg(test)]
use serde_json::Value as Json;

pub(crate) use self::lifecycle::PlayerBodyRuntime;
use self::world::BodyWorld;
pub(crate) use self::world::{DeadeyeScene, DeadeyeTarget, ViewSubject};
#[cfg(test)]
use crate::game_content::PRACTICE_RANGE_MODE;
use crate::{
    accounts::parse_guid,
    game_combat::RoomSpatialFrame,
    game_content::is_junkenstein_mode,
    game_heroes::Hero,
    game_instance::{RETAIL_QUANTA, TeamMemberCounts},
    game_pools::{BODY_GRAPH, FALLBACK_POOLS, HealthPools},
    game_projectiles::ProjectileLeases,
    game_replication::{OwnerBodyRuntime, OwnerRoomActivity},
    script_codecs::CodecTable,
    script_graph::GraphStore,
    script_init::{Component, InitFault, MAX_INIT_WORK, Root, TargetTagEffects},
    script_snapshot_wire::{SnapshotError, View},
    script_wire::Variables,
    vitals::Vitals,
    world::Movement,
};
#[cfg(test)]
use crate::{game_heroes::SOLDIER, script_init::World, script_runtime::FRAME_MS};

#[derive(Clone, Copy)]
pub(crate) struct OwnerBodySpawn {
    pub controller: u32,
    pub entity: u32,
    pub body_filter: u64,
    pub slot: u8,
    pub frame: u32,
    pub pose: Movement,
    pub eye_position: Option<[f64; 3]>,
    pub quanta: u32,
    /// The selected skin asset. Zero selects the stock model.
    pub skin: u64,
}

#[derive(Clone, Default)]
pub(crate) struct OwnerBodySetup {
    pub team_member_counts: Option<TeamMemberCounts>,
    pub spatial_frame: Option<Arc<RoomSpatialFrame>>,
    pub room_activity: Option<OwnerRoomActivity>,
    /// The body's mover values: the hook promotion measures the lip with
    /// these and no other numbers.
    pub mover_settings: crate::mover_settings::MoverSettings,
}

pub(crate) struct PlayerBodyFactory {
    graphs: Arc<GraphStore>,
    codecs: Arc<CodecTable>,
    mode: u64,
    hero: u64,
    body: u64,
    roots: Vec<Root>,
    variables: Variables,
    pools: Option<HealthPools>,
    target_tags: Option<BTreeSet<u64>>,
    controller_on_fire: bool,
    /// The reference component policy (`Component.strict`); the owner
    /// stream may be prepared under the normal policy (see
    /// `script_expr::Context::strict`).
    strict: bool,
    charge_route: charge::ChargeRoute,
    anim_timing: Option<Arc<crate::anim_timing::AnimationTiming>>,
}

#[derive(Debug, thiserror::Error)]
pub(crate) enum OwnerBodyPreparationError {
    #[error("the hero body definition is absent from the graph bundle")]
    MissingBody,
    #[error(transparent)]
    RootLayout(InitFault),
    #[error("defender body graph instance ID exhausted")]
    ModeRootId,
    #[error("Junkenstein defender body graph is absent")]
    MissingModeRoot,
    #[error("authored charge graph {0:04X} must remain a single private root after room setup")]
    AuthoredChargeRoot(u32),
    #[error("compatibility charge graph {0:04X} must remain a single public root after room setup")]
    CompatibilityChargeRoot(u32),
    #[error(transparent)]
    Start(#[from] OwnerBodyStartupError),
    #[error(transparent)]
    Projection(SnapshotError),
    #[error(transparent)]
    Encoding(SnapshotError),
}

#[derive(Debug, thiserror::Error)]
pub(crate) enum OwnerBodyStartupError {
    #[error("the owner VM requires the 16 ms retail clock")]
    Clock,
    #[error(transparent)]
    VmInitialize(InitFault),
    #[error(transparent)]
    FirstFrame(InitFault),
    #[error(transparent)]
    MovementModifiers(InitFault),
    #[error(transparent)]
    ProjectileLeases(io::Error),
    #[error(transparent)]
    ChargeAdmission(io::Error),
}

impl PlayerBodyFactory {
    pub(crate) fn from_body(
        graphs: Arc<GraphStore>,
        codecs: Arc<CodecTable>,
        mode: u64,
        hero: &Hero,
        pools: Option<HealthPools>,
        controller_on_fire: bool,
    ) -> Result<Self, OwnerBodyPreparationError> {
        let body = graphs
            .bodies()
            .get(&hero.body)
            .filter(|body| body.hero.as_deref().and_then(parse_guid) == Some(hero.guid))
            .or_else(|| {
                graphs.entity_body(hero.body).filter(|body| {
                    body.hero.is_none()
                        || body.hero.as_deref().and_then(parse_guid) == Some(hero.guid)
                })
            })
            .ok_or(OwnerBodyPreparationError::MissingBody)?;
        let mut roots = crate::script_init::player_roots(body, &graphs)
            .map_err(OwnerBodyPreparationError::RootLayout)?;
        if is_junkenstein_mode(mode) {
            const DEFENDER_BODY_GRAPH: u32 = 0x0ca5;
            let public_roots = roots
                .iter()
                .position(Root::is_server_only)
                .unwrap_or(roots.len());
            let id = roots[..public_roots]
                .last()
                .map_or(0, |root| root.id)
                .checked_add(1)
                .ok_or(OwnerBodyPreparationError::ModeRootId)?;
            if graphs.graph(DEFENDER_BODY_GRAPH).is_none() {
                return Err(OwnerBodyPreparationError::MissingModeRoot);
            }
            roots.insert(public_roots, Root::new(id, DEFENDER_BODY_GRAPH));
        }
        if let Some(graph) = body.authored_charge_graph {
            let mut mounted = roots.iter().filter(|root| root.graph == graph);
            if !mounted.next().is_some_and(Root::is_server_only) || mounted.next().is_some() {
                return Err(OwnerBodyPreparationError::AuthoredChargeRoot(graph));
            }
        }
        let pools = if body.hero.is_none() || pools.is_none() {
            crate::game_pools::authored_pools(&graphs, body)
                .ok()
                .flatten()
                .or(pools)
        } else {
            pools
        };
        let health = pools.unwrap_or(FALLBACK_POOLS).total();
        let variables = crate::script_init::player_entity_variables(body, health, health);
        let target_tags = body.target_tags.clone().or_else(|| {
            if body.hero.is_some() {
                return None;
            }
            graphs
                .bodies()
                .values()
                .find(|primary| primary.hero.as_deref().and_then(parse_guid) == Some(hero.guid))
                .and_then(|primary| primary.target_tags.clone())
        });
        let charge_route = charge::ChargeRoute::prepare(body, mode, &roots)?;
        Ok(Self {
            graphs,
            codecs,
            mode,
            hero: hero.guid,
            body: hero.body,
            roots,
            variables,
            pools,
            target_tags,
            controller_on_fire,
            // The reference production default: `Component.__init__` keeps
            // the compatibility (normal) policy, and strict is the opt-in
            // for mission reconstruction. The owner stream follows the
            // reference production policy; harnesses that must fail loud
            // opt in with `with_strict(true)`.
            strict: false,
            charge_route,
            anim_timing: None,
        })
    }

    /// Chooses the reference component policy for the owner stream: `strict`
    /// true keeps unresolved natives and unmodeled state classes loud reports
    /// (the reference mission-reconstruction opt-in); `strict` false (the
    /// reference production default, `Component.__init__`) applies its
    /// fallbacks: typed-null natives and plain base lifecycle. See
    /// `script_expr::Context::strict`. Test lanes pin either side; the
    /// production stream uses the reference default.
    #[cfg(test)]
    pub(crate) fn with_strict(mut self, strict: bool) -> Self {
        self.strict = strict;
        self
    }

    pub(crate) fn with_anim_timing(
        mut self,
        anim_timing: Option<Arc<crate::anim_timing::AnimationTiming>>,
    ) -> Self {
        self.anim_timing = anim_timing;
        self
    }

    pub(crate) fn probe_startup(
        &self,
        spawn: OwnerBodySpawn,
        setup: OwnerBodySetup,
    ) -> Result<(), OwnerBodyPreparationError> {
        let runtime = self.start_runtime(spawn, setup)?;
        let encoder = runtime.encoder();
        let snapshot = runtime
            .snapshot()
            .expect("successful body startup has a snapshot");
        let projected = encoder
            .project(snapshot, View::Owner)
            .map_err(OwnerBodyPreparationError::Projection)?;
        encoder
            .full(&projected)
            .map_err(OwnerBodyPreparationError::Encoding)?;
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn prepare(
        graphs: Arc<GraphStore>,
        codecs: Arc<CodecTable>,
        mode: u64,
        hero: &Hero,
        pools: Option<HealthPools>,
        controller_on_fire: bool,
        probe: (OwnerBodySpawn, OwnerBodySetup),
    ) -> Result<Self, OwnerBodyPreparationError> {
        // Test preparation exercises the reference's strict reconstruction
        // mode; lanes that want the production policy build their factory
        // from `from_body` directly and opt in explicitly.
        let factory = Self::from_body(graphs, codecs, mode, hero, pools, controller_on_fire)?
            .with_strict(true);
        factory.probe_startup(probe.0, probe.1)?;
        Ok(factory)
    }

    #[cfg(test)]
    pub(crate) fn start(&self, spawn: OwnerBodySpawn) -> io::Result<PlayerBodyRuntime> {
        self.start_with_setup(spawn, OwnerBodySetup::default())
    }

    pub(crate) fn start_with_setup(
        &self,
        spawn: OwnerBodySpawn,
        setup: OwnerBodySetup,
    ) -> io::Result<PlayerBodyRuntime> {
        self.start_runtime(spawn, setup)
            .map_err(|error| match error {
                OwnerBodyStartupError::ProjectileLeases(source)
                | OwnerBodyStartupError::ChargeAdmission(source) => source,
                other => io::Error::new(io::ErrorKind::Unsupported, other),
            })
    }

    fn start_runtime(
        &self,
        spawn: OwnerBodySpawn,
        setup: OwnerBodySetup,
    ) -> Result<PlayerBodyRuntime, OwnerBodyStartupError> {
        if spawn.quanta != RETAIL_QUANTA {
            return Err(OwnerBodyStartupError::Clock);
        }
        let has_authored_pools = self
            .roots
            .iter()
            .any(|root| !root.is_server_only() && root.graph == BODY_GRAPH);
        let mut world = BodyWorld {
            spatial_frame: setup.spatial_frame,
            team_member_counts: setup.team_member_counts,
            room_activity: setup.room_activity,
            script_requests: Default::default(),
            graphs: Arc::clone(&self.graphs),
            controller: spawn.controller,
            entity: spawn.entity,
            body_filter: spawn.body_filter,
            position: spawn.pose.position,
            eye_position: spawn.eye_position,
            movement: spawn.pose,
            throttles: [0; 2],
            mode: self.mode,
            hero: self.hero,
            body_definition: self.body,
            anim_timing: self.anim_timing.clone(),
            vitals: if has_authored_pools {
                Vitals::empty()
            } else {
                self.pools.unwrap_or(FALLBACK_POOLS).vitals()
            },
            pool_types_known: self.pools.is_some() || has_authored_pools,
            target_tags: self.target_tags.clone(),
            command_views: Default::default(),
            wheel_cosmetics: Default::default(),
            model_hardpoints: self.graphs.bodies().get(&self.body).and_then(|body| {
                body.model_geometry(spawn.skin)
                    .or_else(|| (!body.has_model_hardpoints()).then(Arc::default))
            }),
            target_tag_effects: TargetTagEffects::default(),
            controller_on_fire: self.controller_on_fire,
            defenders: Arc::default(),
            sightings: Vec::new(),
            sighting_source: None,
            death_source: None,
            cinematic_aim: Default::default(),
            facing_claims: Default::default(),
            deadeye_scene: DeadeyeScene::default(),
            velocity_changes: Vec::new(),
            scripted_motion: None,
            // Filled from the mover on the first posed step; the trace can
            // only launch from a commanded button edge.
            collision_category: 0,
            mover_settings: setup.mover_settings,
            voice_variant_counter: 0,
            voice_cooldowns: Default::default(),
            pet_positions: Default::default(),
            spawned_possessables: Default::default(),
            pending_body_swap: None,
            combat_mods: Default::default(),
            deflect: Default::default(),
            recall_history: Default::default(),
            active_auras: Default::default(),
            ball_roll_speed: None,
        };
        let mut component = Component::start_with_public_schema_policy(
            Arc::clone(&self.graphs),
            spawn.entity,
            self.variables.clone(),
            self.roots.clone(),
            spawn.frame,
            &mut world,
            (MAX_INIT_WORK, self.strict),
        )
        .map_err(OwnerBodyStartupError::VmInitialize)?;
        component
            .advance_frame(spawn.frame, &mut world)
            .map_err(OwnerBodyStartupError::FirstFrame)?;
        world.vitals.clear_changed();
        let modifiers = component
            .movement_modifiers(&mut world)
            .map_err(OwnerBodyStartupError::MovementModifiers)?;
        let leases = ProjectileLeases::from_authored(
            &self.graphs,
            component.snapshot().expect("body startup has a snapshot"),
            spawn.slot,
            spawn.frame,
            |instance, raw, config| {
                component.evaluate_state_config(instance, raw, config, &mut world)
            },
        )
        .map_err(OwnerBodyStartupError::ProjectileLeases)?;
        let mut runtime = PlayerBodyRuntime {
            component,
            world,
            codecs: Arc::clone(&self.codecs),
            modifiers,
            leases,
            frame: spawn.frame,
            wheel_delivery_frame: spawn.frame,
            charge_binding: self.charge_route.bind(self.mode, self.hero),
            ult_charge_scale: 1.0,
        };
        runtime
            .sync_charge_admission(true)
            .map_err(OwnerBodyStartupError::ChargeAdmission)?;
        Ok(runtime)
    }
}

pub(super) fn unsupported(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::Unsupported, error.to_string())
}

#[cfg(test)]
pub(crate) fn fixture_body_factory() -> (PlayerBodyFactory, OwnerBodySpawn) {
    fixture_body_factory_with_nodes(Vec::new())
}

#[cfg(test)]
pub(crate) fn fixture_body_factory_with_nodes(
    nodes: Vec<serde_json::Value>,
) -> (PlayerBodyFactory, OwnerBodySpawn) {
    use serde_json::json;

    let body = 0x0400_0000_0000_0001;
    let hero = Hero {
        guid: SOLDIER,
        name: "Fixture".into(),
        role: "Damage".into(),
        body,
    };
    let graph = |index, nodes: Vec<serde_json::Value>| {
        json!({
            "index":index,"items":nodes.len(),"nodes":nodes,"states":[],"entries":[],"remote":[],
            "sync_vars":[],"containers":{},"unknown_nodes":[],"fields":{},
            "header":{"m_nodesBitCount":4,"m_statesBitCount":4,
                "m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}
        })
    };
    let mut graphs = vec![graph(42, Vec::new())];
    let body_graph = if nodes.is_empty() {
        42
    } else {
        // Keep the fixture listener on one body root. The manager and weapon
        // still use the empty graph and each subscribe only to their own data.
        graphs.push(graph(43, nodes));
        43
    };
    let bundle = json!({"version":1,"missing":[],"graphs":graphs,"bodies":{
        format!("0x{body:016X}"): {"hero": format!("0x{SOLDIER:016X}"), "name":"Fixture",
            "graphs":[{"graph":body_graph,"m_75408640":0}],"manager":42,"weapons":[null,42]}
    }});
    let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    let codecs = Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap());
    let spawn = OwnerBodySpawn {
        controller: 0xa000_0100,
        entity: 0xa000_0101,
        body_filter: crate::match_scene::team_bit(0).unwrap(),
        slot: 0,
        frame: 100,
        pose: Movement {
            position: [1.0, 2.0, 3.0],
            ..Movement::default()
        },
        eye_position: None,
        skin: 0,
        quanta: RETAIL_QUANTA,
    };
    let factory = PlayerBodyFactory::prepare(
        graphs,
        codecs,
        PRACTICE_RANGE_MODE,
        &hero,
        None,
        false,
        (spawn, OwnerBodySetup::default()),
    )
    .unwrap();
    (factory, spawn)
}

#[cfg(test)]
pub(crate) fn fixture_hitscan_factory(damage: f64) -> PlayerBodyFactory {
    use serde_json::json;

    fixture_volley_factory(
        20,
        false,
        json!({
            "m_A17BE89B":2,"m_numShotsPerSecond":9,"m_FEC435C6":180,
            "m_6396149F":{"m_modifyHealth":{"m_amount":damage},"m_A872C70E":2}
        }),
    )
}

#[cfg(test)]
pub(crate) const FIXTURE_PROJECTILE_DEFINITION: u64 = 0x0400_0000_0000_072c;

#[cfg(test)]
pub(crate) fn fixture_projectile_factory(damage: f64) -> PlayerBodyFactory {
    fixture_volley_factory(29, true, fixture_projectile_fields(damage))
}

#[cfg(test)]
fn fixture_projectile_fields(damage: f64) -> Json {
    serde_json::json!({
        "m_A17BE89B":1,"m_numShotsPerSecond":1,"m_FEC435C6":100,
        "m_45271E8C":true,"m_numProjectilesPerShot":1,
        "m_projectileSpeed":50,"m_projectileLifetime":2,
        "m_projectileEntity":{"$":"STUStatescriptWeaponProjectileEntity","m_A9A62FD0":1,
            "m_entityDef":{"$":"STU_8556841E","m_EE729DCB":0,"m_resourceKey":null,
                "m_entityDef":format!("0x{FIXTURE_PROJECTILE_DEFINITION:016X}")}},
        "m_6396149F":{"m_modifyHealth":{"m_amount":damage}},
        "m_D2D11CE9":{"m_modifyHealth":{"m_amount":80.0},"m_0D30E40C":[
            {"m_radius":1,"m_B399CACB":1},
            {"m_radius":3,"m_B399CACB":0.5}]}
    })
}

#[cfg(test)]
fn fixture_volley_factory(button: u8, networked: bool, mut volley: Json) -> PlayerBodyFactory {
    use serde_json::json;

    volley["m_2BBEEAB8"] = json!(true);
    let remote = if networked { json!([2]) } else { json!([]) };
    let graph = json!({
        "index":42,"items":3,"states":[1,2],"entries":[0],"remote":remote,
        "sync_vars":[],"containers":{},"unknown_nodes":[],"fields":{"m_680A2CB2":[0]},
        "header":{"m_nodesBitCount":4,"m_statesBitCount":4,
            "m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4},
        "nodes":[
            {"pos":0,"class":"STUStatescriptEntry","uid":0,"rid":0,"remote":-1,
                "state":null,"client_only":false,"server_only":false,"fields":{
                "m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]],"m_FF3DAF1E":[]}}},
            {"pos":1,"class":"STUStatescriptStateLogicalButton","uid":1,"rid":1,"remote":-1,
                "state":0,"client_only":false,"server_only":true,"fields":{
                "m_2BBEEAB8":true,"m_logicalButton":button,
                "m_onGoingDownPlug":{"$":"Plug","links":[[2,"m_beginPlug"]],"m_FF3DAF1E":[]}}},
            {"pos":2,"class":"STUStatescriptStateWeaponVolley","uid":2,"rid":2,
                "remote":if networked { 0 } else { -1 },
                "state":1,"client_only":false,"server_only":!networked,"fields":volley}
        ]
    });
    let bundle = json!({"version":1,"missing":[],"graphs":[graph],"bodies":{}});
    let mut factory = fixture_body_factory().0;
    factory.graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    if networked {
        factory.codecs =
            Arc::new(CodecTable::from_json(crate::script_snapshot_wire::FIXTURE_CODECS).unwrap());
    }
    factory.roots = vec![Root::new(1, 42)];
    factory
}
