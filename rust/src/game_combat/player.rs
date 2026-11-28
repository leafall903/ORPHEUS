//! Room-authoritative player bodies and generation-checked lifecycle in `RoomSimulation`.

use std::{collections::BTreeMap, io, sync::Arc, time::Duration};

use bevy_ecs::component::Component;

use crate::{
    game_combat::{
        AdmittedFlight, AppliedDamage, BioticField, BotHit, CombatError, EliminationStreak,
        HealingPulse, HealthDamageEvidence, HealthEventContext, PosedHitscan, RoomSimulation,
        SampledBody, script_eye_position,
    },
    game_combat_messages::CombatNotice,
    game_hitscan::{HurtPose, Impact, TargetHistory, TargetPose, ViewFrame},
    game_instance::TeamMemberCounts,
    game_projectiles::ProjectileToken,
    game_replication::{BodyId, OwnerBodyRuntime, OwnerRoomActivity},
    mover::Mover,
    pve_encounter::messages::{DefenderMessage, DefenderSighting},
    script_enemy::EnemyTarget,
    script_init::HealthTargetFacts,
    script_snapshot_wire::SceneProjection,
    vitals::{Damage, Death, Vitals},
    world::{Component as WorldComponent, EntityUpdate, Operation},
};

#[cfg(any(test, feature = "inspector"))]
use crate::game_debug::{Actor, ActorKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct PlayerBodyHandle {
    pub(crate) entity: bevy_ecs::entity::Entity,
    pub(crate) id: BodyId,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct PlayerDeath {
    pub(crate) owner: u32,
    pub(crate) body: BodyId,
    pub(crate) source: u32,
    pub(crate) killed_by: bool,
}

#[derive(Clone)]
pub(crate) enum PlayerBodyEvent {
    Created {
        owner: u32,
        id: BodyId,
        update: Box<EntityUpdate>,
    },
    Retired {
        owner: u32,
        id: BodyId,
    },
    ScriptHalted {
        owner: u32,
        id: BodyId,
        health: Option<WorldComponent>,
    },
}

/// Room-owned authoritative player body component stored inside `RoomSimulation`'s ECS world.
#[derive(Component)]
pub(crate) struct PlayerBodyState {
    pub(crate) id: BodyId,
    pub(crate) owner: u32,
    pub(crate) name: String,
    pub(crate) slot: u8,
    pub(crate) team: u8,
    pub(crate) hero: u64,
    pub(crate) body_template: u64,
    pub(super) elimination_streak: Option<EliminationStreak>,
    pub(crate) mover: Mover,
    pub(crate) history: TargetHistory,
    pub(crate) runtime: Option<Box<dyn OwnerBodyRuntime>>,
    runtime_halted: bool,
    halted_vitals: Option<Vitals>,
    pub(crate) frame: u32,
    pub(crate) fields: Vec<(ProjectileToken, BioticField)>,
    pub(crate) launches: Vec<AdmittedFlight>,
    pub(crate) hitscan: Vec<(PosedHitscan, crate::game_hitscan::HitTargetPolicy)>,
    pub(crate) observer_create: Option<EntityUpdate>,
    pub(crate) respawn_at: Option<Duration>,
    pub(crate) last_buttons: u32,
    pub(crate) pending_pressed_buttons: u32,
    pub(crate) pending_released_buttons: u32,
    pub(super) recent_hit_feedback: std::collections::VecDeque<(u32, u32, bool, bool)>,
}

impl PlayerBodyState {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        id: BodyId,
        owner: u32,
        slot: u8,
        team: u8,
        hero: u64,
        mover: Mover,
        runtime: Option<Box<dyn OwnerBodyRuntime>>,
        frame: u32,
        observer_create: Option<EntityUpdate>,
    ) -> Self {
        Self {
            id,
            owner,
            name: format!("player_{owner:08x}"),
            slot,
            team,
            hero,
            body_template: 0,
            elimination_streak: None,
            mover,
            history: TargetHistory::new(None),
            runtime,
            runtime_halted: false,
            halted_vitals: None,
            frame,
            fields: Vec::new(),
            launches: Vec::new(),
            hitscan: Vec::new(),
            observer_create,
            respawn_at: None,
            last_buttons: 0,
            pending_pressed_buttons: 0,
            pending_released_buttons: 0,
            recent_hit_feedback: std::collections::VecDeque::new(),
        }
    }

    pub(super) fn should_emit_hit_feedback(
        &mut self,
        target: u32,
        frame: u32,
        critical: bool,
        killed: bool,
    ) -> bool {
        if let Some(entry) = self
            .recent_hit_feedback
            .iter_mut()
            .rev()
            .find(|e| e.0 == target && e.1.abs_diff(frame) <= 2)
        {
            let upgrades = (critical && !entry.2) || (killed && !entry.3);
            entry.2 |= critical;
            entry.3 |= killed;
            return upgrades;
        }
        if self.recent_hit_feedback.len() >= 32 {
            self.recent_hit_feedback.pop_front();
        }
        self.recent_hit_feedback
            .push_back((target, frame, critical, killed));
        true
    }

    pub(crate) fn with_body_template(mut self, body_template: u64) -> Self {
        self.body_template = body_template;
        self
    }

    #[cfg(all(test, feature = "lua-mods"))]
    pub(crate) fn set_test_halted_vitals(&mut self, vitals: Vitals) {
        self.halted_vitals = Some(vitals);
    }

    pub(crate) fn alive(&self) -> bool {
        self.vitals().is_none_or(|vitals| vitals.death().is_none())
    }

    pub(crate) fn needs_runtime_restart(&self) -> bool {
        self.runtime_halted && self.alive()
    }

    pub(crate) fn vitals(&self) -> Option<&Vitals> {
        match &self.runtime {
            Some(runtime) => runtime.vitals(),
            None => self.halted_vitals.as_ref(),
        }
    }

    pub(super) fn heal(&mut self, amount: f64) -> f64 {
        match &mut self.runtime {
            Some(runtime) => runtime.heal(amount),
            None => self
                .halted_vitals
                .as_mut()
                .map_or(0.0, |vitals| vitals.heal(amount)),
        }
    }

    pub(crate) fn take_health_update(&mut self) -> Option<WorldComponent> {
        if let Some(runtime) = &mut self.runtime {
            return runtime.take_health_update();
        }
        self.halted_vitals
            .as_mut()
            .and_then(Vitals::take_cumulative_update)
    }

    pub(super) fn native_filter_flags(&self) -> Option<u64> {
        crate::match_scene::team_bit(self.team)
            .ok()
            .map(|team| 1 | team | if self.alive() { 0 } else { 0x10 })
    }

    pub(super) fn health_target_facts(&self) -> HealthTargetFacts {
        HealthTargetFacts {
            target: self.id.entity,
            room_status: None,
            filter_flags: self.native_filter_flags(),
            tags: self
                .runtime
                .as_ref()
                .map_or_else(Default::default, |runtime| runtime.target_tags()),
            script_state: self
                .runtime
                .as_ref()
                .and_then(|runtime| runtime.snapshot())
                .map_or(
                    crate::script_entity_state::EntityScriptState::Unavailable,
                    |snapshot| {
                        crate::script_entity_state::EntityScriptState::Running(Arc::new(
                            snapshot.variables.clone(),
                        ))
                    },
                ),
        }
    }

    pub(crate) fn target_pose(&self) -> TargetPose {
        let (height, radius) = self.mover.capsule();
        TargetPose {
            feet: self.mover.pose().position,
            height,
            radius,
            eye: self.mover.eye_height(),
        }
    }

    pub(super) fn script_eye_position(&self) -> Option<[f64; 3]> {
        script_eye_position(self.hero, &self.mover)
    }

    pub(crate) fn enemy_target(&self) -> Option<EnemyTarget> {
        let vitals = self.vitals()?;
        let runtime = self.runtime.as_deref();
        let script_state = runtime.and_then(OwnerBodyRuntime::snapshot).map_or(
            crate::script_entity_state::EntityScriptState::Unavailable,
            |snapshot| {
                crate::script_entity_state::EntityScriptState::Running(Arc::new(
                    snapshot.variables.clone(),
                ))
            },
        );
        Some(EnemyTarget {
            hero: Some(self.hero),
            filter_flags: match runtime {
                Some(runtime) => runtime.target_filter_flags(),
                None => self.native_filter_flags(),
            },
            tags: runtime.map_or_else(Default::default, OwnerBodyRuntime::target_tags),
            health: Some(vitals.total()),
            velocity: Some(self.mover.pose().velocity),
            script_state,
            entity: self.id.entity,
            team: self.team,
            pose: self.target_pose(),
            alive: vitals.death().is_none(),
        })
    }

    pub(crate) fn junkenstein_defender(&self) -> Option<u32> {
        let runtime = self.runtime.as_ref()?;
        let vitals = runtime.vitals()?;
        let snapshot = runtime.snapshot()?;
        (self.team == 0
            && vitals.death().is_none()
            && snapshot.initialization_complete()
            && snapshot
                .instances
                .values()
                .any(|instance| instance.graph == 0x0ca5))
        .then_some(self.id.entity)
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_actor(&self) -> Actor {
        let pose = self.mover.pose();
        let vitals = self.vitals();
        Actor {
            entity: self.id.entity,
            generation: Some(self.id.generation),
            kind: ActorKind::Player,
            team: self.team,
            position: pose.position,
            velocity: pose.velocity,
            alive: vitals.is_none_or(|vitals| vitals.death().is_none()),
            health: vitals.map(crate::vitals::Vitals::total),
            script_active: self.runtime.is_some(),
            last_contact_clip: None,
        }
    }

    fn advance_fields(&mut self, tick: u32) -> (Vec<HealingPulse>, Vec<ProjectileToken>, f64) {
        if self.runtime.is_none() {
            return (Vec::new(), Vec::new(), 0.0);
        }
        let mut pulses = Vec::new();
        let mut expired_tokens = Vec::new();
        self.fields.retain_mut(|(token, field)| {
            let (pulse, expired) = field.advance(tick);
            pulses.extend(pulse);
            if expired {
                expired_tokens.push(*token);
            }
            !expired
        });
        let pose = self.target_pose();
        let healed = self.heal(HealingPulse::total_at(&pulses, pose));
        (pulses, expired_tokens, healed)
    }

    fn receive_damage(&mut self, amount: f64, source: Death) -> Damage {
        let result = if let Some(runtime) = &mut self.runtime {
            let result = runtime.receive_damage(amount, source);
            self.mover.set_authored_facing(runtime.authored_facing());
            result
        } else {
            self.halted_vitals
                .as_mut()
                .map_or_else(Damage::default, |vitals| vitals.damage(amount, source))
        };
        if result.killed {
            self.mover.clear_pending_motion();
        }
        result
    }

    pub(crate) fn projection(&self) -> Option<SceneProjection<'_>> {
        let runtime = self.runtime.as_ref()?;
        Some(SceneProjection {
            snapshot: runtime.snapshot()?,
            encoder: runtime.encoder(),
        })
    }
}

impl RoomSimulation {
    pub(crate) fn advance_player_fields(
        &mut self,
        owner: BodyId,
        tick: u32,
    ) -> Option<(Vec<HealingPulse>, Vec<ProjectileToken>)> {
        let (pulses, expired_tokens, healed) = self.player_body_mut(owner)?.advance_fields(tick);
        self.record_health_applied(owner.entity, owner.entity, healed, false, None);
        if healed > 0.0 {
            self.deliver_applied_heal(owner.entity, owner.entity, healed);
        }
        Some((pulses, expired_tokens))
    }

    pub(crate) fn next_player_generation(&self, owner: u32, floor: u64) -> io::Result<u64> {
        self.player_generations
            .get(&owner)
            .copied()
            .unwrap_or(0)
            .max(floor)
            .checked_add(1)
            .ok_or_else(|| invalid("player body lifetime exhausted"))
    }

    pub(crate) fn set_protected_hero(&mut self, protected: Option<u64>) {
        self.protected_hero = protected;
    }

    pub(crate) fn spawn_player_body(
        &mut self,
        state: PlayerBodyState,
    ) -> io::Result<PlayerBodyHandle> {
        self.spawn_player_body_inner(state, false)
    }

    pub(crate) fn swap_player_body(
        &mut self,
        state: PlayerBodyState,
    ) -> io::Result<PlayerBodyHandle> {
        self.spawn_player_body_inner(state, true)
    }

    fn spawn_player_body_inner(
        &mut self,
        mut state: PlayerBodyState,
        transfer_pets: bool,
    ) -> io::Result<PlayerBodyHandle> {
        let id = state.id;
        let owner = state.owner;
        if id.generation == 0 {
            return Err(invalid("player body generation must be positive"));
        }
        let spawned_current = self.player_generations.get(&owner).copied().unwrap_or(0);
        if id.generation <= spawned_current {
            return Err(invalid(format!(
                "stale player body generation {} for owner {:08X} (current {})",
                id.generation, owner, spawned_current
            )));
        }
        let wire_current = self
            .latest_wire_generations
            .get(&id.entity)
            .copied()
            .unwrap_or(0);
        if id.generation <= wire_current {
            return Err(invalid(format!(
                "stale player body generation {} for wire {:08X} (current {})",
                id.generation, id.entity, wire_current
            )));
        }
        if let Some(&entity) = self.players_by_wire.get(&id.entity) {
            if let Some(existing) = self.world.get::<PlayerBodyState>(entity) {
                if existing.owner != owner {
                    return Err(invalid(format!(
                        "wire entity {:08X} is owned by {:08X}, not {:08X}",
                        id.entity, existing.owner, owner
                    )));
                }
            }
        }
        if let Some(previous) = self.players_by_owner.shift_remove(&owner) {
            let transfer_to = transfer_pets.then_some(id.entity);
            self.despawn_player_handle_inner(previous, transfer_to);
        }
        self.player_generations.insert(owner, id.generation);
        self.latest_wire_generations
            .insert(id.entity, id.generation);
        state.mover.set_authored_facing(None);
        if let Some(runtime) = state.runtime.as_mut() {
            if let Some(frame) = self.owner_startup_spatial_frame(owner) {
                runtime.set_spatial_frame(frame);
            }
            state.mover.set_authored_facing(runtime.authored_facing());
        }
        if let Some(name) = self.player_names.get(&owner) {
            state.name = name.clone();
        }
        // Keep the template for peers that start observing after this event.
        let observer_create = state.observer_create.clone();
        let spawn_frame = state.frame;
        let spawn_hero = state.hero;
        let spawn_team = state.team;
        let spawn_name = state.name.clone();
        let entity = self.world.spawn(state).id();
        let handle = PlayerBodyHandle { entity, id };
        self.players_by_wire.insert(id.entity, entity);
        self.players_by_owner.insert(owner, handle);
        self.player_targets_active = true;
        self.scoreboard.register_player(
            owner,
            spawn_team,
            spawn_hero,
            spawn_name,
            f64::from(spawn_frame) * crate::game_clock::TICK_PERIOD.as_secs_f64(),
        );
        if let Some(update) = observer_create {
            self.player_events.push(PlayerBodyEvent::Created {
                owner,
                id,
                update: Box::new(update),
            });
        }
        if !transfer_pets {
            self.queue_hero_spawn_voice(owner, id, spawn_hero, 0, spawn_frame);
        }
        #[cfg(feature = "lua-mods")]
        self.mod_on_player_spawn(id, spawn_frame);
        #[cfg(not(feature = "lua-mods"))]
        let _ = spawn_frame;
        self.debug_assert_player_invariants();
        Ok(handle)
    }

    fn debug_assert_player_invariants(&self) {
        if !cfg!(debug_assertions) {
            return;
        }
        debug_assert_eq!(self.players_by_owner.len(), self.players_by_wire.len());
        for (&owner, &handle) in &self.players_by_owner {
            let state = self
                .world
                .get::<PlayerBodyState>(handle.entity)
                .expect("players_by_owner references missing ECS entity");
            debug_assert_eq!(state.owner, owner);
            debug_assert_eq!(state.id, handle.id);
            debug_assert_eq!(
                self.players_by_wire.get(&handle.id.entity).copied(),
                Some(handle.entity)
            );
        }
    }

    fn despawn_player_handle(&mut self, handle: PlayerBodyHandle) -> bool {
        self.despawn_player_handle_inner(handle, None)
    }

    fn despawn_player_handle_inner(
        &mut self,
        handle: PlayerBodyHandle,
        transfer_pets_to: Option<u32>,
    ) -> bool {
        let Some(state) = self.world.get::<PlayerBodyState>(handle.entity) else {
            return false;
        };
        if state.id != handle.id {
            return false;
        }
        let owner = state.owner;
        let id = state.id;
        if self.players_by_wire.get(&id.entity).copied() == Some(handle.entity) {
            self.players_by_wire.shift_remove(&id.entity);
            self.scripted_health
                .retain(|update| update.entity != id.entity);
            self.observer_player_health
                .retain(|(_, update)| update.entity != id.entity);
        }
        if self
            .players_by_owner
            .get(&owner)
            .is_some_and(|current| *current == handle)
        {
            self.players_by_owner.shift_remove(&owner);
        }
        self.actor_statuses.remove(&id.entity);
        Arc::make_mut(&mut self.retired_entity_ids).insert(id.entity);
        if let Some(new_owner) = transfer_pets_to {
            self.transfer_owner_pets_and_deployables(id.entity, new_owner);
        } else {
            self.retire_owner_pets_and_deployables(id.entity);
        }
        self.world.despawn(handle.entity);
        self.player_events
            .push(PlayerBodyEvent::Retired { owner, id });
        self.debug_assert_player_invariants();
        true
    }

    #[cfg(test)]
    pub(crate) fn retire_player_body(&mut self, id: BodyId) -> bool {
        let Some(&entity) = self.players_by_wire.get(&id.entity) else {
            return false;
        };
        self.despawn_player_handle(PlayerBodyHandle { entity, id })
    }

    pub(crate) fn retire_player_owner(&mut self, owner: u32) -> Option<BodyId> {
        let handle = self.players_by_owner.shift_remove(&owner)?;
        #[cfg(feature = "lua-mods")]
        {
            let tick = self.spatial_frame.as_ref().map_or(0, |(t, _)| *t);
            self.mod_on_player_leave(owner, handle.id, tick);
        }
        self.despawn_player_handle(handle).then_some(handle.id)
    }

    pub(crate) fn clear_players(&mut self) {
        let handles: Vec<_> = self.players_by_owner.values().copied().collect();
        for handle in handles {
            self.despawn_player_handle(handle);
        }
        self.debug_assert_player_invariants();
    }

    /// Drop VM-owned effects while keeping the body's vitals and completed shots.
    pub(crate) fn clear_player_runtime(&mut self, id: BodyId) -> bool {
        let Some(state) = self.player_body_mut(id) else {
            return false;
        };
        let frame = state.mover.pose().frame.unwrap_or(state.frame);
        state.mover.track_modifiers(frame, &BTreeMap::new());
        state.mover.set_cinematic_aim(None);
        state.mover.set_authored_facing(None);
        state.mover.clear_pending_script_motion();
        if let Some(mut runtime) = state.runtime.take() {
            state.runtime_halted = true;
            state.halted_vitals = runtime.take_vitals();
            if let Some(vitals) = &mut state.halted_vitals {
                vitals.clear_temporary_shields();
            }
        }
        state.fields.clear();
        state.launches.clear();
        true
    }

    fn retire_faulted_player_script(&mut self, id: BodyId) {
        if self.clear_player_runtime(id) {
            if let Some(state) = self.player_body(id) {
                self.player_events.push(PlayerBodyEvent::ScriptHalted {
                    owner: state.owner,
                    id,
                    health: None,
                });
            }
        }
    }

    pub(crate) fn player_body(&self, id: BodyId) -> Option<&PlayerBodyState> {
        let &entity = self.players_by_wire.get(&id.entity)?;
        let state = self.world.get::<PlayerBodyState>(entity)?;
        (state.id == id).then_some(state)
    }

    pub(crate) fn player_body_mut(&mut self, id: BodyId) -> Option<&mut PlayerBodyState> {
        let &entity = self.players_by_wire.get(&id.entity)?;
        let state = self.world.get_mut::<PlayerBodyState>(entity)?.into_inner();
        (state.id == id).then_some(state)
    }

    pub(crate) fn player_body_by_owner(&self, owner: u32) -> Option<&PlayerBodyState> {
        let handle = *self.players_by_owner.get(&owner)?;
        let state = self.world.get::<PlayerBodyState>(handle.entity)?;
        (state.id == handle.id).then_some(state)
    }

    pub(crate) fn player_body_by_owner_mut(&mut self, owner: u32) -> Option<&mut PlayerBodyState> {
        let handle = *self.players_by_owner.get(&owner)?;
        let state = self
            .world
            .get_mut::<PlayerBodyState>(handle.entity)?
            .into_inner();
        (state.id == handle.id).then_some(state)
    }

    pub(crate) fn player_handle_for_wire(&self, wire: u32) -> Option<BodyId> {
        let &entity = self.players_by_wire.get(&wire)?;
        let state = self.world.get::<PlayerBodyState>(entity)?;
        Some(state.id)
    }

    pub(crate) fn player_body_generation_current(&self, body: BodyId) -> bool {
        if let Some(&entity) = self.players_by_wire.get(&body.entity) {
            return self
                .world
                .get::<PlayerBodyState>(entity)
                .is_some_and(|state| state.id == body);
        }
        !self.latest_wire_generations.contains_key(&body.entity)
    }

    pub(crate) fn take_player_events(&mut self) -> Vec<PlayerBodyEvent> {
        let mut events = std::mem::take(&mut self.player_events);
        events.retain_mut(|event| {
            let PlayerBodyEvent::ScriptHalted { id, health, .. } = event else {
                return true;
            };
            let Some(state) = self
                .player_body_mut(*id)
                .filter(|state| state.runtime.is_none())
            else {
                return false;
            };
            // Combat can consume and publish a newer cumulative health update
            // after the halt. Collect only changes still pending at the drain.
            *health = state.take_health_update();
            true
        });
        events
    }

    pub(crate) fn take_player_deaths(&mut self) -> Vec<PlayerDeath> {
        std::mem::take(&mut self.player_deaths)
    }

    pub(crate) fn record_observer_player_health(&mut self, owner: u32, update: EntityUpdate) {
        self.observer_player_health.push((owner, update));
    }

    pub(crate) fn take_observer_player_health(&mut self) -> Vec<(u32, EntityUpdate)> {
        std::mem::take(&mut self.observer_player_health)
    }

    pub(crate) fn schedule_player_respawn(&mut self, body: BodyId, due: Duration) {
        if let Some(state) = self.player_body_mut(body) {
            if !state.alive() && state.respawn_at.is_none() {
                state.respawn_at = Some(due);
            }
        }
    }

    pub(crate) fn pending_player_respawn(&self, body: BodyId) -> Option<Duration> {
        self.player_body(body)
            .filter(|state| !state.alive())
            .and_then(|state| state.respawn_at)
    }

    pub(crate) fn is_player_respawn_due(&self, body: BodyId, now: Duration) -> bool {
        self.pending_player_respawn(body)
            .is_some_and(|due| now >= due)
    }

    pub(crate) fn clear_player_respawn(&mut self, body: BodyId) {
        if let Some(state) = self.player_body_mut(body) {
            state.respawn_at = None;
        }
    }

    pub(crate) fn set_player_activity(&mut self, id: BodyId, activity: OwnerRoomActivity) {
        let Some(runtime) = self
            .player_body_mut(id)
            .and_then(|state| state.runtime.as_mut())
        else {
            return;
        };
        runtime.set_room_activity(activity);
        if runtime
            .snapshot()
            .is_some_and(crate::script_init::Snapshot::initialization_complete)
        {
            if let Err(error) = runtime.set_encounter_active(activity.encounter_active()) {
                eprintln!(
                    "Owner encounter admission failed for {:08x}: {error}",
                    id.entity
                );
            }
        }
    }

    pub(crate) fn deliver_coalesced_hits(&mut self, owner: u32, hits: &[BotHit]) {
        let mut merged: Vec<AppliedDamage> = Vec::new();
        for hit in hits {
            if let Some(existing) = merged.iter_mut().find(|d| {
                d.source == hit.damage.source
                    && d.target_facts.target == hit.damage.target_facts.target
            }) {
                existing.amount += hit.damage.amount;
                for loss in &hit.damage.named_pool_losses {
                    if let Some(prev) = existing
                        .named_pool_losses
                        .iter_mut()
                        .find(|l| l.identifier == loss.identifier)
                    {
                        prev.amount += loss.amount;
                    } else {
                        existing.named_pool_losses.push(loss.clone());
                    }
                }
            } else {
                merged.push(hit.damage.clone());
            }
        }
        for damage in merged {
            self.deliver_applied_damage(owner, damage);
        }
    }

    pub(crate) fn deliver_applied_damage(&mut self, owner: u32, damage: AppliedDamage) {
        if let Some(state) = self.player_body_by_owner_mut(owner) {
            if state.id.entity == damage.source {
                if let Some(runtime) = &mut state.runtime {
                    if let Err(error) = runtime.receive_applied_damage(damage) {
                        eprintln!(
                            "Owner damage event failed for {:08x}: {error}",
                            state.id.entity
                        );
                    }
                }
            }
        }
    }

    pub(crate) fn deliver_applied_damage_by_wire(&mut self, damage: AppliedDamage) {
        if let Some(id) = self.player_handle_for_wire(damage.source) {
            if let Some(owner) = self.player_body(id).map(|body| body.owner) {
                self.deliver_applied_damage(owner, damage);
            }
        }
    }

    pub(crate) fn deliver_applied_heal(&mut self, source: u32, target: u32, amount: f64) {
        if !amount.is_finite() || amount <= 0.0 {
            return;
        }
        let mut heal_cue = None;
        if let Some(id) = self.player_handle_for_wire(source) {
            if let Some(state) = self.player_body_mut(id) {
                if let Some(runtime) = &mut state.runtime {
                    if let Err(error) = runtime.receive_applied_heal(source, target, amount) {
                        eprintln!(
                            "Owner heal event failed for {:08x}: {error}",
                            state.id.entity
                        );
                    }
                }
                if target != source {
                    heal_cue = Some((state.owner, state.id, state.frame));
                }
            }
        }
        if let Some((owner, listener, shot_frame)) = heal_cue {
            let now_secs = f64::from(shot_frame) * crate::game_clock::TICK_PERIOD.as_secs_f64();
            self.scoreboard.record_heal(owner, amount, now_secs);
            self.replay.record_event(crate::game_replay::ReplayEvent {
                tick: shot_frame,
                kind: crate::game_replay::ReplayEventKind::HealBurst {
                    healer: source,
                    amount,
                },
            });
            if let Some(target_sample) = self.sample_health_body(target) {
                let target_full = self
                    .by_wire
                    .get(&target)
                    .and_then(|&entity| self.world.get::<super::BotState>(entity))
                    .map(|b| b.vitals.total() + 0.5 >= b.vitals.maximum())
                    .or_else(|| {
                        self.players_by_wire
                            .get(&target)
                            .and_then(|&entity| self.world.get::<PlayerBodyState>(entity))
                            .and_then(|p| p.vitals())
                            .map(|v| v.total() + 0.5 >= v.maximum())
                    })
                    .unwrap_or(false);
                let target_id = target_sample.identity();
                self.player_cues.push(super::PlayerRoomCue {
                    owner,
                    listener,
                    source: target_id,
                    notice: CombatNotice::HealConfirm {
                        target,
                        amount,
                        shot_frame,
                    },
                });
                self.player_cues.push(super::PlayerRoomCue {
                    owner,
                    listener,
                    source: target_id,
                    notice: CombatNotice::HealEvent {
                        healer: source,
                        target,
                        amount,
                        full: target_full,
                        loadout: 0,
                    },
                });
            }
            if amount >= 15.0 && self.by_wire.contains_key(&target) {
                self.schedule_bot_voice_reply(
                    target,
                    crate::actor_voice::STIMULUS_THANKS,
                    24,
                    shot_frame,
                );
            }
        }
    }

    pub(crate) fn take_player_projectile_launches(
        &mut self,
        id: BodyId,
    ) -> Option<Vec<AdmittedFlight>> {
        let state = self.player_body_mut(id)?;
        Some(std::mem::take(&mut state.launches))
    }

    pub(crate) fn take_player_hitscan(
        &mut self,
        id: BodyId,
    ) -> Vec<(PosedHitscan, crate::game_hitscan::HitTargetPolicy)> {
        self.player_body_mut(id)
            .map_or_else(Vec::new, |state| std::mem::take(&mut state.hitscan))
    }

    pub(crate) fn sync_player_activity(&mut self, activity: OwnerRoomActivity) {
        let ids: Vec<_> = self
            .players_by_owner
            .values()
            .map(|handle| handle.id)
            .collect();
        for id in ids {
            self.set_player_activity(id, activity);
        }
    }

    pub(crate) fn collect_player_enemy_targets(&self) -> Vec<EnemyTarget> {
        let mut targets: Vec<_> = self
            .players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .filter_map(PlayerBodyState::enemy_target)
            .collect();
        #[cfg(test)]
        if let Some(external) = &self.external_targets {
            targets.extend(external.iter().cloned());
        }
        targets.sort_unstable_by_key(|target| target.entity);
        targets
    }

    pub(crate) fn collect_junkenstein_defenders(&self) -> Vec<u32> {
        let mut defenders: Vec<_> = self
            .players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .filter_map(|state| Some((state.slot, state.junkenstein_defender()?)))
            .collect();
        defenders.sort_unstable_by_key(|entry| entry.0);
        defenders.into_iter().map(|entry| entry.1).collect()
    }

    pub(crate) fn team_member_counts(&self) -> Option<TeamMemberCounts> {
        self.team_member_counts
    }

    pub(crate) fn set_team_member_counts(&mut self, counts: TeamMemberCounts) {
        self.team_member_counts = Some(counts);
        for &entity in self.players_by_wire.values() {
            if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
                if let Some(runtime) = &mut state.runtime {
                    runtime.set_team_member_counts(counts);
                }
            }
        }
    }

    pub(crate) fn set_player_defenders(&mut self, defenders: Arc<[u32]>) {
        for &entity in self.players_by_wire.values() {
            if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
                if let Some(runtime) = &mut state.runtime {
                    runtime.set_defenders(Arc::clone(&defenders));
                }
            }
        }
    }

    pub(crate) fn take_player_defender_sightings(&mut self) -> Vec<DefenderSighting> {
        let mut sightings = Vec::new();
        for &entity in self.players_by_wire.values() {
            if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
                if let Some(runtime) = &mut state.runtime {
                    sightings.extend(runtime.take_sightings());
                }
            }
        }
        sightings
    }

    pub(crate) fn deliver_player_defender_sighting(&mut self, message: DefenderSighting) -> bool {
        let Some(&entity) = self.players_by_wire.get(&message.target) else {
            return false;
        };
        let Some(state) = self.world.get_mut::<PlayerBodyState>(entity) else {
            return false;
        };
        let state = state.into_inner();
        if state.junkenstein_defender() != Some(message.target) {
            return false;
        }
        if let Some(runtime) = &mut state.runtime {
            if let Err(error) = runtime.receive_sighting(message) {
                let id = state.id;
                eprintln!(
                    "Junkenstein defender message failed for {:08x}: {error}",
                    id.entity
                );
                if runtime.snapshot().is_none() {
                    self.retire_faulted_player_script(id);
                }
            }
        }
        true
    }

    pub(crate) fn deliver_player_defender_message(
        &mut self,
        id: BodyId,
        deadeye: Option<crate::script_player::DeadeyeScene>,
        message: DefenderMessage,
    ) -> bool {
        let Some(state) = self.player_body_mut(id) else {
            return false;
        };
        let Some(runtime) = &mut state.runtime else {
            return false;
        };
        if let Some(scene) = deadeye {
            runtime.set_deadeye_targets(scene);
        }
        match runtime.receive_defender_message(message) {
            Ok(_) => {
                state.mover.set_cinematic_aim(runtime.cinematic_aim());
                state.mover.set_authored_facing(runtime.authored_facing());
                true
            }
            Err(error) => {
                eprintln!(
                    "Junkenstein defender message failed for {:08x}: {error}",
                    id.entity
                );
                if runtime.snapshot().is_none() {
                    self.retire_faulted_player_script(id);
                } else {
                    state.mover.set_authored_facing(None);
                }
                false
            }
        }
    }

    pub(crate) fn deliver_player_defender_messages(
        &mut self,
        deadeye: Option<&crate::script_player::DeadeyeScene>,
        messages: &[DefenderMessage],
    ) {
        if messages.is_empty() {
            return;
        }
        let bodies: Vec<_> = self
            .players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .filter(|state| state.team == 0)
            .map(|state| state.id)
            .collect();
        for message in messages {
            for &id in &bodies {
                if !message.accepts_body(id.entity) {
                    continue;
                }
                self.deliver_player_defender_message(id, deadeye.cloned(), message.clone());
            }
        }
    }

    pub(crate) fn damage_player_body_by_wire(
        &mut self,
        id: BodyId,
        amount: f64,
        source: Death,
        impact: Option<(&Impact, &HealthTargetFacts)>,
    ) -> Option<(u32, Damage, Option<EntityUpdate>)> {
        let protected_hero = self.protected_hero;
        let state = self.player_body_mut(id)?;
        if protected_hero == Some(state.hero) {
            return None;
        }
        let owner = state.owner;
        let entity = state.id.entity;
        let had_armor_or_shield = state.vitals().is_some_and(|v| {
            v.amount(Some(crate::vitals::PoolKind::Armour), false) > 0.0
                || v.amount(Some(crate::vitals::PoolKind::Shields), false) > 0.0
        });
        let outcome = state.receive_damage(amount, source);
        let health = state.take_health_update().map(|component| EntityUpdate {
            entity,
            operation: Some(Operation::Update(vec![component])),
            ..EntityUpdate::default()
        });
        let mut death_script_fault = None;
        if outcome.killed {
            let source_facts = self.death_source_facts(source.killer);
            if let Some(runtime) = self
                .player_body_mut(id)
                .and_then(|state| state.runtime.as_mut())
            {
                let (critical, loadout) = impact.map_or((false, None), |(impact, _)| {
                    (impact.critical, impact.loadout)
                });
                death_script_fault = runtime
                    .publish_death(source.killer, critical, loadout, source_facts)
                    .err();
            }
        }
        self.record_health_applied(
            source.killer,
            entity,
            -outcome.dealt,
            outcome.killed,
            Some(HealthDamageEvidence {
                impact,
                named_pool_losses: &outcome.named_pool_losses,
                frame: Some(source.frame),
            }),
        );
        if !outcome.killed {
            let critical = impact.is_some_and(|(impact, _)| impact.critical);
            let armor_or_shield =
                had_armor_or_shield || outcome.named_pool_losses.iter().any(|l| l.amount > 0.0);
            self.record_received_damage(
                owner,
                id,
                source.killer,
                outcome.dealt as f32,
                impact.and_then(|(impact, _)| impact.geometry),
                critical,
                armor_or_shield,
                false,
            );
        }
        if let Some(error) = death_script_fault {
            eprintln!("player {entity:08X} death script stopped: {error}");
            self.retire_faulted_player_script(id);
        }
        Some((owner, outcome, health))
    }

    fn death_source_facts(&self, wire: u32) -> Option<super::DeathSourceFacts> {
        if let Some(player) = self
            .player_handle_for_wire(wire)
            .and_then(|id| self.player_body(id))
        {
            return Some(super::DeathSourceFacts {
                target: player.health_target_facts(),
                velocity: player.mover.pose().velocity,
            });
        }
        let state = self
            .world
            .get::<super::BotState>(*self.by_wire.get(&wire)?)?;
        Some(super::DeathSourceFacts {
            target: state.health_target_facts(),
            velocity: state.bot.velocity,
        })
    }

    pub(crate) fn observer_player_creates(
        &self,
        exclude_owner: u32,
    ) -> Vec<(BodyId, EntityUpdate)> {
        self.players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .filter(|state| state.owner != exclude_owner)
            .filter_map(|state| {
                let mut update = state.observer_create.clone()?;
                let Operation::Create(create) = update.operation.as_mut()? else {
                    return None;
                };
                let pose = state.mover.pose();
                create.position = pose.position.map(|axis| axis as f32);
                create.rotation = crate::game_body::yaw_rotation(f64::from(pose.yaw));
                if let Some(health) = state.vitals().map(Vitals::component) {
                    if let Some(component) = create
                        .components
                        .iter_mut()
                        .find(|c| c.index == health.index)
                    {
                        *component = health;
                    }
                }
                Some((state.id, update))
            })
            .collect()
    }

    pub(crate) fn player_projections(
        &self,
        exclude_owner: u32,
    ) -> impl Iterator<Item = SceneProjection<'_>> {
        self.players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .filter(move |state| state.owner != exclude_owner)
            .filter_map(PlayerBodyState::projection)
    }

    pub(crate) fn observer_player_movements(
        &self,
        exclude_owner: u32,
    ) -> Vec<(u32, crate::world::Movement)> {
        self.players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .filter(|state| state.owner != exclude_owner)
            .map(|state| (state.id.entity, state.mover.pose()))
            .collect()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn player_debug_actors(&self) -> Vec<Actor> {
        self.players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .map(PlayerBodyState::debug_actor)
            .collect()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(super) fn push_player_debug_physics(
        &self,
        contact_normals: &mut Vec<crate::game_debug::DebugContactNormal>,
        normals_truncated: &mut bool,
        ground_probes: &mut Vec<crate::game_debug::DebugGroundProbe>,
        probes_truncated: &mut bool,
    ) {
        for &entity in self.players_by_wire.values() {
            let Some(state) = self.world.get::<PlayerBodyState>(entity) else {
                continue;
            };
            if let Some(probe) = state.mover.debug_ground_probe(state.id.entity) {
                if ground_probes.len() == crate::game_debug::MAX_DEBUG_GROUND_PROBES {
                    *probes_truncated = true;
                } else {
                    ground_probes.push(probe);
                }
            }
            for contact in state.mover.debug_contact_normals(state.id.entity) {
                if contact_normals.len() == crate::game_debug::MAX_DEBUG_CONTACT_NORMALS {
                    *normals_truncated = true;
                    break;
                }
                contact_normals.push(contact);
            }
        }
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(super) fn push_player_debug_scripts(
        &self,
        out: &mut Vec<crate::game_debug::EntityScriptSnapshot>,
    ) {
        for &entity in self.players_by_wire.values() {
            let Some(state) = self.world.get::<PlayerBodyState>(entity) else {
                continue;
            };
            if let Some(snapshot) = state
                .runtime
                .as_ref()
                .and_then(|runtime| runtime.snapshot())
            {
                out.push(crate::game_debug::summarize_script_snapshot(
                    state.id.entity,
                    "player",
                    snapshot,
                ));
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn push_player_hitscan_candidates_with_policy(
        &self,
        source: u32,
        team: u8,
        policy: crate::game_hitscan::HitTargetPolicy,
        queries: &crate::scene_assets::BotQueries<'_>,
        view: ViewFrame,
        visible: &impl Fn(u32) -> bool,
        candidates: &mut Vec<(u32, HurtPose)>,
    ) {
        #[cfg(feature = "lua-mods")]
        let free_for_all = queries.free_for_all() || self.mod_friendly_fire();
        #[cfg(not(feature = "lua-mods"))]
        let free_for_all = queries.free_for_all();
        for &entity in self.players_by_wire.values() {
            let Some(state) = self.world.get::<PlayerBodyState>(entity) else {
                continue;
            };
            if !state.alive() || state.id.entity == source || !visible(state.id.entity) {
                continue;
            }
            // In friendly-fire mods, keep heal-only ally beams/shots (e.g. Ana) treating teammates
            // as allies so healing still lands while damaging shots can hit everyone.
            let heal_only_ally = policy.affects_allies && !policy.affects_enemies;
            let same_team = if heal_only_ally {
                state.team == team
            } else {
                !free_for_all && state.team == team
            };
            let allowed = if same_team {
                policy.affects_allies
            } else {
                policy.affects_enemies
            };
            if !allowed {
                continue;
            }
            if let Some(pose) = state.history.at(view, state.target_pose()) {
                let m_pose = state.mover.pose();
                let speed = m_pose.velocity[0].hypot(m_pose.velocity[2]);
                let postures = self.ability_rules.postures;
                let is_ball_roll = (state.body_template == postures.wrecking_ball_body
                    || pose.radius >= postures.wrecking_ball_roll_min_radius)
                    && pose.height <= postures.wrecking_ball_roll_max_height
                    && speed > postures.wrecking_ball_roll_min_speed;
                let posture = if is_ball_roll {
                    crate::game_hitscan::HurtPosture::Ball
                } else if state.body_template == postures.bastion_body
                    && state.mover.crouched()
                    && speed < postures.bastion_sentry_max_speed
                {
                    crate::game_hitscan::HurtPosture::Sentry
                } else {
                    crate::game_hitscan::HurtPosture::Standard
                };
                let stance = crate::game_hitscan::SkeletalStance {
                    yaw: m_pose.yaw,
                    speed,
                    crouched: state.mover.crouched(),
                    head_protected: is_ball_roll,
                    posture,
                };
                candidates.push((
                    state.id.entity,
                    queries
                        .hurt_pose(state.body_template, pose)
                        .with_stance(stance),
                ));
            }
        }
    }

    pub(super) fn extend_player_weapon_targets(
        &self,
        queries: &crate::scene_assets::BotQueries<'_>,
        targets: &mut Vec<super::WeaponTarget>,
    ) {
        targets.extend(
            self.players_by_wire
                .values()
                .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
                .filter(|state| state.alive())
                .map(|state| super::WeaponTarget {
                    wire: state.id.entity,
                    team: state.team,
                    pose: queries.hurt_pose(state.body_template, state.target_pose()),
                }),
        );
    }

    pub(super) fn apply_player_impact(
        &mut self,
        source: u32,
        shot_frame: u32,
        context: crate::game_combat::ShotContext,
        impact: &Impact,
    ) -> Result<Option<BotHit>, CombatError> {
        let Some(id) = self.player_handle_for_wire(impact.entity) else {
            return Ok(None);
        };
        let state = self.player_body(id).ok_or(CombatError::MissingPlayerBody {
            wire: id.entity,
            entity: *self
                .players_by_wire
                .get(&id.entity)
                .expect("checked wire handle"),
        })?;
        if self.protected_hero == Some(state.hero) || !state.alive() {
            return Ok(None);
        }
        let victim_team = state.team;
        let fallback_health = state.vitals().map(Vitals::cumulative_component);
        let target_facts = state.health_target_facts();
        let (attacker_out_dmg, _, _, _, _) = self.actor_combat_modifiers(source);
        let (_, victim_in_dmg, _, _, _) = self.actor_combat_modifiers(impact.entity);
        let invulnerable = self
            .actor_statuses
            .get(&impact.entity)
            .and_then(|s| s.invulnerable_until)
            .is_some_and(|until| context.tick < until);
        let raw_damage = if invulnerable {
            0.0
        } else {
            impact.damage * attacker_out_dmg * victim_in_dmg
        };
        #[cfg(feature = "lua-mods")]
        let damage_amount = self.mod_evaluate_damage(
            source,
            impact.entity,
            raw_damage,
            impact.critical,
            false,
            0,
            context.tick,
            context.now,
        );
        #[cfg(not(feature = "lua-mods"))]
        let damage_amount = raw_damage;
        if damage_amount <= 0.0 {
            return Ok(None);
        }
        self.wake_sleeping_actor(impact.entity, context.tick)?;
        let Some((owner, outcome, health)) = self.damage_player_body_by_wire(
            id,
            damage_amount,
            Death {
                time: context.now.as_secs_f64(),
                frame: context.tick,
                killer: source,
            },
            Some((impact, &target_facts)),
        ) else {
            return Ok(None);
        };
        if outcome.dealt <= 0.0 {
            self.scripted_health.extend(health);
            return Ok(None);
        }
        let killed = outcome.killed;
        let Some(health) = health.or_else(|| {
            killed.then(|| EntityUpdate {
                entity: id.entity,
                operation: fallback_health.map(|component| Operation::Update(vec![component])),
                ..EntityUpdate::default()
            })
        }) else {
            return Ok(None);
        };
        self.scripted_health.push(health);
        self.record_actor_hurt_voice(
            id.entity,
            false,
            source,
            impact.critical,
            killed,
            context.tick,
        );
        let self_hit = source == id.entity;
        if killed {
            self.actor_statuses.remove(&id.entity);
            self.player_deaths.push(PlayerDeath {
                owner,
                body: id,
                source,
                killed_by: !self_hit,
            });
            self.record_enemy_elimination(source, id.entity, victim_team, context.tick);
            #[cfg(feature = "lua-mods")]
            self.mod_on_kill(
                source,
                id.entity,
                impact.critical,
                context.tick,
                context.now,
            );
        }
        if self_hit {
            return Ok(None);
        }
        Ok(Some(BotHit {
            damage: AppliedDamage {
                source,
                amount: outcome.dealt,
                named_pool_losses: outcome.named_pool_losses,
                tags: impact.tags,
                target_facts,
            },
            notices: [
                Some(CombatNotice::Hit {
                    target: id.entity,
                    dealt: outcome.dealt,
                    critical: impact.critical,
                    killed,
                    shot_frame,
                }),
                killed.then_some(CombatNotice::Elimination {
                    killer: source,
                    victim: id.entity,
                    loadout: impact.loadout.unwrap_or(0),
                    fire: 100,
                    critical: impact.critical,
                }),
            ],
        }))
    }

    pub(super) fn heal_player_bodies(
        &mut self,
        team: u8,
        skip_wire: Option<u32>,
        source: Option<SampledBody>,
        pulses: &[HealingPulse],
    ) {
        #[cfg(feature = "lua-mods")]
        let healing_scale = self.mod_healing_scale();
        let source_out_heal = source.map_or(1.0, |owner| self.actor_combat_modifiers(owner.wire).2);
        let current_tick = self.spatial_frame.as_ref().map_or(0, |(tick, _)| *tick);
        let mut credited_heals = Vec::new();
        for (&wire, &entity) in &self.players_by_wire {
            if Some(wire) == skip_wire {
                continue;
            }
            let status_scale = match self.actor_statuses.get(&wire) {
                Some(status)
                    if status
                        .anti_heal_until
                        .is_some_and(|until| current_tick < until) =>
                {
                    0.0
                }
                Some(status)
                    if status
                        .heal_boost_until
                        .is_some_and(|until| current_tick < until) =>
                {
                    1.5
                }
                _ => 1.0,
            };
            if status_scale <= 0.0 {
                continue;
            }
            let (_, _, _, victim_in_heal, _) = self.actor_combat_modifiers(wire);
            let Some(state) = self.world.get_mut::<PlayerBodyState>(entity) else {
                continue;
            };
            let state = state.into_inner();
            if state.id.entity != wire || state.team != team || !state.alive() {
                continue;
            }
            let amount = HealingPulse::total_at(pulses, state.target_pose())
                * source_out_heal
                * victim_in_heal
                * status_scale;
            #[cfg(feature = "lua-mods")]
            let amount = amount * healing_scale;
            if amount <= 0.0 {
                continue;
            }
            let healed = state.heal(amount);
            if healed <= 0.0 {
                continue;
            }
            if let Some(component) = state.take_health_update() {
                self.scripted_health.push(EntityUpdate {
                    entity: wire,
                    operation: Some(Operation::Update(vec![component])),
                    ..EntityUpdate::default()
                });
            }
            if let Some(owner_body) = source {
                credited_heals.push((owner_body.wire, wire, healed));
            }
            let target = SampledBody { wire, entity };
            let room_status = self.sample_health_target_room_status(target);
            self.health_events.stage(
                source,
                target,
                healed,
                false,
                HealthEventContext {
                    world: &self.world,
                    damage: None,
                },
                room_status,
            );
        }
        for (healer, target, healed) in credited_heals {
            self.deliver_applied_heal(healer, target, healed);
        }
    }

    pub(crate) fn record_player_poses(&mut self, tick: u32) {
        let entities: Vec<_> = self.players_by_wire.values().copied().collect();
        for entity in entities {
            let Some(state) = self.world.get_mut::<PlayerBodyState>(entity) else {
                continue;
            };
            let state = state.into_inner();
            if state.alive() {
                let pose = state.target_pose();
                state.history.record(tick, pose);
            }
        }
    }
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}
