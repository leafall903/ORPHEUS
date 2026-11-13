//! Prepared owner statescript for a live body in build 104319.

use std::{
    collections::{BTreeMap, BTreeSet},
    io,
    sync::Arc,
};

use serde_json::Value as Json;

use crate::{
    accounts::parse_guid,
    collision_scene::CollisionScene,
    commands::Command,
    game_combat::{
        BIOTIC_CANISTER, PosedBioticField, PosedHitscan, PosedProjectile, PosedShot, Stun,
        eye_height,
    },
    game_content::{PRACTICE_RANGE_MODE, TUTORIAL_MODE, is_junkenstein_mode},
    game_heroes::{CASSIDY, Hero, SOLDIER},
    game_hitscan::{HitDefinition, TargetPose, aim_direction},
    game_instance::RETAIL_QUANTA,
    game_pools::{BODY_GRAPH, FALLBACK_POOLS, HealthPools},
    game_projectiles::{ProjectileLeases, authored_number, local_prediction, predicted_sync},
    game_replication::{BodyStep, OwnerBodyRuntime},
    junkenstein_messages::{
        self, DefenderMessage, DefenderSighting, SIGHTING, SIGHTING_TAGS, SightingSource,
    },
    mover::{Mover, sample_movement_flags},
    mover_modifiers::{Modifier, ModifierKey},
    projectile_flight::{ProjectileBallistics, authored_gravity, lifted_direction},
    scene_assets::{MOVER_CATEGORY, PreparedScene},
    script_codecs::CodecTable,
    script_expr::{EntityRelation, OnFire, native_shape, to_float, valid_entity},
    script_graph::GraphStore,
    script_init::{
        Component, MAX_INIT_WORK, Root, Site, Snapshot, TargetTagEffect, TargetTagEffects, World,
    },
    script_runtime::FRAME_MS,
    script_snapshot_wire::{SnapshotEncoder, View},
    script_wire::{Value, Variables},
    static_ray::{Vec3, capsule_entry_distance, norm},
    vitals::{Death, PoolKind, Vitals},
    world::{Component as EntityComponent, GAME_MODE_ENTITY, Movement},
};

#[derive(Clone, Copy)]
pub(crate) struct OwnerBodySpawn {
    pub controller: u32,
    pub entity: u32,
    pub body_filter: u64,
    pub slot: u8,
    pub frame: u32,
    pub pose: Movement,
    pub quanta: u32,
}

pub(crate) struct PlayerBodyFactory {
    graphs: Arc<GraphStore>,
    codecs: Arc<CodecTable>,
    mode: u64,
    hero: u64,
    roots: Vec<Root>,
    variables: Variables,
    pools: Option<HealthPools>,
    target_tags: Option<BTreeSet<u64>>,
    controller_on_fire: bool,
}

impl PlayerBodyFactory {
    pub(crate) fn prepare(
        graphs: Arc<GraphStore>,
        codecs: Arc<CodecTable>,
        mode: u64,
        hero: &Hero,
        pools: Option<HealthPools>,
        controller_on_fire: bool,
        probe: OwnerBodySpawn,
    ) -> io::Result<Self> {
        let body = graphs
            .bodies()
            .get(&hero.body)
            .filter(|body| body.hero.as_deref().and_then(parse_guid) == Some(hero.guid))
            .ok_or_else(|| {
                unsupported("the hero body definition is absent from the graph bundle")
            })?;
        let mut roots = crate::script_init::player_roots(body, &graphs).map_err(unsupported)?;
        if is_junkenstein_mode(mode) {
            const DEFENDER_BODY_GRAPH: u32 = 0x0ca5;
            let id = roots
                .last()
                .map_or(0, |root| root.id)
                .checked_add(1)
                .ok_or_else(|| unsupported("defender body graph instance ID exhausted"))?;
            if graphs.graph(DEFENDER_BODY_GRAPH).is_none() {
                return Err(unsupported("Junkenstein defender body graph is absent"));
            }
            roots.push(Root::new(id, DEFENDER_BODY_GRAPH));
        }
        let health = pools.unwrap_or(FALLBACK_POOLS).total();
        let variables = crate::script_init::player_entity_variables(body, health, health);
        let target_tags = body.target_tags.clone();
        let factory = Self {
            graphs,
            codecs,
            mode,
            hero: hero.guid,
            roots,
            variables,
            pools,
            target_tags,
            controller_on_fire,
        };
        let runtime = factory.start(probe)?;
        let encoder = runtime.encoder();
        let snapshot = runtime
            .snapshot()
            .expect("successful body startup has a snapshot");
        let projected = encoder
            .project(snapshot, View::Owner)
            .map_err(unsupported)?;
        encoder.full(&projected).map_err(unsupported)?;
        Ok(factory)
    }

    pub(crate) fn start(&self, spawn: OwnerBodySpawn) -> io::Result<PlayerBodyRuntime> {
        if spawn.quanta != RETAIL_QUANTA {
            return Err(unsupported("the owner VM requires the 16 ms retail clock"));
        }
        let has_authored_pools = self.roots.iter().any(|root| root.graph == BODY_GRAPH);
        let mut world = BodyWorld {
            graphs: Arc::clone(&self.graphs),
            controller: spawn.controller,
            entity: spawn.entity,
            body_filter: spawn.body_filter,
            position: spawn.pose.position,
            movement: spawn.pose,
            throttles: [0; 2],
            mode: self.mode,
            hero: self.hero,
            vitals: if has_authored_pools {
                Vitals::empty()
            } else {
                self.pools.unwrap_or(FALLBACK_POOLS).vitals()
            },
            pool_types_known: self.pools.is_some() || has_authored_pools,
            target_tags: self.target_tags.clone(),
            target_tag_effects: TargetTagEffects::default(),
            controller_on_fire: self.controller_on_fire,
            defenders: Arc::default(),
            sightings: Vec::new(),
            sighting_source: None,
            cinematic_aim: None,
            deadeye_scene: DeadeyeScene::default(),
            velocity_changes: Vec::new(),
            damage_credit: None,
        };
        let mut component = Component::start(
            Arc::clone(&self.graphs),
            spawn.entity,
            self.variables.clone(),
            self.roots.clone(),
            spawn.frame,
            &mut world,
            MAX_INIT_WORK,
        )
        .map_err(unsupported)?;
        component
            .advance_frame(spawn.frame, &mut world)
            .map_err(unsupported)?;
        world.vitals.clear_changed();
        let modifiers = component
            .movement_modifiers(&mut world)
            .map_err(unsupported)?;
        let leases = ProjectileLeases::from_authored(
            &self.graphs,
            component.snapshot().expect("body startup has a snapshot"),
            spawn.slot,
            spawn.frame,
            |instance, raw, config| {
                component.evaluate_state_config(instance, raw, config, &mut world)
            },
        )?;
        let mut runtime = PlayerBodyRuntime {
            component,
            world,
            codecs: Arc::clone(&self.codecs),
            modifiers,
            leases,
            frame: spawn.frame,
            encounter_active: false,
        };
        runtime.sync_charge_admission(true)?;
        Ok(runtime)
    }
}

pub(crate) struct PlayerBodyRuntime {
    encounter_active: bool,
    component: Component,
    world: BodyWorld,
    codecs: Arc<CodecTable>,
    modifiers: Option<BTreeMap<ModifierKey, Modifier>>,
    leases: ProjectileLeases,
    frame: u32,
}

fn flashbang_duration(graphs: &GraphStore) -> Option<&Json> {
    graphs
        .graph(0x0228)?
        .node(7)?
        .field("m_69F4B52C.m_6525E98B.m_1EB5A024")?
        .as_array()?
        .iter()
        .find(|row| {
            row.get("m_0D09D2D9")
                .and_then(crate::script_graph::asset_guid)
                == Some(0x0d80_0000_0000_00d7)
        })?
        .get("m_value")
}

const DEADEYE_GRAPH: u32 = 0x022f;
const DEADEYE_TARGET_EFFECT: u32 = 0x01ee;
impl PlayerBodyRuntime {
    fn sync_charge_admission(&mut self, active: bool) -> io::Result<()> {
        if !self.world.has_deadeye() {
            return Ok(());
        }
        self.encounter_active = active;
        let enabled = active && self.world.vitals.death().is_none();
        let variable = crate::script_expr::Variable {
            scope: crate::script_expr::Scope::Entity,
            id: 2426,
        };
        if self.component.resolved_variable(0, variable) != Some(Value::Bool(enabled)) {
            // The room owns this live defender capability. The authored 0B8B
            // graph owns the passive wait, amount, multipliers, damage gates
            // and cap; the former 60-second host fill is intentionally removed.
            self.component
                .set_entity_variable(2426, Value::Bool(enabled), &mut self.world)
                .map_err(unsupported)?;
            self.component
                .run_now(&mut self.world)
                .map_err(unsupported)?;
        }
        Ok(())
    }
}

impl OwnerBodyRuntime for PlayerBodyRuntime {
    fn set_encounter_active(&mut self, active: bool) -> io::Result<()> {
        self.sync_charge_admission(active)
    }
    fn receive_applied_damage(
        &mut self,
        damage: crate::game_combat::AppliedDamage,
    ) -> io::Result<()> {
        if !self.world.has_deadeye()
            || !self.encounter_active
            || damage.source != self.world.entity
            || self.world.vitals.death().is_some()
            || !damage.amount.is_finite()
            || damage.amount <= 0.0
        {
            return Ok(());
        }
        if self.component.snapshot().is_some_and(|snapshot| {
            snapshot.instances.values().any(|instance| {
                instance.graph == DEADEYE_GRAPH
                    && instance
                        .states
                        .get(&0)
                        .is_some_and(crate::script_init::State::ability_running)
            })
        }) {
            return Ok(());
        }
        let instance = self
            .component
            .snapshot()
            .and_then(|snapshot| {
                snapshot
                    .instances
                    .iter()
                    .find(|(_, instance)| instance.graph == 0x0b8b)
                    .map(|(&id, _)| id)
            })
            .ok_or_else(|| {
                unsupported("damage credit requires the authored common charge graph")
            })?;
        let parameters = Variables::from([
            (0x13, Value::Float(f64::from(damage.amount as f32))),
            (0x15, Value::Entity(damage.target)),
            (
                0x3d,
                Value::Array(
                    damage
                        .tags
                        .as_slice()
                        .iter()
                        .copied()
                        .map(Value::Asset)
                        .collect(),
                ),
            ),
            // AppliedDamage is net pool loss; no gross mitigation contributions
            // remain to subtract in the authored detail fold.
            (0x624, Value::Array(Vec::new())),
        ]);
        self.component
            .queue_message(instance, 0x0240_0000_0000_0012, parameters)
            .map_err(unsupported)?;
        self.world.damage_credit = Some(damage);
        let result = self.component.run_now(&mut self.world).map_err(unsupported);
        self.world.damage_credit = None;
        result
    }
    fn target_tags(&self) -> crate::script_init::TargetTagSnapshot {
        self.world
            .target_tag_effects
            .snapshot(self.world.target_tags.as_ref())
    }
    fn target_filter_flags(&self) -> Option<u64> {
        Some(self.world.own_filter_flags())
    }

    fn set_deadeye_scene(&mut self, scene: &PreparedScene, team: u8) -> io::Result<()> {
        if self.world.has_deadeye() {
            self.world.deadeye_scene = scene.deadeye_scene(team).map_err(io::Error::other)?;
        }
        Ok(())
    }

    fn set_defenders(&mut self, defenders: Arc<[u32]>) {
        self.world.defenders = defenders;
    }

    fn take_sightings(&mut self) -> Vec<DefenderSighting> {
        std::mem::take(&mut self.world.sightings)
    }

    fn receive_sighting(&mut self, message: DefenderSighting) -> io::Result<()> {
        if !is_junkenstein_mode(self.world.mode)
            || message.target != self.world.entity
            || self.world.vitals.death().is_some()
        {
            return Ok(());
        }
        self.component
            .queue_entity_message_from(message.source.entity, SIGHTING, message.parameters)
            .map_err(unsupported)?;
        self.world.sighting_source = Some(message.source);
        let result = self.component.run_now(&mut self.world).map_err(unsupported);
        self.world.sighting_source = None;
        result
    }

    fn receive_defender_message(&mut self, message: DefenderMessage) -> io::Result<usize> {
        if !is_junkenstein_mode(self.world.mode) || !message.accepts_body(self.world.entity) {
            return Ok(0);
        }
        let queued = self
            .component
            .queue_entity_message_from(message.source, message.message, message.parameters)
            .map_err(unsupported)?;
        self.component
            .run_now(&mut self.world)
            .map_err(unsupported)?;
        Ok(queued)
    }

    fn cinematic_aim(&self) -> Option<[i32; 2]> {
        self.world.cinematic_aim.map(|(_, aim)| aim)
    }

    fn vitals(&self) -> Option<&Vitals> {
        Some(&self.world.vitals)
    }

    fn heal(&mut self, amount: f64) {
        self.world.vitals.heal(amount);
    }

    fn receive_damage(&mut self, amount: f64, source: Death) -> bool {
        let result = self.world.vitals.damage(amount, source);
        if result.killed {
            self.component.set_owner_dead(true);
            if let Err(error) = self.sync_charge_admission(self.encounter_active) {
                eprintln!("Owner death charge cleanup failed: {error}");
            }
        }
        result.killed
    }

    fn take_health_update(&mut self) -> Option<EntityComponent> {
        let vitals = &mut self.world.vitals;
        if !vitals.has_changed() {
            return None;
        }
        vitals.clear_changed();
        Some(vitals.cumulative_component())
    }

    fn snapshot(&self) -> Option<&Snapshot> {
        self.component.snapshot()
    }

    fn encoder(&self) -> SnapshotEncoder<'_> {
        SnapshotEncoder::new(&self.world.graphs, &self.codecs)
    }

    fn advance(&mut self, command: Command, mover: &Mover, step: BodyStep) -> io::Result<()> {
        if command.frame <= self.frame
            || (step != BodyStep::UnposedTimer && command.frame != self.frame.saturating_add(1))
        {
            return Err(unsupported(
                "the owner VM frame is outside the command sequence",
            ));
        }
        let pose = mover.pose();
        self.world.position = pose.position;
        // Command gaps and timer advances retain the last movement record.
        if step == BodyStep::Posed {
            self.world.movement = pose;
        }
        if step == BodyStep::UnposedTimer {
            self.component
                .advance_frame(command.frame, &mut self.world)
                .map_err(unsupported)?;
        } else {
            self.world.throttles = [command.right, command.forward];
            self.component
                .advance_command(command, &mut self.world)
                .map_err(unsupported)?;
            self.modifiers = self
                .component
                .movement_modifiers(&mut self.world)
                .map_err(unsupported)?;
        }
        self.frame = command.frame;
        Ok(())
    }

    fn frame_ms(&self) -> u32 {
        FRAME_MS as u32
    }

    fn take_velocity_changes(&mut self) -> Vec<(u32, crate::mover::VelocityChange)> {
        if self.world.vitals.death().is_some() {
            self.world.velocity_changes.clear();
        }
        std::mem::take(&mut self.world.velocity_changes)
    }

    fn movement_modifiers(&self) -> Option<&BTreeMap<ModifierKey, Modifier>> {
        self.modifiers.as_ref()
    }

    fn projectile_leases(&self) -> Option<&ProjectileLeases> {
        (!self.leases.blocks().is_empty()).then_some(&self.leases)
    }

    fn take_shots(&mut self, command: Option<Command>, mover: &Mover) -> Vec<PosedShot> {
        let shots = self.component.take_shots().unwrap_or_default();
        let Some(command) = command else {
            return Vec::new();
        };
        let [x, y, z] = mover.pose().position;
        let hero = self.world.hero;
        let origin = [x, y + eye_height(hero, mover.crouched()), z];
        shots
            .into_iter()
            .filter_map(|shot| {
                let node = self.component.shot_node(&shot)?;
                // 0228/7 supplies the duration override for the Flashbang effect,
                // including the mode-dependent branch. Keep policy in authored data.
                let stun_duration = (hero == CASSIDY && (shot.graph, node.pos) == (0x01e9, 7))
                    .then(|| flashbang_duration(&self.world.graphs).cloned())
                    .flatten();
                // Python e1128509 declares this primary-only implicit policy;
                // it is not a recovered global native critical default.
                let default_critical = if (hero, shot.graph, node.pos) == (CASSIDY, 0x01d2, 31) {
                    2.0
                } else {
                    1.0
                };
                let mut evaluate = |config: &Json| {
                    self.component
                        .evaluate_shot_config(&shot, config, &mut self.world)
                };
                if shot.graph == 0x0257
                    && node.pos == 4
                    && hero == SOLDIER
                    && node
                        .field("m_49CBA3BC.m_entityDefinition")
                        .and_then(|config| evaluate(config).ok())
                        == Some(Value::Asset(0x0400_0000_0000_03dc))
                    && node
                        .field("m_projectileEntity.m_entityDef")
                        .and_then(|config| evaluate(config).ok())
                        == Some(Value::Asset(BIOTIC_CANISTER))
                    && self.leases.entity(BIOTIC_CANISTER, shot.frame, 0).is_some()
                {
                    return Some(PosedShot::BioticField(PosedBioticField {
                        shot,
                        feet: [x, y, z],
                    }));
                }
                let speed = node.field("m_projectileSpeed").unwrap_or(&Json::Null);
                if !speed.is_null() && to_float(&evaluate(speed).ok()?) != 0.0 {
                    // Admit only the known unconditional gravity path for Flashbang;
                    // other projectile motion/aim configurations remain unsupported.
                    let flashbang = hero == CASSIDY && (shot.graph, node.pos) == (0x01e9, 7);
                    let gravity = flashbang
                        .then(|| authored_gravity(&node.fields, &mut evaluate))
                        .flatten();
                    if !node.is_networked()
                        || !local_prediction(node)
                        || !predicted_sync(node, &mut evaluate).ok()?
                        || node
                            .field("m_projectileMotions")
                            .is_some_and(|v| !v.is_null() && gravity.is_none())
                        || node.field("m_aimID").is_some_and(|v| !v.is_null())
                        || authored_number(
                            node.field("m_numProjectilesPerShot"),
                            1.0,
                            &mut evaluate,
                        )
                        .ok()?
                            != 1.0
                    {
                        return None;
                    }
                    let ballistics =
                        ProjectileBallistics::from_authored(node, &mut evaluate).ok()??;
                    let definition = ballistics.definition?;
                    self.leases.entity(definition, shot.frame, 0)?;
                    if !ballistics.direct.is_some_and(|hit| hit.amount > 0.0)
                        && !ballistics
                            .blast
                            .as_ref()
                            .is_some_and(|blast| blast.hit.amount > 0.0)
                    {
                        return None;
                    }
                    // Preserve the existing rejection of unsupported mixed
                    // payloads outside the verified Flashbang zero-direct case.
                    if !flashbang
                        && (ballistics.direct.is_some_and(|hit| hit.amount <= 0.0)
                            || ballistics
                                .blast
                                .as_ref()
                                .is_some_and(|blast| blast.hit.amount <= 0.0))
                    {
                        return None;
                    }
                    let mut direction = aim_direction(&command);
                    if flashbang {
                        let lift = authored_number(
                            node.field("m_5EC1EB38.m_22CE7F30"),
                            0.0,
                            &mut evaluate,
                        )
                        .ok()?;
                        direction = lifted_direction(direction, lift)?;
                    }
                    let stun = if flashbang {
                        let effect = node.field("m_D2D11CE9.m_modifyHealth.m_277A4ECA")?;
                        if evaluate(effect).ok()? != Value::Asset(0x04a0_0000_0000_0079) {
                            return None;
                        }
                        Some(Stun::from_seconds(to_float(
                            &evaluate(stun_duration.as_ref()?).ok()?,
                        ))?)
                    } else {
                        None
                    };
                    return Some(PosedShot::Projectile(Box::new(PosedProjectile {
                        stun,
                        shot,
                        ballistics,
                        origin,
                        direction,
                        gravity,
                        // Scoped reconstruction shared with Python e1128509.
                        expiry_blast: flashbang && gravity.is_some(),
                    })));
                }
                let hit = HitDefinition::from_authored_with_critical(
                    node.field("m_6396149F")?,
                    default_critical,
                    &mut evaluate,
                )
                .ok()??;
                if hit.amount <= 0.0 {
                    return None;
                }
                let reach = node.field("m_FEC435C6").unwrap_or(&Json::Null);
                let reach = if reach.is_null() {
                    0.0
                } else {
                    to_float(&evaluate(reach).ok()?)
                };
                let direction = if (shot.graph, node.pos) == (DEADEYE_TARGET_EFFECT, 14) {
                    let snapshot = self.component.snapshot()?;
                    let Value::Entity(entity) = *snapshot
                        .instances
                        .get(&shot.instance)?
                        .variables
                        .get(&536)?
                    else {
                        return None;
                    };
                    let Value::Array(visible) = snapshot.variables.get(&5874)? else {
                        return None;
                    };
                    if !visible.contains(&Value::Entity(entity)) {
                        return None;
                    }
                    self.world.deadeye_scene.shot_direction(origin, entity)?
                } else {
                    aim_direction(&command)
                };
                Some(PosedShot::Hitscan(Box::new(PosedHitscan {
                    source: shot.entity,
                    origin,
                    direction,
                    command,
                    shot_frame: shot.frame,
                    reach,
                    hit,
                })))
            })
            .collect()
    }
}

#[derive(Default)]
pub(crate) struct DeadeyeScene {
    pub health: Option<std::collections::BTreeMap<u32, f64>>,
    pub targets: Vec<DeadeyeTarget>,
    pub collision: Option<Arc<CollisionScene>>,
    pub view_subjects: BTreeMap<u32, ViewSubject>,
}

pub(crate) struct ViewSubject {
    pub position: Vec3,
    pub forward: Vec3,
    pub tags: crate::script_init::TargetTagSnapshot,
}

pub(crate) struct DeadeyeTarget {
    pub velocity: Option<[f64; 3]>,
    pub script_state: crate::script_entity_state::EntityScriptState,
    pub entity: u32,
    pub pose: TargetPose,
    pub health: f64,
}

impl DeadeyeScene {
    fn shot_direction(&self, origin: Vec3, entity: u32) -> Option<Vec3> {
        let target = self
            .targets
            .iter()
            .find(|target| target.entity == entity)?
            .pose;
        let delta = target_delta(origin, target);
        let distance = norm(delta);
        (distance > 0.0 && self.aim_point_visible(origin, target))
            .then(|| delta.map(|axis| axis / distance))
    }

    fn aim_point_visible(&self, origin: Vec3, target: TargetPose) -> bool {
        let Some(collision) = &self.collision else {
            return true;
        };
        let [bottom, top] = target.segment();
        // Admission must use the same ray as the emitted auto-aim shot. The
        // nearest capsule point can be hidden below a balcony while the actual
        // aim point is exposed; it is not a valid proxy for this shot's path.
        let delta = target_delta(origin, target);
        let length = norm(delta);
        if length <= 1e-9 {
            return true;
        }
        let direction = delta.map(|axis| axis / length);
        let Some(reach) =
            capsule_entry_distance(origin, direction, length, bottom, top, target.radius)
        else {
            return false;
        };
        reach <= 1e-9
            || collision
                .cast_ray(origin, direction, reach, MOVER_CATEGORY)
                .is_ok_and(|hit| hit.is_none_or(|hit| hit.distance + 1e-9 >= reach))
    }

    fn select(
        &self,
        origin: Vec3,
        forward: Vec3,
        radius: f64,
        angle: f64,
        visible: bool,
    ) -> Vec<u32> {
        let threshold = (angle * 0.5).to_radians().cos();
        let mut matches = Vec::new();
        for &DeadeyeTarget {
            entity,
            pose: target,
            ..
        } in &self.targets
        {
            let delta = target_delta(origin, target);
            let distance = norm(delta);
            let projection: f64 = delta.iter().zip(forward).map(|(a, b)| a * b).sum();
            if distance == 0.0 || distance > radius || projection < threshold * distance {
                continue;
            }
            if visible && !self.aim_point_visible(origin, target) {
                continue;
            }
            matches.push((distance, entity));
        }
        matches.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        matches.into_iter().map(|(_, entity)| entity).collect()
    }
}

fn target_delta(origin: Vec3, target: TargetPose) -> Vec3 {
    let [x, y, z] = target.feet;
    let [_, top] = target.segment();
    [
        x - origin[0],
        (y + target.eye).min(top[1]) - origin[1],
        z - origin[2],
    ]
}

struct BodyWorld {
    damage_credit: Option<crate::game_combat::AppliedDamage>,
    graphs: Arc<GraphStore>,
    controller: u32,
    entity: u32,
    body_filter: u64,
    position: [f64; 3],
    movement: Movement,
    throttles: [i8; 2],
    mode: u64,
    hero: u64,
    vitals: Vitals,
    pool_types_known: bool,
    target_tags: Option<BTreeSet<u64>>,
    target_tag_effects: TargetTagEffects,
    controller_on_fire: bool,
    defenders: Arc<[u32]>,
    sightings: Vec<DefenderSighting>,
    sighting_source: Option<SightingSource>,
    cinematic_aim: Option<(Site, [i32; 2])>,
    deadeye_scene: DeadeyeScene,
    velocity_changes: Vec<(u32, crate::mover::VelocityChange)>,
}

impl World for BodyWorld {
    fn engine_setting(&self, _site: Site, key: u32) -> crate::script_expr::EngineSetting {
        if is_junkenstein_mode(self.mode) {
            crate::junkenstein_host::engine_setting(key)
        } else {
            crate::script_expr::EngineSetting::Unavailable
        }
    }

    fn edit_velocity(&mut self, site: Site, frame: u32, value: Value, cap: bool) -> bool {
        use crate::mover::VelocityChange;
        if self.hero != CASSIDY
            || !self.is_player_site(site)
            || site.graph != 0x01f0
            || !matches!((site.node, cap), (Some(8 | 11), false) | (Some(17), true))
            || self.vitals.death().is_some()
        {
            return false;
        }
        let change = match (value, cap) {
            (Value::Vector(desired), false) => VelocityChange::Delta(std::array::from_fn(|axis| {
                f64::from(desired[axis] as f32 - self.movement.velocity[axis] as f32)
            })),
            (Value::Float(cap), true) => VelocityChange::HorizontalCap(cap),
            _ => return false,
        };
        if !change.valid() {
            return false;
        }
        self.velocity_changes
            .push((frame.saturating_add(1), change));
        true
    }

    fn deadeye_target_alive(&self, site: Site, entity: u32) -> bool {
        self.has_deadeye()
            && site.entity == self.entity
            && self.vitals.death().is_none()
            && self
                .deadeye_scene
                .targets
                .iter()
                .any(|target| target.entity == entity)
    }

    fn deadeye_targets(&self, site: Site, radius: f64, angle: f64) -> Option<Vec<u32>> {
        if !self.has_deadeye() || site.entity != self.entity || site.graph != DEADEYE_GRAPH {
            return None;
        }
        if self.vitals.death().is_some() || self.graphs.graph(DEADEYE_TARGET_EFFECT).is_none() {
            return Some(Vec::new());
        }
        let [x, y, z] = self.position;
        let crouched = self.movement.flags & crate::mover_posture::CROUCHED != 0;
        let origin = [x, y + eye_height(self.hero, crouched), z];
        let forward = aim_direction(&Command {
            yaw: self.movement.yaw,
            pitch: self.movement.pitch,
            ..Command::default()
        });
        Some(
            self.deadeye_scene
                .select(origin, forward, radius, angle, site.node == Some(86)),
        )
    }

    fn player_stats_available(&self, site: Site) -> Option<bool> {
        (self.is_player_site(site) && is_junkenstein_mode(self.mode)).then_some(false)
    }

    fn spatial_binding_owner_available(&self, site: Site) -> Option<bool> {
        // This authoritative body host has no local client-world auxiliary
        // owner. Its remote native client still evaluates the view bindings.
        (self.is_player_site(site)
            && is_junkenstein_mode(self.mode)
            && site.graph == 0x0ced
            && matches!(site.node, Some(0 | 4 | 6)))
        .then_some(false)
    }

    fn set_cinematic_aim_lock(&mut self, site: Site, active: bool) -> bool {
        if site.entity != self.entity
            || (site.graph, site.node) != (0x0ca5, Some(70))
            || !is_junkenstein_mode(self.mode)
        {
            return false;
        }
        if active {
            self.cinematic_aim = Some((site, [self.movement.yaw, self.movement.pitch]));
        } else if self.cinematic_aim.is_some_and(|(owner, _)| owner == site) {
            self.cinematic_aim = None;
        }
        true
    }
    fn ruleset(&mut self, id: u32) -> Option<Value> {
        if self.hero == CASSIDY && is_junkenstein_mode(self.mode) {
            // 104319 Cassidy schemas 001B/0026, Python e1128509 rulesets.py.
            match id {
                0x2819 => return Some(Value::Float(1.0)),
                0x290c => return Some(Value::Bool(true)),
                _ => (),
            }
        }
        if (self.mode, self.hero) != (PRACTICE_RANGE_MODE, SOLDIER) {
            return None;
        }
        // Defaults read by the target startup, from ow174/game/script/rulesets.py.
        match id {
            0x2538 | 0x2819 | 0x32e8 => Some(Value::Float(1.0)),
            0x260f => Some(Value::Bool(true)),
            0x281a | 0x32e0 | 0x32e9 => Some(Value::Bool(false)),
            _ => None,
        }
    }

    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if let Some(damage) = &self.damage_credit {
            if damage.target == entity {
                return damage.target_state.read(id);
            }
        }
        if entity != GAME_MODE_ENTITY {
            return self
                .deadeye_scene
                .targets
                .iter()
                .find(|target| target.entity == entity)?
                .script_state
                .read(id);
        }
        self.graphs
            .mode_variables(self.mode)
            .map(|variables| variables.get(&id).copied().map_or(Value::Null, Value::from))
    }

    fn body_angle(
        &self,
        entity: u32,
        axis: crate::script_expr::BodyAngle,
    ) -> crate::script_expr::AngleQuery {
        use crate::script_expr::{AngleQuery, BodyAngle};
        if !is_junkenstein_mode(self.mode) {
            return AngleQuery::Unavailable;
        }
        if entity == 0 {
            return AngleQuery::Unbound;
        }
        if entity != self.entity {
            return AngleQuery::Unavailable;
        }
        AngleQuery::from_packed(match axis {
            BodyAngle::Yaw => self.movement.yaw,
            BodyAngle::Pitch => self.movement.pitch,
        })
    }

    fn entity_velocity(&self, entity: u32) -> crate::script_expr::VelocityQuery {
        use crate::script_expr::VelocityQuery;
        if entity == 0 {
            return VelocityQuery::Unbound;
        }
        if entity == self.entity {
            return VelocityQuery::Present(self.movement.velocity);
        }
        self.deadeye_scene
            .targets
            .iter()
            .find(|target| target.entity == entity)
            .and_then(|target| target.velocity)
            .map_or(VelocityQuery::Unavailable, VelocityQuery::Present)
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            Some(self.position)
        } else {
            self.sighting_source
                .filter(|source| source.entity == entity)
                .map(|source| source.position)
                .or_else(|| {
                    self.deadeye_scene
                        .view_subjects
                        .get(&entity)
                        .map(|subject| subject.position)
                })
        }
    }

    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity && is_junkenstein_mode(self.mode) {
            let yaw = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
            let (sin, cos) = yaw.sin_cos();
            Some([sin, 0.0, cos])
        } else {
            self.deadeye_scene
                .view_subjects
                .get(&entity)
                .map(|subject| subject.forward)
        }
    }

    fn send_message(
        &mut self,
        site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if !self.is_player_site(site)
            || !is_junkenstein_mode(self.mode)
            || (site.graph, site.node) != (0x01ca, Some(127))
            || message != SIGHTING
            || !self.defenders.contains(&target)
            || self.sightings.len() >= MAX_INIT_WORK
        {
            return false;
        }
        self.sightings.push(DefenderSighting {
            target,
            source: SightingSource {
                entity: self.entity,
                position: self.position,
                tags: SIGHTING_TAGS.map(|tag| self.has_target_tag(site, self.entity, tag)),
                filter_flags: Some(self.body_filter),
            },
            parameters: parameters.clone(),
        });
        true
    }

    fn movement_sample(&self, site: Site) -> Option<u16> {
        if !self.is_player_site(site) {
            return None;
        }
        sample_movement_flags(&self.movement)
    }

    fn body_scale(&mut self, site: Site, target: u32, override_value: &Value) -> Option<f64> {
        if target != self.entity || !self.is_player_site(site) {
            return None;
        }
        match override_value {
            Value::Null | Value::Integer(1) => Some(1.0),
            Value::Float(scale) if *scale == 1.0 => Some(1.0),
            _ => None,
        }
    }

    fn body_health(&self, site: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
        if !self.is_player_site(site) || (kind.is_some() && !self.pool_types_known) {
            return None;
        }
        let amount = self.vitals.amount(kind, maximum);
        (amount.is_finite() && amount >= 0.0).then_some(amount)
    }

    fn total_health(&self, site: Site, entity: u32) -> crate::script_expr::HealthQuery {
        use crate::script_expr::HealthQuery;
        if !self.is_player_site(site) {
            return HealthQuery::Unavailable;
        }
        if entity == 0 {
            return HealthQuery::Unbound;
        }
        if entity == self.entity {
            return HealthQuery::Present(self.vitals.total());
        }
        self.deadeye_scene
            .health
            .as_ref()
            .and_then(|health| health.get(&entity).copied())
            .or_else(|| {
                self.deadeye_scene
                    .targets
                    .iter()
                    .find(|target| target.entity == entity)
                    .map(|target| target.health)
            })
            .map_or(HealthQuery::Unavailable, HealthQuery::Present)
    }

    fn entity_health(
        &self,
        site: Site,
        entity: u32,
        kind: Option<PoolKind>,
        maximum: bool,
    ) -> Option<f64> {
        if entity == self.entity {
            return self.body_health(site, kind, maximum);
        }
        if self.has_deadeye()
            && self.is_player_site(site)
            && site.graph == DEADEYE_TARGET_EFFECT
            && kind.is_none()
            && !maximum
        {
            return Some(
                self.deadeye_scene
                    .targets
                    .iter()
                    .find(|target| target.entity == entity)
                    .map_or(0.0, |target| target.health),
            );
        }
        None
    }

    fn add_health_pool(
        &mut self,
        site: Site,
        kind: PoolKind,
        maximum: f64,
        current: f64,
    ) -> Option<u32> {
        if !self.is_player_site(site) || !self.pool_types_known {
            return None;
        }
        self.vitals.add_pool(kind, maximum, current)
    }

    fn health_pool_current(&self, site: Site, pool: u32) -> Option<f64> {
        if !self.is_player_site(site) {
            return None;
        }
        self.vitals.pool_current(pool)
    }

    fn remove_health_pool(&mut self, site: Site, pool: u32) -> Option<f64> {
        if !self.is_player_site(site) {
            return None;
        }
        self.vitals.remove_pool(pool)
    }

    fn has_target_tag(&self, site: Site, entity: u32, tag: u64) -> Option<bool> {
        if !self.is_player_site(site) {
            return None;
        }
        if entity != self.entity {
            if let Some(damage) = &self.damage_credit {
                if damage.target == entity {
                    return damage.target_tags.presence(tag);
                }
            }
            return self
                .sighting_source
                .filter(|source| source.entity == entity)
                .and_then(|source| {
                    let index = SIGHTING_TAGS.iter().position(|&known| known == tag)?;
                    source.tags[index]
                })
                .or_else(|| {
                    self.deadeye_scene
                        .view_subjects
                        .get(&entity)
                        .and_then(|subject| subject.tags.presence(tag))
                });
        }
        if let Some(present) = self.target_tag_effects.presence(tag) {
            return Some(present);
        }
        Some(self.target_tags.as_ref()?.contains(&tag))
    }

    fn set_target_tag_effect(&mut self, site: Site, effect: TargetTagEffect, active: bool) -> bool {
        if !self.is_player_site(site) || effect.entity != self.entity {
            return false;
        }
        self.target_tag_effects.apply(effect, active)
    }

    fn entity_filter_flags(&self, site: Site, entity: u32) -> Option<u64> {
        if !self.is_player_site(site) {
            return None;
        }
        if entity == self.entity {
            Some(self.own_filter_flags())
        } else {
            if let Some(damage) = &self.damage_credit {
                if damage.target == entity {
                    return damage.target_flags;
                }
            }
            self.sighting_source
                .filter(|source| source.entity == entity)?
                .filter_flags
        }
    }

    fn scene_filter_policy(
        &self,
        site: Site,
        filter: &Json,
    ) -> Option<crate::script_expr::SceneFilterPolicy> {
        use crate::script_expr::{FilterPredicate, FilterRelation, SceneFilterPolicy};
        (self.charge_site(site)
            && self.damage_credit.is_some()
            && filter["m_66747E7D"] == 2
            && filter
                .get("m_89831602")
                .is_none_or(|v| v.is_null() || v == 0)
            && filter.get("m_condition").is_none_or(Json::is_null))
        .then_some(SceneFilterPolicy {
            relation: FilterRelation::Hostile,
            predicate: FilterPredicate::None,
        })
    }
    fn entities_hostile(&self, site: Site, source: u32, target: u32, option: bool) -> Option<bool> {
        if !self.charge_site(site) || source != self.entity || option {
            return None;
        }
        let damage = self
            .damage_credit
            .as_ref()
            .filter(|event| event.target == target)?;
        let left = self.own_filter_flags();
        let right = damage.target_flags?;
        crate::junkenstein_host::ordinary_hostility(left, right)
    }
    fn body_on_fire(&mut self, site: Site, entity: u32) -> Option<OnFire> {
        self.related_entity(site, EntityRelation::CurrentPossessor, entity)
            .map(|_| OnFire::Active(self.controller_on_fire))
    }

    fn related_entity(&mut self, site: Site, relation: EntityRelation, entity: u32) -> Option<u32> {
        if !self.is_player_site(site)
            || !valid_entity(self.controller)
            || self.controller == self.entity
        {
            return None;
        }
        // Both relations point to the same controller for this human body.
        match relation {
            EntityRelation::OwningController | EntityRelation::CurrentPossessor
                if entity == self.entity =>
            {
                Some(self.controller)
            }
            EntityRelation::PossessedBody if entity == self.controller => Some(self.entity),
            EntityRelation::ControllerBodyField if entity == self.controller => Some(self.entity),
            EntityRelation::ControllerBodyField
                if entity == self.entity || !valid_entity(entity) =>
            {
                Some(0)
            }
            // The authenticated human body has no AI control entity in this world.
            EntityRelation::AiControlEntity if entity == self.entity => Some(0),
            _ => None,
        }
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        if self.hero == CASSIDY
            && self.is_player_site(site)
            && site.graph == 0x01f0
            && matches!(site.node, Some(8 | 11 | 19))
            && config
                .get("m_entity")
                .is_some_and(|entity| native_shape(entity, "STU_B5A0CAF0", None))
        {
            let yaw = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
            let (sin, cos) = yaw.sin_cos();
            let vector = match config.get("$").and_then(Json::as_str) {
                Some("STU_021D01CD") => Some([sin, 0.0, cos]),
                Some("STU_66F3B89E") => Some([cos, 0.0, -sin]),
                Some("STU_2200A66D") if site.node == Some(19) => Some(aim_direction(&Command {
                    yaw: self.movement.yaw,
                    pitch: self.movement.pitch,
                    ..Command::default()
                })),
                _ => None,
            };
            if let Some(vector) = vector {
                return Some(Value::Vector(vector.map(|v| f64::from(v as f32))));
            }
        }

        if self.is_player_site(site) && self.has_deadeye() {
            match (
                site.graph,
                site.node,
                config.get("$").and_then(Json::as_str),
            ) {
                (DEADEYE_GRAPH, Some(84), Some("STU_ED45C5E8")) if is_owner_selector(config) => {
                    return Some(Value::Null);
                }
                (DEADEYE_TARGET_EFFECT, Some(28), Some("STU_7C005F8F"))
                    if crate::script_expr::native_fields(config, &["m_82B6345B", "m_entity"])
                        && config
                            .get("m_82B6345B")
                            .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None))
                        && config
                            .pointer("/m_entity/m_identifier")
                            .and_then(Json::as_u64)
                            == Some(536) =>
                {
                    // The prepared Junkenstein roster has no Deadeye immunity overrides.
                    return Some(Value::Bool(false));
                }
                _ => (),
            }
        }
        if self.is_player_site(site) && is_junkenstein_mode(self.mode) {
            if (site.graph, site.node) == (0x01ca, Some(179))
                && native_shape(config, "STU_E72F6348", Some("m_entity"))
                && config
                    .get("m_entity")
                    .is_some_and(|mode| native_shape(mode, "STU_A3DEF368", None))
            {
                return Some(Value::Bool(self.graphs.mode_variables(self.mode).is_some()));
            }
            if (site.graph, site.node) == (0x01ca, Some(126))
                && junkenstein_messages::sighting_selector(config)
            {
                return Some(junkenstein_messages::recipients(&self.defenders));
            }
            if matches!(
                (site.graph, site.node),
                (0x01ca, Some(11 | 127)) | (0x0ca5, Some(77))
            ) && junkenstein_messages::self_team_query(config)
            {
                let team = (self.body_filter >> 23) & 31;
                return Some(Value::Integer(if team == 0 {
                    -1
                } else {
                    i64::from(team.trailing_zeros())
                }));
            }
        }
        match config.get("$")?.as_str()? {
            "STU_1FF67CA6" => Some(Value::Entity(self.entity)),
            "STU_310D6529" => {
                const THROTTLE_UNIT: f32 = 1.0 / 127.0;
                let [right, forward] = self
                    .throttles
                    .map(|byte| f64::from(f32::from(byte) * THROTTLE_UNIT));
                Some(Value::Vector([right, forward, 0.0]))
            }
            "STU_37F20D24" => Some(Value::Asset(self.mode)),
            "STU_A3DEF368" if self.graphs.mode_variables(self.mode).is_some() => {
                Some(Value::Entity(GAME_MODE_ENTITY))
            }
            "STU_4A101787" => Some(Value::Bool(false)),
            "STU_ED45C5E8"
                if (site.graph, site.node, self.mode)
                    == (0x01ca, Some(126), PRACTICE_RANGE_MODE)
                    && self.is_player_site(site)
                    && config
                        .get("m_B2ABF4FF")
                        .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None))
                    && config.pointer("/m_2BA6230F/m_value").and_then(Json::as_f64)
                        == Some(0.0)
                    && ["m_5912C8DF", "m_5A72EEF5", "m_C9E6F46B"]
                        .iter()
                        .all(|flag| {
                            config
                                .get(*flag)
                                .and_then(|flag| flag.get("m_value"))
                                .and_then(Json::as_u64)
                                == Some(0)
                        }) =>
            {
                // The solo Practice Range has no recipients for this voice route.
                Some(Value::Array(Vec::new()))
            }
            "STU_65ABFCCC"
                if (site.graph, site.node, self.mode)
                    == (0x01ca, Some(127), PRACTICE_RANGE_MODE)
                    && self.is_player_site(site)
                    && config
                        .get("m_target")
                        .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None)) =>
            {
                Some(Value::Null)
            }
            "STU_A77AC2AC"
                if self.is_player_site(site)
                    && self.mode != 0
                    && native_shape(config, "STU_A77AC2AC", None) =>
            {
                Some(Value::Bool(self.mode == TUTORIAL_MODE))
            }
            // The reference has no model for this entity selector and
            // defaults it to None. It never reaches the 01CA site where the
            // owner VM meets it, and that graph handles the message it sends
            // to the selection, so None probably drops a local delivery. Only
            // the all-false form around the owner is answered; the original
            // selection is unverified.
            "STU_ED45C5E8"
                if self.is_player_site(site)
                    && self.mode == PRACTICE_RANGE_MODE
                    && is_owner_selector(config) =>
            {
                Some(Value::Null)
            }
            _ => None,
        }
    }
}

/// Whether every operand of an entity selector is the owner or a zero
/// literal, with exactly one owner operand.
fn is_owner_selector(config: &Json) -> bool {
    let mut owners = 0;
    config.as_object().is_some_and(|fields| {
        fields.iter().all(|(key, value)| match key.as_str() {
            "$" => true,
            "m_EE729DCB" => value.as_u64() == Some(0),
            _ if native_shape(value, "STU_B5A0CAF0", None) => {
                owners += 1;
                true
            }
            _ => {
                ["STUConfigVarBool", "STUConfigVarFloat"]
                    .into_iter()
                    .any(|class| native_shape(value, class, Some("m_value")))
                    && value["m_value"].as_f64() == Some(0.0)
            }
        })
    }) && owners == 1
}

impl BodyWorld {
    fn charge_site(&self, site: Site) -> bool {
        self.is_player_site(site) && self.has_deadeye() && site.graph == 0x0b8b
    }
    fn has_deadeye(&self) -> bool {
        self.hero == CASSIDY && is_junkenstein_mode(self.mode)
    }

    fn own_filter_flags(&self) -> u64 {
        let mut flags = self.body_filter;
        if is_junkenstein_mode(self.mode) {
            flags |= 1;
            if self.vitals.death().is_some() {
                flags |= 0x10;
            }
        }
        flags
    }

    fn is_player_site(&self, site: Site) -> bool {
        site.entity == self.entity && valid_entity(self.entity)
    }
}

fn unsupported(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::Unsupported, error.to_string())
}

#[cfg(test)]
pub(crate) fn fixture_body_factory() -> (PlayerBodyFactory, OwnerBodySpawn) {
    use serde_json::json;

    let body = 0x0400_0000_0000_0001;
    let hero = Hero {
        guid: SOLDIER,
        name: "Fixture".into(),
        role: "Damage".into(),
        body,
    };
    let graph = json!({
        "index":42,"items":0,"nodes":[],"states":[],"entries":[],"remote":[],
        "sync_vars":[],"containers":{},"unknown_nodes":[],"fields":{},
        "header":{"m_nodesBitCount":4,"m_statesBitCount":4,
            "m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}
    });
    let bundle = json!({"version":1,"missing":[],"graphs":[graph],"bodies":{
        format!("0x{body:016X}"): {"hero": format!("0x{SOLDIER:016X}"), "name":"Fixture",
            "graphs":[{"graph":42,"m_75408640":0}],"manager":42,"weapons":[null,42]}
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
        quanta: RETAIL_QUANTA,
    };
    let factory = PlayerBodyFactory::prepare(
        graphs,
        codecs,
        PRACTICE_RANGE_MODE,
        &hero,
        None,
        false,
        spawn,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn castle_summoner_deadeye_sightlines_follow_real_collision() {
        use crate::game_bots::{BotCatalog, BotWalk};
        use crate::game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog};
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = crate::scene_assets::SceneAssets::load(&runtime).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        let hero = scene.heroes().find_hero("Symmetra").unwrap();
        let catalog=BotCatalog::from_json(&serde_json::json!({
            "heroes":{"0x02E0000000000016":{"name":"Sightline target","body":format!("0x{:016X}",hero.body),"health":1000}},
            "maps":{"0x03":{"hints":{},"spawners":[{"hero":"0x02E0000000000016","team":1,
                "position":[61.984676361083984,18.126224517822266,-103.21149444580078],
                "rotation":[0,0,0,1],"graphs":[]}]}}
        }).to_string()).unwrap();
        let bots = catalog.make(3, |_| BotWalk::default());
        let pose = scene.bot_simulation().1.target(&bots[0]);
        let mut selection = scene.deadeye_scene(0).unwrap();
        selection.targets.push(DeadeyeTarget {
            velocity: Some([0.0; 3]),
            script_state: Default::default(),
            entity: bots[0].entity,
            pose,
            health: 1000.0,
        });
        for [x, y, z] in [
            [87.013671875, 12.6474609375, -64.34765625],
            [79.775390625, 13.0693359375, -78.779296875],
        ] {
            let origin = [x, y + eye_height(CASSIDY, false), z];
            let direction = selection
                .shot_direction(origin, bots[0].entity)
                .expect("the actual auto-aim point is exposed above the balcony");
            let [a, b] = pose.segment();
            let reach = capsule_entry_distance(
                origin,
                direction,
                norm(target_delta(origin, pose)),
                a,
                b,
                pose.radius,
            )
            .unwrap();
            assert!(
                selection
                    .collision
                    .as_ref()
                    .unwrap()
                    .cast_ray(origin, direction, reach, MOVER_CATEGORY)
                    .unwrap()
                    .is_none()
            );
        }
    }

    #[test]
    fn shared_deadeye_aim_ray_visibility_contract() {
        use crate::collision_cache::{CollisionCache, Mesh, Triangle};
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/deadeye-aim-ray-visibility-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "deadeye.aim-ray-visibility.v1");
        assert_eq!(contract["target_build"], 104319);
        let origin: Vec3 = serde_json::from_value(contract["input"]["origin"].clone()).unwrap();
        let raw = &contract["input"]["target"];
        let pose = TargetPose {
            feet: serde_json::from_value(raw["feet"].clone()).unwrap(),
            height: raw["height"].as_f64().unwrap(),
            radius: raw["radius"].as_f64().unwrap(),
            eye: raw["eye"].as_f64().unwrap(),
        };
        for case in contract["input"]["cases"].as_array().unwrap() {
            let triangles: Vec<[[f32; 3]; 3]> =
                serde_json::from_value(case["triangles"].clone()).unwrap();
            let cache=CollisionCache {
                header:serde_json::from_value(serde_json::json!({"meshes":[[triangles.len()*3,triangles.len()]],"hulls":0,"models":0,"placements":0,"identifiers":[]})).unwrap(),
                meshes:vec![Mesh {vertices:triangles.iter().flatten().copied().collect(),
                    triangles:(0..triangles.len()).map(|index|Triangle {corners:[index as i32*3,index as i32*3+1,index as i32*3+2],wings:[-1;3],mask:MOVER_CATEGORY,surface:0}).collect()}],
                hulls:vec![],models:vec![],placements:vec![],
            };
            let selection = DeadeyeScene {
                health: None,
                collision: Some(Arc::new(
                    CollisionScene::movement_probe(&cache, &BTreeSet::new()).unwrap(),
                )),
                targets: vec![DeadeyeTarget {
                    entity: 0xc000_1000,
                    pose,
                    health: 1000.0,
                    velocity: None,
                    script_state: Default::default(),
                }],
                view_subjects: BTreeMap::new(),
            };
            let expected = case["expected"]["visible"].as_bool().unwrap();
            assert_eq!(
                selection.aim_point_visible(origin, pose),
                expected,
                "{}",
                case["name"]
            );
            assert_eq!(
                selection.shot_direction(origin, 0xc000_1000).is_some(),
                expected,
                "{}",
                case["name"]
            );
            assert_eq!(
                selection.select(origin, [0.0, 0.0, 1.0], 200.0, 180.0, true),
                if expected { vec![0xc000_1000] } else { vec![] },
                "{}",
                case["name"]
            );
        }
    }
    use crate::{
        collision_index::TriangleIndex, contact_world::ContactWorld,
        game_instance::TOURNAMENT_QUANTA, mover_settings::MoverSettings,
    };

    #[test]
    fn engine_table_is_available_only_on_the_encounter_owner_host() {
        use crate::script_expr::EngineSetting;
        let (factory, spawn) = fixture_body_factory();
        let mut runtime = factory.start(spawn).unwrap();
        let site = Site {
            entity: spawn.entity,
            instance: 1,
            graph: 42,
            node: Some(0),
        };
        assert_eq!(
            runtime.world.engine_setting(site, 0xe1c1_e402),
            EngineSetting::Unavailable
        );
        for mode in [
            crate::game_content::JUNKENSTEIN_REVENGE,
            crate::game_content::JUNKENSTEIN_ENDLESS,
        ] {
            runtime.world.mode = mode;
            assert_eq!(
                runtime.world.engine_setting(site, 0xe1c1_e402),
                EngineSetting::Present(Value::Integer(128))
            );
            assert_eq!(
                runtime.world.engine_setting(site, 0),
                EngineSetting::Missing
            );
        }
        runtime.world.mode = TUTORIAL_MODE;
        assert_eq!(
            runtime.world.engine_setting(site, 0xe1c1_e402),
            EngineSetting::Unavailable
        );
    }

    #[test]
    fn cassidy_junkenstein_rules_enable_fan_without_changing_other_hero_defaults() {
        let (factory, spawn) = fixture_body_factory();
        let mut runtime = factory.start(spawn).unwrap();
        let world = &mut runtime.world;
        world.hero = CASSIDY;
        world.mode = crate::game_content::JUNKENSTEIN_REVENGE;
        assert_eq!(world.ruleset(0x2819), Some(Value::Float(1.0)));
        assert_eq!(world.ruleset(0x290c), Some(Value::Bool(true)));
        assert_eq!(world.ruleset(0x290b), None);
        world.hero = SOLDIER;
        assert_eq!(world.ruleset(0x290c), None);
        world.hero = CASSIDY;
        world.mode = PRACTICE_RANGE_MODE;
        assert_eq!(world.ruleset(0x290c), None);
    }

    #[test]
    fn encounter_body_angles_follow_the_live_owner_pose() {
        use crate::script_expr::{AngleQuery, BodyAngle};
        let (factory, spawn) = fixture_body_factory();
        let mut runtime = factory.start(spawn).unwrap();
        let world = &mut runtime.world;
        world.mode = crate::game_content::JUNKENSTEIN_REVENGE;
        world.movement.yaw = 8192;
        world.movement.pitch = -8192;
        assert_eq!(
            world.body_angle(world.entity, BodyAngle::Yaw),
            AngleQuery::Present(45.0)
        );
        assert_eq!(
            world.body_angle(world.entity, BodyAngle::Pitch),
            AngleQuery::Present(-45.0)
        );
        assert_eq!(world.body_angle(0, BodyAngle::Yaw), AngleQuery::Unbound);
        assert_eq!(
            world.body_angle(world.entity + 1, BodyAngle::Yaw),
            AngleQuery::Unavailable
        );
        world.movement.yaw = 32768;
        assert_eq!(
            world.body_angle(world.entity, BodyAngle::Yaw),
            AngleQuery::Unavailable
        );
        world.mode = PRACTICE_RANGE_MODE;
        assert_eq!(
            world.body_angle(world.entity, BodyAngle::Pitch),
            AngleQuery::Unavailable
        );
    }

    #[test]
    fn roll_velocity_bridge_preserves_sampled_delta_and_rejects_foreign_owners() {
        use crate::mover::VelocityChange;
        let (factory, spawn) = fixture_body_factory();
        let mut runtime = factory.start(spawn).unwrap();
        runtime.world.hero = CASSIDY;
        runtime.world.movement.velocity = [3.0, 2.0, 4.0];
        let site = Site {
            entity: spawn.entity,
            instance: 6,
            graph: 0x01f0,
            node: Some(8),
        };
        assert!(
            runtime
                .world
                .edit_velocity(site, 10, Value::Vector([0.0, 2.0, 15.0]), false)
        );
        assert_eq!(
            runtime.take_velocity_changes(),
            vec![(11, VelocityChange::Delta([-3.0, 0.0, 11.0]))]
        );
        assert!(runtime.take_velocity_changes().is_empty());
        assert!(!runtime.world.edit_velocity(
            Site {
                entity: spawn.entity + 1,
                ..site
            },
            10,
            Value::Vector([0.0; 3]),
            false
        ));
        assert!(
            !runtime
                .world
                .edit_velocity(site, 10, Value::Vector([f64::INFINITY; 3]), false)
        );
        assert!(
            !runtime
                .world
                .edit_velocity(site, 10, Value::Float(5.5), true)
        );
        let cap = Site {
            node: Some(17),
            ..site
        };
        assert!(
            runtime
                .world
                .edit_velocity(cap, 11, Value::Float(5.5), true)
        );
        assert_eq!(
            runtime.take_velocity_changes(),
            vec![(12, VelocityChange::HorizontalCap(5.5))]
        );
    }

    #[test]
    fn player_natives_follow_the_verified_owner_and_override_shapes() {
        use crate::script_expr::EvalError;
        use serde_json::json;

        let spawn = fixture_body_factory().1;
        let mut runtime = fixture_hitscan_factory(20.0).start(spawn).unwrap();
        let evaluate = |body: &mut PlayerBodyRuntime, config: &Json| {
            body.component
                .evaluate_state_config(1, 0, config, &mut body.world)
        };
        let owner = json!({"$":"STU_B5A0CAF0","m_EE729DCB":0});
        let size = json!({"$":"STU_FDBB0B67","m_entity":owner});
        assert_eq!(evaluate(&mut runtime, &size), Ok(Value::Float(1.0)));
        for (value, supported) in [
            (Value::Integer(1), true),
            (Value::Float(1.0), true),
            (Value::Bool(true), false),
            (Value::Float(1.25), false),
            (Value::Null, true),
        ] {
            runtime
                .component
                .set_entity_variable(31_210, value, &mut runtime.world)
                .unwrap();
            assert_eq!(
                evaluate(&mut runtime, &size),
                if supported {
                    Ok(Value::Float(1.0))
                } else {
                    Err(EvalError::UnsupportedConfig("STU_FDBB0B67".into()))
                }
            );
        }
        let dynamic = json!({"$":"STUConfigVarDynamic","m_identifier":400,"m_60DB8F99":true});
        let dynamic_size = json!({"$":"STU_FDBB0B67","m_entity":dynamic});
        for (entity, supported) in [(spawn.entity, true), (spawn.entity + 1, false), (0, false)] {
            runtime
                .component
                .set_entity_variable(400, Value::Entity(entity), &mut runtime.world)
                .unwrap();
            assert_eq!(
                evaluate(&mut runtime, &dynamic_size).ok(),
                supported.then_some(Value::Float(1.0))
            );
        }

        let ai = json!({"$":"STU_DB742151","m_entity":owner,"m_EE729DCB":0});
        assert_eq!(evaluate(&mut runtime, &ai), Ok(Value::Entity(0)));
        let path = json!({"$":"STU_D3EB8D87","m_00903303":ai,"m_EE729DCB":0});
        assert_eq!(evaluate(&mut runtime, &path), Ok(Value::Bool(false)));
        let other_path =
            json!({"$":"STU_D3EB8D87","m_00903303":{"$":"STU_DB742151","m_entity":dynamic}});
        assert_eq!(
            evaluate(&mut runtime, &other_path),
            Err(EvalError::UnsupportedConfig("STU_DB742151".into()))
        );
        let branch = json!({"$":"STU_A77AC2AC","m_EE729DCB":0});
        assert_eq!(evaluate(&mut runtime, &branch), Ok(Value::Bool(false)));
        runtime.world.mode = TUTORIAL_MODE;
        assert_eq!(evaluate(&mut runtime, &branch), Ok(Value::Bool(true)));
        runtime.world.mode = PRACTICE_RANGE_MODE;
        let flag = |value| json!({"$":"STUConfigVarBool","m_EE729DCB":0,"m_value":value});
        let selector = json!({
            "$":"STU_ED45C5E8","m_EE729DCB":0,"m_range":{"$":"STUConfigVarFloat","m_value":0},
            "m_first":flag(0),"m_second":flag(0),"m_subject":owner
        });
        assert_eq!(evaluate(&mut runtime, &selector), Ok(Value::Null));
        let unsupported_selector = Err(EvalError::UnsupportedConfig("STU_ED45C5E8".into()));
        for (field, value) in [
            ("m_second", flag(1)),
            ("m_range", json!({"$":"STUConfigVarFloat","m_value":2.5})),
            ("m_subject", dynamic.clone()),
            ("m_other", owner.clone()),
            ("m_filter", Json::Null),
        ] {
            let mut variant = selector.clone();
            variant[field] = value;
            assert_eq!(
                evaluate(&mut runtime, &variant),
                unsupported_selector,
                "{field}"
            );
        }
        runtime.world.mode = 0;
        assert_eq!(
            evaluate(&mut runtime, &branch),
            Err(EvalError::UnsupportedConfig("STU_A77AC2AC".into()))
        );
        assert_eq!(evaluate(&mut runtime, &selector), unsupported_selector);
    }

    #[test]
    fn target_tag_effects_restore_authored_tags_and_remaining_grants() {
        let (mut factory, spawn) = fixture_body_factory();
        let authored = 0x0d60_0000_0000_0042;
        let tag = 0x0d60_0000_0000_0422;
        factory.target_tags = Some(BTreeSet::from([authored]));
        let world = &mut factory.start(spawn).unwrap().world;
        let site = Site {
            entity: spawn.entity,
            instance: 1,
            graph: 42,
            node: Some(1),
        };
        let read = |world: &BodyWorld, tag| world.has_target_tag(site, spawn.entity, tag);
        let grant = TargetTagEffect {
            entity: spawn.entity,
            tag,
            present: true,
        };
        let suppress = TargetTagEffect {
            present: false,
            ..grant
        };
        let mask_authored = TargetTagEffect {
            tag: authored,
            ..suppress
        };

        assert_eq!(read(world, authored), Some(true));
        assert!(world.set_target_tag_effect(site, mask_authored, true));
        assert_eq!(read(world, authored), Some(false));
        assert!(world.set_target_tag_effect(site, mask_authored, false));
        assert_eq!(read(world, authored), Some(true));

        assert_eq!(read(world, tag), Some(false));
        for _ in 0..2 {
            assert!(world.set_target_tag_effect(site, grant, true));
        }
        assert_eq!(read(world, tag), Some(true));
        for _ in 0..2 {
            assert!(world.set_target_tag_effect(site, suppress, true));
        }
        assert_eq!(read(world, tag), Some(false));
        assert!(!world.set_target_tag_effect(site, grant, true));
        assert!(world.set_target_tag_effect(site, grant, false));
        assert!(world.set_target_tag_effect(site, suppress, false));
        assert_eq!(read(world, tag), Some(false));
        assert!(world.set_target_tag_effect(site, suppress, false));
        assert_eq!(read(world, tag), Some(true));
        assert!(world.set_target_tag_effect(site, grant, false));
        assert_eq!(read(world, tag), Some(false));
        assert!(!world.set_target_tag_effect(site, grant, false));
    }

    #[test]
    fn target_tag_effects_keep_unknown_tags_and_replacement_independent() {
        let (factory, spawn) = fixture_body_factory();
        let tag = 0x0d60_0000_0000_0422;
        let site = Site {
            entity: spawn.entity,
            instance: 1,
            graph: 42,
            node: Some(1),
        };
        let read = |world: &BodyWorld| world.has_target_tag(site, spawn.entity, tag);
        let effect = TargetTagEffect {
            entity: spawn.entity,
            tag,
            present: true,
        };
        let mut runtime = factory.start(spawn).unwrap();
        assert_eq!(read(&runtime.world), None);
        let foreign = TargetTagEffect {
            entity: spawn.controller,
            ..effect
        };
        assert!(!runtime.world.set_target_tag_effect(site, foreign, true));
        assert!(runtime.world.set_target_tag_effect(site, effect, true));
        assert_eq!(read(&runtime.world), Some(true));
        assert_eq!(
            runtime.world.has_target_tag(site, spawn.entity, tag + 1),
            None
        );

        let replacement = factory
            .start(OwnerBodySpawn {
                frame: spawn.frame + 1,
                ..spawn
            })
            .unwrap();
        assert_eq!(read(&replacement.world), None);
        assert!(runtime.world.set_target_tag_effect(site, effect, false));
        assert_eq!(read(&runtime.world), None);
    }

    #[test]
    fn health_queries_read_the_authoritative_vitals() {
        use crate::script_expr::EvalError;
        use serde_json::json;

        let spawn = fixture_body_factory().1;
        let mut runtime = fixture_hitscan_factory(20.0).start(spawn).unwrap();
        let evaluate = |body: &mut PlayerBodyRuntime, config: &Json| {
            body.component
                .evaluate_state_config(1, 0, config, &mut body.world)
        };
        let owner = json!({"$": "STU_B5A0CAF0"});
        let current = json!({"$": "STU_32E6E4D8", "m_entity": owner});
        let maximum = json!({"$": "STU_7D7D2470", "m_entity": owner});
        assert_eq!(evaluate(&mut runtime, &current), Ok(Value::Float(200.0)));
        assert_eq!(evaluate(&mut runtime, &maximum), Ok(Value::Float(200.0)));
        assert!(!runtime.receive_damage(
            75.0,
            crate::vitals::Death {
                time: 0.0,
                frame: spawn.frame,
                killer: spawn.entity + 1,
            },
        ));
        assert_eq!(evaluate(&mut runtime, &current), Ok(Value::Float(125.0)));
        assert_eq!(evaluate(&mut runtime, &maximum), Ok(Value::Float(200.0)));

        let target = json!({"$": "STUConfigVarDynamic", "m_identifier": 400, "m_60DB8F99": true});
        let current = json!({"$": "STU_32E6E4D8", "m_entity": target});
        for entity in [spawn.entity + 1, 0] {
            runtime
                .component
                .set_entity_variable(400, Value::Entity(entity), &mut runtime.world)
                .unwrap();
            assert_eq!(
                evaluate(&mut runtime, &current),
                Err(EvalError::UnsupportedConfig("STU_32E6E4D8".into()))
            );
        }
    }

    #[test]
    fn typed_health_queries_read_the_live_distribution_after_damage() {
        use crate::script_expr::EvalError;
        use serde_json::json;

        let (_, spawn) = fixture_body_factory();
        let mut factory = fixture_hitscan_factory(20.0);
        let mixed = HealthPools {
            health: 200.0,
            armour: 50.0,
            shields: 25.0,
        };
        let evaluate = |body: &mut PlayerBodyRuntime, config: &Json| {
            body.component
                .evaluate_state_config(1, 0, config, &mut body.world)
        };
        let query = |class, kind| {
            json!({
                "$":class, "m_entity":{"$":"STU_B5A0CAF0"}, "m_7BE02F89":null,
                "m_7E8B666C":{"$":"STU_297E1A30","m_7E8B666C":kind,"m_EE729DCB":0}
            })
        };
        let current = "STU_783B4CF0";
        let maximum = "STU_68883EAC";
        let unsupported = |class: &str| Err(EvalError::UnsupportedConfig(class.into()));

        let mut unknown = factory.start(spawn).unwrap();
        assert_eq!(
            evaluate(&mut unknown, &query(current, 0)),
            unsupported(current)
        );
        factory.pools = Some(FALLBACK_POOLS);
        let mut single = factory.start(spawn).unwrap();
        let hit = crate::vitals::Death {
            time: 0.0,
            frame: spawn.frame,
            killer: spawn.entity + 1,
        };
        single.world.vitals.damage(75.0, hit);
        for (kind, amount) in [(0, 125.0), (1, 0.0), (2, 0.0)] {
            assert_eq!(
                evaluate(&mut single, &query(current, kind)),
                Ok(Value::Float(amount))
            );
        }

        factory.pools = Some(mixed);
        for id in [253, 1732, 1737] {
            factory.variables.insert(id, Value::Float(mixed.total()));
        }
        let mut body = factory.start(spawn).unwrap();
        for (kind, amount) in [(0, 200.0), (1, 50.0), (2, 25.0)] {
            for class in [current, maximum] {
                assert_eq!(
                    evaluate(&mut body, &query(class, kind)),
                    Ok(Value::Float(amount))
                );
            }
        }
        assert!(body.take_health_update().is_none());
        assert_eq!(body.world.vitals.damage(30.0, hit).dealt, 27.5);
        for (kind, amount) in [(0, 200.0), (1, 47.5), (2, 0.0)] {
            assert_eq!(
                evaluate(&mut body, &query(current, kind)),
                Ok(Value::Float(amount))
            );
        }
        assert_eq!(
            evaluate(&mut body, &query(maximum, 2)),
            Ok(Value::Float(25.0))
        );
        assert_eq!(
            body.take_health_update(),
            Some(body.world.vitals.component())
        );
        assert!(body.take_health_update().is_none());
        assert!(body.world.vitals.damage(1000.0, hit).killed);
        for kind in [0, 1, 2] {
            assert_eq!(
                evaluate(&mut body, &query(current, kind)),
                Ok(Value::Float(0.0))
            );
        }
        let mut named = query(current, 0);
        named["m_7BE02F89"] = json!({"$":"STUConfigVarIdentifier","m_identifier":"0x01"});
        let mut foreign = query(current, 0);
        foreign["m_entity"] = json!({"$":"STU_F76C486C","m_D63CBEC2":{"$":"STU_B5A0CAF0"}});
        for config in [named, query(current, 3), foreign] {
            assert_eq!(evaluate(&mut single, &config), unsupported(current));
        }
    }

    #[test]
    fn entity_relations_resolve_only_the_active_player_pair() {
        use crate::script_expr::EvalError;
        use serde_json::json;

        let (_, mut spawn) = fixture_body_factory();
        let factory = fixture_hitscan_factory(20.0);
        spawn.controller = 0xa012_3400;
        let replacement = OwnerBodySpawn {
            entity: 0xa012_7856,
            ..spawn
        };
        let mut first = factory.start(spawn).unwrap();
        let mut second = factory.start(replacement).unwrap();
        let target = json!({"$":"STUConfigVarDynamic","m_identifier":400,"m_60DB8F99":true});
        let owner = json!({"$":"STU_F76C486C","m_D63CBEC2":target,"m_EE729DCB":0});
        let possessor = json!({"$":"STU_F7262B36","m_801B298F":target});
        let body = json!({"$":"STU_0AC15F78","m_entity":target});
        let invalid_owner = json!({"$":"STU_F76C486C","m_D63CBEC2":target,"m_EE729DCB":1});
        let evaluate = |runtime: &mut PlayerBodyRuntime, config: &Json, target: Value| {
            runtime
                .component
                .set_entity_variable(400, target, &mut runtime.world)
                .unwrap();
            runtime
                .component
                .evaluate_state_config(1, 0, config, &mut runtime.world)
        };
        let unsupported_owner = || Err(EvalError::UnsupportedConfig("STU_F76C486C".into()));
        let unsupported_body = || Err(EvalError::UnsupportedConfig("STU_0AC15F78".into()));
        assert_eq!(
            evaluate(&mut first, &owner, Value::Entity(spawn.entity)),
            Ok(Value::Entity(spawn.controller))
        );
        assert_eq!(
            evaluate(&mut first, &possessor, Value::Entity(spawn.entity)),
            Ok(Value::Entity(spawn.controller))
        );
        assert_eq!(
            evaluate(&mut first, &body, Value::Entity(spawn.controller)),
            Ok(Value::Entity(spawn.entity))
        );
        assert_eq!(
            evaluate(&mut second, &body, Value::Entity(spawn.controller)),
            Ok(Value::Entity(replacement.entity))
        );
        assert_eq!(
            evaluate(&mut second, &owner, Value::Entity(replacement.entity)),
            Ok(Value::Entity(spawn.controller))
        );
        assert_eq!(
            evaluate(&mut second, &possessor, Value::Entity(replacement.entity)),
            Ok(Value::Entity(spawn.controller))
        );
        for entity in [spawn.entity, spawn.controller, 0] {
            assert_eq!(
                evaluate(&mut second, &owner, Value::Entity(entity)),
                unsupported_owner()
            );
        }
        assert_eq!(
            evaluate(&mut second, &body, Value::Entity(replacement.entity)),
            unsupported_body()
        );
        assert_eq!(
            evaluate(&mut second, &possessor, Value::Entity(spawn.entity)),
            Err(EvalError::UnsupportedConfig("STU_F7262B36".into()))
        );
        assert_eq!(
            evaluate(
                &mut second,
                &body,
                Value::Integer(i64::from(spawn.controller))
            ),
            unsupported_body()
        );
        assert_eq!(
            evaluate(
                &mut second,
                &invalid_owner,
                Value::Entity(replacement.entity)
            ),
            unsupported_owner()
        );
    }

    #[test]
    fn verified_body_starts_fresh_instances_and_advances_only_its_own_retail_clock() {
        let (factory, spawn) = fixture_body_factory();
        assert!(
            factory
                .start(OwnerBodySpawn {
                    quanta: TOURNAMENT_QUANTA,
                    ..spawn
                })
                .is_err()
        );
        let mut first = factory.start(spawn).unwrap();
        let second = factory
            .start(OwnerBodySpawn {
                entity: spawn.entity + 1,
                ..spawn
            })
            .unwrap();
        let snapshot = first.snapshot().unwrap();
        assert_eq!(
            snapshot.instances.keys().copied().collect::<Vec<_>>(),
            [1, 2, 3]
        );
        assert_eq!(snapshot.instances[&3].variables[&28], Value::Integer(1));
        assert_eq!(snapshot.variables[&1732], Value::Float(200.0));
        assert_eq!(first.movement_modifiers().map(BTreeMap::len), Some(0));
        assert_eq!(
            first.world.position(spawn.entity),
            Some(spawn.pose.position)
        );
        assert_eq!(first.world.movement, spawn.pose);
        assert_eq!(second.snapshot().unwrap().entity, spawn.entity + 1);

        let site = Site {
            entity: spawn.entity,
            instance: 1,
            graph: 42,
            node: None,
        };
        assert_eq!(first.world.movement_sample(site), Some(2));
        assert_eq!(second.world.movement_sample(site), None);
        let mut mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            spawn.pose.position,
            0,
            0x18,
        )
        .unwrap();
        let command = Command {
            frame: 101,
            right: -63,
            forward: 64,
            ..Command::default()
        };
        mover
            .step(
                &TriangleIndex::new([]).unwrap(),
                command,
                0.016,
                |mover, command| first.advance(command, mover, BodyStep::Posed).unwrap(),
            )
            .unwrap();
        assert_eq!(
            first.world.position(spawn.entity),
            Some(mover.pose().position)
        );
        let posed = mover.pose();
        let sample = sample_movement_flags(&posed).expect("a regular posed movement record");
        assert_eq!(first.world.movement, posed);
        assert_eq!(first.world.movement_sample(site), Some(sample));
        assert_eq!(first.component.now_ms(), 102 * FRAME_MS - 1);
        assert_eq!(second.component.now_ms(), 101 * FRAME_MS - 1);
        let gap = Command {
            frame: 104,
            ..command
        };
        assert!(first.advance(gap, &mover, BodyStep::Posed).is_err());
        assert_eq!(first.component.now_ms(), 102 * FRAME_MS - 1);
        let unposed_mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [4.0, 5.0, 6.0],
            0,
            0x18,
        )
        .unwrap();
        first
            .advance(
                Command {
                    frame: 102,
                    ..Command::default()
                },
                &unposed_mover,
                BodyStep::UnposedBoundary,
            )
            .unwrap();
        assert_eq!(first.world.movement, posed);
        first
            .advance(
                Command {
                    frame: 200,
                    ..Command::default()
                },
                &unposed_mover,
                BodyStep::UnposedTimer,
            )
            .unwrap();
        assert_eq!(first.world.movement, posed);
        first
            .advance(
                Command {
                    frame: 201,
                    ..Command::default()
                },
                &mover,
                BodyStep::UnposedBoundary,
            )
            .unwrap();
        assert_eq!(first.world.throttles, [0, 0]);
        assert_eq!(first.component.now_ms(), 202 * FRAME_MS - 1);
    }

    #[test]
    fn body_world_queries_only_expose_its_body_and_available_mode_variable_table() {
        use serde_json::json;

        let (factory, spawn) = fixture_body_factory();
        let mut world = factory.start(spawn).unwrap().world;
        assert_eq!(world.position(spawn.entity), Some(spawn.pose.position));
        world.position = [0.0; 3];
        assert_eq!(world.position(spawn.entity), Some([0.0; 3]));
        for foreign in [0, spawn.controller, spawn.entity + 1, GAME_MODE_ENTITY] {
            assert_eq!(world.position(foreign), None);
        }
        // The initial fixture has no mode table; it cannot claim an unset
        // value for a mode it knows nothing about.
        assert_eq!(world.entity_variable(GAME_MODE_ENTITY, 1), None);
        world.graphs = Arc::new(
            GraphStore::from_json(
                &serde_json::to_vec(&json!({
                    "version":1,"graphs":[],"bodies":{},"missing":[],
                    "mode_variables":{format!("0x{:016X}", world.mode):{"1":42}}
                }))
                .unwrap(),
            )
            .unwrap(),
        );
        assert_eq!(
            world.entity_variable(GAME_MODE_ENTITY, 1),
            Some(Value::Integer(42))
        );
        assert_eq!(
            world.entity_variable(GAME_MODE_ENTITY, 2),
            Some(Value::Null)
        );
        for foreign in [0, spawn.controller, spawn.entity, spawn.entity + 1] {
            assert_eq!(world.entity_variable(foreign, 1), None);
        }
        world.mode += 1;
        assert_eq!(world.entity_variable(GAME_MODE_ENTITY, 1), None);
    }

    #[test]
    fn posed_volley_keeps_the_authored_shot_frame_and_drains_unposed_reports() {
        let factory = fixture_hitscan_factory(20.0);
        let (_, spawn) = fixture_body_factory();
        let mut runtime = factory.start(spawn).unwrap();
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            spawn.pose.position,
            0,
            0x18,
        )
        .unwrap();
        let fire = Command {
            frame: 101,
            buttons: crate::commands::FIRE,
            ..Command::default()
        };
        runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
        let command = Command {
            frame: 102,
            yaw: 12,
            pitch: 30,
            ..Command::default()
        };
        let shots = runtime.take_shots(Some(command), &mover);
        let [PosedShot::Hitscan(shot)] = shots.as_slice() else {
            panic!("expected the opening shot")
        };
        assert_eq!(shot.source, spawn.entity);
        assert_eq!(shot.origin, [1.0, 3.55, 3.0]);
        assert_eq!(shot.shot_frame, 101);
        assert_eq!(shot.command, command);
        assert_eq!(
            (shot.reach, shot.hit.amount, shot.hit.critical),
            (180.0, 20.0, 2.0)
        );
        assert!(runtime.take_shots(Some(command), &mover).is_empty());

        runtime
            .advance(command, &mover, BodyStep::UnposedBoundary)
            .unwrap();
        assert_eq!(
            runtime.snapshot().unwrap().instances[&1].states[&0].kind,
            crate::script_init::StateKind::LogicalButton { down: false }
        );
        let timer = Command {
            frame: 107,
            ..Command::default()
        };
        runtime
            .advance(timer, &mover, BodyStep::UnposedTimer)
            .unwrap();
        assert!(runtime.take_shots(None, &mover).is_empty());
        assert!(runtime.take_shots(Some(timer), &mover).is_empty());
    }

    #[test]
    fn secondary_volley_uses_authored_projectile_and_owner_lease() {
        let factory = fixture_projectile_factory(40.0);
        let (_, mut spawn) = fixture_body_factory();
        spawn.slot = 2;
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            spawn.pose.position,
            0,
            0x18,
        )
        .unwrap();
        let fire = Command {
            frame: spawn.frame + 1,
            buttons: crate::commands::SECONDARY,
            yaw: 16384,
            ..Command::default()
        };
        let mut runtime = factory.start(spawn).unwrap();
        assert_eq!(
            runtime.projectile_leases().unwrap().entity(
                FIXTURE_PROJECTILE_DEFINITION,
                fire.frame,
                0
            ),
            Some(0xa210_0065)
        );
        runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
        let shots = runtime.take_shots(Some(fire), &mover);
        let [PosedShot::Projectile(rocket)] = shots.as_slice() else {
            panic!("expected a predicted rocket");
        };
        assert_eq!(
            (rocket.shot.entity, rocket.shot.frame),
            (spawn.entity, fire.frame)
        );
        assert_eq!((rocket.shot.instance, rocket.shot.raw_state), (1, 1));
        assert_eq!(rocket.origin, [1.0, 3.55, 3.0]);
        assert_eq!(rocket.direction, aim_direction(&fire));
        assert_eq!(
            rocket.ballistics.definition,
            Some(FIXTURE_PROJECTILE_DEFINITION)
        );
        assert_eq!(
            (rocket.ballistics.speed, rocket.ballistics.lifetime),
            (50.0, 2.0)
        );
        assert_eq!(rocket.ballistics.direct.unwrap().amount, 40.0);
        assert_eq!(rocket.ballistics.blast.as_ref().unwrap().hit.amount, 80.0);
        assert!(runtime.take_shots(Some(fire), &mover).is_empty());

        let encoder = runtime.encoder();
        let owner = encoder
            .project(runtime.snapshot().unwrap(), View::Owner)
            .unwrap();
        assert_eq!(
            owner.volley_identity(1, 1),
            Some((rocket.shot.activation, rocket.shot.activation_ms))
        );
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Flashbang effect graphs"]
    fn authored_flashbang_duration_follows_both_mode_branches() {
        use serde_json::json;
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap();
        let config = flashbang_duration(&graphs).unwrap();
        for (flag, ticks) in [(false, 50), (true, 107)] {
            let (_, spawn) = fixture_body_factory();
            let mut runtime = fixture_volley_factory(29, true, fixture_projectile_fields(40.0))
                .start(spawn)
                .unwrap();
            let mode = format!("0x{:016X}", runtime.world.mode);
            runtime.world.graphs = Arc::new(
                GraphStore::from_json(
                    &serde_json::to_vec(&json!({
                        "version":1,"graphs":[],"bodies":{},"missing":[],
                        "mode_variables":{mode:{"13408":flag}}
                    }))
                    .unwrap(),
                )
                .unwrap(),
            );
            let duration = runtime
                .component
                .evaluate_state_config(1, 1, config, &mut runtime.world)
                .unwrap();
            assert_eq!(
                Stun::from_seconds(to_float(&duration)).unwrap().ticks,
                ticks
            );
        }
    }

    #[test]
    fn unsupported_projectile_modes_do_not_become_hitscan_or_straight_damage() {
        use serde_json::json;

        let (_, spawn) = fixture_body_factory();
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            spawn.pose.position,
            0,
            0x18,
        )
        .unwrap();
        let fire = Command {
            frame: spawn.frame + 1,
            buttons: crate::commands::SECONDARY,
            ..Command::default()
        };
        for (field, value) in [
            ("m_45271E8C", json!(false)),
            ("m_projectileMotions", json!([{"$":"STU_E1A5B579"}])),
            ("m_numProjectilesPerShot", json!(2)),
            ("m_6396149F", json!({"m_modifyHealth":{"m_amount":-1.0}})),
        ] {
            let mut fields = fixture_projectile_fields(40.0);
            fields[field] = value;
            let mut runtime = fixture_volley_factory(29, true, fields)
                .start(spawn)
                .unwrap();
            runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
            assert!(runtime.take_shots(Some(fire), &mover).is_empty(), "{field}");
        }
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 statescript catalog"]
    fn prepared_soldier_starts_the_reference_body_and_weapon_tree() {
        use crate::{
            game_clock::FIRST_TICK, game_content::GameMap, script_codecs::PayloadMode,
            script_init::StateKind,
        };
        use std::path::PathBuf;

        let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
        let pools = crate::game_pools::PoolCatalog::from_graphs(&graphs)
            .unwrap()
            .hero_pools(SOLDIER)
            .unwrap();
        let body = &graphs.bodies()[&0x0400_0000_0000_03cf];
        let roots = crate::script_init::player_roots(body, &graphs).unwrap();
        let health = pools.total();
        let variables = crate::script_init::player_entity_variables(body, health, health);
        let mut world = BodyWorld {
            graphs: Arc::clone(&graphs),
            controller: 0xa000_0100,
            entity: 0xa000_0101,
            body_filter: crate::match_scene::team_bit(0).unwrap(),
            position: GameMap::practice_range().spawn_point(0, 0).unwrap().0,
            movement: Movement::default(),
            throttles: [0; 2],
            mode: PRACTICE_RANGE_MODE,
            hero: SOLDIER,
            vitals: Vitals::empty(),
            pool_types_known: true,
            target_tags: body.target_tags.clone(),
            target_tag_effects: TargetTagEffects::default(),
            controller_on_fire: false,
            defenders: Arc::default(),
            sightings: Vec::new(),
            sighting_source: None,
            cinematic_aim: None,
            deadeye_scene: DeadeyeScene::default(),
            velocity_changes: Vec::new(),
            damage_credit: None,
        };
        let mut component = Component::start(
            graphs,
            world.entity,
            variables,
            roots,
            FIRST_TICK,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.advance_frame(FIRST_TICK, &mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        for diagnostic in &snapshot.diagnostics {
            eprintln!(
                "{:04X}:{:?} {:?}",
                diagnostic.site.graph, diagnostic.site.node, diagnostic.issue
            );
        }
        // The frozen BodyScript replay allocates the same IDs with the team
        // mask. The exit retires 08B6 (9) and its unstarted child 0BF8 (25).
        let expected = [
            (1, 0x0033, None),
            (2, 0x004b, None),
            (3, 0x0043, None),
            (4, 0x0251, None),
            (5, 0x0255, None),
            (6, 0x0257, None),
            (7, 0x0259, None),
            (8, 0x091b, None),
            (10, 0x0015, None),
            (11, 0x0254, None),
            (12, 0x01c7, Some((2, 0))),
            (13, 0x01cf, Some((2, 3))),
            (14, 0x02c5, Some((2, 5))),
            (15, 0x0b8b, Some((2, 10))),
            (16, 0x0c7d, Some((2, 18))),
            (17, 0x01ca, Some((2, 1))),
            (18, 0x0ba4, Some((2, 11))),
            (19, 0x05ac, Some((2, 8))),
            (20, 0x02cf, Some((2, 6))),
            (21, 0x0b3b, Some((2, 9))),
            (22, 0x01d8, Some((2, 4))),
            (23, 0x0f82, Some((2, 21))),
            (24, 0x05ad, Some((2, 7))),
            (26, 0x0e8f, Some((13, 32))),
        ];
        let instances: Vec<_> = snapshot
            .instances
            .iter()
            .map(|(&id, instance)| (id, instance.graph, instance.parent))
            .collect();
        assert_eq!(instances, expected);
        let movement_graph = &snapshot.instances[&12];
        let movement = &movement_graph.states[&4];
        assert!(movement.active);
        assert_eq!(movement.kind, StateKind::MovementSample(2));
        assert_eq!(snapshot.variables[&545], Value::Bool(true));
        assert!(
            !snapshot
                .diagnostics
                .iter()
                .any(|item| item.site.graph == 0x01c7 && item.site.node == Some(7))
        );
        let body_pools = &snapshot.instances[&1];
        assert!(body_pools.states[&0].active);
        assert_eq!(
            body_pools.states[&0].kind,
            StateKind::HealthPool { pool: Some(1) }
        );
        assert_eq!(body_pools.variables[&520], Value::Float(200.0));
        assert_eq!(world.vitals.component(), pools.vitals().component());
        let target_tag = &snapshot.instances[&12].states[&31];
        let effect = TargetTagEffect {
            entity: world.entity,
            tag: 0x0d60_0000_0000_0422,
            present: true,
        };
        assert!(target_tag.active);
        assert_eq!(
            target_tag.kind,
            StateKind::TargetTag {
                effect: Some(effect)
            }
        );
        let site = Site {
            entity: world.entity,
            instance: 12,
            graph: 0x01c7,
            node: Some(96),
        };
        assert_eq!(
            world.has_target_tag(site, world.entity, effect.tag),
            Some(true)
        );
        assert!(
            !snapshot
                .diagnostics
                .iter()
                .any(|diagnostic| diagnostic.site == site)
        );
        let Value::Map(messages) = &snapshot.instances[&24].variables[&29859] else {
            panic!("the authored message table must be a map");
        };
        assert_eq!(messages.len(), 20);
        assert_eq!(
            messages[0],
            (
                Value::Asset(0x0a90_0000_0000_0171),
                Value::Asset(0x0240_0000_0000_05ad)
            )
        );
        assert_eq!(
            snapshot.instances[&1].variables[&9737],
            Value::Entity(world.entity)
        );
        assert!(
            !snapshot
                .diagnostics
                .iter()
                .any(|item| item.site.graph == 0x0033 && item.site.node == Some(51))
        );
        let watch = &snapshot.instances[&13].states[&21];
        assert!(watch.active);
        assert_eq!(watch.kind, crate::script_init::StateKind::Watch);
        assert!(
            !snapshot
                .diagnostics
                .iter()
                .any(|item| item.site.graph == 0x01cf && item.site.node == Some(41))
        );
        assert_eq!(snapshot.instances[&11].variables[&28], Value::Integer(1));
        for (instance, state) in [(10, 42), (10, 43), (11, 83), (13, 138), (13, 139), (14, 30)] {
            let controller = &snapshot.instances[&instance].states[&state];
            assert!(controller.active);
            assert_eq!(controller.kind, crate::script_init::StateKind::Plain);
        }
        let tips = &snapshot.instances[&4].states[&0];
        assert!(tips.active);
        assert_eq!(tips.kind, StateKind::Plain);
        let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
        for mode in [
            PayloadMode::Remote { full: true },
            PayloadMode::Owner {
                full: true,
                frame_time: i64::from(FIRST_TICK) * FRAME_MS as i64,
                frame_ms: FRAME_MS as u32,
            },
        ] {
            for class in [
                "STU_CD46AF93",
                "STU_F5A99B2A",
                "STU_D9843704",
                "STU_E56BA8D8",
            ] {
                let codec = codecs.codec(class).unwrap();
                assert_eq!(codec.payload(&BTreeMap::new(), mode).unwrap().bit_len(), 0);
            }
        }
        for (instance, raw, position) in [(1, 13, 39), (2, 22, 32), (2, 23, 33), (10, 39, 73)] {
            let instance = &snapshot.instances[&instance];
            let node = world
                .graphs
                .graph(instance.graph)
                .unwrap()
                .state(usize::from(raw))
                .unwrap();
            assert_eq!((node.pos, node.class.as_str()), (position, "STU_CD46AF93"));
            let state = &instance.states[&raw];
            assert!(state.active);
            assert_eq!(state.kind, StateKind::ForCode);
        }
        for (instance, raw) in [(1, 4), (10, 41)] {
            let child = &snapshot.instances[&instance].states[&raw];
            assert!(child.active);
            assert_eq!(child.kind, StateKind::BooleanSwitch(false));
        }
        let selection = &snapshot.instances[&18].states[&0];
        assert!(selection.active);
        assert_eq!(selection.kind, StateKind::BooleanSwitch(false));
        assert!(!snapshot.instances[&18].states.contains_key(&1));
        let on_fire = &snapshot.instances[&13].states[&17];
        assert!(on_fire.active);
        assert_eq!(on_fire.kind, StateKind::BooleanSwitch(false));
        assert!(!snapshot.diagnostics.iter().any(|diagnostic| {
            diagnostic.site.graph == 0x01cf && diagnostic.site.node == Some(31)
        }));
        for (graph, node) in [
            (0x0033, 1),
            (0x0033, 66),
            (0x004b, 19),
            (0x004b, 20),
            (0x08b6, 3),
            (0x08b6, 11),
            (0x0ba4, 10),
        ] {
            assert!(!snapshot.diagnostics.iter().any(|diagnostic| {
                diagnostic.site.graph == graph && diagnostic.site.node == Some(node)
            }));
        }
        let filter_diagnostic = snapshot
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.site.graph == 0x004b && diagnostic.site.node == Some(21));
        assert_eq!(filter_diagnostic, world.target_tags.is_none());
        // With tags, startup continues past the effect action at 01C7 node 74.
        assert_eq!(
            snapshot.initialization_complete(),
            world.target_tags.is_some()
        );
        for raw in [12, 13] {
            let filter = &snapshot.instances[&2].states[&raw];
            assert!(filter.active);
            assert_eq!(filter.kind, StateKind::BooleanSwitch(true));
        }
        if world.target_tags.is_some() {
            let filter = &snapshot.instances[&2].states[&14];
            assert!(filter.active);
            assert_eq!(filter.kind, StateKind::BooleanSwitch(false));
        }

        let authored_graphs = Arc::clone(&world.graphs);
        let authored_field = |graph, position, field: &str| {
            authored_graphs
                .graph(graph)
                .unwrap()
                .node(position)
                .unwrap()
                .field(field)
                .unwrap()
                .clone()
        };
        let sprint_condition = authored_field(0x0255, 6, "m_condition");
        let watch_possessor = authored_field(0x01cf, 41, "m_B7A3CF78");
        let shield_refill = authored_field(0x0033, 66, "m_BD83C8E6");
        let health_present = authored_field(0x08b6, 2, "m_condition");
        let filter = authored_field(0x004b, 20, "m_condition");
        let mask = world.body_filter;
        for (flags, passes) in [(mask, true), (mask | 2, false)] {
            world.body_filter = flags;
            assert_eq!(
                component.evaluate_state_config(2, 13, &filter, &mut world),
                Ok(Value::Bool(passes))
            );
        }
        world.body_filter = mask;
        let on_fire = authored_field(0x01cf, 31, "m_condition");
        for value in [false, true] {
            world.controller_on_fire = value;
            assert_eq!(
                component.evaluate_state_config(13, 17, &on_fire, &mut world),
                Ok(Value::Bool(value))
            );
        }
        world.controller_on_fire = false;
        let entries = authored_field(0x05ad, 240, "m_value");
        let mut message = authored_field(0x05ad, 183, "m_gameMessage");
        // Supply one authored key as a literal rather than simulating its UI input.
        message["m_configVars"] = serde_json::json!([entries["m_configVars"][0]["m_key"].clone()]);
        message["m_expression"]["m_opcodes"] = serde_json::json!([8, 0, 14, 0, 17, 0]);
        assert_eq!(
            component.evaluate_state_config(24, 0, &message, &mut world),
            Ok(Value::Asset(0x0240_0000_0000_05ad))
        );
        if let Some(original_tags) = world.target_tags.clone() {
            let filter = authored_field(0x004b, 21, "m_condition");
            let tag_query = authored_field(0x0043, 45, "m_condition");
            let required = crate::script_graph::target_tag_guid(&tag_query["m_88B0050A"]).unwrap();
            assert!(!original_tags.contains(&required));
            for present in [false, true] {
                if present {
                    world.target_tags.as_mut().unwrap().insert(required);
                }
                assert_eq!(
                    component.evaluate_state_config(2, 14, &filter, &mut world),
                    Ok(Value::Bool(present))
                );
                assert_eq!(
                    component.evaluate_state_config(3, 0, &tag_query, &mut world),
                    Ok(Value::Bool(present))
                );
            }
            world.target_tags = Some(original_tags);
        }
        let rifle_conditions =
            [81, 83].map(|position| authored_field(0x0254, position, "m_condition"));
        // 01C7 st48 reads the same current and maximum health as the
        // health-ratio branches in 01CF st7/st9.
        let full_health = authored_field(0x01c7, 145, "m_condition");
        assert_eq!(
            component.evaluate_state_config(13, 21, &watch_possessor, &mut world),
            Ok(Value::Entity(world.controller))
        );
        assert_eq!(
            component.evaluate_state_config(1, 27, &shield_refill, &mut world),
            Ok(Value::Bool(false))
        );
        assert_eq!(
            component.evaluate_state_config(12, 48, &health_present, &mut world),
            Ok(Value::Bool(true))
        );
        assert_eq!(
            component.evaluate_state_config(12, 48, &full_health, &mut world),
            Ok(Value::Bool(true))
        );
        // 0255 st2 follows the throttle comparison for a human player.
        for (forward, enabled) in [(63, false), (64, true)] {
            world.throttles = [0, forward];
            assert_eq!(
                component.evaluate_state_config(5, 2, &sprint_condition, &mut world),
                Ok(Value::Bool(enabled))
            );
        }
        // Both 0254 rifle branches have the same parameterless human condition.
        for condition in rifle_conditions {
            assert_eq!(
                component.evaluate_state_config(11, 10, &condition, &mut world),
                Ok(Value::Bool(false))
            );
        }
        world.vitals.damage(
            75.0,
            crate::vitals::Death {
                time: 0.0,
                frame: FIRST_TICK + 1,
                killer: world.entity + 1,
            },
        );
        component.advance_frame(FIRST_TICK + 1, &mut world).unwrap();
        let damaged = component.snapshot().unwrap();
        assert_eq!(damaged.variables[&253], Value::Float(125.0));
        assert_eq!(damaged.variables[&1732], Value::Float(125.0));
        assert_eq!(damaged.variables[&1737], Value::Float(200.0));
        assert_eq!(damaged.instances[&1].variables[&520], Value::Float(125.0));
        assert_eq!(
            component.evaluate_state_config(12, 48, &full_health, &mut world),
            Ok(Value::Bool(false))
        );
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 hero and graph catalogs"]
    fn authored_armour_and_shield_roots_reproduce_the_reference_body_pools() {
        use crate::game_clock::FIRST_TICK;
        use std::path::PathBuf;

        let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let heroes =
            crate::game_heroes::HeroCatalog::load(root.join("data/game_heroes_174.json")).unwrap();
        let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
        let catalog = crate::game_pools::PoolCatalog::from_graphs(&graphs).unwrap();
        for (hero, secondary_state) in [(0x02e0_0000_0000_0007, 3), (0x02e0_0000_0000_0068, 1)] {
            let body = &graphs.bodies()[&heroes.get(hero).unwrap().body];
            let pools = catalog.hero_pools(hero).unwrap();
            let roots = crate::script_init::player_roots(body, &graphs)
                .unwrap()
                .into_iter()
                .filter(|root| root.graph == BODY_GRAPH)
                .collect();
            let health = pools.total();
            let mut world = BodyWorld {
                graphs: Arc::clone(&graphs),
                controller: 0xa000_0100,
                entity: 0xa000_0101,
                body_filter: crate::match_scene::team_bit(0).unwrap(),
                position: [0.0; 3],
                movement: Movement::default(),
                throttles: [0; 2],
                mode: PRACTICE_RANGE_MODE,
                hero,
                vitals: Vitals::empty(),
                pool_types_known: true,
                target_tags: body.target_tags.clone(),
                target_tag_effects: TargetTagEffects::default(),
                controller_on_fire: false,
                defenders: Arc::default(),
                sightings: Vec::new(),
                sighting_source: None,
                cinematic_aim: None,
                deadeye_scene: DeadeyeScene::default(),
                velocity_changes: Vec::new(),
                damage_credit: None,
            };
            let component = Component::start(
                Arc::clone(&graphs),
                world.entity,
                crate::script_init::player_entity_variables(body, health, health),
                roots,
                FIRST_TICK,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(!snapshot.diagnostics.iter().any(|item| {
                matches!(item.issue, crate::script_init::Issue::UnsupportedField(field) if field.starts_with("health pool"))
            }));
            let states = &snapshot.instances[&1].states;
            assert_eq!(
                states[&secondary_state].kind,
                crate::script_init::StateKind::HealthPool { pool: Some(1) }
            );
            assert_eq!(
                states[&0].kind,
                crate::script_init::StateKind::HealthPool { pool: Some(2) }
            );
            assert_eq!(world.vitals.component(), pools.vitals().component());
        }
    }
}
