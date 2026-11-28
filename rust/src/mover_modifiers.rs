//! Frame-scoped movement modifiers from build 104319 statescript.
//!
//! The scalar slots and operation order follow `ow174/game/mover.py`.

use std::{cmp::Ordering, collections::BTreeMap};

use crate::{
    commands::{FRAME_HALF, frame_distance},
    mover_ground::flat_unit,
    mover_settings::MoverSettings,
};

const DEFAULT_DIRECTION: [f32; 2] = [0.0, 1.0];
const THROTTLE_EPSILON: f32 = 1e-6;

const THROTTLE_FORWARD: u8 = 17;
const THROTTLE_BACKWARD: u8 = 18;
const THROTTLE_LATERAL: u8 = 19;
pub const JUMP_INTENT: u8 = 20;
pub const CROUCH_INTENT: u8 = 21;
pub const VELOCITY_MAGNITUDE: u8 = 32;
pub const VELOCITY_HORIZONTAL: u8 = 33;
pub const VELOCITY_Y: u8 = 34;
pub const VELOCITY_JUMP_RATIO: u8 = 35;
pub const LAND_LIMIT: u8 = 36;
pub(crate) const CAP_TINY: f64 = 1.1920929e-5;

const ACCEL_GROUND: usize = 1;
const ACCEL_AIR: usize = 2;
const ACCEL_TOUCHING: usize = 3;
const DECEL_GROUND: usize = 4;
const DECEL_GROUND_BASE: usize = 5;
const DECEL_GROUND_SPEED: usize = 6;
const DECEL_AIR: usize = 7;
const DECEL_AIR_BASE: usize = 8;
const DECEL_AIR_SPEED: usize = 9;
const JUMP_SPEED: usize = 10;
const GRAVITY: usize = 11;
const TERMINAL: usize = 13;
const TERMINAL_UP: usize = 14;
const GROUND_ACCEL: usize = 15;
const GROUND_DECEL: usize = 16;
pub(crate) const COLLISION: u8 = 22;
const STEP_DOWN: usize = 38;

pub use orpheus_statescript::script_modifiers::{Modifier, ModifierKey, Operation};
pub(crate) use orpheus_statescript::script_modifiers::{SPEED_SLOT, narrow};

#[derive(Clone, Copy, Debug)]
pub struct DirectionModifier {
    pub operation: Operation,
    pub value: [f32; 3],
}

fn throttle_length(right: f32, forward: f32) -> f32 {
    let squared = right * right + forward * forward;
    if squared <= THROTTLE_EPSILON {
        return 0.0;
    }
    let estimate = orpheus_collision::contact_math::rsqrt(squared);
    squared * ((3.0 - squared * (estimate * estimate)) * (estimate * 0.5))
}

fn flat_angle(x: f32, z: f32) -> f32 {
    if x != 0.0 || z != 0.0 {
        x.atan2(z)
    } else {
        0.0
    }
}

/// Apply channel-2 `MOVE_DIRECTION` vector mods to turn `(right, forward)` throttles while
/// preserving throttle length (`ow174/game/mover.py::directed_throttles`).
pub fn directed_throttles(
    mods: &[DirectionModifier],
    right: f32,
    forward: f32,
    facing: [f32; 2],
) -> [f32; 2] {
    if mods.is_empty() {
        return [right, forward];
    }
    let [fx, fz] = flat_unit(facing[0], facing[1]).unwrap_or(DEFAULT_DIRECTION);
    let reference = [fx, 0.0, fz];
    let mut direction = reference;
    for modifier in mods {
        direction = match modifier.operation {
            Operation::Add => [
                direction[0] + modifier.value[0],
                direction[1] + modifier.value[1],
                direction[2] + modifier.value[2],
            ],
            Operation::Multiply => [
                direction[0] * modifier.value[0],
                direction[1] * modifier.value[1],
                direction[2] * modifier.value[2],
            ],
            Operation::Set => modifier.value,
            Operation::Minimum | Operation::Maximum => direction,
        };
    }
    if direction == reference {
        return [right, forward];
    }
    let [x, z] = flat_unit(direction[0], direction[2]).unwrap_or(DEFAULT_DIRECTION);
    let length = throttle_length(right, forward);
    let angle = flat_angle(right, forward) - (flat_angle(x, z) - flat_angle(fx, fz));
    [angle.sin() * length, angle.cos() * length]
}

#[derive(Clone)]
struct Interval {
    modifier: Modifier,
    start: u32,
    end: Option<u32>,
}

#[derive(Clone, Default)]
pub struct ModifierTimeline {
    intervals: BTreeMap<ModifierKey, Interval>,
}

impl ModifierTimeline {
    /// Refresh the active states after a simulated command. Newly added or
    /// retired states take effect at the next frame. Retained states can
    /// refresh their value and priority without changing their start order.
    pub fn track(&mut self, frame: u32, active: &BTreeMap<ModifierKey, Modifier>) {
        let next = frame.wrapping_add(1);
        self.intervals.retain(|key, interval| {
            if interval.end.is_some_and(|end| reached(frame, end)) {
                return false;
            }
            if !active.contains_key(key) {
                interval.end.get_or_insert(next);
            }
            true
        });
        for (&key, &modifier) in active {
            let interval = self.intervals.entry(key).or_insert(Interval {
                modifier,
                start: next,
                end: None,
            });
            if interval.end.take().is_some() {
                interval.start = next;
                interval.modifier = modifier;
            } else {
                interval.modifier.value = modifier.value;
                interval.modifier.priority = modifier.priority;
            }
        }
    }

    pub fn at(&self, frame: u32) -> ActiveModifiers {
        let mut active: Vec<_> = self
            .intervals
            .values()
            .filter_map(|interval| {
                let age = frame_distance(frame, interval.start);
                (age < FRAME_HALF && interval.end.is_none_or(|end| !reached(frame, end)))
                    .then_some((age, interval.modifier))
            })
            .collect();
        // Stable sort keeps the iteration order of equal start and priority
        // values. The timeline already iterates by instance, state and slot.
        active.sort_by(|(left_age, left), (right_age, right)| {
            let priority = if left.priority == right.priority {
                Ordering::Equal
            } else {
                left.priority.total_cmp(&right.priority)
            };
            priority.then_with(|| right_age.cmp(left_age))
        });
        ActiveModifiers(active)
    }
}

fn reached(frame: u32, boundary: u32) -> bool {
    frame_distance(frame, boundary) < FRAME_HALF
}

#[derive(Default)]
pub struct ActiveModifiers(Vec<(u32, Modifier)>);

impl ActiveModifiers {
    pub fn intent(&self, index: u8, base: f64) -> f64 {
        self.0
            .iter()
            .map(|(_, modifier)| *modifier)
            .filter(|modifier| modifier.index == index)
            .fold(base, |value, modifier| modifier.apply(value))
    }

    pub(crate) fn character_collision(&self, base: &MoverSettings) -> bool {
        self.intent(COLLISION, f64::from(base.character_collision)) > CAP_TINY
    }

    /// Apply continuous per-tick velocity modifiers (retail slots 32..=35:
    /// 3D speed, horizontal speed, vertical velocity, jump-ratio velocity).
    /// E.g. Wrecking Ball Piledriver `160A:STU_316CFEF2_5` sets slot 34 = -98.0
    /// and slot 33 = 0.0 to slam straight downward.
    pub(crate) fn apply_velocity(&self, velocity: &mut [f64; 3], jump_speed: f64) {
        for &(_, modifier) in &self.0 {
            match modifier.index {
                VELOCITY_MAGNITUDE => {
                    let speed = velocity[0].hypot(velocity[1]).hypot(velocity[2]);
                    let next = modifier.apply(speed).max(0.0);
                    if speed > 1e-6 && next.is_finite() {
                        let scale = next / speed;
                        velocity[0] = narrow(velocity[0] * scale);
                        velocity[1] = narrow(velocity[1] * scale);
                        velocity[2] = narrow(velocity[2] * scale);
                    } else if next == 0.0 {
                        *velocity = [0.0; 3];
                    }
                }
                VELOCITY_HORIZONTAL => {
                    let hspeed = velocity[0].hypot(velocity[2]);
                    let next = modifier.apply(hspeed).max(0.0);
                    if hspeed > 1e-6 && next.is_finite() {
                        let scale = next / hspeed;
                        velocity[0] = narrow(velocity[0] * scale);
                        velocity[2] = narrow(velocity[2] * scale);
                    } else if next == 0.0 {
                        velocity[0] = 0.0;
                        velocity[2] = 0.0;
                    }
                }
                VELOCITY_Y => {
                    let next = modifier.apply(velocity[1]);
                    if next.is_finite() {
                        velocity[1] = narrow(next);
                    }
                }
                VELOCITY_JUMP_RATIO => {
                    let base = if jump_speed.abs() > 1e-6 {
                        velocity[1] / jump_speed
                    } else {
                        0.0
                    };
                    let next = modifier.apply(base);
                    if next.is_finite() {
                        velocity[1] = narrow(next * jump_speed);
                    }
                }
                _ => {}
            }
        }
    }

    pub(crate) fn throttles(&self, right: f64, forward: f64) -> (f64, f64) {
        if !self
            .0
            .iter()
            .any(|(_, modifier)| (THROTTLE_FORWARD..=THROTTLE_LATERAL).contains(&modifier.index))
        {
            return (right, forward);
        }
        // 104319 C33960 separates the forward/backward magnitudes and
        // restores the original lateral sign after evaluating the stack.
        let ahead = self.intent(THROTTLE_FORWARD, forward.max(0.0));
        let back = self.intent(THROTTLE_BACKWARD, -forward.min(0.0));
        let lateral = self.intent(THROTTLE_LATERAL, right.abs());
        let lateral = if right < 0.0 { -lateral } else { lateral };
        (
            lateral.clamp(-1.0, 1.0),
            narrow(ahead - back).clamp(-1.0, 1.0),
        )
    }

    pub fn settings(&self, base: &MoverSettings) -> MoverSettings {
        if self.0.is_empty() {
            return *base;
        }
        let mut slots = [None; STEP_DOWN + 1];
        slots[..10].fill(Some(1.0));
        for (index, value) in [
            (JUMP_SPEED, base.jump_speed),
            (GRAVITY, 1.0),
            (TERMINAL, base.terminal_velocity),
            (TERMINAL_UP, base.terminal_velocity_up),
            (GROUND_ACCEL, base.ground_gravity_accel),
            (GROUND_DECEL, base.ground_gravity_decel),
            (STEP_DOWN, 1.0),
        ] {
            slots[index] = Some(value);
        }
        for &(_, modifier) in &self.0 {
            if let Some(Some(value)) = slots.get_mut(usize::from(modifier.index)) {
                *value = modifier.apply(*value);
            }
        }
        let slot = |index: usize| slots[index].expect("initialized movement scalar");
        let nonnegative = |index| slot(index).max(0.0);
        let speed = nonnegative(usize::from(SPEED_SLOT));
        let ground_decel = nonnegative(DECEL_GROUND);
        let air_decel = nonnegative(DECEL_AIR);
        MoverSettings {
            run_forward: narrow(base.run_forward * speed),
            run_backward: narrow(base.run_backward * speed),
            run_strafe: narrow(base.run_strafe * speed),
            crouch_forward: narrow(base.crouch_forward * speed),
            crouch_backward: narrow(base.crouch_backward * speed),
            crouch_strafe: narrow(base.crouch_strafe * speed),
            accel_ground: narrow(base.accel_ground * nonnegative(ACCEL_GROUND)),
            accel_air: narrow(base.accel_air * nonnegative(ACCEL_AIR)),
            accel_air_touching: narrow(base.accel_air_touching * nonnegative(ACCEL_TOUCHING)),
            decel_ground_base: narrow(
                narrow(nonnegative(DECEL_GROUND_BASE) * ground_decel) * base.decel_ground_base,
            ),
            decel_ground_per_speed: narrow(ground_decel * base.decel_ground_per_speed),
            decel_ground_base_speed: narrow(
                narrow(speed * slot(DECEL_GROUND_SPEED)) * base.decel_ground_base_speed,
            ),
            decel_air_base: narrow(
                narrow(nonnegative(DECEL_AIR_BASE) * air_decel) * base.decel_air_base,
            ),
            decel_air_per_speed: narrow(air_decel * base.decel_air_per_speed),
            decel_air_base_speed: narrow(
                narrow(speed * slot(DECEL_AIR_SPEED)) * base.decel_air_base_speed,
            ),
            jump_speed: nonnegative(JUMP_SPEED),
            gravity_scale: narrow(slot(GRAVITY) * base.gravity_scale),
            terminal_velocity: nonnegative(TERMINAL),
            terminal_velocity_up: nonnegative(TERMINAL_UP),
            ground_gravity_accel: slot(GROUND_ACCEL),
            ground_gravity_decel: slot(GROUND_DECEL),
            character_collision: u32::from(self.character_collision(base)),
            step_down: narrow(base.step_down * nonnegative(STEP_DOWN)),
            ..*base
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn modifier(operation: Operation, value: f64, priority: f64) -> Modifier {
        Modifier {
            index: SPEED_SLOT,
            operation,
            value,
            priority,
        }
    }

    #[test]
    fn intervals_switch_on_the_next_frame_across_wrap() {
        let key = ModifierKey {
            instance: 1,
            state: 22,
            slot: 0,
        };
        let base = MoverSettings::default();
        let mut timeline = ModifierTimeline::default();
        let sprint = BTreeMap::from([(key, modifier(Operation::Multiply, 1.5, 0.0))]);
        timeline.track(u32::MAX, &sprint);
        assert_eq!(timeline.at(u32::MAX).settings(&base), base);
        assert_eq!(timeline.at(0).settings(&base).run_forward, 8.25);
        timeline.track(0, &BTreeMap::new());
        assert_eq!(timeline.at(0).settings(&base).run_forward, 8.25);
        assert_eq!(timeline.at(1).settings(&base), base);
        timeline.track(1, &BTreeMap::new());
        assert!(timeline.intervals.is_empty());
        timeline.track(2, &sprint);
        assert_eq!(timeline.at(2).settings(&base), base);
        assert_eq!(timeline.at(3).settings(&base).run_forward, 8.25);
    }

    #[test]
    fn throttle_modifiers_split_axes_and_preserve_the_command_bytes() {
        use crate::{commands::Command, mover_kinematics::MotionInput};

        let command = Command {
            forward: -127,
            right: -127,
            ..Command::default()
        };
        let input = MotionInput::from(&command);
        let mut timeline = ModifierTimeline::default();
        let active = BTreeMap::from([
            (
                ModifierKey {
                    instance: 1,
                    state: 1,
                    slot: 0,
                },
                Modifier {
                    index: THROTTLE_FORWARD,
                    ..modifier(Operation::Set, 1.0, 0.0)
                },
            ),
            (
                ModifierKey {
                    instance: 1,
                    state: 1,
                    slot: 1,
                },
                Modifier {
                    index: THROTTLE_BACKWARD,
                    ..modifier(Operation::Multiply, 0.5, 0.0)
                },
            ),
            (
                ModifierKey {
                    instance: 1,
                    state: 1,
                    slot: 2,
                },
                Modifier {
                    index: THROTTLE_LATERAL,
                    ..modifier(Operation::Multiply, 2.0, 0.0)
                },
            ),
        ]);
        timeline.track(10, &active);
        assert_eq!(input.with_modifiers(&timeline.at(10)), input);
        let applied = timeline.at(11);
        assert_eq!(applied.throttles(-1.0, -1.0), (-1.0, 0.5));
        let adjusted = input.with_modifiers(&applied);
        assert_ne!(adjusted, input);
        assert_eq!(adjusted.throttles, [-127, -127]);
        timeline.track(11, &BTreeMap::new());
        assert_eq!(input.with_modifiers(&timeline.at(12)), input);
    }

    #[test]
    fn application_orders_priority_then_first_frame_then_state_slot() {
        let key = |state| ModifierKey {
            instance: 1,
            state,
            slot: 0,
        };
        let mut timeline = ModifierTimeline::default();
        let mut active = BTreeMap::from([(key(9), modifier(Operation::Set, 3.0, 0.0))]);
        timeline.track(1, &active);
        active.insert(key(3), modifier(Operation::Multiply, 3.0, 0.0));
        active.insert(key(2), modifier(Operation::Add, 1.0, 0.0));
        active.insert(key(1), modifier(Operation::Minimum, 1.0, 1.0));
        timeline.track(2, &active);
        assert_eq!(timeline.at(2).intent(SPEED_SLOT, 1.0), 3.0);
        assert_eq!(timeline.at(3).intent(SPEED_SLOT, 1.0), 1.0);
        active.remove(&key(1));
        active.get_mut(&key(9)).unwrap().value = 2.0;
        timeline.track(3, &active);
        assert_eq!(timeline.at(4).intent(SPEED_SLOT, 1.0), 9.0);
    }
}
