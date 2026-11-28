//! Load the authorized data needed before the game worker can create a world.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{self, BufReader},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
};

use serde::Deserialize;

pub use crate::game_combat::CombatError;

use crate::{
    accounts::parse_guid,
    actor_voice::ActorVoiceCatalog,
    bits::BitWriter,
    collision_cache::{RayCacheIdentity, chunk_guid, read_movement_cache, read_ray_cache},
    collision_index::TriangleIndex,
    collision_scene::CollisionScene,
    combat_voice::CombatVoiceCatalog,
    contact_world::ContactWorld,
    game_bots::{Bot, BotCatalog, BotWalk},
    game_clock::FIRST_TICK,
    game_combat::{
        ENCOUNTER_INSTANCE, EncounterRegistration, EncounterWorld, GateState, GateWorld,
        RoomSimulation, script_eye_position,
    },
    game_content::{
        GameMap, JUNKENSTEIN_GATE, JunkensteinCard, PRACTICE_RANGE_MAP, PRACTICE_RANGE_MODE,
    },
    game_controller::Controller,
    game_heroes::{CASSIDY, Hero, HeroCatalog},
    game_hitscan::HurtVolumeCatalog,
    game_instance::RETAIL_QUANTA,
    game_pools::PoolCatalog,
    game_replication::{OwnerBodyRuntime, OwnerRoomActivity},
    game_stats::{PlayerStats, StatsCatalog, initial_on_fire},
    map_environment::EnvironmentCatalog,
    match_scene::{
        PlayerAppearance, initial_world, junkenstein_presentation, mode_presentation, team_bit,
    },
    mover::Mover,
    mover_ground::NOT_GROUND,
    mover_settings::MoverCatalog,
    patrol_script::PatrolScript,
    runtime::{
        ACTOR_VOICE_CATALOG, COMBAT_HURT_VOLUMES, COMBAT_VOICE_CATALOG, JUNKENSTEIN_MAP_WORLD,
        MAP_ENVIRONMENT,
    },
    script_codecs::CodecTable,
    script_expr::native_shape,
    script_graph::{GraphStore, asset_guid},
    script_init::{self, Root},
    script_player::{OwnerBodyPreparationError, OwnerBodySetup, OwnerBodySpawn, PlayerBodyFactory},
    script_wire::{Value, Variables},
    world::{Component, EntityUpdate},
};

#[cfg(test)]
use crate::{
    game_heroes::SOLDIER,
    game_service::player_entity,
    pve_encounter::messages::DefenderMessage,
    script_init::{Site, World},
    script_snapshot_wire::{CountdownFrame, ProjectedSnapshot},
    world::GAME_MODE_ENTITY,
};

#[cfg(any(test, feature = "inspector"))]
use crate::{debug_geometry::DebugGeometry, game_debug::Actor};

pub(crate) const MOVER_CATEGORY: u16 = 0x18;
const OWNER_PROBE_CONTROLLER: u32 = 0xa000_0100;

pub(crate) fn weapon_terrain_category(scene: &CollisionScene) -> u16 {
    scene.weapon_terrain_category().unwrap_or(MOVER_CATEGORY)
}

fn mover_category(map: &GameMap, team: u8) -> u16 {
    mover_category_for_team(map.free_for_all, team)
}

fn mover_category_for_team(free_for_all: bool, team: u8) -> u16 {
    match (free_for_all, team) {
        (false, 0) => 0x08,
        (false, 1) => 0x10,
        _ => MOVER_CATEGORY,
    }
}

fn is_practice_range(map: &GameMap) -> bool {
    map.map_guid == PRACTICE_RANGE_MAP && map.mode_guid == PRACTICE_RANGE_MODE
}

fn authored_mode_variables(graphs: &GraphStore, mode: u64) -> Option<Variables> {
    Some(
        graphs
            .mode_variables(mode)?
            .iter()
            .map(|(&id, &value)| (id, Value::from(value)))
            .collect(),
    )
}

fn ground_height(
    ground: &TriangleIndex,
    category: u16,
) -> impl Fn(f64, f64, f64, f64) -> Option<f64> + '_ {
    move |x, z, top, bottom| {
        ground
            .ground(x, z, top, bottom, category)
            .filter(|hit| hit.surface & NOT_GROUND == 0)
            .map(|hit| hit.y)
    }
}

type OwnerBodyFactory = dyn Fn(&Hero, OwnerBodySpawn, OwnerBodySetup) -> io::Result<Option<Box<dyn OwnerBodyRuntime>>>
    + Send
    + Sync;

fn practice_owner_selector(
    factories: BTreeMap<
        (u64, u64),
        Result<Arc<PlayerBodyFactory>, Arc<SceneOwnerPreparationError>>,
    >,
    graphs: Arc<GraphStore>,
    codecs: Option<Arc<CodecTable>>,
    mode: u64,
    pools: Arc<PoolCatalog>,
    controller_on_fire: Option<bool>,
    anim_timing: Option<Arc<crate::anim_timing::AnimationTiming>>,
) -> Arc<OwnerBodyFactory> {
    let cache = Mutex::new(factories);
    Arc::new(move |hero, spawn, setup| {
        let key = (hero.guid, hero.body);
        let entry = {
            let mut guard = cache
                .lock()
                .map_err(|_| io::Error::other("owner body factory cache poisoned"))?;
            if let Some(existing) = guard.get(&key) {
                existing.clone()
            } else if graphs.entity_body(hero.body).is_some() {
                let compiled = match (codecs.as_ref(), controller_on_fire) {
                    (Some(codecs), Some(on_fire)) => PlayerBodyFactory::from_body(
                        Arc::clone(&graphs),
                        Arc::clone(codecs),
                        mode,
                        hero,
                        pools.hero_pools(hero.guid),
                        on_fire,
                    )
                    .map(|f| Arc::new(f.with_anim_timing(anim_timing.clone())))
                    .map_err(|source| Arc::new(SceneOwnerPreparationError::Body(Box::new(source)))),
                    (None, _) => Err(Arc::new(SceneOwnerPreparationError::Codecs)),
                    (_, None) => Err(Arc::new(SceneOwnerPreparationError::ControllerFlag)),
                };
                guard.insert(key, compiled.clone());
                compiled
            } else {
                return Ok(None);
            }
        };
        let factory = entry
            .as_ref()
            .map_err(|error| io::Error::new(io::ErrorKind::Unsupported, Arc::clone(error)))?;
        let runtime: Box<dyn OwnerBodyRuntime> = Box::new(factory.start_with_setup(spawn, setup)?);
        Ok(Some(runtime))
    })
}

#[derive(Debug, thiserror::Error)]
pub(crate) enum SceneOwnerPreparationError {
    #[error("the owner statescript codec table is unavailable")]
    Codecs,
    #[error("the controller's initial on-fire flag is unavailable")]
    ControllerFlag,
    #[error(transparent)]
    SpawnMover(io::Error),
    #[error(transparent)]
    TeamFilter(io::Error),
    #[error(transparent)]
    SpatialFrame(CombatError),
    #[error(transparent)]
    Body(Box<OwnerBodyPreparationError>),
}

#[derive(Clone)]
struct MapCollision {
    movement_cache: String,
    ground: Arc<CollisionScene>,
    combat_rays: Arc<CollisionScene>,
    combat_sweeps: Option<Arc<ContactWorld>>,
    contacts: Option<Arc<ContactWorld>>,
}

#[derive(PartialEq, Eq)]
struct CollisionKey {
    map: u64,
    mode: u64,
    contacts: bool,
}

pub struct SceneAssets {
    junkenstein_validation: Option<crate::pve_encounter::validation::JunkensteinValidation>,
    collision: PathBuf,
    junkenstein_map_world: PathBuf,
    last_collision: Mutex<Option<(CollisionKey, MapCollision)>>,
    trace_contacts: bool,
    mode_props: BTreeMap<u64, BTreeSet<u64>>,
    environment: EnvironmentCatalog,
    ability_rules: Arc<crate::hero_ability_rules::HeroAbilityRules>,
    graphs: Arc<GraphStore>,
    codecs: Option<Arc<CodecTable>>,
    practice_owner_body: OnceLock<Option<Arc<OwnerBodyFactory>>>,
    bots: BotCatalog,
    movers: Arc<MoverCatalog>,
    hurt_volumes: Arc<HurtVolumeCatalog>,
    actor_voice: Option<Arc<ActorVoiceCatalog>>,
    anim_timing: Option<Arc<crate::anim_timing::AnimationTiming>>,
    combat_voice: Arc<CombatVoiceCatalog>,
    stats: Arc<StatsCatalog>,
    heroes: Arc<HeroCatalog>,
    pools: Arc<PoolCatalog>,
}

mod encounter;
mod prepared;
mod queries;
#[cfg(test)]
mod tests;

pub use self::encounter::JunkensteinGate;
use self::encounter::SpawnAnchor;
pub(crate) use self::encounter::SpawnAnchors;
pub use self::prepared::PreparedScene;
pub(crate) use self::queries::BotQueries;

impl SceneAssets {
    pub(crate) fn set_junkenstein_validation(
        &mut self,
        settings: crate::pve_encounter::validation::JunkensteinValidation,
    ) {
        self.junkenstein_validation = Some(settings);
    }

    pub fn load(runtime: impl AsRef<Path>) -> io::Result<Self> {
        let runtime = runtime.as_ref();
        let data = runtime.join("data");
        let graphs = Arc::new(GraphStore::load(data.join("game_graphs_174.json.gz"))?);
        let codecs = match CodecTable::load(data.join("statescript_codecs_174.json")) {
            Ok(table) => Some(Arc::new(table)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let pools = Arc::new(PoolCatalog::from_graphs(&graphs)?);
        let mut combat_voice = CombatVoiceCatalog::load(runtime.join(COMBAT_VOICE_CATALOG))?;
        combat_voice.bind_multikill_exclusions(&graphs);
        Ok(Self {
            collision: runtime.join("collision"),
            junkenstein_map_world: runtime.join(JUNKENSTEIN_MAP_WORLD),
            last_collision: Mutex::new(None),
            trace_contacts: false,
            junkenstein_validation: None,
            mode_props: read_mode_props(&data.join("collision_props_174.json"))?,
            environment: EnvironmentCatalog::load(runtime.join(MAP_ENVIRONMENT))?,
            ability_rules: crate::hero_ability_rules::HeroAbilityRules::load_with_status_scripts(
                data.join("hero_ability_rules_174.json"),
                runtime.join(crate::runtime::STATUS_SCRIPTS),
            )?,
            graphs,
            codecs,
            practice_owner_body: OnceLock::new(),
            bots: BotCatalog::load(data.join("ai_spawners_174.json"))?,
            movers: Arc::new(MoverCatalog::load(data.join("game_movers_174.json"))?),
            hurt_volumes: Arc::new(HurtVolumeCatalog::load(&runtime.join(COMBAT_HURT_VOLUMES))?),
            actor_voice: ActorVoiceCatalog::load(&runtime.join(ACTOR_VOICE_CATALOG))?.map(Arc::new),
            anim_timing: crate::anim_timing::AnimationTiming::load(&data)?.map(Arc::new),
            combat_voice: Arc::new(combat_voice),
            stats: Arc::new(StatsCatalog::load(data.join("game_stats_174.json"))?),
            heroes: Arc::new(HeroCatalog::load(data.join("game_heroes_174.json"))?),
            pools,
        })
    }

    pub(crate) fn enable_contact_trace(&mut self) {
        self.trace_contacts = true;
    }

    pub fn graphs(&self) -> &GraphStore {
        &self.graphs
    }

    pub fn heroes(&self) -> &HeroCatalog {
        &self.heroes
    }

    pub(crate) fn prepare_junkenstein(
        &self,
        map: GameMap,
        card: JunkensteinCard,
        admitted_team: u8,
    ) -> io::Result<PreparedScene> {
        if !map.is_junkenstein()
            || map.mode_guid != card.mode
            || map.team_sizes.first() != Some(&card.players)
            || admitted_team != 0
        {
            return Err(invalid(
                "Junkenstein queue does not match the admitted map and roster",
            ));
        }
        for hero in [
            0x02e0_0000_0000_0175,
            0x02e0_0000_0000_0176,
            0x02e0_0000_0000_0177,
        ] {
            let pair = self
                .graphs
                .enemy_pair(hero)
                .ok_or_else(|| invalid("missing Junkenstein NPC pair"))?;
            if self.movers.authored_for_body(pair.body).is_none() {
                return Err(invalid(format!(
                    "missing authored NPC mover {:016X}; prepare this runtime with --target-tags-xml and the Junkenstein NPC catalog",
                    pair.body
                )));
            }
        }
        let mut scene = self.prepare(map)?;
        let gate = scene
            .junkenstein_gate()
            .cloned()
            .expect("prepared Junkenstein gate");
        let anchors = scene
            .simulation()
            .spawn_anchors()
            .cloned()
            .unwrap_or_default();
        let (shared, encounter) = self.encounter_roots(card.mode)?;
        let mut variables = authored_mode_variables(&self.graphs, card.mode)
            .ok_or_else(|| invalid("Junkenstein mode variables are missing"))?;
        if let Some(challenge) = card.challenge {
            variables.insert(challenge, Value::Bool(true));
            if challenge == 0x7d62 {
                variables.insert(0x7e0f, Value::Float(1.0));
            }
        }
        let (ready_message, ready_parameter) = junkenstein_ready_binding(&self.graphs)?;
        let mut gate_state = GateState::default();
        let gate_script = script_init::Component::start(
            Arc::clone(&self.graphs),
            gate.entity,
            Variables::new(),
            vec![Root::new(1, gate.graph)],
            0,
            &mut GateWorld {
                graphs: &self.graphs,
                mode: card.mode,
                entity: gate.entity,
                state: &mut gate_state,
                bodies: scene.simulation(),
                mode_variables: &variables,
            },
            script_init::MAX_INIT_WORK,
        )
        .map_err(invalid)?;
        let gate_variables = gate_script
            .snapshot()
            .expect("started gate script")
            .variables
            .clone();
        if gate_state.team != Some(2) || gate_variables.get(&8571) != Some(&Value::Integer(1)) {
            return Err(invalid(
                "Junkenstein gate script did not initialize its team and health",
            ));
        }
        let mut result_action = None;
        let mut screens_complete = false;
        let encounter = {
            let mut startup = EncounterWorld {
                graphs: &self.graphs,
                mode: card.mode,
                gate: &gate,
                anchors: &anchors,
                gate_variables: &gate_variables,
                bodies: scene.simulation_mut(),
                creator: None,
                result_action: &mut result_action,
                screens_complete: &mut screens_complete,
                team: admitted_team,
                difficulty: card.difficulty,
            };
            script_init::Component::start(
                Arc::clone(&self.graphs),
                crate::world::GAME_MODE_ENTITY,
                variables,
                vec![
                    Root::new(1, shared),
                    Root::new(ENCOUNTER_INSTANCE, encounter),
                ],
                0,
                &mut startup,
                script_init::MAX_INIT_WORK,
            )
            .map_err(invalid)?
        };
        let snapshot = encounter.snapshot().expect("newly started encounter");
        scene.mode_ui = junkenstein_presentation(&self.graphs, snapshot)?;
        if snapshot.variables.get(&6751) != Some(&Value::Asset(card.difficulty))
            || snapshot.variables.get(&8439)
                != Some(&Value::Array(vec![Value::Entity(gate.entity)]))
        {
            return Err(invalid(
                "Junkenstein script did not bind the queue difficulty and gate",
            ));
        }
        let voice_set = self
            .graphs
            .graph(0x0c8c)
            .and_then(|graph| graph.node(240))
            .filter(|node| {
                node.class == "STU_353AF1A9"
                    && native_shape(&node.fields["m_target"], "STU_B5A0CAF0", None)
            })
            .filter(|node| node.fields["m_DA4846A5"]["$"] == "STUConfigVoiceDefinition")
            .and_then(|node| asset_guid(&node.fields["m_DA4846A5"]["m_C6C61CB0"]))
            .filter(|&guid| guid == crate::pve_encounter::voice::ENCOUNTER_VOICE_SET)
            .ok_or_else(|| invalid("Junkenstein narrator voice-set binding is unavailable"))?;
        let npcs = match crate::npc_catalog::NpcCatalog::load(
            self.collision
                .parent()
                .expect("runtime root")
                .join(crate::runtime::JUNKENSTEIN_NPCS),
        ) {
            Ok(catalog) => Some(Arc::new(catalog)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let mode = scene.map.mode_guid;
        scene
            .simulation_mut()
            .register_junkenstein_encounter(EncounterRegistration {
                voice_set,
                graphs: Arc::clone(&self.graphs),
                npcs,
                heroes: Arc::clone(&self.heroes),
                movers: Arc::clone(&self.movers),
                mode,
                script: encounter,
                codecs: self.codecs.clone(),
                gate_script,
                gate_state,
                team: admitted_team,
                difficulty: card.difficulty,
                ready_message,
                ready_parameter,
                result_action,
                screens_complete,
            })
            .map_err(invalid)?;
        if Some(card) == JunkensteinCard::from_index(0x3c) {
            scene.owner_body_factory = self.prepare_owner_body(&scene, CASSIDY);
        }
        scene.junkenstein_validation = self
            .junkenstein_validation
            .and_then(|settings| settings.for_card(card));
        if let Some(capacity) = scene
            .junkenstein_validation
            .and_then(|settings| settings.gate_capacity)
        {
            scene.set_gate_capacity(capacity)?;
        }
        Ok(scene)
    }

    /// Load the map before handing it to the worker. The render layouts and
    /// graph requirements are checked without blocking a game tick.
    pub fn prepare(&self, map: GameMap) -> io::Result<PreparedScene> {
        let (junkenstein_gate, junkenstein_anchors) = if map.is_junkenstein() {
            let (gate, anchors) =
                read_junkenstein_map(&self.junkenstein_map_world, &map, &self.graphs)?;
            (Some(gate), anchors)
        } else {
            (None, SpawnAnchors::default())
        };
        let mut bots = self.bots.make(map.map_guid, |body| {
            let settings = self.movers.for_body(Some(body));
            BotWalk::from_mover(
                settings.run_forward,
                settings.accel_ground,
                settings.decel_ground_base,
            )
        });
        let scripted_bots = bots
            .iter()
            .filter(|bot| self.graphs.entity_body(bot.spawn.body).is_some())
            .count();
        let MapCollision {
            movement_cache: _movement_cache,
            ground,
            combat_rays,
            combat_sweeps,
            contacts,
        } = self.map_collision(
            &map,
            is_practice_range(&map)
                || map.is_junkenstein()
                || bots.iter().any(|bot| !bot.spawn.route.is_empty()),
        )?;
        let breakables = Arc::new(crate::collision_scene::BreakableState::new());
        if let Some(world) = &contacts {
            for bot in bots.iter_mut().filter(|bot| !bot.spawn.route.is_empty()) {
                let category = mover_category(&map, bot.spawn.team);
                bot.attach_scene_collision(
                    self.movers.for_body(Some(bot.spawn.body)),
                    Some(ground.as_ref()),
                    Arc::clone(world),
                    category,
                    ground_height(ground.triangles(), category),
                );
                bot.set_breakables(Arc::clone(&breakables));
                if self.trace_contacts {
                    bot.enable_contact_trace();
                }
            }
        }
        let controller = Controller::load(&self.graphs, map.mode_guid)?;
        let encounter = map
            .is_junkenstein()
            .then(|| {
                self.encounter_roots(map.mode_guid)
                    .map(|(_, encounter)| encounter)
            })
            .transpose()?;
        // Client presenters can read the room mode entity's authored variables.
        let variables = authored_mode_variables(&self.graphs, map.mode_guid).unwrap_or_default();
        let mode_ui = mode_presentation(&self.graphs, &variables, encounter)?;
        let initial_board = crate::game_stats::with_on_fire(
            self.stats
                .component(&PlayerStats::default(), map.mode_guid, None, None),
            Some(0.0),
            Some(crate::game_progression::ON_FIRE_THRESHOLD),
            Some(false),
        );
        let junkenstein_anchors = Arc::new(junkenstein_anchors);
        let mut simulation = RoomSimulation::new(bots).map_err(io::Error::other)?;
        simulation.set_free_for_all(map.free_for_all);
        simulation.load_bot_target_tags(&self.graphs);
        simulation.set_combat_voice(Arc::clone(&self.combat_voice));
        simulation.set_body_contact_sets(self.graphs.body_contact_sets());
        simulation.set_stats_catalog(Arc::clone(&self.stats), map.mode_guid);
        simulation.set_ability_rules(Arc::clone(&self.ability_rules));
        simulation.enable_received_damage_feedback();
        simulation.enable_authored_hit_feedback(1.0);
        simulation.enable_authored_elimination_feedback();
        simulation.set_map_guid(map.map_guid);
        simulation.set_environment(self.environment.for_map(map.map_guid));
        if let Some(pvp) = crate::pvp_mode::PvpObjectiveRuntime::from_game_map(&map) {
            simulation.set_pvp_objective(pvp);
        }
        if self.graphs.uses_host_health_events() {
            simulation.enable_host_health_events();
        }
        if self.graphs.uses_room_spatial_frame() || self.actor_voice.is_some() {
            simulation.enable_spatial_queries(
                map.free_for_all,
                Arc::clone(&combat_rays),
                self.graphs
                    .room_spatial_needs()
                    .max(crate::script_graph::RoomSpatialNeeds::EntityTags),
            );
            simulation.set_breakables(Arc::clone(&breakables));
            if let Some(catalog) = &self.actor_voice {
                simulation.enable_actor_voice(Arc::clone(catalog));
            }
            simulation.enable_spatial_actor_rays(Arc::clone(&self.movers));
        }
        if contacts.is_some() {
            simulation.enable_movement_contacts(map.free_for_all, Arc::clone(&self.movers));
        }
        simulation.set_script_graphs(Arc::clone(&self.graphs), map.mode_guid);
        if let Some(codecs) = self.codecs.as_ref() {
            simulation.set_script_codecs(Arc::clone(codecs));
        }
        if let Some(codecs) = self
            .codecs
            .as_ref()
            .filter(|_| is_practice_range(&map) && scripted_bots > 0)
        {
            simulation.enable_stock_bot_scripts(
                Arc::clone(&self.graphs),
                Arc::clone(codecs),
                map.mode_guid,
            );
        }
        if let Some(gate) = junkenstein_gate {
            simulation
                .register_junkenstein_gate(gate, Arc::clone(&junkenstein_anchors))
                .map_err(io::Error::other)?;
        }
        let mut scene = PreparedScene {
            map,
            #[cfg(any(test, feature = "inspector"))]
            movement_cache: _movement_cache,
            ground,
            combat_rays,
            combat_sweeps,
            contacts,
            breakables,
            controller,
            mode_ui,
            junkenstein_validation: None,
            initial_board,
            simulation,
            scripted_bots,
            movers: Arc::clone(&self.movers),
            hurt_volumes: Arc::clone(&self.hurt_volumes),
            heroes: Arc::clone(&self.heroes),
            pools: Arc::clone(&self.pools),
            owner_body_factory: None,
        };
        if is_practice_range(scene.map()) {
            scene.owner_body_factory = self
                .practice_owner_body
                .get_or_init(|| self.compile_practice_factories(&scene))
                .clone();
        }
        Ok(scene)
    }

    fn encounter_roots(&self, mode_guid: u64) -> io::Result<(u32, u32)> {
        let mode = self
            .graphs
            .mode_scripts(mode_guid)
            .filter(|mode| mode.pve && !mode.scripted_loading)
            .ok_or_else(|| invalid("missing Junkenstein mode scripts"))?;
        let shared = mode
            .shared_graph
            .ok_or_else(|| invalid("missing Junkenstein shared graph"))?;
        let encounter = mode
            .graph
            .ok_or_else(|| invalid("missing Junkenstein encounter graph"))?;
        let body = mode.teams.get(&0).and_then(|team| team.body);
        for (graph, label) in [(Some(shared), "shared"), (body, "defender body")] {
            if graph.and_then(|index| self.graphs.graph(index)).is_none() {
                return Err(invalid(format!("missing Junkenstein {label} graph")));
            }
        }
        Ok((shared, encounter))
    }

    fn map_collision(&self, map: &GameMap, mover_contacts: bool) -> io::Result<MapCollision> {
        let key = CollisionKey {
            map: map.map_guid,
            mode: map.mode_guid,
            contacts: mover_contacts,
        };
        let mut last = self
            .last_collision
            .lock()
            .map_err(|_| io::Error::other("movement scene cache lock poisoned"))?;
        if let Some((cached, collision)) = last.as_ref() {
            if cached == &key {
                return Ok(collision.clone());
            }
        }

        let movement_cache = format!("{:04X}.col", map.map_guid as u32);
        let path = self.collision.join(&movement_cache);
        let cache = read_movement_cache(BufReader::new(File::open(&path)?)).map_err(invalid)?;
        for (field, expected) in [("map", map.map_guid), ("chunk", chunk_guid(map.map_guid))] {
            if let Some(value) = cache.header.source.get(field) {
                if value.as_str().and_then(parse_guid) != Some(expected) {
                    return Err(invalid("movement cache identifies a different map"));
                }
            }
        }
        let empty = BTreeSet::new();
        let identifiers = self.mode_props.get(&map.mode_guid).unwrap_or(&empty);
        let ground =
            Arc::new(CollisionScene::movement_probe(&cache, identifiers).map_err(invalid)?);
        let expanded = match load_combat_geometry(&path, map.map_guid, identifiers) {
            Ok(expanded) => expanded,
            Err(error) => {
                eprintln!(
                    "{} expanded combat geometry unavailable: {error}; using movement geometry",
                    map.name
                );
                None
            }
        };
        let movement_contacts = (mover_contacts || expanded.is_none())
            .then(|| ContactWorld::from_movement_cache(cache, identifiers).map(Arc::new))
            .transpose()
            .map_err(invalid)?;
        let (combat_rays, combat_sweeps) = match expanded {
            Some((rays, sweeps)) => (Arc::new(rays), Some(Arc::new(sweeps))),
            None => (Arc::clone(&ground), movement_contacts.clone()),
        };
        let contacts = movement_contacts.filter(|_| mover_contacts);
        let collision = MapCollision {
            movement_cache,
            ground,
            combat_rays,
            combat_sweeps,
            contacts,
        };
        // Retain one prepared key. Active rooms hold their own immutable handles.
        *last = Some((key, collision.clone()));
        Ok(collision)
    }

    fn prepare_owner_body(
        &self,
        scene: &PreparedScene,
        hero_guid: u64,
    ) -> Option<Arc<OwnerBodyFactory>> {
        let hero = self.heroes.get(hero_guid)?;
        let prepared = scene
            .simulation()
            .capture_spatial_frame(FIRST_TICK)
            .map_err(SceneOwnerPreparationError::SpatialFrame)
            .and_then(|spatial_frame| {
                self.prepare_owner_factory(
                    scene,
                    hero,
                    OwnerBodySetup {
                        spatial_frame,
                        room_activity: Some(scene.owner_room_activity()),
                        ..OwnerBodySetup::default()
                    },
                )
            })
            .map_err(Arc::new);
        if let Err(reason) = &prepared {
            eprintln!(
                "{} owner statescript unavailable in {} ({reason})",
                hero.name, scene.map.name
            );
        }
        Some(Arc::new(move |hero, spawn, setup| {
            if hero.guid != hero_guid {
                return Ok(None);
            }
            let factory = prepared
                .as_ref()
                .map_err(|reason| io::Error::new(io::ErrorKind::Unsupported, Arc::clone(reason)))?;
            let runtime: Box<dyn OwnerBodyRuntime> =
                Box::new(factory.start_with_setup(spawn, setup)?);
            Ok(Some(runtime))
        }))
    }

    fn compile_practice_factories(&self, scene: &PreparedScene) -> Option<Arc<OwnerBodyFactory>> {
        // A scriptless scene omits the entire owner configuration.
        let bodies = self.graphs.bodies();
        if self.codecs.is_none()
            && bodies.values().all(|body| body.hero.is_none())
            && self
                .heroes
                .heroes()
                .all(|hero| !bodies.contains_key(&hero.body))
        {
            return None;
        }
        // Cache graph layouts only. Each selected body starts with the current room context.
        let factories: BTreeMap<_, _> = self
            .heroes
            .heroes()
            .map(|hero| {
                (
                    (hero.guid, hero.body),
                    self.compile_owner_factory(scene, hero)
                        .map(Arc::new)
                        .map_err(Arc::new),
                )
            })
            .collect();
        let on_fire = initial_on_fire(&scene.initial_board);
        (!factories.is_empty()).then(|| {
            practice_owner_selector(
                factories,
                Arc::clone(&self.graphs),
                self.codecs.clone(),
                scene.map.mode_guid,
                Arc::clone(&self.pools),
                on_fire,
                self.anim_timing.clone(),
            )
        })
    }

    #[cfg(test)]
    pub(crate) fn test_actor_voice(&self) -> Option<Arc<ActorVoiceCatalog>> {
        self.actor_voice.as_ref().map(Arc::clone)
    }

    pub(crate) fn compile_owner_factory(
        &self,
        scene: &PreparedScene,
        hero: &Hero,
    ) -> Result<PlayerBodyFactory, SceneOwnerPreparationError> {
        let codecs = self
            .codecs
            .as_ref()
            .ok_or(SceneOwnerPreparationError::Codecs)?;
        let controller_on_fire = initial_on_fire(&scene.initial_board)
            .ok_or(SceneOwnerPreparationError::ControllerFlag)?;
        PlayerBodyFactory::from_body(
            Arc::clone(&self.graphs),
            Arc::clone(codecs),
            scene.map.mode_guid,
            hero,
            self.pools.hero_pools(hero.guid),
            controller_on_fire,
        )
        .map(|factory| factory.with_anim_timing(self.anim_timing.clone()))
        .map_err(|source| SceneOwnerPreparationError::Body(Box::new(source)))
    }

    fn prepare_owner_factory(
        &self,
        scene: &PreparedScene,
        hero: &Hero,
        setup: OwnerBodySetup,
    ) -> Result<PlayerBodyFactory, SceneOwnerPreparationError> {
        let factory = self.compile_owner_factory(scene, hero)?;
        let mover = scene
            .player_spawn_mover(hero.body, 0)
            .map_err(SceneOwnerPreparationError::SpawnMover)?;
        factory
            .probe_startup(
                OwnerBodySpawn {
                    slot: 0,
                    controller: OWNER_PROBE_CONTROLLER,
                    entity: 0xa000_0101,
                    body_filter: team_bit(0).map_err(SceneOwnerPreparationError::TeamFilter)?,
                    frame: FIRST_TICK,
                    pose: mover.pose(),
                    eye_position: script_eye_position(hero.guid, &mover),
                    skin: 0,
                    quanta: RETAIL_QUANTA,
                },
                setup,
            )
            .map_err(|source| SceneOwnerPreparationError::Body(Box::new(source)))?;
        Ok(factory)
    }
}

fn load_combat_geometry(
    movement_path: &Path,
    map_guid: u64,
    identifiers: &BTreeSet<u64>,
) -> io::Result<Option<(CollisionScene, ContactWorld)>> {
    let file = match File::open(movement_path.with_extension("rays.col")) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let data = movement_path
        .parent()
        .and_then(Path::parent)
        .ok_or_else(|| invalid("missing runtime directory for combat ray cache"))?
        .join("data");
    let map_key = format!("0x{map_guid:016X}");
    let keys: serde_json::Value = serde_json::from_reader(BufReader::new(File::open(
        data.join("collision_keys_174.json"),
    )?))
    .map_err(invalid)?;
    let collision_ckey = keys[&map_key]["ckey"]
        .as_str()
        .ok_or_else(|| invalid("missing map collision key"))?;
    let props: serde_json::Value = match File::open(data.join("collision_props_174.json")) {
        Ok(file) => serde_json::from_reader(BufReader::new(file)).map_err(invalid)?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => serde_json::Value::Null,
        Err(error) => return Err(error),
    };
    let identity = RayCacheIdentity {
        map_guid,
        collision_ckey,
        placement_ckey: props["maps"][&map_key]["ckey"].as_str(),
        prop_records_sha256: None,
    };
    let cache = read_ray_cache(BufReader::new(file), identity).map_err(invalid)?;
    let rays = CollisionScene::expanded_rays(&cache, identifiers, identity).map_err(invalid)?;
    let sweeps =
        ContactWorld::from_expanded_ray_cache(cache, identifiers, identity).map_err(invalid)?;
    Ok(Some((rays, sweeps)))
}

#[derive(Deserialize)]
struct PropCatalog {
    modes: BTreeMap<String, Vec<String>>,
}

fn read_mode_props(path: &Path) -> io::Result<BTreeMap<u64, BTreeSet<u64>>> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(error) => return Err(error),
    };
    let catalog: PropCatalog = serde_json::from_slice(&bytes).map_err(invalid)?;
    let guid = |value: &str| parse_guid(value).ok_or_else(|| invalid("invalid mode prop GUID"));
    catalog
        .modes
        .into_iter()
        .map(|(mode, props)| {
            Ok((
                guid(&mode)?,
                props
                    .iter()
                    .map(|value| guid(value))
                    .collect::<io::Result<_>>()?,
            ))
        })
        .collect()
}

fn junkenstein_ready_binding(graphs: &GraphStore) -> io::Result<(u64, u32)> {
    let mismatch = || invalid("Junkenstein hero-ready sender and receiver do not match");
    let sender = graphs
        .graph(0x0c90)
        .and_then(|graph| graph.node(11))
        .filter(|node| {
            node.class == "STU_38EE1100"
                && node
                    .field("m_params[0].m_value.m_identifier")
                    .and_then(serde_json::Value::as_u64)
                    == Some(2114)
        })
        .ok_or_else(mismatch)?;
    let receiver = graphs
        .graph(0x0c8c)
        .and_then(|graph| graph.node(3))
        .filter(|node| {
            node.class == "STUStatescriptStateGameMessageEntry"
                && node.field("m_39B47960").and_then(serde_json::Value::as_u64) == Some(1)
        })
        .ok_or_else(mismatch)?;
    let message = sender
        .field("m_gameMessage.m_gameMessage")
        .and_then(asset_guid)
        .filter(|message| message >> 48 == 0x0240)
        .ok_or_else(mismatch)?;
    let parameter = sender
        .field("m_params[0].m_B5051BCE")
        .and_then(asset_guid)
        .filter(|parameter| parameter >> 48 == 0x0a40)
        .ok_or_else(mismatch)?;
    if receiver
        .field("m_gameMessage.m_gameMessage")
        .and_then(asset_guid)
        != Some(message)
        || receiver
            .field("m_params[0].m_B5051BCE")
            .and_then(asset_guid)
            != Some(parameter)
    {
        return Err(mismatch());
    }
    Ok((message, u32::from(parameter as u16)))
}

fn read_junkenstein_map(
    path: &Path,
    map: &GameMap,
    graphs: &GraphStore,
) -> io::Result<(JunkensteinGate, SpawnAnchors)> {
    let data: serde_json::Value =
        serde_json::from_reader(BufReader::new(File::open(path)?)).map_err(invalid)?;
    let guid = |value: &serde_json::Value| value.as_str().and_then(parse_guid);
    if data["version"].as_u64() != Some(1) || guid(&data["map"]) != Some(map.map_guid) {
        return Err(invalid(
            "Junkenstein placement data identifies a different map or format",
        ));
    }
    if !map.placeables.contains(&JUNKENSTEIN_GATE) {
        return Err(invalid(
            "Junkenstein mode is missing the castle gate placement",
        ));
    }
    let record = map
        .first_entity
        .and_then(|first| JUNKENSTEIN_GATE.checked_sub(first))
        .ok_or_else(|| invalid("Junkenstein map has no valid first ENTITY index"))?;
    let entry = &data["entities"][record as usize];
    let definition = &entry["definition"];
    if entry["index"].as_u64() != Some(u64::from(record))
        || guid(definition) != Some(0x0400_0000_0000_16f6)
        || entry["record_metadata"]
            .as_str()
            .filter(|hex| hex.len() == 88)
            .and_then(|hex| hex.get(72..76))
            != Some("0000")
    {
        return Err(invalid(
            "Junkenstein gate record is not the authored server placement",
        ));
    }
    let components =
        data["definitions"][definition.as_str().expect("validated GUID")]["components"]["entries"]
            .as_array()
            .ok_or_else(|| invalid("Junkenstein gate has no definition components"))?;
    let tags = components.iter().filter_map(|row| {
        let value = &row["value"];
        (value["$"] == "STUTargetTagComponent").then_some(&value["m_targetTags"])
    });
    let placed = entry["components"]
        .as_array()
        .ok_or_else(|| invalid("Junkenstein gate has no placed components"))?;
    let placed_tags = placed.iter().filter_map(|row| {
        (row["class"] == "STUTargetTagInstanceData").then_some(&row["fields"]["m_targetTags"])
    });
    let tags: Vec<_> = tags.collect();
    let placed_tags: Vec<_> = placed_tags.collect();
    if tags.len() != 1
        || placed_tags.len() > 1
        || placed_tags
            .iter()
            .any(|tags| !tags.is_null() && !tags.as_array().is_some_and(Vec::is_empty))
        || !tags[0].as_array().is_some_and(|tags| {
            tags.iter()
                .any(|tag| guid(tag) == Some(JunkensteinGate::TAG))
        })
    {
        return Err(invalid(
            "Junkenstein gate is missing its unique definition tag",
        ));
    }
    let script = placed
        .iter()
        .find(|row| row["class"] == "STUStatescriptComponentInstanceData")
        .and_then(|row| guid(&row["fields"]["m_6D10093E"][0]["m_graph"]));
    let graph = 0x0ca7;
    if script != Some(0x0580_0000_0000_0000 | u64::from(graph)) || graphs.graph(graph).is_none() {
        return Err(invalid(
            "Junkenstein gate is missing its authored graph 0CA7",
        ));
    }
    let position: [f32; 3] = serde_json::from_value(entry["position"].clone()).map_err(invalid)?;
    if position.iter().any(|coordinate| !coordinate.is_finite()) {
        return Err(invalid("Junkenstein gate has an invalid position"));
    }
    let gate = JunkensteinGate {
        entity: 0x8000_0000 | JUNKENSTEIN_GATE,
        position: position.map(f64::from),
        graph,
    };
    let anchors = read_spawn_anchors(&data, map)?;
    if anchors.targets(JunkensteinGate::TAG) != Some(&[gate.entity]) {
        return Err(invalid(
            "Junkenstein gate tag does not uniquely resolve to its placement",
        ));
    }
    Ok((gate, anchors))
}

fn read_spawn_anchors(data: &serde_json::Value, map: &GameMap) -> io::Result<SpawnAnchors> {
    let mut anchors = SpawnAnchors::default();
    let first = map.first_entity.expect("validated first entity");
    for &flat in &map.placeables {
        let Some(index) = flat.checked_sub(first) else {
            continue;
        };
        let placement = &data["entities"][index as usize];
        let Some(definition) = placement["definition"].as_str() else {
            continue;
        };
        let defaults = data["definitions"][definition]["components"]["entries"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|row| &row["value"])
            .filter(|row| row["$"] == "STUTargetTagComponent")
            .collect::<Vec<_>>();
        let overrides = placement["components"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|row| row["class"] == "STUTargetTagInstanceData")
            .map(|row| &row["fields"])
            .collect::<Vec<_>>();
        let valid_components = defaults.len() == 1 && overrides.len() <= 1;
        let tags = |rows: Vec<&serde_json::Value>| -> BTreeSet<u64> {
            rows.into_iter()
                .flat_map(|row| row["m_targetTags"].as_array().into_iter().flatten())
                .filter_map(|value| value.as_str().and_then(parse_guid))
                .collect()
        };
        let defaults = tags(defaults);
        let overrides = tags(overrides);
        if defaults.is_empty() && overrides.is_empty() {
            continue;
        }
        let entity = 0x8000_0000 | flat;
        let valid_tags = valid_components && (defaults.is_empty() || overrides.is_empty());
        for &tag in defaults.union(&overrides) {
            let targets = anchors
                .tagged
                .entry(tag)
                .or_insert_with(|| Some(Vec::new()));
            if !valid_tags {
                *targets = None;
            } else if let Some(targets) = targets {
                targets.push(entity);
            }
        }
        let position: [f32; 3] =
            serde_json::from_value(placement["position"].clone()).map_err(invalid)?;
        let rotation: [f32; 4] =
            serde_json::from_value(placement["rotation"].clone()).map_err(invalid)?;
        let [x, y, z, w] = rotation.map(f64::from);
        let forward = [
            2.0 * (x * z + w * y),
            2.0 * (y * z - w * x),
            1.0 - 2.0 * (x * x + y * y),
        ]
        .map(|value| f64::from(value as f32));
        if placement["index"].as_u64() != Some(u64::from(index))
            || placement["record_metadata"]
                .as_str()
                .filter(|hex| hex.len() == 88)
                .and_then(|hex| hex.get(72..76))
                != Some("0000")
            || position.iter().any(|axis| !axis.is_finite())
            || rotation
                .iter()
                .any(|axis| !(-1.0001..=1.0001).contains(axis))
        {
            return Err(invalid(
                "Junkenstein tagged placement has an invalid transform",
            ));
        }
        anchors.transforms.insert(
            entity,
            SpawnAnchor {
                position: position.map(f64::from),
                forward,
            },
        );
    }
    Ok(anchors)
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}
