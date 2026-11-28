//! StateScript continuous state effects, health pools, AI overrides, stacks, and weapon volleys.

use std::{collections::BTreeMap, sync::Arc};

use orpheus_runtime::format::parse_guid;
use serde_json::Value as Json;

use crate::{
    script_expr::{self, Scope, Variable, truthy},
    script_graph::{Node, asset_guid},
    script_modifiers::{Modifier, ModifierKey, Operation, SPEED_SLOT, narrow},
    script_runtime::{FRAME_MS, GAME_MODE_ENTITY, MAX_MOVEMENT_AXIS, PoolKind},
    script_wire::{Value, Variables},
};

use super::super::{
    ABILITY_PUSHED, ABILITY_RUNNING, HUD_HEALTH, HUD_MAX_HEALTH, LINK_PRIORITY, MAX_PENDING_SHOTS,
    MAX_PLUG_DEPTH, OWNER_HEALTH, SCRIPT_SOURCE, SUBGRAPH_STATES,
    machine::{Event, LinkValue, StateKey, VarKey, VariableLink, Volley},
    shapes::{
        array_field, chase_lerp, chase_step, chase_value, configured, field, immediate_self_damage,
        named_temporary_shield, pool_kind, pool_number, raw_indices, same, shot_delay, source_key,
        stored,
    },
    types::{ChasePayload, InitFault, Issue, ReportedShot, ShotEvent, StateKind},
    world::{
        AiBehaviorRequest, AiControlOverride, AiFacing, AiWeaponOverride, PetSpawn, Site,
        TargetTagEffect, VelocityAxis, VelocityOp, World,
    },
};
use super::Vm;

impl<'graph, 'world, 'machine, W: World> Vm<'graph, 'world, 'machine, W> {
    pub(in crate::script_init) fn place_spray(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let unlock = self.evaluate(key.instance, node, field(&node.fields, "m_E441DC25"), None);
        let reach = self
            .evaluate(key.instance, node, field(&node.fields, "m_7F47658A"), None)
            .as_ref()
            .map_or(0.0, script_expr::to_float);
        let placement = match unlock {
            Some(Value::Asset(asset))
                if asset >> 48 == 0x0250
                    && asset & 0x0000_ffff_ffff_ffff != 0
                    && reach.is_finite()
                    && reach > 0.0 =>
            {
                match self.world.spray_placement(reach, self.command_frame) {
                    Some(hit) => hit.map(|hit| (asset, hit)),
                    None => {
                        self.report(self.site(key.instance, node), Issue::ObservationUnavailable);
                        None
                    }
                }
            }
            _ => None,
        };
        if let Some(state) = self.state_mut(key) {
            state.kind = StateKind::SprayPlacement(placement);
        }
        let plug = if placement.is_some() {
            "m_A39D2005"
        } else {
            "m_9254FB21"
        };
        self.follow(key.instance, node, plug, Some(key), depth)?;
        if placement.is_none() {
            self.post(key.instance, Event::Abort(key.raw))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn begin_target_tag(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let add = field(&node.fields, "m_targetTags");
        let suppress = field(&node.fields, "m_44D1BE94");
        let (literal, present) = match (add.is_null(), suppress.is_null()) {
            (false, true) => (add, true),
            (true, false) => (suppress, false),
            _ => {
                self.report(site, Issue::UnsupportedField("target tag operands"));
                return Ok(());
            }
        };
        let Some(tag) = script_expr::target_tag_config(literal).filter(|&tag| tag != 0) else {
            self.report(site, Issue::UnsupportedField("target tag literal"));
            return Ok(());
        };
        let Some(Value::Entity(entity)) =
            self.evaluate(key.instance, node, field(&node.fields, "m_31714AFC"), None)
        else {
            self.report(site, Issue::UnsupportedField("target tag entity"));
            return Ok(());
        };
        let effect = TargetTagEffect {
            entity,
            tag,
            present,
        };
        if !self.world.set_target_tag_effect(site, effect, true) {
            self.report(site, Issue::UnsupportedField("target tag effect"));
            return Ok(());
        }
        self.state_mut(key).expect("active target tag").kind = StateKind::TargetTag {
            effect: Some(effect),
        };
        Ok(())
    }

    pub(in crate::script_init) fn ai_behavior_value(
        &mut self,
        key: StateKey,
        node: &Node,
        name: &str,
        default: f64,
    ) -> Result<Option<Value>, InitFault> {
        let mut config = field(&node.fields, name);
        for _ in 0..MAX_PLUG_DEPTH {
            self.spend("AI behavior scalar")?;
            if config.is_null() {
                return Ok(Some(Value::Float(default)));
            }
            if script_expr::native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                config = config
                    .get("m_configVars")
                    .and_then(Json::as_array)
                    .and_then(|items| items.first())
                    .unwrap_or(&Json::Null);
            } else {
                return Ok(self
                    .evaluate(key.instance, node, config, Some(key))
                    .map(|value| {
                        if value == Value::Null {
                            Value::Float(default)
                        } else {
                            value
                        }
                    }));
            }
        }
        self.report(
            self.site(key.instance, node),
            Issue::UnsupportedField("AI behavior config depth"),
        );
        Ok(None)
    }

    pub(in crate::script_init) fn update_ai_behavior(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let previous = match self.state(key).map(|state| state.kind) {
            Some(StateKind::AiBehavior { record }) => record,
            _ => return Ok(()),
        };
        let (priority, prefer_later) = if let Some(previous) = previous {
            (previous.priority, previous.prefer_later)
        } else {
            let cfg = field(&node.fields, "m_priority");
            if cfg.is_null() {
                (0.0, false)
            } else {
                if !script_expr::native_fields(cfg, &["m_priority", "m_resourceKey"])
                    || field(cfg, "$").as_str() != Some("STU_8A491388")
                    || !field(cfg, "m_resourceKey").is_null()
                    || !field(cfg, "m_priority")
                        .as_str()
                        .and_then(parse_guid)
                        .is_some_and(|guid| guid >> 48 == 0x0c40)
                {
                    self.report(
                        site,
                        Issue::UnsupportedField("AI behavior priority resource"),
                    );
                    return Ok(());
                }
                match script_expr::stack_priority(cfg) {
                    Ok(priority) => (f64::from(priority.level), priority.above_equals),
                    Err(error) => {
                        self.report(site, Issue::Expression(error));
                        return Ok(());
                    }
                }
            }
        };
        let Some(flag) = self.ai_behavior_value(key, node, "m_88881C38", 0.0)? else {
            return Ok(());
        };
        let mut shared = [0.0; 2];
        for (number, name) in shared.iter_mut().zip(["m_36998F8A", "m_44064F45"]) {
            let Some(value) = self.ai_behavior_value(key, node, name, 0.0)? else {
                return Ok(());
            };
            *number = script_expr::to_float(&value);
        }
        let (class, fields): (&'static str, &[(&str, f64)]) = match node.class.as_str() {
            "STU_B61E6681" => ("STU_B61E6681", &[("m_203D6B00", 0.0), ("m_weight", 0.0)]),
            "STU_CFA338CB" => (
                "STU_CFA338CB",
                &[
                    ("m_DC8A3A89", 0.0),
                    ("m_503D6044", 0.0),
                    ("m_157D72D1", 0.0),
                ],
            ),
            "STU_600E3C59" => (
                "STU_600E3C59",
                &[
                    ("m_D655C99A", 2.0),
                    ("m_width", 2.0),
                    ("m_weight", 0.0),
                    ("", 0.0),
                    ("m_B802CBBA", 1.0),
                ],
            ),
            _ => unreachable!("AI behavior class"),
        };
        let mut inputs = [0.0; 5];
        for (index, (name, default)) in fields.iter().enumerate() {
            if name.is_empty() {
                continue;
            }
            let Some(value) = self.ai_behavior_value(key, node, name, *default)? else {
                return Ok(());
            };
            inputs[index] = script_expr::to_float(&value);
        }
        if class == "STU_CFA338CB" {
            inputs[2] =
                f64::from((inputs[2] * f64::from((std::f64::consts::PI / 180.0) as f32)) as f32);
        }
        if class == "STU_600E3C59" {
            inputs[3] = f64::from(match field(&node.fields, "m_DD792FF0").as_u64() {
                Some(0) => 1.0f32 / 60.0,
                Some(1) => 0.05,
                Some(2) => 0.1,
                Some(4) => 0.5,
                _ => 0.2,
            });
        }
        if shared
            .iter()
            .chain(inputs.iter())
            .any(|value| !value.is_finite())
        {
            self.report(site, Issue::UnsupportedField("AI behavior finite inputs"));
            return Ok(());
        }
        let record = AiBehaviorRequest {
            class,
            priority,
            prefer_later,
            flag: truthy(&flag),
            shared,
            inputs,
        };
        match self.world.ai_behavior_request(site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI behavior").kind = StateKind::AiBehavior {
                    record: Some(record),
                }
            }
            Some(false) => (),
            None => self.report(site, Issue::UnsupportedField("AI behavior component")),
        }
        Ok(())
    }

    pub(in crate::script_init) fn refresh_contact(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(value) = self.evaluate(
            key.instance,
            node,
            field(&node.fields, "m_53FCEDBF"),
            Some(key),
        ) else {
            return Ok(());
        };
        let targets = match value {
            Value::Array(items) => items,
            Value::Null => Vec::new(),
            value => vec![value],
        };
        let mut entities = Vec::with_capacity(targets.len());
        let generic_player = self.world.admits_generic_player_runtime(site);
        for value in targets {
            self.spend("contact effect target")?;
            let Value::Entity(entity) = value else {
                if generic_player {
                    continue;
                }
                self.report(site, Issue::UnsupportedField("contact effect target"));
                return Ok(());
            };
            if !script_expr::valid_entity(entity) {
                if generic_player {
                    continue;
                }
                self.report(site, Issue::UnsupportedField("contact effect target"));
                return Ok(());
            }
            entities.push(entity);
        }
        // The state admission already checked the immutable resource and sync binding.
        let resource = node.field("m_contactSet.m_contactSet").and_then(asset_guid);
        if !resource.is_some_and(|resource| {
            self.world
                .emit_contact_effect(site, site.entity, resource, &entities)
        }) {
            self.report(site, Issue::UnsupportedField("contact effect service"));
        }
        Ok(())
    }

    pub(in crate::script_init) fn begin_npc_health_change(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        if self.world.modify_health(site, 0) {
            self.register_tick(key, self.machine.now_ms);
            return Ok(());
        }
        if let Some(damage) = immediate_self_damage(node, key.raw) {
            if !damage.skip_dead_owner || !self.machine.owner_dead {
                return self.apply_npc_self_damage(site, damage.amount);
            }
            return Ok(());
        }
        let Some(target) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_FA3ECC43"),
            Value::Null,
        )?
        else {
            return Ok(());
        };
        if node.pos == 48 && target == Value::Array(Vec::new()) {
            return Ok(());
        }
        let config = field(&node.fields, "m_modifyHealth");
        if self.world.admits_generic_player_runtime(site) {
            let entities: Vec<u32> = match &target {
                Value::Entity(entity) if script_expr::valid_entity(*entity) => vec![*entity],
                Value::Array(items) => items
                    .iter()
                    .filter_map(|v| match *v {
                        Value::Entity(entity) if script_expr::valid_entity(entity) => Some(entity),
                        _ => None,
                    })
                    .collect(),
                _ => Vec::new(),
            };
            if entities.is_empty() {
                return Ok(());
            }
            let amount = self
                .creation_scalar(key.instance, node, field(config, "m_amount"), Value::Null)?
                .and_then(pool_number)
                .filter(|v| v.is_finite() && *v > 0.0)
                .unwrap_or(0.0);
            if amount > 0.0 {
                let is_heal = field(config, "m_93CDFD2F").as_u64() == Some(0);
                let delta = if is_heal { amount } else { -amount };
                for entity in entities {
                    let _ = self.world.queue_health_change(site, entity, delta);
                }
            }
            self.post(key.instance, Event::Finish(key.raw))?;
            return Ok(());
        }
        if self.world.admits_generic_player_runtime(site) {
            if let Value::Entity(target) = target {
                if script_expr::valid_entity(target)
                    && target != site.entity
                    && field(config, "m_93CDFD2F").as_u64() == Some(1)
                {
                    if let Some(amount) = self
                        .creation_scalar(
                            key.instance,
                            node,
                            field(config, "m_amount"),
                            Value::Null,
                        )?
                        .and_then(pool_number)
                        .filter(|amount| (0.0..=f64::from(f32::MAX)).contains(amount))
                    {
                        let _ = self.world.queue_modify_health(
                            site,
                            target,
                            -f64::from(amount as f32),
                            None,
                        );
                    }
                }
            }
            self.post(key.instance, Event::Finish(key.raw))?;
            return Ok(());
        }
        let Value::Entity(target) = target else {
            self.report(site, Issue::UnsupportedField("NPC damage target"));
            return Ok(());
        };
        if node.pos != 38
            || target == site.entity
            || !script_expr::valid_entity(target)
            || field(config, "m_93CDFD2F").as_u64() != Some(1)
            || !field(&node.fields, "m_95F1FB0B").is_null()
        {
            self.report(site, Issue::UnsupportedField("NPC health service"));
            return Ok(());
        }
        for name in ["m_instigator", "m_724CBA57"] {
            if self.creation_scalar(key.instance, node, field(config, name), Value::Null)?
                != Some(Value::Entity(site.entity))
            {
                self.report(site, Issue::UnsupportedField("NPC damage source"));
                return Ok(());
            }
        }
        let Some(amount) = self
            .creation_scalar(key.instance, node, field(config, "m_amount"), Value::Null)?
            .and_then(pool_number)
            .filter(|amount| (0.0..=f64::from(f32::MAX)).contains(amount))
        else {
            self.report(site, Issue::UnsupportedField("NPC damage amount"));
            return Ok(());
        };
        let amount = f64::from(amount as f32);
        if self.world.explosion_damage(site, target, amount) {
            self.post(key.instance, Event::Finish(key.raw))?;
        } else {
            self.report(
                site,
                Issue::UnsupportedField("NPC explosion damage service"),
            );
        }
        Ok(())
    }

    fn apply_npc_self_damage(&mut self, site: Site, amount: f64) -> Result<(), InitFault> {
        if !self.world.self_damage(site, amount) {
            self.report(site, Issue::UnsupportedField("NPC self damage service"));
        }
        self.publish_health_totals(site)
    }

    pub(in crate::script_init) fn update_named_shield_decay(
        &mut self,
        key: StateKey,
        node: &Node,
        elapsed_ms: u64,
    ) -> Result<bool, InitFault> {
        let site = self.site(key.instance, node);
        if self.machine.owner_dead {
            self.post(key.instance, Event::Finish(key.raw))?;
            return Ok(false);
        }
        let identifier = script_expr::named_shield_identifier(field(&node.fields, "m_7BE02F89"))
            .expect("validated named shield identifier");
        let effect = field(&node.fields, "m_modifyHealth");
        let Some(rate) = self
            .creation_scalar(key.instance, node, field(effect, "m_amount"), Value::Null)?
            .and_then(pool_number)
            .filter(|rate| *rate > 0.0 && *rate <= f64::from(f32::MAX))
        else {
            self.report(site, Issue::UnsupportedField("named shield decay rate"));
            return Ok(false);
        };
        let amount = rate * (elapsed_ms as f64 / 1_000.0);
        let Some(remaining) = self.world.drain_temporary_shield(site, identifier, amount) else {
            self.report(site, Issue::UnsupportedField("named shield decay service"));
            return Ok(false);
        };
        if elapsed_ms != 0 {
            self.publish_health_totals(site)?;
        }
        if remaining <= 0.0 {
            self.post(key.instance, Event::Finish(key.raw))?;
            return Ok(false);
        }
        Ok(true)
    }

    pub(in crate::script_init) fn begin_shield_recharge(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        if !script_expr::valid_entity(site.entity) || !self.world.recharge_shields(site, 0.0) {
            self.report(site, Issue::UnsupportedField("shield recharge service"));
            return Ok(());
        }
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    pub(in crate::script_init) fn tick_shield_recharge(
        &mut self,
        key: StateKey,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead || elapsed_ms == 0 {
            return Ok(());
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("shield recharge");
        let site = self.site(key.instance, node);
        let config = field(&node.fields, "m_modifyHealth");
        let rate = self
            .creation_scalar(key.instance, node, field(config, "m_amount"), Value::Null)?
            .and_then(pool_number)
            .filter(|rate| (0.0..=f64::from(f32::MAX)).contains(rate));
        let Some(rate) = rate else {
            self.report(site, Issue::UnsupportedField("shield recharge rate"));
            return Ok(());
        };
        if !self
            .world
            .recharge_shields(site, rate * elapsed_ms as f64 / 1000.0)
        {
            self.report(site, Issue::UnsupportedField("shield recharge service"));
        }
        self.publish_health_totals(site)
    }

    pub(in crate::script_init) fn refresh_ground_ray(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let fields = &node.fields;
        let segment = &fields["m_F5732403"];
        let origin = self.evaluate_at(site, &fields["m_3BC31C57"], None);
        let direction = self.evaluate_at(site, &segment["m_direction"], None);
        let length = self
            .evaluate_at(site, &segment["m_6A79BD98"], None)
            .and_then(pool_number);
        let (Some(Value::Vector(origin)), Some(Value::Vector(direction)), Some(length)) =
            (origin, direction, length)
        else {
            self.report(site, Issue::UnsupportedField("ground ray geometry"));
            return Ok(());
        };
        if !length.is_finite() || length <= 0.0 || direction != [0.0, -1.0, 0.0] {
            self.report(site, Issue::UnsupportedField("ground ray segment"));
            return Ok(());
        }
        let Some(hit) =
            self.world
                .ability_ground_ray(site, self.machine.frame, origin, direction, length)
        else {
            self.report(site, Issue::UnsupportedField("ground ray collision scene"));
            return Ok(());
        };
        let output = |field: &str| {
            let variable = script_expr::lvalue(&fields["m_target"][field])
                .ok()
                .flatten()
                .expect("ground ray output");
            source_key(key.instance, variable)
        };
        if let Some(contact) = hit.as_ref() {
            self.write(output("m_5ED803CF"), Value::Vector(contact.point))?;
        }
        self.write(output("m_7BA44A69"), Value::Bool(hit.is_some()))?;
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    pub(in crate::script_init) fn refresh_summoner_beam_ray(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let segment = field(&node.fields, "m_F5732403");
        let origin = self
            .evaluate(key.instance, node, field(&node.fields, "m_3BC31C57"), None)
            .and_then(|v| match v {
                Value::Vector(pos) if pos.iter().all(|a| a.is_finite()) => Some(pos),
                _ => None,
            });
        let (direction, length) = if segment.get("$").and_then(Json::as_str) == Some("STU_E5D454BA")
        {
            let end_pos =
                match self.evaluate(key.instance, node, field(segment, "m_BD631144"), None) {
                    Some(Value::Vector(pos)) if pos.iter().all(|a| a.is_finite()) => Some(pos),
                    _ => {
                        match self.evaluate(key.instance, node, field(segment, "m_entity"), None) {
                            Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => {
                                self.world.position(entity)
                            }
                            _ => None,
                        }
                    }
                };
            match (origin.or_else(|| self.world.position(site.entity)), end_pos) {
                (Some(start), Some(end)) => {
                    let delta = [end[0] - start[0], end[1] - start[1], end[2] - start[2]];
                    let dist = delta[0].hypot(delta[1]).hypot(delta[2]);
                    if dist.is_finite() && dist > 1e-6 {
                        (Some(delta.map(|a| a / dist)), Some(dist))
                    } else {
                        (None, None)
                    }
                }
                _ => (None, None),
            }
        } else {
            let direction = self
                .evaluate(key.instance, node, field(segment, "m_direction"), None)
                .and_then(|v| match v {
                    Value::Vector(dir) if dir.iter().all(|a| a.is_finite()) => Some(dir),
                    _ => None,
                });
            let length = self
                .evaluate(key.instance, node, field(segment, "m_6A79BD98"), None)
                .and_then(pool_number)
                .filter(|len| len.is_finite() && *len > 0.0);
            (direction, length)
        };
        let radius = self
            .evaluate(key.instance, node, field(&node.fields, "m_57DAE1B5"), None)
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(0.0);
        let filter = {
            let primary = field(&node.fields, "m_filter");
            if !primary.is_null() {
                primary
            } else {
                field(&node.fields, "m_3D667CE3")
            }
        };
        let generic = self
            .world
            .generic_beam_contact(site, origin, direction, length, radius, filter);
        let (entity, hit, point, normal, distance) = if let Some(contact) = generic {
            (
                contact.entity,
                contact.hit,
                contact.point,
                contact.normal,
                contact.distance,
            )
        } else if let Some(contact) = self.world.summoner_beam_contact(site) {
            (contact.entity, contact.hit, None, None, None)
        } else {
            self.report(site, Issue::UnsupportedField("Summoner beam ray service"));
            return Ok(());
        };
        let outputs = field(&node.fields, "m_target");
        for (name, value) in [
            ("m_692DFF72", entity.map_or(Value::Null, Value::Entity)),
            ("m_750377AA", Value::Bool(hit)),
            ("m_7BA44A69", Value::Bool(hit)),
            ("m_0CBF2A15", Value::Bool(hit)),
        ] {
            if let Some(output) = script_expr::lvalue(field(outputs, name))
                .map_err(|_| InitFault::InvalidRoot(key.instance))?
            {
                self.write(source_key(key.instance, output), value)?;
            }
        }
        for (name, vector) in [
            ("m_5ED803CF", point),
            ("m_F2F8606D", point),
            ("m_A7C2505B", normal),
        ] {
            if let Some(vector) = vector {
                if let Some(output) = script_expr::lvalue(field(outputs, name))
                    .map_err(|_| InitFault::InvalidRoot(key.instance))?
                {
                    self.write(source_key(key.instance, output), Value::Vector(vector))?;
                }
            }
        }
        if let Some(distance) = distance {
            if let Some(output) = script_expr::lvalue(field(outputs, "m_98DC2D67"))
                .map_err(|_| InitFault::InvalidRoot(key.instance))?
            {
                self.write(source_key(key.instance, output), Value::Float(distance))?;
            }
        }
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    pub(in crate::script_init) fn apply_generic_modify_health(
        &mut self,
        key: StateKey,
        node: &Node,
        elapsed_ms: Option<u64>,
    ) -> Result<(), InitFault> {
        let targets = self.evaluate_modify_health_targets(key, node);
        if targets.is_empty() {
            return Ok(());
        }
        self.apply_generic_modify_health_to_targets(key, node, elapsed_ms, &targets)
    }

    /// Retail `m_DD792FF0 == 1` (`STUStatescriptStateModifyHealth` on-target-enter mode,
    /// e.g. Wrecking Ball Fireball `0x1203:63`): fire once for each entity entering the
    /// tracked target set, and allow a fresh hit only after that entity leaves and re-enters.
    pub(in crate::script_init) fn apply_generic_modify_health_on_enter(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<bool, InitFault> {
        let current = self.evaluate_modify_health_targets(key, node);
        let previous = self
            .state(key)
            .map(|state| state.tracked_targets.as_slice())
            .unwrap_or(&[]);
        let newly_entered: Vec<u32> = current
            .iter()
            .copied()
            .filter(|entity| !previous.contains(entity))
            .collect();
        if let Some(state) = self.state_mut(key) {
            state.tracked_targets = current;
        }
        if newly_entered.is_empty() {
            return Ok(false);
        }
        self.apply_generic_modify_health_to_targets(key, node, None, &newly_entered)?;
        Ok(true)
    }

    fn evaluate_modify_health_targets(&mut self, key: StateKey, node: &Node) -> Vec<u32> {
        let id = key.instance;
        let site = self.site(id, node);
        let target_field = field(&node.fields, "m_FA3ECC43");
        let target_value = if target_field.is_null() {
            Value::Entity(site.entity)
        } else {
            match self.evaluate(id, node, target_field, None) {
                Some(value) => value,
                None => return Vec::new(),
            }
        };
        let items = match target_value {
            Value::Array(items) => items,
            Value::Null => Vec::new(),
            other => vec![other],
        };
        let mut out = Vec::with_capacity(items.len());
        for item in items {
            if let Value::Entity(target) = item {
                if script_expr::valid_entity(target) && !out.contains(&target) {
                    out.push(target);
                }
            }
        }
        out
    }

    fn apply_generic_modify_health_to_targets(
        &mut self,
        key: StateKey,
        node: &Node,
        elapsed_ms: Option<u64>,
        targets: &[u32],
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead || elapsed_ms == Some(0) || targets.is_empty() {
            return Ok(());
        }
        let id = key.instance;
        let site = self.site(id, node);
        let effect = field(&node.fields, "m_modifyHealth");
        let mut amount = self
            .evaluate(id, node, field(effect, "m_amount"), None)
            .and_then(pool_number)
            .filter(|amount| amount.is_finite() && *amount > 0.0)
            .unwrap_or(0.0);
        if let Some(ms) = elapsed_ms {
            amount = f64::from((amount * (ms as f64 / 1000.0)) as f32);
            if !amount.is_finite() || amount <= 0.0 {
                return Ok(());
            }
        }
        let direct_knockback = if elapsed_ms.is_none() {
            match self.evaluate(id, node, field(effect, "m_1376B4DF"), None) {
                Some(Value::Vector(v))
                    if v.iter().all(|c| c.is_finite()) && {
                        let mag = v[0].hypot(v[1]).hypot(v[2]);
                        mag > 1e-4 && mag <= 45.0
                    } =>
                {
                    Some(v)
                }
                _ => None,
            }
        } else {
            None
        };
        let knockback = if elapsed_ms.is_none() && direct_knockback.is_none() {
            crate::script_init::Knockback::from_authored(effect, &mut |cfg| {
                self.evaluate(id, node, cfg, None).ok_or_else(|| {
                    script_expr::EvalError::UnsupportedConfig("modify health knockback".into())
                })
            })
        } else {
            None
        };
        if amount <= 0.0 && knockback.is_none() && direct_knockback.is_none() {
            return Ok(());
        }
        let damage = field(effect, "m_93CDFD2F").as_u64() == Some(1);
        let delta = if amount > 0.0 {
            if damage { -amount } else { amount }
        } else {
            0.0
        };
        let kb_origin = if knockback.is_some() {
            let origin_field = field(effect, "m_C0139FA6");
            if origin_field.is_null() {
                self.world.position(site.entity)
            } else {
                match self.evaluate(id, node, origin_field, None) {
                    Some(Value::Vector(pos)) if pos.iter().all(|a| a.is_finite()) => Some(pos),
                    Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => {
                        self.world.position(entity)
                    }
                    _ => self.world.position(site.entity),
                }
            }
        } else {
            None
        };
        let loadout = {
            let l = field(effect, "m_loadout");
            asset_guid(field(l, "m_loadout")).or_else(|| match self.evaluate(id, node, l, None) {
                Some(Value::Asset(g)) if g != 0 => Some(g),
                _ => None,
            })
        };
        let mut hit_tags = crate::script_init::HitTags::EMPTY;
        for row in array_field(effect, "m_148F3152") {
            let tag_cfg = field(row, "m_EE3055BD");
            if let Some(guid) = asset_guid(field(tag_cfg, "m_identifier")).or_else(|| {
                match self.evaluate(id, node, tag_cfg, None) {
                    Some(Value::Asset(g)) if g != 0 => Some(g),
                    _ => None,
                }
            }) {
                let _ = hit_tags.push(guid);
            }
        }
        for &target in targets {
            let impulse = direct_knockback.or_else(|| {
                let kb = knockback.as_ref()?;
                let origin = kb_origin?;
                let target_pos = self.world.position(target)?;
                let [dx, dy, dz] = std::array::from_fn(|axis| target_pos[axis] - origin[axis]);
                let distance = dx.hypot(dy).hypot(dz);
                if dx == 0.0 && dz == 0.0 {
                    let forward = self.world.forward(site.entity).unwrap_or([0.0; 3]);
                    kb.radial_delta([forward[0], forward[2]], distance, true, 1.0)
                } else {
                    kb.radial_delta([dx, dz], distance, true, 1.0)
                }
            });
            if delta != 0.0 || impulse.is_some() {
                let _ = self
                    .world
                    .queue_modify_health_with_meta(site, target, delta, impulse, loadout, hit_tags);
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn refresh_generic_tracked_targets(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        if self.machine.owner_dead {
            return self.publish_tracked_targets(key, node, Vec::new());
        }
        let origin_field = field(&node.fields, "m_8DCA77B6");
        let offset_field = field(&node.fields, "m_05BE62DD");
        // Without a reference entity, the supplied center is already in world
        // coordinates. An expression may itself add a displacement to a body.
        let origin = if origin_field.is_null() {
            if offset_field.is_null() {
                site.entity
            } else {
                0
            }
        } else {
            match self.evaluate(id, node, origin_field, None) {
                Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => entity,
                _ => return self.publish_tracked_targets(key, node, Vec::new()),
            }
        };
        let offset = if offset_field.is_null() {
            [0.0; 3]
        } else {
            match self.evaluate(id, node, offset_field, None) {
                Some(Value::Vector(v)) if v.iter().all(|a| a.is_finite()) => v,
                Some(Value::Entity(body)) if origin == 0 && script_expr::valid_entity(body) => {
                    let Some(pos) = self.world.position(body) else {
                        return self.publish_tracked_targets(key, node, Vec::new());
                    };
                    pos
                }
                _ => return self.publish_tracked_targets(key, node, Vec::new()),
            }
        };
        let radius = self
            .evaluate(id, node, field(&node.fields, "m_radius"), None)
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(0.0);
        if radius <= 0.0 {
            return self.publish_tracked_targets(key, node, Vec::new());
        }
        let local_space = origin != 0 && field(&node.fields, "m_0297060F").as_u64() == Some(1);
        let end_field = field(&node.fields, "m_B3A77CD0");
        let end_offset = if end_field.is_null() {
            None
        } else {
            match self.evaluate(id, node, end_field, None) {
                Some(Value::Vector(v)) if v.iter().all(|a| a.is_finite()) => Some(v),
                _ => None,
            }
        };
        let cylinder_radius = self
            .evaluate(id, node, field(&node.fields, "m_16BDE607"), None)
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(0.0);
        let filter = {
            let primary = field(&node.fields, "m_filter");
            if !primary.is_null() {
                primary
            } else {
                field(&node.fields, "m_3D667CE3")
            }
        };
        let visible = field(&node.fields, "m_32AFE9AA").as_u64() == Some(1);
        let angle_field = field(&node.fields, "m_A30B383E");
        let candidates = if !angle_field.is_null() || cylinder_radius > 0.0 {
            let angle = if angle_field.is_null() {
                0.0
            } else {
                self.evaluate(id, node, angle_field, None)
                    .and_then(pool_number)
                    .filter(|a| (0.0..=360.0).contains(a))
                    .unwrap_or(360.0)
            };
            let yaw_deg = self
                .evaluate(id, node, field(&node.fields, "m_D45E22FA"), None)
                .and_then(pool_number)
                .filter(|v| v.is_finite());
            let pitch_deg = self
                .evaluate(id, node, field(&node.fields, "m_E46A19A3"), None)
                .and_then(pool_number)
                .filter(|v| v.is_finite());
            let Some(candidates) = self.world.cone_targets_directed(
                site,
                origin,
                offset,
                radius,
                angle,
                cylinder_radius,
                yaw_deg,
                pitch_deg,
                visible,
                filter,
            ) else {
                return self.publish_tracked_targets(key, node, Vec::new());
            };
            candidates
        } else {
            let Some(candidates) = self.world.swept_tracked_targets(
                site,
                origin,
                offset,
                end_offset,
                local_space,
                radius,
                visible,
                filter,
            ) else {
                return self.publish_tracked_targets(key, node, Vec::new());
            };
            candidates
        };
        let mut targets = Vec::with_capacity(candidates.len());
        let condition = field(filter, "m_condition");
        let masks = script_expr::entity_masks(filter);
        for sample in candidates {
            let candidate = sample.entity;
            self.spend("generic tracked target condition")?;
            if !script_expr::valid_entity(candidate) {
                continue;
            }
            // The host has already filtered its spatial candidates; when it supplies
            // native flags too, enforce the same mask contract as an entity filter operand.
            if self
                .world
                .entity_filter_flags(site, candidate)
                .is_some_and(|flags| {
                    !masks.is_some_and(|masks| script_expr::matches_entity_masks(masks, flags))
                })
            {
                continue;
            }
            let accepted = if condition.is_null() {
                true
            } else {
                let previous = self
                    .parameters
                    .replace(Arc::new(Variables::from([(8, Value::Entity(candidate))])));
                let value = self.evaluate(id, node, condition, None);
                self.parameters = previous;
                value.is_some_and(|v| truthy(&v))
            };
            if accepted {
                targets.push(sample);
            }
        }
        self.publish_tracked_samples(key, node, targets)
    }

    pub(in crate::script_init) fn tick_summoner_beam_damage(
        &mut self,
        key: StateKey,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead || elapsed_ms == 0 {
            return Ok(());
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("Summoner beam damage");
        let site = self.site(key.instance, node);
        let target = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_FA3ECC43"),
            Value::Null,
        )?;
        let Some(Value::Entity(target)) = target.filter(
            |value| matches!(value, Value::Entity(entity) if script_expr::valid_entity(*entity)),
        ) else {
            return Ok(());
        };
        let rate = self
            .creation_scalar(
                key.instance,
                node,
                &node.fields["m_modifyHealth"]["m_amount"],
                Value::Null,
            )?
            .and_then(pool_number)
            .filter(|amount| amount.is_finite() && *amount >= 0.0);
        if !rate.is_some_and(|rate| {
            self.world.summoner_beam_damage(
                site,
                target,
                f64::from((rate * elapsed_ms as f64 / 1000.0) as f32),
            )
        }) {
            self.report(
                site,
                Issue::UnsupportedField("Summoner beam damage service"),
            );
        }
        Ok(())
    }

    pub(in crate::script_init) fn refresh_witch_staff_effect(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let params = array_field(&node.fields, "m_params");
        if asset_guid(&node.fields["m_A76A8CF6"]["m_A76A8CF6"]) != Some(0x04a0_0000_0000_0019)
            || params.len() != 1
            || asset_guid(field(&params[0], "m_B5051BCE")) != Some(0x0a40_0000_0000_002a)
        {
            self.report(
                site,
                Issue::UnsupportedField("Witch staff effect resource or parameters"),
            );
            return Ok(());
        }
        let target = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_targets"),
            Value::Null,
        )?;
        let effect =
            if let Some(Value::Entity(target)) = target.filter(|_| !self.machine.owner_dead) {
                let amount = self
                    .creation_scalar(
                        key.instance,
                        node,
                        field(&params[0], "m_value"),
                        Value::Null,
                    )?
                    .and_then(pool_number)
                    .filter(|amount| amount.is_finite() && *amount >= 0.0);
                amount.map(|amount| (target, amount))
            } else {
                None
            };
        if !self.world.set_witch_staff_effect(site, effect) {
            self.report(site, Issue::UnsupportedField("Witch staff effect service"));
        }
        Ok(())
    }

    pub(in crate::script_init) fn tick_witch_staff_healing(
        &mut self,
        key: StateKey,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead || elapsed_ms == 0 {
            return Ok(());
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("Witch staff healing");
        let site = self.site(key.instance, node);
        let Value::Entity(source) = self
            .machine
            .value(VarKey::Instance(key.instance, SCRIPT_SOURCE), None)
        else {
            self.report(site, Issue::UnsupportedField("Witch staff healing source"));
            return Ok(());
        };
        let amount = self
            .creation_scalar(
                key.instance,
                node,
                &node.fields["m_modifyHealth"]["m_amount"],
                Value::Null,
            )?
            .and_then(pool_number)
            .filter(|amount| amount.is_finite() && *amount >= 0.0);
        if !amount.is_some_and(|amount| {
            self.world.heal_from_witch_staff(
                site,
                source,
                f64::from((amount * elapsed_ms as f64 / 1000.0) as f32),
            )
        }) {
            self.report(site, Issue::UnsupportedField("Witch staff healing service"));
        }
        self.publish_health_totals(site)
    }

    pub(in crate::script_init) fn refresh_tracked_targets(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        if matches!((site.graph, site.node), (0x006b, Some(6 | 7 | 121))) {
            let radius = self
                .creation_scalar(
                    key.instance,
                    node,
                    field(&node.fields, "m_radius"),
                    Value::Null,
                )?
                .and_then(pool_number)
                .filter(|radius| *radius > 0.0 && radius.is_finite());
            if let Some(targets) =
                radius.and_then(|radius| self.world.witch_staff_targets(site, radius))
            {
                return self.publish_tracked_targets(key, node, targets);
            }
        }
        let Some(Value::Entity(origin)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_8DCA77B6"),
            Value::Null,
        )?
        else {
            self.report(site, Issue::UnsupportedField("tracked target origin"));
            return Ok(());
        };
        let Some(Value::Vector(offset)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_05BE62DD"),
            Value::Vector([0.0; 3]),
        )?
        else {
            self.report(site, Issue::UnsupportedField("tracked target offset"));
            return Ok(());
        };
        let Some(radius) = self
            .creation_scalar(
                key.instance,
                node,
                field(&node.fields, "m_radius"),
                Value::Null,
            )?
            .and_then(pool_number)
            .filter(|radius| *radius >= 0.0 && radius.is_finite())
        else {
            self.report(site, Issue::UnsupportedField("tracked target radius"));
            return Ok(());
        };
        let filter = {
            let primary = field(&node.fields, "m_filter");
            if !primary.is_null() {
                primary
            } else {
                field(&node.fields, "m_3D667CE3")
            }
        };
        let angle_field = field(&node.fields, "m_A30B383E");
        if !angle_field.is_null() {
            let Some(angle) = self
                .creation_scalar(key.instance, node, angle_field, Value::Null)?
                .and_then(pool_number)
                .filter(|angle| (0.0..=360.0).contains(angle))
            else {
                self.report(site, Issue::UnsupportedField("tracked target angle"));
                return Ok(());
            };
            let visible = field(&node.fields, "m_32AFE9AA").as_u64() == Some(1);
            let Some(targets) = self
                .world
                .cone_targets(site, origin, offset, radius, angle, visible, filter)
            else {
                self.report(site, Issue::UnsupportedField("tracked target cone service"));
                return Ok(());
            };
            return self.publish_tracked_targets(key, node, targets);
        }
        let Some(candidates) = self
            .world
            .tracked_targets(site, origin, offset, radius, filter)
        else {
            self.report(site, Issue::UnsupportedField("tracked target service"));
            return Ok(());
        };
        let mut targets = Vec::new();
        for candidate in candidates {
            self.spend("tracked target condition")?;
            if !script_expr::valid_entity(candidate) {
                self.report(site, Issue::UnsupportedField("tracked target identity"));
                return Ok(());
            }
            let condition = field(filter, "m_condition");
            let accepted = if condition.is_null() {
                true
            } else {
                let previous = self
                    .parameters
                    .replace(Arc::new(Variables::from([(8, Value::Entity(candidate))])));
                let value = self.evaluate(key.instance, node, condition, None);
                self.parameters = previous;
                let Some(value) = value else {
                    return Ok(());
                };
                truthy(&value)
            };
            if accepted {
                targets.push(candidate);
            }
        }
        self.publish_tracked_targets(key, node, targets)
    }

    pub(in crate::script_init) fn publish_tracked_targets(
        &mut self,
        key: StateKey,
        node: &Node,
        targets: Vec<u32>,
    ) -> Result<(), InitFault> {
        self.publish_tracked_samples(key, node, targets.into_iter().map(Into::into).collect())
    }

    fn publish_tracked_samples(
        &mut self,
        key: StateKey,
        node: &Node,
        samples: Vec<crate::script_init::TrackedTarget>,
    ) -> Result<(), InitFault> {
        if let Some(output) = script_expr::lvalue(field(&node.fields, "m_CEE20F6F"))
            .map_err(|_| InitFault::InvalidRoot(key.instance))?
        {
            let Some(distances) = samples
                .iter()
                .map(|target| {
                    target
                        .surface_distance
                        .filter(|d| d.is_finite() && *d >= 0.0)
                        .map(|distance| Value::Float(f64::from(distance as f32)))
                })
                .collect::<Option<Vec<_>>>()
            else {
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField("tracked capsule surface distance"),
                );
                return Ok(());
            };
            self.write(source_key(key.instance, output), Value::Array(distances))?;
        }
        let targets: Vec<_> = samples.into_iter().map(|sample| sample.entity).collect();
        if let Some(output) = script_expr::lvalue(field(&node.fields, "m_8A2905A0"))
            .map_err(|_| InitFault::InvalidRoot(key.instance))?
        {
            self.write(
                source_key(key.instance, output),
                Value::Array(
                    targets
                        .iter()
                        .map(|target| Value::Entity(*target))
                        .collect(),
                ),
            )?;
        }
        if let Some(output) = script_expr::lvalue(field(&node.fields, "m_1B89EA79"))
            .map_err(|_| InitFault::InvalidRoot(key.instance))?
        {
            self.write(
                source_key(key.instance, output),
                Value::Integer(targets.len() as i64),
            )?;
        }
        self.state_mut(key)
            .expect("tracked target state")
            .tracked_targets = targets;
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    pub(in crate::script_init) fn refresh_deadeye_effects(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        const TARGET_GRAPH: u32 = 0x01ee;
        const REAPPLY: u64 = 0x0240_0000_0000_000f;
        let site = self.site(key.instance, node);
        let selected = match stored(&self.machine.snapshot, VarKey::Entity(2551)) {
            Value::Array(values) if self.graphs.graph(TARGET_GRAPH).is_some() => values
                .into_iter()
                .filter_map(|value| match value {
                    Value::Entity(entity) if self.world.entity_alive(site, entity) => Some(entity),
                    _ => None,
                })
                .collect::<Vec<_>>(),
            _ => Vec::new(),
        };
        self.retain_target_children(key, &selected)?;
        for entity in selected {
            let Some(child) =
                self.ensure_target_child(key, entity, TARGET_GRAPH, false, None, || {
                    Variables::from([
                        (536, Value::Entity(entity)),
                        (SCRIPT_SOURCE, Value::Entity(site.entity)),
                    ])
                })?
            else {
                continue;
            };
            self.machine.queue_entries_from(
                self.graphs,
                Some(child),
                Some(site.entity),
                REAPPLY,
                Variables::from([(92, Value::Entity(entity))]),
                self.machine.now_ms,
            )?;
        }
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    pub(in crate::script_init) fn begin_for_code(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        if !matches!(field(&node.fields, "m_DC897C87"), Json::Null)
            && !matches!(field(&node.fields, "m_DC897C87").as_u64(), Some(0 | 1))
        {
            self.report(
                self.site(key.instance, node),
                Issue::UnsupportedField("for-code notification channel"),
            );
            return Ok(());
        }
        if !field(&node.fields, "m_values").is_null()
            && !field(&node.fields, "m_values")
                .as_array()
                .is_some_and(|rows| rows.iter().all(Json::is_object))
        {
            self.report(
                self.site(key.instance, node),
                Issue::UnsupportedField("for-code value rows"),
            );
            return Ok(());
        }
        self.machine.code_states.push(key);
        self.register_tick(key, self.machine.now_ms);
        self.publish_for_code(key, node)
    }

    pub(in crate::script_init) fn publish_for_code(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let channel = field(&node.fields, "m_DC897C87").as_u64().unwrap_or(1) as usize;
        if channel > 1 {
            return Ok(());
        }
        for row in array_field(&node.fields, "m_values") {
            self.spend("native code value update")?;
            let config = field(row, "m_value");
            if !config.is_null() {
                self.evaluate(key.instance, node, config, None);
            }
            if let Some(identifier) = field(row, "m_identifier")
                .as_str()
                .and_then(parse_guid)
                .filter(|id| *id != 0)
            {
                self.machine.code_pending[channel].push(identifier);
            }
        }
        if channel == 0 {
            self.notify_code(0)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn notify_code(&mut self, channel: usize) -> Result<(), InitFault> {
        let pending = std::mem::take(&mut self.machine.code_pending[channel]);
        let mut selected = Vec::new();
        for identifier in pending {
            for (token, filter) in self.machine.code_observers.clone() {
                self.spend("native code observer selection")?;
                if filter.is_none_or(|filter| filter == identifier) && !selected.contains(&token) {
                    selected.push(token);
                }
            }
        }
        for token in selected {
            self.spend("native code notification")?;
            if !self.world.code_changed(self.machine.snapshot.entity, token) {
                self.report(
                    Site {
                        entity: self.machine.snapshot.entity,
                        instance: 0,
                        graph: 0,
                        node: None,
                    },
                    Issue::UnsupportedField("native code observer"),
                );
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn unregister_for_code(&mut self, key: StateKey) {
        if let Some(index) = self
            .machine
            .code_states
            .iter()
            .position(|registered| *registered == key)
        {
            self.machine.code_states.swap_remove(index);
        }
    }

    pub(in crate::script_init) fn code_value(
        &mut self,
        identifier: u64,
        mut initial: Value,
    ) -> Result<(bool, Value), InitFault> {
        for key in self.machine.code_states.clone() {
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("registered code state");
            for row in array_field(&node.fields, "m_values") {
                self.spend("native code value lookup")?;
                if field(row, "m_identifier").as_str().and_then(parse_guid) == Some(identifier) {
                    let config = field(row, "m_value");
                    let failed_base = field(config, "$").as_str() == Some("STU_B40ACFBA")
                        && script_expr::native_fields(config, &["m_52EA7AB9"]);
                    if !config.is_null() && !failed_base {
                        if let Some(value) = self.evaluate(key.instance, node, config, None) {
                            initial = value;
                        }
                    }
                    return Ok((true, initial));
                }
            }
            initial = Value::Null;
        }
        Ok((false, initial))
    }

    pub(in crate::script_init) fn begin_ai_control_override(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(Value::Entity(target)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_00D49978"),
            Value::Null,
        )?
        else {
            self.report(site, Issue::UnsupportedField("AI control target"));
            return Ok(());
        };
        let priority = field(&node.fields, "m_priority");
        let (priority, prefer_later) = if priority.is_null() {
            (9999.0, false)
        } else {
            match script_expr::stack_priority(priority) {
                Ok(value) => (f64::from(value.level), value.above_equals),
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(());
                }
            }
        };
        let limits = if node.class == "STU_473549CA" {
            let mut limits = [f64::from(std::f32::consts::PI); 2];
            for (index, name) in ["m_49EEBB65", "m_FD4D6C19"].into_iter().enumerate() {
                if !field(&node.fields, name).is_null() {
                    let Some(value) = self.creation_scalar(
                        key.instance,
                        node,
                        field(&node.fields, name),
                        Value::Float(limits[index]),
                    )?
                    else {
                        return Ok(());
                    };
                    limits[index] = script_expr::to_float(&value);
                }
            }
            if limits.iter().any(|value| !value.is_finite()) {
                self.report(site, Issue::UnsupportedField("AI control limits"));
                return Ok(());
            }
            Some(limits)
        } else {
            None
        };
        let record = AiControlOverride {
            target,
            limits,
            priority,
            prefer_later,
        };
        match self.world.ai_control_override(site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI control state").kind =
                    StateKind::AiControlOverride {
                        record: Some(record),
                    }
            }
            Some(false) => (),
            None => self.report(site, Issue::UnsupportedField("AI control component")),
        }
        Ok(())
    }

    pub(in crate::script_init) fn update_ai_control_setting(&mut self, key: StateKey, node: &Node) {
        let id = key.instance;
        let Some(scalar) = self.evaluate(id, node, field(&node.fields, "m_0468C6D4"), Some(key))
        else {
            return;
        };
        let Some(flag) = self.evaluate(id, node, field(&node.fields, "m_D6E83CD4"), Some(key))
        else {
            return;
        };
        let site = self.site(id, node);
        if !self.world.set_ai_control_setting(
            site,
            (script_expr::to_float(&scalar) as f32).max(0.1),
            truthy(&flag),
        ) {
            self.report(
                site,
                Issue::UnsupportedField("AI control setting component"),
            );
        }
    }

    pub(in crate::script_init) fn publish_facing_record(
        &mut self,
        node: &Node,
        site: Site,
        record: AiFacing,
        active: bool,
    ) -> Option<bool> {
        if node.declares_facing_claim() {
            self.world.set_facing_claim(site, record, active)
        } else {
            self.world.ai_facing(site, record, active)
        }
    }

    pub(in crate::script_init) fn update_ai_facing(
        &mut self,
        key: StateKey,
        node: &Node,
        previous: Option<AiFacing>,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let authored = node.declares_facing_claim();
        let (target, priority) = if authored {
            (
                site.entity,
                node.facing_claim_priority()
                    .expect("validated facing claim priority"),
            )
        } else if let Some(previous) = previous {
            (previous.target, previous.priority)
        } else {
            let priority = field(&node.fields, "m_priority");
            if !field(&node.fields, "m_baseEntity").is_null()
                || asset_guid(field(priority, "m_priority")) != Some(0x0c40_0000_0000_0003)
                || !field(priority, "m_resourceKey").is_null()
            {
                self.report(site, Issue::UnsupportedField("AI facing options"));
                return Ok(());
            }
            let Some(Value::Entity(target)) =
                self.evaluate(key.instance, node, field(&node.fields, "m_00D49978"), None)
            else {
                self.report(site, Issue::UnsupportedField("AI facing target"));
                return Ok(());
            };
            (
                target,
                script_expr::stack_priority(priority).expect("validated priority asset"),
            )
        };
        let operand = if authored { "direction" } else { "m_7CCD8B4B" };
        let Some(direction) = self.evaluate(key.instance, node, field(&node.fields, operand), None)
        else {
            if authored {
                self.post(key.instance, Event::Abort(key.raw))?;
            }
            return Ok(());
        };
        let direction = match direction {
            Value::Vector(direction) if direction.iter().all(|axis| axis.is_finite()) => direction,
            _ => {
                self.report(
                    site,
                    Issue::UnsupportedField(if authored {
                        "facing claim direction"
                    } else {
                        "AI facing direction"
                    }),
                );
                if authored {
                    self.post(key.instance, Event::Abort(key.raw))?;
                }
                return Ok(());
            }
        };
        let direction = if authored {
            let scale = direction.into_iter().map(f64::abs).fold(0.0, f64::max);
            if scale == 0.0 {
                self.report(site, Issue::UnsupportedField("facing claim direction"));
                self.post(key.instance, Event::Abort(key.raw))?;
                return Ok(());
            }
            let scaled = direction.map(|axis| axis / scale);
            let magnitude = scaled.iter().map(|axis| axis * axis).sum::<f64>().sqrt();
            scaled.map(|axis| axis / magnitude)
        } else {
            direction
        };
        let record = AiFacing {
            target,
            direction,
            priority,
        };
        match self.publish_facing_record(node, site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI facing").kind =
                    StateKind::AiFacing(Some(record));
                self.register_tick(key, self.machine.now_ms);
            }
            Some(false) => self.post(key.instance, Event::Abort(key.raw))?,
            None => {
                self.report(
                    site,
                    Issue::UnsupportedField(if authored {
                        "facing claim component"
                    } else {
                        "AI facing component"
                    }),
                );
                if authored {
                    self.post(key.instance, Event::Abort(key.raw))?;
                }
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn begin_display_name(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let target = field(&node.fields, "m_14EDDDA7");
        let (name, shape) = match (site.graph, node.pos) {
            (0x0cf3, 21..=25) => (
                0x7105 + u64::from(node.pos - 21),
                script_expr::native_shape(target, "STU_B5A0CAF0", None),
            ),
            (0x0cbc, 20 | 21 | 22 | 27 | 109) | (0x0d1d, 6 | 11 | 73) => {
                let name = match (site.graph, node.pos) {
                    (0x0cbc, 20) => 0x4ccc,
                    (0x0cbc, 21) => 0x4ccd,
                    (0x0cbc, 22) => 0x4cce,
                    (0x0cbc, 27) => 0x4cd3,
                    (0x0cbc, 109) => 0x6abf,
                    (0x0d1d, 6) => 0x5768,
                    (0x0d1d, 11) => 0x5769,
                    _ => 0x5e06,
                };
                (
                    name,
                    script_expr::native_shape(target, "STU_DB742151", Some("m_entity")),
                )
            }
            _ => (0, false),
        };
        let name = 0x0de0_0000_0000_0000 | name;
        if !shape || asset_guid(field(&node.fields, "m_overrideName")) != Some(name) {
            self.report(
                site,
                Issue::UnsupportedField("display name override options"),
            );
            return Ok(());
        }
        let Some(Value::Entity(entity)) = self.evaluate(key.instance, node, target, None) else {
            self.report(
                site,
                Issue::UnsupportedField("display name override target"),
            );
            return Ok(());
        };
        if !script_expr::valid_entity(entity)
            || self.world.entity_component92(site, entity) == Some(false)
        {
            return Ok(());
        }
        if !self.world.set_display_name(site, entity, name, true) {
            self.report(
                site,
                Issue::UnsupportedField("display name override target"),
            );
            return Ok(());
        }
        self.state_mut(key).expect("active display name").kind = StateKind::DisplayName {
            binding: Some((entity, name)),
        };
        Ok(())
    }

    pub(in crate::script_init) fn begin_ai_weapon_override(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(Value::Entity(target)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_00D49978"),
            Value::Null,
        )?
        else {
            self.report(site, Issue::UnsupportedField("AI weapon target"));
            return Ok(());
        };
        let Some(slot) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_D66E7D68"),
            Value::Integer(9999),
        )?
        else {
            return Ok(());
        };
        let Some(Value::Asset(identifier)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_F2810377"),
            Value::Asset(0),
        )?
        else {
            self.report(site, Issue::UnsupportedField("AI weapon identifier"));
            return Ok(());
        };
        let Some(enabled) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_F8585ABF"),
            Value::Null,
        )?
        else {
            return Ok(());
        };
        let priority = field(&node.fields, "m_priority");
        let (priority, prefer_later) = if priority.is_null() {
            (9999.0, false)
        } else {
            let priority = match script_expr::stack_priority(priority) {
                Ok(priority) => priority,
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(());
                }
            };
            (f64::from(priority.level), priority.above_equals)
        };
        let record = AiWeaponOverride {
            target,
            slot: script_expr::to_int(&slot, 9999),
            identifier,
            enabled: truthy(&enabled),
            priority,
            prefer_later,
        };
        match self.world.ai_weapon_override(site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI weapon state").kind =
                    StateKind::AiWeaponOverride {
                        record: Some(record),
                    }
            }
            Some(false) => (),
            None => self.report(site, Issue::UnsupportedField("AI weapon control component")),
        }
        Ok(())
    }

    pub(in crate::script_init) fn check_health_capacity(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let target = self.evaluate(key.instance, node, field(&node.fields, "m_FA3ECC43"), None);
        let amount = self
            .evaluate(key.instance, node, field(&node.fields, "m_amount"), None)
            .and_then(pool_number);
        if target != Some(Value::Entity(site.entity))
            || field(&node.fields, "m_7E8B666C").as_u64() != Some(0)
            || !amount.is_some_and(|amount| {
                amount > 0.0
                    && self.world.body_health(site, Some(PoolKind::Health), true) == Some(amount)
            })
        {
            self.report(
                site,
                Issue::UnsupportedField("authored NPC health capacity"),
            );
        }
        Ok(())
    }

    pub(in crate::script_init) fn begin_health_pool(
        &mut self,
        key: StateKey,
        node: &Node,
        wave_bonus: bool,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let generic_player = self.world.admits_generic_player_runtime(site);
        let target_field = field(&node.fields, "m_FA3ECC43");
        let target_ok = (generic_player && target_field.is_null())
            || matches!(
                self.evaluate(id, node, target_field, None),
                Some(Value::Entity(target)) if target == site.entity
            );
        if !target_ok {
            if !generic_player {
                self.report(site, Issue::UnsupportedField("health pool target"));
            }
            return Ok(());
        }
        if wave_bonus {
            return self.begin_wave_health(key, node, site);
        }
        let Some(maximum) = self
            .evaluate(id, node, field(&node.fields, "m_amount"), Some(key))
            .and_then(pool_number)
        else {
            if !generic_player {
                self.report(site, Issue::UnsupportedField("health pool capacity"));
            }
            return Ok(());
        };
        if generic_player && (!maximum.is_finite() || maximum <= 0.0) {
            return Ok(());
        }
        if let Some(identifier) = named_temporary_shield(node, key.raw) {
            let instance = &self.machine.snapshot.instances[&id];
            let stacked = instance.parent.is_none() && instance.retire_after_frame.is_some();
            if maximum == 0.0 {
                if stacked {
                    self.post(id, Event::Finish(key.raw))?;
                }
                return Ok(());
            }
            let Some(pool) = self
                .world
                .add_temporary_shield(site, identifier, maximum, stacked)
            else {
                if !generic_player {
                    self.report(
                        site,
                        Issue::UnsupportedField("temporary shield registration"),
                    );
                }
                return Ok(());
            };
            self.state_mut(key).expect("active temporary shield").kind =
                StateKind::HealthPool { pool: Some(pool) };
            if stacked {
                self.register_tick(key, self.machine.now_ms);
            }
            return self.publish_health_totals(site);
        }
        let initial = field(&node.fields, "m_BCFDD2E9");
        let current = if initial.is_null() {
            maximum
        } else {
            let Some(current) = self.evaluate(id, node, initial, None).and_then(pool_number) else {
                if !generic_player {
                    self.report(site, Issue::UnsupportedField("health pool initial amount"));
                }
                return Ok(());
            };
            current
        };
        let Some(kind) = pool_kind(field(&node.fields, "m_7E8B666C")) else {
            if !generic_player {
                self.report(site, Issue::UnsupportedField("health pool kind"));
            }
            return Ok(());
        };
        let Some(pool) = self.world.add_health_pool(site, kind, maximum, current) else {
            if !generic_player {
                self.report(site, Issue::UnsupportedField("health pool registration"));
            }
            return Ok(());
        };
        self.state_mut(key).expect("active health pool").kind =
            StateKind::HealthPool { pool: Some(pool) };
        self.register_tick(key, self.machine.now_ms);
        self.publish_health_pool(key, node, current)
    }

    pub(in crate::script_init) fn begin_wave_health(
        &mut self,
        key: StateKey,
        node: &Node,
        site: Site,
    ) -> Result<(), InitFault> {
        const DIFFICULTY: u32 = 9948;
        let amount = field(&node.fields, "m_amount");
        let actual = self.world.entity_variable(GAME_MODE_ENTITY, DIFFICULTY);
        let local = self
            .machine
            .value(VarKey::Instance(key.instance, DIFFICULTY), None);
        let Some(Value::Integer(difficulty @ 0..=3)) = actual else {
            self.report(site, Issue::UnsupportedField("wave health difficulty"));
            return Ok(());
        };
        if Some(local) != actual
            || self
                .world
                .body_health(site, Some(PoolKind::Health), true)
                .is_none_or(|health| health <= 0.0)
        {
            self.report(
                site,
                Issue::UnsupportedField("wave health difficulty or base health"),
            );
            return Ok(());
        }
        let table = array_field(field(amount, "m_814F8EC3"), "m_configVars");
        let Some(amount) = self
            .evaluate(key.instance, node, &table[difficulty as usize], None)
            .and_then(pool_number)
        else {
            self.report(site, Issue::UnsupportedField("wave health amount"));
            return Ok(());
        };
        if amount == 0.0 && node.pos == 43 && difficulty == 0 {
            return Ok(());
        }
        if amount <= 0.0 {
            self.report(site, Issue::UnsupportedField("wave health amount"));
            return Ok(());
        }
        let Some(pool) = self.world.add_wave_health(site, amount) else {
            self.report(site, Issue::UnsupportedField("wave health registration"));
            return Ok(());
        };
        if let Some(pool) = pool {
            self.state_mut(key).expect("active wave health pool").kind =
                StateKind::HealthPool { pool: Some(pool) };
            self.publish_health_totals(site)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn publish_health_pool(
        &mut self,
        key: StateKey,
        node: &Node,
        current: f64,
    ) -> Result<(), InitFault> {
        if !field(&node.fields, "m_7BE02F89").is_null() {
            // The reference exposes the named value through the pool query.
            return self.publish_health_totals(self.site(key.instance, node));
        }
        if let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "m_CEAB19E8")) {
            let output = source_key(key.instance, output);
            let current = Value::Float(current);
            if !same(&self.machine.value(output, None), &current) {
                self.write(output, current)?;
            }
        }
        self.publish_health_totals(self.site(key.instance, node))
    }

    pub(in crate::script_init) fn publish_health_totals(
        &mut self,
        site: Site,
    ) -> Result<(), InitFault> {
        for (variable, maximum) in [
            (OWNER_HEALTH, false),
            (HUD_HEALTH, false),
            (HUD_MAX_HEALTH, true),
        ] {
            if let Some(amount) = self.world.body_health(site, None, maximum) {
                let key = VarKey::Entity(variable);
                let value = Value::Float(amount);
                if !same(&self.machine.value(key, None), &value) {
                    self.write(key, value)?;
                }
            }
        }
        Ok(())
    }

    fn rejects_stack_value(&mut self, key: StateKey, node: &Node, value: &Value) -> bool {
        if node.declares_stack_claim() && matches!(value, Value::Map(_) | Value::IdentifierMap(_)) {
            self.report(
                self.site(key.instance, node),
                Issue::UnsupportedField("map-valued stack claim"),
            );
            true
        } else {
            false
        }
    }

    pub(in crate::script_init) fn begin_stack(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(value) = self.evaluate(
            id,
            node,
            node.stack_value().unwrap_or(&Json::Null),
            Some(key),
        ) else {
            return Ok(());
        };
        if self.rejects_stack_value(key, node, &value) {
            return Ok(());
        }
        let (variable, priority) = if let Some((variable, priority, duration)) = node.stack_claim()
        {
            if let Some(frames) = duration {
                // The top subgraph can synchronously end its owner.
                self.post_after(
                    id,
                    u64::from(frames.get()) * FRAME_MS,
                    Event::Finish(key.raw),
                )?;
            }
            (variable, priority)
        } else {
            let variable = match script_expr::lvalue(field(&node.fields, "m_out_Var")) {
                Ok(Some(variable)) => variable,
                Ok(None) => return Ok(()),
                Err(error) => {
                    self.report(self.site(id, node), Issue::Expression(error));
                    return Ok(());
                }
            };
            let priority = match script_expr::stack_priority(field(&node.fields, "m_priority")) {
                Ok(priority) => priority,
                Err(error) => {
                    self.report(self.site(id, node), Issue::Expression(error));
                    return Ok(());
                }
            };
            (variable, priority)
        };
        let target = source_key(id, variable);
        self.state_mut(key).expect("active stack").linked_variable = Some(target);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority,
                output: LinkValue::Fixed(value),
                stack: true,
            },
            depth,
        )
    }

    pub(in crate::script_init) fn begin_cosmetic(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let target = match script_expr::lvalue(field(&node.fields, "m_1DEF59FA")) {
            Ok(Some(target)) => source_key(key.instance, target),
            Ok(None) => return Ok(()),
            Err(error) => {
                self.report(self.site(key.instance, node), Issue::Expression(error));
                return Ok(());
            }
        };
        self.state_mut(key)
            .expect("active cosmetic")
            .linked_variable = Some(target);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority: LINK_PRIORITY,
                stack: false,
                output: LinkValue::Fixed(Value::Handle {
                    kind: 1,
                    instance: u32::from(key.instance),
                    state: key.raw,
                    extra: 0,
                }),
            },
            depth,
        )
    }

    pub(in crate::script_init) fn begin_chase(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let target = match script_expr::lvalue(field(&node.fields, "m_out_Var")) {
            Ok(target) => target.map(|target| source_key(id, target)),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        let initial = field(&node.fields, "m_EB0FC261");
        let current = if initial.is_null() {
            target.map_or(Value::Null, |target| self.machine.value(target, None))
        } else {
            let Some(value) = self.evaluate(id, node, initial, None) else {
                return Ok(());
            };
            value
        };
        let current = chase_value(&current);
        let remaining_ms = if configured(node, "m_5BEFF040") {
            let Some(value) = self.evaluate(id, node, field(&node.fields, "m_5BEFF040"), None)
            else {
                return Ok(());
            };
            Some((script_expr::to_float(&value) * 1000.0) as i64)
        } else {
            None
        };
        let now = self.machine.now_ms;
        let state = self.state_mut(key).expect("active chase");
        state.chase = Some(ChasePayload {
            current: current.clone(),
            reached: false,
            remaining_ms,
            last_ms: now,
        });
        state.linked_variable = target;
        if let Some(target) = target {
            let old = self.machine.value(target, None);
            self.set_base(target, current);
            self.insert_link(
                target,
                VariableLink {
                    state: key,
                    priority: LINK_PRIORITY,
                    output: LinkValue::Chase,
                    stack: false,
                },
                depth,
            )?;
            self.notify_value(target, &old, None)?;
        }
        self.register_tick(key, now);
        Ok(())
    }

    pub(in crate::script_init) fn tick_chase(&mut self, key: StateKey) -> Result<(), InitFault> {
        let id = key.instance;
        let now = self.machine.now_ms;
        let Some(state) = self.state_mut(key) else {
            return Ok(());
        };
        let target = state.linked_variable;
        let Some(chase) = state.chase.as_mut() else {
            return Ok(());
        };
        let elapsed = (now - chase.last_ms) as i64;
        chase.last_ms = now;
        if elapsed == 0 || chase.reached {
            return Ok(());
        }
        let mut next = chase.clone();
        let before = target.map(|target| self.machine.value(target, None));
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("chase state");
        let Some(destination) = self.chase_destination(key, node) else {
            if field(&node.fields, "m_destination").is_null()
                && (configured(node, "m_destinationBone")
                    || configured(node, "m_destinationObject"))
            {
                return self.post(id, Event::Abort(key.raw));
            }
            return Ok(());
        };
        if let Some(remaining) = next.remaining_ms {
            if remaining <= elapsed {
                next.current = destination;
                next.reached = true;
            } else {
                let share = f64::from((elapsed as f64 / remaining as f64) as f32);
                next.current = chase_lerp(next.current, destination, share);
                next.remaining_ms = Some(remaining - elapsed);
            }
        } else {
            let rate = field(&node.fields, "m_9B0385B4");
            let rate = if rate.is_null() {
                1.0
            } else {
                let Some(value) = self.evaluate(id, node, rate, Some(key)) else {
                    return Ok(());
                };
                script_expr::to_float(&value)
            };
            if rate > 0.0 {
                let per_ms = (rate * f64::from(0.001_f32)) as f32;
                let distance = (f64::from(per_ms) * elapsed as f64) as f32;
                let step = f64::from((f64::from(distance) + script_expr::EPSILON) as f32);
                (next.current, next.reached) = chase_step(next.current, destination, step);
            }
        }
        let reached = next.reached;
        self.state_mut(key).expect("active chase").chase = Some(next);
        if let (Some(target), Some(before)) = (target, before) {
            self.notify_value(target, &before, None)?
        }
        if reached {
            self.post(id, Event::CheckTransitions(key.raw))?;
            if !configured(node, "m_CB671004") {
                self.post(id, Event::Finish(key.raw))?
            }
        }
        Ok(())
    }

    fn chase_destination(&mut self, key: StateKey, node: &Node) -> Option<Value> {
        let object = field(&node.fields, "m_destinationObject");
        let bone = field(&node.fields, "m_destinationBone");
        let offset = field(&node.fields, "m_destination");
        if object.is_null() && bone.is_null() {
            return self
                .evaluate(key.instance, node, offset, Some(key))
                .map(|v| chase_value(&v));
        }
        let Value::Entity(entity) = self.evaluate(key.instance, node, object, Some(key))? else {
            return None;
        };
        if !script_expr::valid_entity(entity) {
            return None;
        }
        let bone = if bone.is_null() {
            0
        } else if let Value::Asset(id) = self.evaluate(key.instance, node, bone, Some(key))? {
            id
        } else {
            return None;
        };
        let local = if offset.is_null() {
            [0.0; 3]
        } else if let Value::Vector(v) = self.evaluate(key.instance, node, offset, Some(key))? {
            v
        } else {
            return None;
        };
        // Both endpoints of the interpolation must use the same world frame.
        // An unobserved bone leaves the last sample in place until the next tick.
        self.world
            .body_bone_point(entity, bone, local)
            .map(Value::Vector)
    }

    pub(in crate::script_init) fn chase_write(
        &mut self,
        key: StateKey,
        target: VarKey,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("chase state");
        match field(&node.fields, "m_1E10B2B1").as_i64().unwrap_or(0) {
            0 => self.unlink_single_output(key, self.output_write_depth)?,
            1 => {
                let value = chase_value(&stored(&self.machine.snapshot, target));
                if let Some(chase) = self.state_mut(key).and_then(|state| state.chase.as_mut()) {
                    chase.current = value;
                }
            }
            _ => (),
        }
        Ok(())
    }

    pub(in crate::script_init) fn finish_chase(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(state) = self.state_mut(key) else {
            return Ok(());
        };
        let Some(target) = state.linked_variable.take() else {
            return Ok(());
        };
        let current = state.chase.as_ref().map(|chase| chase.current.clone());
        self.remove_link(key, target, current, depth)
    }

    pub(in crate::script_init) fn volley_config(
        &mut self,
        id: u16,
        node: &Node,
        name: &str,
        default: Value,
    ) -> Option<Value> {
        let config = field(&node.fields, name);
        if config.is_null() {
            Some(default)
        } else {
            self.evaluate(id, node, config, None)
        }
    }

    pub(in crate::script_init) fn begin_volley(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let names = [
            "m_A17BE89B",
            "m_A55BC904",
            "m_5F40D9D3",
            "m_numShotsPerSecond",
        ];
        let mut config = [
            Value::Integer(1),
            Value::Integer(1),
            Value::Integer(1),
            Value::Float(1.0),
        ];
        for (value, name) in config.iter_mut().zip(names) {
            let Some(found) = self.volley_config(id, node, name, value.clone()) else {
                return Ok(());
            };
            *value = found;
        }
        let [max, min, per, rate] = config;
        let (Ok(maximum), Ok(minimum)) = (
            u32::try_from(script_expr::to_int(&max, 0).max(0)),
            u32::try_from(script_expr::to_int(&min, 0).max(0)),
        ) else {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("weapon volley shot count"),
            );
            return Ok(());
        };
        let per_shot = script_expr::to_int(&per, 0).max(0);
        let rate = if rate == Value::Null {
            1.0
        } else {
            script_expr::to_float(&rate).max(1e-5)
        };
        if !rate.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("weapon volley rate"),
            );
            return Ok(());
        }
        let mut outputs = [None; 3];
        for (output, name) in outputs
            .iter_mut()
            .zip(["m_24E351A4", "m_17770E7E", "m_3FFC9EB9"])
        {
            let cfg = field(&node.fields, name);
            if cfg.is_null() {
                continue;
            }
            *output = match script_expr::lvalue(cfg) {
                Ok(Some(var)) => Some(var),
                Ok(None) => {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("weapon volley output"),
                    );
                    return Ok(());
                }
                Err(error) => {
                    self.report(self.site(id, node), Issue::Expression(error));
                    return Ok(());
                }
            };
        }
        let [ammo_var, _, _] = outputs;
        let outputs = outputs.map(|output| output.map(|var| source_key(id, var)));
        for (index, output) in outputs.iter().enumerate() {
            if output.is_some() && outputs[..index].contains(output) {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("duplicate weapon volley output"),
                );
                return Ok(());
            }
        }
        let ammo0 = outputs[0].map_or_else(
            || i64::from(maximum).saturating_mul(per_shot),
            |var| script_expr::to_int(&self.machine.value(var, None), 0).max(0),
        );
        let spread = outputs[2].map_or(0.0, |var| {
            script_expr::to_float(&self.machine.value(var, None)).clamp(0.0, 1.0)
        });
        if !spread.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("weapon volley spread"),
            );
            return Ok(());
        }
        let planned = if per_shot == 0 {
            maximum
        } else {
            (ammo0 / per_shot).min(i64::from(maximum)) as u32
        };
        let launch = field(&node.fields, "m_5EC1EB38");
        let (spread_ramp, spread_delay_ms) = if launch["$"] == "STU_239B571E" {
            let delay = field(launch, "m_23C72C36");
            let delay_ms = if delay.is_null() {
                Some(0)
            } else {
                self.evaluate(id, node, delay, None)
                    .map(|value| script_expr::to_float(&value))
                    .filter(|secs| secs.is_finite() && *secs >= 0.0)
                    .map(|secs| (secs * 1000.0).round() as u64)
            };
            let config = field(launch, "m_AC20B1C1");
            let ramp = if config.is_null() {
                Some(0.0)
            } else {
                self.evaluate(id, node, config, None)
                    .map(|value| script_expr::to_float(&value) as f32)
                    .filter(|ramp| ramp.is_finite() && *ramp >= 0.0)
            };
            match (ramp, delay_ms) {
                (Some(ramp), Some(delay_ms)) => (Some(ramp), delay_ms),
                _ => (None, 0),
            }
        } else {
            (None, 0)
        };
        let start_ms = self.machine.now_ms;
        self.state_mut(key).expect("active volley").kind = StateKind::WeaponVolley {
            volleys: 1,
            offset_ms: 0,
            ammo0,
            ammo_var,
        };
        self.machine.volleys.insert(
            key,
            Volley {
                start_ms,
                ammo0,
                per_shot,
                rate,
                spread,
                last_spread: spread,
                spread_ramp,
                spread_delay_ms,
                planned,
                reported: 0,
                unposed_rounds: 0,
                outputs,
            },
        );
        for (slot, (target, base)) in outputs
            .into_iter()
            .zip([
                Value::Integer(ammo0),
                Value::Float(rate),
                Value::Float(spread),
            ])
            .enumerate()
        {
            let Some(target) = target else { continue };
            let before = self.machine.value(target, None);
            self.set_base(target, base);
            self.insert_link(
                target,
                VariableLink {
                    state: key,
                    priority: LINK_PRIORITY,
                    stack: false,
                    output: LinkValue::Volley(slot as u8),
                },
                depth,
            )?;
            self.notify_value(target, &before, None)?;
        }
        if per_shot == 0 || i64::from(minimum) < ammo0 / per_shot {
            self.post_after(
                id,
                shot_delay(minimum.saturating_sub(1), rate),
                Event::Timer(key.raw, 1),
            )?;
        }
        self.post_after(
            id,
            shot_delay(planned.saturating_sub(1), rate),
            Event::Timer(key.raw, 2),
        )
    }

    pub(in crate::script_init) fn report_active_volleys(&mut self) -> Result<(), InitFault> {
        let active: Vec<_> = self
            .machine
            .snapshot
            .instances
            .iter()
            .flat_map(|(&id, instance)| {
                instance.states.iter().filter_map(move |(&raw, state)| {
                    (state.active && matches!(state.kind, StateKind::WeaponVolley { .. }))
                        .then_some(StateKey { instance: id, raw })
                })
            })
            .collect();
        for key in active {
            self.report_volley(key)?
        }
        Ok(())
    }

    pub(in crate::script_init) fn report_volley(&mut self, key: StateKey) -> Result<(), InitFault> {
        if self.machine.owner_dead {
            return Ok(());
        }
        let Some(activation) = self.state(key).map(|state| state.activation) else {
            return Ok(());
        };
        let instance = &self.machine.snapshot.instances[&key.instance];
        let Some(volley) = self.machine.volleys.get_mut(&key) else {
            return Ok(());
        };
        if !self.machine.permit_weapon_shots {
            // Locate the first round past the new clock without walking an
            // arbitrarily long, unposed interval one shot at a time.
            let mut first = volley.reported;
            let mut end = volley.planned;
            while first < end {
                let middle = first + (end - first) / 2;
                let due = volley
                    .start_ms
                    .saturating_add(shot_delay(middle, volley.rate));
                if due <= self.machine.now_ms {
                    first = middle + 1;
                } else {
                    end = middle;
                }
            }
            volley.unposed_rounds += first - volley.reported;
            volley.reported = first;
            return Ok(());
        }
        let start_ms = volley.start_ms;
        let rate = volley.rate;
        let graph = instance.graph;
        let mut ordinal = volley.reported;
        let planned = volley.planned;
        let mut presentation = None;
        let spread = (volley.spread, volley.spread_ramp, volley.spread_delay_ms);
        while ordinal < planned {
            let time_ms = start_ms.saturating_add(shot_delay(ordinal, rate));
            if time_ms > self.machine.now_ms {
                break;
            }
            if self.machine.shots.len() == MAX_PENDING_SHOTS {
                return Err(InitFault::PendingShots);
            }
            let (presentation, presentation_states) =
                presentation.get_or_insert_with(|| self.volley_presentation(key));
            self.machine.shots.push(ReportedShot {
                event: ShotEvent {
                    entity: self.machine.snapshot.entity,
                    instance: key.instance,
                    graph,
                    raw_state: key.raw,
                    frame: (time_ms / FRAME_MS) as u32,
                    time_ms,
                    activation,
                    activation_ms: start_ms,
                    ordinal,
                },
                presentation: presentation.clone(),
                presentation_states: presentation_states.clone(),
                normalized_spread: Volley::normalized_shot_spread(
                    spread.0,
                    spread.1,
                    spread.2,
                    time_ms - start_ms,
                    rate,
                ),
            });
            ordinal += 1;
        }
        let (spread_change, spread_var) = {
            let volley = self.machine.volleys.get_mut(&key).expect("reported volley");
            volley.reported = ordinal;
            let current = volley.spread_at(self.machine.now_ms);
            if volley.spread_ramp.is_some() && (current - volley.last_spread).abs() > 1e-7 {
                let before = volley.last_spread;
                volley.last_spread = current;
                (Some(before), volley.outputs[2])
            } else {
                (None, None)
            }
        };
        if let (Some(before), Some(var_key)) = (spread_change, spread_var) {
            self.notify_value(var_key, &Value::Float(before), None)?;
        }
        Ok(())
    }

    fn volley_presentation(&self, key: StateKey) -> (Vec<(Variable, Value)>, Vec<u16>) {
        let instance = &self.machine.snapshot.instances[&key.instance];
        if instance.is_server_only() {
            return (Vec::new(), Vec::new());
        }
        let graph = self.graph(key.instance);
        let Some(volley_node) = graph.state(usize::from(key.raw)) else {
            return (Vec::new(), Vec::new());
        };
        let is_synced = |variable: &Variable| {
            let scope = u8::from(variable.scope == Scope::Entity);
            graph
                .sync_vars
                .iter()
                .any(|sync| sync.0 == Some(variable.id) && sync.1 == scope)
                && (variable.scope == Scope::Entity
                    || graph
                        .owner_only_instance_vars()
                        .binary_search(&variable.id)
                        .is_err())
                && !graph.server_only_variables().any(|id| id == variable.id)
        };
        let mut presented = Vec::new();
        let mut presented_states = Vec::new();
        let mut seen = std::collections::BTreeSet::new();
        let mut push_var = |variable: Variable, value: Value| {
            if seen.insert((matches!(variable.scope, Scope::Entity), variable.id)) {
                presented.push((variable, value));
            }
        };
        if let Some(plug) = volley_node.field("m_subgraphPlug") {
            let children = array_field(plug, SUBGRAPH_STATES);
            if !children.is_empty() {
                for (&raw, state) in &instance.states {
                    if !state.active {
                        continue;
                    }
                    let Some(node) = graph.state(usize::from(raw)) else {
                        continue;
                    };
                    if raw_indices(children).any(|child| child == raw)
                        && node.is_networked()
                        && graph.remote_positions().contains(&node.pos)
                        && matches!(
                            node.class.as_str(),
                            "STUStatescriptStateStack" | "STUStatescriptStateDataFlowMapping"
                        )
                    {
                        presented_states.push(raw);
                    }
                    let var_field = match node.class.as_str() {
                        "STUStatescriptStateDataFlowMapping"
                            if node.is_networked()
                                && graph.remote_positions().contains(&node.pos) =>
                        {
                            "m_answer"
                        }
                        "STUStatescriptStateStack"
                            if node.is_networked()
                                && graph.remote_positions().contains(&node.pos) =>
                        {
                            "m_out_Var"
                        }
                        _ => continue,
                    };
                    let Ok(Some(variable)) = script_expr::lvalue(field(&node.fields, var_field))
                    else {
                        continue;
                    };
                    if !is_synced(&variable) {
                        continue;
                    }
                    let source = source_key(key.instance, variable);
                    let Some(top) = self.machine.links.get(&source).and_then(|list| list.last())
                    else {
                        continue;
                    };
                    if top.state.instance != key.instance
                        || !raw_indices(children).any(|child| child == top.state.raw)
                    {
                        continue;
                    }
                    push_var(variable, self.machine.value(source, None));
                }
            }
        }
        if let Some(sync_vars) = volley_node.frame_action_variables() {
            for variable in sync_vars {
                if !is_synced(&variable) {
                    continue;
                }
                let value = self.machine.value(source_key(key.instance, variable), None);
                if !matches!(value, Value::Null) {
                    push_var(variable, value);
                }
            }
        }
        (presented, presented_states)
    }

    pub(in crate::script_init) fn finish_volley(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        self.report_volley(key)?;
        let Some(volley) = self.machine.volleys.get(&key) else {
            return Ok(());
        };
        let outputs = volley.outputs;
        let at = self.machine.now_ms.saturating_add(1);
        let values =
            [0, 1, 2].map(|slot| volley.output(slot, at, self.machine.permit_weapon_shots));
        for (target, value) in outputs.into_iter().zip(values) {
            if let Some(target) = target {
                self.remove_link(key, target, Some(value), depth)?
            }
        }
        self.machine.volleys.remove(&key);
        Ok(())
    }

    pub(in crate::script_init) fn insert_link(
        &mut self,
        target: VarKey,
        link: VariableLink,
        depth: usize,
    ) -> Result<(), InitFault> {
        let old = self.machine.value(target, None);
        self.machine.snapshot_dirty = true;
        match target {
            VarKey::Entity(id) => self
                .machine
                .snapshot
                .variables
                .entry(id)
                .or_insert(Value::Null),
            VarKey::Instance(instance, id) => self.machine.snapshot.instances[&instance]
                .variables
                .entry(id)
                .or_insert(Value::Null),
        };
        let entries = self.machine.links.entry(target).or_default();
        entries.retain(|entry| entry.state != link.state);
        let at = entries
            .iter()
            .position(|entry| {
                entry.priority.level > link.priority.level
                    || (entry.priority.level == link.priority.level && !link.priority.above_equals)
            })
            .unwrap_or(entries.len());
        entries.insert(at, link);
        self.notify_value(target, &old, None)?;
        self.sync_stack(target, depth)
    }

    pub(in crate::script_init) fn sync_stack(
        &mut self,
        variable: VarKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        let Some(entries) = self.machine.links.get(&variable) else {
            return Ok(());
        };
        let states: Vec<_> = entries
            .iter()
            .filter(|entry| entry.stack)
            .map(|entry| entry.state)
            .collect();
        for key in states {
            self.spend("stack position change")?;
            let Some(state) = self
                .state(key)
                .filter(|state| state.active && !state.ending)
            else {
                continue;
            };
            let top = self
                .machine
                .links
                .get(&variable)
                .and_then(|entries| entries.last());
            if let StateKind::Ability { flags } = state.kind {
                if flags & ABILITY_PUSHED != 0 && state.ability_stack == Some(variable) {
                    let node = self
                        .graph(key.instance)
                        .state(usize::from(key.raw))
                        .expect("ability state");
                    if top.is_some_and(|entry| {
                        entry.state == key && matches!(entry.output, LinkValue::AbilityStack)
                    }) {
                        if flags & ABILITY_RUNNING == 0 {
                            self.start_ability(key, node, depth + 1)?;
                        }
                    } else {
                        self.stop_ability(key, node, depth + 1)?;
                    }
                }
                continue;
            }
            if state.linked_variable != Some(variable) {
                continue;
            }
            let top = top.is_some_and(|entry| entry.state == key);
            let kind = StateKind::Stack { top, under: !top };
            if state.kind == kind {
                continue;
            }
            self.state_mut(key).expect("active stack").kind = kind;
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("stack state");
            let (old, next) = if top {
                ("m_underSubgraphPlug", "m_topSubgraphPlug")
            } else {
                ("m_topSubgraphPlug", "m_underSubgraphPlug")
            };
            self.exit_subgraph(key.instance, node, old, depth + 1)?;
            self.follow(key.instance, node, next, Some(key), depth)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn unlink_single_output(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(target) = self
            .state_mut(key)
            .and_then(|state| state.linked_variable.take())
        else {
            return Ok(());
        };
        self.remove_link(key, target, None, depth)
    }

    pub(in crate::script_init) fn remove_link(
        &mut self,
        key: StateKey,
        target: VarKey,
        bake: Option<Value>,
        depth: usize,
    ) -> Result<(), InitFault> {
        self.unlink_output(key, target, bake)?;
        self.sync_stack(target, depth)
    }

    pub(in crate::script_init) fn unlink_output(
        &mut self,
        key: StateKey,
        target: VarKey,
        bake: Option<Value>,
    ) -> Result<(), InitFault> {
        let old = self.machine.value(target, None);
        if let Some(value) = bake {
            self.set_base(target, value);
        }
        if let Some(entries) = self.machine.links.get_mut(&target) {
            entries.retain(|entry| entry.state != key);
            if entries.is_empty() {
                self.machine.links.remove(&target);
            }
        }
        self.notify_value(target, &old, None)
    }

    pub(in crate::script_init) fn update_stack(&mut self, key: StateKey) -> Result<(), InitFault> {
        let Some(target) = self.state(key).and_then(|state| state.linked_variable) else {
            return Ok(());
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("stack state");
        let Some(value) = node.stack_value().filter(|config| !config.is_null()) else {
            return Ok(());
        };
        let Some(value) = self.evaluate(id, node, value, Some(key)) else {
            return Ok(());
        };
        if self.rejects_stack_value(key, node, &value) {
            return Ok(());
        }
        let old = self.machine.value(target, None);
        if let Some(LinkValue::Fixed(current)) = self
            .machine
            .links
            .get_mut(&target)
            .and_then(|entries| entries.iter_mut().find(|entry| entry.state == key))
            .map(|entry| &mut entry.output)
        {
            if !same(current, &value) {
                *current = value;
                self.notify_value(target, &old, Some(key))?;
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_pet(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let Some(Value::Asset(definition)) = self.evaluate(
            id,
            node,
            field(&node.fields, "m_entityDefinition"),
            Some(key),
        ) else {
            return Ok(());
        };
        if definition >> 48 != 0x0400 || definition & 0x0000_ffff_ffff_ffff == 0 {
            return Ok(());
        }
        let bone_attached = !field(&node.fields, "m_888BCD8B").is_null();
        // `m_AA26E7DD` names the owner/subject entity (e.g. Mei Ice Wall `0x05FE:17`
        // passes `self` with `m_888BCD8B = null`), while `m_888BCD8B` specifies an actual
        // skeleton bone attachment (e.g. Reinhardt Barrier `0x0095:47`). Only parent the
        // pet onto the owner's movement transform when bone-attached!
        let attach = if bone_attached {
            match self.evaluate(id, node, field(&node.fields, "m_AA26E7DD"), Some(key)) {
                Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => Some(entity),
                _ => None,
            }
        } else {
            None
        };
        let position = match self.evaluate(id, node, field(&node.fields, "m_position"), Some(key)) {
            Some(Value::Vector(pos)) if pos.iter().all(|c| c.is_finite()) => Some(pos),
            Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => {
                self.world.position(entity)
            }
            _ => None,
        };
        let possessable = matches!(
            self.evaluate(id, node, field(&node.fields, "m_E5312022"), Some(key)),
            Some(Value::Bool(true)) | Some(Value::Integer(1))
        );
        let Some(pet_id) = self.world.spawn_pet(
            site,
            PetSpawn {
                definition,
                attach,
                position,
                bone_attached,
                possessable,
            },
        ) else {
            return Ok(());
        };
        if let Some(state) = self.state_mut(key) {
            state.kind = StateKind::Pet {
                entity: Some(pet_id),
            };
        }
        if let Ok(Some(target)) = script_expr::lvalue(field(&node.fields, "m_B3019338")) {
            self.write(source_key(id, target), Value::Entity(pet_id))?;
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn end_pet(
        &mut self,
        key: StateKey,
        node: &Node,
        entity: u32,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        self.world.despawn_pet(site, entity);
        if let Ok(Some(target)) = script_expr::lvalue(field(&node.fields, "m_B3019338")) {
            let var_key = source_key(id, target);
            if same(&self.machine.value(var_key, None), &Value::Entity(entity)) {
                self.write(var_key, Value::Null)?;
            }
        }
        Ok(())
    }

    // Publish on activation, dependency changes and explicit polling, rather
    // than rerolling random-valued rows whenever a mover reads its properties.
    pub(in crate::script_init) fn refresh_movement_modifier(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        if let Some((factor, _)) = node.movement_scale() {
            self.spend("movement modifier")?;
            self.machine.movement_modifiers.insert(
                ModifierKey {
                    instance: id,
                    state: key.raw,
                    slot: 0,
                },
                Modifier {
                    index: SPEED_SLOT,
                    operation: Operation::Multiply,
                    value: narrow(factor),
                    priority: 0.0,
                },
            );
            return Ok(());
        }
        let mut modifiers = BTreeMap::new();
        for (slot, config) in array_field(&node.fields, "m_BC5E91CF").iter().enumerate() {
            self.spend("movement modifier")?;
            let shape = config.get("$").and_then(Json::as_str);
            let (operation, default) = match shape {
                Some("STU_4CB0950F") => (Operation::Add, 0.0),
                Some("STU_D8CC6B10") => (Operation::Multiply, 1.0),
                Some("STU_56DEE1BB") => (Operation::Set, 0.0),
                Some("STU_C61711C8") => (Operation::Minimum, f64::from(f32::MAX)),
                Some("STU_F197CFBF") => (Operation::Maximum, f64::from(f32::MIN_POSITIVE)),
                Some("STU_09F9B1FE")
                    if self
                        .world
                        .admits_generic_player_runtime(self.site(id, node)) =>
                {
                    if let Some(value) =
                        self.evaluate(id, node, field(config, "m_9B7A63EA"), Some(key))
                    {
                        let mode = config.get("m_CFEC5F76").and_then(Json::as_u64).unwrap_or(0);
                        let op = match (mode, value) {
                            (0, Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => {
                                Some(VelocityOp::SetVector(v))
                            }
                            (0, val @ (Value::Float(_) | Value::Integer(_))) => {
                                let speed = script_expr::to_float(&val);
                                if speed.is_finite() {
                                    Some(if speed == 0.0 {
                                        VelocityOp::SetVector([0.0; 3])
                                    } else {
                                        VelocityOp::SetSpeed {
                                            axis: VelocityAxis::All,
                                            value: speed,
                                        }
                                    })
                                } else {
                                    None
                                }
                            }
                            (1, Value::Vector([vx, _, vz])) if vx.is_finite() && vz.is_finite() => {
                                Some(VelocityOp::SetHorizontalVector([vx, vz]))
                            }
                            (1, val @ (Value::Float(_) | Value::Integer(_))) => {
                                let speed = script_expr::to_float(&val);
                                if speed.is_finite() {
                                    Some(if speed == 0.0 {
                                        VelocityOp::SetHorizontalVector([0.0, 0.0])
                                    } else {
                                        VelocityOp::SetSpeed {
                                            axis: VelocityAxis::Horizontal,
                                            value: speed,
                                        }
                                    })
                                } else {
                                    None
                                }
                            }
                            (2, Value::Vector([_, vy, _])) if vy.is_finite() => {
                                Some(VelocityOp::SetSpeed {
                                    axis: VelocityAxis::Vertical,
                                    value: vy,
                                })
                            }
                            (2, val @ (Value::Float(_) | Value::Integer(_))) => {
                                let speed = script_expr::to_float(&val);
                                speed.is_finite().then_some(VelocityOp::SetSpeed {
                                    axis: VelocityAxis::Vertical,
                                    value: speed,
                                })
                            }
                            _ => None,
                        };
                        if let Some(op) = op {
                            self.world.apply_velocity_ops(
                                self.site(id, node),
                                self.machine.frame,
                                &[op],
                            );
                        }
                    }
                    continue;
                }
                Some("STU_ED2B22BD")
                    if self
                        .world
                        .admits_generic_player_runtime(self.site(id, node)) =>
                {
                    if let Some(component) = config.get("m_9B7A63EA").filter(|c| {
                        c.get("$").and_then(Json::as_str) == Some("STUCharacterMoverComponent")
                    }) {
                        // Retail full-form override (e.g. Wrecking Ball Roll `1203:STU_316CFEF2_1`):
                        // swap the entire MoverSettings profile while this state is active instead
                        // of approximating with partial ratios.
                        if let Some(profile) =
                            crate::script_modifiers::MoverProfile::from_stu_component(component)
                        {
                            self.machine.mover_overrides.insert(key, profile);
                            continue;
                        }
                        let priority = self
                            .movement_number(key, node, field(config, "m_priority"), 0.0)
                            .unwrap_or(0.0);
                        let base_slot = 1000usize.saturating_add(slot.saturating_mul(8));
                        let mut push = |sub: usize, index: u8, operation: Operation, value: f64| {
                            if value.is_finite() {
                                modifiers.insert(
                                    ModifierKey {
                                        instance: id,
                                        state: key.raw,
                                        slot: base_slot + sub,
                                    },
                                    Modifier {
                                        index,
                                        operation,
                                        value: narrow(value),
                                        priority,
                                    },
                                );
                            }
                        };
                        if let Some(v) = component
                            .get("m_CFD3E635")
                            .or_else(|| component.get("m_19452F15"))
                            .and_then(Json::as_f64)
                        {
                            if v.is_finite() && v > 0.0 && (v - 5.5).abs() > 1e-4 {
                                push(0, SPEED_SLOT, Operation::Multiply, v / 5.5);
                            }
                        }
                        if let Some(v) = component.get("m_DAA0618B").and_then(Json::as_f64) {
                            if v.is_finite() && v >= 0.0 && (v - 35.0).abs() > 1e-4 {
                                push(1, 1, Operation::Multiply, v / 35.0);
                            }
                        }
                        if let Some(v) = component.get("m_E631BA84").and_then(Json::as_f64) {
                            if v.is_finite() && v >= 0.0 && (v - 3.5).abs() > 1e-4 {
                                push(2, 2, Operation::Multiply, v / 3.5);
                            }
                        }
                        if let Some(v) = component.get("m_EC289987").and_then(Json::as_f64) {
                            if v.is_finite() && v >= 0.0 && (v - 6.0).abs() > 1e-4 {
                                push(3, 10, Operation::Set, v);
                            }
                        }
                        if let Some(v) = component.get("m_C2C40969").and_then(Json::as_f64) {
                            if v.is_finite() && (v - 1.75).abs() > 1e-4 {
                                push(4, 11, Operation::Multiply, v / 1.75);
                            }
                        }
                        if let Some(v) = component.get("m_1FF06061").and_then(Json::as_f64) {
                            if v == 0.0 {
                                push(5, 22, Operation::Set, 0.0);
                            }
                        }
                    }
                    continue;
                }
                _ => {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("movement modifier operation"),
                    );
                    continue;
                }
            };
            let index_field = config.get("m_A9561CA0");
            let Some(index) = (if index_field.is_none_or(Json::is_null) {
                Some(SPEED_SLOT)
            } else {
                index_field
                    .and_then(Json::as_u64)
                    .and_then(|value| u8::try_from(value).ok())
            }) else {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("movement modifier index"),
                );
                continue;
            };
            let Some(value) = self.movement_number(key, node, field(config, "m_9B7A63EA"), default)
            else {
                continue;
            };
            let Some(priority) = self.movement_number(key, node, field(config, "m_priority"), 0.0)
            else {
                continue;
            };
            modifiers.insert(
                ModifierKey {
                    instance: id,
                    state: key.raw,
                    slot,
                },
                Modifier {
                    index,
                    operation,
                    value,
                    priority,
                },
            );
        }
        self.machine
            .movement_modifiers
            .retain(|entry, _| entry.instance != id || entry.state != key.raw);
        self.machine.movement_modifiers.extend(modifiers);
        Ok(())
    }

    pub(in crate::script_init) fn movement_number(
        &mut self,
        key: StateKey,
        node: &Node,
        config: &Json,
        default: f64,
    ) -> Option<f64> {
        if config.is_null() {
            return Some(default);
        }
        let value = self.evaluate(key.instance, node, config, Some(key))?;
        Some(match value {
            Value::Null => 0.0,
            Value::Bool(_) | Value::Integer(_) | Value::Float(_) => {
                f64::from(script_expr::to_float(&value) as f32)
            }
            _ => default,
        })
    }

    pub(in crate::script_init) fn register_tick(&mut self, key: StateKey, start_ms: u64) {
        self.machine
            .ticking
            .entry(key.instance)
            .or_default()
            .entry(key.raw)
            .or_insert(start_ms);
    }

    pub(in crate::script_init) fn begin_shockwave(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let immediate = self
            .evaluate(key.instance, node, field(&node.fields, "m_3418CEA4"), None)
            .is_some_and(|value| truthy(&value));
        let has_target = script_expr::lvalue(field(&node.fields, "m_8A2905A0"))
            .ok()
            .flatten()
            .is_some();
        if has_target
            && !immediate
            && node
                .fields
                .get("m_04F3240D")
                .and_then(|v| v.get("m_value"))
                .and_then(|v| v.as_i64())
                == Some(1)
            && !field(&node.fields, "m_05BE62DD").is_null()
            && !field(&node.fields, "m_D59188D5").is_null()
        {
            return self.begin_ground_wave(key, node);
        }
        if ((site.graph, node.pos) == (0x0B27, 95)
            || self.world.admits_generic_player_runtime(site))
            && has_target
            && field(&node.fields, "m_04F3240D").is_null()
            && immediate
            && self.collect_shockwave(key, node, 0.0)?
        {
            if let Some(state) = self.state_mut(key) {
                state.kind = StateKind::Shockwave { managed: true };
            }
            self.register_tick(key, self.machine.now_ms);
        } else if self.world.admits_generic_player_runtime(site) {
            self.post(key.instance, Event::Finish(key.raw))?;
        } else {
            self.report(site, Issue::UnsupportedField("shockwave service"));
        }
        Ok(())
    }

    fn begin_ground_wave(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        use crate::script_expr::EvalError;
        use crate::script_init::GroundWave;

        let site = self.site(key.instance, node);
        let fields = &node.fields;
        let get_profile = |field: &str| {
            fields[field]["m_C1903771"]
                .get("m_C1903771")
                .and_then(asset_guid)
        };
        if fields["m_04F3240D"]["m_value"] != 1
            || get_profile("m_3D667CE3") != Some(0x0690_0000_0000_002d)
            || get_profile("m_9A8CA980") != Some(0x0690_0000_0000_012d)
            || fields["m_filter"]["m_4EA686DB"] != 20
            || fields["m_filter"]["m_84065FD1"] != 64
            || fields["m_filter"]["m_89831602"] != 1
            || fields["m_filter"]["m_condition"].is_null()
        {
            self.report(
                site,
                Issue::UnsupportedField("ground wave collision profile"),
            );
            return Ok(());
        }
        let wave = self.with_instance_context(site, None, |context| {
            let mut vector = |name| match script_expr::evaluate(&fields[name], context)? {
                Value::Vector(xyz) if xyz.into_iter().all(f64::is_finite) => Ok(xyz),
                _ => Err(EvalError::MalformedConfig("ground wave position")),
            };
            let centre = vector("m_05BE62DD")?;
            let sight_origin = vector("m_D59188D5")?;
            let mut scalar = |name| {
                pool_number(script_expr::evaluate(&fields[name], context)?)
                    .filter(|v| v.is_finite())
                    .ok_or(EvalError::MalformedConfig("ground wave scalar"))
            };
            let wave = GroundWave {
                centre,
                sight_origin,
                yaw: scalar("m_D45E22FA")?,
                aperture: scalar("m_A30B383E")?,
                distance: scalar("m_6A79BD98")?,
                speed: scalar("m_speed")?,
                max_rise: scalar("m_E81B2985")?,
                max_drop: scalar("m_FF3B7DF2")?,
                ground_range: scalar("m_525E5A40")?,
            };
            if wave.speed <= 0.0
                || wave.distance <= 0.0
                || !(0.0..=360.0).contains(&wave.aperture)
                || [wave.max_rise, wave.max_drop, wave.ground_range]
                    .into_iter()
                    .any(|v| v < 0.0)
            {
                return Err(EvalError::MalformedConfig("ground wave bounds"));
            }
            Ok(wave)
        });
        match wave {
            Ok(wave) => self.state_mut(key).expect("wave state").ground_wave = Some(Box::new(wave)),
            Err(error) => {
                self.report(site, Issue::Expression(error));
                return Ok(());
            }
        }
        if self.collect_shockwave(key, node, 0.0)? {
            self.state_mut(key).expect("wave state").kind = StateKind::Shockwave { managed: true };
            self.register_tick(key, self.machine.now_ms);
        } else {
            self.report(site, Issue::UnsupportedField("ground wave room sample"));
        }
        Ok(())
    }

    fn collect_shockwave(
        &mut self,
        key: StateKey,
        node: &Node,
        reach: f64,
    ) -> Result<bool, InitFault> {
        let site = self.site(key.instance, node);
        let Some(target) = script_expr::lvalue(field(&node.fields, "m_8A2905A0"))
            .ok()
            .flatten()
        else {
            return Ok(false);
        };
        let wave = self
            .state(key)
            .and_then(|state| state.ground_wave.as_deref())
            .copied();
        let bodies = if let Some(wave) = wave {
            let Some(candidates) = self.world.ground_wave_targets(site, &wave, reach) else {
                return Ok(false);
            };
            let mut accepted = Vec::new();
            for candidate in candidates {
                self.spend("ground wave candidate")?;
                let previous = self
                    .parameters
                    .replace(Arc::new(Variables::from([(8, Value::Entity(candidate))])));
                let condition = self.evaluate(
                    key.instance,
                    node,
                    &node.fields["m_filter"]["m_condition"],
                    None,
                );
                self.parameters = previous;
                let Some(condition) = condition else {
                    return Ok(false);
                };
                if truthy(&condition) {
                    accepted.push(candidate);
                }
            }
            accepted
        } else {
            let Some(candidates) = self.world.shockwave_targets(site, reach) else {
                return Ok(false);
            };
            candidates
        };
        let var_key = source_key(key.instance, target);
        let mut items = match self.machine.value(var_key, None) {
            Value::Array(items) => items,
            _ => Vec::new(),
        };
        let mut seen: std::collections::HashSet<u32> = items
            .iter()
            .filter_map(|item| match *item {
                Value::Entity(id) if id != 0 => Some(id),
                _ => None,
            })
            .collect();
        let before = items.len();
        for body in bodies {
            if body != 0 && seen.insert(body) {
                items.push(Value::Entity(body));
            }
        }
        if items.len() != before {
            self.write(var_key, Value::Array(items))?;
        }
        Ok(true)
    }

    pub(in crate::script_init) fn tick_shockwave(
        &mut self,
        key: StateKey,
    ) -> Result<(), InitFault> {
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("shockwave state");
        if let Some(state) = self.state(key) {
            if let Some(wave) = state.ground_wave.as_deref() {
                let elapsed = self.machine.now_ms.saturating_sub(state.start_ms);
                let reach = wave.reach(elapsed);
                let finished = reach >= wave.distance;
                if !self.collect_shockwave(key, node, reach)? {
                    self.report(
                        self.site(key.instance, node),
                        Issue::UnsupportedField("ground wave room sample"),
                    );
                    return Ok(());
                }
                if finished {
                    self.post(key.instance, Event::Finish(key.raw))?;
                }
                return Ok(());
            }
        }
        let reach = self
            .evaluate(key.instance, node, field(&node.fields, "m_6A79BD98"), None)
            .and_then(|value| match value {
                Value::Bool(_) | Value::Integer(_) | Value::Float(_) => {
                    let f = script_expr::to_float(&value);
                    Some(if f.is_finite() { f.max(0.0) } else { 0.0 })
                }
                _ => None,
            })
            .unwrap_or(0.0);
        if !self.collect_shockwave(key, node, reach)? {
            let site = self.site(key.instance, node);
            self.report(site, Issue::UnsupportedField("shockwave service"));
        }
        self.post(key.instance, Event::Finish(key.raw))
    }

    pub(in crate::script_init) fn begin_placement_selector(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let generic = self.world.admits_generic_player_runtime(site);
        if (site.graph, node.pos) != (0x0B27, 15) && !generic {
            self.report(site, Issue::UnsupportedState(node.class.clone()));
            return Ok(());
        }
        let enabled = if generic {
            let toggle = field(&node.fields, "m_A6BB0869");
            toggle.is_null()
                || self
                    .evaluate(key.instance, node, toggle, None)
                    .is_none_or(|value| truthy(&value))
        } else {
            !field(&node.fields, "m_B02B1FD7").is_null()
                && self
                    .evaluate(key.instance, node, field(&node.fields, "m_A6BB0869"), None)
                    .is_some_and(|value| truthy(&value))
        };
        if enabled {
            self.register_tick(key, self.machine.now_ms);
        }
        Ok(())
    }

    pub(in crate::script_init) fn tick_placement_selector(
        &mut self,
        key: StateKey,
    ) -> Result<(), InitFault> {
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("placement selector state");
        let site = self.site(key.instance, node);
        let generic = self.world.admits_generic_player_runtime(site);
        if (site.graph, node.pos) != (0x0B27, 15) && !generic {
            return Ok(());
        }
        let sampled = matches!(
            self.state(key).map(|state| state.kind),
            Some(StateKind::PlacementSelector { sampled: true, .. })
        );
        let frame_ms = crate::script_runtime::FRAME_MS;
        let at_frame_end = self.machine.now_ms % frame_ms == frame_ms - 1;
        let ready_field = field(&node.fields, "m_B02B1FD7");
        let ready = if generic && ready_field.is_null() {
            true
        } else {
            self.evaluate(key.instance, node, ready_field, None)
                .is_some_and(|value| truthy(&value))
        };
        if (sampled && !at_frame_end) || !ready {
            return Ok(());
        }
        if let Some(StateKind::PlacementSelector { sampled, .. }) =
            self.state_mut(key).map(|state| &mut state.kind)
        {
            *sampled = true;
        }
        let point = match self.world.select_landing(site) {
            super::super::LandingSelection::Unhandled => {
                self.report(site, Issue::UnsupportedField("landing selection"));
                return Ok(());
            }
            super::super::LandingSelection::Invalid => None,
            super::super::LandingSelection::Point(point) => Some(point),
        };
        if let Some(point) = point {
            for field_name in ["m_2FECD1E3", "m_1549F0F9"] {
                if let Some(target) = script_expr::lvalue(field(&node.fields, field_name))
                    .ok()
                    .flatten()
                {
                    self.write(source_key(key.instance, target), Value::Entity(0))?;
                }
            }
            if let Some(target) = script_expr::lvalue(field(&node.fields, "m_30710B76"))
                .ok()
                .flatten()
            {
                self.write(source_key(key.instance, target), Value::Vector(point))?;
            }
        }
        if let Some(target) = script_expr::lvalue(field(&node.fields, "m_C3240FFC"))
            .ok()
            .flatten()
        {
            self.write(
                source_key(key.instance, target),
                Value::Bool(point.is_some()),
            )?;
        }
        if point.is_some() {
            self.post(key.instance, Event::CheckTransitions(key.raw))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn begin_move_to(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        if node.class == "STU_194D744E" {
            let site = self.site(key.instance, node);
            let destination =
                self.evaluate(key.instance, node, field(&node.fields, "m_BD2A95EE"), None);
            if let (Some(Value::Vector(destination)), Some(speed)) =
                (destination, self.world.ai_walk_speed(site))
            {
                self.set_move_request(key, destination, speed);
            }
            return Ok(());
        }
        let site = self.site(key.instance, node);
        let generic_player = self.world.admits_generic_player_runtime(site);
        let speed_config = field(&node.fields, "m_speed");
        if speed_config.is_null()
            || (!generic_player && field(&node.fields, "m_B060F688").as_u64() != Some(0))
        {
            return Ok(());
        }
        self.register_tick(key, self.machine.now_ms);
        self.refresh_world_move_to(key, node)
    }

    pub(in crate::script_init) fn refresh_world_move_to(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        // An unavailable operand must not keep driving the previous request.
        if let Some(state) = self.state_mut(key) {
            state.kind = StateKind::MoveTo { movement: None };
        }
        let speed_config = field(&node.fields, "m_speed");
        let constraint = field(&node.fields, "m_5BEFF040");
        if !constraint.is_null()
            && !matches!(
                self.evaluate(key.instance, node, constraint, None),
                None | Some(Value::Null | Value::Integer(0) | Value::Float(0.0))
            )
        {
            return Ok(());
        }
        let bone_config = field(&node.fields, "m_destinationBone");
        let bone = if bone_config.is_null() {
            0
        } else {
            match self.evaluate(key.instance, node, bone_config, None) {
                // A static map hit clears its attachment output with a null asset.
                None
                | Some(Value::Null | Value::Integer(0) | Value::Float(0.0) | Value::Asset(0)) => 0,
                Some(Value::Asset(bone)) => bone,
                _ => return Ok(()),
            }
        };
        let entity_config = field(&node.fields, "m_destinationEntity");
        let entity = if entity_config.is_null() {
            None
        } else {
            match self.evaluate(key.instance, node, entity_config, None) {
                Some(Value::Entity(0)) => None,
                Some(Value::Entity(entity))
                    if script_expr::valid_entity(entity)
                        && self
                            .world
                            .admits_generic_player_runtime(self.site(key.instance, node)) =>
                {
                    Some(entity)
                }
                _ => return Ok(()),
            }
        };
        let vector_config = field(&node.fields, "m_865F3D01");
        let point = if vector_config.is_null() && entity.is_some() {
            [0.0; 3]
        } else if let Some(Value::Vector(point)) =
            self.evaluate(key.instance, node, vector_config, None)
        {
            point
        } else {
            return Ok(());
        };
        let destination = match entity {
            Some(actor) => {
                let Some(point) = self.world.body_bone_point(actor, bone, point) else {
                    return Ok(());
                };
                point
            }
            None if bone == 0 => point,
            None => return Ok(()),
        };
        let speed = match self.evaluate(key.instance, node, speed_config, None) {
            Some(Value::Integer(n)) => n as f64,
            Some(Value::Float(f)) => f,
            _ => return Ok(()),
        };
        self.set_move_request(key, destination, speed);
        Ok(())
    }

    fn set_move_request(&mut self, key: StateKey, destination: [f64; 3], speed: f64) {
        let max_axis = MAX_MOVEMENT_AXIS;
        if speed > 0.0
            && speed <= max_axis
            && destination
                .iter()
                .all(|&axis| (-max_axis..=max_axis).contains(&axis))
        {
            if let Some(state) = self.state_mut(key) {
                state.kind = StateKind::MoveTo {
                    movement: Some((destination, speed)),
                };
            }
        }
    }

    #[inline(never)]
    pub(in crate::script_init) fn run_create_entity(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
    ) -> Result<(), InitFault> {
        let site = self.site(id, node);
        let mut spawned = Value::Null;
        if let Some(Value::Asset(definition)) =
            self.evaluate(id, node, field(&node.fields, "m_entityDefinition"), caller)
        {
            if definition >> 48 == 0x0400 && definition & 0x0000_ffff_ffff_ffff != 0 {
                let attach =
                    match self.evaluate(id, node, field(&node.fields, "m_AA26E7DD"), caller) {
                        Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => {
                            Some(entity)
                        }
                        _ => None,
                    };
                let position =
                    match self.evaluate(id, node, field(&node.fields, "m_position"), caller) {
                        Some(Value::Vector(pos)) if pos.iter().all(|c| c.is_finite()) => Some(pos),
                        Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => {
                            self.world.position(entity)
                        }
                        _ => None,
                    };
                let bone_attached = !field(&node.fields, "m_888BCD8B").is_null();
                let possessable = matches!(
                    self.evaluate(id, node, field(&node.fields, "m_E5312022"), caller),
                    Some(Value::Bool(true)) | Some(Value::Integer(1))
                );
                if let Some(pet_id) = self.world.spawn_pet(
                    site,
                    crate::script_init::PetSpawn {
                        definition,
                        attach,
                        position,
                        bone_attached,
                        possessable,
                    },
                ) {
                    spawned = Value::Entity(pet_id);
                }
            }
        }
        if let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "m_B3019338")) {
            let target = source_key(id, output);
            if spawned != Value::Null || stored(&self.machine.snapshot, target) == Value::Null {
                self.write(target, spawned)?;
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn refresh_filter_array(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "m_F2805C84")) else {
            return Ok(());
        };
        let source = self
            .evaluate(id, node, field(&node.fields, "m_814F8EC3"), Some(key))
            .unwrap_or(Value::Null);
        let items = match source {
            Value::Array(items) => items,
            Value::Null => Vec::new(),
            other => vec![other],
        };
        let filter = {
            let primary = field(&node.fields, "m_filter");
            if !primary.is_null() {
                primary
            } else {
                field(&node.fields, "m_3D667CE3")
            }
        };
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let own_team = self.world.entity_team(site, site.entity);
        let mut filtered = Vec::with_capacity(items.len());
        for item in items {
            let keep = match item {
                Value::Entity(entity) if script_expr::valid_entity(entity) => {
                    if !self.world.entity_alive(site, entity) {
                        false
                    } else {
                        let target_team = self.world.entity_team(site, entity);
                        match (team_mode, hostile_flag) {
                            (1, 0) => own_team.is_some() && target_team == own_team,
                            (2, _) | (_, 1) => own_team.is_none() || target_team != own_team,
                            _ => true,
                        }
                    }
                }
                Value::Entity(_) | Value::Null => false,
                _ => true,
            };
            if keep {
                filtered.push(item);
            }
        }
        let target_key = source_key(id, output);
        let result = Value::Array(filtered);
        if !same(&self.machine.value(target_key, None), &result) {
            self.write(target_key, result)?;
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_combat_mod(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let stat = asset_guid(field(&node.fields, "m_60703B1E")).unwrap_or(0);
        let multiplicative = field(&node.fields, "m_41B10FF2").as_u64() == Some(1);
        let value = self
            .evaluate(id, node, field(&node.fields, "m_50530EAB"), None)
            .and_then(pool_number)
            .filter(|v| v.is_finite())
            .unwrap_or(if multiplicative { 1.0 } else { 0.0 });
        let cc_immune = self
            .evaluate(id, node, field(&node.fields, "m_941C56C2"), None)
            .is_some_and(|v| truthy(&v));
        if self.world.set_combat_mod(
            site,
            Some(crate::script_init::CombatModEntry {
                stat,
                multiplicative,
                value,
                cc_immune,
            }),
        ) {
            if let Some(state) = self.state_mut(key) {
                state.kind = StateKind::CombatModFilter { registered: true };
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_deflect(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let reflect = field(&node.fields, "m_82DA44E0").as_u64() == Some(1);
        let half_angle_deg = self
            .evaluate(id, node, field(&node.fields, "m_025D0C39"), None)
            .and_then(pool_number)
            .filter(|a| a.is_finite() && *a > 0.0 && *a <= 180.0)
            .unwrap_or(90.0);
        if let Some(state) = self.state_mut(key) {
            state.kind = StateKind::DeflectProjectiles {
                reflect,
                half_angle_deg,
            };
        }
        let _ = self.world.set_deflect_state(
            site,
            Some(crate::script_init::DeflectState {
                reflect,
                half_angle_deg,
            }),
        );
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn refresh_generic_contact_sweep(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let reach = self
            .evaluate(id, node, field(&node.fields, "m_3B671E99"), None)
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(2.5)
            .clamp(0.5, 22.0);
        let origin = match self.evaluate(id, node, field(&node.fields, "m_5BC939F0"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => Some(v),
            _ => self
                .world
                .body_eye_position(site.entity)
                .or_else(|| self.world.position(site.entity)),
        };
        let direction = match self.evaluate(id, node, field(&node.fields, "m_04732804"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => {
                let len = v[0].hypot(v[1]).hypot(v[2]);
                if len > 1e-4 {
                    Some([v[0] / len, v[1] / len, v[2] / len])
                } else {
                    self.world.forward(site.entity)
                }
            }
            _ => self.world.forward(site.entity),
        };
        // `m_53FCEDBF` is the input subject entity/array (e.g. `STU_B5A0CAF0` in `0x0D06:20`,
        // `var 375` punched-target array in `0x0D06:70`) — evaluate for watch registration
        // without overwriting it!
        let _ = self.evaluate(id, node, field(&node.fields, "m_53FCEDBF"), Some(key));
        static HOSTILE_FILTER: std::sync::LazyLock<Json> =
            std::sync::LazyLock::new(|| serde_json::json!({ "m_66747E7D": 2, "m_89831602": 1 }));
        if let Ok(Some(pt_var)) = script_expr::lvalue(field(&node.fields, "m_7AD5153D")) {
            if let Some(contact) = self.world.generic_beam_contact(
                site,
                origin,
                direction,
                Some(reach),
                0.55,
                &HOSTILE_FILTER,
            ) {
                if let Some(pt) = contact.point {
                    self.write(source_key(id, pt_var), Value::Vector(pt))?;
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn refresh_target_aura(
        &mut self,
        key: StateKey,
        node: &Node,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let effect =
            asset_guid(field(field(&node.fields, "m_A76A8CF6"), "m_A76A8CF6")).unwrap_or(0);
        if let Some(state) = self.state_mut(key) {
            state.kind = StateKind::TargetAura { effect };
        }
        let target_field = field(&node.fields, "m_targets");
        let target_val = if target_field.is_null() {
            Value::Entity(site.entity)
        } else {
            self.evaluate(id, node, target_field, None)
                .unwrap_or(Value::Null)
        };
        let mut targets = Vec::new();
        match target_val {
            Value::Entity(e) if script_expr::valid_entity(e) => targets.push(e),
            Value::Array(items) => {
                for item in items {
                    if let Value::Entity(e) = item {
                        if script_expr::valid_entity(e) {
                            targets.push(e);
                        }
                    }
                }
            }
            _ => {}
        }
        let param = array_field(&node.fields, "m_params")
            .first()
            .and_then(|row| self.evaluate(id, node, field(row, "m_value"), None))
            .and_then(pool_number)
            .filter(|v| v.is_finite());
        let _ = self
            .world
            .set_target_aura(site, &targets, effect, param, elapsed_ms);
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_target_subscript(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let graph_guid =
            asset_guid(field(field(&node.fields, "m_script"), "m_graph")).or_else(|| {
                asset_guid(field(
                    field(field(&node.fields, "m_69F4B52C"), "m_6525E98B"),
                    "m_graph",
                ))
            });
        let Some(guid) = graph_guid else {
            return Ok(());
        };
        let graph = u32::from(guid as u16);
        if self.graphs.graph(graph).is_none() {
            return Ok(());
        }
        let scope = self.instance_scope(id);
        let Ok(child) = self.machine.next_instance_id(scope.is_isolated()) else {
            return Ok(());
        };
        if self
            .create(
                child,
                graph,
                Some((id, key.raw)),
                Variables::new(),
                crate::script_init::Startup::Ordinary,
                scope,
            )
            .is_ok()
        {
            if let Some(state) = self.state_mut(key) {
                state.kind = StateKind::TargetSubScript { child: Some(child) };
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_constant_accel(
        &mut self,
        key: StateKey,
        node: &Node,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead || elapsed_ms == 0 {
            return Ok(());
        }
        let id = key.instance;
        let site = self.site(id, node);
        let dt = (elapsed_ms as f64 / 1000.0).min(0.05);
        let accel = match self.evaluate(id, node, field(&node.fields, "m_16882D22"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            _ => [0.0, 26.0, 0.0],
        };
        let max_up = self
            .evaluate(id, node, field(&node.fields, "m_B74245E8"), None)
            .and_then(pool_number)
            .filter(|v| v.is_finite() && *v > 0.0)
            .unwrap_or(5.5);
        let frame = self.machine.frame;
        let ops = [
            crate::script_init::VelocityOp::AddVector([
                accel[0] * dt,
                accel[1] * dt,
                accel[2] * dt,
            ]),
            crate::script_init::VelocityOp::CapSpeed {
                axis: crate::script_init::VelocityAxis::VerticalUp,
                max: max_up,
            },
        ];
        let _ = self.world.apply_velocity_ops(site, frame, &ops);
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_wall_climb(
        &mut self,
        key: StateKey,
        node: &Node,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead {
            return Ok(());
        }
        let id = key.instance;
        let site = self.site(id, node);
        let Some(pos) = self.world.position(site.entity) else {
            return Ok(());
        };
        let Some(fwd) = self.world.forward(site.entity) else {
            return Ok(());
        };
        let origin = [pos[0], pos[1] + 1.0, pos[2]];
        let probe_dist = self
            .evaluate(id, node, field(&node.fields, "m_1190FA0E"), None)
            .and_then(pool_number)
            .filter(|d| d.is_finite() && *d > 0.2 && *d < 4.0)
            .unwrap_or(1.25)
            .max(1.25);
        let right = [fwd[2], 0.0, -fwd[0]];
        let dirs = if node.class == "STU_92ED01D0" {
            vec![
                fwd,
                [
                    fwd[0] * 0.866 + right[0] * 0.5,
                    0.0,
                    fwd[2] * 0.866 + right[2] * 0.5,
                ],
                [
                    fwd[0] * 0.866 - right[0] * 0.5,
                    0.0,
                    fwd[2] * 0.866 - right[2] * 0.5,
                ],
            ]
        } else {
            // 8-direction ring so Lúcio maintains wallride cleanly around curved/angled walls.
            vec![
                right,
                [-right[0], 0.0, -right[2]],
                [
                    fwd[0] * 0.707 + right[0] * 0.707,
                    0.0,
                    fwd[2] * 0.707 + right[2] * 0.707,
                ],
                [
                    fwd[0] * 0.707 - right[0] * 0.707,
                    0.0,
                    fwd[2] * 0.707 - right[2] * 0.707,
                ],
                fwd,
                [
                    -fwd[0] * 0.707 + right[0] * 0.707,
                    0.0,
                    -fwd[2] * 0.707 + right[2] * 0.707,
                ],
                [
                    -fwd[0] * 0.707 - right[0] * 0.707,
                    0.0,
                    -fwd[2] * 0.707 - right[2] * 0.707,
                ],
                [-fwd[0], 0.0, -fwd[2]],
            ]
        };
        let mut wall_normal = None;
        for dir in dirs {
            if let Some(Some(hit)) =
                self.world
                    .spatial_ray_cast(site, self.machine.frame, origin, dir, probe_dist)
            {
                if hit.normal[1].abs() < 0.55 {
                    wall_normal = Some(hit.normal);
                    break;
                }
            }
        }
        let touching = wall_normal.is_some();
        if let Ok(Some(flag_var)) = script_expr::lvalue(field(&node.fields, "m_7F8E47ED")) {
            let var_key = source_key(id, flag_var);
            let val = Value::Bool(touching);
            if !same(&self.machine.value(var_key, None), &val) {
                self.write(var_key, val)?;
            }
        }
        if let Some(normal) = wall_normal {
            for field_name in ["m_DF4A5F82", "m_068B20CE", "m_24680BA9"] {
                if let Ok(Some(n_var)) = script_expr::lvalue(field(&node.fields, field_name)) {
                    let var_key = source_key(id, n_var);
                    let val = Value::Vector(normal);
                    if !same(&self.machine.value(var_key, None), &val) {
                        self.write(var_key, val)?;
                    }
                }
            }
            let frame = self.machine.frame;
            let dt = (elapsed_ms as f64 / 1000.0).min(0.05);
            if node.class == "STU_92ED01D0" {
                let speed = self
                    .evaluate(id, node, field(&node.fields, "m_4E3BB2CE"), None)
                    .and_then(pool_number)
                    .filter(|s| s.is_finite() && *s > 0.0 && *s <= 20.0)
                    .unwrap_or(7.5);
                let _ = self.world.apply_velocity_ops(
                    site,
                    frame,
                    &[crate::script_init::VelocityOp::SetSpeed {
                        axis: crate::script_init::VelocityAxis::Vertical,
                        value: speed,
                    }],
                );
            } else if !field(&node.fields, "m_4573AFA0").is_null() && dt > 0.0 {
                // WallRide (Lúcio): project forward glide tangent to the wall normal with gentle
                // inward cling so Lúcio rides continuously along straight and curved walls.
                let dot = fwd[0] * normal[0] + fwd[2] * normal[2];
                let tx = fwd[0] - normal[0] * dot;
                let tz = fwd[2] - normal[2] * dot;
                let tlen = tx.hypot(tz);
                let (gx, gz) = if tlen > 1e-4 {
                    (tx / tlen, tz / tlen)
                } else {
                    (fwd[0], fwd[2])
                };
                let boost = 28.0 * dt;
                let cling = 3.0 * dt;
                let _ = self.world.apply_velocity_ops(
                    site,
                    frame,
                    &[
                        crate::script_init::VelocityOp::SetSpeed {
                            axis: crate::script_init::VelocityAxis::Vertical,
                            value: 0.35,
                        },
                        crate::script_init::VelocityOp::AddVector([
                            gx * boost - normal[0] * cling,
                            0.0,
                            gz * boost - normal[2] * cling,
                        ]),
                        crate::script_init::VelocityOp::CapSpeed {
                            axis: crate::script_init::VelocityAxis::Horizontal,
                            max: 10.5,
                        },
                    ],
                );
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_grapple(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        // The graph supplies the attachment offset and minimum cable length.
        // Its nested movement modifiers also feed the owner's ordinary mover.
        let anchor = match self.evaluate(id, node, field(&node.fields, "m_865F3D01"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            _ => {
                self.post(id, Event::Finish(key.raw))?;
                return Ok(());
            }
        };
        let attach_offset = match self.evaluate(id, node, field(&node.fields, "m_3BC31C57"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            _ => return self.post(id, Event::Finish(key.raw)),
        };
        let min_rope_len = self
            .evaluate(id, node, field(&node.fields, "m_189141AE"), None)
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r >= 0.0);
        let (Some(pos), Some(min_rope_len)) = (self.world.position(site.entity), min_rope_len)
        else {
            return self.post(id, Event::Finish(key.raw));
        };
        let dx = anchor[0] - (pos[0] + attach_offset[0]);
        let dy = anchor[1] - (pos[1] + attach_offset[1]);
        let dz = anchor[2] - (pos[2] + attach_offset[2]);
        let initial_dist = dx.hypot(dy).hypot(dz);
        let rope_len = initial_dist.max(min_rope_len);
        if let Some(state) = self.state_mut(key) {
            state.grapple = Some(Box::new(crate::script_init::TetherConstraint {
                anchor,
                attach_offset,
                rope_len,
                min_rope_len,
            }));
        }
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_grapple(
        &mut self,
        key: StateKey,
        node: &Node,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if elapsed_ms != 0 {
            let site = self.site(key.instance, node);
            if let Some(pos) = self.world.position(site.entity) {
                if let Some(cable) = self
                    .state_mut(key)
                    .and_then(|state| state.grapple.as_deref_mut())
                {
                    cable.shorten_at(pos);
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_recall_history(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let window_secs = field(&node.fields, "m_F0BE865D")
            .as_f64()
            .filter(|w| w.is_finite() && *w > 0.5 && *w <= 10.0)
            .unwrap_or(3.0);
        let _ = self.world.record_recall_sample(site, window_secs);
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_recall_rewind(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let frame = self.machine.frame.saturating_add(1);
        if let Some(pos) = self.world.execute_recall(site, frame) {
            if let Ok(Some(out_var)) = script_expr::lvalue(field(&node.fields, "m_6B5DA460")) {
                self.write(source_key(id, out_var), Value::Vector(pos))?;
            }
        }
        // Retail Tracer Recall completes rewind in ~0.9s (56 ticks) instead of stalling indefinitely.
        self.post_after(id, 900, Event::Timer(key.raw, 1))?;
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_spatial_query(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let center = match self.evaluate(id, node, field(&node.fields, "m_position"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            Some(Value::Entity(e)) if script_expr::valid_entity(e) => {
                self.world.position(e).unwrap_or([0.0; 3])
            }
            _ => self.world.position(site.entity).unwrap_or([0.0; 3]),
        };
        let radius = self
            .evaluate(id, node, field(&node.fields, "m_radius"), None)
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(6.0);
        static ANY_FILTER: std::sync::LazyLock<Json> =
            std::sync::LazyLock::new(|| serde_json::json!({ "m_66747E7D": 0, "m_89831602": 0 }));
        let targets = self
            .world
            .tracked_targets(site, 0, center, radius, &ANY_FILTER)
            .unwrap_or_default();
        // `STU_B5C87F3D` (shared Quick Melee `0043:24` breakable cone) uses `m_C33731A1`
        // as an INPUT variable (damage = var[476] = 30), NOT an output array! Overwriting
        // `m_C33731A1` with `Value::Array` corrupted `var[476]` and crashed `0043:10`.
        if node.class != "STU_B5C87F3D" {
            if let Ok(Some(out_var)) = script_expr::lvalue(field(&node.fields, "m_C33731A1")) {
                let val = Value::Array(targets.into_iter().map(Value::Entity).collect());
                self.write(source_key(id, out_var), val)?;
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_physics_explosion(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        // Static room geometry has no dynamic rigid bodies. Actor knockback is
        // authored separately through ModifyHealth on the same landing.
        if self.world.has_dynamic_map_rigid_bodies(site) == Some(false) {
            return Ok(());
        }
        let center = match self.evaluate(id, node, field(&node.fields, "m_position"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            Some(Value::Entity(e)) if script_expr::valid_entity(e) => {
                self.world.position(e).unwrap_or([0.0; 3])
            }
            _ => self.world.position(site.entity).unwrap_or([0.0; 3]),
        };
        let radius = self
            .evaluate(id, node, field(&node.fields, "m_radius"), None)
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(4.0)
            .clamp(0.25, 40.0);
        let impulse = self
            .evaluate(id, node, field(&node.fields, "m_65438EEB"), None)
            .and_then(pool_number)
            .filter(|v| v.is_finite())
            .unwrap_or(0.0);
        let direction = match self.evaluate(id, node, field(&node.fields, "m_direction"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => Some(v),
            _ => None,
        };
        let _ = self
            .world
            .apply_physics_explosion(site, center, radius, impulse, direction);
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_hit_tracker(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let targets_val = self
            .evaluate(id, node, field(&node.fields, "m_targets"), Some(key))
            .unwrap_or(Value::Null);
        let new_count: u16 = match &targets_val {
            Value::Array(items) => items
                .iter()
                .filter(|v| matches!(v, Value::Entity(e) if script_expr::valid_entity(*e)))
                .count()
                .min(u16::MAX as usize) as u16,
            Value::Entity(e) if script_expr::valid_entity(*e) => 1,
            _ => 0,
        };
        let prev_count = match self.state(key).map(|s| s.kind) {
            Some(StateKind::HitTracker { hit_count }) => hit_count,
            _ => 0,
        };
        if new_count != prev_count {
            if let Some(state) = self.state_mut(key) {
                state.kind = StateKind::HitTracker {
                    hit_count: new_count,
                };
            }
            if new_count > prev_count {
                self.follow(id, node, "m_C41A9BE2", Some(key), 0)?;
            } else if new_count == 0 {
                self.follow(id, node, "m_0647CAD7", Some(key), 0)?;
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn refresh_ray_cast(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        // Retail `STUStatescriptStateRayCast` uses `m_startPosWS`, `m_dirWS`, `m_castLength`,
        // `m_out_HitSomething`, `m_out_HitPointWS`, `m_out_HitNormal`, `m_out_HitEntity`
        // (e.g. Wrecking Ball Piledriver `160A:RayCast_90`).
        if !field(&node.fields, "m_startPosWS").is_null()
            || !field(&node.fields, "m_dirWS").is_null()
            || !field(&node.fields, "m_castLength").is_null()
        {
            return self.run_authored_raycast(id, node, Some(key));
        }
        let site = self.site(id, node);
        let origin = match self.evaluate(id, node, field(&node.fields, "m_position"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            Some(Value::Entity(e)) if script_expr::valid_entity(e) => self
                .world
                .body_eye_position(e)
                .or_else(|| self.world.position(e))
                .unwrap_or([0.0; 3]),
            _ => self
                .world
                .body_eye_position(site.entity)
                .or_else(|| self.world.position(site.entity))
                .unwrap_or([0.0; 3]),
        };
        let direction = match self.evaluate(id, node, field(&node.fields, "m_direction"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => {
                let len = v[0].hypot(v[1]).hypot(v[2]);
                if len > 1e-4 {
                    [v[0] / len, v[1] / len, v[2] / len]
                } else {
                    self.world
                        .authored_forward(site.entity)
                        .unwrap_or([0.0, 0.0, 1.0])
                }
            }
            _ => self
                .world
                .authored_forward(site.entity)
                .or_else(|| self.world.forward(site.entity))
                .unwrap_or([0.0, 0.0, 1.0]),
        };
        let range = ["m_range", "m_distance", "m_maxDistance", "m_244723D6"]
            .iter()
            .find_map(|f| {
                self.evaluate(id, node, field(&node.fields, f), None)
                    .and_then(pool_number)
            })
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(100.0)
            .clamp(0.25, 250.0);
        let hit = self
            .world
            .spatial_ray_cast(site, self.machine.frame, origin, direction, range)
            .flatten();
        let (hit_bool, hit_pos, hit_norm, hit_dist) = if let Some(h) = hit {
            let pt = [
                origin[0] + direction[0] * h.distance,
                origin[1] + direction[1] * h.distance,
                origin[2] + direction[2] * h.distance,
            ];
            (true, pt, h.normal, h.distance)
        } else {
            let end = [
                origin[0] + direction[0] * range,
                origin[1] + direction[1] * range,
                origin[2] + direction[2] * range,
            ];
            (false, end, [0.0, 1.0, 0.0], range)
        };
        for (fname, val) in [
            ("m_hitPosition", Value::Vector(hit_pos)),
            ("m_hitNormal", Value::Vector(hit_norm)),
            ("m_hitDistance", Value::Float(hit_dist)),
            ("m_hit", Value::Bool(hit_bool)),
        ] {
            if let Ok(Some(out_var)) = script_expr::lvalue(field(&node.fields, fname)) {
                let target_key = source_key(id, out_var);
                if !same(&self.machine.value(target_key, None), &val) {
                    self.write(target_key, val)?;
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_entity_attach(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let mut target = 0u32;
        for fname in [
            "m_target",
            "m_3E3E203A",
            "m_FB9FD621",
            "m_source",
            "m_50D63347",
        ] {
            if let Some(Value::Entity(e)) =
                self.evaluate(id, node, field(&node.fields, fname), None)
            {
                if script_expr::valid_entity(e) {
                    target = e;
                    break;
                }
            }
        }
        // Beam/tether lock-on (`STU_9932D728`) resolves live beam contact if target isn't bound yet.
        if node.class == "STU_9932D728" {
            static ANY_BEAM_FILTER: std::sync::LazyLock<Json> = std::sync::LazyLock::new(
                || serde_json::json!({ "m_66747E7D": 0, "m_89831602": 0 }),
            );
            let origin = self
                .world
                .body_eye_position(site.entity)
                .or_else(|| self.world.position(site.entity));
            let dir = self
                .world
                .authored_forward(site.entity)
                .or_else(|| self.world.forward(site.entity));
            if let Some(contact) = self.world.generic_beam_contact(
                site,
                origin,
                dir,
                Some(20.0),
                0.45,
                &ANY_BEAM_FILTER,
            ) {
                if let Some(e) = contact.entity {
                    target = e;
                    if let Ok(Some(out_var)) =
                        script_expr::lvalue(field(&node.fields, "m_D4E290F3"))
                    {
                        self.write(source_key(id, out_var), Value::Entity(e))?;
                    }
                }
            }
        }
        // `STU_85124386` binds `m_B049E822` to source/self if lvalue.
        if node.class == "STU_85124386" {
            if let Ok(Some(out_var)) = script_expr::lvalue(field(&node.fields, "m_B049E822")) {
                let bound = if target != 0 { target } else { site.entity };
                self.write(source_key(id, out_var), Value::Entity(bound))?;
            }
        }
        if let Some(state) = self.state_mut(key) {
            state.kind = StateKind::EntityAttach { target };
        }
        if target != 0 && target != site.entity {
            self.register_tick(key, self.machine.now_ms);
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_entity_attach(
        &mut self,
        key: StateKey,
        node: &Node,
        target: u32,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        if target != 0 && target != site.entity && self.world.entity_exists(target) == Some(false) {
            self.post(id, Event::Finish(key.raw))?;
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_modifier_control(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        // Evaluate authored scale/parameter fields so watched dependencies stay live.
        for fname in [
            "m_69A20070",
            "m_10705336",
            "m_9A86AC74",
            "m_865D75D0",
            "m_90C89D90",
            "m_1A18950A",
        ] {
            let f = field(&node.fields, fname);
            if !f.is_null() {
                let _ = self.evaluate(id, node, f, None);
            }
        }
        if node.class == "STU_E3C424E1" {
            let block_a = self
                .evaluate(id, node, field(&node.fields, "m_8A043AB4"), None)
                .is_some_and(|v| truthy(&v));
            let block_b = self
                .evaluate(id, node, field(&node.fields, "m_C0D9CA3F"), None)
                .is_some_and(|v| truthy(&v));
            if block_a || block_b {
                if let Some(state) = self.state_mut(key) {
                    state.kind = StateKind::ModifierControl { blocks_input: true };
                }
                self.machine.control_blocked = true;
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_kinematics_control(
        &mut self,
        key: StateKey,
        node: &Node,
        mode: u8,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        match mode {
            1 => {
                // `STU_C1BA7545`: 3D Free Flight (Mercy Valkyrie / Echo Flight / Sigma Flux)
                let speed_scale = self
                    .evaluate(id, node, field(&node.fields, "m_2166C07A"), None)
                    .and_then(pool_number)
                    .filter(|s| s.is_finite() && *s > 0.1)
                    .map(|s| {
                        if s > 3.5 {
                            (s / 5.5).clamp(0.5, 2.5)
                        } else {
                            s.clamp(0.5, 2.5)
                        }
                    })
                    .unwrap_or(1.35);
                self.machine.movement_modifiers.insert(
                    ModifierKey {
                        instance: id,
                        state: key.raw,
                        slot: 0,
                    },
                    Modifier {
                        index: SPEED_SLOT,
                        operation: Operation::Multiply,
                        value: narrow(speed_scale),
                        priority: 0.0,
                    },
                );
                self.register_tick(key, self.machine.now_ms);
            }
            2 => {
                // `STU_582E0D5F`: 3rd-person camera offset/orbit state (e.g. Wrecking Ball Roll
                // `1203:STU_582E0D5F`, Reinhardt Shield, Brigitte Shield) — client-side camera
                // framing only; must NOT alter movement speed.
            }
            3 => {
                // `STU_FBFF5DE7`: Charge / Rocket Punch forward directional drive
                self.tick_kinematics_control(key, node, 3)?;
                self.register_tick(key, self.machine.now_ms);
            }
            4 => {
                // `STU_4773DA56`: Wall-impact detector during Charge / Punch
                self.register_tick(key, self.machine.now_ms);
            }
            5 => {
                // `STU_C168636D`: Pinned target carry monitor (Charge / Rocket Punch)
                let _ = self.evaluate(id, node, field(&node.fields, "m_6716919E"), None);
                let _ = self.evaluate(id, node, field(&node.fields, "m_9F17FE89"), None);
                self.register_tick(key, self.machine.now_ms);
            }
            6 => {
                // `STU_54A1A6B6`: Wall-vault / double-jump impulse (Hanzo / Genji)
                let mag = self
                    .evaluate(id, node, field(&node.fields, "m_B4B3486A"), None)
                    .and_then(pool_number)
                    .filter(|v| v.is_finite() && *v > 0.0)
                    .unwrap_or(6.5)
                    .clamp(1.0, 22.0);
                let dir = match self.evaluate(id, node, field(&node.fields, "m_5CF4834B"), None) {
                    Some(Value::Vector(v))
                        if v.iter().all(|c| c.is_finite()) && v.iter().any(|c| c.abs() > 1e-3) =>
                    {
                        v
                    }
                    _ => [0.0, 1.0, 0.0],
                };
                let len = dir[0].hypot(dir[1]).hypot(dir[2]).max(1e-4);
                let vec = [
                    dir[0] / len * mag,
                    (dir[1] / len * mag).max(mag * 0.5),
                    dir[2] / len * mag,
                ];
                let _ = self.world.apply_velocity_ops(
                    site,
                    self.machine.frame,
                    &[crate::script_init::VelocityOp::AddVector(vec)],
                );
            }
            7 => {
                // `STU_EF4C12FB`: Directional dash / glide
                let speed = self
                    .evaluate(id, node, field(&node.fields, "m_speed"), None)
                    .and_then(pool_number)
                    .filter(|v| v.is_finite() && *v > 0.0)
                    .unwrap_or(14.0)
                    .clamp(1.0, 35.0);
                let dir = match self.evaluate(id, node, field(&node.fields, "m_direction"), None) {
                    Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
                    _ => self
                        .world
                        .authored_forward(site.entity)
                        .unwrap_or([0.0, 0.0, 1.0]),
                };
                let len = dir[0].hypot(dir[1]).hypot(dir[2]).max(1e-4);
                let vel = [
                    dir[0] / len * speed,
                    dir[1] / len * speed,
                    dir[2] / len * speed,
                ];
                let _ = self.world.apply_velocity_ops(
                    site,
                    self.machine.frame,
                    &[crate::script_init::VelocityOp::SetVector(vel)],
                );
                self.register_tick(key, self.machine.now_ms);
            }
            _ => {
                // `STU_6154B909` (Meteor Strike `0x0C51:70` sky targeting) vs
                // `STU_D09BD154` / `STU_1E356736` (Seismic Slam / Air arc control)
                let _ = self.evaluate(id, node, field(&node.fields, "m_4999D53A"), None);
                let _ = self.evaluate(id, node, field(&node.fields, "m_62F6795F"), None);
                if node.class == "STU_6154B909" {
                    // Hold vertical velocity at 0 while Doomfist steers the Meteor Strike
                    // ground reticle via `0x0C51:69` (`speed = 35`), until LMB (`pos=63`)
                    // or sky timeout (`pos=74`) lands him.
                    let _ = self.world.apply_velocity_ops(
                        site,
                        self.machine.frame,
                        &[crate::script_init::VelocityOp::SetSpeed {
                            axis: crate::script_init::VelocityAxis::Vertical,
                            value: 0.0,
                        }],
                    );
                    self.register_tick(key, self.machine.now_ms);
                } else {
                    let target_pt =
                        match self.evaluate(id, node, field(&node.fields, "m_2A7E6E17"), None) {
                            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => Some(v),
                            _ => None,
                        };
                    let pos = self.world.position(site.entity);
                    let dir = target_pt
                        .zip(pos)
                        .and_then(|(dst, src)| {
                            let dx = dst[0] - src[0];
                            let dz = dst[2] - src[2];
                            let hlen = dx.hypot(dz);
                            (hlen > 0.25).then_some([dx / hlen, dst[1] - src[1], dz / hlen])
                        })
                        .or_else(|| self.world.authored_forward(site.entity))
                        .or_else(|| self.world.forward(site.entity))
                        .unwrap_or([0.0, 0.0, 1.0]);
                    let hlen = dir[0].hypot(dir[2]).max(1e-4);
                    let airborne = self
                        .world
                        .movement_sample(site)
                        .is_some_and(|sample| sample.air_ticks > 0);
                    let vel = if airborne {
                        [dir[0] / hlen * 18.0, -13.5, dir[2] / hlen * 18.0]
                    } else {
                        [dir[0] / hlen * 15.0, 6.2, dir[2] / hlen * 15.0]
                    };
                    let _ = self.world.apply_velocity_ops(
                        site,
                        self.machine.frame,
                        &[crate::script_init::VelocityOp::SetVector(vel)],
                    );
                    self.register_tick(key, self.machine.now_ms);
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_kinematics_control(
        &mut self,
        key: StateKey,
        node: &Node,
        mode: u8,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        match mode {
            1 => {
                // 3D Flight vertical control: Jump rises, Crouch descends, otherwise hover cleanly.
                let buttons = self.machine.command_buttons;
                let vy = if buttons & 0x0040 != 0 {
                    6.5
                } else if buttons & 0x0080 != 0 {
                    -6.5
                } else {
                    0.0
                };
                let _ = self.world.apply_velocity_ops(
                    site,
                    self.machine.frame,
                    &[crate::script_init::VelocityOp::SetSpeed {
                        axis: crate::script_init::VelocityAxis::Vertical,
                        value: vy,
                    }],
                );
            }
            3 => {
                // Charge / Rocket Punch forward steering along authored vector (`m_26DA7CF8`) or facing.
                // Rocket Punch (`0x0D06:17`) authors dash speed in `m_87A4D0AD` (37.5 m/s).
                let speed = self
                    .evaluate(id, node, field(&node.fields, "m_87A4D0AD"), None)
                    .and_then(pool_number)
                    .filter(|s| s.is_finite() && *s > 1.0)
                    .or_else(|| {
                        self.evaluate(id, node, field(&node.fields, "m_3A5CB679"), None)
                            .and_then(pool_number)
                            .filter(|s| s.is_finite() && *s > 1.0)
                    })
                    .unwrap_or(16.5)
                    .clamp(4.0, 45.0);
                let authored_dir =
                    match self.evaluate(id, node, field(&node.fields, "m_26DA7CF8"), None) {
                        Some(Value::Vector(v))
                            if v.iter().all(|c| c.is_finite()) && v[0].hypot(v[2]) > 1e-4 =>
                        {
                            Some(v)
                        }
                        _ => None,
                    };
                if let Some(fwd) = authored_dir
                    .or_else(|| self.world.forward(site.entity))
                    .or_else(|| self.world.authored_forward(site.entity))
                {
                    let hlen = fwd[0].hypot(fwd[2]).max(1e-4);
                    let _ = self.world.apply_velocity_ops(
                        site,
                        self.machine.frame,
                        &[crate::script_init::VelocityOp::SetHorizontalVector([
                            fwd[0] / hlen * speed,
                            fwd[2] / hlen * speed,
                        ])],
                    );
                }
            }
            4 => {
                // Wall impact detector during high-speed Charge / Punch
                if let (Some(pos), Some(fwd)) = (
                    self.world.position(site.entity),
                    self.world.forward(site.entity),
                ) {
                    let hlen = fwd[0].hypot(fwd[2]);
                    if hlen > 1e-4 {
                        let dir = [fwd[0] / hlen, 0.0, fwd[2] / hlen];
                        let origin = [pos[0], pos[1] + 1.0, pos[2]];
                        if let Some(Some(hit)) =
                            self.world
                                .spatial_ray_cast(site, self.machine.frame, origin, dir, 1.05)
                        {
                            if hit.normal[1].abs() < 0.5 {
                                self.post(id, Event::Finish(key.raw))?;
                            }
                        }
                    }
                }
            }
            5 => {
                // Pinned target carry: pull pinned actor along in front of charging caster.
                let pinned = ["m_target", "m_6716919E", "m_9F17FE89"]
                    .into_iter()
                    .find_map(|fname| {
                        match self.evaluate(id, node, field(&node.fields, fname), None) {
                            Some(Value::Entity(e))
                                if script_expr::valid_entity(e) && e != site.entity =>
                            {
                                Some(e)
                            }
                            _ => None,
                        }
                    });
                if let (Some(target), Some(caster_pos), Some(fwd)) = (
                    pinned,
                    self.world.position(site.entity),
                    self.world
                        .forward(site.entity)
                        .or_else(|| self.world.authored_forward(site.entity)),
                ) {
                    if let Some(target_pos) = self.world.position(target) {
                        let hlen = fwd[0].hypot(fwd[2]).max(1e-4);
                        let pin_spot = [
                            caster_pos[0] + fwd[0] / hlen * 1.35,
                            caster_pos[1],
                            caster_pos[2] + fwd[2] / hlen * 1.35,
                        ];
                        let pull = [
                            (pin_spot[0] - target_pos[0]).clamp(-22.0, 22.0) * 8.0,
                            (pin_spot[1] - target_pos[1]).clamp(-8.0, 8.0) * 4.0,
                            (pin_spot[2] - target_pos[2]).clamp(-22.0, 22.0) * 8.0,
                        ];
                        let _ = self
                            .world
                            .queue_modify_health(site, target, 0.0, Some(pull));
                    }
                }
            }
            7 => {
                // Sustained directional dash
                if let Some(Value::Vector(dir)) =
                    self.evaluate(id, node, field(&node.fields, "m_direction"), None)
                {
                    let speed = self
                        .evaluate(id, node, field(&node.fields, "m_speed"), None)
                        .and_then(pool_number)
                        .filter(|v| v.is_finite() && *v > 0.0)
                        .unwrap_or(14.0)
                        .clamp(1.0, 35.0);
                    let len = dir[0].hypot(dir[1]).hypot(dir[2]).max(1e-4);
                    let vel = [
                        dir[0] / len * speed,
                        dir[1] / len * speed,
                        dir[2] / len * speed,
                    ];
                    let _ = self.world.apply_velocity_ops(
                        site,
                        self.machine.frame,
                        &[crate::script_init::VelocityOp::SetVector(vel)],
                    );
                }
            }
            _ => {
                if node.class == "STU_6154B909" {
                    let _ = self.evaluate(id, node, field(&node.fields, "m_4999D53A"), None);
                    let _ = self.evaluate(id, node, field(&node.fields, "m_62F6795F"), None);
                    let _ = self.world.apply_velocity_ops(
                        site,
                        self.machine.frame,
                        &[crate::script_init::VelocityOp::SetSpeed {
                            axis: crate::script_init::VelocityAxis::Vertical,
                            value: 0.0,
                        }],
                    );
                    return Ok(());
                }
                // Seismic Slam / Air arc control: steer forward and finish on ground contact.
                let target_pt =
                    match self.evaluate(id, node, field(&node.fields, "m_2A7E6E17"), None) {
                        Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => Some(v),
                        _ => None,
                    };
                let pos = self.world.position(site.entity);
                let dir = target_pt
                    .zip(pos)
                    .and_then(|(dst, src)| {
                        let dx = dst[0] - src[0];
                        let dz = dst[2] - src[2];
                        let hlen = dx.hypot(dz);
                        (hlen > 0.25).then_some([dx / hlen, 0.0, dz / hlen])
                    })
                    .or_else(|| self.world.forward(site.entity))
                    .or_else(|| self.world.authored_forward(site.entity))
                    .unwrap_or([0.0, 0.0, 1.0]);
                let hlen = dir[0].hypot(dir[2]).max(1e-4);
                let _ = self.world.apply_velocity_ops(
                    site,
                    self.machine.frame,
                    &[crate::script_init::VelocityOp::SetHorizontalVector([
                        dir[0] / hlen * 14.5,
                        dir[2] / hlen * 14.5,
                    ])],
                );
                let started_ms = self
                    .state(key)
                    .map_or(self.machine.now_ms, |state| state.start_ms);
                let elapsed_ms = self.machine.now_ms.saturating_sub(started_ms);
                let grounded_now = self
                    .world
                    .movement_sample(site)
                    .is_some_and(|sample| sample.air_ticks == 0)
                    || pos.is_some_and(|p| {
                        self.world
                            .spatial_ray_cast(
                                site,
                                self.machine.frame,
                                [p[0], p[1] + 0.35, p[2]],
                                [0.0, -1.0, 0.0],
                                0.42,
                            )
                            .flatten()
                            .is_some()
                    });
                if (elapsed_ms >= 112 && grounded_now) || elapsed_ms >= 1400 {
                    let _ = self.world.apply_velocity_ops(
                        site,
                        self.machine.frame,
                        &[crate::script_init::VelocityOp::SetHorizontalVector([
                            0.0, 0.0,
                        ])],
                    );
                    self.post(id, Event::Finish(key.raw))?;
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn refresh_placement_cast(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let origin = match self.evaluate(id, node, field(&node.fields, "m_position"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            _ => self
                .world
                .body_eye_position(site.entity)
                .or_else(|| self.world.position(site.entity))
                .unwrap_or([0.0; 3]),
        };
        let direction = match self
            .evaluate(id, node, field(&node.fields, "m_direction"), None)
            .or_else(|| self.evaluate(id, node, field(&node.fields, "m_7BA28FCB"), None))
        {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => {
                let len = v[0].hypot(v[1]).hypot(v[2]);
                if len > 1e-4 {
                    [v[0] / len, v[1] / len, v[2] / len]
                } else {
                    self.world
                        .authored_forward(site.entity)
                        .unwrap_or([0.0, 0.0, 1.0])
                }
            }
            _ => self
                .world
                .authored_forward(site.entity)
                .or_else(|| self.world.forward(site.entity))
                .unwrap_or([0.0, 0.0, 1.0]),
        };
        let range = ["m_BA42208A", "m_5154A0FD", "m_width"]
            .iter()
            .find_map(|f| {
                self.evaluate(id, node, field(&node.fields, f), None)
                    .and_then(pool_number)
            })
            .filter(|r| r.is_finite() && *r > 0.5)
            .unwrap_or(30.0)
            .clamp(1.0, 60.0);
        let hit = self
            .world
            .spatial_ray_cast(site, self.machine.frame, origin, direction, range)
            .flatten();
        let (now_valid, pt, norm) = if let Some(h) = hit {
            let p = [
                origin[0] + direction[0] * h.distance,
                origin[1] + direction[1] * h.distance,
                origin[2] + direction[2] * h.distance,
            ];
            (true, p, h.normal)
        } else {
            let fallback_dist = range.min(12.0);
            let p = [
                origin[0] + direction[0] * fallback_dist,
                self.world
                    .position(site.entity)
                    .map_or(origin[1], |pos| pos[1]),
                origin[2] + direction[2] * fallback_dist,
            ];
            (true, p, [0.0, 1.0, 0.0])
        };
        for (fname, val) in [
            ("m_B747F285", Value::Vector(pt)),
            ("m_179FED70", Value::Vector(pt)),
            ("m_182435FB", Value::Vector(pt)),
            ("m_264326B3", Value::Vector(pt)),
            ("m_9AC8B314", Value::Vector(norm)),
            ("m_2B8E9543", Value::Vector(norm)),
            ("m_1EB0DDDA", Value::Bool(now_valid)),
            ("m_AB733228", Value::Bool(now_valid)),
            ("m_CC27E08A", Value::Bool(now_valid)),
        ] {
            if let Ok(Some(out_var)) = script_expr::lvalue(field(&node.fields, fname)) {
                let target_key = source_key(id, out_var);
                if !same(&self.machine.value(target_key, None), &val) {
                    self.write(target_key, val)?;
                }
            }
        }
        let was_valid = matches!(
            self.state(key).map(|s| s.kind),
            Some(StateKind::PlacementCast { valid: true })
        );
        if now_valid != was_valid {
            if let Some(state) = self.state_mut(key) {
                state.kind = StateKind::PlacementCast { valid: now_valid };
            }
            if now_valid {
                self.follow(id, node, "m_F6955E81", Some(key), 0)?;
                self.follow(id, node, "m_9B77AA38", Some(key), 0)?;
            } else {
                self.follow(id, node, "m_425337C6", Some(key), 0)?;
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_teleport_effect(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        if node.class == "STU_746F8777" {
            let dest = match self.evaluate(id, node, field(&node.fields, "m_AF774F7D"), None) {
                Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => Some(v),
                _ => match self.evaluate(id, node, field(&node.fields, "m_target"), None) {
                    Some(Value::Entity(e)) if script_expr::valid_entity(e) && e != site.entity => {
                        self.world.position(e)
                    }
                    _ => None,
                },
            };
            if let Some(dest) = dest {
                if let (Some(reloc), Some(frame)) = (
                    crate::script_init::Relocation::new(dest),
                    self.machine.frame.checked_add(1),
                ) {
                    let _ = self.world.queue_scripted_motion(
                        site,
                        frame,
                        crate::script_init::ScriptedMotion::Teleport(reloc),
                    );
                }
            }
        } else {
            // `STUStatescriptStateBlinkEffect`: evaluate start/end `STU_B8F4347B` effect descriptors
            for fname in ["m_3024B8FC", "m_FCA24F07"] {
                let desc = field(&node.fields, fname);
                let eff = field(desc, "m_58D56DD4");
                if !eff.is_null() {
                    let _ = self.evaluate(id, node, eff, None);
                }
                let ent = field(desc, "m_3E3E203A");
                if !ent.is_null() {
                    let _ = self.evaluate(id, node, ent, None);
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn tick_proximity_trigger(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let origin_entity = match self.evaluate(id, node, field(&node.fields, "m_56B8547E"), None) {
            Some(Value::Entity(e)) if script_expr::valid_entity(e) => e,
            _ => site.entity,
        };
        let offset = match self.evaluate(id, node, field(&node.fields, "m_BD631144"), None) {
            Some(Value::Vector(v)) if v.iter().all(|c| c.is_finite()) => v,
            _ => [0.0; 3],
        };
        let radius = self
            .evaluate(id, node, field(&node.fields, "m_radius"), None)
            .or_else(|| self.evaluate(id, node, field(&node.fields, "m_21A537D3"), None))
            .and_then(pool_number)
            .filter(|r| r.is_finite() && *r > 0.0)
            .unwrap_or(3.0)
            .clamp(0.25, 30.0);
        static HOSTILE_PROX_FILTER: std::sync::LazyLock<Json> =
            std::sync::LazyLock::new(|| serde_json::json!({ "m_66747E7D": 2, "m_89831602": 1 }));
        let hits = self
            .world
            .tracked_targets(site, origin_entity, offset, radius, &HOSTILE_PROX_FILTER)
            .unwrap_or_default();
        let was_triggered = matches!(
            self.state(key).map(|s| s.kind),
            Some(StateKind::ProximityTrigger { triggered: true })
        );
        if let Some(&first) = hits.first() {
            if !was_triggered {
                if let Ok(Some(out_var)) = script_expr::lvalue(field(&node.fields, "m_CB6ABF9A")) {
                    self.write(source_key(id, out_var), Value::Entity(first))?;
                }
                if let Some(state) = self.state_mut(key) {
                    state.kind = StateKind::ProximityTrigger { triggered: true };
                }
                self.follow(id, node, "m_6C433538", Some(key), 0)?;
            }
        } else if was_triggered {
            if let Some(state) = self.state_mut(key) {
                state.kind = StateKind::ProximityTrigger { triggered: false };
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_entity_status(
        &mut self,
        key: StateKey,
        node: &Node,
        mode: u8,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        match mode {
            1 => {
                // `STU_C5D842D8`: Fortify / CC-immunity status (Orisa / Wrecking Ball)
                let _ = self.world.set_combat_mod(
                    site,
                    Some(crate::script_init::CombatModEntry {
                        stat: 0,
                        multiplicative: true,
                        value: 1.0,
                        cc_immune: true,
                    }),
                );
            }
            2 => {
                // `STU_B63ECEB8`: Contact-set entity link (Moira Biotic Grasp)
                let _ = self.evaluate(id, node, field(&node.fields, "m_CA7AB946"), None);
                self.register_tick(key, self.machine.now_ms);
            }
            3 => {
                // `STU_75C1D62C`: Dynamic value sync
                let val = self
                    .evaluate(id, node, field(&node.fields, "m_value"), None)
                    .or_else(|| self.evaluate(id, node, field(&node.fields, "m_814F8EC3"), None));
                if let (Some(v), Ok(Some(out_var))) =
                    (val, script_expr::lvalue(field(&node.fields, "m_D6968981")))
                {
                    self.write(source_key(id, out_var), v)?;
                }
            }
            _ => {
                // Evaluate authored target / scalar / dynamic inputs so watched links stay current.
                for fname in [
                    "m_target",
                    "m_BBBF2499",
                    "m_00D49978",
                    "m_D7AC2990",
                    "m_F2E6B51C",
                    "m_BD2A95EE",
                    "m_15C19570",
                    "m_F35B6FEA",
                ] {
                    let f = field(&node.fields, fname);
                    if !f.is_null() {
                        let _ = self.evaluate(id, node, f, None);
                    }
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_ux_presenter(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        for fname in [
            "m_entityID",
            "m_58492C31",
            "m_F56F4985",
            "m_4777D5EE",
            "m_83DD0ED5",
            "m_45017CEA",
        ] {
            let f = field(&node.fields, fname);
            if !f.is_null() {
                let _ = self.evaluate(id, node, f, None);
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_sync_vars(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        // Evaluate and write synced variable entries (`m_values`, `m_vars`, `m_entries`, `m_value`).
        for list_field in ["m_values", "m_vars", "m_entries"] {
            for item in array_field(&node.fields, list_field) {
                if let Some(obj) = item.as_object() {
                    let val_cfg = obj
                        .get("m_value")
                        .or_else(|| obj.get("m_source"))
                        .unwrap_or(&Json::Null);
                    let out_cfg = obj
                        .get("m_out_Var")
                        .or_else(|| obj.get("m_26E3B366"))
                        .or_else(|| obj.get("m_target"))
                        .unwrap_or(&Json::Null);
                    if !val_cfg.is_null() {
                        if let Some(val) = self.evaluate(id, node, val_cfg, Some(key)) {
                            if let Ok(Some(lval)) = script_expr::lvalue(out_cfg) {
                                self.write(source_key(id, lval), val)?;
                            }
                        }
                    }
                }
            }
        }
        let val_cfg = field(&node.fields, "m_value");
        if !val_cfg.is_null() {
            if let Some(val) = self.evaluate(id, node, val_cfg, Some(key)) {
                for dst in ["m_26E3B366", "m_out_Var", "m_A07336C2"] {
                    if let Ok(Some(lval)) = script_expr::lvalue(field(&node.fields, dst)) {
                        self.write(source_key(id, lval), val.clone())?;
                    }
                }
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_hero_override(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        for fname in ["m_entity", "m_hero", "m_C16F72F6"] {
            let f = field(&node.fields, fname);
            if !f.is_null() {
                let _ = self.evaluate(id, node, f, None);
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_ai_tactic(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        for fname in [
            "m_89AABA94",
            "m_radius",
            "m_height",
            "m_AE1D56F0",
            "m_BE0AB214",
            "m_F4B6197E",
            "m_CA523921",
        ] {
            let f = field(&node.fields, fname);
            if !f.is_null() {
                let _ = self.evaluate(id, node, f, None);
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn begin_field_driven_state(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        // Evaluate any authored condition, value/variable outputs, game messages, or contact effects.
        let cond_cfg = field(&node.fields, "m_condition");
        if !cond_cfg.is_null() {
            let _ = self.evaluate(id, node, cond_cfg, Some(key));
        }
        if let Some(msg) = node.game_message_id() {
            if let Some(params) = self.game_message_parameters(id, node)? {
                let _ = self.world.route_game_message(site, msg, &params);
            }
        }
        let val_cfg = field(&node.fields, "m_value");
        if !val_cfg.is_null() {
            if let Some(val) = self.evaluate(id, node, val_cfg, None) {
                for dst in ["m_out_Var", "m_26E3B366"] {
                    if let Ok(Some(lval)) = script_expr::lvalue(field(&node.fields, dst)) {
                        self.write(source_key(id, lval), val.clone())?;
                    }
                }
            }
        }
        for fname in [
            "m_entity",
            "m_target",
            "m_targets",
            "m_AC2333F7",
            "m_effect",
            "m_amount",
            "m_index",
            "m_priority",
        ] {
            let f = field(&node.fields, fname);
            if !f.is_null() {
                let _ = self.evaluate(id, node, f, None);
            }
        }
        Ok(())
    }

    #[inline(never)]
    pub(in crate::script_init) fn run_retail_action(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        depth: usize,
        behavior: super::retail_catalog::RetailActionBehavior,
    ) -> Result<bool, InitFault> {
        use super::retail_catalog::RetailActionBehavior;
        let site = self.site(id, node);
        match behavior {
            RetailActionBehavior::RemoteSetVar => {
                // `STU_0BDBC7B2`: write `m_value` into `m_out_Var` on `m_entity` (or self).
                let target_entity =
                    match self.evaluate(id, node, field(&node.fields, "m_entity"), caller) {
                        Some(Value::Entity(e)) if script_expr::valid_entity(e) => e,
                        _ => site.entity,
                    };
                if let Some(val) = self.evaluate(id, node, field(&node.fields, "m_value"), caller) {
                    if let Ok(Some(out_var)) = script_expr::lvalue(field(&node.fields, "m_out_Var"))
                    {
                        if target_entity == site.entity {
                            self.write(source_key(id, out_var), val)?;
                        }
                    }
                }
            }
            RetailActionBehavior::SendGameMessage => {
                // `STU_1470EF32` / message-bearing actions: dispatch authored GameMessage + params.
                if let Some(msg) = node.game_message_id() {
                    if let Some(params) = self.game_message_parameters(id, node)? {
                        let _ = self.world.route_game_message(site, msg, &params);
                        let _ = self.machine.queue_entries_from(
                            self.graphs,
                            None,
                            Some(site.entity),
                            msg,
                            params,
                            self.machine.now_ms,
                        )?;
                    }
                }
            }
            RetailActionBehavior::EffectTrigger => {
                // `STU_E0687628`: evaluate effect asset & emit contact effect if targets resolve.
                let effect_guid = self
                    .evaluate(id, node, field(&node.fields, "m_AC2333F7"), caller)
                    .or_else(|| self.evaluate(id, node, field(&node.fields, "m_effect"), caller))
                    .and_then(|v| match v {
                        Value::Asset(g) if g != 0 => Some(g),
                        _ => None,
                    });
                if let Some(guid) = effect_guid {
                    let targets =
                        match self.evaluate(id, node, field(&node.fields, "m_targets"), caller) {
                            Some(Value::Array(items)) => items
                                .into_iter()
                                .filter_map(|v| match v {
                                    Value::Entity(e) if script_expr::valid_entity(e) => Some(e),
                                    _ => None,
                                })
                                .collect(),
                            Some(Value::Entity(e)) if script_expr::valid_entity(e) => vec![e],
                            _ => vec![site.entity],
                        };
                    let _ = self
                        .world
                        .emit_contact_effect(site, site.entity, guid, &targets);
                }
            }
            RetailActionBehavior::EntityCommand => {
                // Evaluate target entity / team / slot / value fields.
                for fname in [
                    "m_targets",
                    "m_entityID",
                    "m_entity",
                    "m_target",
                    "m_67465E75",
                    "m_slot",
                    "m_value",
                ] {
                    let f = field(&node.fields, fname);
                    if !f.is_null() {
                        let _ = self.evaluate(id, node, f, caller);
                    }
                }
            }
            RetailActionBehavior::FieldDrivenAction => {
                // Introspect generic authored fields: write `m_out_Var`/`m_value`, branch `m_condition`.
                let val_cfg = field(&node.fields, "m_value");
                if !val_cfg.is_null() {
                    if let Some(val) = self.evaluate(id, node, val_cfg, caller) {
                        for dst in ["m_out_Var", "m_26E3B366"] {
                            if let Ok(Some(lval)) = script_expr::lvalue(field(&node.fields, dst)) {
                                self.write(source_key(id, lval), val.clone())?;
                            }
                        }
                    }
                }
                let cond_cfg = field(&node.fields, "m_condition");
                if !cond_cfg.is_null() {
                    if let Some(cond) = self.evaluate(id, node, cond_cfg, caller) {
                        let branch = if truthy(&cond) {
                            "m_truePlug"
                        } else {
                            "m_falsePlug"
                        };
                        self.follow(id, node, branch, caller, depth)?;
                    }
                }
            }
        }
        self.follow(id, node, "m_outPlug", caller, depth)?;
        Ok(true)
    }
}
