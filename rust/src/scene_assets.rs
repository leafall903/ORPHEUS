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
    bits::BitWriter,
    collision_cache::{chunk_guid, read_movement_cache},
    collision_index::TriangleIndex,
    collision_scene::CollisionScene,
    contact_world::ContactWorld,
    game_bots::{Bot, BotCatalog, BotWalk},
    game_clock::FIRST_TICK,
    game_combat::RoomSimulation,
    game_content::{
        GameMap, JUNKENSTEIN_GATE, JunkensteinCard, PRACTICE_RANGE_MAP, PRACTICE_RANGE_MODE,
    },
    game_controller::Controller,
    game_heroes::{CASSIDY, Hero, HeroCatalog, SOLDIER},
    game_instance::RETAIL_QUANTA,
    game_pools::PoolCatalog,
    game_replication::OwnerBodyRuntime,
    game_service::player_entity,
    game_stats::{PlayerStats, StatsCatalog, initial_on_fire},
    junkenstein_messages::{DefenderMessage, POST_MATCH_PHASE},
    junkenstein_spawn::SpawnSearches,
    match_scene::{
        PlayerAppearance, initial_world, junkenstein_presentation, mode_presentation, team_bit,
    },
    mover::Mover,
    mover_ground::NOT_GROUND,
    mover_settings::{MoverCatalog, MoverSettings},
    patrol_script::PatrolScript,
    runtime::JUNKENSTEIN_MAP_WORLD,
    script_codecs::CodecTable,
    script_expr::{EntityRelation, native_shape},
    script_graph::{GraphStore, ModeValue, asset_guid},
    script_init::{self, Root, Site, World},
    script_player::{OwnerBodySpawn, PlayerBodyFactory},
    script_snapshot_wire::{
        COUNTDOWN_STATES, COUNTDOWN_VAR, CountdownFrame, ProjectedSnapshot, SnapshotEncoder,
        encounter_countdown_delta, encounter_message_delta, mode_action_delta,
    },
    script_wire::{Value, Variables},
    world::{Component, ComponentValue, EntityUpdate, GAME_MODE_ENTITY},
};

#[cfg(any(test, feature = "inspector"))]
use crate::{debug_geometry::DebugGeometry, game_debug::Actor};

pub(crate) const MOVER_CATEGORY: u16 = 0x18;
const ENCOUNTER_INSTANCE: u16 = 2;

fn mover_category(map: &GameMap, team: u8) -> u16 {
    match (map.free_for_all, team) {
        (false, 0) => 0x08,
        (false, 1) => 0x10,
        _ => MOVER_CATEGORY,
    }
}

fn is_practice_range(map: &GameMap) -> bool {
    map.map_guid == PRACTICE_RANGE_MAP && map.mode_guid == PRACTICE_RANGE_MODE
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

type OwnerBodyFactory =
    dyn Fn(&Hero, OwnerBodySpawn) -> io::Result<Option<Box<dyn OwnerBodyRuntime>>> + Send + Sync;

#[derive(Clone)]
struct MapCollision {
    movement_cache: String,
    ground: Arc<CollisionScene>,
    contacts: Option<Arc<ContactWorld>>,
}

#[derive(PartialEq, Eq)]
struct CollisionKey {
    map: u64,
    mode: u64,
    contacts: bool,
}

pub struct SceneAssets {
    collision: PathBuf,
    junkenstein_map_world: PathBuf,
    last_collision: Mutex<Option<(CollisionKey, MapCollision)>>,
    trace_contacts: bool,
    mode_props: BTreeMap<u64, BTreeSet<u64>>,
    graphs: Arc<GraphStore>,
    codecs: Option<Arc<CodecTable>>,
    practice_owner_body: OnceLock<Option<Arc<OwnerBodyFactory>>>,
    bots: BotCatalog,
    movers: Arc<MoverCatalog>,
    stats: StatsCatalog,
    heroes: Arc<HeroCatalog>,
    pools: Arc<PoolCatalog>,
}

pub struct PreparedScene {
    map: GameMap,
    #[cfg(any(test, feature = "inspector"))]
    movement_cache: String,
    ground: Arc<CollisionScene>,
    contacts: Option<Arc<ContactWorld>>,
    controller: Controller,
    mode_ui: BitWriter,
    junkenstein_gate: Option<JunkensteinGate>,
    junkenstein_anchors: Arc<SpawnAnchors>,
    encounter: Option<JunkensteinEncounter>,
    initial_board: Component,
    simulation: RoomSimulation,
    scripted_bots: usize,
    movers: Arc<MoverCatalog>,
    heroes: Arc<HeroCatalog>,
    pools: Arc<PoolCatalog>,
    pub(crate) owner_body_factory: Option<Arc<OwnerBodyFactory>>,
}

/// Authored server placement and script for the castle gate.
#[derive(Debug, PartialEq)]
pub struct JunkensteinGate {
    pub entity: u32,
    pub position: [f64; 3],
    pub graph: u32,
}

impl JunkensteinGate {
    pub const TAG: u64 = 0x0d60_0000_0000_030e;
}

struct SpawnAnchor {
    position: [f64; 3],
    forward: [f64; 3],
}

#[derive(Default)]
pub(crate) struct SpawnAnchors {
    transforms: BTreeMap<u32, SpawnAnchor>,
    tagged: BTreeMap<u64, Option<Vec<u32>>>,
}

impl SpawnAnchors {
    pub(crate) fn targets(&self, tag: u64) -> Option<&[u32]> {
        match self.tagged.get(&tag) {
            Some(targets) => targets.as_deref(),
            None => Some(&[]),
        }
    }

    pub(crate) fn position(&self, entity: u32) -> Option<[f64; 3]> {
        self.transforms.get(&entity).map(|anchor| anchor.position)
    }

    pub(crate) fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        self.transforms.get(&entity).map(|anchor| anchor.forward)
    }
}

struct EncounterWorld<'a> {
    gate: &'a JunkensteinGate,
    anchors: &'a SpawnAnchors,
    gate_variables: &'a Variables,
    bodies: &'a mut RoomSimulation,
    creator: Option<EnemyCreator<'a>>,
    result_action: &'a mut Option<Site>,
    screens_complete: &'a mut bool,
    team: u8,
    difficulty: u64,
}

struct EnemyCreator<'a> {
    graphs: &'a Arc<GraphStore>,
    codecs: &'a Arc<CodecTable>,
    catalog: Option<&'a crate::npc_catalog::NpcCatalog>,
    heroes: &'a HeroCatalog,
    movers: &'a MoverCatalog,
    mode: u64,
    mode_variables: Arc<Variables>,
    tick: u32,
}

impl World for EncounterWorld<'_> {
    fn engine_setting(&self, _site: Site, key: u32) -> crate::script_expr::EngineSetting {
        crate::junkenstein_host::engine_setting(key)
    }

    fn play_victory_voice(&mut self, site: Site, actor: &Value) -> Option<u64> {
        use crate::junkenstein_voice::{VICTORY_CUE, VoiceRequest, victory_line};

        if (site.entity, site.graph, site.node) != (GAME_MODE_ENTITY, 0x0c8c, Some(221)) {
            return None;
        }
        let hero = match *actor {
            Value::Entity(entity) => self.bodies.encounter_enemy_hero(entity),
            Value::Asset(hero) => Some(hero),
            _ => None,
        };
        let (line, duration) = victory_line(hero);
        self.bodies.queue_mode_voice(VoiceRequest {
            anchor: GAME_MODE_ENTITY,
            speaker: GAME_MODE_ENTITY,
            stimulus: VICTORY_CUE,
            line,
        });
        Some(duration)
    }

    fn queue_mode_action(&mut self, site: Site) -> bool {
        if (site.entity, site.instance, site.graph)
            != (GAME_MODE_ENTITY, ENCOUNTER_INSTANCE, 0x0c8c)
        {
            return false;
        }
        self.result_action.get_or_insert(site);
        true
    }

    fn send_message(
        &mut self,
        _site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if target != self.gate.entity {
            return false;
        }
        self.bodies
            .queue_scene_message(crate::script_enemy::SceneMessage {
                target,
                message,
                parameters: parameters.clone(),
            });
        true
    }

    fn route_game_message(&mut self, site: Site, message: u64, parameters: &Variables) -> bool {
        use crate::junkenstein_messages::{ENCOUNTER_RESULT, POST_MATCH_COMPLETE};

        if let Some(request) = DefenderMessage::victory_view(site, message, parameters) {
            return self.bodies.queue_defender_message(request);
        }

        if (site.entity, site.graph, site.node, message)
            == (GAME_MODE_ENTITY, 0x180a, Some(28), POST_MATCH_COMPLETE)
            && parameters.is_empty()
        {
            *self.screens_complete = true;
            return true;
        }

        if (site.entity, site.graph, site.node, message)
            == (GAME_MODE_ENTITY, 0x0c8c, Some(84), ENCOUNTER_RESULT)
        {
            if parameters.len() != 4
                || ![0x3ae, 0x3ad, 0x3b4, 0x3b5]
                    .into_iter()
                    .all(|id| parameters.contains_key(&id))
                || !self.bodies.queue_defender_message(DefenderMessage {
                    target: None,
                    source: site.entity,
                    message,
                    parameters: parameters.clone(),
                })
            {
                return false;
            }
            self.bodies
                .queue_scene_message(crate::script_enemy::SceneMessage {
                    target: self.gate.entity,
                    message,
                    parameters: parameters.clone(),
                });
            self.bodies.queue_enemy_result(parameters);
            return true;
        }
        site.entity == GAME_MODE_ENTITY
            && parameters.is_empty()
            && DefenderMessage::authored(site, message)
                .is_some_and(|message| self.bodies.queue_defender_message(message))
    }

    fn related_entity(&mut self, site: Site, relation: EntityRelation, entity: u32) -> Option<u32> {
        if (site.entity, site.graph, site.node, relation)
            != (
                GAME_MODE_ENTITY,
                0x0cba,
                Some(9),
                EntityRelation::CurrentPossessor,
            )
        {
            return None;
        }
        self.bodies.encounter_possessor(entity)
    }

    fn play_script(&mut self, site: Site, target: u32, request: script_init::PlayScript) -> bool {
        self.bodies.queue_encounter_wave(site, target, request)
    }

    fn create_entity(
        &mut self,
        site: Site,
        request: &script_init::EntityCreation,
    ) -> Result<Option<u32>, String> {
        if site.entity != GAME_MODE_ENTITY {
            return Err("unsupported scene entity creation site".into());
        }
        let creator = self
            .creator
            .as_ref()
            .ok_or("missing prepared enemy creation services")?;
        let script_init::CreationKind::Hero { hero, .. } = &request.kind else {
            return Err("encounter creation requires an authored hero".into());
        };
        let definition = crate::npc_catalog::encounter_definition(
            creator.catalog,
            creator.graphs,
            creator.heroes,
            *hero,
        )
        .map_err(|error| error.to_string())?;
        let mover = creator.movers.for_body(Some(definition.body));
        self.bodies
            .create_enemy(crate::script_enemy::EnemySpawn {
                graphs: Arc::clone(creator.graphs),
                codecs: Arc::clone(creator.codecs),
                definition: &definition,
                request,
                mode: creator.mode,
                mode_variables: Arc::clone(&creator.mode_variables),
                tick: creator.tick,
                walk: BotWalk::from_mover(
                    mover.run_forward,
                    mover.accel_ground,
                    mover.decel_ground_base,
                ),
                gate: Some(crate::script_enemy::EnemyGate {
                    entity: self.gate.entity,
                    position: self.gate.position,
                }),
            })
            .map(Some)
    }
    fn creation_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.anchors
            .transforms
            .get(&entity)
            .map(|anchor| anchor.position)
    }
    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity == self.gate.entity {
            return Some(self.gate_variables.get(&id).cloned().unwrap_or(Value::Null));
        }
        None
    }

    fn static_target(&self, tag: u64) -> Option<u32> {
        match self.anchors.targets(tag)? {
            &[entity] => Some(entity),
            _ => None,
        }
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.anchors
            .transforms
            .get(&entity)
            .map(|anchor| anchor.position)
    }

    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        self.anchors.forward(entity)
    }

    fn team_members(&self, team: u8) -> Option<u32> {
        (team < 5).then_some(u32::from(team == self.team))
    }

    fn encounter_body_alive(&self, entity: u32) -> Option<bool> {
        Some(self.bodies.encounter_body_alive(entity))
    }

    fn has_target_tag(&self, site: Site, entity: u32, tag: u64) -> Option<bool> {
        if site.entity != GAME_MODE_ENTITY
            || !matches!(
                (site.graph, site.node, tag),
                (0x0c8c, Some(59), 0x0d60_0000_0000_011e)
                    | (0x0c8c, Some(61), 0x0d60_0000_0000_00f6)
                    | (0x0c8c, Some(58), 0x0d60_0000_0000_0120)
                    | (0x0c8c, Some(57), 0x0d60_0000_0000_00fa)
            )
        {
            return None;
        }
        self.bodies.encounter_body_has_target_tag(entity, tag)
    }

    fn participant_targets(&self, site: Site, filter: &serde_json::Value) -> Option<Vec<u32>> {
        let filter = filter.as_object()?;
        let player_query = (site.graph, site.node) == (0x0c8c, Some(231));
        let enemy_query = (site.graph, site.node) == (0x0cba, Some(56));
        let masks = [
            ("m_84065FD1", if player_query { 0x40000 } else { 1 }),
            (
                "m_1E14CCA8",
                if player_query { 0x800000 } else { 0x1000000 },
            ),
            ("m_4EA686DB", 0x10),
        ];
        if site.entity != GAME_MODE_ENTITY
            || !(player_query || enemy_query)
            || self.team != 0
            || filter
                .get("m_B486603E")
                .is_some_and(|value| !value.is_null())
            || !masks.iter().all(|(key, value)| {
                filter.get(*key).and_then(serde_json::Value::as_u64) == Some(*value)
            })
            || filter.iter().any(|(key, value)| {
                key != "$"
                    && !masks.iter().any(|(mask, _)| key == mask)
                    && !value.is_null()
                    && value.as_u64() != Some(0)
            })
        {
            return None;
        }
        Some(if player_query {
            vec![player_entity(0)]
        } else {
            self.bodies
                .bodies()
                .ok()?
                .filter(|bot| bot.spawn.team == 1 && !bot.dead)
                .map(|bot| bot.entity)
                .collect()
        })
    }

    fn native(&mut self, site: Site, config: &serde_json::Value) -> Option<Value> {
        if native_shape(config, "STU_A3DEF368", None) {
            return Some(Value::Entity(GAME_MODE_ENTITY));
        }
        if site.entity == GAME_MODE_ENTITY
            && site.graph == 0x0cc0
            && config["$"] == "STU_B3E67A36"
            && crate::script_expr::native_fields(
                config,
                &["m_filter", "m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"],
            )
            && ["m_filter", "m_9C7BD8B8", "m_8B2A4DF3"]
                .iter()
                .all(|key| config[key].is_null())
        {
            let tag = crate::script_expr::target_tag_config(&config["m_B486603E"])?;
            if matches!(
                tag,
                0x0d60_0000_0000_00fa
                    | 0x0d60_0000_0000_00ff
                    | 0x0d60_0000_0000_011e
                    | 0x0d60_0000_0000_0120
            ) {
                let entity = self.anchors.targets(tag)?.first().copied().or_else(|| {
                    self.bodies
                        .bodies()
                        .ok()?
                        .map(|bot| bot.entity)
                        .find(|&body| {
                            self.bodies.encounter_body_has_target_tag(body, tag) == Some(true)
                        })
                });
                return Some(Value::Entity(entity.unwrap_or(0)));
            }
        }
        // Bind the queue input at the original difficulty assignment.
        (site.entity == GAME_MODE_ENTITY
            && site.graph == 0x0c8c
            && site.node == Some(263)
            && native_shape(config, "STU_7BD2CA19", Some("m_resourceKey"))
            && config
                .get("m_resourceKey")
                .is_some_and(serde_json::Value::is_null))
        .then_some(Value::Asset(self.difficulty))
    }
}

#[derive(Default)]
struct GateState {
    team: Option<i8>,
    messages: Vec<crate::script_enemy::SceneMessage>,
    voice_requests: Vec<crate::junkenstein_voice::VoiceRequest>,
    voice_variation: u64,
}

struct GateWorld<'a> {
    entity: u32,
    state: &'a mut GateState,
    bodies: &'a RoomSimulation,
    mode_variables: &'a Variables,
}

impl World for GateWorld<'_> {
    fn engine_setting(&self, _site: Site, key: u32) -> crate::script_expr::EngineSetting {
        crate::junkenstein_host::engine_setting(key)
    }

    fn native(&mut self, _site: Site, config: &serde_json::Value) -> Option<Value> {
        native_shape(config, "STU_A3DEF368", None).then_some(Value::Entity(GAME_MODE_ENTITY))
    }
    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity == GAME_MODE_ENTITY {
            Some(self.mode_variables.get(&id).cloned().unwrap_or(Value::Null))
        } else {
            None
        }
    }
    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
    fn send_message(
        &mut self,
        _site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if target != GAME_MODE_ENTITY {
            return false;
        }
        self.state.messages.push(crate::script_enemy::SceneMessage {
            target,
            message,
            parameters: parameters.clone(),
        });
        true
    }
    fn assign_team(&mut self, site: Site, entity: u32, team: i8) -> bool {
        if site.entity != self.entity || entity != self.entity {
            return false;
        }
        self.state.team = Some(team);
        true
    }
    fn play_gate_voice(&mut self, site: Site, stimulus: u64, attacker: Option<u32>) -> Option<u64> {
        use crate::junkenstein_voice::{VoiceRequest, gate_lines, select_line};

        if site.entity != self.entity
            || site.graph != 0x0ca7
            || self.state.voice_requests.len() >= script_init::MAX_INIT_WORK
        {
            return None;
        }
        let attacker_hero = attacker.and_then(|entity| self.bodies.encounter_enemy_hero(entity));
        let (line, duration_ms) = select_line(
            gate_lines(stimulus, attacker_hero),
            self.entity,
            &mut self.state.voice_variation,
        )?;
        self.state.voice_requests.push(VoiceRequest {
            anchor: self.entity,
            speaker: GAME_MODE_ENTITY,
            stimulus,
            line,
        });
        Some(duration_ms)
    }
}

struct JunkensteinEncounter {
    voice_set: u64,
    graphs: Arc<GraphStore>,
    npcs: Option<Arc<crate::npc_catalog::NpcCatalog>>,
    heroes: Arc<HeroCatalog>,
    movers: Arc<MoverCatalog>,
    mode: u64,
    script: script_init::Component,
    codecs: Option<Arc<CodecTable>>,
    searches: SpawnSearches,
    gate_script: script_init::Component,
    gate_state: GateState,
    team: u8,
    difficulty: u64,
    ready_message: u64,
    ready_parameter: u32,
    first_tick: Option<u32>,
    ready_hero: Option<u64>,
    result_action: Option<Site>,
    screens_complete: bool,
}

impl JunkensteinEncounter {
    fn step(
        &mut self,
        (gate, anchors, bodies): (&JunkensteinGate, &SpawnAnchors, &mut RoomSimulation),
        (collision, spawn_mover): (&CollisionScene, &MoverSettings),
        tick: u32,
        hero: Option<u64>,
    ) -> Result<(), script_init::InitFault> {
        let first = *self.first_tick.get_or_insert(tick);
        let frame = tick.saturating_sub(first);
        for message in bodies.take_scene_messages() {
            if message.target == gate.entity {
                self.gate_script.queue_entity_frame_message(
                    frame,
                    message.message,
                    message.parameters,
                )?;
            } else if message.target == GAME_MODE_ENTITY {
                self.script.queue_entity_frame_message(
                    frame,
                    message.message,
                    message.parameters,
                )?;
            }
        }
        for (body, parameters) in bodies.take_boss_intro_starts() {
            self.script.queue_frame_message_from(
                frame,
                body,
                crate::junkenstein_messages::BOSS_INTRO_STARTED,
                parameters,
            )?;
        }
        self.searches
            .step(frame, &mut self.script, collision, spawn_mover)?;
        let mut gate_world = GateWorld {
            entity: gate.entity,
            state: &mut self.gate_state,
            bodies,
            mode_variables: &self.script.snapshot().expect("live encounter").variables,
        };
        let previous_gate_variables = self
            .gate_script
            .snapshot()
            .expect("live gate")
            .variables
            .clone();
        self.gate_script.advance_frame(frame, &mut gate_world)?;
        let gate_variables = &self
            .gate_script
            .snapshot()
            .expect("advanced gate")
            .variables;
        for id in previous_gate_variables
            .keys()
            .chain(gate_variables.keys())
            .copied()
            .collect::<BTreeSet<_>>()
        {
            if previous_gate_variables.get(&id) != gate_variables.get(&id) {
                self.script
                    .queue_foreign_variable_change(frame, gate.entity, id)?;
            }
        }
        for message in std::mem::take(&mut self.gate_state.messages) {
            self.script
                .queue_entity_frame_message(frame, message.message, message.parameters)?;
        }
        let gate_variables = &self
            .gate_script
            .snapshot()
            .expect("advanced gate script")
            .variables;
        let mut world = EncounterWorld {
            gate,
            anchors,
            gate_variables,
            bodies,
            creator: self.codecs.as_ref().map(|codecs| EnemyCreator {
                graphs: &self.graphs,
                codecs,
                catalog: self.npcs.as_deref(),
                heroes: &self.heroes,
                movers: &self.movers,
                mode: self.mode,
                tick,
                mode_variables: Arc::new(
                    self.script
                        .snapshot()
                        .expect("live encounter")
                        .variables
                        .clone(),
                ),
            }),
            result_action: &mut self.result_action,
            screens_complete: &mut self.screens_complete,
            team: self.team,
            difficulty: self.difficulty,
        };
        if let Some(hero) = hero.filter(|&hero| self.ready_hero != Some(hero)) {
            // The persistent 0C8C receiver aggregates its 0C90 sender as an array.
            let parameters =
                Variables::from([(self.ready_parameter, Value::Array(vec![Value::Asset(hero)]))]);
            if self.script.queue_frame_message(
                frame,
                ENCOUNTER_INSTANCE,
                self.ready_message,
                parameters,
            )? != 0
            {
                self.ready_hero = Some(hero);
            }
        }
        self.script.advance_frame(frame, &mut world)?;
        let mode = self.script.snapshot().expect("live encounter");
        world
            .bodies
            .set_mode_variables(Arc::new(mode.variables.clone()));
        world.bodies.set_mode_boss_pause(mode.boss_pause_active());
        Ok(())
    }
}

impl SceneAssets {
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
        Ok(Self {
            collision: runtime.join("collision"),
            junkenstein_map_world: runtime.join(JUNKENSTEIN_MAP_WORLD),
            last_collision: Mutex::new(None),
            trace_contacts: false,
            mode_props: read_mode_props(&data.join("collision_props_174.json"))?,
            graphs,
            codecs,
            practice_owner_body: OnceLock::new(),
            bots: BotCatalog::load(data.join("ai_spawners_174.json"))?,
            movers: Arc::new(MoverCatalog::load(data.join("game_movers_174.json"))?),
            stats: StatsCatalog::load(data.join("game_stats_174.json"))?,
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
            .junkenstein_gate
            .as_ref()
            .expect("prepared Junkenstein gate");
        let (shared, encounter) = self.encounter_roots(card.mode)?;
        let variables = self
            .graphs
            .mode_variables(card.mode)
            .ok_or_else(|| invalid("Junkenstein mode variables are missing"))?;
        let mut variables: Variables = variables
            .iter()
            .map(|(&key, &value)| {
                let value = match value {
                    ModeValue::Bool(value) => Value::Bool(value),
                    ModeValue::Integer(value) => Value::Integer(value),
                    ModeValue::Asset(value) => Value::Asset(value),
                };
                (key, value)
            })
            .collect();
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
                entity: gate.entity,
                state: &mut gate_state,
                bodies: &scene.simulation,
                mode_variables: &variables,
            },
            script_init::MAX_INIT_WORK,
        )
        .map_err(invalid)?;
        let gate_variables = &gate_script
            .snapshot()
            .expect("started gate script")
            .variables;
        if gate_state.team != Some(2) || gate_variables.get(&8571) != Some(&Value::Integer(1)) {
            return Err(invalid(
                "Junkenstein gate script did not initialize its team and health",
            ));
        }
        let mut result_action = None;
        let mut screens_complete = false;
        let mut startup = EncounterWorld {
            gate,
            anchors: &scene.junkenstein_anchors,
            gate_variables,
            bodies: &mut scene.simulation,
            creator: None,
            result_action: &mut result_action,
            screens_complete: &mut screens_complete,
            team: admitted_team,
            difficulty: card.difficulty,
        };
        let encounter = script_init::Component::start(
            Arc::clone(&self.graphs),
            GAME_MODE_ENTITY,
            variables,
            vec![
                Root::new(1, shared),
                Root::new(ENCOUNTER_INSTANCE, encounter),
            ],
            0,
            &mut startup,
            script_init::MAX_INIT_WORK,
        )
        .map_err(invalid)?;
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
            .filter(|&guid| guid == crate::junkenstein_voice::ENCOUNTER_VOICE_SET)
            .ok_or_else(|| invalid("Junkenstein narrator voice-set binding is unavailable"))?;
        scene.simulation.set_mode_voice_set(voice_set);
        scene.encounter = Some(JunkensteinEncounter {
            voice_set,
            graphs: Arc::clone(&self.graphs),
            heroes: Arc::clone(&self.heroes),
            npcs: match crate::npc_catalog::NpcCatalog::load(
                self.collision
                    .parent()
                    .expect("runtime root")
                    .join(crate::runtime::JUNKENSTEIN_NPCS),
            ) {
                Ok(catalog) => Some(Arc::new(catalog)),
                Err(error) if error.kind() == io::ErrorKind::NotFound => None,
                Err(error) => return Err(error),
            },
            movers: Arc::clone(&self.movers),
            mode: scene.map.mode_guid,
            script: encounter,
            codecs: self.codecs.clone(),
            searches: SpawnSearches::default(),
            gate_script,
            gate_state,
            team: admitted_team,
            difficulty: card.difficulty,
            ready_message,
            ready_parameter,
            first_tick: None,
            ready_hero: None,
            result_action,
            screens_complete,
        });
        if Some(card) == JunkensteinCard::from_index(0x3c) {
            scene.owner_body_factory = self.prepare_owner_body(&scene, CASSIDY);
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
            contacts,
        } = self.map_collision(
            &map,
            is_practice_range(&map)
                || map.is_junkenstein()
                || bots.iter().any(|bot| !bot.spawn.route.is_empty()),
        )?;
        if let Some(world) = &contacts {
            for bot in bots.iter_mut().filter(|bot| !bot.spawn.route.is_empty()) {
                let category = mover_category(&map, bot.spawn.team);
                bot.attach_collision(
                    self.movers.for_body(Some(bot.spawn.body)),
                    Arc::clone(world),
                    category,
                    ground_height(ground.triangles(), category),
                );
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
        let mode_ui = mode_presentation(&self.graphs, &Variables::new(), encounter)?;
        let initial_board =
            self.stats
                .component(&PlayerStats::default(), map.mode_guid, None, None);
        let junkenstein_anchors = Arc::new(junkenstein_anchors);
        let mut simulation = RoomSimulation::new(bots).map_err(io::Error::other)?;
        if junkenstein_gate.is_some() {
            simulation.set_spawn_anchors(Arc::clone(&junkenstein_anchors));
        }
        let mut scene = PreparedScene {
            map,
            #[cfg(any(test, feature = "inspector"))]
            movement_cache: _movement_cache,
            ground,
            contacts,
            controller,
            mode_ui,
            junkenstein_gate,
            junkenstein_anchors,
            encounter: None,
            initial_board,
            simulation,
            scripted_bots,
            movers: Arc::clone(&self.movers),
            heroes: Arc::clone(&self.heroes),
            pools: Arc::clone(&self.pools),
            owner_body_factory: None,
        };
        if is_practice_range(scene.map()) && scripted_bots == 0 {
            scene.owner_body_factory = self
                .practice_owner_body
                .get_or_init(|| self.prepare_owner_body(&scene, SOLDIER))
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

    fn map_collision(&self, map: &GameMap, contacts: bool) -> io::Result<MapCollision> {
        let key = CollisionKey {
            map: map.map_guid,
            mode: map.mode_guid,
            contacts,
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
        let cache = read_movement_cache(BufReader::new(File::open(path)?)).map_err(invalid)?;
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
        let contacts = contacts
            .then(|| ContactWorld::from_movement_cache(cache, identifiers).map(Arc::new))
            .transpose()
            .map_err(invalid)?;
        let collision = MapCollision {
            movement_cache,
            ground,
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
        let prepared = self
            .codecs
            .as_ref()
            .ok_or_else(|| "the owner statescript codec table is unavailable".to_owned())
            .and_then(|codecs| {
                let controller_on_fire =
                    initial_on_fire(&scene.initial_board).ok_or_else(|| {
                        "the controller's initial on-fire flag is unavailable".to_owned()
                    })?;
                let mover = scene
                    .player_spawn_mover(hero.body, 0)
                    .map_err(|error| error.to_string())?;
                PlayerBodyFactory::prepare(
                    Arc::clone(&self.graphs),
                    Arc::clone(codecs),
                    scene.map.mode_guid,
                    hero,
                    self.pools.hero_pools(hero.guid),
                    controller_on_fire,
                    OwnerBodySpawn {
                        slot: 0,
                        controller: 0xa000_0100,
                        entity: 0xa000_0101,
                        body_filter: team_bit(0).map_err(|error| error.to_string())?,
                        frame: FIRST_TICK,
                        pose: mover.pose(),
                        quanta: RETAIL_QUANTA,
                    },
                )
                .map_err(|error| error.to_string())
            });
        if let Err(reason) = &prepared {
            eprintln!(
                "{} owner statescript unavailable in {} ({reason})",
                hero.name, scene.map.name
            );
        }
        Some(Arc::new(move |hero, spawn| {
            if hero.guid != hero_guid {
                return Ok(None);
            }
            let factory = prepared
                .as_ref()
                .map_err(|reason| io::Error::new(io::ErrorKind::Unsupported, reason.clone()))?;
            let runtime: Box<dyn OwnerBodyRuntime> = Box::new(factory.start(spawn)?);
            Ok(Some(runtime))
        }))
    }
}

impl PreparedScene {
    pub(crate) fn enemy_projections(
        &self,
    ) -> Result<impl Iterator<Item = crate::script_snapshot_wire::SceneProjection<'_>>, CombatError>
    {
        self.simulation.enemy_projections()
    }
    pub(crate) fn gate_projection(
        &self,
    ) -> Option<crate::script_snapshot_wire::SceneProjection<'_>> {
        let encounter = self.encounter.as_ref()?;
        Some(crate::script_snapshot_wire::SceneProjection {
            snapshot: encounter.gate_script.snapshot()?,
            encoder: crate::script_snapshot_wire::SnapshotEncoder::new(
                &encounter.graphs,
                encounter.codecs.as_deref()?,
            ),
        })
    }
    pub(crate) fn take_enemy_creates(&mut self) -> Vec<EntityUpdate> {
        self.simulation.take_created()
    }
    pub(crate) fn take_encounter_voice_requests(
        &mut self,
    ) -> Vec<crate::junkenstein_voice::VoiceRequest> {
        let mut requests = self.encounter.as_mut().map_or_else(Vec::new, |encounter| {
            std::mem::take(&mut encounter.gate_state.voice_requests)
        });
        requests.extend(self.simulation.take_encounter_voice_requests());
        requests
    }
    pub fn map(&self) -> &GameMap {
        &self.map
    }

    pub fn scripted_bots(&self) -> usize {
        self.scripted_bots
    }

    pub(crate) fn movement_ready(&self) -> bool {
        (is_practice_range(&self.map) || self.map.is_junkenstein())
            && self.scripted_bots == 0
            && self.contacts.is_some()
    }

    pub(crate) fn movement_index(&self) -> &TriangleIndex {
        self.ground.triangles()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_geometry(&self) -> DebugGeometry {
        DebugGeometry::new(
            &self.map,
            self.movement_cache.clone(),
            MOVER_CATEGORY,
            Arc::clone(&self.ground),
            self.contacts.clone(),
        )
    }

    pub(crate) fn player_spawn_mover(&self, body: u64, team: u8) -> io::Result<Mover> {
        let (position, yaw) = self
            .map
            .spawn_point(team, 0)
            .ok_or_else(|| invalid("no spawn for the selected hero"))?;
        let yaw = (yaw * 65536.0 / 360.0).round_ties_even() as i32;
        let contacts = self.contacts.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::Unsupported,
                "the scene has no native player collision",
            )
        })?;
        Mover::new(
            *self.movers.for_body(Some(body)),
            Arc::clone(contacts),
            position,
            yaw,
            mover_category(&self.map, team),
        )
        .map_err(invalid)
    }

    pub(crate) fn owner_body_runtime(
        &self,
        hero: &Hero,
        spawn: OwnerBodySpawn,
    ) -> io::Result<Option<Box<dyn OwnerBodyRuntime>>> {
        self.owner_body_factory
            .as_ref()
            .map_or(Ok(None), |factory| factory(hero, spawn))
    }

    pub fn heroes(&self) -> &HeroCatalog {
        &self.heroes
    }

    pub fn pools(&self) -> &PoolCatalog {
        &self.pools
    }

    pub fn controller(&self) -> Controller {
        self.controller
    }

    pub fn mode_ui(&self) -> &BitWriter {
        &self.mode_ui
    }

    pub fn junkenstein_gate(&self) -> Option<&JunkensteinGate> {
        self.junkenstein_gate.as_ref()
    }

    #[cfg(test)]
    pub(crate) fn gate_snapshot(&self) -> Option<&script_init::Snapshot> {
        self.encounter.as_ref()?.gate_script.snapshot()
    }

    #[cfg(test)]
    pub(crate) fn set_test_gate_capacity(&mut self, capacity: f64) {
        let capacity = f64::from(capacity as f32);
        assert!(capacity.is_finite() && capacity > 0.0);
        let gate = self.junkenstein_gate.as_ref().unwrap();
        let encounter = self.encounter.as_mut().unwrap();
        let mut world = GateWorld {
            entity: gate.entity,
            state: &mut encounter.gate_state,
            bodies: &self.simulation,
            mode_variables: &encounter.script.snapshot().unwrap().variables,
        };
        encounter
            .gate_script
            .set_test_instance_variable(1, 8567, Value::Float(capacity), &mut world)
            .unwrap();
        encounter.gate_script.run_now(&mut world).unwrap();
        assert_eq!(
            encounter.gate_script.resolved_variable(
                0,
                crate::script_expr::Variable {
                    scope: crate::script_expr::Scope::Entity,
                    id: 7786,
                }
            ),
            Some(Value::Float(capacity))
        );
    }

    pub(crate) fn encounter_snapshot(&self) -> Option<&script_init::Snapshot> {
        self.encounter.as_ref()?.script.snapshot()
    }

    pub(crate) fn encounter_countdown(&self) -> Option<CountdownFrame> {
        let snapshot = self.encounter_snapshot()?;
        let instance = snapshot.instances.get(&ENCOUNTER_INSTANCE)?;
        if instance.states.get(&COUNTDOWN_STATES[0])?.activation == 0 {
            return None;
        }
        let value = snapshot
            .resolved_bindings
            .get(&(0, COUNTDOWN_VAR))
            .or_else(|| snapshot.variables.get(&COUNTDOWN_VAR))?
            .clone();
        Some(CountdownFrame {
            value,
            states: COUNTDOWN_STATES.map(|raw| {
                instance
                    .states
                    .get(&raw)
                    .filter(|state| state.active)?
                    .chase
                    .clone()
            }),
        })
    }

    pub(crate) fn encounter_countdown_wire(
        &self,
        countdown: &CountdownFrame,
    ) -> io::Result<Option<BitWriter>> {
        let Some(encounter) = &self.encounter else {
            return Ok(None);
        };
        let Some(codecs) = &encounter.codecs else {
            return Ok(None);
        };
        let instance = self
            .encounter_snapshot()
            .and_then(|snapshot| snapshot.instances.get(&ENCOUNTER_INSTANCE))
            .ok_or_else(|| invalid("missing Junkenstein encounter instance"))?;
        let graph = encounter
            .script
            .graph_store()
            .graph(instance.graph)
            .ok_or_else(|| invalid("missing Junkenstein encounter graph"))?;
        encounter_countdown_delta(graph, codecs, ENCOUNTER_INSTANCE, countdown)
            .map(Some)
            .map_err(invalid)
    }

    pub(crate) fn encounter_screens_complete(&self) -> bool {
        self.encounter
            .as_ref()
            .is_some_and(|encounter| encounter.screens_complete)
    }

    pub(crate) fn encounter_screen_delta(
        &self,
        previous: Option<&ProjectedSnapshot>,
    ) -> io::Result<Option<(ProjectedSnapshot, BitWriter)>> {
        let Some(encounter) = &self.encounter else {
            return Ok(None);
        };
        let (Some(snapshot), Some(codecs)) =
            (encounter.script.snapshot(), encounter.codecs.as_ref())
        else {
            return Ok(None);
        };
        let encoder = SnapshotEncoder::new(&encounter.graphs, codecs);
        let Some(current) = encoder.post_match_screens(snapshot).map_err(invalid)? else {
            return Ok(None);
        };
        let Some(payload) = encoder
            .post_match_delta(previous, &current)
            .map_err(invalid)?
        else {
            return Ok(None);
        };
        Ok(Some((current, payload)))
    }

    pub(crate) fn encounter_notification_delta(
        &self,
        previous: Option<&ProjectedSnapshot>,
    ) -> io::Result<Option<(ProjectedSnapshot, BitWriter)>> {
        let Some(encounter) = &self.encounter else {
            return Ok(None);
        };
        let (Some(snapshot), Some(codecs)) =
            (encounter.script.snapshot(), encounter.codecs.as_ref())
        else {
            return Ok(None);
        };
        let encoder = SnapshotEncoder::new(&encounter.graphs, codecs);
        let Some(current) = encoder.mission_notifications(snapshot).map_err(invalid)? else {
            return Ok(None);
        };
        let Some(payload) = encoder.mounted_delta(previous, &current).map_err(invalid)? else {
            return Ok(None);
        };
        Ok(Some((current, payload)))
    }

    pub(crate) fn encounter_phase_wire(
        &self,
        delivered: &std::collections::BTreeMap<u16, u16>,
    ) -> io::Result<Option<(u16, u16, BitWriter)>> {
        let Some(encounter) = &self.encounter else {
            return Ok(None);
        };
        let (Some(snapshot), Some(codecs)) =
            (encounter.script.snapshot(), encounter.codecs.as_ref())
        else {
            return Ok(None);
        };
        let Some(instance) = snapshot.instances.get(&ENCOUNTER_INSTANCE) else {
            return Ok(None);
        };
        let graph = encounter
            .graphs
            .graph(instance.graph)
            .ok_or_else(|| invalid("missing Junkenstein encounter graph"))?;
        // These are authored persistent sends, not the notification graph's
        // server-side receivers. The client-only HUD listens to the sends.
        for (&raw, state) in &instance.states {
            if !state.active
                || !matches!(
                    state.kind,
                    script_init::StateKind::PersistentSend(
                        0x0240_0000_0000_0694 | 0x0240_0000_0000_06a8
                    )
                )
                || delivered.get(&raw) == Some(&state.activation)
            {
                continue;
            }
            let payload = encounter_message_delta(
                graph,
                codecs,
                (ENCOUNTER_INSTANCE, None),
                raw,
                &Variables::new(),
                Variables::new(),
            )
            .map_err(invalid)?;
            return Ok(Some((raw, state.activation, payload)));
        }
        Ok(None)
    }

    pub(crate) fn encounter_active(&self) -> bool {
        self.encounter
            .as_ref()
            .is_some_and(|encounter| encounter.result_action.is_none())
    }

    pub(crate) fn encounter_match_result(&self) -> Option<Component> {
        let encounter = self.encounter.as_ref()?;
        encounter.result_action?;
        let snapshot = encounter.script.snapshot()?;
        let instance = snapshot.instances.get(&ENCOUNTER_INSTANCE)?;
        let Value::Integer(winner) = instance.variables.get(&7525)? else {
            return None;
        };
        let winner = i32::try_from(*winner).ok()?;
        // Retail MECGameMode: the winner at +168 is compared with the local
        // team; the native MatchTime binding reads the float at +170.
        let mut fields = vec![None; 10];
        fields[7] = Some(ComponentValue::I32(winner));
        fields[9] = Some(ComponentValue::F32(
            encounter.script.now_ms() as f32 / 1000.0,
        ));
        Some(Component { index: 114, fields })
    }

    pub(crate) fn encounter_post_match_wire(&self) -> io::Result<Option<BitWriter>> {
        let Some(encounter) = &self.encounter else {
            return Ok(None);
        };
        let (Some(snapshot), Some(codecs)) =
            (encounter.script.snapshot(), encounter.codecs.as_ref())
        else {
            return Ok(None);
        };
        let Some(instance) = snapshot.instances.get(&ENCOUNTER_INSTANCE) else {
            return Ok(None);
        };
        let raw = 8;
        if !instance.states.get(&raw).is_some_and(|state| {
            state.active && state.kind == script_init::StateKind::PersistentSend(POST_MATCH_PHASE)
        }) {
            return Ok(None);
        }
        let graph = encounter
            .graphs
            .graph(instance.graph)
            .ok_or_else(|| invalid("missing Junkenstein encounter graph"))?;
        let entity_variables = snapshot
            .variables
            .get(&8619)
            .map(|value| (8619, value.clone()))
            .into_iter()
            .collect();
        let instance_variables = instance
            .variables
            .get(&7525)
            .map(|value| (7525, value.clone()))
            .into_iter()
            .collect();
        encounter_message_delta(
            graph,
            codecs,
            (ENCOUNTER_INSTANCE, None),
            raw,
            &entity_variables,
            instance_variables,
        )
        .map(Some)
        .map_err(invalid)
    }

    pub(crate) fn encounter_result_wire(&self) -> io::Result<Option<BitWriter>> {
        use crate::junkenstein_messages::RESULT_SUMMARY;
        use crate::script_init::StateKind;

        let Some(encounter) = &self.encounter else {
            return Ok(None);
        };
        let (Some(snapshot), Some(codecs)) =
            (encounter.script.snapshot(), encounter.codecs.as_ref())
        else {
            return Ok(None);
        };
        let parent = (ENCOUNTER_INSTANCE, 13);
        let Some((&id, instance)) = snapshot
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == 0x0cc7 && instance.parent == Some(parent))
        else {
            return Ok(None);
        };
        let Some(raw) = [2, 11].into_iter().find(|raw| {
            instance.states.get(raw).is_some_and(|state| {
                state.active && state.kind == StateKind::PersistentSend(RESULT_SUMMARY)
            })
        }) else {
            return Ok(None);
        };
        let graph = encounter
            .graphs
            .graph(instance.graph)
            .ok_or_else(|| invalid("missing Junkenstein results graph"))?;
        let select = |variables: &Variables, ids: &[u32]| -> Variables {
            ids.iter()
                .filter_map(|&id| variables.get(&id).map(|value| (id, value.clone())))
                .collect()
        };
        let (entity_ids, instance_ids): (&[u32], &[u32]) = if raw == 2 {
            (&[8525, 8642, 8643], &[])
        } else {
            (&[1303], &[176, 11817])
        };
        encounter_message_delta(
            graph,
            codecs,
            (id, Some(parent)),
            raw,
            &select(&snapshot.variables, entity_ids),
            select(&instance.variables, instance_ids),
        )
        .map(Some)
        .map_err(invalid)
    }

    pub(crate) fn encounter_outcome_wire(&self, stamp: u32) -> io::Result<Option<BitWriter>> {
        let Some(encounter) = &self.encounter else {
            return Ok(None);
        };
        let Some(site) = encounter.result_action else {
            return Ok(None);
        };
        let graph = encounter
            .graphs
            .graph(site.graph)
            .ok_or_else(|| invalid("missing encounter outcome graph"))?;
        let position = site
            .node
            .ok_or_else(|| invalid("missing encounter outcome node"))?;
        mode_action_delta(graph, site.instance, position, stamp)
            .map(Some)
            .map_err(invalid)
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn encounter_debug(&self) -> Option<crate::game_debug::Encounter> {
        let encounter = self.encounter.as_ref()?;
        encounter.first_tick?;
        let snapshot = encounter.script.snapshot()?;
        let instance = snapshot.instances.get(&ENCOUNTER_INSTANCE)?;
        Some(crate::game_debug::Encounter {
            elapsed_ms: encounter.script.now_ms(),
            countdown_started: instance
                .states
                .get(&52)
                .is_some_and(|state| state.activation != 0),
        })
    }

    pub(crate) fn step_encounter(&mut self, tick: u32, hero: Option<u64>) {
        let Some(encounter) = &mut self.encounter else {
            return;
        };
        let gate = self
            .junkenstein_gate
            .as_ref()
            .expect("initialized Junkenstein gate");
        let spawn_mover = self.movers.for_body(Some(0x0400_0000_0000_16be));
        if let Err(error) = encounter.step(
            (gate, &self.junkenstein_anchors, &mut self.simulation),
            (&self.ground, spawn_mover),
            tick,
            hero,
        ) {
            eprintln!("Junkenstein encounter halted: {error}");
            self.simulation.set_mode_boss_pause(false);
            self.encounter = None;
        }
    }

    pub fn bots(&self) -> Result<impl ExactSizeIterator<Item = &Bot>, CombatError> {
        self.simulation.bodies()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn bot_actors(&self) -> Result<impl ExactSizeIterator<Item = Actor>, CombatError> {
        self.simulation.debug_actors()
    }

    pub(crate) fn deadeye_scene(
        &self,
        team: u8,
    ) -> Result<crate::script_player::DeadeyeScene, CombatError> {
        let queries = BotQueries::new(
            self.map.free_for_all,
            &self.movers,
            &self.ground,
            self.contacts.as_ref(),
        );
        let targets = self
            .simulation
            .living_enemies(team)?
            .map(|(bot, health)| crate::script_player::DeadeyeTarget {
                velocity: Some(bot.velocity),
                script_state: self.simulation.body_script_state(bot.entity),
                entity: bot.entity,
                pose: queries.target(bot),
                health,
            })
            .collect();
        Ok(crate::script_player::DeadeyeScene {
            health: Some(self.simulation.body_healths()?),
            targets,
            collision: Some(self.ground.clone()),
            view_subjects: self.simulation.boss_view_subjects()?,
        })
    }

    #[cfg(test)]
    pub(crate) fn invalidate_bot_component(&mut self) -> bool {
        self.simulation.invalidate_bot_component()
    }

    pub(crate) fn bot_simulation(&mut self) -> (&mut RoomSimulation, BotQueries<'_>) {
        if let Some(encounter) = &self.encounter {
            self.simulation.set_mode_voice_set(encounter.voice_set);
        }
        (
            &mut self.simulation,
            BotQueries::new(
                self.map.free_for_all,
                &self.movers,
                &self.ground,
                self.contacts.as_ref(),
            ),
        )
    }

    pub(crate) fn retire_projectiles(&mut self, connection: u32) {
        self.simulation.retire_projectiles(connection);
    }

    pub(crate) fn set_junkenstein_defenders(&mut self, defenders: Arc<[u32]>) {
        self.simulation.set_junkenstein_defenders(defenders);
    }

    pub(crate) fn clear_projectiles(&mut self) {
        self.simulation.clear_projectiles();
    }

    pub(crate) fn step_bots(
        &mut self,
        tick: u32,
        patrol: &PatrolScript,
    ) -> Result<(), CombatError> {
        let floor = self.ground.triangles();
        let map = &self.map;
        self.simulation.for_each_body_mut(|bot| {
            if let Err(error) = bot.step(
                tick,
                patrol,
                ground_height(floor, mover_category(map, bot.spawn.team)),
            ) {
                eprintln!(
                    "Patrol for bot {:08x} stopped at frame {tick}: {error}",
                    bot.entity
                );
            }
        })
    }

    pub fn initial_world(&self, viewer: &PlayerAppearance<'_>) -> io::Result<Vec<EntityUpdate>> {
        let mut updates = initial_world(
            &self.map,
            viewer,
            self.initial_board.clone(),
            std::iter::empty(),
        )?;
        updates.extend(self.simulation.initial_creates()?);
        Ok(updates)
    }
}

/// Map queries used by bot simulation without borrowing its entity storage.
pub(crate) struct BotQueries<'a> {
    free_for_all: bool,
    movers: &'a MoverCatalog,
    ground: &'a CollisionScene,
    contacts: Option<&'a Arc<ContactWorld>>,
}

impl<'a> BotQueries<'a> {
    pub(crate) fn new(
        free_for_all: bool,
        movers: &'a MoverCatalog,
        ground: &'a CollisionScene,
        contacts: Option<&'a Arc<ContactWorld>>,
    ) -> Self {
        Self {
            free_for_all,
            movers,
            ground,
            contacts,
        }
    }

    pub(crate) fn free_for_all(&self) -> bool {
        self.free_for_all
    }

    pub(crate) fn attach_objective_mover(&self, bot: &mut Bot) -> bool {
        let Some(contacts) = self.contacts else {
            return false;
        };
        bot.attach_collision(
            self.movers.for_body(Some(bot.spawn.body)),
            Arc::clone(contacts),
            MOVER_CATEGORY,
            ground_height(self.ground.triangles(), MOVER_CATEGORY),
        );
        true
    }

    pub(crate) fn step_gate_approach(
        &self,
        approach: &mut crate::junkenstein_navigation::GateApproach,
        bot: &mut Bot,
        tick: u32,
        speed_scale: f64,
    ) -> bool {
        approach.step(
            bot,
            tick,
            self.ground,
            self.movers.for_body(Some(bot.spawn.body)),
            speed_scale,
        )
    }

    pub(crate) fn step_reaper_exit(
        &self,
        exit: &mut crate::junkenstein_navigation::ReaperExit,
        bot: &mut Bot,
        tick: u32,
        can_walk: bool,
    ) -> io::Result<bool> {
        exit.step(bot, self.ground.triangles(), tick, can_walk, |bot| {
            self.contacts
                .map(|contacts| {
                    Mover::new(
                        *self.movers.for_body(Some(bot.spawn.body)),
                        Arc::clone(contacts),
                        bot.position,
                        bot.movement(tick).yaw,
                        MOVER_CATEGORY,
                    )
                    .map_err(invalid)
                })
                .transpose()
        })
    }

    pub(crate) fn step_boss_pursuit(
        &self,
        pursuit: &mut crate::junkenstein_navigation::BossPursuit,
        bot: &mut Bot,
        tick: u32,
        defenders: &[crate::script_enemy::EnemyTarget],
        arc: Option<crate::junkenstein_navigation::GrenadeArc>,
    ) -> Option<crate::junkenstein_navigation::BossTarget> {
        pursuit.step(
            bot,
            tick,
            self.ground,
            (self.movers.for_body(Some(bot.spawn.body)), arc),
            defenders,
            |origin, direction, range| {
                self.hitscan_wall(origin, direction, range)
                    .is_ok_and(|wall| wall.is_none())
            },
        )
    }

    pub(crate) fn pursue_caster_target(
        &self,
        pursuit: &mut crate::junkenstein_navigation::BossPursuit,
        bot: &mut Bot,
        tick: u32,
        target: &crate::script_enemy::EnemyTarget,
        reach: f64,
    ) {
        let eye = crate::game_combat::eye_height(bot.spawn.hero, false);
        pursuit.pursue(
            bot,
            tick,
            (self.ground, self.movers.for_body(Some(bot.spawn.body))),
            target,
            reach,
            |[x, y, z], pose| self.visible_aim([x, y + eye, z], pose).is_some(),
        );
    }

    pub(crate) fn target(&self, bot: &Bot) -> crate::game_hitscan::TargetPose {
        let settings = self.movers.for_body(Some(bot.spawn.body));
        crate::game_hitscan::TargetPose {
            feet: bot.position,
            height: settings.stand_height,
            radius: settings.stand_radius,
            eye: crate::game_combat::eye_height(bot.spawn.hero, false),
        }
    }

    pub(crate) fn hitscan_wall(
        &self,
        origin: [f64; 3],
        direction: [f64; 3],
        reach: f64,
    ) -> Result<Option<f64>, crate::collision_index::IndexError> {
        self.ground
            .cast_ray(origin, direction, reach, MOVER_CATEGORY)
            .map(|hit| hit.map(|hit| hit.distance))
    }

    pub(crate) fn summoner_beam_contacts(
        &self,
        origin: [f64; 3],
        direction: [f64; 3],
        reach: f64,
        targets: &[crate::script_enemy::EnemyTarget],
    ) -> [script_init::BeamContact; 2] {
        use crate::static_ray::capsule_entry_distance;
        let Ok(wall) = self.hitscan_wall(origin, direction, reach) else {
            return Default::default();
        };
        [0.0, 0.2].map(|radius| {
            let entity = targets
                .iter()
                .filter(|target| target.team == 0 && target.alive)
                .filter_map(|target| {
                    let [bottom, top] = target.pose.segment();
                    let contact = capsule_entry_distance(
                        origin,
                        direction,
                        reach,
                        bottom,
                        top,
                        target.pose.radius + radius,
                    )?;
                    wall.is_none_or(|wall| contact < wall)
                        .then_some((contact, target.entity))
                })
                .min_by(|(a, first), (b, second)| a.total_cmp(b).then_with(|| first.cmp(second)))
                .map(|(_, entity)| entity);
            script_init::BeamContact {
                entity,
                hit: entity.is_some() || wall.is_some(),
            }
        })
    }

    pub(crate) fn visible_aim(
        &self,
        origin: [f64; 3],
        target: crate::game_hitscan::TargetPose,
    ) -> Option<[f64; 3]> {
        use crate::static_ray::{capsule_entry_distance, norm};
        let [bottom, top] = target.segment();
        let [x, y, z] = target.feet;
        let aim = |point: [f64; 3]| {
            let delta = std::array::from_fn(|axis| point[axis] - origin[axis]);
            let distance = norm(delta);
            if distance <= 1e-9 {
                return None;
            }
            let direction = delta.map(|axis| axis / distance);
            let contact =
                capsule_entry_distance(origin, direction, distance, bottom, top, target.radius)?;
            self.hitscan_wall(origin, direction, contact)
                .is_ok_and(|wall| wall.is_none_or(|wall| wall >= contact))
                .then_some(direction)
        };
        let eye = [x, y + target.eye.min(target.height - 0.01), z];
        [eye, top, bottom].into_iter().find_map(aim).or_else(|| {
            let dx = x - origin[0];
            let dz = z - origin[2];
            let horizontal = dx.hypot(dz);
            if horizontal <= 1e-9 {
                return None;
            }
            let width = target.radius * 0.75 / horizontal;
            let middle = (bottom[1] + top[1]) * 0.5;
            aim([x + dz * width, middle, z - dx * width])
                .or_else(|| aim([x - dz * width, middle, z + dx * width]))
        })
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn junkenstein_participant_queries_use_live_room_bodies_and_reject_unknown_filters() {
        use serde_json::json;
        let catalog = BotCatalog::from_json(
            &json!({
                "heroes":{"0x01":{"name":"fixture","body":"0x02","health":100}},
                "maps":{"0x03":{"hints":{},"spawners":[
                    {"hero":"0x01","team":1,"position":[0,0,0],"rotation":[0,0,0,1],"graphs":[]},
                    {"hero":"0x01","team":1,"position":[0,0,0],"rotation":[0,0,0,1],"graphs":[]},
                    {"hero":"0x01","team":0,"position":[0,0,0],"rotation":[0,0,0,1],"graphs":[]},
                    {"hero":"0x01","team":1,"position":[0,0,0],"rotation":[0,0,0,1],"graphs":[]}
                ]}}
            })
            .to_string(),
        )
        .unwrap();
        let mut bodies = RoomSimulation::new(catalog.make(3, |_| BotWalk::default())).unwrap();
        bodies
            .for_each_body_mut(|bot| bot.dead = bot.entity == BOT_ENTITY + 1)
            .unwrap();
        let gate = JunkensteinGate {
            entity: 0x8000_0032,
            position: [0.0; 3],
            graph: 0x0ca7,
        };
        let world = EncounterWorld {
            gate: &gate,
            anchors: &SpawnAnchors::default(),
            gate_variables: &Variables::new(),
            bodies: &mut bodies,
            creator: None,
            result_action: &mut None,
            screens_complete: &mut false,
            team: 0,
            difficulty: 0,
        };
        let site = Site {
            entity: GAME_MODE_ENTITY,
            instance: 1,
            graph: 0x0cba,
            node: Some(56),
        };
        let filter = json!({"$":"STUConfigVarFilter","m_84065FD1":1,"m_1E14CCA8":0x1000000,"m_4EA686DB":0x10});
        assert_eq!(
            world.participant_targets(site, &filter),
            Some(vec![BOT_ENTITY, BOT_ENTITY + 3])
        );
        let mut unsupported = filter.clone();
        unsupported["m_condition"] = json!(true);
        assert_eq!(world.participant_targets(site, &unsupported), None);
        let mut unsupported = filter.clone();
        unsupported["m_1E14CCA8"] = json!(0x800000);
        assert_eq!(world.participant_targets(site, &unsupported), None);
        assert_eq!(
            world.participant_targets(Site { entity: 0, ..site }, &filter),
            None
        );
        let mut unsupported = filter.clone();
        unsupported["m_B486603E"] = json!(0);
        assert_eq!(world.participant_targets(site, &unsupported), None);
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 Junkenstein collision and graph data"]
    fn junkenstein_prepares_all_card_difficulties_and_challenges() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, MapCatalog},
            lobby_arcade::JUNKENSTEIN,
        };

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let maps = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
        for &index in JUNKENSTEIN {
            let card = JunkensteinCard::from_index(index).unwrap();
            let map = maps
                .game_map(
                    JUNKENSTEIN_MAP,
                    card.mode,
                    "Junkenstein's Revenge",
                    false,
                    &[card.players, 0],
                )
                .unwrap();
            assert!(map.placeables.contains(&50));
            let scene = assets
                .prepare_junkenstein(map, card, 0)
                .unwrap_or_else(|error| panic!("{index:03X}: {error}"));
            assert!(scene.movement_ready());
            let bytes = scene.mode_ui().as_bytes();
            let mut wire = crate::bits::BitReader::new(bytes);
            assert_eq!(wire.bits(2).unwrap(), 0); // observer full frame
            let snapshot = scene.encounter_snapshot().unwrap();
            let mut id = 0;
            let mut descriptors = Vec::new();
            loop {
                let delta = wire.w_u16().unwrap();
                if delta == 0 {
                    break;
                }
                id += delta;
                let graph = wire.bits(16).unwrap() as u32;
                assert_eq!(wire.bits(2).unwrap(), 0); // descriptor local to this entity
                let parent = if wire.bit().unwrap() {
                    Some((
                        wire.w_var().unwrap().try_into().unwrap(),
                        wire.w_var().unwrap().try_into().unwrap(),
                    ))
                } else {
                    None
                };
                if id == 1 {
                    // The client common UI root differs from the server common graph.
                    assert_eq!((graph, parent), (0x0cb6, None));
                } else {
                    assert_eq!(snapshot.instances[&id].graph, graph);
                    assert_eq!(snapshot.instances[&id].parent, parent);
                }
                assert!(!wire.bit().unwrap()); // no descriptor references
                descriptors.push((id, graph, parent));
                assert!(descriptors.len() <= snapshot.instances.len());
            }
            let hud = descriptors
                .iter()
                .find(|(_, graph, parent)| *graph == 0x0cbb && *parent == Some((2, 6)))
                .expect("common HUD descriptor")
                .0;
            for (graph, parent) in [
                (0x0cb6, None),
                (0x0c8c, None),
                (0x0cbb, Some((2, 6))),
                (0x03f2, Some((hud, 44))),
                (0x180a, Some((hud, 45))),
                (0x1188, Some((2, 68))),
            ] {
                assert!(
                    descriptors
                        .iter()
                        .any(|(_, actual, actual_parent)| *actual == graph
                            && *actual_parent == parent),
                    "card {index:03X} missing HUD descriptor {graph:04X} with parent {parent:?}"
                );
            }
            let gate = scene.junkenstein_gate().unwrap();
            assert_eq!(gate.entity, 0x8000_0032);
            assert_eq!(gate.graph, 0x0ca7);
            assert_eq!(
                gate.position,
                [89.34700012207031, 12.671699523925781, -62.7287712097168]
            );
            let snapshot = scene.encounter_snapshot().unwrap();
            assert!(!snapshot.diagnostics.iter().any(|diagnostic| matches!(&diagnostic.issue,
                script_init::Issue::Expression(crate::script_expr::EvalError::UnsupportedConfig(class))
                    if class=="STU_A753DC79")),"card {index:03X}: {:?}",snapshot.diagnostics);
            assert_eq!(
                snapshot.variables.get(&6751),
                Some(&Value::Asset(card.difficulty))
            );
            for flag in [0x7d53, 0x7d54, 0x7d60, 0x7d61, 0x7d62, 0x7d63] {
                assert_eq!(
                    snapshot.variables.get(&flag) == Some(&Value::Bool(true)),
                    card.challenge == Some(flag)
                );
            }
            assert_eq!(
                snapshot.variables.get(&0x7e0f),
                (index == 0x16f).then_some(&Value::Float(1.0))
            );
            assert_eq!(
                snapshot.variables.get(&8439),
                Some(&Value::Array(vec![Value::Entity(gate.entity)]))
            );
            assert!(
                !snapshot
                    .diagnostics
                    .iter()
                    .any(|diagnostic| diagnostic.site.graph == 0x0c8c
                        && matches!(diagnostic.site.node, Some(6 | 15 | 263))),
                "{:?}",
                snapshot.diagnostics
            );
        }
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_cassidy_receives_victory_view_and_result() {
        use crate::game_content::{JUNKENSTEIN_MAP, MapCatalog};
        use crate::{
            npc_catalog::{NpcCatalog, encounter_definition},
            script_enemy::EnemySpawn,
            script_init::{CreationKind, EntityCreation},
        };
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
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
        let boss = crate::junkenstein_navigation::ROADHOG;
        let catalog =
            NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        let definition =
            encounter_definition(Some(&catalog), &assets.graphs, scene.heroes(), boss).unwrap();
        let request = EntityCreation {
            position: [82.63870239257813, 12.682595252990723, -83.56700115966802],
            kind: CreationKind::Hero {
                hero: boss,
                team: 1,
                yaw: 180.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: Some(assets.graphs.enemy_pair(boss).unwrap().controller),
                skin: None,
            },
        };
        let subject = scene
            .bot_simulation()
            .0
            .create_enemy(EnemySpawn {
                graphs: Arc::clone(&assets.graphs),
                codecs: Arc::clone(assets.codecs.as_ref().unwrap()),
                definition: &definition,
                request: &request,
                mode: card.mode,
                mode_variables: Arc::new(Variables::from([
                    (6751, Value::Asset(card.difficulty)),
                    (9948, Value::Integer(0)),
                ])),
                tick: FIRST_TICK,
                walk: BotWalk::default(),
                gate: None,
            })
            .unwrap();
        assert_eq!(
            scene.simulation.boss_view_subjects().unwrap()[&subject]
                .tags
                .presence(0x0d60_0000_0000_011e),
            Some(true)
        );
        let hero = scene.heroes().find_hero("Cassidy").unwrap();
        let mover = scene.player_spawn_mover(hero.body, 0).unwrap();
        let entity = 0xa000_0101;
        let mut owner = scene
            .owner_body_runtime(
                hero,
                OwnerBodySpawn {
                    slot: 0,
                    controller: entity - 1,
                    entity,
                    body_filter: team_bit(0).unwrap(),
                    frame: FIRST_TICK,
                    pose: mover.pose(),
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .unwrap();
        for (message, params) in [
            (
                crate::junkenstein_messages::VICTORY_VIEW,
                Variables::from([(0x390, Value::Bool(true)), (8, Value::Entity(entity))]),
            ),
            (
                crate::junkenstein_messages::VICTORY_VIEW,
                Variables::from([(0x390, Value::Bool(false))]),
            ),
            (
                crate::junkenstein_messages::VICTORY_VIEW,
                Variables::from([(0x390, Value::Bool(true)), (8, Value::Entity(subject))]),
            ),
            (
                crate::junkenstein_messages::VICTORY_VIEW,
                Variables::from([(0x390, Value::Bool(false))]),
            ),
            (
                crate::junkenstein_messages::ENCOUNTER_RESULT,
                Variables::from([
                    (0x3ae, Value::Bool(true)),
                    (0x3ad, Value::Entity(subject)),
                    (0x3b4, Value::Bool(true)),
                    (0x3b5, Value::Integer(3)),
                ]),
            ),
        ] {
            owner.set_deadeye_scene(&scene, 0).unwrap();
            assert!(
                owner
                    .receive_defender_message(DefenderMessage {
                        target: None,
                        source: GAME_MODE_ENTITY,
                        message,
                        parameters: params,
                    })
                    .unwrap()
                    > 0
            );
            let snapshot = owner.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "message {message:X}: {:?}",
                snapshot.diagnostics
            );
            let encoder = owner.encoder();
            encoder
                .full(
                    &encoder
                        .project(snapshot, crate::script_snapshot_wire::View::Owner)
                        .unwrap(),
                )
                .unwrap();
        }
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_gate_damage_stages_publish_the_authored_animations() {
        use crate::game_content::{JUNKENSTEIN_MAP, MapCatalog};
        use crate::script_snapshot_wire::View;

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
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
        let mut previous = None;
        for (frame, health, raw, alias) in [
            (1, 1.0, 10, 0x050d),
            (2, 0.75, 20, 0x050f),
            (3, 0.5, 21, 0x0510),
            (4, 0.25, 22, 0x0511),
            (5, 0.0, 22, 0x0511),
        ] {
            if frame != 0 {
                let gate = scene.junkenstein_gate.as_ref().unwrap();
                let encounter = scene.encounter.as_mut().unwrap();
                encounter
                    .gate_script
                    .queue_entity_frame_message(
                        frame,
                        0x0240_0000_0000_06b2,
                        Variables::from([
                            (0x03ac, Value::Float(if frame == 1 { 0.0 } else { 250.0 })),
                            (0x12, Value::Entity(0)),
                        ]),
                    )
                    .unwrap();
                let mut world = GateWorld {
                    entity: gate.entity,
                    state: &mut encounter.gate_state,
                    bodies: &scene.simulation,
                    mode_variables: &encounter.script.snapshot().unwrap().variables,
                };
                encounter
                    .gate_script
                    .advance_frame(frame, &mut world)
                    .unwrap();
            }
            let snapshot = scene.gate_snapshot().unwrap();
            assert!(
                snapshot.diagnostics.is_empty(),
                "{frame}: {:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                crate::script_expr::to_float(&snapshot.variables[&8571]),
                health
            );
            assert_eq!(
                snapshot.instances[&1].states[&2].kind,
                script_init::StateKind::BooleanSwitch(health > 0.0)
            );
            let graph = assets.graphs.graph(0x0ca7).unwrap();
            let node = graph.node(raw).unwrap();
            assert!(
                snapshot.instances[&1]
                    .states
                    .get(&node.state.unwrap())
                    .is_some_and(|state| state.active),
                "frame {frame}: expected active gate animation {raw}; states={:?}",
                snapshot.instances[&1]
                    .states
                    .iter()
                    .map(|(&raw, state)| (raw, state.active))
                    .collect::<Vec<_>>()
            );
            assert_eq!(
                asset_guid(&node.fields["m_11E0A658"]["m_animAlias"]),
                Some(0x0c80_0000_0000_0000 | alias),
            );
            let projection = scene.gate_projection().unwrap();
            let observer = projection
                .encoder
                .project(projection.snapshot, View::Observer)
                .unwrap();
            projection.encoder.full(&observer).unwrap();
            if let Some(before) = previous {
                assert_ne!(
                    before, observer,
                    "damage stage did not change observer state"
                );
            }
            previous = Some(observer);
        }
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_cassidy_primary_and_fan_damage_all_boss_bodies() {
        use crate::junkenstein_navigation::{JUNKRAT, REAPER, ROADHOG, SUMMONER, WITCH};
        verify_normal_cassidy_damage_and_charge(
            &[REAPER, ROADHOG, JUNKRAT, WITCH, SUMMONER],
            &[(false, false), (false, true), (true, true)],
        );
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_cassidy_common_npc_damage_charge_and_retirement() {
        use crate::junkenstein_navigation::{SHOCK_TIRE, ZOMBARDIER};
        const ZOMNIC: u64 = 0x02e0_0000_0000_0175;
        verify_normal_cassidy_damage_and_charge(
            &[ZOMNIC, ZOMBARDIER, SHOCK_TIRE],
            &[(false, false)],
        );
    }

    fn verify_normal_cassidy_damage_and_charge(heroes: &[u64], variants: &[(bool, bool)]) {
        use crate::{
            commands::{Command, FIRE, SECONDARY},
            game_combat::{PosedShot, ShotContext},
            game_combat_messages::CombatNotice,
            game_content::{JUNKENSTEIN_MAP, MapCatalog},
            game_replication::BodyStep,
            npc_catalog::{NpcCatalog, encounter_definition},
            script_enemy::EnemySpawn,
            script_init::{CreationKind, EntityCreation, PlayScript},
        };
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let graphs = Arc::clone(&assets.graphs);
        let codecs = Arc::clone(assets.codecs.as_ref().unwrap());
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
        let cassidy = scene.heroes().find_hero("Cassidy").unwrap().clone();
        let mut retirement_failures = vec![];
        let mut score_total = 0.0_f32;
        let mut score_tick = FIRST_TICK;
        let catalog =
            NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        for &boss in heroes {
            let boss_body = crate::npc_catalog::is_junkenstein_boss(boss);
            let definition =
                encounter_definition(Some(&catalog), &graphs, scene.heroes(), boss).unwrap();
            for &(secondary, head) in variants {
                scene.simulation = RoomSimulation::new(vec![]).unwrap();
                scene
                    .simulation
                    .set_spawn_anchors(Arc::clone(&scene.junkenstein_anchors));
                let mover = Mover::new(
                    *scene.movers.for_body(Some(cassidy.body)),
                    Arc::clone(scene.contacts.as_ref().unwrap()),
                    [74.0, 12.6, -105.0],
                    0,
                    MOVER_CATEGORY,
                )
                .unwrap();
                let mut owner = scene
                    .owner_body_runtime(
                        &cassidy,
                        OwnerBodySpawn {
                            slot: 0,
                            controller: 0xa000_0100,
                            entity: 0xa000_0101,
                            body_filter: team_bit(0).unwrap(),
                            frame: FIRST_TICK,
                            pose: mover.pose(),
                            quanta: RETAIL_QUANTA,
                        },
                    )
                    .unwrap()
                    .unwrap();
                let request = EntityCreation {
                    position: [74.0, 12.6, -99.0],
                    kind: CreationKind::Hero {
                        hero: boss,
                        team: 1,
                        yaw: 180.0,
                        flags: [Value::Bool(false), Value::Bool(false)],
                        controller: Some(graphs.enemy_pair(boss).unwrap().controller),
                        skin: None,
                    },
                };
                let (room, queries) = scene.bot_simulation();
                let entity = room
                    .create_enemy(EnemySpawn {
                        graphs: Arc::clone(&graphs),
                        codecs: Arc::clone(&codecs),
                        definition: &definition,
                        request: &request,
                        mode: card.mode,
                        mode_variables: Arc::new(Variables::from([
                            (6751, Value::Asset(card.difficulty)),
                            (9948, Value::Integer(0)),
                        ])),
                        tick: FIRST_TICK,
                        walk: BotWalk::default(),
                        gate: None,
                    })
                    .unwrap_or_else(|error| panic!("Boss {boss:X}: {error}"));
                room.take_created();
                room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
                    hero: Some(CASSIDY),
                    filter_flags: owner.target_filter_flags(),
                    tags: owner.target_tags(),
                    health: owner.vitals().map(|vitals| vitals.total()),
                    velocity: Some(mover.pose().velocity),
                    script_state: crate::script_entity_state::EntityScriptState::Running(Arc::new(
                        owner.snapshot().unwrap().variables.clone(),
                    )),
                    entity: 0xa000_0101,
                    team: 0,
                    pose: crate::game_hitscan::TargetPose {
                        feet: mover.pose().position,
                        height: 1.8,
                        radius: 0.35,
                        eye: 1.55,
                    },
                    alive: true,
                }]);
                room.activate_combat();
                let caller = Site {
                    entity: GAME_MODE_ENTITY,
                    instance: 1,
                    graph: 0x0cba,
                    node: Some(9),
                };
                assert!(room.queue_encounter_wave(
                    caller,
                    entity + 1,
                    PlayScript {
                        policy: 0,
                        overrides: vec![],
                        graph: 0x0c95,
                        source: GAME_MODE_ENTITY,
                        parameters: vec![(
                            0x0a40_0000_0000_03a1,
                            Value::Array(vec![Value::Vector(request.position)])
                        )],
                        auto_retire: true,
                    }
                ));
                room.finish_tick(
                    &queries,
                    FIRST_TICK,
                    std::time::Duration::from_millis(u64::from(FIRST_TICK) * 16),
                )
                .unwrap();
                let maximum = room
                    .living_enemies(0)
                    .unwrap()
                    .find(|(bot, _)| bot.entity == entity)
                    .unwrap()
                    .1;
                let common_aim = if boss_body {
                    None
                } else {
                    let bot = room
                        .bodies()
                        .unwrap()
                        .find(|bot| bot.entity == entity)
                        .unwrap();
                    let target = queries.target(bot);
                    let delta = [
                        target.feet[0] - mover.pose().position[0],
                        target.feet[1] + target.height.min((target.eye - 0.2).max(0.1)) * 0.5
                            - mover.pose().position[1]
                            - 1.55,
                        target.feet[2] - mover.pose().position[2],
                    ];
                    let yaw =
                        (delta[0].atan2(delta[2]) / std::f64::consts::TAU * 65536.0).round() as i32;
                    let pitch = ((-delta[1]).atan2(delta[0].hypot(delta[2]))
                        / std::f64::consts::TAU
                        * 65536.0)
                        .round() as i32;
                    Some((yaw, pitch))
                };
                let mut shots = 0;
                let mut total_dealt = 0.0;
                let mut killed = false;
                let mut death_frame = None;
                let mut last_frame = FIRST_TICK;
                for step in 1..=4000 {
                    let fan = secondary && step <= 90;
                    let command = Command {
                        frame: FIRST_TICK + step,
                        yaw: common_aim.map_or(0, |aim| aim.0),
                        pitch: common_aim.map_or(if head { 0 } else { 954 }, |aim| aim.1),
                        buttons: if fan {
                            SECONDARY
                        } else if step == 1 || step > 90 {
                            FIRE
                        } else {
                            0
                        },
                        ..Command::default()
                    };
                    last_frame = command.frame;
                    owner.advance(command, &mover, BodyStep::Posed).unwrap();
                    for shot in owner.take_shots(Some(command), &mover) {
                        let PosedShot::Hitscan(shot) = shot else {
                            panic!("expected Cassidy hitscan")
                        };
                        if step <= 90 {
                            shots += 1;
                        }
                        assert_eq!(shot.hit.critical, if fan { 1.0 } else { 2.0 });
                        assert_eq!(shot.hit.amount, if fan { 50.0 } else { 70.0 });
                        let context = ShotContext {
                            team: 0,
                            quanta: RETAIL_QUANTA,
                            tick: command.frame,
                            now: std::time::Duration::from_millis(u64::from(command.frame) * 16),
                        };
                        assert!(
                            room.hitscan(&queries, *shot, context, |_| false)
                                .unwrap()
                                .is_none(),
                            "unacknowledged target received damage"
                        );
                        let hit = room.hitscan(&queries, *shot, context, |_| true).unwrap();
                        if killed {
                            assert!(hit.is_none(), "dead Boss received another hit");
                            continue;
                        }
                        let hit = hit.expect("visible Boss on the street must be hit");
                        assert_eq!(hit.health.entity, entity);
                        let before_credit = crate::script_expr::to_float(
                            &owner.snapshot().unwrap().variables[&342],
                        );
                        let mut stale_source = hit.damage.clone();
                        stale_source.source ^= 1;
                        owner.receive_applied_damage(stale_source).unwrap();
                        assert_eq!(
                            crate::script_expr::to_float(
                                &owner.snapshot().unwrap().variables[&342]
                            ),
                            before_credit,
                            "a foreign source cannot charge this owner"
                        );
                        owner.receive_applied_damage(hit.damage.clone()).unwrap();
                        let credited = owner.snapshot().unwrap();
                        assert!(
                            credited.initialization_complete(),
                            "{:?}",
                            credited.diagnostics
                        );
                        let after_credit = crate::script_expr::to_float(&credited.variables[&342]);
                        let cost = crate::script_expr::to_float(&credited.variables[&338]);
                        // Independent normal-mode expectation from 0B8B's
                        // ordered 59/97 target-tag branches, before 23's cap.
                        let factor = if hit
                            .damage
                            .target_tags
                            .presence(0x0d60_0000_0000_03c3)
                            .unwrap()
                        {
                            0.5_f32
                        } else if hit
                            .damage
                            .target_tags
                            .presence(0x0d60_0000_0000_011e)
                            .unwrap()
                        {
                            0.75_f32
                        } else {
                            1.0_f32
                        };
                        assert_eq!(
                            after_credit,
                            f64::from(
                                (before_credit as f32 + hit.damage.amount as f32 * factor)
                                    .min(cost as f32)
                            ),
                            "Boss {boss:X}: net pool loss, target reduction and charge cap"
                        );
                        let Some(CombatNotice::Hit {
                            target,
                            dealt,
                            critical,
                            killed: terminal,
                            shot_frame,
                        }) = hit.notices[0]
                        else {
                            panic!("missing hit feedback")
                        };
                        assert_eq!(
                            (target, critical, shot_frame),
                            (entity, head && !fan, shot.shot_frame)
                        );
                        let expected = (if fan {
                            50.0_f64
                        } else if head {
                            140.0_f64
                        } else {
                            70.0_f64
                        })
                        .min(maximum - total_dealt);
                        assert_eq!(dealt, expected);
                        total_dealt += dealt;
                        killed = terminal;
                        if terminal {
                            death_frame = Some(command.frame);
                        }
                        assert_eq!(terminal, total_dealt == maximum);
                        assert_eq!(
                            hit.notices[1],
                            terminal.then_some(CombatNotice::Elimination {
                                killer: shot.source,
                                victim: entity,
                            })
                        );
                        let current = room
                            .debug_actors()
                            .unwrap()
                            .find(|actor| actor.entity == entity)
                            .unwrap()
                            .health
                            .unwrap();
                        assert!((current - (maximum - total_dealt)).abs() < 0.001);
                        let Some(crate::world::Operation::Update(components)) =
                            hit.health.operation
                        else {
                            panic!("missing health component update")
                        };
                        let pools = components
                            .iter()
                            .find(|component| component.index == 51)
                            .unwrap();
                        let wire_health: f64 = pools
                            .fields
                            .iter()
                            .flatten()
                            .map(|value| {
                                let crate::world::ComponentValue::Blob20(bytes) = value else {
                                    panic!("health pool blob")
                                };
                                f64::from(f32::from_le_bytes(bytes[8..12].try_into().unwrap()))
                            })
                            .sum();
                        assert!(
                            (wire_health - current).abs() < 0.001,
                            "Boss health wire differs from room pools"
                        );
                    }
                    let snapshot = owner.snapshot().unwrap();
                    assert!(
                        snapshot.initialization_complete(),
                        "{:?}",
                        snapshot.diagnostics
                    );
                    if step == 90 {
                        assert_eq!(shots, if secondary { 6 } else { 1 });
                        let weapon = snapshot
                            .instances
                            .values()
                            .find(|instance| instance.graph == 0x01d2)
                            .unwrap();
                        assert_eq!(
                            weapon.variables[&53],
                            Value::Integer(if secondary { 0 } else { 5 })
                        );
                    }
                    if step >= 90 && killed {
                        break;
                    }
                }
                assert!(killed, "Boss {boss:X} survived ordinary Cassidy fire");
                assert!(!room.encounter_body_alive(entity));
                let mut voices = room.take_encounter_voice_requests();
                let mut retired = vec![];
                let death_frame = death_frame.unwrap();
                let mut score_carriers = Vec::new();
                for frame in last_frame + 1..=last_frame + 625 {
                    let replacements = room
                        .finish_tick(
                            &queries,
                            frame,
                            std::time::Duration::from_millis(u64::from(frame) * 16),
                        )
                        .unwrap();
                    assert!(
                        replacements.is_empty(),
                        "encounter Boss respawned as a training bot"
                    );
                    score_carriers.extend(room.take_scene_messages());
                    voices.extend(room.take_encounter_voice_requests());
                    retired.extend(room.take_retired_enemies());
                    if matches!(boss, 0x02e0_0000_0000_0175 | 0x02e0_0000_0000_0177) {
                        assert_eq!(
                            retired == vec![entity],
                            frame - death_frame >= 500,
                            "common corpse must retire at exactly the eight-second boundary"
                        );
                    }
                }
                use crate::junkenstein_voice::{self, BossSpeech};
                let defeat = voices
                    .iter()
                    .filter(|request| {
                        matches!(
                            junkenstein_voice::boss_speech(boss, None, request.stimulus),
                            Some(BossSpeech::Defeat(_))
                        )
                    })
                    .collect::<Vec<_>>();
                if boss_body {
                    assert_eq!(
                        defeat.len(),
                        1,
                        "Boss {boss:X} must submit exactly one defeat voice: {voices:?}"
                    );
                    let notice = defeat[0];
                    assert_eq!((notice.anchor, notice.speaker), (entity, GAME_MODE_ENTITY));
                    let Some(BossSpeech::Defeat(rows)) =
                        junkenstein_voice::boss_speech(boss, None, notice.stimulus)
                    else {
                        unreachable!()
                    };
                    assert!(rows.iter().any(|row| row.line == notice.line
                        && (row.killer.is_none() || row.killer == Some(CASSIDY))));
                    assert!(
                        junkenstein_voice::boss_duration(notice.line as u32, notice.stimulus, true)
                            .is_some_and(|duration| duration > 0.0)
                    );
                } else {
                    assert!(
                        defeat.is_empty(),
                        "common NPC cannot submit a Boss defeat cue"
                    );
                }
                if retired != vec![entity]
                    || room.bodies().unwrap().any(|body| body.entity == entity)
                {
                    retirement_failures.push((boss, secondary, head, retired));
                }
                // Independent 0CBC target-tag branches: 00F6 -> 200;
                // 00FA/00FF/011E/0120 -> 400; 031A -> 40; 0318/default -> 10.
                let expected_points = match boss as u16 {
                    0x2 => 200.0,
                    0x4 | 0x16 | 0x40 | 0x65 => 400.0,
                    0x175 | 0x177 => 10.0,
                    0x176 => 40.0,
                    _ => panic!("unestablished score fixture hero {boss:X}"),
                };
                let points: Vec<_> = score_carriers
                    .iter()
                    .filter(|message| message.message == 0x0240_0000_0000_06a4)
                    .map(|message| {
                        assert_eq!(message.target, GAME_MODE_ENTITY);
                        crate::script_expr::to_float(&message.parameters[&0x39e])
                    })
                    .collect();
                assert_eq!(
                    points,
                    [expected_points],
                    "hero {boss:X}: one authored base score per death"
                );
                for message in &score_carriers {
                    let parameter = match message.message {
                        0x0240_0000_0000_06a4 => Some(0x39e),
                        0x0240_0000_0000_06c8 => Some(0x3af),
                        _ => None,
                    };
                    if let Some(parameter) = parameter {
                        score_total +=
                            crate::script_expr::to_float(&message.parameters[&parameter]) as f32;
                    }
                }
                // Return real body messages to the normal scene delivery path;
                // no score values or substitute messages are injected.
                for message in score_carriers {
                    room.queue_scene_message(message);
                }
                score_tick += 1;
                scene.step_encounter(score_tick, Some(CASSIDY));
                let snapshot = scene.encounter_snapshot().unwrap();
                let expected_score = f64::from(score_total * 1.2_f32);
                assert_eq!(
                    snapshot.resolved_bindings.get(&(0, 2499)),
                    Some(&Value::Float(expected_score)),
                    "hero {boss:X}: actual mode accumulation after Cassidy defeats the NPC"
                );
                let (projected, payload) =
                    scene.encounter_notification_delta(None).unwrap().unwrap();
                assert_eq!(
                    projected.entity_variables().get(&2499),
                    Some(&Value::Float(expected_score))
                );
                assert!(payload.bit_len() > 0);
            }
        }
        assert!(
            retirement_failures.is_empty(),
            "Boss death retirement failures (hero, fan, head, retired): {retirement_failures:?}"
        );
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein collision and graph data"]
    fn normal_cassidy_flashbang_input_produces_authored_gravity_and_range_blast() {
        use crate::{
            game_combat::PosedShot,
            game_content::{JUNKENSTEIN_MAP, MapCatalog},
            game_replication::BodyStep,
            projectile_flight::{ProjectileFlight, lifted_direction},
        };
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
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
        let scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        let hero = scene.heroes().find_hero("Cassidy").unwrap();
        let mover = scene.player_spawn_mover(hero.body, 0).unwrap();
        let mut owner = scene
            .owner_body_runtime(
                hero,
                OwnerBodySpawn {
                    slot: 0,
                    controller: 0xa000_0100,
                    entity: 0xa000_0101,
                    body_filter: team_bit(0).unwrap(),
                    frame: FIRST_TICK,
                    pose: mover.pose(),
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .unwrap();
        let mut launches = Vec::new();
        for step in 1..=800 {
            owner
                .advance(
                    crate::commands::Command {
                        frame: FIRST_TICK + step,
                        buttons: if matches!(step, 1 | 50 | 700) { 16 } else { 0 },
                        ..Default::default()
                    },
                    &mover,
                    BodyStep::Posed,
                )
                .unwrap();
            for shot in owner.take_shots(
                Some(crate::commands::Command {
                    frame: FIRST_TICK + step,
                    ..Default::default()
                }),
                &mover,
            ) {
                let PosedShot::Projectile(projectile) = shot else {
                    panic!("Flashbang must be a projectile")
                };
                launches.push(*projectile);
            }
        }
        assert_eq!(
            launches.len(),
            2,
            "early cooldown input must be rejected; later input must recover"
        );
        assert!(launches[0].shot.frame < FIRST_TICK + 50);
        assert!(
            launches[1].shot.frame >= FIRST_TICK + 700,
            "launch frames {:?}, first tick {FIRST_TICK}",
            launches
                .iter()
                .map(|launch| launch.shot.frame)
                .collect::<Vec<_>>()
        );
        let launch = launches.remove(0);
        assert_eq!((launch.shot.graph, launch.shot.ordinal), (0x01e9, 0));
        assert_eq!(launch.stun, crate::game_combat::Stun::from_seconds(0.8));
        assert_eq!(launch.stun.unwrap().ticks, 50);
        assert_eq!(launch.gravity, Some(20.0));
        assert!(launch.expiry_blast);
        assert_eq!(launch.ballistics.speed, 30.0);
        assert!((launch.ballistics.lifetime - 7.0 / 30.0).abs() < 1e-8);
        assert_eq!(launch.ballistics.definition, Some(0x0400_0000_0000_037d));
        let blast = launch.ballistics.blast.as_ref().unwrap();
        assert_eq!(blast.hit.amount, 25.0);
        assert_eq!(blast.rings, [(3.0, 1.0)]);
        assert_eq!(
            launch.direction,
            lifted_direction([0.0, 0.0, 1.0], 0.05).unwrap()
        );
        let mut flight =
            ProjectileFlight::new(launch.ballistics, launch.origin, launch.direction, Some(0))
                .unwrap()
                .with_gravity(launch.gravity.unwrap())
                .with_expiry_blast(launch.expiry_blast);
        let target = crate::game_hitscan::TargetPose {
            feet: [
                launch.origin[0] + 2.0,
                launch.origin[1] - 1.55,
                launch.origin[2] + 7.0,
            ],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        };
        let end = (1..=30)
            .find_map(|tick| {
                flight
                    .advance(tick, &[(42, target)], |_, _, _| Ok(None))
                    .unwrap()
            })
            .expect("Flashbang range expiry");
        assert!(end.expired);
        assert_eq!(
            end.impacts
                .iter()
                .map(|hit| (hit.entity, hit.damage))
                .collect::<Vec<_>>(),
            [(42, 25.0)]
        );
        let snapshot = owner.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let encoder = owner.encoder();
        let projected = encoder
            .project(snapshot, crate::script_snapshot_wire::View::Owner)
            .unwrap();
        encoder.full(&projected).unwrap();
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein collision and graph data"]
    fn normal_cassidy_roll_moves_without_stopping_the_owner_vm() {
        use crate::{
            commands::Command,
            game_content::{JUNKENSTEIN_MAP, MapCatalog},
            game_replication::BodyStep,
            script_snapshot_wire::View,
        };
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
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
        let scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        use crate::{
            collision_cache::{Mesh, Triangle},
            collision_index::SceneTriangle,
            contact_mesh::ContactMesh,
            contact_world::{StaticGeometry, StaticShape},
        };
        let vertices = [
            [-50.0, 0.0, -50.0],
            [-50.0, 0.0, 50.0],
            [50.0, 0.0, 50.0],
            [50.0, 0.0, -50.0],
        ];
        let corners = [[0, 1, 2], [0, 2, 3]];
        let floor = TriangleIndex::new(corners.map(|indices| SceneTriangle {
            vertices: indices.map(|i| vertices[i]),
            mask: 0x18,
            surface: 0,
        }))
        .unwrap();
        let mesh = Mesh {
            vertices: vertices.map(|v| v.map(|x| x as f32)).to_vec(),
            triangles: corners
                .map(|indices| Triangle {
                    corners: indices.map(|i| i as i32),
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                })
                .to_vec(),
        };
        let contacts = Arc::new(
            ContactWorld::new(vec![
                StaticShape::new(
                    StaticGeometry::Mesh(Arc::new(ContactMesh::new(mesh, [1.0; 3], 0x18).unwrap())),
                    None,
                )
                .unwrap(),
            ])
            .unwrap(),
        );
        let cassidy = scene.heroes().find_hero("Cassidy").unwrap();
        for (case, yaw, forward, right, expected) in [
            (0, 0, 0, 0, [0.0, 15.0]),
            (1, 0, 0, 0, [0.0, 15.0]),
            (2, 0, 0, 127, [-15.0, 0.0]),
            (3, 0, -127, 0, [0.0, -15.0]),
            (4, 16384, 127, 0, [15.0, 0.0]),
            (
                5,
                0,
                127,
                127,
                [-15.0 / 2.0_f64.sqrt(), 15.0 / 2.0_f64.sqrt()],
            ),
        ] {
            let mut mover = if case == 0 {
                scene.player_spawn_mover(cassidy.body, 0).unwrap()
            } else {
                Mover::new(
                    *scene.movers.for_body(Some(cassidy.body)),
                    Arc::clone(&contacts),
                    [0.0; 3],
                    yaw,
                    0x18,
                )
                .unwrap()
            };
            let mut owner = scene
                .owner_body_runtime(
                    cassidy,
                    OwnerBodySpawn {
                        slot: 0,
                        controller: 0xa000_0100,
                        entity: 0xa000_0101,
                        body_filter: team_bit(0).unwrap(),
                        frame: FIRST_TICK,
                        pose: mover.pose(),
                        quanta: RETAIL_QUANTA,
                    },
                )
                .unwrap()
                .unwrap();

            let start = mover.pose().position;
            let rays = if case == 0 {
                scene.movement_index()
            } else {
                &floor
            };
            let mut fastest: f64 = 0.0;
            for step in 1..=130 {
                let command = Command {
                    frame: FIRST_TICK + step,
                    yaw,
                    buttons: if step == 5 || step == 125 {
                        1
                    } else if step == 50 {
                        8
                    } else {
                        0
                    },
                    forward: if step >= 50 { forward } else { 0 },
                    right: if step >= 50 { right } else { 0 },
                    ..Default::default()
                };
                mover
                    .step(rays, command, 0.016, |mover, command| {
                        owner.advance(command, mover, BodyStep::Posed).unwrap();
                        let snapshot = owner.snapshot().unwrap();
                        assert!(
                            snapshot.initialization_complete(),
                            "case {case} step {step}: {:?}",
                            snapshot.diagnostics
                        );
                        owner.encoder().project(snapshot, View::Owner).unwrap();
                        for (frame, change) in owner.take_velocity_changes() {
                            mover.queue_velocity_change(frame, change);
                        }
                        mover.track_modifiers(command.frame, owner.movement_modifiers().unwrap());
                    })
                    .unwrap();
                let weapon = owner
                    .snapshot()
                    .unwrap()
                    .instances
                    .values()
                    .find(|i| i.graph == 0x01d2)
                    .unwrap();
                if step == 45 {
                    assert_eq!(weapon.variables[&53], Value::Integer(5));
                }
                if step == 90 {
                    assert_eq!(weapon.variables[&53], Value::Integer(6));
                }
                if step == 130 {
                    assert_eq!(weapon.variables[&53], Value::Integer(5));
                }
                let [x, _, z] = mover.pose().velocity;
                fastest = fastest.max(x.hypot(z));
                if case != 0 && step == 51 {
                    assert!(
                        (x - expected[0]).abs() < 0.02 && (z - expected[1]).abs() < 0.02,
                        "case {case}: {x}, {z}"
                    );
                }
                if case == 1 && step == 50 {
                    assert_eq!(mover.pose().position, start);
                }
                if case == 1 && step == 90 {
                    let distance = mover.pose().position[2] - start[2];
                    assert!((5.8..6.5).contains(&distance), "Roll distance {distance}");
                    assert!(x.hypot(z) <= 5.5);
                }
            }
            assert!(fastest > 10.0, "Roll never accelerated: {fastest}");
            if case == 0 {
                let end = mover.pose().position;
                let distance = (end[0] - start[0]).hypot(end[2] - start[2]);
                assert!(
                    (1.0..2.0).contains(&distance),
                    "Spawn collision displacement: {distance}"
                );
            }
        }
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 Junkenstein collision and graph data"]
    fn normal_cassidy_charges_locks_and_fires_the_authored_deadeye() {
        use crate::{
            commands::Command,
            game_combat::{PosedShot, ShotContext},
            game_content::{JUNKENSTEIN_MAP, MapCatalog},
            game_hitscan::aim_direction,
            game_replication::BodyStep,
            script_snapshot_wire::View,
            script_stream::{BodyStream, ChangeKey, DeltaKind},
        };

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
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
        let cassidy = scene.heroes().find_hero("Cassidy").unwrap();
        let mover = scene.player_spawn_mover(cassidy.body, 0).unwrap();
        let mut owner = scene
            .owner_body_runtime(
                cassidy,
                OwnerBodySpawn {
                    slot: 0,
                    controller: 0xa000_0100,
                    entity: 0xa000_0101,
                    body_filter: team_bit(0).unwrap(),
                    frame: FIRST_TICK,
                    pose: mover.pose(),
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .unwrap();
        let initial = owner.snapshot().unwrap();
        let (&defender_id, defender) = initial
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == 0x0ca5)
            .unwrap();
        assert!(
            defender.states[&59].active,
            "full defender startup must mount the body-field stack"
        );
        assert_eq!(
            initial.resolved_bindings.get(&(defender_id, 17630)),
            Some(&Value::Entity(0))
        );
        let charge = |owner: &dyn crate::game_replication::OwnerBodyRuntime| {
            crate::script_expr::to_float(
                owner
                    .snapshot()
                    .unwrap()
                    .variables
                    .get(&342)
                    .unwrap_or(&Value::Null),
            )
        };
        let cost = crate::script_expr::to_float(&owner.snapshot().unwrap().variables[&338]);
        assert!(cost > 0.0);
        assert_eq!(charge(&*owner), 0.0);
        let fill_steps = (cost / 5.0).ceil() as u32 * 1000 / 16 + 1;
        for step in 1..=fill_steps {
            owner
                .advance(
                    Command {
                        frame: FIRST_TICK + step,
                        ..Default::default()
                    },
                    &mover,
                    BodyStep::Posed,
                )
                .unwrap();
            if step == 1 {
                assert_eq!(charge(&*owner), 0.0);
            }
            if step == 62 {
                assert_eq!(charge(&*owner), 0.0);
            }
            if step == 63 {
                assert_eq!(charge(&*owner), 5.0);
            }
        }
        assert_eq!(charge(&*owner), cost);
        let snapshot = owner.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let encoder = owner.encoder();
        let idle = encoder.project(snapshot, View::Owner).unwrap();
        let mut stream = BodyStream::new(snapshot.entity);
        stream.create_arrived();
        let baseline = encoder.queue_full(&mut stream, &idle).unwrap();
        stream.arrived(baseline.last);
        let initial = encoder
            .queue_delta(
                &mut stream,
                &idle,
                DeltaKind::InitialOwner(FIRST_TICK + fill_steps),
                16,
            )
            .unwrap()
            .unwrap();
        stream.sent(initial.last);
        stream.arrived(initial.last);
        let pose = mover.pose();
        let forward = aim_direction(&Command {
            yaw: pose.yaw,
            pitch: pose.pitch,
            ..Command::default()
        });
        let position = |distance: f64| -> [f64; 3] {
            std::array::from_fn(|axis| pose.position[axis] + distance * forward[axis])
        };
        let [front_x, front_y, front_z] = position(2.0);
        let off_axis = [
            front_x + 0.8 * forward[2],
            front_y,
            front_z - 0.8 * forward[0],
        ];
        let [left_x, left_y, left_z] = position(2.2);
        let left_of_aim = [left_x - 0.9 * forward[2], left_y, left_z + 0.9 * forward[0]];
        let [x, y, z] = pose.position;
        let eye = [
            x,
            y + crate::game_combat::eye_height(cassidy.guid, false),
            z,
        ];
        let wall = scene
            .ground
            .cast_ray(eye, forward, 190.0, MOVER_CATEGORY)
            .unwrap()
            .expect("the Normal Cassidy spawn faces castle geometry");
        let bot_catalog = BotCatalog::from_json(
            &serde_json::json!({
                "heroes": {"0x01": {"name": "Fixture body", "body": "0x02", "health": 1000}},
                "maps": {"0x03": {"hints": {}, "spawners": [
                    {"hero": "0x01", "team": 1, "position": off_axis,
                     "rotation": [0, 0, 0, 1], "graphs": []},
                    {"hero": "0x01", "team": 0, "position": position(3.0),
                     "rotation": [0, 0, 0, 1], "graphs": []},
                    {"hero": "0x01", "team": 1, "position": position(-3.0),
                     "rotation": [0, 0, 0, 1], "graphs": []},
                    {"hero": "0x01", "team": 1, "position": position(220.0),
                     "rotation": [0, 0, 0, 1], "graphs": []},
                    {"hero": "0x01", "team": 1, "position": position(wall.distance + 4.0),
                     "rotation": [0, 0, 0, 1], "graphs": []},
                    {"hero": "0x01", "team": 1, "position": left_of_aim,
                     "rotation": [0, 0, 0, 1], "graphs": []}
                ]}}
            })
            .to_string(),
        )
        .unwrap();
        let bots = bot_catalog.make(3, |_| BotWalk::default());
        let target = bots[0].entity;
        let occluded = bots[4].entity;
        let second = bots[5].entity;
        scene.simulation = RoomSimulation::new(bots).unwrap();
        owner.set_deadeye_scene(&scene, 0).unwrap();
        owner
            .advance(
                Command {
                    frame: FIRST_TICK + (fill_steps + 1),
                    buttons: 0x20,
                    ..Command::default()
                },
                &mover,
                BodyStep::Posed,
            )
            .unwrap();
        assert_eq!(charge(&*owner), 0.0);
        for step in (fill_steps + 2)..=(fill_steps + 41) {
            owner
                .advance(
                    Command {
                        frame: FIRST_TICK + step,
                        ..Command::default()
                    },
                    &mover,
                    BodyStep::Posed,
                )
                .unwrap();
        }
        let snapshot = owner.snapshot().unwrap();
        assert_eq!(
            snapshot.variables.get(&2551),
            Some(&Value::Array(vec![
                Value::Entity(target),
                Value::Entity(second),
                Value::Entity(occluded)
            ])),
            "{:?}",
            snapshot.diagnostics
        );
        assert!(!snapshot.diagnostics.iter().any(|diagnostic| {
            diagnostic.site.graph == 0x022f && matches!(diagnostic.site.node, Some(26 | 86))
        }));
        assert_eq!(
            snapshot.variables.get(&5874),
            Some(&Value::Array(vec![
                Value::Entity(target),
                Value::Entity(second)
            ]))
        );
        let effects = snapshot
            .instances
            .iter()
            .filter(|(_, instance)| instance.graph == 0x01ee)
            .map(|(&id, instance)| (id, instance.variables[&536].clone()))
            .collect::<Vec<_>>();
        assert_eq!(
            effects
                .iter()
                .map(|(_, target)| target.clone())
                .collect::<Vec<_>>(),
            [
                Value::Entity(target),
                Value::Entity(second),
                Value::Entity(occluded)
            ]
        );
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let encoder = owner.encoder();
        let lock_snapshot = encoder.project_next(snapshot, &idle).unwrap();
        encoder.full(&lock_snapshot).unwrap();
        let lock = encoder
            .queue_delta(
                &mut stream,
                &lock_snapshot,
                DeltaKind::Correction(FIRST_TICK + (fill_steps + 41)),
                16,
            )
            .unwrap()
            .unwrap();
        let sent = stream.sent(lock.last).unwrap();
        for &(instance, _) in &effects {
            assert!(sent.changes.contains(&ChangeKey::Instance(instance)));
        }
        stream.arrived(lock.last);
        let roster = std::mem::replace(
            &mut scene.simulation,
            RoomSimulation::new(Vec::new()).unwrap(),
        );
        owner.set_deadeye_scene(&scene, 0).unwrap();
        owner
            .advance(
                Command {
                    frame: FIRST_TICK + (fill_steps + 42),
                    ..Command::default()
                },
                &mover,
                BodyStep::Posed,
            )
            .unwrap();
        for variable in [2551, 5874] {
            assert_eq!(
                owner.snapshot().unwrap().variables.get(&variable),
                Some(&Value::Array(Vec::new()))
            );
        }
        assert!(
            !owner
                .snapshot()
                .unwrap()
                .instances
                .values()
                .any(|instance| instance.graph == 0x01ee)
        );
        scene.simulation = roster;
        let mut hit_entities = Vec::new();
        let mut last_damage = None;
        for step in (fill_steps + 43)..=(fill_steps + 400) {
            owner.set_deadeye_scene(&scene, 0).unwrap();
            let command = Command {
                frame: FIRST_TICK + step,
                buttons: if step == (fill_steps + 343) { 0x20 } else { 0 },
                yaw: pose.yaw,
                pitch: pose.pitch,
                ..Command::default()
            };
            owner.advance(command, &mover, BodyStep::Posed).unwrap();
            let snapshot = owner.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{step}: {:?}",
                snapshot.diagnostics
            );
            owner
                .encoder()
                .project_next(snapshot, &idle)
                .unwrap_or_else(|error| panic!("{step}: {error}"));
            for shot in owner.take_shots(Some(command), &mover) {
                let PosedShot::Hitscan(shot) = shot else {
                    panic!("Deadeye must fire the authored hitscan");
                };
                let (simulation, queries) = scene.bot_simulation();
                let context = ShotContext {
                    team: 0,
                    quanta: RETAIL_QUANTA,
                    tick: command.frame,
                    now: std::time::Duration::ZERO,
                };
                if let Some(impact) = simulation
                    .hitscan(&queries, *shot, context, |_| true)
                    .unwrap()
                {
                    last_damage = Some(impact.damage.clone());
                    owner.receive_applied_damage(impact.damage).unwrap();
                    assert_eq!(charge(&*owner), 0.0, "Deadeye must not recharge itself");
                    hit_entities.push(impact.health.entity);
                }
            }
        }
        hit_entities.sort_unstable();
        assert_eq!(hit_entities, [target, second]);
        assert!(!scene.simulation.encounter_body_alive(target));
        assert!(!scene.simulation.encounter_body_alive(second));
        assert!(scene.simulation.encounter_body_alive(occluded));
        let snapshot = owner.snapshot().unwrap();
        assert!(
            !snapshot
                .instances
                .values()
                .any(|instance| instance.graph == 0x01ee)
        );
        let encoder = owner.encoder();
        let release_snapshot = encoder.project_next(snapshot, &idle).unwrap();
        encoder.full(&release_snapshot).unwrap();
        let release = encoder
            .queue_delta(
                &mut stream,
                &release_snapshot,
                DeltaKind::Correction(FIRST_TICK + (fill_steps + 400)),
                16,
            )
            .unwrap()
            .unwrap();
        let sent = stream.sent(release.last).unwrap();
        for (instance, _) in effects {
            assert!(sent.changes.contains(&ChangeKey::Instance(instance)));
        }
        let charge_at_end = charge(&*owner);
        owner.set_encounter_active(false).unwrap();
        owner.receive_applied_damage(last_damage.unwrap()).unwrap();
        for step in fill_steps + 401..=fill_steps + 501 {
            owner
                .advance(
                    Command {
                        frame: FIRST_TICK + step,
                        ..Command::default()
                    },
                    &mover,
                    BodyStep::Posed,
                )
                .unwrap();
        }
        assert_eq!(
            charge(&*owner),
            charge_at_end,
            "round end must cancel passive and damage charging"
        );
        assert_eq!(
            owner.snapshot().unwrap().variables.get(&2426),
            Some(&Value::Bool(false))
        );
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 Junkenstein collision and graph data"]
    fn junkenstein_notification_updates_follow_authored_messages() {
        use crate::game_content::{JUNKENSTEIN_MAP, MapCatalog};

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let maps = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = maps
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        let (initial, payload) = scene.encounter_notification_delta(None).unwrap().unwrap();
        assert_eq!(initial.instance_count(), 1);
        assert!(payload.bit_len() > 0);
        assert!(
            scene
                .encounter_notification_delta(Some(&initial))
                .unwrap()
                .is_none()
        );
        scene.step_encounter(2700, None);
        assert!(
            scene
                .encounter
                .as_mut()
                .unwrap()
                .script
                .queue_entity_message(0x0240_0000_0000_0694, Variables::new())
                .unwrap()
                > 0
        );
        scene.step_encounter(2701, None);
        let (changed, payload) = scene
            .encounter_notification_delta(Some(&initial))
            .unwrap()
            .unwrap();
        assert_eq!(changed.instance_count(), 1);
        assert!(payload.bit_len() > 0);
        assert!(
            scene
                .encounter_notification_delta(Some(&changed))
                .unwrap()
                .is_none()
        );
        assert!(
            scene
                .encounter
                .as_mut()
                .unwrap()
                .script
                .queue_entity_message(0x0240_0000_0000_06a8, Variables::new())
                .unwrap()
                > 0
        );
        let mut baseline = changed;
        let mut updates = 0;
        for tick in 2702..=2822 {
            scene.step_encounter(tick, None);
            assert!(scene.encounter.is_some(), "encounter halted at {tick}");
            if let Some((current, payload)) =
                scene.encounter_notification_delta(Some(&baseline)).unwrap()
            {
                assert_eq!(current.instance_count(), 1);
                assert!(payload.bit_len() > 0);
                baseline = current;
                updates += 1;
            }
        }
        assert!(updates > 0, "06A8 must change the notification graph");
        assert!(
            updates < 121,
            "notifications must leave ticks for the countdown"
        );
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 Junkenstein collision and graph data"]
    fn junkenstein_room_clock_starts_countdown_once_after_cassidy_selection() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, MapCatalog},
            script_init::StateKind,
        };
        use sha2::Digest;

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let maps = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = maps
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        // Python remote_delta vectors for the same 104319 graph and codec table.
        for (value, active, width, expected) in [
            (
                8.0,
                [true, false, false, false],
                121,
                "10c02e0a0a01000c020e010000100400",
            ),
            (
                5.0,
                [false, true, false, false],
                121,
                "10c02e0aaa00000c020e020000140800",
            ),
            (
                0.0,
                [false, false, false, false],
                85,
                "10c02e0a0a00000c020e00",
            ),
            (
                470.0,
                [false, false, true, false],
                149,
                "10c02e0a1000607d0800c020e0080080f52100",
            ),
            (
                12.0,
                [false, false, false, true],
                121,
                "10c02e0a8a01000c020e040000501000",
            ),
        ] {
            let wire = scene
                .encounter_countdown_wire(&CountdownFrame {
                    value: Value::Float(value),
                    states: active.map(|on| {
                        on.then_some(script_init::ChasePayload {
                            current: Value::Float(value),
                            reached: false,
                            remaining_ms: None,
                            last_ms: 0,
                        })
                    }),
                })
                .unwrap()
                .unwrap();
            assert_eq!(wire.bit_len(), width);
            let actual: String = wire
                .as_bytes()
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            assert_eq!(actual, expected);
        }
        let cassidy = scene.heroes().find_hero("Cassidy").unwrap();
        let hero = cassidy.guid;
        let mut owner = scene
            .owner_body_runtime(
                cassidy,
                OwnerBodySpawn {
                    slot: 0,
                    controller: 0xa000_0100,
                    entity: 0xa000_0101,
                    body_filter: team_bit(0).unwrap(),
                    frame: FIRST_TICK,
                    pose: scene.player_spawn_mover(cassidy.body, 0).unwrap().pose(),
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .expect("Normal Cassidy owner runtime");
        let snapshot = owner.snapshot().unwrap();
        let revolver = snapshot
            .instances
            .values()
            .find(|instance| instance.graph == 0x01d2)
            .expect("Cassidy revolver");
        assert_eq!(revolver.variables[&198], Value::Integer(6));
        assert_eq!(revolver.variables[&53], Value::Integer(6));
        let defender = snapshot
            .instances
            .values()
            .find(|instance| instance.graph == 0x0ca5)
            .expect("defender message receiver");
        assert_eq!(defender.states.len(), 1);
        let intro_receiver = &defender.states[&11];
        assert!(intro_receiver.active);
        assert_eq!(intro_receiver.kind, StateKind::MessageIdle);
        assert_eq!(owner.vitals().unwrap().total(), 225.0);
        for (source, message) in [
            (
                GAME_MODE_ENTITY,
                crate::junkenstein_messages::RESET_HIGHLIGHTS,
            ),
            (0xc000_1000, crate::junkenstein_messages::BOSS_INTRO_ENDED),
        ] {
            assert_eq!(
                owner
                    .receive_defender_message(DefenderMessage {
                        target: None,
                        source,
                        message,
                        parameters: Variables::new()
                    })
                    .unwrap(),
                1
            );
        }
        assert!(owner.snapshot().unwrap().initialization_complete());
        let mover = scene.player_spawn_mover(cassidy.body, 0).unwrap();
        let pose = mover.pose();
        let yaw = f64::from(pose.yaw) * std::f64::consts::TAU / 65_536.0;
        let (sin, cos) = yaw.sin_cos();
        let [x, y, z] = pose.position;
        let sender = 0xc000_1000;
        for direction in [-1.0, 1.0] {
            owner
                .receive_sighting(crate::junkenstein_messages::DefenderSighting {
                    target: 0xa000_0101,
                    source: crate::junkenstein_messages::SightingSource {
                        entity: sender,
                        position: [x + direction * sin, y, z + direction * cos],
                        tags: [Some(false); 3],
                        filter_flags: Some(1 | team_bit(1).unwrap()),
                    },
                    parameters: Variables::from([
                        (0x8d, Value::Integer(1)),
                        (0x5c, Value::Entity(sender)),
                    ]),
                })
                .unwrap();
            let receiver = owner
                .snapshot()
                .unwrap()
                .instances
                .values()
                .find(|instance| instance.graph == 0x0ca5)
                .unwrap();
            assert_eq!(receiver.variables[&40], Value::Entity(sender));
            assert_eq!(
                receiver.variables.get(&1725),
                (direction > 0.0).then_some(&Value::Integer(0))
            );
        }

        owner.set_defenders(Arc::from([0xa000_0101]));
        for frame in FIRST_TICK + 1..=FIRST_TICK + 62 {
            owner
                .advance(
                    crate::commands::Command {
                        frame,
                        ..Default::default()
                    },
                    &mover,
                    crate::game_replication::BodyStep::UnposedTimer,
                )
                .unwrap_or_else(|error| panic!("Cassidy stopped at frame {frame}: {error}"));
        }
        let snapshot = owner.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        scene.step_encounter(2700, None);
        let before = &scene.encounter_snapshot().unwrap().instances[&ENCOUNTER_INSTANCE];
        assert_eq!(before.states[&3].kind, StateKind::BooleanSwitch(false));
        assert!(!before.states.get(&52).is_some_and(|state| state.active));

        scene.step_encounter(2701, Some(hero));
        let ready = &scene.encounter_snapshot().unwrap().instances[&ENCOUNTER_INSTANCE];
        assert_eq!(
            ready.variables[&8449],
            Value::Array(vec![Value::Asset(hero)])
        );
        assert_eq!(ready.states[&3].kind, StateKind::BooleanSwitch(true));
        assert!(ready.states[&52].active);
        assert_eq!(ready.states[&52].start_ms, 16);
        assert_eq!(
            scene
                .encounter_countdown()
                .unwrap()
                .states
                .map(|state| state.is_some()),
            [true, false, false, false]
        );

        for tick in 2702..2730 {
            scene.step_encounter(tick, Some(hero));
        }
        let after = &scene.encounter_snapshot().unwrap().instances[&ENCOUNTER_INSTANCE];
        assert_eq!(after.states[&52].activation, 1);
        assert!(
            after.states[&52]
                .chase
                .as_ref()
                .is_some_and(|chase| chase.last_ms > 16)
        );
        let ana = scene.heroes().find_hero("Ana").unwrap().guid;
        scene.step_encounter(2730, Some(ana));
        let changed = &scene.encounter_snapshot().unwrap().instances[&ENCOUNTER_INSTANCE];
        assert_eq!(
            changed.variables[&8449],
            Value::Array(vec![Value::Asset(ana)])
        );
        assert_eq!(changed.states[&52].activation, 1);

        for tick in 2731..=3220 {
            scene.step_encounter(tick, Some(ana));
        }
        let started = &scene.encounter_snapshot().unwrap().instances[&ENCOUNTER_INSTANCE];
        assert_eq!(started.variables[&59], Value::Integer(1));
        assert_eq!(started.states[&53].activation, 1);
        for tick in 3221..=4080 {
            scene.step_encounter(tick, Some(ana));
        }
        let scene_vm = scene.encounter.as_ref().unwrap();
        let gate = scene_vm.gate_script.snapshot().unwrap();
        assert_eq!(gate.variables[&8571], Value::Integer(1));
        assert_eq!(scene_vm.gate_state.team, Some(2));
        let snapshot = scene.encounter_snapshot().unwrap();
        let mode = &snapshot.instances[&ENCOUNTER_INSTANCE];
        assert_eq!(
            mode.variables[&8618],
            Value::Array(vec![Value::Entity(player_entity(0))])
        );
        assert_eq!(mode.variables[&59], Value::Integer(2));
        assert!(
            snapshot
                .instances
                .values()
                .any(|instance| instance.graph == 0x0cb3)
        );
        let first_spawner = snapshot
            .instances
            .values()
            .find(|instance| instance.graph == 0x0cba)
            .expect("authored first spawn graph");
        assert_eq!(
            first_spawner.variables[&8398],
            Value::Asset(0x02e0_0000_0000_0175)
        );
        assert_eq!(
            first_spawner.variables[&8364],
            Value::Asset(0x0d60_0000_0000_0328)
        );
        assert_eq!(first_spawner.variables[&6737], Value::Integer(8));
        let anchor = 0x8000_0107;
        assert_eq!(first_spawner.variables[&1629], Value::Entity(anchor));
        let (search_key, request) = scene_vm
            .script
            .spawn_searches()
            .next()
            .expect("pending first spawn search");
        assert_eq!(
            request.position,
            [74.47403717041016, 12.70523452758789, -99.36385345458984]
        );
        assert_eq!(
            (request.radius, request.minimum, request.maximum),
            (8.0, 10, 300)
        );
        assert_eq!(
            request.direction,
            [-0.2588191628456116, 0.0, -0.9659258127212524]
        );
        assert!(!first_spawner.variables.contains_key(&8402));

        for tick in 4081..=4120 {
            scene.step_encounter(tick, Some(ana));
        }
        let snapshot = scene.encounter_snapshot().unwrap();
        let first_spawner = snapshot
            .instances
            .values()
            .find(|instance| instance.graph == 0x0cba)
            .unwrap();
        let Value::Array(points) = &first_spawner.variables[&8402] else {
            panic!("spawn search must publish candidate positions")
        };
        assert!((10..=300).contains(&points.len()));
        assert_eq!(points.len(), 172);
        let mut digest = sha2::Sha256::new();
        for point in points {
            let Value::Vector(point) = point else {
                panic!("candidate must be a vector")
            };
            for &axis in point {
                digest.update((axis as f32).to_le_bytes());
            }
        }
        assert_eq!(
            format!("{:x}", digest.finalize()),
            "698b0355c4711243657e6e8829b2ef2d4b9cd5ab39dcb68b899367eda21dbd97"
        );
        let samples = [
            (
                0,
                [74.55856718113806, 12.682595778099707, -99.04838341805228],
            ),
            (
                1,
                [74.73517358618709, 12.682595448753904, -99.86565797066675],
            ),
            (
                2,
                [73.78785046921642, 12.682595758201575, -99.11389116855813],
            ),
            (
                171,
                [66.65998277248119, 12.383752996930431, -100.77117497656228],
            ),
        ];
        for (index, expected) in samples {
            let Value::Vector(actual) = &points[index] else {
                panic!("candidate must be a vector")
            };
            for (actual, expected) in actual.iter().zip(expected) {
                assert!(
                    (actual - expected).abs() < 1e-9,
                    "candidate {index}: {actual} != {expected}"
                );
            }
        }
        for point in points {
            let Value::Vector([x, _, z]) = point else {
                panic!("candidate must be a vector")
            };
            assert!((x - request.position[0]).hypot(z - request.position[2]) <= request.radius);
        }
        let first_instance = snapshot
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == 0x0cba)
            .map(|(&id, _)| id)
            .unwrap();
        let selection = &first_spawner.states[&2];
        assert!(selection.active);
        assert_eq!(selection.kind, script_init::StateKind::RecentInput);
        let roster_filter = &first_spawner.states[&1];
        assert!(roster_filter.active);
        assert_eq!(roster_filter.kind, StateKind::LivingBodyArray);
        let candidates = points.clone();
        let mut selected_tick = None;
        for tick in 4121..=4200 {
            scene.step_encounter(tick, Some(ana));
            if scene
                .encounter_snapshot()
                .unwrap()
                .instances
                .get(&first_instance)
                .is_some_and(|instance| instance.variables.contains_key(&8370))
            {
                selected_tick = Some(tick);
                break;
            }
        }
        let selected_tick = selected_tick.expect("spawner must select before retiring");
        let snapshot = scene.encounter_snapshot().unwrap();
        let spawner = &snapshot.instances[&first_instance];
        assert!(candidates.contains(&spawner.variables[&8370]));
        if runtime
            .join("data/junkenstein/junkenstein-npcs.json")
            .is_file()
        {
            assert!(
                !snapshot
                    .diagnostics
                    .iter()
                    .any(|diagnostic| diagnostic.site.instance == first_instance
                        && diagnostic.site.node == Some(7)
                        && matches!(diagnostic.issue, script_init::Issue::EntityCreation(_))),
                "{:?}",
                snapshot.diagnostics
            );
            let enemies = scene.simulation.bodies().unwrap();
            assert_eq!(enemies.len(), 3);
            let roster = enemies
                .map(|enemy| {
                    assert_eq!(enemy.spawn.hero, 0x02e0_0000_0000_0175);
                    assert_eq!(enemy.spawn.team, 1);
                    assert!(candidates.contains(&Value::Vector(enemy.position)));
                    Value::Entity(enemy.entity)
                })
                .collect::<Vec<_>>();
            // The living filter was evaluated before the newest creation.
            assert_eq!(spawner.variables[&8681], Value::Array(roster[..2].to_vec()));
            assert_eq!(spawner.variables[&8399], Value::Array(roster));
            assert!(
                !snapshot.diagnostics.iter().any(|diagnostic| {
                    diagnostic.site.instance == first_instance && diagnostic.site.node == Some(9)
                }),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                scene.simulation.bodies().unwrap().last().unwrap().position,
                match &spawner.variables[&8370] {
                    Value::Vector(position) => *position,
                    _ => panic!("spawn position"),
                }
            );
        } else {
            assert_eq!(spawner.variables[&8681], Value::Array(vec![]));
            assert!(
                snapshot
                    .diagnostics
                    .iter()
                    .any(|diagnostic| diagnostic.site.instance == first_instance
                        && diagnostic.site.node == Some(7)
                        && matches!(&diagnostic.issue,script_init::Issue::EntityCreation(error)
                    if error=="missing prepared enemy creation services"
                        || error=="missing prepared NPC definition")),
                "authored spawner must evaluate its creation request: {:?}",
                snapshot.diagnostics
            );
        }
        let encounter = scene.encounter.as_mut().unwrap();
        let queued = encounter
            .script
            .queue_frame_message(
                selected_tick - 2700 + 1,
                first_instance,
                0x0240_0000_0000_0695,
                Variables::new(),
            )
            .unwrap();
        assert_eq!(queued, 1);
        scene.step_encounter(selected_tick + 1, Some(ana));
        assert!(
            !scene
                .encounter_snapshot()
                .unwrap()
                .instances
                .contains_key(&first_instance)
        );
        // A retired search must ignore a delayed completion without restoring its instance.
        scene
            .encounter
            .as_mut()
            .unwrap()
            .script
            .queue_spawn_result(selected_tick - 2700 + 2, search_key, vec![[0.0; 3]; 10])
            .unwrap();
        scene.step_encounter(selected_tick + 2, Some(ana));
        assert!(
            !scene
                .encounter_snapshot()
                .unwrap()
                .instances
                .contains_key(&first_instance)
        );
    }
    use crate::game_bots::BOT_ENTITY;

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range pack"]
    fn repeated_rooms_share_collision_indexes_and_keep_independent_bodies() {
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(runtime).unwrap();
        let first = assets.prepare(GameMap::practice_range()).unwrap();
        let mut second = assets.prepare(GameMap::practice_range()).unwrap();
        assert!(Arc::ptr_eq(&first.ground, &second.ground));
        assert!(Arc::ptr_eq(
            first.contacts.as_ref().unwrap(),
            second.contacts.as_ref().unwrap()
        ));
        let before = first.bots().unwrap().next().unwrap().position;
        let after = [before[0] + 1.0, before[1], before[2]];
        second
            .bot_simulation()
            .0
            .body_mut(BOT_ENTITY)
            .unwrap()
            .position = after;
        assert_eq!(first.bots().unwrap().next().unwrap().position, before);
        assert_eq!(second.bots().unwrap().next().unwrap().position, after);
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range pack"]
    fn prepared_practice_debug_view_contains_real_collision_layers() {
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(runtime).unwrap();
        let scene = assets.prepare(GameMap::practice_range()).unwrap();
        let view = serde_json::to_value(scene.debug_geometry().view()).unwrap();

        assert_eq!(view["map"], "0800000000000688");
        assert_eq!(view["sources"]["movement_cache"], "0688.col");
        assert_eq!(
            view["origin"],
            serde_json::to_value(scene.map().spawn_point(0, 0).unwrap().0).unwrap()
        );
        assert!(view["counts"]["triangles"].as_u64().unwrap() > 100);
        assert!(view["counts"]["proxies"].as_u64().unwrap() > 10);
        assert_eq!(
            view["totals"]["triangles"],
            scene.movement_index().triangle_count()
        );
        assert_eq!(
            view["totals"]["proxies"],
            scene.contacts.as_ref().unwrap().shapes().len()
        );
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range pack without bot VM graphs"]
    fn stock_soldier_owner_session_idles_fires_reloads_and_sprints() {
        use crate::commands::Command;
        use crate::game_replication::BodyStep;
        use crate::script_init::{Snapshot, StateKind};
        use crate::script_snapshot_wire::{View, uncertain_polled_switch};
        use crate::script_stream::{BodyStream, DeltaKind};
        use crate::script_wire::Value;
        use std::collections::BTreeSet;

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(runtime).unwrap();
        let scene = assets.prepare(GameMap::practice_range()).unwrap();
        assert!(scene.movement_ready());
        let hero = scene.heroes().get(SOLDIER).unwrap();
        let spawn = OwnerBodySpawn {
            slot: 0,
            controller: 0xa000_0100,
            entity: 0xa000_0101,
            body_filter: team_bit(0).unwrap(),
            frame: FIRST_TICK,
            pose: scene.player_spawn_mover(hero.body, 0).unwrap().pose(),
            quanta: RETAIL_QUANTA,
        };
        if assets.graphs.bodies()[&hero.body].target_tags.is_some() {
            let mut runtime = scene.owner_body_runtime(hero, spawn).unwrap().unwrap();
            let snapshot = runtime.snapshot().unwrap();
            let encoder = runtime.encoder();
            // The authored message table stays in the VM for lookups.
            let table = &snapshot.instances[&24];
            assert_eq!(table.graph, 0x05ad);
            assert!(matches!(&table.variables[&29859], Value::Map(entries) if entries.len() == 20));
            let mut streams = Vec::new();
            for view in [View::Owner, View::Observer] {
                let projected = encoder.project(snapshot, view).unwrap();
                let mut stream = BodyStream::new(spawn.entity);
                let baseline = encoder.queue_full(&mut stream, &projected).unwrap();
                stream.create_arrived();
                stream.arrived(baseline.last);
                // Only the unbound table is omitted, as Python omits its None.
                let sent = projected.instance_variables(24).unwrap();
                assert!(!sent.contains_key(&29859));
                for (&variable, value) in &table.variables {
                    let value = snapshot
                        .resolved_bindings
                        .get(&(24, variable))
                        .unwrap_or(value);
                    if variable != 29859 && !matches!(value, Value::Null) {
                        assert_eq!(sent.get(&variable), Some(value), "{variable}");
                    }
                }
                streams.push((stream, projected));
            }
            // A live session: idle for about 3 s, hold fire for 1.5 s, reload,
            // fire briefly, stand until the idle fidget plays, fire through it,
            // then hold forward and Sprint. Both streams project and encode
            // every frame, as the worker sends them.
            let int = |value: Option<&Value>| match value {
                None => None,
                Some(Value::Integer(value)) => Some(*value),
                Some(value) => panic!("unexpected integer {value:?}"),
            };
            let flag = |value: Option<&Value>| match value {
                None => None,
                Some(Value::Bool(value)) => Some(*value),
                Some(value) => panic!("unexpected flag {value:?}"),
            };
            // A switch polled on a native the owner client may answer
            // differently stays off the owner stream.
            let uncertain = |snapshot: &Snapshot| {
                let mut found = BTreeSet::new();
                for (&id, instance) in &snapshot.instances {
                    let graph = assets.graphs.graph(instance.graph).unwrap();
                    for (&raw, state) in &instance.states {
                        let node = graph.state(usize::from(raw)).unwrap();
                        if state.active && uncertain_polled_switch(node, state) {
                            found.insert((id, raw));
                        }
                    }
                }
                found
            };
            // The reference withholds these switches too, and two more in
            // instance 9, which this tree retires at startup. Instance 18 is
            // one of the tagged weapon-manager subgraphs the reference lacks.
            let withheld = BTreeSet::from([
                (1, 10),
                (1, 15),
                (1, 20),
                (1, 21),
                (1, 22),
                (1, 23),
                (2, 12),
                (2, 13),
                (2, 14),
                (12, 25),
                (12, 28),
                (12, 30),
                (12, 48),
                (13, 17),
                (18, 0),
            ]);
            assert_eq!(uncertain(snapshot), withheld);
            for (_, projected) in &streams {
                for &(id, raw) in &withheld {
                    assert!(!projected.instance_states(id).unwrap().contains(&raw));
                }
            }
            let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
            let mut owner = Vec::new();
            let mut owner_hud = Vec::new();
            for t in 1..=900 {
                let frame = FIRST_TICK + t;
                let (forward, buttons) = match t {
                    188..=281 | 520..=540 | 600..=640 => (0, 1),
                    300 => (0, 0x400),
                    700 => (127, 8),
                    701..=760 => (127, 0),
                    _ => (0, 0),
                };
                let command = Command {
                    frame,
                    forward,
                    buttons,
                    ..Command::default()
                };
                mover
                    .step(scene.movement_index(), command, 0.016, |_, _| {})
                    .unwrap();
                runtime.advance(command, &mover, BodyStep::Posed).unwrap();
                if let Some(modifiers) = runtime.movement_modifiers() {
                    mover.track_modifiers(frame, modifiers);
                }
                runtime.take_shots(Some(command), &mover);
                let snapshot = runtime.snapshot().unwrap();
                // The worker cancels the body script at the first diagnostic.
                assert!(
                    snapshot.diagnostics.is_empty(),
                    "{t}: {:?}",
                    snapshot.diagnostics
                );
                // The idle fidget animation has no authored duration. Like
                // the reference, it stays on without a timer until held fire
                // ends it.
                let idle = &snapshot.instances[&14];
                assert_eq!(idle.graph, 0x02c5);
                let playing = idle.states.get(&19).is_some_and(|state| state.active);
                if playing {
                    assert_eq!(idle.states[&19].kind, StateKind::Anim);
                    assert!(
                        snapshot
                            .pending_timers
                            .get(&14)
                            .into_iter()
                            .flatten()
                            .all(|timer| timer.raw_state != 19)
                    );
                }
                assert_eq!(playing, (586..600).contains(&t), "{t}");
                // Sprint starts on the press with forward held, runs while
                // forward stays held and drives its HUD flag, as in the
                // reference.
                let sprint = &snapshot.instances[&5];
                assert_eq!(sprint.graph, 0x0255);
                let (flags, hud) = match t {
                    700 => (5, Some(1)),
                    701..=760 => (1, Some(1)),
                    761.. => (0, Some(0)),
                    _ => (0, None),
                };
                assert_eq!(
                    (sprint.states[&1].kind, int(sprint.variables.get(&699))),
                    (StateKind::Ability { flags }, hud),
                    "{t}"
                );
                let excluded = uncertain(snapshot);
                let encoder = runtime.encoder();
                for (stream, initial) in &mut streams {
                    let next = encoder.project_next(snapshot, initial).unwrap();
                    let kind = match initial.view() {
                        View::Owner => stream.owner_kind(frame, false, false).unwrap(),
                        View::Observer => Some(DeltaKind::Observer { resend: false }),
                    };
                    let Some(kind) = kind else { continue };
                    let delta = encoder.queue_delta(stream, &next, kind, runtime.frame_ms());
                    if let Some(chunk) = delta.unwrap() {
                        stream.sent(chunk.last);
                        stream.arrived(chunk.last);
                    }
                    if next.view() != View::Owner {
                        continue;
                    }
                    for &(id, raw) in &excluded {
                        assert!(!next.instance_states(id).unwrap().contains(&raw), "{t}");
                    }
                    let rifle = next.instance_variables(11).unwrap();
                    let fact = (int(rifle.get(&53)), flag(rifle.get(&51)));
                    if owner.last().is_none_or(|&(_, last)| last != fact) {
                        owner.push((t, fact));
                    }
                    let hud = int(next.instance_variables(5).unwrap().get(&699));
                    if owner_hud.last().is_none_or(|&(_, last)| last != hud) {
                        owner_hud.push((t, hud));
                    }
                }
            }
            assert_eq!(streams[0].0.command_frame(), FIRST_TICK + 900);
            // The owner frames carry the Sprint HUD flag the client shows.
            assert_eq!(owner_hud, [(1, None), (700, Some(1)), (761, Some(0))]);
            // The frozen reference produces the same rifle ammo and reload
            // transitions from these inputs: 14 rounds in 1.5 s, the magazine
            // refilled 47 frames after the reload press, the reload ended 46
            // frames later, and six rounds through the idle fidget.
            let mut expected = vec![(1, (Some(30), None))];
            for (t, ammo) in [
                188, 195, 201, 208, 215, 222, 229, 236, 243, 250, 257, 264, 271, 278,
            ]
            .into_iter()
            .zip((16..30).rev())
            {
                expected.push((t, (Some(ammo), None)));
            }
            expected.extend([
                (300, (Some(16), Some(true))),
                (347, (Some(30), Some(true))),
                (393, (Some(30), Some(false))),
                (520, (Some(29), Some(false))),
                (527, (Some(28), Some(false))),
                (533, (Some(27), Some(false))),
                (540, (Some(26), Some(false))),
                (600, (Some(25), Some(false))),
                (607, (Some(24), Some(false))),
                (613, (Some(23), Some(false))),
                (620, (Some(22), Some(false))),
                (627, (Some(21), Some(false))),
                (634, (Some(20), Some(false))),
            ]);
            assert_eq!(owner, expected);
        } else {
            let Err(blocked) = scene.owner_body_runtime(hero, spawn) else {
                panic!("the untagged stock owner body was expected to stop before attach");
            };
            assert_eq!(blocked.kind(), io::ErrorKind::Unsupported);
            assert!(
                blocked
                    .to_string()
                    .contains("UnsupportedConfig(\"STU_AC829876\")"),
                "{blocked}"
            );
        }
        let tracer = scene.heroes().find_hero("Tracer").unwrap();
        assert!(scene.owner_body_runtime(tracer, spawn).unwrap().is_none());
    }

    #[test]
    #[ignore = "requires a tagged 104319 Practice Range pack"]
    fn stock_soldier_casts_biotic_field() {
        use crate::{commands::Command, game_replication::BodyStep};

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(runtime).unwrap();
        let scene = assets.prepare(GameMap::practice_range()).unwrap();
        let hero = scene.heroes().get(SOLDIER).unwrap();
        let mover = scene.player_spawn_mover(hero.body, 0).unwrap();
        let spawn = OwnerBodySpawn {
            slot: 0,
            controller: 0xa000_0100,
            entity: 0xa000_0101,
            body_filter: team_bit(0).unwrap(),
            frame: FIRST_TICK,
            pose: mover.pose(),
            quanta: RETAIL_QUANTA,
        };
        let mut owner = scene.owner_body_runtime(hero, spawn).unwrap().unwrap();
        let mut shots = Vec::new();
        for frame in FIRST_TICK + 1..=FIRST_TICK + 120 {
            let command = Command {
                frame,
                buttons: if frame == FIRST_TICK + 40 { 0x10 } else { 0 },
                ..Command::default()
            };
            owner.advance(command, &mover, BodyStep::Posed).unwrap();
            let snapshot = owner.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "frame {frame}: {:?}",
                snapshot.diagnostics
            );
            shots.extend(owner.take_shots(Some(command), &mover));
        }
        let [crate::game_combat::PosedShot::BioticField(field)] = shots.as_slice() else {
            panic!("the actual ability must emit exactly one Biotic Field");
        };
        assert_eq!(field.shot.graph, 0x0257);
        assert_eq!(field.feet, mover.pose().position);
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range pack"]
    fn elevated_practice_patrols_reach_their_hints_without_crossing_walls() {
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let mut assets = SceneAssets::load(runtime).unwrap();
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        assets.enable_contact_trace();
        let mut traced = assets.prepare(GameMap::practice_range()).unwrap();
        assert_eq!(scene.bots().unwrap().len(), 15);
        let scripted = match assets.graphs.graph_indexes().count() {
            373 | 699 | 700 => 0,
            405 => 15,
            count => panic!("no patrol reference profile for the {count}-graph bundle"),
        };
        assert_eq!(scene.scripted_bots(), scripted);
        let bots = [BOT_ENTITY + 4, BOT_ENTITY + 9];
        let mut previous = bots.map(|entity| {
            scene
                .bots()
                .unwrap()
                .find(|bot| bot.entity == entity)
                .unwrap()
                .position
        });
        let goals = [[3, 4], [0, 1]];
        let mut visited = [0; 2];
        let patrol = PatrolScript::load().unwrap();
        for tick in 1001..=2900 {
            scene.step_bots(tick, &patrol).unwrap();
            traced.step_bots(tick, &patrol).unwrap();
            for (slot, entity) in bots.into_iter().enumerate() {
                let bot = scene
                    .bots()
                    .unwrap()
                    .find(|bot| bot.entity == entity)
                    .unwrap();
                let traced_bot = traced
                    .bots()
                    .unwrap()
                    .find(|bot| bot.entity == entity)
                    .unwrap();
                assert_eq!(
                    traced_bot.movement(tick),
                    bot.movement(tick),
                    "contact trace changed bot {entity:x} at {tick}"
                );
                let before = previous[slot];
                previous[slot] = bot.position;
                let delta =
                    std::array::from_fn::<_, 3, _>(|axis| bot.position[axis] - before[axis]);
                let length = delta[0].hypot(delta[1]).hypot(delta[2]);
                if length > 0.001 {
                    let origin = [before[0], before[1] + 1.05, before[2]];
                    let category = mover_category(scene.map(), bot.spawn.team);
                    let hit = scene
                        .ground
                        .cast_ray(origin, delta.map(|axis| axis / length), length, category)
                        .unwrap();
                    assert!(
                        hit.is_none_or(|hit| hit.normal[1].abs() >= 0.1),
                        "bot {entity:x} crossed a wall at {tick}: {before:?} -> {:?}, {hit:?}",
                        bot.position
                    );
                }
                if let Some(&index) = goals[slot].get(visited[slot]) {
                    let hint = bot.spawn.route[index];
                    if (bot.position[0] - hint[0]).hypot(bot.position[2] - hint[2]) < 0.06
                        && (bot.position[1] - hint[1]).abs() < 0.15
                    {
                        visited[slot] += 1;
                    }
                }
            }
        }
        assert_eq!(visited, [2; 2], "last positions {previous:?}");
    }
}
