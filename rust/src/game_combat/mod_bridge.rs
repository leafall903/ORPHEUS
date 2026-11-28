//! Live `RoomSimulation` bridge for the bounded Lua 5.4 Mod / Workshop host (`--features lua-mods`).
//!
//! Each room owns its own isolated Lua 5.4 VM (`ModScript`) instantiated from the
//! shared `ModHost` source, so concurrent rooms never share mutable Lua state or
//! contend on the host mutex during 60Hz ticks. All hooks are fault-isolated per
//! hook name and never panic or retire the room.

use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
    time::Duration,
};

use crate::{
    collision_scene::CollisionScene,
    commands::{CROUCH, FIRE, JUMP, SECONDARY},
    game_bots::{BOT_ENTITY, BOT_RESPAWN_TIME, Bot},
    game_clock::TICK_PERIOD,
    game_combat::{
        BotState, PlayerBodyState, RoomSimulation, bot_create, bot_health_update,
        player::PlayerDeath, script_requests::PlayerRoomCue,
    },
    game_combat_messages::CombatNotice,
    game_heroes::HeroCatalog,
    game_hitscan::aim_direction,
    game_parameters::{
        ClientWheelAction, EMOTE_WHEEL_ACTIONS, GREETING_WHEEL_ACTION, NEED_HEALING_WHEEL_ACTION,
        SPRAY_WHEEL_ACTIONS, THANKS_WHEEL_ACTION, VOICELINE_WHEEL_ACTIONS,
    },
    game_replication::BodyId,
    mod_script::{
        ModAction, ModDamageEvent, ModHealEvent, ModHeroInfo, ModHost, ModKillEvent,
        ModPlayerSnapshot, ModRaycastHit, ModRoomContext, ModRules, ModScript, ModWorldView,
    },
    mover::VelocityChange,
    room_primitives::INTERACT_BUTTON_BIT,
    script_init::{Relocation, ScriptedMotion},
    vitals::{Death, Vitals},
    world::{EntityUpdate, Operation},
};

const MAX_BROADCAST_LOG: usize = 64;
const MAX_PENDING_CHAT_REPLIES: usize = 64;
const MAX_CUSTOM_PROJECTILES: usize = 64;
const MAX_MOD_SPAWNED_BOTS: usize = 24;
const MAX_FAULTS_PER_HOOK: u32 = 5;

/// Verified OW 1.74 wire input button masks (matches `script_runtime::INPUTS`).
const MOD_BUTTON_MAP: &[(u32, &str)] = &[
    (FIRE, "primary"),
    (SECONDARY, "secondary"),
    (0x0004, "weapon_swap"),
    (0x0008, "ability1"),
    (0x0010, "ability2"),
    (0x0020, "ultimate"),
    (JUMP, "jump"),
    (CROUCH, "crouch"),
    (0x0400, "reload"),
    (0x0800, "melee"),
    (INTERACT_BUTTON_BIT, "interact"),
];

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) enum BotModAi {
    Idle,
    Dance {
        base_y: f64,
        until_tick: Option<u32>,
    },
    Follow,
}

#[derive(Clone, Debug)]
struct CustomProjectile {
    source: u32,
    position: [f64; 3],
    velocity: [f64; 3],
    damage: f64,
    radius: f64,
    gravity: f64,
    ttl_ticks: u32,
}

struct RoomModWorldView {
    actors: Vec<ModPlayerSnapshot>,
    heroes: Option<Arc<HeroCatalog>>,
    rays: Option<Arc<CollisionScene>>,
}

impl ModWorldView for RoomModWorldView {
    fn raycast(
        &self,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
    ) -> Option<ModRaycastHit> {
        let rays = self.rays.as_deref()?;
        let len = (direction[0] * direction[0]
            + direction[1] * direction[1]
            + direction[2] * direction[2])
            .sqrt();
        if !len.is_finite() || len <= 1e-6 {
            return None;
        }
        let dir = [direction[0] / len, direction[1] / len, direction[2] / len];
        let hit = rays.cast_ray(origin, dir, max_distance, 0x0018).ok()??;
        Some(ModRaycastHit {
            distance: hit.distance,
            point: hit.point,
            normal: hit.normal,
        })
    }

    fn players_in_radius(
        &self,
        center: [f64; 3],
        radius: f64,
        team: Option<u8>,
    ) -> Vec<ModPlayerSnapshot> {
        let r2 = radius * radius;
        self.actors
            .iter()
            .filter(|a| {
                if let Some(t) = team {
                    if a.team != t {
                        return false;
                    }
                }
                let dx = a.position[0] - center[0];
                let dy = a.position[1] - center[1];
                let dz = a.position[2] - center[2];
                dx * dx + dy * dy + dz * dz <= r2
            })
            .cloned()
            .collect()
    }

    fn get_entity(&self, entity: u32) -> Option<ModPlayerSnapshot> {
        self.actors.iter().find(|a| a.entity == entity).cloned()
    }

    fn find_hero(&self, query: &str) -> Option<ModHeroInfo> {
        let hero = self.heroes.as_deref()?.find_hero(query)?;
        Some(ModHeroInfo {
            guid: hero.guid,
            name: hero.name.clone(),
            role: hero.role.clone(),
        })
    }

    fn heroes(&self) -> Vec<ModHeroInfo> {
        self.heroes
            .as_deref()
            .map(|cat| {
                cat.heroes()
                    .map(|h| ModHeroInfo {
                        guid: h.guid,
                        name: h.name.clone(),
                        role: h.role.clone(),
                    })
                    .collect()
            })
            .unwrap_or_default()
    }
}

#[derive(Default)]
pub(super) struct RoomModState {
    pub(super) map_guid: u64,
    pub(super) mode_guid: u64,
    pub(super) last_generation: u64,
    pub(super) started_generation: u64,
    pub(super) room_script: Option<ModScript>,
    pub(super) active_rules: ModRules,
    pub(super) heroes: Option<Arc<HeroCatalog>>,
    pub(super) rays: Option<Arc<CollisionScene>>,
    pub(super) player_gravity_overrides: HashMap<u32, f64>,
    pub(super) player_speed_overrides: HashMap<u32, f64>,
    pub(super) actor_damage_scales: HashMap<u32, f64>,
    pub(super) actor_damage_taken_scales: HashMap<u32, f64>,
    pub(super) actor_healing_scales: HashMap<u32, f64>,
    pub(super) bot_ai_modes: HashMap<u32, BotModAi>,
    pub(super) spawned_mod_bots: Vec<u32>,
    pub(super) pending_hero_switches: HashMap<u32, u64>,
    pub(super) pending_chat_replies: VecDeque<String>,
    pub(super) broadcast_log: Vec<String>,
    pub(super) fault_counts: HashMap<&'static str, u32>,
    pub(super) joined_owners: HashMap<u32, u64>,
    custom_projectiles: Vec<CustomProjectile>,
    in_mod_callback: bool,
}

impl RoomSimulation {
    pub(crate) fn attach_mod_host(
        &mut self,
        host: Arc<Mutex<ModHost>>,
        map_guid: u64,
        mode_guid: u64,
    ) {
        let instantiated = host
            .lock()
            .ok()
            .and_then(|guard| guard.instantiate_for_room().ok());
        self.mod_host = Some(host);
        self.mod_state.map_guid = map_guid;
        self.mod_state.mode_guid = mode_guid;
        self.mod_state.last_generation = 0;
        self.mod_state.started_generation = 0;
        self.mod_state.player_gravity_overrides.clear();
        self.mod_state.player_speed_overrides.clear();
        self.mod_state.actor_damage_scales.clear();
        self.mod_state.actor_damage_taken_scales.clear();
        self.mod_state.actor_healing_scales.clear();
        self.mod_state.bot_ai_modes.clear();
        self.mod_state.pending_hero_switches.clear();
        self.mod_state.fault_counts.clear();
        if let Some((_, script)) = instantiated {
            self.mod_state.active_rules = script.rules();
            self.mod_state.room_script = Some(script);
        } else {
            self.mod_state.active_rules = ModRules::default();
            self.mod_state.room_script = None;
        }
        self.sync_player_mod_state();
    }

    pub(crate) fn set_mod_world_assets(
        &mut self,
        heroes: Arc<HeroCatalog>,
        rays: Arc<CollisionScene>,
    ) {
        self.mod_state.heroes = Some(heroes);
        self.mod_state.rays = Some(rays);
    }

    pub(crate) fn take_mod_hero_switch(&mut self, owner: u32) -> Option<u64> {
        self.mod_state.pending_hero_switches.remove(&owner)
    }

    pub(crate) fn drain_mod_chat_replies(&mut self) -> Vec<String> {
        self.mod_state.pending_chat_replies.drain(..).collect()
    }

    pub(super) fn mod_friendly_fire(&self) -> bool {
        self.mod_host.is_some() && self.mod_state.active_rules.friendly_fire
    }

    pub(super) fn mod_healing_scale(&self) -> f64 {
        if self.mod_host.is_some() {
            self.mod_state.active_rules.healing_scale
        } else {
            1.0
        }
    }

    pub(super) fn mod_healing_scale_for(&self, healer: u32, target: u32) -> f64 {
        if self.mod_host.is_none() {
            return 1.0;
        }
        let base = self.mod_state.active_rules.healing_scale;
        let out = self
            .mod_state
            .actor_healing_scales
            .get(&healer)
            .copied()
            .unwrap_or(1.0);
        let _ = target;
        (base * out).max(0.0)
    }

    #[cfg(test)]
    pub(crate) fn mod_active_rules(&self) -> ModRules {
        self.mod_state.active_rules
    }

    #[cfg(test)]
    pub(crate) fn mod_broadcast_log(&self) -> &[String] {
        &self.mod_state.broadcast_log
    }

    fn hero_name_for(&self, hero_guid: u64) -> String {
        self.mod_state
            .heroes
            .as_deref()
            .and_then(|cat| cat.get(hero_guid))
            .map(|h| h.name.clone())
            .unwrap_or_default()
    }

    fn snapshot_player_for_mod(&self, wire: u32) -> Option<ModPlayerSnapshot> {
        let &entity = self.players_by_wire.get(&wire)?;
        let state = self.world.get::<PlayerBodyState>(entity)?;
        let (health, max_health) = state
            .vitals()
            .map_or((0.0, 0.0), |v| (v.total(), v.maximum()));
        let pose = state.mover.pose();
        let facing = aim_direction(&crate::commands::Command {
            yaw: pose.yaw,
            pitch: pose.pitch,
            ..Default::default()
        });
        let ult_charge = state
            .runtime
            .as_deref()
            .map_or(0.0, |rt| rt.ult_charge_percent());
        let emoting = state.runtime.as_deref().is_some_and(|rt| rt.is_emoting());
        Some(ModPlayerSnapshot {
            entity: state.id.entity,
            name: state.name.clone(),
            team: state.team,
            hero_guid: state.hero,
            hero_name: self.hero_name_for(state.hero),
            health,
            max_health,
            position: pose.position,
            velocity: pose.velocity,
            yaw: f64::from(pose.yaw) * 360.0 / 65536.0,
            pitch: f64::from(pose.pitch) * 360.0 / 65536.0,
            facing,
            ult_charge,
            alive: state.alive(),
            crouching: state.mover.crouched(),
            grounded: pose.flags & crate::mover_posture::IN_AIR == 0,
            emoting,
            is_bot: false,
        })
    }

    fn snapshot_bot_for_mod(&self, wire: u32) -> Option<ModPlayerSnapshot> {
        let &entity = self.by_wire.get(&wire)?;
        let state = self.world.get::<BotState>(entity)?;
        let yaw_rad = state.bot.yaw * std::f64::consts::TAU / 65536.0;
        let facing = [yaw_rad.sin(), 0.0, yaw_rad.cos()];
        let emoting = matches!(
            self.mod_state.bot_ai_modes.get(&wire),
            Some(BotModAi::Dance { .. })
        );
        Some(ModPlayerSnapshot {
            entity: state.bot.entity,
            name: state.bot.spawn.name.clone(),
            team: state.bot.spawn.team,
            hero_guid: state.bot.spawn.hero,
            hero_name: self.hero_name_for(state.bot.spawn.hero),
            health: state.vitals.total(),
            max_health: state.vitals.maximum(),
            position: state.bot.position,
            velocity: state.bot.velocity,
            yaw: state.bot.yaw * 360.0 / 65536.0,
            pitch: f64::from(state.bot.pitch()) * 360.0 / 65536.0,
            facing,
            ult_charge: 0.0,
            alive: !state.bot.dead && state.vitals.death().is_none(),
            crouching: false,
            grounded: true,
            emoting,
            is_bot: true,
        })
    }

    fn build_mod_context_and_world(
        &self,
        tick: u32,
        now: Duration,
        sender_entity: Option<u32>,
    ) -> (ModRoomContext, RoomModWorldView) {
        let players: Vec<ModPlayerSnapshot> = self
            .players_by_wire
            .keys()
            .filter_map(|&wire| self.snapshot_player_for_mod(wire))
            .collect();
        let bots: Vec<ModPlayerSnapshot> = self
            .by_wire
            .keys()
            .filter_map(|&wire| self.snapshot_bot_for_mod(wire))
            .collect();
        let mut actors = Vec::with_capacity(players.len() + bots.len());
        actors.extend(players.iter().cloned());
        actors.extend(bots.iter().cloned());
        let ctx = ModRoomContext {
            tick,
            elapsed_secs: now.as_secs_f64(),
            map_guid: self.mod_state.map_guid,
            mode_guid: self.mod_state.mode_guid,
            player_count: players.len(),
            time_scale: self.mod_state.active_rules.time_scale,
            rules: self.mod_state.active_rules,
            sender_entity,
            players,
            bots,
        };
        let world_view = RoomModWorldView {
            actors,
            heroes: self.mod_state.heroes.clone(),
            rays: self.mod_state.rays.clone(),
        };
        (ctx, world_view)
    }

    fn sync_player_mod_state(&mut self) {
        if self.mod_host.is_none() {
            return;
        }
        let time_scale = self.mod_state.active_rules.time_scale.clamp(0.1, 3.0);
        let base_gravity = self.mod_state.active_rules.gravity_scale * time_scale * time_scale;
        let base_speed = self.mod_state.active_rules.speed_scale * time_scale;
        let ult_scale = self.mod_state.active_rules.ult_charge_scale;
        let cd_scale = (self.mod_state.active_rules.cooldown_scale / time_scale).clamp(0.05, 10.0);
        let entities: Vec<(u32, bevy_ecs::entity::Entity)> = self
            .players_by_wire
            .iter()
            .map(|(&wire, &entity)| (wire, entity))
            .collect();
        for (wire, entity) in entities {
            let gravity = self
                .mod_state
                .player_gravity_overrides
                .get(&wire)
                .copied()
                .map_or(base_gravity, |g| g * time_scale * time_scale);
            let speed = self
                .mod_state
                .player_speed_overrides
                .get(&wire)
                .copied()
                .map_or(base_speed, |s| s * time_scale);
            if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
                state.mover.set_mod_scales(gravity, speed);
                if let Some(runtime) = state.runtime.as_deref_mut() {
                    runtime.set_ult_charge_scale(ult_scale);
                    runtime.set_cooldown_scale(cd_scale);
                }
            }
        }
    }

    fn sync_room_vm_generation(&mut self) -> Option<(u64, bool)> {
        let host = self.mod_host.as_ref().map(Arc::clone)?;
        let current_gen = host.lock().ok()?.generation();
        if current_gen != self.mod_state.last_generation || self.mod_state.room_script.is_none() {
            let (gen_id, script) = host.lock().ok()?.instantiate_for_room().ok()?;
            self.mod_state.last_generation = gen_id;
            self.mod_state.active_rules = script.rules();
            self.mod_state.room_script = Some(script);
            self.mod_state.player_gravity_overrides.clear();
            self.mod_state.player_speed_overrides.clear();
            self.mod_state.actor_damage_scales.clear();
            self.mod_state.actor_damage_taken_scales.clear();
            self.mod_state.actor_healing_scales.clear();
            self.mod_state.fault_counts.clear();
            self.sync_player_mod_state();
            return Some((gen_id, true));
        }
        Some((current_gen, false))
    }

    fn log_mod_fault(
        &mut self,
        generation: u64,
        hook: &'static str,
        error: impl std::fmt::Display,
    ) {
        let count = self.mod_state.fault_counts.entry(hook).or_insert(0);
        *count = count.saturating_add(1);
        if *count <= MAX_FAULTS_PER_HOOK {
            eprintln!("[mod] hook `{hook}` fault #{count} (gen {generation}): {error}");
        }
    }

    fn hook_silenced(&self, hook: &'static str) -> bool {
        self.mod_state.fault_counts.get(hook).copied().unwrap_or(0) >= MAX_FAULTS_PER_HOOK
    }

    fn with_room_script<R>(
        &mut self,
        f: impl FnOnce(&ModScript, &mut RoomSimulation) -> R,
    ) -> Option<R> {
        if self.mod_state.in_mod_callback {
            return None;
        }
        let script = self.mod_state.room_script.take()?;
        self.mod_state.in_mod_callback = true;
        let out = f(&script, self);
        self.mod_state.in_mod_callback = false;
        self.mod_state.room_script = Some(script);
        Some(out)
    }

    pub(super) fn mod_on_player_spawn(&mut self, id: BodyId, tick: u32) {
        let Some((gen_id, _)) = self.sync_room_vm_generation() else {
            return;
        };
        self.sync_player_mod_state();
        let Some(snapshot) = self.snapshot_player_for_mod(id.entity) else {
            return;
        };
        let owner = self.player_body(id).map(|s| s.owner).unwrap_or(0);
        let prev_hero = self
            .mod_state
            .joined_owners
            .insert(owner, snapshot.hero_guid);
        let now = Duration::from_secs_f64(f64::from(tick) * TICK_PERIOD.as_secs_f64());
        let (ctx, world_view) = self.build_mod_context_and_world(tick, now, Some(id.entity));

        // Fire on_player_join the first time this owner enters the room, or on_hero_switch on hero change.
        if prev_hero.is_none() && !self.hook_silenced("on_player_join") {
            let outcome = self.with_room_script(|script, _| {
                script.on_player_join_with_world(&ctx, &snapshot, &world_view)
            });
            if let Some(res) = outcome {
                match res {
                    Ok(actions) => self.apply_mod_actions(actions, tick, now),
                    Err(error) => self.log_mod_fault(gen_id, "on_player_join", error),
                }
            }
        } else if let Some(old_hero) = prev_hero {
            if old_hero != snapshot.hero_guid && !self.hook_silenced("on_hero_switch") {
                let outcome = self.with_room_script(|script, _| {
                    script.on_hero_switch_with_world(
                        &ctx,
                        &snapshot,
                        old_hero,
                        snapshot.hero_guid,
                        &world_view,
                    )
                });
                if let Some(res) = outcome {
                    match res {
                        Ok(actions) => self.apply_mod_actions(actions, tick, now),
                        Err(error) => self.log_mod_fault(gen_id, "on_hero_switch", error),
                    }
                }
            }
        }

        if !self.hook_silenced("on_player_spawn") {
            let outcome = self.with_room_script(|script, _| {
                script.on_player_spawn_with_world(&ctx, &snapshot, &world_view)
            });
            if let Some(res) = outcome {
                match res {
                    Ok(actions) => self.apply_mod_actions(actions, tick, now),
                    Err(error) => self.log_mod_fault(gen_id, "on_player_spawn", error),
                }
            }
        }
    }

    pub(super) fn mod_on_player_leave(&mut self, owner: u32, id: BodyId, tick: u32) {
        self.mod_state.joined_owners.remove(&owner);
        self.mod_state.pending_hero_switches.remove(&owner);
        let Some((gen_id, _)) = self.sync_room_vm_generation() else {
            return;
        };
        if self.hook_silenced("on_player_leave") {
            return;
        }
        let Some(snapshot) = self.snapshot_player_for_mod(id.entity) else {
            return;
        };
        let now = Duration::from_secs_f64(f64::from(tick) * TICK_PERIOD.as_secs_f64());
        let (ctx, world_view) = self.build_mod_context_and_world(tick, now, Some(id.entity));
        let outcome = self.with_room_script(|script, _| {
            script.on_player_leave_with_world(&ctx, &snapshot, &world_view)
        });
        if let Some(res) = outcome {
            match res {
                Ok(actions) => self.apply_mod_actions(actions, tick, now),
                Err(error) => self.log_mod_fault(gen_id, "on_player_leave", error),
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn mod_evaluate_damage(
        &mut self,
        attacker: u32,
        victim: u32,
        raw_damage: f64,
        headshot: bool,
        melee: bool,
        ability_slot: i32,
        tick: u32,
        now: Duration,
    ) -> f64 {
        let Some((gen_id, _)) = self.sync_room_vm_generation() else {
            return raw_damage;
        };
        let out_scale = self
            .mod_state
            .actor_damage_scales
            .get(&attacker)
            .copied()
            .unwrap_or(1.0);
        let in_scale = self
            .mod_state
            .actor_damage_taken_scales
            .get(&victim)
            .copied()
            .unwrap_or(1.0);
        let scaled =
            (raw_damage * self.mod_state.active_rules.damage_scale * out_scale * in_scale).max(0.0);
        if self.hook_silenced("on_damage")
            || !self
                .mod_state
                .room_script
                .as_ref()
                .is_some_and(ModScript::has_on_damage)
        {
            return scaled;
        }
        let (ctx, world_view) = self.build_mod_context_and_world(tick, now, Some(attacker));
        let event = ModDamageEvent {
            attacker,
            victim,
            damage: scaled,
            headshot,
            melee,
            ability_slot,
        };
        let outcome = self
            .with_room_script(|script, _| script.on_damage_with_world(&ctx, &event, &world_view));
        match outcome {
            Some(Ok(result)) => {
                if !result.actions.is_empty() {
                    self.apply_mod_actions(result.actions, tick, now);
                }
                result.damage
            }
            Some(Err(error)) => {
                self.log_mod_fault(gen_id, "on_damage", error);
                scaled
            }
            None => scaled,
        }
    }

    pub(super) fn mod_on_heal(
        &mut self,
        healer: u32,
        target: u32,
        amount: f64,
        tick: u32,
        now: Duration,
    ) {
        if amount <= 0.0 || !amount.is_finite() {
            return;
        }
        let Some((gen_id, _)) = self.sync_room_vm_generation() else {
            return;
        };
        if self.hook_silenced("on_heal")
            || !self
                .mod_state
                .room_script
                .as_ref()
                .is_some_and(ModScript::has_on_heal)
        {
            return;
        }
        let (ctx, world_view) = self.build_mod_context_and_world(tick, now, Some(healer));
        let event = ModHealEvent {
            healer,
            target,
            amount,
        };
        let outcome =
            self.with_room_script(|script, _| script.on_heal_with_world(&ctx, &event, &world_view));
        if let Some(res) = outcome {
            match res {
                Ok(actions) => self.apply_mod_actions(actions, tick, now),
                Err(error) => self.log_mod_fault(gen_id, "on_heal", error),
            }
        }
    }

    pub(super) fn mod_on_kill(
        &mut self,
        killer: u32,
        victim: u32,
        headshot: bool,
        tick: u32,
        now: Duration,
    ) {
        let Some((gen_id, _)) = self.sync_room_vm_generation() else {
            return;
        };
        if self.hook_silenced("on_kill") {
            return;
        }
        let (ctx, world_view) = self.build_mod_context_and_world(tick, now, Some(killer));
        let event = ModKillEvent {
            killer,
            victim,
            headshot,
        };
        let outcome =
            self.with_room_script(|script, _| script.on_kill_with_world(&ctx, &event, &world_view));
        if let Some(res) = outcome {
            match res {
                Ok(actions) => self.apply_mod_actions(actions, tick, now),
                Err(error) => self.log_mod_fault(gen_id, "on_kill", error),
            }
        }
    }

    /// Dispatch a live in-game chat message into the room's isolated Lua VM and return
    /// all chat replies generated by `on_chat` (and any actions it returned).
    pub(crate) fn mod_dispatch_chat_from(
        &mut self,
        sender_owner: Option<u32>,
        sender_name: &str,
        text: &str,
        tick: u32,
        now: Duration,
    ) -> Vec<String> {
        let Some((gen_id, _)) = self.sync_room_vm_generation() else {
            return Vec::new();
        };
        if self.hook_silenced("on_chat") {
            return Vec::new();
        }
        let sender_entity = sender_owner
            .and_then(|owner| self.players_by_owner.get(&owner).map(|h| h.id.entity))
            .or_else(|| {
                self.players_by_wire.iter().find_map(|(&wire, &ecs)| {
                    self.world
                        .get::<PlayerBodyState>(ecs)
                        .filter(|s| s.name.eq_ignore_ascii_case(sender_name))
                        .map(|_| wire)
                })
            })
            .or_else(|| self.players_by_wire.keys().next().copied());
        let (ctx, world_view) = self.build_mod_context_and_world(tick, now, sender_entity);
        let outcome = self.with_room_script(|script, _| {
            script.on_chat_with_world(&ctx, sender_name, text, &world_view)
        });
        if let Some(res) = outcome {
            match res {
                Ok(actions) => self.apply_mod_actions(actions, tick, now),
                Err(error) => self.log_mod_fault(gen_id, "on_chat", error),
            }
        }
        self.drain_mod_chat_replies()
    }

    #[cfg(test)]
    pub(crate) fn mod_dispatch_chat(&mut self, sender: &str, text: &str, tick: u32, now: Duration) {
        let _ = self.mod_dispatch_chat_from(None, sender, text, tick, now);
    }

    pub(super) fn step_mod_host(
        &mut self,
        button_edges: &[(u32, u32, u32)],
        tick: u32,
        now: Duration,
    ) {
        let Some((gen_id, reloaded)) = self.sync_room_vm_generation() else {
            return;
        };

        // 1. Fire `on_room_start` on initial attach or live hot-reload.
        if reloaded || self.mod_state.started_generation != gen_id {
            self.mod_state.started_generation = gen_id;
            let (ctx, world_view) = self.build_mod_context_and_world(tick, now, None);
            let outcome = self
                .with_room_script(|script, _| script.on_room_start_with_world(&ctx, &world_view));
            if let Some(res) = outcome {
                match res {
                    Ok(actions) => self.apply_mod_actions(actions, tick, now),
                    Err(error) => self.log_mod_fault(gen_id, "on_room_start", error),
                }
            }
        } else {
            self.sync_player_mod_state();
        }

        // 2. Step Workshop bot AI overrides (`dance`, `follow`, `idle`) and custom projectiles.
        self.step_mod_bot_ai(tick);
        self.step_custom_projectiles(tick, now);

        // 3. Dispatch rising & falling button edges to `on_button`.
        if !self.hook_silenced("on_button") && !button_edges.is_empty() {
            for &(wire, pressed_mask, released_mask) in button_edges {
                if pressed_mask == 0 && released_mask == 0 {
                    continue;
                }
                let Some(snapshot) = self.snapshot_player_for_mod(wire) else {
                    continue;
                };
                let (ctx, world_view) = self.build_mod_context_and_world(tick, now, Some(wire));
                for &(bit, name) in MOD_BUTTON_MAP {
                    if pressed_mask & bit != 0 {
                        let outcome = self.with_room_script(|script, _| {
                            script.on_button_edge_with_world(
                                &ctx,
                                &snapshot,
                                name,
                                true,
                                &world_view,
                            )
                        });
                        if let Some(res) = outcome {
                            match res {
                                Ok(actions) => self.apply_mod_actions(actions, tick, now),
                                Err(error) => self.log_mod_fault(gen_id, "on_button", error),
                            }
                        }
                    }
                    if released_mask & bit != 0 {
                        let outcome = self.with_room_script(|script, _| {
                            script.on_button_edge_with_world(
                                &ctx,
                                &snapshot,
                                name,
                                false,
                                &world_view,
                            )
                        });
                        if let Some(res) = outcome {
                            match res {
                                Ok(actions) => self.apply_mod_actions(actions, tick, now),
                                Err(error) => self.log_mod_fault(gen_id, "on_button", error),
                            }
                        }
                    }
                }
            }
        }

        // 4. Dispatch per-tick hook `on_tick` (only builds context when script defines `on_tick`).
        if !self.hook_silenced("on_tick")
            && self
                .mod_state
                .room_script
                .as_ref()
                .is_some_and(ModScript::has_on_tick)
        {
            let dt = TICK_PERIOD.as_secs_f64() * self.mod_state.active_rules.time_scale;
            let (ctx, world_view) = self.build_mod_context_and_world(tick, now, None);
            let outcome =
                self.with_room_script(|script, _| script.on_tick_with_world(&ctx, dt, &world_view));
            if let Some(res) = outcome {
                match res {
                    Ok(actions) => self.apply_mod_actions(actions, tick, now),
                    Err(error) => self.log_mod_fault(gen_id, "on_tick", error),
                }
            }
        }
    }

    fn step_mod_bot_ai(&mut self, tick: u32) {
        if self.mod_state.bot_ai_modes.is_empty() {
            return;
        }
        let speed_base =
            self.mod_state.active_rules.speed_scale * self.mod_state.active_rules.time_scale;
        let player_positions: Vec<[f64; 3]> = self
            .players_by_wire
            .values()
            .filter_map(|&e| self.world.get::<PlayerBodyState>(e))
            .filter(|p| p.alive())
            .map(|p| p.mover.pose().position)
            .collect();
        let entries: Vec<(u32, BotModAi)> = self
            .mod_state
            .bot_ai_modes
            .iter()
            .map(|(&w, &m)| (w, m))
            .collect();
        let mut expired = Vec::new();
        for (wire, mode) in entries {
            let Some(&ecs_entity) = self.by_wire.get(&wire) else {
                expired.push(wire);
                continue;
            };
            let speed_scale = self
                .mod_state
                .player_speed_overrides
                .get(&wire)
                .copied()
                .unwrap_or(speed_base);
            let Some(mut state) = self.world.get_mut::<BotState>(ecs_entity) else {
                continue;
            };
            match mode {
                BotModAi::Idle => {
                    state.bot.stop_objective(tick);
                    state.bot.velocity = [0.0; 3];
                }
                BotModAi::Dance { base_y, until_tick } => {
                    if until_tick.is_some_and(|until| tick >= until) {
                        state.bot.position[1] = base_y;
                        expired.push(wire);
                    } else {
                        state.bot.step_mod_dance(tick, base_y);
                    }
                }
                BotModAi::Follow => {
                    if let Some(&target) = player_positions.iter().min_by(|a, b| {
                        let da = (a[0] - state.bot.position[0]).hypot(a[2] - state.bot.position[2]);
                        let db = (b[0] - state.bot.position[0]).hypot(b[2] - state.bot.position[2]);
                        da.total_cmp(&db)
                    }) {
                        state.bot.step_mod_follow(tick, target, speed_scale);
                    }
                }
            }
        }
        for wire in expired {
            self.mod_state.bot_ai_modes.remove(&wire);
        }
    }

    fn step_custom_projectiles(&mut self, tick: u32, now: Duration) {
        if self.mod_state.custom_projectiles.is_empty() {
            return;
        }
        let dt = TICK_PERIOD.as_secs_f64() * self.mod_state.active_rules.time_scale;
        let rays = self.mod_state.rays.clone();
        let mut active = std::mem::take(&mut self.mod_state.custom_projectiles);
        let mut survivors = Vec::with_capacity(active.len());
        let mut impacts: Vec<(u32, [f64; 3], f64, f64)> = Vec::new();

        for mut proj in active.drain(..) {
            if proj.ttl_ticks == 0 {
                continue;
            }
            proj.ttl_ticks -= 1;
            proj.velocity[1] -= proj.gravity * dt;
            let step = [
                proj.velocity[0] * dt,
                proj.velocity[1] * dt,
                proj.velocity[2] * dt,
            ];
            let step_len = (step[0] * step[0] + step[1] * step[1] + step[2] * step[2]).sqrt();
            let mut hit_pos = None;
            if step_len > 1e-6 {
                let dir = [step[0] / step_len, step[1] / step_len, step[2] / step_len];
                if let Some(rays) = rays.as_deref() {
                    if let Some(hit) = rays
                        .cast_ray(proj.position, dir, step_len, 0x0018)
                        .ok()
                        .flatten()
                    {
                        hit_pos = Some(hit.point);
                    }
                }
            }
            let next_pos = hit_pos.unwrap_or([
                proj.position[0] + step[0],
                proj.position[1] + step[1],
                proj.position[2] + step[2],
            ]);
            if hit_pos.is_none() {
                // Direct proximity hit against any living actor other than source
                let hit_r = proj.radius.clamp(0.45, 1.0);
                let hit_r2 = hit_r * hit_r;
                let direct = self
                    .players_by_wire
                    .iter()
                    .filter(|&(&w, _)| w != proj.source)
                    .filter_map(|(_, &e)| self.world.get::<PlayerBodyState>(e))
                    .filter(|s| s.alive())
                    .any(|s| {
                        let p = s.mover.pose().position;
                        let dx = p[0] - next_pos[0];
                        let dy = (p[1] + 0.9) - next_pos[1];
                        let dz = p[2] - next_pos[2];
                        dx * dx + dy * dy + dz * dz <= hit_r2
                    })
                    || self
                        .by_wire
                        .iter()
                        .filter(|&(&w, _)| w != proj.source)
                        .filter_map(|(_, &e)| self.world.get::<BotState>(e))
                        .filter(|s| !s.bot.dead)
                        .any(|s| {
                            let p = s.bot.position;
                            let dx = p[0] - next_pos[0];
                            let dy = (p[1] + 0.9) - next_pos[1];
                            let dz = p[2] - next_pos[2];
                            dx * dx + dy * dy + dz * dz <= hit_r2
                        });
                if direct {
                    hit_pos = Some(next_pos);
                }
            }
            if let Some(impact_at) = hit_pos {
                impacts.push((proj.source, impact_at, proj.damage, proj.radius));
            } else {
                proj.position = next_pos;
                survivors.push(proj);
            }
        }
        self.mod_state.custom_projectiles = survivors;

        for (source, center, damage, radius) in impacts {
            let r2 = radius * radius;
            let mut victims = Vec::new();
            for (&wire, &e) in &self.players_by_wire {
                if wire == source {
                    continue;
                }
                if let Some(s) = self.world.get::<PlayerBodyState>(e) {
                    if s.alive() {
                        let p = s.mover.pose().position;
                        let dx = p[0] - center[0];
                        let dy = (p[1] + 0.9) - center[1];
                        let dz = p[2] - center[2];
                        if dx * dx + dy * dy + dz * dz <= r2 {
                            victims.push(wire);
                        }
                    }
                }
            }
            for (&wire, &e) in &self.by_wire {
                if wire == source {
                    continue;
                }
                if let Some(s) = self.world.get::<BotState>(e) {
                    if !s.bot.dead {
                        let p = s.bot.position;
                        let dx = p[0] - center[0];
                        let dy = (p[1] + 0.9) - center[1];
                        let dz = p[2] - center[2];
                        if dx * dx + dy * dy + dz * dz <= r2 {
                            victims.push(wire);
                        }
                    }
                }
            }
            for victim in victims {
                self.apply_mod_direct_damage(victim, source, damage, tick, now);
            }
        }
    }

    fn apply_mod_actions(&mut self, actions: Vec<ModAction>, tick: u32, now: Duration) {
        for action in actions {
            match action {
                ModAction::Heal { entity, amount } => {
                    if amount > 0.0 && amount.is_finite() {
                        let scaled = amount * self.mod_healing_scale_for(entity, entity);
                        self.apply_mod_heal(entity, scaled);
                    }
                }
                ModAction::Damage { entity, amount } => {
                    if amount > 0.0 && amount.is_finite() {
                        self.apply_mod_direct_damage(entity, 0, amount, tick, now);
                    }
                }
                ModAction::SetHealth { entity, health } => {
                    if health >= 0.0 && health.is_finite() {
                        if let Some(current) = self.actor_current_health(entity) {
                            let delta = health - current;
                            if delta > 1e-6 {
                                self.apply_mod_heal(entity, delta);
                            } else if delta < -1e-6 {
                                self.apply_mod_direct_damage(entity, 0, -delta, tick, now);
                            }
                        }
                    }
                }
                ModAction::Kill { entity } => {
                    self.apply_mod_direct_damage(entity, 0, 100_000.0, tick, now);
                }
                ModAction::Respawn { entity } => {
                    if let Some(id) = self.player_handle_for_wire(entity) {
                        if let Some(state) = self.player_body_mut(id) {
                            state.respawn_at = Some(now);
                        }
                    } else if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<BotState>(ecs_entity) {
                            state.respawn_at = Some(now);
                        }
                    }
                }
                ModAction::AddHealthPool {
                    entity,
                    kind,
                    amount,
                } => {
                    if !amount.is_finite() || amount <= 0.0 {
                        continue;
                    }
                    let pool_id = 0x0d80_7700_0000_0000_u64 | u64::from(tick & 0xffff);
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            let state = state.into_inner();
                            if state.alive() {
                                if let Some(runtime) = state.runtime.as_deref_mut() {
                                    // Temporary shields replicate via the native named shield slot.
                                    let _ = runtime;
                                }
                                // Heal + add temporary shield pool when vitals support it.
                                let _ = kind;
                                state.heal(amount);
                                if let Some(component) = state.take_health_update() {
                                    self.scripted_health.push(EntityUpdate {
                                        entity,
                                        operation: Some(Operation::Update(vec![component])),
                                        ..Default::default()
                                    });
                                }
                            }
                        }
                    } else if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        if let Some(state) = self.world.get_mut::<BotState>(ecs_entity) {
                            let state = state.into_inner();
                            if !state.bot.dead {
                                if kind.eq_ignore_ascii_case("shield") {
                                    let _ =
                                        state.vitals.add_temporary_shield(pool_id, amount, true);
                                } else {
                                    state.vitals.heal(amount);
                                }
                                self.scripted_health.push(bot_health_update(state, false));
                            }
                        }
                    }
                }
                ModAction::ApplyImpulse { entity, velocity } => {
                    let change = VelocityChange::Impulse(velocity);
                    if !change.valid() {
                        continue;
                    }
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            state.mover.queue_velocity_change(tick, change);
                        }
                    } else if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<BotState>(ecs_entity) {
                            state.bot.queue_velocity_changes(vec![(tick, change)]);
                        }
                    }
                }
                ModAction::SetVelocity { entity, velocity } => {
                    if !velocity.iter().all(|v| v.is_finite()) {
                        continue;
                    }
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            let cur = state.mover.pose().velocity;
                            let delta = [
                                velocity[0] - cur[0],
                                velocity[1] - cur[1],
                                velocity[2] - cur[2],
                            ];
                            let change = VelocityChange::Delta(delta);
                            if change.valid() {
                                state.mover.queue_velocity_change(tick, change);
                            }
                        }
                    } else if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<BotState>(ecs_entity) {
                            state.bot.set_mod_velocity(velocity, tick);
                        }
                    }
                }
                ModAction::Teleport { entity, position } => {
                    let Some(reloc) = Relocation::new(position) else {
                        continue;
                    };
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            state
                                .mover
                                .force_scripted_motion(tick, ScriptedMotion::Teleport(reloc));
                        }
                    } else if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<BotState>(ecs_entity) {
                            state.bot.position = position;
                        }
                    }
                }
                ModAction::SetFacing { entity, yaw, pitch } => {
                    if !yaw.is_finite() || !pitch.is_finite() {
                        continue;
                    }
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            let y = ((yaw.rem_euclid(360.0) * 65536.0 / 360.0).round() as i32)
                                as i16 as i32;
                            let p = (pitch.clamp(-89.0, 89.0) * 65536.0 / 360.0).round() as i32;
                            state.mover.set_mod_facing(y, p);
                        }
                    } else if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<BotState>(ecs_entity) {
                            state.bot.set_mod_facing(yaw, pitch, tick);
                        }
                    }
                }
                ModAction::SetSpeedScale { entity, scale } => {
                    if scale.is_finite() && scale > 0.0 {
                        self.mod_state.player_speed_overrides.insert(entity, scale);
                        self.sync_player_mod_state();
                    }
                }
                ModAction::SetGravityScale { entity, scale } => {
                    if scale.is_finite() && scale > 0.0 {
                        self.mod_state
                            .player_gravity_overrides
                            .insert(entity, scale);
                        self.sync_player_mod_state();
                    }
                }
                ModAction::SetDamageScale { entity, scale } => {
                    if scale.is_finite() && scale >= 0.0 {
                        self.mod_state.actor_damage_scales.insert(entity, scale);
                    }
                }
                ModAction::SetDamageTakenScale { entity, scale } => {
                    if scale.is_finite() && scale >= 0.0 {
                        self.mod_state
                            .actor_damage_taken_scales
                            .insert(entity, scale);
                    }
                }
                ModAction::SetHealingScale { entity, scale } => {
                    if scale.is_finite() && scale >= 0.0 {
                        self.mod_state.actor_healing_scales.insert(entity, scale);
                    }
                }
                ModAction::PlayEmote { entity, slot } => {
                    let guid = EMOTE_WHEEL_ACTIONS[usize::from(slot.min(3))];
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        let triggered = self
                            .world
                            .get_mut::<PlayerBodyState>(ecs_entity)
                            .and_then(|mut s| {
                                let rt = s.runtime.as_deref_mut()?;
                                let _ = rt.queue_wheel_selection(
                                    ClientWheelAction::synthetic(guid),
                                    None,
                                );
                                Some(())
                            })
                            .is_some();
                        if triggered && !self.hook_silenced("on_emote") {
                            if let Some(snap) = self.snapshot_player_for_mod(entity) {
                                let (ctx, world_view) =
                                    self.build_mod_context_and_world(tick, now, Some(entity));
                                let gen_id = self.mod_state.last_generation;
                                let outcome = self.with_room_script(|script, _| {
                                    script.on_emote_with_world(&ctx, &snap, slot, &world_view)
                                });
                                if let Some(res) = outcome {
                                    match res {
                                        Ok(follow) => self.apply_mod_actions(follow, tick, now),
                                        Err(e) => self.log_mod_fault(gen_id, "on_emote", e),
                                    }
                                }
                            }
                        }
                    } else if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        if let Some(state) = self.world.get::<BotState>(ecs_entity) {
                            let base_y = state.bot.position[1];
                            self.mod_state.bot_ai_modes.insert(
                                entity,
                                BotModAi::Dance {
                                    base_y,
                                    until_tick: Some(tick.saturating_add(280)), // ~4.5s dance
                                },
                            );
                        }
                    }
                }
                ModAction::PlayVoiceline { entity, slot } => {
                    let guid = VOICELINE_WHEEL_ACTIONS[usize::from(slot.min(3))];
                    self.trigger_player_wheel_action(entity, guid);
                }
                ModAction::PlaceSpray { entity, slot } => {
                    let guid = SPRAY_WHEEL_ACTIONS[usize::from(slot.min(3))];
                    self.trigger_player_wheel_action(entity, guid);
                }
                ModAction::Communication { entity, kind } => {
                    let guid = match kind.to_ascii_lowercase().as_str() {
                        "need_healing" | "heal" => NEED_HEALING_WHEEL_ACTION,
                        "thanks" | "thank_you" => THANKS_WHEEL_ACTION,
                        _ => GREETING_WHEEL_ACTION,
                    };
                    self.trigger_player_wheel_action(entity, guid);
                }
                ModAction::PlayVoiceCue { entity, stimulus } => {
                    if stimulus == 0 {
                        continue;
                    }
                    let Some(source) = self.sample_health_body(entity) else {
                        continue;
                    };
                    let source_id = source.identity();
                    let listeners: Vec<(u32, BodyId)> = self
                        .players_by_wire
                        .values()
                        .filter_map(|&e| self.world.get::<PlayerBodyState>(e))
                        .filter(|p| p.alive())
                        .map(|p| (p.owner, p.id))
                        .collect();
                    for (owner, listener) in listeners {
                        self.player_cues.push(PlayerRoomCue {
                            owner,
                            listener,
                            source: source_id,
                            notice: CombatNotice::Voice {
                                anchor: entity,
                                speaker: entity,
                                listener: listener.entity,
                                stimulus,
                                line: 0,
                            },
                        });
                    }
                }
                ModAction::SetHero { entity, hero } => {
                    let hero_guid = self
                        .mod_state
                        .heroes
                        .as_deref()
                        .and_then(|cat| cat.find_hero(&hero))
                        .map(|h| h.guid)
                        .or_else(|| crate::accounts::parse_guid(&hero));
                    if let (Some(hero_guid), Some(&ecs_entity)) =
                        (hero_guid, self.players_by_wire.get(&entity))
                    {
                        if let Some(state) = self.world.get::<PlayerBodyState>(ecs_entity) {
                            self.mod_state
                                .pending_hero_switches
                                .insert(state.owner, hero_guid);
                        }
                    }
                }
                ModAction::SetStatus {
                    entity,
                    status,
                    duration,
                } => {
                    let ticks = (duration / TICK_PERIOD.as_secs_f64()).round().max(1.0) as u32;
                    let until = tick.saturating_add(ticks);
                    match status.to_ascii_lowercase().as_str() {
                        "stun" | "stunned" => self.apply_actor_stun(entity, tick, ticks),
                        "sleep" | "asleep" => {
                            self.apply_actor_stun(entity, tick, ticks);
                            let entry = self.actor_statuses.entry(entity).or_default();
                            entry.sleep_until =
                                Some(entry.sleep_until.map_or(until, |cur| cur.max(until)));
                        }
                        "freeze" | "frozen" => {
                            self.apply_actor_stun(entity, tick, ticks);
                            let entry = self.actor_statuses.entry(entity).or_default();
                            entry.chill_stacks = 100;
                            entry.chill_last_tick = tick;
                        }
                        "anti_heal" | "antiheal" => {
                            let entry = self.actor_statuses.entry(entity).or_default();
                            entry.anti_heal_until =
                                Some(entry.anti_heal_until.map_or(until, |cur| cur.max(until)));
                        }
                        "heal_boost" | "healboost" => {
                            let entry = self.actor_statuses.entry(entity).or_default();
                            entry.heal_boost_until =
                                Some(entry.heal_boost_until.map_or(until, |cur| cur.max(until)));
                        }
                        "hack" | "hacked" => {
                            let entry = self.actor_statuses.entry(entity).or_default();
                            entry.hacked_until =
                                Some(entry.hacked_until.map_or(until, |cur| cur.max(until)));
                        }
                        "invulnerable" | "invuln" => {
                            let entry = self.actor_statuses.entry(entity).or_default();
                            entry.invulnerable_until =
                                Some(entry.invulnerable_until.map_or(until, |cur| cur.max(until)));
                        }
                        "cc_immune" | "fortify" => {
                            let entry = self.actor_statuses.entry(entity).or_default();
                            entry.cc_immune_until =
                                Some(entry.cc_immune_until.map_or(until, |cur| cur.max(until)));
                        }
                        _ => {}
                    }
                }
                ModAction::ClearStatus { entity, status } => {
                    let name = status.to_ascii_lowercase();
                    if name == "all" {
                        self.actor_statuses.remove(&entity);
                        self.clear_actor_stun_controls(entity);
                    } else if let Some(entry) = self.actor_statuses.get_mut(&entity) {
                        match name.as_str() {
                            "stun" | "stunned" => {
                                entry.stunned_until = None;
                                self.clear_actor_stun_controls(entity);
                            }
                            "sleep" | "asleep" => {
                                entry.sleep_until = None;
                                entry.stunned_until = None;
                                self.clear_actor_stun_controls(entity);
                            }
                            "freeze" | "frozen" => {
                                entry.chill_stacks = 0;
                                entry.stunned_until = None;
                                self.clear_actor_stun_controls(entity);
                            }
                            "anti_heal" | "antiheal" => entry.anti_heal_until = None,
                            "heal_boost" | "healboost" => entry.heal_boost_until = None,
                            "hack" | "hacked" => entry.hacked_until = None,
                            "invulnerable" | "invuln" => entry.invulnerable_until = None,
                            "cc_immune" | "fortify" => entry.cc_immune_until = None,
                            _ => {}
                        }
                    }
                }
                ModAction::SetUltCharge { entity, percent } => {
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            if let Some(rt) = state.runtime.as_deref_mut() {
                                let _ = rt.set_ult_charge_percent(percent);
                            }
                        }
                    }
                }
                ModAction::AddUltCharge { entity, amount } => {
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            if let Some(rt) = state.runtime.as_deref_mut() {
                                let _ = rt.add_ult_charge_points(amount);
                            }
                        }
                    }
                }
                ModAction::ResetCooldowns { entity } => {
                    if let Some(&ecs_entity) = self.players_by_wire.get(&entity) {
                        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                            if let Some(rt) = state.runtime.as_deref_mut() {
                                let _ = rt.reset_all_cooldowns();
                            }
                        }
                    }
                }
                ModAction::SpawnBot {
                    hero,
                    team,
                    position,
                    name,
                } => {
                    self.spawn_mod_bot(&hero, team, position, name, tick);
                }
                ModAction::DespawnBot { entity } => {
                    self.despawn_mod_bot(entity);
                }
                ModAction::SetBotAi { entity, mode } => {
                    if let Some(&ecs_entity) = self.by_wire.get(&entity) {
                        let base_y = self
                            .world
                            .get::<BotState>(ecs_entity)
                            .map_or(0.0, |s| s.bot.position[1]);
                        match mode.to_ascii_lowercase().as_str() {
                            "idle" => {
                                self.mod_state.bot_ai_modes.insert(entity, BotModAi::Idle);
                            }
                            "dance" | "emote" => {
                                self.mod_state.bot_ai_modes.insert(
                                    entity,
                                    BotModAi::Dance {
                                        base_y,
                                        until_tick: None,
                                    },
                                );
                            }
                            "follow" => {
                                self.mod_state.bot_ai_modes.insert(entity, BotModAi::Follow);
                            }
                            _ => {
                                self.mod_state.bot_ai_modes.remove(&entity);
                            }
                        }
                    }
                }
                ModAction::SpawnProjectile {
                    source,
                    origin,
                    direction,
                    speed,
                    damage,
                    radius,
                    gravity,
                } => {
                    if self.mod_state.custom_projectiles.len() < MAX_CUSTOM_PROJECTILES {
                        let len = (direction[0] * direction[0]
                            + direction[1] * direction[1]
                            + direction[2] * direction[2])
                            .sqrt();
                        if len.is_finite() && len > 1e-6 {
                            let velocity = [
                                direction[0] / len * speed,
                                direction[1] / len * speed,
                                direction[2] / len * speed,
                            ];
                            self.mod_state.custom_projectiles.push(CustomProjectile {
                                source,
                                position: origin,
                                velocity,
                                damage,
                                radius,
                                gravity,
                                ttl_ticks: 300, // 5 seconds max flight
                            });
                        }
                    }
                }
                ModAction::SetTimeScale { scale } => {
                    if scale.is_finite() && scale > 0.0 {
                        self.mod_state.active_rules.time_scale = scale.clamp(0.1, 3.0);
                        self.sync_player_mod_state();
                    }
                }
                ModAction::ResetInteractiveProp { prop_id } => {
                    self.interactive_props_mut().reset_prop(prop_id);
                }
                ModAction::ApplyPropImpulse { prop_id, velocity } => {
                    if let Some(prop) = self.interactive_props_mut().props.get_mut(&prop_id) {
                        prop.velocity[0] += velocity[0];
                        prop.velocity[1] += velocity[1];
                        prop.velocity[2] += velocity[2];
                    }
                }
                ModAction::SetRoomRule { key, value } => {
                    if !value.is_finite() {
                        continue;
                    }
                    match key.as_str() {
                        "damage_scale" if value >= 0.0 => {
                            self.mod_state.active_rules.damage_scale = value;
                        }
                        "healing_scale" if value >= 0.0 => {
                            self.mod_state.active_rules.healing_scale = value;
                        }
                        "gravity_scale" if value > 0.0 => {
                            self.mod_state.active_rules.gravity_scale = value;
                            self.sync_player_mod_state();
                        }
                        "speed_scale" if value > 0.0 => {
                            self.mod_state.active_rules.speed_scale = value;
                            self.sync_player_mod_state();
                        }
                        "ult_charge_scale" if value >= 0.0 => {
                            self.mod_state.active_rules.ult_charge_scale = value;
                            self.sync_player_mod_state();
                        }
                        "cooldown_scale" if value > 0.0 => {
                            self.mod_state.active_rules.cooldown_scale = value;
                            self.sync_player_mod_state();
                        }
                        "time_scale" if value > 0.0 => {
                            self.mod_state.active_rules.time_scale = value.clamp(0.1, 3.0);
                            self.sync_player_mod_state();
                        }
                        "friendly_fire" => {
                            self.mod_state.active_rules.friendly_fire = value != 0.0;
                        }
                        _ => {}
                    }
                }
                ModAction::BroadcastChat { text }
                | ModAction::SendChat { text, .. }
                | ModAction::Announce { text } => {
                    eprintln!("[mod] {text}");
                    if self.mod_state.broadcast_log.len() >= MAX_BROADCAST_LOG {
                        self.mod_state.broadcast_log.remove(0);
                    }
                    self.mod_state.broadcast_log.push(text.clone());
                    if self.mod_state.pending_chat_replies.len() >= MAX_PENDING_CHAT_REPLIES {
                        self.mod_state.pending_chat_replies.pop_front();
                    }
                    self.mod_state.pending_chat_replies.push_back(text);
                }
            }
        }
    }

    fn trigger_player_wheel_action(&mut self, wire: u32, action_guid: u64) {
        if let Some(&ecs_entity) = self.players_by_wire.get(&wire) {
            if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(ecs_entity) {
                if state.alive() {
                    if let Some(rt) = state.runtime.as_deref_mut() {
                        let _ = rt
                            .queue_wheel_selection(ClientWheelAction::synthetic(action_guid), None);
                    }
                }
            }
        }
    }

    fn clear_actor_stun_controls(&mut self, wire: u32) {
        if let Ok(Some(state)) = self.state_mut(wire) {
            state.bot.clear_stun();
        } else if let Some(id) = self.player_handle_for_wire(wire) {
            if let Some(player) = self.player_body_mut(id) {
                player.mover.clear_stun();
            }
        }
    }

    fn spawn_mod_bot(
        &mut self,
        hero_query: &str,
        team: u8,
        position: [f64; 3],
        name: Option<String>,
        tick: u32,
    ) {
        if self.mod_state.spawned_mod_bots.len() >= MAX_MOD_SPAWNED_BOTS {
            return;
        }
        let resolved = self
            .mod_state
            .heroes
            .as_deref()
            .and_then(|cat| cat.find_hero(hero_query))
            .map(|h| (h.guid, h.body, h.name.clone()));
        let (hero, body, default_name) = match resolved {
            Some(found) => found,
            None => {
                // Fallback to the first seated bot's template in this room so even rooms without
                // HeroCatalog can spawn bots cleanly without hardcoding any GUID.
                let Some(template) = self
                    .by_wire
                    .values()
                    .find_map(|&e| self.world.get::<BotState>(e))
                    .map(|s| (s.bot.spawn.hero, s.bot.spawn.body, s.bot.spawn.name.clone()))
                else {
                    return;
                };
                template
            }
        };
        let wire = self.next_bot;
        if !(BOT_ENTITY..0xd000_0000).contains(&wire) || self.by_wire.contains_key(&wire) {
            return;
        }
        self.next_bot = wire.saturating_add(1);
        let display_name = name.unwrap_or(default_name);
        let health = 200.0;
        let bot = Bot::mod_spawned(
            wire,
            hero,
            body,
            display_name,
            team.min(1),
            health,
            position,
            0,
            tick,
        );
        let vitals = Vitals::new(health, 0.0, 0.0);
        if let Ok(update) = bot_create(&bot, Some(&vitals)) {
            let state = BotState {
                bot,
                vitals,
                history: crate::game_hitscan::TargetHistory::new(None),
                respawn_at: None,
                enemy: None,
                stock_script: None,
                base_target_tags: Default::default(),
            };
            let entity = self.world.spawn(state).id();
            self.by_wire.insert(wire, entity);
            self.created.push(update);
            self.mod_state.spawned_mod_bots.push(wire);
        }
    }

    fn despawn_mod_bot(&mut self, wire: u32) {
        if let Some(ecs_entity) = self.by_wire.shift_remove(&wire) {
            self.world.despawn(ecs_entity);
            self.actor_statuses.remove(&wire);
            self.mod_state.bot_ai_modes.remove(&wire);
            self.mod_state.spawned_mod_bots.retain(|&w| w != wire);
            Arc::make_mut(&mut self.retired_entity_ids).insert(wire);
            self.retired_enemies.push(wire);
        }
    }

    fn actor_current_health(&self, wire: u32) -> Option<f64> {
        if let Some(&entity) = self.players_by_wire.get(&wire) {
            return self
                .world
                .get::<PlayerBodyState>(entity)
                .and_then(|state| state.vitals())
                .map(crate::vitals::Vitals::total);
        }
        if let Some(&entity) = self.by_wire.get(&wire) {
            return self
                .world
                .get::<BotState>(entity)
                .map(|state| state.vitals.total());
        }
        None
    }

    fn apply_mod_heal(&mut self, wire: u32, amount: f64) {
        if let Some(&entity) = self.players_by_wire.get(&wire) {
            if let Some(state) = self.world.get_mut::<PlayerBodyState>(entity) {
                let state = state.into_inner();
                if state.alive() && state.vitals().is_some() {
                    let healed = state.heal(amount);
                    if healed > 0.0 {
                        if let Some(component) = state.take_health_update() {
                            self.scripted_health.push(EntityUpdate {
                                entity: wire,
                                operation: Some(Operation::Update(vec![component])),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
            return;
        }
        if let Some(&entity) = self.by_wire.get(&wire) {
            if let Some(state) = self.world.get_mut::<BotState>(entity) {
                let state = state.into_inner();
                if !state.bot.dead {
                    let healed = state.vitals.heal(amount);
                    if healed > 0.0 {
                        if let Some(script) = &mut state.stock_script {
                            script.on_heal(state.vitals.total());
                        }
                        self.scripted_health.push(bot_health_update(state, false));
                    }
                }
            }
        }
    }

    fn apply_mod_direct_damage(
        &mut self,
        wire: u32,
        source: u32,
        amount: f64,
        tick: u32,
        now: Duration,
    ) {
        let death = Death {
            time: now.as_secs_f64(),
            frame: tick,
            killer: source,
        };
        if let Some(id) = self.player_handle_for_wire(wire) {
            let victim_team = self.player_body(id).map_or(0, |s| s.team);
            if let Some((owner, outcome, health)) =
                self.damage_player_body_by_wire(id, amount, death, None)
            {
                self.scripted_health.extend(health);
                if outcome.dealt > 0.0 || outcome.killed {
                    self.record_actor_hurt_voice(wire, false, source, false, outcome.killed, tick);
                }
                if outcome.killed {
                    self.actor_statuses.remove(&wire);
                    self.player_deaths.push(PlayerDeath {
                        owner,
                        body: id,
                        source,
                        killed_by: source != 0 && source != wire,
                    });
                    self.record_enemy_elimination(source, wire, victim_team, tick);
                    self.mod_on_kill(source, wire, false, tick, now);
                }
            }
            return;
        }
        if let Some(&entity) = self.by_wire.get(&wire) {
            let result = self.world.get_mut::<BotState>(entity).and_then(|state| {
                let state = state.into_inner();
                let team = state.bot.spawn.team;
                let dmg = Self::damage_bot_with_hit(state, amount, death, now, false, None)?;
                if dmg.killed && state.respawn_at.is_none() {
                    state.respawn_at = Some(now.saturating_add(BOT_RESPAWN_TIME));
                }
                let update = bot_health_update(state, dmg.killed);
                Some((team, dmg, update))
            });
            if let Some((victim_team, dmg, update)) = result {
                self.scripted_health.push(update);
                if dmg.dealt > 0.0 || dmg.killed {
                    self.record_actor_hurt_voice(wire, true, source, false, dmg.killed, tick);
                }
                if dmg.killed {
                    self.record_enemy_elimination(source, wire, victim_team, tick);
                    self.mod_on_kill(source, wire, false, tick, now);
                }
            }
        }
    }
}
