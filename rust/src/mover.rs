//! Server-side movement for an authored 104319 character capsule.

use std::{collections::BTreeMap, sync::Arc};

use crate::{
    collision_index::TriangleIndex,
    commands::Command,
    contact_body::CharacterBody,
    contact_cache::CapsulePose,
    contact_gjk::PointSetSize,
    contact_hull::HullContactError,
    contact_solver::clip_velocity,
    contact_world::ContactWorld,
    mover_ground::{GroundProbe, NOT_GROUND, ProbePose, pogo, standing_body},
    mover_kinematics::{Ground, MotionInput, Poses, VelocityStep, poses},
    mover_modifiers::{Modifier, ModifierKey, ModifierTimeline},
    mover_posture::{
        CROUCHED, IN_AIR, JUMPED, NOT_GROUND_UNDER, Posture, PostureStep, TOUCHING, snap,
    },
    mover_settings::MoverSettings,
    script_init::{
        MoveOutcome, MoveToKey, Relocation, ScriptedMotion, SweptDisplacement, VelocityAxis,
    },
    world::{Movement, NO_INPUT},
};

const ABYSS: f64 = 100.0;
const EPSILON: f64 = 1.1754943508222875e-36;

/// Authored velocity edits, applied once before the next collision-aware pose.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum VelocityChange {
    Delta([f64; 3]),
    Impulse([f64; 3]),
    HorizontalCap(f64),
    VerticalImpulse {
        vertical: f64,
        delta: [f64; 3],
    },
    SetVelocity([f64; 3]),
    SetHorizontalVelocity([f64; 2]),
    SetAxisSpeed {
        axis: VelocityAxis,
        value: f64,
    },
    CapAxisSpeed {
        axis: VelocityAxis,
        max: f64,
    },
    ScaleAxisSpeed {
        axis: VelocityAxis,
        factor: f64,
    },
    ClampAxisSpeed {
        axis: VelocityAxis,
        min: f64,
        max: f64,
    },
}

impl VelocityChange {
    /// Apply this edit and report a positive launch that should leave the ground.
    pub(crate) fn apply(self, velocity: &mut [f64; 3]) -> bool {
        self.apply_with_jump(velocity, 6.0)
    }

    pub(crate) fn apply_with_jump(self, velocity: &mut [f64; 3], jump_speed: f64) -> bool {
        match self {
            Self::Delta(delta) => {
                for (axis, delta) in velocity.iter_mut().zip(delta) {
                    *axis = f64::from((*axis as f32) + delta as f32);
                }
            }
            Self::Impulse(delta) => {
                let mut next = *velocity;
                Self::Delta(delta).apply(&mut next);
                if next.iter().all(|axis| axis.is_finite()) {
                    *velocity = next;
                    return delta[1] > 0.0 && next[1] > 0.0;
                }
            }
            Self::HorizontalCap(cap) => {
                let speed = velocity[0].hypot(velocity[2]);
                if speed > cap && speed > 0.0 {
                    for axis in [0, 2] {
                        velocity[axis] = f64::from((velocity[axis] * cap / speed) as f32);
                    }
                }
            }
            Self::VerticalImpulse { vertical, delta } => {
                let mut next = *velocity;
                next[1] = vertical;
                Self::Delta(delta).apply(&mut next);
                if next.iter().all(|axis| axis.is_finite()) {
                    *velocity = next;
                    return next[1] > 0.0;
                }
            }
            Self::SetVelocity(target) => {
                let next = target.map(|axis| f64::from(axis as f32));
                if next.iter().all(|axis| axis.is_finite()) {
                    *velocity = next;
                    return next[1] > 0.0;
                }
            }
            Self::SetHorizontalVelocity([vx, vz]) => {
                let nx = f64::from(vx as f32);
                let nz = f64::from(vz as f32);
                if nx.is_finite() && nz.is_finite() {
                    velocity[0] = nx;
                    velocity[2] = nz;
                }
            }
            Self::SetAxisSpeed { axis, value } => match axis {
                VelocityAxis::All => {
                    let speed = velocity[0].hypot(velocity[1]).hypot(velocity[2]);
                    if speed > 1e-6 {
                        let scale = value / speed;
                        for comp in velocity.iter_mut() {
                            *comp = f64::from((*comp * scale) as f32);
                        }
                    }
                }
                VelocityAxis::Horizontal => {
                    let speed = velocity[0].hypot(velocity[2]);
                    if speed > 1e-6 {
                        let scale = value / speed;
                        for idx in [0, 2] {
                            velocity[idx] = f64::from((velocity[idx] * scale) as f32);
                        }
                    }
                }
                VelocityAxis::Vertical => {
                    velocity[1] = f64::from(value as f32);
                    return velocity[1] > 0.0;
                }
                VelocityAxis::VerticalUp => {
                    // Retail `STU_9AF0FA80` with `m_A9561CA0 == 3` encodes a jump_speed
                    // multiplier (e.g. Genji Double Jump 1.5x, Lucio wallride hop 1.15x).
                    let next = f64::from((value * jump_speed) as f32);
                    if next > velocity[1] {
                        velocity[1] = next;
                        return velocity[1] > 0.0;
                    }
                }
            },
            Self::CapAxisSpeed { axis, max } => match axis {
                VelocityAxis::All => {
                    let speed = velocity[0].hypot(velocity[1]).hypot(velocity[2]);
                    if speed > max && speed > 0.0 {
                        let scale = max / speed;
                        for comp in velocity.iter_mut() {
                            *comp = f64::from((*comp * scale) as f32);
                        }
                    }
                }
                VelocityAxis::Horizontal => {
                    Self::HorizontalCap(max).apply(velocity);
                }
                VelocityAxis::Vertical => {
                    if velocity[1].abs() > max {
                        velocity[1] = f64::from((velocity[1].signum() * max) as f32);
                    }
                }
                VelocityAxis::VerticalUp => {
                    if velocity[1] > max {
                        velocity[1] = f64::from(max as f32);
                    }
                }
            },
            Self::ScaleAxisSpeed { axis, factor } => match axis {
                VelocityAxis::All => {
                    for comp in velocity.iter_mut() {
                        *comp = f64::from((*comp * factor) as f32);
                    }
                }
                VelocityAxis::Horizontal => {
                    for idx in [0, 2] {
                        velocity[idx] = f64::from((velocity[idx] * factor) as f32);
                    }
                }
                VelocityAxis::Vertical | VelocityAxis::VerticalUp => {
                    velocity[1] = f64::from((velocity[1] * factor) as f32);
                }
            },
            Self::ClampAxisSpeed { axis, min, max } => match axis {
                VelocityAxis::All => {
                    let speed = velocity[0].hypot(velocity[1]).hypot(velocity[2]);
                    if speed > 1e-6 {
                        let clamped = speed.clamp(min, max);
                        if (clamped - speed).abs() > 1e-9 {
                            let scale = clamped / speed;
                            for comp in velocity.iter_mut() {
                                *comp = f64::from((*comp * scale) as f32);
                            }
                        }
                    }
                }
                VelocityAxis::Horizontal => {
                    let speed = velocity[0].hypot(velocity[2]);
                    if speed > 1e-6 {
                        let clamped = speed.clamp(min, max);
                        if (clamped - speed).abs() > 1e-9 {
                            let scale = clamped / speed;
                            for idx in [0, 2] {
                                velocity[idx] = f64::from((velocity[idx] * scale) as f32);
                            }
                        }
                    }
                }
                VelocityAxis::Vertical => {
                    velocity[1] = f64::from(velocity[1].clamp(min, max) as f32);
                }
                VelocityAxis::VerticalUp => {
                    if velocity[1] > 0.0 {
                        velocity[1] = f64::from(velocity[1].clamp(min, max) as f32);
                    }
                }
            },
        }
        false
    }

    pub(crate) fn valid(self) -> bool {
        match self {
            Self::Delta(delta) | Self::Impulse(delta) | Self::SetVelocity(delta) => {
                delta.iter().all(|value| (*value as f32).is_finite())
            }
            Self::SetHorizontalVelocity(delta) => {
                delta.iter().all(|value| (*value as f32).is_finite())
            }
            Self::HorizontalCap(cap) | Self::CapAxisSpeed { max: cap, .. } => {
                (cap as f32).is_finite() && cap >= 0.0
            }
            Self::VerticalImpulse { vertical, delta } => {
                (vertical as f32).is_finite()
                    && Self::Delta(delta).valid()
                    && (vertical as f32 + delta[1] as f32).is_finite()
            }
            Self::SetAxisSpeed { value, .. } => (value as f32).is_finite(),
            Self::ScaleAxisSpeed { factor, .. } => (factor as f32).is_finite() && factor >= 0.0,
            Self::ClampAxisSpeed { min, max, .. } => {
                (min as f32).is_finite() && (max as f32).is_finite() && min <= max
            }
        }
    }
}

/// Project the owner's current movement record into the authored 13-bit sample.
pub(crate) fn sample_movement_flags(pose: &Movement) -> Option<u16> {
    // These modes require nested native movement records. Regular Rust poses
    // cannot enter them or enqueue the scheduled transition represented by bit 10.
    if pose.flags & (0x20 | 0x80) != 0 {
        return None;
    }
    let [x, y, z] = pose.velocity.map(|value| value as f32);
    if ![x, y, z].iter().all(|value| value.is_finite()) {
        return None;
    }
    let airborne = pose.flags & IN_AIR != 0;
    let vertical_threshold = 100.0 * f32::EPSILON;
    let moving = (x * x + y * y) + z * z > 10.0 * f32::EPSILON;
    Some(
        u16::from(airborne)
            | u16::from(!airborne) << 1
            | u16::from(y > vertical_threshold) << 2
            | u16::from(y < -vertical_threshold) << 3
            | u16::from(moving) << 4
            | u16::from(pose.throttles != [0; 2]) << 5
            | u16::from(pose.flags & CROUCHED != 0) << 6
            | u16::from(pose.flags & NOT_GROUND_UNDER != 0) << 9
            | u16::from(pose.flags & JUMPED != 0) << 11,
    )
}

pub struct Mover {
    base: MoverSettings,
    category: u16,
    probe: GroundProbe,
    body: CharacterBody,
    posture: Posture,
    modifiers: ModifierTimeline,
    cinematic_aim: Option<[i32; 2]>,
    authored_facing: Option<[i32; 2]>,
    move_request: Option<([f64; 3], f64)>,
    player_dead: bool,
    player_stunned: bool,
    stop_for_stun: Option<u32>,
    velocity_changes: Vec<(u32, VelocityChange)>,
    scripted_motion: Option<(u32, ScriptedMotion)>,
    scripted_motion_frame: Option<u32>,
    owner_move: Option<(MoveToKey, [f64; 3], f64)>,
    owner_move_result: Option<(u32, MoveToKey, MoveOutcome)>,
    /// Whether the last pose ran the owner-move drive AND moved the pose
    /// (reference: the owner's correction window opens on a committed,
    /// feet-moving drive pose only — a dt-discarded or fully seated drive
    /// is no scripted motion). Set by `move_body`, consumed by `step`.
    drive_pose_moved: bool,
    actor_contact: bool,
    corrected_input: bool,
    ball_roll_speed: Option<f64>,
    last_command: Option<Command>,
    last_input: Option<u32>,
    pose: Movement,
    #[cfg(feature = "lua-mods")]
    mod_gravity_scale: f64,
    #[cfg(feature = "lua-mods")]
    mod_speed_scale: f64,
    #[cfg(any(test, feature = "inspector"))]
    last_ground_probe: Option<crate::game_debug::DebugGroundProbe>,
    #[cfg(any(test, feature = "inspector"))]
    last_contact_normals: Vec<crate::game_debug::DebugContactNormal>,
}

impl Mover {
    pub fn new(
        base: MoverSettings,
        world: Arc<ContactWorld>,
        position: [f64; 3],
        yaw: i32,
        category: u16,
    ) -> Result<Self, PointSetSize> {
        let position = position.map(snap);
        Ok(Self {
            base,
            category,
            probe: GroundProbe::new(&base)?,
            body: standing_body(&base, world, position, category),
            posture: Posture::new(&base),
            modifiers: ModifierTimeline::default(),
            cinematic_aim: None,
            authored_facing: None,
            move_request: None,
            player_dead: false,
            player_stunned: false,
            stop_for_stun: None,
            velocity_changes: Vec::new(),
            scripted_motion: None,
            scripted_motion_frame: None,
            owner_move: None,
            owner_move_result: None,
            drive_pose_moved: false,
            actor_contact: false,
            corrected_input: false,
            ball_roll_speed: None,
            last_command: None,
            last_input: None,
            pose: Movement {
                position,
                yaw,
                gravity: base.gravity_scale,
                ..Movement::default()
            },
            #[cfg(feature = "lua-mods")]
            mod_gravity_scale: 1.0,
            #[cfg(feature = "lua-mods")]
            mod_speed_scale: 1.0,
            #[cfg(any(test, feature = "inspector"))]
            last_ground_probe: None,
            #[cfg(any(test, feature = "inspector"))]
            last_contact_normals: Vec::new(),
        })
    }

    pub fn pose(&self) -> Movement {
        self.pose
    }

    /// The team proxy collision category this mover queries map geometry with.
    pub fn collision_category(&self) -> u16 {
        self.category
    }

    pub(crate) fn eye_height(&self) -> f64 {
        self.base.eye_height(self.crouched())
    }

    pub(crate) fn contact_capsule(&self) -> CapsulePose {
        self.body.capsule_pose()
    }

    pub(crate) fn set_breakables(
        &mut self,
        breakables: Arc<crate::collision_scene::BreakableState>,
    ) {
        self.body.set_breakables(breakables);
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn set_mod_scales(&mut self, gravity_scale: f64, speed_scale: f64) {
        if gravity_scale.is_finite() && gravity_scale > 0.0 {
            self.mod_gravity_scale = gravity_scale.clamp(0.05, 10.0);
            self.pose.gravity = self.base.gravity_scale * self.mod_gravity_scale;
        }
        if speed_scale.is_finite() && speed_scale > 0.0 {
            self.mod_speed_scale = speed_scale.clamp(0.1, 10.0);
        }
    }

    #[cfg(all(test, feature = "lua-mods"))]
    pub(crate) fn mod_scales(&self) -> (f64, f64) {
        (self.mod_gravity_scale, self.mod_speed_scale)
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_ground_probe(
        &self,
        entity: u32,
    ) -> Option<crate::game_debug::DebugGroundProbe> {
        self.last_ground_probe.map(|mut probe| {
            probe.entity = entity;
            probe
        })
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_contact_normals(
        &self,
        entity: u32,
    ) -> impl Iterator<Item = crate::game_debug::DebugContactNormal> + '_ {
        self.last_contact_normals
            .iter()
            .copied()
            .map(move |mut contact| {
                contact.entity = entity;
                contact
            })
    }

    pub(crate) fn capsule(&self) -> (f64, f64) {
        if self.crouched() {
            (self.base.crouch_height, self.base.crouch_radius)
        } else {
            (self.base.stand_height, self.base.stand_radius)
        }
    }

    pub(crate) fn crouched(&self) -> bool {
        self.posture.crouched()
    }

    pub(crate) fn spring_offset(&self) -> f32 {
        self.posture.spring_offset()
    }

    pub fn track_modifiers(&mut self, frame: u32, active: &BTreeMap<ModifierKey, Modifier>) {
        self.modifiers.track(frame, active);
    }

    pub(crate) fn character_collision_for_snapshot(&self, admitted_at: u32) -> bool {
        // Room ticks and input frames can diverge. Use the next frame on the
        // capsule's own clock to include newly started or retired intervals.
        let frame = self.pose.frame.unwrap_or(admitted_at).wrapping_add(1);
        self.character_collision_at(frame)
    }

    fn character_collision_at(&self, frame: u32) -> bool {
        self.modifiers.at(frame).character_collision(&self.base)
    }

    pub(crate) fn set_cinematic_aim(&mut self, aim: Option<[i32; 2]>) {
        self.cinematic_aim = aim;
    }

    pub(crate) fn set_authored_facing(&mut self, facing: Option<[i32; 2]>) {
        self.authored_facing = facing;
    }

    pub(crate) fn set_move_request(&mut self, request: Option<([f64; 3], f64)>) {
        self.move_request = request;
    }

    pub(crate) fn queue_velocity_change(&mut self, frame: u32, change: VelocityChange) {
        Self::push_merged_velocity_change(&mut self.velocity_changes, frame, change);
    }

    pub(crate) fn push_merged_velocity_change(
        queue: &mut Vec<(u32, VelocityChange)>,
        frame: u32,
        change: VelocityChange,
    ) {
        if !change.valid() {
            return;
        }
        if let VelocityChange::Impulse(incoming) = change {
            if let Some((_, VelocityChange::Impulse(existing))) = queue
                .iter_mut()
                .find(|(due, queued)| *due == frame && matches!(queued, VelocityChange::Impulse(_)))
            {
                for axis in 0..3 {
                    if incoming[axis].abs() > existing[axis].abs() {
                        existing[axis] = incoming[axis];
                    }
                }
                return;
            }
        }
        queue.push((frame, change));
    }

    /// Keep the first request until its first actual pose at or after `frame`.
    pub(crate) fn queue_scripted_motion(&mut self, frame: u32, motion: ScriptedMotion) -> bool {
        if self.scripted_motion.is_some() {
            return false;
        }
        self.scripted_motion = Some((frame, motion));
        true
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn force_scripted_motion(&mut self, frame: u32, motion: ScriptedMotion) {
        self.scripted_motion = Some((frame, motion));
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn set_mod_facing(&mut self, yaw: i32, pitch: i32) {
        self.pose.yaw = yaw;
        self.pose.pitch = pitch;
    }

    #[cfg(test)]
    pub(crate) fn queue_swept_displacement(
        &mut self,
        frame: u32,
        displacement: SweptDisplacement,
    ) -> bool {
        self.queue_scripted_motion(frame, ScriptedMotion::Sweep(displacement))
    }

    /// Drop queued and continuous script controls while keeping the resolved
    /// pose, current momentum and queued external impulses.
    pub(crate) fn clear_pending_script_motion(&mut self) {
        self.velocity_changes
            .retain(|(_, change)| matches!(change, VelocityChange::Impulse(_)));
        self.scripted_motion = None;
        self.owner_move = None;
        self.move_request = None;
        self.ball_roll_speed = None;
    }

    pub(crate) fn clear_pending_motion(&mut self) {
        self.velocity_changes.clear();
        self.stop_for_stun = None;
        self.clear_pending_script_motion();
    }

    pub(crate) fn has_pending_script_motion(&self) -> bool {
        self.scripted_motion.is_some()
    }

    /// Whether this successfully completed pose moved through a script request.
    pub(crate) fn scripted_motion_at(&self, frame: u32) -> bool {
        self.scripted_motion_frame == Some(frame)
    }

    pub(crate) fn set_owner_move(&mut self, request: Option<(MoveToKey, [f64; 3], f64)>) {
        self.owner_move = request;
    }

    pub(crate) fn owner_move_result(&self, frame: u32) -> Option<(MoveToKey, MoveOutcome)> {
        self.owner_move_result
            .filter(|(posed, _, _)| *posed == frame)
            .map(|(_, key, outcome)| (key, outcome))
    }

    pub(crate) fn needs_owner_correction_at(&self, frame: u32) -> bool {
        self.scripted_motion_at(frame)
            || (self.pose.frame == Some(frame) && (self.actor_contact || self.corrected_input))
    }

    pub(crate) fn set_ball_roll_speed(&mut self, speed: Option<f64>) {
        self.ball_roll_speed = speed.filter(|s| s.is_finite() && *s > 0.0);
    }

    /// Apply a soft horizontal separation nudge when overlapping an allied actor.
    pub(crate) fn apply_ally_separation(
        &mut self,
        delta: [f64; 2],
        blockers: &[CapsulePose],
        dt: f64,
    ) {
        if delta == [0.0; 2]
            || !delta[0].is_finite()
            || !delta[1].is_finite()
            || dt <= 0.0
            || !dt.is_finite()
            || !self
                .pose
                .frame
                .is_some_and(|frame| self.character_collision_at(frame))
        {
            return;
        }
        let start = self.body.position();
        let mut staged = self.body.fresh_at(start);
        let Ok(moved) = staged.move_capsule_against(
            [delta[0] as f32, 0.0, delta[1] as f32],
            dt as f32,
            blockers,
        ) else {
            return;
        };
        let feet = std::array::from_fn(|axis| {
            self.pose.position[axis] + f64::from(moved.position[axis]) - f64::from(start[axis])
        })
        .map(snap);
        if feet == self.pose.position {
            return;
        }
        staged.teleport(self.capsule_origin(feet));
        self.body = staged;
        self.pose.position = feet;
        self.actor_contact = true;
    }

    /// Simulate the newest command and any reconstructible poses before it.
    /// The callback can advance the body's statescript at each posed frame.
    pub fn step(
        &mut self,
        rays: &TriangleIndex,
        newest: Command,
        dt: f64,
        after_pose: impl FnMut(&mut Self, Command),
    ) -> Result<(), HullContactError> {
        self.step_against(rays, newest, dt, &[], after_pose)
    }

    /// Use one frozen actor-contact slice for this command's recovered poses.
    pub fn step_against(
        &mut self,
        rays: &TriangleIndex,
        newest: Command,
        dt: f64,
        blockers: &[CapsulePose],
        after_pose: impl FnMut(&mut Self, Command),
    ) -> Result<(), HullContactError> {
        let mut pending = self.pending_poses(newest);
        self.step_pending(rays, &mut pending, dt, usize::MAX, || blockers, after_pose)?;
        Ok(())
    }

    pub(crate) fn pending_poses(&self, newest: Command) -> Poses {
        poses(self.last_command, newest)
    }

    pub(crate) fn set_player_dead(&mut self, dead: bool) {
        self.player_dead = dead;
    }

    pub(crate) fn stun(&mut self, frame: u32) {
        self.player_stunned = true;
        self.clear_pending_script_motion();
        self.stop_for_stun = Some(self.stop_for_stun.map_or(frame, |due| due.min(frame)));
    }

    pub(crate) fn clear_stun(&mut self) {
        self.player_stunned = false;
    }

    pub(crate) fn is_stunned(&self) -> bool {
        self.player_stunned
    }

    pub(crate) fn step_pending<B: AsRef<[CapsulePose]>>(
        &mut self,
        rays: &TriangleIndex,
        pending: &mut Poses,
        dt: f64,
        limit: usize,
        mut actor_capsules: impl FnMut() -> B,
        mut after_pose: impl FnMut(&mut Self, Command),
    ) -> Result<usize, HullContactError> {
        let blockers = if limit != 0 && pending.len() != 0 {
            Some(actor_capsules())
        } else {
            None
        };
        let blockers = blockers.as_ref().map_or(&[][..], AsRef::as_ref);
        let mut stepped = 0;
        let mut last_stepped = None;
        for mut command in pending.by_ref().take(limit) {
            let corrected_input = self.player_stunned
                && (command.right != 0
                    || command.forward != 0
                    || command.buttons & (crate::commands::CROUCH | crate::commands::JUMP) != 0);
            if self.player_dead || self.player_stunned {
                command = Command {
                    frame: command.frame,
                    yaw: command.yaw,
                    pitch: command.pitch,
                    ..Command::default()
                };
            }
            last_stepped = Some(command);
            if let Some(aim) = self.cinematic_aim.or(self.authored_facing) {
                [command.yaw, command.pitch] = aim;
            }
            self.step_pose(rays, command, dt, blockers)?;
            self.corrected_input = corrected_input;
            after_pose(self, command);
            stepped += 1;
        }
        if let Some(command) = last_stepped.filter(|_| pending.len() == 0) {
            self.last_command = Some(command);
        }
        Ok(stepped)
    }

    fn step_pose(
        &mut self,
        rays: &TriangleIndex,
        command: Command,
        dt: f64,
        blockers: &[CapsulePose],
    ) -> Result<(), HullContactError> {
        let active = self.modifiers.at(command.frame);
        #[cfg(feature = "lua-mods")]
        let settings = {
            let mut settings = active.settings(&self.base);
            if (self.mod_gravity_scale - 1.0).abs() > 1e-6 {
                settings.gravity_scale *= self.mod_gravity_scale;
            }
            if (self.mod_speed_scale - 1.0).abs() > 1e-6 {
                settings.run_forward *= self.mod_speed_scale;
                settings.run_backward *= self.mod_speed_scale;
                settings.run_strafe *= self.mod_speed_scale;
                settings.crouch_forward *= self.mod_speed_scale;
                settings.crouch_backward *= self.mod_speed_scale;
                settings.crouch_strafe *= self.mod_speed_scale;
            }
            settings
        };
        #[cfg(not(feature = "lua-mods"))]
        let mut settings = active.settings(&self.base);
        #[cfg(feature = "lua-mods")]
        let mut settings = settings;
        if let Some(roll_speed) = self.ball_roll_speed {
            settings.run_forward = settings.run_forward.max(roll_speed);
            settings.run_backward = settings.run_backward.max(roll_speed);
            settings.run_strafe = settings.run_strafe.max(roll_speed);
        }
        let blockers = if settings.character_collision != 0 {
            blockers
        } else {
            &[]
        };
        self.scripted_motion_frame = None;
        self.owner_move_result = None;
        let mut scripted_motion_changed = false;
        let mut swept_actor_contact = false;
        if self
            .scripted_motion
            .is_some_and(|(frame, _)| frame <= command.frame)
        {
            // Consume once even if the solver rejects the attempt. Stage on a
            // fresh capsule so an error cannot separate the body from its feet.
            if let Some((_, motion)) = self.scripted_motion.take() {
                let before = self.pose.position;
                match motion {
                    ScriptedMotion::Sweep(displacement) => {
                        swept_actor_contact =
                            self.apply_swept_displacement(displacement, dt, blockers)?;
                    }
                    ScriptedMotion::Relocate(relocation) => {
                        self.apply_relocation(relocation, blockers)?;
                    }
                    ScriptedMotion::Teleport(relocation) => {
                        self.apply_teleport(relocation, blockers)?;
                    }
                }
                scripted_motion_changed = self.pose.position != before;
            }
        }
        let mut launched = false;
        let mut velocity_edited = false;
        if self
            .stop_for_stun
            .is_some_and(|frame| frame <= command.frame)
        {
            self.stop_for_stun = None;
            velocity_edited = self.pose.velocity[0] != 0.0 || self.pose.velocity[2] != 0.0;
            self.pose.velocity[0] = 0.0;
            self.pose.velocity[2] = 0.0;
            scripted_motion_changed |= velocity_edited;
        }
        self.velocity_changes.retain(|&(frame, change)| {
            if frame <= command.frame {
                let before = self.pose.velocity;
                launched |= change.apply_with_jump(&mut self.pose.velocity, settings.jump_speed);
                if self.pose.velocity[1] > 0.0 && before[1] <= 0.0 {
                    launched = true;
                }
                velocity_edited |= self.pose.velocity != before;
                scripted_motion_changed |=
                    !matches!(change, VelocityChange::Delta(_)) && self.pose.velocity != before;
                false
            } else {
                true
            }
        });
        let move_to_velocity = self.move_request.and_then(|(dest, speed)| {
            let delta = [
                dest[0] - self.pose.position[0],
                dest[1] - self.pose.position[1],
                dest[2] - self.pose.position[2],
            ];
            let dist = delta[0].hypot(delta[1]).hypot(delta[2]);
            if dist <= 0.1 || !dist.is_finite() || speed <= 0.0 {
                return None;
            }
            let step_speed = speed.min(dist / dt.max(1e-4));
            let scale = step_speed / dist;
            let hop_y = if dist > 2.2
                && (-0.25..0.75).contains(&delta[1])
                && self.posture.ground() == Ground::Standing
            {
                4.8
            } else {
                delta[1] * scale
            };
            Some([delta[0] * scale, hop_y, delta[2] * scale])
        });
        if let Some(mv) = move_to_velocity {
            self.pose.velocity = mv;
            if mv[1] > 0.0 {
                launched = true;
            }
            scripted_motion_changed = true;
        }
        if launched && self.pose.velocity[1] > 0.0 {
            self.posture.begin_velocity_launch();
        }
        let mut input = MotionInput::from(&command).with_modifiers(&active);
        // The authored direction has already been packed by the server. Keep
        // its pitch, including straight up or down, when it supplies the pose.
        if let (None, Some(_), Ok(pitch)) = (
            self.cinematic_aim,
            self.authored_facing,
            i16::try_from(command.pitch),
        ) {
            input.pitch = pitch;
            input.pitch_held = false;
        }
        let mut probe = self.probe.probe(
            rays,
            self.body.world(),
            &settings,
            ProbePose {
                feet: self.pose.position,
                velocity: self.pose.velocity,
                spring: self.posture.spring_offset(),
                crouched: self.posture.crouched(),
                airborne: self.posture.ground() != Ground::Standing,
            },
            dt,
            self.category,
        )?;
        // Allow standing solidly on top of an enemy actor's head capsule.
        let mut standing_on_actor = false;
        if !blockers.is_empty() && self.pose.velocity[1] <= 0.5 {
            let self_radius = if self.posture.crouched() {
                settings.crouch_radius
            } else {
                settings.stand_radius
            };
            let feet_y = self.pose.position[1];
            let mut best_crown: Option<f64> = None;
            for blocker in blockers {
                let b_top = f64::from(blocker.position[1] + blocker.top + blocker.radius);
                let dx = self.pose.position[0] - f64::from(blocker.position[0]);
                let dz = self.pose.position[2] - f64::from(blocker.position[2]);
                let min_dist = self_radius + f64::from(blocker.radius);
                if dx.hypot(dz) < min_dist
                    && feet_y >= b_top - 0.55
                    && feet_y <= b_top + settings.step_down + 0.15
                    && best_crown.is_none_or(|prev| b_top > prev)
                {
                    best_crown = Some(b_top);
                }
            }
            if let Some(crown_y) = best_crown {
                if probe.ground.is_none_or(|hit| crown_y > hit.y) {
                    probe.ground = Some(crate::collision_index::GroundHit {
                        y: crown_y,
                        normal: [0.0, 1.0, 0.0],
                        plane_offset: 0.0,
                        mask: self.category,
                        surface: 0,
                    });
                    standing_on_actor = true;
                }
            }
        }
        #[cfg(any(test, feature = "inspector"))]
        {
            let origin = self.pose.position;
            self.last_ground_probe = Some(crate::game_debug::DebugGroundProbe {
                tick: command.frame,
                entity: 0,
                origin,
                hit: probe.ground.map(|hit| [origin[0], hit.y, origin[2]]),
                normal: probe.ground.map(|hit| hit.normal),
                grounded: probe.ground.is_some(),
                non_ground_under: probe.non_ground_under,
            });
        }
        self.posture.advance(
            PostureStep {
                command: &command,
                base: &self.base,
                values: &settings,
                mods: &active,
                probe,
                dt,
                pitch_held: input.pitch_held,
            },
            &mut self.pose.position,
            &mut self.pose.velocity,
            |radius, top| self.body.try_resize_against(radius, top, blockers),
        )?;
        let ground = self.posture.ground();
        let velocity = if let Some(mv) = move_to_velocity {
            mv
        } else {
            VelocityStep {
                base: &self.base,
                settings: &settings,
                input,
                ground,
                crouched: self.posture.crouched(),
                dt,
            }
            .advance(self.pose.velocity)
        };
        if ground != Ground::Standing && move_to_velocity.is_none() {
            self.posture.apply_fall(settings.gravity_scale, dt);
        }
        let normal = probe.ground.map_or([0.0, 1.0, 0.0], |hit| hit.normal);
        // A pre-cast jump is history, not a jump request on the terminal pose.
        let allow_arrival_settle = settings.gravity_scale > 0.0
            && active.intent(
                crate::mover_modifiers::JUMP_INTENT,
                f64::from(u8::from(command.buttons & crate::commands::JUMP != 0)),
            ) <= 0.5
            && !velocity_edited
            && !scripted_motion_changed;
        let (vertical, actor_contact) = self.move_body(
            rays,
            velocity,
            normal,
            dt,
            command.frame,
            blockers,
            &settings,
            allow_arrival_settle,
            standing_on_actor,
        )?;
        // Reference parity: the owner's correction window opens on a pose
        // whose dt-valid drive actually moved the feet, not on every frame
        // with a pending request (a dt-discarded or fully-seated drive runs
        // ordinary motion, not scripted motion).
        scripted_motion_changed |= self.drive_pose_moved;
        self.posture.finish_fall(settings.gravity_scale, vertical);

        if input.throttles != [0; 2] {
            self.last_input = Some(command.frame);
            self.pose.input_throttles = input.throttles;
        }
        self.pose.frame = Some(command.frame);
        self.pose.input_frame = match self.last_input {
            Some(frame) if frame == command.frame => None,
            frame => Some(frame.unwrap_or(NO_INPUT)),
        };
        self.pose.throttles = input.throttles;
        self.pose.yaw = input.yaw;
        self.pose.pitch = i32::from(input.pitch);
        self.pose.flags = self.posture.flags;
        self.pose.air_ticks = self.posture.air_ticks;
        self.pose.crouch_frame = self.posture.crouch_end;
        self.pose.gravity = settings.gravity_scale;
        self.pose.spring = self.posture.spring_steps().map(i32::from);
        self.pose.spring_speed = f64::from(self.posture.spring_speed());
        self.pose.fall = self.posture.fall;
        self.scripted_motion_frame = scripted_motion_changed.then_some(command.frame);
        // A clipped sweep can leave the snapped pose unchanged while still
        // needing to correct the owner's prediction of that displacement.
        self.actor_contact = swept_actor_contact || actor_contact || standing_on_actor;
        Ok(())
    }

    fn apply_swept_displacement(
        &mut self,
        displacement: SweptDisplacement,
        dt: f64,
        actor_capsules: &[CapsulePose],
    ) -> Result<bool, HullContactError> {
        let start = self.body.position();
        let mut staged = self.body.fresh_at(start);
        let movement =
            staged.move_capsule_against(displacement.delta(), dt as f32, actor_capsules)?;
        let actor_contact = movement.actor_contact;
        let end = movement.position;
        let feet = std::array::from_fn(|axis| {
            self.pose.position[axis] + f64::from(end[axis]) - f64::from(start[axis])
        })
        .map(snap);
        staged.teleport(self.capsule_origin(feet));
        self.body = staged;
        self.pose.position = feet;
        Ok(actor_contact)
    }

    fn apply_relocation(
        &mut self,
        relocation: Relocation,
        blockers: &[CapsulePose],
    ) -> Result<(), HullContactError> {
        let Some(feet) = relocation
            .destination_from(self.pose.position)
            .map(|feet| feet.map(snap))
        else {
            return Ok(());
        };
        let delta = std::array::from_fn(|axis| feet[axis] - self.pose.position[axis]);
        if SweptDisplacement::new(delta).is_none() {
            return Ok(());
        }
        let origin = self.capsule_origin(feet);
        if self.body.relocate_if_clear(origin, blockers)? {
            self.pose.position = feet;
        }
        Ok(())
    }

    fn apply_teleport(
        &mut self,
        relocation: Relocation,
        blockers: &[CapsulePose],
    ) -> Result<(), HullContactError> {
        let Some(feet) = relocation.teleport_destination().map(|feet| feet.map(snap)) else {
            return Ok(());
        };
        let origin = self.capsule_origin(feet);
        if self.body.relocate_if_clear(origin, blockers)? {
            self.pose.position = feet;
            return Ok(());
        }
        let lifted = [feet[0], feet[1] + 0.35, feet[2]];
        let lifted_origin = self.capsule_origin(lifted);
        if self.body.relocate_if_clear(lifted_origin, blockers)? {
            self.pose.position = lifted;
        }
        Ok(())
    }

    fn capsule_origin(&self, [x, y, z]: [f64; 3]) -> [f32; 3] {
        let lift = pogo(&self.base, self.posture.crouched()) as f32 + self.posture.spring_offset();
        [x as f32, (y + f64::from(lift)) as f32, z as f32]
    }

    #[allow(clippy::too_many_arguments)]
    fn move_body(
        &mut self,
        rays: &TriangleIndex,
        velocity: [f64; 3],
        normal: [f64; 3],
        dt: f64,
        _frame: u32,
        blockers: &[CapsulePose],
        settings: &MoverSettings,
        allow_arrival_settle: bool,
        standing_on_actor: bool,
    ) -> Result<(f64, bool), HullContactError> {
        let grounded = self.posture.ground() == Ground::Standing;
        self.drive_pose_moved = false;
        let [x, y, z] = velocity;
        let velocity = if grounded && (x * x + y * y) + z * z >= EPSILON {
            along_ground([x, z], normal)
        } else {
            velocity
        };
        let [x, y, z] = velocity.map(|axis| axis as f32);
        let dt = dt as f32;
        let rise = self.posture.spring_speed();
        // Replace this pose's displacement, not the capsule solver. The
        // measured sweep velocity seeds ordinary movement after scope exit.
        // A drive whose whole step falls under the quantisation is dropped
        // for this pose: the driven velocity reads the authored zero and no
        // ending fires (reference mover.py:942-946, 215).
        let drive = self.owner_move.filter(|(_, _, speed)| {
            speed.is_finite() && f64::from(dt).is_finite() && *speed * f64::from(dt) >= SCRIPT_STEP
        });
        let [x, y, z] = if self.owner_move.is_some() && drive.is_none() {
            [0.0, 0.0, 0.0]
        } else {
            [x, y, z]
        };
        let ordinary_displacement = [
            ((0.0 * rise + x) * dt) + 0.0,
            ((rise + y) * dt) + 0.0,
            ((0.0 * rise + z) * dt) + 0.0,
        ];
        let start = self.body.position();
        let previous_feet = self.pose.position;
        let displacement = drive.map_or(ordinary_displacement, |(_, destination, speed)| {
            let delta = std::array::from_fn::<_, 3, _>(|i| destination[i] - previous_feet[i]);
            let length = distance(delta);
            let step = (speed * f64::from(dt)).min(length).min(32.0);
            delta.map(|axis| {
                if length > 0.0 {
                    (axis * step / length) as f32
                } else {
                    0.0
                }
            })
        });
        let movement = self.body.move_capsule_against(displacement, dt, blockers)?;
        let actor_contact = movement.actor_contact;
        let [mut x, mut y, mut z] = clip_velocity([x, y, z], movement.planes).map(f64::from);
        if grounded {
            let squared = (x * x + y * y) + z * z;
            let flat = x * x + z * z;
            if squared >= EPSILON && flat >= EPSILON {
                let factor = (squared / flat).sqrt();
                (x, y, z) = (x * factor, 0.0, z * factor);
            }
        }
        self.posture.flags =
            (self.posture.flags & !TOUCHING) | (u32::from(movement.touching) * TOUCHING);
        // Preserve wall-ride / wall-climb vertical glide when touching a wall with
        // reduced gravity so Lúcio/Genji/Hanzo are not dragged down by wall friction.
        if !grounded && movement.touching && settings.gravity_scale <= 0.25 && y < 0.0 {
            y = 0.0;
        }
        let lift = pogo(&self.base, self.posture.crouched()) as f32 + self.posture.spring_offset();
        #[cfg(any(test, feature = "inspector"))]
        let contact_planes: Vec<[f64; 3]> = movement
            .planes
            .iter()
            .take(crate::game_debug::MAX_DEBUG_MOVER_NORMALS)
            .map(|plane| plane.normal.map(f64::from))
            .collect();
        let capsule_end = movement.position;
        let [px, py, pz] = capsule_end;
        let feet = f64::from(py - lift);
        let floor = rays.lowest() - ABYSS;
        let feet_raw = [f64::from(px), feet.max(floor), f64::from(pz)];
        self.pose.position = feet_raw.map(snap);
        #[cfg(any(test, feature = "inspector"))]
        {
            let point = self.pose.position;
            self.last_contact_normals.clear();
            self.last_contact_normals
                .extend(contact_planes.into_iter().map(|normal| {
                    crate::game_debug::DebugContactNormal {
                        tick: _frame,
                        entity: 0,
                        point,
                        normal,
                    }
                }));
        }
        let [px, py, pz] = self.pose.position;
        self.body
            .teleport([px as f32, (py + f64::from(lift)) as f32, pz as f32]);
        if feet < floor {
            y = 0.0;
        }
        if let Some((key, destination, _)) = drive {
            // The drive branch ran (dt-valid): the pose counts as scripted
            // motion for the owner's correction window only if the drive
            // actually moved the feet (reference: server_motion opens the
            // window on a committed, feet-moving drive pose; a fully seated
            // drive commits no motion).
            self.drive_pose_moved = self.pose.position != previous_feet;
            let applied = std::array::from_fn::<_, 3, _>(|i| capsule_end[i] - start[i]);
            [x, y, z] = applied.map(|delta| f64::from(delta / dt));
            if grounded && self.drive_pose_moved && y > 0.0 && py > previous_feet[1] {
                // Check the committed feet without the standing step-down band
                // that can reseat the mover on the old floor.
                let support = self.probe.probe(
                    rays,
                    self.body.world(),
                    settings,
                    ProbePose {
                        feet: [px, py - SCRIPT_STEP, pz],
                        velocity: [x, y, z],
                        spring: self.posture.spring_offset(),
                        crouched: self.posture.crouched(),
                        airborne: true,
                    },
                    f64::from(dt),
                    self.category,
                )?;
                // Keep upstream crown support if it still reaches the feet.
                let crown_support = standing_on_actor
                    && blockers.iter().any(|blocker| {
                        let crown = f64::from(blocker.position[1] + blocker.top + blocker.radius);
                        let radius = f64::from(blocker.radius)
                            + if self.posture.crouched() {
                                settings.crouch_radius
                            } else {
                                settings.stand_radius
                            };
                        (py - crown).abs() <= SCRIPT_STEP
                            && (px - f64::from(blocker.position[0]))
                                .hypot(pz - f64::from(blocker.position[2]))
                                < radius
                    });
                if !crown_support && support.ground.is_none_or(|hit| hit.y < py - SCRIPT_STEP) {
                    self.posture.begin_velocity_launch();
                }
            }
            // RAW solver feet, exactly as the reference (whose position is
            // float, not the wire grid): the drive ends when the remaining
            // distance is within the quantisation (arrival) or this pose
            // closed less than half a quantisation of the line (geometric
            // block). Anything that keeps closing the line — a graze along
            // a face included — keeps the drive alive on the measured sweep
            // velocity. The wire-grid snap stays for pose/records; measuring
            // on it would let the half-up ratchet fake one grid tick of
            // progress per pose on a head-on press and tunnel the capsule.
            let delta = std::array::from_fn::<_, 3, _>(|i| destination[i] - previous_feet[i]);
            let whole = distance(delta);
            let remaining = distance(std::array::from_fn(|i| destination[i] - feet_raw[i]));
            let outcome = if remaining <= SCRIPT_STEP {
                Some(MoveOutcome::Arrived)
            } else if whole - remaining < SCRIPT_STEP / 2.0 {
                Some(MoveOutcome::Blocked)
            } else {
                None
            };
            self.owner_move_result = outcome.map(|outcome| (_frame, key, outcome));
            // A world target at walkable surface height is a stop point on
            // normal arrival. Check map support at the target AND the actual
            // terminal feet; the pre-sweep probe does not establish either.
            // Do not change posture or remove sideways solver motion.
            if outcome == Some(MoveOutcome::Arrived)
                && allow_arrival_settle
                && whole > 0.0
                && [destination, self.pose.position]
                    .into_iter()
                    .all(|[px, py, pz]| {
                        rays.ground(px, pz, py + SCRIPT_STEP, py - SCRIPT_STEP, self.category)
                            .is_some_and(|hit| {
                                hit.surface & NOT_GROUND == 0
                                    && hit.normal[1]
                                        > self
                                            .base
                                            .max_slope
                                            .to_radians()
                                            .clamp(0.0, 1.5707845)
                                            .cos()
                            })
                    })
            {
                let axis = delta.map(|v| v / whole);
                let forward = (x * axis[0] + y * axis[1] + z * axis[2]).max(0.0);
                [x, y, z] = std::array::from_fn(|i| [x, y, z][i] - axis[i] * forward);
            }
        }
        self.pose.velocity = [x, y, z].map(snap);
        Ok((y, actor_contact))
    }
}

/// Position quantisation, the reference mover's `STEP`
/// (mover.py:215; engine constant 0x7FF789B77730). Scripted drives
/// compare progress against it, not against an invented epsilon.
const SCRIPT_STEP: f64 = 1.0 / 1024.0;

fn distance(delta: [f64; 3]) -> f64 {
    delta.into_iter().fold(0.0, f64::hypot)
}

fn along_ground([x, z]: [f64; 2], [nx, ny, nz]: [f64; 3]) -> [f64; 3] {
    let speed = (x * x + z * z).sqrt();
    let along = x * nx + z * nz;
    let projected = [x - nx * along, -ny * along, z - nz * along];
    let (px, pz) = (-z, x);
    let length = (px * px + pz * pz).sqrt();
    if length * length < EPSILON || speed <= 0.0 {
        return projected;
    }
    let (px, pz) = (px / length, pz / length);
    let direction = [-pz * ny, pz * nx - px * nz, px * ny];
    let [dx, dy, dz] = direction;
    let norm = (dx * dx + dy * dy + dz * dz).sqrt();
    if norm <= 0.0 {
        return projected;
    }
    let sign = if (dx * projected[0] + dy * projected[1]) + dz * projected[2] < 0.0 {
        -norm
    } else {
        norm
    };
    direction.map(|axis| axis / sign * speed)
}

#[cfg(test)]
mod tests {
    use std::{
        collections::BTreeSet,
        fs::{self, File},
        path::PathBuf,
    };

    use sha2::{Digest, Sha256};

    use super::*;
    use crate::{
        collision_cache::read_movement_cache,
        collision_scene::CollisionScene,
        commands::{CROUCH, JUMP},
        mover_modifiers::Operation,
        mover_settings::MoverCatalog,
        world::movement_record,
    };

    fn swept_fixture() -> serde_json::Value {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/native-swept-displacement-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["case_id"], "native-swept-displacement.synthetic.v1");
        assert_eq!(fixture["evidence"], "synthetic-native");
        fixture
    }

    #[test]
    fn grounded_crouch_release_waits_for_clearance_from_other_actors() {
        #[derive(serde::Deserialize)]
        struct Case {
            stand_height: f64,
            stand_radius: f64,
            crouch_height: f64,
            crouch_radius: f64,
            floor_lift: f64,
            blocker_feet: [[f64; 3]; 2],
        }
        let case: Case = serde_json::from_str(include_str!(
            "../../fixtures/behavior/actor-stance-clearance-v1.json"
        ))
        .unwrap();
        let settings = MoverSettings {
            stand_height: case.stand_height,
            stand_radius: case.stand_radius,
            crouch_height: case.crouch_height,
            crouch_radius: case.crouch_radius,
            stand_pogo: case.floor_lift,
            crouch_pogo: case.floor_lift,
            ..MoverSettings::default()
        };
        let ground = TriangleIndex::new([crate::collision_index::SceneTriangle {
            vertices: [[-10.0, 0.0, -10.0], [0.0, 0.0, 10.0], [10.0, 0.0, -10.0]],
            mask: 0x18,
            surface: 0,
        }])
        .unwrap();
        let geometry = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let step = |mover: &mut Mover, frame, buttons, blockers: &[CapsulePose]| {
            mover
                .step_against(
                    &ground,
                    Command {
                        frame,
                        buttons,
                        ..Command::default()
                    },
                    0.016,
                    blockers,
                    |_, _| {},
                )
                .unwrap();
        };
        for feet in case.blocker_feet {
            let blocker = crate::mover_ground::standing_capsule(&settings, feet);
            let mut mover = Mover::new(settings, Arc::clone(&geometry), [0.0; 3], 0, 0x18).unwrap();
            let standing = mover.contact_capsule();
            step(&mut mover, 1, CROUCH, &[]);
            assert!(mover.crouched());
            assert_eq!(mover.posture.ground(), Ground::Standing);
            let crouched = mover.contact_capsule();
            let position = mover.pose().position;
            for frame in 2..=3 {
                step(&mut mover, frame, 0, &[blocker]);
                let actual = mover.contact_capsule();
                assert!(mover.crouched(), "blocker at {feet:?}");
                assert_eq!((actual.radius, actual.top), (crouched.radius, crouched.top));
                assert_eq!(mover.pose().position, position);
                assert_eq!(mover.pose().crouch_frame, None);
                assert!(!mover.needs_owner_correction_at(frame));
            }
            step(&mut mover, 4, 0, &[]);
            let actual = mover.contact_capsule();
            assert!(!mover.crouched());
            assert_eq!((actual.radius, actual.top), (standing.radius, standing.top));
            assert_eq!(mover.pose().position, position);
            assert_eq!(mover.pose().crouch_frame, Some(4));
        }
    }

    #[test]
    fn owner_move_takeoff_keeps_the_first_two_swept_poses() {
        use crate::{collision_index::SceneTriangle, mover_correction::CorrectionHistory};

        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/native-owner-move-takeoff-v1.json"
        ))
        .unwrap();
        let dt = fixture["input"]["dt"].as_f64().unwrap();
        let speed = fixture["input"]["speed"].as_f64().unwrap();
        let target = std::array::from_fn(|i| fixture["input"]["target"][i].as_f64().unwrap());
        let minimum_y = fixture["expected"]["minimum_second_feet_y"]
            .as_f64()
            .unwrap();
        let sample_bits = fixture["expected"]["rising_airborne_sample_bits"]
            .as_u64()
            .unwrap();
        let rays = TriangleIndex::new([SceneTriangle {
            vertices: [[-10.0, 0.0, -10.0], [0.0, 0.0, 10.0], [10.0, 0.0, -10.0]],
            mask: 0x18,
            surface: 0,
        }])
        .unwrap();
        let mut mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(vec![]).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        let key = MoveToKey {
            instance: 1,
            raw: 0,
            activation: 1,
            start_ms: 0,
        };
        mover.set_owner_move(Some((key, target, speed)));
        let mut history = CorrectionHistory::new(1, 1);
        let mut observed = Vec::new();
        for frame in 1..=2 {
            mover
                .step(
                    &rays,
                    Command {
                        frame,
                        ..Command::default()
                    },
                    dt,
                    |_, _| {},
                )
                .unwrap();
            let pose = mover.pose();
            let probe = mover.last_ground_probe.unwrap();
            assert!(mover.scripted_motion_at(frame));
            history.swept_motion(frame, 16_000);
            assert!(history.record(pose));
            let correction = history
                .candidate(frame, true)
                .unwrap()
                .update()
                .movement
                .unwrap();
            assert_eq!(correction.position, pose.position);
            assert_eq!(correction.velocity, pose.velocity);
            assert_eq!(correction.flags, pose.flags);
            let sample = sample_movement_flags(&pose).unwrap();
            eprintln!(
                "frame={frame} pre_support={:?} feet={:?} velocity={:?} flags={:#x} sample={sample:#x}",
                probe.hit, pose.position, pose.velocity, pose.flags
            );
            observed.push((probe.hit, pose, sample));
        }
        assert!(
            observed[0].0.is_some(),
            "the cast begins with real floor support"
        );
        assert!(
            observed[1].1.position[1] > minimum_y,
            "the second sweep must not reseat on the old floor"
        );
        assert!(
            observed
                .iter()
                .all(|(_, _, sample)| u64::from(sample & 7) == sample_bits),
            "both committed poses are rising and airborne"
        );
        assert!(observed[1].0.is_none());
    }

    #[test]
    fn owner_move_up_a_supported_slope_stays_standing() {
        use crate::collision_index::SceneTriangle;

        let rays = TriangleIndex::new([SceneTriangle {
            vertices: [[-10.0, -5.0, -10.0], [0.0, 0.0, 10.0], [10.0, 5.0, -10.0]],
            mask: 0x18,
            surface: 0,
        }])
        .unwrap();
        let mut mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(vec![]).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        let key = MoveToKey {
            instance: 1,
            raw: 0,
            activation: 1,
            start_ms: 0,
        };
        mover.set_owner_move(Some((key, [4.0, 2.0, 0.0], 20.0)));
        for frame in 1..=2 {
            mover
                .step(
                    &rays,
                    Command {
                        frame,
                        ..Command::default()
                    },
                    0.016,
                    |_, _| {},
                )
                .unwrap();
            assert!(mover.last_ground_probe.unwrap().grounded);
            let pose = mover.pose();
            assert!(pose.velocity[1] > 0.0);
            assert!((pose.position[1] - pose.position[0] * 0.5).abs() <= SCRIPT_STEP);
            assert_eq!(sample_movement_flags(&pose).unwrap() & 7, 6);
        }
    }

    #[test]
    fn owner_move_supported_arrival_only_retires_forward_drive() {
        use crate::collision_index::SceneTriangle;

        // Reduced replay of the last 104319 cast: the final pose of a
        // diagonal upward drive reaching the height of a walkable shelf.
        let start = [31.10546875, 12.328125, 15.21875];
        let target = [30.985521803373274, 12.500020027160645, 15.357037517438323];
        for (surface, buttons, initial_y, should_settle) in [
            (0, 0, 0.0, true),
            (0, 0, 4.0, true),
            (0, 0, -4.0, true),
            (1, 0, 0.0, false),
            (0, JUMP, 0.0, false),
        ] {
            let rays = TriangleIndex::new([SceneTriangle {
                vertices: [[27.0, 12.5, 14.0], [31.02, 12.5, 20.0], [31.02, 12.5, 14.0]],
                mask: 0x18,
                surface,
            }])
            .unwrap();
            let mut mover = Mover::new(
                MoverSettings::default(),
                Arc::new(ContactWorld::new(vec![]).unwrap()),
                start,
                0,
                0x18,
            )
            .unwrap();
            mover.pose.velocity[1] = initial_y;
            let key = MoveToKey {
                instance: 8,
                raw: 5,
                activation: 1,
                start_ms: 0,
            };
            mover.set_owner_move(Some((key, target, 20.0)));
            mover
                .step(
                    &rays,
                    Command {
                        frame: 1,
                        buttons,
                        ..Command::default()
                    },
                    0.016,
                    |_, _| {},
                )
                .unwrap();
            assert_eq!(
                mover.owner_move_result(1),
                Some((key, MoveOutcome::Arrived))
            );
            let terminal = mover.pose();
            mover.set_owner_move(None);
            mover
                .step(
                    &rays,
                    Command {
                        frame: 2,
                        ..Command::default()
                    },
                    0.016,
                    |_, _| {},
                )
                .unwrap();
            if should_settle {
                assert_eq!(
                    terminal.velocity, [0.0; 3],
                    "the arrival sweep consists entirely of drive motion"
                );
                assert_eq!(
                    mover.pose().position,
                    terminal.position,
                    "the first free pose must stay on the shelf"
                );
            } else {
                assert!(
                    terminal.velocity[1] > 10.0,
                    "unsupported or jump poses retain their drive carry"
                );
            }
        }
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range movement cache"]
    fn practice_range_owner_move_replays_last_promoted_arrival() {
        let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let cache =
            read_movement_cache(File::open(root.join("collision/0688.col")).unwrap()).unwrap();
        let ids = BTreeSet::new();
        let scene = CollisionScene::movement_probe(&cache, &ids).unwrap();
        let world = Arc::new(ContactWorld::from_movement_cache(cache, &ids).unwrap());
        let target = [30.985521803373274, 12.500020027160645, 15.357037517438323];
        let key = MoveToKey {
            instance: 8,
            raw: 5,
            activation: 1,
            start_ms: 0,
        };
        let mut mover = Mover::new(
            MoverSettings::default(),
            world,
            [38.00390625, 2.4716796875, 7.30859375],
            -7501,
            0x18,
        )
        .unwrap();
        mover.pose.velocity = [0.0, -4.0634765625, 0.0];
        mover.set_owner_move(Some((key, target, 20.0)));
        let mut arrival = None;
        for frame in 1..=90 {
            mover
                .step(
                    scene.triangles(),
                    Command {
                        frame,
                        yaw: -7501,
                        pitch: -7047,
                        ..Command::default()
                    },
                    0.016,
                    |_, _| {},
                )
                .unwrap();
            if let Some((_, outcome)) = mover.owner_move_result(frame) {
                assert_eq!(
                    outcome,
                    MoveOutcome::Arrived,
                    "frame {frame}, pose {:?}",
                    mover.pose()
                );
                arrival = Some(frame);
                break;
            }
        }
        let frame = arrival.expect("drive must reach the live target");
        let at = mover.pose();
        mover.set_owner_move(None);
        for frame in frame + 1..=frame + 60 {
            mover
                .step(
                    scene.triangles(),
                    Command {
                        frame,
                        yaw: -7501,
                        pitch: -7047,
                        ..Command::default()
                    },
                    0.016,
                    |_, _| {},
                )
                .unwrap();
        }
        let settled = mover.pose();
        println!(
            "terminal frame={frame} feet={:?} velocity={:?}; settled feet={:?} velocity={:?}",
            at.position, at.velocity, settled.position, settled.velocity
        );
        assert!(
            distance(at.velocity) <= SCRIPT_STEP,
            "drive carry at arrival: {:?}",
            at.velocity
        );
        assert!(
            distance(std::array::from_fn(|i| settled.position[i] - at.position[i])) <= SCRIPT_STEP
        );
        assert_eq!(settled.velocity, [0.0; 3]);
        assert_eq!(mover.posture.ground(), Ground::Standing);
    }

    #[test]
    fn swept_displacement_waits_for_one_actual_pose_and_first_pending_request_wins() {
        let fixture = swept_fixture();
        let input = &fixture["input"];
        let due: u32 = input["due_frame"].as_u64().unwrap().try_into().unwrap();
        let delta: [f64; 3] = serde_json::from_value(input["displacement"].clone()).unwrap();
        let displacement = SweptDisplacement::new(delta).unwrap();
        let second = SweptDisplacement::new(
            serde_json::from_value(input["rejected_second_displacement"].clone()).unwrap(),
        )
        .unwrap();
        let rays = TriangleIndex::new([]).unwrap();
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let make = || {
            Mover::new(
                MoverSettings {
                    gravity_scale: 0.0,
                    ..MoverSettings::default()
                },
                Arc::clone(&world),
                [0.0; 3],
                0,
                0x18,
            )
            .unwrap()
        };
        let mut moved = make();
        let mut reference = make();
        let velocity = serde_json::from_value(input["velocity"].clone()).unwrap();
        moved.pose.velocity = velocity;
        reference.pose.velocity = velocity;
        assert!(moved.queue_swept_displacement(due, displacement));
        assert!(!moved.queue_swept_displacement(due, second));
        assert!(moved.has_pending_script_motion());
        for frame in 1..=due + 2 {
            let command = Command {
                frame,
                ..Command::default()
            };
            if frame == due {
                let mut pending = moved.pending_poses(command);
                assert_eq!(
                    moved
                        .step_pending(&rays, &mut pending, 0.016, 0, Vec::new, |_, _| {})
                        .unwrap(),
                    0
                );
                assert!(moved.has_pending_script_motion());
                assert!(!moved.scripted_motion_at(frame));
            }
            if frame == due + 1 {
                assert!(moved.queue_swept_displacement(frame, second));
                moved.clear_pending_script_motion();
                assert!(!moved.has_pending_script_motion());
                assert!(moved.scripted_motion_at(due));
                assert!(!moved.scripted_motion_at(frame));
            }
            moved.step(&rays, command, 0.016, |_, _| {}).unwrap();
            reference.step(&rays, command, 0.016, |_, _| {}).unwrap();
            let expected = if frame >= due { delta } else { [0.0; 3] };
            assert_eq!(
                moved.pose.position,
                std::array::from_fn(|axis| reference.pose.position[axis] + expected[axis])
            );
            assert_eq!(moved.pose.velocity, reference.pose.velocity);
            assert_eq!(moved.pose.flags, reference.pose.flags);
            assert_eq!(moved.pose.frame, reference.pose.frame);
            assert_eq!(moved.has_pending_script_motion(), frame < due);
            assert_eq!(moved.scripted_motion_at(frame), frame == due);
        }
    }

    #[test]
    fn swept_displacement_marker_excludes_rounded_and_canceled_requests() {
        let rays = TriangleIndex::new([]).unwrap();
        let mut mover = Mover::new(
            MoverSettings {
                gravity_scale: 0.0,
                ..MoverSettings::default()
            },
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        let tiny = SweptDisplacement::new([0.000_001, 0.0, 0.0]).unwrap();
        assert!(mover.queue_swept_displacement(1, tiny));
        mover
            .step(
                &rays,
                Command {
                    frame: 1,
                    ..Command::default()
                },
                0.016,
                |_, _| {},
            )
            .unwrap();
        assert_eq!(mover.pose.position[0], 0.0);
        assert!(!mover.has_pending_script_motion());
        assert!(!mover.scripted_motion_at(1));
        assert!(
            mover.queue_swept_displacement(2, SweptDisplacement::new([2.0, 0.0, 0.0]).unwrap())
        );
        mover.clear_pending_script_motion();
        mover
            .step(
                &rays,
                Command {
                    frame: 2,
                    ..Command::default()
                },
                0.016,
                |_, _| {},
            )
            .unwrap();
        assert_eq!(mover.pose.position[0], 0.0);
        assert!(!mover.has_pending_script_motion());
        assert!(!mover.scripted_motion_at(2));
    }

    #[test]
    fn posed_batches_sample_actor_capsules_without_empty_or_zero_budget_work() {
        let fixture = swept_fixture();
        let input = &fixture["input"];
        let due: u32 = input["due_frame"].as_u64().unwrap().try_into().unwrap();
        let settings = MoverSettings {
            gravity_scale: 0.0,
            stand_height: input["stand_height"].as_f64().unwrap(),
            stand_radius: input["stand_radius"].as_f64().unwrap(),
            ..MoverSettings::default()
        };
        let blocker = crate::mover_ground::standing_capsule(
            &settings,
            serde_json::from_value(input["actor_feet"].clone()).unwrap(),
        );
        let displacement = SweptDisplacement::new(
            serde_json::from_value(input["wall_displacement"].clone()).unwrap(),
        )
        .unwrap();
        let rays = TriangleIndex::new([]).unwrap();
        let mut mover = Mover::new(
            settings,
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        assert!(mover.queue_swept_displacement(due, displacement));
        let mut samples = 0;
        for frame in 1..=due + 1 {
            let command = Command {
                frame,
                ..Command::default()
            };
            let mut pending = mover.pending_poses(command);
            if frame == due {
                mover
                    .step_pending(
                        &rays,
                        &mut pending,
                        0.016,
                        0,
                        || {
                            samples += 1;
                            vec![blocker]
                        },
                        |_, _| {},
                    )
                    .unwrap();
                assert_eq!(samples, frame - 1);
            }
            mover
                .step_pending(
                    &rays,
                    &mut pending,
                    0.016,
                    usize::MAX,
                    || {
                        samples += 1;
                        vec![blocker]
                    },
                    |_, _| {},
                )
                .unwrap();
            assert_eq!(samples, frame);
            assert_eq!(mover.scripted_motion_at(frame), frame == due);
        }
        assert!(mover.pose.position[2] > fixture["expected"]["wall_min_z"].as_f64().unwrap());
        assert!(mover.pose.position[2] < fixture["expected"]["wall_max_z"].as_f64().unwrap());
        assert_eq!(mover.contact_capsule(), mover.body.capsule_pose());
        assert!(!mover.has_pending_script_motion());
        let before = mover.pose.position;
        assert!(mover.queue_swept_displacement(due + 2, displacement));
        mover.clear_pending_script_motion();
        let mut pending = mover.pending_poses(Command {
            frame: due + 2,
            ..Command::default()
        });
        mover
            .step_pending(
                &rays,
                &mut pending,
                0.016,
                usize::MAX,
                || {
                    samples += 1;
                    vec![blocker]
                },
                |_, _| {},
            )
            .unwrap();
        assert_eq!(mover.pose.position, before);
        assert_eq!(samples, due + 2);
        mover
            .step_pending(
                &rays,
                &mut pending,
                0.016,
                usize::MAX,
                || -> Vec<CapsulePose> { panic!("exhausted poses must not sample actor capsules") },
                |_, _| {},
            )
            .unwrap();
    }

    #[test]
    fn regular_momentum_uses_one_actor_snapshot_for_all_recovered_poses() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/native-regular-actor-contact-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(
            fixture["case_id"],
            "native-regular-actor-contact.synthetic.v1"
        );
        assert_eq!(fixture["evidence"], "synthetic-native");
        let input = &fixture["input"];
        let warmup_frame: u32 = input["warmup_frame"].as_u64().unwrap().try_into().unwrap();
        let final_frame: u32 = input["final_frame"].as_u64().unwrap().try_into().unwrap();
        let dt = input["dt"].as_f64().unwrap();
        let settings = MoverSettings {
            gravity_scale: 0.0,
            decel_air_per_speed: 0.0,
            stand_height: input["stand_height"].as_f64().unwrap(),
            stand_radius: input["stand_radius"].as_f64().unwrap(),
            ..MoverSettings::default()
        };
        let blocker = crate::mover_ground::standing_capsule(
            &settings,
            serde_json::from_value(input["actor_feet"].clone()).unwrap(),
        );
        let rays = TriangleIndex::new([]).unwrap();
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let make = || Mover::new(settings, Arc::clone(&world), [0.0; 3], 0, 0x18).unwrap();
        let mut blocked = make();
        let mut free = make();
        for mover in [&mut blocked, &mut free] {
            mover
                .step(
                    &rays,
                    Command {
                        frame: warmup_frame,
                        ..Command::default()
                    },
                    dt,
                    |_, _| {},
                )
                .unwrap();
            mover.pose.velocity = serde_json::from_value(input["velocity"].clone()).unwrap();
        }
        let command = Command {
            frame: final_frame,
            ..Command::default()
        };
        let mut pending = blocked.pending_poses(command);
        assert_eq!(
            pending.len(),
            fixture["expected"]["posed_frames"].as_u64().unwrap() as usize
        );
        blocked
            .step_pending(
                &rays,
                &mut pending,
                dt,
                0,
                || -> Vec<CapsulePose> { panic!("zero pose budget must not sample actors") },
                |_, _| {},
            )
            .unwrap();
        let mut samples = 0;
        let mut posed = 0;
        blocked
            .step_pending(
                &rays,
                &mut pending,
                dt,
                usize::MAX,
                || {
                    samples += 1;
                    vec![blocker]
                },
                |mover, command| {
                    posed += 1;
                    assert!(
                        mover.pose.position[2] < fixture["expected"]["maximum_z"].as_f64().unwrap()
                    );
                    assert!(!mover.scripted_motion_at(command.frame));
                },
            )
            .unwrap();
        free.step(&rays, command, dt, |_, _| {}).unwrap();
        assert_eq!(
            samples,
            fixture["expected"]["sample_count"].as_u64().unwrap()
        );
        assert_eq!(posed, fixture["expected"]["posed_frames"].as_u64().unwrap());
        assert!(blocked.pose.position[2] > fixture["expected"]["minimum_z"].as_f64().unwrap());
        assert!(free.pose.position[2] > fixture["expected"]["maximum_z"].as_f64().unwrap());
        assert!(!blocked.has_pending_script_motion());
    }

    fn swept_wall_world(input: &serde_json::Value, category: u16) -> Arc<ContactWorld> {
        use crate::{
            collision_cache::{Mesh, Triangle},
            contact_mesh::ContactMesh,
            contact_world::{StaticGeometry, StaticShape},
        };

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
        Arc::new(ContactWorld::new(vec![shape]).unwrap())
    }

    #[test]
    fn scripted_relocation_and_teleport_require_a_clear_destination() {
        let fixture = swept_fixture();
        let input = &fixture["input"];
        let category = u16::try_from(input["category"].as_u64().unwrap()).unwrap();
        let settings = MoverSettings {
            gravity_scale: 0.0,
            stand_height: input["stand_height"].as_f64().unwrap(),
            stand_radius: input["stand_radius"].as_f64().unwrap(),
            ..MoverSettings::default()
        };
        let destination = serde_json::from_value(input["wall_displacement"].clone()).unwrap();
        let blocker = crate::mover_ground::standing_capsule(&settings, destination);
        let relocation = Relocation::new(destination).unwrap();
        let rays = TriangleIndex::new([]).unwrap();
        for motion in [
            ScriptedMotion::Relocate(relocation),
            ScriptedMotion::Teleport(relocation),
        ] {
            let mut mover = Mover::new(
                settings,
                swept_wall_world(input, category),
                [0.0; 3],
                0,
                category,
            )
            .unwrap();
            assert!(mover.queue_scripted_motion(2, motion));
            assert!(
                !mover.queue_swept_displacement(2, SweptDisplacement::new(destination).unwrap())
            );
            for frame in 1..=4 {
                if frame == 3 {
                    assert!(mover.queue_scripted_motion(frame, motion));
                }
                let blockers = if frame < 3 {
                    std::slice::from_ref(&blocker)
                } else {
                    &[]
                };
                mover
                    .step_against(
                        &rays,
                        Command {
                            frame,
                            ..Command::default()
                        },
                        0.016,
                        blockers,
                        |_, _| {},
                    )
                    .unwrap();
                assert_eq!(mover.has_pending_script_motion(), frame == 1);
                assert_eq!(mover.scripted_motion_at(frame), frame == 3);
                assert_eq!(
                    mover.pose.position,
                    if frame < 3 { [0.0; 3] } else { destination }
                );
            }
        }
    }

    #[test]
    fn elevated_hostile_capsule_does_not_push_the_owner_into_a_neighbor() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/hostile-capsule-glance-v1.json"
        ))
        .unwrap();
        let input = &fixture["vertical_clearance"];
        let feet = |key: &str| serde_json::from_value::<[f64; 3]>(input[key].clone()).unwrap();
        let settings = MoverSettings {
            gravity_scale: 0.0,
            stand_radius: 0.5,
            stand_height: 2.0,
            stand_pogo: 0.5,
            ..MoverSettings::default()
        };
        let geometry = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let rays = TriangleIndex::new([]).unwrap();
        let elevated = crate::mover_ground::standing_capsule(&settings, feet("elevated"));
        let neighbor = crate::mover_ground::standing_capsule(&settings, feet("neighbor"));
        for blockers in [[elevated, neighbor], [neighbor, elevated]] {
            let mut owner = Mover::new(settings, Arc::clone(&geometry), [0.0; 3], 0, 0x18).unwrap();
            assert!(elevated.bounds().low[1] > owner.contact_capsule().bounds().high[1]);
            let command = Command {
                frame: 1,
                ..Command::default()
            };
            owner
                .step_against(&rays, command, 0.016, &blockers, |_, _| {})
                .unwrap();
            assert_eq!(owner.pose().position, [0.0; 3]);
            assert!(!owner.needs_owner_correction_at(command.frame));
        }

        let support = crate::mover_ground::standing_capsule(&settings, [0.0; 3]);
        let crown = f64::from(support.bounds().high[1]);
        let mut owner = Mover::new(settings, geometry, [0.0, crown, 0.0], 0, 0x18).unwrap();
        owner
            .step_against(
                &rays,
                Command {
                    frame: 1,
                    ..Command::default()
                },
                0.016,
                &[support],
                |_, _| {},
            )
            .unwrap();
        assert_eq!(owner.pose().position, [0.0, crown, 0.0]);
        assert!(owner.needs_owner_correction_at(1));
    }

    #[test]
    fn repeated_hostile_contact_holds_the_boundary_and_allows_a_glance() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/hostile-capsule-glance-v1.json"
        ))
        .unwrap();
        let input = &fixture["held_contact"];
        let dt = input["step_seconds"].as_f64().unwrap();
        let tolerance = input["tolerance"].as_f64().unwrap();
        let actor_feet: [f64; 3] = serde_json::from_value(input["actor_feet"].clone()).unwrap();
        let speed = input["normal_speed"].as_f64().unwrap();
        let tangent = input["tangent_speed"].as_f64().unwrap();
        let head_on = u32::try_from(input["head_on_frames"].as_u64().unwrap()).unwrap();
        let glance = u32::try_from(input["glance_frames"].as_u64().unwrap()).unwrap();
        let settings = MoverSettings {
            gravity_scale: 0.0,
            decel_air_base: 0.0,
            decel_air_per_speed: 0.0,
            ..MoverSettings::default()
        };
        let actor = crate::mover_ground::standing_capsule(&settings, actor_feet);
        let rays = TriangleIndex::new([]).unwrap();
        let mut mover = Mover::new(
            settings,
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        let combined_radius = f64::from(mover.contact_capsule().radius + actor.radius);
        let mut previous = 0.0;
        for frame in 1..=head_on + glance {
            let vn = if frame <= head_on { 0.0 } else { tangent };
            mover.queue_velocity_change(frame, VelocityChange::SetVelocity([vn, 0.0, speed]));
            mover
                .step_against(
                    &rays,
                    Command {
                        frame,
                        ..Command::default()
                    },
                    dt,
                    &[actor],
                    |_, _| {},
                )
                .unwrap();
            let [x, _, z] = mover.pose().position;
            let distance = (x - actor_feet[0]).hypot(z - actor_feet[2]);
            assert!(
                distance >= combined_radius - tolerance,
                "frame {frame} separation {distance}"
            );
            if frame <= head_on {
                assert!(
                    z >= previous - tolerance,
                    "frame {frame} moved back {previous} to {z}"
                );
            }
            if frame == head_on {
                assert!(
                    distance <= combined_radius + 0.02,
                    "stopped short: {distance}"
                );
            }
            previous = z;
        }
        assert!(mover.pose().position[0] > 0.1);
    }

    #[test]
    fn effective_character_collision_controls_sweeps_and_regular_motion() {
        let fixture = swept_fixture();
        let input = &fixture["input"];
        let category: u16 = input["category"].as_u64().unwrap().try_into().unwrap();
        let settings = MoverSettings {
            gravity_scale: 0.0,
            decel_air_base: 0.0,
            decel_air_per_speed: 0.0,
            stand_height: input["stand_height"].as_f64().unwrap(),
            stand_radius: input["stand_radius"].as_f64().unwrap(),
            ..MoverSettings::default()
        };
        let world = swept_wall_world(input, category);
        let rays = TriangleIndex::new([]).unwrap();
        let wall_z = fixture["expected"]["wall_max_z"].as_f64().unwrap();
        let actor_z = wall_z / 2.0;
        let blocker = crate::mover_ground::standing_capsule(&settings, [0.0, 0.0, actor_z]);
        let delta = [0.0, 0.0, wall_z * 2.0];
        let dt = 0.25;
        let collision_disabled = BTreeMap::from([(
            ModifierKey {
                instance: 1,
                state: 1,
                slot: 0,
            },
            Modifier {
                index: crate::mover_modifiers::COLLISION,
                operation: Operation::Set,
                value: 0.0,
                priority: 0.0,
            },
        )]);

        for swept in [false, true] {
            let mut mover =
                Mover::new(settings, Arc::clone(&world), [0.0; 3], 0, category).unwrap();
            let start = mover.body.position();
            for (frame, collides) in [(1, true), (2, false), (3, true)] {
                // Reset position to exercise each interval against the same
                // blockers while keeping the real command and modifier clocks.
                mover.pose.position = [0.0; 3];
                mover.body = mover.body.fresh_at(start);
                mover.pose.velocity = [0.0; 3];
                if swept {
                    assert!(
                        mover.queue_swept_displacement(
                            frame,
                            SweptDisplacement::new(delta).unwrap()
                        )
                    );
                } else {
                    mover.queue_velocity_change(
                        frame,
                        VelocityChange::Delta(delta.map(|axis| axis / dt)),
                    );
                }
                mover
                    .step_against(
                        &rays,
                        Command {
                            frame,
                            ..Command::default()
                        },
                        dt,
                        &[blocker],
                        |_, _| {},
                    )
                    .unwrap();
                let z = mover.pose.position[2];
                let range = if collides {
                    0.0..actor_z
                } else {
                    actor_z..wall_z
                };
                assert!(
                    range.contains(&z) && z > range.start,
                    "frame {frame}, swept {swept}, z {z}"
                );
                assert_eq!(mover.scripted_motion_at(frame), swept);
                if frame == 1 {
                    mover.track_modifiers(frame, &collision_disabled);
                } else if frame == 2 {
                    mover.track_modifiers(frame, &BTreeMap::new());
                }
            }
        }
    }

    #[test]
    fn swept_displacement_uses_the_existing_capsule_without_clipping_velocity() {
        let fixture = swept_fixture();
        let input = &fixture["input"];
        let category: u16 = input["category"].as_u64().unwrap().try_into().unwrap();
        let mut mover = Mover::new(
            MoverSettings {
                stand_height: input["stand_height"].as_f64().unwrap(),
                stand_radius: input["stand_radius"].as_f64().unwrap(),
                ..MoverSettings::default()
            },
            swept_wall_world(input, category),
            [0.0; 3],
            8192,
            category,
        )
        .unwrap();
        mover.pose.velocity = serde_json::from_value(input["velocity"].clone()).unwrap();
        mover.pose.pitch = -2048;
        let before = mover.pose();
        let displacement = SweptDisplacement::new(
            serde_json::from_value(input["wall_displacement"].clone()).unwrap(),
        )
        .unwrap();
        mover
            .apply_swept_displacement(displacement, 0.016, &[])
            .unwrap();
        let after = mover.pose();
        assert!(after.position[2] > fixture["expected"]["wall_min_z"].as_f64().unwrap());
        assert!(after.position[2] < fixture["expected"]["wall_max_z"].as_f64().unwrap());
        assert_eq!(after.velocity, before.velocity);
        assert_eq!(
            (after.yaw, after.pitch, after.flags),
            (before.yaw, before.pitch, before.flags)
        );
        let lift =
            pogo(&mover.base, mover.posture.crouched()) as f32 + mover.posture.spring_offset();
        assert_eq!(
            mover.body.position(),
            [
                after.position[0] as f32,
                (after.position[1] + f64::from(lift)) as f32,
                after.position[2] as f32,
            ]
        );
        mover
            .apply_swept_displacement(displacement, 0.016, &[])
            .unwrap();
        let tolerance = fixture["expected"]["repeated_block_tolerance"]
            .as_f64()
            .unwrap();
        assert!((mover.pose.position[2] - after.position[2]).abs() <= tolerance);
        assert_eq!(mover.pose.velocity, before.velocity);
    }

    #[test]
    fn velocity_changes_are_ordered_once_and_wait_for_their_pose() {
        let rays = TriangleIndex::new([]).unwrap();
        let mut mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        let mut reference = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        for frame in 1..=4 {
            let command = Command {
                frame,
                ..Default::default()
            };
            if frame == 3 {
                reference.pose.velocity[0] = 3.0;
                reference.pose.velocity[2] = 4.0;
            }
            mover
                .step(&rays, command, 0.016, |mover, command| {
                    if command.frame == 1 {
                        mover.queue_velocity_change(3, VelocityChange::Delta([6.0, 0.0, 8.0]));
                        mover.queue_velocity_change(3, VelocityChange::HorizontalCap(5.0));
                        mover.queue_velocity_change(3, VelocityChange::Delta([f64::NAN, 0.0, 0.0]));
                    }
                })
                .unwrap();
            reference.step(&rays, command, 0.016, |_, _| {}).unwrap();
            assert_eq!(mover.pose(), reference.pose());
        }
        assert!(mover.velocity_changes.is_empty());
        let mut velocity = [6.0, 7.0, 8.0];
        VelocityChange::HorizontalCap(5.0).apply(&mut velocity);
        assert_eq!(velocity, [3.0, 7.0, 4.0]);
        assert!(!VelocityChange::HorizontalCap(-1.0).valid());
    }

    #[test]
    fn vertical_impulse_leaves_supported_ground_at_the_due_pose() {
        use crate::collision_index::SceneTriangle;

        let contract: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/script-vertical-velocity-impulse-v1.json"
        ))
        .unwrap();
        let input = &contract["input"];
        let expected = &contract["expected"];
        let change = VelocityChange::VerticalImpulse {
            vertical: input["vertical_reset"].as_f64().unwrap(),
            delta: serde_json::from_value(input["delta"].clone()).unwrap(),
        };
        let mut velocity: [f64; 3] =
            serde_json::from_value(input["prior_velocity"].clone()).unwrap();
        let before_physics: [f64; 3] =
            serde_json::from_value(expected["velocity_before_ground_and_gravity"].clone()).unwrap();
        assert!(change.valid());
        assert!(change.apply(&mut velocity));
        assert_eq!(velocity, before_physics);

        let extreme = f64::from(f32::MAX);
        let overflow = VelocityChange::VerticalImpulse {
            vertical: 2.0,
            delta: [extreme, 8.0, 1.0],
        };
        velocity = [extreme, -4.0, -1.5];
        assert!(overflow.valid());
        assert!(!overflow.apply(&mut velocity));
        assert_eq!(velocity, [extreme, -4.0, -1.5]);

        let rays = TriangleIndex::new([SceneTriangle {
            vertices: [[-10.0, 0.0, -10.0], [0.0, 0.0, 20.0], [10.0, 0.0, -10.0]],
            mask: 0x18,
            surface: 0,
        }])
        .unwrap();
        let mut mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        let source = input["frame"].as_u64().unwrap() as u32;
        let due = expected["apply_frame"].as_u64().unwrap() as u32;
        mover.queue_velocity_change(due, change);
        let mut step = |frame| {
            mover
                .step(
                    &rays,
                    Command {
                        frame,
                        ..Default::default()
                    },
                    0.016,
                    |_, _| {},
                )
                .unwrap();
            mover.pose()
        };
        let grounded = step(source);
        assert_eq!(grounded.flags & IN_AIR, 0);
        let lifted = step(due);
        assert_ne!(lifted.flags & IN_AIR, 0);
        assert!(lifted.position[1] > grounded.position[1]);
        assert!(lifted.velocity[1] > 0.0);
        let next = step(due + 1);
        assert!(next.position[1] > lifted.position[1]);
        assert!(next.velocity[1] < lifted.velocity[1]);
        assert!(mover.velocity_changes.is_empty());
    }

    #[test]
    fn directional_modifier_changes_live_motion_without_changing_input_records() {
        let rays = TriangleIndex::new([]).unwrap();
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let make_mover = || {
            Mover::new(
                MoverSettings::default(),
                Arc::clone(&world),
                [0.0; 3],
                0,
                0x18,
            )
            .unwrap()
        };
        let mut modified = make_mover();
        let mut reference = make_mover();
        modified.track_modifiers(
            0,
            &BTreeMap::from([(
                ModifierKey {
                    instance: 1,
                    state: 1,
                    slot: 0,
                },
                Modifier {
                    index: 17,
                    operation: Operation::Multiply,
                    value: 0.5,
                    priority: 0.0,
                },
            )]),
        );
        for frame in 1..=20 {
            let mut command = Command {
                frame,
                forward: 126,
                ..Command::default()
            };
            modified.step(&rays, command, 0.016, |_, _| {}).unwrap();
            command.forward = 63;
            reference.step(&rays, command, 0.016, |_, _| {}).unwrap();
            assert_eq!(modified.pose().position, reference.pose().position);
            assert_eq!(modified.pose().velocity, reference.pose().velocity);
            assert_eq!(modified.pose().throttles, [0, 126]);
            assert_eq!(modified.pose().input_throttles, [0, 126]);
        }
        assert!(modified.pose().position[2] > 0.0);
    }

    #[test]
    fn owner_movement_sample_covers_fresh_and_live_poses() {
        let mut pose = Movement::default();
        assert_eq!(sample_movement_flags(&pose), Some(1 << 1));

        pose.frame = Some(20);
        assert_eq!(sample_movement_flags(&pose), Some(1 << 1));
        pose.flags = CROUCHED | NOT_GROUND_UNDER;
        assert_eq!(
            sample_movement_flags(&pose),
            Some((1 << 1) | (1 << 6) | (1 << 9))
        );
        pose.flags = JUMPED;
        pose.velocity = [0.0, 2.0, 0.0];
        pose.throttles = [0, -127];
        assert_eq!(
            sample_movement_flags(&pose),
            Some(1 | (1 << 2) | (1 << 4) | (1 << 5) | (1 << 11))
        );
        pose.flags = 0x800000;
        pose.velocity[1] = -2.0;
        pose.throttles = [0; 2];
        assert_eq!(sample_movement_flags(&pose), Some(1 | (1 << 3) | (1 << 4)));
        pose.flags |= 0x80;
        assert_eq!(sample_movement_flags(&pose), None);
    }

    #[test]
    fn scripted_drive_endings_follow_the_reference_threshold_rule() {
        // Reviewer (1) + (3) on the mover alone. The production loop feeds
        // the drive from the live state each pose (`stage_scripted_motion`)
        // and drops it once a terminal was consumed; the mover itself keeps
        // no self-clearing state. Rules mirrored from `_move_to`
        // (mover.py:215, 942-946, 1502-1536): a pose that keeps closing the
        // line — a graze along a face included — keeps the drive, only a
        // pose that closes less than half a quantisation ends it (Blocked,
        // routed and plug-tested in `move_to.rs`), arrival within the
        // quantisation ends it (Arrived), and a drive too slow to cover one
        // quantisation this pose is dropped without an ending, velocity
        // zeroed. The reviewer's "stalled near an obstruction without
        // terminal" hypothesis is REFUTED for the graze: the old
        // `1 mm / 10 %` shortfall rule called the FIRST grazing pose
        // Blocked (measured before this rule change: every pull against
        // sloped geometry aborted at once), and the climb below now ends in
        // a single Arrived after sliding the whole face. A head-on press
        // against a single flat quad behaves fixture-dependently in this
        // port (skin creep / one-sided winding); that regime is closed on
        // the authored graph side (gate Wait / scope exit / kill) and its
        // solver-level forensics is a separate concern from the drive rule.
        use crate::{
            collision_cache::{Mesh, Triangle},
            contact_mesh::ContactMesh,
            contact_world::{StaticGeometry, StaticShape},
        };
        let step = MoveToKey {
            instance: 1,
            raw: 0,
            activation: 1,
            start_ms: 0,
        };
        // A 30-degree slope under the feet rising toward -y: a drive into
        // it grazes upward and reaches the destination above the edge.
        let wall = Mesh {
            vertices: vec![
                [-20.0, -10.0, 7.77],
                [-20.0, 10.0, -3.77],
                [20.0, -10.0, 7.77],
                [20.0, 10.0, -3.77],
            ],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1, 3, -1],
                    mask: 0x18,
                    surface: 0,
                },
                Triangle {
                    corners: [3, 2, 1],
                    wings: [-1, 0, -1],
                    mask: 0x18,
                    surface: 0,
                },
            ],
        };
        let mesh = ContactMesh::new(wall, [1.0; 3], u16::MAX).unwrap();
        let shape = StaticShape::new(StaticGeometry::Mesh(Arc::new(mesh)), None).unwrap();
        let world = Arc::new(ContactWorld::new(vec![shape]).unwrap());
        let rays = TriangleIndex::new([]).unwrap();
        let neutral = || Command {
            frame: 9,
            ..Command::default()
        };
        let mut mover = Mover::new(
            MoverSettings::default(),
            Arc::clone(&world),
            [0.0, -9.4, 7.4],
            0,
            0x18,
        )
        .unwrap();
        let destination = [0.0, -6.0, 12.0];
        mover.set_owner_move(Some((step, destination, 8.0)));
        let mut endings = Vec::new();
        for _ in 0..400 {
            mover.step(&rays, neutral(), 0.025, |_, _| {}).unwrap();
            // Reviewer (1) follow-up: each moved drive pose marks the frame
            // as scripted motion for the owner's correction window.
            assert!(mover.scripted_motion_at(9));
            if let Some((_, outcome)) = mover.owner_move_result(9) {
                endings.push(outcome);
                // The live state ends; staging re-derives None from now on.
                mover.set_owner_move(None);
                break;
            }
        }
        assert_eq!(
            endings,
            vec![MoveOutcome::Arrived],
            "grazing keeps driving to the destination, pose {:?}",
            mover.pose.position
        );
        assert!(
            mover.pose.position[2] > 11.0,
            "the graze climbed over the face, pose {:?}",
            mover.pose.position
        );
        // Degenerate pose: a drive too slow to cover a quantisation step
        // drops without an ending; the driven velocity reads zero (there is
        // no fractional world-target progress kept between poses).
        let mut slow = Mover::new(
            MoverSettings::default(),
            Arc::clone(&world),
            [0.0, -9.4, 7.4],
            0,
            0x18,
        )
        .unwrap();
        slow.set_owner_move(Some((step, destination, 0.0001)));
        slow.step(&rays, neutral(), 0.025, |_, _| {}).unwrap();
        assert_eq!(
            slow.owner_move_result(9),
            None,
            "no ending on a dropped drive"
        );
        assert_eq!(
            slow.pose.velocity,
            [0.0, 0.0, 0.0],
            "the drop zeroes the driven velocity"
        );
        // The dropped drive is ordinary motion, not scripted motion: the
        // owner's correction window must not open on this frame (the
        // pre-fix `owner_move.is_some()` marked it anyway).
        assert!(
            !slow.scripted_motion_at(9),
            "a dt-discarded drive must not mark a scripted-motion frame"
        );
    }

    #[test]
    fn ground_probe_and_contact_normals_are_recorded_read_only() {
        use crate::{
            collision_cache::{Mesh, Triangle},
            collision_index::SceneTriangle,
            contact_mesh::ContactMesh,
            contact_world::{StaticGeometry, StaticShape},
        };

        let floor_tris = [
            SceneTriangle {
                vertices: [[-10.0, 0.0, -10.0], [-10.0, 0.0, 10.0], [10.0, 0.0, 10.0]],
                mask: 0x18,
                surface: 0,
            },
            SceneTriangle {
                vertices: [[10.0, 0.0, 10.0], [10.0, 0.0, -10.0], [-10.0, 0.0, -10.0]],
                mask: 0x18,
                surface: 0,
            },
        ];
        let rays = TriangleIndex::new(floor_tris).unwrap();
        let wall = Mesh {
            vertices: vec![
                [-5.0, 0.0, 1.0],
                [-5.0, 4.0, 1.0],
                [5.0, 0.0, 1.0],
                [5.0, 4.0, 1.0],
            ],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1, 3, -1],
                    mask: 0x18,
                    surface: 0,
                },
                Triangle {
                    corners: [3, 2, 1],
                    wings: [-1, 0, -1],
                    mask: 0x18,
                    surface: 0,
                },
            ],
        };
        let mesh = ContactMesh::new(wall, [1.0; 3], u16::MAX).unwrap();
        let shape = StaticShape::new(StaticGeometry::Mesh(Arc::new(mesh)), None).unwrap();
        let world = Arc::new(ContactWorld::new(vec![shape]).unwrap());
        let mut mover =
            Mover::new(MoverSettings::default(), world, [0.0, 0.0, 0.0], 0, 0x18).unwrap();
        assert!(mover.debug_ground_probe(42).is_none());

        let mut hit_wall = false;
        let mut last_frame = 0;
        for frame in 1..=30 {
            last_frame = frame;
            let command = Command {
                frame,
                forward: 127,
                ..Command::default()
            };
            mover.step(&rays, command, 0.016, |_, _| {}).unwrap();
            let probe = mover.debug_ground_probe(42).unwrap();
            assert_eq!(probe.entity, 42);
            assert_eq!(probe.tick, frame);
            assert!(probe.grounded);
            assert_eq!(probe.normal, Some([0.0, 1.0, 0.0]));
            let normals: Vec<_> = mover.debug_contact_normals(42).collect();
            assert!(normals.len() <= crate::game_debug::MAX_DEBUG_MOVER_NORMALS);
            if normals.iter().any(|contact| {
                contact.entity == 42 && contact.tick == frame && contact.normal[2] < -0.5
            }) {
                hit_wall = true;
            }
        }
        assert!(hit_wall, "expected wall contact normal against Z=1 wall");
        assert!(last_frame == 30);
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range movement cache"]
    fn practice_range_player_movement_records_match_python() {
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let cache =
            read_movement_cache(File::open(runtime.join("collision/0688.col")).unwrap()).unwrap();
        let ids = BTreeSet::new();
        let scene = CollisionScene::movement_probe(&cache, &ids).unwrap();
        let world = Arc::new(ContactWorld::from_movement_cache(cache, &ids).unwrap());
        let catalog_path = runtime.join("data/game_movers_174.json");
        let settings = MoverCatalog::load(&catalog_path).unwrap();
        let records: BTreeMap<String, serde_json::Value> =
            serde_json::from_slice(&fs::read(catalog_path).unwrap()).unwrap();
        let expected = match records.len() {
            // Combined Python cc620be includes normalized spring bounds.
            38 => "1db24f3d6b8bbda247fcf7db88134d4ad88c5b27b05453d3f7a777f97c4ee9fd",
            43 => "289ba18c0a1af9e621c4cc72d5e51a8acf45d1fb7a5806af7e866313c26c9e8e",
            count => panic!("no Python reference vector for the {count}-mover replay"),
        };
        let sprint = BTreeMap::from([(
            ModifierKey {
                instance: 1,
                state: 1,
                slot: 0,
            },
            Modifier {
                index: 0,
                operation: Operation::Multiply,
                value: 1.5,
                priority: 0.0,
            },
        )]);
        let released = BTreeMap::new();
        let mut combined = Sha256::new();
        for (i, guid) in records.keys().enumerate() {
            let body = crate::accounts::parse_guid(guid).unwrap();
            let spawn = if i % 3 == 2 {
                [45.5, 2.5, 34.75]
            } else {
                [54.56507, 1.0, 42.14788]
            };
            let category = [0x08, 0x10, 0x18][i % 3];
            let mut mover = Mover::new(
                *settings.for_body(Some(body)),
                Arc::clone(&world),
                spawn,
                -31010,
                category,
            )
            .unwrap();
            let first = 4000 + i as u32 * 500;
            let mut digest = Sha256::new();
            let mut visited = 0;
            for n in 0..304 {
                if (20..24).contains(&n) || (80..118).contains(&n) || (210..278).contains(&n) {
                    continue;
                }
                let jump = (24..29).contains(&n) || (138..153).contains(&n);
                let crouch = (45..75).contains(&n) || (121..136).contains(&n);
                let command = Command {
                    frame: first + n,
                    forward: [127, -127, 58, 0][(n as usize / 16) % 4],
                    right: [0, 0, -127, 111][(n as usize / 20) % 4],
                    yaw: (n as i32 / 12) * 4096 - 31010,
                    pitch: (n as i32 % 28) * 2500 - 32000,
                    buttons: (u32::from(jump) * JUMP) | (u32::from(crouch) * CROUCH),
                    ..Command::default()
                };
                mover
                    .step(scene.triangles(), command, 0.016, |mover, pose| {
                        visited += 1;
                        let record = movement_record(&mover.pose(), Some(pose.frame + 2)).unwrap();
                        digest.update((record.bit_len() as u32).to_le_bytes());
                        digest.update(record.as_bytes());
                        let active = if (12..35).contains(&((pose.frame - first) % 72)) {
                            &sprint
                        } else {
                            &released
                        };
                        mover.track_modifiers(pose.frame, active);
                    })
                    .unwrap();
            }
            assert_eq!(visited, 236);
            combined.update(body.to_le_bytes());
            combined.update(digest.finalize());
        }
        assert_eq!(format!("{:x}", combined.finalize()), expected);
    }
}
