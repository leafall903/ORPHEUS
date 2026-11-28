//! Room-authoritative health effects for player and NPC bodies.

use super::{
    BotState, CombatError, HealthDamageEvidence, PlayerBodyState, RoomSimulation,
    player::PlayerDeath,
};
use crate::{
    game_hitscan::Impact,
    script_expr::valid_entity,
    vitals::Death,
    world::{EntityUpdate, Operation},
};
use std::time::Duration;

#[derive(Clone, Copy)]
pub(super) enum HealthEffect {
    Damage(f64),
    Heal(f64),
}

impl HealthEffect {
    pub(super) fn from_delta(delta: f64) -> Option<Self> {
        if !delta.is_finite() || delta == 0.0 {
            None
        } else if delta < 0.0 {
            Some(Self::Damage(-delta))
        } else {
            Some(Self::Heal(delta))
        }
    }
}

#[derive(Default)]
pub(super) struct SettledHealth {
    pub(super) delta: f64,
    pub(super) killed: bool,
}

impl RoomSimulation {
    /// Return whether this room owns the target, including a dead or immune body.
    pub(super) fn settle_health(
        &mut self,
        wire: u32,
        effect: HealthEffect,
        source: Death,
        now: Duration,
    ) -> Result<bool, CombatError> {
        self.settle_health_with_impact(wire, effect, source, now, None)
    }

    pub(super) fn settle_health_with_impact(
        &mut self,
        wire: u32,
        effect: HealthEffect,
        source: Death,
        now: Duration,
        impact: Option<&Impact>,
    ) -> Result<bool, CombatError> {
        Ok(self
            .apply_health(wire, effect, source, now, impact)?
            .is_some())
    }

    /// A known target can settle with no health change.
    pub(super) fn apply_health(
        &mut self,
        wire: u32,
        effect: HealthEffect,
        source: Death,
        now: Duration,
        impact: Option<&Impact>,
    ) -> Result<Option<SettledHealth>, CombatError> {
        let (attacker_out_dmg, _, attacker_out_heal, _, _) =
            self.actor_combat_modifiers(source.killer);
        let (_, victim_in_dmg, _, victim_in_heal, _) = self.actor_combat_modifiers(wire);
        let effect = match effect {
            HealthEffect::Heal(amount) => {
                #[cfg(feature = "lua-mods")]
                let mod_heal = self.mod_healing_scale_for(source.killer, wire);
                #[cfg(not(feature = "lua-mods"))]
                let mod_heal = 1.0;
                let mut scaled = amount * attacker_out_heal * victim_in_heal * mod_heal;
                if let Some(status) = self.actor_statuses.get(&wire) {
                    if status
                        .anti_heal_until
                        .is_some_and(|until| source.frame < until)
                    {
                        scaled = 0.0;
                    } else if status
                        .heal_boost_until
                        .is_some_and(|until| source.frame < until)
                    {
                        scaled *= 1.5;
                    }
                }
                HealthEffect::Heal(scaled)
            }
            HealthEffect::Damage(amount) => {
                let invulnerable = self
                    .actor_statuses
                    .get(&wire)
                    .and_then(|s| s.invulnerable_until)
                    .is_some_and(|until| source.frame < until);
                let base_scaled = if invulnerable {
                    0.0
                } else {
                    amount * attacker_out_dmg * victim_in_dmg
                };
                #[cfg(feature = "lua-mods")]
                let scaled = if base_scaled > 0.0 && source.killer != 0 {
                    self.mod_evaluate_damage(
                        source.killer,
                        wire,
                        base_scaled,
                        impact.is_some_and(|i| i.critical),
                        impact.is_none(),
                        -1,
                        source.frame,
                        now,
                    )
                } else {
                    base_scaled
                };
                #[cfg(not(feature = "lua-mods"))]
                let scaled = base_scaled;
                if scaled > 0.0 {
                    self.wake_sleeping_actor(wire, source.frame)?;
                }
                HealthEffect::Damage(scaled)
            }
        };
        let critical = impact.is_some_and(|impact| impact.critical);
        let effect = match effect {
            HealthEffect::Damage(amount) if amount > 0.0 => {
                let clamp_floor = if let Some(&entity) = self.by_wire.get(&wire) {
                    self.world.get::<BotState>(entity).and_then(|s| {
                        (!s.bot.dead
                            && self.immortality_floor_active(s.bot.position, s.bot.spawn.team))
                        .then(|| (s.vitals.total(), s.vitals.maximum() * 0.20))
                    })
                } else if let Some(id) = self.player_handle_for_wire(wire) {
                    self.player_body(id).and_then(|p| {
                        let vitals = p.vitals()?;
                        (p.alive()
                            && self.immortality_floor_active(p.mover.pose().position, p.team))
                        .then(|| (vitals.total(), vitals.maximum() * 0.20))
                    })
                } else {
                    None
                };
                if let Some((current, floor)) = clamp_floor {
                    HealthEffect::Damage((current - floor).max(0.0).min(amount))
                } else {
                    HealthEffect::Damage(amount)
                }
            }
            other => other,
        };
        if let Some(state) = self.state_mut(wire)? {
            let victim_team = state.bot.spawn.team;
            let target_facts = impact.map(|_| state.health_target_facts());
            let (delta, killed, named_pool_losses) = match effect {
                HealthEffect::Damage(amount) => Self::damage_bot_with_hit(
                    state, amount, source, now, impact,
                )
                .map_or((0.0, false, None), |outcome| {
                    (
                        -outcome.dealt,
                        outcome.killed,
                        Some(outcome.named_pool_losses),
                    )
                }),
                HealthEffect::Heal(amount) if !state.bot.dead => {
                    (state.vitals.heal(amount), false, None)
                }
                HealthEffect::Heal(_) => (0.0, false, None),
            };
            if delta != 0.0 {
                if delta > 0.0 {
                    if let Some(script) = &mut state.stock_script {
                        script.on_heal(state.vitals.total());
                    }
                }
                let update = super::bot_health_update(state, killed);
                self.scripted_health.push(update);
                self.record_health_applied(
                    source.killer,
                    wire,
                    delta,
                    killed,
                    named_pool_losses
                        .as_deref()
                        .map(|named_pool_losses| HealthDamageEvidence {
                            impact: impact.zip(target_facts.as_ref()),
                            named_pool_losses,
                            frame: Some(source.frame),
                        }),
                );
                if delta < 0.0 || killed {
                    self.record_actor_hurt_voice(
                        wire,
                        true,
                        source.killer,
                        critical,
                        killed,
                        source.frame,
                    );
                }
                if killed {
                    self.record_enemy_elimination(source.killer, wire, victim_team, source.frame);
                    #[cfg(feature = "lua-mods")]
                    self.mod_on_kill(source.killer, wire, critical, source.frame, now);
                }
                #[cfg(feature = "lua-mods")]
                if delta > 0.0 {
                    self.mod_on_heal(source.killer, wire, delta, source.frame, now);
                }
            }
            return Ok(Some(SettledHealth { delta, killed }));
        }
        let Some((id, victim_team)) = self
            .player_handle_for_wire(wire)
            .and_then(|id| self.player_body(id).map(|state| (id, state.team)))
        else {
            // Also resolve live room pets and deployables (barriers, turrets, Ice Wall,
            // Immortality Field) so beams, melee, and AoE `ModifyHealth` can damage them.
            if let HealthEffect::Damage(amount) = effect {
                let is_pet = self.room_pets.contains_key(&wire);
                let is_dep = !is_pet && self.room_deployables.contains_key(&wire);
                if is_pet || is_dep {
                    let dealt = self.damage_barrier_or_deployable(
                        wire,
                        is_pet,
                        amount,
                        source.killer,
                        source.frame,
                        now,
                    );
                    let killed = if is_pet {
                        self.room_pets
                            .get(&wire)
                            .and_then(|p| p.vitals.as_ref())
                            .is_some_and(|v| v.death().is_some())
                    } else {
                        self.room_deployables
                            .get(&wire)
                            .and_then(|d| d.vitals.as_ref())
                            .is_some_and(|v| v.death().is_some())
                    };
                    return Ok(Some(SettledHealth {
                        delta: -dealt,
                        killed,
                    }));
                }
            }
            return Ok(None);
        };
        let mut settled = SettledHealth::default();
        match effect {
            HealthEffect::Damage(amount) => {
                let target_facts = impact
                    .and_then(|_| self.player_body(id))
                    .map(|state| state.health_target_facts());
                let evidence = impact.zip(target_facts.as_ref());
                if let Some((owner, outcome, health)) =
                    self.damage_player_body_by_wire(id, amount, source, evidence)
                {
                    self.scripted_health.extend(health);
                    settled = SettledHealth {
                        delta: -outcome.dealt,
                        killed: outcome.killed,
                    };
                    if outcome.dealt > 0.0 || outcome.killed {
                        self.record_actor_hurt_voice(
                            wire,
                            false,
                            source.killer,
                            critical,
                            outcome.killed,
                            source.frame,
                        );
                    }
                    if outcome.killed {
                        // Echo Duplicate: reaching 0 HP while duplicated reverts cleanly to
                        // the original hero instead of counting as a death.
                        if let Some(dup) = self.active_duplicates.remove(&owner) {
                            if let Some(state) = self.player_body_mut(id) {
                                let max_hp = state.vitals().map_or(200.0, |v| v.maximum());
                                let _ = state.heal(max_hp);
                            }
                            self.pending_duplicate_reverts
                                .insert(owner, dup.original_body);
                        } else {
                            let death_pos = self
                                .player_body(id)
                                .map(|s| s.mover.pose().position)
                                .unwrap_or([0.0; 3]);
                            self.soul_anchors.push(super::SoulAnchor {
                                body: id,
                                team: victim_team,
                                position: death_pos,
                                expires_tick: source.frame.saturating_add(62 * 10),
                            });
                            self.actor_statuses.remove(&wire);
                            self.scoreboard.record_death(owner, wire);
                            self.player_deaths.push(PlayerDeath {
                                owner,
                                body: id,
                                source: source.killer,
                                killed_by: valid_entity(source.killer) && source.killer != wire,
                            });
                            self.record_enemy_elimination(
                                source.killer,
                                wire,
                                victim_team,
                                source.frame,
                            );
                            #[cfg(feature = "lua-mods")]
                            self.mod_on_kill(source.killer, wire, critical, source.frame, now);
                        }
                    }
                }
            }
            HealthEffect::Heal(amount) => {
                if let Some(state) = self
                    .player_body_mut(id)
                    .filter(|state| state.alive() && state.vitals().is_some())
                {
                    let healed = state.heal(amount);
                    if let Some(health) = state.take_health_update() {
                        self.scripted_health.push(EntityUpdate {
                            entity: wire,
                            operation: Some(Operation::Update(vec![health])),
                            ..Default::default()
                        });
                    }
                    self.record_health_applied(source.killer, wire, healed, false, None);
                    #[cfg(feature = "lua-mods")]
                    if healed > 0.0 {
                        self.mod_on_heal(source.killer, wire, healed, source.frame, now);
                    }
                    settled.delta = healed;
                }
            }
        }
        Ok(Some(settled))
    }

    pub(super) fn actor_is_cc_immune(&self, wire: u32, tick: u32) -> bool {
        if self.actor_combat_modifiers(wire).4 {
            return true;
        }
        self.actor_statuses
            .get(&wire)
            .and_then(|s| s.cc_immune_until)
            .is_some_and(|until| tick < until)
    }

    pub(super) fn wake_sleeping_actor(&mut self, wire: u32, tick: u32) -> Result<(), CombatError> {
        let was_sleeping = self.actor_statuses.get_mut(&wire).and_then(|status| {
            if status.sleep_until.is_some_and(|until| tick < until) {
                status.sleep_until = None;
                Some(status.stunned_until.unwrap_or(tick).saturating_sub(tick))
            } else {
                None
            }
        });
        if let Some(remaining_stun) = was_sleeping {
            if let Some(state) = self.state_mut(wire)? {
                state.bot.clear_stun();
                if remaining_stun > 0 {
                    state.bot.stun_for(tick, remaining_stun);
                }
            } else if let Some(id) = self.player_handle_for_wire(wire) {
                if let Some(player) = self.player_body_mut(id) {
                    if remaining_stun == 0 {
                        player.mover.clear_stun();
                    }
                }
            }
            self.settle_control_scripts(tick);
        }
        Ok(())
    }

    pub(super) fn apply_actor_stun(&mut self, wire: u32, tick: u32, ticks: u32) {
        self.hold_actor(wire, tick, ticks, super::status_scripts::Control::Stun);
    }

    fn hold_actor(
        &mut self,
        wire: u32,
        tick: u32,
        ticks: u32,
        kind: super::status_scripts::Control,
    ) -> bool {
        if ticks == 0 || self.actor_is_cc_immune(wire, tick) {
            return false;
        }
        if let Ok(Some(state)) = self.state_mut(wire) {
            if state.bot.dead {
                return false;
            }
            state.bot.stun_for(tick, ticks);
        } else if let Some(id) = self.player_handle_for_wire(wire) {
            if let Some(player) = self.player_body_mut(id).filter(|p| p.alive()) {
                let frame = player.mover.pose().frame.unwrap_or(player.frame);
                player.mover.stun(frame.saturating_add(1));
            } else {
                return false;
            }
        } else {
            return false;
        }
        let entry = self.actor_statuses.entry(wire).or_default();
        let slot = match kind {
            super::status_scripts::Control::Sleep => &mut entry.sleep_until,
            super::status_scripts::Control::Stun => &mut entry.stunned_until,
        };
        let until = tick.saturating_add(ticks);
        *slot = Some(slot.map_or(until, |previous| previous.max(until)));
        true
    }

    /// Apply a retail `m_277A4ECA` projectile status effect GUID to a live actor,
    /// resolving authored stun/sleep/freeze durations from the loaded `GraphStore`
    /// wrapper graphs when available instead of hardcoding hero-specific constants.
    pub(super) fn apply_projectile_status_effect(
        &mut self,
        effect_guid: u64,
        source_wire: u32,
        target_wire: u32,
        is_ally: bool,
        tick: u32,
    ) {
        use super::status_scripts::Control;
        use crate::hero_ability_rules::ProjectileStatusEffectBehavior as EffectBehavior;

        let Some(behavior) = self.ability_rules.projectile_status_effect(effect_guid) else {
            return;
        };
        let graph = self.ability_rules.status_script(effect_guid);
        if !self.admits_projectile_control(target_wire, effect_guid) {
            return;
        }
        let graphs = self.script_graphs.as_ref().map(|(g, _)| g.as_ref());
        let authored_secs = |wrapper_graph: u32, fallback: f64| -> f64 {
            graphs
                .and_then(|store| extract_wrapper_stun_seconds(store, wrapper_graph))
                .unwrap_or(fallback)
        };
        let secs_to_ticks = |secs: f64| -> u32 {
            crate::game_combat::Stun::from_seconds(secs).map_or(1, |s| s.ticks)
        };

        match behavior {
            EffectBehavior::Stun {
                wrapper_graph,
                fallback_secs,
            } if !is_ally => {
                let ticks = secs_to_ticks(authored_secs(wrapper_graph, fallback_secs));
                if self.hold_actor(target_wire, tick, ticks, Control::Stun) {
                    self.attach_control_script(
                        target_wire,
                        graph,
                        source_wire,
                        Control::Stun,
                        tick,
                        ticks,
                    );
                    self.emit_status_vfx_feedback(source_wire, target_wire);
                }
            }
            EffectBehavior::Sleep {
                wrapper_graph,
                fallback_secs,
            } if !is_ally => {
                let sleep_ticks = secs_to_ticks(authored_secs(wrapper_graph, fallback_secs));
                if self.hold_actor(target_wire, tick, sleep_ticks, Control::Sleep) {
                    self.attach_control_script(
                        target_wire,
                        graph,
                        source_wire,
                        Control::Sleep,
                        tick,
                        sleep_ticks,
                    );
                    self.emit_status_vfx_feedback(source_wire, target_wire);
                }
            }
            EffectBehavior::ChillFreeze {
                wrapper_graph,
                stacks,
                fallback_secs,
            } if !is_ally => {
                if !self.actor_is_cc_immune(target_wire, tick) {
                    let entry = self.actor_statuses.entry(target_wire).or_default();
                    if tick.saturating_sub(entry.chill_last_tick) > 62 {
                        entry.chill_stacks = 0;
                    }
                    entry.chill_last_tick = tick;
                    entry.chill_stacks = entry.chill_stacks.saturating_add(1);
                    if entry.chill_stacks >= stacks {
                        entry.chill_stacks = 0;
                        let freeze_ticks =
                            secs_to_ticks(authored_secs(wrapper_graph, fallback_secs));
                        self.apply_actor_stun(target_wire, tick, freeze_ticks);
                        self.emit_status_vfx_feedback(source_wire, target_wire);
                    }
                }
            }
            EffectBehavior::HealModifier { duration_secs } => {
                let ticks = secs_to_ticks(duration_secs);
                let until = tick.saturating_add(ticks);
                let entry = self.actor_statuses.entry(target_wire).or_default();
                if is_ally {
                    entry.heal_boost_until = Some(until);
                } else {
                    entry.anti_heal_until = Some(until);
                    self.emit_status_vfx_feedback(source_wire, target_wire);
                }
            }
            EffectBehavior::HoT {
                duration_secs,
                total_heal,
            } if is_ally => {
                let ticks = secs_to_ticks(duration_secs).clamp(1, u32::from(u16::MAX)) as u16;
                let entry = self.actor_statuses.entry(target_wire).or_default();
                entry.hot_ticks = ticks;
                entry.hot_per_tick = total_heal / f64::from(ticks);
                entry.hot_source = source_wire;
            }
            _ => {}
        }
    }

    pub(super) fn step_status_effects(&mut self, tick: u32) {
        if self.actor_statuses.is_empty() {
            self.settle_control_scripts(tick);
            return;
        }
        let mut heals = Vec::new();
        let mut dots = Vec::new();
        let mut unstunned_players = Vec::new();
        let mut rooted_wires = Vec::new();
        let mut hook_pulls: Vec<(u32, u32, [f64; 3])> = Vec::new();
        let mut dead_wires = Vec::new();
        for (&wire, status) in &self.actor_statuses {
            let alive = if let Some(&entity) = self.by_wire.get(&wire) {
                self.world
                    .get::<BotState>(entity)
                    .is_some_and(|s| !s.bot.dead)
            } else if let Some(&entity) = self.players_by_wire.get(&wire) {
                self.world
                    .get::<PlayerBodyState>(entity)
                    .is_some_and(|p| p.alive())
            } else {
                false
            };
            if !alive {
                dead_wires.push(wire);
                if status.stunned_until.is_some() {
                    unstunned_players.push(wire);
                }
            }
        }
        for wire in dead_wires {
            self.actor_statuses.remove(&wire);
        }
        self.settle_control_scripts(tick);
        self.actor_statuses.retain(|&wire, status| {
            if status.anti_heal_until.is_some_and(|until| tick >= until) {
                status.anti_heal_until = None;
            }
            if status.heal_boost_until.is_some_and(|until| tick >= until) {
                status.heal_boost_until = None;
            }
            if status.sleep_until.is_some_and(|until| tick >= until) {
                status.sleep_until = None;
                unstunned_players.push(wire);
            }
            if status.stunned_until.is_some_and(|until| tick >= until) {
                status.stunned_until = None;
                unstunned_players.push(wire);
            }
            if status.hacked_until.is_some_and(|until| tick >= until) {
                status.hacked_until = None;
            }
            if status.invulnerable_until.is_some_and(|until| tick >= until) {
                status.invulnerable_until = None;
            }
            if status.cc_immune_until.is_some_and(|until| tick >= until) {
                status.cc_immune_until = None;
            }
            if status.chill_stacks > 0 && tick.saturating_sub(status.chill_last_tick) > 94 {
                status.chill_stacks = 0;
            }
            if status.rooted_until.is_some_and(|until| tick >= until) {
                status.rooted_until = None;
            } else if status.rooted_until.is_some() {
                rooted_wires.push(wire);
            }
            if let Some((puller, dest, until)) = status.hook_pull {
                if tick >= until {
                    status.hook_pull = None;
                } else {
                    hook_pulls.push((wire, puller, dest));
                }
            }
            if status.beam_charge > 0.0 && tick.saturating_sub(status.beam_last_hit_tick) > 124 {
                status.beam_charge = (status.beam_charge - 0.015).max(0.0);
            }
            if status.hot_ticks > 0 {
                status.hot_ticks -= 1;
                heals.push((status.hot_source, wire, status.hot_per_tick));
            }
            if status.dot_ticks > 0 {
                status.dot_ticks -= 1;
                dots.push((status.dot_source, wire, status.dot_per_tick));
            }
            status.anti_heal_until.is_some()
                || status.heal_boost_until.is_some()
                || status.sleep_until.is_some()
                || status.stunned_until.is_some()
                || status.hacked_until.is_some()
                || status.invulnerable_until.is_some()
                || status.cc_immune_until.is_some()
                || status.rooted_until.is_some()
                || status.hook_pull.is_some()
                || status.beam_charge > 0.0
                || status.chill_stacks > 0
                || status.hot_ticks > 0
                || status.dot_ticks > 0
        });
        // Clamp horizontal velocity to zero on rooted targets (e.g. Junkrat Steel Trap).
        for wire in rooted_wires {
            if let Some(id) = self.player_handle_for_wire(wire) {
                if let Some(player) = self.player_body_mut(id).filter(|p| p.alive()) {
                    let frame = player.mover.pose().frame.unwrap_or(player.frame);
                    player.mover.queue_velocity_change(
                        frame.saturating_add(1),
                        crate::mover::VelocityChange::SetHorizontalVelocity([0.0, 0.0]),
                    );
                }
            }
        }
        // Step 2-stage Chain Hook pulls with LOS break validation.
        let frame_arc = self
            .spatial_frame
            .as_ref()
            .map(|(_, f)| std::sync::Arc::clone(f));
        for (victim, puller, dest) in hook_pulls {
            let Some(vpos) = self
                .sample_health_body(victim)
                .and_then(|s| s.position(&self.world))
            else {
                continue;
            };
            let dx = dest[0] - vpos[0];
            let dy = dest[1] - vpos[1];
            let dz = dest[2] - vpos[2];
            let dist = dx.hypot(dy).hypot(dz);
            if dist <= 0.6 {
                if let Some(st) = self.actor_statuses.get_mut(&victim) {
                    st.hook_pull = None;
                }
                continue;
            }
            let dir = [dx / dist, dy / dist, dz / dist];
            let los_broken = frame_arc.as_deref().is_some_and(|frame| {
                let site = crate::script_init::Site {
                    entity: puller,
                    instance: 0,
                    graph: 0,
                    node: None,
                };
                frame
                    .ray_cast(
                        site,
                        tick,
                        [vpos[0], vpos[1] + 1.0, vpos[2]],
                        dir,
                        dist.min(20.0) - 0.3,
                    )
                    .flatten()
                    .is_some()
            });
            if los_broken {
                if let Some(st) = self.actor_statuses.get_mut(&victim) {
                    st.hook_pull = None;
                }
                continue;
            }
            let pull_speed = 32.0_f64.min(dist * 18.0);
            let _ = self.queue_combat_impulse(
                victim,
                tick,
                [
                    dir[0] * pull_speed * 0.15,
                    dir[1] * 4.0,
                    dir[2] * pull_speed * 0.15,
                ],
            );
        }
        for wire in unstunned_players {
            if self
                .actor_statuses
                .get(&wire)
                .is_some_and(|s| s.sleep_until.is_some() || s.stunned_until.is_some())
            {
                continue;
            }
            if let Some(id) = self.player_handle_for_wire(wire) {
                if let Some(player) = self.player_body_mut(id) {
                    player.mover.clear_stun();
                }
            }
        }
        let now =
            Duration::from_secs_f64(f64::from(tick) * crate::game_clock::TICK_PERIOD.as_secs_f64());
        for (source, target, amount) in heals {
            let death = Death {
                time: now.as_secs_f64(),
                frame: tick,
                killer: source,
            };
            if let Ok(Some(outcome)) =
                self.apply_health(target, HealthEffect::Heal(amount), death, now, None)
            {
                if outcome.delta > 0.0 {
                    self.deliver_applied_heal(source, target, outcome.delta);
                }
            }
        }
        for (source, target, amount) in dots {
            let death = Death {
                time: now.as_secs_f64(),
                frame: tick,
                killer: source,
            };
            let _ = self.apply_health(target, HealthEffect::Damage(amount), death, now, None);
        }
    }
}

/// Inspect a retail effect wrapper graph (`0x0228`, `0x05E7`, `0x0BDE`, etc.) for a
/// `STUStatescriptActionPlayScript` targeting the shared stun control graph (`0x224E`)
/// and evaluate its authored duration override (`0x0D800000000000D7`).
fn extract_wrapper_stun_seconds(
    graphs: &crate::script_graph::GraphStore,
    wrapper_graph: u32,
) -> Option<f64> {
    const SHARED_STUN_GUID: u64 = 0x0580_0000_0000_224e;
    const DURATION_VAR_GUID: u64 = 0x0d80_0000_0000_00d7;
    let graph = graphs.graph(wrapper_graph)?;
    for node in &graph.nodes {
        if node.class != "STUStatescriptActionPlayScript" {
            continue;
        }
        let Some(spec) = node.field("m_69F4B52C.m_6525E98B") else {
            continue;
        };
        if crate::script_graph::asset_guid(spec.get("m_graph")?) != Some(SHARED_STUN_GUID) {
            continue;
        }
        let rows = spec.get("m_1EB5A024").and_then(|v| v.as_array())?;
        for row in rows {
            if crate::script_graph::asset_guid(row.get("m_0D09D2D9")?) == Some(DURATION_VAR_GUID) {
                let val = crate::script_expr::evaluate(
                    row.get("m_value")?,
                    &mut crate::script_expr::EmptyContext,
                )
                .ok()?;
                let secs = match val {
                    crate::script_wire::Value::Float(v) => v,
                    crate::script_wire::Value::Integer(v) => v as f64,
                    _ => continue,
                };
                if secs.is_finite() && secs > 0.0 {
                    return Some(secs);
                }
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::game_combat::{
        BOT_RESPAWN_TIME,
        tests::{fixture_bots, fixture_ground},
    };
    use crate::game_combat::{RoomScriptRequests, script_requests::MAX_BODY_SCRIPT_REQUESTS};
    use std::sync::Arc;

    #[test]
    fn contact_damage_and_impulse_follow_room_team_wall_and_body_admission() {
        use crate::{mover_settings::MoverCatalog, script_graph::RoomSpatialNeeds};
        let bots = fixture_bots();
        let (source, target) = (bots[0].entity, bots[1].entity);
        for free_for_all in [false, true] {
            let mut room = RoomSimulation::new(fixture_bots()).unwrap();
            room.enable_spatial_queries(
                free_for_all,
                Arc::new(fixture_ground()),
                RoomSpatialNeeds::Queries,
            );
            room.enable_spatial_actor_rays(Arc::new(MoverCatalog::default()));
            let tick = 7;
            room.prepare_spatial_frame(tick).unwrap();
            let frame = &room.spatial_frame.as_ref().unwrap().1;
            let targets = frame
                .hostile_contact_targets(source, [2.0, 0.5, 0.0], [1.0, 0.5, 0.0], 0.25, 0x18)
                .unwrap();
            assert_eq!(
                targets
                    .iter()
                    .map(|target| target.entity)
                    .collect::<Vec<_>>(),
                if free_for_all { vec![target] } else { vec![] }
            );
            if !free_for_all {
                continue;
            }
            assert!(
                frame
                    .hostile_contact_targets(source, [2.0, 0.5, 0.0], [-3.0, 0.5, 0.25], 0.25, 0x18)
                    .unwrap()
                    .is_empty()
            );
            let mut requests = RoomScriptRequests::default();
            assert!(!requests.queue_contact_damage(
                frame,
                tick,
                25.0,
                &[(target, None), (target, None)]
            ));
            assert!(requests.queue_contact_damage(
                frame,
                tick,
                25.0,
                &[(target, Some([5.0, 2.0, 0.0]))]
            ));
            let source = room.sample_health_body(source).unwrap();
            let [effect] = requests.take().try_into().ok().unwrap();
            room.settle_script_request(effect, source, tick, Duration::ZERO)
                .unwrap();
            assert_eq!(room.body_healths().unwrap()[&target], 75.0);
            assert!(
                room.state_mut(target)
                    .unwrap()
                    .unwrap()
                    .bot
                    .has_combat_motion()
            );
        }
    }

    #[test]
    #[ignore = "requires tagged 104319 Practice Range graphs and authored static ray profiles"]
    fn prepared_stock_quick_melee_reapplies_damage_and_knockback_each_swing() {
        use crate::{
            commands::Command,
            game_clock::FIRST_TICK,
            game_combat::{PlayerBodyState, script_eye_position},
            game_combat_messages::CombatNotice,
            game_content::GameMap,
            game_heroes::SOLDIER,
            game_instance::RETAIL_QUANTA,
            game_replication::{BodyId, BodyStep},
            match_scene::team_bit,
            scene_assets::SceneAssets,
            script_player::OwnerBodySpawn,
            script_snapshot_wire::View,
        };

        let path = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(path).unwrap();
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        let hero = assets.heroes().get(SOLDIER).unwrap();
        let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
        let owner = 0xa000_0100;
        let id = BodyId {
            entity: owner + 1,
            generation: 1,
        };
        let spawn = OwnerBodySpawn {
            slot: 0,
            controller: owner,
            entity: id.entity,
            body_filter: team_bit(0).unwrap(),
            frame: FIRST_TICK,
            pose: mover.pose(),
            eye_position: script_eye_position(hero.guid, &mover),
            skin: 0,
            quanta: RETAIL_QUANTA,
        };
        let mut runtime = scene.owner_body_runtime(hero, spawn).unwrap().unwrap();
        let source_pose = scene.player_spawn_mover(hero.body, 0).unwrap();
        let room = scene.simulation_mut();
        room.spawn_player_body(PlayerBodyState::new(
            id,
            owner,
            0,
            0,
            hero.guid,
            source_pose,
            None,
            FIRST_TICK,
            None,
        ))
        .unwrap();
        let target = room
            .bodies()
            .unwrap()
            .find(|bot| bot.spawn.team == 1)
            .unwrap()
            .entity;
        let [x, y, z] = mover.pose().position;
        let in_range = [x - 1.5, y, z];
        let bot = room.body_mut(target).unwrap();
        bot.position = in_range;
        // The source is near level geometry. Isolate capsule movement from that
        // obstruction while keeping the map's authored combat line-of-sight.
        bot.attach_collision(
            &crate::mover_settings::MoverSettings::default(),
            Arc::new(crate::contact_world::ContactWorld::new(Vec::new()).unwrap()),
            0x10,
            |_, _, _, _| Some(y),
        );
        let health = room.body_healths().unwrap()[&target];
        let patrol = crate::patrol_script::PatrolScript::load().unwrap();
        let mut hit_frames = Vec::new();
        let mut displaced_frames = Vec::new();
        let mut pending_knockback = None;
        for dt in 1..250 {
            let frame = FIRST_TICK + dt;
            if let Some((hit_frame, before)) = pending_knockback.take() {
                scene.step_bots(frame, &patrol).unwrap();
                let bot = scene.simulation_mut().body_mut(target).unwrap();
                assert!(
                    bot.position[0] < before,
                    "frame {frame}: before x={before}, after={:?}, velocity={:?}",
                    bot.position,
                    bot.velocity
                );
                displaced_frames.push(hit_frame);
            }
            if matches!(dt, 120 | 220) {
                // Reset the same body's pose to isolate reactivation from range.
                let bot = scene.simulation_mut().body_mut(target).unwrap();
                bot.clear_velocity_changes();
                bot.velocity = [0.0; 3];
                bot.position = if dt == 120 { in_range } else { [x - 8.0, y, z] };
            }
            let command = Command {
                frame,
                yaw: -16384,
                buttons: if matches!(dt, 20 | 120 | 220) {
                    0x800
                } else {
                    0
                },
                ..Command::default()
            };
            mover
                .step(scene.movement_index(), command, 0.016, |_, _| {})
                .unwrap();
            let room = scene.simulation_mut();
            room.prepare_spatial_frame(frame).unwrap();
            runtime.set_spatial_frame(Arc::clone(&room.spatial_frame.as_ref().unwrap().1));
            runtime.advance(command, &mover, BodyStep::Posed).unwrap();
            let source = room.sample_health_body(id.entity).unwrap();
            let requests = runtime.take_script_requests();
            let snapshot = runtime.snapshot().unwrap();
            assert!(
                snapshot.diagnostics.is_empty(),
                "frame {frame}: {:?}",
                snapshot.diagnostics
            );
            if !requests.is_empty() {
                for view in [View::Owner, View::Observer] {
                    runtime.encoder().project(snapshot, view).unwrap();
                }
            }
            for effect in requests {
                room.settle_script_request(effect, source, frame, Duration::ZERO)
                    .unwrap();
                hit_frames.push(frame);
                let bot = room.body_mut(target).unwrap();
                assert!(
                    bot.has_combat_motion(),
                    "frame {frame}: melee impulse was not queued"
                );
                pending_knockback = Some((frame, bot.position[0]));
            }
        }
        let room = scene.simulation_mut();
        assert_eq!(hit_frames.len(), 2);
        assert_eq!(displaced_frames, hit_frames);
        assert_eq!(room.body_healths().unwrap()[&target], health - 60.0);
        let all_cues = room.take_player_cues();
        let cues: Vec<_> = all_cues
            .into_iter()
            .filter(|cue| {
                if matches!(cue.notice, CombatNotice::Hit { .. }) {
                    true
                } else {
                    assert!(
                        matches!(cue.notice, CombatNotice::Voice { .. }),
                        "unexpected non-hit cue: {:?}",
                        cue.notice
                    );
                    false
                }
            })
            .collect();
        assert_eq!(cues.len(), hit_frames.len());
        for (cue, shot_frame) in cues.into_iter().zip(hit_frames) {
            assert_eq!(cue.owner, owner);
            assert_eq!(cue.listener, id);
            assert_eq!(
                cue.notice,
                CombatNotice::Hit {
                    target,
                    dealt: 30.0,
                    critical: false,
                    killed: false,
                    shot_frame,
                }
            );
        }
    }

    #[test]
    fn authored_health_settlement_uses_bot_health_and_death_lifecycle() {
        let bots = fixture_bots();
        let source = bots[0].entity;
        let target = bots[1].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        let source_body = room.sample_health_body(source).unwrap();
        room.enable_spatial_queries(
            false,
            Arc::new(fixture_ground()),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        let tick = 3;
        let now = Duration::from_millis(48);
        room.prepare_spatial_frame(tick).unwrap();
        let frame = Arc::clone(&room.spatial_frame.as_ref().unwrap().1);
        let mut requests = RoomScriptRequests::default();
        assert!(!requests.queue_health(&frame, target, f64::NAN));
        assert!(!requests.queue_health(&frame, target, 0.0));
        assert!(!requests.queue_health(&frame, 0, 1.0));

        for (amount, expected, changed) in [
            (-25.0, 75.0, true),
            (f64::MAX, 100.0, true),
            (-200.0, 0.0, true),
            (100.0, 0.0, false),
        ] {
            assert!(requests.queue_health(&frame, target, amount));
            for request in requests.take() {
                room.settle_script_request(request, source_body, tick, now)
                    .unwrap();
            }
            assert_eq!(room.body_healths().unwrap()[&target], expected);
            let updates = room.take_scripted_health();
            assert_eq!(updates.len(), usize::from(changed));
            assert!(updates.iter().all(|update| update.entity == target));
        }
        let (death, respawn) = room.observed_bot_death(target).unwrap().unwrap();
        assert_eq!(death.killer, source);
        assert_eq!(death.frame, tick);
        assert_eq!(respawn, now + BOT_RESPAWN_TIME);
    }

    #[test]
    fn scripted_kill_respawns_once_when_combat_is_inactive() {
        #[derive(serde::Deserialize)]
        struct Contract {
            schema_version: u32,
            input: Input,
            expected: Expected,
        }
        #[derive(serde::Deserialize)]
        struct Input {
            kill_tick: u32,
            kill_time_ms: u64,
            health_change: f64,
        }
        #[derive(serde::Deserialize)]
        struct Expected {
            replacement_counts: [usize; 3],
            fresh_health: f64,
        }

        let contract: Contract = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/training-bot-scripted-respawn-v1.json"
        ))
        .unwrap();
        assert_eq!(contract.schema_version, 1);
        let tick = contract.input.kill_tick;
        let now = Duration::from_millis(contract.input.kill_time_ms);
        let due = now + BOT_RESPAWN_TIME;
        let one_ns = Duration::from_nanos(1);
        let [before_due, at_due, after_due] = contract.expected.replacement_counts;

        let bots = fixture_bots();
        let source = bots[0].entity;
        let target = bots[1].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        let source_body = room.sample_health_body(source).unwrap();
        let target_body = room.sample_health_body(target).unwrap();
        let ground = Arc::new(fixture_ground());
        let movers = crate::mover_settings::MoverCatalog::default();
        let queries = crate::scene_assets::BotQueries::new(false, &movers, &ground, None);
        room.enable_spatial_queries(
            false,
            Arc::clone(&ground),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        room.prepare_spatial_frame(tick).unwrap();

        let mut requests = RoomScriptRequests::default();
        let frame = &room.spatial_frame.as_ref().unwrap().1;
        assert!(requests.queue_health(frame, target, contract.input.health_change));
        for request in requests.take() {
            room.settle_script_request(request, source_body, tick, now)
                .unwrap();
        }
        assert_eq!(room.observed_bot_death(target).unwrap().unwrap().1, due);
        assert!(!room.combat_active);

        let early = room.finish_tick(&queries, tick + 1, due - one_ns).unwrap();
        assert_eq!(early.len(), before_due);
        assert!(room.sample_health_body(target) == Some(target_body));

        let replacements = room.finish_tick(&queries, tick + 2, due).unwrap();
        assert_eq!(replacements.len(), at_due);
        let [replacement] = replacements.as_slice() else {
            panic!("expected one replacement at the respawn deadline");
        };
        assert_eq!(replacement.retired, target);
        let fresh = replacement.created.entity;
        assert_ne!(fresh, target);
        assert!(matches!(
            &replacement.created.operation,
            Some(Operation::Create(_))
        ));
        assert!(room.sample_health_body(target).is_none());
        assert!(room.world.get_entity(target_body.entity).is_err());
        let fresh_body = room.sample_health_body(fresh).unwrap();
        assert_ne!(fresh_body.entity, target_body.entity);
        assert_eq!(
            room.body_healths().unwrap()[&fresh],
            contract.expected.fresh_health
        );

        let repeated = room.finish_tick(&queries, tick + 3, due + one_ns).unwrap();
        assert_eq!(repeated.len(), after_due);
        assert!(room.sample_health_body(fresh) == Some(fresh_body));
        assert!(room.sample_health_body(source) == Some(source_body));
        assert!(!room.combat_active);
    }

    #[test]
    fn authored_health_queue_has_a_body_local_limit() {
        let bots = fixture_bots();
        let target = bots[0].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        room.enable_spatial_queries(
            false,
            Arc::new(fixture_ground()),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        room.prepare_spatial_frame(3).unwrap();
        let frame = &room.spatial_frame.as_ref().unwrap().1;
        let mut requests = RoomScriptRequests::default();
        for _ in 0..MAX_BODY_SCRIPT_REQUESTS {
            assert!(requests.queue_health(frame, target, -1.0));
        }
        assert!(!requests.queue_health(frame, target, -1.0));
        assert_eq!(requests.take().len(), MAX_BODY_SCRIPT_REQUESTS);
        assert!(requests.queue_health(frame, target, -1.0));
    }

    #[test]
    fn admitted_health_batches_keep_order_and_skip_replaced_targets_at_settlement() {
        use crate::game_combat::BotState;

        let bots = fixture_bots();
        let first = bots[0].entity;
        let second = bots[1].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        let source = room.sample_health_body(first).unwrap();
        room.enable_spatial_queries(
            false,
            Arc::new(fixture_ground()),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        let mut requests = RoomScriptRequests::default();
        let tick = 3;
        let now = Duration::from_millis(48);
        room.prepare_spatial_frame(tick).unwrap();
        let frame = &room.spatial_frame.as_ref().unwrap().1;
        assert!(requests.queue_health_changes(frame, &[(second, -20.0), (first, -10.0)]));
        assert_eq!(room.body_healths().unwrap()[&first], 100.0);
        assert_eq!(room.body_healths().unwrap()[&second], 100.0);
        for request in requests.take() {
            room.settle_script_request(request, source, tick, now)
                .unwrap();
        }
        assert_eq!(
            room.take_scripted_health()
                .into_iter()
                .map(|update| update.entity)
                .collect::<Vec<_>>(),
            [second, first],
        );

        room.prepare_spatial_frame(tick + 1).unwrap();
        let frame = &room.spatial_frame.as_ref().unwrap().1;
        assert!(requests.queue_health_changes(frame, &[(second, 10.0), (first, -5.0)]));
        // Model replacement between admission and settlement while keeping the public ID.
        let previous = room.by_wire[&second];
        let body = room.world.entity_mut(previous).take::<BotState>().unwrap();
        assert!(room.world.despawn(previous));
        let replacement = room.world.spawn(body).id();
        assert_ne!(previous, replacement);
        assert_eq!(room.by_wire.insert(second, replacement), Some(previous));
        for request in requests.take() {
            room.settle_script_request(request, source, tick + 1, now)
                .unwrap();
        }
        assert_eq!(room.body_healths().unwrap()[&first], 85.0);
        assert_eq!(room.body_healths().unwrap()[&second], 80.0);
        let updates = room.take_scripted_health();
        assert_eq!(updates.len(), 1);
        assert_eq!(updates[0].entity, first);
    }

    #[test]
    fn scripted_health_damage_emits_bot_and_player_hurt_voice_cues_and_ignores_heal() {
        use crate::{
            combat_voice::CombatVoiceCatalog, contact_world::ContactWorld,
            game_combat::PlayerBodyState, game_heroes::SOLDIER, game_replication::BodyId,
            mover::Mover, mover_settings::MoverSettings, script_player::fixture_body_factory,
        };

        let bots = fixture_bots();
        let bot = bots[1].entity;
        let npc_attacker = bots[0].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        let (factory, spawn) = fixture_body_factory();
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            spawn.pose.position,
            0,
            0x08,
        )
        .unwrap();
        let player = BodyId {
            entity: spawn.entity,
            generation: 1,
        };
        room.spawn_player_body(PlayerBodyState::new(
            player,
            spawn.controller,
            spawn.slot,
            0,
            SOLDIER,
            mover,
            Some(Box::new(factory.start(spawn).unwrap())),
            spawn.frame,
            None,
        ))
        .unwrap();
        let catalog = CombatVoiceCatalog::from_json(
            r#"{
                "hurt_cooldown_ticks": 5,
                "hurt": {
                    "minor": { "stimulus": "0x0EE0000000000003", "lines": ["0x0760000000000006"] },
                    "bot_death": { "stimulus": "0x0EE0000000000001", "lines": ["0x076000000000819E"] }
                }
            }"#,
        )
        .unwrap();
        room.set_combat_voice(Arc::new(catalog));

        let dealer = Death {
            time: 0.1,
            frame: 10,
            killer: player.entity,
        };
        assert!(
            room.settle_health(
                bot,
                HealthEffect::Damage(10.0),
                Death {
                    killer: npc_attacker,
                    ..dealer
                },
                Duration::from_millis(155),
            )
            .unwrap()
        );
        assert!(room.take_player_cues().is_empty());
        assert!(
            room.settle_health(
                bot,
                HealthEffect::Damage(20.0),
                dealer,
                Duration::from_millis(160)
            )
            .unwrap()
        );
        // Heal never emits a hurt cue even after cooldown.
        assert!(
            room.settle_health(
                bot,
                HealthEffect::Heal(5.0),
                Death {
                    frame: 20,
                    ..dealer
                },
                Duration::from_millis(320),
            )
            .unwrap()
        );
        assert!(
            room.settle_health(
                player.entity,
                HealthEffect::Damage(25.0),
                Death {
                    killer: bot,
                    frame: 11,
                    time: 0.176,
                },
                Duration::from_millis(176),
            )
            .unwrap()
        );

        let cues = room.take_player_cues();
        assert_eq!(cues.len(), 2);
        assert_eq!(
            cues[0].notice,
            crate::game_combat_messages::CombatNotice::Voice {
                anchor: bot,
                speaker: bot,
                listener: player.entity,
                stimulus: 0x0EE0_0000_0000_0003,
                line: 0x0760_0000_0000_0006,
            }
        );
        assert_eq!(
            cues[1].notice,
            crate::game_combat_messages::CombatNotice::Voice {
                anchor: player.entity,
                speaker: player.entity,
                listener: player.entity,
                stimulus: 0x0EE0_0000_0000_0003,
                line: 0x0760_0000_0000_0006,
            }
        );
    }
}
