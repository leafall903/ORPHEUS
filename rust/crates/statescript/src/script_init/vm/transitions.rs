//! StateScript state ticking, timers, watches, switches, registration, and teardown.

use std::{num::NonZeroU32, sync::Arc};

use serde_json::Value as Json;

use crate::{
    script_expr::{self, Scope, equal, truthy},
    script_graph::{Node, asset_guid},
    script_runtime::frame_start,
    script_wire::{Value, Variables},
};

use super::super::{
    ABILITY_HELD, MAX_PLUG_DEPTH, SUBGRAPH_STATES, SWITCH_TO_FALSE, SWITCH_TO_TRUE,
    machine::{
        Event, ObservationInput, ObservedSample, RegisteredClass, Startup, StateKey,
        UnavailableSample, VarKey,
    },
    shapes::{
        array_field, branch_path, captured_aim_lock_shape, case_path, configured, field,
        is_unlinked_plug, parent_owned_graph_duration, pool_number, raw_indices, registered_state,
        same, source_key, stored,
    },
    types::{InitFault, Issue, StateKind},
    world::{Site, World},
};
use super::Vm;

enum ObservationResult {
    Value(Value),
    Unavailable,
    Invalid,
}

impl<'graph, 'world, 'machine, W: World> Vm<'graph, 'world, 'machine, W> {
    pub(in crate::script_init) fn tick_states(&mut self, id: u16) -> Result<bool, InitFault> {
        let now = self.machine.now_ms;
        let mut samples_queued = false;
        let due: Vec<_> = self
            .machine
            .ticking
            .get(&id)
            .into_iter()
            .flatten()
            .filter_map(|(&raw, &last)| (last != now).then_some(raw))
            .collect();
        for raw in due {
            self.spend("polled state tick")?;
            if let Some(last) = self
                .machine
                .ticking
                .get_mut(&id)
                .and_then(|states| states.get_mut(&raw))
            {
                let elapsed_ms = now.saturating_sub(*last);
                *last = now;
                let key = StateKey { instance: id, raw };
                match self
                    .state(key)
                    .filter(|state| state.active)
                    .map(|state| state.kind)
                {
                    Some(StateKind::BooleanSwitch(_)) => {
                        self.check_switch(key, 0)?;
                    }
                    Some(StateKind::DelayedSwitch { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("delayed switch");
                        self.recheck_delayed_switch(key, node, 0)?;
                    }
                    Some(StateKind::HealthPool { pool: Some(pool) }) => {
                        let node = self.graph(id).state(usize::from(raw)).expect("health pool");
                        if let Some(current) =
                            self.world.health_pool_current(self.site(id, node), pool)
                        {
                            if current == 0.0 && !field(&node.fields, "m_7BE02F89").is_null() {
                                self.post(id, Event::Finish(raw))?;
                            } else {
                                self.publish_health_pool(key, node, current)?;
                            }
                        }
                    }
                    Some(StateKind::ShieldRecharge) => {
                        self.tick_shield_recharge(key, elapsed_ms)?
                    }
                    Some(StateKind::NamedShieldDecay) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("named shield decay");
                        self.update_named_shield_decay(key, node, elapsed_ms)?;
                    }
                    Some(StateKind::WitchStaffHealing) => {
                        self.tick_witch_staff_healing(key, elapsed_ms)?
                    }
                    Some(StateKind::SummonerBeamRay) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("Summoner beam ray");
                        self.refresh_summoner_beam_ray(key, node)?;
                    }
                    Some(StateKind::GroundRay) => {
                        let node = self.graph(id).state(usize::from(raw)).expect("ground ray");
                        self.refresh_ground_ray(key, node)?;
                    }
                    Some(StateKind::SummonerBeamDamage) => {
                        self.tick_summoner_beam_damage(key, elapsed_ms)?
                    }
                    Some(StateKind::WitchStaffEffect) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("Witch staff effect");
                        self.refresh_witch_staff_effect(key, node)?;
                    }
                    Some(StateKind::MovementMod) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("movement modifier");
                        self.refresh_movement_modifier(key, node)?;
                    }
                    Some(StateKind::LogicalButton { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("logical button state");
                        self.refresh_logical_button_binding(key, node)?;
                    }
                    Some(StateKind::MovementSample { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("movement sample");
                        self.update_movement_sample(key, node, 0)?;
                    }
                    Some(StateKind::Switch { .. }) => self.post(id, Event::Reevaluate(raw))?,
                    Some(StateKind::Shockwave { .. }) => self.tick_shockwave(key)?,
                    Some(StateKind::StaticTrace { .. }) => self.tick_static_trace(key)?,
                    Some(StateKind::MoveTo { .. }) => {
                        let node = self.graph(id).state(usize::from(raw)).expect("move to");
                        self.refresh_world_move_to(key, node)?;
                    }
                    Some(StateKind::ModifyHealth) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("modify health state");
                        self.world.modify_health(self.site(id, node), elapsed_ms);
                    }
                    Some(StateKind::GenericModifyHealth {
                        continuous: true, ..
                    }) => {
                        // Retail continuous beams (Winston Tesla, Zarya, Symmetra, Moira)
                        // settle at 20 Hz (~48 ms) rather than flooding 62.5 Hz hit notices.
                        if elapsed_ms < 48 {
                            if let Some(last) = self
                                .machine
                                .ticking
                                .get_mut(&id)
                                .and_then(|states| states.get_mut(&raw))
                            {
                                *last = now.saturating_sub(elapsed_ms);
                            }
                            continue;
                        }
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("generic modify health state");
                        self.apply_generic_modify_health(key, node, Some(elapsed_ms))?;
                    }
                    Some(StateKind::GenericModifyHealth { on_enter: true, .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("generic modify health state");
                        let applied = self.apply_generic_modify_health_on_enter(key, node)?;
                        if applied
                            && (field(&node.fields, "m_8B707686").as_u64() == Some(1)
                                || node.links("m_onFinishedPlug").next().is_some())
                        {
                            self.post(id, Event::Finish(raw))?;
                        }
                    }
                    Some(StateKind::GenericContactSweep) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("generic contact sweep state");
                        self.refresh_generic_contact_sweep(key, node)?;
                    }
                    Some(StateKind::TargetAura { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("target aura state");
                        self.refresh_target_aura(key, node, elapsed_ms)?;
                    }
                    Some(StateKind::ConstantAccel) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("constant accel state");
                        self.tick_constant_accel(key, node, elapsed_ms)?;
                    }
                    Some(StateKind::WallClimb) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("wall climb state");
                        self.tick_wall_climb(key, node, elapsed_ms)?;
                    }
                    Some(StateKind::Grapple) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("grapple state");
                        self.tick_grapple(key, node, elapsed_ms)?;
                    }
                    Some(StateKind::FilterArray) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("filter array state");
                        self.refresh_filter_array(key, node)?;
                    }
                    Some(StateKind::RecallHistory) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("recall history state");
                        self.tick_recall_history(key, node)?;
                    }
                    Some(StateKind::BallRoll { .. }) => {}
                    Some(StateKind::HitTracker { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("hit tracker state");
                        self.tick_hit_tracker(key, node)?;
                    }
                    Some(StateKind::RayCast) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("ray cast state");
                        self.refresh_ray_cast(key, node)?;
                    }
                    Some(StateKind::EntityAttach { target }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("entity attach state");
                        self.tick_entity_attach(key, node, target)?;
                    }
                    Some(StateKind::KinematicsControl { mode }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("kinematics control state");
                        self.tick_kinematics_control(key, node, mode)?;
                    }
                    Some(StateKind::EntityStatus { mode }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("entity status state");
                        self.begin_entity_status(key, node, mode)?;
                    }
                    Some(StateKind::PlacementCast { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("placement cast state");
                        self.refresh_placement_cast(key, node)?;
                    }
                    Some(StateKind::ProximityTrigger { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("proximity trigger state");
                        self.tick_proximity_trigger(key, node)?;
                    }
                    Some(StateKind::PlacementSelector { .. }) => {
                        self.tick_placement_selector(key)?
                    }
                    Some(StateKind::ForCode) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("for-code state");
                        self.publish_for_code(key, node)?;
                    }
                    Some(StateKind::TrackedTargets) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("tracked target state");
                        self.refresh_tracked_targets(key, node)?;
                    }
                    Some(StateKind::GenericTrackedTargets) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("generic tracked target state");
                        self.refresh_generic_tracked_targets(key, node)?;
                        self.try_complete(key, node, 0)?;
                    }
                    Some(StateKind::ViewTargets { next_sample })
                        if self.machine.frame >= next_sample =>
                    {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("view tracker state");
                        if self.sample_view_targets(key, node)? {
                            self.try_complete(key, node, 0)?;
                        }
                    }
                    Some(StateKind::DeadeyeEffects) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("Deadeye effect state");
                        self.refresh_deadeye_effects(key, node)?;
                    }
                    Some(StateKind::ExplosionContact) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("contact state");
                        self.refresh_contact(key, node)?;
                    }
                    Some(StateKind::AiBehavior { record: Some(_) }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("AI behavior state");
                        self.update_ai_behavior(key, node)?;
                    }
                    Some(StateKind::Ability { .. }) => self.activate_ability(key, 0)?,
                    Some(StateKind::ChaseVar) => self.tick_chase(key)?,
                    Some(StateKind::Watch) => self.check_watch(key)?,
                    Some(StateKind::Observe { sampled_frame })
                        if sampled_frame < self.machine.frame =>
                    {
                        samples_queued |= self.sample_observed_value(key)?;
                    }
                    Some(StateKind::TrackEntities { sampled_frame })
                        if sampled_frame < self.machine.frame =>
                    {
                        self.sample_target_children(key)?;
                    }
                    Some(StateKind::LivingBodyArray) => self.refresh_living_body_array(key)?,
                    Some(StateKind::AiFacing(Some(record))) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("AI facing state");
                        self.update_ai_facing(key, node, Some(record))?;
                    }
                    Some(StateKind::Wait) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("elapsed wait");
                        self.check_elapsed_wait(key, node)?;
                    }
                    Some(StateKind::WaitFrames { target }) if target <= self.machine.frame => {
                        self.post(id, Event::Finish(raw))?;
                    }
                    _ => (),
                }
            }
        }
        Ok(samples_queued)
    }

    pub(in crate::script_init) fn clear_tick(&mut self, key: StateKey) {
        if let Some(states) = self.machine.ticking.get_mut(&key.instance) {
            states.shift_remove(&key.raw);
            if states.is_empty() {
                self.machine.ticking.remove(&key.instance);
            }
        }
    }

    pub(in crate::script_init) fn arm_wait(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let death_generation = match self.origin {
            crate::script_runtime::DispatchOrigin::BodyDeath(generation) => Some(generation),
            _ => None,
        };
        if let Some(state) = self.state_mut(key) {
            state.death_generation = death_generation;
        }
        if configured(node, "m_BF1A93B0") {
            self.register_tick(key, self.machine.now_ms);
            return self.check_elapsed_wait(key, node);
        }
        let Some(milliseconds) = self.wait_milliseconds(key, node, false) else {
            return Ok(());
        };
        if milliseconds <= 0.0 {
            self.post(id, Event::Finish(key.raw))
        } else {
            self.post_after(id, milliseconds as u64, Event::Timer(key.raw, 1))
        }
    }

    fn check_elapsed_wait(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let Some(milliseconds) = self.wait_milliseconds(key, node, true) else {
            return Ok(());
        };
        let wait = self.state(key).expect("active wait");
        let start = wait.start_ms;
        let death_generation = wait.death_generation;
        if self.machine.now_ms.saturating_sub(start) >= milliseconds as u64 {
            if let Some(generation) = death_generation {
                self.machine.queues.push_with_origin(
                    key.instance,
                    self.machine.now_ms,
                    Event::Finish(key.raw),
                    crate::script_runtime::DispatchOrigin::BodyDeath(generation),
                )?;
            } else {
                self.post(key.instance, Event::Finish(key.raw))?;
            }
        }
        Ok(())
    }

    fn wait_milliseconds(&mut self, key: StateKey, node: &Node, dynamic: bool) -> Option<f32> {
        let value = self.evaluate(
            key.instance,
            node,
            field(&node.fields, "m_timeout"),
            (!dynamic).then_some(key),
        )?;
        let milliseconds = ((script_expr::to_float(&value) * 1000.0) as f32).round();
        if milliseconds.is_finite()
            && (!dynamic
                || (matches!(value, Value::Integer(_) | Value::Float(_))
                    && f64::from(milliseconds) < u64::MAX as f64))
        {
            return Some(milliseconds);
        }
        self.report(
            self.site(key.instance, node),
            Issue::UnsupportedField("Wait timeout"),
        );
        None
    }

    pub(super) fn arm_parent_owned_duration(
        &mut self,
        key: StateKey,
        node: &Node,
        duration: &Json,
    ) -> Result<(), InitFault> {
        let Some(Value::Float(seconds)) = self.evaluate(key.instance, node, duration, None) else {
            return Ok(());
        };
        let milliseconds = ((seconds * 1000.0) as f32).round().max(0.0);
        if f64::from(milliseconds) >= u64::MAX as f64 {
            self.report(
                self.site(key.instance, node),
                Issue::UnsupportedField("parent-owned graph duration"),
            );
            return Ok(());
        }
        self.post_after(key.instance, milliseconds as u64, Event::Timer(key.raw, 1))
    }

    pub(in crate::script_init) fn resolve_anim_key(
        &mut self,
        id: u16,
        node: &Node,
    ) -> Option<(u64, u64, u64)> {
        let alias_field = field(&node.fields, "m_11E0A658");
        let alias = if alias_field.is_null() {
            return None;
        } else {
            match self.evaluate(id, node, alias_field, None)? {
                Value::Asset(guid) => guid,
                Value::Integer(0) | Value::Null => 0,
                Value::Float(0.0) => 0,
                _ => return None,
            }
        };
        if alias == 0 {
            return Some((0, 0, 0));
        }
        let category = match field(&node.fields, "m_22326315") {
            field if field.is_null() => 0,
            field => match self.evaluate(id, node, field, None)? {
                Value::Asset(guid) => guid,
                _ => 0,
            },
        };
        let mut blend_tree = 0u64;
        if let Some(instance) = self.machine.snapshot.instances.get(&id) {
            let active_trees: Vec<(u16, &Node)> = instance
                .states
                .iter()
                .filter_map(|(&raw, state)| {
                    if !state.active || state.ending {
                        return None;
                    }
                    let candidate = self.graph(id).state(usize::from(raw))?;
                    (candidate.class == "STU_A875CA49").then_some((raw, candidate))
                })
                .collect();
            if let Some((_, tree_node)) = active_trees.last() {
                if let Some(Value::Asset(guid)) =
                    self.evaluate(id, tree_node, field(&tree_node.fields, "m_65D335ED"), None)
                {
                    blend_tree = guid;
                }
            }
        }
        Some((blend_tree, alias, category))
    }

    pub(in crate::script_init) fn arm_animation(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let looping = field(&node.fields, "m_37EE887D");
        if !looping.is_null() {
            if let Some(value) = self.evaluate(id, node, looping, None) {
                if truthy(&value) {
                    return Ok(());
                }
            } else {
                return Ok(());
            }
        }
        let duration_field = field(&node.fields, "m_86701C3E");
        let mut authored_seconds = if duration_field.is_null() {
            None
        } else {
            let Some(seconds) = self.evaluate(id, node, duration_field, None) else {
                return Ok(());
            };
            Some(script_expr::to_float(&seconds))
        };
        if authored_seconds.is_none_or(|s| s < f64::from(f32::from_bits(0x38FA_0000))) {
            if let Some((blend_tree, alias, category)) = self.resolve_anim_key(id, node) {
                let site = self.site(id, node);
                if let Some(resolved) = self
                    .world
                    .animation_duration(site, blend_tree, alias, category)
                {
                    let speed_field = field(&node.fields, "m_speedScalar");
                    let scalar = if speed_field.is_null() {
                        1.0
                    } else {
                        let Some(val) = self.evaluate(id, node, speed_field, None) else {
                            return Ok(());
                        };
                        script_expr::to_float(&val)
                    };
                    let seconds = if scalar > 0.0 {
                        resolved / scalar
                    } else {
                        resolved
                    };
                    authored_seconds = Some(f64::from(seconds as f32));
                }
            }
        }
        if authored_seconds.is_none()
            && !looping.is_null()
            && self
                .world
                .admits_generic_player_runtime(self.site(id, node))
            && (!is_unlinked_plug(&node.fields, "m_onEndPlug", "STUStatescriptOutputPlug")
                || !is_unlinked_plug(&node.fields, "m_onFinishedPlug", "STUStatescriptOutputPlug"))
        {
            // Non-looping transition clips with an explicit end/finished link
            // (e.g. Wrecking Ball 0x1203:Anim_41 -> looping Anim_40) need a
            // fallback finish duration when no anim_timing_174.json table is
            // present; otherwise the server keeps the one-shot active forever
            // and the client reverts to idle pose when the clip finishes.
            authored_seconds = Some(0.4);
        }
        let Some(seconds) = authored_seconds else {
            let client_removes_zombardier_animation = (self.graph(id).index, node.pos)
                == (0x0ca1, 58)
                && field(&node.fields, "m_154DBA91")["$"] == "STUConfigVarBool"
                && field(&node.fields, "m_154DBA91")["m_value"].as_u64() == Some(1)
                && is_unlinked_plug(&node.fields, "m_onEndPlug", "STUStatescriptOutputPlug");
            // Without m_playOverDuration the length is the animation asset's,
            // which the reference does not know either: it arms no timer and
            // no finish, so the state stays on until a transition or its
            // parent ends it. Accept the untimed idle shape and Zombardier's
            // client-removed firing animation when they have no timer links.
            if (!field(&node.fields, "m_154DBA91").is_null()
                && !client_removes_zombardier_animation
                && !self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)))
                || !is_unlinked_plug(&node.fields, "m_A3B86BB0", "STUStatescriptOutputPlug")
                || !is_unlinked_plug(&node.fields, "m_onFinishedPlug", "STUStatescriptOutputPlug")
            {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("animation asset duration"),
                );
            }
            return Ok(());
        };
        let delay = ((seconds * 1000.0) as f32).round();
        if !delay.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("animation duration"),
            );
            return Ok(());
        }
        let removes_self = field(&node.fields, "m_154DBA91");
        let client_ends = if removes_self.is_null() {
            false
        } else {
            let Some(value) = self.evaluate(id, node, removes_self, None) else {
                return Ok(());
            };
            truthy(&value)
        };
        let delay_ms = delay.max(0.0) as u64;
        self.post_after(id, delay_ms, Event::Timer(key.raw, 1))?;
        if !client_ends {
            self.post_after(id, delay_ms, Event::Finish(key.raw))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn arm_frame_wait(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(value) = self.evaluate(id, node, field(&node.fields, "m_3016B9A1"), None) else {
            return Ok(());
        };
        let count = script_expr::to_int(&value, 0) as u32;
        let target = self.machine.frame.wrapping_add(count);
        self.state_mut(key).expect("active frame wait").kind = StateKind::WaitFrames { target };
        self.register_tick(key, self.machine.now_ms);
        if target <= self.machine.frame {
            self.post(id, Event::Finish(key.raw))
        } else {
            Ok(())
        }
    }

    pub(in crate::script_init) fn stamp_cinematic_time_scale(
        &mut self,
        key: StateKey,
        node: &Node,
    ) {
        let Some(delay) =
            self.evaluate(key.instance, node, field(&node.fields, "m_17D12F6B"), None)
        else {
            return;
        };
        let delay = script_expr::to_int(&delay, 0).max(0) as u32;
        let boundary_frame = self.machine.frame.wrapping_add(delay);
        self.state_mut(key)
            .expect("cinematic time scale state")
            .kind = StateKind::CinematicTimeScale { boundary_frame };
    }

    pub(in crate::script_init) fn arm_phase_transition(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let mut durations = [0; 3];
        for (duration, name) in durations
            .iter_mut()
            .zip(["m_AD8C28BA", "m_C69C2A73", "m_D7ED647E"])
        {
            let Some(value) = self
                .evaluate(key.instance, node, field(&node.fields, name), None)
                .and_then(pool_number)
                .filter(|value| *value >= 0.0 && *value < 2_147_483.0)
            else {
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField("phase transition duration"),
                );
                return Ok(());
            };
            *duration = (value * 1000.0).round() as u64;
        }
        let [enter, hold, exit] = durations;
        self.post_after(key.instance, enter, Event::Timer(key.raw, 1))?;
        self.post_after(key.instance, enter + hold + exit, Event::Abort(key.raw))
    }

    pub(in crate::script_init) fn timer(
        &mut self,
        key: StateKey,
        parameter: u16,
    ) -> Result<(), InitFault> {
        let Some(state) = self.state(key).filter(|state| state.active) else {
            return Ok(());
        };
        let kind = state.kind;
        if matches!(kind, StateKind::StaticTrace { .. }) && parameter == 1 {
            return self.tick_static_trace(key);
        }
        if let StateKind::Interval {
            period_frames,
            next_frame: Some(next),
        } = kind
        {
            if parameter == 1 && next <= self.machine.frame {
                self.fire_interval(key, period_frames, next)?;
            }
            return Ok(());
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("active timer state");
        if parameter == 1
            && (matches!(kind, StateKind::Wait | StateKind::RecallRewind)
                || (kind == StateKind::Plain
                    && (parent_owned_graph_duration(node).is_some()
                        || crate::script_init::shapes::timed_position_duration(node).is_some())))
        {
            self.post(key.instance, Event::Finish(key.raw))?;
        }
        if kind == StateKind::VoiceStimulus {
            return self.voice_timer(key, node, parameter);
        }
        if kind == StateKind::Defer && parameter == 1 {
            self.follow(key.instance, node, "m_FD0C0646", Some(key), 0)?;
            if self.state(key).is_some_and(|state| state.active) {
                self.follow(key.instance, node, "m_8E484B2E", Some(key), 0)?;
            }
            return Ok(());
        }
        if matches!(kind, StateKind::LogicalButton { .. })
            && parameter == 1
            && !self.button_held(key, node)
        {
            self.set_button_down(key, node, false, 0)?;
        }
        if kind == StateKind::PhaseTransition && parameter == 1 {
            self.follow(key.instance, node, "m_82A8A67E", Some(key), 0)?;
        }
        if kind == StateKind::Anim && parameter == 1 {
            self.follow(key.instance, node, "m_A3B86BB0", Some(key), 0)?;
        }
        if let StateKind::BossIntroVoice(cue) = kind {
            if parameter == 1 {
                let site = self.site(key.instance, node);
                if let Some(duration_ms) = self.world.play_boss_intro_voice(site, cue) {
                    self.post_after(key.instance, duration_ms, Event::Finish(key.raw))?;
                } else {
                    self.report(site, Issue::UnsupportedField("Boss intro voice service"));
                }
            }
        }
        if kind == StateKind::VictoryVoice && parameter == 1 {
            let site = self.site(key.instance, node);
            if let Some(actor) =
                self.evaluate(key.instance, node, field(&node.fields, "m_8C8C5285"), None)
            {
                if let Some(duration_ms) = self.world.play_victory_voice(site, &actor) {
                    self.post_after(key.instance, duration_ms, Event::Finish(key.raw))?;
                } else {
                    self.report(site, Issue::UnsupportedField("victory voice service"));
                }
            }
        }
        if let StateKind::GateVoice(stimulus) = kind {
            if parameter == 1 {
                let attacker = field(&node.fields, "m_8C8C5285");
                let attacker = if attacker.is_null() {
                    None
                } else {
                    match self.evaluate(key.instance, node, attacker, None) {
                        Some(Value::Entity(entity)) => Some(entity),
                        _ => None,
                    }
                };
                let site = self.site(key.instance, node);
                if let Some(duration_ms) = self.world.play_gate_voice(site, stimulus, attacker) {
                    self.post_after(key.instance, duration_ms, Event::Finish(key.raw))?;
                } else {
                    self.report(site, Issue::UnsupportedField("gate voice service"));
                }
            }
        }
        if let StateKind::DelayedSwitch { current, undecided } = kind {
            let branch = match parameter {
                SWITCH_TO_TRUE => Some(true),
                SWITCH_TO_FALSE => Some(false),
                _ => None,
            };
            if let Some(branch) = branch {
                self.state_mut(key)
                    .expect("active delayed switch")
                    .delayed_timers[usize::from(branch)] = false;
                if undecided || current != branch {
                    self.set_delayed_branch(key, node, branch, 0)?;
                }
            }
        }
        if matches!(kind, StateKind::Ability { .. }) && matches!(parameter, 1 | 2) {
            self.activate_ability(key, 0)?;
        }
        if matches!(kind, StateKind::WeaponVolley { .. }) {
            match parameter {
                1 => {
                    self.follow(key.instance, node, "m_onMinShotsPlug", Some(key), 0)?;
                }
                2 => self.post(key.instance, Event::Finish(key.raw))?,
                _ => (),
            }
        }
        self.try_complete(key, node, 0)
    }

    pub(in crate::script_init) fn spawn_child(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<Option<u16>, InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let Some(guid) = node.subscript_graph_asset() else {
            self.report(site, Issue::InvalidSubScript);
            return Ok(None);
        };
        let graph = u32::from(guid as u16);
        if self.graphs.graph(graph).is_none() {
            self.report(site, Issue::MissingGraph(graph));
            return Ok(None);
        }
        let mut vars = Variables::new();
        let mut entity_vars = Variables::new();
        if (site.graph, node.pos, graph) == (0x0c95, 7, 0x0ce2) {
            let schema = node.field("m_36F7B64C.m_36F7B64C").unwrap_or(&Json::Null);
            if field(schema, "$").as_str() != Some("STU_29C4C486")
                || !field(schema, "m_CB2634D7").is_null()
                || field(schema, "m_75408640").as_u64() != Some(0)
                || !field(schema, "m_17BB822A").is_array()
            {
                self.report(site, Issue::UnsupportedField("subscript schema override"));
                return Ok(None);
            }
            for row in array_field(schema, "m_17BB822A") {
                self.spend("subscript schema override")?;
                let identifier = asset_guid(field(row, "m_0D09D2D9")).filter(|id| {
                    matches!(id, 0x0d80_0000_0000_01af | 0x0d80_0000_0000_01f5)
                        && field(row, "$").as_str() == Some("STU_6364CD4F")
                });
                let Some(identifier) = identifier else {
                    self.report(site, Issue::UnsupportedField("subscript schema override"));
                    return Ok(None);
                };
                let Some(value) = self.evaluate(id, node, field(row, "m_value"), None) else {
                    return Ok(None);
                };
                vars.insert(u32::from(identifier as u16), value);
            }
        }
        for item in array_field(&node.fields, "m_89402D74") {
            if !item.is_object() {
                continue;
            }
            let target = match script_expr::lvalue(field(item, "m_out_Var")) {
                Ok(Some(target)) => target,
                Ok(None) => continue,
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(None);
                }
            };
            let config = field(item, "m_value");
            let value = self.assignment_value(id, node, config, Value::Null)?;
            let Some(value) = value else {
                return Ok(None);
            };
            match target.scope {
                Scope::Instance => vars.insert(target.id, value),
                Scope::Entity => entity_vars.insert(target.id, value),
            };
        }
        let scope = self.instance_scope(id);
        let child = self.machine.next_instance_id(scope.is_isolated())?;
        if self.create(
            child,
            graph,
            Some((id, key.raw)),
            vars,
            Startup::Ordinary,
            scope,
        )? {
            // The reference sets child binding bases directly before Entry.
            self.machine.snapshot.variables.extend(entity_vars);
            return Ok(Some(child));
        }
        Ok(None)
    }

    pub(in crate::script_init) fn try_complete(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        for (index, _) in array_field(&node.fields, "m_transitionPlug")
            .iter()
            .enumerate()
        {
            if self.follow(
                key.instance,
                node,
                &format!("m_transitionPlug[{index}]"),
                Some(key),
                depth,
            )? {
                self.post(key.instance, Event::Abort(key.raw))?;
                break;
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn changed(&mut self, key: StateKey) -> Result<(), InitFault> {
        if let Some(state) = self.state_mut(key) {
            state.pending_change = false;
            if state.active && matches!(state.kind, StateKind::Switch { .. }) {
                self.post(key.instance, Event::Reevaluate(key.raw))?;
            } else if state.active
                && !state.pending_timer
                && matches!(
                    state.kind,
                    StateKind::BooleanSwitch(_)
                        | StateKind::DelayedSwitch { .. }
                        | StateKind::AiBehavior { record: Some(_) }
                )
            {
                state.pending_timer = true;
                self.post(key.instance, Event::Reevaluate(key.raw))?;
            } else if state.active && matches!(state.kind, StateKind::Stack { .. }) {
                self.update_stack(key)?;
            } else if state.active && state.kind == StateKind::AiControlSetting {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("AI control setting state");
                self.update_ai_control_setting(key, node);
            } else if state.active && state.kind == StateKind::MovementMod {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("movement modifier");
                self.refresh_movement_modifier(key, node)?;
            } else if state.active && matches!(state.kind, StateKind::LogicalButton { .. }) {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("logical button state");
                self.refresh_logical_button_binding(key, node)?;
            } else if state.active && state.kind == StateKind::ExplosionContact {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("contact state");
                self.refresh_contact(key, node)?;
            } else if state.active && state.kind == StateKind::PhaseScriptList {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("phase script list");
                self.refresh_phase_scripts(key, node)?;
            } else if state.active && state.kind == StateKind::Watch {
                self.check_watch(key)?;
            } else if state.active && state.kind == StateKind::LivingBodyArray {
                self.refresh_living_body_array(key)?;
            } else if state.active && matches!(state.kind, StateKind::HealthPool { pool: Some(_) })
            {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("active health pool");
                let site = self.site(key.instance, node);
                if !self.world.admits_generic_player_runtime(site) {
                    self.report(
                        site,
                        Issue::UnsupportedField("changing health pool capacity"),
                    );
                }
            }
        }
        if let Some(state) = self.state(key).filter(|state| state.active) {
            let graph = self.graph(key.instance);
            if let Some(node) = graph.state(usize::from(key.raw)) {
                if state.kind == StateKind::Anim
                    || !array_field(&node.fields, "m_transitionPlug").is_empty()
                {
                    self.try_complete(key, node, 0)?;
                }
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn schedule_interval(
        &mut self,
        key: StateKey,
        next_frame: Option<u32>,
    ) -> Result<(), InitFault> {
        if let Some(next) = next_frame {
            let delay = frame_start(next).saturating_sub(self.machine.now_ms);
            self.post_after(key.instance, delay, Event::Timer(key.raw, 1))?;
        }
        Ok(())
    }

    fn fire_interval(
        &mut self,
        key: StateKey,
        period_frames: NonZeroU32,
        scheduled_frame: u32,
    ) -> Result<(), InitFault> {
        let period = period_frames.get();
        let frame = self.machine.frame;
        // Drop missed pulses while keeping the original cadence.
        let next_frame = frame.checked_add(period - (frame - scheduled_frame) % period);
        self.state_mut(key).expect("active interval").kind = StateKind::Interval {
            period_frames,
            next_frame,
        };
        self.schedule_interval(key, next_frame)?;

        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("interval state");
        self.follow(key.instance, node, "on_interval", Some(key), 0)?;
        if self
            .state(key)
            .is_some_and(|state| state.active && !state.ending)
        {
            self.try_complete(key, node, 0)?;
        }
        Ok(())
    }

    fn sample_observed_value(&mut self, key: StateKey) -> Result<bool, InitFault> {
        let id = key.instance;
        let frame = self.machine.frame;
        let Some(node) = self.graph(id).state(usize::from(key.raw)) else {
            return Ok(false);
        };
        let Some(state) = self
            .state_mut(key)
            .filter(|state| state.active && !state.ending)
        else {
            return Ok(false);
        };
        let activation = state.activation;
        state.kind = StateKind::Observe {
            sampled_frame: frame,
        };
        let site = self.site(id, node);
        let value = match self.observation_result(site, field(&node.fields, "value")) {
            ObservationResult::Value(value) => value,
            ObservationResult::Unavailable => {
                let has_hook = node
                    .field("on_unavailable")
                    .is_some_and(|plug| !plug.is_null());
                if has_hook
                    && self.state(key).is_some_and(|state| {
                        state.active && !state.ending && state.activation == activation
                    })
                {
                    self.post(
                        id,
                        Event::ObserveSample {
                            raw: key.raw,
                            activation,
                            frame,
                            available: false,
                        },
                    )?;
                    return Ok(true);
                }
                return Ok(false);
            }
            ObservationResult::Invalid => return Ok(false),
        };
        let Some(state) = self
            .state_mut(key)
            .filter(|state| state.active && !state.ending && state.activation == activation)
        else {
            return Ok(false);
        };
        let changed = state.watch_seen.as_ref() != Some(&value);
        let on_sample = node.field("on_sample").is_some_and(|plug| !plug.is_null());
        if !changed && !on_sample {
            return Ok(false);
        }
        let target = match script_expr::lvalue(field(&node.fields, "output")) {
            Ok(Some(target)) => target,
            Ok(None) => {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("observe output"),
                );
                return Ok(false);
            }
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(false);
            }
        };
        state.watch_seen = Some(value.clone());
        let output = source_key(id, target);
        self.write(output, value)?;
        if changed
            && configured(node, "on_change")
            && self.is_current_observation(key, activation, frame)
        {
            self.run_observation_callback(
                ObservedSample {
                    source: key,
                    activation,
                    frame,
                    input: Some((output, self.machine.value(output, None))),
                },
                node,
                "on_change",
            )?;
        }
        if !self
            .state(key)
            .is_some_and(|state| state.active && !state.ending && state.activation == activation)
        {
            return Ok(false);
        }
        if on_sample {
            self.post(
                id,
                Event::ObserveSample {
                    raw: key.raw,
                    activation,
                    frame,
                    available: true,
                },
            )?;
            return Ok(true);
        }
        self.try_complete(key, node, 0)?;
        Ok(false)
    }

    pub(in crate::script_init) fn deliver_observed_sample(
        &mut self,
        key: StateKey,
        activation: u16,
        frame: u32,
        available: bool,
    ) -> Result<(), InitFault> {
        if !self.is_current_observation(key, activation, frame) {
            return Ok(());
        }
        let activation_is_live = |vm: &Self| {
            vm.state(key).is_some_and(|state| {
                state.active && !state.ending && state.activation == activation
            })
        };
        let Some(node) = self.graph(key.instance).state(usize::from(key.raw)) else {
            return Ok(());
        };
        if available {
            let Some(sample) = self.state(key).and_then(|state| state.watch_seen.clone()) else {
                return Ok(());
            };
            let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "output")) else {
                return Ok(());
            };
            let output = source_key(key.instance, output);
            // Other ticks and earlier plugs may have reused the output before
            // this callback reached the queue. Publish the sample through the
            // ordinary write path so existing bindings keep their priority.
            let mut visible = same(&self.machine.value(output, None), &sample);
            if !visible && !same(&stored(&self.machine.snapshot, output), &sample) {
                self.write(output, sample.clone())?;
                if !activation_is_live(self) {
                    return Ok(());
                }
                visible = same(&self.machine.value(output, None), &sample);
            }
            if visible {
                self.run_observation_callback(
                    ObservedSample {
                        source: key,
                        activation,
                        frame,
                        input: Some((output, sample)),
                    },
                    node,
                    "on_sample",
                )?;
            } else {
                self.report(self.site(key.instance, node), Issue::ObservationUnavailable);
            }
        } else {
            self.run_observation_callback(
                ObservedSample {
                    source: key,
                    activation,
                    frame,
                    input: None,
                },
                node,
                "on_unavailable",
            )?;
        }
        if activation_is_live(self) {
            self.try_complete(key, node, 0)?;
        }
        Ok(())
    }

    fn is_current_observation(&self, source: StateKey, activation: u16, frame: u32) -> bool {
        frame == self.machine.frame
            && self.state(source).is_some_and(|state| {
                state.active
                    && !state.ending
                    && state.activation == activation
                    && state.kind
                        == StateKind::Observe {
                            sampled_frame: frame,
                        }
            })
    }

    fn run_observation_callback(
        &mut self,
        sample: ObservedSample,
        node: &Node,
        path: &str,
    ) -> Result<(), InitFault> {
        let source = sample.source;
        self.with_observation_input(
            Some(ObservationInput {
                sample: Arc::new(sample),
                on_unavailable: UnavailableSample::CancelStart,
            }),
            |vm| {
                vm.follow(source.instance, node, path, Some(source), 0)
                    .map(|_| ())
            },
        )
    }

    fn with_observation_input(
        &mut self,
        input: Option<ObservationInput>,
        run: impl FnOnce(&mut Self) -> Result<(), InitFault>,
    ) -> Result<(), InitFault> {
        let previous = std::mem::replace(&mut self.observed_input, input);
        let result = run(self);
        self.observed_input = previous;
        result
    }

    pub(super) fn resume_observed(
        &mut self,
        input: ObservationInput,
        run: impl FnOnce(&mut Self) -> Result<(), InitFault>,
    ) -> Result<(), InitFault> {
        if self.observation_input_available(&input.sample) {
            self.with_observation_input(Some(input), run)
        } else if input.on_unavailable == UnavailableSample::ReadCurrent {
            self.with_observation_input(None, run)
        } else {
            Ok(())
        }
    }

    fn observation_input_available(&mut self, sample: &ObservedSample) -> bool {
        if !self.is_current_observation(sample.source, sample.activation, sample.frame) {
            return false;
        }
        if sample.input.as_ref().is_some_and(|(output, value)| {
            self.machine
                .links
                .get(output)
                .is_some_and(|entries| !entries.is_empty())
                && !same(&self.machine.value(*output, None), value)
        }) {
            let source = sample.source;
            if let Some(node) = self.graph(source.instance).state(usize::from(source.raw)) {
                self.report(
                    self.site(source.instance, node),
                    Issue::ObservationUnavailable,
                );
            }
            return false;
        }
        true
    }

    pub(super) fn observation_value(&mut self, site: Site, value: &Json) -> Option<Value> {
        match self.observation_result(site, value) {
            ObservationResult::Value(value) => Some(value),
            ObservationResult::Unavailable | ObservationResult::Invalid => None,
        }
    }

    fn observation_result(&mut self, site: Site, value: &Json) -> ObservationResult {
        match self.evaluate_with_context(site, value, None, Value::Null, false) {
            Ok(value) => ObservationResult::Value(value),
            Err(
                script_expr::EvalError::UnavailableEntityPosition(_)
                | script_expr::EvalError::UnavailableEntityDirection(_)
                | script_expr::EvalError::UnavailableBodyEyePosition(_)
                | script_expr::EvalError::UnavailableBodyBone { .. }
                | script_expr::EvalError::UnavailableEntityVariable { .. }
                | script_expr::EvalError::UnavailableSpatialCandidates
                | script_expr::EvalError::UnavailableSpatialVisibility
                | script_expr::EvalError::UnavailableSpatialRayCast
                | script_expr::EvalError::UnavailableRoomEntityFacts,
            ) => {
                // A later sample can succeed as the scene updates.
                self.report(site, Issue::ObservationUnavailable);
                ObservationResult::Unavailable
            }
            Err(error) => {
                self.report(site, Issue::Expression(error));
                ObservationResult::Invalid
            }
        }
    }

    pub(in crate::script_init) fn check_watch(&mut self, key: StateKey) -> Result<(), InitFault> {
        let Some(seen) = self
            .state(key)
            .filter(|state| state.active)
            .and_then(|state| state.watch_seen.clone())
        else {
            return Ok(());
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("Watch state");
        if self
            .evaluate(id, node, field(&node.fields, "m_B7A3CF78"), Some(key))
            .is_some_and(|value| !equal(&value, &seen))
        {
            self.post(id, Event::WatchChanged(key.raw))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn deliver_watch(&mut self, key: StateKey) -> Result<(), InitFault> {
        let Some(seen) = self
            .state(key)
            .filter(|state| state.active)
            .and_then(|state| state.watch_seen.clone())
        else {
            return Ok(());
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("Watch state");
        match script_expr::lvalue(field(&node.fields, "m_48A8CDEC")) {
            Ok(Some(target)) => self.write(source_key(id, target), seen)?,
            Ok(None) => (),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        }
        self.follow(id, node, "m_A70DB860", Some(key), 0)?;
        let seen = self.evaluate(id, node, field(&node.fields, "m_B7A3CF78"), Some(key));
        if let Some(state) = self.state_mut(key) {
            state.watch_seen = seen
        }
        self.try_complete(key, node, 0)
    }

    pub(in crate::script_init) fn reevaluate(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(state) = self.state_mut(key).filter(|state| state.active) else {
            return Ok(());
        };
        state.pending_timer = false;
        let kind = state.kind;
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("switch state");
        let evaluated = if matches!(kind, StateKind::DelayedSwitch { .. }) {
            self.recheck_delayed_switch(key, node, depth)?;
            true
        } else if matches!(kind, StateKind::AiBehavior { record: Some(_) }) {
            self.update_ai_behavior(key, node)?;
            true
        } else if let StateKind::Switch { case } = kind {
            let Some(next) = self.switch_case(key, node) else {
                return Ok(());
            };
            if next != case {
                self.exit_subgraph(key.instance, node, &case_path(case), depth + 1)?;
                self.state_mut(key).expect("active switch").kind = StateKind::Switch { case: next };
                self.follow(key.instance, node, &case_path(next), Some(key), depth)?;
            }
            true
        } else {
            self.check_switch(key, depth)?
        };
        if evaluated {
            self.try_complete(key, node, depth)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn check_switch(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<bool, InitFault> {
        let Some(StateKind::BooleanSwitch(current)) = self
            .state(key)
            .filter(|state| state.active)
            .map(|state| state.kind)
        else {
            return Ok(false);
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("active state node");
        if let Some(value) = self.evaluate(id, node, field(&node.fields, "m_condition"), Some(key))
        {
            let next = truthy(&value);
            if next != current {
                self.exit_subgraph(id, node, branch_path(current), depth + 1)?;
                self.state_mut(key).expect("active switch").kind = StateKind::BooleanSwitch(next);
                self.enter_branch(key, node, next, depth)?;
            }
            return Ok(true);
        }
        Ok(false)
    }

    pub(in crate::script_init) fn begin_delayed_switch(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(undecided) = self.evaluate(id, node, field(&node.fields, "m_308B8A80"), None)
        else {
            return Ok(());
        };
        let undecided = truthy(&undecided);
        let current = if undecided {
            false
        } else {
            let initial = field(&node.fields, "m_418D9D9D");
            let config = if initial.is_null() {
                field(&node.fields, "m_BD83C8E6")
            } else {
                initial
            };
            let Some(initial) = self.evaluate(id, node, config, Some(key)) else {
                return Ok(());
            };
            truthy(&initial)
        };
        self.state_mut(key).expect("active delayed switch").kind =
            StateKind::DelayedSwitch { current, undecided };
        self.register_tick(key, self.machine.now_ms);
        if !undecided {
            self.enter_branch(key, node, current, depth)?;
        }
        self.recheck_delayed_switch(key, node, depth)
    }

    pub(in crate::script_init) fn recheck_delayed_switch(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(StateKind::DelayedSwitch { current, undecided }) = self
            .state(key)
            .filter(|state| state.active)
            .map(|state| state.kind)
        else {
            return Ok(());
        };
        let id = key.instance;
        let condition = field(&node.fields, "m_BD83C8E6");
        let false_override = field(&node.fields, "m_E8FC060D");
        let requested = if current {
            let Some(holds_false) =
                self.delayed_false_condition(key, node, condition, false_override)
            else {
                return Ok(());
            };
            holds_false.then_some(false)
        } else {
            let Some(holds_true) = self.evaluate(id, node, condition, Some(key)) else {
                return Ok(());
            };
            if truthy(&holds_true) {
                Some(true)
            } else if undecided {
                let Some(holds_false) =
                    self.delayed_false_condition(key, node, condition, false_override)
                else {
                    return Ok(());
                };
                holds_false.then_some(false)
            } else {
                None
            }
        };
        let Some(branch) = requested else {
            if !undecided {
                self.cancel_delayed_timer(key, !current);
            }
            return Ok(());
        };
        let delay_field = if branch { "m_7130456B" } else { "m_ED60694A" };
        let Some(delay) = self.evaluate(id, node, field(&node.fields, delay_field), None) else {
            return Ok(());
        };
        let seconds = script_expr::to_float(&delay);
        let milliseconds = ((seconds as f32) * 1000.0).trunc();
        if !milliseconds.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("delayed switch timeout"),
            );
            return Ok(());
        }
        if seconds > 0.0 {
            self.arm_delayed_timer(key, branch, milliseconds as u64)?;
        }
        if undecided {
            self.cancel_delayed_timer(key, !branch);
        }
        if seconds <= 0.0 {
            self.set_delayed_branch(key, node, branch, depth)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn delayed_false_condition(
        &mut self,
        key: StateKey,
        node: &Node,
        condition: &Json,
        override_config: &Json,
    ) -> Option<bool> {
        let invert = override_config.is_null();
        let config = if invert { condition } else { override_config };
        self.evaluate(key.instance, node, config, Some(key))
            .map(|value| truthy(&value) != invert)
    }

    pub(in crate::script_init) fn arm_delayed_timer(
        &mut self,
        key: StateKey,
        branch: bool,
        delay_ms: u64,
    ) -> Result<(), InitFault> {
        let index = usize::from(branch);
        if !self
            .state(key)
            .expect("active delayed switch")
            .delayed_timers[index]
        {
            let parameter = if branch {
                SWITCH_TO_TRUE
            } else {
                SWITCH_TO_FALSE
            };
            self.post_after(key.instance, delay_ms, Event::Timer(key.raw, parameter))?;
            self.state_mut(key)
                .expect("active delayed switch")
                .delayed_timers[index] = true;
        }
        Ok(())
    }

    pub(in crate::script_init) fn cancel_delayed_timer(&mut self, key: StateKey, branch: bool) {
        let pending = &mut self
            .state_mut(key)
            .expect("active delayed switch")
            .delayed_timers[usize::from(branch)];
        if std::mem::take(pending) {
            let parameter = if branch {
                SWITCH_TO_TRUE
            } else {
                SWITCH_TO_FALSE
            };
            self.machine.queues.retain(key.instance, |_, event| {
                !matches!(event, Event::Timer(raw, param) if *raw == key.raw && *param == parameter)
            });
        }
    }

    pub(in crate::script_init) fn set_delayed_branch(
        &mut self,
        key: StateKey,
        node: &Node,
        next: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        if let StateKind::DelayedSwitch {
            current,
            undecided: false,
        } = self.state(key).expect("active delayed switch").kind
        {
            self.exit_subgraph(key.instance, node, branch_path(current), depth + 1)?;
        }
        self.state_mut(key).expect("active delayed switch").kind = StateKind::DelayedSwitch {
            current: next,
            undecided: false,
        };
        self.enter_branch(key, node, next, depth)
    }

    pub(in crate::script_init) fn switch_case(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Option<Option<usize>> {
        let value = self.evaluate(
            key.instance,
            node,
            field(&node.fields, "m_262AE982"),
            Some(key),
        )?;
        let cases = array_field(&node.fields, "m_05706D1C");
        // Retail `m_F0F0720B == 1` marks a 0-based indexed switch (e.g. Brigitte
        // LMB alternating swings `1248:81`, Sigma LMB `187F:62`) where every case
        // row has `m_value: null` and the integer value selects `cases[index]`.
        if field(&node.fields, "m_F0F0720B").as_u64() == Some(1) {
            let idx = script_expr::to_int(&value, -1);
            if idx >= 0 {
                let idx = idx as usize;
                if cases.get(idx).is_some_and(Json::is_object) {
                    return Some(Some(idx));
                }
            }
            return Some(None);
        }
        for (index, case) in cases.iter().enumerate() {
            if case.is_object() {
                let candidate = self.evaluate(key.instance, node, field(case, "m_value"), None)?;
                if equal(&value, &candidate) {
                    return Some(Some(index));
                }
            }
        }
        Some(None)
    }

    pub(in crate::script_init) fn enter_branch(
        &mut self,
        key: StateKey,
        node: &Node,
        branch: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        let output = if branch {
            "m_onTruePlug"
        } else {
            "m_onFalsePlug"
        };
        self.follow(key.instance, node, output, Some(key), depth)?;
        if self.state(key).is_some_and(|state| state.active) {
            self.follow(key.instance, node, branch_path(branch), Some(key), depth)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn register_state(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let entry = registered_state(self.graph(key.instance), node).unwrap_or(
            super::super::machine::RegisteredState {
                class: if node.class == "STU_9935FBC8" {
                    RegisteredClass::CinematicAim
                } else {
                    RegisteredClass::EntityControl
                },
                server_only: node.server_only,
                suppresses_model: false,
            },
        );
        if entry.suppresses_model && !self.world.set_encounter_model_enabled(site, false) {
            self.report(site, Issue::UnsupportedField("encounter model control"));
            return Ok(());
        }
        if entry.class == RegisteredClass::CinematicAim {
            let is_cone_clamp = ["m_C33684DE", "m_8FA7FF85", "m_D6119DFB", "m_83221709"]
                .iter()
                .any(|name| {
                    let cfg = field(&node.fields, name);
                    !cfg.is_null() && field(cfg, "m_value").as_i64() != Some(0)
                });
            let accepted = if captured_aim_lock_shape(node) {
                let mut degrees = [0.0; 2];
                for (out, name) in degrees.iter_mut().zip(["m_A53161C8", "m_AB37A892"]) {
                    let Some(value) = self.evaluate_at_with_initial(
                        site,
                        field(&node.fields, name),
                        None,
                        Value::Float(0.0),
                    ) else {
                        return Ok(());
                    };
                    *out = match value {
                        Value::Integer(value) => value as f64,
                        Value::Float(value) if value.is_finite() => value,
                        _ => {
                            self.report(site, Issue::UnsupportedField("captured aim lock pose"));
                            return Ok(());
                        }
                    };
                }
                self.world.set_captured_aim_lock(site, Some(degrees))
            } else if is_cone_clamp && self.world.admits_generic_player_runtime(site) {
                // Cone-limited aim (`0x0D06:81` Rocket Punch ±60° yaw / ±30° pitch) must
                // link in `CinematicAim` without hard-locking server `mover.cinematic_aim`.
                true
            } else {
                self.world.set_cinematic_aim_lock(site, true)
            };
            if !accepted {
                self.report(site, Issue::UnsupportedField("cinematic aim lock pose"));
                return Ok(());
            }
        }
        self.machine.registered.insert(key, entry);
        self.update_state_links(entry.class)
    }

    pub(in crate::script_init) fn unregister_state(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let Some(entry) = self.machine.registered.shift_remove(&key) else {
            return Ok(());
        };
        if entry.class == RegisteredClass::CinematicAim {
            let site = self.site(key.instance, node);
            let is_cone_clamp = ["m_C33684DE", "m_8FA7FF85", "m_D6119DFB", "m_83221709"]
                .iter()
                .any(|name| {
                    let cfg = field(&node.fields, name);
                    !cfg.is_null() && field(cfg, "m_value").as_i64() != Some(0)
                });
            if captured_aim_lock_shape(node) {
                self.world.set_captured_aim_lock(site, None);
            } else if !(is_cone_clamp && self.world.admits_generic_player_runtime(site)) {
                self.world.set_cinematic_aim_lock(site, false);
            }
        }
        if entry.suppresses_model
            && !self
                .machine
                .registered
                .values()
                .any(|state| state.suppresses_model)
        {
            let site = self.site(key.instance, node);
            if !self.world.set_encounter_model_enabled(site, true) {
                self.report(site, Issue::UnsupportedField("encounter model control"));
            }
        }
        self.state_mut(key).expect("registered state").kind = StateKind::Registered { next: None };
        self.update_state_links(entry.class)
    }

    pub(in crate::script_init) fn update_state_links(
        &mut self,
        class: RegisteredClass,
    ) -> Result<(), InitFault> {
        let mut next = None;
        for (&key, entry) in self.machine.registered.iter().rev() {
            if entry.class != class {
                continue;
            }
            self.machine.work_left =
                self.machine
                    .work_left
                    .checked_sub(1)
                    .ok_or(InitFault::WorkBudget {
                        limit: self.machine.max_work,
                        operation: "linked state registry",
                    })?;
            self.machine.snapshot_dirty = true;
            self.machine.snapshot.instances[&key.instance].states[&key.raw].kind =
                StateKind::Registered { next };
            if !entry.server_only {
                next = Some((key.instance, key.raw));
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn end(
        &mut self,
        key: StateKey,
        finished: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        let Some(state) = self
            .state_mut(key)
            .filter(|state| state.active && !state.ending)
        else {
            return Ok(());
        };
        state.ending = true;
        let kind = state.kind;
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("active state node");
        self.machine
            .queues
            .retain(id, |_, event| event.state() != Some(key.raw));
        self.clear_tick(key);
        self.machine
            .voice_poll
            .retain(|registered| *registered != key);
        // A final shot can be due in the same tick that its authored
        // subgraph (including the client presentation inputs) is torn down.
        if matches!(kind, StateKind::WeaponVolley { .. }) {
            self.report_volley(key)?;
        }
        self.exit_subgraph(id, node, "m_subgraphPlug", depth + 1)?;
        match kind {
            StateKind::VoiceStimulus => {
                self.world.voice_context(self.site(id, node));
            }
            StateKind::ForCode => {
                self.unregister_for_code(key);
                self.publish_for_code(key, node)?;
            }
            StateKind::Registered { .. } => self.unregister_state(key, node)?,
            StateKind::CinematicTimeScale { .. } => self.stamp_cinematic_time_scale(key, node),
            StateKind::SameTeamContact { registered: true } => {
                self.world
                    .set_same_team_contact_suppressed(self.site(id, node), false);
            }
            StateKind::OutgoingDamageFilter { registered: true } => {
                self.world
                    .set_outgoing_damage_filter(self.site(id, node), None);
            }
            StateKind::AiBehavior {
                record: Some(record),
            } => {
                self.world
                    .ai_behavior_request(self.site(id, node), record, false);
            }
            StateKind::AiControlSetting => {
                self.world
                    .set_ai_control_setting(self.site(id, node), 5.0, false);
            }
            StateKind::AiControlOverride {
                record: Some(record),
            } => {
                self.world
                    .ai_control_override(self.site(id, node), record, false);
            }
            StateKind::AiFacing(Some(record)) => {
                self.publish_facing_record(node, self.site(id, node), record, false);
            }
            StateKind::AiWeaponOverride {
                record: Some(record),
            } => {
                self.world
                    .ai_weapon_override(self.site(id, node), record, false);
            }
            StateKind::DisplayName {
                binding: Some((entity, name)),
            } => {
                self.world
                    .set_display_name(self.site(id, node), entity, name, false);
            }
            StateKind::Defer => {
                if let Some(pending) = self.machine.deferred.get_mut(&id) {
                    pending.retain(|(_, raw)| *raw != key.raw);
                }
                self.exit_subgraph(id, node, "m_8E484B2E", depth + 1)?;
            }
            StateKind::MovementMod => {
                self.machine
                    .movement_modifiers
                    .retain(|entry, _| entry.instance != key.instance || entry.state != key.raw);
                self.machine.mover_overrides.remove(&key);
            }
            StateKind::MovementSample { .. } => {
                self.update_movement_sample(key, node, depth)?;
            }
            StateKind::Shockwave { .. } => {
                if let Some(state) = self.state_mut(key) {
                    state.ground_wave = None;
                    state.kind = StateKind::Shockwave { managed: false };
                }
            }
            StateKind::PlacementSelector { .. } => {
                if let Some(state) = self.state_mut(key) {
                    state.kind = StateKind::PlacementSelector {
                        confirmed: false,
                        sampled: false,
                    };
                }
            }
            StateKind::MoveTo { .. } => {
                if let Some(state) = self.state_mut(key) {
                    state.kind = StateKind::MoveTo { movement: None };
                }
            }
            StateKind::TargetTag {
                effect: Some(effect),
            } => {
                self.world
                    .set_target_tag_effect(self.site(id, node), effect, false);
            }
            StateKind::HealthPool { pool: Some(pool) } => {
                if let Some(current) = self.world.remove_health_pool(self.site(id, node), pool) {
                    self.publish_health_pool(key, node, current)?;
                }
            }
            StateKind::Pet {
                entity: Some(pet_id),
            } => {
                self.end_pet(key, node, pet_id)?;
            }
            StateKind::CombatModFilter { registered: true } => {
                self.world.set_combat_mod(self.site(id, node), None);
            }
            StateKind::DeflectProjectiles { .. } => {
                self.world.set_deflect_state(self.site(id, node), None);
            }
            StateKind::TargetAura { effect } => {
                self.world
                    .set_target_aura(self.site(id, node), &[], effect, None, 0);
            }
            StateKind::Grapple => {
                if let Some(state) = self.state_mut(key) {
                    state.grapple = None;
                }
            }
            StateKind::TargetSubScript { child: Some(child) } => {
                self.destroy(child)?;
            }
            StateKind::BallRoll { .. } => {
                self.world
                    .set_ball_roll_state(self.site(id, node), false, 0.0);
                self.machine
                    .movement_modifiers
                    .retain(|entry, _| entry.instance != key.instance || entry.state != key.raw);
            }
            StateKind::KinematicsControl { .. } => {
                self.machine
                    .movement_modifiers
                    .retain(|entry, _| entry.instance != key.instance || entry.state != key.raw);
            }
            StateKind::ModifierControl { blocks_input: true } => {
                self.machine.control_blocked = false;
            }
            StateKind::EntityStatus { mode: 1 } => {
                self.world.set_combat_mod(self.site(id, node), None);
            }
            StateKind::BooleanSwitch(branch) => {
                self.exit_subgraph(id, node, branch_path(branch), depth + 1)?
            }
            StateKind::DelayedSwitch {
                current,
                undecided: false,
            } => self.exit_subgraph(id, node, branch_path(current), depth + 1)?,
            StateKind::Switch { case } => {
                self.exit_subgraph(id, node, &case_path(case), depth + 1)?
            }
            StateKind::PhaseScriptList | StateKind::PhaseScript => {
                for child in self.scoped_children(key) {
                    self.destroy(child)?;
                }
            }
            StateKind::WitchStaffEffect => {
                self.world.set_witch_staff_effect(self.site(id, node), None);
            }
            StateKind::DeadeyeEffects | StateKind::TrackEntities { .. } => {
                self.retain_target_children(key, &[])?;
            }
            StateKind::SubScript { child: Some(child) } => self.destroy(child)?,
            StateKind::MessageIdle | StateKind::BossPauseReceiver { .. } => {
                self.exit_subgraph(id, node, "m_33DA5163", depth + 1)?;
                self.exit_subgraph(id, node, "m_452862E6", depth + 1)?;
            }
            StateKind::AiCandidate { selected: true } => {
                self.exit_subgraph(id, node, "m_6F92F4BD", depth + 1)?;
            }
            StateKind::ButtonBlock(Some(_)) => self.release_button_block(key, depth + 1)?,
            StateKind::LogicalButton { .. } => {
                self.exit_subgraph(id, node, "m_80AF45FB", depth + 1)?;
                self.exit_subgraph(id, node, "m_1C9EF058", depth + 1)?;
            }
            StateKind::Stack { .. } => {
                self.exit_subgraph(id, node, "m_topSubgraphPlug", depth + 1)?;
                self.exit_subgraph(id, node, "m_underSubgraphPlug", depth + 1)?;
                self.unlink_single_output(key, depth + 1)?;
            }
            StateKind::CosmeticEntity => self.unlink_single_output(key, depth + 1)?,
            StateKind::ChaseVar => self.finish_chase(key, depth + 1)?,
            StateKind::WeaponVolley { .. } => self.finish_volley(key, depth + 1)?,
            StateKind::Ability { flags } => {
                self.set_ability_flags(key, flags & !ABILITY_HELD);
                self.stop_ability(key, node, depth + 1)?;
                self.finish_cooldown(key)?;
            }
            _ => (),
        }
        let state = self.state_mut(key).expect("ending state");
        if matches!(
            kind,
            StateKind::TrackedTargets
                | StateKind::TrackEntities { .. }
                | StateKind::ViewTargets { .. }
        ) {
            state.tracked_targets.clear();
        }
        state.active = false;
        state.ending = false;
        state.pending_change = false;
        state.pending_timer = false;
        state.delayed_timers = [false; 2];
        if matches!(kind, StateKind::SubScript { .. }) {
            state.kind = StateKind::SubScript { child: None };
        }
        if matches!(kind, StateKind::HealthPool { .. }) {
            state.kind = StateKind::HealthPool { pool: None };
        }
        if matches!(kind, StateKind::Pet { .. }) {
            state.kind = StateKind::Pet { entity: None };
        }
        if matches!(kind, StateKind::CombatModFilter { .. }) {
            state.kind = StateKind::CombatModFilter { registered: false };
        }
        if matches!(kind, StateKind::TargetSubScript { .. }) {
            state.kind = StateKind::TargetSubScript { child: None };
        }
        if kind == StateKind::Grapple {
            state.grapple = None;
        }
        if matches!(kind, StateKind::BallRoll { .. }) {
            state.kind = StateKind::BallRoll { fireball: false };
        }
        if matches!(kind, StateKind::DisplayName { .. }) {
            state.kind = StateKind::DisplayName { binding: None };
        }
        if matches!(kind, StateKind::TargetTag { .. }) {
            state.kind = StateKind::TargetTag { effect: None };
        }
        if matches!(kind, StateKind::AiWeaponOverride { .. }) {
            state.kind = StateKind::AiWeaponOverride { record: None };
        }
        if matches!(kind, StateKind::AiControlOverride { .. }) {
            state.kind = StateKind::AiControlOverride { record: None };
        }
        if matches!(kind, StateKind::AiFacing(_)) {
            state.kind = StateKind::AiFacing(None);
        }
        if matches!(kind, StateKind::SameTeamContact { .. }) {
            state.kind = StateKind::SameTeamContact { registered: false };
        }
        if matches!(kind, StateKind::OutgoingDamageFilter { .. }) {
            state.kind = StateKind::OutgoingDamageFilter { registered: false };
        }
        if matches!(kind, StateKind::AiBehavior { .. }) {
            state.kind = StateKind::AiBehavior { record: None };
        }
        if matches!(kind, StateKind::LogicalButton { .. }) {
            state.kind = StateKind::LogicalButton { down: false };
        }
        if matches!(kind, StateKind::Stack { .. }) {
            state.kind = StateKind::Stack {
                top: false,
                under: false,
            };
        }
        if let Some(watched_vars) = self.machine.state_watches.remove(&key) {
            for var_key in watched_vars {
                if let Some(watchers) = self.machine.watchers.get_mut(&var_key) {
                    watchers.retain(|watcher| *watcher != key);
                    if watchers.is_empty() {
                        self.machine.watchers.remove(&var_key);
                    }
                }
            }
        }
        if let Some(foreign_keys) = self.machine.state_foreign_watches.remove(&key) {
            for fk in foreign_keys {
                if let Some(watchers) = self.machine.foreign_watchers.get_mut(&fk) {
                    watchers.retain(|watcher| *watcher != key);
                    if watchers.is_empty() {
                        self.machine.foreign_watchers.remove(&fk);
                    }
                }
            }
        }
        let ending = if finished {
            "m_onFinishedPlug"
        } else {
            "m_onAbortPlug"
        };
        if kind != StateKind::VoiceStimulus {
            self.follow(id, node, ending, Some(key), depth)?;
        }
        self.follow(id, node, "m_onEndPlug", Some(key), depth)?;
        Ok(())
    }

    pub(in crate::script_init) fn exit_subgraph(
        &mut self,
        id: u16,
        node: &Node,
        path: &str,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(plug) = node.field(path) else {
            return Ok(());
        };
        let slots = array_field(plug, SUBGRAPH_STATES);
        if slots.is_empty() {
            return Ok(());
        }
        for raw in raw_indices(slots) {
            self.end(StateKey { instance: id, raw }, false, depth)?;
        }
        // A later child's end callback can queue a start for an earlier child.
        let now = self.machine.now_ms;
        self.machine.queues.retain(id, |due, event| {
            due != now
                || event
                    .begin_state()
                    .is_none_or(|state| raw_indices(slots).all(|raw| raw != state))
        });
        Ok(())
    }

    pub(in crate::script_init) fn drop_begin(&mut self, id: u16, raw: u16) {
        let now = self.machine.now_ms;
        self.machine.queues.retain(id, |due, event| {
            due != now || event.begin_state() != Some(raw)
        });
    }

    pub(in crate::script_init) fn exit_instance(&mut self, id: u16) -> Result<(), InitFault> {
        let parent = self.machine.snapshot.instances[&id].parent;
        self.destroy(id)?;
        let Some((instance, raw)) = parent else {
            return Ok(());
        };
        let key = StateKey { instance, raw };
        let Some(state) = self.state_mut(key).filter(|state| {
            state.active && !state.ending && state.kind == StateKind::SubScript { child: Some(id) }
        }) else {
            return Ok(());
        };
        state.kind = StateKind::SubScript { child: None };
        let node = self
            .graph(instance)
            .state(usize::from(raw))
            .expect("active subscript");
        if !configured(node, "m_24AB04BB") {
            self.post(instance, Event::Finish(raw))?;
        }
        Ok(())
    }

    fn owned_children(&self, id: u16) -> Vec<u16> {
        let instances = &self.machine.snapshot.instances;
        instances[&id]
            .states
            .values()
            .flat_map(|state| state.target_children.values().copied())
            .filter(|child| instances.contains_key(child))
            .chain(instances.iter().rev().filter_map(|(&child, instance)| {
                instance
                    .parent
                    .is_some_and(|(parent, _)| parent == id)
                    .then_some(child)
            }))
            .collect()
    }

    pub(super) fn quiesce_actor_scopes(&mut self, roots: &[u16]) -> Result<(), InitFault> {
        let mut pending = roots.to_vec();
        let mut visited = std::collections::BTreeSet::new();
        while let Some(id) = pending.pop() {
            if !self.machine.snapshot.instances.contains_key(&id) || !visited.insert(id) {
                continue;
            }
            self.spend("retiring actor scope")?;
            pending.extend(self.owned_children(id));
            for state in self.machine.snapshot.instances[&id].states.values_mut() {
                state.ending = true;
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn destroy(&mut self, id: u16) -> Result<(), InitFault> {
        let mut pending = vec![(id, false)];
        while let Some((id, children_done)) = pending.pop() {
            if !self.machine.snapshot.instances.contains_key(&id) {
                continue;
            }
            if !children_done {
                self.spend("instance teardown")?;
                let children = self.owned_children(id);
                for state in self.machine.snapshot.instances[&id].states.values_mut() {
                    state.ending = true;
                    state.target_children.clear();
                }
                pending.push((id, true));
                pending.extend(children.into_iter().map(|child| (child, false)));
                continue;
            }
            let outputs: Vec<_> = self.machine.snapshot.instances[&id]
                .states
                .iter()
                .filter_map(|(&raw, state)| {
                    (state.linked_variable.is_some()
                        || state.ability_stack.is_some()
                        || (state.active
                            && matches!(
                                state.kind,
                                StateKind::ForCode | StateKind::AiControlSetting
                            ))
                        || matches!(
                            state.kind,
                            StateKind::Registered { .. }
                                | StateKind::SameTeamContact { registered: true }
                                | StateKind::OutgoingDamageFilter { registered: true }
                                | StateKind::WeaponVolley { .. }
                                | StateKind::ButtonBlock(Some(_))
                                | StateKind::HealthPool { pool: Some(_) }
                                | StateKind::Pet { entity: Some(_) }
                                | StateKind::CombatModFilter { registered: true }
                                | StateKind::DeflectProjectiles { .. }
                                | StateKind::TargetAura { .. }
                                | StateKind::BallRoll { .. }
                                | StateKind::ModifierControl { blocks_input: true }
                                | StateKind::EntityStatus { mode: 1 }
                                | StateKind::DisplayName { binding: Some(_) }
                                | StateKind::TargetTag { effect: Some(_) }
                                | StateKind::AiWeaponOverride { record: Some(_) }
                                | StateKind::AiControlOverride { record: Some(_) }
                                | StateKind::AiFacing(Some(_))
                                | StateKind::AiBehavior { record: Some(_) }
                                | StateKind::VoiceStimulus
                        ))
                    .then_some((raw, state.kind))
                })
                .collect();
            for (raw, kind) in outputs {
                let key = StateKey { instance: id, raw };
                if kind == StateKind::ForCode {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("for-code state");
                    self.unregister_for_code(key);
                    self.publish_for_code(key, node)?;
                } else if matches!(kind, StateKind::Registered { .. }) {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("linked state");
                    self.unregister_state(key, node)?;
                } else if kind == StateKind::VoiceStimulus {
                    let node = self.graph(id).state(usize::from(raw)).expect("voice state");
                    if self.state(key).is_some_and(|state| state.active) {
                        self.world.voice_context(self.site(id, node));
                    }
                    self.world.voice_context(self.site(id, node));
                } else if let StateKind::SameTeamContact { registered: true } = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("team contact state");
                    self.world
                        .set_same_team_contact_suppressed(self.site(id, node), false);
                } else if let StateKind::OutgoingDamageFilter { registered: true } = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("outgoing damage filter state");
                    self.world
                        .set_outgoing_damage_filter(self.site(id, node), None);
                } else if let StateKind::AiBehavior {
                    record: Some(record),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI behavior state");
                    self.world
                        .ai_behavior_request(self.site(id, node), record, false);
                } else if kind == StateKind::AiControlSetting {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI control setting state");
                    self.world
                        .set_ai_control_setting(self.site(id, node), 5.0, false);
                } else if let StateKind::AiControlOverride {
                    record: Some(record),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI control state");
                    self.world
                        .ai_control_override(self.site(id, node), record, false);
                } else if let StateKind::AiFacing(Some(record)) = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI facing state");
                    self.publish_facing_record(node, self.site(id, node), record, false);
                } else if let StateKind::AiWeaponOverride {
                    record: Some(record),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI weapon state");
                    self.world
                        .ai_weapon_override(self.site(id, node), record, false);
                } else if let StateKind::DisplayName {
                    binding: Some((entity, name)),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("display name");
                    self.world
                        .set_display_name(self.site(id, node), entity, name, false);
                } else if let StateKind::TargetTag {
                    effect: Some(effect),
                } = kind
                {
                    let node = self.graph(id).state(usize::from(raw)).expect("target tag");
                    self.world
                        .set_target_tag_effect(self.site(id, node), effect, false);
                } else if matches!(kind, StateKind::ButtonBlock(Some(_))) {
                    self.release_button_block(key, 0)?;
                } else if let StateKind::HealthPool { pool: Some(pool) } = kind {
                    let node = self.graph(id).state(usize::from(raw)).expect("health pool");
                    let site = self.site(id, node);
                    self.world.remove_health_pool(site, pool);
                    self.publish_health_totals(site)?;
                } else if let StateKind::Pet {
                    entity: Some(pet_id),
                } = kind
                {
                    let node = self.graph(id).state(usize::from(raw)).expect("pet state");
                    self.world.despawn_pet(self.site(id, node), pet_id);
                } else if let StateKind::CombatModFilter { registered: true } = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("combat mod state");
                    self.world.set_combat_mod(self.site(id, node), None);
                } else if matches!(kind, StateKind::DeflectProjectiles { .. }) {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("deflect state");
                    self.world.set_deflect_state(self.site(id, node), None);
                } else if let StateKind::TargetAura { effect } = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("target aura state");
                    self.world
                        .set_target_aura(self.site(id, node), &[], effect, None, 0);
                } else if matches!(kind, StateKind::BallRoll { .. }) {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("ball roll state");
                    self.world
                        .set_ball_roll_state(self.site(id, node), false, 0.0);
                } else if matches!(kind, StateKind::ModifierControl { blocks_input: true }) {
                    self.machine.control_blocked = false;
                } else if matches!(kind, StateKind::EntityStatus { mode: 1 }) {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("entity status state");
                    self.world.set_combat_mod(self.site(id, node), None);
                } else if matches!(kind, StateKind::WeaponVolley { .. }) {
                    self.finish_volley(key, 0)?;
                } else if kind == StateKind::ChaseVar {
                    self.finish_chase(key, 0)?;
                } else if matches!(kind, StateKind::Ability { .. }) {
                    self.unlink_ability_stack(key, 0)?;
                    self.finish_cooldown(key)?;
                } else {
                    self.unlink_single_output(key, 0)?;
                }
            }
            self.detach_actor_target_guard(id);
            self.machine.snapshot_dirty = true;
            self.machine.snapshot.instances.shift_remove(&id);
            self.machine
                .movement_modifiers
                .retain(|key, _| key.instance != id);
            self.machine
                .mover_overrides
                .retain(|key, _| key.instance != id);
            self.machine
                .disabled_start_entries
                .retain(|(instance, _)| *instance != id);
            self.machine.queues.remove(id);
            self.machine.ticking.remove(&id);
            self.machine.deferred.remove(&id);
            self.machine.voice_poll.retain(|key| key.instance != id);
            self.machine
                .ability_presses
                .retain(|key, _| key.instance != id);
            self.machine
                .state_watches
                .retain(|watcher, _| watcher.instance != id);
            self.machine
                .state_foreign_watches
                .retain(|watcher, _| watcher.instance != id);
            self.machine.watchers.retain(|key, watchers| {
                if matches!(key, VarKey::Instance(instance, _) if *instance == id) {
                    return false;
                }
                watchers.retain(|watcher| watcher.instance != id);
                !watchers.is_empty()
            });
            self.machine.foreign_watchers.retain(|_, watchers| {
                watchers.retain(|watcher| watcher.instance != id);
                !watchers.is_empty()
            });
        }
        Ok(())
    }
}
