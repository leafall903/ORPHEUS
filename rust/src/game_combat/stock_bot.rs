//! Minimal generic body StateScript host for non-encounter room bots.
//!
//! Used by Practice Range stock bots when the runtime pack carries authored
//! entity definitions (such as `0x0C18` -> `0x0D24` nameplates).

use std::{
    collections::{BTreeMap, HashMap},
    f64::consts::TAU,
    sync::Arc,
};

use serde_json::Value as Json;

use super::{
    RoomScriptRequest, RoomScriptRequests, RoomSpatialFrame,
    observed_projectiles::{ObservedShot, StockShotBatch},
};
use crate::{
    commands::Command,
    game_bots::Bot,
    game_projectiles::predicted_sync,
    mover::sample_movement_flags,
    script_codecs::CodecTable,
    script_enemy::projectile_pattern,
    script_expr::{EngineSetting, EntityRelation, is_zeroed_owner_selector, native_shape},
    script_graph::{GraphStore, graph_asset_index},
    script_init::{
        self, AiControlOverride, AiWeaponOverride, Component, HUD_MAX_HEALTH, InitFault, Issue,
        MAX_INIT_WORK, MovementSample, OWNER_HEALTH, RoomCue, RoomMessage, Root, Site,
        TargetTagEffect, TargetTagEffects, World,
    },
    script_runtime::held_button_mask,
    script_snapshot_wire::{SceneProjection, SnapshotEncoder, View},
    script_wire::{Value, Variables},
    vitals::{Death, PoolKind, Vitals},
    world::GAME_MODE_ENTITY,
};

#[cfg(test)]
mod tests;

const HUD_HEALTH: u32 = 1732;
const RESET_MESSAGE: u64 = 0x0240_0000_0000_0013;
const YAW_UNITS: f64 = 65_536.0;

#[derive(Clone)]
pub(crate) struct StockBotCatalog {
    pub(crate) graphs: Arc<GraphStore>,
    pub(crate) codecs: Arc<CodecTable>,
    pub(crate) mode: u64,
}

#[derive(Clone)]
pub(super) struct StockBotWorld {
    graphs: Arc<GraphStore>,
    mode: u64,
    entity: u32,
    hero: u64,
    body: u64,
    team: u8,
    dead: bool,
    position: [f64; 3],
    forward: [f64; 3],
    movement: Option<MovementSample>,
    walk_speed: f64,
    vitals: Vitals,
    target_tag_effects: TargetTagEffects,
    display_names: HashMap<Site, u64>,
    ai_control_overrides: BTreeMap<(u16, u16), AiControlOverride>,
    placement_graphs: Vec<u32>,
    spatial_frame: Option<Arc<RoomSpatialFrame>>,
    script_requests: RoomScriptRequests,
    velocity_changes: Vec<(u32, crate::mover::VelocityChange)>,
    voice_variant_counter: usize,
}

impl StockBotWorld {
    fn filter_flags(&self) -> u64 {
        1 | (0x0080_0000 << self.team) | if self.dead { 0x10 } else { 0 }
    }
}

impl World for StockBotWorld {
    fn admits_velocity_ops(&self, site: Site) -> bool {
        site.entity == self.entity && !self.dead
    }

    fn apply_velocity_ops(
        &mut self,
        site: Site,
        frame: u32,
        ops: &[crate::script_init::VelocityOp],
    ) -> bool {
        if site.entity != self.entity || self.dead {
            return false;
        }
        let mut applied = false;
        for &op in ops {
            let change = crate::mover::VelocityChange::from_script_op(op);
            if change.valid() && self.velocity_changes.len() < MAX_INIT_WORK {
                self.velocity_changes
                    .push((frame.saturating_add(1), change));
                applied = true;
            }
        }
        applied
    }

    fn queue_room_message(&mut self, site: Site, target: u32, message: RoomMessage) -> bool {
        site.entity == self.entity
            && !self.dead
            && self
                .spatial_frame
                .as_deref()
                .is_some_and(|frame| self.script_requests.queue_message(frame, target, message))
    }

    fn queue_room_cue(&mut self, site: Site, targets: &[u32], cue: RoomCue) -> bool {
        site.entity == self.entity
            && !self.dead
            && self.spatial_frame.as_deref().is_some_and(|frame| {
                self.script_requests
                    .queue_cue(frame, site.entity, targets, cue)
            })
    }

    fn actor_voice_available(&self, site: Site) -> bool {
        site.entity == self.entity
            && !self.dead
            && self
                .spatial_frame
                .as_deref()
                .is_some_and(|frame| frame.actor_voice_available(site.entity))
    }

    fn queue_actor_voice(
        &mut self,
        site: Site,
        criterion: u64,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        self.queue_actor_voice_with_audience(site, criterion, site.entity, None, None, choose);
    }

    fn queue_actor_voice_with_audience(
        &mut self,
        site: Site,
        criterion: u64,
        speaker: u32,
        subject: Option<u32>,
        audience: Option<&[u32]>,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        let Some(frame) = self.spatial_frame.as_deref() else {
            return;
        };
        if let Some((cue, listeners)) = frame.actor_voice_request_with_audience(
            speaker,
            crate::game_combat::ActorVoiceQuery::Criterion(criterion),
            subject,
            audience,
            choose,
        ) {
            self.script_requests
                .queue_speaker_cue(frame, site.entity, speaker, &listeners, cue);
        }
    }

    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity == GAME_MODE_ENTITY {
            return self
                .graphs
                .mode_variables(self.mode)
                .map(|variables| variables.get(&id).copied().map_or(Value::Null, Value::from));
        }
        if entity != self.entity {
            return None;
        }
        match id {
            OWNER_HEALTH | HUD_HEALTH => Some(Value::Float(self.vitals.total())),
            HUD_MAX_HEALTH => Some(Value::Float(self.vitals.maximum())),
            _ => None,
        }
    }

    fn known_position(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            return Some(self.position);
        }
        self.spatial_frame
            .as_deref()?
            .actor_pose(entity)
            .map(|(position, _)| position)
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.known_position(entity)
    }

    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            return Some(self.forward);
        }
        let (_, [yaw, pitch]) = self.spatial_frame.as_deref()?.actor_pose(entity)?;
        Some(crate::game_hitscan::direction_for_angles(yaw, pitch))
    }

    fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.position(entity)
    }

    fn authored_forward(&self, entity: u32) -> Option<[f64; 3]> {
        self.forward(entity)
    }

    fn body_bone_point(&mut self, entity: u32, bone: u64, local: [f64; 3]) -> Option<[f64; 3]> {
        if entity != self.entity {
            return self.spatial_frame.as_ref()?.actor_bone_point(
                &self.graphs,
                entity,
                bone,
                local,
            );
        }
        if self.dead {
            return None;
        }
        let point = if bone == 0 {
            local
        } else {
            self.graphs
                .bone_reference_point(self.body, 0, bone, local)?
        };
        crate::script_expr::body_space_point(self.position, Some(self.forward), point)
    }

    fn body_eye_position(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            return Some([
                self.position[0],
                self.position[1] + super::eye_height(self.hero, false),
                self.position[2],
            ]);
        }
        self.spatial_frame.as_deref()?.actor_eye_position(entity)
    }

    fn movement_sample(&self, site: Site) -> Option<MovementSample> {
        (site.entity == self.entity)
            .then_some(self.movement)
            .flatten()
    }

    fn entity_exists(&self, entity: u32) -> Option<bool> {
        if entity == GAME_MODE_ENTITY {
            return Some(self.graphs.mode_variables(self.mode).is_some());
        }
        (entity == self.entity).then_some(true)
    }

    fn entity_hero(&self, _site: Site, entity: u32) -> Option<u64> {
        (entity == self.entity).then_some(self.hero)
    }

    fn entity_definition(&self, _site: Site, entity: u32) -> Option<u64> {
        (entity == self.entity).then_some(self.body)
    }

    fn entity_team(&self, _site: Site, entity: u32) -> Option<i8> {
        i8::try_from(self.team)
            .ok()
            .filter(|_| entity == self.entity)
    }

    fn entity_filter_flags(&self, _site: Site, entity: u32) -> Option<u64> {
        (entity == self.entity).then(|| self.filter_flags())
    }

    fn has_target_tag(&self, _site: Site, entity: u32, tag: u64) -> Option<bool> {
        if entity != self.entity {
            return None;
        }
        if let Some(effect) = self.target_tag_effects.presence(tag) {
            return Some(effect);
        }
        let body = self.graphs.entity_body(self.body)?;
        Some(body.target_tags.as_ref()?.contains(&tag))
    }

    fn set_target_tag_effect(&mut self, site: Site, effect: TargetTagEffect, active: bool) -> bool {
        site.entity == self.entity && self.target_tag_effects.apply(effect, active)
    }

    fn set_display_name(&mut self, site: Site, entity: u32, name: u64, active: bool) -> bool {
        if site.entity != self.entity || entity != self.entity {
            return false;
        }
        if active {
            self.display_names.insert(site, name);
        } else {
            self.display_names.remove(&site);
        }
        true
    }

    fn engine_setting(&self, _site: Site, key: u32) -> EngineSetting {
        crate::body_ruleset::engine_setting(&self.graphs, self.mode, key)
    }

    fn local_command_present(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(false)
    }

    fn local_combat_context_available(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(false)
    }

    fn has_dynamic_map_rigid_bodies(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(false)
    }

    fn for_code_transport(&self, _site: Site) -> Option<bool> {
        Some(false)
    }

    fn voice_context(&mut self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then(|| self.actor_voice_available(site))
    }

    fn voice_manager(&mut self, site: Site) -> Option<bool> {
        self.voice_context(site)
    }

    fn voice_clock(&mut self, site: Site, now_ms: u64) -> Option<f64> {
        self.voice_context(site)
            .filter(|&ok| ok)
            .map(|_| now_ms as f64 / 1000.0)
    }

    fn voice_stimulus(
        &mut self,
        site: Site,
        guid: u64,
    ) -> Option<Option<crate::script_voice::Stimulus>> {
        if !self.actor_voice_available(site) {
            return None;
        }
        let playable = self
            .spatial_frame
            .as_deref()
            .is_some_and(|frame| frame.actor_voice_has_stimulus(site.entity, guid));
        Some(playable.then_some(crate::script_voice::Stimulus {
            guid,
            playable: true,
            mode: false,
        }))
    }

    fn voice_allow(&mut self, site: Site, stimulus: crate::script_voice::Stimulus) -> Option<bool> {
        self.actor_voice_available(site)
            .then_some(stimulus.playable)
    }

    fn voice_submit(
        &mut self,
        site: Site,
        stimulus: crate::script_voice::Stimulus,
        targets: &[u32],
        _actor: Option<&Value>,
    ) -> Result<Option<u32>, &'static str> {
        if !self.actor_voice_available(site) {
            return Ok(None);
        }
        if !targets.is_empty() && !targets.contains(&site.entity) {
            return Ok(Some(0));
        }
        let pick = self.voice_variant_counter;
        self.voice_variant_counter = self.voice_variant_counter.wrapping_add(1);
        let Some((cue, listeners)) = self.spatial_frame.as_deref().and_then(|frame| {
            frame.actor_voice_request(
                site.entity,
                crate::game_combat::ActorVoiceQuery::Stimulus(stimulus.guid),
                &mut |count| pick % count,
            )
        }) else {
            return Ok(Some(0));
        };
        Ok(Some(u32::from(self.queue_room_cue(site, &listeners, cue))))
    }

    fn voice_duration(&mut self, site: Site, handle: u32, guid: u64, _mode: bool) -> Option<f64> {
        if !self.actor_voice_available(site) || handle == 0 {
            return None;
        }
        Some(
            self.spatial_frame
                .as_deref()
                .and_then(|frame| frame.voice_line_duration(guid))
                .unwrap_or(1.0),
        )
    }

    fn voice_part(&mut self, site: Site, _number: u16) -> Option<f64> {
        self.actor_voice_available(site).then_some(0.0)
    }

    fn player_stats_available(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(false)
    }

    fn player_stats_service_available(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(false)
    }

    fn spatial_binding_owner_available(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(false)
    }

    fn outline_context_available(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(false)
    }

    fn body_active(&self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then_some(true)
    }

    fn body_health(&self, site: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
        (site.entity == self.entity).then(|| match kind {
            None if maximum => self.vitals.maximum(),
            None => self.vitals.total(),
            Some(_) => self.vitals.amount(kind, maximum),
        })
    }

    fn add_health_pool(
        &mut self,
        site: Site,
        kind: PoolKind,
        maximum: f64,
        current: f64,
    ) -> Option<u32> {
        (site.entity == self.entity)
            .then(|| self.vitals.add_pool(kind, maximum, current))
            .flatten()
    }

    fn remove_health_pool(&mut self, site: Site, pool: u32) -> Option<f64> {
        (site.entity == self.entity)
            .then(|| self.vitals.remove_pool(pool))
            .flatten()
    }

    fn health_pool_current(&self, site: Site, pool: u32) -> Option<f64> {
        (site.entity == self.entity)
            .then(|| self.vitals.pool_current(pool))
            .flatten()
    }

    fn add_temporary_shield(
        &mut self,
        site: Site,
        identifier: u64,
        amount: f64,
        stacked: bool,
    ) -> Option<u32> {
        (site.entity == self.entity)
            .then(|| {
                self.vitals
                    .add_temporary_shield(identifier, amount, stacked)
            })
            .flatten()
    }

    fn temporary_shield_current(&self, site: Site, identifier: u64) -> Option<f64> {
        (site.entity == self.entity).then(|| self.vitals.temporary_shield_current(identifier))
    }

    fn drain_temporary_shield(&mut self, site: Site, identifier: u64, amount: f64) -> Option<f64> {
        (site.entity == self.entity).then(|| self.vitals.drain_temporary_shield(identifier, amount))
    }

    fn related_entity(&mut self, site: Site, relation: EntityRelation, entity: u32) -> Option<u32> {
        if site.entity != self.entity {
            return None;
        }
        match relation {
            EntityRelation::PossessedBody
                if entity == self.entity && self.placement_graphs.contains(&site.graph) =>
            {
                Some(self.entity)
            }
            EntityRelation::OwningController
            | EntityRelation::CurrentPossessor
            | EntityRelation::AiControlEntity
                if entity == self.entity =>
            {
                Some(0)
            }
            EntityRelation::PossessedBody | EntityRelation::ControllerBodyField if entity == 0 => {
                Some(0)
            }
            _ => None,
        }
    }

    fn ai_walk_speed(&self, site: Site) -> Option<f64> {
        (site.entity == self.entity && !self.dead && self.placement_graphs.contains(&site.graph))
            .then_some(self.walk_speed)
    }

    fn selects_ai_behavior(&mut self, site: Site) -> Option<bool> {
        (site.entity == self.entity).then(|| self.placement_graphs.contains(&site.graph))
    }

    fn ai_control_override(
        &mut self,
        site: Site,
        record: AiControlOverride,
        active: bool,
    ) -> Option<bool> {
        if site.entity != self.entity || record.target != self.entity {
            return None;
        }
        let key = (site.instance, site.node?);
        self.ai_control_overrides.remove(&key);
        if active {
            self.ai_control_overrides.insert(key, record);
        }
        Some(true)
    }

    fn ai_weapon_override(
        &mut self,
        site: Site,
        record: AiWeaponOverride,
        _active: bool,
    ) -> Option<bool> {
        (site.entity == self.entity && record.target == self.entity && site.node.is_some())
            .then_some(false)
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        if site.entity != self.entity {
            return None;
        }
        let class = config.get("$").and_then(Json::as_str)?;
        match class {
            "STU_37F20D24"
                if native_shape(config, class, Some("m_resourceKey"))
                    && config.get("m_resourceKey").is_none_or(Json::is_null) =>
            {
                Some(Value::Asset(self.mode))
            }
            "STU_A3DEF368"
                if native_shape(config, class, None)
                    && self.entity_exists(GAME_MODE_ENTITY) == Some(true) =>
            {
                Some(Value::Entity(GAME_MODE_ENTITY))
            }
            "STU_E72F6348"
                if self.entity_exists(GAME_MODE_ENTITY) == Some(false)
                    && native_shape(config, class, Some("m_entity"))
                    && config
                        .get("m_entity")
                        .is_some_and(|mode| native_shape(mode, "STU_A3DEF368", None)) =>
            {
                Some(Value::Bool(false))
            }
            "STU_4A101787" | "STU_A77AC2AC" | "STU_D3EB8D87"
                if native_shape(config, class, None) =>
            {
                Some(Value::Bool(false))
            }
            "STU_295D117D" => Some(Value::Null),
            "STU_ED45C5E8"
                if is_zeroed_owner_selector(config)
                    || crate::script_expr::native_fields(
                        config,
                        &[
                            "m_B2ABF4FF",
                            "m_2BA6230F",
                            "m_5912C8DF",
                            "m_5A72EEF5",
                            "m_C9E6F46B",
                        ],
                    ) =>
            {
                Some(Value::Null)
            }
            _ => None,
        }
    }
}

pub(crate) struct StockBotScript {
    component: Component,
    world: StockBotWorld,
    graphs: Arc<GraphStore>,
    codecs: Arc<CodecTable>,
    frame: u32,
    weapon_inputs: BTreeMap<i64, u32>,
    faulted: bool,
}

#[derive(Clone, Copy)]
pub(super) enum BodyEntry {
    Initial,
    Respawn,
}

impl StockBotScript {
    pub(super) fn status_script(
        &mut self,
        op: super::status_scripts::StatusScriptOp<'_>,
    ) -> Result<Option<u16>, String> {
        if self.faulted {
            return Ok(None);
        }
        let mut world = self.world.clone();
        let outcome = op
            .run(&mut self.component, &mut world)
            .map_err(|error| error.to_string())?;
        self.world = world;
        Ok(outcome)
    }

    pub(super) fn take_velocity_changes(&mut self) -> Vec<(u32, crate::mover::VelocityChange)> {
        std::mem::take(&mut self.world.velocity_changes)
    }
    pub(super) fn start(
        catalog: &StockBotCatalog,
        bot: &Bot,
        tick: u32,
        entry: BodyEntry,
    ) -> Option<Self> {
        let entity_body = catalog.graphs.entity_body(bot.spawn.body)?;
        let mut roots = match script_init::body_roots(entity_body) {
            Ok(roots) => roots,
            Err(error) => {
                eprintln!("stock bot {:08X} roots unavailable: {error}", bot.entity);
                return None;
            }
        };
        let has_body_graph = roots
            .iter()
            .any(|root| !root.is_server_only() && root.graph == crate::game_pools::BODY_GRAPH);
        let initial = Variables::from([
            (OWNER_HEALTH, Value::Float(bot.spawn.health)),
            (HUD_HEALTH, Value::Float(bot.spawn.health)),
            (HUD_MAX_HEALTH, Value::Float(bot.spawn.health)),
        ]);
        let weapon_inputs = entity_body
            .ai_weapon_buttons
            .iter()
            .filter_map(|(slot, button)| {
                let slot = slot.parse::<i64>().ok()?;
                let button = u8::try_from(button.as_u64()?).ok()?;
                Some((slot, held_button_mask(button)?))
            })
            .collect::<BTreeMap<_, _>>();
        let mut placement_graphs = Vec::new();
        if !weapon_inputs.is_empty() {
            for index in bot
                .spawn
                .placement_graphs
                .iter()
                .copied()
                .filter_map(graph_asset_index)
            {
                if !roots.iter().any(|root| root.graph == index)
                    && catalog
                        .graphs
                        .graph(index)
                        .is_some_and(|graph| graph.drives_ai_weapon_input())
                {
                    roots.push(Root::server_only(index));
                    placement_graphs.push(index);
                }
            }
        }
        let mut world = StockBotWorld {
            graphs: Arc::clone(&catalog.graphs),
            mode: catalog.mode,
            entity: bot.entity,
            hero: bot.spawn.hero,
            body: bot.spawn.body,
            team: bot.spawn.team,
            dead: bot.dead,
            position: bot.position,
            forward: forward_from_yaw(bot.yaw),
            movement: sample_bot_movement(bot, tick),
            walk_speed: bot.walk_speed(),
            vitals: if has_body_graph {
                Vitals::empty()
            } else {
                Vitals::new(bot.spawn.health, 0.0, 0.0)
            },
            target_tag_effects: TargetTagEffects::default(),
            display_names: HashMap::new(),
            ai_control_overrides: BTreeMap::new(),
            placement_graphs,
            spatial_frame: None,
            script_requests: RoomScriptRequests::default(),
            velocity_changes: Vec::new(),
            voice_variant_counter: 0,
        };
        let component = match Component::start_with_public_schema(
            Arc::clone(&catalog.graphs),
            bot.entity,
            initial,
            roots,
            tick,
            &mut world,
            MAX_INIT_WORK,
        ) {
            Ok(component) => component,
            Err(error) => {
                eprintln!("stock bot {:08X} startup stopped: {error}", bot.entity);
                return None;
            }
        };
        let mut script = Self {
            component,
            world,
            graphs: Arc::clone(&catalog.graphs),
            codecs: Arc::clone(&catalog.codecs),
            frame: tick,
            weapon_inputs,
            faulted: false,
        };
        // Both initial spawns and respawns deliver RESET_MESSAGE (0x13) to the
        // full body entity so 0BA8:8 sets shared_var[1226] (combat anim layer)
        // and placement graphs (0B92) arm cleanly on both teams.
        let _ = entry;
        let queued =
            script
                .component
                .queue_entity_message_from(bot.entity, RESET_MESSAGE, Variables::new());
        let result = queued.and_then(|count| {
            if count == 0 {
                Ok(())
            } else {
                script.component.run_now(&mut script.world)
            }
        });
        script.check_projection(result);
        Some(script)
    }

    pub(super) fn receive_host_event(
        &mut self,
        event: crate::script_init::HostEvent,
        context: crate::script_init::HostHealthContext,
    ) {
        if self.faulted {
            return;
        }
        let result = self
            .component
            .queue_host_event_with_context(event, context)
            .and_then(|count| {
                if count == 0 {
                    Ok(())
                } else {
                    self.component.run_now(&mut self.world)
                }
            });
        self.check_projection(result);
    }

    pub(super) fn on_damage(
        &mut self,
        remaining_health: f64,
        source: Death,
        tags: &crate::script_init::HitTags,
    ) {
        if self.faulted {
            return;
        }
        let current = self.world.vitals.total();
        let dealt = (current - remaining_health).max(0.0);
        if dealt > 0.0 {
            self.world.vitals.damage(dealt, source);
        }
        let result = self.publish_health(remaining_health).and_then(|()| {
            if dealt > 0.0 && !self.world.dead {
                // Retail damage-taken message (`0x0240000000000011`, params 0x13=amount,
                // 0x3D=hurt-flinch tags, 0x65=hit direction vector): drives `0x0E8C:14`
                // 3rd-person hurt-flinch (`Anim 0x0174`) and `0x08B6:6` wobble without
                // faulting `0x0E8C:49` (`0x03FA` vector sync var).
                use crate::game_combat_messages::{DAMAGE_TAKEN_MESSAGE, damage_taken_parameters};
                let default_dir = [-self.world.forward[0], 0.0, -self.world.forward[2]];
                let hit_dir = self
                    .world
                    .spatial_frame
                    .as_deref()
                    .and_then(|frame| frame.actor_eye_position(source.killer))
                    .map(|origin| {
                        let dx = self.world.position[0] - origin[0];
                        let dz = self.world.position[2] - origin[2];
                        let len = (dx * dx + dz * dz).sqrt();
                        if len > 1e-4 {
                            [dx / len, 0.0, dz / len]
                        } else {
                            default_dir
                        }
                    })
                    .unwrap_or(default_dir);
                let params = damage_taken_parameters(dealt, hit_dir, source.killer, tags);
                if self.component.queue_entity_message_from(
                    source.killer,
                    DAMAGE_TAKEN_MESSAGE,
                    params,
                )? > 0
                {
                    self.component.run_now(&mut self.world)?;
                }
            }
            Ok(())
        });
        self.check_projection(result);
    }

    pub(super) fn on_heal(&mut self, current_health: f64) {
        if self.faulted || self.world.dead {
            return;
        }
        self.world
            .vitals
            .heal(current_health - self.world.vitals.total());
        let result = self.publish_health(current_health);
        self.check_projection(result);
    }

    fn publish_health(&mut self, health: f64) -> Result<(), InitFault> {
        let health = Value::Float(health);
        self.component
            .set_entity_variable(OWNER_HEALTH, health.clone(), &mut self.world)
            .and_then(|()| {
                self.component
                    .set_entity_variable(HUD_HEALTH, health, &mut self.world)
            })
    }

    pub(super) fn on_death(&mut self, source: Death, impact: Option<&crate::game_hitscan::Impact>) {
        if self.faulted || self.world.dead {
            return;
        }
        self.world.dead = true;
        self.component.set_owner_dead(true);
        let next_frame = source.frame.max(self.frame + 1);
        if let Err(error) = self.component.advance_frame(next_frame, &mut self.world) {
            self.stop_script(error);
            return;
        }
        self.frame = next_frame;
        if let Err(error) = self.publish_health(0.0) {
            self.stop_script(error);
            return;
        }
        let result = crate::game_hitscan::queue_script_death(
            &mut self.component,
            source.killer,
            impact,
            Some(source.frame),
        )
        .and_then(|_| self.component.run_now(&mut self.world));
        self.check_projection(result);
    }

    pub(super) fn target_tags(&self) -> crate::script_init::TargetTagSnapshot {
        static EMPTY_TAGS: std::sync::LazyLock<std::collections::BTreeSet<u64>> =
            std::sync::LazyLock::new(std::collections::BTreeSet::new);
        let base = self.graphs.entity_body(self.world.body);
        self.world.target_tag_effects.snapshot(Some(
            base.and_then(|body| body.target_tags.as_ref())
                .unwrap_or(&EMPTY_TAGS),
        ))
    }

    pub(super) fn script_state(&self) -> crate::script_entity_state::EntityScriptState {
        use crate::script_entity_state::EntityScriptState;
        self.projection()
            .map_or(EntityScriptState::Unavailable, |projection| {
                EntityScriptState::Running(Arc::new(projection.snapshot.variables.clone()))
            })
    }

    pub(super) fn set_spatial_frame(&mut self, frame: Arc<RoomSpatialFrame>) {
        self.world.spatial_frame = Some(frame);
    }

    pub(super) fn accepts_room_messages(&self) -> bool {
        !self.faulted && !self.world.dead
    }

    pub(super) fn receive_room_message(&mut self, sender: u32, message: RoomMessage) {
        if self.accepts_room_messages() {
            let _ = self.component.queue_room_message_from(sender, message);
        }
    }

    pub(super) fn take_script_requests(&mut self) -> Vec<RoomScriptRequest> {
        let requests = self.world.script_requests.take();
        if self.accepts_room_messages() {
            requests
        } else {
            Vec::new()
        }
    }

    pub(super) fn walk_request(&self) -> Option<([f64; 3], f64)> {
        if self.faulted || self.world.dead || self.world.placement_graphs.is_empty() {
            return None;
        }
        let mut requests = self
            .component
            .snapshot()?
            .instances
            .values()
            .filter(|instance| instance.is_server_only())
            .flat_map(|instance| instance.states.values())
            .filter_map(|state| state.move_request());
        let request = requests.next()?;
        requests.next().is_none().then_some(request)
    }

    pub(super) fn advance(&mut self, tick: u32, bot: &Bot) {
        self.world.position = bot.position;
        self.world.forward = forward_from_yaw(bot.yaw);
        self.world.movement = sample_bot_movement(bot, tick);
        if self.faulted || tick <= self.frame {
            return;
        }
        self.frame = tick;
        let result = if !self.world.placement_graphs.is_empty() && !bot.dead {
            let buttons = if bot.is_stunned(tick) {
                0
            } else {
                self.component
                    .ai_fire_slots()
                    .filter_map(|slot| self.weapon_inputs.get(&slot))
                    .fold(0, |buttons, mask| buttons | mask)
            };
            let command = Command {
                frame: tick,
                yaw: bot.yaw.round_ties_even() as i32,
                buttons,
                ..Command::default()
            };
            self.component.advance_command(command, &mut self.world)
        } else {
            self.component.advance_frame(tick, &mut self.world)
        }
        .and_then(|()| self.component.poll_voice(&mut self.world));
        self.check_projection(result);
    }

    pub(super) fn take_projectiles(&mut self, tick: u32, bot: &Bot) -> Vec<StockShotBatch> {
        let shots = self.component.take_reported_shots().unwrap_or_default();
        if self.faulted || bot.dead || bot.is_stunned(tick) {
            return Vec::new();
        }
        let [x, y, z] = bot.position;
        let origin = [x, y + super::eye_height(bot.spawn.hero, false), z];
        let forward = self.world.forward;
        let mut projectiles = Vec::new();
        let mut projected = None;
        for report in shots {
            let shot = report.event;
            let Some(node) = self.component.shot_node(&shot) else {
                continue;
            };
            let mut evaluate = |value: &Json| {
                self.component
                    .evaluate_shot_config(&shot, value, &mut self.world)
            };
            let Some(pattern) =
                projectile_pattern(&node.fields, forward, &shot, node.pos, &mut evaluate)
            else {
                continue;
            };
            let presentation = pattern
                .definition_and_lifetime()
                .filter(|_| {
                    node.field("m_49CBA3BC").is_none_or(|value| {
                        value.is_null() || value.as_array().is_some_and(Vec::is_empty)
                    }) && predicted_sync(node, &mut evaluate).unwrap_or(false)
                })
                .and_then(|(definition, lifetime)| {
                    let snapshot = projected
                        .get_or_insert_with(|| {
                            let projection = self.projection()?;
                            projection
                                .encoder
                                .project(projection.snapshot, View::Observer)
                                .ok()
                        })
                        .as_ref()?;
                    let volley = snapshot.freeze_volley_with_states(
                        shot,
                        report.presentation,
                        &report.presentation_states,
                    )?;
                    Some(ObservedShot::new(
                        volley, definition, origin, forward, tick, lifetime,
                    ))
                });
            let flights: Vec<_> = pattern
                .launch(bot.entity, bot.spawn.team, origin, tick)
                .collect();
            if !flights.is_empty() {
                projectiles.push(StockShotBatch {
                    flights,
                    presentation,
                });
            }
        }
        projectiles
    }

    fn check_projection(&mut self, result: Result<(), InitFault>) {
        if let Err(error) = result {
            self.stop_script(error);
            return;
        }
        let diagnostic = self.component.snapshot().and_then(|snapshot| {
            snapshot.diagnostics.iter().find(|diagnostic| {
                !diagnostic.server_only && diagnostic.issue != Issue::ObservationUnavailable
            })
        });
        if let Some(diagnostic) = diagnostic {
            self.stop_script(format!(
                "{:04X}:{:?}: {:?}",
                diagnostic.site.graph, diagnostic.site.node, diagnostic.issue
            ));
        }
    }

    fn stop_script(&mut self, reason: impl std::fmt::Display) {
        eprintln!(
            "stock bot {:08X} script stopped: {reason}",
            self.world.entity
        );
        self.faulted = true;
    }

    pub(super) fn projection(&self) -> Option<SceneProjection<'_>> {
        if self.faulted {
            return None;
        }
        Some(SceneProjection {
            snapshot: self.component.snapshot()?,
            encoder: SnapshotEncoder::new(&self.graphs, &self.codecs),
        })
    }
}

fn sample_bot_movement(bot: &Bot, tick: u32) -> Option<MovementSample> {
    let pose = bot.movement(tick);
    Some(MovementSample {
        flags: sample_movement_flags(&pose)?,
        air_ticks: pose.air_ticks,
        pose_frame: pose.frame,
    })
}

fn forward_from_yaw(yaw: f64) -> [f64; 3] {
    let (sin, cos) = (yaw * TAU / YAW_UNITS).sin_cos();
    [sin, 0.0, cos]
}
