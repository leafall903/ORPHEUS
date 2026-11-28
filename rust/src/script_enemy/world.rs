use std::collections::BTreeMap;

use super::{policy::body_has_target_tag, *};
use crate::{
    game_hitscan::direction_for_angles, script_expr::RoomEntityFacts, script_init::MovementSample,
};

const MAX_LOCAL_HEALTH_CHANGES: usize = 64;

impl EnemyWorld<'_> {
    fn record_local_health_change(
        &mut self,
        source: u32,
        delta: f64,
        killed: bool,
        named_pool_losses: Option<Vec<NamedPoolLoss>>,
    ) {
        let changes = &mut self.control_records.local_health_changes;
        if delta != 0.0
            && self.graphs.uses_host_health_events()
            && changes.len() < MAX_LOCAL_HEALTH_CHANGES
        {
            changes.push(LocalHealthChange {
                source,
                delta,
                killed,
                named_pool_losses,
            });
        }
    }

    fn live_staff_source(&self, source: u32) -> bool {
        self.control_records
            .witch_staff_sources
            .contains_key(&source)
            && self.binding.roster.as_ref().is_some_and(|roster| {
                roster.iter().any(|target| {
                    target.entity == source && target.alive && target.team == self.binding.team
                })
            })
    }
}

impl World for EnemyWorld<'_> {
    fn local_command_present(&self, site: Site) -> Option<bool> {
        (site.entity == self.binding.body && is_junkenstein_mode(self.binding.mode))
            .then_some(false)
    }

    fn has_dynamic_map_rigid_bodies(&self, site: Site) -> Option<bool> {
        (site.entity == self.binding.body && crate::script_expr::valid_entity(site.entity))
            .then_some(false)
    }

    fn cap_horizontal_velocity(&mut self, site: Site, frame: u32, cap: f64) -> bool {
        if site.entity != self.binding.body
            || !is_junkenstein_mode(self.binding.mode)
            || self.vitals.death().is_some()
            || self.control_records.velocity_changes.len() >= MAX_INIT_WORK
        {
            return false;
        }
        let change = crate::mover::VelocityChange::HorizontalCap(cap);
        if !change.valid() {
            return false;
        }
        self.control_records
            .velocity_changes
            .push((frame.saturating_add(1), change));
        true
    }

    fn queue_scripted_motion(
        &mut self,
        site: Site,
        frame: u32,
        motion: script_init::ScriptedMotion,
    ) -> Option<bool> {
        if site.entity != self.binding.body || !crate::script_expr::valid_entity(site.entity) {
            return None;
        }
        if self.vitals.death().is_some() || self.control_records.scripted_motion.is_some() {
            return Some(false);
        }
        self.control_records.scripted_motion = Some((frame, motion));
        Some(true)
    }

    fn engine_setting(&self, _site: Site, key: u32) -> crate::script_expr::EngineSetting {
        crate::body_ruleset::engine_setting(self.graphs, self.binding.mode, key)
    }

    fn summoner_beam_contact(&self, site: Site) -> Option<BeamContact> {
        if self.binding.hero != SUMMONER || site.entity != self.binding.body || site.graph != 0x157c
        {
            return None;
        }
        match site.node {
            Some(202) => Some(self.control_records.summoner_rays[0]),
            Some(203) => Some(self.control_records.summoner_rays[1]),
            _ => None,
        }
    }

    fn summoner_beam_damage(&mut self, site: Site, target: u32, amount: f64) -> bool {
        if self.binding.hero != SUMMONER
            || (site.entity, site.graph, site.node) != (self.binding.body, 0x157c, Some(170))
        {
            return false;
        }
        let [thin, wide] = self.control_records.summoner_rays;
        if thin.entity.or(wide.entity) == Some(target) && !self.control_records.room_boss_pause {
            self.control_records.scripted_damage.push(EnemyDamage {
                impact: None,
                source: site.entity,
                target,
                amount,
            });
        }
        true
    }
    fn ruleset(&mut self, id: u32) -> Option<Value> {
        crate::body_ruleset::get(
            self.graphs,
            self.binding.mode,
            self.binding.hero,
            crate::body_ruleset::ActorKind::Npc,
            id,
        )
    }

    fn objective_route_active(&self, site: Site) -> Option<bool> {
        if site.entity == self.binding.controller {
            self.control_records.objective_route_active
        } else {
            None
        }
    }

    fn request_corpse_retirement(&mut self, site: Site, entity: u32, phase_exit: bool) -> bool {
        if site.entity != self.binding.body
            || ![self.binding.body, self.binding.controller].contains(&entity)
            || (!phase_exit && self.vitals.death().is_none())
        {
            return false;
        }
        *self.retirement_requested = true;
        true
    }
    fn body_angle(
        &self,
        entity: u32,
        axis: crate::script_expr::BodyAngle,
    ) -> crate::script_expr::AngleQuery {
        use crate::script_expr::{AngleQuery, BodyAngle};
        if entity == 0 {
            return AngleQuery::Unbound;
        }
        if entity != self.binding.body {
            return AngleQuery::Unavailable;
        }
        AngleQuery::from_packed(match axis {
            BodyAngle::Yaw => self.binding.pose.yaw,
            BodyAngle::Pitch => self.binding.pose.pitch,
        })
    }

    fn entity_velocity(&self, entity: u32) -> crate::script_expr::VelocityQuery {
        use crate::script_expr::VelocityQuery;
        if !is_junkenstein_mode(self.binding.mode) {
            return VelocityQuery::Unavailable;
        }
        if entity == 0 {
            return VelocityQuery::Unbound;
        }
        if entity == self.binding.body {
            return VelocityQuery::Present(self.binding.pose.velocity);
        }
        self.binding
            .roster
            .as_ref()
            .and_then(|roster| roster.iter().find(|target| target.entity == entity))
            .and_then(|target| target.velocity)
            .map_or(VelocityQuery::Unavailable, VelocityQuery::Present)
    }

    fn entity_exists(&self, entity: u32) -> Option<bool> {
        if self.binding.retired_entities.contains(&entity) {
            return Some(false);
        }
        (entity == self.binding.controller
            || entity == GAME_MODE_ENTITY
            || self.known_position(entity).is_some())
        .then_some(true)
    }

    fn known_position(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.binding.body {
            Some(self.binding.pose.position)
        } else if let Some(gate) = self.binding.gate.filter(|gate| gate.entity == entity) {
            Some(gate.position)
        } else if let Some(target) = self
            .binding
            .roster
            .as_ref()
            .and_then(|roster| roster.iter().find(|target| target.entity == entity))
        {
            Some(target.pose.feet)
        } else {
            self.binding.spawn_anchors.as_ref()?.position(entity)
        }
    }
    fn emit_contact_effect(
        &mut self,
        site: Site,
        source: u32,
        _resource: u64,
        targets: &[u32],
    ) -> bool {
        site.entity == self.binding.body
            && source == self.binding.body
            && targets
                .iter()
                .all(|target| self.known_position(*target).is_some())
    }
    fn entity_team(&self, _site: Site, entity: u32) -> Option<i8> {
        if self.binding.retired_entities.contains(&entity) {
            Some(-1)
        } else if entity == self.binding.body {
            i8::try_from(self.binding.team).ok()
        } else {
            self.binding
                .roster
                .as_ref()?
                .iter()
                .find(|target| target.entity == entity)
                .and_then(|target| i8::try_from(target.team).ok())
        }
    }
    fn team_members(&self, team: u8) -> Option<u32> {
        is_junkenstein_mode(self.binding.mode).then(|| {
            if team == 0 {
                self.control_records.defenders.len() as u32
            } else {
                0
            }
        })
    }
    fn self_damage(&mut self, site: Site, amount: f64) -> bool {
        if site.entity != self.binding.body {
            return false;
        }
        let result = self.vitals.damage(
            amount,
            crate::vitals::Death {
                time: self.binding.now_secs,
                frame: self.binding.frame,
                killer: self.binding.body,
            },
        );
        self.record_local_health_change(
            self.binding.body,
            -result.dealt,
            result.killed,
            Some(result.named_pool_losses),
        );
        true
    }
    fn recharge_shields(&mut self, site: Site, amount: f64) -> bool {
        if site.entity != self.binding.body
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
            || !amount.is_finite()
            || amount < 0.0
        {
            return false;
        }
        self.vitals.recharge_shields(amount);
        true
    }
    fn explosion_damage(&mut self, site: Site, target: u32, amount: f64) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x0c89, Some(38))
            || !self.binding.roster.as_ref().is_some_and(|roster| {
                roster
                    .iter()
                    .any(|candidate| candidate.entity == target && candidate.alive)
            })
        {
            return false;
        }
        self.control_records.scripted_damage.push(EnemyDamage {
            impact: None,
            source: site.entity,
            target,
            amount,
        });
        true
    }
    fn tracked_targets(
        &mut self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        if site.entity != self.binding.body || origin != self.binding.body || site.graph != 0x0c89 {
            return None;
        }
        if site.node == Some(47) && filter.get("m_84065FD1").and_then(Json::as_u64) == Some(0x80) {
            // No damageable scene actor carries the gate's mask bit; its damage uses a message.
            return Some(Vec::new());
        }
        if site.node != Some(52) {
            return None;
        }
        let center: [f64; 3] = std::array::from_fn(|axis| {
            f64::from((self.binding.pose.position[axis] + offset[axis]) as f32)
        });
        let mut targets = Vec::new();
        for target in self.binding.roster.as_ref()?.iter() {
            if target.entity == origin
                || target
                    .pose
                    .feet
                    .iter()
                    .zip(center)
                    .map(|(a, b)| (a - b).powi(2))
                    .sum::<f64>()
                    .sqrt()
                    > radius
            {
                continue;
            }
            if target.alive {
                targets.push(target.entity);
            }
        }
        Some(targets)
    }
    fn witch_staff_targets(&self, site: Site, radius: f64) -> Option<Vec<u32>> {
        if self.binding.hero != WITCH || site.entity != self.binding.body {
            return None;
        }
        Some(
            self.control_records
                .witch_ally
                .filter(|_| {
                    self.control_records
                        .witch_target_distance
                        .is_some_and(|distance| distance <= radius)
                })
                .into_iter()
                .collect(),
        )
    }
    fn set_witch_staff_effect(&mut self, site: Site, effect: Option<(u32, f64)>) -> bool {
        if self.binding.hero != WITCH || site.entity != self.binding.body {
            return false;
        }
        self.control_records.witch_staff_effect = effect;
        true
    }

    fn heal_from_witch_staff(&mut self, site: Site, source: u32, amount: f64) -> bool {
        if site.entity != self.binding.body
            || site.entity == source
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        if !self.control_records.room_boss_pause && self.live_staff_source(source) {
            let healed = self.vitals.heal(amount);
            self.record_local_health_change(source, healed, false, None);
        }
        true
    }
    fn combat_targets(&self, site: Site, config: &Json) -> Option<Vec<u32>> {
        if site.entity != self.binding.body
            || !matches!(site.graph, 0x0ccd | 0x0071)
            || !is_junkenstein_mode(self.binding.mode)
        {
            return None;
        }
        let primary = &config["m_9C7BD8B8"];
        match site.graph {
            0x0ccd
                if crate::script_expr::target_tag_config(primary).is_some()
                    && config["m_filter"]["m_9C7BD8B8"].is_null() => {}
            0x0071
                if primary.is_null()
                    && config["m_filter"]["m_66747E7D"] == 1
                    && config["m_filter"]["m_89831602"] == 4 => {}
            _ => return None,
        }
        Some(
            self.binding
                .roster
                .as_ref()?
                .iter()
                .map(|target| target.entity)
                .collect(),
        )
    }
    fn static_target(&self, tag: u64) -> Option<u32> {
        if tag == crate::scene_assets::JunkensteinGate::TAG {
            return self.binding.gate.map(|gate| gate.entity);
        }
        let &[entity] = self.binding.spawn_anchors.as_ref()?.targets(tag)? else {
            return None;
        };
        Some(entity)
    }
    fn for_code_transport(&self, _site: Site) -> Option<bool> {
        Some(false)
    }

    fn route_game_message(&mut self, site: Site, message: u64, parameters: &Variables) -> bool {
        if !parameters.is_empty() {
            return false;
        }
        let Some(request) = DefenderMessage::authored(site, message) else {
            return false;
        };
        if site.entity != self.binding.body
            || !is_junkenstein_mode(self.binding.mode)
            || self.control_records.defender_messages.len() >= MAX_INIT_WORK
            || (message == BOSS_INTRO_ENDED && self.messages.len() >= MAX_INIT_WORK)
        {
            return false;
        }
        self.control_records.defender_messages.push(request);
        if message == BOSS_INTRO_ENDED {
            self.messages.push_back(PairDelivery {
                target: self.binding.controller,
                input: PairInput::Message {
                    sender: site.entity,
                    message,
                    parameters: Variables::new(),
                    required_graph: Some(0x0cdc),
                },
            });
        }
        true
    }

    fn send_message(
        &mut self,
        site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if (site.entity, site.graph, site.node, target, message)
            == (
                self.binding.body,
                0x0cbc,
                Some(30),
                GAME_MODE_ENTITY,
                BOSS_INTRO_STARTED,
            )
            && is_junkenstein_mode(self.binding.mode)
            && parameters.len() == 1
            && parameters.get(&8) == Some(&Value::Entity(site.entity))
            && self.control_records.boss_intro_starts.len() < MAX_INIT_WORK
        {
            self.control_records
                .boss_intro_starts
                .push(parameters.clone());
            return true;
        }
        if message == crate::pve_encounter::messages::FLASHBANG_HIT
            && site.entity == self.binding.body
            && is_junkenstein_mode(self.binding.mode)
            && self.control_records.defenders.contains(&target)
        {
            if self.control_records.defender_messages.len() >= MAX_INIT_WORK {
                return false;
            }
            if let Some(request) = DefenderMessage::flashbang(site, target, parameters) {
                self.control_records.defender_messages.push(request);
                return true;
            }
        }
        if ![self.binding.body, self.binding.controller, GAME_MODE_ENTITY].contains(&target)
            && !self.binding.gate.is_some_and(|gate| gate.entity == target)
            || self.messages.len() >= MAX_INIT_WORK
        {
            return self.queue_defender_sighting(site, target, message, parameters);
        }
        self.messages.push_back(PairDelivery {
            target,
            input: PairInput::Message {
                sender: site.entity,
                message,
                parameters: parameters.clone(),
                required_graph: None,
            },
        });
        true
    }
    fn play_script(&mut self, caller: Site, target: u32, request: PlayScript) -> bool {
        if (caller.entity, target, caller.graph)
            != (self.binding.controller, self.binding.body, 0x0c95)
            || !matches!(
                (caller.node, request.graph),
                (Some(2), 0x0cbc) | (Some(3), 0x0ccd) | (Some(6), 0x0ce3)
            )
            || !is_junkenstein_mode(self.binding.mode)
            || self.messages.len() >= MAX_INIT_WORK
        {
            return false;
        }
        self.messages.push_back(PairDelivery {
            target,
            input: PairInput::PlayScript { caller, request },
        });
        true
    }
    fn ai_behavior_request(
        &mut self,
        site: Site,
        record: script_init::AiBehaviorRequest,
        active: bool,
    ) -> Option<bool> {
        if site.entity != self.binding.controller {
            return None;
        }
        let key = (site.entity, site.instance, site.node?);
        if !active {
            self.behavior_records.shift_remove(&key);
            return Some(true);
        }
        if let Some(previous) = self.behavior_records.get_mut(&key) {
            *previous = record;
            return Some(true);
        }
        let previous = std::mem::take(self.behavior_records);
        let mut following = Vec::new();
        for (key, other) in previous {
            if record.priority > other.priority
                || (record.prefer_later && record.priority == other.priority)
            {
                following.push((key, other));
            } else {
                self.behavior_records.insert(key, other);
            }
        }
        self.behavior_records.insert(key, record);
        self.behavior_records.extend(following);
        Some(true)
    }
    fn entity_hero(&self, site: Site, target: u32) -> Option<u64> {
        (target == self.binding.body
            && [self.binding.body, self.binding.controller].contains(&site.entity))
        .then_some(self.binding.hero)
    }
    fn entity_definition(&self, site: Site, target: u32) -> Option<u64> {
        if ![self.binding.body, self.binding.controller].contains(&site.entity) {
            return None;
        }
        if target == self.binding.body {
            Some(self.binding.pair.body)
        } else if target == self.binding.controller {
            Some(self.binding.pair.controller)
        } else {
            None
        }
    }
    fn entity_health(
        &self,
        site: Site,
        target: u32,
        kind: Option<PoolKind>,
        maximum: bool,
    ) -> Option<f64> {
        if (site.graph, site.node) == (0x01cf, Some(8)) {
            let source = self.control_records.witch_staff_sources.get(&target)?;
            let pool = match kind? {
                PoolKind::Health => 0,
                PoolKind::Armour => 1,
                PoolKind::Shields => return None,
            };
            return Some(if maximum {
                source.capacity[pool]
            } else {
                source.health[pool]
            });
        }
        (target == self.binding.body
            && [self.binding.body, self.binding.controller].contains(&site.entity))
        .then(|| self.vitals.amount(kind, maximum))
    }
    fn body_display_name(&self, site: Site, entity: u32) -> Option<String> {
        if (site.graph, site.node) != (0x01cf, Some(7)) {
            return None;
        }
        Some(
            self.control_records
                .witch_staff_sources
                .get(&entity)?
                .name
                .clone(),
        )
    }
    fn outline_context_available(&self, _site: Site) -> Option<bool> {
        Some(false)
    }
    fn supports_static_model_protection(&self, site: Site) -> bool {
        site.entity == self.binding.body
            && is_junkenstein_mode(self.binding.mode)
            && crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
    }

    fn set_encounter_model_enabled(&mut self, site: Site, enabled: bool) -> bool {
        const MASK: u64 = (1 << 45) | (1 << 15);
        if !self.supports_static_model_protection(site) {
            return false;
        }
        if enabled {
            self.control_records.model_flags &= !MASK;
        } else {
            self.control_records.model_flags |= MASK;
        }
        true
    }
    fn set_captured_aim_lock(&mut self, site: Site, degrees: Option<[f64; 2]>) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x224e, Some(34))
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        // These room-owned scene bodies have an unparented authoritative mover.
        // Foreign links and player owner-full cache replication are not supplied here.
        if let Some(degrees) = degrees {
            let Some(aim) = crate::linked_aim::unparented_lock(degrees) else {
                return false;
            };
            self.control_records.cinematic_aim.insert(site, aim);
        } else {
            self.control_records.cinematic_aim.shift_remove(&site);
        }
        true
    }

    fn set_cinematic_aim_lock(&mut self, site: Site, active: bool) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x0ce0, Some(45))
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
        {
            return false;
        }
        if active {
            let pose = self.binding.pose;
            self.control_records
                .cinematic_aim
                .insert(site, [pose.yaw, pose.pitch]);
        } else {
            self.control_records.cinematic_aim.shift_remove(&site);
        }
        true
    }
    fn set_same_team_contact_suppressed(&mut self, site: Site, suppressed: bool) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x0cbc, Some(129))
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
        {
            return false;
        }
        if suppressed {
            self.control_records
                .same_team_contact_suppressions
                .insert(site);
        } else {
            self.control_records
                .same_team_contact_suppressions
                .remove(&site);
        }
        true
    }
    fn set_outgoing_damage_filter(&mut self, site: Site, adjustment: Option<f64>) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node, self.binding.hero) != (0x0cbc, Some(141), ROADHOG)
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        if let Some(adjustment) = adjustment {
            let Some(excluded_tags) = self
                .graphs
                .graph(site.graph)
                .and_then(|graph| graph.node(141))
                .and_then(|node| node.field("m_5CFFCBAE"))
                .and_then(Json::as_array)
                .filter(|tags| !tags.is_empty())
                .and_then(|tags| {
                    tags.iter()
                        .map(|tag| asset_guid(tag).filter(|&guid| guid != 0))
                        .collect::<Option<Vec<_>>>()
                })
            else {
                return false;
            };
            self.control_records.outgoing_damage_filters.insert(
                site,
                OutgoingDamageFilter {
                    adjustment,
                    excluded_tags,
                },
            );
        } else {
            self.control_records
                .outgoing_damage_filters
                .shift_remove(&site);
        }
        true
    }

    fn voice_context(&mut self, site: Site) -> Option<bool> {
        (site.entity == self.binding.body
            && is_junkenstein_mode(self.binding.mode)
            && crate::npc_catalog::is_junkenstein_boss(self.binding.hero))
        .then_some(true)
    }
    fn voice_manager(&mut self, site: Site) -> Option<bool> {
        self.voice_context(site)
    }
    fn voice_clock(&mut self, site: Site, now_ms: u64) -> Option<f64> {
        self.voice_context(site).map(|_| now_ms as f64 / 1000.0)
    }
    fn voice_stimulus(
        &mut self,
        site: Site,
        guid: u64,
    ) -> Option<Option<crate::script_voice::Stimulus>> {
        self.voice_context(site)?;
        let kind =
            crate::pve_encounter::voice::boss_speech(self.binding.hero, self.binding.skin, guid)?;
        Some(Some(crate::script_voice::Stimulus {
            guid,
            playable: true,
            mode: kind.mode(),
        }))
    }
    fn voice_allow(&mut self, site: Site, stimulus: crate::script_voice::Stimulus) -> Option<bool> {
        self.voice_context(site)?;
        let kind = crate::pve_encounter::voice::boss_speech(
            self.binding.hero,
            self.binding.skin,
            stimulus.guid,
        )?;
        crate::pve_encounter::voice::speech_allowed(
            kind,
            self.binding.mode_voice_set,
            crate::pve_encounter::voice::body_voice_set(self.binding.hero, self.binding.skin),
        )
    }
    fn voice_actor_required(&self, _site: Site, stimulus: crate::script_voice::Stimulus) -> bool {
        matches!(
            crate::pve_encounter::voice::boss_speech(
                self.binding.hero,
                self.binding.skin,
                stimulus.guid
            ),
            Some(crate::pve_encounter::voice::BossSpeech::Defeat(_))
        )
    }
    fn voice_submit(
        &mut self,
        site: Site,
        stimulus: crate::script_voice::Stimulus,
        targets: &[u32],
        actor: Option<&Value>,
    ) -> Result<Option<u32>, &'static str> {
        use crate::pve_encounter::voice::BossSpeech;
        if self.voice_context(site).is_none() {
            return Ok(None);
        }
        let Some(kind) = crate::pve_encounter::voice::boss_speech(
            self.binding.hero,
            self.binding.skin,
            stimulus.guid,
        ) else {
            return Ok(None);
        };
        let hero = if matches!(kind, BossSpeech::Defeat(_)) {
            let actor = actor.ok_or("voice killer hero facts")?;
            let killer = crate::script_expr::entity_scalar(actor);
            if !crate::script_expr::valid_entity(killer)
                || [GAME_MODE_ENTITY, self.binding.controller].contains(&killer)
            {
                None
            } else if killer == self.binding.body {
                Some(self.binding.hero)
            } else {
                Some(
                    self.binding
                        .roster
                        .as_ref()
                        .and_then(|roster| roster.iter().find(|target| target.entity == killer))
                        .and_then(|target| target.hero)
                        .ok_or("voice killer hero facts")?,
                )
            }
        } else {
            None
        };
        let draw = if kind.needs_draw() {
            crate::pve_encounter::voice::voice_draw(
                site.entity,
                &mut self.control_records.voice_variation,
            )
        } else {
            0.0
        };
        let (handle, request) = crate::pve_encounter::voice::speech_submission(
            site,
            kind,
            stimulus.guid,
            hero,
            targets,
            draw,
        )?;
        if let Some(request) = request {
            if self.control_records.voice_requests.len() >= MAX_INIT_WORK {
                return Ok(None);
            }
            self.control_records.voice_requests.push(request);
        }
        Ok(Some(handle))
    }
    fn voice_duration(&mut self, site: Site, handle: u32, guid: u64, mode: bool) -> Option<f64> {
        self.voice_context(site)?;
        crate::pve_encounter::voice::boss_duration(handle, guid, mode)
    }
    fn voice_part(&mut self, site: Site, _number: u16) -> Option<f64> {
        self.voice_context(site).map(|_| 0.0)
    }

    fn boss_intro_cue(&self, node: &crate::script_graph::Node) -> Option<IntroCue> {
        crate::pve_encounter::voice::intro_cue(node)
    }

    fn play_boss_intro_voice(&mut self, site: Site, cue: IntroCue) -> Option<u64> {
        if site.entity != self.binding.body
            || site.graph != 0x0ce0
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
            || self.control_records.voice_requests.len() >= MAX_INIT_WORK
        {
            return None;
        }
        let lines =
            crate::pve_encounter::voice::intro_lines(cue, self.binding.hero, self.binding.skin);
        let Some((line, duration_ms)) = crate::pve_encounter::voice::select_line(
            lines,
            site.entity,
            &mut self.control_records.voice_variation,
        ) else {
            return Some(0);
        };
        self.control_records.voice_requests.push(VoiceRequest {
            anchor: site.entity,
            speaker: if cue.narrator {
                GAME_MODE_ENTITY
            } else {
                site.entity
            },
            stimulus: cue.stimulus,
            line,
        });
        Some(duration_ms)
    }
    fn set_ai_control_setting(&mut self, site: Site, scalar: f32, flag: bool) -> bool {
        if site.entity != self.binding.body
            || self.binding.hero != SHOCK_TIRE
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        self.control_records.ai_control_setting = Some((scalar, flag));
        true
    }

    fn player_stats_service_available(&self, site: Site) -> Option<bool> {
        // The bound NPC body has no StateScript player stats service.
        (site.entity == self.binding.body && crate::script_expr::valid_entity(site.entity))
            .then_some(false)
    }

    fn local_combat_context_available(&self, _site: Site) -> Option<bool> {
        Some(false)
    }
    fn ai_control_override(
        &mut self,
        site: Site,
        record: script_init::AiControlOverride,
        active: bool,
    ) -> Option<bool> {
        if record.target != self.binding.body {
            return None;
        }
        let key = (site.entity, site.instance, site.node?);
        self.control_records.overrides.shift_remove(&key);
        if active {
            let previous = std::mem::take(&mut self.control_records.overrides);
            let mut following = Vec::new();
            for (key, other) in previous {
                if record.priority > other.priority
                    || (record.prefer_later && record.priority == other.priority)
                {
                    following.push((key, other));
                } else {
                    self.control_records.overrides.insert(key, other);
                }
            }
            self.control_records.overrides.insert(key, record);
            self.control_records.overrides.extend(following);
        }
        Some(true)
    }
    fn set_facing_claim(
        &mut self,
        site: Site,
        record: script_init::AiFacing,
        active: bool,
    ) -> Option<bool> {
        if site.entity != self.binding.body {
            return None;
        }
        if active {
            if record.target != self.binding.body
                || self.vitals.death().is_some()
                || crate::facing::angles(record.direction).is_none()
            {
                return Some(false);
            }
            self.control_records.facing.insert(site, record);
        } else {
            self.control_records.facing.shift_remove(&site);
        }
        Some(true)
    }

    fn ai_facing(
        &mut self,
        site: Site,
        record: script_init::AiFacing,
        active: bool,
    ) -> Option<bool> {
        if site.entity != self.binding.controller
            || !is_junkenstein_mode(self.binding.mode)
            || !matches!(
                (site.graph, site.node),
                (0x0c95, Some(5)) | (0x185d, Some(57))
            )
        {
            return None;
        }
        if record.target != self.binding.body || (active && self.vitals.death().is_some()) {
            return Some(false);
        }
        if active {
            self.control_records.facing.insert(site, record);
        } else {
            self.control_records.facing.shift_remove(&site);
        }
        Some(true)
    }

    fn ai_weapon_override(
        &mut self,
        site: Site,
        record: AiWeaponOverride,
        active: bool,
    ) -> Option<bool> {
        if record.target != self.binding.body
            || ![self.binding.body, self.binding.controller].contains(&site.entity)
            || site.node.is_none()
        {
            return None;
        }
        let control = self.graphs.enemy_body(self.binding.pair.controller)?;
        let identifiers = control
            .ai_weapon_identifiers
            .get(&record.slot.to_string())
            .and_then(Json::as_array);
        if identifiers.is_none_or(|values| {
            !values
                .iter()
                .any(|value| value.as_u64() == Some(record.identifier))
        }) {
            return Some(false);
        }
        self.control_records.weapons.shift_remove(&site);
        if active {
            let index = self
                .control_records
                .weapons
                .values()
                .position(|other| {
                    record.priority > other.priority
                        || (record.prefer_later && record.priority == other.priority)
                })
                .unwrap_or(self.control_records.weapons.len());
            self.control_records
                .weapons
                .shift_insert(index, site, record);
        }
        Some(true)
    }
    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if self.binding.retired_entities.contains(&entity) {
            return Some(Value::Null);
        }
        let variables = if entity == GAME_MODE_ENTITY {
            &self.binding.mode_variables
        } else if entity == self.binding.body {
            self.body_variables
        } else if entity == self.binding.controller {
            self.controller_variables
        } else {
            return self
                .binding
                .roster
                .as_ref()?
                .iter()
                .find(|target| target.entity == entity)?
                .script_state
                .read(id);
        };
        Some(variables.get(&id).cloned().unwrap_or(Value::Null))
    }

    fn queue_health_change(&mut self, site: Site, target: u32, delta: f64) -> bool {
        if site.entity != self.binding.body {
            return false;
        }
        self.binding.spatial_frame.as_deref().is_some_and(|frame| {
            self.control_records
                .script_requests
                .queue_health(frame, target, delta)
        })
    }

    fn queue_health_changes(&mut self, site: Site, changes: &[(u32, f64)]) -> bool {
        if site.entity != self.binding.body {
            return false;
        }
        self.binding.spatial_frame.as_deref().is_some_and(|frame| {
            self.control_records
                .script_requests
                .queue_health_changes(frame, changes)
        })
    }

    fn room_body_generation(&self, wire: u32) -> Option<u64> {
        self.binding.spatial_frame.as_deref()?.body_generation(wire)
    }

    fn queue_room_message(&mut self, site: Site, target: u32, message: RoomMessage) -> bool {
        if site.entity != self.binding.body {
            return false;
        }
        self.binding.spatial_frame.as_deref().is_some_and(|frame| {
            self.control_records
                .script_requests
                .queue_message(frame, target, message)
        })
    }

    fn queue_room_cue(
        &mut self,
        site: Site,
        targets: &[u32],
        cue: crate::script_init::RoomCue,
    ) -> bool {
        site.entity == self.binding.body
            && self.binding.spatial_frame.as_deref().is_some_and(|frame| {
                self.control_records
                    .script_requests
                    .queue_cue(frame, site.entity, targets, cue)
            })
    }

    fn spatial_candidates(&self) -> Option<&[crate::script_expr::SpatialCandidate]> {
        Some(self.binding.spatial_frame.as_deref()?.candidates())
    }

    fn room_entities(&self) -> Option<&BTreeMap<u32, RoomEntityFacts>> {
        self.binding.spatial_frame.as_deref()?.entity_facts()
    }

    fn room_actor_identity(&self, entity: u32) -> Option<u64> {
        self.binding
            .spatial_frame
            .as_deref()?
            .actor_identity(entity)
    }

    fn spatial_line_clear(&self, origin: [f64; 3], target: [f64; 3]) -> Option<bool> {
        self.binding
            .spatial_frame
            .as_deref()?
            .line_clear(origin, target)
    }

    fn spatial_ray_cast(
        &self,
        site: Site,
        vm_frame: u32,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        self.binding.spatial_frame.as_deref()?.ray_cast(
            site,
            vm_frame,
            origin,
            direction,
            max_distance,
        )
    }

    fn spatial_actor_ray_cast(
        &self,
        site: Site,
        vm_frame: u32,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
        targets: &[u32],
    ) -> Option<Option<crate::script_expr::SpatialActorRayHit>> {
        self.binding.spatial_frame.as_deref()?.actor_ray_cast(
            site,
            vm_frame,
            origin,
            direction,
            max_distance,
            targets,
        )
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.known_position(entity)
    }
    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        self.binding.spawn_anchors.as_ref()?.forward(entity)
    }
    fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.binding.body {
            return Some(self.binding.pose.position);
        }
        let frame = self.binding.spatial_frame.as_deref();
        if let Some((position, _)) = frame.and_then(|frame| frame.actor_pose(entity)) {
            return Some(position);
        }
        if self.binding.retired_entities.contains(&entity) {
            return None;
        }
        if frame.is_none() {
            return self.position(entity);
        }
        self.binding
            .gate
            .filter(|gate| gate.entity == entity)
            .map(|gate| gate.position)
            .or_else(|| self.binding.spawn_anchors.as_ref()?.position(entity))
    }

    fn authored_forward(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.binding.body {
            let pose = &self.binding.pose;
            return Some(direction_for_angles(pose.yaw, pose.pitch));
        }
        if let Some((_, [yaw, pitch])) = self
            .binding
            .spatial_frame
            .as_deref()
            .and_then(|frame| frame.actor_pose(entity))
        {
            return Some(direction_for_angles(yaw, pitch));
        }
        if self.binding.retired_entities.contains(&entity) {
            return None;
        }
        self.forward(entity)
    }

    fn body_bone_point(&mut self, entity: u32, bone: u64, local: [f64; 3]) -> Option<[f64; 3]> {
        if entity == self.binding.body {
            if self.vitals.death().is_some() {
                return None;
            }
            let point = if bone == 0 {
                local
            } else {
                self.graphs.bone_reference_point(
                    self.binding.pair.body,
                    self.binding.skin.unwrap_or(0),
                    bone,
                    local,
                )?
            };
            let pose = &self.binding.pose;
            return crate::script_expr::body_space_point(
                pose.position,
                Some(direction_for_angles(pose.yaw, 0)),
                point,
            );
        }
        if let Some(frame) = self.binding.spatial_frame.as_ref() {
            if frame.is_retired(entity) {
                return None;
            }
            if frame.actor_alive(entity) {
                return frame.actor_bone_point(self.graphs, entity, bone, local);
            }
        }
        if bone != 0 || self.binding.retired_entities.contains(&entity) {
            return None;
        }
        let origin = self.authored_position(entity)?;
        crate::script_expr::body_space_point(origin, self.authored_forward(entity), local)
    }

    fn body_eye_position(&self, entity: u32) -> Option<[f64; 3]> {
        self.binding
            .spatial_frame
            .as_deref()?
            .actor_eye_position(entity)
    }

    fn targets_with_tag(&self, site: Site, tag: u64, excluded: u64) -> Option<Vec<u32>> {
        if (site.entity, site.graph, site.node) != (self.binding.body, 0x00ca, Some(33))
            || !is_junkenstein_mode(self.binding.mode)
            || (tag, excluded) != (0x0d60_0000_0000_0350, 0x0d60_0000_0000_0353)
        {
            return None;
        }
        let anchors = self.binding.spawn_anchors.as_ref()?;
        let excluded_placements = anchors.targets(excluded)?;
        let mut targets: Vec<_> = anchors
            .targets(tag)?
            .iter()
            .copied()
            .filter(|target| !excluded_placements.contains(target))
            .collect();
        // These encounter tags have no producer on foreign combat bodies.
        if body_has_target_tag(self.graphs, self.binding, self.target_tag_effects, tag)
            == Some(true)
            && body_has_target_tag(self.graphs, self.binding, self.target_tag_effects, excluded)
                != Some(true)
        {
            targets.push(self.binding.body);
        }
        Some(targets)
    }

    fn tagged_placements(&self, site: Site, tag: u64) -> Option<&[u32]> {
        if site.entity != self.binding.controller {
            return None;
        }
        self.binding.spawn_anchors.as_ref()?.targets(tag)
    }
    fn movement_sample(&self, site: Site) -> Option<MovementSample> {
        if site.entity != self.binding.body {
            return None;
        }
        let pose = &self.binding.pose;
        Some(MovementSample {
            flags: sample_movement_flags(pose)?,
            air_ticks: pose.air_ticks,
            pose_frame: pose.frame,
        })
    }
    fn total_health(&self, site: Site, entity: u32) -> crate::script_expr::HealthQuery {
        use crate::script_expr::HealthQuery;
        if ![self.binding.body, self.binding.controller].contains(&site.entity)
            || !is_junkenstein_mode(self.binding.mode)
        {
            return HealthQuery::Unavailable;
        }
        if entity == 0 {
            return HealthQuery::Unbound;
        }
        if entity == self.binding.body {
            return HealthQuery::Present(self.vitals.total());
        }
        self.binding
            .roster
            .as_ref()
            .and_then(|roster| roster.iter().find(|target| target.entity == entity))
            .and_then(|target| target.health)
            .map_or(HealthQuery::Unavailable, HealthQuery::Present)
    }

    fn body_health(&self, site: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
        (site.entity == self.binding.body).then(|| match kind {
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
        (site.entity == self.binding.body)
            .then(|| self.vitals.add_pool(kind, maximum, current))
            .flatten()
    }
    fn add_temporary_shield(
        &mut self,
        site: Site,
        identifier: u64,
        amount: f64,
        stacked: bool,
    ) -> Option<u32> {
        if site.entity != self.binding.body {
            return None;
        }
        self.vitals
            .add_temporary_shield(identifier, amount, stacked)
    }

    fn temporary_shield_current(&self, site: Site, identifier: u64) -> Option<f64> {
        (site.entity == self.binding.body).then(|| self.vitals.temporary_shield_current(identifier))
    }

    fn drain_temporary_shield(&mut self, site: Site, identifier: u64, amount: f64) -> Option<f64> {
        (site.entity == self.binding.body)
            .then(|| self.vitals.drain_temporary_shield(identifier, amount))
    }

    fn health_pool_current(&self, site: Site, pool: u32) -> Option<f64> {
        (site.entity == self.binding.body)
            .then(|| self.vitals.pool_current(pool))
            .flatten()
    }
    fn remove_health_pool(&mut self, site: Site, pool: u32) -> Option<f64> {
        (site.entity == self.binding.body)
            .then(|| self.vitals.remove_pool(pool))
            .flatten()
    }
    fn add_wave_health(&mut self, site: Site, amount: f64) -> Option<Option<u32>> {
        if site.entity != self.binding.body || site.graph != 0x0cbc {
            return None;
        }
        if self.vitals.death().is_some() {
            return Some(None);
        }
        self.vitals.add_wave_health(amount).map(Some)
    }
    fn body_scale(&mut self, site: Site, target: u32, overridden: &Value) -> Option<f64> {
        (site.entity == self.binding.body
            && target == self.binding.body
            && matches!(overridden, Value::Null | Value::Integer(1)))
        .then_some(1.0)
    }
    fn has_target_tag(&self, site: Site, entity: u32, tag: u64) -> Option<bool> {
        if site.entity != self.binding.body && site.entity != self.binding.controller {
            return None;
        }
        if entity == self.binding.body {
            return body_has_target_tag(self.graphs, self.binding, self.target_tag_effects, tag);
        }
        if entity != self.binding.controller {
            return self
                .binding
                .roster
                .as_ref()?
                .iter()
                .find(|target| target.entity == entity)?
                .tags
                .presence(tag);
        }
        let tags: &BTreeSet<_> = self
            .graphs
            .enemy_body(self.binding.pair.controller)?
            .target_tags
            .as_ref()?;
        Some(tags.contains(&tag))
    }
    fn set_target_tag_effect(&mut self, site: Site, effect: TargetTagEffect, active: bool) -> bool {
        site.entity == self.binding.body
            && effect.entity == self.binding.body
            && self.target_tag_effects.apply(effect, active)
    }
    fn body_on_fire(&mut self, site: Site, entity: u32) -> Option<OnFire> {
        // The scene controller is linked, but has no GMP player stats.
        ((site.entity == self.binding.body || site.entity == self.binding.controller)
            && entity == self.binding.body)
            .then_some(OnFire::NoStats)
    }

    fn entity_component92(&self, site: Site, entity: u32) -> Option<bool> {
        if site.entity != self.binding.body && site.entity != self.binding.controller {
            return None;
        }
        let definition = if entity == self.binding.controller {
            self.binding.pair.controller
        } else if entity == self.binding.body {
            self.binding.pair.body
        } else if site.graph == 0x0079
            && self
                .control_records
                .witch_staff_sources
                .values()
                .any(|source| source.controller == entity)
        {
            self.graphs.enemy_pair(WITCH)?.controller
        } else {
            return None;
        };
        self.graphs.enemy_body(definition)?.component92_present
    }

    fn related_entity(&mut self, site: Site, relation: EntityRelation, entity: u32) -> Option<u32> {
        match relation {
            EntityRelation::OwningController
            | EntityRelation::CurrentPossessor
            | EntityRelation::AiControlEntity
                if entity == self.binding.body =>
            {
                Some(self.binding.controller)
            }
            EntityRelation::AiControlEntity if entity == self.binding.controller => {
                Some(self.binding.controller)
            }
            EntityRelation::PossessedBody if entity == self.binding.controller => {
                Some(self.binding.body)
            }
            EntityRelation::AiControlEntity if site.graph == 0x0079 => self
                .control_records
                .witch_staff_sources
                .get(&entity)
                .map(|source| source.controller),
            _ => None,
        }
    }
    fn linked_entity(&mut self, _site: Site, entity: u32, slot: u32) -> Option<u32> {
        if entity != self.binding.controller {
            return None;
        }
        match (self.binding.hero, slot) {
            (SUMMONER, 0 | 1) => {
                Some(self.control_records.summoner_targets[slot as usize].unwrap_or(0))
            }
            (_, 0) => Some(
                self.control_records
                    .weapon_target
                    .map_or(0, |target| target.entity),
            ),
            (WITCH, 1) => Some(self.control_records.witch_ally.unwrap_or(0)),
            _ => None,
        }
    }

    fn scene_filter_policy(
        &self,
        site: Site,
        filter: &Json,
    ) -> Option<crate::script_expr::SceneFilterPolicy> {
        use crate::script_expr::{FilterPredicate, FilterRelation, SceneFilterPolicy};
        if site.entity != self.binding.body {
            return None;
        }
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let hostile = team_mode == 2 && (hostile_flag == 0 || hostile_flag == 1);
        let friendly = team_mode == 1 && matches!(hostile_flag, 0 | 4);
        if !hostile && !friendly {
            return None;
        }
        let predicate = filter
            .get("m_condition")
            .is_some_and(|value| !value.is_null());
        for key in ["m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"] {
            if filter.get(key).is_some_and(|value| {
                !value.is_null() && crate::script_expr::target_tag_config(value).is_none()
            }) {
                return None;
            }
        }
        Some(SceneFilterPolicy {
            relation: if friendly {
                FilterRelation::FriendlyOther
            } else {
                FilterRelation::Hostile
            },
            predicate: if !predicate {
                FilterPredicate::None
            } else if friendly {
                FilterPredicate::Target
            } else {
                FilterPredicate::Scalar
            },
        })
    }

    fn entity_filter_flags(&self, _site: Site, entity: u32) -> Option<u64> {
        if entity == self.binding.body {
            return Some(
                1 | (0x0080_0000 << self.binding.team)
                    | if self.vitals.death().is_some() {
                        0x10
                    } else {
                        0
                    },
            );
        }
        self.binding
            .roster
            .as_ref()?
            .iter()
            .find(|target| target.entity == entity)?
            .filter_flags
    }
    fn native_uses_initial(&self, site: Site, config: &Json) -> bool {
        if site.entity == self.binding.body
            && self.binding.hero == SHOCK_TIRE
            && is_junkenstein_mode(self.binding.mode)
            && native_shape(config, "STU_0340A42A", Some("m_entity"))
            && native_shape(&config["m_entity"], "STU_B5A0CAF0", None)
        {
            // The autonomous tire has no owning scene participant.
            return true;
        }
        site.entity == self.binding.controller
            && site.graph == 0x0c83
            && is_junkenstein_mode(self.binding.mode)
            && crate::script_expr::native_fields(config, &["m_resourceKey", "m_00903303"])
            && config
                .get("m_00903303")
                .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None))
    }

    fn selects_ai_behavior(&mut self, site: Site) -> Option<bool> {
        // The encounter host drives its own behavior requests. These dormant
        // candidates have no selecting service in the reference scene.
        (site.entity == self.binding.controller
            && is_junkenstein_mode(self.binding.mode)
            && matches!(
                (site.graph, site.node),
                (0x06fe, Some(45))
                    | (0x0bc8 | 0x0bca | 0x0bd0 | 0x0cdc | 0x0ce2, Some(2))
                    | (0x0c83, Some(3))
                    | (0x0c88, Some(0))
                    | (0x0cc1, Some(20))
                    | (0x0b6f | 0x0cd0, Some(1))
                    | (0x0cd9, Some(25))
            ))
        .then_some(false)
    }

    fn set_display_name(&mut self, site: Site, entity: u32, name: u64, active: bool) -> bool {
        if entity != self.binding.controller || self.entity_component92(site, entity) != Some(true)
        {
            return false;
        }
        if active {
            self.control_records.display_names.insert(site, name);
        } else {
            self.control_records.display_names.shift_remove(&site);
        }
        true
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        if site.entity == self.binding.body
            && crate::script_expr::native_fields(config, &["m_entity", "m_filter"])
            && config["$"] == "STU_AC829876"
            && config["m_filter"]
                .get("m_84065FD1")
                .and_then(Json::as_u64)
                .is_some_and(|mask| mask & 0x40 != 0)
        {
            // World-object mask bit 0x40 is absent on living actor bodies.
            return Some(Value::Bool(false));
        }
        if (site.entity, site.graph, site.node) == (self.binding.body, 0x0079, Some(0))
            && is_junkenstein_mode(self.binding.mode)
            && config["$"] == "STU_4049CF77"
            && crate::script_expr::dynamic(&config["m_C5D0FA56"])
                .ok()
                .flatten()
                .is_some_and(|source| source.id == 27)
            && asset_guid(&config["m_F00C2F3D"]["m_identifier"]) == Some(0x0d80_0000_0000_2162)
        {
            let tier = match self.binding.mode_variables.get(&6751)? {
                Value::Asset(0x0d80_0000_0000_195e) => 1.0,
                Value::Asset(0x0d80_0000_0000_195f) => 2.0,
                Value::Asset(0x0d80_0000_0000_26df) => 3.0,
                Value::Asset(0x0d80_0000_0000_1960) => 4.0,
                _ => return None,
            };
            return Some(Value::Float(tier));
        }
        if (site.entity, site.graph, site.node, self.binding.hero)
            == (self.binding.body, 0x157e, Some(17), 0x02e0_0000_0000_0016)
            && is_junkenstein_mode(self.binding.mode)
            && config["$"] == "STU_967463AB"
            && crate::script_expr::native_fields(config, &["m_92D12BE6", "m_C8655030"])
            && native_shape(&config["m_92D12BE6"], "STU_DB742151", Some("m_entity"))
            && native_shape(&config["m_92D12BE6"]["m_entity"], "STU_B5A0CAF0", None)
            && asset_guid(&config["m_C8655030"]["m_entityDef"]) == Some(0x0400_0000_0000_2658)
        {
            // A newly initialized Summoner owns no deployables.
            return Some(Value::Integer(0));
        }
        if site.entity == self.binding.body && is_junkenstein_mode(self.binding.mode) {
            if config.get("$").and_then(Json::as_str) == Some("STU_4049CF77")
                && config.get("m_C5D0FA56").is_none_or(Json::is_null)
                && config["m_F00C2F3D"]["$"] == "STUConfigVarIdentifier"
                && crate::script_expr::native_fields(
                    &config["m_F00C2F3D"],
                    &["m_identifier", "m_resourceKey"],
                )
                && config["m_F00C2F3D"]["m_resourceKey"].is_null()
            {
                let identifier = asset_guid(&config["m_F00C2F3D"]["m_identifier"])?;
                let amounts = match (
                    site.graph,
                    site.node,
                    self.binding.pair.body,
                    self.binding.pair.controller,
                    identifier,
                ) {
                    (
                        0x0019,
                        Some(10),
                        0x0400_0000_0000_0022,
                        0x0400_0000_0000_1958,
                        0x0d80_0000_0000_1961,
                    ) => [1.75, 3.5, 5.25, 7.0],
                    (
                        0x01c6,
                        Some(26 | 29),
                        0x0400_0000_0000_028f,
                        0x0400_0000_0000_1959,
                        0x0d80_0000_0000_1961 | 0x0d80_0000_0000_1962,
                    ) => [f64::from(1.8000001_f32), 4.5, 6.75, 9.0],
                    (
                        0x01c6,
                        Some(29),
                        0x0400_0000_0000_028f,
                        0x0400_0000_0000_1959,
                        0x0d80_0000_0000_1c98,
                    ) => [10.0, 25.0, 37.5, 50.0],
                    (
                        0x022b,
                        Some(31),
                        0x0400_0000_0000_0380,
                        0x0400_0000_0000_16fb,
                        0x0d80_0000_0000_1961,
                    ) => [15.0, 30.0, 30.0, 30.0],
                    (
                        0x022b,
                        Some(31),
                        0x0400_0000_0000_0380,
                        0x0400_0000_0000_16fb,
                        0x0d80_0000_0000_1962,
                    ) => [10.0, 20.0, 20.0, 20.0],
                    (
                        0x006d,
                        Some(10),
                        0x0400_0000_0000_00e4,
                        0x0400_0000_0000_172e,
                        0x0d80_0000_0000_1961,
                    ) => [5.0, 10.0, 15.0, 20.0],
                    (
                        0x0ca1,
                        Some(0),
                        0x0400_0000_0000_171c,
                        0x0400_0000_0000_171d,
                        0x0d80_0000_0000_1961,
                    ) => [30.0, 60.0, 60.0, 90.0],
                    (
                        0x157c,
                        Some(60),
                        0x0400_0000_0000_26f0,
                        0x0400_0000_0000_1dd3,
                        0x0d80_0000_0000_1961,
                    ) => [f64::from(3.2_f32), 8.0, 12.0, 16.0],
                    (
                        0x157c,
                        Some(207),
                        0x0400_0000_0000_26f0,
                        0x0400_0000_0000_1dd3,
                        0x0d80_0000_0000_1962,
                    ) => [16.0, 40.0, 60.0, 80.0],
                    _ => return None,
                };
                let difficulty = match self.binding.mode_variables.get(&6751)? {
                    Value::Asset(0x0d80_0000_0000_195e) => 0,
                    Value::Asset(0x0d80_0000_0000_195f) => 1,
                    Value::Asset(0x0d80_0000_0000_26df) => 2,
                    Value::Asset(0x0d80_0000_0000_1960) => 3,
                    _ => return None,
                };
                return Some(Value::Float(amounts[difficulty]));
            }
            if matches!(
                (site.graph, site.node),
                (0x01ca, Some(126)) | (0x00cb | 0x14b1, _)
            ) && crate::pve_encounter::messages::sighting_selector(config)
            {
                return Some(crate::pve_encounter::messages::recipients(
                    &self.control_records.defenders,
                ));
            }
            if matches!(
                (site.graph, site.node),
                (0x01ca, Some(127)) | (0x00cb | 0x14b1, _)
            ) && crate::pve_encounter::messages::self_team_query(config)
            {
                return Some(Value::Integer(i64::from(self.binding.team)));
            }
        }
        if native_shape(config, "STU_A3DEF368", None) {
            Some(Value::Entity(GAME_MODE_ENTITY))
        } else if native_shape(config, "STU_37F20D24", Some("m_resourceKey"))
            && config.get("m_resourceKey").is_none_or(Json::is_null)
        {
            Some(Value::Asset(self.binding.mode))
        } else if native_shape(config, "STU_A77AC2AC", None) {
            Some(Value::Bool(self.binding.mode == TUTORIAL_MODE))
        } else {
            None
        }
    }
}

impl EnemyWorld<'_> {
    fn queue_defender_sighting(
        &mut self,
        site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if is_junkenstein_mode(self.binding.mode)
            && site.entity == self.binding.body
            && matches!(
                (site.graph, site.node),
                (0x01ca, Some(127)) | (0x00cb | 0x14b1, _)
            )
            && message == SIGHTING
            && self.control_records.defenders.contains(&target)
        {
            if self.control_records.sightings.len() >= MAX_INIT_WORK {
                return false;
            }
            let tags = SIGHTING_TAGS.map(|tag| self.has_target_tag(site, site.entity, tag));
            self.control_records.sightings.push(DefenderSighting {
                target,
                source: SightingSource {
                    entity: site.entity,
                    position: self.binding.pose.position,
                    tags,
                    filter_flags: self.entity_filter_flags(site, site.entity),
                },
                parameters: parameters.clone(),
            });
            return true;
        }
        false
    }
}
