//! Room-local execution and fault types. Unwinding never resumes a damaged room.

use super::*;
use std::panic::{AssertUnwindSafe, catch_unwind};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum RoomStage {
    Admission,
    Routing,
    Receive,
    Input,
    Bots,
    Projectiles,
    FinishTick,
    Loading,
    Send,
    Retire,
    Skin,
    #[cfg(any(test, feature = "inspector"))]
    Debug,
    Drain,
}

impl RoomStage {
    pub(super) fn name(self) -> &'static str {
        match self {
            Self::Admission => "admission",
            Self::Routing => "routing",
            Self::Receive => "receive",
            Self::Input => "input",
            Self::Bots => "bots",
            Self::Projectiles => "projectiles",
            Self::FinishTick => "finish_tick",
            Self::Loading => "loading",
            Self::Send => "send",
            Self::Retire => "retire",
            Self::Skin => "skin",
            #[cfg(any(test, feature = "inspector"))]
            Self::Debug => "debug",
            Self::Drain => "drain",
        }
    }
}

#[derive(Debug, Error)]
pub(super) enum RoomError {
    #[error("room invariant: {0}")]
    Invariant(&'static str),
    #[error("room operation: {0}")]
    Operation(#[from] io::Error),
    #[error("room combat: {0}")]
    Combat(#[from] crate::game_combat::CombatError),
    #[error("room frame: {0}")]
    Frame(#[from] crate::world::WorldError),
    #[error("room message: {0}")]
    Channel(#[from] crate::game_messages::ChannelError),
    #[error("room reliable stream: {0}")]
    Reliable(#[from] crate::game_egress::OrdinalExhausted),
    #[error("room link: {0:?}")]
    Link(crate::game_connection::TransmitError),
    #[error("socket transport: {0}")]
    Transport(#[source] io::Error),
    #[error("room operation panicked")]
    Panicked,
}

#[derive(Debug, Error)]
pub(super) enum WorkerError {
    #[error("game worker routing invariant: {0}")]
    Routing(&'static str),
    #[error("game worker transport: {0}")]
    Transport(#[source] io::Error),
}

impl From<WorkerError> for io::Error {
    fn from(error: WorkerError) -> Self {
        Self::other(error)
    }
}

#[derive(Clone, Debug)]
pub(super) struct FaultRecord {
    pub(super) id: MatchId,
    pub(super) stage: RoomStage,
    pub(super) connection: Option<u32>,
    pub(super) tick: u32,
    pub(super) reason: &'static str,
}

impl RoomError {
    pub(super) fn category(&self) -> &'static str {
        match self {
            Self::Invariant(_) => "invariant",
            Self::Operation(_) => "operation",
            Self::Combat(_) => "combat",
            Self::Frame(_) => "frame",
            Self::Channel(_) => "message",
            Self::Reliable(_) => "reliable",
            Self::Link(_) => "link",
            Self::Transport(_) => "transport",
            Self::Panicked => "panic",
        }
    }
}

#[cfg(test)]
#[derive(Clone, Copy)]
pub(super) enum InjectedFault {
    Error,
    Panic,
    BrokenBot,
}

/// The closure may borrow only this room, immutable catalogs and the current
/// connection's Sender. On unwind the caller removes the room and its links;
/// neither its peer nor ECS state is reused. Global routing is outside this
/// boundary. This fallback requires panic=unwind; abort/OOM remain fatal.
pub(super) fn guard_room<T>(
    room: &mut Room,
    stage: RoomStage,
    operation: impl FnOnce(&mut Room) -> Result<T, RoomError>,
) -> Result<T, RoomError> {
    catch_unwind(AssertUnwindSafe(|| {
        #[cfg(test)]
        if room.injection.is_some_and(|(at, _)| at == stage) {
            match room.injection.take().map(|(_, fault)| fault) {
                Some(InjectedFault::Error) => {
                    return Err(RoomError::Invariant("injected room failure"));
                }
                Some(InjectedFault::Panic) => panic!("injected room failure"),
                Some(InjectedFault::BrokenBot) => {
                    let scene = &mut room
                        .world
                        .as_mut()
                        .ok_or(RoomError::Invariant("bot fault requires a prepared room"))?
                        .scene;
                    if !scene.invalidate_bot_component() {
                        return Err(RoomError::Invariant("bot fault requires a bot"));
                    }
                }
                None => (),
            }
        }
        #[cfg(not(test))]
        let _ = stage;
        operation(room)
    }))
    .unwrap_or(Err(RoomError::Panicked))
}

impl Room {
    /// Temporarily detach just this peer to permit simultaneous room simulation
    /// and delivery borrows. Failure leaves it detached, and retires the room.
    pub(super) fn with_peer<T>(
        &mut self,
        connection: u32,
        operation: impl FnOnce(&mut Room, &mut Peer, usize) -> Result<T, RoomError>,
    ) -> Result<T, RoomError> {
        let member = self
            .members
            .get_mut(&connection)
            .ok_or(RoomError::Invariant("routed member is missing"))?;
        let slot = member.slot;
        let mut peer = member
            .peer
            .take()
            .ok_or(RoomError::Invariant("active peer is missing"))?;
        if self.players.get(slot).is_none_or(|player| player.gone) {
            return Err(RoomError::Invariant(
                "active member has no live roster slot",
            ));
        }
        let result = operation(self, &mut peer, slot)?;
        self.members
            .get_mut(&connection)
            .ok_or(RoomError::Invariant("member disappeared during room work"))?
            .peer = Some(peer);
        Ok(result)
    }

    pub(super) fn admit(&mut self, connection: u32) -> Result<(), RoomError> {
        let member = self
            .members
            .get_mut(&connection)
            .ok_or(RoomError::Invariant("admitted connection has no member"))?;
        if member.peer.is_some() {
            return Err(RoomError::Invariant("connection admitted twice"));
        }
        let player = self
            .players
            .get(member.slot)
            .filter(|player| !player.gone)
            .ok_or(RoomError::Invariant(
                "admitted member has no live roster slot",
            ))?;
        member.peer = Some(Peer::new(player.entity));
        Ok(())
    }

    pub(super) fn retire_member(&mut self, connection: u32) -> Result<(), RoomError> {
        if let Some(member) = self.members.shift_remove(&connection) {
            let player = self
                .players
                .get_mut(member.slot)
                .ok_or(RoomError::Invariant("retired member has no roster slot"))?;
            if player.gone {
                return Err(RoomError::Invariant("retired roster slot already gone"));
            }
            player.gone = true;
            if let Some(world) = &mut self.world {
                world.scene.retire_projectiles(connection);
            }
        }
        Ok(())
    }

    pub(super) fn receive(
        &mut self,
        connection: u32,
        received: &crate::game_connection::Received,
        schemas: &Schemas,
        tick: u32,
        now: Duration,
        draining: bool,
    ) -> Result<(), RoomError> {
        if received.admitted {
            self.admit(connection)?;
        }
        self.with_peer(connection, |room, peer, slot| {
            if let Some((ack, bits)) = received.ack {
                let report = peer.egress.acknowledge(ack, bits);
                if !draining {
                    peer.world.acknowledge(report, now);
                }
            }
            if received.closed {
                return Ok(());
            }
            peer.open |= received.opened;
            if draining {
                return Ok(());
            }
            if received.opened {
                peer.reliable(schemas, room.message(slot, true)?)?;
            }
            if let Some(payload) = &received.payload {
                let Peer {
                    ingress,
                    egress,
                    loading,
                    world,
                    ..
                } = peer;
                let mut response_error = None;
                let commands =
                    ingress.receive(schemas, payload, |message| {
                        match MessageKind::from(message.id) {
                            MessageKind::Ping => {
                                let received = wall_nanoseconds();
                                if let Ok(pong) =
                                    pong(&message.fields, received, wall_nanoseconds())
                                {
                                    let result = encode(schemas, pong.id, &pong.fields)
                                        .map_err(RoomError::from)
                                        .and_then(|bytes| {
                                            egress.queue_reliable(bytes).map_err(RoomError::from)
                                        });
                                    if let Err(error) = result {
                                        response_error.get_or_insert(error);
                                    }
                                }
                            }
                            MessageKind::MapLoaded => {
                                loading.mark_loaded(now);
                            }
                            MessageKind::GameMessage if room.world.is_some() => {
                                world.queue_game_message(tick, now, message);
                            }
                            _ => (),
                        }
                    });
                if let Some(error) = response_error {
                    return Err(error);
                }
                // Invalid external input remains a rejected packet, not a room fault.
                if let (Ok(commands), Some(_)) = (commands, &room.world) {
                    world.queue_commands(tick, &commands);
                }
            }
            Ok(())
        })
    }

    pub(super) fn input(
        &mut self,
        connection: u32,
        tick: u32,
        now: Duration,
        budget: usize,
    ) -> Result<(usize, bool), RoomError> {
        self.with_peer(connection, |room, peer, slot| {
            let world = room
                .world
                .as_ref()
                .ok_or(RoomError::Invariant("input without world"))?;
            let viewer = room
                .appearance(slot)
                .ok_or(RoomError::Invariant("input without viewer"))?;
            let context = ShotContext {
                team: viewer.team,
                quanta: room.players[slot].quanta(),
                tick,
                now,
            };
            // Movement/input failure previously closes this member. Keep that
            // scope and report the cause; body-script failures are handled locally.
            peer.world
                .set_encounter_active(world.scene.encounter_active());
            let (used, leave) = match peer.world.advance_input(
                &world.scene,
                &viewer,
                context.quanta,
                budget,
            ) {
                Ok(result) => result,
                Err(error) => {
                    eprintln!(
                        "Game input for connection {connection} at frame {tick} stopped: {error}"
                    );
                    (budget, true)
                }
            };
            if used > budget {
                return Err(RoomError::Invariant("input exceeded assigned budget"));
            }
            let shots = peer.world.take_hitscan();
            if (!leave && peer.world.combat_body_active()) || !shots.is_empty() {
                let world = room
                    .world
                    .as_mut()
                    .ok_or(RoomError::Invariant("combat without world"))?;
                let (bots, queries) = world.scene.bot_simulation();
                bots.activate_combat();
                for shot in shots {
                    if let Some(hit) = bots.hitscan(&queries, shot, context, |entity| {
                        peer.world.identity_ready(entity)
                    })? {
                        peer.bot_hit(hit);
                    }
                }
            }
            if leave {
                peer.clear_world();
                if let Some(world) = &mut room.world {
                    world.scene.retire_projectiles(connection);
                }
                peer.loading = MapReadiness::default();
            }
            Ok((used, leave))
        })
    }
}

#[derive(Default)]
pub(super) struct RoomUpdates {
    pub(super) replacements: Vec<BotReplacement>,
    pub(super) created: Vec<EntityUpdate>,
    pub(super) health: Vec<EntityUpdate>,
    pub(super) retired: Vec<u32>,
}

impl Room {
    pub(super) fn finish_simulation(
        &mut self,
        tick: u32,
        now: Duration,
    ) -> Result<RoomUpdates, RoomError> {
        let Some(world) = &mut self.world else {
            return Ok(RoomUpdates::default());
        };
        let junkenstein = world.scene.map().is_junkenstein();
        let active = world.scene.encounter_active();
        for member in self.members.values_mut() {
            if let Some(peer) = &mut member.peer {
                peer.world.set_encounter_active(active);
            }
        }
        let mut defenders = Vec::new();
        let mut bodies = Vec::new();
        let mut targets = Vec::new();
        for (&connection, member) in &self.members {
            let player = self
                .players
                .get(member.slot)
                .ok_or(RoomError::Invariant("encounter member has invalid slot"))?;
            let Some(peer) = &member.peer else {
                continue;
            };
            if player.gone {
                continue;
            }
            if peer.world.world_started() {
                if let Some(target) = peer.world.enemy_target(player.info.team) {
                    targets.push(target);
                }
            }
            if junkenstein {
                if let Some(body) = peer.world.junkenstein_defender(player.info.team) {
                    defenders.push((member.slot, connection, body));
                }
                if let Some(body) = peer.world.spawned_body() {
                    bodies.push((connection, body, player.info.team));
                }
            }
        }
        defenders.sort_unstable_by_key(|entry| entry.0);
        let mut sightings = Vec::new();
        if junkenstein {
            let entities: Arc<[u32]> = defenders.iter().map(|entry| entry.2).collect();
            world.scene.set_junkenstein_defenders(Arc::clone(&entities));
            for &(_, connection, _) in &defenders {
                let peer = self
                    .members
                    .get_mut(&connection)
                    .and_then(|member| member.peer.as_mut())
                    .ok_or(RoomError::Invariant("encounter defender disappeared"))?;
                peer.world.set_junkenstein_defenders(Arc::clone(&entities));
                sightings.extend(peer.world.take_defender_sightings());
            }
        }
        let (replacements, health, retired, damage, messages, bot_sightings) = {
            let (bots, queries) = world.scene.bot_simulation();
            bots.set_external_targets(targets);
            let replacements = bots.finish_tick(&queries, tick, now)?;
            (
                replacements,
                bots.take_scripted_health(),
                bots.take_retired_enemies(),
                bots.take_external_enemy_damage(),
                bots.take_defender_messages(),
                bots.take_defender_sightings(),
            )
        };
        if junkenstein {
            for hit in damage {
                let Some(&(connection, _, _)) = bodies.iter().find(|entry| entry.1 == hit.target)
                else {
                    continue;
                };
                let peer = self
                    .members
                    .get_mut(&connection)
                    .and_then(|member| member.peer.as_mut())
                    .ok_or(RoomError::Invariant("damage recipient disappeared"))?;
                let source = hit.source;
                if !world.scene.protects_cassidy(peer.world.encounter_hero())
                    && peer.world.apply_enemy_damage(hit, now, tick)
                {
                    peer.queue_combat_notice(CombatNotice::Death {
                        source,
                        killed_by: true,
                    });
                }
            }
            for message in messages {
                for &(connection, body, team) in &bodies {
                    if team != 0 || !message.accepts_body(body) {
                        continue;
                    }
                    self.members
                        .get_mut(&connection)
                        .and_then(|member| member.peer.as_mut())
                        .ok_or(RoomError::Invariant(
                            "defender message recipient disappeared",
                        ))?
                        .world
                        .deliver_defender_message(&world.scene, body, message.clone());
                }
            }
            for request in world.scene.take_encounter_voice_requests() {
                for &(connection, listener, team) in &bodies {
                    if team != 0 {
                        continue;
                    }
                    self.members
                        .get_mut(&connection)
                        .and_then(|member| member.peer.as_mut())
                        .ok_or(RoomError::Invariant("voice recipient disappeared"))?
                        .queue_combat_notice(CombatNotice::Voice {
                            anchor: request.anchor,
                            speaker: request.speaker,
                            listener,
                            stimulus: request.stimulus,
                            line: request.line,
                        });
                }
            }
            sightings.extend(bot_sightings);
            for message in sightings {
                if let Some(&(slot, connection, _)) =
                    defenders.iter().find(|entry| entry.2 == message.target)
                {
                    self.members
                        .get_mut(&connection)
                        .and_then(|member| member.peer.as_mut())
                        .ok_or(RoomError::Invariant("sighting recipient disappeared"))?
                        .world
                        .deliver_defender_sighting(self.players[slot].info.team, message);
                }
            }
        }
        Ok(RoomUpdates {
            replacements,
            created: world.scene.take_enemy_creates(),
            health,
            retired,
        })
    }
}
