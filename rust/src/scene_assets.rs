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
    game_combat::{
        ENCOUNTER_INSTANCE, EncounterRegistration, EncounterWorld, GateState, GateWorld,
        RoomSimulation,
    },
    game_content::{
        GameMap, JUNKENSTEIN_GATE, JunkensteinCard, PRACTICE_RANGE_MAP, PRACTICE_RANGE_MODE,
    },
    game_controller::Controller,
    game_heroes::{CASSIDY, Hero, HeroCatalog, SOLDIER},
    game_instance::RETAIL_QUANTA,
    game_pools::PoolCatalog,
    game_replication::OwnerBodyRuntime,
    game_stats::{PlayerStats, StatsCatalog, initial_on_fire},
    match_scene::{
        PlayerAppearance, initial_world, junkenstein_presentation, mode_presentation, team_bit,
    },
    mover::Mover,
    mover_ground::NOT_GROUND,
    mover_settings::MoverCatalog,
    patrol_script::PatrolScript,
    runtime::JUNKENSTEIN_MAP_WORLD,
    script_codecs::CodecTable,
    script_expr::native_shape,
    script_graph::{GraphStore, ModeValue, asset_guid},
    script_init::{self, Root},
    script_player::{OwnerBodySpawn, PlayerBodyFactory},
    script_wire::{Value, Variables},
    world::{Component, EntityUpdate},
};

#[cfg(test)]
use crate::{
    game_service::player_entity,
    junkenstein_messages::DefenderMessage,
    script_init::{Site, World},
    script_snapshot_wire::{CountdownFrame, ProjectedSnapshot},
    world::GAME_MODE_ENTITY,
};

#[cfg(any(test, feature = "inspector"))]
use crate::{debug_geometry::DebugGeometry, game_debug::Actor};

pub(crate) const MOVER_CATEGORY: u16 = 0x18;

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
    junkenstein_validation: Option<crate::junkenstein_validation::JunkensteinValidation>,
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
        settings: crate::junkenstein_validation::JunkensteinValidation,
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
        Ok(Self {
            collision: runtime.join("collision"),
            junkenstein_map_world: runtime.join(JUNKENSTEIN_MAP_WORLD),
            last_collision: Mutex::new(None),
            trace_contacts: false,
            junkenstein_validation: None,
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
            .junkenstein_gate()
            .cloned()
            .expect("prepared Junkenstein gate");
        let anchors = scene
            .simulation()
            .spawn_anchors()
            .cloned()
            .unwrap_or_default();
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
            .filter(|&guid| guid == crate::junkenstein_voice::ENCOUNTER_VOICE_SET)
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
                bot.attach_scene_collision(
                    self.movers.for_body(Some(bot.spawn.body)),
                    Some(ground.as_ref()),
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
            contacts,
            controller,
            mode_ui,
            junkenstein_validation: None,
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
