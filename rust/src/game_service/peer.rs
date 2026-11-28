//! Per-peer delivery, loading readiness, and combat UI state.

use super::*;
use crate::{
    game_combat::{PlayerDeath, RoomSimulation},
    game_replication::BodyId,
    script_init::RoomBodyIdentity,
};

const MAX_PENDING_COMBAT_NOTICES: usize = 256;

pub(super) struct Peer {
    pub(super) ingress: GameIngress,
    pub(super) egress: GameEgress<Arc<Record>>,
    pub(super) loading: MapReadiness,
    pub(super) ownership: Option<u32>,
    pub(super) open: bool,
    pub(super) world: WorldDelivery,
    pub(super) combat_ui: CombatUi,
    authored_cue_ordinal: Option<u32>,
    #[cfg(test)]
    pub(super) observed_receipts: Option<Vec<crate::game_egress::DeliveryReport<Arc<Record>>>>,
}

#[derive(Default)]
pub(super) struct CombatUi {
    notices: VecDeque<PendingCombatNotice>,
    replacements: VecDeque<(Duration, BotReplacement)>,
    /// Recent `(shooter_body, target, shot_frame, critical, killed)` hit notices
    /// already admitted to the wire, preventing multi-pellet shotguns (Doomfist,
    /// Reaper) whose pellets land across adjacent ticks from replaying hit dings.
    recent_hit_notices: VecDeque<(BodyId, u32, u32, bool, bool)>,
    /// Recent `(healer_body, target, last_frame)` heal confirmation notices
    /// throttled to ~100ms (6 ticks) so continuous beams emit crisp heal cues.
    recent_heal_notices: VecDeque<(BodyId, u32, u32)>,
    payload_voice: crate::room_primitives::RoomVoiceRouter,
}

impl CombatUi {
    pub(super) fn queue_legacy(&mut self, notice: CombatNotice) {
        if self.notices.len() >= MAX_PENDING_COMBAT_NOTICES {
            self.notices.pop_front();
        }
        self.notices.push_back(PendingCombatNotice {
            notice,
            audience: CombatAudience::Legacy,
        });
    }
}

struct AuthoredCueIdentity {
    listener: BodyId,
    source: RoomBodyIdentity,
}

struct PendingCombatNotice {
    notice: CombatNotice,
    audience: CombatAudience,
}

enum CombatAudience {
    Legacy,
    Shooter(BodyId),
    Victim(BodyId),
    Authored(AuthoredCueIdentity),
}

impl PendingCombatNotice {
    fn matches_audience(
        &self,
        body: Option<BodyId>,
        owner: u32,
        simulation: Option<&RoomSimulation>,
    ) -> bool {
        match &self.audience {
            CombatAudience::Legacy => match self.notice {
                CombatNotice::Voice { listener, .. } => {
                    body.is_some_and(|body| body.entity == listener)
                }
                _ => true,
            },
            CombatAudience::Shooter(recipient) | CombatAudience::Victim(recipient) => {
                body == Some(*recipient)
            }
            CombatAudience::Authored(cue) => {
                let listener = match self.notice {
                    CombatNotice::Voice { listener, .. } => listener,
                    CombatNotice::DamageReceived { victim, .. } => victim,
                    _ => return false,
                };
                body == Some(cue.listener)
                    && cue.listener.entity == listener
                    && simulation.is_some_and(|room| {
                        room.room_cue_source_current(cue.source)
                            && room.room_cue_listener_current(owner, cue.listener)
                    })
            }
        }
    }
}

pub(super) fn initial_controller(
    scene: &PreparedScene,
    tick: u32,
) -> io::Result<crate::bits::BitWriter> {
    scene
        .controller()
        .controller_frame(
            tick,
            ControllerFrame {
                select_open: true,
                skins: !scene.map().is_junkenstein(),
                hide_assemble: scene.hide_assemble(),
                ..ControllerFrame::default()
            },
        )
        .map_err(invalid)
}

pub(super) fn script_record(
    entity: u32,
    payload: &crate::bits::BitWriter,
) -> io::Result<EntityUpdate> {
    Ok(EntityUpdate {
        entity,
        chunk: Some(chunk(payload, 0, 1).map_err(invalid)?),
        ..EntityUpdate::default()
    })
}

pub(crate) fn player_entity(slot: usize) -> u32 {
    0xa000_0000 | ((slot as u32 + 1) << 8)
}

impl Peer {
    pub(super) fn new(owner: u32) -> Self {
        Self {
            ingress: GameIngress::default(),
            egress: GameEgress::default(),
            loading: MapReadiness::default(),
            ownership: None,
            open: false,
            world: WorldDelivery::new(owner),
            combat_ui: CombatUi::default(),
            authored_cue_ordinal: None,
            #[cfg(test)]
            observed_receipts: None,
        }
    }

    pub(super) fn clear_world(&mut self) {
        self.world.clear();
        self.combat_ui = CombatUi::default();
    }

    pub(super) fn clear_world_in_room(
        &mut self,
        simulation: &mut crate::game_combat::RoomSimulation,
    ) {
        self.world.clear_in_room(simulation);
        self.combat_ui = CombatUi::default();
    }

    pub(super) fn bot_hit(&mut self, hit: BotHit) {
        if let Some(body) = self
            .world
            .active_body_id()
            .filter(|body| body.entity == hit.damage.source)
        {
            for notice in hit.notices.into_iter().flatten() {
                self.queue_shooter_notice(body, notice);
            }
        }
    }

    pub(super) fn queue_shooter_notice(&mut self, body: BodyId, notice: CombatNotice) {
        if let CombatNotice::Hit {
            target,
            dealt,
            critical,
            killed,
            shot_frame,
        } = notice
        {
            if let Some(PendingCombatNotice {
                notice:
                    CombatNotice::Hit {
                        dealt: existing_dealt,
                        critical: existing_critical,
                        killed: existing_killed,
                        ..
                    },
                ..
            }) = self.combat_ui.notices.iter_mut().rev().find(|pending| {
                matches!(
                    (&pending.audience, &pending.notice),
                    (
                        CombatAudience::Shooter(existing_body),
                        CombatNotice::Hit {
                            target: existing_target,
                            shot_frame: existing_frame,
                            ..
                        }
                    ) if *existing_body == body
                        && *existing_target == target
                        && *existing_frame == shot_frame
                )
            }) {
                *existing_dealt += dealt;
                *existing_critical |= critical;
                *existing_killed |= killed;
                return;
            }
            if let Some(recent) = self
                .combat_ui
                .recent_hit_notices
                .iter_mut()
                .rev()
                .find(|entry| {
                    entry.0 == body && entry.1 == target && entry.2.abs_diff(shot_frame) <= 2
                })
            {
                let upgrades_audio = (critical && !recent.3) || (killed && !recent.4);
                recent.3 |= critical;
                recent.4 |= killed;
                if !upgrades_audio {
                    return;
                }
            } else {
                if self.combat_ui.recent_hit_notices.len() >= 32 {
                    self.combat_ui.recent_hit_notices.pop_front();
                }
                self.combat_ui
                    .recent_hit_notices
                    .push_back((body, target, shot_frame, critical, killed));
            }
        } else if let CombatNotice::HealConfirm {
            target,
            amount,
            shot_frame,
        } = notice
        {
            if let Some(PendingCombatNotice {
                notice:
                    CombatNotice::HealConfirm {
                        amount: existing_amount,
                        shot_frame: existing_frame,
                        ..
                    },
                ..
            }) = self.combat_ui.notices.iter_mut().rev().find(|pending| {
                matches!(
                    (&pending.audience, &pending.notice),
                    (
                        CombatAudience::Shooter(existing_body),
                        CombatNotice::HealConfirm {
                            target: existing_target,
                            ..
                        }
                    ) if *existing_body == body && *existing_target == target
                )
            }) {
                *existing_amount += amount;
                *existing_frame = (*existing_frame).max(shot_frame);
                return;
            }
            if let Some(recent) = self
                .combat_ui
                .recent_heal_notices
                .iter_mut()
                .rev()
                .find(|entry| entry.0 == body && entry.1 == target)
            {
                if shot_frame.saturating_sub(recent.2) < 6 {
                    return;
                }
                recent.2 = shot_frame;
            } else {
                if self.combat_ui.recent_heal_notices.len() >= 32 {
                    self.combat_ui.recent_heal_notices.pop_front();
                }
                self.combat_ui
                    .recent_heal_notices
                    .push_back((body, target, shot_frame));
            }
        } else if let CombatNotice::HealEvent {
            target,
            amount,
            full,
            ..
        } = notice
        {
            if let Some(PendingCombatNotice {
                notice:
                    CombatNotice::HealEvent {
                        amount: existing_amount,
                        full: existing_full,
                        ..
                    },
                ..
            }) = self.combat_ui.notices.iter_mut().rev().find(|pending| {
                matches!(
                    (&pending.audience, &pending.notice),
                    (
                        CombatAudience::Shooter(existing_body),
                        CombatNotice::HealEvent {
                            target: existing_target,
                            ..
                        }
                    ) if *existing_body == body && *existing_target == target
                )
            }) {
                *existing_amount += amount;
                *existing_full |= full;
                return;
            }
        }
        self.queue_notice(notice, CombatAudience::Shooter(body));
    }

    pub(super) fn queue_bot_replacement(&mut self, replacement: BotReplacement, expires: Duration) {
        if let Some((_, pending)) = self
            .combat_ui
            .replacements
            .iter_mut()
            .find(|(_, pending)| pending.created.entity == replacement.retired)
        {
            // Intermediate generations held here were never published to this peer.
            pending.created = replacement.created;
            pending.waits_for_presentation = replacement.waits_for_presentation;
        } else if self.world.tracks_current_bot(replacement.retired) {
            self.combat_ui
                .replacements
                .push_back((expires, replacement));
        }
    }

    pub(super) fn queue_combat_notice(&mut self, notice: CombatNotice) {
        self.queue_notice(notice, CombatAudience::Legacy);
    }

    pub(super) fn advance_payload_voice(
        &mut self,
        schemas: &Schemas,
        team: u8,
        now: Duration,
    ) -> Result<(), RoomError> {
        use crate::room_primitives::VoiceCue;
        let listener = self.world.spawned_body().unwrap_or(u32::MAX);
        let send =
            |egress: &mut GameEgress<Arc<Record>>, cue: VoiceCue| -> Result<u32, RoomError> {
                let message = CombatNotice::Voice {
                    speaker: GAME_MODE_ENTITY,
                    anchor: GAME_MODE_ENTITY,
                    listener,
                    stimulus: cue.stimulus_guid(),
                    line: cue.line_guid(),
                }
                .encode(schemas)?;
                Ok(egress.queue_reliable(message)?)
            };
        let cues = self.world.take_payload_cues();
        let router = &mut self.combat_ui.payload_voice;
        if let Some(&first) = cues.first() {
            let ordinal = send(&mut self.egress, first)?;
            router.enqueue(u64::from(ordinal), 2.0, team, &cues);
        } else if let Some(next) = router.poll(now.as_secs_f64(), team, |ordinal| {
            u64::from(u32::MAX) >= ordinal && self.egress.delivered_through(ordinal as u32)
        }) {
            let ordinal = send(&mut self.egress, next)?;
            if let Some(pending) = &mut router.follow_up {
                pending.ordinal = u64::from(ordinal);
            }
        }
        Ok(())
    }

    pub(super) fn queue_player_death(&mut self, death: PlayerDeath) {
        if self.world.owner() == death.owner && self.world.active_body_id() == Some(death.body) {
            self.queue_notice(
                CombatNotice::Death {
                    source: death.source,
                    killed_by: death.killed_by,
                },
                CombatAudience::Victim(death.body),
            );
        }
    }

    pub(super) fn death_notice_pending(&self, victim: BodyId) -> bool {
        self.combat_ui.notices.iter().any(
            |pending| matches!(&pending.audience, CombatAudience::Victim(body) if *body == victim),
        )
    }

    fn queue_notice(&mut self, notice: CombatNotice, audience: CombatAudience) {
        if self.combat_ui.notices.len() == MAX_PENDING_COMBAT_NOTICES {
            self.combat_ui.notices.pop_front();
        }
        self.combat_ui
            .notices
            .push_back(PendingCombatNotice { notice, audience });
    }

    pub(super) fn queue_player_cue(
        &mut self,
        listener: BodyId,
        source: RoomBodyIdentity,
        notice: CombatNotice,
    ) {
        if self.combat_ui.notices.len() == MAX_PENDING_COMBAT_NOTICES {
            return;
        }
        self.combat_ui.notices.push_back(PendingCombatNotice {
            notice,
            audience: CombatAudience::Authored(AuthoredCueIdentity { listener, source }),
        });
    }

    #[cfg(test)]
    pub(super) fn pending_combat_notices(&self) -> impl Iterator<Item = &CombatNotice> {
        self.combat_ui.notices.iter().map(|pending| &pending.notice)
    }

    pub(super) fn acknowledge(
        &mut self,
        ack: u32,
        bits: u64,
    ) -> crate::game_egress::DeliveryReport<Arc<Record>> {
        #[cfg(feature = "inspector")]
        let pending_cue = self.authored_cue_ordinal.filter(|&ordinal| {
            crate::owner_trace::playtest_enabled() && !self.egress.delivered_through(ordinal)
        });
        let report = self.egress.acknowledge(ack, bits);
        #[cfg(feature = "inspector")]
        if let Some(ordinal) = pending_cue.filter(|&ordinal| self.egress.delivered_through(ordinal))
        {
            crate::owner_trace::playtest(format_args!(
                "combat audio acknowledged owner={:08X} ordinal={ordinal}",
                self.world.owner(),
            ));
        }
        report
    }

    pub(super) fn flush_combat(
        &mut self,
        schemas: &Schemas,
        now: Duration,
        simulation: Option<&RoomSimulation>,
    ) -> Result<(), RoomError> {
        for _ in 0..self.combat_ui.notices.len() {
            let pending = self
                .combat_ui
                .notices
                .pop_front()
                .ok_or(RoomError::Invariant("pending combat notice disappeared"))?;
            if !pending.matches_audience(
                self.world.active_body_id(),
                self.world.owner(),
                simulation,
            ) {
                continue;
            }
            let notice = pending.notice;
            let authored = match &pending.audience {
                CombatAudience::Authored(cue) => Some(cue),
                _ => None,
            };
            let is_voice = matches!(notice, CombatNotice::Voice { .. });
            if authored.is_some_and(|cue| {
                !self.world.identity_ready(cue.listener.entity)
                    || (is_voice
                        && self.authored_cue_ordinal.is_some_and(|ordinal| {
                            self.egress.oldest_pending_reliable() <= u64::from(ordinal)
                        }))
            }) {
                self.combat_ui.notices.push_back(pending);
                continue;
            }
            if matches!(&pending.audience, CombatAudience::Shooter(body) if !self.world.owner_body_ready(*body))
                || notice
                    .required_identity()
                    .is_some_and(|entity| !self.world.identity_ready(entity))
                || (notice.needs_mode_ui() && !self.world.mode_ready())
            {
                self.combat_ui.notices.push_back(pending);
                continue;
            }
            let bytes = match notice.encode(schemas) {
                Ok(bytes) => bytes,
                Err(crate::game_messages::ChannelError::UnknownMessage { .. })
                    if matches!(
                        notice,
                        CombatNotice::HealEvent { .. }
                            | CombatNotice::ScorePopup { .. }
                            | CombatNotice::HighlightHeader { .. }
                            | CombatNotice::CommendationCardNotice { .. }
                            | CombatNotice::CardVoteUpdate { .. }
                    ) =>
                {
                    continue;
                }
                Err(error) => return Err(error.into()),
            };
            let ordinal = self.egress.queue_reliable(bytes)?;
            if authored.is_some() && is_voice {
                self.authored_cue_ordinal = Some(ordinal);
            }
            #[cfg(feature = "inspector")]
            if crate::owner_trace::playtest_enabled() {
                match notice {
                    CombatNotice::Hit {
                        target,
                        dealt,
                        critical,
                        killed,
                        shot_frame,
                    } => {
                        crate::owner_trace::playtest(format_args!(
                            "combat hit admitted owner={:08X} frame={shot_frame} target={target:08X} dealt={dealt} critical={critical} killed={killed} ordinal={ordinal}",
                            self.world.owner(),
                        ));
                    }
                    CombatNotice::DamageReceived {
                        source,
                        victim,
                        dealt,
                        ..
                    } => {
                        crate::owner_trace::playtest(format_args!(
                            "received damage admitted owner={:08X} source={source:08X} victim={victim:08X} dealt={dealt} ordinal={ordinal}",
                            self.world.owner(),
                        ));
                    }
                    CombatNotice::Voice {
                        speaker,
                        listener,
                        stimulus,
                        line,
                        ..
                    } => {
                        crate::owner_trace::playtest(format_args!(
                            "combat audio admitted owner={:08X} speaker={speaker:08X} listener={listener:08X} stimulus={stimulus:016X} line={line:016X} ordinal={ordinal} authored={}",
                            self.world.owner(),
                            authored.is_some(),
                        ));
                    }
                    _ => (),
                }
            }
            for entity in notice.referenced_entities() {
                self.world.track_entity_notice(entity, ordinal);
            }
        }
        for _ in 0..self.combat_ui.replacements.len() {
            let (expires, replacement) = self
                .combat_ui
                .replacements
                .pop_front()
                .ok_or(RoomError::Invariant("pending bot replacement disappeared"))?;
            let entity = replacement.retired;
            if !self.world.tracks_current_bot(entity) {
                continue;
            }
            let has_notice = self
                .combat_ui
                .notices
                .iter()
                .any(|pending| pending.notice.required_identity() == Some(entity));
            let waiting_for_reliable = self.world.pending_entity_notice(entity, &self.egress);
            // A live replacement can itself have a pending create behind the
            // earlier generation's destroy. Keep that ordering across respawns.
            if !self.world.identity_ready(entity)
                || (now < expires
                    && (has_notice || waiting_for_reliable || self.world.health_pending(entity)))
            {
                self.combat_ui
                    .replacements
                    .push_back((expires, replacement));
            } else {
                self.combat_ui
                    .notices
                    .retain(|pending| pending.notice.required_identity() != Some(entity));
                self.world.replace_bot(replacement);
            }
        }
        Ok(())
    }

    pub(super) fn reliable(
        &mut self,
        schemas: &Schemas,
        message: Message,
    ) -> Result<u32, RoomError> {
        let bytes = encode(schemas, message.id, &message.fields)?;
        Ok(self.egress.queue_reliable(bytes)?)
    }

    pub(super) fn advance_loading(
        &mut self,
        schemas: &Schemas,
        room: &Room,
        slot: usize,
        now: Duration,
        tick: u32,
    ) -> Result<(), RoomError> {
        let player = room.players.get(slot).ok_or(RoomError::Invariant(
            "loading member has invalid roster slot",
        ))?;
        let ready = WorldReadiness {
            ownership_delivered: self
                .ownership
                .is_some_and(|id| self.egress.delivered_through(id)),
            collision_ready: room.world.is_some(),
            bot_scripts_ready: room
                .world
                .as_ref()
                .is_some_and(|world| world.scene.bot_scripts_ready()),
        };
        while let Some(step) = self.loading.take_due(now, ready) {
            match step {
                MapReadyStep::Ownership => {
                    self.ownership = Some(self.reliable(schemas, ownership(player.entity))?);
                }
                MapReadyStep::Game => {
                    self.reliable(schemas, room.message(slot, false)?)?;
                }
                MapReadyStep::World => {
                    let world = room
                        .world
                        .as_ref()
                        .ok_or(RoomError::Invariant("map-entry prerequisites missing"))?;
                    let viewer = room
                        .appearance(slot)
                        .ok_or(RoomError::Invariant("prepared player missing"))?;
                    for other in (0..room.players.len()).filter(|&other| other != slot) {
                        if let Some(participant) = room.appearance(other) {
                            self.world
                                .queue_participant(crate::match_scene::player_create(
                                    &room.map,
                                    &participant,
                                    None,
                                    None,
                                )?);
                        }
                    }
                    self.world.start(&world.scene, &viewer, tick)?;
                }
                MapReadyStep::Controller => self.world.presentation_due = true,
            }
        }
        if self.world.presentation_due {
            let world = room
                .world
                .as_ref()
                .ok_or(RoomError::Invariant("started world missing"))?;
            self.world.present(&world.scene, tick)?;
        }
        if self.world.world_started() {
            let world = room
                .world
                .as_ref()
                .ok_or(RoomError::Invariant("started scene scripts have no world"))?;
            self.world.present_scene_scripts(&world.scene, tick)?;
            self.world.present_bot_controllers(&world.scene)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authored_cues_preserve_legacy_notice_matching_and_capacity() {
        let listener = BodyId {
            entity: 0xa001_0100,
            generation: 4,
        };
        let replacement = BodyId {
            generation: 5,
            ..listener
        };
        let notice = CombatNotice::Voice {
            anchor: 0xc001_0000,
            speaker: 0xc001_0000,
            listener: listener.entity,
            stimulus: 1,
            line: 2,
        };
        let source = RoomBodyIdentity {
            wire: 0xc001_0000,
            generation: bevy_ecs::world::World::new().spawn_empty().id().to_bits(),
        };
        let mut peer = Peer::new(0xa001_0000);
        peer.queue_player_cue(listener, source, notice);
        peer.queue_combat_notice(notice);

        let legacy = &peer.combat_ui.notices[1];
        assert!(legacy.matches_audience(Some(replacement), peer.world.owner(), None));

        // With no current body, flushing discards both before reliable admission.
        let schemas = Schemas::from_json("{}").unwrap();
        peer.flush_combat(&schemas, Duration::ZERO, None).unwrap();
        assert!(peer.combat_ui.notices.is_empty());

        let legacy = CombatNotice::Death {
            source: 0,
            killed_by: false,
        };
        for _ in 0..MAX_PENDING_COMBAT_NOTICES {
            peer.queue_combat_notice(legacy);
        }
        peer.queue_player_cue(listener, source, notice);
        assert_eq!(peer.combat_ui.notices.len(), MAX_PENDING_COMBAT_NOTICES);
        assert!(
            peer.pending_combat_notices()
                .all(|notice| *notice == legacy)
        );
    }

    #[test]
    fn queued_cue_requires_a_living_listener_at_reliable_admission() {
        use crate::{
            contact_world::ContactWorld, game_combat::PlayerBodyState, game_heroes::SOLDIER,
            game_replication::OwnerBodyRuntime, mover::Mover, mover_settings::MoverSettings,
            script_player::fixture_body_factory, vitals::Death,
        };
        use serde_json::{Value as Json, json};

        let fixture: Json = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/script-room-cue-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"]["reliable_admission"];
        let expected = &fixture["expected"]["reliable_admission"];
        let speaker = u32::try_from(fixture["input"]["speaker"].as_u64().unwrap()).unwrap();
        let (factory, spawn) = fixture_body_factory();
        let listener = BodyId {
            entity: spawn.entity,
            generation: input["listener_generation"].as_u64().unwrap(),
        };
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let movement_world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let body = |id, owner, slot, runtime: Option<Box<dyn OwnerBodyRuntime>>| {
            let mover = Mover::new(
                MoverSettings::default(),
                Arc::clone(&movement_world),
                spawn.pose.position,
                0,
                0x08,
            )
            .unwrap();
            PlayerBodyState::new(
                id,
                owner,
                slot,
                1,
                SOLDIER,
                mover,
                runtime,
                spawn.frame,
                None,
            )
        };
        let source = simulation
            .spawn_player_body(body(
                BodyId {
                    entity: speaker,
                    generation: 1,
                },
                speaker - 1,
                1,
                None,
            ))
            .unwrap();
        simulation
            .spawn_player_body(body(
                listener,
                spawn.controller,
                0,
                Some(Box::new(factory.start(spawn).unwrap())),
            ))
            .unwrap();

        let notice = CombatNotice::Voice {
            anchor: GAME_MODE_ENTITY,
            speaker: GAME_MODE_ENTITY,
            listener: listener.entity,
            stimulus: 1,
            line: 2,
        };
        let mut peer = Peer::new(spawn.controller);
        peer.queue_player_cue(
            listener,
            RoomBodyIdentity {
                wire: speaker,
                generation: source.entity.to_bits(),
            },
            notice,
        );
        peer.queue_combat_notice(notice);
        let eligibility = |room: &RoomSimulation| {
            let matches = |index: usize| {
                peer.combat_ui.notices[index].matches_audience(
                    Some(listener),
                    peer.world.owner(),
                    Some(room),
                )
            };
            json!({"authored": matches(0), "legacy": matches(1)})
        };
        assert_eq!(eligibility(&simulation), expected["before_listener_death"]);
        assert!(!peer.combat_ui.notices[0].matches_audience(
            Some(BodyId {
                generation: listener.generation + 1,
                ..listener
            }),
            peer.world.owner(),
            Some(&simulation),
        ));
        assert!(!peer.combat_ui.notices[0].matches_audience(
            Some(listener),
            speaker - 1,
            Some(&simulation),
        ));

        let (_, outcome, _) = simulation
            .damage_player_body_by_wire(
                listener,
                input["late_damage"].as_f64().unwrap(),
                Death {
                    killer: speaker,
                    frame: spawn.frame + 1,
                    time: 0.016,
                },
                None,
            )
            .unwrap();
        assert!(outcome.killed);
        assert_eq!(
            simulation.player_handle_for_wire(listener.entity),
            Some(listener)
        );
        assert_eq!(eligibility(&simulation), expected["after_listener_death"]);
    }
}
