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
    game_content::{GameMap, PRACTICE_RANGE_MAP, PRACTICE_RANGE_MODE},
    game_controller::Controller,
    game_heroes::{Hero, HeroCatalog, SOLDIER},
    game_instance::RETAIL_QUANTA,
    game_pools::PoolCatalog,
    game_replication::OwnerBodyRuntime,
    game_stats::{PlayerStats, StatsCatalog, initial_on_fire},
    match_scene::{PlayerAppearance, initial_world, mode_ui, team_bit},
    mover::Mover,
    mover_ground::NOT_GROUND,
    mover_settings::MoverCatalog,
    patrol_script::PatrolScript,
    script_codecs::CodecTable,
    script_graph::GraphStore,
    script_player::{OwnerBodySpawn, PlayerBodyFactory},
    script_wire::Variables,
    world::{Component, EntityUpdate},
};

#[cfg(any(test, feature = "inspector"))]
use crate::{debug_geometry::DebugGeometry, game_debug::Actor};

const MOVER_CATEGORY: u16 = 0x18;

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
    initial_board: Component,
    simulation: RoomSimulation,
    scripted_bots: usize,
    movers: Arc<MoverCatalog>,
    heroes: Arc<HeroCatalog>,
    pools: Arc<PoolCatalog>,
    pub(crate) owner_body_factory: Option<Arc<OwnerBodyFactory>>,
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

    /// Load the map before handing it to the worker. The render layouts and
    /// graph requirements are checked without blocking a game tick.
    pub fn prepare(&self, map: GameMap) -> io::Result<PreparedScene> {
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
            is_practice_range(&map) || bots.iter().any(|bot| !bot.spawn.route.is_empty()),
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
        let mode_ui = mode_ui(&self.graphs, &Variables::new())?;
        let initial_board =
            self.stats
                .component(&PlayerStats::default(), map.mode_guid, None, None);
        let mut scene = PreparedScene {
            map,
            #[cfg(any(test, feature = "inspector"))]
            movement_cache: _movement_cache,
            ground,
            contacts,
            controller,
            mode_ui,
            initial_board,
            simulation: RoomSimulation::new(bots).map_err(io::Error::other)?,
            scripted_bots,
            movers: Arc::clone(&self.movers),
            heroes: Arc::clone(&self.heroes),
            pools: Arc::clone(&self.pools),
            owner_body_factory: None,
        };
        if is_practice_range(scene.map()) && scripted_bots == 0 {
            scene.owner_body_factory = self
                .practice_owner_body
                .get_or_init(|| self.prepare_owner_body(&scene))
                .clone();
        }
        Ok(scene)
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

    fn prepare_owner_body(&self, scene: &PreparedScene) -> Option<Arc<OwnerBodyFactory>> {
        let hero = self.heroes.get(SOLDIER)?;
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
            eprintln!("Practice Range Soldier owner statescript unavailable ({reason})");
        }
        Some(Arc::new(move |hero, spawn| {
            if hero.guid != SOLDIER {
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
    pub fn map(&self) -> &GameMap {
        &self.map
    }

    pub fn scripted_bots(&self) -> usize {
        self.scripted_bots
    }

    pub(crate) fn movement_ready(&self) -> bool {
        is_practice_range(&self.map) && self.scripted_bots == 0 && self.contacts.is_some()
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

    pub fn bots(&self) -> Result<impl ExactSizeIterator<Item = &Bot>, CombatError> {
        self.simulation.bodies()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn bot_actors(&self) -> Result<impl ExactSizeIterator<Item = Actor>, CombatError> {
        self.simulation.debug_actors()
    }

    #[cfg(test)]
    pub(crate) fn invalidate_bot_component(&mut self) -> bool {
        self.simulation.invalidate_bot_component()
    }

    pub(crate) fn bot_simulation(&mut self) -> (&mut RoomSimulation, BotQueries<'_>) {
        (
            &mut self.simulation,
            BotQueries::new(self.map.free_for_all, &self.movers, &self.ground),
        )
    }

    pub(crate) fn retire_projectiles(&mut self, connection: u32) {
        self.simulation.retire_projectiles(connection);
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
        initial_world(
            &self.map,
            viewer,
            self.initial_board.clone(),
            self.bots().map_err(io::Error::other)?,
        )
    }
}

/// Map queries used by bot simulation without borrowing its entity storage.
pub(crate) struct BotQueries<'a> {
    free_for_all: bool,
    movers: &'a MoverCatalog,
    ground: &'a CollisionScene,
}

impl<'a> BotQueries<'a> {
    pub(crate) fn new(
        free_for_all: bool,
        movers: &'a MoverCatalog,
        ground: &'a CollisionScene,
    ) -> Self {
        Self {
            free_for_all,
            movers,
            ground,
        }
    }

    pub(crate) fn free_for_all(&self) -> bool {
        self.free_for_all
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

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
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
    #[ignore = "requires a prepared 104319 Practice Range pack"]
    fn elevated_practice_patrols_reach_their_hints_without_crossing_walls() {
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let mut assets = SceneAssets::load(runtime).unwrap();
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        assets.enable_contact_trace();
        let mut traced = assets.prepare(GameMap::practice_range()).unwrap();
        assert_eq!(scene.bots().unwrap().len(), 15);
        let scripted = if assets.graphs.graph_indexes().count() == 373 {
            0
        } else {
            15
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
