//! Shared, map-agnostic static navigation queries over `CollisionScene` and `ContactWorld`.
//!
//! Provides walkable-ground classification, upright character capsule clearance,
//! incremental edge walking, bounded A* pathfinding with line-of-sight smoothing,
//! and radial/authored spawn validation with explicit `NoPath`, `BudgetExhausted`,
//! `NoSurface`, and `Unknown` outcomes.

use std::{
    cmp::{Ordering, Reverse},
    collections::{BinaryHeap, HashMap},
};

use crate::{
    collision_index::GroundHit,
    collision_scene::CollisionScene,
    contact_gjk::sweep::MeshProbe,
    contact_world::ContactWorld,
    static_ray::{Vec3, norm},
};

/// Surface bit marking non-walkable geometry (`NOT_GROUND` in retail mover probes).
pub const NOT_GROUND_SURFACE: u16 = 1;

/// Default collision category mask for character movement (`0x08 | 0x10`).
pub const DEFAULT_NAV_CATEGORY: u16 = 0x18;

const EDGE_STEP: f64 = 0.25;
const STEP_VERTICAL_SLACK: f64 = 0.05;
const HEADROOM_FLOOR_EPSILON: f64 = 0.01;
const MIN_CAPSULE_RADIUS: f32 = 0.02;

/// Map-agnostic character navigation parameters derived from a body's mover settings.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NavAgent {
    pub stand_radius: f64,
    pub stand_height: f64,
    pub stand_pogo: f64,
    pub step_up: f64,
    pub step_down: f64,
    pub max_slope_deg: f64,
    pub category: u16,
}

impl Default for NavAgent {
    fn default() -> Self {
        Self {
            stand_radius: 0.5,
            stand_height: 2.0,
            stand_pogo: 0.5,
            step_up: 0.6,
            step_down: 0.5,
            max_slope_deg: 45.0,
            category: DEFAULT_NAV_CATEGORY,
        }
    }
}

impl NavAgent {
    pub fn is_valid(&self) -> bool {
        self.stand_radius.is_finite()
            && self.stand_height.is_finite()
            && self.stand_pogo.is_finite()
            && self.step_up.is_finite()
            && self.step_down.is_finite()
            && self.max_slope_deg.is_finite()
            && self.stand_radius > 0.0
            && self.stand_height > self.stand_radius * 2.0
            && self.stand_pogo >= 0.0
            && self.step_up >= 0.0
            && self.step_down >= 0.0
            && (0.0..90.0).contains(&self.max_slope_deg)
            && self.category != 0
    }

    pub fn slope_cos(&self) -> f64 {
        self.max_slope_deg.to_radians().cos()
    }

    fn capsule_dimensions(&self) -> (f32, f32, f32) {
        let radius = self.stand_radius as f32;
        let height = self.stand_height as f32;
        let lift = self.stand_pogo as f32;
        let r = radius.max(MIN_CAPSULE_RADIUS);
        let rest = height - (lift + (radius + radius));
        let segment = (r * 0.1).max(rest);
        (r, segment + r, lift)
    }

    fn standing_origin(&self, [x, y, z]: Vec3, lift: f32) -> [f32; 3] {
        [x as f32, (y + f64::from(lift)) as f32, z as f32]
    }
}

/// Result of probing walkable ground at `(x, z)` within a vertical `[bottom, top]` window.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum GroundQuery {
    /// A walkable surface within the slope limit and with standing capsule headroom.
    Walkable(GroundHit),
    /// A surface exists in `[bottom, top]`, but is flagged `NOT_GROUND`, exceeds
    /// `max_slope_deg`, or lacks vertical headroom for the agent's standing capsule.
    Unwalkable(GroundHit),
    /// The query is inside the loaded scene's XZ extent, but no surface exists in `[bottom, top]`.
    NoSurface,
    /// The scene is empty, parameters/coordinates are invalid, or `(x, z)` lies outside the scene.
    Unknown,
}

impl GroundQuery {
    pub fn walkable_hit(self) -> Option<GroundHit> {
        match self {
            Self::Walkable(hit) => Some(hit),
            _ => None,
        }
    }
}

/// Result of checking upright capsule clearance at a point or along a segment.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ClearanceQuery {
    Clear,
    Blocked,
    Unknown,
}

/// Result of stepping an agent capsule across terrain from `start` toward `(target_x, target_z)`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum EdgeQuery {
    Walkable(Vec3),
    Blocked,
    Unknown,
}

impl EdgeQuery {
    pub fn landed(self) -> Option<Vec3> {
        match self {
            Self::Walkable(point) => Some(point),
            _ => None,
        }
    }
}

/// Bounded search parameters for map-agnostic A* pathfinding.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PathOptions {
    pub grid_step: f64,
    pub goal_tolerance: f64,
    pub max_visited: usize,
    pub max_grid_radius: i32,
}

impl Default for PathOptions {
    fn default() -> Self {
        Self {
            grid_step: 1.0,
            goal_tolerance: 0.75,
            max_visited: 2048,
            max_grid_radius: 64,
        }
    }
}

impl PathOptions {
    pub fn is_valid(&self) -> bool {
        self.grid_step.is_finite()
            && self.grid_step >= 0.25
            && self.goal_tolerance.is_finite()
            && self.goal_tolerance >= 0.05
            && self.max_visited > 0
            && self.max_grid_radius > 0
    }
}

/// Result of a bounded static navigation path query.
#[derive(Clone, Debug, PartialEq)]
pub enum PathQuery {
    /// Ordered waypoints `[start, ..., goal]` where every consecutive pair is walkable and clear.
    Found(Vec<Vec3>),
    /// Start or goal is unwalkable, or the reachable connected component was exhausted.
    NoPath,
    /// Search reached `max_visited` nodes while open frontier candidates remained.
    BudgetExhausted,
    /// Empty collision scene, invalid agent/options, or coordinates outside the scene bounds.
    Unknown,
}

/// Parameters for radial cone spawn candidate sampling.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpawnSearchRequest {
    pub origin: Vec3,
    pub direction: Vec3,
    pub radius: f64,
    pub half_angle_rad: f64,
    pub minimum: usize,
    pub maximum: usize,
}

/// Result of validating radial or authored spawn candidates.
#[derive(Clone, Debug, PartialEq)]
pub enum SpawnQuery {
    Found(Vec<Vec3>),
    NoValidSpawns { candidates: Vec<Vec3> },
    Unknown,
}

fn within_scene_xz(scene: &CollisionScene, agent: &NavAgent, x: f64, z: f64) -> bool {
    if !scene.triangles().in_grid_range(x, z) {
        return false;
    }
    let Some((min, max)) = scene.triangles().scene_bounds() else {
        return false;
    };
    let margin = agent.stand_radius;
    x >= min[0] - margin && x <= max[0] + margin && z >= min[2] - margin && z <= max[2] + margin
}

fn has_vertical_headroom(
    scene: &CollisionScene,
    agent: &NavAgent,
    [x, y, z]: Vec3,
) -> ClearanceQuery {
    let radius = agent.stand_radius;
    let top = agent.stand_height - radius;
    if top <= radius {
        return ClearanceQuery::Clear;
    }
    match scene.cast_ray(
        [x, y + radius, z],
        [0.0, 1.0, 0.0],
        top - radius,
        agent.category,
    ) {
        Ok(None) => ClearanceQuery::Clear,
        Ok(Some(_)) => ClearanceQuery::Blocked,
        Err(_) => ClearanceQuery::Unknown,
    }
}

/// Query the highest surface at `(x, z)` within `[bottom, top]` and classify its walkability.
pub fn walkable_ground(
    scene: &CollisionScene,
    agent: &NavAgent,
    x: f64,
    z: f64,
    top: f64,
    bottom: f64,
) -> GroundQuery {
    if !agent.is_valid()
        || !x.is_finite()
        || !z.is_finite()
        || !top.is_finite()
        || !bottom.is_finite()
        || bottom > top
        || !within_scene_xz(scene, agent, x, z)
    {
        return GroundQuery::Unknown;
    }
    let Some(hit) = scene.triangles().ground(x, z, top, bottom, agent.category) else {
        return GroundQuery::NoSurface;
    };
    if hit.surface & NOT_GROUND_SURFACE != 0 || hit.normal[1] < agent.slope_cos() {
        return GroundQuery::Unwalkable(hit);
    }
    match has_vertical_headroom(scene, agent, [x, hit.y, z]) {
        ClearanceQuery::Clear => GroundQuery::Walkable(hit),
        ClearanceQuery::Blocked => GroundQuery::Unwalkable(hit),
        ClearanceQuery::Unknown => GroundQuery::Unknown,
    }
}

/// Check upright character capsule clearance between `from` and `to` against
/// `CollisionScene` ray geometry and (when provided) `ContactWorld` static shapes.
pub fn capsule_clearance(
    scene: &CollisionScene,
    contacts: Option<&ContactWorld>,
    agent: &NavAgent,
    from: Vec3,
    to: Vec3,
) -> ClearanceQuery {
    if !agent.is_valid()
        || !from.into_iter().chain(to).all(f64::is_finite)
        || !within_scene_xz(scene, agent, from[0], from[2])
        || !within_scene_xz(scene, agent, to[0], to[2])
    {
        return ClearanceQuery::Unknown;
    }
    for point in [from, to] {
        match has_vertical_headroom(scene, agent, point) {
            ClearanceQuery::Clear => {}
            other => return other,
        }
    }
    let radius = agent.stand_radius;
    let top = agent.stand_height - radius;
    let dx = to[0] - from[0];
    let dz = to[2] - from[2];
    let length = dx.hypot(dz);
    if length > 0.0 {
        let direction = [dx / length, 0.0, dz / length];
        let side = [-direction[2] * radius, direction[0] * radius];
        let base_y = from[1].max(to[1]);
        for offset in [-1.0, 0.0, 1.0] {
            for height in [radius + HEADROOM_FLOOR_EPSILON, top] {
                let origin = [
                    from[0] + side[0] * offset,
                    base_y + height,
                    from[2] + side[1] * offset,
                ];
                match scene.cast_ray(origin, direction, length, agent.category) {
                    Ok(None) => {}
                    Ok(Some(_)) => return ClearanceQuery::Blocked,
                    Err(_) => return ClearanceQuery::Unknown,
                }
            }
        }
    }
    if let Some(world) = contacts {
        let (cap_radius, cap_top, lift) = agent.capsule_dimensions();
        let start = agent.standing_origin(from, lift);
        let end = agent.standing_origin(to, lift);
        match world.scene_sweep(MeshProbe {
            points: &[[0.0, cap_radius, 0.0], [0.0, cap_top, 0.0]],
            radius: cap_radius,
            start,
            end,
            category: agent.category,
        }) {
            Ok(None) => {}
            Ok(Some(_)) => return ClearanceQuery::Blocked,
            Err(_) => return ClearanceQuery::Unknown,
        }
    }
    ClearanceQuery::Clear
}

/// Step a character capsule from `start` to `(target_x, target_z)` in `0.25m` increments,
/// requiring continuous walkable floor support and capsule clearance at every step.
pub fn walk_edge(
    scene: &CollisionScene,
    contacts: Option<&ContactWorld>,
    agent: &NavAgent,
    start: Vec3,
    target_x: f64,
    target_z: f64,
) -> EdgeQuery {
    if !agent.is_valid()
        || !start.into_iter().all(f64::is_finite)
        || !target_x.is_finite()
        || !target_z.is_finite()
        || !within_scene_xz(scene, agent, start[0], start[2])
    {
        return EdgeQuery::Unknown;
    }
    let distance = (target_x - start[0]).hypot(target_z - start[2]);
    let count = (distance / EDGE_STEP).ceil().max(1.0) as usize;
    let mut previous = start;
    for step in 1..=count {
        let fraction = step as f64 / count as f64;
        let x = start[0] + (target_x - start[0]) * fraction;
        let z = start[2] + (target_z - start[2]) * fraction;
        let y = match walkable_ground(
            scene,
            agent,
            x,
            z,
            previous[1] + agent.step_up + STEP_VERTICAL_SLACK,
            previous[1] - agent.step_down - STEP_VERTICAL_SLACK,
        ) {
            GroundQuery::Walkable(hit) => hit.y,
            GroundQuery::Unwalkable(_) | GroundQuery::NoSurface | GroundQuery::Unknown => {
                return EdgeQuery::Blocked;
            }
        };
        let next = [x, y, z];
        match capsule_clearance(scene, contacts, agent, previous, next) {
            ClearanceQuery::Clear => previous = next,
            ClearanceQuery::Blocked => return EdgeQuery::Blocked,
            ClearanceQuery::Unknown => return EdgeQuery::Unknown,
        }
    }
    EdgeQuery::Walkable(previous)
}

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
    point: Vec3,
    cost: f64,
    parent: Option<Address>,
    visited: bool,
}

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

fn address(x: i32, z: i32, y: f64) -> Address {
    (x, z, (y * 4.0).round_ties_even() as i32)
}

fn distance_3d(a: Vec3, b: Vec3) -> f64 {
    norm(std::array::from_fn(|axis| a[axis] - b[axis]))
}

fn planar_distance(a: Vec3, b: Vec3) -> f64 {
    (a[0] - b[0]).hypot(a[2] - b[2])
}

fn snap_endpoint(scene: &CollisionScene, agent: &NavAgent, point: Vec3) -> GroundQuery {
    walkable_ground(
        scene,
        agent,
        point[0],
        point[2],
        point[1] + agent.step_up + 0.25,
        point[1] - agent.step_down - 1.0,
    )
}

fn smooth_path(
    scene: &CollisionScene,
    contacts: Option<&ContactWorld>,
    agent: &NavAgent,
    raw: &[Vec3],
) -> Vec<Vec3> {
    if raw.len() <= 2 {
        return raw.to_vec();
    }
    let mut out = Vec::with_capacity(raw.len());
    out.push(raw[0]);
    let mut anchor = 0;
    while anchor + 1 < raw.len() {
        let mut furthest = anchor + 1;
        for candidate in (anchor + 2..raw.len()).rev() {
            let target = raw[candidate];
            if planar_distance(raw[anchor], target) <= 32.0 * EDGE_STEP {
                if let EdgeQuery::Walkable(landed) =
                    walk_edge(scene, contacts, agent, raw[anchor], target[0], target[2])
                {
                    if (landed[1] - target[1]).abs() <= agent.step_up + STEP_VERTICAL_SLACK {
                        furthest = candidate;
                        break;
                    }
                }
            }
        }
        out.push(raw[furthest]);
        anchor = furthest;
    }
    out
}

/// Find a bounded, collision-checked static path between `start` and `goal`.
pub fn find_path(
    scene: &CollisionScene,
    contacts: Option<&ContactWorld>,
    agent: &NavAgent,
    start: Vec3,
    goal: Vec3,
    options: &PathOptions,
) -> PathQuery {
    if !agent.is_valid()
        || !options.is_valid()
        || !start.into_iter().chain(goal).all(f64::is_finite)
    {
        return PathQuery::Unknown;
    }
    let start_point = match snap_endpoint(scene, agent, start) {
        GroundQuery::Walkable(hit) => [start[0], hit.y, start[2]],
        GroundQuery::Unwalkable(_) | GroundQuery::NoSurface => return PathQuery::NoPath,
        GroundQuery::Unknown => return PathQuery::Unknown,
    };
    let goal_point = match snap_endpoint(scene, agent, goal) {
        GroundQuery::Walkable(hit) => [goal[0], hit.y, goal[2]],
        GroundQuery::Unwalkable(_) | GroundQuery::NoSurface => return PathQuery::NoPath,
        GroundQuery::Unknown => return PathQuery::Unknown,
    };
    if capsule_clearance(scene, contacts, agent, start_point, start_point) != ClearanceQuery::Clear
        || capsule_clearance(scene, contacts, agent, goal_point, goal_point)
            != ClearanceQuery::Clear
    {
        return PathQuery::NoPath;
    }
    let vertical_limit = agent.step_up + agent.step_down + 0.25;
    if planar_distance(start_point, goal_point) <= options.goal_tolerance
        && (start_point[1] - goal_point[1]).abs() <= vertical_limit
    {
        return PathQuery::Found(vec![start_point]);
    }
    if planar_distance(start_point, goal_point) <= 64.0 * EDGE_STEP {
        if let EdgeQuery::Walkable(landed) = walk_edge(
            scene,
            contacts,
            agent,
            start_point,
            goal_point[0],
            goal_point[2],
        ) {
            if (landed[1] - goal_point[1]).abs() <= vertical_limit {
                return PathQuery::Found(vec![start_point, landed]);
            }
        }
    }

    let origin = address(0, 0, start_point[1]);
    let mut frontier = BinaryHeap::from([Reverse(QueueEntry {
        score: distance_3d(start_point, goal_point),
        cost: 0.0,
        address: origin,
    })]);
    let mut visits = HashMap::from([(
        origin,
        Visit {
            point: start_point,
            cost: 0.0,
            parent: None,
            visited: false,
        },
    )]);
    let mut visited_count = 0_usize;

    while let Some(Reverse(entry)) = frontier.pop() {
        let point = {
            let visit = visits
                .get_mut(&entry.address)
                .expect("queued navigation address");
            if visit.visited || visit.cost != entry.cost {
                continue;
            }
            visit.visited = true;
            visit.point
        };
        visited_count += 1;

        let planar_left = planar_distance(point, goal_point);
        if planar_left <= options.goal_tolerance.max(options.grid_step * 1.5)
            && (point[1] - goal_point[1]).abs() <= vertical_limit * 2.0
        {
            if planar_left <= 1e-4 && (point[1] - goal_point[1]).abs() <= vertical_limit {
                let raw = reconstruct_path(&visits, entry.address, None);
                return PathQuery::Found(smooth_path(scene, contacts, agent, &raw));
            }
            if let EdgeQuery::Walkable(landed) =
                walk_edge(scene, contacts, agent, point, goal_point[0], goal_point[2])
            {
                if (landed[1] - goal_point[1]).abs() <= vertical_limit {
                    let tail = (planar_distance(point, landed) > 1e-3).then_some(landed);
                    let raw = reconstruct_path(&visits, entry.address, tail);
                    return PathQuery::Found(smooth_path(scene, contacts, agent, &raw));
                }
            }
            if planar_left <= options.goal_tolerance
                && (point[1] - goal_point[1]).abs() <= vertical_limit
            {
                let raw = reconstruct_path(&visits, entry.address, None);
                return PathQuery::Found(smooth_path(scene, contacts, agent, &raw));
            }
        }

        if visited_count >= options.max_visited {
            return if frontier.is_empty() {
                PathQuery::NoPath
            } else {
                PathQuery::BudgetExhausted
            };
        }

        for (dx, dz) in DIRECTIONS {
            let (nx, nz) = (entry.address.0 + dx, entry.address.1 + dz);
            if nx.abs() > options.max_grid_radius || nz.abs() > options.max_grid_radius {
                continue;
            }
            let target_x = start_point[0] + f64::from(nx) * options.grid_step;
            let target_z = start_point[2] + f64::from(nz) * options.grid_step;
            let EdgeQuery::Walkable(neighbor) =
                walk_edge(scene, contacts, agent, point, target_x, target_z)
            else {
                continue;
            };
            let step_cost = distance_3d(point, neighbor);
            if step_cost <= 1e-6 {
                continue;
            }
            let cost = entry.cost + step_cost;
            let next = address(nx, nz, neighbor[1]);
            if visits
                .get(&next)
                .is_some_and(|old| old.visited || cost >= old.cost)
            {
                continue;
            }
            visits.insert(
                next,
                Visit {
                    point: neighbor,
                    cost,
                    parent: Some(entry.address),
                    visited: false,
                },
            );
            frontier.push(Reverse(QueueEntry {
                score: cost + distance_3d(neighbor, goal_point),
                cost,
                address: next,
            }));
        }
    }

    PathQuery::NoPath
}

fn reconstruct_path(
    visits: &HashMap<Address, Visit>,
    last: Address,
    tail: Option<Vec3>,
) -> Vec<Vec3> {
    let mut reversed = Vec::new();
    if let Some(tail) = tail {
        reversed.push(tail);
    }
    let mut cursor = Some(last);
    while let Some(addr) = cursor {
        let visit = &visits[&addr];
        reversed.push(visit.point);
        cursor = visit.parent;
    }
    reversed.reverse();
    reversed
}

/// Sample and validate radial cone spawn candidates around `request.origin`.
pub fn valid_spawns(
    scene: &CollisionScene,
    contacts: Option<&ContactWorld>,
    agent: &NavAgent,
    request: &SpawnSearchRequest,
) -> SpawnQuery {
    if !agent.is_valid()
        || !request
            .origin
            .into_iter()
            .chain(request.direction)
            .all(f64::is_finite)
        || !request.radius.is_finite()
        || request.radius < 0.0
        || !request.half_angle_rad.is_finite()
        || request.half_angle_rad < 0.0
        || request.maximum == 0
        || request.minimum > request.maximum
    {
        return SpawnQuery::Unknown;
    }
    let [x, y, z] = request.origin;
    let start = match walkable_ground(scene, agent, x, z, y + agent.step_up, y - agent.step_down) {
        GroundQuery::Walkable(hit) => [x, hit.y, z],
        GroundQuery::Unwalkable(_) | GroundQuery::NoSurface => {
            return SpawnQuery::NoValidSpawns {
                candidates: Vec::new(),
            };
        }
        GroundQuery::Unknown => return SpawnQuery::Unknown,
    };
    let [dx, _, dz] = request.direction;
    let golden_turn = (5.0_f64.sqrt() - 1.0) / 2.0;
    let mut positions = Vec::new();
    for sample in 0..request.maximum {
        let radial = request.radius * ((sample as f64 + 0.5) / request.maximum as f64).sqrt();
        let angle = dz.atan2(dx)
            + request.half_angle_rad * (2.0 * (sample as f64 * golden_turn).fract() - 1.0);
        let target_x = start[0] + radial * angle.cos();
        let target_z = start[2] + radial * angle.sin();
        if let EdgeQuery::Walkable(landed) =
            walk_edge(scene, contacts, agent, start, target_x, target_z)
        {
            positions.push(landed);
        }
    }
    if positions.len() >= request.minimum {
        SpawnQuery::Found(positions)
    } else {
        SpawnQuery::NoValidSpawns {
            candidates: positions,
        }
    }
}

/// Validate and snap a slice of authored spawn positions onto walkable, capsule-clear ground.
pub fn snap_spawn_points(
    scene: &CollisionScene,
    contacts: Option<&ContactWorld>,
    agent: &NavAgent,
    spawns: &[Vec3],
    minimum: usize,
) -> SpawnQuery {
    if !agent.is_valid()
        || spawns.is_empty()
        || minimum > spawns.len()
        || !spawns.iter().flatten().all(|v| v.is_finite())
        || scene.triangles().scene_bounds().is_none()
    {
        return SpawnQuery::Unknown;
    }
    let mut valid = Vec::new();
    for &spawn in spawns {
        if let GroundQuery::Walkable(hit) = walkable_ground(
            scene,
            agent,
            spawn[0],
            spawn[2],
            spawn[1] + agent.step_up + 0.5,
            spawn[1] - agent.step_down - 2.0,
        ) {
            let point = [spawn[0], hit.y, spawn[2]];
            if capsule_clearance(scene, contacts, agent, point, point) == ClearanceQuery::Clear {
                valid.push(point);
            }
        }
    }
    if valid.len() >= minimum && !valid.is_empty() {
        SpawnQuery::Found(valid)
    } else {
        SpawnQuery::NoValidSpawns { candidates: valid }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use serde_json::json;

    use super::*;
    use crate::collision_cache::{CollisionCache, Mesh, Triangle};

    fn quad_triangles(vertices: [[f32; 3]; 4], mask: u16, surface: u16) -> Mesh {
        Mesh {
            vertices: vertices.to_vec(),
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask,
                    surface,
                },
                Triangle {
                    corners: [2, 3, 0],
                    wings: [-1; 3],
                    mask,
                    surface,
                },
            ],
        }
    }

    fn build_scene_and_world(meshes: Vec<Mesh>) -> (CollisionScene, ContactWorld) {
        let counts: Vec<[usize; 2]> = meshes
            .iter()
            .map(|m| [m.vertices.len(), m.triangles.len()])
            .collect();
        let cache = CollisionCache {
            header: serde_json::from_value(json!({
                "meshes": counts,
                "hulls": 0,
                "models": 0,
                "placements": 0,
                "identifiers": [],
            }))
            .unwrap(),
            meshes,
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        let ids = BTreeSet::new();
        let scene = CollisionScene::movement_probe(&cache, &ids).unwrap();
        let world = ContactWorld::from_movement_cache(cache, &ids).unwrap();
        (scene, world)
    }

    #[test]
    fn walkable_ground_classifies_flat_ramp_steep_non_ground_pit_low_ceiling_and_unknown() {
        let flat = quad_triangles(
            [
                [-4.0, 0.0, -4.0],
                [-4.0, 0.0, 4.0],
                [4.0, 0.0, 4.0],
                [4.0, 0.0, -4.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        // Gentle ramp: rises 2m over 4m in Z (26.56 deg < 45 deg).
        let ramp = quad_triangles(
            [
                [-4.0, 0.0, 4.0],
                [-4.0, 2.0, 8.0],
                [4.0, 2.0, 8.0],
                [4.0, 0.0, 4.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        // Steep slope: rises 4m over 2m in Z (63.4 deg > 45 deg).
        let steep = quad_triangles(
            [
                [5.0, 0.0, -2.0],
                [5.0, 4.0, 0.0],
                [9.0, 4.0, 0.0],
                [9.0, 0.0, -2.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        // NOT_GROUND surface flag = 1.
        let non_ground = quad_triangles(
            [
                [5.0, 0.0, 2.0],
                [5.0, 0.0, 6.0],
                [9.0, 0.0, 6.0],
                [9.0, 0.0, 2.0],
            ],
            DEFAULT_NAV_CATEGORY,
            NOT_GROUND_SURFACE,
        );
        // Low ceiling at y = 1.2m over x in [-3.5, -2.0], z in [-3.5, -2.0].
        let low_ceiling = quad_triangles(
            [
                [-3.5, 1.2, -3.5],
                [-3.5, 1.2, -2.0],
                [-2.0, 1.2, -2.0],
                [-2.0, 1.2, -3.5],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        let (scene, _) = build_scene_and_world(vec![flat, ramp, steep, non_ground, low_ceiling]);
        let agent = NavAgent::default();

        match walkable_ground(&scene, &agent, 0.0, 0.0, 1.0, -1.0) {
            GroundQuery::Walkable(hit) => assert!((hit.y - 0.0).abs() < 1e-6),
            other => panic!("expected walkable flat floor, got {other:?}"),
        }
        match walkable_ground(&scene, &agent, 0.0, 6.0, 3.0, -1.0) {
            GroundQuery::Walkable(hit) => assert!((hit.y - 1.0).abs() < 1e-5),
            other => panic!("expected walkable ramp, got {other:?}"),
        }
        assert!(matches!(
            walkable_ground(&scene, &agent, 7.0, -1.0, 5.0, -1.0),
            GroundQuery::Unwalkable(_)
        ));
        assert!(matches!(
            walkable_ground(&scene, &agent, 7.0, 4.0, 1.0, -1.0),
            GroundQuery::Unwalkable(_)
        ));
        assert!(matches!(
            walkable_ground(&scene, &agent, -2.75, -2.75, 0.5, -0.5),
            GroundQuery::Unwalkable(_)
        ));
        // Gap between x=4 and x=5 inside the scene's overall XZ bounds [-4..9, -4..8].
        assert_eq!(
            walkable_ground(&scene, &agent, 4.5, 0.0, 2.0, -2.0),
            GroundQuery::NoSurface
        );
        // Coordinates outside the scene's XZ bounds or invalid parameters return Unknown.
        assert_eq!(
            walkable_ground(&scene, &agent, 100.0, 100.0, 2.0, -2.0),
            GroundQuery::Unknown
        );
        assert_eq!(
            walkable_ground(&scene, &agent, f64::NAN, 0.0, 2.0, -2.0),
            GroundQuery::Unknown
        );
        let (empty_scene, _) = build_scene_and_world(vec![]);
        assert_eq!(
            walkable_ground(&empty_scene, &agent, 0.0, 0.0, 2.0, -2.0),
            GroundQuery::Unknown
        );
    }

    #[test]
    fn find_path_routes_around_walls_climbs_ramps_and_reports_no_path_budget_and_unknown() {
        // Main floor [-8..8, -2..12] at y=0, plus an elevated ramp at x in [2..6], z in [12..16] rising to y=1.5,
        // and an upper platform [2..6, 16..20] at y=1.5.
        let floor = quad_triangles(
            [
                [-8.0, 0.0, -2.0],
                [-8.0, 0.0, 12.0],
                [8.0, 0.0, 12.0],
                [8.0, 0.0, -2.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        let ramp = quad_triangles(
            [
                [2.0, 0.0, 12.0],
                [2.0, 1.5, 16.0],
                [6.0, 1.5, 16.0],
                [6.0, 0.0, 12.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        let upper = quad_triangles(
            [
                [2.0, 1.5, 16.0],
                [2.0, 1.5, 20.0],
                [6.0, 1.5, 20.0],
                [6.0, 1.5, 16.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        // Vertical wall blocking x in [-2.5..2.5] at z = 5.0 (height 0..3).
        let wall = quad_triangles(
            [
                [-2.5, 0.0, 5.0],
                [-2.5, 3.0, 5.0],
                [2.5, 3.0, 5.0],
                [2.5, 0.0, 5.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        // Disconnected island at x in [-8..-4], z in [16..20], y = 0.0 (4m gap across z in [12..16]).
        let island = quad_triangles(
            [
                [-8.0, 0.0, 16.0],
                [-8.0, 0.0, 20.0],
                [-4.0, 0.0, 20.0],
                [-4.0, 0.0, 16.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        let (scene, world) = build_scene_and_world(vec![floor, ramp, upper, wall, island]);
        let agent = NavAgent::default();
        let options = PathOptions::default();

        // Direct segment across z=5 is blocked by the wall.
        assert_eq!(
            capsule_clearance(
                &scene,
                Some(&world),
                &agent,
                [0.0, 0.0, 1.0],
                [0.0, 0.0, 9.0]
            ),
            ClearanceQuery::Blocked
        );
        assert_eq!(
            walk_edge(&scene, Some(&world), &agent, [0.0, 0.0, 1.0], 0.0, 9.0),
            EdgeQuery::Blocked
        );

        // Pathfinder routes around the wall and climbs the ramp to the upper platform.
        let PathQuery::Found(path) = find_path(
            &scene,
            Some(&world),
            &agent,
            [0.0, 0.0, 1.0],
            [4.0, 1.5, 18.0],
            &options,
        ) else {
            panic!("expected path around wall and up ramp");
        };
        assert!(path.len() >= 3, "path must detour around wall: {path:?}");
        assert_eq!(path.first().copied(), Some([0.0, 0.0, 1.0]));
        let end = *path.last().unwrap();
        assert!((end[0] - 4.0).hypot(end[2] - 18.0) <= options.goal_tolerance);
        assert!((end[1] - 1.5).abs() < 0.1);
        for pair in path.windows(2) {
            assert_eq!(
                walk_edge(
                    &scene,
                    Some(&world),
                    &agent,
                    pair[0],
                    pair[1][0],
                    pair[1][2]
                ),
                EdgeQuery::Walkable(pair[1]),
                "every smoothed path segment must be walkable: {pair:?}"
            );
        }

        // Disconnected island returns explicit NoPath.
        assert_eq!(
            find_path(
                &scene,
                Some(&world),
                &agent,
                [0.0, 0.0, 1.0],
                [-6.0, 0.0, 18.0],
                &options,
            ),
            PathQuery::NoPath
        );

        // Tight visit budget on a multi-step detour returns explicit BudgetExhausted.
        let tight = PathOptions {
            max_visited: 3,
            ..options
        };
        assert_eq!(
            find_path(
                &scene,
                Some(&world),
                &agent,
                [0.0, 0.0, 1.0],
                [4.0, 1.5, 18.0],
                &tight,
            ),
            PathQuery::BudgetExhausted
        );

        // Out-of-scene coordinates return explicit Unknown.
        assert_eq!(
            find_path(
                &scene,
                Some(&world),
                &agent,
                [0.0, 0.0, 1.0],
                [500.0, 0.0, 500.0],
                &options,
            ),
            PathQuery::Unknown
        );
    }

    #[test]
    fn valid_spawns_and_snap_spawn_points_filter_unwalkable_and_blocked_candidates() {
        let floor = quad_triangles(
            [
                [-3.0, 0.0, -1.0],
                [-3.0, 0.0, 8.0],
                [3.0, 0.0, 8.0],
                [3.0, 0.0, -1.0],
            ],
            DEFAULT_NAV_CATEGORY,
            0,
        );
        let (scene, world) = build_scene_and_world(vec![floor]);
        let agent = NavAgent::default();

        let request = SpawnSearchRequest {
            origin: [0.0, 0.0, 0.0],
            direction: [0.0, 0.0, 1.0],
            radius: 4.0,
            half_angle_rad: 0.4,
            minimum: 2,
            maximum: 6,
        };
        let SpawnQuery::Found(spawns) = valid_spawns(&scene, Some(&world), &agent, &request) else {
            panic!("expected valid cone spawns on +Z floor");
        };
        assert_eq!(spawns.len(), 6);

        // Searching into the -Z void fails minimum count and returns NoValidSpawns.
        let void_request = SpawnSearchRequest {
            direction: [0.0, 0.0, -1.0],
            minimum: 4,
            ..request
        };
        assert!(matches!(
            valid_spawns(&scene, Some(&world), &agent, &void_request),
            SpawnQuery::NoValidSpawns { .. }
        ));

        let snapped = snap_spawn_points(
            &scene,
            Some(&world),
            &agent,
            &[[0.0, 0.25, 2.0], [0.0, 0.1, 5.0], [0.0, 0.0, -3.5]],
            2,
        );
        assert_eq!(
            snapped,
            SpawnQuery::Found(vec![[0.0, 0.0, 2.0], [0.0, 0.0, 5.0]])
        );
    }
}
