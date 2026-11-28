//! Deferred script operations for generation-checked room bodies.

use super::{
    BotState, CombatError, EliminationStreak, PlayerBodyState, RoomSimulation, RoomSpatialFrame,
    SampledBody, Vec3, health::HealthEffect,
};
use crate::{
    game_combat_messages::CombatNotice,
    game_hitscan::ImpactGeometry,
    game_replication::BodyId,
    script_init::{RoomBodyIdentity, RoomCue, RoomCueSpeaker, RoomMessage},
    vitals::Death,
    world::GAME_MODE_ENTITY,
};
use bevy_ecs::entity::Entity;
use std::{collections::HashSet, time::Duration};

pub(super) const MAX_BODY_SCRIPT_REQUESTS: usize = 64;

#[derive(Clone, Copy)]
pub(super) struct ActorHealthSnapshot {
    pub(super) total: f64,
    pub(super) max_total: f64,
    pub(super) health: f64,
    pub(super) max_health: f64,
    pub(super) armour: f64,
    pub(super) max_armour: f64,
    pub(super) shields: f64,
    pub(super) max_shields: f64,
}

impl ActorHealthSnapshot {
    pub(super) fn from_vitals(vitals: &crate::vitals::Vitals) -> Self {
        use crate::vitals::PoolKind;
        Self {
            total: vitals.total(),
            max_total: vitals.maximum(),
            health: vitals.amount(Some(PoolKind::Health), false),
            max_health: vitals.amount(Some(PoolKind::Health), true),
            armour: vitals.amount(Some(PoolKind::Armour), false),
            max_armour: vitals.amount(Some(PoolKind::Armour), true),
            shields: vitals.amount(Some(PoolKind::Shields), false),
            max_shields: vitals.amount(Some(PoolKind::Shields), true),
        }
    }

    pub(super) fn amount(self, kind: Option<crate::vitals::PoolKind>, maximum: bool) -> f64 {
        use crate::vitals::PoolKind;
        match (kind, maximum) {
            (None, false) => self.total,
            (None, true) => self.max_total,
            (Some(PoolKind::Health), false) => self.health,
            (Some(PoolKind::Health), true) => self.max_health,
            (Some(PoolKind::Armour), false) => self.armour,
            (Some(PoolKind::Armour), true) => self.max_armour,
            (Some(PoolKind::Shields), false) => self.shields,
            (Some(PoolKind::Shields), true) => self.max_shields,
        }
    }
}

#[derive(Clone)]
pub(super) struct RoomScriptTarget {
    pub(super) entity: Entity,
    pub(super) controller: Option<u32>,
    pub(super) model: Option<(u64, u64)>,
    pub(super) candidate_index: Option<usize>,
    pub(super) position: Vec3,
    pub(super) angles: [i32; 2],
    pub(super) velocity: Vec3,
    pub(super) eye_position: Option<Vec3>,
    pub(super) filter_flags: Option<u64>,
    pub(super) script_state: crate::script_entity_state::EntityScriptState,
    pub(super) health: bool,
    pub(super) health_snapshot: Option<ActorHealthSnapshot>,
    pub(super) messages: bool,
    pub(super) cue_listener: bool,
    pub(super) voice: Option<(u64, u64)>,
}

pub(crate) enum ActorVoiceQuery {
    Criterion(u64),
    #[allow(dead_code)]
    OwnerDeath(u64),
    EquippedEffect(u64),
    Stimulus(u64),
}

#[derive(Clone, Copy)]
pub(super) struct DeathVoiceBody {
    pub(super) entity: Entity,
    pub(super) team: Option<u8>,
    pub(super) voice: (u64, u64),
    pub(super) listener: bool,
}

impl RoomSpatialFrame {
    pub(crate) fn actor_voice_available(&self, source: u32) -> bool {
        self.actor_voice.is_some()
            && self
                .script_targets
                .get(&source)
                .is_some_and(|body| body.voice.is_some())
    }

    pub(crate) fn actor_voice_has_stimulus(&self, source: u32, stimulus: u64) -> bool {
        let Some(catalog) = self.actor_voice.as_ref() else {
            return false;
        };
        let Some((_, source_body)) = self.actor_sample(source) else {
            return false;
        };
        let Some((hero, skin)) = source_body.voice else {
            return false;
        };
        catalog.stimulus_options(hero, skin, stimulus).is_some()
    }

    pub(crate) fn actor_voice_request(
        &self,
        source: u32,
        query: ActorVoiceQuery,
        choose: &mut dyn FnMut(usize) -> usize,
    ) -> Option<(RoomCue, Vec<u32>)> {
        self.actor_voice_request_with_audience(source, query, None, None, choose)
    }

    pub(crate) fn actor_voice_request_with_audience(
        &self,
        source: u32,
        query: ActorVoiceQuery,
        subject: Option<u32>,
        explicit_audience: Option<&[u32]>,
        choose: &mut dyn FnMut(usize) -> usize,
    ) -> Option<(RoomCue, Vec<u32>)> {
        let catalog = self.actor_voice.as_ref()?;
        let corpse = matches!(query, ActorVoiceQuery::OwnerDeath(_))
            .then(|| self.dead_voice.get(&source))
            .flatten();
        let (team, (hero, skin)) = if let Some(body) = corpse {
            (body.team, body.voice)
        } else {
            let (actor, body) = self.actor_sample(source)?;
            (actor.team, body.voice?)
        };
        let options = match query {
            ActorVoiceQuery::Criterion(criterion) | ActorVoiceQuery::OwnerDeath(criterion) => {
                catalog.options(hero, skin, criterion)
            }
            ActorVoiceQuery::EquippedEffect(effect) => catalog.equipped_options(hero, skin, effect),
            ActorVoiceQuery::Stimulus(stimulus) => catalog.stimulus_options(hero, skin, stimulus),
        }?;
        let mut listeners: Vec<_> = match explicit_audience {
            Some(audience) => {
                let mut seen = HashSet::with_capacity(audience.len());
                audience
                    .iter()
                    .copied()
                    .filter(|entity| {
                        seen.insert(*entity)
                            && self
                                .script_targets
                                .get(entity)
                                .is_some_and(|body| body.cue_listener)
                    })
                    .collect()
            }
            None => {
                let team_only = matches!(
                    query,
                    ActorVoiceQuery::Criterion(_) | ActorVoiceQuery::OwnerDeath(_)
                );
                self.candidates
                    .iter()
                    .filter(|listener| {
                        (!team_only
                            || listener.entity == source
                            || team.is_some() && team == listener.team)
                            && self
                                .script_targets
                                .get(&listener.entity)
                                .is_some_and(|body| body.cue_listener)
                    })
                    .map(|listener| listener.entity)
                    .collect()
            }
        };
        if corpse.is_some_and(|body| body.listener)
            && explicit_audience.is_none_or(|ids| ids.contains(&source))
        {
            listeners.push(source);
        }
        if listeners.is_empty() || listeners.len() > RoomCue::MAX_TARGETS {
            return None;
        }
        let ally_heroes: Vec<u64> = self
            .candidates
            .iter()
            .filter(|cand| cand.entity != source && team.is_some() && team == cand.team)
            .filter_map(|cand| self.script_targets.get(&cand.entity)?.voice.map(|(h, _)| h))
            .collect();
        let ctx = crate::actor_voice::VoiceContext {
            map: self.map_guid,
            target_hero: subject
                .and_then(|wire| self.script_targets.get(&wire)?.voice.map(|(hero, _)| hero)),
            ally_heroes: &ally_heroes,
        };
        let (stimulus, line) = options.choose_with_context(&ctx, choose)?;
        Some((
            RoomCue {
                stimulus,
                line,
                speaker: RoomCueSpeaker::Actor,
            },
            listeners,
        ))
    }
}

#[derive(Clone)]
pub(crate) struct RoomScriptRequest {
    wire: u32,
    entity: Entity,
    effect: ScriptEffect,
}

impl RoomScriptRequest {
    fn for_target(frame: &RoomSpatialFrame, wire: u32, effect: ScriptEffect) -> Option<Self> {
        let target = frame.script_targets.get(&wire)?;
        let supported = match &effect {
            ScriptEffect::Health(_) | ScriptEffect::Contact { .. } | ScriptEffect::Stun { .. } => {
                target.health
            }
            ScriptEffect::Message(_) => target.messages,
            ScriptEffect::Play { .. } => target.messages && target.candidate_index.is_some(),
            ScriptEffect::Cue(..) => target.cue_listener,
            ScriptEffect::SpawnPet { .. }
            | ScriptEffect::DespawnPet { .. }
            | ScriptEffect::StartDuplicate { .. }
            | ScriptEffect::HackHealthPack { .. } => true,
        };
        supported.then_some(Self {
            wire,
            entity: target.entity,
            effect,
        })
    }
}

#[derive(Clone)]
enum ScriptEffect {
    Health(HealthEffect),
    Contact {
        source_frame: u32,
        damage: f64,
        loadout: Option<u64>,
        tags: crate::script_init::HitTags,
        impulse: Option<Vec3>,
    },
    Message(RoomMessage),
    Play {
        caller: crate::script_init::Site,
        request: crate::script_init::PlayScript,
        instigator: Entity,
    },
    Cue(RoomCue, Entity, u32, Entity, bool),
    SpawnPet {
        pet_id: u32,
        definition: u64,
        attach: Option<u32>,
        position: Vec3,
        yaw: i32,
        max_health: Option<f64>,
        local_offset: Option<Vec3>,
    },
    DespawnPet {
        pet_id: u32,
    },
    Stun {
        ticks: u32,
    },
    StartDuplicate {
        target_wire: u32,
        original_body: u64,
    },
    HackHealthPack {
        position: Vec3,
    },
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct PlayerRoomCue {
    pub(crate) owner: u32,
    pub(crate) listener: BodyId,
    pub(crate) source: RoomBodyIdentity,
    pub(crate) notice: CombatNotice,
}

#[derive(Clone, Default)]
pub(crate) struct RoomScriptRequests(Vec<RoomScriptRequest>);

impl RoomScriptRequests {
    pub(crate) fn queue_start_duplicate(
        &mut self,
        frame: &RoomSpatialFrame,
        owner_wire: u32,
        target_wire: u32,
        original_body: u64,
    ) -> bool {
        self.queue(
            frame,
            owner_wire,
            ScriptEffect::StartDuplicate {
                target_wire,
                original_body,
            },
        )
    }

    pub(crate) fn queue_health(&mut self, frame: &RoomSpatialFrame, wire: u32, delta: f64) -> bool {
        let Some(effect) = HealthEffect::from_delta(delta) else {
            return false;
        };
        self.queue(frame, wire, ScriptEffect::Health(effect))
    }

    /// Admit every target into the body outbox, or leave existing work intact.
    pub(crate) fn queue_health_changes(
        &mut self,
        frame: &RoomSpatialFrame,
        changes: &[(u32, f64)],
    ) -> bool {
        self.queue_batch(
            frame,
            changes.iter().map(|&(wire, delta)| {
                Some((wire, ScriptEffect::Health(HealthEffect::from_delta(delta)?)))
            }),
        )
    }

    pub(crate) fn queue_contact_damage(
        &mut self,
        frame: &RoomSpatialFrame,
        source_frame: u32,
        damage: f64,
        changes: &[(u32, Option<Vec3>)],
    ) -> bool {
        self.queue_contact_damage_with_meta(
            frame,
            source_frame,
            damage,
            None,
            crate::script_init::HitTags::EMPTY,
            changes,
        )
    }

    pub(crate) fn queue_contact_damage_with_meta(
        &mut self,
        frame: &RoomSpatialFrame,
        source_frame: u32,
        damage: f64,
        loadout: Option<u64>,
        tags: crate::script_init::HitTags,
        changes: &[(u32, Option<Vec3>)],
    ) -> bool {
        if !damage.is_finite() || damage < 0.0 {
            return false;
        }
        self.queue_batch(
            frame,
            changes.iter().map(|&(wire, impulse)| {
                let valid = impulse
                    .is_some_and(|delta| crate::mover::VelocityChange::Impulse(delta).valid());
                (if impulse.is_some() {
                    valid
                } else {
                    damage > 0.0
                })
                .then_some((
                    wire,
                    ScriptEffect::Contact {
                        source_frame,
                        damage,
                        loadout,
                        tags,
                        impulse,
                    },
                ))
            }),
        )
    }

    fn queue_batch(
        &mut self,
        frame: &RoomSpatialFrame,
        changes: impl ExactSizeIterator<Item = Option<(u32, ScriptEffect)>>,
    ) -> bool {
        if changes.len() > MAX_BODY_SCRIPT_REQUESTS.saturating_sub(self.0.len()) {
            return false;
        }
        let mut targets = HashSet::with_capacity(changes.len());
        let mut staged = Vec::with_capacity(changes.len());
        for change in changes {
            let Some((wire, effect)) = change else {
                return false;
            };
            if !targets.insert(wire) {
                return false;
            }
            let Some(request) = RoomScriptRequest::for_target(frame, wire, effect) else {
                return false;
            };
            staged.push(request);
        }
        self.0.extend(staged);
        true
    }

    pub(crate) fn queue_play_script(
        &mut self,
        frame: &RoomSpatialFrame,
        caller: crate::script_init::Site,
        target: u32,
        request: crate::script_init::PlayScript,
    ) -> bool {
        let Some(source) = frame.script_targets.get(&request.source) else {
            return false;
        };
        self.queue(
            frame,
            target,
            ScriptEffect::Play {
                caller,
                request,
                instigator: source.entity,
            },
        )
    }

    pub(crate) fn queue_message(
        &mut self,
        frame: &RoomSpatialFrame,
        wire: u32,
        message: RoomMessage,
    ) -> bool {
        if message.id == 0 {
            return false;
        }
        self.queue(frame, wire, ScriptEffect::Message(message))
    }

    pub(crate) fn queue_broadcast_others(
        &mut self,
        frame: &RoomSpatialFrame,
        exclude_wire: u32,
        message: RoomMessage,
    ) -> bool {
        if message.id == 0 {
            return false;
        }
        // One extra recipient is sufficient for queue_batch to reject an oversized broadcast.
        let mut recipients: Vec<_> = frame
            .script_targets
            .iter()
            .filter_map(|(&wire, target)| (wire != exclude_wire && target.messages).then_some(wire))
            .take(MAX_BODY_SCRIPT_REQUESTS + 1)
            .collect();
        recipients.sort_unstable();
        self.queue_batch(
            frame,
            recipients
                .into_iter()
                .map(|wire| Some((wire, ScriptEffect::Message(message.clone())))),
        )
    }

    pub(crate) fn queue_cue(
        &mut self,
        frame: &RoomSpatialFrame,
        source: u32,
        targets: &[u32],
        cue: RoomCue,
    ) -> bool {
        self.queue_speaker_cue(frame, source, source, targets, cue)
    }

    pub(crate) fn queue_speaker_cue(
        &mut self,
        frame: &RoomSpatialFrame,
        source: u32,
        speaker: u32,
        targets: &[u32],
        cue: RoomCue,
    ) -> bool {
        self.queue_voice_delivery(frame, source, speaker, targets, cue, false)
    }

    #[allow(dead_code)]
    pub(crate) fn queue_death_voice(
        &mut self,
        frame: &RoomSpatialFrame,
        source: u32,
        criterion: u64,
        subject: Option<u32>,
        audience: Option<&[u32]>,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        if let Some((cue, listeners)) = frame.actor_voice_request_with_audience(
            source,
            ActorVoiceQuery::OwnerDeath(criterion),
            subject,
            audience,
            choose,
        ) {
            self.queue_voice_delivery(frame, source, source, &listeners, cue, true);
        }
    }

    fn queue_voice_delivery(
        &mut self,
        frame: &RoomSpatialFrame,
        source: u32,
        speaker: u32,
        targets: &[u32],
        cue: RoomCue,
        death: bool,
    ) -> bool {
        let corpse = death.then(|| frame.dead_voice.get(&source)).flatten();
        let Some(speaker_entity) = frame
            .script_targets
            .get(&speaker)
            .map(|body| body.entity)
            .or_else(|| corpse.map(|body| body.entity))
        else {
            return false;
        };
        if cue.stimulus == 0 || cue.line == 0 || targets.len() > RoomCue::MAX_TARGETS {
            return false;
        }
        let Some(source) = frame
            .script_targets
            .get(&source)
            .map(|target| target.entity)
            .or_else(|| corpse.map(|body| body.entity))
        else {
            return false;
        };
        let mut seen = HashSet::with_capacity(targets.len());
        let staged: Vec<_> = targets
            .iter()
            .copied()
            .filter(|wire| seen.insert(*wire))
            .filter_map(|wire| {
                let effect = ScriptEffect::Cue(
                    cue,
                    source,
                    speaker,
                    speaker_entity,
                    death && wire == speaker,
                );
                if let Some(body) = corpse.filter(|b| b.listener && wire == speaker) {
                    Some(RoomScriptRequest {
                        wire,
                        entity: body.entity,
                        effect,
                    })
                } else {
                    RoomScriptRequest::for_target(frame, wire, effect)
                }
            })
            .collect();
        if staged.len() > MAX_BODY_SCRIPT_REQUESTS.saturating_sub(self.0.len()) {
            return false;
        }
        self.0.extend(staged);
        true
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn queue_spawn_pet(
        &mut self,
        frame: &RoomSpatialFrame,
        owner: u32,
        pet_id: u32,
        definition: u64,
        attach: Option<u32>,
        position: Vec3,
        yaw: i32,
        max_health: Option<f64>,
        local_offset: Option<Vec3>,
    ) -> bool {
        if definition == 0 || !position.iter().all(|c| c.is_finite()) {
            return false;
        }
        self.queue(
            frame,
            owner,
            ScriptEffect::SpawnPet {
                pet_id,
                definition,
                attach,
                position,
                yaw,
                max_health,
                local_offset,
            },
        )
    }

    pub(crate) fn queue_despawn_pet(
        &mut self,
        frame: &RoomSpatialFrame,
        owner: u32,
        pet_id: u32,
    ) -> bool {
        self.queue(frame, owner, ScriptEffect::DespawnPet { pet_id })
    }

    pub(crate) fn queue_stun(&mut self, frame: &RoomSpatialFrame, target: u32, ticks: u32) -> bool {
        if ticks == 0 {
            return false;
        }
        self.queue(frame, target, ScriptEffect::Stun { ticks })
    }

    pub(crate) fn queue_hack_health_pack(
        &mut self,
        frame: &RoomSpatialFrame,
        owner_wire: u32,
        position: Vec3,
    ) -> bool {
        self.queue(frame, owner_wire, ScriptEffect::HackHealthPack { position })
    }

    fn queue(&mut self, frame: &RoomSpatialFrame, wire: u32, effect: ScriptEffect) -> bool {
        if self.0.len() >= MAX_BODY_SCRIPT_REQUESTS {
            return false;
        }
        let Some(request) = RoomScriptRequest::for_target(frame, wire, effect) else {
            return false;
        };
        self.0.push(request);
        true
    }

    pub(crate) fn take(&mut self) -> Vec<RoomScriptRequest> {
        std::mem::take(&mut self.0)
    }
}

impl RoomSimulation {
    /// Collect every actor's outgoing work before routing to any recipient.
    pub(super) fn settle_authored_requests(
        &mut self,
        tick: u32,
        now: Duration,
    ) -> Result<(), CombatError> {
        if self.spatial_frame.is_none() {
            return Ok(());
        }
        let mut batches = Vec::new();
        for (&wire, &entity) in &self.players_by_wire {
            if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
                if let Some(runtime) = state.runtime.as_mut() {
                    let requests = runtime.take_script_requests();
                    if !requests.is_empty() {
                        batches.push((SampledBody { wire, entity }, requests));
                    }
                }
            }
        }
        for (&wire, &entity) in &self.by_wire {
            if let Some(mut state) = self.world.get_mut::<BotState>(entity) {
                if let Some(enemy) = &mut state.enemy {
                    let requests = enemy.scripts.take_script_requests();
                    if !requests.is_empty()
                        && enemy.fault.is_none()
                        && !enemy.scripts.retirement_requested
                    {
                        batches.push((SampledBody { wire, entity }, requests));
                    }
                }
                if let Some(script) = &mut state.stock_script {
                    let requests = script.take_script_requests();
                    if !requests.is_empty() {
                        batches.push((SampledBody { wire, entity }, requests));
                    }
                }
            }
        }
        for (owner, requests) in self.drain_deployable_script_requests() {
            if let Some(source) = self.sample_health_body(owner) {
                batches.push((source, requests));
            }
        }
        for (source, requests) in batches {
            for request in requests {
                self.settle_script_request(request, source, tick, now)?;
            }
        }
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn settle_test_script_requests(
        &mut self,
        source_wire: u32,
        requests: Vec<RoomScriptRequest>,
        tick: u32,
    ) -> Result<(), CombatError> {
        let Some(source) = self.sample_health_body(source_wire) else {
            return Ok(());
        };
        for request in requests {
            self.settle_script_request(request, source, tick, Duration::ZERO)?;
        }
        Ok(())
    }

    pub(super) fn settle_script_request(
        &mut self,
        request: RoomScriptRequest,
        source: SampledBody,
        tick: u32,
        now: Duration,
    ) -> Result<(), CombatError> {
        if !self.room_body_generation_current(request.wire, request.entity)
            || !self.room_body_generation_current(source.wire, source.entity)
        {
            return Ok(());
        }
        let death = Death {
            time: now.as_secs_f64(),
            frame: tick,
            killer: source.wire,
        };
        match request.effect {
            ScriptEffect::Health(mut effect) => {
                let is_heal = matches!(effect, HealthEffect::Heal(_));
                if let HealthEffect::Damage(ref mut amount) = effect {
                    *amount =
                        self.scale_continuous_beam_damage(source.wire, request.wire, *amount, tick);
                }
                if let Some(outcome) = self.apply_health(request.wire, effect, death, now, None)? {
                    if is_heal && outcome.delta > 0.0 {
                        self.deliver_applied_heal(source.wire, request.wire, outcome.delta);
                    }
                }
            }
            ScriptEffect::Contact {
                source_frame,
                damage,
                loadout,
                tags,
                impulse,
            } => {
                let same_team_other = source.wire != request.wire && {
                    #[cfg(feature = "lua-mods")]
                    let ffa = self.free_for_all || self.mod_friendly_fire();
                    #[cfg(not(feature = "lua-mods"))]
                    let ffa = self.free_for_all;
                    if ffa {
                        false
                    } else {
                        let source_team = self.actor_team(source.wire);
                        let target_team = self.actor_team(request.wire);
                        source_team.is_some() && source_team == target_team
                    }
                };
                if same_team_other {
                    return Ok(());
                }
                let damage =
                    self.scale_continuous_beam_damage(source.wire, request.wire, damage, tick);
                if damage > 0.0 {
                    let target_facts = self
                        .sample_health_body(request.wire)
                        .and_then(|body| body.health_target_facts(&self.world));
                    let contact_impact = crate::game_hitscan::Impact {
                        tags,
                        entity: request.wire,
                        distance: 0.0,
                        damage,
                        critical: false,
                        loadout,
                        impulse,
                        geometry: None,
                    };
                    let settled = self.apply_health(
                        request.wire,
                        HealthEffect::Damage(damage),
                        death,
                        now,
                        Some(&contact_impact),
                    )?;
                    if let Some(outcome) = settled.filter(|outcome| outcome.delta < 0.0) {
                        let dealt = -outcome.delta;
                        // Ensure monotonic non-zero `shot_frame` so client `0x6c3043`
                        // always processes `CombatNotice::Hit` and reveals overhead healthbars.
                        let hit_frame = source_frame.max(tick).max(1);
                        self.record_contact_feedback(
                            source,
                            request.wire,
                            hit_frame,
                            dealt,
                            outcome.killed,
                            loadout,
                        );
                        if let Some(attacker_owner) = self
                            .player_handle_for_wire(source.wire)
                            .and_then(|id| self.player_body(id).map(|p| p.owner))
                        {
                            self.scoreboard.record_damage(
                                attacker_owner,
                                request.wire,
                                dealt,
                                false,
                                now.as_secs_f64(),
                            );
                        }
                        if source.wire != request.wire {
                            if let Some(mut facts) = target_facts {
                                if let Some(body) = self.sample_health_body(request.wire) {
                                    facts.room_status = self.sample_health_target_room_status(body);
                                }
                                self.deliver_applied_damage_by_wire(super::AppliedDamage {
                                    source: source.wire,
                                    amount: dealt,
                                    named_pool_losses: Vec::new(),
                                    tags: contact_impact.tags,
                                    target_facts: facts,
                                });
                            }
                        }
                        if let Some(id) = self.player_handle_for_wire(request.wire) {
                            if let Some(owner) = self.player_body(id).map(|p| p.owner) {
                                self.record_received_damage(
                                    owner,
                                    id,
                                    source.wire,
                                    dealt as f32,
                                    None,
                                    false,
                                    false,
                                    false,
                                );
                            }
                        }
                    }
                }
                if let Some(delta) = impulse {
                    self.queue_combat_impulse(request.wire, tick, delta)?;
                }
            }
            ScriptEffect::Play {
                caller,
                request: played,
                instigator,
            } => {
                if caller.entity == source.wire
                    && self.room_body_generation_current(played.source, instigator)
                {
                    self.run_status_script(
                        request.wire,
                        super::status_scripts::StatusScriptOp::Play {
                            caller,
                            request: &played,
                        },
                    );
                }
            }
            ScriptEffect::Message(message) => {
                // Authored hero ability messages with room-level gameplay semantics:
                // - Mercy Resurrect (`0x0785` / `0x1BBE` / `0x0959`) -> revive fallen ally SoulAnchor
                // - Roadhog Chain Hook (`0x01B3` / `0x076B` / `0x0A44` / `0x0B57`) -> 2-stage hook pull
                // - Junkrat Concussion Mine detonate (`0x013D` / `0x013E` / `0x013F`) -> remote mine blast
                let topic = message.id;
                if matches!(
                    topic,
                    0x0240_0000_0000_0785 | 0x0240_0000_0000_1bbe | 0x0240_0000_0000_0959
                ) {
                    if let (Some(team), Some(pos)) =
                        (self.actor_team(source.wire), source.position(&self.world))
                    {
                        let _ = self.try_resurrect_nearest_ally(team, pos, tick, now);
                    }
                } else if matches!(
                    topic,
                    0x0240_0000_0000_01b3
                        | 0x0240_0000_0000_076b
                        | 0x0240_0000_0000_0a44
                        | 0x0240_0000_0000_0b57
                ) && request.wire != source.wire
                {
                    let hostile = self.free_for_all
                        || self.actor_team(source.wire) != self.actor_team(request.wire);
                    if hostile {
                        self.attach_roadhog_hook_pull(source.wire, request.wire, tick);
                    }
                } else if matches!(topic, 0x0240_0000_0000_013d..=0x0240_0000_0000_013f) {
                    self.detonate_owner_concussion_mine(source.wire, tick, now);
                }
                self.route_room_message(request.wire, request.entity, source.wire, message);
            }
            ScriptEffect::StartDuplicate {
                target_wire,
                original_body,
            } => {
                self.activate_echo_duplicate(source.wire, target_wire, original_body, tick);
            }
            ScriptEffect::SpawnPet {
                pet_id,
                definition,
                attach,
                position,
                yaw,
                max_health,
                local_offset,
            } => {
                let team = self.actor_team(source.wire).unwrap_or(0);
                self.spawn_room_pet(
                    source.wire,
                    team,
                    pet_id,
                    definition,
                    attach,
                    position,
                    yaw,
                    max_health,
                    local_offset,
                    tick,
                );
            }
            ScriptEffect::DespawnPet { pet_id } => {
                self.despawn_room_pet(pet_id);
            }
            ScriptEffect::Stun { ticks } => {
                let same_team_other = source.wire != request.wire && {
                    #[cfg(feature = "lua-mods")]
                    let ffa = self.free_for_all || self.mod_friendly_fire();
                    #[cfg(not(feature = "lua-mods"))]
                    let ffa = self.free_for_all;
                    if ffa {
                        false
                    } else {
                        let source_team = self.actor_team(source.wire);
                        let target_team = self.actor_team(request.wire);
                        source_team.is_some() && source_team == target_team
                    }
                };
                if same_team_other {
                    return Ok(());
                }
                self.apply_actor_stun(request.wire, tick, ticks);
                // Roadhog Chain Hook latch stun at distance -> engage 2-stage pull.
                let roadhog_body = self.ability_rules.roadhog_hook.body;
                let is_roadhog = self
                    .players_by_wire
                    .get(&source.wire)
                    .and_then(|&ent| self.world.get::<PlayerBodyState>(ent))
                    .is_some_and(|p| p.body_template == roadhog_body);
                if is_roadhog && request.wire != source.wire {
                    self.attach_roadhog_hook_pull(source.wire, request.wire, tick);
                }
            }
            ScriptEffect::HackHealthPack { position } => {
                if let Some(team) = self.actor_team(source.wire) {
                    let _ = self.hack_health_pack(position, team, tick);
                }
            }
            ScriptEffect::Cue(cue, source_entity, voice_wire, voice_entity, death_self) => {
                if source_entity != source.entity
                    || !self.room_body_generation_current(voice_wire, voice_entity)
                {
                    return Ok(());
                }
                if let Some(player) =
                    self.world
                        .get::<PlayerBodyState>(request.entity)
                        .filter(|player| {
                            player.alive()
                                || death_self && request.wire == voice_wire && !player.alive()
                        })
                {
                    let speaker = match cue.speaker {
                        RoomCueSpeaker::Actor => voice_wire,
                        RoomCueSpeaker::Mode => GAME_MODE_ENTITY,
                    };
                    self.player_cues.push(PlayerRoomCue {
                        owner: player.owner,
                        listener: player.id,
                        source: RoomBodyIdentity {
                            wire: voice_wire,
                            generation: voice_entity.to_bits(),
                        },
                        notice: CombatNotice::Voice {
                            anchor: speaker,
                            speaker,
                            listener: request.wire,
                            stimulus: cue.stimulus,
                            line: cue.line,
                        },
                    });
                    if cue.stimulus == crate::actor_voice::STIMULUS_HELLO
                        && request.wire == voice_wire
                    {
                        self.trigger_nearby_bot_hello_reply(voice_wire, tick);
                    }
                }
            }
        }
        Ok(())
    }

    fn record_contact_feedback(
        &mut self,
        source: SampledBody,
        target: u32,
        shot_frame: u32,
        dealt: f64,
        killed: bool,
        loadout: Option<u64>,
    ) {
        let Some(shooter) = self.world.get::<PlayerBodyState>(source.entity) else {
            return;
        };
        let source = source.identity();
        let notices = [
            Some(CombatNotice::Hit {
                target,
                dealt,
                critical: false,
                killed,
                shot_frame,
            }),
            killed.then_some(CombatNotice::Elimination {
                killer: source.wire,
                victim: target,
                loadout: loadout.unwrap_or(0),
                fire: 100,
                critical: false,
            }),
        ];
        self.player_cues
            .extend(notices.into_iter().flatten().map(|notice| PlayerRoomCue {
                owner: shooter.owner,
                listener: shooter.id,
                source,
                notice,
            }));
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn record_received_damage(
        &mut self,
        owner: u32,
        victim: BodyId,
        source: u32,
        dealt: f32,
        geometry: Option<ImpactGeometry>,
        critical: bool,
        armor_or_shield: bool,
        special_vfx: bool,
    ) {
        if !self.received_damage_feedback
            || victim.entity == source
            || !dealt.is_finite()
            || dealt <= 0.0
        {
            return;
        }
        let Some(source) = self.sample_health_body(source) else {
            return;
        };
        let origin = geometry
            .map(ImpactGeometry::origin)
            .or_else(|| source.position(&self.world));
        let victim_state = self
            .players_by_wire
            .get(&victim.entity)
            .and_then(|&entity| self.world.get::<PlayerBodyState>(entity));
        let victim_center = victim_state.map(|state| {
            let p = state.mover.pose().position;
            [p[0], p[1] + state.mover.eye_height() * 0.75, p[2]]
        });
        let normal = geometry.and_then(ImpactGeometry::surface_normal);
        let fallback_dir = match (origin, victim_center) {
            (Some(orig), Some(vc)) => {
                let delta = [orig[0] - vc[0], orig[1] - vc[1], orig[2] - vc[2]];
                let len = crate::static_ray::norm(delta);
                (len > 1e-6).then(|| delta.map(|v| v / len))
            }
            _ => None,
        };
        let impact_point = match (victim_center, normal.or(fallback_dir)) {
            (Some(vc), Some(n)) => Some([
                vc[0] + n[0] * 0.35,
                vc[1] + n[1] * 0.25,
                vc[2] + n[2] * 0.35,
            ]),
            (vc, _) => vc,
        };
        let contact_set = victim_state
            .and_then(|state| {
                self.body_contact_sets
                    .get(&state.body_template)
                    .copied()
                    .filter(|&set| set != 0)
                    .or_else(|| {
                        (state.body_template != 0).then(|| {
                            self.body_contact_sets
                                .values()
                                .copied()
                                .find(|&set| set != 0)
                        })?
                    })
            })
            .unwrap_or(0);
        self.player_cues.push(PlayerRoomCue {
            owner,
            listener: victim,
            source: source.identity(),
            notice: CombatNotice::DamageReceived {
                source: source.wire,
                victim: victim.entity,
                dealt,
                origin,
                impact_point,
                normal,
                contact_set,
                critical,
                armor_or_shield,
                special_vfx,
            },
        });
    }

    /// Trigger a client-side `25000` (`@0xd0 = true`) status hit reaction on a
    /// stunned/slept/frozen/anti-healed player victim so affected players display
    /// immediate impact VFX/flinch feedback on status application.
    pub(super) fn emit_status_vfx_feedback(&mut self, source_wire: u32, target_wire: u32) {
        if !self.received_damage_feedback || source_wire == target_wire {
            return;
        }
        if let Some(id) = self.player_handle_for_wire(target_wire) {
            if let Some(owner) = self.player_body(id).map(|p| p.owner) {
                self.record_received_damage(owner, id, source_wire, 1.0, None, false, false, true);
            }
        }
    }

    pub(crate) fn room_cue_source_current(&self, source: RoomBodyIdentity) -> bool {
        self.sample_health_body(source.wire)
            .is_some_and(|body| body.identity() == source)
    }

    fn room_body_generation_current(&self, wire: u32, entity: Entity) -> bool {
        self.by_wire
            .get(&wire)
            .or_else(|| self.players_by_wire.get(&wire))
            == Some(&entity)
            || self
                .room_pets
                .get(&wire)
                .is_some_and(|pet| pet.ecs_entity == entity)
            || self
                .room_deployables
                .get(&wire)
                .is_some_and(|dep| dep.ecs_entity == entity)
    }

    pub(crate) fn room_cue_listener_current(&self, owner: u32, listener: BodyId) -> bool {
        self.player_body(listener)
            .is_some_and(|player| player.owner == owner && player.alive())
    }

    pub(crate) fn take_player_cues(&mut self) -> Vec<PlayerRoomCue> {
        let mut cues = std::mem::take(&mut self.player_cues);
        cues.retain(|cue| {
            self.room_cue_source_current(cue.source)
                && self.room_cue_listener_current(cue.owner, cue.listener)
        });
        cues
    }

    pub(super) fn record_actor_hurt_voice(
        &mut self,
        speaker_wire: u32,
        is_bot: bool,
        attacker: u32,
        critical: bool,
        killed: bool,
        frame: u32,
    ) {
        if !self.combat_voice.has_hurt_cues() || !crate::script_expr::valid_entity(speaker_wire) {
            return;
        }
        if is_bot && !self.players_by_wire.contains_key(&attacker) {
            return;
        }
        let prev = self.hurt_voice_history.get(&speaker_wire).copied();
        let cooldown = self.combat_voice.hurt_cooldown_ticks();
        if !killed
            && prev.is_some_and(|(last_frame, _)| frame.saturating_sub(last_frame) < cooldown)
        {
            return;
        }
        let pick = prev.map_or(0, |(_, count)| count);
        let body = if is_bot {
            self.by_wire.get(&speaker_wire).and_then(|&entity| {
                self.world
                    .get::<BotState>(entity)
                    .map(|state| state.bot.spawn.body)
            })
        } else {
            self.players_by_wire.get(&speaker_wire).and_then(|&entity| {
                self.world
                    .get::<PlayerBodyState>(entity)
                    .map(|state| state.body_template)
            })
        };
        let Some((stimulus, line)) = self
            .combat_voice
            .hurt_cue(body, is_bot, critical, killed, pick)
        else {
            return;
        };
        let last_frame = prev.map_or(frame, |(last, _)| last.max(frame));
        self.hurt_voice_history
            .insert(speaker_wire, (last_frame, pick.wrapping_add(1)));
        for (&listener_wire, &player_entity) in &self.players_by_wire {
            let Some(player) = self
                .world
                .get::<PlayerBodyState>(player_entity)
                .filter(|player| player.id.entity == listener_wire && player.alive())
            else {
                continue;
            };
            self.player_cues.push(PlayerRoomCue {
                owner: player.owner,
                listener: player.id,
                source: RoomBodyIdentity {
                    wire: listener_wire,
                    generation: player_entity.to_bits(),
                },
                notice: CombatNotice::Voice {
                    anchor: speaker_wire,
                    speaker: speaker_wire,
                    listener: listener_wire,
                    stimulus,
                    line,
                },
            });
        }
    }

    pub(super) fn record_enemy_elimination(
        &mut self,
        killer_wire: u32,
        victim_wire: u32,
        victim_team: u8,
        frame: u32,
    ) {
        if killer_wire == victim_wire || !crate::script_expr::valid_entity(killer_wire) {
            return;
        }
        let Some(&entity) = self.players_by_wire.get(&killer_wire) else {
            return;
        };
        let Some(player) = self
            .world
            .get::<PlayerBodyState>(entity)
            .filter(|player| player.id.entity == killer_wire)
        else {
            return;
        };
        if !self.free_for_all && player.team == victim_team {
            return;
        }
        let owner = player.owner;
        let listener = player.id;
        let now_secs = f64::from(frame) * crate::game_clock::TICK_PERIOD.as_secs_f64();
        let on_objective = self
            .pvp_objective
            .as_ref()
            .zip(
                self.sample_health_body(victim_wire)
                    .and_then(|sample| sample.position(&self.world)),
            )
            .is_some_and(|(pvp, pos)| pvp.is_on_objective(pos));
        self.scoreboard
            .record_elimination(owner, victim_wire, on_objective, now_secs);
        let owner_on_fire = self
            .scoreboard
            .player(owner)
            .is_some_and(|board| board.on_fire.on_fire);
        // Both the elimination event and the OnFire transition are forwarded to
        // the body VM below. Its graph owns waits, speakers and voice criteria.
        self.replay.record_event(crate::game_replay::ReplayEvent {
            tick: frame,
            kind: crate::game_replay::ReplayEventKind::Elimination {
                killer: killer_wire,
                victim: victim_wire,
                critical: false,
                environmental: false,
                ult_shutdown: false,
                lifesaver: false,
                distance: 10.0,
            },
        });
        let target = self.authored_elimination_target(victim_wire);
        let window = self.combat_voice.multikill_window_ticks();
        let eligible = window != 0
            && (!self.combat_voice.has_multikill_exclusions()
                || target
                    .as_ref()
                    .map(|facts| self.combat_voice.multikill_target_allowed(&facts.tags))
                    .unwrap_or_else(|| {
                        self.sample_health_body(victim_wire)
                            .and_then(|body| body.health_target_facts(&self.world))
                            .is_some_and(|facts| {
                                self.combat_voice.multikill_target_allowed(&facts.tags)
                            })
                    }));
        let genji_body = self.ability_rules.genji_dash_reset.body;
        let Some(mut player) = self.world.get_mut::<PlayerBodyState>(entity) else {
            return;
        };
        let is_genji = player.body_template == genji_body;
        if let Some(runtime) = player.runtime.as_mut() {
            runtime.set_on_fire(owner_on_fire);
        }
        if let Some((target, runtime)) = target.zip(player.runtime.as_mut()) {
            let _ = runtime.receive_authored_elimination_feedback(target.into());
        }
        if is_genji {
            if let Some(runtime) = player.runtime.as_mut() {
                let _ = runtime.reset_all_cooldowns();
            }
        }
        if !eligible {
            return;
        }
        let streak = player.elimination_streak.get_or_insert(EliminationStreak {
            last_frame: frame,
            expires_at: frame.saturating_add(window),
            count: 0,
        });
        if frame < streak.last_frame {
            // Older settlements cannot rewind the announcer's window.
            return;
        }
        streak.count = if frame <= streak.expires_at {
            streak.count.saturating_add(1)
        } else {
            1
        };
        // On the expiry frame, message actions can still announce against the
        // old count. The finishing wait then clears it and cancels its reentry.
        if frame != streak.expires_at {
            streak.expires_at = frame.saturating_add(window);
        }
        streak.last_frame = frame;
        let count = streak.count;
        if let Some((stimulus, line)) = self.combat_voice.multikill_cue(count, count as usize) {
            self.player_cues.push(PlayerRoomCue {
                owner,
                listener,
                source: RoomBodyIdentity {
                    wire: killer_wire,
                    generation: entity.to_bits(),
                },
                notice: CombatNotice::Voice {
                    anchor: GAME_MODE_ENTITY,
                    speaker: GAME_MODE_ENTITY,
                    listener: killer_wire,
                    stimulus,
                    line,
                },
            });
        }
    }

    fn route_room_message(&mut self, wire: u32, entity: Entity, source: u32, message: RoomMessage) {
        // A full or halted recipient can drop the message. Scheduling does
        // not execute that VM or turn its local queue rejection into a room fault.
        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
            if state.alive() {
                if let Some(runtime) = &mut state.runtime {
                    let _ = runtime.receive_room_message(source, message);
                }
            }
        } else if let Some(mut state) = self.world.get_mut::<BotState>(entity) {
            if !state.bot.dead && state.vitals.death().is_none() {
                if let Some(enemy) = state
                    .enemy
                    .as_mut()
                    .filter(|enemy| enemy.fault.is_none() && !enemy.scripts.retirement_requested)
                {
                    let _ = enemy.scripts.receive_room_message(source, message);
                } else if let Some(script) = &mut state.stock_script {
                    script.receive_room_message(source, message);
                }
            }
        } else if let Some(pet) = self.room_pets.get_mut(&wire) {
            if let Some(component) = pet.component.as_mut() {
                let _ = component.queue_room_message_from(source, message);
            }
        } else if let Some(dep) = self.room_deployables.get_mut(&wire) {
            if let Some(component) = dep.component.as_mut() {
                let _ = component.queue_room_message_from(source, message);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::{collections::HashMap, sync::Arc};

    use super::*;
    use crate::game_combat::tests::fixture_ground;

    #[test]
    fn room_cues_reach_only_selected_live_player_generations() {
        use crate::{
            contact_world::ContactWorld, game_combat::tests::fixture_bots, mover::Mover,
            mover_settings::MoverSettings, script_graph::RoomSpatialNeeds,
        };

        let bots = fixture_bots();
        let speaker = bots[0].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        let first = BodyId {
            entity: 30,
            generation: 1,
        };
        let second = BodyId {
            entity: 31,
            generation: 1,
        };
        let outsider = BodyId {
            entity: 32,
            generation: 1,
        };
        let spawn = |room: &mut RoomSimulation, id, owner, slot| {
            let mover = Mover::new(
                MoverSettings::default(),
                Arc::new(ContactWorld::new(Vec::new()).unwrap()),
                [1.0, 0.0, 0.0],
                0,
                8,
            )
            .unwrap();
            room.spawn_player_body(PlayerBodyState::new(
                id, owner, slot, 1, 1, mover, None, 1, None,
            ))
            .unwrap();
        };
        spawn(&mut room, first, 2, 0);
        spawn(&mut room, second, 3, 1);
        spawn(&mut room, outsider, 4, 2);
        room.enable_spatial_queries(false, Arc::new(fixture_ground()), RoomSpatialNeeds::Queries);
        room.prepare_spatial_frame(1).unwrap();

        let frame = &room.spatial_frame.as_ref().unwrap().1;
        let source = frame.script_targets[&speaker].entity;
        let sender = SampledBody {
            wire: speaker,
            entity: source,
        };
        let cue = RoomCue {
            stimulus: 41,
            line: 42,
            speaker: RoomCueSpeaker::Actor,
        };
        let mode_cue = RoomCue {
            speaker: RoomCueSpeaker::Mode,
            ..cue
        };
        let mut outbox = RoomScriptRequests::default();
        assert!(outbox.queue_cue(
            frame,
            speaker,
            &[first.entity, speaker, second.entity, first.entity],
            cue
        ));
        assert!(outbox.queue_cue(frame, speaker, &[second.entity], cue));
        assert!(outbox.queue_cue(frame, speaker, &[first.entity], mode_cue));
        assert!(outbox.queue_cue(frame, speaker, &[], cue));
        let [
            valid,
            expires_after_settlement,
            expires_before_settlement,
            mode,
        ]: [RoomScriptRequest; 4] = outbox.take().try_into().ok().unwrap();
        assert_eq!(valid.wire, first.entity);
        assert_eq!(expires_after_settlement.wire, second.entity);

        for request in [valid, expires_after_settlement, mode] {
            room.settle_script_request(request, sender, 1, Duration::ZERO)
                .unwrap();
        }
        assert!(room.retire_player_body(second));
        spawn(
            &mut room,
            BodyId {
                generation: 2,
                ..second
            },
            3,
            1,
        );
        room.settle_script_request(expires_before_settlement, sender, 1, Duration::ZERO)
            .unwrap();
        assert_eq!(
            room.take_player_cues(),
            [
                PlayerRoomCue {
                    owner: 2,
                    listener: first,
                    source: sender.identity(),
                    notice: CombatNotice::Voice {
                        anchor: speaker,
                        speaker,
                        listener: first.entity,
                        stimulus: 41,
                        line: 42,
                    },
                },
                PlayerRoomCue {
                    owner: 2,
                    listener: first,
                    source: sender.identity(),
                    notice: CombatNotice::Voice {
                        anchor: GAME_MODE_ENTITY,
                        speaker: GAME_MODE_ENTITY,
                        listener: first.entity,
                        stimulus: 41,
                        line: 42,
                    },
                }
            ]
        );
        assert!(room.take_player_cues().is_empty());

        room.prepare_spatial_frame(2).unwrap();
        let frame = &room.spatial_frame.as_ref().unwrap().1;
        assert!(outbox.queue_cue(frame, outsider.entity, &[first.entity], mode_cue));
        let request = outbox.take().pop().unwrap();
        let outsider_sender = room.sample_health_body(outsider.entity).unwrap();
        room.settle_script_request(request, outsider_sender, 2, Duration::ZERO)
            .unwrap();
        assert!(room.retire_player_body(outsider));
        spawn(
            &mut room,
            BodyId {
                generation: 2,
                ..outsider
            },
            4,
            2,
        );
        assert!(room.take_player_cues().is_empty());
    }

    fn request_frame(bodies: &[(u32, bool, bool)]) -> RoomSpatialFrame {
        let mut world = bevy_ecs::world::World::new();
        let mut target = |candidate_index, health, messages| RoomScriptTarget {
            entity: world.spawn_empty().id(),
            model: None,
            controller: None,
            candidate_index: Some(candidate_index),
            position: [0.0; 3],
            angles: [0; 2],
            velocity: [0.0; 3],
            eye_position: None,
            filter_flags: None,
            script_state: Default::default(),
            health,
            health_snapshot: None,
            messages,
            cue_listener: health,
            voice: None,
        };
        RoomSpatialFrame {
            candidates: bodies
                .iter()
                .map(|&(entity, _, _)| crate::script_expr::SpatialCandidate {
                    entity,
                    position: [0.0; 3],
                    team: None,
                })
                .collect(),
            actor_capsules: None,
            entity_facts: None,
            dead_voice: HashMap::new(),
            script_targets: bodies
                .iter()
                .enumerate()
                .map(|(index, &(wire, health, messages))| (wire, target(index, health, messages)))
                .collect(),
            player_wires: HashMap::new(),
            ground: Arc::new(fixture_ground()),
            breakables: None,
            actor_voice: None,
            map_guid: None,
            retired: Arc::default(),
            captured_tick: 0,
            debug_rays: Arc::default(),
        }
    }

    #[test]
    fn invalid_batches_leave_mixed_outbox_work_and_capacity_intact() {
        let frame = request_frame(&[(1, true, false), (2, true, false), (3, false, true)]);
        let mut outbox = RoomScriptRequests::default();
        let message = RoomMessage {
            id: 71,
            parameters: Default::default(),
        };
        assert!(outbox.queue_message(&frame, 3, message));

        for invalid in [(0, -2.0), (3, -2.0), (2, f64::NAN), (2, 0.0), (1, 2.0)] {
            assert!(!outbox.queue_health_changes(&frame, &[(1, -1.0), invalid]));
            assert_eq!(outbox.0.len(), 1);
        }
        for _ in 0..MAX_BODY_SCRIPT_REQUESTS - 2 {
            assert!(outbox.queue_health(&frame, 1, -1.0));
        }
        assert!(!outbox.queue_health_changes(&frame, &[(1, -1.0), (2, 2.0)]));
        assert_eq!(outbox.0.len(), MAX_BODY_SCRIPT_REQUESTS - 1);
        assert!(outbox.queue_health_changes(&frame, &[(2, 2.0)]));
        assert!(outbox.queue_health_changes(&frame, &[]));
        assert!(!outbox.queue_cue(
            &frame,
            3,
            &[2],
            RoomCue {
                stimulus: 9,
                line: 10,
                speaker: RoomCueSpeaker::Actor,
            }
        ));

        let queued = outbox.take();
        assert_eq!(queued.len(), MAX_BODY_SCRIPT_REQUESTS);
        assert!(matches!(queued[0].effect, ScriptEffect::Message(_)));
        assert_eq!(queued.last().unwrap().wire, 2);
    }

    #[test]
    fn room_script_broadcast_is_ordered_and_atomic_at_capacity() {
        #[derive(serde::Deserialize)]
        struct Case {
            sender: u32,
            message_id: u64,
            message_receivers: Vec<u32>,
            cue_only_listener: u32,
            expected_recipients: Vec<u32>,
        }
        let case: Case = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/script-room-broadcast-v1.json"
        ))
        .unwrap();
        let mut bodies: Vec<_> = case
            .message_receivers
            .iter()
            .map(|&wire| (wire, false, true))
            .collect();
        bodies.push((case.cue_only_listener, true, false));
        let message = RoomMessage {
            id: case.message_id,
            parameters: Default::default(),
        };
        let needed = case.expected_recipients.len();
        let first = request_frame(&bodies);
        bodies.reverse();
        for frame in [first, request_frame(&bodies)] {
            for available in [needed - 1, needed] {
                let mut outbox = RoomScriptRequests::default();
                let queued = MAX_BODY_SCRIPT_REQUESTS - available;
                for _ in 0..queued {
                    assert!(outbox.queue_health(&frame, case.cue_only_listener, -1.0));
                }
                let accepted = outbox.queue_broadcast_others(&frame, case.sender, message.clone());
                assert_eq!(accepted, available == needed);
                let requests = outbox.take();
                assert!(requests[..queued].iter().all(|request| {
                    request.wire == case.cue_only_listener
                        && matches!(request.effect, ScriptEffect::Health(_))
                }));
                let delivered: Vec<_> = requests[queued..]
                    .iter()
                    .map(|request| {
                        assert!(
                            matches!(&request.effect, ScriptEffect::Message(m) if m == &message)
                        );
                        assert_eq!(request.entity, frame.script_targets[&request.wire].entity);
                        request.wire
                    })
                    .collect();
                if accepted {
                    assert_eq!(delivered, case.expected_recipients);
                } else {
                    assert!(delivered.is_empty());
                }
            }
        }
    }

    #[test]
    fn room_script_broadcast_without_receivers_uses_no_capacity() {
        let frame = request_frame(&[(1, false, true), (2, true, false)]);
        let mut outbox = RoomScriptRequests::default();
        for _ in 0..MAX_BODY_SCRIPT_REQUESTS {
            assert!(outbox.queue_health(&frame, 2, -1.0));
        }
        let message = RoomMessage {
            id: 0,
            parameters: Default::default(),
        };
        assert!(!outbox.queue_broadcast_others(&frame, 1, message.clone()));
        assert!(outbox.queue_broadcast_others(&frame, 1, RoomMessage { id: 5, ..message }));
        assert_eq!(outbox.take().len(), MAX_BODY_SCRIPT_REQUESTS);
    }
}
