//! A room-tick snapshot for player and NPC capsule movement.

use std::{collections::BTreeMap, sync::Arc};

use crate::{
    contact_cache::CapsulePose, game_bots::Bot, mover::VelocityChange,
    mover_settings::MoverCatalog, static_ray::Vec3,
};

use super::{CombatError, PlayerBodyState, RoomSimulation};

#[cfg(test)]
mod tests;

pub(super) struct MovementContacts {
    free_for_all: bool,
    movers: Arc<MoverCatalog>,
    frame: Option<(u32, Arc<MovementContactFrame>)>,
    captured_player_count: usize,
    refresh_for_peer: bool,
}

pub(crate) struct MovementContactFrame {
    actors: BTreeMap<u32, (Option<u8>, CapsulePose)>,
}

impl MovementContactFrame {
    /// Wire ordering is stable even when the ECS reuses a body slot.
    pub(crate) fn blockers(&self, source: u32, team: u8) -> Vec<CapsulePose> {
        self.blockers_where(source, team, |_| true)
    }

    pub(crate) fn blockers_where(
        &self,
        source: u32,
        team: u8,
        mut visible: impl FnMut(u32) -> bool,
    ) -> Vec<CapsulePose> {
        self.actors
            .iter()
            .filter_map(|(&entity, &(other_team, pose))| {
                (entity != source && other_team != Some(team) && visible(entity)).then_some(pose)
            })
            .collect()
    }

    pub(crate) fn ally_separation(&self, source: u32, team: u8, pose: CapsulePose) -> [f64; 2] {
        self.ally_separation_where(
            source,
            team,
            pose,
            crate::game_clock::TICK_PERIOD.as_secs_f64(),
            |_| true,
        )
    }

    pub(crate) fn ally_separation_where(
        &self,
        source: u32,
        team: u8,
        pose: CapsulePose,
        dt: f64,
        mut visible: impl FnMut(u32) -> bool,
    ) -> [f64; 2] {
        let mut push = [0.0f64; 2];
        for (&entity, &(other_team, other_pose)) in &self.actors {
            if entity == source || other_team != Some(team) || !visible(entity) {
                continue;
            }
            let reach = crate::contact_body::planar_contact_radius(pose, other_pose);
            let dx = f64::from(pose.position[0]) - f64::from(other_pose.position[0]);
            let dz = f64::from(pose.position[2]) - f64::from(other_pose.position[2]);
            let dist = dx.hypot(dz);
            if dist >= reach {
                continue;
            }
            let overlap = (reach - dist) / (f64::from(pose.radius) + f64::from(other_pose.radius));
            let (nx, nz) = if dist > 1.0e-5 {
                (dx / dist, dz / dist)
            } else if source < entity {
                (1.0, 0.0)
            } else {
                (-1.0, 0.0)
            };
            push[0] += nx * overlap;
            push[1] += nz * overlap;
        }
        let mag = push[0].hypot(push[1]);
        let max_step = 2.8 * dt;
        if mag > 1.0e-6 {
            let step = (mag * max_step).min(max_step);
            [push[0] / mag * step, push[1] / mag * step]
        } else {
            [0.0; 2]
        }
    }

    pub(crate) fn stage_bot(&self, bot: &mut Bot, tick: u32) {
        if !bot.dead && !bot.has_movement_blockers(tick) {
            let blockers = if self.actors.contains_key(&bot.entity) {
                if let Some((_, pose)) = self.actors.get(&bot.entity) {
                    bot.set_ally_separation(self.ally_separation(
                        bot.entity,
                        bot.spawn.team,
                        *pose,
                    ));
                }
                self.blockers(bot.entity, bot.spawn.team)
            } else {
                Vec::new()
            };
            bot.set_movement_blockers(tick, blockers);
        }
    }
}

impl RoomSimulation {
    pub(super) fn queue_combat_impulse(
        &mut self,
        wire: u32,
        tick: u32,
        delta: Vec3,
    ) -> Result<(), CombatError> {
        let change = VelocityChange::Impulse(delta);
        if !change.valid() {
            return Ok(());
        }
        if let Some(id) = self.player_handle_for_wire(wire) {
            if self.actor_is_cc_immune(wire, tick) {
                return Ok(());
            }
            if let Some(player) = self.player_body_mut(id).filter(|state| state.alive()) {
                // Input frames can run ahead of the room clock. Apply once at
                // the next actual pose so correction carries that pose's frame.
                let frame = player.mover.pose().frame.unwrap_or(player.frame);
                player
                    .mover
                    .queue_velocity_change(frame.saturating_add(1), change);
            }
        } else if let Some(state) = self.state_mut(wire)? {
            if !state.bot.dead && state.vitals.death().is_none() {
                let frame = tick.saturating_add(1);
                let applied = state
                    .enemy
                    .as_mut()
                    .filter(|enemy| enemy.fault.is_none())
                    .is_some_and(|enemy| enemy.queue_active_mover_change(frame, change));
                if !applied {
                    state.bot.queue_velocity_changes(vec![(frame, change)]);
                }
            }
        }
        Ok(())
    }

    pub(crate) fn enable_movement_contacts(
        &mut self,
        free_for_all: bool,
        movers: Arc<MoverCatalog>,
    ) {
        self.movement_contacts = Some(MovementContacts {
            free_for_all,
            movers,
            frame: None,
            captured_player_count: 0,
            refresh_for_peer: false,
        });
    }

    pub(crate) fn movement_contact_frame(&self) -> Option<Arc<MovementContactFrame>> {
        let (_, frame) = self.movement_contacts.as_ref()?.frame.as_ref()?;
        Some(Arc::clone(frame))
    }

    /// Input batches are resolved in scheduler order. Each peer sees the poses
    /// already committed by earlier peers, even within the same room tick.
    pub(crate) fn prepare_peer_movement_frame(&mut self, tick: u32) -> Result<(), CombatError> {
        let refresh = self.players_by_wire.len() > 1
            || self
                .movement_contacts
                .as_ref()
                .is_some_and(|contacts| contacts.refresh_for_peer);
        self.capture_movement_frame(tick, refresh)
    }

    /// Freeze current player poses once before advancing any NPCs.
    pub(crate) fn prepare_npc_movement_frame(&mut self, tick: u32) -> Result<(), CombatError> {
        self.capture_movement_frame(tick, !self.players_by_wire.is_empty())?;
        if let Some(contacts) = &mut self.movement_contacts {
            // NPC and combat stages can still use this frozen frame. The next
            // input stage must see the NPC movement those stages commit.
            contacts.refresh_for_peer = true;
        }
        Ok(())
    }

    /// Reuse the frozen contact frame within the same update stage.
    pub(crate) fn prepare_movement_frame(&mut self, tick: u32) -> Result<(), CombatError> {
        self.capture_movement_frame(tick, false)
    }

    fn capture_movement_frame(&mut self, tick: u32, refresh: bool) -> Result<(), CombatError> {
        let Some(contacts) = self.movement_contacts.as_ref() else {
            return Ok(());
        };
        let player_count = self.players_by_wire.len();
        if !refresh
            && contacts.captured_player_count == player_count
            && contacts
                .frame
                .as_ref()
                .is_some_and(|(frame, _)| *frame == tick)
        {
            return Ok(());
        }
        let team = |team| (!contacts.free_for_all).then_some(team);
        let mut actors = BTreeMap::new();
        for state in self.states() {
            let state = state?;
            let bot = &state.bot;
            if bot.dead || state.vitals.death().is_some() {
                continue;
            }
            let settings = contacts.movers.for_body(Some(bot.spawn.body));
            let enabled = state
                .enemy
                .as_ref()
                .filter(|enemy| enemy.fault.is_none())
                .map_or(settings.character_collision != 0, |enemy| {
                    enemy.scripts.character_collision(tick, settings)
                });
            if enabled {
                actors.insert(
                    bot.entity,
                    (team(bot.spawn.team), bot.contact_capsule(settings)),
                );
            }
        }
        for (&wire, &entity) in &self.players_by_wire {
            let state = self
                .world
                .get::<PlayerBodyState>(entity)
                .ok_or(CombatError::MissingPlayerBody { wire, entity })?;
            if state.id.entity != wire {
                return Err(CombatError::PlayerBodyIdentity {
                    wire,
                    actual: state.id.entity,
                });
            }
            if state.alive() && state.mover.character_collision_for_snapshot(state.frame) {
                actors.insert(wire, (team(state.team), state.mover.contact_capsule()));
            }
        }
        // Solid blocking world structures (e.g. Mei Ice Wall pillars): team `None`
        // so both allies and enemies are hard-blocked AND can stand on top of them.
        for (wire, pose) in self.solid_wall_capsules() {
            actors.insert(wire, (None, pose));
        }
        if let Some(contacts) = &mut self.movement_contacts {
            contacts.frame = Some((tick, Arc::new(MovementContactFrame { actors })));
            contacts.captured_player_count = player_count;
            contacts.refresh_for_peer = false;
        }
        Ok(())
    }
}
