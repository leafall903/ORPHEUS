//! Generic, data-driven room & objective primitives shared across PvP, PvE, and custom modes.
//!
//! All five primitives (`ObbTrigger`, `SplineTrack`/`SplineMover`, `DynamicEntityBus`/
//! `InteractivePropRuntime`, `DynamicBarrierRegistry`, and `RoomVoiceRouter`) are strictly
//! data-driven with zero map/hero/prop GUID hardcoding.

use std::collections::{BTreeMap, BTreeSet, VecDeque};

use orpheus_statescript::script_wire::Value;

use crate::{
    contact_math::ContactFrame,
    contact_round::{RoundShape, Sphere},
    contact_world::{ContactWorld, ContactWorldError, StaticGeometry, StaticShape},
    world::ANGLE_UNITS,
};

pub const SPECTATOR_TEAM: u8 = 3;
pub(crate) const FREE_FOR_ALL_ROSTER_TEAM: u8 = 2;
pub const MAP_ENTITY_BIT: u32 = 0x8000_0000;

pub const DOOR_OPEN_ANIM: u16 = 0;
pub const DOOR_CLOSE_ANIM: u16 = 1;
pub const DOOR_OPEN_ALIAS: u16 = 5;
pub const DOOR_CLOSE_ALIAS: u16 = 8;
pub const GATE_DEBRIS_DELAY: f64 = 3.0;

pub const FOLLOW_UP_GAP: f64 = 2.0;
pub const CAPTURE_FOLLOW_UP_GAP: f64 = 3.5;
pub const SCORE_FOLLOW_UP_GAP: f64 = 1.5;

pub(crate) fn choose_seat(
    capacity: u8,
    requested: Option<u8>,
    current: Option<u8>,
    occupied: impl Fn(u8) -> bool,
) -> Option<u8> {
    let available = |slot| slot < capacity && !occupied(slot);
    if let Some(slot) = requested {
        return available(slot).then_some(slot);
    }
    current
        .filter(|&slot| available(slot))
        .or_else(|| (0..capacity).find(|&slot| available(slot)))
}

// ---------------------------------------------------------------------------
// 1. ObbTrigger & ObbZone
// ---------------------------------------------------------------------------

/// Oriented bounding box trigger volume constructed from retail 10-float bounds:
/// `[qx, qy, qz, qw, cx, cy, cz, hx, hy, hz]`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ObbTrigger {
    pub rotation: [f64; 4],
    pub center: [f64; 3],
    pub half_extents: [f64; 3],
}

impl ObbTrigger {
    pub fn from_raw(raw: [f64; 10]) -> Self {
        let mut q = [raw[0], raw[1], raw[2], raw[3]];
        let norm = (q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]).sqrt();
        if norm > 1e-12 {
            for axis in &mut q {
                *axis /= norm;
            }
        } else {
            q = [0.0, 0.0, 0.0, 1.0];
        }
        Self {
            rotation: q,
            center: [raw[4], raw[5], raw[6]],
            half_extents: [raw[7].abs(), raw[8].abs(), raw[9].abs()],
        }
    }

    pub fn contains(&self, point: [f64; 3]) -> bool {
        let rel = [
            point[0] - self.center[0],
            point[1] - self.center[1],
            point[2] - self.center[2],
        ];
        let local = unrotate_vec(self.rotation, rel);
        local[0].abs() <= self.half_extents[0] + 1e-9
            && local[1].abs() <= self.half_extents[1] + 1e-9
            && local[2].abs() <= self.half_extents[2] + 1e-9
    }
}

/// Sampled actor state for zone occupancy queries.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ActorSample {
    pub entity: u32,
    pub team: u8,
    pub position: [f64; 3],
    pub alive: bool,
}

impl ActorSample {
    pub fn living(entity: u32, team: u8, position: [f64; 3]) -> Self {
        Self {
            entity,
            team,
            position,
            alive: true,
        }
    }
}

/// Per-team occupant list inside a trigger zone (excluding spectators and dead actors).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TeamOccupancy {
    pub team0: Vec<u32>,
    pub team1: Vec<u32>,
}

/// Collection of OBB triggers forming a single gameplay zone (capture point, healing room, goal).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ObbZone {
    pub boxes: Vec<ObbTrigger>,
}

impl ObbZone {
    pub fn new(boxes: Vec<ObbTrigger>) -> Self {
        Self { boxes }
    }

    pub fn contains(&self, point: [f64; 3]) -> bool {
        self.boxes.iter().any(|obb| obb.contains(point))
    }

    pub fn occupants(&self, actors: &[ActorSample]) -> TeamOccupancy {
        let mut out = TeamOccupancy::default();
        for actor in actors {
            if !actor.alive || actor.team == SPECTATOR_TEAM || !self.contains(actor.position) {
                continue;
            }
            match actor.team {
                0 => out.team0.push(actor.entity),
                1 => out.team1.push(actor.entity),
                _ => {}
            }
        }
        out
    }
}

pub fn rotate_vec(q: [f64; 4], v: [f64; 3]) -> [f64; 3] {
    let [qx, qy, qz, qw] = q;
    let [vx, vy, vz] = v;
    let tx = 2.0 * (qy * vz - qz * vy);
    let ty = 2.0 * (qz * vx - qx * vz);
    let tz = 2.0 * (qx * vy - qy * vx);
    [
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    ]
}

pub fn unrotate_vec(q: [f64; 4], v: [f64; 3]) -> [f64; 3] {
    rotate_vec([-q[0], -q[1], -q[2], q[3]], v)
}

pub fn quat_mul(a: [f64; 4], b: [f64; 4]) -> [f64; 4] {
    [
        a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
        a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
        a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
        a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
    ]
}

// ---------------------------------------------------------------------------
// 2. SplineTrack, SplineMover & RiderCarry
// ---------------------------------------------------------------------------

pub const DEFAULT_PUSH_RADIUS: f64 = 7.5;
pub const DEFAULT_PUSH_HEIGHT: f64 = 4.0;
pub const DEFAULT_PUSH_SPEEDS: [f64; 4] = [0.0, 1.4, 1.65, 1.9];
pub const DEFAULT_AUTO_DRIVE_SPEED: f64 = 2.0;
pub const DEFAULT_ROLLBACK_DELAY: f64 = 10.0;
pub const DEFAULT_ROLLBACK_SPEED: f64 = 0.3;
pub const SAMPLES_PER_SEGMENT: usize = 32;
pub const RAIL_LOOKAHEAD: f64 = 0.75;

#[derive(Clone, Debug, PartialEq, serde::Deserialize)]
pub struct SplineNode {
    pub position: [f64; 3],
    /// Relative cubic Bezier control offsets `(c1, c2, end)` from `position`.
    pub curve: Option<[[f64; 3]; 3]>,
    pub checkpoint: Option<u32>,
    #[serde(default)]
    pub wait_seconds: f64,
    pub open_gate: Option<u32>,
    pub close_gate: Option<u32>,
    #[serde(default)]
    pub gate_impact: bool,
    pub auto_drive_to: Option<usize>,
}

impl SplineNode {
    pub fn simple(position: [f64; 3]) -> Self {
        Self {
            position,
            curve: None,
            checkpoint: None,
            wait_seconds: 0.0,
            open_gate: None,
            close_gate: None,
            gate_impact: false,
            auto_drive_to: None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SplinePose {
    pub position: [f64; 3],
    pub yaw: i16,
    pub pitch: i16,
    pub rotation: [f64; 4],
}

#[derive(Clone, Debug, PartialEq)]
pub struct SplineTrack {
    pub nodes: Vec<SplineNode>,
    /// Cumulative arc-length at each sampled point `(distance, position)`.
    samples: Vec<(f64, [f64; 3])>,
    pub node_distances: Vec<f64>,
    pub total_length: f64,
}

impl SplineTrack {
    pub fn from_nodes(nodes: Vec<SplineNode>) -> Self {
        let mut samples = Vec::new();
        let mut node_distances = Vec::with_capacity(nodes.len());
        let mut total = 0.0;

        if let Some(first) = nodes.first() {
            samples.push((0.0, first.position));
            node_distances.push(0.0);
        }

        for i in 0..nodes.len().saturating_sub(1) {
            let a = &nodes[i];
            let b = &nodes[i + 1];
            let p0 = a.position;
            let (p1, p2, p3) = if let Some([c1, c2, end]) = a.curve {
                (
                    [p0[0] + c1[0], p0[1] + c1[1], p0[2] + c1[2]],
                    [p0[0] + c2[0], p0[1] + c2[1], p0[2] + c2[2]],
                    [p0[0] + end[0], p0[1] + end[1], p0[2] + end[2]],
                )
            } else {
                (p0, b.position, b.position)
            };
            let mut prev = p0;
            for step in 1..=SAMPLES_PER_SEGMENT {
                let t = (step as f64) / (SAMPLES_PER_SEGMENT as f64);
                let pt = cubic_bezier(p0, p1, p2, p3, t);
                total += dist3(prev, pt);
                samples.push((total, pt));
                prev = pt;
            }
            node_distances.push(total);
        }

        Self {
            nodes,
            samples,
            node_distances,
            total_length: total,
        }
    }

    pub fn point_at(&self, distance: f64) -> [f64; 3] {
        if self.samples.is_empty() {
            return [0.0; 3];
        }
        let d = distance.clamp(0.0, self.total_length);
        let idx = self
            .samples
            .partition_point(|(s, _)| *s < d)
            .min(self.samples.len() - 1);
        if idx == 0 {
            return self.samples[0].1;
        }
        let (d0, p0) = self.samples[idx - 1];
        let (d1, p1) = self.samples[idx];
        let span = d1 - d0;
        let t = if span > 1e-9 { (d - d0) / span } else { 0.0 };
        [
            p0[0] + (p1[0] - p0[0]) * t,
            p0[1] + (p1[1] - p0[1]) * t,
            p0[2] + (p1[2] - p0[2]) * t,
        ]
    }

    pub fn pose_at(&self, distance: f64) -> SplinePose {
        let d = distance.clamp(0.0, self.total_length);
        let pos = self.point_at(d);
        let back_d = (d - RAIL_LOOKAHEAD).max(0.0);
        let ahead_d = (d + RAIL_LOOKAHEAD).min(self.total_length);
        let back = self.point_at(back_d);
        let ahead = self.point_at(ahead_d);
        let dx = ahead[0] - back[0];
        let dy = ahead[1] - back[1];
        let dz = ahead[2] - back[2];
        let horiz = dx.hypot(dz);
        let yaw_rad = if horiz > 1e-9 { dx.atan2(dz) } else { 0.0 };
        let pitch_rad = if horiz > 1e-9 || dy.abs() > 1e-9 {
            (-dy).atan2(horiz)
        } else {
            0.0
        };
        let yaw = (yaw_rad * ANGLE_UNITS).round() as i16;
        let pitch = (pitch_rad * ANGLE_UNITS).round() as i16;
        let hy = (f64::from(yaw) / ANGLE_UNITS) * 0.5;
        let hp = (f64::from(pitch) / ANGLE_UNITS) * 0.5;
        let rotation = quat_mul(
            [0.0, hy.sin(), 0.0, hy.cos()],
            [hp.sin(), 0.0, 0.0, hp.cos()],
        );
        SplinePose {
            position: pos,
            yaw,
            pitch,
            rotation,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SplineSpeedCurve {
    pub speeds: [f64; 4],
    pub auto_drive_speed: f64,
    pub rollback_delay: f64,
    pub rollback_speed: f64,
    pub speed_scale: f64,
    pub push_radius: f64,
    pub push_height: f64,
}

impl Default for SplineSpeedCurve {
    fn default() -> Self {
        Self {
            speeds: DEFAULT_PUSH_SPEEDS,
            auto_drive_speed: DEFAULT_AUTO_DRIVE_SPEED,
            rollback_delay: DEFAULT_ROLLBACK_DELAY,
            rollback_speed: DEFAULT_ROLLBACK_SPEED,
            speed_scale: 1.0,
            push_radius: DEFAULT_PUSH_RADIUS,
            push_height: DEFAULT_PUSH_HEIGHT,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SplineStepEvents {
    pub open_gates: Vec<(u32, f64)>,
    pub close_gates: Vec<u32>,
    pub gate_impact_at: Option<f64>,
    pub reached_checkpoints: Vec<u32>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SplineMover {
    pub track: SplineTrack,
    pub curve: SplineSpeedCurve,
    pub distance: f64,
    pub speed: f64,
    pub direction: i8,
    pub pushers: Vec<u32>,
    pub blockers: Vec<u32>,
    pub last_attacker: f64,
    pub last_update: f64,
    pub next_route_event: usize,
    pub route_wait_until: f64,
    pub route_drive_end: f64,
    pub route_rollback_floor: f64,
    pub auto_drive: bool,
    pub auto_drive_end: f64,
    pub active_auto_drive: bool,
    pub rollback_floor: f64,
    pub last_step_scripted: bool,
}

impl SplineMover {
    pub fn new(track: SplineTrack, curve: SplineSpeedCurve, auto_drive: bool) -> Self {
        let mut next_route_event = 0;
        while next_route_event < track.nodes.len() && track.node_distances[next_route_event] <= 0.0
        {
            let node = &track.nodes[next_route_event];
            if node.wait_seconds > 0.0
                || node.open_gate.is_some()
                || node.close_gate.is_some()
                || node.gate_impact
                || node.auto_drive_to.is_some()
            {
                break;
            }
            next_route_event += 1;
        }
        let auto_drive_end = if auto_drive && track.node_distances.len() > 1 {
            track.node_distances[1]
        } else {
            0.0
        };
        Self {
            track,
            curve,
            distance: 0.0,
            speed: 0.0,
            direction: 0,
            pushers: Vec::new(),
            blockers: Vec::new(),
            last_attacker: 0.0,
            last_update: 0.0,
            next_route_event,
            route_wait_until: 0.0,
            route_drive_end: 0.0,
            route_rollback_floor: 0.0,
            auto_drive,
            auto_drive_end,
            active_auto_drive: auto_drive,
            rollback_floor: 0.0,
            last_step_scripted: false,
        }
    }

    pub fn pose(&self) -> SplinePose {
        self.track.pose_at(self.distance)
    }

    pub fn scripted_movement(&self) -> bool {
        self.last_update < self.route_wait_until
            || self.distance < self.route_drive_end
            || self.active_auto_drive
    }

    pub fn contested(&self) -> bool {
        !self.pushers.is_empty() && !self.blockers.is_empty() && !self.scripted_movement()
    }

    pub fn effective_pushers(&self) -> &[u32] {
        if self.last_update < self.route_wait_until {
            &[]
        } else {
            &self.pushers
        }
    }

    pub fn effective_direction(&self) -> i8 {
        if self.last_step_scripted || self.scripted_movement() {
            0
        } else {
            self.direction
        }
    }

    pub fn step(
        &mut self,
        now: f64,
        actors: &[ActorSample],
        attacker_team: u8,
        defender_team: u8,
    ) -> SplineStepEvents {
        self.sample_occupants(self.pose().position, actors, attacker_team, defender_team);
        self.step_sampled(now, f64::INFINITY)
    }

    pub(crate) fn sample_occupants(
        &mut self,
        position: [f64; 3],
        actors: &[ActorSample],
        attacker_team: u8,
        defender_team: u8,
    ) {
        self.pushers.clear();
        self.blockers.clear();
        for actor in actors.iter().filter(|actor| actor.alive) {
            let dy = (actor.position[1] - position[1]).abs();
            let horiz = (actor.position[0] - position[0]).hypot(actor.position[2] - position[2]);
            if dy <= self.curve.push_height && horiz <= self.curve.push_radius {
                if actor.team == attacker_team {
                    self.pushers.push(actor.entity);
                } else if actor.team == defender_team {
                    self.blockers.push(actor.entity);
                }
            }
        }
    }

    pub(crate) fn step_sampled(&mut self, now: f64, end: f64) -> SplineStepEvents {
        let mut events = SplineStepEvents {
            open_gates: Vec::new(),
            close_gates: Vec::new(),
            gate_impact_at: None,
            reached_checkpoints: Vec::new(),
        };
        let prev_distance = self.distance;
        let elapsed = (now.min(end) - self.last_update.max(self.route_wait_until)).max(0.0);
        let route_driving = prev_distance < self.route_drive_end;
        let auto_end = self.auto_drive_end.max(self.route_drive_end);
        let auto_driving = prev_distance < auto_end;
        self.last_step_scripted = auto_driving || now < self.route_wait_until || route_driving;
        let mut push_dist = 0.0;

        if auto_driving && elapsed > 0.0 {
            push_dist = (elapsed * self.curve.auto_drive_speed * self.curve.speed_scale)
                .min((auto_end - prev_distance).max(0.0));
            self.last_attacker = now;
        } else if now < self.route_wait_until {
            self.last_attacker = now;
        } else if !self.pushers.is_empty() && now < end {
            self.last_attacker = now;
            if self.blockers.is_empty() {
                let count = self.pushers.len().min(3);
                push_dist = elapsed * self.curve.speeds[count] * self.curve.speed_scale;
            }
        } else if now > self.last_attacker + self.curve.rollback_delay {
            let rb_dt = elapsed.min(now - self.last_attacker - self.curve.rollback_delay);
            let floor = self.rollback_floor.max(self.route_rollback_floor);
            self.distance = (self.distance
                - rb_dt * self.curve.rollback_speed * self.curve.speed_scale)
                .max(floor);
        }

        self.last_update = now;
        if push_dist > 0.0 {
            let mut dest = (self.distance + push_dist).min(self.track.total_length);
            while self.next_route_event < self.track.nodes.len() {
                let marker = self.track.node_distances[self.next_route_event];
                if dest + 1e-9 < marker {
                    break;
                }
                let node = &self.track.nodes[self.next_route_event];
                self.next_route_event += 1;
                if let Some(tag) = node.open_gate {
                    events.open_gates.push((tag, node.wait_seconds));
                }
                if let Some(tag) = node.close_gate {
                    events.close_gates.push(tag);
                    self.route_rollback_floor = self.route_rollback_floor.max(marker);
                }
                if node.gate_impact {
                    events.gate_impact_at = Some(now);
                }
                if let Some(idx) = node.auto_drive_to {
                    if let Some(&target_d) = self.track.node_distances.get(idx) {
                        self.route_drive_end = target_d;
                    }
                }
                if node.wait_seconds > 0.0 || node.auto_drive_to.is_some() {
                    self.route_wait_until = now + node.wait_seconds;
                    dest = marker;
                    break;
                }
            }
            for (idx, node) in self.track.nodes.iter().enumerate() {
                if let Some(cp) = node.checkpoint {
                    let marker = self.track.node_distances[idx];
                    if prev_distance + 1e-9 < marker && dest + 1e-9 >= marker {
                        self.rollback_floor = self.rollback_floor.max(marker);
                        events.reached_checkpoints.push(cp);
                    }
                }
            }
            self.distance = dest;
            if self.active_auto_drive && self.distance >= self.auto_drive_end {
                self.active_auto_drive = false;
                self.rollback_floor = self.rollback_floor.max(self.auto_drive_end);
            }
            if route_driving && self.distance >= self.route_drive_end {
                self.route_rollback_floor = self.route_rollback_floor.max(self.route_drive_end);
            }
        }

        self.speed = if elapsed > 0.0 {
            (self.distance - prev_distance) / elapsed
        } else {
            0.0
        };
        if now < self.route_wait_until || route_driving && self.distance >= self.route_drive_end {
            self.speed = 0.0;
        }
        self.direction = if self.distance > prev_distance + 1e-9 {
            1
        } else if self.distance + 1e-9 < prev_distance {
            -1
        } else {
            0
        };
        events
    }
}

/// Computes local rider anchor and world carry delta on a moving `SplinePose`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RiderCarry;

impl RiderCarry {
    pub fn local_anchor(pose: SplinePose, world_pos: [f64; 3]) -> [f64; 3] {
        unrotate_vec(
            pose.rotation,
            [
                world_pos[0] - pose.position[0],
                world_pos[1] - pose.position[1],
                world_pos[2] - pose.position[2],
            ],
        )
    }

    pub fn world_target(pose: SplinePose, local_anchor: [f64; 3]) -> [f64; 3] {
        let r = rotate_vec(pose.rotation, local_anchor);
        [
            pose.position[0] + r[0],
            pose.position[1] + r[1],
            pose.position[2] + r[2],
        ]
    }
}

// ---------------------------------------------------------------------------
// 3. DynamicEntityBus & InteractivePropRuntime
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct BusMessage {
    pub target: u32,
    pub message: u64,
    pub params: BTreeMap<u32, Value>,
}

/// Generic registry of dynamic room entities, synced entity variables, and routed messages.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct DynamicEntityBus {
    pub entities: BTreeSet<u32>,
    pub vars: BTreeMap<u32, BTreeMap<u32, Value>>,
    pub queue: VecDeque<BusMessage>,
}

impl DynamicEntityBus {
    pub fn spawn(&mut self, entity: u32) {
        self.entities.insert(entity);
        self.vars.entry(entity).or_default();
    }

    pub fn despawn(&mut self, entity: u32) {
        self.entities.remove(&entity);
        self.vars.remove(&entity);
    }

    pub fn set_var(&mut self, entity: u32, key: u32, value: Value) {
        self.vars.entry(entity).or_default().insert(key, value);
    }

    pub fn get_var(&self, entity: u32, key: u32) -> Option<&Value> {
        self.vars.get(&entity)?.get(&key)
    }

    pub fn send(&mut self, target: u32, message: u64, params: BTreeMap<u32, Value>) {
        self.queue.push_back(BusMessage {
            target,
            message,
            params,
        });
    }
}

/// Data-driven interactive physics prop (e.g. spawn-room basketball, bottle, screen).
#[derive(Clone, Debug, PartialEq)]
pub struct PhysicsProp {
    pub entity: u32,
    pub spawn_position: [f64; 3],
    pub position: [f64; 3],
    pub velocity: [f64; 3],
    pub radius: f64,
    pub restitution: f64,
    pub friction: f64,
    pub gravity: f64,
    pub active: bool,
}

impl PhysicsProp {
    pub fn new(entity: u32, spawn_position: [f64; 3], radius: f64) -> Self {
        Self {
            entity,
            spawn_position,
            position: spawn_position,
            velocity: [0.0; 3],
            radius,
            restitution: 0.72,
            friction: 0.92,
            gravity: 16.0,
            active: true,
        }
    }

    pub fn apply_impulse(&mut self, dir: [f64; 3], strength: f64) {
        let len = (dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2]).sqrt();
        if len <= 1e-9 || !strength.is_finite() {
            return;
        }
        let inv = strength / len;
        self.velocity[0] += dir[0] * inv;
        self.velocity[1] += dir[1] * inv;
        self.velocity[2] += dir[2] * inv;
    }

    pub fn reset(&mut self) {
        self.position = self.spawn_position;
        self.velocity = [0.0; 3];
        self.active = true;
    }
}

/// Data-driven trigger goal (e.g. basketball hoop volume) emitting an effect cue when entered.
#[derive(Clone, Debug, PartialEq)]
pub struct TriggerGoal {
    pub entity: u32,
    pub volume: ObbTrigger,
    pub require_downward: bool,
    pub effect_cue_id: u64,
    pub triggered_entities: BTreeSet<u32>,
}

/// Retail 1.74 logical button bit for Interact (`Button(30, "Interact")` in `05800000000023b0`).
pub const INTERACT_BUTTON_BIT: u32 = 1 << 30;

/// Retail 1.74 reset button cooldown (`Wait_14 after(2.5)` in `05800000000023b0`).
pub const DEFAULT_RESET_BUTTON_COOLDOWN_SECS: f64 = 2.5;

/// Default interact reach for reset buttons when no custom radius is specified.
pub const DEFAULT_RESET_BUTTON_RADIUS: f64 = 2.5;

/// Data-driven reset button linked to one or more `PhysicsProp` entities.
#[derive(Clone, Debug, PartialEq)]
pub struct ResetButtonProp {
    pub entity: u32,
    pub position: [f64; 3],
    pub interact_radius: f64,
    pub cooldown_secs: f64,
    pub last_pressed_at: Option<f64>,
    pub linked_props: Vec<u32>,
    pub reset_cue_id: u64,
}

impl ResetButtonProp {
    pub fn new(entity: u32, position: [f64; 3], linked_props: Vec<u32>, reset_cue_id: u64) -> Self {
        Self {
            entity,
            position,
            interact_radius: DEFAULT_RESET_BUTTON_RADIUS,
            cooldown_secs: DEFAULT_RESET_BUTTON_COOLDOWN_SECS,
            last_pressed_at: None,
            linked_props,
            reset_cue_id,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PropCueEvent {
    pub source_entity: u32,
    pub prop_entity: u32,
    pub cue_id: u64,
}

/// Coordinates physics props, floor/wall reflection, goal triggers, and reset buttons.
#[derive(Clone, Debug, Default)]
pub struct InteractivePropRuntime {
    pub props: BTreeMap<u32, PhysicsProp>,
    pub goals: Vec<TriggerGoal>,
    pub buttons: BTreeMap<u32, ResetButtonProp>,
    pub emitted_cues: Vec<PropCueEvent>,
}

impl InteractivePropRuntime {
    pub fn apply_hit_impulse(&mut self, prop_entity: u32, dir: [f64; 3], strength: f64) -> bool {
        if let Some(prop) = self.props.get_mut(&prop_entity) {
            prop.apply_impulse(dir, strength);
            true
        } else {
            false
        }
    }

    /// Reset a single `PhysicsProp` to its authored spawn position and clear goal triggers.
    pub fn reset_prop(&mut self, prop_entity: u32) -> bool {
        let Some(prop) = self.props.get_mut(&prop_entity) else {
            return false;
        };
        prop.reset();
        for goal in &mut self.goals {
            goal.triggered_entities.remove(&prop_entity);
        }
        true
    }

    /// Intersect a weapon ray against all active `PhysicsProp` spheres and impart impulse to the nearest hit.
    pub fn hit_prop_by_ray(
        &mut self,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
        impulse: f64,
    ) -> Option<u32> {
        if self.props.is_empty()
            || !max_distance.is_finite()
            || max_distance <= 0.0
            || !impulse.is_finite()
            || impulse <= 0.0
        {
            return None;
        }
        let mut nearest: Option<(u32, f64)> = None;
        for (&entity, prop) in &self.props {
            if !prop.active || prop.radius <= 0.0 {
                continue;
            }
            let oc = [
                origin[0] - prop.position[0],
                origin[1] - prop.position[1],
                origin[2] - prop.position[2],
            ];
            let b = oc[0] * direction[0] + oc[1] * direction[1] + oc[2] * direction[2];
            let c = oc[0] * oc[0] + oc[1] * oc[1] + oc[2] * oc[2] - prop.radius * prop.radius;
            let disc = b * b - c;
            if disc < 0.0 {
                continue;
            }
            let root = disc.sqrt();
            let t = if -b - root >= 0.0 {
                -b - root
            } else if -b + root >= 0.0 {
                0.0
            } else {
                continue;
            };
            if t <= max_distance && nearest.is_none_or(|(_, best)| t < best) {
                nearest = Some((entity, t));
            }
        }
        let (entity, _) = nearest?;
        let lift_dir = [direction[0], (direction[1] + 0.25).max(0.15), direction[2]];
        self.apply_hit_impulse(entity, lift_dir, impulse);
        Some(entity)
    }

    /// Impart a radial impulse to all active `PhysicsProp` spheres overlapping a blast or melee sphere.
    pub fn hit_props_in_sphere(&mut self, center: [f64; 3], radius: f64, impulse: f64) -> usize {
        if self.props.is_empty()
            || !radius.is_finite()
            || radius <= 0.0
            || !impulse.is_finite()
            || impulse <= 0.0
        {
            return 0;
        }
        let mut count = 0usize;
        for prop in self.props.values_mut() {
            if !prop.active {
                continue;
            }
            let dx = prop.position[0] - center[0];
            let dy = prop.position[1] - center[1];
            let dz = prop.position[2] - center[2];
            let dist = (dx * dx + dy * dy + dz * dz).sqrt();
            let reach = radius + prop.radius.max(0.0);
            if dist <= reach {
                let falloff = (1.0 - dist / reach.max(1e-6)).clamp(0.25, 1.0);
                let dir = if dist > 1e-6 {
                    [dx / dist, (dy / dist + 0.35).max(0.2), dz / dist]
                } else {
                    [0.0, 1.0, 0.0]
                };
                prop.apply_impulse(dir, impulse * falloff);
                count += 1;
            }
        }
        count
    }

    pub fn press_button(&mut self, button_entity: u32) -> bool {
        let Some(button) = self.buttons.get(&button_entity).cloned() else {
            return false;
        };
        for prop_id in &button.linked_props {
            if self.reset_prop(*prop_id) {
                self.emitted_cues.push(PropCueEvent {
                    source_entity: button_entity,
                    prop_entity: *prop_id,
                    cue_id: button.reset_cue_id,
                });
            }
        }
        true
    }

    /// Press a reset button while enforcing its retail cooldown (e.g. 2.5s in `05800000000023b0`).
    pub fn press_button_at(&mut self, button_entity: u32, now_secs: f64) -> bool {
        let Some(button) = self.buttons.get_mut(&button_entity) else {
            return false;
        };
        if let Some(last) = button.last_pressed_at {
            if now_secs < last + button.cooldown_secs {
                return false;
            }
        }
        button.last_pressed_at = Some(now_secs);
        self.press_button(button_entity)
    }

    /// Press the closest reset button within its `interact_radius` of `pos` if off cooldown.
    pub fn press_nearby_button(&mut self, pos: [f64; 3], now_secs: f64) -> Option<u32> {
        let candidate = self
            .buttons
            .iter()
            .filter_map(|(&entity, button)| {
                let d = dist3(pos, button.position);
                (d <= button.interact_radius).then_some((entity, d))
            })
            .min_by(|a, b| a.1.total_cmp(&b.1).then_with(|| a.0.cmp(&b.0)))
            .map(|(entity, _)| entity)?;
        self.press_button_at(candidate, now_secs)
            .then_some(candidate)
    }

    pub fn take_emitted_cues(&mut self) -> Vec<PropCueEvent> {
        std::mem::take(&mut self.emitted_cues)
    }

    /// Advance all active physics props against a floor height & optional contact plane list.
    pub fn step(&mut self, dt: f64, floor_y: f64, wall_planes: &[([f64; 3], [f64; 3])]) {
        if dt <= 0.0 {
            return;
        }
        for prop in self.props.values_mut() {
            if !prop.active {
                continue;
            }
            prop.velocity[1] -= prop.gravity * dt;
            for i in 0..3 {
                prop.position[i] += prop.velocity[i] * dt;
            }
            // Floor bounce + rolling friction
            if prop.position[1] - prop.radius < floor_y {
                prop.position[1] = floor_y + prop.radius;
                if prop.velocity[1] < 0.0 {
                    let bounce = -prop.velocity[1] * prop.restitution;
                    prop.velocity[1] = if bounce > 0.25 { bounce } else { 0.0 };
                }
                prop.velocity[0] *= prop.friction;
                prop.velocity[2] *= prop.friction;
            }
            // Planar wall reflections
            for &(point_on_wall, normal) in wall_planes {
                let d = (prop.position[0] - point_on_wall[0]) * normal[0]
                    + (prop.position[1] - point_on_wall[1]) * normal[1]
                    + (prop.position[2] - point_on_wall[2]) * normal[2];
                if d < prop.radius {
                    let push = prop.radius - d;
                    for (pos, n) in prop.position.iter_mut().zip(normal) {
                        *pos += n * push;
                    }
                    let vn = prop.velocity[0] * normal[0]
                        + prop.velocity[1] * normal[1]
                        + prop.velocity[2] * normal[2];
                    if vn < 0.0 {
                        let factor = (1.0 + prop.restitution) * vn;
                        for (vel, n) in prop.velocity.iter_mut().zip(normal) {
                            *vel -= factor * n;
                        }
                    }
                }
            }
            // Evaluate OBB goal triggers
            for goal in &mut self.goals {
                let inside = goal.volume.contains(prop.position);
                if inside && (!goal.require_downward || prop.velocity[1] <= 0.0) {
                    if goal.triggered_entities.insert(prop.entity) {
                        self.emitted_cues.push(PropCueEvent {
                            source_entity: goal.entity,
                            prop_entity: prop.entity,
                            cue_id: goal.effect_cue_id,
                        });
                    }
                } else if !inside {
                    goal.triggered_entities.remove(&prop.entity);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// 4. DynamicBarrierRegistry
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BarrierKind {
    Door,
    CheckpointGate,
    GateDebris,
    TeamBarrier,
    Overlay,
    RestrictedSign,
}

impl BarrierKind {
    pub(crate) fn is_room_visual(self) -> bool {
        matches!(
            self,
            Self::TeamBarrier | Self::Overlay | Self::RestrictedSign
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DoorPose {
    Closed = 0,
    Opening = 1,
    Open = 2,
    Closing = 3,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DoorState {
    pub pose: DoorPose,
    pub states: BTreeSet<u16>,
    pub starts_at: f64,
    pub ends_at: f64,
    pub timed: bool,
    pub debris_played: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RouteGateCommand {
    pub opened: bool,
    pub starts_at: f64,
    pub ends_at: f64,
    pub collision_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub enum BarrierShape {
    Sphere {
        center: [f32; 3],
        radius: f32,
        mask: u16,
        translation: [f32; 3],
        rotation: [f32; 4],
    },
}

#[derive(Clone, Debug, PartialEq)]
pub struct BarrierEntry {
    pub placeable: u32,
    pub entity: u32,
    pub kind: BarrierKind,
    pub rooms: Vec<(usize, u8)>,
    pub viewers: Vec<u8>,
    pub position: [f64; 3],
    pub rotation: Option<[f64; 4]>,
    pub open_seconds: f64,
    pub close_seconds: f64,
    pub open_delay: f64,
    pub close_delay: f64,
    pub gate_tag: Option<u32>,
    pub gate_debris: bool,
    pub debris_rail_node: Option<usize>,
    pub payload_unlock: bool,
    pub shapes: Vec<BarrierShape>,
}

impl BarrierEntry {
    pub fn new(placeable: u32, kind: BarrierKind, rooms: Vec<(usize, u8)>) -> Self {
        Self {
            placeable,
            entity: MAP_ENTITY_BIT | placeable,
            kind,
            rooms,
            viewers: Vec::new(),
            position: [placeable as f64, 0.0, 0.0],
            rotation: None,
            open_seconds: 2.0,
            close_seconds: 2.0,
            open_delay: 0.0,
            close_delay: 0.0,
            gate_tag: None,
            gate_debris: false,
            debris_rail_node: None,
            payload_unlock: false,
            shapes: Vec::new(),
        }
    }

    pub(crate) fn door_command(&self, opened: bool, now: f64, wait: f64) -> RouteGateCommand {
        let (delay, duration) = if opened {
            (self.open_delay, self.open_seconds)
        } else {
            (self.close_delay, self.close_seconds)
        };
        let starts_at = now + delay;
        let ends_at = starts_at + duration;
        let collision_at = if opened && wait > 0.0 {
            ends_at.min(now + wait)
        } else {
            ends_at
        };
        RouteGateCommand {
            opened,
            starts_at,
            ends_at,
            collision_at,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct DynamicBarrierRegistry {
    pub entries: Vec<BarrierEntry>,
    pub gate_commands: BTreeMap<u32, RouteGateCommand>,
    pub debris_started: BTreeMap<u32, f64>,
    pub debris_seconds: f64,
}

impl DynamicBarrierRegistry {
    pub fn new(entries: Vec<BarrierEntry>) -> Self {
        Self {
            entries,
            gate_commands: BTreeMap::new(),
            debris_started: BTreeMap::new(),
            debris_seconds: 3.0,
        }
    }

    pub fn door_open(&self, entity: u32, stage: usize, is_active_or_unlocked: bool) -> bool {
        if let Some(cmd) = self.gate_commands.get(&entity) {
            return cmd.opened;
        }
        let Some(entry) = self.entries.iter().find(|e| e.entity == entity) else {
            return false;
        };
        match entry.kind {
            BarrierKind::CheckpointGate | BarrierKind::GateDebris => false,
            BarrierKind::Door => {
                if !is_active_or_unlocked || entry.payload_unlock {
                    return false;
                }
                entry
                    .rooms
                    .iter()
                    .any(|&(room_stage, _)| room_stage <= stage)
            }
            _ => false,
        }
    }

    pub fn door_passable(
        &self,
        entity: u32,
        now: f64,
        stage: usize,
        is_active_or_unlocked: bool,
    ) -> bool {
        if let Some(cmd) = self.gate_commands.get(&entity) {
            return cmd.opened == (now >= cmd.collision_at);
        }
        self.door_open(entity, stage, is_active_or_unlocked)
    }

    pub fn request_route_gate(&mut self, tag: u32, opened: bool, now: f64, wait: f64) -> bool {
        let mut changed = false;
        for entry in &self.entries {
            if entry.gate_tag != Some(tag) {
                continue;
            }
            let currently_open = self
                .gate_commands
                .get(&entry.entity)
                .is_some_and(|c| c.opened);
            if currently_open == opened {
                continue;
            }
            self.gate_commands
                .insert(entry.entity, entry.door_command(opened, now, wait));
            changed = true;
        }
        changed
    }

    pub fn door_state(
        &self,
        entity: u32,
        now: f64,
        stage: usize,
        is_active_or_unlocked: bool,
        previous: Option<&DoorState>,
    ) -> DoorState {
        let entry = self.entries.iter().find(|e| e.entity == entity);
        if let Some(&started) = self.debris_started.get(&entity) {
            let ends = started + self.debris_seconds;
            if now < ends {
                return DoorState {
                    pose: DoorPose::Opening,
                    states: BTreeSet::from([DOOR_OPEN_ANIM]),
                    starts_at: started,
                    ends_at: ends,
                    timed: true,
                    debris_played: false,
                };
            }
            return DoorState {
                pose: DoorPose::Open,
                states: BTreeSet::from([DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS]),
                starts_at: started,
                ends_at: ends,
                timed: true,
                debris_played: false,
            };
        }

        if let Some(cmd) = self.gate_commands.get(&entity) {
            if now < cmd.starts_at {
                return DoorState {
                    pose: if cmd.opened {
                        DoorPose::Closed
                    } else {
                        DoorPose::Open
                    },
                    states: previous.map(|p| p.states.clone()).unwrap_or_default(),
                    starts_at: 0.0,
                    ends_at: 0.0,
                    timed: false,
                    debris_played: previous.is_some_and(|p| p.debris_played),
                };
            }
            let playing = now < cmd.ends_at;
            let (pose, clip, alias) = if cmd.opened {
                (
                    if playing {
                        DoorPose::Opening
                    } else {
                        DoorPose::Open
                    },
                    DOOR_OPEN_ANIM,
                    DOOR_OPEN_ALIAS,
                )
            } else {
                (
                    if playing {
                        DoorPose::Closing
                    } else {
                        DoorPose::Closed
                    },
                    DOOR_CLOSE_ANIM,
                    DOOR_CLOSE_ALIAS,
                )
            };
            let mut states = BTreeSet::from([clip]);
            if !playing {
                states.insert(alias);
            }
            let debris_played = previous.is_some_and(|p| p.debris_played)
                || (entry.is_some_and(|e| e.gate_debris)
                    && cmd.opened
                    && now >= cmd.ends_at + GATE_DEBRIS_DELAY);
            return DoorState {
                pose,
                states,
                starts_at: cmd.starts_at,
                ends_at: cmd.ends_at,
                timed: true,
                debris_played,
            };
        }

        let opened = self.door_open(entity, stage, is_active_or_unlocked);
        if !opened {
            return DoorState {
                pose: DoorPose::Closed,
                states: BTreeSet::from([DOOR_CLOSE_ANIM, DOOR_CLOSE_ALIAS]),
                starts_at: 0.0,
                ends_at: 0.0,
                timed: false,
                debris_played: false,
            };
        }
        DoorState {
            pose: DoorPose::Open,
            states: BTreeSet::from([DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS]),
            starts_at: 0.0,
            ends_at: 0.0,
            timed: false,
            debris_played: false,
        }
    }

    pub fn room_barriers(&self, stage: usize, team: u8, active: bool) -> BTreeSet<u32> {
        if !active {
            return BTreeSet::new();
        }
        self.entries
            .iter()
            .filter(|e| e.kind == BarrierKind::TeamBarrier && e.rooms.contains(&(stage, team)))
            .map(|e| e.entity)
            .collect()
    }

    pub fn room_visuals(&self, stage: usize, team: u8, active: bool) -> BTreeSet<u32> {
        if !active {
            return BTreeSet::new();
        }
        self.entries
            .iter()
            .filter(|e| {
                matches!(
                    e.kind,
                    BarrierKind::TeamBarrier | BarrierKind::Overlay | BarrierKind::RestrictedSign
                ) && e.rooms.iter().any(|&(s, _)| s == stage)
                    && (e.viewers.is_empty() && e.rooms.contains(&(stage, team))
                        || e.viewers.contains(&team))
            })
            .map(|e| e.entity)
            .collect()
    }

    /// Build a temporary `ContactWorld` containing all currently active barrier/door shapes.
    pub fn active_contact_world(
        &self,
        now: f64,
        stage: usize,
        active: bool,
    ) -> Result<ContactWorld, ContactWorldError> {
        let mut shapes = Vec::new();
        for entry in &self.entries {
            let include = match entry.kind {
                BarrierKind::Door | BarrierKind::CheckpointGate | BarrierKind::GateDebris => {
                    !self.door_passable(entry.entity, now, stage, active)
                }
                BarrierKind::TeamBarrier | BarrierKind::Overlay => {
                    active && entry.rooms.iter().any(|&(s, _)| s == stage)
                }
                BarrierKind::RestrictedSign => false,
            };
            if include {
                for shape in &entry.shapes {
                    match *shape {
                        BarrierShape::Sphere {
                            center,
                            radius,
                            mask,
                            translation,
                            rotation,
                        } => {
                            shapes.push(StaticShape::new(
                                StaticGeometry::Round {
                                    shape: RoundShape::Sphere(Sphere { center, radius }),
                                    mask,
                                },
                                Some(ContactFrame {
                                    translation,
                                    rotation,
                                }),
                            )?);
                        }
                    }
                }
            }
        }
        ContactWorld::new(shapes)
    }
}

// ---------------------------------------------------------------------------
// 5. RoomVoiceRouter
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct VoiceCue {
    pub stimulus: u32,
    pub line: u32,
}

impl VoiceCue {
    pub const fn new(stimulus: u32, line: u32) -> Self {
        Self { stimulus, line }
    }

    pub const fn stimulus_guid(self) -> u64 {
        0x0EE0_0000_0000_0000 | (self.stimulus as u64)
    }

    pub const fn line_guid(self) -> u64 {
        0x0760_0000_0000_0000 | (self.line as u64)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct QueuedVoiceFollowUp {
    pub ordinal: u64,
    pub delay: f64,
    pub team: u8,
    pub cue: VoiceCue,
    pub due: Option<f64>,
    pub rest: VecDeque<VoiceCue>,
}

/// Duration-gated sequential voice router shared by mode announcers and hero bodies.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RoomVoiceRouter {
    pub follow_up: Option<QueuedVoiceFollowUp>,
}

impl RoomVoiceRouter {
    pub fn clear(&mut self) {
        self.follow_up = None;
    }

    pub fn enqueue(&mut self, ordinal: u64, delay: f64, team: u8, cues: &[VoiceCue]) {
        if cues.len() <= 1 {
            self.follow_up = None;
            return;
        }
        let mut rest = VecDeque::new();
        for &c in &cues[2..] {
            rest.push_back(c);
        }
        self.follow_up = Some(QueuedVoiceFollowUp {
            ordinal,
            delay,
            team,
            cue: cues[1],
            due: None,
            rest,
        });
    }

    pub fn poll(
        &mut self,
        now: f64,
        team: u8,
        reliable_delivered: impl Fn(u64) -> bool,
    ) -> Option<VoiceCue> {
        let pending = self.follow_up.as_mut()?;
        if pending.team != team {
            self.follow_up = None;
            return None;
        }
        if pending.due.is_none() {
            if reliable_delivered(pending.ordinal) {
                pending.due = Some(now + pending.delay);
            }
            return None;
        }
        if now >= pending.due? {
            let mut taken = self.follow_up.take()?;
            let cue = taken.cue;
            if let Some(next_cue) = taken.rest.pop_front() {
                taken.cue = next_cue;
                taken.due = None;
                self.follow_up = Some(taken);
            }
            return Some(cue);
        }
        None
    }
}

// ---------------------------------------------------------------------------
// Math Helpers
// ---------------------------------------------------------------------------

fn cubic_bezier(p0: [f64; 3], p1: [f64; 3], p2: [f64; 3], p3: [f64; 3], t: f64) -> [f64; 3] {
    let u = 1.0 - t;
    let uu = u * u;
    let tt = t * t;
    let uuu = uu * u;
    let ttt = tt * t;
    let c1 = 3.0 * uu * t;
    let c2 = 3.0 * u * tt;
    [
        uuu * p0[0] + c1 * p1[0] + c2 * p2[0] + ttt * p3[0],
        uuu * p0[1] + c1 * p1[1] + c2 * p2[1] + ttt * p3[1],
        uuu * p0[2] + c1 * p1[2] + c2 * p2[2] + ttt * p3[2],
    ]
}

fn dist3(a: [f64; 3], b: [f64; 3]) -> f64 {
    let dx = b[0] - a[0];
    let dy = b[1] - a[1];
    let dz = b[2] - a[2];
    (dx * dx + dy * dy + dz * dz).sqrt()
}

#[cfg(test)]
mod tests;
