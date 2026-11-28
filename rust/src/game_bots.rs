//! Map AI spawners and the training bots' server-side patrol movement.
//!
//! The catalog and patrol behavior follow ow174/game/bots.py.

use std::{
    collections::{HashMap, VecDeque},
    f64::consts,
    fs, io,
    path::Path,
    sync::Arc,
    time::Duration,
};

use serde::Deserialize;

use crate::accounts::parse_guid;
use crate::collision_scene::CollisionScene;
use crate::commands::Command;
use crate::contact_body::CharacterBody;
use crate::contact_cache::CapsulePose;
use crate::contact_math::{add, sub};
use crate::contact_solver::clip_velocity;
use crate::contact_world::ContactWorld;
use crate::game_clock::TICK_PERIOD;
use crate::game_debug::ContactClip;
use crate::mover::VelocityChange;
use crate::mover_ground::{pogo, standing_body, standing_capsule, standing_origin};
use crate::mover_kinematics::{Ground, MotionInput, VelocityStep};
use crate::mover_posture::{IN_AIR, snap};
use crate::mover_settings::MoverSettings;
use crate::navigation::{PathOptions, PathQuery, find_path};
use crate::patrol_script::{AiActorSnapshot, AiPatrolSnapshot, PatrolScript, WaitEvent};
use crate::script_init::ScriptedMotion;
use crate::world::{Movement, NO_INPUT};

pub const BOT_ENTITY: u32 = 0xc000_1000;
pub const BOT_CONTROLLER_BASE: u32 = 0xa000_8000;
pub const DEAD_MOVEMENT_FLAG: u32 = 0x2000;
pub const SETTLE_TICKS: u32 = 63;
pub const BOT_RESPAWN_TIME: Duration = Duration::from_secs(3);
pub const BOT_RECORD_DELAY: u32 = 8;

const PATROL: u64 = 0x0580_0000_0000_0b8e;
const ARRIVED: f64 = 0.05;
const MAX_DETOUR_SAMPLES: usize = 64;
const STEP_UP: f64 = 0.6;
const DROP: f64 = 4.0;
const MAX_SLOPE: f64 = 1.0;
const FORWARD: f64 = 127.0;
const YAW_UNITS: f64 = 65536.0;
const TURN_RATE: f64 = YAW_UNITS / 6.0;
const TURN_AFTER: f64 = 0.5;
const CATCH_UP: u32 = 625;
const EPSILON: f64 = 1.0e-9;
const GRAVITY_SCALE: f64 = 1.75;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BotWalk {
    pub speed: f64,
    pub acceleration: f64,
    pub brake: f64,
}

impl BotWalk {
    pub fn from_mover(run_forward: f64, accel_ground: f64, decel_ground_base: f64) -> Self {
        Self {
            speed: run_forward,
            acceleration: accel_ground * run_forward,
            brake: decel_ground_base * run_forward,
        }
    }
}

impl Default for BotWalk {
    fn default() -> Self {
        Self::from_mover(5.5, 35.0, 15.0)
    }
}

#[derive(Debug)]
pub struct BotSpawn {
    pub hero: u64,
    pub name: String,
    pub body: u64,
    pub team: u8,
    pub health: f64,
    pub position: [f64; 3],
    pub yaw: i32,
    pub(crate) placement_graphs: Vec<u64>,
    pub route: Vec<[f64; 3]>,
    initial_index: i64,
}

#[derive(Debug, Default)]
pub struct BotCatalog {
    maps: HashMap<u64, Vec<Arc<BotSpawn>>>,
}

impl BotCatalog {
    pub fn load(path: impl AsRef<Path>) -> io::Result<Self> {
        let path = path.as_ref();
        match path.metadata() {
            Ok(metadata) if !metadata.is_file() => Ok(Self::default()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error),
            Ok(_) => Self::from_json(&fs::read_to_string(path)?),
        }
    }

    pub fn from_json(text: &str) -> io::Result<Self> {
        let raw: RawTable = serde_json::from_str(text)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        let mut maps = HashMap::with_capacity(raw.maps.len());
        for (map, entry) in raw.maps {
            let mut spawns = Vec::new();
            for row in entry.spawners {
                let Some(hero) = raw.heroes.get(&row.hero) else {
                    continue;
                };
                if row.identifier.is_some() {
                    continue;
                }
                let placement_graphs = row
                    .graphs
                    .iter()
                    .map(|graph| guid(graph))
                    .collect::<io::Result<Vec<_>>>()?;
                let route = if placement_graphs.contains(&PATROL) {
                    row.vars
                        .route
                        .iter()
                        .filter_map(|point| entry.hints.get(point).copied())
                        .collect()
                } else {
                    Vec::new()
                };
                let team = u8::try_from(row.team)
                    .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
                spawns.push(Arc::new(BotSpawn {
                    hero: guid(&row.hero)?,
                    name: hero.name.clone(),
                    body: guid(&hero.body)?,
                    team,
                    health: hero.health,
                    position: row.position,
                    yaw: yaw_of(row.rotation),
                    placement_graphs,
                    route,
                    initial_index: row.vars.index,
                }));
            }
            maps.insert(guid(&map)?, spawns);
        }
        Ok(Self { maps })
    }

    /// Entity IDs follow the authored order after excluding mode-specific
    /// spawners and unknown heroes. Body physics comes from the shared mover.
    pub fn make(&self, map_guid: u64, mover: impl Fn(u64) -> BotWalk) -> Vec<Bot> {
        self.maps
            .get(&map_guid)
            .into_iter()
            .flatten()
            .enumerate()
            .map(|(index, spawn)| {
                Bot::new(
                    BOT_ENTITY + index as u32,
                    Arc::clone(spawn),
                    mover(spawn.body),
                )
            })
            .collect()
    }
}

#[derive(Deserialize)]
struct RawTable {
    heroes: HashMap<String, RawHero>,
    maps: HashMap<String, RawMap>,
}

#[derive(Deserialize)]
struct RawHero {
    name: String,
    body: String,
    health: f64,
}

#[derive(Deserialize)]
struct RawMap {
    spawners: Vec<RawSpawner>,
    hints: HashMap<String, [f64; 3]>,
}

#[derive(Deserialize)]
struct RawSpawner {
    hero: String,
    team: i32,
    identifier: Option<String>,
    position: [f64; 3],
    rotation: [f64; 4],
    graphs: Vec<String>,
    #[serde(default)]
    vars: RawVars,
}

#[derive(Default, Deserialize)]
struct RawVars {
    #[serde(default, rename = "0x0D80000000001B23")]
    route: Vec<String>,
    #[serde(default, rename = "0x0D80000000000027")]
    index: i64,
}

fn guid(text: &str) -> io::Result<u64> {
    parse_guid(text).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid bot catalog GUID {text:?}"),
        )
    })
}

fn s16(angle: f64) -> f64 {
    (angle + YAW_UNITS / 2.0).rem_euclid(YAW_UNITS) - YAW_UNITS / 2.0
}

fn yaw_of([_, y, _, w]: [f64; 4]) -> i32 {
    s16((y.atan2(w) * YAW_UNITS / consts::PI).round_ties_even()) as i32
}

fn heading([dx, dz]: [f64; 2]) -> f64 {
    s16((dx.atan2(dz) * YAW_UNITS / consts::TAU).round_ties_even())
}

fn throttles(yaw: f64, [dx, dz]: [f64; 2]) -> [i8; 2] {
    let (sin, cos) = (yaw * consts::TAU / YAW_UNITS).sin_cos();
    let right = -dx * cos + dz * sin;
    let forward = dx * sin + dz * cos;
    [
        (right * FORWARD).round_ties_even() as i8,
        (forward * FORWARD).round_ties_even() as i8,
    ]
}

fn planar_distance(a: [f64; 3], b: [f64; 3]) -> f64 {
    (a[0] - b[0]).hypot(a[2] - b[2])
}

type Detour = [Option<[f64; 3]>; 2];

#[derive(Debug)]
struct PatrolPlans {
    first_index: usize,
    first: Detour,
    legs: Vec<Detour>,
}

#[derive(Debug)]
struct PatrolCollision {
    body: CharacterBody,
    lift: f32,
    trace: Option<Box<PatrolTrace>>,
}

#[derive(Debug, Default)]
struct PatrolTrace {
    last_clip: Option<ContactClip>,
    #[cfg(any(test, feature = "inspector"))]
    last_normals: Vec<crate::game_debug::DebugContactNormal>,
}

impl PatrolCollision {
    fn clear(&self, from: [f64; 3], to: [f64; 3]) -> bool {
        self.body
            .sweep(
                standing_origin(from, self.lift),
                standing_origin(to, self.lift),
            )
            .is_ok_and(|hit| hit.is_none())
    }

    fn supported(
        &self,
        from: [f64; 3],
        to: [f64; 3],
        floor_tolerance: f64,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) -> bool {
        let width = 2.0 * f64::from(self.body.radius());
        let samples = (planar_distance(from, to) / width).ceil() as usize;
        if samples == 0 || samples > MAX_DETOUR_SAMPLES || !self.clear(from, to) {
            return false;
        }
        (0..=samples).all(|sample| {
            let t = sample as f64 / samples as f64;
            let [x, y, z] = std::array::from_fn(|axis| from[axis] + (to[axis] - from[axis]) * t);
            ground(x, z, y + floor_tolerance, y - floor_tolerance).is_some()
        })
    }

    fn advance(
        &mut self,
        tick: u32,
        from: [f64; 3],
        to: [f64; 3],
        dt: f64,
        blockers: &[CapsulePose],
    ) -> [f64; 3] {
        self.try_advance(tick, from, to, dt, blockers, None)
            .unwrap_or(from)
    }

    fn try_advance(
        &mut self,
        tick: u32,
        from: [f64; 3],
        to: [f64; 3],
        dt: f64,
        actor_capsules: &[CapsulePose],
        velocity_after_contacts: Option<&mut [f64; 3]>,
    ) -> Result<[f64; 3], crate::contact_hull::HullContactError> {
        let start = standing_origin(from, self.lift);
        self.body.teleport(start);
        let delta = sub(standing_origin(to, self.lift), start);
        let mut clip = None;
        let movement = if self.trace.is_some() {
            self.body
                .move_capsule_against_traced(delta, dt as f32, actor_capsules, &mut clip)
        } else {
            self.body
                .move_capsule_against(delta, dt as f32, actor_capsules)
        };
        let after = match movement {
            Ok(moved) => {
                if let Some(velocity) = velocity_after_contacts {
                    *velocity = clip_velocity(velocity.map(|axis| axis as f32), moved.planes)
                        .map(f64::from);
                }
                let after = if moved.position == add(start, delta) {
                    to
                } else {
                    let [x, y, z] = moved.position.map(f64::from);
                    [x, y - f64::from(self.lift), z]
                };
                #[cfg(any(test, feature = "inspector"))]
                if let Some(trace) = &mut self.trace {
                    trace.last_normals.clear();
                    trace.last_normals.extend(
                        moved
                            .planes
                            .iter()
                            .take(crate::game_debug::MAX_DEBUG_MOVER_NORMALS)
                            .map(|plane| crate::game_debug::DebugContactNormal {
                                tick,
                                entity: 0,
                                point: after,
                                normal: plane.normal.map(f64::from),
                            }),
                    );
                }
                after
            }
            Err(error) => {
                self.body.teleport(start);
                #[cfg(any(test, feature = "inspector"))]
                if let Some(trace) = &mut self.trace {
                    trace.last_normals.clear();
                }
                return Err(error);
            }
        };
        if let (Some(trace), Some(clip)) = (&mut self.trace, clip) {
            trace.last_clip = Some(ContactClip {
                tick,
                proxy_id: clip.proxy_id,
                category: clip.category,
                fraction: clip.fraction,
                before: from,
                intended: to,
                after,
            });
        }
        Ok(after)
    }
}

#[derive(Debug)]
struct WalkRoute {
    destination: [f64; 3],
    waypoints: VecDeque<[f64; 3]>,
}

#[derive(Debug)]
pub struct Bot {
    pub entity: u32,
    pub slot: Option<u8>,
    pub spawn: Arc<BotSpawn>,
    pub position: [f64; 3],
    pub yaw: f64,
    pitch: i32,
    pub velocity: [f64; 3],
    movement_flags: u32,
    pub dead: bool,
    walk: BotWalk,
    pace: f64,
    direction: [f64; 2],
    index: usize,
    wait: f64,
    target: Option<[f64; 3]>,
    waypoints: VecDeque<[f64; 3]>,
    walk_route: Option<WalkRoute>,
    patrol_rejoin: Option<[f64; 3]>,
    collision: Option<PatrolCollision>,
    movement_blockers: Option<(u32, Arc<[CapsulePose]>)>,
    plans: Option<Arc<PatrolPlans>>,
    start: [f64; 3],
    keys: bool,
    turning: f64,
    held: [i8; 2],
    last_held: [i8; 2],
    input_tick: Option<u32>,
    moved_tick: Option<u32>,
    last_tick: Option<u32>,
    records_from: Option<u32>,
    patrol_failed: bool,
    stunned_until: u32,
    velocity_changes: Vec<(u32, crate::mover::VelocityChange)>,
    combat_motion_tick: Option<u32>,
    anchor_return: Option<[f64; 3]>,
    ally_separation: [f64; 2],
}

impl Bot {
    pub(crate) fn scene_enemy(
        entity: u32,
        definition: &crate::npc_catalog::NpcDefinition,
        team: u8,
        position: [f64; 3],
        yaw: f64,
        walk: BotWalk,
        tick: u32,
    ) -> Self {
        let mut bot = Self::new(
            entity,
            Arc::new(BotSpawn {
                hero: definition.hero,
                body: definition.body,
                name: definition.name.clone(),
                team,
                health: definition.pools.total(),
                position,
                yaw: s16((yaw * 65536.0 / 360.0).round_ties_even()) as i32,
                placement_graphs: Vec::new(),
                route: Vec::new(),
                initial_index: 0,
            }),
            walk,
        );
        bot.records_from = Some(tick.saturating_add(BOT_RECORD_DELAY));
        bot
    }

    /// Spawn a Lua Workshop custom bot at an arbitrary map position.
    #[cfg(feature = "lua-mods")]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn mod_spawned(
        entity: u32,
        hero: u64,
        body: u64,
        name: String,
        team: u8,
        health: f64,
        position: [f64; 3],
        yaw: i32,
        tick: u32,
    ) -> Self {
        let mut bot = Self::new(
            entity,
            Arc::new(BotSpawn {
                hero,
                body,
                name,
                team,
                health,
                position,
                yaw,
                placement_graphs: Vec::new(),
                route: Vec::new(),
                initial_index: 0,
            }),
            BotWalk::default(),
        );
        bot.records_from = Some(tick.saturating_add(1));
        bot
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn pitch(&self) -> i32 {
        self.pitch
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn set_mod_facing(&mut self, yaw_degrees: f64, pitch_degrees: f64, tick: u32) {
        if yaw_degrees.is_finite() {
            self.yaw = s16((yaw_degrees * YAW_UNITS / 360.0).round_ties_even());
        }
        if pitch_degrees.is_finite() {
            self.pitch = (pitch_degrees.clamp(-89.0, 89.0) * YAW_UNITS / 360.0).round() as i32;
        }
        self.moved_tick = Some(tick);
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn set_mod_velocity(&mut self, velocity: [f64; 3], tick: u32) {
        if velocity.iter().all(|v| v.is_finite()) {
            self.velocity = velocity;
            self.moved_tick = Some(tick);
        }
    }

    /// Perform a rhythmic dance spin + celebratory bob visible to all players.
    #[cfg(feature = "lua-mods")]
    pub(crate) fn step_mod_dance(&mut self, tick: u32, base_y: f64) {
        if self.dead {
            return;
        }
        self.stop_objective(tick);
        // Smooth rhythmic rotation (~180 deg/s) and subtle bounce
        self.yaw = s16(self.yaw + (YAW_UNITS / 120.0));
        let phase = (f64::from(tick % 30) / 30.0) * consts::TAU;
        self.position[1] = base_y + phase.sin().abs() * 0.35;
        self.pitch = (phase.cos() * 1400.0) as i32;
        self.velocity = [0.0, 0.0, 0.0];
        self.moved_tick = Some(tick);
    }

    /// Move a Workshop bot smoothly toward a target world position (`follow` mode).
    #[cfg(feature = "lua-mods")]
    pub(crate) fn step_mod_follow(&mut self, tick: u32, target: [f64; 3], speed_scale: f64) {
        if self.dead {
            return;
        }
        let dx = target[0] - self.position[0];
        let dz = target[2] - self.position[2];
        let dist = dx.hypot(dz);
        if dist <= 2.5 {
            self.yaw = heading([dx, dz]);
            self.velocity = [0.0; 3];
            self.moved_tick = Some(tick);
            return;
        }
        let dt = TICK_PERIOD.as_secs_f64();
        let speed = self.walk.speed * speed_scale.clamp(0.1, 10.0);
        let step = (speed * dt).min(dist - 2.0).max(0.0);
        let nx = dx / dist;
        let nz = dz / dist;
        self.position[0] += nx * step;
        self.position[2] += nz * step;
        self.yaw = heading([nx, nz]);
        self.velocity = [nx * speed, 0.0, nz * speed];
        self.moved_tick = Some(tick);
    }

    /// Persistent controller entity ID for a seated bot across respawns.
    pub fn controller(&self) -> Option<u32> {
        self.slot
            .map(|slot| 0xa000_0000 | ((0x80 + u32::from(slot)) << 8))
    }

    fn new(entity: u32, spawn: Arc<BotSpawn>, walk: BotWalk) -> Self {
        let index = if spawn.route.is_empty() {
            0
        } else {
            spawn.initial_index.rem_euclid(spawn.route.len() as i64) as usize
        };
        Self {
            entity,
            slot: None,
            position: spawn.position,
            yaw: f64::from(spawn.yaw),
            pitch: 0,
            velocity: [0.0; 3],
            movement_flags: 0,
            dead: false,
            walk,
            pace: 0.0,
            direction: [0.0, 1.0],
            index,
            wait: 0.0,
            target: None,
            waypoints: VecDeque::new(),
            walk_route: None,
            patrol_rejoin: None,
            collision: None,
            movement_blockers: None,
            plans: None,
            start: spawn.position,
            keys: false,
            turning: 0.0,
            held: [0; 2],
            last_held: [0; 2],
            input_tick: None,
            moved_tick: None,
            last_tick: None,
            records_from: None,
            patrol_failed: false,
            stunned_until: 0,
            velocity_changes: Vec::new(),
            combat_motion_tick: None,
            anchor_return: None,
            ally_separation: [0.0; 2],
            spawn,
        }
    }

    pub(crate) fn set_breakables(
        &mut self,
        breakables: Arc<crate::collision_scene::BreakableState>,
    ) {
        if let Some(collision) = &mut self.collision {
            collision.body.set_breakables(breakables);
        }
    }

    pub(crate) fn attach_collision(
        &mut self,
        settings: &MoverSettings,
        world: Arc<ContactWorld>,
        category: u16,
        ground: impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) {
        self.attach_scene_collision(settings, None, world, category, ground);
    }

    pub(crate) fn attach_scene_collision(
        &mut self,
        settings: &MoverSettings,
        scene: Option<&CollisionScene>,
        world: Arc<ContactWorld>,
        category: u16,
        mut ground: impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) {
        self.collision = Some(PatrolCollision {
            body: standing_body(settings, Arc::clone(&world), self.position, category),
            lift: pogo(settings, false) as f32,
            trace: None,
        });
        let count = self.spawn.route.len();
        if count != 0 {
            let nav = scene.map(|s| (s, world.as_ref(), settings.nav_agent(category)));
            let first_index = (self.index + 1) % count;
            self.plans = Some(Arc::new(PatrolPlans {
                first_index,
                first: self.detour(
                    self.position,
                    self.spawn.route[first_index],
                    nav,
                    &mut ground,
                ),
                legs: (0..count)
                    .map(|index| {
                        let from = self.spawn.route[(index + count - 1) % count];
                        self.detour(from, self.spawn.route[index], nav, &mut ground)
                    })
                    .collect(),
            }));
        }
    }

    pub(crate) fn enable_contact_trace(&mut self) {
        if let Some(collision) = &mut self.collision {
            collision.trace.get_or_insert_with(Box::default);
        }
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn last_contact_clip(&self) -> Option<ContactClip> {
        self.collision.as_ref()?.trace.as_ref()?.last_clip
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_contact_normals(&self) -> Vec<crate::game_debug::DebugContactNormal> {
        let Some(trace) = self
            .collision
            .as_ref()
            .and_then(|collision| collision.trace.as_ref())
        else {
            return Vec::new();
        };
        trace
            .last_normals
            .iter()
            .copied()
            .map(|mut contact| {
                contact.entity = self.entity;
                contact
            })
            .collect()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_route(&self) -> Option<crate::game_debug::DebugBotRoute> {
        if self.spawn.route.is_empty() && self.target.is_none() && self.waypoints.is_empty() {
            return None;
        }
        let truncated = self.spawn.route.len() > crate::game_debug::MAX_DEBUG_ROUTE_WAYPOINTS
            || self.waypoints.len() > crate::game_debug::MAX_DEBUG_ROUTE_WAYPOINTS;
        Some(crate::game_debug::DebugBotRoute {
            entity: self.entity,
            kind: "patrol",
            patrol_index: (!self.spawn.route.is_empty()).then_some(self.index),
            waypoints: self
                .spawn
                .route
                .iter()
                .copied()
                .take(crate::game_debug::MAX_DEBUG_ROUTE_WAYPOINTS)
                .collect(),
            target: self.target,
            target_entity: None,
            path: self
                .waypoints
                .iter()
                .copied()
                .take(crate::game_debug::MAX_DEBUG_ROUTE_WAYPOINTS)
                .collect(),
            truncated,
        })
    }

    fn detour(
        &self,
        from: [f64; 3],
        target: [f64; 3],
        nav: Option<(&CollisionScene, &ContactWorld, crate::navigation::NavAgent)>,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) -> Detour {
        let Some(collision) = &self.collision else {
            return [None; 2];
        };
        let same_floor = |point: [f64; 3]| (point[1] - from[1]).abs() <= ARRIVED;
        let distance = planar_distance(from, target);
        if distance <= ARRIVED || !same_floor(target) || collision.clear(from, target) {
            return [None; 2];
        }
        let hint = self
            .spawn
            .route
            .iter()
            .copied()
            .filter(|&point| {
                same_floor(point)
                    && planar_distance(from, point) > ARRIVED
                    && planar_distance(point, target) > ARRIVED
                    && collision.supported(from, point, STEP_UP, ground)
                    && collision.supported(point, target, STEP_UP, ground)
            })
            .min_by(|&a, &b| {
                let length = |point| planar_distance(from, point) + planar_distance(point, target);
                length(a).total_cmp(&length(b))
            });
        if hint.is_some() {
            return [hint, None];
        }

        // Try a body-width lane to either side. Each leg must fit the same
        // capsule and stay supported by the same floor as ordinary movement.
        let [x, _, z] = from;
        let [tx, _, tz] = target;
        let scale = 2.0 * f64::from(collision.body.radius()) / distance;
        for sign in [1.0, -1.0] {
            let offset = [(tz - z) * scale * sign, 0.0, (x - tx) * scale * sign];
            let side = |point: [f64; 3]| std::array::from_fn(|axis| point[axis] + offset[axis]);
            let [a, b] = [side(from), side(target)];
            if collision.supported(from, a, ARRIVED, ground)
                && collision.supported(a, b, ARRIVED, ground)
                && collision.supported(b, target, ARRIVED, ground)
            {
                return [Some(a), Some(b)];
            }
        }
        if let Some((scene, world, agent)) = nav {
            if let PathQuery::Found(path) = find_path(
                scene,
                Some(world),
                &agent,
                from,
                target,
                &PathOptions::default(),
            ) {
                if path.len() == 3 {
                    return [Some(path[1]), None];
                } else if path.len() >= 4 {
                    return [Some(path[1]), Some(path[path.len() - 2])];
                }
            }
        }
        [None; 2]
    }

    fn planned_detour(&self, previous: usize) -> Detour {
        let Some(plans) = &self.plans else {
            return [None; 2];
        };
        if self.index != (previous + 1) % self.spawn.route.len() {
            // The cached leg belongs to the authored cyclic edge only.
            return [None; 2];
        }
        if self.index == plans.first_index && self.position == self.spawn.position {
            return plans.first;
        }
        let from = self.spawn.route[previous];
        if planar_distance(self.position, from) <= ARRIVED
            && (self.position[1] - from[1]).abs() <= STEP_UP
        {
            plans.legs[self.index]
        } else {
            [None; 2]
        }
    }

    /// The floor provider can give a surface Y within the top and bottom
    /// probe bounds. A missing surface uses the authored hint heights.
    pub(crate) fn step(
        &mut self,
        tick: u32,
        script: &PatrolScript,
        mut ground: impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) -> io::Result<()> {
        let sep = std::mem::take(&mut self.ally_separation);
        if !self.dead && sep != [0.0; 2] && !self.is_stunned(tick) {
            let dt = TICK_PERIOD.as_secs_f64();
            let [x, y, z] = self.position;
            let nx = x + sep[0];
            let nz = z + sep[1];
            let ny = ground(nx, nz, y + STEP_UP, y - DROP).unwrap_or(y);
            let intended = [nx, ny, nz];
            let blockers = self.movement_blocker_snapshot(tick);
            let after = self.collision.as_mut().map_or(intended, |collision| {
                collision.advance(
                    tick,
                    self.position,
                    intended,
                    dt,
                    blockers.as_deref().unwrap_or(&[]),
                )
            });
            if after != self.position {
                self.position = after;
                self.moved_tick = Some(tick);
            }
        }
        if !self.patrol_active() {
            if !self.dead && self.anchor_return.is_some() && self.walk_route.is_none() {
                if self.is_stunned(tick) {
                    self.last_tick = Some(tick);
                    self.stop_objective(tick);
                    return Ok(());
                }
                let ticks = self
                    .last_tick
                    .map_or(1, |last| tick.saturating_sub(last))
                    .min(CATCH_UP)
                    .min(if self.stunned_until == 0 {
                        u32::MAX
                    } else {
                        tick.saturating_sub(self.stunned_until.saturating_sub(1))
                    });
                if ticks == 0 {
                    return Ok(());
                }
                self.last_tick = Some(tick);
                self.step_anchor_return(tick, ticks, &mut ground);
            }
            return Ok(());
        }
        if self.is_stunned(tick) {
            self.last_tick = Some(tick);
            self.stop_objective(tick);
            return Ok(());
        }
        let ticks = self
            .last_tick
            .map_or(1, |last| tick.saturating_sub(last))
            .min(CATCH_UP)
            .min(if self.stunned_until == 0 {
                u32::MAX
            } else {
                tick.saturating_sub(self.stunned_until.saturating_sub(1))
            });
        if ticks == 0 {
            return Ok(());
        }
        let first_step = self.last_tick.is_none();
        self.last_tick = Some(tick);
        let result = self.step_patrol(tick, ticks, first_step, script, &mut ground);
        if result.is_err() {
            self.patrol_failed = true;
            self.keys = false;
            self.held = [0; 2];
            self.stand();
            self.moved_tick = Some(tick);
        }
        result
    }

    pub(crate) fn is_stunned(&self, tick: u32) -> bool {
        !self.dead && tick < self.stunned_until
    }

    pub(crate) fn stun_for(&mut self, tick: u32, ticks: u32) {
        if self.dead || ticks == 0 {
            return;
        }
        self.stunned_until = self.stunned_until.max(tick.saturating_add(ticks));
        self.stop_objective(tick);
    }

    pub(crate) fn clear_stun(&mut self) {
        self.stunned_until = 0;
    }

    pub(crate) fn patrol_active(&self) -> bool {
        !self.spawn.route.is_empty() && !self.dead && !self.patrol_failed
    }

    #[cfg(test)]
    pub(crate) fn fixture_walker(target: [f64; 3]) -> Self {
        tests::walker(target)
    }

    fn ai_actor_snapshot(&self, tick: u32) -> AiActorSnapshot {
        AiActorSnapshot {
            entity: self.entity,
            generation: None,
            team: self.spawn.team,
            position: self.position,
            yaw: self.yaw,
            alive: !self.dead,
            health: self.spawn.health,
            max_health: self.spawn.health,
            stunned: self.is_stunned(tick),
            patrol: Some(AiPatrolSnapshot {
                route_index: self.index,
                route_count: self.spawn.route.len(),
                wait_seconds: self.wait,
                walking: self.keys,
            }),
        }
    }

    fn step_patrol(
        &mut self,
        tick: u32,
        ticks: u32,
        first_step: bool,
        script: &PatrolScript,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) -> io::Result<()> {
        let blockers = self.movement_blocker_snapshot(tick);
        let blockers = blockers.as_deref().unwrap_or(&[]);
        if first_step {
            self.wait = script.wait(self.ai_actor_snapshot(tick), WaitEvent::Spawn, tick)?;
        }
        let dt = TICK_PERIOD.as_secs_f64();
        for at in tick - (ticks - 1)..=tick {
            self.tick(at, dt, script, ground, blockers)?;
        }
        Ok(())
    }

    fn tick(
        &mut self,
        tick: u32,
        dt: f64,
        script: &PatrolScript,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
        blockers: &[CapsulePose],
    ) -> io::Result<()> {
        let before = (self.position, self.yaw.round_ties_even());
        self.held = [0; 2];
        if !self.keys {
            self.wait -= dt;
            if self.target.is_none() && self.wait <= EPSILON {
                let previous = self.index;
                self.index = script.think(self.ai_actor_snapshot(tick), tick)?;
                let goal = self.spawn.route[self.index];
                let detour = self.planned_detour(previous);
                self.waypoints.extend(detour.into_iter().flatten());
                self.target = Some(self.waypoints.pop_front().unwrap_or(goal));
                if self.target != Some(goal) {
                    self.waypoints.push_back(goal);
                }
                self.start = self.position;
                self.keys = true;
                self.turning = 0.0;
            }
        }
        if self.keys {
            self.walk(tick, dt, script, ground, blockers)?;
        } else {
            if self.target.is_some() {
                self.slide(tick, dt, ground, blockers);
            }
            if self.turning > EPSILON {
                self.turning -= dt;
                self.turn(dt);
            }
        }
        if (self.position, self.yaw.round_ties_even()) != before {
            self.moved_tick = Some(tick);
        }
        Ok(())
    }

    fn walk(
        &mut self,
        tick: u32,
        dt: f64,
        script: &PatrolScript,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
        blockers: &[CapsulePose],
    ) -> io::Result<()> {
        let mut offset = self.to_target();
        let mut distance = offset[0].hypot(offset[1]);
        if distance <= ARRIVED {
            if let Some(goal) = self.waypoints.pop_front() {
                self.target = Some(goal);
                self.start = self.position;
                offset = self.to_target();
                distance = offset[0].hypot(offset[1]);
            }
        }
        if distance <= ARRIVED {
            self.wait = script.wait(self.ai_actor_snapshot(tick), WaitEvent::Release, tick)?;
            self.keys = false;
            self.stand();
            return Ok(());
        }
        let pace = self.walk.speed.min(self.pace + self.walk.acceleration * dt);
        let mut stride = pace * dt;
        let stop = self
            .waypoints
            .is_empty()
            .then(|| self.slide_length(pace, dt))
            .filter(|&slide| distance - stride <= slide);
        if let Some(slide) = stop {
            // Resolve the decision before publishing this tick's steering.
            self.wait = script.wait(self.ai_actor_snapshot(tick), WaitEvent::Release, tick)?;
            stride = (distance - slide).max(0.0);
            self.keys = false;
            self.turning = TURN_AFTER;
        }
        self.direction = offset.map(|axis| axis / distance);
        self.turn(dt);
        self.pace = pace;
        self.held = throttles(self.yaw, self.direction);
        self.last_held = self.held;
        self.input_tick = Some(tick);
        self.advance(tick, stride, distance, ground, blockers);
        Ok(())
    }

    fn slide(
        &mut self,
        tick: u32,
        dt: f64,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
        blockers: &[CapsulePose],
    ) {
        self.pace = (self.pace - self.walk.brake * dt).max(0.0);
        let [dx, dz] = self.to_target();
        let distance = dx.hypot(dz);
        if self.pace > 0.0
            && distance > EPSILON
            && self.advance(tick, self.pace * dt, distance, ground, blockers) < distance - EPSILON
        {
            return;
        }
        self.stand();
    }

    fn stand(&mut self) {
        self.pace = 0.0;
        self.velocity = [0.0; 3];
        self.target = None;
        self.waypoints.clear();
    }

    fn to_target(&self) -> [f64; 2] {
        let [tx, _, tz] = self.target.expect("a walking bot has a target");
        [tx - self.position[0], tz - self.position[2]]
    }

    fn slide_length(&self, pace: f64, dt: f64) -> f64 {
        let slow = self.walk.brake * dt;
        let mut pace = pace - slow;
        let mut length = 0.0;
        if slow <= 0.0 && pace > 0.0 {
            return f64::INFINITY;
        }
        for _ in 0..CATCH_UP {
            if pace <= 0.0 {
                return length;
            }
            length += pace * dt;
            pace -= slow;
        }
        // Invalid or extremely weak braking must not stall the tick.
        f64::INFINITY
    }

    fn turn(&mut self, dt: f64) {
        let left = s16(heading(self.direction) - self.yaw);
        let units = TURN_RATE * dt;
        self.yaw = s16(self.yaw + left.clamp(-units, units));
    }

    fn advance(
        &mut self,
        tick: u32,
        mut stride: f64,
        distance: f64,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
        blockers: &[CapsulePose],
    ) -> f64 {
        let dt = TICK_PERIOD.as_secs_f64();
        let [x, y, z] = self.position;
        let [dx, dz] = self.direction;
        let probe = stride.min(distance);
        let probe_y = self.floor(x + dx * probe, y, z + dz * probe, ground);
        let rise = (probe_y - y).abs();
        if 0.0 < rise && rise <= MAX_SLOPE * probe {
            stride *= probe / probe.hypot(rise);
        }
        let reuse_probe = stride == probe && stride < distance;
        let [nx, nz] = if stride >= distance {
            stride = distance;
            let [tx, _, tz] = self.target.expect("a walking bot has a target");
            [tx, tz]
        } else {
            [x + dx * stride, z + dz * stride]
        };
        let ny = if reuse_probe {
            probe_y
        } else {
            self.floor(nx, y, nz, ground)
        };
        let intended = [nx, ny, nz];
        let [nx, ny, nz] = self.collision.as_mut().map_or(intended, |collision| {
            collision.advance(tick, self.position, intended, dt, blockers)
        });
        self.position = [nx, ny, nz];
        self.velocity = [(nx - x) / dt, (ny - y) / dt, (nz - z) / dt];
        if self.position == intended {
            stride
        } else {
            ((nx - x) * dx + (nz - z) * dz).max(0.0)
        }
    }

    fn floor(
        &self,
        x: f64,
        y: f64,
        z: f64,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) -> f64 {
        if let Some(height) = ground(x, z, y + STEP_UP, y - DROP) {
            return height;
        }
        let [sx, sy, sz] = self.start;
        let [tx, ty, tz] = self.target.expect("a walking bot has a target");
        let whole = (tx - sx).hypot(tz - sz);
        let gone = if whole <= 0.0 {
            1.0
        } else {
            ((x - sx).hypot(z - sz) / whole).min(1.0)
        };
        sy + (ty - sy) * gone
    }

    pub fn walking(&self) -> bool {
        self.target.is_some()
    }

    pub fn died(&mut self, tick: u32) {
        self.dead = true;
        self.walk_route = None;
        self.patrol_rejoin = None;
        self.anchor_return = None;
        self.ally_separation = [0.0; 2];
        self.movement_blockers = None;
        self.clear_velocity_changes();
        self.keys = false;
        self.held = [0; 2];
        self.stand();
        self.moved_tick = Some(tick);
    }

    pub(crate) fn spawn_hitbox_ready(&self, tick: u32) -> bool {
        !self.dead && self.records_from.is_none_or(|ready| tick >= ready)
    }

    pub(crate) fn set_ally_separation(&mut self, delta: [f64; 2]) {
        self.ally_separation = delta;
    }

    pub(crate) fn has_anchor_return(&self) -> bool {
        !self.dead && self.anchor_return.is_some()
    }

    pub fn respawned(&self, entity: u32, records_from: u32) -> Self {
        let mut fresh = Self::new(entity, Arc::clone(&self.spawn), self.walk);
        fresh.slot = self.slot;
        fresh.collision = self.collision.as_ref().map(|collision| PatrolCollision {
            body: collision
                .body
                .fresh_at(standing_origin(fresh.position, collision.lift)),
            lift: collision.lift,
            trace: collision.trace.as_ref().map(|_| Box::default()),
        });
        fresh.plans = self.plans.clone();
        fresh.records_from = Some(records_from);
        fresh
    }

    pub fn record_due(&self, tick: u32, every: u32) -> bool {
        if self.records_from.is_some_and(|from| tick < from) {
            return false;
        }
        if self.walking()
            || self
                .moved_tick
                .is_some_and(|moved| tick.saturating_sub(moved) < SETTLE_TICKS)
        {
            return true;
        }
        let every = every.max(1);
        let period = (SETTLE_TICKS / every).max(1);
        (tick / every) % period == self.entity % period
    }

    pub(crate) fn apply_encounter_facing(&mut self, [x, _, z]: [f64; 3], tick: u32) {
        if self.dead || (x == 0.0 && z == 0.0) {
            return;
        }
        let yaw = heading([x, z]);
        if self.yaw != yaw {
            self.yaw = yaw;
            self.moved_tick = Some(tick);
        }
    }

    pub(crate) fn queue_velocity_changes(
        &mut self,
        changes: Vec<(u32, crate::mover::VelocityChange)>,
    ) {
        for (frame, change) in changes {
            crate::mover::Mover::push_merged_velocity_change(
                &mut self.velocity_changes,
                frame,
                change,
            );
        }
    }

    /// Cancel pending movement edits when this body dies.
    pub(crate) fn clear_velocity_changes(&mut self) {
        self.velocity_changes.clear();
        self.combat_motion_tick = None;
    }

    pub(crate) fn clear_script_velocity_changes(&mut self) {
        self.velocity_changes
            .retain(|(_, change)| matches!(change, VelocityChange::Impulse(_)));
    }

    pub(crate) fn has_combat_motion(&self) -> bool {
        self.combat_motion_tick.is_some()
            || self
                .velocity_changes
                .iter()
                .any(|(_, change)| matches!(change, VelocityChange::Impulse(_)))
    }

    pub(crate) fn has_collision(&self) -> bool {
        self.collision.is_some()
    }

    /// Consume combat impulses before AI steering, including for stationary actors.
    pub(crate) fn step_combat_motion(
        &mut self,
        tick: u32,
        settings: &MoverSettings,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) -> bool {
        if self.dead || !self.has_combat_motion() {
            return false;
        }
        if self.combat_motion_tick == Some(tick) {
            return true;
        }
        let mut velocity = self.velocity;
        let continuing = self.combat_motion_tick.is_some() && velocity != [0.0; 3];
        let mut applied_impulse = false;
        self.velocity_changes.retain(|&(due, change)| {
            if due <= tick && matches!(change, VelocityChange::Impulse(_)) {
                let before = velocity;
                change.apply(&mut velocity);
                applied_impulse |= before != velocity;
                false
            } else {
                true
            }
        });
        self.combat_motion_tick = None;
        if !continuing && !applied_impulse {
            return false;
        }
        let blockers = self.movement_blocker_snapshot(tick);
        let Some(collision) = self.collision.as_mut() else {
            return false;
        };
        let dt = TICK_PERIOD.as_secs_f64();
        let from = self.position;
        let [x, y, z] = from;
        let grounded = velocity[1] <= 0.0 && ground(x, z, y + ARRIVED, y - ARRIVED).is_some();
        if grounded
            && self.movement_flags & IN_AIR == 0
            && !applied_impulse
            && velocity.map(snap) == [0.0; 3]
        {
            // A sub-grid residue from contact or friction has no visible
            // motion and must release steering back to the AI.
            self.velocity = [0.0; 3];
            return false;
        }
        let previous_flags = self.movement_flags;
        if grounded {
            if velocity[1] < 0.0 {
                velocity[1] = 0.0;
            }
            if velocity[1] == 0.0 {
                self.movement_flags &= !IN_AIR;
            } else {
                self.movement_flags |= IN_AIR;
            }
        } else {
            self.movement_flags |= IN_AIR;
        }
        // Bound extreme knockback speeds so impulses (e.g. Lucio Soundwave) can never
        // tunnel bots through thin floors or produce out-of-world NaN coordinates.
        let speed = velocity[0].hypot(velocity[1]).hypot(velocity[2]);
        if !speed.is_finite() {
            self.velocity = [0.0; 3];
            return false;
        }
        if speed > 45.0 {
            let scale = 45.0 / speed;
            velocity = [
                velocity[0] * scale,
                velocity[1] * scale,
                velocity[2] * scale,
            ];
        }
        let starting_velocity = velocity;
        let velocity = VelocityStep {
            base: settings,
            settings,
            input: MotionInput::from(&Command::default()),
            ground: if grounded {
                Ground::Standing
            } else {
                Ground::Airborne
            },
            crouched: false,
            dt,
        }
        .advance(velocity);
        if !velocity.into_iter().all(|axis| (axis as f32).is_finite()) {
            self.velocity = [0.0; 3];
            return false;
        }
        let mut travel_velocity = velocity;
        let horizontal_impulse =
            starting_velocity[0] != self.velocity[0] || starting_velocity[2] != self.velocity[2];
        if horizontal_impulse && grounded {
            // The impulse changes the starting velocity immediately. Ground
            // friction can stop it by the end of the tick, after some travel.
            for axis in [0, 2] {
                travel_velocity[axis] = (starting_velocity[axis] + velocity[axis]) * 0.5;
            }
        }
        let mut to = std::array::from_fn(|axis| from[axis] + travel_velocity[axis] * dt);
        if grounded && to[1] < from[1] {
            to[1] = from[1];
        }
        let mut velocity = velocity;
        let mut after = match collision.try_advance(
            tick,
            from,
            to,
            dt,
            blockers.as_deref().unwrap_or(&[]),
            Some(&mut velocity),
        ) {
            Ok(after) => after,
            Err(_) => {
                velocity = [0.0; 3];
                from
            }
        };
        // Clamp combat-motion vertical position against walkable ground beneath the
        // actor's feet so explosive knockback impulses never push bots under the floor
        // without accidentally snapping upward-launched bots onto overhead geometry.
        let floor_probe = ground(
            after[0],
            after[2],
            from[1].min(after[1]) + 0.45,
            after[1].min(from[1]) - 3.5,
        );
        if let Some(floor_y) = floor_probe {
            if after[1] < floor_y || (velocity[1] <= 0.0 && after[1] <= floor_y + 0.02) {
                after[1] = floor_y;
                if velocity[1] < 0.0 {
                    velocity[1] = 0.0;
                }
                if velocity[1] <= 0.0 {
                    self.movement_flags &= !IN_AIR;
                }
            }
        } else if grounded && after[1] < from[1] - 0.15 {
            // If an explosive impulse swept a grounded bot off/under thin geometry where
            // no walkable floor exists beneath, keep feet at or above the starting height.
            after[1] = from[1];
            if velocity[1] < 0.0 {
                velocity[1] = 0.0;
            }
        }
        self.position = after;
        if after != from {
            self.walk_route = None;
            if self.patrol_active() {
                // Keep the authored leg in progress. During a wait, return to
                // the position the bot was holding before the impulse.
                let destination = self.target.map_or(from, |_| self.spawn.route[self.index]);
                self.patrol_rejoin.get_or_insert(destination);
            } else if !self.dead
                && self.spawn.placement_graphs.is_empty()
                && planar_distance(after, self.spawn.position) > ARRIVED
            {
                self.anchor_return = Some(self.spawn.position);
            }
        }
        // The solver may move an overlapping capsule sideways to separate it.
        // That position correction must not create momentum for the next tick.
        self.velocity = velocity;
        self.held = [0; 2];
        self.combat_motion_tick = Some(tick);
        self.last_tick = Some(tick);
        if after != from || self.movement_flags != previous_flags {
            self.moved_tick = Some(tick);
        }
        true
    }

    fn step_anchor_return(
        &mut self,
        tick: u32,
        ticks: u32,
        ground: &mut impl FnMut(f64, f64, f64, f64) -> Option<f64>,
    ) {
        let blockers = self.movement_blocker_snapshot(tick);
        let blockers = blockers.as_deref().unwrap_or(&[]);
        let dt = TICK_PERIOD.as_secs_f64();
        for at in tick - (ticks - 1)..=tick {
            let before = (self.position, self.yaw.round_ties_even());
            self.held = [0; 2];
            if self.target.is_some() {
                let mut offset = self.to_target();
                let mut distance = offset[0].hypot(offset[1]);
                if distance <= ARRIVED {
                    if let Some(goal) = self.waypoints.pop_front() {
                        self.target = Some(goal);
                        self.start = self.position;
                        offset = self.to_target();
                        distance = offset[0].hypot(offset[1]);
                    }
                }
                if distance <= ARRIVED {
                    self.stand();
                    self.keys = false;
                } else {
                    self.direction = offset.map(|axis| axis / distance);
                    let desired_yaw = heading(self.direction);
                    let yaw_error = s16(desired_yaw - self.yaw);
                    let return_turn = (TURN_RATE * 3.0) * dt;
                    if yaw_error.abs() <= return_turn + EPSILON {
                        self.yaw = s16(desired_yaw);
                    } else {
                        self.yaw = s16(self.yaw + yaw_error.clamp(-return_turn, return_turn));
                    }
                    // Turn toward the return path before walking so knocked-back bots
                    // do not backpedal facing away from their destination.
                    if s16(desired_yaw - self.yaw).abs() > 8192.0 {
                        self.pace = 0.0;
                        self.velocity = [0.0; 3];
                        self.held = [0; 2];
                    } else {
                        let pace = self.walk.speed.min(self.pace + self.walk.acceleration * dt);
                        let stride = (pace * dt).min(distance);
                        self.pace = pace;
                        self.held = throttles(self.yaw, self.direction);
                        self.last_held = self.held;
                        self.input_tick = Some(at);
                        let moved = self.advance(at, stride, distance, ground, blockers);
                        if moved <= EPSILON {
                            self.stand();
                            self.keys = false;
                            self.held = [0; 2];
                        }
                    }
                }
            } else {
                let target_yaw = f64::from(self.spawn.yaw);
                let left = s16(target_yaw - self.yaw);
                let units = TURN_RATE * dt;
                if left.abs() <= units + EPSILON {
                    self.yaw = s16(target_yaw);
                    self.anchor_return = None;
                } else {
                    self.yaw = s16(self.yaw + left.clamp(-units, units));
                }
            }
            if (self.position, self.yaw.round_ties_even()) != before {
                self.moved_tick = Some(at);
            }
            if self.anchor_return.is_none() {
                break;
            }
        }
    }

    pub(crate) fn rejoin_patrol(
        &mut self,
        tick: u32,
        plan: impl FnOnce([f64; 3], [f64; 3]) -> PathQuery,
    ) {
        if self.has_combat_motion() || self.is_stunned(tick) {
            return;
        }
        if !self.patrol_active() {
            if !self.dead && self.spawn.placement_graphs.is_empty() && self.target.is_none() {
                let Some(destination) = self.anchor_return else {
                    return;
                };
                if planar_distance(self.position, destination) > ARRIVED {
                    if let PathQuery::Found(path) = plan(self.position, destination) {
                        self.waypoints = path.into_iter().skip(1).collect();
                        self.target = Some(self.waypoints.pop_front().unwrap_or(destination));
                    } else {
                        self.waypoints.clear();
                        self.target = Some(destination);
                    }
                    self.start = self.position;
                    self.keys = true;
                    self.turning = 0.0;
                }
            }
            return;
        }
        let Some(destination) = self.patrol_rejoin.take() else {
            return;
        };
        if let PathQuery::Found(path) = plan(self.position, destination) {
            self.waypoints = path.into_iter().skip(1).collect();
            self.target = Some(self.waypoints.pop_front().unwrap_or(destination));
            self.start = self.position;
            self.keys = true;
            self.turning = 0.0;
        }
    }

    /// Freeze the room's actor-contact admission for this update tick.
    pub(crate) fn set_movement_blockers(&mut self, tick: u32, blockers: Vec<CapsulePose>) {
        self.movement_blockers = Some((tick, blockers.into()));
    }

    pub(crate) fn has_movement_blockers(&self, tick: u32) -> bool {
        self.movement_blockers
            .as_ref()
            .is_some_and(|(at, _)| *at == tick)
    }

    pub(crate) fn movement_blockers(&self, tick: u32) -> &[CapsulePose] {
        self.movement_blockers
            .as_ref()
            .filter(|(at, _)| *at == tick)
            .map_or(&[], |(_, blockers)| blockers.as_ref())
    }

    fn movement_blocker_snapshot(&self, tick: u32) -> Option<Arc<[CapsulePose]>> {
        self.movement_blockers
            .as_ref()
            .filter(|(at, _)| *at == tick)
            .map(|(_, blockers)| Arc::clone(blockers))
    }

    /// Use the bound physical capsule, or the same standing geometry before binding.
    pub(crate) fn contact_capsule(&self, settings: &MoverSettings) -> CapsulePose {
        self.collision.as_ref().map_or_else(
            || standing_capsule(settings, self.position),
            |collision| CapsulePose {
                position: standing_origin(self.position, collision.lift),
                ..collision.body.capsule_pose()
            },
        )
    }

    /// Attempt one scheduled capsule move without changing steering or velocity.
    pub(crate) fn apply_scripted_motion(&mut self, tick: u32, motion: ScriptedMotion) -> bool {
        if self.dead {
            return false;
        }
        let blockers = self.movement_blocker_snapshot(tick);
        let Some(collision) = self.collision.as_mut() else {
            return false;
        };
        let from = self.position;
        let blockers = blockers.as_deref().unwrap_or(&[]);
        let after = match motion {
            ScriptedMotion::Sweep(displacement) => {
                let delta = displacement.delta();
                let to = std::array::from_fn(|axis| from[axis] + f64::from(delta[axis]));
                if !from
                    .into_iter()
                    .chain(to)
                    .all(|axis| (axis as f32).is_finite())
                {
                    return false;
                }
                let Ok(after) = collision.try_advance(
                    tick,
                    from,
                    to,
                    TICK_PERIOD.as_secs_f64(),
                    blockers,
                    None,
                ) else {
                    return false;
                };
                after
            }
            ScriptedMotion::Relocate(relocation) => {
                let Some(to) = relocation.destination_from(from) else {
                    return false;
                };
                if !collision
                    .body
                    .relocate_if_clear(standing_origin(to, collision.lift), blockers)
                    .unwrap_or(false)
                {
                    return false;
                }
                if let Some(trace) = &mut collision.trace {
                    trace.last_clip = None;
                    #[cfg(any(test, feature = "inspector"))]
                    trace.last_normals.clear();
                }
                to
            }
            ScriptedMotion::Teleport(relocation) => {
                let Some(to) = relocation.teleport_destination() else {
                    return false;
                };
                let origin = standing_origin(to, collision.lift);
                if !collision
                    .body
                    .relocate_if_clear(origin, blockers)
                    .unwrap_or(false)
                {
                    return false;
                }
                if let Some(trace) = &mut collision.trace {
                    trace.last_clip = None;
                    #[cfg(any(test, feature = "inspector"))]
                    trace.last_normals.clear();
                }
                to
            }
        };
        self.position = after;
        if after != from {
            self.walk_route = None;
            self.moved_tick = Some(tick);
        }
        true
    }

    pub(crate) fn walk_speed(&self) -> f64 {
        self.walk.speed
    }

    pub(crate) fn move_to_objective(&mut self, tick: u32, target: [f64; 3], speed: f64) {
        self.walk_to(tick, target, speed, true, false);
    }

    pub(crate) fn follow_walk_request(
        &mut self,
        tick: u32,
        destination: [f64; 3],
        speed: f64,
        plan: impl FnOnce([f64; 3]) -> PathQuery,
    ) {
        if self.combat_motion_tick == Some(tick) {
            return;
        }
        if self
            .walk_route
            .as_ref()
            .is_some_and(|route| route.destination != destination)
        {
            self.walk_route = None;
        }
        let route = self.walk_route.get_or_insert_with(|| {
            let waypoints = match plan(self.position) {
                PathQuery::Found(path) => path.into_iter().skip(1).collect(),
                _ => VecDeque::new(),
            };
            WalkRoute {
                destination,
                waypoints,
            }
        });
        while route
            .waypoints
            .front()
            .is_some_and(|&next| planar_distance(self.position, next) <= ARRIVED)
        {
            route.waypoints.pop_front();
        }
        // The graph's destination remains the last target of the mover.
        let next = route.waypoints.front().copied().unwrap_or(destination);
        self.walk_to(tick, next, speed, true, true);
    }

    pub(crate) fn end_walk_request(&mut self, tick: u32) {
        self.walk_route = None;
        let home_yaw = f64::from(self.spawn.yaw);
        if (self.yaw - home_yaw).abs() > EPSILON {
            self.yaw = home_yaw;
            self.moved_tick = Some(tick);
        }
        self.stop_objective(tick);
    }

    fn walk_to(
        &mut self,
        tick: u32,
        target: [f64; 3],
        speed: f64,
        face_target: bool,
        smooth_turn: bool,
    ) {
        if self.combat_motion_tick == Some(tick) {
            return;
        }
        let start = self.position;
        let [x, y, z] = std::array::from_fn(|axis| target[axis] - start[axis]);
        let distance = x.hypot(y).hypot(z);
        if distance == 0.0 {
            self.stop_objective(tick);
            return;
        }
        let dt = TICK_PERIOD.as_secs_f64();
        let fraction = (speed * dt / distance).min(1.0);
        let mut intended = [
            start[0] + x * fraction,
            start[1] + y * fraction,
            start[2] + z * fraction,
        ];
        if self
            .velocity_changes
            .iter()
            .any(|&(frame, _)| frame == tick)
        {
            let mut velocity = [x * fraction / dt, y * fraction / dt, z * fraction / dt];
            for &(_, change) in self
                .velocity_changes
                .iter()
                .filter(|&&(frame, _)| frame == tick)
            {
                change.apply(&mut velocity);
            }
            intended = std::array::from_fn(|axis| start[axis] + velocity[axis] * dt);
        }
        self.velocity_changes.retain(|&(frame, _)| frame > tick);
        let blockers = self.movement_blocker_snapshot(tick);
        let collision = self.collision.as_mut().expect("bound objective capsule");
        self.position = collision.advance(
            tick,
            start,
            intended,
            dt,
            blockers.as_deref().unwrap_or(&[]),
        );
        if self.position == start {
            self.stop_objective(tick);
            return;
        }
        let delta: [f64; 3] = std::array::from_fn(|axis| self.position[axis] - start[axis]);
        self.velocity = delta.map(|axis| axis / dt);
        let [dx, _, dz] = delta;
        let horizontal = dx.hypot(dz);
        if face_target && (x != 0.0 || z != 0.0) {
            if smooth_turn {
                let planar = x.hypot(z);
                if planar > EPSILON {
                    self.direction = [x / planar, z / planar];
                    self.turn(dt);
                }
            } else {
                self.yaw = heading([x, z]);
            }
        }
        self.held = if (snap(dx), snap(dz)) == (0.0, 0.0) {
            [0; 2]
        } else {
            throttles(self.yaw, [dx / horizontal, dz / horizontal])
        };
        if self.held != [0; 2] {
            self.last_held = self.held;
            self.input_tick = Some(tick);
        }
        self.moved_tick = Some(tick);
    }

    pub(crate) fn stop_objective(&mut self, tick: u32) {
        if self.combat_motion_tick == Some(tick) {
            return;
        }
        self.velocity_changes.retain(|&(frame, _)| frame > tick);
        if self.held != [0; 2] || self.velocity != [0.0; 3] {
            self.held = [0; 2];
            self.velocity = [0.0; 3];
            self.moved_tick = Some(tick);
        }
    }

    pub(crate) fn apply_encounter_aim(&mut self, [yaw, pitch]: [i32; 2], tick: u32) {
        if !self.dead && (self.yaw != f64::from(yaw) || self.pitch != pitch) {
            self.yaw = f64::from(yaw);
            self.pitch = pitch;
            self.moved_tick = Some(tick);
        }
    }

    pub(crate) fn apply_objective_pose(&mut self, pose: Movement, tick: u32) {
        self.position = pose.position;
        self.yaw = f64::from(pose.yaw);
        self.pitch = pose.pitch;
        self.velocity = pose.velocity;
        self.movement_flags = pose.flags;
        self.held = pose.throttles;
        if self.held != [0; 2] {
            self.last_held = self.held;
            self.input_tick = Some(tick);
        }
        self.moved_tick = Some(tick);
    }

    pub(crate) fn apply_objective_aim(&mut self, [x, y, z]: [f64; 3], tick: u32) {
        let yaw = heading([x, z]) as i32;
        let pitch = (-y.atan2(x.hypot(z)) * YAW_UNITS / consts::TAU).round_ties_even() as i32;
        self.apply_encounter_aim([yaw, pitch], tick);
    }

    pub fn movement(&self, tick: u32) -> Movement {
        let input_frame = if self.held != [0; 2] {
            None
        } else {
            match self.input_tick {
                None => Some(NO_INPUT),
                Some(frame) if frame < tick => Some(frame),
                Some(_) => None,
            }
        };
        Movement {
            position: self.position,
            yaw: s16(self.yaw.round_ties_even()) as i32,
            pitch: self.pitch,
            velocity: self.velocity,
            flags: if self.dead {
                DEAD_MOVEMENT_FLAG
            } else {
                self.movement_flags
            },
            throttles: self.held,
            input_frame,
            input_throttles: self.last_held,
            gravity: GRAVITY_SCALE,
            ..Movement::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::game_clock::SEND_EVERY;
    use crate::game_content::PRACTICE_RANGE_MAP;
    use crate::world::movement_record;
    use sha2::{Digest, Sha256};

    const TRAINING: u64 = 0x02e0_0000_0000_016b;
    const TRAINING_BODY: u64 = 0x0400_0000_0000_1403;
    const FAR_RUNNER: u64 = 0x02e0_0000_0000_016e;

    pub(super) fn walker(target: [f64; 3]) -> Bot {
        Bot::new(
            BOT_ENTITY,
            Arc::new(BotSpawn {
                hero: TRAINING,
                name: "Training Bot".into(),
                body: TRAINING_BODY,
                team: 1,
                health: 200.0,
                position: [0.0; 3],
                yaw: 0,
                placement_graphs: Vec::new(),
                route: vec![[0.0; 3], target],
                initial_index: 0,
            }),
            BotWalk::from_mover(4.5, 1.0, 15.0),
        )
    }

    #[test]
    fn swept_bot_displacement_requires_a_live_capsule_and_preserves_motion() {
        use crate::{
            collision_cache::{Mesh, Triangle},
            contact_mesh::ContactMesh,
            contact_world::{StaticGeometry, StaticShape},
            script_init::{Relocation, SweptDisplacement},
        };

        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/native-swept-displacement-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["case_id"], "native-swept-displacement.synthetic.v1");
        assert_eq!(fixture["evidence"], "synthetic-native");
        let input = &fixture["input"];
        let category: u16 = input["category"].as_u64().unwrap().try_into().unwrap();
        let settings = MoverSettings {
            stand_height: input["stand_height"].as_f64().unwrap(),
            stand_radius: input["stand_radius"].as_f64().unwrap(),
            ..MoverSettings::default()
        };
        let displacement =
            SweptDisplacement::new(serde_json::from_value(input["displacement"].clone()).unwrap())
                .unwrap();
        let mut free = walker([0.0, 0.0, 10.0]);
        assert!(!free.apply_scripted_motion(1, ScriptedMotion::Sweep(displacement)));
        assert_eq!(free.position, [0.0; 3]);
        free.attach_collision(
            &settings,
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            category,
            |_, _, _, _| Some(0.0),
        );
        free.velocity = serde_json::from_value(input["velocity"].clone()).unwrap();
        free.yaw = 8192.0;
        free.pitch = -2048;
        free.held = [21, -32];
        let before = free.movement(1);
        let input_tick = free.input_tick;
        assert!(free.apply_scripted_motion(2, ScriptedMotion::Sweep(displacement)));
        assert_eq!(free.position, displacement.delta().map(f64::from));
        let after = free.movement(2);
        assert_eq!(after.velocity, before.velocity);
        assert_eq!(
            (after.yaw, after.pitch, after.throttles),
            (before.yaw, before.pitch, before.throttles)
        );
        assert_eq!(free.input_tick, input_tick);
        assert_eq!(free.moved_tick, Some(2));
        free.dead = true;
        let stopped = free.position;
        assert!(!free.apply_scripted_motion(3, ScriptedMotion::Sweep(displacement)));
        assert_eq!(free.position, stopped);

        let wall = Mesh {
            vertices: serde_json::from_value(input["wall_vertices"].clone()).unwrap(),
            triangles: input["wall_triangles"]
                .as_array()
                .unwrap()
                .iter()
                .map(|corners| Triangle {
                    corners: serde_json::from_value(corners.clone()).unwrap(),
                    wings: [-1; 3],
                    mask: category,
                    surface: 0,
                })
                .collect(),
        };
        let mesh = ContactMesh::new(wall, [1.0; 3], u16::MAX).unwrap();
        let shape = StaticShape::new(StaticGeometry::Mesh(Arc::new(mesh)), None).unwrap();
        let mut blocked = walker([0.0, 0.0, 10.0]);
        blocked.attach_collision(
            &settings,
            Arc::new(ContactWorld::new(vec![shape]).unwrap()),
            category,
            |_, _, _, _| Some(0.0),
        );
        blocked.enable_contact_trace();
        blocked.velocity = before.velocity;
        blocked.yaw = f64::from(before.yaw);
        blocked.pitch = before.pitch;
        let displacement = SweptDisplacement::new(
            serde_json::from_value(input["wall_displacement"].clone()).unwrap(),
        )
        .unwrap();
        let sweep = ScriptedMotion::Sweep(displacement);
        assert!(blocked.apply_scripted_motion(10, sweep));
        assert!(blocked.position[2] > fixture["expected"]["wall_min_z"].as_f64().unwrap());
        assert!(blocked.position[2] < fixture["expected"]["wall_max_z"].as_f64().unwrap());
        assert_eq!(blocked.velocity, before.velocity);
        assert_eq!(
            (blocked.movement(10).yaw, blocked.pitch),
            (before.yaw, before.pitch)
        );
        assert_eq!(blocked.moved_tick, Some(10));
        let clip = blocked.last_contact_clip().unwrap();
        assert_eq!(clip.tick, 10);
        assert_eq!(clip.after, blocked.position);
        let stopped = blocked.position;
        assert!(blocked.apply_scripted_motion(11, sweep));
        let tolerance = fixture["expected"]["repeated_block_tolerance"]
            .as_f64()
            .unwrap();
        assert!((blocked.position[2] - stopped[2]).abs() <= tolerance);
        assert_eq!(blocked.velocity, before.velocity);

        let behind_wall = displacement.delta().map(f64::from);
        let relocation = ScriptedMotion::Relocate(Relocation::new(behind_wall).unwrap());
        assert!(blocked.apply_scripted_motion(12, relocation));
        assert_eq!(blocked.position, behind_wall);
        assert!(blocked.last_contact_clip().is_none());
        assert_eq!(blocked.velocity, before.velocity);
        assert_eq!(blocked.moved_tick, Some(12));
        let teleport = |to| ScriptedMotion::Teleport(Relocation::new(to).unwrap());
        assert!(blocked.apply_scripted_motion(13, teleport([0.0; 3])));
        assert_eq!(blocked.position, [0.0; 3]);

        let mut actor_blocked = walker([0.0, 0.0, 10.0]);
        let fallback = actor_blocked.contact_capsule(&settings);
        actor_blocked.attach_collision(
            &settings,
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            category,
            |_, _, _, _| Some(0.0),
        );
        assert_eq!(actor_blocked.contact_capsule(&settings), fallback);
        actor_blocked.enable_contact_trace();
        actor_blocked.velocity = before.velocity;
        let actor_feet = serde_json::from_value(input["actor_feet"].clone()).unwrap();
        let actor = standing_capsule(&settings, actor_feet);
        actor_blocked.set_movement_blockers(20, vec![actor]);
        assert!(actor_blocked.apply_scripted_motion(20, sweep));
        assert!(actor_blocked.position[2] > fixture["expected"]["wall_min_z"].as_f64().unwrap());
        assert!(actor_blocked.position[2] < fixture["expected"]["wall_max_z"].as_f64().unwrap());
        assert_eq!(actor_blocked.velocity, before.velocity);
        assert_eq!(actor_blocked.moved_tick, Some(20));
        assert!(actor_blocked.last_contact_clip().is_none());
        assert_eq!(
            actor_blocked.contact_capsule(&settings),
            standing_capsule(&settings, actor_blocked.position)
        );
        let before_relocation = actor_blocked.position;
        let occupied = ScriptedMotion::Relocate(Relocation::new(actor_feet).unwrap());
        assert!(!actor_blocked.apply_scripted_motion(20, occupied));
        assert_eq!(actor_blocked.position, before_relocation);

        actor_blocked.set_movement_blockers(21, vec![actor]);
        let capsule = actor_blocked
            .collision
            .as_ref()
            .unwrap()
            .body
            .capsule_pose();
        assert!(!actor_blocked.apply_scripted_motion(21, teleport(actor_feet)));
        assert_eq!(actor_blocked.position, before_relocation);
        assert_eq!(
            actor_blocked
                .collision
                .as_ref()
                .unwrap()
                .body
                .capsule_pose(),
            capsule
        );
        assert_eq!(actor_blocked.moved_tick, Some(20));
        assert_eq!(actor_blocked.velocity, before.velocity);
        assert!(actor_blocked.apply_scripted_motion(21, teleport(behind_wall)));
        assert_eq!(actor_blocked.position, behind_wall);
        assert_eq!(actor_blocked.moved_tick, Some(21));
    }

    #[test]
    fn patrol_catch_up_and_objective_movement_share_tick_scoped_actor_contacts() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/native-regular-actor-contact-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["evidence"], "synthetic-native");
        let input = &fixture["input"];
        let settings = MoverSettings {
            stand_height: input["stand_height"].as_f64().unwrap(),
            stand_radius: input["stand_radius"].as_f64().unwrap(),
            ..MoverSettings::default()
        };
        let actor = standing_capsule(
            &settings,
            serde_json::from_value(input["actor_feet"].clone()).unwrap(),
        );
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let patrol = PatrolScript::load().unwrap();
        let mut blocked = walker([0.0, 0.0, 10.0]);
        let mut free = walker([0.0, 0.0, 10.0]);
        for bot in [&mut blocked, &mut free] {
            bot.attach_collision(&settings, Arc::clone(&world), 0x18, |_, _, _, _| Some(0.0));
            bot.keys = true;
            bot.target = Some([0.0, 0.0, 10.0]);
            bot.pace = bot.walk.speed;
            bot.last_tick = Some(0);
        }
        blocked.set_movement_blockers(100, vec![actor]);
        assert!(blocked.has_movement_blockers(100));
        assert_eq!(blocked.movement_blockers(100), &[actor]);
        assert!(!blocked.has_movement_blockers(99));
        assert!(blocked.movement_blockers(99).is_empty());
        blocked.step(100, &patrol, |_, _, _, _| Some(0.0)).unwrap();
        free.step(100, &patrol, |_, _, _, _| Some(0.0)).unwrap();
        let maximum_z = fixture["expected"]["maximum_z"].as_f64().unwrap();
        assert!(blocked.position[2] > fixture["expected"]["minimum_z"].as_f64().unwrap());
        assert!(blocked.position[2] < maximum_z);
        assert!(free.position[2] > maximum_z);
        let fresh = blocked.respawned(BOT_ENTITY + 1, 101);
        assert!(!fresh.has_movement_blockers(100));
        assert!(fresh.movement_blockers(100).is_empty());

        blocked.set_movement_blockers(101, vec![actor]);
        blocked.move_to_objective(101, [0.0, 0.0, 10.0], 1000.0);
        assert!(blocked.position[2] < maximum_z);
        assert!(blocked.movement_blockers(102).is_empty());
        blocked.move_to_objective(102, [0.0, 0.0, 10.0], 1000.0);
        assert!(blocked.position[2] > maximum_z);
        blocked.set_movement_blockers(103, vec![actor]);
        blocked.died(103);
        assert!(!blocked.has_movement_blockers(103));
        assert!(blocked.movement_blockers(103).is_empty());
    }

    #[test]
    fn passive_ally_separation_respects_current_hostile_blockers() {
        #[derive(Deserialize)]
        struct Case {
            radius: f64,
            blocker_x: f64,
            separation_x: f64,
            ticks: u32,
        }
        let case: Case = serde_json::from_str(include_str!(
            "../../fixtures/behavior/npc-team-separation-v1.json"
        ))
        .unwrap();
        let settings = MoverSettings {
            stand_radius: case.radius,
            ..MoverSettings::default()
        };
        let blocker = standing_capsule(&settings, [case.blocker_x, 0.0, 0.0]);
        let patrol = PatrolScript::load().unwrap();
        let idle = || {
            let mut bot = walker([0.0; 3]);
            Arc::get_mut(&mut bot.spawn).unwrap().route.clear();
            bot
        };
        let mut bound = idle();
        let mut unbound = idle();
        bound.attach_collision(
            &settings,
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            0x18,
            |_, _, _, _| Some(0.0),
        );
        let min_distance = case.radius + f64::from(blocker.radius);
        for tick in 1..=case.ticks {
            for bot in [&mut bound, &mut unbound] {
                bot.set_movement_blockers(tick, vec![blocker]);
                bot.set_ally_separation([case.separation_x, 0.0]);
                bot.step(tick, &patrol, |_, _, _, _| Some(0.0)).unwrap();
            }
            let gap = case.blocker_x - bound.position[0];
            assert!(
                gap >= min_distance - f64::from(crate::contact_triangle::TOUCH),
                "{gap}"
            );
        }
        assert!(bound.position[0] > 0.0);
        assert!(unbound.position[0] > case.blocker_x);

        // Last tick's obstacle no longer participates.
        let at_contact = bound.position[0];
        bound.set_ally_separation([case.separation_x, 0.0]);
        bound
            .step(case.ticks + 1, &patrol, |_, _, _, _| Some(0.0))
            .unwrap();
        assert!((bound.position[0] - at_contact - case.separation_x).abs() < 1.0e-6);
    }

    fn route_vector(bots: &mut [Bot], ticks: impl IntoIterator<Item = u32>) -> String {
        let mut digest = Sha256::new();
        let patrol = PatrolScript::load().unwrap();
        for tick in ticks {
            for bot in &mut *bots {
                bot.step(tick, &patrol, |_, _, _, _| None).unwrap();
                let bits = movement_record(&bot.movement(tick), Some(tick)).unwrap();
                digest.update(tick.to_le_bytes());
                digest.update(u32::try_from(bits.bit_len()).unwrap().to_le_bytes());
                digest.update([u8::from(bot.record_due(tick, SEND_EVERY))]);
                digest.update(bits.as_bytes());
            }
        }
        format!("{:x}", digest.finalize())
    }

    #[test]
    fn catalogs_only_unconditional_spawners_and_preserves_the_hint_sequence() {
        let catalog = BotCatalog::from_json(r#"{
            "heroes": {
                "0x02E000000000016B": {"name":"Fixture bot","body":"0x0400000000001403","health":200}
            },
            "maps": {"0x0800000000000688": {
                "hints": {"a": [1,2,3], "b": [4,5,6]},
                "spawners": [
                    {"hero":"0x02E000000000016B","team":0,"identifier":"0xF",
                     "position":[0,0,0],"rotation":[0,0,0,1],"graphs":[]},
                    {"hero":"0x02E0000000000000","team":0,
                     "position":[0,0,0],"rotation":[0,0,0,1],"graphs":[]},
                    {"hero":"0x02E000000000016B","team":1,"identifier":null,
                     "position":[2,3,4],"rotation":[0,0.70710678118,0,0.70710678118],
                     "graphs":["0x0580000000000B8E","0x0580000000000B92"],
                     "vars":{"0x0D80000000001B23":["b","missing","a","b"],"0x0D80000000000027":2}},
                    {"hero":"0x02E000000000016B","team":0,
                     "position":[0,0,0],"rotation":[0,0,0,1],"graphs":[],
                     "vars":{"0x0D80000000001B23":["a"]}}
                ]
            }}
        }"#).unwrap();
        let bots = catalog.make(PRACTICE_RANGE_MAP, |body| {
            assert_eq!(body, TRAINING_BODY);
            BotWalk::from_mover(4.5, 1.0, 15.0)
        });
        assert_eq!(bots.len(), 2);
        assert_eq!(bots[0].entity, BOT_ENTITY);
        assert_eq!(bots[1].entity, BOT_ENTITY + 1);
        assert_eq!(bots[0].spawn.hero, TRAINING);
        assert_eq!(bots[0].spawn.name, "Fixture bot");
        assert_eq!(bots[0].spawn.team, 1);
        assert_eq!(bots[0].spawn.health, 200.0);
        assert_eq!(bots[0].position, [2.0, 3.0, 4.0]);
        assert_eq!(bots[0].yaw, 16384.0);
        assert_eq!(
            bots[0].spawn.placement_graphs,
            [PATROL, 0x0580_0000_0000_0b92]
        );
        assert_eq!(
            bots[0].spawn.route,
            [[4.0, 5.0, 6.0], [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
        );
        assert_eq!(bots[0].index, 2);
        assert!(bots[1].spawn.placement_graphs.is_empty());
        assert!(bots[1].spawn.route.is_empty());
        assert!(catalog.make(0, |_| unreachable!()).is_empty());
    }

    #[test]
    fn shared_flashbang_horizontal_motion_contract() {
        use crate::mover::VelocityChange;
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-horizontal-cap-v1.json"
        ))
        .unwrap();
        let active = fixture["active_frame"].as_u64().unwrap() as u32;
        let cap = fixture["upper"].as_f64().unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let velocity: [f64; 4] = serde_json::from_value(case["input"].clone()).unwrap();
            let expected: [f64; 4] = serde_json::from_value(case["expected"].clone()).unwrap();
            let tick = case["frame"].as_u64().unwrap() as u32;
            let target = [
                velocity[0] * 100.0,
                velocity[1] * 100.0,
                velocity[2] * 100.0,
            ];
            let speed = velocity[0].hypot(velocity[1]).hypot(velocity[2]);
            let mut bot = walker(target);
            bot.attach_collision(
                &MoverSettings::default(),
                Arc::new(ContactWorld::new(vec![]).unwrap()),
                0x08,
                |_, _, _, _| Some(0.0),
            );
            bot.queue_velocity_changes(vec![(active, VelocityChange::HorizontalCap(cap))]);
            bot.move_to_objective(tick, target, speed);
            for axis in 0..3 {
                assert!(
                    (bot.velocity[axis] - expected[axis]).abs() < 2e-5,
                    "frame{tick} velocity {:?}, expected {expected:?}",
                    bot.velocity
                );
                assert!(
                    (bot.position[axis] - expected[axis] * TICK_PERIOD.as_secs_f64()).abs() < 2e-6
                );
            }
            assert_eq!(bot.velocity_changes.len(), usize::from(tick < active));
            if tick == active {
                let before = bot.position;
                bot.move_to_objective(active + 1, target, speed);
                let step = std::array::from_fn::<_, 3, _>(|axis| bot.position[axis] - before[axis]);
                assert!(
                    (step[0].hypot(step[2])
                        - velocity[0].hypot(velocity[2]) * TICK_PERIOD.as_secs_f64())
                    .abs()
                        < 2e-5
                );
            }
        }
        let mut stopped = walker([0.0, 0.0, 10.0]);
        stopped.queue_velocity_changes(vec![(active, VelocityChange::HorizontalCap(cap))]);
        stopped.stop_objective(active);
        assert!(
            stopped.velocity_changes.is_empty(),
            "control pause expires the edit without deferred catch-up"
        );
    }

    #[test]
    fn pending_velocity_edits_cancel_on_death_and_do_not_follow_replacement() {
        use crate::mover::VelocityChange;

        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/native-regular-actor-contact-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["evidence"], "synthetic-native");
        let input = &fixture["input"];
        let before_due: u32 = input["warmup_frame"].as_u64().unwrap().try_into().unwrap();
        let due: u32 = input["final_frame"].as_u64().unwrap().try_into().unwrap();
        assert!(before_due < due);
        let velocity: [f64; 3] = serde_json::from_value(input["velocity"].clone()).unwrap();
        let change = (due, VelocityChange::Delta(velocity));
        let mut bot = walker([0.0, 0.0, 10.0]);
        bot.velocity = velocity;
        bot.queue_velocity_changes(vec![change]);
        let resolved = bot.movement(before_due);
        bot.clear_velocity_changes();
        assert!(bot.velocity_changes.is_empty());
        assert_eq!(bot.movement(before_due), resolved);

        bot.queue_velocity_changes(vec![change]);
        let fresh = bot.respawned(BOT_ENTITY + 1, due);
        assert!(fresh.velocity_changes.is_empty());
        assert_eq!(bot.velocity_changes, vec![change]);
        let position = bot.position;
        bot.died(before_due);
        assert!(bot.velocity_changes.is_empty());
        assert_eq!(bot.position, position);
        assert_eq!(bot.velocity, [0.0; 3]);
        assert!(bot.dead);
    }

    #[test]
    fn missing_spawner_file_leaves_an_empty_catalog() {
        let root = tempfile::tempdir().unwrap();
        let catalog = BotCatalog::load(root.path().join("ai_spawners_174.json")).unwrap();
        assert!(
            catalog
                .make(PRACTICE_RANGE_MAP, |_| unreachable!())
                .is_empty()
        );
    }

    #[test]
    fn backward_walk_brake_and_next_leg_match_python_wire_vectors() {
        // Python tests/test_bots.py::behind(), captured at every 16 ms tick.
        let mut bot = walker([0.0, 0.0, -20.0]);
        assert_eq!(
            route_vector(std::slice::from_mut(&mut bot), 1..=750),
            "80053f3ad91f5ca2e6e9fb638a2cb5fd1ac83851602e4079a6385c9ce39a7ee7"
        );
    }

    #[test]
    fn stun_retains_the_route_extends_and_recovers_without_paused_time_catchup() {
        let patrol = PatrolScript::load().unwrap();
        let run = |contiguous: bool| {
            let mut bot = walker([0.0, 0.0, 30.0]);
            // Begin on the active patrol leg, after the authored spawn wait.
            bot.last_tick = Some(0);
            for tick in 1..=100 {
                bot.step(tick, &patrol, |_, _, _, _| None).unwrap();
            }
            let position = bot.position;
            let target = bot.target;
            bot.stun_for(100, 50);
            bot.stun_for(110, 50);
            bot.stun_for(120, 1); // shorter hits do not shorten the active effect
            assert!(bot.is_stunned(159));
            assert!(!bot.is_stunned(160));
            for tick in 101..160 {
                if contiguous {
                    bot.step(tick, &patrol, |_, _, _, _| None).unwrap();
                }
                assert_eq!(bot.position, position);
                assert_eq!(bot.target, target);
                assert_eq!(bot.velocity, [0.0; 3]);
            }
            bot.step(160, &patrol, |_, _, _, _| None).unwrap();
            assert_ne!(bot.position, position);
            assert!(norm_delta(bot.position, position) < 0.2);
            bot
        };
        fn norm_delta(a: [f64; 3], b: [f64; 3]) -> f64 {
            (a[0] - b[0]).hypot(a[1] - b[1]).hypot(a[2] - b[2])
        }
        let a = run(true);
        let b = run(false);
        assert_eq!(a.position, b.position);
        assert_eq!(a.velocity, b.velocity);
        let fresh = a.respawned(a.entity + 1, 200);
        assert!(!fresh.is_stunned(100));
        let mut dead = b;
        dead.died(161);
        dead.stun_for(162, 100);
        assert!(!dead.is_stunned(163));
    }

    #[test]
    fn skipped_ticks_catch_up_without_replaying_a_duplicate_tick() {
        let patrol = PatrolScript::load().unwrap();
        let mut contiguous = walker([0.0, 0.0, 10.0]);
        for tick in 1..=251 {
            contiguous.step(tick, &patrol, |_, _, _, _| None).unwrap();
        }
        let mut skipped = walker([0.0, 0.0, 10.0]);
        for tick in [1, 201, 251, 251, 100] {
            skipped.step(tick, &patrol, |_, _, _, _| None).unwrap();
        }
        assert_eq!(contiguous.movement(251), skipped.movement(251));

        let mut late = walker([0.0, 0.0, 10.0]);
        let mut bounded = walker([0.0, 0.0, 10.0]);
        late.step(1, &patrol, |_, _, _, _| None).unwrap();
        late.step(10_000, &patrol, |_, _, _, _| None).unwrap();
        bounded
            .step(10_000 - CATCH_UP, &patrol, |_, _, _, _| None)
            .unwrap();
        bounded.step(10_000, &patrol, |_, _, _, _| None).unwrap();
        assert_eq!(late.movement(10_000), bounded.movement(10_000));
    }

    #[test]
    fn terrain_probe_uses_ramp_speed_and_falls_back_to_hint_height() {
        let patrol = PatrolScript::load().unwrap();
        let mut ramp = walker([0.0, 10.0, 20.0]);
        let mut fallback = walker([0.0, 10.0, 20.0]);
        for tick in 1..=344 {
            ramp.step(tick, &patrol, |_, z, top, bottom| {
                let height = 0.5 * z;
                (bottom..=top).contains(&height).then_some(height)
            })
            .unwrap();
            fallback.step(tick, &patrol, |_, _, _, _| None).unwrap();
        }
        let [_, y, z] = ramp.position;
        assert!((y - 0.5 * z).abs() < 1.0e-12);
        let [_, vy, vz] = ramp.velocity;
        assert!((vy.hypot(vz) - 4.5).abs() < 1.0e-8);
        assert!((vz - 4.5 / 1.0_f64.hypot(0.5)).abs() < 1.0e-8);
        assert!((fallback.position[1] - 0.5 * fallback.position[2]).abs() < 1.0e-12);
    }

    #[test]
    fn patrol_uses_the_shared_capsule_and_team_mask() {
        use crate::{
            collision_cache::{Mesh, Triangle},
            contact_mesh::ContactMesh,
            contact_world::{StaticGeometry, StaticShape},
        };

        let wall = Mesh {
            vertices: vec![
                [-10.0, 0.0, 2.0],
                [-10.0, 4.0, 2.0],
                [10.0, 0.0, 2.0],
                [10.0, 4.0, 2.0],
            ],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1, 3, -1],
                    mask: 0x08,
                    surface: 0,
                },
                Triangle {
                    corners: [3, 2, 1],
                    wings: [-1, 0, -1],
                    mask: 0x08,
                    surface: 0,
                },
            ],
        };
        let mesh = ContactMesh::new(wall, [1.0; 3], u16::MAX).unwrap();
        let wall = StaticShape::new(StaticGeometry::Mesh(Arc::new(mesh)), None).unwrap();
        let world = Arc::new(ContactWorld::new(vec![wall]).unwrap());
        let settings = MoverSettings::default();
        let patrol = PatrolScript::load().unwrap();
        let mut blocked = walker([0.0, 0.0, 4.0]);
        let mut traced = walker([0.0, 0.0, 4.0]);
        let mut allowed = walker([0.0, 0.0, 4.0]);
        blocked.attach_collision(&settings, Arc::clone(&world), 0x08, |_, _, _, _| Some(0.0));
        traced.attach_collision(&settings, Arc::clone(&world), 0x08, |_, _, _, _| Some(0.0));
        allowed.attach_collision(&settings, world, 0x10, |_, _, _, _| Some(0.0));
        traced.enable_contact_trace();
        allowed.enable_contact_trace();
        let mut observed_block = false;
        let mut observed_normal = false;
        for tick in 1..=295 {
            let before = traced.position;
            for bot in [&mut blocked, &mut traced, &mut allowed] {
                bot.step(tick, &patrol, |_, _, _, _| Some(0.0)).unwrap();
            }
            assert_eq!(traced.movement(tick), blocked.movement(tick));
            if let Some(clip) = traced.last_contact_clip().filter(|clip| clip.tick == tick) {
                assert_eq!(clip.proxy_id, 0);
                assert_eq!(clip.category, 0x08);
                assert!((0.0..1.0).contains(&clip.fraction));
                assert_eq!(clip.before, before);
                assert_eq!(clip.after, traced.position);
                observed_block |= clip.after[2] < clip.intended[2];
                let normals = traced.debug_contact_normals();
                observed_normal |= normals
                    .iter()
                    .any(|normal| normal.entity == traced.entity && normal.normal[2] < -0.5);
            }
        }
        assert!(observed_block, "no clipped approach to the synthetic wall");
        assert!(observed_normal, "expected traced patrol contact normal");
        assert!(traced.debug_route().is_some());
        assert!(blocked.last_contact_clip().is_none());
        assert!(blocked.debug_contact_normals().is_empty());
        assert!(allowed.last_contact_clip().is_none());
        assert!(
            traced
                .respawned(BOT_ENTITY + 1, 296)
                .last_contact_clip()
                .is_none()
        );
        assert!(blocked.position[2] < 2.0);
        assert!(allowed.position[2] > 3.0);
        assert!(blocked.velocity[2].abs() < 0.05);
    }

    #[test]
    fn patrol_and_scripted_return_share_navigation_around_wide_wall() {
        use crate::collision_cache::{CollisionCache, Mesh, Triangle};
        use serde_json::json;
        use std::collections::BTreeSet;

        // Floor [-6..6, -1..9] and a 3m-wide wall [-1.5..1.5] at z=4 (wider than 2*radius = 1m).
        let floor = Mesh {
            vertices: vec![
                [-6.0, 0.0, -1.0],
                [-6.0, 0.0, 9.0],
                [6.0, 0.0, 9.0],
                [6.0, 0.0, -1.0],
            ],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                },
                Triangle {
                    corners: [2, 3, 0],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                },
            ],
        };
        let wall = Mesh {
            vertices: vec![
                [-1.5, 0.0, 4.0],
                [-1.5, 3.0, 4.0],
                [1.5, 3.0, 4.0],
                [1.5, 0.0, 4.0],
            ],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                },
                Triangle {
                    corners: [2, 3, 0],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                },
            ],
        };
        let cache = CollisionCache {
            header: serde_json::from_value(json!({
                "meshes": [[4, 2], [4, 2]],
                "hulls": 0,
                "models": 0,
                "placements": 0,
                "identifiers": [],
            }))
            .unwrap(),
            meshes: vec![floor, wall],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        let ids = BTreeSet::new();
        let scene = CollisionScene::movement_probe(&cache, &ids).unwrap();
        let world = Arc::new(ContactWorld::from_movement_cache(cache, &ids).unwrap());
        let settings = MoverSettings::default();
        let mut bot = walker([0.0, 0.0, 8.0]);
        bot.attach_scene_collision(
            &settings,
            Some(&scene),
            Arc::clone(&world),
            0x18,
            |_, _, _, _| Some(0.0),
        );
        let plans = bot.plans.as_ref().expect("planned patrol legs");
        assert!(
            plans.first[0].is_some(),
            "shared navigation should compute a detour around the 3m-wide wall: {:?}",
            plans.first
        );

        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/ai-navigation-destination-v1.json"
        ))
        .unwrap();
        let input = &fixture["wall_return"];
        let start = serde_json::from_value(input["displaced_position"].clone()).unwrap();
        let destination = serde_json::from_value(input["destination"].clone()).unwrap();
        let speed = input["speed"].as_f64().unwrap();
        let wall = input["wall_z"].as_f64().unwrap();
        let half_width = input["wall_half_width"].as_f64().unwrap();
        let mut returning = walker(destination);
        Arc::get_mut(&mut returning.spawn).unwrap().route.clear();
        returning.position = start;
        let movers = crate::mover_settings::MoverCatalog::default();
        let queries = crate::scene_assets::BotQueries::new(false, &movers, &scene, Some(&world));
        let mut crossed = false;
        let tick_budget = input["tick_budget"].as_u64().unwrap() as u32;
        for tick in 1..=tick_budget {
            let prior = returning.position;
            queries.step_ai_walk(&mut returning, tick, destination, speed);
            if prior[2] >= wall && returning.position[2] < wall {
                assert!(returning.position[0].abs() > half_width);
                assert_ne!(returning.movement(tick).throttles, [0; 2]);
                crossed = true;
            }
        }
        assert!(crossed, "scripted walk stopped at {:?}", returning.position);
        assert!(planar_distance(returning.position, destination) <= ARRIVED);
        returning.end_walk_request(tick_budget + 1);
        assert!(returning.walk_route.is_none());
        assert_eq!(returning.movement(tick_budget + 1).throttles, [0; 2]);

        let mut patrol_bot = walker(start);
        patrol_bot.position = start;
        patrol_bot.target = Some(destination);
        patrol_bot.keys = true;
        let mut ground = |_, _, top, bottom| (bottom <= 0.0 && 0.0 <= top).then_some(0.0);
        patrol_bot.attach_collision(&settings, Arc::clone(&world), 0x18, ground);
        let impulse = serde_json::from_value(input["patrol_impulse"].clone()).unwrap();
        patrol_bot.queue_velocity_changes(vec![(1, VelocityChange::Impulse(impulse))]);
        let patrol = PatrolScript::load().unwrap();
        let mut crossed = false;
        for tick in 1..=tick_budget {
            if patrol_bot.step_combat_motion(tick, &settings, &mut ground) {
                continue;
            }
            let prior = patrol_bot.position;
            queries.rejoin_patrol(&mut patrol_bot, tick, 0x18);
            patrol_bot.step(tick, &patrol, ground).unwrap();
            if prior[2] >= wall && patrol_bot.position[2] < wall {
                assert!(patrol_bot.position[0].abs() > half_width);
                assert_ne!(patrol_bot.movement(tick).throttles, [0; 2]);
                crossed = true;
            }
            if planar_distance(patrol_bot.position, destination) <= ARRIVED && !patrol_bot.keys {
                break;
            }
        }
        assert!(crossed, "patrol stopped at {:?}", patrol_bot.position);
        assert!(planar_distance(patrol_bot.position, destination) <= ARRIVED);
        assert_eq!(patrol_bot.index, 0, "the authored patrol leg is retained");
    }

    #[test]
    fn death_stops_the_bot_and_respawn_restarts_the_route_after_create() {
        let patrol = PatrolScript::load().unwrap();
        let mut bot = walker([0.0, 0.0, 10.0]);
        bot.step(1, &patrol, |_, _, _, _| None).unwrap();
        bot.step(250, &patrol, |_, _, _, _| None).unwrap();
        assert!(bot.walking());
        let death_position = bot.position;
        bot.died(250);
        bot.step(400, &patrol, |_, _, _, _| unreachable!()).unwrap();
        assert!(!bot.walking());
        assert_eq!(bot.position, death_position);
        let state = bot.movement(250);
        assert_eq!(state.flags, DEAD_MOVEMENT_FLAG);
        assert_eq!(state.velocity, [0.0; 3]);
        assert_eq!(state.throttles, [0; 2]);
        assert_eq!(state.input_frame, None);
        assert_eq!(bot.movement(251).input_frame, Some(250));
        assert!(bot.record_due(250 + SETTLE_TICKS - 1, SEND_EVERY));

        let replacement = BOT_ENTITY + 15;
        let tick = 250
            + BOT_RESPAWN_TIME
                .as_millis()
                .div_ceil(TICK_PERIOD.as_millis()) as u32;
        let records_from = tick + BOT_RECORD_DELAY;
        let mut fresh = bot.respawned(replacement, records_from);
        assert!(Arc::ptr_eq(&fresh.spawn, &bot.spawn));
        assert_eq!(fresh.entity, replacement);
        assert!(!fresh.dead);
        assert_eq!(fresh.position, [0.0; 3]);
        assert_eq!(fresh.movement(tick).input_frame, Some(NO_INPUT));
        let turn = |at: &u32| at % SETTLE_TICKS == fresh.entity % SETTLE_TICKS;
        let before = (records_from - SETTLE_TICKS..records_from)
            .find(turn)
            .unwrap();
        let after = (records_from..records_from + SETTLE_TICKS)
            .find(turn)
            .unwrap();
        assert!(!fresh.record_due(before, 1));
        assert!(fresh.record_due(after, 1));
        fresh.step(tick, &patrol, |_, _, _, _| None).unwrap();
        fresh.step(tick + 186, &patrol, |_, _, _, _| None).unwrap();
        assert!(!fresh.walking());
        fresh.step(tick + 187, &patrol, |_, _, _, _| None).unwrap();
        assert!(fresh.walking());
    }

    #[cfg(feature = "lua-debug")]
    #[test]
    fn failed_patrol_decision_leaves_the_route_unmodified_and_only_stops_that_body() {
        let patrol = PatrolScript::from_source(
            r#"
            return {
                abi = 2,
                think = function(snapshot, events)
                    local event = events[1]
                    if event.kind == "think" then
                        if event.tick == 188 then
                            return { { kind = "patrol_slot", slot = 99 } }
                        end
                        return { { kind = "patrol_slot", slot = 1 } }
                    end
                    return { { kind = "wait", seconds = 3 } }
                end,
            }
            "#,
        )
        .unwrap();
        let mut failed = walker([0.0, 0.0, 10.0]);
        let mut other = walker([0.0, 0.0, 10.0]);
        failed.step(1, &patrol, |_, _, _, _| None).unwrap();
        assert!(
            failed
                .step(188, &patrol, |_, _, _, _| unreachable!())
                .is_err()
        );
        assert_eq!(failed.index, 0);
        assert_eq!(failed.position, [0.0; 3]);
        assert!(!failed.walking());
        failed
            .step(250, &patrol, |_, _, _, _| unreachable!())
            .unwrap();

        other.step(2, &patrol, |_, _, _, _| None).unwrap();
        other.step(189, &patrol, |_, _, _, _| None).unwrap();
        assert!(other.walking());
        let mut fresh = failed.respawned(BOT_ENTITY + 15, 300);
        fresh.step(300, &patrol, |_, _, _, _| None).unwrap();
        fresh.step(487, &patrol, |_, _, _, _| None).unwrap();
        assert!(fresh.walking());
    }

    #[cfg(feature = "lua-debug")]
    #[test]
    fn failed_release_does_not_publish_an_unapplied_input() {
        let patrol = PatrolScript::from_source(
            r#"
            return {
                abi = 2,
                think = function(snapshot, events)
                    local event = events[1]
                    if event.kind == "spawn" then
                        return { { kind = "wait", seconds = 0.016 } }
                    elseif event.kind == "think" then
                        return { { kind = "patrol_slot", slot = 1 } }
                    end
                    return {}
                end,
            }
            "#,
        )
        .unwrap();
        let target = [1.2, 0.0, 0.0];
        let mut bot = walker(target);
        for tick in 1..=188 {
            let previous = (bot.position, bot.yaw, bot.input_tick, bot.last_held);
            if let Err(error) = bot.step(tick, &patrol, |_, _, _, _| None) {
                assert!(error.to_string().contains("invalid AI command"));
                assert!(planar_distance(bot.position, target) > ARRIVED);
                assert_eq!(
                    previous,
                    (bot.position, bot.yaw, bot.input_tick, bot.last_held)
                );
                assert_eq!(bot.input_tick, Some(tick - 1));
                assert_eq!(bot.held, [0; 2]);
                assert!(!bot.patrol_active());
                return;
            }
        }
        panic!("expected the release policy to reject its command");
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 AI spawner and mover tables"]
    fn fifteen_practice_bots_match_python_roster_and_patrol_frames() {
        use std::path::PathBuf;

        let root = std::env::var_os("ORPHEUS_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).to_owned());
        let data = root.join("data");
        let catalog = BotCatalog::load(data.join("ai_spawners_174.json")).unwrap();
        let raw_movers: HashMap<String, serde_json::Value> =
            serde_json::from_str(&fs::read_to_string(data.join("game_movers_174.json")).unwrap())
                .unwrap();
        let mut bots = catalog.make(PRACTICE_RANGE_MAP, |body| {
            let values = raw_movers
                .get(&format!("0x{body:016X}"))
                .or_else(|| raw_movers.get("0x04000000000003CF"));
            let number = |key, default| {
                values
                    .and_then(|values| values.get(key))
                    .and_then(serde_json::Value::as_f64)
                    .unwrap_or(default)
            };
            BotWalk::from_mover(
                number("run_forward", 5.5),
                number("accel_ground", 35.0),
                number("decel_ground_base", 15.0),
            )
        });
        assert_eq!(bots.len(), 15);
        assert_eq!(bots.iter().filter(|bot| bot.spawn.team == 1).count(), 11);
        assert_eq!(bots.iter().filter(|bot| bot.spawn.team == 0).count(), 4);
        assert_eq!(
            bots.iter()
                .filter(|bot| !bot.spawn.route.is_empty())
                .count(),
            6
        );
        assert_eq!(
            bots.iter()
                .filter(|bot| bot.spawn.hero == FAR_RUNNER)
                .count(),
            4
        );

        let mut roster = Sha256::new();
        for (index, bot) in bots.iter().enumerate() {
            let spawn = &bot.spawn;
            assert_eq!(bot.entity, BOT_ENTITY + index as u32);
            roster.update(bot.entity.to_le_bytes());
            roster.update(spawn.hero.to_le_bytes());
            roster.update(spawn.body.to_le_bytes());
            roster.update([spawn.team]);
            roster.update((spawn.health as f32).to_le_bytes());
            roster.update(spawn.yaw.to_le_bytes());
            // The pinned Python roster serialized its firing-placement marker.
            roster.update([u8::from(
                spawn.placement_graphs.contains(&0x0580_0000_0000_0b92),
            )]);
            roster.update(u32::try_from(spawn.name.len()).unwrap().to_le_bytes());
            roster.update(spawn.name.as_bytes());
            for value in bot.position.into_iter().chain([
                bot.walk.speed,
                bot.walk.acceleration,
                bot.walk.brake,
            ]) {
                roster.update((value as f32).to_le_bytes());
            }
            roster.update(i32::try_from(spawn.initial_index).unwrap().to_le_bytes());
            roster.update(u32::try_from(spawn.route.len()).unwrap().to_le_bytes());
            for &point in &spawn.route {
                for value in point {
                    roster.update((value as f32).to_le_bytes());
                }
            }
        }
        // Generated by Python bots.make and world.movement_record with no floor data.
        assert_eq!(
            format!("{:x}", roster.finalize()),
            "04745e4f99dbbfa08bdef514d77ccc0b8e023e769efa4f5fb58e6f794f50ac4b"
        );
        assert_eq!(
            route_vector(&mut bots, (1..=1300).chain([1325, 1955, 1956, 1960])),
            "b149457ffd1e86f976def1ffb6d53c7a955582cced2fbcc03fd424c1a0a893e9"
        );
    }
}
#[test]
fn idle_bot_combat_impulse_uses_capsule_and_ticks_once() {
    let mut bot = tests::walker([0.0, 0.0, 10.0]);
    Arc::get_mut(&mut bot.spawn).unwrap().route.clear();
    assert!(!bot.patrol_active());
    let settings = MoverSettings::default();
    let mut floor = |_, _, top, bottom| (bottom <= 0.0 && 0.0 <= top).then_some(0.0);
    bot.attach_collision(
        &settings,
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        0x08,
        floor,
    );
    let start = bot.position;
    bot.queue_velocity_changes(vec![(12, VelocityChange::Impulse([3.0, 7.0, 0.0]))]);
    assert!(!bot.step_combat_motion(11, &settings, &mut floor));
    assert!(bot.step_combat_motion(12, &settings, &mut floor));
    let first = bot.position;
    assert!(first[0] > start[0] && first[1] > start[1]);
    assert!(bot.step_combat_motion(12, &settings, &mut floor));
    assert_eq!(bot.position, first);
    assert!(bot.step_combat_motion(13, &settings, &mut floor));
    assert!(bot.position[1] > first[1]);
    assert!(bot.velocity_changes.is_empty());
}

#[test]
fn grounded_bot_moves_once_per_small_horizontal_impulse() {
    #[derive(Deserialize)]
    struct Contract {
        impulse: [f64; 3],
        arrival_ticks: Vec<u32>,
        max_travel_per_tick: f64,
    }
    let contract: Contract = serde_json::from_str(include_str!(
        "../../fixtures/behavior/bot-grounded-impulse-v1.json"
    ))
    .unwrap();
    let mut bot = tests::walker([0.0, 0.0, 10.0]);
    let settings = MoverSettings::default();
    let mut floor = |_, _, top, bottom| (bottom <= 0.0 && 0.0 <= top).then_some(0.0);
    bot.attach_collision(
        &settings,
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        0x08,
        floor,
    );
    for tick in contract.arrival_ticks {
        let old = bot.position;
        bot.queue_velocity_changes(vec![(tick, VelocityChange::Impulse(contract.impulse))]);
        assert!(bot.step_combat_motion(tick, &settings, &mut floor));
        let next = bot.position;
        assert!(next[0] > old[0] && next[0] - old[0] <= contract.max_travel_per_tick);
        assert_eq!(bot.velocity, [0.0; 3]);
        assert!(bot.step_combat_motion(tick, &settings, &mut floor));
        assert!(!bot.step_combat_motion(tick + 1, &settings, &mut floor));
        assert_eq!(bot.position, next);
    }
}

#[test]
fn grounded_velocity_below_movement_precision_releases_npc_steering() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../fixtures/behavior/bot-grounded-impulse-v1.json"
    ))
    .unwrap();
    let settling = &fixture["settling"];
    let residue = serde_json::from_value(settling["velocity"].clone()).unwrap();
    let tick = u32::try_from(settling["tick"].as_u64().unwrap()).unwrap();
    let settings = MoverSettings::default();
    let room = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let mut bot = tests::walker([0.0, 0.0, 10.0]);
    let mut floor = |_, _, top, bottom| (bottom <= 0.0 && 0.0 <= top).then_some(0.0);
    bot.attach_collision(&settings, Arc::clone(&room), 0x18, floor);
    bot.velocity = residue;
    bot.combat_motion_tick = Some(tick - 1);
    assert!(!bot.step_combat_motion(tick, &settings, &mut floor));
    assert!(!bot.has_combat_motion());
    assert_eq!(bot.velocity, [0.0; 3]);
    let patrol = PatrolScript::load().unwrap();
    for next in tick..tick + 200 {
        bot.step(next, &patrol, floor).unwrap();
    }
    assert!(bot.position[2] > 0.1, "{:?}", bot.position);
    assert!(bot.input_tick.is_some());

    let mut airborne = tests::walker([0.0, 0.0, 10.0]);
    airborne.attach_collision(&settings, room, 0x18, |_, _, _, _| None);
    airborne.velocity = residue;
    airborne.combat_motion_tick = Some(tick - 1);
    assert!(airborne.step_combat_motion(tick, &settings, &mut |_, _, _, _| None));
    assert!(airborne.has_combat_motion());
    assert!(airborne.velocity[1] < 0.0);
}

#[test]
fn scripted_walk_publishes_locomotion_only_for_horizontal_travel() {
    let input: serde_json::Value = serde_json::from_str(include_str!(
        "../../fixtures/behavior/ai-navigation-destination-v1.json"
    ))
    .unwrap();
    let contract = &input["observer_locomotion"];
    let target = serde_json::from_value(contract["walking_target"].clone()).unwrap();
    let throttle: [i8; 2] = serde_json::from_value(contract["walking_throttles"].clone()).unwrap();
    let yaw = contract["aim_yaw"].as_i64().unwrap() as i32;
    let speed = contract["speed"].as_f64().unwrap();
    let mut bot = tests::walker(target);
    bot.yaw = f64::from(yaw);
    bot.attach_collision(
        &MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        0x18,
        |_, _, _, _| None,
    );
    bot.walk_to(10, target, speed, false, false);
    let walking = bot.movement(10);
    assert_eq!(walking.yaw, yaw);
    assert_eq!(walking.throttles, throttle);
    assert!(walking.velocity[2] > 0.0);

    let mut vertical_target = bot.position;
    vertical_target[1] = contract["vertical_target_y"].as_f64().unwrap();
    bot.move_to_objective(11, vertical_target, speed);
    let vertical = bot.movement(11);
    assert_eq!(vertical.yaw, yaw);
    assert_eq!(vertical.throttles, [0; 2]);
    assert_eq!(vertical.input_frame, Some(10));
    assert_eq!(vertical.input_throttles, throttle);
    assert!(vertical.velocity[1] > 0.0);
    assert!(bot.record_due(11, 4));
}

#[test]
fn bot_contact_separation_does_not_become_knockback_velocity() {
    #[derive(Deserialize)]
    struct Overlap {
        blocker_offset: f32,
        impulse: [f64; 3],
        minimum_separation: f64,
        horizontal_tolerance: f64,
    }
    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../fixtures/behavior/bot-grounded-impulse-v1.json"
    ))
    .unwrap();
    let input: Overlap = serde_json::from_value(contract["overlap"].clone()).unwrap();
    let mut bot = tests::walker([0.0, 0.0, 10.0]);
    Arc::get_mut(&mut bot.spawn).unwrap().route.clear();
    let settings = MoverSettings::default();
    let mut floor = |_, _, top, bottom| (bottom <= 0.0 && 0.0 <= top).then_some(0.0);
    bot.attach_collision(
        &settings,
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        0x08,
        floor,
    );
    let mut overlapping = bot.contact_capsule(&settings);
    overlapping.position[0] += input.blocker_offset;
    let start = bot.position;
    bot.set_movement_blockers(12, vec![overlapping]);
    bot.queue_velocity_changes(vec![(12, VelocityChange::Impulse(input.impulse))]);
    assert!(bot.step_combat_motion(12, &settings, &mut floor));
    assert!(
        bot.position[0] < start[0] - input.minimum_separation,
        "{:?}",
        bot.position
    );
    assert!(
        bot.velocity[0].abs() < input.horizontal_tolerance,
        "{:?}",
        bot.velocity
    );
    let displaced_x = bot.position[0];
    assert!(bot.step_combat_motion(13, &settings, &mut floor));
    assert!(
        (bot.position[0] - displaced_x).abs() < input.horizontal_tolerance,
        "{:?}",
        bot.position
    );
}

#[test]
fn stationary_bot_returns_to_anchor_and_restores_spawn_yaw_after_impulse() {
    let mut bot = tests::walker([0.0, 0.0, 10.0]);
    Arc::get_mut(&mut bot.spawn).unwrap().route.clear();
    let settings = MoverSettings::default();
    let mut floor = |_, _, top, bottom| (bottom <= 0.0 && 0.0 <= top).then_some(0.0);
    bot.attach_collision(
        &settings,
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        0x08,
        floor,
    );
    bot.queue_velocity_changes(vec![(12, VelocityChange::Impulse([6.0, 0.0, 0.0]))]);
    for tick in 12..=40 {
        if !bot.step_combat_motion(tick, &settings, &mut floor) {
            break;
        }
    }
    assert!(bot.has_anchor_return());
    assert!(planar_distance(bot.position, bot.spawn.position) > ARRIVED);

    let patrol = PatrolScript::load().unwrap();
    let mut tick = 41;
    while bot.has_anchor_return() && tick < 200 {
        bot.rejoin_patrol(tick, |start, destination| {
            PathQuery::Found(vec![start, destination])
        });
        bot.step(tick, &patrol, &mut floor).unwrap();
        tick += 1;
    }
    assert!(!bot.has_anchor_return());
    assert!(planar_distance(bot.position, bot.spawn.position) <= ARRIVED);
    assert!((bot.yaw - f64::from(bot.spawn.yaw)).abs() <= EPSILON);
}

#[test]
fn respawned_bot_delays_hitbox_until_records_from() {
    let bot = tests::walker([0.0, 0.0, 10.0]);
    assert!(bot.spawn_hitbox_ready(0));
    let respawned = bot.respawned(BOT_ENTITY + 1, 50);
    assert!(!respawned.spawn_hitbox_ready(49));
    assert!(respawned.spawn_hitbox_ready(50));
    assert!(respawned.spawn_hitbox_ready(51));
}

#[test]
fn same_tick_helix_impulses_merge_without_stacking() {
    let mut bot = tests::walker([0.0, 0.0, 10.0]);
    let settings = MoverSettings::default();
    let mut floor = |_, _, top, bottom| (bottom <= 0.0 && 0.0 <= top).then_some(0.0);
    bot.attach_collision(
        &settings,
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        0x08,
        floor,
    );
    bot.queue_velocity_changes(vec![
        (12, VelocityChange::Impulse([4.0, 6.0, 0.0])),
        (12, VelocityChange::Impulse([4.0, 6.0, 0.0])),
        (12, VelocityChange::Impulse([4.0, 6.0, 0.0])),
    ]);
    assert!(bot.step_combat_motion(12, &settings, &mut floor));
    assert!(bot.velocity[0] <= 4.0 + 1.0e-4, "{:?}", bot.velocity);
    assert!(bot.velocity[1] <= 6.0 + 1.0e-4, "{:?}", bot.velocity);
}
