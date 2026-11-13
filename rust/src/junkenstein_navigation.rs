//! Incremental collision routing for Junkenstein's gate attackers.

use std::{
    cmp::{Ordering, Reverse},
    collections::{BinaryHeap, HashMap, VecDeque},
};

use crate::{
    collision_index::TriangleIndex,
    collision_scene::CollisionScene,
    commands::Command,
    game_bots::Bot,
    game_clock::TICK_PERIOD,
    game_hitscan::{TargetPose, aim_direction},
    junkenstein_spawn::walk_edge,
    mover::Mover,
    mover_posture::IN_AIR,
    mover_settings::MoverSettings,
    projectile_flight::{gravity_step, launch_velocity},
    static_ray::{along, capsule_entry_distance, norm},
};

pub(crate) const REAPER: u64 = 0x02e0_0000_0000_0002;
pub(crate) const WITCH: u64 = 0x02e0_0000_0000_0004;
pub(crate) const SUMMONER: u64 = 0x02e0_0000_0000_0016;
pub(crate) const ROADHOG: u64 = 0x02e0_0000_0000_0040;
pub(crate) const JUNKRAT: u64 = 0x02e0_0000_0000_0065;
pub(crate) const REAPER_BALCONY: [f64; 3] = [71.52854, 18.48874, -104.94691];
const REAPER_EXIT_YAW: i32 = 3591;
const REAPER_EXIT_TICKS: u32 = 100;

pub(crate) const ROADHOG_PRIMARY_RANGE: f64 = 8.749;
const ROADHOG_SECONDARY_MIN: f64 = 8.75;
const ROADHOG_SECONDARY_MAX: f64 = 19.75;

const REPLAN_TICKS: u32 = 30;
const ZOMNIC: u64 = 0x02e0_0000_0000_0175;
pub(crate) const SHOCK_TIRE: u64 = 0x02e0_0000_0000_0176;
pub(crate) const ZOMBARDIER: u64 = 0x02e0_0000_0000_0177;
const SEARCH_POP_BUDGET: usize = 16;
const MAX_VISITED: usize = 2048;
const GRID_LIMIT: i32 = 32;
const HORIZON: f64 = 24.0;
const ARRIVAL_MARGIN: f64 = 0.05;
const WAYPOINT_TOLERANCE: f64 = 0.02;
const DIRECTIONS: [(i32, i32); 8] = [
    (1, 0),
    (-1, 0),
    (0, 1),
    (0, -1),
    (1, 1),
    (1, -1),
    (-1, 1),
    (-1, -1),
];

type Point = [f64; 3];
type Address = (i32, i32, i32);

#[derive(PartialEq)]
struct QueueEntry {
    score: f64,
    cost: f64,
    address: Address,
}

impl Eq for QueueEntry {}

impl PartialOrd for QueueEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for QueueEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        self.score
            .total_cmp(&other.score)
            .then_with(|| self.cost.total_cmp(&other.cost))
            .then_with(|| self.address.cmp(&other.address))
    }
}

struct Visit {
    point: Point,
    cost: f64,
    parent: Option<Address>,
    visited: bool,
}

struct Search {
    start: Point,
    frontier: BinaryHeap<Reverse<QueueEntry>>,
    visits: HashMap<Address, Visit>,
    visited: usize,
}

fn distance(from: Point, to: Point) -> f64 {
    norm(std::array::from_fn(|axis| from[axis] - to[axis]))
}

pub(crate) enum ReaperExit {
    Waiting,
    Walking { start: u32, mover: Box<Mover> },
}

impl ReaperExit {
    pub(crate) fn at_spawn(bot: &Bot) -> Option<Self> {
        (bot.spawn.hero == REAPER && distance(bot.position, REAPER_BALCONY) < 0.25)
            .then_some(Self::Waiting)
    }

    /// Keep gravity active after forward input ends until the body lands.
    pub(crate) fn step(
        &mut self,
        bot: &mut Bot,
        rays: &TriangleIndex,
        tick: u32,
        can_walk: bool,
        new_mover: impl FnOnce(&Bot) -> std::io::Result<Option<Mover>>,
    ) -> std::io::Result<bool> {
        if bot.dead {
            return Ok(true);
        }
        if matches!(self, Self::Waiting) {
            if !can_walk {
                return Ok(false);
            }
            let Some(mover) = new_mover(bot)? else {
                return Ok(true);
            };
            *self = Self::Walking {
                start: tick,
                mover: Box::new(mover),
            };
        }
        let Self::Walking { start, mover } = self else {
            unreachable!();
        };
        let elapsed = tick.saturating_sub(*start);
        mover
            .step(
                rays,
                Command {
                    frame: tick,
                    yaw: REAPER_EXIT_YAW,
                    forward: if can_walk && elapsed < REAPER_EXIT_TICKS {
                        i8::MAX
                    } else {
                        0
                    },
                    ..Command::default()
                },
                TICK_PERIOD.as_secs_f64(),
                |_, _| {},
            )
            .map_err(std::io::Error::other)?;
        let pose = mover.pose();
        bot.apply_objective_pose(pose, tick);
        let landed = pose.flags & IN_AIR == 0
            && (pose.position[1] < REAPER_BALCONY[1] - 3.0 || elapsed >= REAPER_EXIT_TICKS);
        if landed {
            bot.stop_objective(tick);
        }
        Ok(landed)
    }
}

fn address(x: i32, z: i32, y: f64) -> Address {
    (x, z, (y * 8.0).round_ties_even() as i32)
}

impl Search {
    fn new(start: Point, score: f64) -> Self {
        let origin = address(0, 0, start[1]);
        Self {
            start,
            frontier: BinaryHeap::from([Reverse(QueueEntry {
                score,
                cost: 0.0,
                address: origin,
            })]),
            visits: HashMap::from([(
                origin,
                Visit {
                    point: start,
                    cost: 0.0,
                    parent: None,
                    visited: false,
                },
            )]),
            visited: 0,
        }
    }

    /// Pending searches retain the frontier. An empty result means exhaustion.
    fn advance(
        &mut self,
        collision: &CollisionScene,
        mover: &MoverSettings,
        target: Point,
        reach: f64,
        accepts: impl Fn(Point) -> bool,
    ) -> Option<VecDeque<Point>> {
        let remaining = |point| (distance(point, target) - reach).max(0.0);
        let initial_distance = remaining(self.start);
        for _ in 0..SEARCH_POP_BUDGET {
            if self.visited >= MAX_VISITED {
                return Some(VecDeque::new());
            }
            let Some(Reverse(entry)) = self.frontier.pop() else {
                return Some(VecDeque::new());
            };
            let visit = self
                .visits
                .get_mut(&entry.address)
                .expect("queued route address");
            if visit.visited || visit.cost != entry.cost {
                continue;
            }
            visit.visited = true;
            self.visited += 1;
            let point = visit.point;
            let left = remaining(point);
            if (left == 0.0 && accepts(point))
                || (initial_distance > HORIZON
                    && entry.cost >= HORIZON
                    && left < initial_distance - 1.0)
            {
                let mut route = VecDeque::new();
                let mut visit = &self.visits[&entry.address];
                while let Some(parent) = visit.parent {
                    route.push_front(visit.point);
                    visit = &self.visits[&parent];
                }
                return Some(route);
            }
            let mut extended_cardinal = false;
            for (dx, dz) in DIRECTIONS {
                let diagonal = dx != 0 && dz != 0;
                if diagonal && extended_cardinal {
                    break;
                }
                let (x, z) = (entry.address.0 + dx, entry.address.1 + dz);
                if x.abs() > GRID_LIMIT || z.abs() > GRID_LIMIT {
                    continue;
                }
                let Some(neighbor) = walk_edge(
                    collision,
                    mover,
                    point,
                    self.start[0] + f64::from(x),
                    self.start[2] + f64::from(z),
                ) else {
                    continue;
                };
                let cost = entry.cost + distance(point, neighbor);
                let next = address(x, z, neighbor[1]);
                if self
                    .visits
                    .get(&next)
                    .is_some_and(|old| old.visited || cost >= old.cost)
                {
                    continue;
                }
                self.visits.insert(
                    next,
                    Visit {
                        point: neighbor,
                        cost,
                        parent: Some(entry.address),
                        visited: false,
                    },
                );
                self.frontier.push(Reverse(QueueEntry {
                    score: cost + remaining(neighbor),
                    cost,
                    address: next,
                }));
                extended_cardinal |= !diagonal;
            }
        }
        None
    }
}

#[derive(Clone, Copy)]
pub(crate) struct BossTarget {
    pub entity: u32,
    pub secondary: bool,
}

#[derive(Clone, Copy)]
pub(crate) struct GrenadeArc {
    pub speed: f64,
    pub gravity: f32,
    pub range: f64,
}

impl GrenadeArc {
    pub(crate) fn aim(
        self,
        from: Point,
        target: TargetPose,
        clear: &impl Fn(Point, Point, f64) -> bool,
    ) -> Option<Point> {
        let origin = [
            from[0],
            from[1] + crate::game_combat::eye_height(JUNKRAT, false),
            from[2],
        ];
        let [bottom, top] = target.segment();
        let [dx, dy, dz] =
            std::array::from_fn(|axis| (bottom[axis] + top[axis]) * 0.5 - origin[axis]);
        let distance = dx.hypot(dz);
        if distance <= 1e-9 {
            return None;
        }
        let gravity = f64::from(self.gravity);
        let speed_sq = self.speed * self.speed;
        let numerator = gravity * distance * distance + 2.0 * dy * speed_sq;
        let discriminant = speed_sq * speed_sq - gravity * numerator;
        if !discriminant.is_finite() || discriminant < 0.0 {
            return None;
        }
        let tangent = numerator / (distance * (speed_sq + discriminant.sqrt()));
        let horizontal = self.speed / 1.0_f64.hypot(tangent);
        let seconds = TICK_PERIOD.as_secs_f64();
        let vertical = horizontal * tangent + gravity * seconds * 0.5;
        let units = 65536.0 / std::f64::consts::TAU;
        let direction = aim_direction(&Command {
            yaw: (dx.atan2(dz) * units).round_ties_even() as i32,
            pitch: (-vertical.atan2(horizontal) * units).round_ties_even() as i32,
            ..Command::default()
        });
        let mut velocity = launch_velocity(direction, self.speed);
        let stride = velocity[0].hypot(velocity[2]) * seconds;
        if stride <= 1e-9 {
            return None;
        }
        let mut point = origin;
        for _ in 0..((distance / stride).ceil() as usize)
            .saturating_add(1)
            .min(256)
        {
            velocity = gravity_step(velocity, self.gravity, seconds);
            let speed = norm(velocity);
            let heading = velocity.map(|axis| axis / speed);
            let length = speed * seconds;
            if let Some(contact) =
                capsule_entry_distance(point, heading, length, bottom, top, target.radius)
            {
                return clear(point, heading, contact).then_some(direction);
            }
            if !clear(point, heading, length) {
                return None;
            }
            point = along(point, heading, length);
        }
        None
    }
}

#[derive(Default)]
pub(crate) struct BossPursuit {
    goal: Option<(u32, Point)>,
    search: Option<Search>,
    path: VecDeque<Point>,
    retry_at: u32,
}

impl BossPursuit {
    pub(crate) fn stop(&mut self, bot: &mut Bot, tick: u32) {
        *self = Self::default();
        bot.stop_objective(tick);
    }

    pub(crate) fn step(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        collision: &CollisionScene,
        (mover, arc): (&MoverSettings, Option<GrenadeArc>),
        defenders: &[crate::script_enemy::EnemyTarget],
        clear: impl Fn(Point, Point, f64) -> bool,
    ) -> Option<BossTarget> {
        let firing_range = match (bot.spawn.hero, arc) {
            (REAPER, _) => 20.0,
            (ROADHOG, _) => ROADHOG_SECONDARY_MAX,
            (JUNKRAT, Some(arc)) => arc.range,
            _ => {
                self.stop(bot, tick);
                return None;
            }
        };
        let mut enemies: Vec<_> = defenders
            .iter()
            .filter(|target| target.team == 0 && target.alive)
            .map(|target| (distance(bot.position, target.pose.feet), target))
            .collect();
        enemies.sort_unstable_by(|(a, first), (b, second)| {
            a.total_cmp(b)
                .then_with(|| first.entity.cmp(&second.entity))
        });
        let visible = |from, to: Point| {
            let [x, y, z] = from;
            let eye = [x, y + 1.55, z];
            [1.55, 0.9].into_iter().find_map(|height| {
                let point = [to[0], to[1] + height, to[2]];
                let delta = std::array::from_fn(|axis| point[axis] - eye[axis]);
                let reach = norm(delta);
                (reach > 0.001)
                    .then(|| delta.map(|axis| axis / reach))
                    .filter(|&direction| clear(eye, direction, reach))
            })
        };
        let firing_solution = |from, target: TargetPose| match arc {
            Some(arc) => arc.aim(from, target, &clear),
            None => visible(from, target.feet),
        };
        for &(range, enemy) in enemies
            .iter()
            .take_while(|(range, _)| *range <= firing_range)
        {
            let secondary = bot.spawn.hero == ROADHOG && range >= ROADHOG_SECONDARY_MIN;
            if bot.spawn.hero == ROADHOG && range > ROADHOG_PRIMARY_RANGE && !secondary {
                continue;
            }
            if let Some(direction) = firing_solution(bot.position, enemy.pose) {
                self.stop(bot, tick);
                bot.apply_objective_aim(direction, tick);
                return Some(BossTarget {
                    entity: enemy.entity,
                    secondary,
                });
            }
        }
        let Some(&(range, target)) = enemies.first() else {
            self.stop(bot, tick);
            return None;
        };
        let pursuit_range = if bot.spawn.hero == ROADHOG && range <= firing_range {
            ROADHOG_PRIMARY_RANGE
        } else {
            firing_range
        };
        self.pursue(
            bot,
            tick,
            (collision, mover),
            target,
            pursuit_range,
            |from, pose| firing_solution(from, pose).is_some(),
        );
        None
    }

    pub(crate) fn pursue(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        (collision, mover): (&CollisionScene, &MoverSettings),
        target: &crate::script_enemy::EnemyTarget,
        pursuit_range: f64,
        clear: impl Fn(Point, TargetPose) -> bool,
    ) {
        if self.goal.is_none_or(|(entity, goal)| {
            entity != target.entity
                || (tick >= self.retry_at
                    && self.search.is_none()
                    && distance(goal, target.pose.feet) > 1.0)
        }) {
            *self = Self {
                goal: Some((target.entity, target.pose.feet)),
                ..Self::default()
            };
        }
        if self.path.is_empty() && tick >= self.retry_at {
            let goal = self.goal.expect("selected pursuit target").1;
            let reach = pursuit_range - ARRIVAL_MARGIN;
            let range = distance(bot.position, target.pose.feet);
            let search = self
                .search
                .get_or_insert_with(|| Search::new(bot.position, (range - reach).max(0.0)));
            if let Some(path) = search.advance(collision, mover, goal, reach, |point| {
                clear(
                    point,
                    TargetPose {
                        feet: goal,
                        ..target.pose
                    },
                )
            }) {
                self.path = path;
                self.search = None;
                self.retry_at = tick.saturating_add(REPLAN_TICKS);
            }
        }
        let Some(&waypoint) = self.path.front() else {
            bot.stop_objective(tick);
            return;
        };
        let before = distance(bot.position, waypoint);
        bot.move_to_objective(tick, waypoint, mover.run_forward);
        let after = distance(bot.position, waypoint);
        if after <= WAYPOINT_TOLERANCE {
            self.path.pop_front();
        } else if before - after < 0.001 {
            self.path.clear();
            self.retry_at = tick.saturating_add(REPLAN_TICKS);
            bot.stop_objective(tick);
        }
    }
}

pub(crate) struct GateApproach {
    gate: Point,
    reach: f64,
    search: Option<Search>,
    path: VecDeque<Point>,
    blocked_at: Option<Point>,
    failed: bool,
    arrived: bool,
}

impl GateApproach {
    pub(crate) fn new(hero: u64, gate: Point) -> Option<Self> {
        let reach = match hero {
            ZOMNIC => 3.0,
            SHOCK_TIRE => 5.0,
            _ => return None,
        };
        Some(Self {
            gate,
            reach,
            search: None,
            path: VecDeque::new(),
            blocked_at: None,
            failed: false,
            arrived: false,
        })
    }

    pub(crate) fn active(&self) -> bool {
        !self.failed
    }

    /// Returns true only on the tick the body reaches its gate threshold.
    pub(crate) fn step(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        collision: &CollisionScene,
        mover: &MoverSettings,
        speed_scale: f64,
    ) -> bool {
        if self.failed || self.arrived || bot.dead {
            return false;
        }
        if speed_scale <= 0.0 {
            bot.stop_objective(tick);
            return false;
        }
        if distance(bot.position, self.gate) < self.reach {
            self.arrived = true;
            bot.stop_objective(tick);
            return true;
        }
        if self.path.is_empty() {
            let reach = self.reach - ARRIVAL_MARGIN;
            let search = self.search.get_or_insert_with(|| {
                Search::new(
                    bot.position,
                    (distance(bot.position, self.gate) - reach).max(0.0),
                )
            });
            let Some(path) = search.advance(collision, mover, self.gate, reach, |_| true) else {
                bot.stop_objective(tick);
                return false;
            };
            self.search = None;
            self.path = path;
        }
        let Some(&waypoint) = self.path.front() else {
            self.failed = true;
            bot.stop_objective(tick);
            return false;
        };
        let previous = distance(bot.position, waypoint);
        bot.move_to_objective(tick, waypoint, mover.run_forward * speed_scale);
        let remaining = distance(bot.position, waypoint);
        if remaining <= WAYPOINT_TOLERANCE {
            self.path.pop_front();
        } else if previous - remaining < 0.001 {
            self.path.clear();
            self.failed = self
                .blocked_at
                .is_some_and(|p| distance(p, bot.position) < 0.25);
            self.blocked_at = Some(bot.position);
            bot.stop_objective(tick);
        }
        false
    }
}
