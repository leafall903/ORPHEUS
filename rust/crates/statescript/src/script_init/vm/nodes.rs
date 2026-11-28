//! StateScript node dispatch, variable writes, target collection, and entity creation.

use serde_json::Value as Json;

use crate::{
    script_expr::{self, EntityRelation, EvalError, equal, truthy},
    script_graph::{Node, asset_guid},
    script_runtime::{FRAME_MS, POST_MATCH_COMPLETE},
    script_wire::Value,
};

use super::super::{
    MAX_PLUG_DEPTH, MOVEMENT_BOOLEAN_OUTPUTS,
    machine::{ArrayInsertion, Event, StateKey},
    shapes::{
        array_field, array_insertion_index, case_path, configured, encounter_base_state_shape,
        equipped_voice_pulse_slot, excluded_target_tags, field, immediate_self_damage,
        is_bare_header_action, is_base_health_pool, is_cinematic_time_scale_envelope,
        is_client_subgraph_controller, is_elapsed_wait, is_empty_optional_plug, is_entry,
        is_horizontal_velocity_clamp, is_literal_frame_wait, is_message_recipient_set,
        is_named_shield_decay, is_networked_for_code_synced, is_owner_movement_sampler,
        is_parent_owned_timed_graph, is_projected_self_message, is_self_shield_recharge,
        is_self_stat_timer, is_self_target, is_stat_action, is_static_map_explosion,
        is_unlinked_plug, is_vertical_velocity_impulse, is_wave_health_pool,
        living_body_array_shape, named_temporary_shield, parent_owned_graph_duration,
        participant_filter, persistent_message, placement_collection_tag, pool_number, raw_indices,
        registered_state, source_key, static_target_operand, stored,
    },
    types::{
        InitFault, Issue, MoveOutcome, MoveToKey, SpawnSearch, SpawnSearchKey, State, StateKind,
    },
    world::{
        CreationKind, EntityCreation, Relocation, RoomMessage, ScriptedMotion, Site,
        SweptDisplacement, VelocityAxis, VelocityOp, World,
    },
};
use super::Vm;

#[derive(Default)]
struct RegistrationChecks {
    admitted: Option<bool>,
    shape_considered: bool,
}

impl RegistrationChecks {
    fn permits(&mut self, allowed: bool) -> bool {
        self.admitted = Some(self.admitted.unwrap_or(false) || allowed);
        allowed
    }

    fn shape(&mut self, compatible: bool) -> bool {
        self.shape_considered = true;
        compatible
    }

    fn allows_runtime_fallback(&self) -> bool {
        self.admitted.is_none() && !self.shape_considered
    }

    fn rejected(&self) -> bool {
        // A compatible placement can still fail independent field checks.
        self.admitted == Some(false)
    }
}

impl<'graph, 'world, 'machine, W: World> Vm<'graph, 'world, 'machine, W> {
    pub(in crate::script_init) fn run_node(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        if node.host_event_kind().is_some() || !self.observed_server_path(id, node) {
            return Ok(false);
        }
        self.spend("node execution")?;
        if is_entry(node) || node.declares_forward_action() {
            return self.follow(id, node, "m_outPlug", None, depth);
        }
        if node.class == "STUStatescriptActionSetVar" {
            if !self.store(id, node, &node.fields, true)? {
                return Ok(false);
            }
            self.follow(id, node, "m_outPlug", None, depth)?;
            return Ok(true);
        }
        if node.class == "STU_BB4AC733" {
            return self.follow(id, node, "m_outPlug", None, depth);
        }
        self.run_node_slow(id, node, caller, depth)
    }

    #[inline(never)]
    fn run_node_slow(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        let mut registration = RegistrationChecks::default();
        match node.class.as_str() {
            _ if node.declares_branch_action() => {
                let Some(value) =
                    self.evaluate(id, node, field(&node.fields, "m_condition"), caller)
                else {
                    return Ok(false);
                };
                let output = if truthy(&value) {
                    "m_truePlug"
                } else {
                    "m_falsePlug"
                };
                self.follow(id, node, output, caller, depth)
            }
            _ if node.declares_room_message_action() => {
                if !self.send_room_message(id, node, caller)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            _ if node.declares_room_cue_action() => {
                if !self.send_room_cue(id, node, caller)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            _ if node.declares_radial_health_action() => {
                if !self.request_radial_health(id, node, caller) {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            _ if node.declares_swept_displacement_action() => {
                let site = self.site(id, node);
                if !script_expr::valid_entity(site.entity) {
                    self.report(site, Issue::UnsupportedField("swept displacement source"));
                    return Ok(false);
                }
                let Some(delta) = self.evaluate(id, node, field(&node.fields, "delta"), caller)
                else {
                    return Ok(false);
                };
                let displacement = match delta {
                    Value::Vector(delta) => SweptDisplacement::new(delta),
                    _ => None,
                };
                let Some(displacement) = displacement else {
                    self.report(site, Issue::Expression(EvalError::MalformedConfig(
                        "expected a finite nonzero swept displacement of at most 32 world units",
                    )));
                    return Ok(false);
                };
                let Some(frame) = self.machine.frame.checked_add(1) else {
                    self.report(site, Issue::UnsupportedField("swept displacement frame"));
                    return Ok(false);
                };
                match self
                    .world
                    .queue_swept_displacement(site, frame, displacement)
                {
                    Some(true) => {
                        self.follow(id, node, "m_outPlug", caller, depth)?;
                        Ok(true)
                    }
                    Some(false) => Ok(false),
                    None => {
                        self.report(site, Issue::UnsupportedField("swept displacement service"));
                        Ok(false)
                    }
                }
            }
            _ if node.declares_relocation_action() => {
                let site = self.site(id, node);
                if !script_expr::valid_entity(site.entity) {
                    self.report(site, Issue::UnsupportedField("relocation source"));
                    return Ok(false);
                }
                let Some(destination) =
                    self.evaluate(id, node, field(&node.fields, "destination"), caller)
                else {
                    return Ok(false);
                };
                let relocation = match destination {
                    Value::Vector(destination) => Relocation::new(destination),
                    _ => None,
                };
                let Some(relocation) = relocation else {
                    self.report(
                        site,
                        Issue::Expression(EvalError::MalformedConfig(
                            "expected a finite float32-representable relocation destination",
                        )),
                    );
                    return Ok(false);
                };
                let Some(frame) = self.machine.frame.checked_add(1) else {
                    self.report(site, Issue::UnsupportedField("relocation frame"));
                    return Ok(false);
                };
                match self.world.queue_scripted_motion(
                    site,
                    frame,
                    ScriptedMotion::Relocate(relocation),
                ) {
                    Some(true) => {
                        self.follow(id, node, "m_outPlug", caller, depth)?;
                        Ok(true)
                    }
                    Some(false) => Ok(false),
                    None => {
                        self.report(site, Issue::UnsupportedField("relocation service"));
                        Ok(false)
                    }
                }
            }
            _ if node.declares_health_change_action() => {
                let site = self.site(id, node);
                let Some(target) = self.evaluate(id, node, field(&node.fields, "target"), caller)
                else {
                    return Ok(false);
                };
                let entity = match target {
                    Value::Entity(entity) if script_expr::valid_entity(entity) => entity,
                    _ => {
                        self.report(
                            site,
                            Issue::Expression(EvalError::MalformedConfig(
                                "expected a bound health change target",
                            )),
                        );
                        return Ok(false);
                    }
                };
                let Some(delta) = self.evaluate(id, node, field(&node.fields, "delta"), caller)
                else {
                    return Ok(false);
                };
                let delta = match delta {
                    Value::Integer(amount) if amount != 0 => amount as f64,
                    Value::Float(amount) if amount.is_finite() && amount != 0.0 => amount,
                    _ => {
                        self.report(
                            site,
                            Issue::Expression(EvalError::MalformedConfig(
                                "expected a finite nonzero health change delta",
                            )),
                        );
                        return Ok(false);
                    }
                };
                if !self.expired_health_event_body(entity)
                    && !self.world.queue_health_change(site, entity, delta)
                {
                    self.report(site, Issue::UnsupportedField("health change request"));
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STUStatescriptActionCreateEntity" => self.create_entity(id, node, depth),
            "STU_6E18DEA0"
                if registration.permits(self.graph(id).index == 0x0ca7)
                    && field(&node.fields, "m_A9220BE5").is_null() =>
            {
                let handle =
                    self.creation_scalar(id, node, field(&node.fields, "m_EDB2BD2A"), Value::Null)?;
                if !matches!(handle, Some(Value::Null | Value::Integer(0))) {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("gate animation handle"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_523EE38D"
                if registration.permits(matches!(
                    (self.graph(id).index, node.pos),
                    (0x0c89, 43) | (0x0c93, 11) | (0x0cbc, 36 | 38 | 57 | 58)
                )) =>
            {
                let Some(Value::Entity(entity)) =
                    self.creation_scalar(id, node, field(&node.fields, "m_entity"), Value::Null)?
                else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("corpse retirement target"),
                    );
                    return Ok(false);
                };
                let phase_exit = self.graph(id).index == 0x0cbc && matches!(node.pos, 57 | 58);
                if !self
                    .world
                    .request_corpse_retirement(self.site(id, node), entity, phase_exit)
                {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("corpse retirement service"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_523EE38D"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                if let Some(Value::Entity(entity)) =
                    self.creation_scalar(id, node, field(&node.fields, "m_entity"), Value::Null)?
                {
                    if script_expr::valid_entity(entity) {
                        let _ = self.world.request_corpse_retirement(
                            self.site(id, node),
                            entity,
                            false,
                        );
                    }
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_064A9E0D"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_AFBB59E9"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                let site = self.site(id, node);
                // `m_C285CBB2` is the impulse recipient (target); `m_0F99C09F` is the instigator.
                let recipient_field = field(&node.fields, "m_C285CBB2");
                let target_field = if recipient_field.is_null() {
                    field(&node.fields, "m_0F99C09F")
                } else {
                    recipient_field
                };
                let target_val = if target_field.is_null() {
                    Some(Value::Entity(site.entity))
                } else {
                    self.evaluate(id, node, target_field, caller)
                };
                if let Some(Value::Vector(delta)) =
                    self.evaluate(id, node, field(&node.fields, "m_direction"), caller)
                {
                    if delta.iter().all(|a| (*a as f32).is_finite()) {
                        let targets = match target_val {
                            Some(Value::Array(items)) => items,
                            Some(v @ Value::Entity(_)) => vec![v],
                            _ => Vec::new(),
                        };
                        for item in targets {
                            if let Value::Entity(entity) = item {
                                if entity == site.entity {
                                    let _ = self.world.apply_velocity_ops(
                                        site,
                                        self.machine.frame,
                                        &[VelocityOp::AddVector(delta)],
                                    );
                                } else if script_expr::valid_entity(entity) {
                                    let _ = self.world.queue_modify_health(
                                        site,
                                        entity,
                                        0.0,
                                        Some(delta),
                                    );
                                }
                            }
                        }
                    }
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_A807207A"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                let site = self.site(id, node);
                if let Some(distance) = self
                    .evaluate(id, node, field(&node.fields, "m_6A79BD98"), caller)
                    .and_then(pool_number)
                    .filter(|d| d.is_finite() && *d > 0.0 && *d <= 32.0)
                {
                    if let Some(frame) = self.machine.frame.checked_add(1) {
                        let _ = self.world.queue_blink(site, frame, distance);
                    }
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_CFCE55DC"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                let site = self.site(id, node);
                let target_field = field(&node.fields, "m_target");
                let targets_self = target_field.is_null()
                    || matches!(
                        self.evaluate(id, node, target_field, caller),
                        Some(Value::Entity(entity)) if entity == site.entity
                    );
                if targets_self {
                    if let Some(Value::Vector(dest)) =
                        self.evaluate(id, node, field(&node.fields, "m_AF774F7D"), caller)
                    {
                        if let (Some(relocation), Some(frame)) =
                            (Relocation::new(dest), self.machine.frame.checked_add(1))
                        {
                            let _ = self.world.queue_scripted_motion(
                                site,
                                frame,
                                ScriptedMotion::Teleport(relocation),
                            );
                        }
                    }
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_B6E3C77D"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                self.run_create_entity(id, node, caller)?;
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_0B0BABC6"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_08D6BA6E"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                self.run_authored_raycast(id, node, caller)?;
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_147B34C6"
                if registration.permits((self.graph(id).index, node.pos) == (0x180a, 22)) =>
            {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_88DE7AD3"
                if registration.permits((self.graph(id).index, node.pos) == (0x0c8c, 72)) =>
            {
                // In 104319 this base action forwards the authored phase handoff.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_0F647E55" => {
                let Some(Value::Integer(team @ -1..=4)) =
                    self.evaluate(id, node, field(&node.fields, "m_67465E75"), None)
                else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("entity team index"),
                    );
                    return Ok(false);
                };
                let Some(targets) = self.evaluate(id, node, field(&node.fields, "m_targets"), None)
                else {
                    return Ok(false);
                };
                let targets = match targets {
                    Value::Array(values) => values,
                    value => vec![value],
                };
                let site = self.site(id, node);
                for target in targets {
                    if let Value::Entity(entity) = target {
                        if script_expr::valid_entity(entity)
                            && !self.world.assign_team(site, entity, team as i8)
                        {
                            self.report(site, Issue::UnsupportedField("entity team assignment"));
                            return Ok(false);
                        }
                    }
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_4C3509FB" | "STU_CBE8BDAA" => self.insert_array(id, node, caller, depth),
            "STUStatescriptActionPlayScript" => self.play_script(id, node, depth),
            "STU_1C2CC6D5" => {
                let destination = field(&node.fields, "m_26E3B366");
                match script_expr::dynamic(destination) {
                    Ok(Some(target)) => {
                        if target.id != 0 {
                            self.write(source_key(id, target), Value::Array(Vec::new()))?;
                        }
                    }
                    Ok(None) if destination.is_null() => {}
                    Ok(None) => {
                        self.report(
                            self.site(id, node),
                            Issue::UnsupportedField("array clear target"),
                        );
                        return Ok(false);
                    }
                    Err(error) => {
                        self.report(self.site(id, node), Issue::Expression(error));
                        return Ok(false);
                    }
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_DA54F8F7" => {
                let output = match script_expr::dynamic(field(&node.fields, "m_26E3B366")) {
                    Ok(Some(output)) if output.id != 0 => output,
                    _ => {
                        self.report(
                            self.site(id, node),
                            Issue::UnsupportedField("array removal destination"),
                        );
                        return Ok(false);
                    }
                };
                let target = source_key(id, output);
                if let Value::Array(mut values) = self.machine.value(target, None) {
                    let value_field = field(&node.fields, "m_value");
                    if !value_field.is_null() {
                        if let Some(remove_val) = self.evaluate(id, node, value_field, None) {
                            let before_len = values.len();
                            match remove_val {
                                Value::Array(items) => {
                                    for _ in 0..values.len() {
                                        self.spend("value array removal")?;
                                    }
                                    values.retain(|existing| {
                                        !items.iter().any(|item| equal(existing, item))
                                    });
                                }
                                single => {
                                    for _ in 0..values.len() {
                                        self.spend("value array removal")?;
                                    }
                                    values.retain(|existing| !equal(existing, &single));
                                }
                            }
                            if values.len() != before_len {
                                self.write(target, Value::Array(values))?;
                            }
                        }
                    } else {
                        let index = self
                            .evaluate_at_with_initial(
                                self.site(id, node),
                                field(&node.fields, "m_index"),
                                None,
                                Value::Integer(-1),
                            )
                            .map(|value| script_expr::to_int(&value, -1))
                            .unwrap_or(-1);
                        if let Ok(index) = usize::try_from(index) {
                            if index < values.len() {
                                for _ in index..values.len() {
                                    self.spend("ordered array removal")?;
                                }
                                values.remove(index);
                                self.write(target, Value::Array(values))?;
                            }
                        }
                    }
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_BC5E4622" if node.is_action() && node.remote == -1 => {
                self.cycle_index(id, node, depth)
            }
            "STU_42578342"
                if node.is_action()
                    && (participant_filter(node).is_some()
                        || placement_collection_tag(node).is_some()) =>
            {
                if !self.collect_targets(id, node)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            // Effects without the dynamic operand already use the base action.
            // The alternate shape remains limited to the verified placements.
            "STUStatescriptActionEffect"
                if field(&node.fields, "m_37B02425").is_null()
                    || (registration.permits(matches!(
                        (self.graph(id).index, node.pos),
                        (0x01c7, 74) | (0x0257, 32) | (0x01c6, 37 | 38)
                    )) && script_expr::native_shape(
                        field(&node.fields, "m_3E3E203A"),
                        "STU_B5A0CAF0",
                        None,
                    ) && asset_guid(field(field(&node.fields, "m_effect"), "m_effect"))
                        == Some(match self.graph(id).index {
                            0x01c7 => 0x0300_0000_0000_1ba9,
                            0x01c6 if node.pos == 37 => 0x0300_0000_0000_03e0,
                            0x01c6 => 0x0300_0000_0000_05a8,
                            _ => 0x0300_0000_0000_330f,
                        })
                        && field(field(&node.fields, "m_effect"), "m_resourceKey").is_null()
                        && field(&node.fields, "m_dataFlow").is_null()) =>
            {
                let effect = field(&node.fields, "m_effect");
                let legacy_self_effect = field(&node.fields, "m_37B02425").is_null()
                    && script_expr::native_shape(
                        field(&node.fields, "m_3E3E203A"),
                        "STU_B5A0CAF0",
                        None,
                    )
                    && effect["$"] == "STUConfigVarEffect"
                    && script_expr::native_fields(effect, &["m_effect", "m_resourceKey"])
                    && field(effect, "m_resourceKey").is_null()
                    && asset_guid(field(effect, "m_effect"))
                        .is_some_and(|guid| guid >> 48 == 0x0300);
                if legacy_self_effect || (node.remote >= 0 && node.is_networked()) {
                    self.capture_frame_action(id, node);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STUStatescriptActionEffect"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                if node.remote >= 0 && node.is_networked() {
                    self.capture_frame_action(id, node);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_187D5125" => {
                let graph = self.graph(id);
                let abilities: Vec<_> = self.machine.snapshot.instances[&id]
                    .states
                    .iter()
                    .filter_map(|(&raw, state)| {
                        (state.active && matches!(state.kind, StateKind::Ability { .. }))
                            .then(|| graph.state(usize::from(raw)).map(|node| (raw, node)))
                            .flatten()
                    })
                    .collect();
                for (raw, ability) in abilities {
                    self.stop_ability(StateKey { instance: id, raw }, ability, depth + 1)?;
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_CAE23847" => Ok(false),
            "STU_3A276B3A" => {
                let condition = field(&node.fields, "m_condition");
                let allowed = if condition.is_null() {
                    true
                } else {
                    let Some(value) = self.evaluate(id, node, condition, caller) else {
                        return Ok(false);
                    };
                    truthy(&value)
                };
                if allowed && !self.send_game_message(id, node)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_D137ECA0" => {
                let condition = field(&node.fields, "m_D10618D1");
                let passed = if condition.is_null() {
                    true
                } else {
                    let Some(value) = self.evaluate(id, node, condition, caller) else {
                        return Ok(false);
                    };
                    truthy(&value)
                };
                if passed {
                    let target = field(&node.fields, "m_out_Var");
                    let value_expr = field(&node.fields, "m_value");
                    if !target.is_null() && !value_expr.is_null() {
                        if let Ok(Some(var)) = script_expr::lvalue(target) {
                            if let Some(value) = self.evaluate(id, node, value_expr, caller) {
                                self.write(source_key(id, var), value)?;
                            } else {
                                return Ok(false);
                            }
                        }
                    }
                    self.follow(id, node, "m_outPlug", caller, depth)
                } else {
                    self.follow(id, node, "m_08FBE734", caller, depth)
                }
            }
            "STU_008D30B1" | "STU_621AF478" | "STU_1F2DB0E9" => {
                for key in [
                    "m_targets",
                    "m_amount",
                    "m_1AC61055",
                    "m_20100AF4",
                    "m_DB744C76",
                    "m_16432044",
                    "m_6447237A",
                    "m_B384134B",
                    "m_AF872E86",
                    "m_E3798C00",
                ] {
                    let cfg = field(&node.fields, key);
                    if !cfg.is_null() && self.evaluate(id, node, cfg, caller).is_none() {
                        return Ok(false);
                    }
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STUStatescriptActionSendGameMessage" => {
                if !self.send_game_message(id, node)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_57692020" if node.is_action() => {
                let site = self.site(id, node);
                let message = node
                    .field("m_gameMessage.m_gameMessage")
                    .and_then(asset_guid);
                if !field(&node.fields, "m_8B34E9C3").is_null() {
                    self.report(site, Issue::UnsupportedField("routed game message targets"));
                    return Ok(false);
                }
                let Some(parameters) = self.game_message_parameters(id, node)? else {
                    return Ok(false);
                };
                let Some(message) = message else {
                    self.report(site, Issue::UnsupportedField("routed game message targets"));
                    return Ok(false);
                };
                let routed = self.world.route_game_message(site, message, &parameters);
                let local = self.machine.queue_entries_from(
                    self.graphs,
                    None,
                    Some(site.entity),
                    message,
                    parameters,
                    self.machine.now_ms,
                )? != 0;
                if !routed && !local {
                    self.report(site, Issue::UnsupportedField("routed game message targets"));
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_C43F9944"
                if registration.permits((self.graph(id).index, node.pos) == (0x180a, 28)) =>
            {
                let site = self.site(id, node);
                let message = POST_MATCH_COMPLETE;
                let delivered = if node.game_message_id() == Some(message)
                    && field(&node.fields, "m_params").is_null()
                {
                    let Some(parameters) = self.game_message_parameters(id, node)? else {
                        return Ok(false);
                    };
                    self.world.route_game_message(site, message, &parameters)
                } else {
                    false
                };
                if !delivered {
                    self.report(site, Issue::UnsupportedField("post-match completion"));
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if registration.permits((self.graph(id).index, node.pos) == (0x0cec, 11))
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_06f2)
                    && is_self_target(node, "m_targets")
                    && field(&node.fields, "m_condition").is_null()
                    && field(&node.fields, "m_params").is_null()
                    && field(&node.fields, "m_8B34E9C3").is_null() =>
            {
                // Client-only cue: the pinned server handler only forwards.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if registration
                    .permits(self.graph(id).index == 0x1188 && matches!(node.pos, 58 | 59))
                    && node.is_action()
                    && node.is_networked()
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_0fce)
                    && script_expr::native_shape(
                        field(&node.fields, "m_targets"),
                        "STU_A3DEF368",
                        None,
                    ) =>
            {
                // The authored HUD announcement targets clients. Like the
                // reference client-message action, it has no server effect;
                // the client VM handles presentation from its own UI graph.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if registration
                    .permits(self.graph(id).index == 0x0019 && matches!(node.pos, 48 | 50))
                    && is_self_target(node, "m_targets")
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_0173) =>
            {
                // Reload timing comes from the animation state. The message's
                // receiver is client-only.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if registration.permits(self.graph(id).index == 0x0c8c)
                    && node.is_action()
                    && node.is_networked()
                    && node.remote >= 0
                    && matches!(
                        (
                            node.pos,
                            node.field("m_gameMessage.m_gameMessage")
                                .and_then(asset_guid)
                        ),
                        (265, Some(0x0240_0000_0000_06e0))
                            | (267 | 268, Some(0x0240_0000_0000_06e1))
                    ) =>
            {
                if !self.world.queue_mode_action(self.site(id, node)) {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("mode action delivery"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if registration.shape(
                    node.literal_game_message_id().is_some()
                        && is_self_target(node, "m_targets")
                        && field(&node.fields, "m_condition").is_null()
                        && field(&node.fields, "m_params").is_null()
                        && field(&node.fields, "m_8B34E9C3").is_null(),
                ) =>
            {
                if !self.capture_frame_action(id, node) {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("client message projection"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            // These placements resolve to the 104319 base action.
            "STU_0FC6DAEA"
                if registration
                    .permits(self.graph(id).index == 0x0ca5 && matches!(node.pos, 66..=69)) =>
            {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            // The reference dispatches this action through its base
            // continuation without reading the config. The authored shape
            // keeps the header with two null operands and unlinked plugs —
            // the same no-payload form the earlier positions carry — so the
            // continuation never needs a position allowlist.
            "STU_9F3FE778"
                if registration.shape(
                    node.state.is_none()
                        && node.remote < 0
                        && !node.server_only
                        && field(&node.fields, "m_5328E4F8").is_null()
                        && field(&node.fields, "m_value").is_null()
                        && field(&node.fields, "m_BF5B22B7").is_null()
                        && field(&node.fields, "m_8BF03679").is_null()
                        && field(&node.fields, "m_A2287776").as_u64() == Some(0)
                        && field(&node.fields, "m_AED90719").as_u64() == Some(0)
                        && is_unlinked_plug(&node.fields, "m_inPlug", "STUStatescriptInputPlug")
                        && is_unlinked_plug(&node.fields, "m_outPlug", "STUStatescriptOutputPlug"),
                ) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_C43F9944"
                if registration.permits((self.graph(id).index, node.pos) == (0x0c8c, 236))
                    && field(&node.fields, "m_condition").is_null()
                    && field(&node.fields, "m_params").is_null()
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_08f9) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_C43F9944"
                if registration.permits(self.graph(id).index == 0x0ce0)
                    && node.is_action()
                    && field(&node.fields, "m_condition").is_null()
                    && field(&node.fields, "m_params").is_null()
                    && matches!(
                        (
                            node.pos,
                            node.field("m_gameMessage.m_gameMessage")
                                .and_then(asset_guid)
                        ),
                        (71, Some(0x0240_0000_0000_08e1)) | (72..=76, Some(0x0240_0000_0000_08e2))
                    ) =>
            {
                // The 104319 dedicated script VM follows the action's continuation.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_CEBDAF17" if registration.shape(is_stat_action(node)) => {
                let site = self.site(id, node);
                let stats_available = if is_self_target(node, "m_targets") {
                    self.world.player_stats_available(site)
                } else {
                    self.world.player_stats_service_available(site)
                };
                if stats_available != Some(false) {
                    self.report(site, Issue::UnsupportedAction(node.class.clone()));
                    return Ok(false);
                }
                // A missing stat service leaves the operands dormant.
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_43B63AD0"
                if registration.shape(
                    node.state.is_none()
                        && !node.is_state()
                        && node.remote == -1
                        && (field(&node.fields, "m_ADEB6E05").as_bool() == Some(true)
                            || field(&node.fields, "m_ADEB6E05").as_u64() == Some(1)),
                ) =>
            {
                // This class uses the base Action continuation.
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_FF3471E9" if registration.shape(is_horizontal_velocity_clamp(node)) => {
                let row = &array_field(&node.fields, "m_BC5E91CF")[0];
                let Some(low) = self
                    .evaluate(id, node, field(row, "m_8AD739CE"), None)
                    .and_then(pool_number)
                else {
                    return Ok(false);
                };
                let Some(high) = self
                    .evaluate(id, node, field(row, "m_8213EB22"), None)
                    .and_then(pool_number)
                else {
                    return Ok(false);
                };
                // Selector 1 clamps horizontal magnitude. A zero lower bound cannot
                // add velocity, so the host only needs to apply the upper cap.
                if low != 0.0
                    || !(high as f32).is_finite()
                    || high < 0.0
                    || !self.world.cap_horizontal_velocity(
                        self.site(id, node),
                        self.machine.frame,
                        high,
                    )
                {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_FF3471E9" if registration.shape(is_vertical_velocity_impulse(node)) => {
                let rows = array_field(&node.fields, "m_BC5E91CF");
                let Some(reset) = self.evaluate(id, node, field(&rows[0], "m_9B7A63EA"), caller)
                else {
                    return Ok(false);
                };
                let Some(impulse) = self.evaluate(id, node, field(&rows[1], "m_9B7A63EA"), caller)
                else {
                    return Ok(false);
                };
                let site = self.site(id, node);
                let (Some(vertical), Value::Vector(delta)) = (pool_number(reset), impulse) else {
                    self.report(
                        site,
                        Issue::Expression(EvalError::MalformedConfig("velocity impulse operands")),
                    );
                    return Ok(false);
                };
                if !(vertical as f32).is_finite()
                    || !delta.iter().all(|&axis| (axis as f32).is_finite())
                    || !(vertical as f32 + delta[1] as f32).is_finite()
                {
                    self.report(
                        site,
                        Issue::Expression(EvalError::MalformedConfig("nonfinite velocity impulse")),
                    );
                    return Ok(false);
                }
                if !self
                    .world
                    .apply_velocity_impulse(site, self.machine.frame, vertical, delta)
                {
                    self.report(site, Issue::UnsupportedField("velocity impulse service"));
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_FF3471E9"
                if registration.shape({
                    let rows = array_field(&node.fields, "m_BC5E91CF");
                    matches!(rows, [row] if matches!(
                        row.get("$").and_then(Json::as_str),
                        Some("STU_748FC65A" | "STU_95A10547")
                    ) && field(row, "m_priority").is_null()
                        && row.get(if row.get("$").and_then(Json::as_str) == Some("STU_748FC65A") {
                            "m_A9561CA0"
                        } else {
                            "m_CFEC5F76"
                        }).and_then(Json::as_u64) == Some(1))
                }) =>
            {
                let rows = array_field(&node.fields, "m_BC5E91CF");
                let [row] = rows else { return Ok(false) };
                let cap = row.get("$").and_then(Json::as_str) == Some("STU_748FC65A");
                let selector = if cap { "m_A9561CA0" } else { "m_CFEC5F76" };
                if row.get(selector).and_then(Json::as_u64) != Some(1) {
                    return Ok(false);
                }
                let Some(value) = self.evaluate(id, node, field(row, "m_9B7A63EA"), None) else {
                    return Ok(false);
                };
                let site = self.site(id, node);
                if !self
                    .world
                    .edit_velocity(site, self.machine.frame, value.clone(), cap)
                {
                    if !self.world.admits_generic_player_runtime(site) {
                        return Ok(false);
                    }
                    let op = match (value, cap) {
                        (Value::Vector(v), false) if v.iter().all(|a| (*a as f32).is_finite()) => {
                            Some(VelocityOp::SetVector(v))
                        }
                        (Value::Float(max), true) if (max as f32).is_finite() && max >= 0.0 => {
                            Some(VelocityOp::CapSpeed {
                                axis: VelocityAxis::Horizontal,
                                max,
                            })
                        }
                        (Value::Integer(max), true) if max >= 0 => Some(VelocityOp::CapSpeed {
                            axis: VelocityAxis::Horizontal,
                            max: max as f64,
                        }),
                        _ => None,
                    };
                    if let Some(op) = op {
                        let _ = self
                            .world
                            .apply_velocity_ops(site, self.machine.frame, &[op]);
                    }
                }
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_FF3471E9" if self.world.admits_velocity_ops(self.site(id, node)) => {
                self.apply_generic_velocity_ops(id, node, caller);
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_D087B558"
                if registration.shape(
                    is_self_target(node, "m_target")
                        && field(&node.fields, "m_instigator").is_null()
                        && node
                            .field("m_D7DDEEAD.m_value")
                            .and_then(Json::as_u64)
                            .is_some(),
                ) =>
            {
                // Continue through this authored action as the reference fallback does.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_D087B558"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node)) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_6FEE499E"
                if registration.shape(matches!(
                    field(&node.fields, "m_93CDFD2F").as_u64(),
                    Some(0 | 1)
                )) =>
            {
                // Movement ability state-reset action (used on Blink, Jump Jet, Grapple, GA, Uppercut).
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STUStatescriptActionCriteria" => {
                self.play_actor_criterion(id, node);
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_7106C356" => {
                if !matches!(
                    node.field("m_text").and_then(|text| text["$"].as_str()),
                    Some("STU_E7579408" | "STU_919FD47C" | "STUConfigVarExpression")
                ) || !self.capture_frame_action(id, node)
                {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("callout action"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STUStatescriptActionEffect" => {
                // The captured 104319 reference runs this presentation-only
                // action as a continuation no-op; the server executes no
                // effect here. The handful of presentation graphs that also
                // replicate frame-action variables are unrelated locations,
                // and their projection rows already flow through the frame
                // action variable table.
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_D38D0035" | "STU_6E5C5F66" => {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_631D36C3" | "STU_71FE9852" | "STU_94771561"
                if node.is_action() && node.remote == -1 =>
            {
                // These classes inherit the native Action handler. Its prediction
                // notification does not read their cosmetic payload.
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_545AB3B8" => {
                // The captured 104319 registry has no exact action binding:
                // its Action base forwards without evaluating loadout/target.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            // The reference has no model for this action: its fallback only
            // follows m_outPlug and reports the path as taken. The native
            // effect, if any, is unverified, so only the payload-free shape
            // every build 104319 instance has is accepted.
            "STU_E9694EFD" if is_bare_header_action(node) => {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_22926252"
                if field(&node.fields, "m_67B81D25").is_null()
                    && field(&node.fields, "m_EB35D2F1").is_null() =>
            {
                self.post(id, Event::ExitInstance)?;
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_3D419B63"
                if registration.shape(
                    is_self_target(node, "m_targets")
                        && node
                            .field("m_category")
                            .and_then(asset_guid)
                            .is_some_and(|category| category >> 48 == 0x01e0)
                        && self
                            .world
                            .voice_category_service_available(self.site(id, node))
                            == Some(false),
                ) =>
            {
                // This operand is a voice category. With no native voice service,
                // the host has no category operation to apply.
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_12F5D52D" => {
                let mut complete = true;
                for item in array_field(&node.fields, "m_143D4C5B") {
                    if item.is_object() {
                        complete = self.store(id, node, item, false)? && complete;
                    }
                }
                if complete {
                    self.follow(id, node, "m_outPlug", None, depth)?;
                }
                Ok(complete)
            }
            "STU_821116A6" => {
                let Some(container) =
                    self.evaluate(id, node, field(&node.fields, "m_814F8EC3"), caller)
                else {
                    return Ok(false);
                };
                let Some(value) = self.evaluate(id, node, field(&node.fields, "m_value"), caller)
                else {
                    return Ok(false);
                };
                if !script_expr::contains(&container, &value) {
                    return self.follow(id, node, "m_falsePlug", caller, depth);
                }
                Ok(self.follow(id, node, "m_truePlug", caller, depth)?
                    || (node.links("m_truePlug").next().is_none()
                        && node.links("m_falsePlug").next().is_none()))
            }
            "STU_3387AB5D" | "STU_7DEB522D" => {
                let condition = if node.class == "STU_7DEB522D" {
                    self.logical_button_id(id, node, None)
                        .is_some_and(|button| self.effective_button_held(button))
                } else {
                    let active = if (self.graph(id).index, node.pos) == (0x0c87, 21) {
                        self.world.objective_route_active(self.site(id, node))
                    } else {
                        None
                    };
                    if let Some(active) = active {
                        active
                    } else {
                        let Some(value) =
                            self.evaluate(id, node, field(&node.fields, "m_condition"), caller)
                        else {
                            return Ok(false);
                        };
                        truthy(&value)
                    }
                };
                if !condition {
                    return self.follow(id, node, "m_falsePlug", caller, depth);
                }
                Ok(self.follow(id, node, "m_truePlug", caller, depth)?
                    || (node.links("m_truePlug").next().is_none()
                        && node.links("m_falsePlug").next().is_none()))
            }
            "STU_4E2BCAAB" => {
                let value = self.evaluate(id, node, field(&node.fields, "m_AA6E611E"), caller);
                let seconds = value
                    .and_then(pool_number)
                    .filter(|seconds| *seconds >= 0.0 && *seconds < 2_147_483.0);
                let state = caller.or_else(|| {
                    self.machine.snapshot.instances[&id]
                        .parent
                        .map(|(instance, raw)| StateKey { instance, raw })
                });
                let ready = if let Some(seconds) = seconds {
                    let delay = ((seconds * 1000.0) as f32).round() as u64;
                    let ready = state.and_then(|key| self.state(key)).is_some_and(|state| {
                        self.machine.now_ms.saturating_sub(state.start_ms) >= delay
                    });
                    if !ready {
                        if let Some(key) = caller {
                            if let Some(state) = self.state(key) {
                                let due = state.start_ms.saturating_add(delay);
                                self.machine.queues.retain(key.instance,|_,event|!matches!(event,Event::Timer(raw,0) if *raw==key.raw));
                                self.post_after(
                                    key.instance,
                                    due.saturating_sub(self.machine.now_ms),
                                    Event::Timer(key.raw, 0),
                                )?;
                            }
                        }
                    }
                    ready
                } else {
                    false
                };
                if !ready {
                    return self.follow(id, node, "m_falsePlug", caller, depth);
                }
                Ok(self.follow(id, node, "m_truePlug", caller, depth)?
                    || (node.links("m_truePlug").next().is_none()
                        && node.links("m_falsePlug").next().is_none()))
            }
            "STUStatescriptActionSwitch" => {
                let Some(value) = self.evaluate(id, node, field(&node.fields, "m_262AE982"), None)
                else {
                    return Ok(false);
                };
                let cases = array_field(&node.fields, "m_FDD34C25");
                let mut path = "m_CD38D5DB".to_owned();
                if field(&node.fields, "m_F0F0720B").as_u64() == Some(1) {
                    // Indexed switch mode (e.g. Wrecking Ball Adaptive Shield `1566` 0..6 enemy chimes).
                    let index = script_expr::to_int(&value, -1);
                    if let Ok(idx) = usize::try_from(index) {
                        if cases.get(idx).is_some_and(Json::is_object) {
                            path = format!("m_FDD34C25[{idx}]");
                        }
                    }
                } else {
                    for (number, case) in cases.iter().enumerate() {
                        if !case.is_object() {
                            continue;
                        }
                        let Some(candidate) = self.evaluate(id, node, field(case, "m_value"), None)
                        else {
                            return Ok(false);
                        };
                        if equal(&value, &candidate) {
                            path = format!("m_FDD34C25[{number}]");
                            break;
                        }
                    }
                }
                self.follow(id, node, &path, None, depth)?;
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            _ => self.execute_generic_or_unsupported_action(
                id,
                node,
                caller,
                depth,
                registration.rejected(),
            ),
        }
    }

    #[inline(never)]
    fn apply_generic_velocity_ops(&mut self, id: u16, node: &Node, caller: Option<StateKey>) {
        let rows = array_field(&node.fields, "m_BC5E91CF");
        let mut ops = Vec::with_capacity(rows.len());
        for row in rows {
            let class = row.get("$").and_then(Json::as_str);
            let axis = match row.get("m_A9561CA0").and_then(Json::as_u64) {
                Some(0) => VelocityAxis::All,
                Some(1) => VelocityAxis::Horizontal,
                Some(2) => VelocityAxis::Vertical,
                Some(3) => VelocityAxis::VerticalUp,
                _ => VelocityAxis::All,
            };
            match class {
                Some("STU_4635C5E2") => {
                    if let Some(Value::Vector(v)) =
                        self.evaluate(id, node, field(row, "m_9B7A63EA"), caller)
                    {
                        if v.iter().all(|a| (*a as f32).is_finite()) {
                            ops.push(VelocityOp::AddVector(v));
                        }
                    }
                }
                Some("STU_95A10547") => {
                    if let Some(Value::Vector(v)) =
                        self.evaluate(id, node, field(row, "m_9B7A63EA"), caller)
                    {
                        if v.iter().all(|a| (*a as f32).is_finite()) {
                            ops.push(VelocityOp::SetVector(v));
                        }
                    }
                }
                Some("STU_9AF0FA80") => {
                    if let Some(val) = self
                        .evaluate(id, node, field(row, "m_9B7A63EA"), caller)
                        .and_then(pool_number)
                        .filter(|v| (*v as f32).is_finite())
                    {
                        ops.push(VelocityOp::SetSpeed { axis, value: val });
                    }
                }
                Some("STU_748FC65A") => {
                    if let Some(val) = self
                        .evaluate(id, node, field(row, "m_9B7A63EA"), caller)
                        .and_then(pool_number)
                        .filter(|v| (*v as f32).is_finite() && *v >= 0.0)
                    {
                        ops.push(VelocityOp::CapSpeed { axis, max: val });
                    }
                }
                Some("STU_1308AA6A" | "STU_EC4841E7") => {
                    if let Some(val) = self
                        .evaluate(id, node, field(row, "m_9B7A63EA"), caller)
                        .and_then(pool_number)
                        .filter(|v| (*v as f32).is_finite() && *v >= 0.0)
                    {
                        ops.push(VelocityOp::ScaleSpeed { axis, factor: val });
                    }
                }
                Some("STU_20E18047") => {
                    let low = self
                        .evaluate(id, node, field(row, "m_8AD739CE"), caller)
                        .and_then(pool_number)
                        .filter(|v| (*v as f32).is_finite() && *v >= 0.0)
                        .unwrap_or(0.0);
                    if let Some(high) = self
                        .evaluate(id, node, field(row, "m_8213EB22"), caller)
                        .and_then(pool_number)
                        .filter(|v| (*v as f32).is_finite() && *v >= low)
                    {
                        ops.push(VelocityOp::ClampSpeed {
                            axis,
                            min: low,
                            max: high,
                        });
                    }
                }
                _ => {}
            }
        }
        if !ops.is_empty() {
            let _ = self
                .world
                .apply_velocity_ops(self.site(id, node), self.machine.frame, &ops);
        }
    }

    #[inline(never)]
    fn execute_generic_or_unsupported_action(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        depth: usize,
        registration_rejected: bool,
    ) -> Result<bool, InitFault> {
        if !node.is_state()
            && self
                .world
                .admits_generic_player_runtime(self.site(id, node))
        {
            if node.is_action() {
                match node.class.as_str() {
                    "STU_100EF8C2" => {
                        let _ = self.capture_frame_action(id, node);
                        self.follow(id, node, "m_outPlug", caller, depth)?;
                        return Ok(true);
                    }
                    "STU_9F3FE778" => {
                        self.follow(id, node, "m_outPlug", None, depth)?;
                        return Ok(true);
                    }
                    "STU_A90C5991" | "STU_A1DF3B2D" | "STU_D6EB7F8D" | "STU_74B41EA0"
                    | "STU_3E5D9838" | "STU_3218BAEB" | "STU_5EAA04BB" | "STU_DC33334B"
                    | "STU_85438EDF" | "STU_9D4A25D1" | "STU_7EBA8E5E" | "STU_6E18DEA0" => {
                        self.follow(id, node, "m_outPlug", caller, depth)?;
                        return Ok(true);
                    }
                    _ => {}
                }
            }
            if let Some(behavior) = super::retail_catalog::classify_retail_action(&node.class) {
                return self.run_retail_action(id, node, caller, depth, behavior);
            }
        }
        self.report_diagnostic(
            self.site(id, node),
            Issue::UnsupportedAction(node.class.clone()),
            registration_rejected,
        );
        Ok(false)
    }

    fn cycle_index(&mut self, id: u16, node: &Node, depth: usize) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let destination = match script_expr::dynamic(field(&node.fields, "m_339A63DB")) {
            Ok(Some(variable)) => source_key(id, variable),
            Ok(None) => return self.follow(id, node, "m_outPlug", None, depth),
            Err(error) => {
                self.report(site, Issue::Expression(error));
                return Ok(false);
            }
        };
        let Some(count) = self.evaluate(id, node, field(&node.fields, "m_D10618D1"), None) else {
            return Ok(false);
        };
        let count = script_expr::to_int(&count, 0);
        let mut selected = None;
        if count > 0 {
            let Some(backwards) = self.evaluate(id, node, field(&node.fields, "m_C78954CB"), None)
            else {
                return Ok(false);
            };
            let step = if truthy(&backwards) { -1 } else { 1 };
            let mut candidate =
                script_expr::to_int(&self.machine.value(destination, None), 0).clamp(0, count - 1);
            for _ in 0..count {
                self.spend("cycle index candidate")?;
                candidate = (candidate + step).rem_euclid(count);
                let condition = field(&node.fields, "m_condition");
                let accepted = self.with_instance_context(site, None, |context| {
                    context.cycle_candidate = Some(candidate);
                    script_expr::evaluate(condition, context)
                });
                match accepted {
                    Ok(value) if truthy(&value) => {
                        selected = Some(candidate);
                        break;
                    }
                    Ok(_) => (),
                    Err(error) => {
                        self.report(site, Issue::Expression(error));
                        return Ok(false);
                    }
                }
            }
        }
        let index = match selected {
            Some(index) => index,
            None if field(&node.fields, "m_A53FE9DA").is_null() => -1,
            None => {
                let Some(failed) = self.evaluate(id, node, field(&node.fields, "m_A53FE9DA"), None)
                else {
                    return Ok(false);
                };
                script_expr::to_int(&failed, -1)
            }
        };
        self.write(destination, Value::Integer(index))?;
        self.follow(id, node, "m_outPlug", None, depth)?;
        Ok(true)
    }

    pub(super) fn assignment_value(
        &mut self,
        id: u16,
        node: &Node,
        config: &Json,
        initial: Value,
    ) -> Result<Option<Value>, InitFault> {
        if script_expr::assignment_group_source(config).is_some() {
            self.assignment_group(id, node, config)
        } else {
            Ok(self.evaluate_at_with_initial(self.site(id, node), config, None, initial))
        }
    }

    pub(in crate::script_init) fn assignment_group(
        &mut self,
        id: u16,
        node: &Node,
        config: &Json,
    ) -> Result<Option<Value>, InitFault> {
        let mut pending = vec![(config, 0)];
        let mut values = Vec::new();
        while let Some((config, depth)) = pending.pop() {
            let group = script_expr::assignment_group_source(config);
            let wrappers = group.map_or(0, |(_, wrappers)| wrappers);
            for _ in 0..=wrappers {
                self.spend("assignment config group")?;
            }
            let depth = depth + wrappers;
            if depth >= script_expr::MAX_CONFIG_DEPTH {
                self.report(
                    self.site(id, node),
                    Issue::Expression(script_expr::EvalError::NestingExceeded),
                );
                return Ok(None);
            }
            if config.is_null() {
                continue;
            }
            if let Some((configs, _)) = group {
                if configs.is_null() {
                    continue;
                }
                let Some(configs) = configs.as_array() else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("assignment config group"),
                    );
                    return Ok(None);
                };
                pending.extend(configs.iter().rev().map(|config| (config, depth + 1)));
                continue;
            }
            let Some(value) = self.evaluate(id, node, config, None) else {
                return Ok(None);
            };
            if value == Value::Null {
                continue;
            }
            let mut items = vec![(value, depth)];
            while let Some((value, depth)) = items.pop() {
                self.spend("assignment value array")?;
                if depth >= script_expr::MAX_CONFIG_DEPTH {
                    self.report(
                        self.site(id, node),
                        Issue::Expression(script_expr::EvalError::NestingExceeded),
                    );
                    return Ok(None);
                }
                if let Value::Array(array) = value {
                    items.extend(array.into_iter().rev().map(|value| (value, depth + 1)));
                } else {
                    values.push(value);
                }
            }
        }
        Ok(Some(Value::Array(values)))
    }

    pub(in crate::script_init) fn store(
        &mut self,
        id: u16,
        node: &Node,
        fields: &Json,
        indexed: bool,
    ) -> Result<bool, InitFault> {
        let config = field(fields, "m_value");
        let site = self.site(id, node);
        let value = self.assignment_value(id, node, config, Value::Integer(0))?;
        let Some(value) = value else { return Ok(false) };
        let destination = match script_expr::destination(field(fields, "m_out_Var")) {
            Ok(Some(target)) => target,
            Ok(None) => return Ok(true),
            Err(error) => {
                self.report(site, Issue::Expression(error));
                return Ok(false);
            }
        };
        let target = source_key(id, destination.variable);
        if !destination.path.is_empty() {
            if self
                .machine
                .links
                .get(&target)
                .is_some_and(|links| !links.is_empty())
            {
                self.report(
                    site,
                    Issue::UnsupportedField("nested destination linked storage"),
                );
                return Ok(false);
            }
            if indexed && (!field(fields, "m_index").is_null() || !field(fields, "m_key").is_null())
            {
                self.report(
                    site,
                    Issue::UnsupportedField("combined expression/index destination"),
                );
                return Ok(false);
            }
            let mut path = Vec::new();
            for part in destination.path {
                path.push(match part {
                    script_expr::DestinationPart::Index(config) => {
                        let Some(index) = self.evaluate(id, node, &config, None) else {
                            return Ok(false);
                        };
                        crate::script_destination::Step::Index(script_expr::to_int(&index, 0) as u16)
                    }
                    script_expr::DestinationPart::Field(field) => crate::script_destination::Step::Field(field),
                });
            }
            let mut root = self.machine.value(target, None);
            let mut cost = 0;
            if let Err(error) =
                crate::script_destination::assign(&mut root, &path, value, &mut cost)
            {
                self.report(site, Issue::UnsupportedField(error));
                return Ok(false);
            }
            self.machine.work_left =
                self.machine
                    .work_left
                    .checked_sub(cost)
                    .ok_or(InitFault::WorkBudget {
                        limit: self.machine.max_work,
                        operation: "nested destination storage",
                    })?;
            self.write(target, root)?;
            return Ok(true);
        }
        if indexed && !field(fields, "m_key").is_null() {
            self.report(site, Issue::UnsupportedField("SetVar map key"));
            return Ok(false);
        }
        let index = field(fields, "m_index");
        if indexed && !index.is_null() {
            let index = if index.is_object() {
                let Some(value) = self.evaluate(id, node, index, None) else {
                    return Ok(false);
                };
                Some(script_expr::to_int(&value, 0))
            } else {
                index
                    .as_i64()
                    .or_else(|| index.as_f64().map(|value| value as i64))
                    .or_else(|| index.as_bool().map(i64::from))
                    .or_else(|| index.as_str()?.trim().parse().ok())
            };
            let Some(index) = index else {
                self.report(site, Issue::UnsupportedField("SetVar array index"));
                return Ok(false);
            };
            if let Ok(index) = usize::try_from(index) {
                let mut items = match self.machine.value(target, None) {
                    Value::Array(items) => items,
                    Value::Null => Vec::new(),
                    value => vec![value],
                };
                if index >= usize::from(u16::MAX) {
                    self.report(
                        site,
                        Issue::UnsupportedField("SetVar array exceeds the wire count"),
                    );
                    return Ok(false);
                }
                if index >= items.len() {
                    let extra = index + 1 - items.len();
                    self.machine.work_left =
                        self.machine
                            .work_left
                            .checked_sub(extra)
                            .ok_or(InitFault::WorkBudget {
                                limit: self.machine.max_work,
                                operation: "SetVar array extension",
                            })?;
                    items.resize(index + 1, Value::Null);
                }
                items[index] = value;
                self.write(target, Value::Array(items))?;
                return Ok(true);
            }
        }
        self.write(target, value)?;
        Ok(true)
    }

    pub(in crate::script_init) fn client_pulse(
        &mut self,
        id: u16,
        node: &Node,
        count: u8,
    ) -> StateKind {
        if let Some(slot) = equipped_voice_pulse_slot(self.graph(id), node) {
            let site = self.site(id, node);
            if let Some(Value::Integer(slot)) = self.evaluate(id, node, slot, None) {
                if let Ok(slot) = usize::try_from(slot) {
                    if self.world.queue_equipped_voice(site, slot, &mut |count| {
                        ((u128::from(self.machine.random_u64()) * count as u128) >> 64) as usize
                    }) {
                        return StateKind::ClientPulse {
                            count,
                            fresh_at: None,
                        };
                    }
                }
            }
        }

        let now = self.machine.now_ms;
        let start = self.machine.snapshot.instances[&id].start_ms;
        StateKind::ClientPulse {
            count: count.wrapping_add(1),
            fresh_at: (now / FRAME_MS > start / FRAME_MS).then_some(now),
        }
    }

    pub(in crate::script_init) fn begin(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(node) = self.graph(id).state(usize::from(key.raw)) else {
            self.report(
                Site {
                    entity: self.machine.snapshot.entity,
                    instance: id,
                    graph: self.graph(id).index,
                    node: None,
                },
                Issue::MissingState(key.raw),
            );
            return Ok(());
        };
        if !self.observed_server_path(id, node) {
            return Ok(());
        }
        if let Some(state) = self.state(key).filter(|state| state.active) {
            let kind = state.kind;
            if kind == StateKind::ForCode {
                self.publish_for_code(key, node)?;
            }
            if matches!(kind, StateKind::SprayPlacement(_)) {
                if let Some(state) = self.state_mut(key) {
                    state.activation = state.activation.wrapping_add(1);
                }
                return self.place_spray(key, node, depth);
            }
            if kind == StateKind::ExplosionContact {
                self.refresh_contact(key, node)?;
            }
            if kind == StateKind::PhaseScriptList {
                self.refresh_phase_scripts(key, node)?;
            }
            if let StateKind::ClientPulse { count, .. } = kind {
                let pulse = self.client_pulse(id, node, count);
                self.state_mut(key).expect("active client pulser").kind = pulse;
            }
            if configured(node, "m_0DBAFD7F") {
                match kind {
                    StateKind::Wait => {
                        self.machine
                            .queues
                            .retain(id, |_, event| event.state() != Some(key.raw));
                        self.clear_tick(key);
                        self.state_mut(key).expect("active wait").start_ms = self.machine.now_ms;
                        self.arm_wait(key, node)?;
                    }
                    StateKind::WaitFrames { .. } => self.arm_frame_wait(key, node)?,
                    _ => (),
                }
            }
            if kind == StateKind::ChaseVar && configured(node, "m_9FF96195") {
                self.finish_chase(key, depth)?;
                let now = self.machine.now_ms;
                if let Some(state) = self.state_mut(key) {
                    state.start_ms = now;
                    state.activation = state.activation.wrapping_add(1);
                }
                self.begin_chase(key, node, depth)?;
            }
            return Ok(());
        }
        let mut registration = RegistrationChecks::default();
        let interval_frames = node.interval_frames();
        let button_lock = node.button_lock();
        let movement_scale = node.movement_scale();
        let legacy_timed_graph =
            node.class == "STU_54FEB21C" && (self.graph(id).index, node.pos) == (0x01f0, 14);
        let timed_graph_duration = if legacy_timed_graph {
            None
        } else {
            parent_owned_graph_duration(node)
        };
        let position_duration = crate::script_init::shapes::timed_position_duration(node);
        let mut wave_bonus = false;
        let kind = match node.class.as_str() {
            "STUStatescriptStateCreateEntity" => {
                // Echo Duplicate (`m_AA26E7DD` -> `STU_0AC15F78` referencing target entity).
                let copy_spec = field(&node.fields, "m_AA26E7DD");
                if copy_spec.get("$").and_then(Json::as_str) == Some("STU_0AC15F78") {
                    if let Some(Value::Entity(target)) =
                        self.evaluate(id, node, field(copy_spec, "m_entity"), None)
                    {
                        if crate::script_expr::valid_entity(target) {
                            let site = self.site(id, node);
                            let _ = self.world.duplicate_target_entity(site, target);
                        }
                    }
                }
                StateKind::Plain
            }
            // These client presentation states use the base state lifecycle on the server.
            "STUStatescriptState"
            | "STU_0C3A5D53"
            | "STU_B1FA9C2A"
            | "STU_63D801B1"
            | "STU_49FE0019"
            | "STU_9D7BF987"
            | "STU_B4CD94F4"
            | "STU_90C3BCEA"
            | "STUStatescriptStateDataFlowMapping"
            | "STU_E13B30A8"
            | "STUStatescriptStateUXPresenter" => StateKind::Plain,
            // Build 104319 resolves E9485CAF through the base State typeinfo:
            // registration 160, constructor AA826B0, vtable 8B6BC800.
            "STU_E9485CAF" => StateKind::Plain,
            "STU_58E77FC5"
                if registration.shape({
                    let mode = field(&node.fields, "m_4DA577AC");
                    node.remote == -1
                        && script_expr::native_shape(mode, "STUConfigVarInt", Some("m_value"))
                        && matches!(field(mode, "m_value").as_i64(), Some(0..=3))
                }) =>
            {
                // 90CA80/90CDE0 set and clear a local animation mode. The
                // owner's state stream carries activation with no payload.
                StateKind::Plain
            }
            "STU_ABEDF4B8"
                if registration.shape(is_empty_optional_plug(
                    field(&node.fields, "m_3DE788AE"),
                    "STU_774760EA",
                )) =>
            {
                // D3FAB0/D3FC40 retain and poll client resources. Without a
                // ready output, completing the request changes no graph state.
                StateKind::Plain
            }
            "STUStatescriptStateAnimatedCamera"
                if registration.permits((self.graph(id).index, node.pos) == (0x0ca5, 43)) =>
            {
                StateKind::Plain
            }
            "STU_96333BBE" | "STU_E8D976BE" | "STU_06AB2ECE"
                if registration.shape(
                    self.world
                        .spatial_binding_owner_available(self.site(id, node))
                        == Some(false),
                ) =>
            {
                // 911920, AE03F0 and 8E3B60 return before binding or
                // evaluating authored fields if 978A30 finds no auxiliary owner.
                StateKind::Plain
            }
            "STU_D040C3EF"
                if registration.shape(
                    is_self_target(node, "m_4A5CA514")
                        && self
                            .world
                            .spatial_binding_owner_available(self.site(id, node))
                            == Some(false),
                ) =>
            {
                // 9AE1F0 returns when the auxiliary service is absent before
                // acquiring a binding. 9AB2D0 skips cleanup without a binding.
                StateKind::Plain
            }
            "STU_9028E035" | "STU_43994F1B" | "STU_93B8758C" | "STU_A875CA49"
                if registration.shape(registered_state(self.graph(id), node).is_some()) =>
            {
                StateKind::Registered { next: None }
            }
            "STU_33A46033" => {
                let site = self.site(id, node);
                if let Some(entry) = registered_state(self.graph(id), node) {
                    if entry.suppresses_model
                        && (self.machine.snapshot.instances[&id].is_server_only()
                            || !self.world.supports_static_model_protection(site))
                    {
                        self.report(
                            site,
                            Issue::UnsupportedField("static model protection context"),
                        );
                        return Ok(());
                    }
                    StateKind::Registered { next: None }
                } else if self.world.admits_generic_player_runtime(site)
                    && node.is_networked()
                    && node.remote >= 0
                {
                    StateKind::Registered { next: None }
                } else {
                    self.report(site, Issue::UnsupportedState(node.class.clone()));
                    return Ok(());
                }
            }
            "STU_9935FBC8"
                if registration.shape(registered_state(self.graph(id), node).is_some())
                    || (self
                        .world
                        .admits_generic_player_runtime(self.site(id, node))
                        && node.is_networked()
                        && node.is_state()) =>
            {
                StateKind::Registered { next: None }
            }
            // The authored zero-valued form inherits the base state reader.
            "STU_D9843704"
                if registration.shape(
                    node.is_networked() && node.remote == -1 && {
                        let value = field(&node.fields, "m_0B528B1E");
                        script_expr::native_shape(value, "STUConfigVarFloat", Some("m_value"))
                            && field(value, "m_value").as_f64() == Some(0.0)
                    },
                ) =>
            {
                StateKind::Plain
            }
            "STU_BD02E168"
                if registration.shape(is_client_subgraph_controller(self.graph(id), node))
                    || self
                        .world
                        .admits_generic_player_runtime(self.site(id, node)) =>
            {
                StateKind::Plain
            }
            // ForCodeSynced publishes to native game-code registration outside
            // the graph. Its VM plugs and empty wire payload use the base state.
            "STU_CD46AF93" | "STU_6A926B21"
                if registration.shape(
                    is_networked_for_code_synced(node)
                        || self.world.for_code_transport(self.site(id, node)) == Some(false),
                ) =>
            {
                StateKind::ForCode
            }
            // Tips register their state key with native presentation code.
            // The graph still runs the base state's plugs and lifecycle.
            "STU_F5A99B2A"
                if registration.shape(
                    node.is_networked()
                        && field(&node.fields, "m_tips")
                            .as_array()
                            .is_some_and(|tips| {
                                !tips.is_empty()
                                    && tips
                                        .iter()
                                        .all(|tip| field(tip, "$").as_str() == Some("STUTip"))
                            }),
                ) =>
            {
                StateKind::Plain
            }
            "STU_7B9E8909" => StateKind::Plain,
            "STU_6A6BBB8D" | "STU_918B3FA9" | "STU_14611C1B" | "STU_200B27C5" | "STU_D52B9026"
            | "STU_4531992F" | "STU_55F72184" | "STU_632B74B9"
                if registration.permits(self.graph(id).index == 0x0c87) =>
            {
                StateKind::Plain
            }
            "STU_724DFBC6" | "STU_531AA630"
                if registration.permits(self.graph(id).index == 0x0cf3) =>
            {
                StateKind::Plain
            }
            _ if encounter_base_state_shape(self.graph(id).index, node)
                .is_some_and(|allowed| registration.shape(allowed)) =>
            {
                StateKind::Plain
            }
            "STU_B5C87F3D" => {
                let site = self.site(id, node);
                if self.world.admits_generic_player_runtime(site) && node.is_state() {
                    StateKind::SpatialQuery
                } else if self.world.local_combat_context_available(site) == Some(false) {
                    StateKind::Plain
                } else {
                    self.report(site, Issue::UnsupportedState(node.class.clone()));
                    return Ok(());
                }
            }
            "STU_76F3EAEF"
                if registration.permits(
                    self.graph(id).index == 0x0cbc
                        && matches!(node.pos, 60 | 61 | 62 | 63 | 64 | 110),
                ) =>
            {
                StateKind::Plain
            }
            "STU_6CC90892"
                if registration.permits((self.graph(id).index, node.pos) == (0x0cbc, 129))
                    && is_self_target(node, "m_D7AC2990") =>
            {
                StateKind::SameTeamContact { registered: false }
            }
            "STUStatescriptStateCombatModFilter"
                if !self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && registration.shape(
                        asset_guid(field(&node.fields, "m_60703B1E"))
                            == Some(0x0d80_0000_0000_0317)
                            && field(&node.fields, "m_41B10FF2").as_u64() == Some(0),
                    ) =>
            {
                StateKind::OutgoingDamageFilter { registered: false }
            }
            "STU_40E05F6B" if self.graph(id).index == 0x0ce0 => {
                match self.world.boss_intro_cue(node) {
                    Some(cue) => StateKind::BossIntroVoice(cue),
                    None => StateKind::VoiceStimulus,
                }
            }
            "STU_40E05F6B"
                if (self.graph(id).index, node.pos) == (0x0c8c, 221)
                    && self.world.is_victory_voice(node) =>
            {
                StateKind::VictoryVoice
            }
            "STU_40E05F6B" if self.graph(id).index == 0x0ca7 => {
                match self.world.gate_voice_cue(node) {
                    Some(cue) => StateKind::GateVoice(cue),
                    None => StateKind::VoiceStimulus,
                }
            }
            "STU_40E05F6B" => StateKind::VoiceStimulus,
            "STU_DC80F7E6"
                if registration.shape(
                    self.world.outline_context_available(self.site(id, node)) == Some(false),
                ) =>
            {
                StateKind::Plain
            }
            "STU_441B50E4"
                if registration.shape(is_self_stat_timer(node))
                    && self.world.player_stats_available(self.site(id, node)) == Some(false) =>
            {
                StateKind::Plain
            }
            "STU_97B1B3EC"
                if registration.permits((self.graph(id).index, node.pos) == (0x0c93, 26)) =>
            {
                StateKind::AiControlSetting
            }
            "STU_473549CA" | "STU_CF8C7CC5" => StateKind::AiControlOverride { record: None },
            "StateFacingClaim" if node.declares_facing_claim() => StateKind::AiFacing(None),
            "STU_FC16FF45"
                if registration.permits(matches!(
                    (self.graph(id).index, node.pos),
                    (0x0c95, 5) | (0x185d, 57)
                )) =>
            {
                StateKind::AiFacing(None)
            }
            "STU_B61E6681" | "STU_CFA338CB" | "STU_600E3C59" => {
                StateKind::AiBehavior { record: None }
            }
            "STU_066B4605" if self.has_no_ai_control_target(id, node) => StateKind::Plain,
            "STU_066B4605" => StateKind::AiWeaponOverride { record: None },
            "STUStatescriptStateHealthPool"
                if registration.shape((self.graph(id).index, node.pos) == (0x0c89, 5)) =>
            {
                StateKind::HealthCapacity
            }
            "STUStatescriptStateHealthPool"
                if is_base_health_pool(node) || named_temporary_shield(node, key.raw).is_some() =>
            {
                StateKind::HealthPool { pool: None }
            }
            "STUStatescriptStateHealthPool"
                if self.graph(id).index == 0x0cbc && is_wave_health_pool(node) =>
            {
                wave_bonus = true;
                StateKind::HealthPool { pool: None }
            }
            "STUStatescriptStateHealthPool"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::HealthPool { pool: None }
            }
            "STU_B7D2C9C0" => StateKind::DisplayName { binding: None },
            "STU_E56BA8D8" if registration.shape(node.is_networked() && node.remote == -1) => {
                StateKind::TargetTag { effect: None }
            }
            "STU_160F2578"
                if registration.shape(
                    static_target_operand(node).is_some()
                        || participant_filter(node).is_some()
                        || excluded_target_tags(node).is_some(),
                ) =>
            {
                StateKind::Plain
            }
            "STU_1F5ED928" if registration.shape(position_duration.is_some()) => StateKind::Plain,
            "STUStatescriptStateBooleanSwitch" => StateKind::BooleanSwitch(false),
            "STU_B591EBFE" => StateKind::DelayedSwitch {
                current: false,
                undecided: true,
            },
            "STU_DEBD057B" => StateKind::Switch { case: None },
            "STU_01178155" => StateKind::ActiveQuery,
            "STUStatescriptStateGamePadVibration" => StateKind::GamePadVibration,
            "STU_37D754C8" | "STUStatescriptStateSubScript" => StateKind::SubScript { child: None },
            "STUStatescriptStateGameMessageEntry"
                if (self.graph(id).index, node.pos) == (0x0cbc, 133) =>
            {
                StateKind::BossPauseReceiver { receiving: false }
            }
            "STUStatescriptStateGameMessageEntry" => StateKind::MessageIdle,
            "STUStatescriptStateWait" if registration.shape(is_elapsed_wait(node)) => {
                StateKind::Wait
            }
            "STUStatescriptStateShockwave" => StateKind::Shockwave { managed: false },
            "STU_861D9015" => StateKind::SprayPlacement(None),
            "STU_4C96470A" => StateKind::PlacementSelector {
                confirmed: false,
                sampled: false,
            },
            "STU_4BBC5D09" => StateKind::StaticTrace {
                origin: [0.0; 3],
                direction: [0.0; 3],
                speed: 0.0,
                length: 0.0,
                distance: 0.0,
                category: 0,
                anchor: None,
                raw_hit: None,
                face: None,
                promoted: false,
                standing: None,
            },
            "STUStatescriptStateMoveTo" => StateKind::MoveTo { movement: None },
            "STU_194D744E"
                if field(&node.fields, "m_89AABA94").as_u64() == Some(0)
                    && field(&node.fields, "m_293715CB").is_null()
                    && self.world.ai_walk_speed(self.site(id, node)).is_some() =>
            {
                StateKind::MoveTo { movement: None }
            }
            "STU_ECAEE401"
                if matches!(
                    self.evaluate(id, node, field(&node.fields, "m_6F4283C8"), None),
                    Some(Value::Integer(3))
                ) && [
                    "m_1B09389D",
                    "m_3D5F729C",
                    "m_80E8303C",
                    "m_881FC2B2",
                    "m_5D988756",
                ]
                .into_iter()
                .all(|plug| node.links(plug).next().is_none())
                    && self.world.ai_walk_speed(self.site(id, node)).is_some() =>
            {
                // This navigation group has no result edges. Its owning graph
                // observes position and releases the movement child on exit.
                StateKind::Plain
            }
            "STU_54FEB21C"
                if registration
                    .shape(is_parent_owned_timed_graph(node) || timed_graph_duration.is_some())
                    || (registration.permits(legacy_timed_graph)
                        && ["m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5"]
                            .iter()
                            .all(|name| field(&node.fields, name).is_null())
                        && node.links("m_E5790891").next().is_none()) =>
            {
                // This class uses the base lifecycle and wire state. The
                // duration shape arms a regular state-owned timer on entry.
                StateKind::Plain
            }
            "STUStatescriptStatePhysicsExplosion"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::PhysicsExplosion
            }
            "STUStatescriptStatePhysicsExplosion"
                if registration.shape(is_static_map_explosion(self.graph(id), node, key.raw)) =>
            {
                let site = self.site(id, node);
                if self.world.has_dynamic_map_rigid_bodies(site) != Some(false) {
                    self.report(site, Issue::UnsupportedState(node.class.clone()));
                    return Ok(());
                }
                // No map body can receive the impulse. The ordinary lifecycle
                // still drives authored transitions and boundary projection.
                StateKind::Plain
            }
            "STU_D74D4F47" => StateKind::WaitFrames { target: 0 },
            "STU_2F07848F"
                if registration.shape(is_cinematic_time_scale_envelope(
                    self.graph(id),
                    node,
                    key.raw,
                )) =>
            {
                StateKind::CinematicTimeScale { boundary_frame: 0 }
            }
            "STU_91EF24A9" => StateKind::AiCandidate { selected: false },
            _ if node.is_ai_weapon_fire() => StateKind::AiWeaponFire(None),
            "STUStatescriptStateLogicalButton" => StateKind::LogicalButton { down: false },
            "STU_E4A30BCF" => StateKind::ButtonBlock(None),
            _ if button_lock.is_some() => StateKind::ButtonBlock(None),
            class
                if matches!(class, "STUStatescriptStateStack" | "STU_A97E6999")
                    || node.declares_stack_claim() =>
            {
                StateKind::Stack {
                    top: false,
                    under: false,
                }
            }
            "STUStatescriptStateAbility" => StateKind::Ability { flags: 0 },
            "STU_316CFEF2" => StateKind::MovementMod,
            _ if movement_scale.is_some() => StateKind::MovementMod,
            "STU_9C9A422A" => StateKind::Defer,
            "STU_75D1C308"
                if (registration
                    .permits(self.graph(id).index == 0x01f0 && matches!(node.pos, 18 | 22))
                    && field(&node.fields, "m_069C6F7E").as_u64() == Some(1)
                    && field(&node.fields, "m_54613F6A").as_u64()
                        == Some(u64::from(node.pos == 22)))
                    || (self
                        .world
                        .admits_generic_player_runtime(self.site(id, node))
                        && node.is_networked()
                        && node.is_state()) =>
            {
                // Retain the base lifecycle and the native linked wire record.
                StateKind::RollMovementParameter
            }
            "STU_3BC72DE1"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_networked()
                    && node.is_state() =>
            {
                StateKind::RollMovementParameter
            }
            "STU_96F1DBCF" if registration.shape(is_owner_movement_sampler(node)) => {
                match self.state(key).map(|state| state.kind) {
                    Some(kind @ StateKind::MovementSample { .. }) => kind,
                    _ => StateKind::MovementSample {
                        flags: 0,
                        pose_frame: None,
                        sampled_at: None,
                    },
                }
            }
            "STUStatescriptStateAnim" => StateKind::Anim,
            "STU_B9898052" => {
                let count = match self.state(key).map(|state| state.kind) {
                    Some(StateKind::ClientPulse { count, .. }) => count,
                    _ => 0,
                };
                self.client_pulse(id, node, count)
            }
            "STUStatescriptStateTrackTargets"
                if registration.shape(node.contact_volume_bindings().is_some()) =>
            {
                StateKind::EmptyTargets
            }
            "STUStatescriptStateTrackTargets"
                if registration.shape(node.view_track().is_some()) =>
            {
                StateKind::ViewTargets {
                    next_sample: self.machine.frame,
                }
            }
            // Other presentation-only trackers retain their empty wire state
            // in the Python reference (`world.tracked_targets` returns
            // NotImplemented / empty targets).
            "STUStatescriptStateTrackTargets"
                if registration.permits((self.graph(id).index, node.pos) == (0x0257, 11)) =>
            {
                StateKind::EmptyTargets
            }
            "STU_691BFA55" | "STU_7CA2FBAF" => StateKind::Effect,
            "STU_23A00A53"
                if registration
                    .permits(self.graph(id).index == 0x0043 && matches!(node.pos, 0 | 9)) =>
            {
                StateKind::Plain
            }
            "STU_23A00A53" if registration.shape(node.contact_effect_resource().is_some()) => {
                StateKind::ExplosionContact
            }
            "STUStatescriptStateModifyHealth"
                if registration.shape(self.contact_damage_tracker(key, node).is_some()) =>
            {
                StateKind::Plain
            }
            "STUStatescriptStateModifyHealth"
                if registration.shape(is_named_shield_decay(node, key.raw)) =>
            {
                StateKind::NamedShieldDecay
            }
            "STUStatescriptStateModifyHealth"
                if registration.shape(is_self_shield_recharge(node, key.raw)) =>
            {
                StateKind::ShieldRecharge
            }
            "STUStatescriptStateModifyHealth"
                if registration.shape(immediate_self_damage(node, key.raw).is_some())
                    || (registration.permits(matches!(
                        (self.graph(id).index, node.pos),
                        (0x0c89, 38 | 48) | (0x0b27, 93)
                    )) && !self
                        .world
                        .admits_generic_player_runtime(self.site(id, node))) =>
            {
                StateKind::ModifyHealth
            }
            "STUStatescriptStateTrackTargets"
                if registration.permits(matches!(
                    (self.graph(id).index, node.pos),
                    (0x0c89, 47 | 52) | (0x022f, 26 | 86)
                )) || (registration.permits(
                    self.graph(id).index == 0x006b && matches!(node.pos, 6 | 7 | 121),
                ) && !self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))) =>
            {
                StateKind::TrackedTargets
            }
            "STU_431F3FCC"
                if registration.permits((self.graph(id).index, node.pos) == (0x022f, 11)) =>
            {
                StateKind::DeadeyeEffects
            }
            "STU_431F3FCC"
                if registration.permits((self.graph(id).index, node.pos) == (0x006b, 62))
                    && !self
                        .world
                        .admits_generic_player_runtime(self.site(id, node)) =>
            {
                StateKind::WitchStaffEffect
            }
            "STUStatescriptStateModifyHealth"
                if registration.permits((self.graph(id).index, node.pos) == (0x0079, 0))
                    && !self
                        .world
                        .admits_generic_player_runtime(self.site(id, node)) =>
            {
                StateKind::WitchStaffHealing
            }
            "STUStatescriptStateTrackRay" if registration.shape(node.tracks_ground_contact()) => {
                StateKind::GroundRay
            }
            "STUStatescriptStateTrackRay"
                if registration
                    .permits(self.graph(id).index == 0x157c && matches!(node.pos, 202 | 203))
                    && node.server_only
                    && !self
                        .world
                        .admits_generic_player_runtime(self.site(id, node)) =>
            {
                StateKind::SummonerBeamRay
            }
            "STUStatescriptStateModifyHealth"
                if registration.permits((self.graph(id).index, node.pos) == (0x157c, 170))
                    && node.server_only
                    && !self
                        .world
                        .admits_generic_player_runtime(self.site(id, node)) =>
            {
                StateKind::SummonerBeamDamage
            }
            "STU_BA4C2296"
                if registration.permits((self.graph(id).index, node.pos) == (0x157c, 143))
                    && self.world.outline_context_available(self.site(id, node)) == Some(false) =>
            {
                StateKind::Plain
            }
            "STU_431F3FCC"
                if registration.permits((self.graph(id).index, node.pos) == (0x157c, 200))
                    && node.server_only =>
            {
                // This effect tracks player criteria. The NPC caster has no player stats.
                StateKind::Plain
            }
            "STU_E3CEF6B9" if registration.shape(is_literal_frame_wait(node)) => {
                StateKind::WaitFrames { target: 0 }
            }
            "STU_38EE1100"
                if node.game_message_id().is_some_and(|topic| {
                    self.graphs.is_projected_message_topic(topic)
                        || self
                            .world
                            .accepts_client_self_message(self.site(id, node), topic)
                }) =>
            {
                let site = self.site(id, node);
                let topic = node.game_message_id().unwrap_or(0);
                let explicit_projected = self.graphs.is_projected_message_topic(topic);
                let valid = script_expr::valid_entity(self.machine.snapshot.entity)
                    && !self.machine.snapshot.instances[&id].is_server_only()
                    && (is_projected_self_message(self.graph(id), node, key.raw)
                        || (!explicit_projected
                            && node.is_networked()
                            && node.is_state()
                            && is_message_recipient_set(field(&node.fields, "m_target"))
                            && node.valid_message_parameters(usize::MAX)));
                if !valid {
                    self.report(site, Issue::UnsupportedMessageRegistration);
                    return Ok(());
                }
                StateKind::ProjectedSelfSend
            }
            "STU_38EE1100" => persistent_message(self.graph(id).index, node)
                .map_or(StateKind::SendMessage, StateKind::PersistentSend),
            // The hook line's presentation attachment state (remote mirror;
            // the effect/hardpoint operands ride the ring): same no-server-
            // presentation behaviour as a plain cosmetic entity state, and
            // it carries no m_1DEF59FA operand, so begin links nothing.
            "STUStatescriptStateCosmeticEntity" | "STU_9932D728" => StateKind::CosmeticEntity,
            "STUStatescriptStateChaseVar" => StateKind::ChaseVar,
            "STU_77E13026"
                if registration.permits((self.graph(id).index, node.pos) == (0x180a, 42)) =>
            {
                StateKind::PhaseScriptList
            }
            "STU_77E13026"
                if registration.shape(
                    node.remote >= 0
                        && ["m_814F8EC3", "m_3DC6FAAA"].into_iter().all(|name| {
                            script_expr::lvalue(field(&node.fields, name))
                                .is_ok_and(|variable| variable.is_some())
                        }),
                ) =>
            {
                StateKind::Plain
            }
            "STU_CB4905E9"
                if registration.permits((self.graph(id).index, node.pos) == (0x180a, 41)) =>
            {
                StateKind::PhaseScript
            }
            "STU_4BD2E886"
                if registration.permits((self.graph(id).index, node.pos) == (0x180a, 46)) =>
            {
                StateKind::PhaseTransition
            }
            "STU_2F4E2E3F" => StateKind::Watch,
            _ if node.declares_observe_state() => StateKind::Observe {
                sampled_frame: self.machine.frame,
            },
            _ if node.target_child_binding().is_some() => StateKind::TrackEntities {
                sampled_frame: self.machine.frame,
            },
            _ if interval_frames.is_some() => {
                let period_frames = interval_frames.expect("checked authored interval");
                StateKind::Interval {
                    period_frames,
                    next_frame: self.machine.frame.checked_add(period_frames.get()),
                }
            }
            "STU_BA2FCDEB" if registration.permits(self.graph(id).index == 0x0cba) => {
                StateKind::SpawnSearch
            }
            "STU_5E805B5A" if registration.permits(self.graph(id).index == 0x0cba) => {
                StateKind::RecentInput
            }
            "STU_0A6BE10E"
                if registration.permits((self.graph(id).index, node.pos) == (0x0043, 63)) =>
            {
                StateKind::Plain
            }
            "STU_0A6BE10E"
                if registration.permits((self.graph(id).index, node.pos) == (0x0cba, 6)) =>
            {
                StateKind::LivingBodyArray
            }
            "STUStatescriptStateWeaponVolley" => StateKind::WeaponVolley {
                volleys: 1,
                offset_ms: 0,
                ammo0: 0,
                ammo_var: None,
            },
            "STUStatescriptStateTrackTargets"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::GenericTrackedTargets
            }
            "STUStatescriptStateTrackRay"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::SummonerBeamRay
            }
            "STUStatescriptStateModifyHealth"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                let continuous = field(&node.fields, "m_7B2B3F5C").as_u64() == Some(1);
                let on_enter = !continuous && field(&node.fields, "m_DD792FF0").as_u64() == Some(1);
                StateKind::GenericModifyHealth {
                    continuous,
                    on_enter,
                }
            }
            "STUStatescriptStatePet"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::Pet { entity: None }
            }
            "STUStatescriptStateCombatModFilter"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::CombatModFilter { registered: false }
            }
            "STUStatescriptStateDeflectProjectiles"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::DeflectProjectiles {
                    reflect: false,
                    half_angle_deg: 90.0,
                }
            }
            "STU_23A00A53"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::GenericContactSweep
            }
            "STU_431F3FCC"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::TargetAura { effect: 0 }
            }
            "STU_CB4905E9"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::TargetSubScript { child: None }
            }
            "STU_A9C4FF83"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::ConstantAccel
            }
            "STU_798B41F4" | "STU_92ED01D0"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::WallClimb
            }
            "STU_8B4B379C"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::Grapple
            }
            "STU_0A6BE10E"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::FilterArray
            }
            "STU_CF950CB5"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::RecallHistory
            }
            "STU_41B3FC2B"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::RecallRewind
            }
            "STU_C537444C"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::BallRoll { fireball: false }
            }
            "STU_550D0C5C" | "STU_C18969E1"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::HitTracker { hit_count: 0 }
            }
            "STUStatescriptStateRayCast"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::RayCast
            }
            "STU_D07EBF01" | "STU_72DDE005" | "STU_85124386" | "STU_61FE0F01" | "STU_5AFB02C7"
            | "STU_45D3C742"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::EntityAttach { target: 0 }
            }
            "STU_3716E1D4" | "STU_D1BF5FA6" | "STU_75DE77A6" | "STU_96DB5529" | "STU_51BBFB1B"
            | "STU_9163D489" | "STU_BEAB146F" | "STU_4D076571" | "STU_E3C424E1"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::ModifierControl {
                    blocks_input: false,
                }
            }
            "STU_C1BA7545"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 1 }
            }
            "STU_582E0D5F"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 2 }
            }
            "STU_FBFF5DE7"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 3 }
            }
            "STU_4773DA56"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 4 }
            }
            "STU_C168636D"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 5 }
            }
            "STU_54A1A6B6"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 6 }
            }
            "STU_EF4C12FB"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 7 }
            }
            "STU_6154B909" | "STU_D09BD154" | "STU_1E356736"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::KinematicsControl { mode: 8 }
            }
            "STU_43DB06B1" | "STU_41484EE1" | "STU_6C7C71D8" | "STU_C3CA88AA"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::PlacementCast { valid: false }
            }
            "STU_746F8777" | "STUStatescriptStateBlinkEffect"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::TeleportEffect
            }
            "STU_FAC489FF" | "STU_57BDCAB6"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::ProximityTrigger { triggered: false }
            }
            "STU_C5D842D8"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::EntityStatus { mode: 1 }
            }
            "STU_B63ECEB8"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::EntityStatus { mode: 2 }
            }
            "STU_75C1D62C"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::EntityStatus { mode: 3 }
            }
            "STU_1E0773EB" | "STU_CB6C0ED7" | "STU_9700865B"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::UxPresenter
            }
            "STU_7D71FDF3"
            | "STU_757BBC46"
            | "STU_1F5ED928"
            | "STU_94DAAA8C"
            | "STU_FA98335E"
            | "STU_D75CDECC"
            | "STU_64CFBC4E"
            | "STU_4DCDA966"
            | "STU_875C9635"
            | "STU_A103C85E"
            | "STU_A6F5A33A"
            | "STU_15F1260F"
            | "STU_2D5C4AF5"
            | "STU_D1D0DED6"
            | "STUStatescriptStateAnimatedCamera"
            | "STU_BA4C2296"
            | "STU_CA861E44"
            | "STU_0DB9E66B"
            | "STU_353AF1A9"
            | "STU_54FEB21C"
                if self
                    .world
                    .admits_generic_player_runtime(self.site(id, node))
                    && node.is_state() =>
            {
                StateKind::EntityStatus { mode: 0 }
            }
            _ if self
                .world
                .admits_generic_player_runtime(self.site(id, node))
                && node.is_state()
                && super::retail_catalog::classify_retail_state(&node.class).is_some() =>
            {
                use super::retail_catalog::RetailStateBehavior;
                match super::retail_catalog::classify_retail_state(&node.class)
                    .expect("classified retail state")
                {
                    RetailStateBehavior::SubScript => StateKind::TargetSubScript { child: None },
                    RetailStateBehavior::LogicalButton => StateKind::LogicalButton { down: false },
                    RetailStateBehavior::ModifierLink => StateKind::ModifierControl {
                        blocks_input: false,
                    },
                    RetailStateBehavior::SyncVars => StateKind::SyncVars,
                    RetailStateBehavior::HeroOverride => StateKind::HeroOverride,
                    RetailStateBehavior::AiTactic => StateKind::AiTactic,
                    RetailStateBehavior::UxPresenter => StateKind::UxPresenter,
                    RetailStateBehavior::EffectState => StateKind::EntityStatus { mode: 0 },
                    RetailStateBehavior::SpatialModeState => StateKind::SpatialQuery,
                    RetailStateBehavior::FieldDrivenState => StateKind::FieldDrivenState,
                }
            }
            _ if registration.allows_runtime_fallback() && node.declares_runtime_plain_state() => {
                StateKind::Plain
            }
            _ if !self.machine.strict => {
                // runtime.py `state_type`: outside a strict component every
                // unmodeled class gets the plain base lifecycle. The state
                // stays wire-plain because the codec table carries no
                // record for such a class.
                StateKind::Plain
            }
            _ => {
                self.report_diagnostic(
                    self.site(id, node),
                    Issue::UnsupportedState(node.class.clone()),
                    registration.rejected(),
                );
                return Ok(());
            }
        };
        let previous = self.state(key);
        let activation = previous.map_or(1, |state| state.activation.wrapping_add(1));
        let buffered_press = previous.and_then(|state| state.buffered_press);
        let untrusted_native = previous.is_some_and(|state| state.untrusted_native);
        for raw in raw_indices(array_field(&node.fields, "m_exclusiveStateIndices")) {
            self.end(StateKey { instance: id, raw }, false, depth + 1)?;
            self.drop_begin(id, raw);
        }
        self.machine.snapshot_dirty = true;
        self.machine.snapshot.instances[&id].states.insert(
            key.raw,
            State {
                active: true,
                activation,
                start_ms: self.machine.now_ms,
                buffered_press,
                untrusted_native,
                ..State::dormant(kind)
            },
        );
        if let Some(duration) = timed_graph_duration.or(position_duration) {
            self.arm_parent_owned_duration(key, node, duration)?;
        }
        match kind {
            StateKind::Registered { .. } => self.register_state(key, node)?,
            StateKind::GamePadVibration => self.post(id, Event::Finish(key.raw))?,
            StateKind::ForCode => self.begin_for_code(key, node)?,
            StateKind::ExplosionContact => self.refresh_contact(key, node)?,
            StateKind::ModifyHealth => self.begin_npc_health_change(key, node)?,
            StateKind::GenericModifyHealth {
                continuous,
                on_enter,
            } => {
                if continuous {
                    self.register_tick(key, self.machine.now_ms);
                } else if on_enter {
                    let applied = self.apply_generic_modify_health_on_enter(key, node)?;
                    if applied
                        && (field(&node.fields, "m_8B707686").as_u64() == Some(1)
                            || node.links("m_onFinishedPlug").next().is_some())
                    {
                        self.post(id, Event::Finish(key.raw))?;
                    } else {
                        self.register_tick(key, self.machine.now_ms);
                    }
                } else {
                    self.apply_generic_modify_health(key, node, None)?;
                    // Discrete one-shot ModifyHealth states (e.g. Piledriver `160A:15` victim loop)
                    // finish once their hit is committed so `m_onFinishedPlug` advances the loop.
                    if field(&node.fields, "m_8B707686").as_u64() == Some(1)
                        || node.links("m_onFinishedPlug").next().is_some()
                    {
                        self.post(id, Event::Finish(key.raw))?;
                    }
                }
            }
            StateKind::Pet { .. } => self.begin_pet(key, node)?,
            StateKind::CombatModFilter { .. } => self.begin_combat_mod(key, node)?,
            StateKind::DeflectProjectiles { .. } => self.begin_deflect(key, node)?,
            StateKind::GenericContactSweep => {
                self.refresh_generic_contact_sweep(key, node)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::TargetAura { .. } => {
                self.refresh_target_aura(key, node, 0)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::TargetSubScript { .. } => self.begin_target_subscript(key, node)?,
            StateKind::ConstantAccel => {
                self.tick_constant_accel(key, node, 16)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::WallClimb => {
                self.tick_wall_climb(key, node, 16)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::Grapple => self.begin_grapple(key, node)?,
            StateKind::FilterArray => {
                self.refresh_filter_array(key, node)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::RecallHistory => {
                self.tick_recall_history(key, node)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::RecallRewind => self.begin_recall_rewind(key, node)?,
            // The authored movement profile owns roll acceleration and friction.
            StateKind::BallRoll { .. } => {}
            StateKind::SpatialQuery => self.begin_spatial_query(key, node)?,
            StateKind::PhysicsExplosion => self.begin_physics_explosion(key, node)?,
            StateKind::HitTracker { .. } => {
                self.tick_hit_tracker(key, node)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::RayCast => {
                self.refresh_ray_cast(key, node)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::EntityAttach { .. } => self.begin_entity_attach(key, node)?,
            StateKind::ModifierControl { .. } => self.begin_modifier_control(key, node)?,
            StateKind::KinematicsControl { mode } => {
                self.begin_kinematics_control(key, node, mode)?
            }
            StateKind::PlacementCast { .. } => {
                self.refresh_placement_cast(key, node)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::TeleportEffect => self.begin_teleport_effect(key, node)?,
            StateKind::ProximityTrigger { .. } => {
                self.tick_proximity_trigger(key, node)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::EntityStatus { mode } => self.begin_entity_status(key, node, mode)?,
            StateKind::UxPresenter => self.begin_ux_presenter(key, node)?,
            StateKind::SyncVars => self.begin_sync_vars(key, node)?,
            StateKind::HeroOverride => self.begin_hero_override(key, node)?,
            StateKind::AiTactic => self.begin_ai_tactic(key, node)?,
            StateKind::FieldDrivenState => self.begin_field_driven_state(key, node)?,
            StateKind::Shockwave { .. } => self.begin_shockwave(key, node)?,
            StateKind::PlacementSelector { .. } => self.begin_placement_selector(key, node)?,
            StateKind::SprayPlacement(_) => self.place_spray(key, node, depth)?,
            StateKind::MoveTo { .. } => self.begin_move_to(key, node)?,
            StateKind::StaticTrace { .. } => self.begin_static_trace(key, node)?,
            StateKind::ShieldRecharge => self.begin_shield_recharge(key, node)?,
            StateKind::NamedShieldDecay => {
                if self.update_named_shield_decay(key, node, 0)? {
                    self.register_tick(key, self.machine.now_ms);
                }
            }
            StateKind::TrackedTargets => self.refresh_tracked_targets(key, node)?,
            StateKind::GenericTrackedTargets => {
                self.refresh_generic_tracked_targets(key, node)?;
                if !array_field(&node.fields, "m_transitionPlug").is_empty() {
                    self.try_complete(key, node, depth)?;
                }
            }
            StateKind::ViewTargets { .. } => {
                self.register_tick(key, self.machine.now_ms);
                if !self.sample_view_targets(key, node)? {
                    return Ok(());
                }
            }
            StateKind::DeadeyeEffects => self.refresh_deadeye_effects(key, node)?,
            StateKind::GroundRay => self.refresh_ground_ray(key, node)?,
            StateKind::WitchStaffEffect | StateKind::WitchStaffHealing => {
                self.register_tick(key, self.machine.now_ms)
            }
            StateKind::SummonerBeamRay => self.refresh_summoner_beam_ray(key, node)?,
            StateKind::SummonerBeamDamage => self.register_tick(key, self.machine.now_ms),
            StateKind::Watch if node.class == "STU_2F4E2E3F" => {
                let site = self.site(id, node);
                if self.world.admits_generic_player_runtime(site) && site.graph == 0x0DD8 {
                    let fwd = self
                        .world
                        .forward(site.entity)
                        .or_else(|| self.world.authored_forward(site.entity))
                        .unwrap_or([0.0, 0.0, 1.0]);
                    let hlen = fwd[0].hypot(fwd[2]).max(1e-4);
                    let _ = self.world.apply_velocity_ops(
                        site,
                        self.machine.frame,
                        &[crate::script_init::VelocityOp::SetVector([
                            fwd[0] / hlen * 3.5,
                            11.5,
                            fwd[2] / hlen * 3.5,
                        ])],
                    );
                    self.follow(id, node, "m_A70DB860", Some(key), depth)?;
                }
            }
            StateKind::Plain if node.class == "STU_066B4605" => {
                // The absent AI relation finishes through the current clock's event queue.
                self.post(id, Event::Finish(key.raw))?;
            }
            StateKind::Plain if node.class == "STUStatescriptStateModifyHealth" => {
                if self.contact_damage_tracker(key, node).is_some() {
                    self.begin_contact_damage(key, node)?;
                } else if field(&node.fields, "m_7B2B3F5C").as_u64() == Some(0) {
                    if let Some(Value::Entity(target)) =
                        self.evaluate(id, node, field(&node.fields, "m_FA3ECC43"), None)
                    {
                        if script_expr::valid_entity(target) {
                            let effect = field(&node.fields, "m_modifyHealth");
                            if let Some(amount) = self
                                .evaluate(id, node, field(effect, "m_amount"), None)
                                .and_then(pool_number)
                                .filter(|amount| amount.is_finite() && *amount > 0.0)
                            {
                                let damage = field(effect, "m_93CDFD2F").as_u64() == Some(1);
                                let delta = if damage { -amount } else { amount };
                                let _ = self.world.queue_health_change(
                                    self.site(id, node),
                                    target,
                                    delta,
                                );
                            }
                        }
                    }
                }
            }
            StateKind::Plain if node.class == "STUStatescriptStateRayCast" => {
                self.run_authored_raycast(id, node, Some(key))?;
            }
            StateKind::Plain if node.class == "STU_160F2578" => {
                if !self.begin_static_target(key, node)? {
                    return Ok(());
                }
            }
            StateKind::HealthPool { .. } => self.begin_health_pool(key, node, wave_bonus)?,
            StateKind::HealthCapacity => self.check_health_capacity(key, node)?,
            StateKind::SameTeamContact { .. } => {
                let site = self.site(id, node);
                if self.world.set_same_team_contact_suppressed(site, true) {
                    self.state_mut(key).expect("active team contact state").kind =
                        StateKind::SameTeamContact { registered: true };
                } else {
                    self.report(site, Issue::UnsupportedField("same-team contact mover"));
                }
            }
            StateKind::OutgoingDamageFilter { .. } => {
                let Some(value) = self.evaluate(id, node, field(&node.fields, "m_50530EAB"), None)
                else {
                    return Ok(());
                };
                let adjustment = match value {
                    Value::Float(number) => number,
                    Value::Integer(number) => number as f64,
                    _ => f64::NAN,
                };
                let site = self.site(id, node);
                if adjustment.is_finite()
                    && self
                        .world
                        .set_outgoing_damage_filter(site, Some(adjustment))
                {
                    self.state_mut(key).expect("active damage filter").kind =
                        StateKind::OutgoingDamageFilter { registered: true };
                } else {
                    self.report(site, Issue::UnsupportedField("outgoing damage filter"));
                }
            }
            StateKind::AiControlSetting => self.update_ai_control_setting(key, node),
            StateKind::AiControlOverride { .. } => self.begin_ai_control_override(key, node)?,
            StateKind::AiFacing(_) => self.update_ai_facing(key, node, None)?,
            StateKind::AiBehavior { .. } => self.update_ai_behavior(key, node)?,
            StateKind::AiWeaponOverride { .. } => self.begin_ai_weapon_override(key, node)?,
            StateKind::TargetTag { .. } => self.begin_target_tag(key, node)?,
            StateKind::DisplayName { .. } => self.begin_display_name(key, node)?,
            StateKind::BooleanSwitch(_) => {
                let Some(value) =
                    self.evaluate(id, node, field(&node.fields, "m_condition"), Some(key))
                else {
                    return Ok(());
                };
                let current = truthy(&value);
                self.state_mut(key).expect("active switch").kind =
                    StateKind::BooleanSwitch(current);
                self.enter_branch(key, node, current, depth)?;
            }
            StateKind::DelayedSwitch { .. } => self.begin_delayed_switch(key, node, depth)?,
            StateKind::Switch { .. } => {
                let Some(case) = self.switch_case(key, node) else {
                    return Ok(());
                };
                self.state_mut(key).expect("active switch").kind = StateKind::Switch { case };
                self.follow(id, node, &case_path(case), Some(key), depth)?;
            }
            StateKind::SubScript { .. } => {
                let child = self.spawn_child(key, node)?;
                self.state_mut(key).expect("active subscript").kind =
                    StateKind::SubScript { child };
            }
            StateKind::MessageIdle | StateKind::BossPauseReceiver { .. } => {
                self.follow(id, node, "m_33DA5163", Some(key), depth)?;
            }
            StateKind::Wait => self.arm_wait(key, node)?,
            StateKind::VoiceStimulus => {
                if !self.begin_voice(key, node)? {
                    return Ok(());
                }
            }
            StateKind::BossIntroVoice(cue) => {
                self.post_after(id, u64::from(cue.delay_ms), Event::Timer(key.raw, 1))?;
            }
            StateKind::GateVoice(_) | StateKind::VictoryVoice => {
                self.post_after(id, 0, Event::Timer(key.raw, 1))?
            }
            StateKind::WaitFrames { .. } => self.arm_frame_wait(key, node)?,
            StateKind::CinematicTimeScale { .. } => self.stamp_cinematic_time_scale(key, node),
            StateKind::AiCandidate { .. } => {
                let site = self.site(id, node);
                if let Some(selected) = self.world.selects_ai_behavior(site) {
                    self.state_mut(key).expect("active AI candidate").kind =
                        StateKind::AiCandidate { selected };
                    if selected {
                        self.follow(id, node, "m_6F92F4BD", Some(key), depth)?;
                    }
                } else {
                    self.report(site, Issue::UnsupportedField("AI behavior selection"));
                }
            }
            StateKind::AiWeaponFire(_) if !self.machine.owner_dead => {
                let target = self.evaluate(id, node, field(&node.fields, "m_14EDDDA7"), None);
                let enabled = self.evaluate(id, node, field(&node.fields, "m_20BCEE62"), None);
                let slot = self.evaluate(id, node, field(&node.fields, "m_A60150E2"), None);
                if let (
                    Some(Value::Entity(target)),
                    Some(Value::Bool(true)),
                    Some(Value::Integer(slot)),
                ) = (target, enabled, slot)
                {
                    if target == self.machine.snapshot.entity && slot >= 0 {
                        self.state_mut(key).expect("active AI weapon fire").kind =
                            StateKind::AiWeaponFire(Some(slot));
                    }
                }
            }
            StateKind::LogicalButton { .. } => self.enter_button(key, node, depth)?,
            StateKind::ButtonBlock(_) => {
                let button = button_lock
                    .map(|(button, _)| button)
                    .or_else(|| self.logical_button_id(id, node, None));
                if let Some(button) = button {
                    if let Some((_, duration)) = button_lock {
                        // Resynchronizing logical buttons can synchronously end this lock.
                        if let Some(frames) = duration {
                            self.post_after(
                                id,
                                u64::from(frames.get()) * FRAME_MS,
                                Event::Finish(key.raw),
                            )?;
                        }
                        self.machine.ability_presses.retain(|_, presses| {
                            presses.retain(|press| press.button != button);
                            !presses.is_empty()
                        });
                    }
                    self.state_mut(key).expect("active button block").kind =
                        StateKind::ButtonBlock(Some(button));
                    self.machine.button_blocks[usize::from(button)] += 1;
                    self.resync_logical_buttons(Some(button), depth + 1)?;
                }
            }
            StateKind::Stack { .. } => self.begin_stack(key, node, depth)?,
            StateKind::Ability { .. } => {
                self.restart_cooldown(key, node, depth)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::Defer => {
                if let Some(priority) = self
                    .evaluate(id, node, field(&node.fields, "m_priority"), None)
                    .map(|value| script_expr::to_float(&value))
                    .filter(|value| value.is_finite())
                {
                    let pending = self.machine.deferred.entry(id).or_default();
                    pending.push((priority, key.raw));
                    // Stable ties retain registration order; delivery reverses it.
                    pending.sort_by(|a, b| {
                        a.0.partial_cmp(&b.0).expect("validated finite priorities")
                    });
                } else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("deferred priority"),
                    );
                }
            }
            StateKind::MovementMod => {
                if let Some((_, Some(frames))) = movement_scale {
                    self.post_after(
                        id,
                        u64::from(frames.get()) * FRAME_MS,
                        Event::Finish(key.raw),
                    )?;
                }
                self.refresh_movement_modifier(key, node)?;
                let has_forced_velocity = array_field(&node.fields, "m_BC5E91CF")
                    .iter()
                    .any(|config| config.get("$").and_then(Json::as_str) == Some("STU_09F9B1FE"));
                if movement_scale.is_none()
                    && (has_forced_velocity
                        || self
                            .evaluate(id, node, field(&node.fields, "m_485109CB"), Some(key))
                            .is_some_and(|value| script_expr::truthy(&value)))
                {
                    self.register_tick(key, self.machine.now_ms);
                }
            }
            StateKind::MovementSample { .. } => {
                self.update_movement_sample(key, node, depth)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::PhaseScriptList => self.refresh_phase_scripts(key, node)?,
            StateKind::PhaseScript => self.begin_phase_script(key, node)?,
            StateKind::PhaseTransition => self.arm_phase_transition(key, node)?,
            StateKind::Anim => self.arm_animation(key, node)?,
            StateKind::SendMessage
                if node.field("m_targets").is_none()
                    && node.game_message_id().is_some_and(|message| message != 0)
                    && is_message_recipient_set(field(&node.fields, "m_target")) =>
            {
                let site = self.site(id, node);
                if self.world.admits_generic_player_runtime(site) {
                    let targets_expr = field(field(&node.fields, "m_target"), "m_targets");
                    if let Some(target_val) = self.evaluate(id, node, targets_expr, Some(key)) {
                        let recipients = match target_val {
                            Value::Array(items) => items,
                            Value::Null => Vec::new(),
                            other => vec![other],
                        };
                        if let Some(message) = node.game_message_id() {
                            if let Some(parameters) = self.game_message_parameters(id, node)? {
                                for item in recipients {
                                    if let Value::Entity(entity) = item {
                                        if script_expr::valid_entity(entity) {
                                            if entity == self.machine.snapshot.entity {
                                                self.machine.queue_entries_from(
                                                    self.graphs,
                                                    None,
                                                    Some(site.entity),
                                                    message,
                                                    parameters.clone(),
                                                    self.machine.now_ms,
                                                )?;
                                            } else {
                                                let _ = self.world.queue_room_message(
                                                    site,
                                                    entity,
                                                    RoomMessage {
                                                        id: message,
                                                        parameters: parameters.clone(),
                                                    },
                                                );
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                } else {
                    // A state-managed recipient set is a registration, not an
                    // expression for the ordinary one-shot message path.
                    self.report(site, Issue::UnsupportedMessageRegistration);
                }
            }
            StateKind::SendMessage => {
                self.send_game_message(id, node)?;
            }
            StateKind::PersistentSend(message)
                if self.graph(id).index == 0x0c8c && matches!(node.pos, 17 | 18 | 83 | 126) =>
            {
                if let Some(parameters) = self.game_message_parameters(id, node)? {
                    self.machine.queue_entries_from(
                        self.graphs,
                        None,
                        Some(self.machine.snapshot.entity),
                        message,
                        parameters,
                        self.machine.now_ms,
                    )?;
                }
            }
            StateKind::CosmeticEntity => self.begin_cosmetic(key, node, depth)?,
            StateKind::ChaseVar => self.begin_chase(key, node, depth)?,
            StateKind::Observe { .. } | StateKind::TrackEntities { .. } => {
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::Interval { next_frame, .. } => self.schedule_interval(key, next_frame)?,
            StateKind::Watch => {
                let seen = self.evaluate(id, node, field(&node.fields, "m_B7A3CF78"), Some(key));
                self.state_mut(key).expect("active Watch").watch_seen = seen;
            }
            StateKind::SpawnSearch => self.begin_spawn_search(key, node)?,
            StateKind::RecentInput => (),
            StateKind::LivingBodyArray => {
                if !living_body_array_shape(node) {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("encounter body array filter"),
                    );
                    return Ok(());
                }
                self.refresh_living_body_array(key)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::WeaponVolley { .. } => self.begin_volley(key, node, depth)?,
            StateKind::Plain
            | StateKind::ProjectedSelfSend
            | StateKind::PersistentSend(_)
            | StateKind::Effect
            | StateKind::ClientPulse { .. }
            | StateKind::EmptyTargets
            | StateKind::ActiveQuery
            | StateKind::RollMovementParameter
            | StateKind::AiWeaponFire(_) => (),
        }
        if self
            .world
            .admits_generic_player_runtime(self.site(id, node))
        {
            if node.class == "STU_54FEB21C" && timed_graph_duration.is_none() {
                self.post(id, Event::Finish(key.raw))?;
            } else if node.class == "STU_CA861E44" {
                let site = self.site(id, node);
                if let Some(Value::Entity(target)) =
                    self.evaluate(id, node, field(&node.fields, "m_C98661CF"), Some(key))
                {
                    if script_expr::valid_entity(target) {
                        let _ = self.world.request_possess_entity(site, target);
                    }
                }
            }
        }
        self.follow(id, node, "m_onBeginPlug", Some(key), depth)?;
        self.follow(id, node, "m_subgraphPlug", Some(key), depth)?;
        self.try_complete(key, node, depth)
    }

    pub(in crate::script_init) fn refresh_living_body_array(
        &mut self,
        key: StateKey,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("active encounter array filter");
        let Ok(Some(output)) = script_expr::dynamic(field(&node.fields, "m_F2805C84")) else {
            unreachable!("validated encounter array output");
        };
        let Some(source) = self.evaluate(id, node, field(&node.fields, "m_814F8EC3"), Some(key))
        else {
            return Ok(());
        };
        let mut pending = match source {
            Value::Array(values) => values,
            Value::Null => Vec::new(),
            value => vec![value],
        };
        pending.reverse();
        let mut living = Vec::new();
        while let Some(value) = pending.pop() {
            self.spend("encounter body array candidate")?;
            match value {
                Value::Array(values) => pending.extend(values.into_iter().rev()),
                Value::Entity(entity) => match self.world.encounter_body_alive(entity) {
                    Some(true) => living.push(value),
                    Some(false) => (),
                    None => {
                        self.report(
                            self.site(id, node),
                            Issue::UnsupportedField("encounter body lifetime"),
                        );
                        return Ok(());
                    }
                },
                _ => living.push(value),
            }
        }
        let target = source_key(id, output);
        let result = Value::Array(living);
        if self.machine.value(target, None) != result {
            self.write(target, result)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn array_scalar(
        &mut self,
        id: u16,
        node: &Node,
        mut config: &Json,
        caller: Option<StateKey>,
        initial: Value,
    ) -> Option<Value> {
        for _ in 0..MAX_PLUG_DEPTH {
            if config["$"].as_str() != Some("STU_06610EBC") {
                return self.evaluate_at_with_initial(self.site(id, node), config, caller, initial);
            }
            config = match array_field(config, "m_configVars").first() {
                Some(config) => config,
                None => return Some(initial),
            };
        }
        self.report(
            self.site(id, node),
            Issue::Expression(EvalError::NestingExceeded),
        );
        None
    }

    pub(in crate::script_init) fn insert_array(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        let output = field(&node.fields, "m_26E3B366");
        let source = field(&node.fields, "m_value");
        if output.is_null() || source.is_null() {
            self.follow(id, node, "m_outPlug", caller, depth)?;
            return Ok(true);
        }
        let target = match script_expr::dynamic(output) {
            Ok(Some(target)) => target,
            Ok(None)
                if matches!(
                    output["$"].as_str(),
                    Some(
                        "STUConfigVarBool"
                            | "STUConfigVarInt"
                            | "STUConfigVarFloat"
                            | "STUConfigVarLogicalButton"
                            | "STU_BD6CC023"
                            | "STU_297E1A30"
                    )
                ) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                return Ok(true);
            }
            _ => {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("array insertion output target"),
                );
                return Ok(false);
            }
        };
        let index_cfg = field(&node.fields, "m_index");
        let index = if index_cfg.is_null() {
            -1
        } else {
            let Some(value) = self.array_scalar(id, node, index_cfg, caller, Value::Integer(-1))
            else {
                return Ok(false);
            };
            array_insertion_index(&value)
        };
        if index > 200 {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion native limit diagnostic"),
            );
            return Ok(false);
        }
        let key_cfg = field(&node.fields, "m_key");
        if !key_cfg.is_null() {
            let Some(key) = self.array_scalar(id, node, key_cfg, caller, Value::Integer(0)) else {
                return Ok(false);
            };
            if target.id == 0
                || matches!(
                    key,
                    Value::Null
                        | Value::Bool(_)
                        | Value::Integer(_)
                        | Value::Float(_)
                        | Value::Array(_)
                        | Value::Map(_)
                        | Value::IdentifierMap(_)
                )
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                return Ok(true);
            }
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion keyed row storage"),
            );
            return Ok(false);
        }
        if target.id == 0 {
            self.follow(id, node, "m_outPlug", caller, depth)?;
            return Ok(true);
        }
        let target = source_key(id, target);
        if self
            .machine
            .links
            .get(&target)
            .is_some_and(|links| !links.is_empty())
        {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion linked variable checkpoint"),
            );
            return Ok(false);
        }
        let before = self.machine.value(target, None);
        let mut items = match stored(&self.machine.snapshot, target) {
            Value::Array(items) => items,
            _ => vec![],
        };
        let original_count = items.len();
        let index = usize::try_from(index).ok();
        if let Some(index) = index {
            while items.len() < index {
                self.spend("array insertion zero gap")?;
                items.push(Value::Integer(0));
            }
        }
        let mut insertion = ArrayInsertion {
            target,
            items,
            index: index.filter(|index| *index < original_count),
            unique: configured(node, "m_8AD67B20"),
        };
        // Providers see each mutation, while watchers see one final release.
        for _ in &insertion.items {
            self.spend("array insertion publication")?;
        }
        self.set_base(target, Value::Array(insertion.items.clone()));
        let diagnostics = self.machine.snapshot.diagnostics.len();
        if node.class == "STU_CBE8BDAA" {
            if let Some(value) = self.evaluate_at_with_initial(
                self.site(id, node),
                source,
                caller,
                Value::Integer(0),
            ) {
                self.insert_array_value(&mut insertion, value, false, 0)?;
            }
        } else {
            self.insert_array_source(id, node, caller, source, &mut insertion, 0)?;
        }
        if self.machine.snapshot.diagnostics.len() != diagnostics {
            return Ok(false);
        }
        self.notify_value(target, &before, None)?;
        if insertion.items.len() > 200 {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion native limit diagnostic"),
            );
            return Ok(false);
        }
        self.follow(id, node, "m_outPlug", caller, depth)?;
        Ok(true)
    }

    pub(in crate::script_init) fn insert_array_source(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        config: &Json,
        insertion: &mut ArrayInsertion,
        depth: usize,
    ) -> Result<bool, InitFault> {
        self.spend("array insertion source config")?;
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        if config.is_null() {
            return Ok(false);
        }
        if config["$"].as_str() == Some("STU_06610EBC") {
            for config in array_field(config, "m_configVars") {
                if self.insert_array_source(id, node, caller, config, insertion, depth + 1)? {
                    return Ok(true);
                }
            }
            return Ok(false);
        }
        if let Ok(Some(variable)) = script_expr::dynamic(config) {
            if source_key(id, variable) == insertion.target && !insertion.items.is_empty() {
                return Ok(true); // Native iterator has reached its own destination start.
            }
        }
        let Some(value) = self.evaluate(id, node, config, caller) else {
            return Ok(true);
        };
        if value != Value::Null {
            self.insert_array_value(insertion, value, true, depth)?;
        }
        Ok(false)
    }

    pub(in crate::script_init) fn insert_array_value(
        &mut self,
        insertion: &mut ArrayInsertion,
        value: Value,
        flatten: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        self.spend("array insertion source row")?;
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        if flatten {
            if let Value::Array(items) = value {
                for value in items {
                    self.insert_array_value(insertion, value, true, depth + 1)?;
                }
                return Ok(());
            }
        }
        if insertion.unique {
            for item in &insertion.items {
                self.spend("array insertion equality")?;
                if equal(item, &value) {
                    return Ok(());
                }
            }
        }
        if let Some(index) = &mut insertion.index {
            for _ in *index..insertion.items.len() {
                self.spend("array insertion ordered shift")?;
            }
            insertion.items.insert(*index, value);
            *index += 1;
        } else {
            insertion.items.push(value);
        }
        for _ in &insertion.items {
            self.spend("array insertion publication")?;
        }
        self.set_base(insertion.target, Value::Array(insertion.items.clone()));
        Ok(())
    }

    pub(in crate::script_init) fn select_recent_input(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "m_C51AF683")) else {
            self.report(site, Issue::UnsupportedField("spawn history output"));
            return Ok(());
        };
        let Some(lifetime) =
            self.evaluate(key.instance, node, field(&node.fields, "m_82317B5F"), None)
        else {
            return Ok(());
        };
        if !matches!(lifetime, Value::Integer(_) | Value::Float(_))
            || !script_expr::to_float(&lifetime).is_finite()
        {
            self.report(site, Issue::UnsupportedField("spawn history lifetime"));
            return Ok(());
        }
        let lifetime = script_expr::to_float(&lifetime);
        let Some(values) =
            self.evaluate(key.instance, node, field(&node.fields, "m_814F8EC3"), None)
        else {
            return Ok(());
        };
        let positions = match values {
            Value::Array(values) => values,
            Value::Null => Vec::new(),
            value => vec![value],
        };
        if positions.iter().any(|value| !matches!(value, Value::Vector(point) if point.iter().all(|axis| axis.is_finite()))) {
            self.report(site, Issue::UnsupportedField("spawn history positions"));
            return Ok(());
        }
        let lifetime_ms = (lifetime * 1000.0).round() as i64;
        let now = self.machine.now_ms;
        let history = &mut self
            .state_mut(key)
            .expect("active recent input state")
            .recent_inputs;
        for index in (0..history.len()).rev() {
            if i128::from(now) - i128::from(history[index].1) > i128::from(lifetime_ms) {
                history.swap_remove(index);
            }
        }
        let selected = if positions.is_empty() {
            Value::Null
        } else {
            if history.len() >= positions.len() {
                let oldest = history
                    .iter()
                    .enumerate()
                    .min_by_key(|(_, item)| item.1)
                    .map(|(index, _)| index)
                    .expect("populated history");
                history.swap_remove(oldest);
            }
            let bound = positions.len() as u64;
            let threshold = bound.wrapping_neg() % bound;
            let first = loop {
                self.spend("spawn position random selection")?;
                let draw = self.machine.random_u64();
                if draw >= threshold {
                    break (draw % bound) as usize;
                }
            };
            let history = &mut self
                .state_mut(key)
                .expect("active recent input state")
                .recent_inputs;
            let mut slot = first;
            while history.iter().any(|&(used, _)| used == slot) {
                slot = (slot + 1) % positions.len();
                if slot == first {
                    break;
                }
            }
            if lifetime_ms > 0 {
                if let Some(item) = history.iter_mut().find(|item| item.0 == slot) {
                    item.1 = now;
                } else {
                    history.push((slot, now));
                }
            }
            positions[slot].clone()
        };
        self.write(source_key(key.instance, output), selected)?;
        self.follow(key.instance, node, "m_1405CDCB", Some(key), depth)?;
        Ok(())
    }

    pub(in crate::script_init) fn creation_scalar(
        &mut self,
        id: u16,
        node: &Node,
        mut config: &Json,
        initial: Value,
    ) -> Result<Option<Value>, InitFault> {
        for _ in 0..MAX_PLUG_DEPTH {
            self.spend("entity creation scalar")?;
            if config.is_null() {
                return Ok(Some(initial));
            }
            if script_expr::native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                config = config
                    .get("m_configVars")
                    .and_then(Json::as_array)
                    .and_then(|values| values.first())
                    .unwrap_or(&Json::Null);
            } else {
                return Ok(self.evaluate_at_with_initial(
                    self.site(id, node),
                    config,
                    None,
                    initial,
                ));
            }
        }
        self.report(
            self.site(id, node),
            Issue::UnsupportedField("entity creation config depth"),
        );
        Ok(None)
    }

    pub(in crate::script_init) fn create_entity(
        &mut self,
        id: u16,
        node: &Node,
        depth: usize,
    ) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let hero = field(&node.fields, "m_A1A715A6");
        let mut unsupported = vec![
            "m_F7621CAB",
            "m_40AF7E2D",
            "m_EC1E473E",
            "m_3E3E203A",
            "m_888BCD8B",
            "m_AA26E7DD",
            "m_A3A50DF2",
            "m_373DBF32",
            "m_E2BF1226",
            "m_0FDD45BB",
            "m_481C68E7",
        ];
        if hero.is_null() {
            unsupported.extend([
                "m_1C558638",
                "m_3EBF988B",
                "m_F8E56B94",
                "m_DE3B4F01",
                "m_D7F85181",
                "m_246C47AF",
            ]);
        } else {
            unsupported.push("m_entityDef");
        }
        let output = field(&node.fields, "m_E95B313B");
        let target = script_expr::lvalue(output);
        let flags_valid = ["m_A086DB58", "m_294B652A", "m_607F6292"]
            .iter()
            .all(|name| {
                let value = field(&node.fields, name);
                value.is_null() || value.as_bool() == Some(false) || value.as_i64() == Some(0)
            });
        let mode = field(&node.fields, "m_51AB9D21");
        if unsupported
            .iter()
            .any(|name| !field(&node.fields, name).is_null())
            || !flags_valid
            || !(mode.is_null() || mode.as_i64() == Some(5))
            || !matches!(&target, Ok(Some(_))) && !output.is_null()
        {
            self.report(site, Issue::UnsupportedField("entity creation options"));
            return Ok(false);
        }
        let target = target.ok().flatten();
        let Some(position) =
            self.creation_scalar(id, node, field(&node.fields, "m_position"), Value::Null)?
        else {
            return Ok(false);
        };
        let position = match position {
            Value::Vector(position) => Some(position),
            Value::Entity(entity) => self.world.creation_position(entity),
            _ => None,
        };
        let Some(position) = position.filter(|point| point.iter().all(|axis| axis.is_finite()))
        else {
            self.report(site, Issue::UnsupportedField("entity creation transform"));
            return Ok(false);
        };
        let kind = if hero.is_null() {
            let Some(Value::Asset(definition)) = self.creation_scalar(
                id,
                node,
                field(&node.fields, "m_entityDef"),
                Value::Asset(0),
            )?
            else {
                self.report(site, Issue::UnsupportedField("entity creation resource"));
                return Ok(false);
            };
            if definition >> 48 != 0x0400 || definition & 0x0000_ffff_ffff_ffff == 0 {
                self.report(site, Issue::UnsupportedField("entity creation resource"));
                return Ok(false);
            }
            CreationKind::Definition(definition)
        } else {
            let Some(Value::Asset(hero)) = self.creation_scalar(id, node, hero, Value::Asset(0))?
            else {
                self.report(site, Issue::UnsupportedField("hero creation identity"));
                return Ok(false);
            };
            let Some(Value::Integer(team @ 0..=1)) =
                self.creation_scalar(id, node, field(&node.fields, "m_3EBF988B"), Value::Null)?
            else {
                self.report(site, Issue::UnsupportedField("hero creation team"));
                return Ok(false);
            };
            let Some(yaw) = self.creation_scalar(
                id,
                node,
                field(&node.fields, "m_1C558638"),
                Value::Float(0.0),
            )?
            else {
                return Ok(false);
            };
            if hero >> 48 != 0x02e0
                || hero & 0x0000_ffff_ffff_ffff == 0
                || !matches!(yaw, Value::Float(_) | Value::Integer(_))
                || !script_expr::to_float(&yaw).is_finite()
            {
                self.report(
                    site,
                    Issue::UnsupportedField("hero creation identity or transform"),
                );
                return Ok(false);
            }
            let mut flags = [Value::Null, Value::Null];
            for (slot, name) in flags.iter_mut().zip(["m_F8E56B94", "m_DE3B4F01"]) {
                let Some(value) =
                    self.creation_scalar(id, node, field(&node.fields, name), Value::Null)?
                else {
                    return Ok(false);
                };
                *slot = value;
            }
            let mut resources = [None; 2];
            for (slot, (name, kind)) in resources
                .iter_mut()
                .zip([("m_D7F85181", 0x0400), ("m_246C47AF", 0x0a50)])
            {
                let Some(value) =
                    self.creation_scalar(id, node, field(&node.fields, name), Value::Asset(0))?
                else {
                    return Ok(false);
                };
                match value {
                    Value::Null | Value::Asset(0) => (),
                    Value::Asset(asset) if asset >> 48 == kind => *slot = Some(asset),
                    _ => {
                        self.report(site, Issue::UnsupportedField("hero creation resource"));
                        return Ok(false);
                    }
                }
            }
            CreationKind::Hero {
                hero,
                team: team as u8,
                yaw: script_expr::to_float(&yaw),
                flags,
                controller: resources[0],
                skin: resources[1],
            }
        };
        match self
            .world
            .create_entity(site, &EntityCreation { position, kind })
        {
            Ok(Some(entity)) if script_expr::valid_entity(entity) && entity != site.entity => {
                if let Some(target) = target {
                    self.write(source_key(id, target), Value::Entity(entity))?;
                }
            }
            Ok(None) => (),
            result => {
                self.report(
                    site,
                    Issue::EntityCreation(match result {
                        Err(error) => error,
                        _ => "entity creation returned an invalid binding".into(),
                    }),
                );
                return Ok(false);
            }
        }
        self.follow(id, node, "m_outPlug", None, depth)?;
        Ok(true)
    }

    pub(in crate::script_init) fn begin_spawn_search(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let destination = script_expr::lvalue(field(&node.fields, "m_8FEA3023"));
        if !matches!(destination, Ok(Some(_)))
            || ["m_priority", "m_2B717EC8", "m_1BCC45B9"]
                .iter()
                .any(|name| !field(&node.fields, name).is_null())
            || node.links("m_7D31C943").next().is_some()
        {
            self.report(site, Issue::UnsupportedField("spawn search configuration"));
            return Ok(());
        }
        let request = (|| {
            let mut input = |name, default| {
                self.evaluate(key.instance, node, field(&node.fields, name), None)
                    .map(|value| if value == Value::Null { default } else { value })
            };
            let position = input("m_position", Value::Vector([0.0; 3]))?;
            let Value::Vector(mut direction) = input("m_ADC02DA1", Value::Vector([0.0; 3]))? else {
                return None;
            };
            let radius = script_expr::to_float(&input("m_radius", Value::Float(1_000_000.0))?)
                .min(1_000_000.0);
            let angle = script_expr::to_float(&input("m_A30B383E", Value::Float(360.0))?);
            let minimum = script_expr::to_int(&input("m_EC1B83C7", Value::Integer(1))?, 1).max(1);
            let maximum =
                script_expr::to_int(&input("m_7084DA43", Value::Integer(1))?, 1).max(minimum);
            let scale = script_expr::to_float(&input("m_F649CC6C", Value::Float(1.0))?).max(0.5);
            if truthy(&input("m_DAD5978F", Value::Bool(false))?)
                || script_expr::to_int(&input("m_7762F430", Value::Integer(-1))?, -1) != -1
                || scale != 0.5
                || !(0.0 < radius && radius <= 70.0)
                || maximum > 300
                || !angle.is_finite()
                || !direction.iter().all(|v| v.is_finite())
            {
                return None;
            }
            let position = match position {
                Value::Entity(entity) if script_expr::valid_entity(entity) => {
                    self.world.position(entity)?
                }
                Value::Vector(position) => position,
                _ => return None,
            };
            if !position.iter().all(|v| v.is_finite()) {
                return None;
            }
            let length = direction[0].hypot(direction[1]).hypot(direction[2]);
            if length > 0.0 {
                direction = direction.map(|value| f64::from((value / length) as f32));
            }
            Some(SpawnSearch {
                position,
                direction,
                radius,
                half_angle: f64::from(
                    (angle * f64::from((std::f64::consts::PI / 360.0) as f32)) as f32,
                ),
                minimum: minimum as usize,
                maximum: maximum as usize,
            })
        })();
        let Some(request) = request else {
            self.report(site, Issue::UnsupportedField("spawn search inputs"));
            return Ok(());
        };
        self.state_mut(key).expect("active search").spawn_search = Some(request);
        Ok(())
    }

    pub(in crate::script_init) fn finish_spawn_search(
        &mut self,
        search: SpawnSearchKey,
        positions: Vec<[f64; 3]>,
    ) -> Result<(), InitFault> {
        let key = StateKey {
            instance: search.instance,
            raw: search.raw,
        };
        let Some(state) = self.state_mut(key).filter(|state| {
            state.active
                && state.activation == search.activation
                && state.start_ms == search.start_ms
        }) else {
            return Ok(());
        };
        let Some(request) = state.spawn_search else {
            return Ok(());
        };
        if !(request.minimum..=request.maximum).contains(&positions.len())
            || !positions.iter().flatten().all(|v| v.is_finite())
        {
            return Ok(());
        }
        state.spawn_search = None;
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("active search graph state");
        let output = script_expr::lvalue(field(&node.fields, "m_8FEA3023"))
            .expect("validated search output")
            .expect("validated search destination");
        self.write(
            source_key(key.instance, output),
            Value::Array(positions.into_iter().map(Value::Vector).collect()),
        )?;
        self.exit_subgraph(key.instance, node, "m_F9D59C3A", 1)?;
        self.post(key.instance, Event::Finish(key.raw))
    }

    /// Deliver one owner move result to the state that authored the request.
    /// Standard state teardown clears the request and runs the authored plugs.
    pub(in crate::script_init) fn deliver_move_result(
        &mut self,
        key: MoveToKey,
        outcome: MoveOutcome,
    ) -> Result<(), InitFault> {
        let state_key = StateKey {
            instance: key.instance,
            raw: key.raw,
        };
        if self.machine.owner_dead
            || !self
                .state(state_key)
                .is_some_and(|state| state.accepts_move_result(key))
        {
            return Ok(());
        }
        // The public reference host maps successful travel to FINISH and
        // obstructed travel to ABORT (match.py, body_command).
        self.end(state_key, outcome == MoveOutcome::Arrived, 0)
    }

    pub(in crate::script_init) fn begin_static_target(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<bool, InitFault> {
        let id = key.instance;
        if participant_filter(node).is_some() {
            return self.collect_targets(id, node);
        }
        let site = self.site(id, node);
        let target = match script_expr::lvalue(field(&node.fields, "m_4AEEE6A1")) {
            Ok(Some(output)) => source_key(id, output),
            Ok(None) => {
                self.report(site, Issue::UnsupportedField("static target output"));
                return Ok(false);
            }
            Err(error) => {
                self.report(site, Issue::Expression(error));
                return Ok(false);
            }
        };
        if let Some((tag, excluded)) = excluded_target_tags(node) {
            let Some(entities) = self.world.targets_with_tag(site, tag, excluded) else {
                self.report(
                    site,
                    Issue::UnsupportedField("tagged targets with exclusions"),
                );
                return Ok(false);
            };
            self.write(
                target,
                Value::Array(entities.into_iter().map(Value::Entity).collect()),
            )?;
            return Ok(true);
        }
        let config = static_target_operand(node).expect("unfiltered static target");
        let Some(value) = self.evaluate(id, node, config, None) else {
            return Ok(false);
        };
        let entity = match value {
            Value::Asset(tag) if tag >> 48 == 0x0d60 => self.world.static_target(tag),
            _ => None,
        };
        let Some(entity) = entity.filter(|&id| script_expr::valid_entity(id)) else {
            self.report(site, Issue::UnsupportedField("unique static target"));
            return Ok(false);
        };
        self.write(target, Value::Array(vec![Value::Entity(entity)]))?;
        Ok(true)
    }

    pub(in crate::script_init) fn collect_targets(
        &mut self,
        id: u16,
        node: &Node,
    ) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "m_4AEEE6A1")) else {
            self.report(
                site,
                Issue::UnsupportedField("participant collection output"),
            );
            return Ok(false);
        };
        let (entities, issue) = if let Some(filter) = participant_filter(node) {
            (
                self.world.participant_targets(site, filter),
                "scene participant filter",
            )
        } else if let Some(tag) = placement_collection_tag(node) {
            (
                self.world.tagged_placements(site, tag).map(<[u32]>::to_vec),
                "scene target collection",
            )
        } else {
            (None, "scene target collection")
        };
        let Some(entities) = entities else {
            self.report(site, Issue::UnsupportedField(issue));
            return Ok(false);
        };
        self.write(
            source_key(id, output),
            Value::Array(entities.into_iter().map(Value::Entity).collect()),
        )?;
        Ok(true)
    }

    pub(in crate::script_init) fn has_no_ai_control_target(
        &mut self,
        id: u16,
        node: &Node,
    ) -> bool {
        let site = self.site(id, node);
        node.is_networked()
            && node.remote == -1
            && script_expr::native_shape(field(&node.fields, "m_00D49978"), "STU_B5A0CAF0", None)
            && self
                .world
                .related_entity(site, EntityRelation::AiControlEntity, site.entity)
                == Some(0)
    }

    pub(in crate::script_init) fn update_movement_sample(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let frame = self.machine.frame;
        let Some(state) = self.state_mut(key) else {
            return Ok(());
        };
        let StateKind::MovementSample {
            flags: previous,
            pose_frame: previous_pose_frame,
            sampled_at,
        } = &mut state.kind
        else {
            return Ok(());
        };
        if *sampled_at == Some(frame) {
            return Ok(());
        }
        let (previous, previous_pose_frame) = (*previous, *previous_pose_frame);
        *sampled_at = Some(frame);
        let site = self.site(key.instance, node);
        let Some(sample) = self
            .world
            .movement_sample(site)
            .filter(|sample| sample.flags < 1 << 13)
        else {
            self.report(site, Issue::UnsupportedField("owner movement sample"));
            return Ok(());
        };
        let changed = previous ^ sample.flags;
        self.state_mut(key).expect("active movement sample").kind = StateKind::MovementSample {
            flags: sample.flags,
            pose_frame: sample.pose_frame,
            sampled_at: Some(frame),
        };
        for (bit, name) in MOVEMENT_BOOLEAN_OUTPUTS.iter().enumerate() {
            let mask = 1 << bit;
            if changed & mask == 0 {
                continue;
            }
            if let Some(variable) = script_expr::lvalue(field(&node.fields, name))
                .expect("validated movement sample output")
            {
                self.write(
                    source_key(key.instance, variable),
                    Value::Bool(sample.flags & mask != 0),
                )?;
            }
        }
        let was_airborne = previous & 1 != 0;
        let airborne = sample.flags & 1 != 0;
        // Gap frames can retain the last pose. A restart requires a fresh
        // movement record with an authoritative zero air-tick count.
        let airborne_restart = was_airborne
            && airborne
            && sample.air_ticks == 0
            && sample.pose_frame.is_some()
            && sample.pose_frame != previous_pose_frame;
        if was_airborne
            && (!airborne || airborne_restart)
            && node.links("m_2102C541").next().is_some()
        {
            self.follow(key.instance, node, "m_2102C541", Some(key), depth + 1)?;
        }
        if airborne
            && (!was_airborne || airborne_restart)
            && self.state(key).is_some_and(|state| state.active)
            && node.links("m_E37BC6C2").next().is_some()
        {
            self.follow(key.instance, node, "m_E37BC6C2", Some(key), depth + 1)?;
        }
        Ok(())
    }

    /// Execute a generic retail `STUStatescriptStateRayCast` or `STU_08D6BA6E`
    /// action raycast and write its authored output variables.
    pub(super) fn run_authored_raycast(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
    ) -> Result<(), InitFault> {
        let site = self.site(id, node);
        let origin = self
            .evaluate(id, node, field(&node.fields, "m_startPosWS"), caller)
            .and_then(|value| match value {
                Value::Vector(v) if v.iter().all(|a| a.is_finite()) => Some(v),
                _ => None,
            });
        let direction = self
            .evaluate(id, node, field(&node.fields, "m_dirWS"), caller)
            .and_then(|value| match value {
                Value::Vector(v) if v.iter().all(|a| a.is_finite()) => {
                    let len = v[0].hypot(v[1]).hypot(v[2]);
                    (len > 1e-6).then(|| v.map(|a| a / len))
                }
                _ => None,
            });
        let length = self
            .evaluate(id, node, field(&node.fields, "m_castLength"), caller)
            .and_then(pool_number)
            .filter(|len| len.is_finite() && *len > 0.0)
            .unwrap_or(0.0);

        let hit = match (origin, direction) {
            (Some(origin), Some(direction)) if length > 0.0 => self
                .world
                .ability_ground_ray(site, self.machine.frame, origin, direction, length)
                .or_else(|| {
                    self.world
                        .spatial_ray_cast(site, self.machine.frame, origin, direction, length)
                })
                .flatten(),
            _ => None,
        };

        let mut write_opt = |field_name: &str, value: Value| -> Result<(), InitFault> {
            let cfg = field(&node.fields, field_name);
            if !cfg.is_null() {
                if let Ok(Some(var)) = script_expr::lvalue(cfg) {
                    self.write(source_key(id, var), value)?;
                }
            }
            Ok(())
        };

        write_opt("m_out_HitSomething", Value::Bool(hit.is_some()))?;
        if let Some(hit) = hit {
            write_opt("m_out_HitPointWS", Value::Vector(hit.point))?;
            write_opt("m_out_HitNormal", Value::Vector(hit.normal))?;
            write_opt("m_out_HitEntity", Value::Null)?;
        } else {
            write_opt("m_out_HitEntity", Value::Null)?;
        }
        Ok(())
    }
}
