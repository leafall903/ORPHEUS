//! Lifetimes for recipient-side scripts attached by an admitted status effect.
use super::{Entity, RoomSimulation};
use crate::script_init::{Component, InitFault, World};
use crate::script_wire::Variables;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Control {
    Stun,
    Sleep,
}

#[derive(Clone, Copy, Debug)]
pub(crate) enum StatusScriptOp<'a> {
    Play {
        caller: crate::script_init::Site,
        request: &'a crate::script_init::PlayScript,
    },
    Attach {
        graph: u32,
        source: u32,
    },
    Detach {
        graph: u32,
        instance: u16,
    },
}

impl StatusScriptOp<'_> {
    pub(crate) fn run(
        self,
        body: &mut Component,
        world: &mut impl World,
    ) -> Result<Option<u16>, InitFault> {
        match self {
            Self::Play { caller, request } => body
                .receive_room_play_script(caller, request, world)
                .map(|()| None),
            Self::Attach { graph, source } => body
                .attach_status_script(graph, source, Variables::new(), world)
                .map(Some),
            Self::Detach { graph, instance } => body
                .detach_status_script(instance, graph, world)
                .map(|()| None),
        }
    }
}

pub(super) struct ActiveStatusScript {
    body: Entity,
    instance: u16,
    kind: Control,
    until: u32,
    retirement_failed: bool,
}

impl RoomSimulation {
    pub(super) fn admits_projectile_control(&mut self, wire: u32, effect: u64) -> bool {
        let legacy = &crate::script_control::LEGACY_PROJECTILE_CONTROL;
        if !legacy.has_effect_marker(&crate::script_wire::Value::Asset(effect)) {
            return true;
        }
        let Ok(Some(bot)) = self.state_mut(wire) else {
            return true;
        };
        let Some(enemy) = bot
            .enemy
            .as_mut()
            .filter(|e| legacy.matches_recipient_mode(e.binding.mode))
        else {
            return true;
        };
        match enemy
            .scripts
            .legacy_projectile_control_allowed(&enemy.graphs, &enemy.binding)
        {
            Ok(allowed) => allowed,
            Err(error) => {
                enemy.halt(&mut bot.bot, error);
                false
            }
        }
    }
    fn status_body(&self, wire: u32) -> Option<Entity> {
        self.players_by_wire
            .get(&wire)
            .or_else(|| self.by_wire.get(&wire))
            .copied()
    }

    pub(super) fn run_status_script(&mut self, wire: u32, op: StatusScriptOp<'_>) -> Option<u16> {
        self.try_status_script(wire, op)
            .and_then(Result::ok)
            .flatten()
    }

    fn try_status_script(
        &mut self,
        wire: u32,
        op: StatusScriptOp<'_>,
    ) -> Option<Result<Option<u16>, String>> {
        let result = if let Some(id) = self.player_handle_for_wire(wire) {
            let player = self.player_body_mut(id)?;
            let runtime = player.runtime.as_mut()?;
            let outcome = runtime.status_script(op);
            if outcome.is_ok() {
                player.mover.set_cinematic_aim(runtime.cinematic_aim());
                player.mover.set_authored_facing(runtime.authored_facing());
                player.mover.set_tether(runtime.active_tether());
                player.mover.set_ball_roll_speed(runtime.ball_roll_speed());
                player.mover.set_mover_override(runtime.mover_override());
                let velocity_changes = runtime.take_velocity_changes();
                if runtime
                    .vitals()
                    .is_some_and(|vitals| vitals.death().is_some())
                {
                    player.mover.clear_pending_motion();
                } else if !player.mover.is_stunned() {
                    player.mover.set_move_request(runtime.move_request());
                    for (frame, change) in velocity_changes {
                        player.mover.queue_status_velocity_change(frame, change);
                    }
                }
                if let Some(modifiers) = runtime.movement_modifiers() {
                    let frame = player.mover.pose().frame.unwrap_or(player.frame);
                    player.mover.track_modifiers(frame, modifiers);
                }
            }
            outcome
        } else {
            let bot = self.state_mut(wire).ok()??;
            if let Some(script) = bot.stock_script.as_mut() {
                let outcome = script.status_script(op);
                if outcome.is_ok() {
                    let changes = script.take_velocity_changes();
                    if !changes.is_empty() && !bot.bot.dead && bot.vitals.death().is_none() {
                        bot.bot.queue_status_velocity_changes(changes);
                    }
                }
                outcome
            } else {
                let enemy = bot.enemy.as_mut().filter(|enemy| enemy.fault.is_none())?;
                enemy
                    .scripts
                    .status_script(&enemy.graphs, &enemy.binding, &mut bot.vitals, op)
            }
        };
        if let Err(error) = &result {
            eprintln!("status script {wire:08X} {op:?}: {error}");
        }
        Some(result)
    }

    pub(super) fn attach_control_script(
        &mut self,
        wire: u32,
        graph: Option<u32>,
        source: u32,
        kind: Control,
        tick: u32,
        ticks: u32,
    ) {
        let (Some(graph), Some(body)) = (graph, self.status_body(wire)) else {
            return;
        };
        let key = (wire, graph);
        let until = tick.saturating_add(ticks);
        if let Some(link) = self.status_scripts.get_mut(&key) {
            if link.body == body && link.until > tick {
                link.until = link.until.max(until);
                return;
            }
        }
        if !self.detach_control_script(key) {
            return;
        }
        if let Some(instance) =
            self.run_status_script(wire, StatusScriptOp::Attach { graph, source })
        {
            self.status_scripts.insert(
                key,
                ActiveStatusScript {
                    body,
                    instance,
                    kind,
                    until,
                    retirement_failed: false,
                },
            );
        }
    }

    fn detach_control_script(&mut self, key: (u32, u32)) -> bool {
        let Some(link) = self.status_scripts.get(&key) else {
            return true;
        };
        if self.status_body(key.0) == Some(link.body) {
            if link.retirement_failed {
                return false;
            }
            let op = StatusScriptOp::Detach {
                graph: key.1,
                instance: link.instance,
            };
            if self
                .try_status_script(key.0, op)
                .is_some_and(|result| result.is_err())
            {
                // The host/VM transaction rolled back. Keep the instance identity
                // until this body retires, without replaying a broken exit each tick.
                if let Some(link) = self.status_scripts.get_mut(&key) {
                    link.retirement_failed = true;
                }
                return false;
            }
        }
        self.status_scripts.shift_remove(&key);
        true
    }

    pub(super) fn settle_control_scripts(&mut self, tick: u32) {
        let expired: Vec<_> = self
            .status_scripts
            .iter()
            .filter_map(|(&key, link)| {
                if link.retirement_failed && self.status_body(key.0) == Some(link.body) {
                    return None;
                }
                let state = self.actor_statuses.get(&key.0);
                let deadline = state.and_then(|s| match link.kind {
                    Control::Sleep => s.sleep_until,
                    Control::Stun => s.stunned_until,
                });
                (tick >= link.until
                    || deadline.is_none_or(|t| t <= tick)
                    || self.status_body(key.0) != Some(link.body))
                .then_some(key)
            })
            .collect();
        for key in expired {
            self.detach_control_script(key);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{game_content::GameMap, scene_assets::SceneAssets, vitals::Death};
    use std::{path::PathBuf, time::Duration};

    #[test]
    #[ignore = "requires 104319 Practice Range and the extracted status script catalog"]
    fn prepared_room_attaches_admitted_sleep_and_stun_once_and_retires_each_effect() {
        let dir = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let scene = SceneAssets::load(&dir).unwrap();
        let mut scene = scene.prepare(GameMap::practice_range()).unwrap();
        let room = scene.simulation_mut();
        let opponent = room
            .states()
            .flatten()
            .find(|s| s.stock_script.is_some())
            .unwrap();
        let target = opponent.bot.entity;
        let team = opponent.bot.spawn.team;
        let source = room
            .bodies()
            .unwrap()
            .find(|bot| bot.spawn.team != team)
            .unwrap()
            .entity;
        let sleep = 0x04a0_0000_0000_026f;
        let stun = 0x04a0_0000_0000_0079;
        for (effect, early_wake) in [(sleep, true), (stun, false)] {
            let graph = room.ability_rules.status_script(effect).unwrap();
            let key = (target, graph);
            let tick = if early_wake { 300 } else { 800 };
            room.prepare_spatial_frame(tick).unwrap();
            room.actor_statuses
                .entry(target)
                .or_default()
                .cc_immune_until = Some(tick + 3);
            room.apply_projectile_status_effect(effect, source, target, false, tick);
            room.apply_projectile_status_effect(effect, source, target, true, tick + 3);
            assert!(!room.status_scripts.contains_key(&key));
            room.apply_projectile_status_effect(effect, source, target, false, tick + 4);
            let instance = room.status_scripts[&key].instance;
            let observed = room
                .state_mut(target)
                .unwrap()
                .unwrap()
                .stock_script
                .as_ref()
                .unwrap()
                .projection()
                .unwrap();
            let branches: Vec<_> = observed
                .snapshot
                .instances
                .iter()
                .filter(|(_, i)| i.graph == graph || i.graph == 0x224e)
                .map(|(id, i)| {
                    (
                        id,
                        i.graph,
                        i.states
                            .iter()
                            .filter(|(_, state)| state.active)
                            .map(|(slot, _)| *slot)
                            .collect::<Vec<_>>(),
                    )
                })
                .collect();
            eprintln!("status {graph:04X} application instance/graph/active states {branches:X?}");
            let count = |room: &RoomSimulation| {
                let e = room.by_wire[&target];
                let bot = room.world.get::<super::super::BotState>(e).unwrap();
                let projection = bot.stock_script.as_ref().unwrap().projection().unwrap();
                projection
                    .snapshot
                    .instances
                    .values()
                    .filter(|i| i.graph == graph)
                    .count()
            };
            assert_eq!(
                count(room),
                1,
                "room never attached exactly one recipient graph"
            );
            room.apply_projectile_status_effect(effect, source, target, false, tick + 5);
            assert_eq!(room.status_scripts[&key].instance, instance);
            assert_eq!(count(room), 1);
            let deadline = room.status_scripts[&key].until;
            if early_wake {
                let frame = tick + 6;
                room.settle_health(
                    target,
                    super::super::HealthEffect::Damage(1.0),
                    Death {
                        time: 0.0,
                        frame,
                        killer: source,
                    },
                    Duration::ZERO,
                )
                .unwrap();
            } else {
                room.step_status_effects(deadline);
            }
            assert!(!room.status_scripts.contains_key(&key));
            assert_eq!(count(room), 0, "retired wrapper is still mounted");
        }

        let frame = 1200;
        let graph = room.ability_rules.status_script(stun).unwrap();
        room.prepare_spatial_frame(frame).unwrap();
        room.apply_projectile_status_effect(stun, source, target, false, frame);
        assert!(room.status_scripts.contains_key(&(target, graph)));
        room.kill_bot_for_test(target, frame + 1, Duration::ZERO);
        let deadline = room.state_mut(target).unwrap().unwrap().respawn_at.unwrap();
        let replaced = room.respawn_due(frame + 2, deadline).unwrap();
        assert!(replaced.iter().any(|change| change.retired == target));
        assert!(!room.status_scripts.contains_key(&(target, graph)));
        assert!(room.status_body(target).is_none());
        room.apply_projectile_status_effect(stun, source, target, false, frame + 3);
        assert!(!room.status_scripts.contains_key(&(target, graph)));
    }
}
