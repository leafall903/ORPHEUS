//! Match-authoritative direct damage and training bot lifetimes.
//!
//! The projectile and body scripts supply shot events. The match supplies
//! the command and pose at consumption, and owns identity-gated delivery.
//! Hit selection, damage, and respawn order follow ow174/game/combat.py.

use std::{collections::HashMap, io, sync::Arc, time::Duration};

use bevy_ecs::{component::Component, entity::Entity, world::World};
use indexmap::IndexMap;

use crate::{
    collision_index::IndexError,
    commands::Command,
    game_bots::{BOT_ENTITY, BOT_RECORD_DELAY, BOT_RESPAWN_TIME, Bot},
    game_combat_messages::CombatNotice,
    game_heroes::SOLDIER,
    game_hitscan::{HitDefinition, HitscanShot, Impact, TargetHistory, TargetPose, ViewFrame},
    game_projectiles::ProjectileToken,
    game_replication::BodyId,
    junkenstein_messages::DefenderMessage,
    junkenstein_navigation::{
        BossPursuit, BossTarget, GateApproach, JUNKRAT, REAPER, ROADHOG, ReaperExit, SUMMONER,
        WITCH, ZOMBARDIER,
    },
    match_scene::bot_create,
    projectile_flight::{ProjectileBallistics, ProjectileFlight},
    scene_assets::BotQueries,
    script_init::ShotEvent,
    static_ray::{Vec3, norm, segment_separation},
    vitals::{Death, Vitals},
    world::{EntityUpdate, Operation},
};

#[cfg(any(test, feature = "inspector"))]
use crate::game_debug::{Actor, ActorKind};

#[derive(Debug, thiserror::Error)]
pub enum CombatError {
    #[error("bot wire ID {0:08X} is already in use")]
    BotIdInUse(u32),
    #[error("bot wire IDs exhausted")]
    BotIdsExhausted,
    #[error("bot {wire:08X} references missing ECS body {entity:?}")]
    MissingBot { wire: u32, entity: Entity },
    #[error("bot index {wire:08X} references body with wire ID {actual:08X}")]
    BotIdentity { wire: u32, actual: u32 },
    #[error("projectile batch {connection} references missing ECS flight {entity:?}")]
    MissingFlight { connection: u32, entity: Entity },
    #[error(
        "projectile batch {connection} generation {generation} belongs to {expected:08X}, not {actual:08X}"
    )]
    FlightOwnerIdentity {
        connection: u32,
        generation: u64,
        expected: u32,
        actual: u32,
    },
    #[error("bot index slot {0} is unavailable")]
    MissingBotSlot(usize),
    #[error("cannot encode respawn for bot {wire:08X}: {source}")]
    RespawnCreate {
        wire: u32,
        #[source]
        source: io::Error,
    },
    #[error("combat collision query failed: {0}")]
    Collision(#[from] IndexError),
}

pub(crate) fn eye_height(hero: u64, crouched: bool) -> f64 {
    match (crouched, hero) {
        (false, _) => 1.55,
        (true, SOLDIER) => 1.085,
        (true, _) => 1.15,
    }
}

#[derive(Clone, Copy)]
pub(crate) struct PosedHitscan {
    pub source: u32,
    pub origin: [f64; 3],
    pub direction: [f64; 3],
    pub command: Command,
    pub shot_frame: u32,
    pub reach: f64,
    pub hit: HitDefinition,
}

pub(crate) enum PosedShot {
    Hitscan(Box<PosedHitscan>),
    Projectile(Box<PosedProjectile>),
    BioticField(PosedBioticField),
}

pub(crate) const BIOTIC_CANISTER: u64 = 0x0400_0000_0000_072d;
const FIELD_DELAY: u32 = 12;
const FIELD_TICKS: u32 = 313; // ceil(5 seconds / 16 ms).
const FIELD_FADE_TICKS: u32 = 282; // ceil(4.5 seconds / 16 ms).
const HEAL_PER_TICK: f64 = 35.0 * 0.016;

pub(crate) struct PosedBioticField {
    pub shot: ShotEvent,
    pub feet: Vec3,
}

pub(crate) struct BioticField {
    centre: Vec3,
    clock: Option<(u32, u32)>,
}

#[derive(Clone, Copy)]
pub(crate) struct HealingPulse {
    centre: Vec3,
    pub amount: f64,
}

impl HealingPulse {
    pub(crate) fn reaches(self, target: TargetPose) -> bool {
        let [a, b] = target.segment();
        norm(segment_separation(self.centre, a, b)) - target.radius <= 5.0
    }
}

impl BioticField {
    pub(crate) const BOUND: u32 = FIELD_DELAY + FIELD_TICKS;

    pub(crate) fn new([x, y, z]: Vec3) -> Self {
        Self {
            centre: [x, y + 1.0, z],
            clock: None,
        }
    }

    pub(crate) fn presentation(&self, tick: u32) -> Option<bool> {
        let (start, _) = self.clock?;
        let age = tick.checked_sub(start)?;
        (age < FIELD_TICKS).then_some(age >= FIELD_FADE_TICKS)
    }

    pub(crate) fn advance(&mut self, tick: u32) -> (Option<HealingPulse>, bool) {
        let (start, last) = self.clock.get_or_insert_with(|| {
            let start = tick.saturating_add(FIELD_DELAY);
            (start, start)
        });
        let end = start.saturating_add(FIELD_TICKS);
        let through = tick.min(end - 1);
        let steps = through.saturating_sub(*last);
        *last = through.max(*last);
        let pulse = (steps != 0).then_some(HealingPulse {
            centre: self.centre,
            amount: HEAL_PER_TICK * f64::from(steps),
        });
        (pulse, tick >= end)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Stun {
    pub ticks: u32,
}

impl Stun {
    pub(crate) fn from_seconds(seconds: f64) -> Option<Self> {
        let ticks = (seconds as f32 / crate::game_clock::TICK_PERIOD.as_secs_f32()).ceil();
        (seconds.is_finite()
            && seconds > 0.0
            && ticks > 0.0
            && f64::from(ticks) < f64::from(u32::MAX))
        .then_some(Self {
            ticks: ticks as u32,
        })
    }
}

pub(crate) struct PosedProjectile {
    pub stun: Option<Stun>,
    pub gravity: Option<f32>,
    pub expiry_blast: bool,
    pub shot: ShotEvent,
    pub ballistics: ProjectileBallistics,
    pub origin: [f64; 3],
    pub direction: [f64; 3],
}

#[derive(Clone, Copy)]
pub(crate) struct ShotContext {
    pub team: u8,
    pub quanta: u32,
    pub tick: u32,
    pub now: Duration,
}

pub(crate) struct BotHit {
    pub damage: AppliedDamage,
    pub notices: [Option<CombatNotice>; 2],
    pub health: EntityUpdate,
}

#[derive(Clone)]
pub(crate) struct AppliedDamage {
    pub source: u32,
    pub target: u32,
    pub amount: f64,
    pub tags: crate::game_hitscan::DamageTags,
    pub target_flags: Option<u64>,
    pub target_tags: crate::script_init::TargetTagSnapshot,
    pub target_state: crate::script_entity_state::EntityScriptState,
}

#[derive(Clone)]
pub(crate) struct BotReplacement {
    pub retired: u32,
    pub created: EntityUpdate,
}

#[derive(Component)]
struct BotState {
    bot: Bot,
    vitals: Vitals,
    history: TargetHistory,
    respawn_at: Option<Duration>,
    enemy: Option<EnemyState>,
}

struct EnemyState {
    scripts: crate::script_enemy::EnemyScripts,
    binding: crate::script_enemy::EnemyBinding,
    graphs: Arc<crate::script_graph::GraphStore>,
    codecs: Arc<crate::script_codecs::CodecTable>,
    fault: Option<String>,
    gate_approach: Option<GateApproach>,
    objective_mover_attached: bool,
    reaper_exit: Option<ReaperExit>,
    boss_pursuit: Option<Box<BossPursuit>>,
    boss_handoff_complete: bool,
}

impl EnemyState {
    fn select_witch_targets(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        queries: &BotQueries<'_>,
        targets: &[crate::script_enemy::EnemyTarget],
        allies: &HashMap<u32, (bool, f64)>,
    ) -> Result<(), crate::script_init::InitFault> {
        let mut ally = None;
        let mut enemy = None;
        if self.scripts.caster_ready() && !bot.dead {
            for target in targets
                .iter()
                .filter(|target| target.alive && target.entity != bot.entity)
            {
                let distance = norm(std::array::from_fn(|axis| {
                    target.pose.feet[axis] - bot.position[axis]
                }));
                if target.team == self.binding.team && distance <= 40.0 {
                    if let Some(&(favored, fraction)) = allies.get(&target.entity) {
                        let priority = (!favored, fraction, distance, target.entity);
                        if ally.is_none_or(|best| priority < best) {
                            ally = Some(priority);
                        }
                    }
                } else if target.team == 0 && distance <= 60.0 {
                    let priority = (distance, target.entity);
                    if enemy.is_none_or(|best| priority < best) {
                        enemy = Some(priority);
                    }
                }
            }
        }
        let selected = ally
            .map(|(_, _, distance, entity)| (distance, entity, 12.0))
            .or_else(|| enemy.map(|(distance, entity)| (distance, entity, 20.0)))
            .and_then(|(distance, entity, reach)| {
                let target = targets.iter().find(|target| target.entity == entity)?;
                Some((distance, target, reach))
            });
        let aim = selected
            .filter(|&(distance, _, reach)| distance <= reach)
            .and_then(|(distance, target, _)| {
                let [x, y, z] = bot.position;
                let direction =
                    queries.visible_aim([x, y + eye_height(WITCH, false), z], target.pose)?;
                Some((distance, direction))
            });
        if let Some(pursuit) = self.boss_pursuit.as_mut() {
            match (aim, selected) {
                (Some((_, direction)), _) => {
                    pursuit.stop(bot, tick);
                    bot.apply_objective_aim(direction, tick);
                }
                (None, Some((_, target, reach))) => {
                    if !self.objective_mover_attached {
                        self.objective_mover_attached = queries.attach_objective_mover(bot);
                    }
                    if self.objective_mover_attached {
                        queries.pursue_caster_target(pursuit, bot, tick, target, reach);
                    }
                }
                (None, None) => pursuit.stop(bot, tick),
            }
        }
        self.scripts
            .set_witch_target_distance(aim.map(|(distance, _)| distance));
        self.scripts.publish_witch_targets(
            tick,
            enemy.map(|(_, entity)| entity),
            ally.map(|(_, _, _, entity)| entity),
        )
    }

    fn select_summoner_targets(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        queries: &BotQueries<'_>,
        targets: &[crate::script_enemy::EnemyTarget],
    ) -> Result<(), crate::script_init::InitFault> {
        let mut slots = [None; 2];
        let mut beam_rays = Default::default();
        let mut weapon = None;
        let pursuit = self.boss_pursuit.as_mut().expect("Summoner pursuit");
        let ready = self.scripts.caster_ready() && !bot.dead;
        let nearest = targets
            .iter()
            .filter(|target| ready && target.alive && target.team == 0)
            .map(|target| {
                let distance = norm(std::array::from_fn(|axis| {
                    target.pose.feet[axis] - bot.position[axis]
                }));
                (distance, target)
            })
            .min_by(|(a, first), (b, second)| {
                a.total_cmp(b)
                    .then_with(|| first.entity.cmp(&second.entity))
            });
        if let (Some([primary, secondary]), Some((distance, target))) =
            (self.scripts.summoner_reaches(), nearest)
        {
            slots = [primary * 1.25, secondary]
                .map(|reach| (distance <= reach).then_some(target.entity));
            let use_secondary = distance > primary + 1.0;
            let reach = if use_secondary {
                secondary
            } else {
                primary + 1.0
            };
            let [x, y, z] = bot.position;
            let aim = (distance <= reach)
                .then(|| queries.visible_aim([x, y + eye_height(SUMMONER, false), z], target.pose))
                .flatten();
            if let Some(aim) = aim {
                pursuit.stop(bot, tick);
                bot.apply_objective_aim(aim, tick);
                weapon = Some(BossTarget {
                    entity: target.entity,
                    secondary: use_secondary,
                });
                if !use_secondary && let Some(reach) = self.scripts.summoner_beam_reach() {
                    let pose = bot.movement(tick);
                    let forward = crate::game_hitscan::aim_direction(&Command {
                        yaw: pose.yaw,
                        pitch: pose.pitch,
                        ..Command::default()
                    });
                    beam_rays = queries.summoner_beam_contacts(
                        [x, y + eye_height(SUMMONER, false), z],
                        forward,
                        reach,
                        targets,
                    );
                }
            } else {
                if !self.objective_mover_attached {
                    self.objective_mover_attached = queries.attach_objective_mover(bot);
                }
                if self.objective_mover_attached {
                    queries.pursue_caster_target(pursuit, bot, tick, target, primary);
                }
            }
        } else {
            pursuit.stop(bot, tick);
        }
        self.scripts.set_summoner_rays(beam_rays);
        self.scripts.publish_summoner_targets(tick, slots, weapon)
    }

    fn aim_zombardier(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        queries: &BotQueries<'_>,
        targets: &[crate::script_enemy::EnemyTarget],
    ) {
        let target = self
            .scripts
            .grenade_arc(&self.graphs, &self.binding)
            .and_then(|arc| {
                let mut enemies = targets
                    .iter()
                    .filter(|target| target.alive && target.team != self.binding.team)
                    .map(|target| {
                        let distance = norm(std::array::from_fn(|axis| {
                            target.pose.feet[axis] - bot.position[axis]
                        }));
                        (distance, target)
                    })
                    .filter(|(distance, _)| *distance <= arc.range)
                    .collect::<Vec<_>>();
                enemies.sort_unstable_by(|(a, first), (b, second)| {
                    a.total_cmp(b)
                        .then_with(|| first.entity.cmp(&second.entity))
                });
                enemies.into_iter().find_map(|(_, target)| {
                    let direction =
                        arc.aim(bot.position, target.pose, &|origin, direction, length| {
                            queries
                                .hitscan_wall(origin, direction, length)
                                .is_ok_and(|wall| wall.is_none())
                        })?;
                    bot.apply_objective_aim(direction, tick);
                    Some(BossTarget {
                        entity: target.entity,
                        secondary: false,
                    })
                })
            });
        self.scripts.set_weapon_target(target);
    }

    fn approach_gate(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        queries: &BotQueries<'_>,
    ) -> Result<(), crate::script_init::InitFault> {
        let Some(approach) = &mut self.gate_approach else {
            return Ok(());
        };
        let Some(instance) = self.scripts.controller.gate_approach_instance() else {
            bot.stop_objective(tick);
            return Ok(());
        };
        if !self.objective_mover_attached {
            self.objective_mover_attached = queries.attach_objective_mover(bot);
            if !self.objective_mover_attached {
                self.scripts.set_objective_route(false);
                return Ok(());
            }
        }
        if queries.step_gate_approach(approach, bot, tick, self.scripts.movement_speed_scale(tick))
        {
            self.scripts.controller.queue_gate_arrival(tick, instance)?;
        }
        self.scripts.set_objective_route(approach.active());
        Ok(())
    }

    fn advance_boss_weapon(
        &mut self,
        bot: &mut Bot,
        tick: u32,
        queries: &BotQueries<'_>,
        paused: bool,
        defenders: &[crate::script_enemy::EnemyTarget],
    ) -> io::Result<()> {
        if !matches!(self.binding.hero, REAPER | ROADHOG | JUNKRAT) {
            return Ok(());
        }
        let Some(pursuit) = self.boss_pursuit.as_mut() else {
            return Ok(());
        };
        // The completed intro graph is later retired from the body script tree.
        self.boss_handoff_complete =
            self.boss_handoff_complete || self.scripts.boss_combat_handoff(&self.binding);
        let ready = !paused
            && self.boss_handoff_complete
            && self.scripts.cinematic_aim().is_none()
            && self.scripts.facing_direction().is_none();
        let mut target = None;
        if let Some(exit) = &mut self.reaper_exit {
            let can_walk = ready
                && defenders
                    .iter()
                    .any(|target| target.team == 0 && target.alive);
            if queries.step_reaper_exit(exit, bot, tick, can_walk)? {
                self.reaper_exit = None;
            }
        } else if ready {
            if !self.objective_mover_attached {
                self.objective_mover_attached = queries.attach_objective_mover(bot);
            }
            if self.objective_mover_attached {
                let arc = self.scripts.grenade_arc(&self.graphs, &self.binding);
                target = queries.step_boss_pursuit(pursuit, bot, tick, defenders, arc);
            }
        } else {
            pursuit.stop(bot, tick);
        }
        self.scripts.set_weapon_target(target);
        Ok(())
    }
}

impl BotState {
    fn script_state(&self) -> crate::script_entity_state::EntityScriptState {
        use crate::script_entity_state::EntityScriptState;
        let Some(enemy) = &self.enemy else {
            return EntityScriptState::Absent;
        };
        if enemy.fault.is_some() {
            return EntityScriptState::Unavailable;
        }
        enemy
            .scripts
            .body
            .snapshot()
            .map_or(EntityScriptState::Unavailable, |snapshot| {
                EntityScriptState::Running(Arc::new(snapshot.variables.clone()))
            })
    }

    fn new(bot: Bot, replacement: Option<u32>) -> Self {
        Self {
            vitals: Vitals::new(bot.spawn.health, 0.0, 0.0),
            bot,
            history: TargetHistory::new(replacement),
            respawn_at: None,
            enemy: None,
        }
    }
}

#[derive(Component)]
pub(crate) struct AdmittedFlight {
    pub stun: Option<Stun>,
    pub hit_effect: Option<crate::script_init::Site>,
    pub token: ProjectileToken,
    pub flight: ProjectileFlight,
}

pub(crate) enum FlightOutcome {
    Ended {
        token: ProjectileToken,
        travelled: f64,
    },
    Failed(ProjectileToken),
}

struct FlightBatch {
    owner: BodyId,
    order: Vec<Entity>,
}

/// Room-authoritative bot bodies and in-flight projectiles.
pub(crate) struct RoomSimulation {
    world: World,
    // Wire lookup also preserves the authored spawner order across respawns.
    by_wire: IndexMap<u32, Entity>,
    next_bot: u32,
    combat_active: bool,
    created: Vec<EntityUpdate>,
    scene_messages: Vec<crate::script_enemy::SceneMessage>,
    external_targets: Option<Vec<crate::script_enemy::EnemyTarget>>,
    external_enemy_damage: Vec<crate::script_enemy::EnemyDamage>,
    scripted_health: Vec<EntityUpdate>,
    retired_enemies: Vec<u32>,
    retired_entity_ids: Arc<std::collections::BTreeSet<u32>>,
    defender_messages: Vec<DefenderMessage>,
    mode_voice_requests: Vec<crate::junkenstein_voice::VoiceRequest>,
    mode_voice_set: Option<u64>,
    mode_boss_pause: bool,
    spawn_anchors: Option<Arc<crate::scene_assets::SpawnAnchors>>,
    // Handle lists preserve launch order within the worker's ordered peer turns.
    projectiles: HashMap<u32, FlightBatch>,
    enemy_projectiles: Vec<Entity>,
    witch_staff_links: IndexMap<u32, (u32, u16)>,
}

impl RoomSimulation {
    pub(crate) fn create_enemy(
        &mut self,
        spawn: crate::script_enemy::EnemySpawn<'_>,
    ) -> Result<u32, String> {
        use crate::{
            script_enemy::{EnemyBinding, EnemyScripts},
            script_init::CreationKind,
        };
        let crate::script_enemy::EnemySpawn {
            graphs,
            codecs,
            definition,
            request,
            mode,
            mode_variables,
            tick,
            walk,
            gate,
        } = spawn;
        let CreationKind::Hero {
            hero,
            team,
            yaw,
            controller,
            skin,
            ..
        } = &request.kind
        else {
            return Err("scene enemy factory needs an authored hero request".into());
        };
        let pair = graphs
            .enemy_pair(*hero)
            .ok_or("missing authored enemy pair")?;
        if ((crate::npc_catalog::is_junkenstein_boss(*hero) || controller.is_some())
            && *controller != Some(pair.controller))
            || definition.body != pair.body
            || definition.hero != *hero
            || *team > 1
            || !yaw.is_finite()
            || definition.pools.health <= 0.0
            || [
                definition.pools.health,
                definition.pools.armour,
                definition.pools.shields,
                definition.pools.total(),
            ]
            .iter()
            .any(|value| !value.is_finite() || *value < 0.0 || *value > f64::from(f32::MAX))
            || request
                .position
                .iter()
                .any(|value| !value.is_finite() || value.abs() > f64::from(f32::MAX))
        {
            return Err("scene enemy creation differs from its validated definition".into());
        }
        let body = self.next_bot;
        let control = body.checked_add(1).ok_or("enemy IDs exhausted")?;
        if !(BOT_ENTITY..0xd000_0000).contains(&body)
            || control >= 0xd000_0000
            || self.by_wire.contains_key(&body)
            || self.by_wire.contains_key(&control)
            || self
                .checked_states()
                .map_err(|error| error.to_string())?
                .into_iter()
                .filter_map(|state| state.enemy.as_ref())
                .any(|enemy| [body, control].contains(&enemy.binding.controller))
        {
            return Err("enemy entity allocator exhausted or collided".into());
        }
        let bot = Bot::scene_enemy(body, definition, *team, request.position, *yaw, walk, tick);
        let binding = EnemyBinding {
            mode_voice_set: self.mode_voice_set,
            body,
            controller: control,
            hero: *hero,
            pair,
            mode,
            mode_variables,
            team: *team,
            skin: *skin,
            pose: bot.movement(tick),
            gate,
            roster: None,
            retired_entities: Arc::clone(&self.retired_entity_ids),
            frame: tick,
            now_secs: 0.0,
            spawn_anchors: self.spawn_anchors.clone(),
        };
        let mut vitals = definition.pools.vitals();
        let scripts =
            EnemyScripts::start(Arc::clone(&graphs), definition, &binding, tick, &mut vitals)?;
        let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(&graphs, &codecs);
        let snapshot = encoder
            .project(
                scripts.body.snapshot().expect("started body"),
                crate::script_snapshot_wire::View::Observer,
            )
            .map_err(|error| error.to_string())?;
        let payload = encoder.full(&snapshot).map_err(|error| error.to_string())?;
        let projection = crate::world::EntityUpdate {
            entity: body,
            chunk: Some(
                crate::script_wire::chunk(&payload, 0, 1).map_err(|error| error.to_string())?,
            ),
            ..Default::default()
        };
        if crate::world::frame(u32::MAX, u32::MAX, &[], &[], &[&projection])
            .map_err(|error| error.to_string())?
            .len()
            > crate::world::MAX_PAYLOAD
        {
            return Err("enemy observer baseline exceeds the datagram budget".into());
        }
        let mut created = bot_create(&bot, Some(&vitals)).map_err(|error| error.to_string())?;
        if let Some(skin) = skin {
            let Some(Operation::Create(definition)) = &mut created.operation else {
                unreachable!()
            };
            definition.skin.guid = *skin;
        }
        let reaper_exit = ReaperExit::at_spawn(&bot);
        let state = BotState {
            bot,
            vitals,
            history: TargetHistory::new(None),
            respawn_at: None,
            enemy: Some(EnemyState {
                scripts,
                binding,
                graphs,
                codecs,
                fault: None,
                gate_approach: gate.and_then(|gate| GateApproach::new(*hero, gate.position)),
                objective_mover_attached: false,
                reaper_exit,
                boss_pursuit: matches!(*hero, REAPER | ROADHOG | JUNKRAT | WITCH | SUMMONER)
                    .then(Box::default),
                boss_handoff_complete: false,
            }),
        };
        // All fallible preparation precedes the identity and wire publication boundary.
        let entity = self.world.spawn(state).id();
        self.by_wire.insert(body, entity);
        self.next_bot = control + 1;
        self.created.push(created);
        self.combat_active = true;
        Ok(body)
    }

    pub(crate) fn take_created(&mut self) -> Vec<EntityUpdate> {
        std::mem::take(&mut self.created)
    }

    pub(crate) fn queue_scene_message(&mut self, message: crate::script_enemy::SceneMessage) {
        self.scene_messages.push(message);
    }
    pub(crate) fn set_external_targets(
        &mut self,
        mut targets: Vec<crate::script_enemy::EnemyTarget>,
    ) {
        targets.sort_unstable_by_key(|target| target.entity);
        self.external_targets = Some(targets);
    }
    pub(crate) fn take_external_enemy_damage(&mut self) -> Vec<crate::script_enemy::EnemyDamage> {
        std::mem::take(&mut self.external_enemy_damage)
    }
    pub(crate) fn take_scripted_health(&mut self) -> Vec<EntityUpdate> {
        std::mem::take(&mut self.scripted_health)
    }
    pub(crate) fn take_retired_enemies(&mut self) -> Vec<u32> {
        std::mem::take(&mut self.retired_enemies)
    }
    pub(crate) fn take_scene_messages(&mut self) -> Vec<crate::script_enemy::SceneMessage> {
        std::mem::take(&mut self.scene_messages)
    }

    pub(crate) fn initial_creates(&self) -> io::Result<Vec<EntityUpdate>> {
        self.states()
            .map(|state| {
                let state = state.map_err(io::Error::other)?;
                let mut update = bot_create(&state.bot, Some(&state.vitals))?;
                if let Some(skin) = state.enemy.as_ref().and_then(|enemy| enemy.binding.skin) {
                    if let Some(Operation::Create(definition)) = &mut update.operation {
                        definition.skin.guid = skin;
                    }
                }
                Ok(update)
            })
            .collect()
    }

    pub(crate) fn enemy_projections(
        &self,
    ) -> Result<impl Iterator<Item = crate::script_snapshot_wire::SceneProjection<'_>>, CombatError>
    {
        Ok(self.checked_states()?.into_iter().filter_map(|state| {
            let enemy = state.enemy.as_ref().filter(|enemy| enemy.fault.is_none())?;
            Some(crate::script_snapshot_wire::SceneProjection {
                snapshot: enemy.scripts.body.snapshot()?,
                encoder: crate::script_snapshot_wire::SnapshotEncoder::new(
                    &enemy.graphs,
                    &enemy.codecs,
                ),
            })
        }))
    }

    pub(crate) fn set_mode_voice_set(&mut self, definition: u64) {
        if self.mode_voice_set == Some(definition) {
            return;
        }
        self.mode_voice_set = Some(definition);
        for &entity in self.by_wire.values() {
            let mut state = self
                .world
                .get_mut::<BotState>(entity)
                .expect("live bot handle");
            if let Some(enemy) = state.enemy.as_mut() {
                enemy.binding.mode_voice_set = Some(definition);
            }
        }
    }

    pub(crate) fn set_mode_variables(&mut self, variables: Arc<crate::script_wire::Variables>) {
        for &entity in self.by_wire.values() {
            let mut state = self
                .world
                .get_mut::<BotState>(entity)
                .expect("live bot handle");
            if let Some(enemy) = state.enemy.as_mut() {
                enemy.binding.mode_variables = Arc::clone(&variables);
            }
        }
    }
    pub(crate) fn set_mode_boss_pause(&mut self, active: bool) {
        self.mode_boss_pause = active;
    }
    pub(crate) fn new(bots: Vec<Bot>) -> Result<Self, CombatError> {
        let mut world = World::new();
        let mut next_bot = BOT_ENTITY;
        let mut by_wire = IndexMap::with_capacity(bots.len());
        for bot in bots {
            if by_wire.contains_key(&bot.entity) {
                return Err(CombatError::BotIdInUse(bot.entity));
            }
            next_bot = next_bot.max(
                bot.entity
                    .checked_add(1)
                    .ok_or(CombatError::BotIdsExhausted)?,
            );
            by_wire.insert(bot.entity, world.spawn(BotState::new(bot, None)).id());
        }
        Ok(Self {
            world,
            by_wire,
            next_bot,
            combat_active: false,
            created: Vec::new(),
            scene_messages: Vec::new(),
            external_targets: None,
            external_enemy_damage: Vec::new(),
            scripted_health: Vec::new(),
            retired_enemies: Vec::new(),
            retired_entity_ids: Arc::default(),
            defender_messages: Vec::new(),
            mode_voice_requests: Vec::new(),
            mode_voice_set: None,
            mode_boss_pause: false,
            spawn_anchors: None,
            projectiles: HashMap::new(),
            enemy_projectiles: Vec::new(),
            witch_staff_links: IndexMap::new(),
        })
    }

    pub(crate) fn set_spawn_anchors(&mut self, anchors: Arc<crate::scene_assets::SpawnAnchors>) {
        self.spawn_anchors = Some(anchors);
    }

    fn states(&self) -> impl ExactSizeIterator<Item = Result<&BotState, CombatError>> {
        self.by_wire.iter().map(|(&wire, &entity)| {
            let state = self
                .world
                .get::<BotState>(entity)
                .ok_or(CombatError::MissingBot { wire, entity })?;
            check_bot_identity(wire, state)?;
            Ok(state)
        })
    }

    fn checked_states(&self) -> Result<Vec<&BotState>, CombatError> {
        self.states().collect()
    }

    fn state_mut(&mut self, wire: u32) -> Result<Option<&mut BotState>, CombatError> {
        let Some(&entity) = self.by_wire.get(&wire) else {
            return Ok(None);
        };
        let state = self
            .world
            .get_mut::<BotState>(entity)
            .ok_or(CombatError::MissingBot { wire, entity })?
            .into_inner();
        check_bot_identity(wire, state)?;
        Ok(Some(state))
    }

    pub(crate) fn bodies(&self) -> Result<impl ExactSizeIterator<Item = &Bot>, CombatError> {
        let bodies = self
            .states()
            .map(|state| state.map(|state| &state.bot))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(bodies.into_iter())
    }

    pub(crate) fn body_script_state(
        &self,
        body: u32,
    ) -> crate::script_entity_state::EntityScriptState {
        self.by_wire
            .get(&body)
            .and_then(|entity| self.world.get::<BotState>(*entity))
            .map_or_else(Default::default, |state| state.script_state())
    }

    pub(crate) fn body_healths(&self) -> Result<std::collections::BTreeMap<u32, f64>, CombatError> {
        self.states()
            .map(|state| state.map(|state| (state.bot.entity, state.vitals.total())))
            .collect()
    }

    pub(crate) fn boss_view_subjects(
        &self,
    ) -> Result<std::collections::BTreeMap<u32, crate::script_player::ViewSubject>, CombatError>
    {
        Ok(self
            .checked_states()?
            .into_iter()
            .filter_map(|state| {
                if !crate::npc_catalog::is_junkenstein_boss(state.bot.spawn.hero) {
                    return None;
                }
                let enemy = state.enemy.as_ref().filter(|enemy| enemy.fault.is_none())?;
                let forward = crate::game_hitscan::aim_direction(&Command {
                    yaw: enemy.binding.pose.yaw,
                    pitch: 0,
                    ..Default::default()
                });
                Some((
                    state.bot.entity,
                    crate::script_player::ViewSubject {
                        position: state.bot.position,
                        forward,
                        tags: enemy.scripts.target_tags(&enemy.graphs, &enemy.binding),
                    },
                ))
            })
            .collect())
    }

    pub(crate) fn living_enemies(
        &self,
        team: u8,
    ) -> Result<impl Iterator<Item = (&Bot, f64)>, CombatError> {
        Ok(self.checked_states()?.into_iter().filter_map(move |state| {
            let bot = &state.bot;
            (bot.spawn.team != team && !bot.dead && state.vitals.death().is_none())
                .then(|| (bot, state.vitals.total()))
        }))
    }

    pub(crate) fn encounter_body_alive(&self, wire_id: u32) -> bool {
        self.by_wire
            .get(&wire_id)
            .and_then(|&entity| self.world.get::<BotState>(entity))
            .is_some_and(|state| !state.bot.dead && state.vitals.death().is_none())
    }

    pub(crate) fn encounter_enemy_hero(&self, wire_id: u32) -> Option<u64> {
        let state = self.world.get::<BotState>(*self.by_wire.get(&wire_id)?)?;
        Some(state.enemy.as_ref()?.binding.hero)
    }

    pub(crate) fn encounter_possessor(&self, body: u32) -> Option<u32> {
        let state = self.world.get::<BotState>(*self.by_wire.get(&body)?)?;
        let enemy = state.enemy.as_ref()?;
        (!state.bot.dead && state.vitals.death().is_none() && enemy.fault.is_none())
            .then_some(enemy.binding.controller)
    }

    pub(crate) fn encounter_body_has_target_tag(&self, body: u32, tag: u64) -> Option<bool> {
        let state = self.world.get::<BotState>(*self.by_wire.get(&body)?)?;
        let enemy = state.enemy.as_ref()?;
        enemy
            .scripts
            .body_has_target_tag(&enemy.graphs, &enemy.binding, tag)
    }

    pub(crate) fn take_boss_intro_starts(&mut self) -> Vec<(u32, crate::script_wire::Variables)> {
        let mut starts = Vec::new();
        for (&body, &handle) in &self.by_wire {
            let state = self
                .world
                .get_mut::<BotState>(handle)
                .expect("live bot handle");
            if let Some(enemy) = &mut state.into_inner().enemy {
                starts.extend(
                    enemy
                        .scripts
                        .take_boss_intro_starts()
                        .into_iter()
                        .map(|parameters| (body, parameters)),
                );
            }
        }
        starts
    }

    pub(crate) fn queue_encounter_wave(
        &mut self,
        caller: crate::script_init::Site,
        controller: u32,
        request: crate::script_init::PlayScript,
    ) -> bool {
        // create_enemy allocates the controller immediately after its body.
        let Some(body) = controller.checked_sub(1) else {
            return false;
        };
        let Some(state) = self.state_mut(body).ok().flatten() else {
            return false;
        };
        let Some(enemy) = state.enemy.as_mut().filter(|enemy| {
            enemy.binding.controller == controller
                && enemy.fault.is_none()
                && !state.bot.dead
                && state.vitals.death().is_none()
        }) else {
            return false;
        };
        enemy
            .scripts
            .queue_wave_script(&enemy.binding, caller, request)
    }

    pub(crate) fn for_each_body_mut(
        &mut self,
        mut step: impl FnMut(&mut Bot),
    ) -> Result<(), CombatError> {
        for (&wire, &entity) in &self.by_wire {
            let mut state = self
                .world
                .get_mut::<BotState>(entity)
                .ok_or(CombatError::MissingBot { wire, entity })?;
            check_bot_identity(wire, &state)?;
            step(&mut state.bot);
            check_bot_identity(wire, &state)?;
        }
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn body_mut(&mut self, wire_id: u32) -> Option<&mut Bot> {
        Some(&mut self.state_mut(wire_id).unwrap()?.bot)
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_actors(&self) -> Result<impl ExactSizeIterator<Item = Actor>, CombatError> {
        let states = self.states().collect::<Result<Vec<_>, _>>()?;
        Ok(states.into_iter().map(|state| {
            let bot = &state.bot;
            Actor {
                entity: bot.entity,
                generation: None,
                kind: ActorKind::Bot,
                team: bot.spawn.team,
                position: bot.position,
                velocity: bot.velocity,
                alive: !bot.dead,
                health: Some(if self.combat_active {
                    state.vitals.total()
                } else {
                    bot.spawn.health
                }),
                script_active: bot.patrol_active(),
                last_contact_clip: bot.last_contact_clip(),
            }
        }))
    }

    pub(crate) fn set_junkenstein_defenders(&mut self, defenders: Arc<[u32]>) {
        for &handle in self.by_wire.values() {
            let mut state = self
                .world
                .get_mut::<BotState>(handle)
                .expect("live bot handle");
            if let Some(enemy) = &mut state.enemy {
                enemy.scripts.set_defenders(Arc::clone(&defenders));
            }
        }
    }

    pub(crate) fn take_defender_sightings(
        &mut self,
    ) -> Vec<crate::junkenstein_messages::DefenderSighting> {
        self.by_wire
            .values()
            .filter_map(|&handle| {
                self.world
                    .get_mut::<BotState>(handle)
                    .expect("live bot handle")
                    .into_inner()
                    .enemy
                    .as_mut()
                    .map(|enemy| enemy.scripts.take_sightings())
            })
            .flatten()
            .collect()
    }

    pub(crate) fn queue_defender_message(&mut self, message: DefenderMessage) -> bool {
        if self.defender_messages.len() >= crate::script_init::MAX_INIT_WORK {
            return false;
        }
        self.defender_messages.push(message);
        true
    }

    pub(crate) fn queue_enemy_result(&mut self, parameters: &crate::script_wire::Variables) {
        for &handle in self.by_wire.values() {
            let mut state = self
                .world
                .get_mut::<BotState>(handle)
                .expect("live bot handle");
            let Some(enemy) = state.enemy.as_mut().filter(|enemy| enemy.fault.is_none()) else {
                continue;
            };
            let body = &mut enemy.scripts.body;
            if body.snapshot().is_some_and(|snapshot| {
                snapshot
                    .instances
                    .values()
                    .any(|instance| instance.graph == 0x0cbc)
            }) && let Err(error) = body.queue_entity_message_from(
                crate::world::GAME_MODE_ENTITY,
                crate::junkenstein_messages::ENCOUNTER_RESULT,
                parameters.clone(),
            ) {
                eprintln!(
                    "enemy {} result delivery failed: {error}",
                    enemy.binding.body
                );
                enemy.fault = Some(error.to_string());
            }
        }
    }

    pub(crate) fn take_defender_messages(&mut self) -> Vec<DefenderMessage> {
        let mut messages = std::mem::take(&mut self.defender_messages);
        for &handle in self.by_wire.values() {
            let state = self
                .world
                .get_mut::<BotState>(handle)
                .expect("live bot handle");
            if let Some(enemy) = &mut state.into_inner().enemy {
                messages.extend(enemy.scripts.take_defender_messages());
            }
        }
        messages
    }

    pub(crate) fn queue_mode_voice(&mut self, request: crate::junkenstein_voice::VoiceRequest) {
        self.mode_voice_requests.push(request);
    }

    pub(crate) fn take_encounter_voice_requests(
        &mut self,
    ) -> Vec<crate::junkenstein_voice::VoiceRequest> {
        let mut requests = std::mem::take(&mut self.mode_voice_requests);
        requests.extend(
            self.by_wire
                .values()
                .filter_map(|&handle| {
                    self.world
                        .get_mut::<BotState>(handle)
                        .expect("live bot handle")
                        .into_inner()
                        .enemy
                        .as_mut()
                        .map(|enemy| enemy.scripts.take_voice_requests())
                })
                .flatten(),
        );
        requests
    }

    pub(crate) fn activate_combat(&mut self) {
        self.combat_active = true;
    }

    fn record_poses(&mut self, queries: &BotQueries<'_>, tick: u32) -> Result<(), CombatError> {
        for (&wire, &entity) in &self.by_wire {
            let state = self
                .world
                .get_mut::<BotState>(entity)
                .ok_or(CombatError::MissingBot { wire, entity })?;
            check_bot_identity(wire, &state)?;
            if !state.bot.dead {
                let state = state.into_inner();
                state.history.record(tick, queries.target(&state.bot));
            }
        }
        Ok(())
    }

    pub(crate) fn hitscan(
        &mut self,
        queries: &BotQueries<'_>,
        shot: PosedHitscan,
        context: ShotContext,
        visible: impl Fn(u32) -> bool,
    ) -> Result<Option<BotHit>, CombatError> {
        let view = ViewFrame::from_command(&shot.command, context.quanta);
        let ray = HitscanShot::with_direction(shot.origin, shot.direction, shot.reach, shot.hit);
        // Scene order breaks exact ray ties.
        let mut candidates = Vec::with_capacity(self.by_wire.len());
        for state in self.states() {
            let state = state?;
            let bot = &state.bot;
            if !eligible_bot(bot, shot.source, context.team, queries.free_for_all())
                || !visible(bot.entity)
            {
                continue;
            }
            if let Some(pose) = state.history.at(view, queries.target(bot)) {
                candidates.push((bot.entity, pose));
            }
        }
        let Some(impact) = ray.resolve(candidates, |origin, direction, reach| {
            queries.hitscan_wall(origin, direction, reach)
        })?
        else {
            return Ok(None);
        };
        self.apply_bot_impact(shot.source, shot.shot_frame, context, &impact)
    }

    pub(crate) fn heal_bots(
        &mut self,
        queries: &BotQueries<'_>,
        team: u8,
        pulses: &[HealingPulse],
    ) -> Result<Vec<EntityUpdate>, CombatError> {
        if queries.free_for_all() || pulses.is_empty() {
            return Ok(Vec::new());
        }
        self.activate_combat();
        let mut updates = Vec::new();
        for (&wire, &entity) in &self.by_wire {
            let state = self
                .world
                .get_mut::<BotState>(entity)
                .ok_or(CombatError::MissingBot { wire, entity })?
                .into_inner();
            check_bot_identity(wire, state)?;
            let bot = &state.bot;
            if bot.dead || bot.spawn.team != team {
                continue;
            }
            let pose = queries.target(bot);
            let amount = pulses
                .iter()
                .filter(|pulse| pulse.reaches(pose))
                .map(|pulse| pulse.amount)
                .sum();
            if state.vitals.heal(amount) <= 0.0 {
                continue;
            }
            let component = state.vitals.component();
            state.vitals.clear_changed();
            updates.push(EntityUpdate {
                entity: bot.entity,
                operation: Some(Operation::Update(vec![component])),
                ..EntityUpdate::default()
            });
        }
        Ok(updates)
    }

    pub(crate) fn advance_projectiles(
        &mut self,
        queries: &BotQueries<'_>,
        connection: u32,
        owner: BodyId,
        launches: Vec<AdmittedFlight>,
        context: ShotContext,
    ) -> Result<(Vec<FlightOutcome>, Vec<BotHit>), CombatError> {
        if let Some(batch) = self.projectiles.get(&connection) {
            // Body generations are monotonic within a live connection. Late
            // work cannot inspect, advance or retire the replacement's batch.
            if owner.generation < batch.owner.generation {
                return Ok((Vec::new(), Vec::new()));
            }
            if owner.generation == batch.owner.generation && owner.entity != batch.owner.entity {
                return Err(CombatError::FlightOwnerIdentity {
                    connection,
                    generation: owner.generation,
                    expected: batch.owner.entity,
                    actual: owner.entity,
                });
            }
            for &entity in &batch.order {
                if self.world.get::<AdmittedFlight>(entity).is_none() {
                    return Err(CombatError::MissingFlight { connection, entity });
                }
            }
        }
        if self
            .projectiles
            .get(&connection)
            .is_some_and(|batch| batch.owner != owner)
        {
            self.retire_projectiles(connection);
        }
        let mut order = self
            .projectiles
            .remove(&connection)
            .map_or_else(Vec::new, |batch| batch.order);
        order.extend(
            launches
                .into_iter()
                .map(|launch| self.world.spawn(launch).id()),
        );
        let mut outcomes = Vec::new();
        let mut hits = Vec::new();
        if order.is_empty() {
            return Ok((outcomes, hits));
        }

        self.activate_combat();
        let mut continuing = Vec::with_capacity(order.len());
        for entity in order {
            // An earlier flight may have killed a target in this same tick.
            let mut candidates = Vec::with_capacity(self.by_wire.len());
            for state in self.states() {
                let bot = &state?.bot;
                if eligible_bot(bot, owner.entity, context.team, queries.free_for_all()) {
                    candidates.push((bot.entity, queries.target(bot)));
                }
            }
            let (token, stun, hit_effect, result) = {
                let mut active = self
                    .world
                    .get_mut::<AdmittedFlight>(entity)
                    .ok_or(CombatError::MissingFlight { connection, entity })?;
                let result =
                    active
                        .flight
                        .advance(context.tick, &candidates, |origin, direction, reach| {
                            queries.hitscan_wall(origin, direction, reach)
                        });
                (active.token, active.stun, active.hit_effect, result)
            };
            match result {
                Ok(None) => {
                    continuing.push(entity);
                    continue;
                }
                Ok(Some(end)) => {
                    for impact in &end.impacts {
                        if let Some(hit) =
                            self.apply_bot_impact(owner.entity, context.tick, context, impact)?
                        {
                            if let Some(stun) = stun {
                                if let Some(state) = self.state_mut(impact.entity)? {
                                    let mut control_allowed = true;
                                    if !state.bot.dead
                                        && let Some(caller) = hit_effect
                                        && let Some(enemy) = state
                                            .enemy
                                            .as_mut()
                                            .filter(|enemy| enemy.fault.is_none())
                                        && crate::game_content::is_junkenstein_mode(
                                            enemy.binding.mode,
                                        )
                                    {
                                        match enemy.scripts.flashbang_control_allowed(
                                            &enemy.graphs,
                                            &enemy.binding,
                                        ) {
                                            Ok(allowed) => control_allowed = allowed,
                                            Err(error) => {
                                                control_allowed = false;
                                                enemy.fault = Some(error);
                                            }
                                        }
                                        enemy
                                            .scripts
                                            .queue_flashbang_effect(&enemy.binding, caller);
                                    }
                                    if control_allowed {
                                        state.bot.stun_for(context.tick, stun.ticks);
                                    }
                                }
                            }
                            hits.push(hit);
                        }
                    }
                    outcomes.push(FlightOutcome::Ended {
                        token,
                        travelled: end.travelled,
                    });
                }
                // A query exceeding the bounded ray budget cancels only this
                // flight. Broken geometry and ECS ownership are room faults.
                Err(IndexError::RayTraversalLimit) => outcomes.push(FlightOutcome::Failed(token)),
                Err(error) => return Err(error.into()),
            }
            if !self.world.despawn(entity) {
                return Err(CombatError::MissingFlight { connection, entity });
            }
        }
        if !continuing.is_empty() {
            self.projectiles.insert(
                connection,
                FlightBatch {
                    owner,
                    order: continuing,
                },
            );
        }
        Ok((outcomes, hits))
    }

    pub(crate) fn retire_projectiles(&mut self, connection: u32) {
        if let Some(batch) = self.projectiles.remove(&connection) {
            for entity in batch.order {
                // Cleanup must remain usable while retiring a damaged room.
                self.world.despawn(entity);
            }
        }
    }

    pub(crate) fn clear_projectiles(&mut self) {
        for entity in self.projectiles.drain().flat_map(|(_, batch)| batch.order) {
            self.world.despawn(entity);
        }
    }

    fn apply_bot_impact(
        &mut self,
        source: u32,
        shot_frame: u32,
        context: ShotContext,
        impact: &Impact,
    ) -> Result<Option<BotHit>, CombatError> {
        let Some(state) = self.state_mut(impact.entity)? else {
            // An impact carrying a retired wire identity must not damage its
            // replacement body.
            return Ok(None);
        };
        let Some(result) = Self::damage_bot_with_hit(
            state,
            impact.damage,
            Death {
                time: context.now.as_secs_f64(),
                frame: context.tick,
                killer: source,
            },
            context.now,
            impact.critical,
            impact.loadout,
        ) else {
            return Ok(None);
        };
        let entity = state.bot.entity;
        let health = EntityUpdate {
            entity,
            operation: Some(Operation::Update(vec![state.vitals.component()])),
            ..EntityUpdate::default()
        };
        state.vitals.clear_changed();
        Ok(Some(BotHit {
            damage: AppliedDamage {
                source,
                target: entity,
                amount: result.dealt,
                tags: impact.tags,
                target_flags: crate::match_scene::team_bit(state.bot.spawn.team)
                    .ok()
                    .map(|team| 1 | team | if state.bot.dead { 0x10 } else { 0 }),
                target_tags: state.enemy.as_ref().map_or_else(Default::default, |enemy| {
                    enemy.scripts.target_tags(&enemy.graphs, &enemy.binding)
                }),
                target_state: state.script_state(),
            },
            notices: [
                Some(CombatNotice::Hit {
                    target: entity,
                    dealt: result.dealt,
                    critical: impact.critical,
                    killed: result.killed,
                    shot_frame,
                }),
                result.killed.then_some(CombatNotice::Elimination {
                    killer: source,
                    victim: entity,
                }),
            ],
            health,
        }))
    }

    fn damage_bot(
        state: &mut BotState,
        amount: f64,
        source: Death,
        now: Duration,
    ) -> Option<crate::vitals::Damage> {
        Self::damage_bot_with_hit(state, amount, source, now, false, None)
    }

    fn damage_bot_with_hit(
        state: &mut BotState,
        amount: f64,
        source: Death,
        now: Duration,
        critical: bool,
        loadout: Option<u64>,
    ) -> Option<crate::vitals::Damage> {
        if state.bot.dead {
            return None;
        }
        let result = state.vitals.damage(amount, source);
        if result.killed {
            state.bot.died(source.frame);
            if let Some(enemy) = &mut state.enemy
                && let Err(error) = enemy.scripts.queue_death(source, critical, loadout)
            {
                enemy.fault = Some(format!("enemy death input: {error}"));
            }
            state.respawn_at = state
                .enemy
                .is_none()
                .then(|| now.saturating_add(BOT_RESPAWN_TIME));
        }
        (result.dealt > 0.0).then_some(result)
    }

    pub(crate) fn finish_tick(
        &mut self,
        queries: &BotQueries<'_>,
        tick: u32,
        now: Duration,
    ) -> Result<Vec<BotReplacement>, CombatError> {
        if !self.combat_active {
            self.states().try_for_each(|state| state.map(|_| ()))?;
            return Ok(Vec::new());
        }
        let states = self.checked_states()?;
        let roster =
            self.external_targets.as_ref().map(|external| {
                Arc::new(
                    external
                        .iter()
                        .cloned()
                        .chain(states.iter().copied().map(|state| {
                            crate::script_enemy::EnemyTarget {
                                hero: Some(state.bot.spawn.hero),
                                filter_flags: Some(
                                    1 | (0x0080_0000 << state.bot.spawn.team)
                                        | if state.bot.dead { 0x10 } else { 0 },
                                ),
                                tags: state.enemy.as_ref().map_or_else(Default::default, |enemy| {
                                    enemy.scripts.target_tags(&enemy.graphs, &enemy.binding)
                                }),
                                health: Some(state.vitals.total()),
                                velocity: Some(state.bot.velocity),
                                script_state: state.script_state(),
                                entity: state.bot.entity,
                                team: state.bot.spawn.team,
                                pose: queries.target(&state.bot),
                                alive: !state.bot.dead,
                            }
                        }))
                        .collect(),
                )
            });
        let mut retired = Vec::new();
        let mut enemy_damage = Vec::new();
        let mut launched = Vec::new();
        let witch_present = states
            .iter()
            .copied()
            .any(|state| state.bot.spawn.hero == WITCH && !state.bot.dead);
        let witch_allies = if witch_present {
            states
                .iter()
                .copied()
                .filter_map(|state| {
                    let enemy = state.enemy.as_ref()?;
                    let current = state.vitals.total();
                    let maximum = state.vitals.maximum();
                    (!state.bot.dead
                        && state.vitals.death().is_none()
                        && enemy.fault.is_none()
                        && current < maximum)
                        .then(|| {
                            let favored = enemy
                                .scripts
                                .body_has_target_tag(
                                    &enemy.graphs,
                                    &enemy.binding,
                                    0x0d60_0000_0000_011e,
                                )
                                .unwrap_or(false);
                            (state.bot.entity, (favored, current / maximum))
                        })
                })
                .collect::<HashMap<_, _>>()
        } else {
            HashMap::new()
        };
        let pause_senders = states
            .iter()
            .copied()
            .filter(|state| {
                state
                    .enemy
                    .as_ref()
                    .is_some_and(|enemy| enemy.fault.is_none() && enemy.scripts.boss_pause_active())
            })
            .count();
        for &entity in self.by_wire.values() {
            let mut state = self
                .world
                .get_mut::<BotState>(entity)
                .expect("live bot handle");
            let BotState {
                bot, vitals, enemy, ..
            } = &mut *state;
            if let Some(enemy) = enemy.as_mut().filter(|enemy| enemy.fault.is_none()) {
                enemy.binding.frame = tick;
                enemy.binding.now_secs = now.as_secs_f64();
                enemy.binding.retired_entities = Arc::clone(&self.retired_entity_ids);
                bot.queue_velocity_changes(enemy.scripts.take_velocity_changes());
                let stunned = bot.is_stunned(tick);
                enemy.scripts.set_control_blocked(stunned);
                let other_pause = pause_senders > usize::from(enemy.scripts.boss_pause_active());
                let room_pause = self.mode_boss_pause || other_pause;
                enemy.scripts.set_room_boss_pause(room_pause);
                if enemy.binding.hero == WITCH {
                    let targets = roster.as_deref().map(Vec::as_slice).unwrap_or_default();
                    if let Err(error) = enemy.select_witch_targets(
                        bot,
                        tick,
                        queries,
                        if room_pause || stunned || vitals.death().is_some() {
                            &[]
                        } else {
                            targets
                        },
                        &witch_allies,
                    ) {
                        enemy.fault = Some(error.to_string());
                        continue;
                    }
                }
                if enemy.binding.hero == SUMMONER {
                    let targets = roster.as_deref().map(Vec::as_slice).unwrap_or_default();
                    if let Err(error) = enemy.select_summoner_targets(
                        bot,
                        tick,
                        queries,
                        if room_pause || stunned || vitals.death().is_some() {
                            &[]
                        } else {
                            targets
                        },
                    ) {
                        enemy.fault = Some(error.to_string());
                        continue;
                    }
                }
                if enemy.binding.hero == ZOMBARDIER {
                    let targets = roster.as_deref().map(Vec::as_slice).unwrap_or_default();
                    enemy.aim_zombardier(
                        bot,
                        tick,
                        queries,
                        if room_pause || stunned || vitals.death().is_some() {
                            &[]
                        } else {
                            targets
                        },
                    );
                }
                if stunned {
                    bot.stop_objective(tick);
                } else if let Err(error) = enemy.approach_gate(bot, tick, queries) {
                    bot.stop_objective(tick);
                    enemy.fault = Some(error.to_string());
                    continue;
                }
                if let Err(error) = enemy.advance_boss_weapon(
                    bot,
                    tick,
                    queries,
                    room_pause || stunned || vitals.death().is_some(),
                    self.external_targets.as_deref().unwrap_or_default(),
                ) {
                    bot.stop_objective(tick);
                    enemy.fault = Some(error.to_string());
                    continue;
                }
                enemy.binding.pose = bot.movement(tick);
                enemy.binding.roster = roster.clone();
                if let Err(error) =
                    enemy
                        .scripts
                        .advance(&enemy.graphs, &enemy.binding, vitals, tick)
                {
                    eprintln!("enemy {} halted: {error}", enemy.binding.body);
                    bot.stop_objective(tick);
                    enemy.fault = Some(error);
                } else if let Some(aim) = enemy.scripts.cinematic_aim() {
                    bot.apply_encounter_aim(aim, tick);
                } else if crate::npc_catalog::is_junkenstein_boss(enemy.binding.hero)
                    && let Some(direction) = enemy.scripts.facing_direction()
                {
                    bot.apply_encounter_facing(direction, tick);
                }
                if enemy.fault.is_none()
                    && matches!(
                        enemy.binding.hero,
                        REAPER | ROADHOG | JUNKRAT | ZOMBARDIER | WITCH | SUMMONER
                    )
                {
                    let shots = enemy.scripts.take_weapon_shots(
                        &enemy.graphs,
                        &enemy.binding,
                        vitals,
                        tick,
                    );
                    launched.extend(shots.projectiles);
                    for volley in shots.hitscan {
                        for pellet in volley.pellets {
                            let targets = roster
                                .iter()
                                .flat_map(|targets| targets.iter())
                                .filter(|target| target.alive && target.team != enemy.binding.team)
                                .map(|target| (target.entity, target.pose));
                            if let Ok(Some(impact)) =
                                pellet.resolve(targets, |origin, direction, reach| {
                                    queries.hitscan_wall(origin, direction, reach)
                                })
                            {
                                enemy_damage.push(crate::script_enemy::EnemyDamage {
                                    source: bot.entity,
                                    target: impact.entity,
                                    amount: enemy
                                        .scripts
                                        .outgoing_damage(impact.damage, &volley.damage_tags),
                                });
                            }
                        }
                    }
                }
                self.scene_messages
                    .extend(enemy.scripts.take_scene_messages());
                enemy_damage.extend(enemy.scripts.take_scripted_damage());
                if vitals.death().is_some() && !bot.dead {
                    bot.died(tick);
                }
                if let Some(health) = vitals.changed_component() {
                    self.scripted_health.push(EntityUpdate {
                        entity: bot.entity,
                        operation: Some(Operation::Update(vec![health])),
                        ..Default::default()
                    });
                    vitals.clear_changed();
                }
                if enemy.fault.is_none()
                    && let Err(error) =
                        enemy
                            .scripts
                            .queue_common_corpse_retirement(&enemy.binding, vitals, tick)
                {
                    enemy.fault = Some(error);
                }
                if enemy.fault.is_none() && enemy.scripts.retirement_requested {
                    if let Err(error) =
                        enemy
                            .scripts
                            .teardown(&enemy.graphs, &enemy.binding, vitals)
                    {
                        enemy.fault = Some(error);
                    } else {
                        retired.push(bot.entity);
                        self.scene_messages
                            .extend(enemy.scripts.take_scene_messages());
                    }
                }
            }
        }
        // +80 voice callbacks are a separate phase after all ordinary actor updates.
        // Select through read-only facts before taking a mutable actor reference.
        let voiced =
            self.states()
                .try_fold(Vec::new(), |mut bodies, state| -> Result<_, CombatError> {
                    let state = state?;
                    if let Some(enemy) = state.enemy.as_ref()
                        && enemy.fault.is_none()
                        && enemy.scripts.has_polled_voice()
                    {
                        bodies.push(state.bot.entity);
                    }
                    Ok(bodies)
                })?;
        for body in voiced {
            let BotState {
                bot, vitals, enemy, ..
            } = self.state_mut(body)?.expect("checked voiced body");
            if let Some(enemy) = enemy.as_mut().filter(|enemy| enemy.fault.is_none())
                && let Err(error) = enemy
                    .scripts
                    .poll_voice(&enemy.graphs, &enemy.binding, vitals)
            {
                bot.stop_objective(tick);
                enemy.fault = Some(error);
            }
        }
        self.advance_enemy_projectiles(
            queries,
            roster.as_deref().map_or(&[], Vec::as_slice),
            tick,
            &mut enemy_damage,
        );
        self.enemy_projectiles.extend(
            launched
                .into_iter()
                .map(|projectile| self.world.spawn(projectile).id()),
        );
        for hit in enemy_damage {
            if let Some(state) = self.state_mut(hit.target)? {
                if Self::damage_bot(
                    state,
                    hit.amount,
                    Death {
                        time: now.as_secs_f64(),
                        frame: tick,
                        killer: hit.source,
                    },
                    now,
                )
                .is_some()
                {
                    let health = state.vitals.component();
                    state.vitals.clear_changed();
                    self.scripted_health.push(EntityUpdate {
                        entity: hit.target,
                        operation: Some(Operation::Update(vec![health])),
                        ..Default::default()
                    });
                }
            } else {
                self.external_enemy_damage.push(hit);
            }
        }
        for wire in retired {
            let entity = self.by_wire.shift_remove(&wire).expect("retired NPC body");
            let enemy = self
                .world
                .get::<BotState>(entity)
                .unwrap()
                .enemy
                .as_ref()
                .unwrap();
            let identities = [enemy.binding.body, enemy.binding.controller];
            Arc::make_mut(&mut self.retired_entity_ids).extend(identities);
            self.world.despawn(entity);
            self.retired_enemies.push(wire);
        }
        if witch_present || !self.witch_staff_links.is_empty() {
            self.sync_witch_staff_links()?;
        }
        let replacements = self.respawn_due(tick, now);
        self.record_poses(queries, tick)?;
        replacements
    }

    fn sync_witch_staff_links(&mut self) -> Result<(), CombatError> {
        use crate::{script_enemy::WitchStaffSource, vitals::PoolKind};
        let pools = [Some(PoolKind::Health), Some(PoolKind::Armour)];
        let desired = self
            .checked_states()?
            .into_iter()
            .filter_map(|state| {
                let enemy = state.enemy.as_ref()?;
                if state.bot.dead || enemy.fault.is_some() || enemy.binding.hero != WITCH {
                    return None;
                }
                let (target, amount) = enemy.scripts.witch_staff_effect()?;
                let source = WitchStaffSource {
                    controller: enemy.binding.controller,
                    name: state.bot.spawn.name.clone(),
                    health: pools.map(|kind| state.vitals.amount(kind, false)),
                    capacity: pools.map(|kind| state.vitals.amount(kind, true)),
                };
                Some((state.bot.entity, (target, source, amount)))
            })
            .collect::<IndexMap<_, _>>();
        let mut links = std::mem::take(&mut self.witch_staff_links);
        let stale = links
            .extract_if(.., |source, (target, _)| {
                desired
                    .get(source)
                    .is_none_or(|(current, _, _)| current != target)
            })
            .collect::<Vec<_>>();
        for (source, (target, instance)) in stale {
            if let Some(state) = self.state_mut(target)?
                && let Some(enemy) = state.enemy.as_mut().filter(|enemy| enemy.fault.is_none())
                && let Err(error) = enemy.scripts.sync_witch_staff(
                    &enemy.graphs,
                    &enemy.binding,
                    &mut state.vitals,
                    Some((source, instance)),
                    None,
                )
            {
                enemy.fault = Some(error);
            }
        }
        for (source, (target, caster, amount)) in desired {
            if let Some(state) = self.state_mut(target)?.filter(|state| !state.bot.dead)
                && let Some(enemy) = state.enemy.as_mut().filter(|enemy| enemy.fault.is_none())
            {
                enemy.scripts.record_witch_staff_source(source, caster);
                if links.contains_key(&source) {
                    continue;
                }
                match enemy.scripts.sync_witch_staff(
                    &enemy.graphs,
                    &enemy.binding,
                    &mut state.vitals,
                    None,
                    Some((source, amount)),
                ) {
                    Ok(Some(instance)) => {
                        links.insert(source, (target, instance));
                    }
                    Ok(None) => (),
                    Err(error) => {
                        enemy.fault = Some(error);
                    }
                }
            }
        }
        self.witch_staff_links = links;
        Ok(())
    }

    fn advance_enemy_projectiles(
        &mut self,
        queries: &BotQueries<'_>,
        roster: &[crate::script_enemy::EnemyTarget],
        tick: u32,
        damage: &mut Vec<crate::script_enemy::EnemyDamage>,
    ) {
        use crate::script_enemy::{EnemyDamage, EnemyProjectile};

        let mut children = Vec::new();
        for handle in std::mem::take(&mut self.enemy_projectiles) {
            let mut projectile = self
                .world
                .get_mut::<EnemyProjectile>(handle)
                .expect("live enemy projectile");
            let targets: Vec<_> = roster
                .iter()
                .filter(|target| target.alive && target.team != projectile.team)
                .map(|target| (target.entity, target.pose))
                .collect();
            match projectile
                .flight
                .advance(tick, &targets, |origin, direction, reach| {
                    queries.hitscan_wall(origin, direction, reach)
                }) {
                Ok(None) => {
                    self.enemy_projectiles.push(handle);
                    continue;
                }
                Ok(Some(end)) => {
                    let source = projectile.source;
                    let team = projectile.team;
                    if end.expired
                        && let Some(split) = projectile.split.take()
                    {
                        children.extend(split.launch(source, team, end.point, tick));
                    }
                    let tags = projectile.damage_tags.clone();
                    let shooter = self
                        .by_wire
                        .get(&source)
                        .and_then(|&body| self.world.get::<BotState>(body))
                        .and_then(|state| state.enemy.as_ref());
                    damage.extend(end.impacts.into_iter().map(|impact| EnemyDamage {
                        source,
                        target: impact.entity,
                        amount: shooter.map_or(impact.damage, |enemy| {
                            enemy.scripts.outgoing_damage(impact.damage, &tags)
                        }),
                    }));
                }
                Err(_) => {}
            }
            self.world.despawn(handle);
        }
        self.enemy_projectiles.extend(
            children
                .into_iter()
                .map(|projectile| self.world.spawn(projectile).id()),
        );
    }

    fn respawn_due(
        &mut self,
        tick: u32,
        now: Duration,
    ) -> Result<Vec<BotReplacement>, CombatError> {
        // Validate the entire batch before retiring any entity handles.
        let mut prepared = Vec::new();
        let mut next_bot = self.next_bot;
        for (slot, (&wire, &previous)) in self.by_wire.iter().enumerate() {
            let state = self
                .world
                .get::<BotState>(previous)
                .ok_or(CombatError::MissingBot {
                    wire,
                    entity: previous,
                })?;
            check_bot_identity(wire, state)?;
            if !state.respawn_at.is_some_and(|deadline| deadline <= now) {
                continue;
            }
            if self.by_wire.contains_key(&next_bot) {
                return Err(CombatError::BotIdInUse(next_bot));
            }
            let fresh = state
                .bot
                .respawned(next_bot, tick.saturating_add(BOT_RECORD_DELAY));
            next_bot = next_bot
                .checked_add(1)
                .ok_or(CombatError::BotIdsExhausted)?;
            let created =
                bot_create(&fresh, None).map_err(|source| CombatError::RespawnCreate {
                    wire: fresh.entity,
                    source,
                })?;
            prepared.push((
                slot,
                previous,
                fresh,
                BotReplacement {
                    retired: state.bot.entity,
                    created,
                },
            ));
        }
        let mut replacements = Vec::with_capacity(prepared.len());
        for (slot, previous, fresh, replacement) in prepared {
            let fresh_wire = fresh.entity;
            if !self.world.despawn(previous) {
                return Err(CombatError::MissingBot {
                    wire: replacement.retired,
                    entity: previous,
                });
            }
            let entity = self.world.spawn(BotState::new(fresh, Some(tick))).id();
            self.by_wire
                .replace_index(slot, fresh_wire)
                .map_err(|_| CombatError::BotIdInUse(fresh_wire))?;
            *self
                .by_wire
                .get_index_mut(slot)
                .ok_or(CombatError::MissingBotSlot(slot))?
                .1 = entity;
            replacements.push(replacement);
        }
        self.next_bot = next_bot;
        Ok(replacements)
    }

    #[cfg(test)]
    pub(crate) fn invalidate_bot_component(&mut self) -> bool {
        let Some(&entity) = self.by_wire.values().next() else {
            return false;
        };
        let Ok(mut entity) = self.world.get_entity_mut(entity) else {
            return false;
        };
        entity.take::<BotState>().is_some()
    }
}

fn check_bot_identity(wire: u32, state: &BotState) -> Result<(), CombatError> {
    if state.bot.entity != wire {
        return Err(CombatError::BotIdentity {
            wire,
            actual: state.bot.entity,
        });
    }
    Ok(())
}

fn eligible_bot(bot: &Bot, source: u32, team: u8, free_for_all: bool) -> bool {
    !bot.dead && bot.entity != source && (free_for_all || bot.spawn.team != team)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{game_content::GameMap, game_instance::RETAIL_QUANTA, scene_assets::SceneAssets};

    fn fixture_bots() -> Vec<Bot> {
        crate::game_bots::BotCatalog::from_json(
            r#"{
            "heroes":{"0x01":{"name":"Test bot","body":"0x02","health":100}},
            "maps":{"0x03":{"hints":{},"spawners":[
                {"hero":"0x01","team":1,"position":[1,0,0],"rotation":[0,0,0,1],"graphs":[]},
                {"hero":"0x01","team":1,"position":[2,0,0],"rotation":[0,0,0,1],"graphs":[]}
            ]}}
        }"#,
        )
        .unwrap()
        .make(3, |_| crate::game_bots::BotWalk::default())
    }

    #[test]
    fn corpse_health_is_available_after_live_target_selection_excludes_it() {
        let mut room = RoomSimulation::new(fixture_bots()).unwrap();
        let victim = room.living_enemies(0).unwrap().next().unwrap().0.entity;
        room.apply_bot_impact(
            7,
            FIXTURE_CONTEXT.tick,
            FIXTURE_CONTEXT,
            &fixture_impact(victim, 10000.0),
        )
        .unwrap()
        .unwrap();
        assert!(
            !room
                .living_enemies(0)
                .unwrap()
                .any(|(bot, _)| bot.entity == victim)
        );
        assert_eq!(room.body_healths().unwrap().get(&victim), Some(&0.0));
        room.respawn_due(
            FIXTURE_CONTEXT.tick + 1,
            FIXTURE_CONTEXT.now + BOT_RESPAWN_TIME,
        )
        .unwrap();
        assert!(!room.body_healths().unwrap().contains_key(&victim));
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_waves_destroy_gate_with_invulnerable_cassidy() {
        verify_natural_ending(NormalEnding::EarlyDefeat);
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_late_waves_destroy_gate_after_all_boss_intros() {
        verify_natural_ending(NormalEnding::LateDefeat);
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_deadeye_clears_natural_bosses_and_reaches_allied_victory() {
        verify_natural_ending(NormalEnding::DeadeyeVictory);
    }

    #[derive(Clone, Copy)]
    enum NormalEnding {
        EarlyDefeat,
        LateDefeat,
        DeadeyeVictory,
    }

    fn verify_natural_ending(ending: NormalEnding) {
        use crate::{
            game_clock::FIRST_TICK,
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            game_heroes::CASSIDY,
            game_instance::RETAIL_QUANTA,
            game_replication::BodyStep,
            script_enemy::EnemyTarget,
            script_player::OwnerBodySpawn,
            world::ComponentValue,
        };
        let late = !matches!(ending, NormalEnding::EarlyDefeat);
        let victory = matches!(ending, NormalEnding::DeadeyeVictory);
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        if late {
            scene.set_test_gate_capacity(1_000_000_000.0);
        }
        let hero = scene.heroes().find_hero("Cassidy").unwrap();
        let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
        let body = 0xa000_0101;
        let flags = crate::match_scene::team_bit(0).unwrap() | 1;
        let mut owner = scene
            .owner_body_runtime(
                hero,
                OwnerBodySpawn {
                    slot: 0,
                    controller: body - 1,
                    entity: body,
                    body_filter: flags,
                    frame: FIRST_TICK,
                    pose: mover.pose(),
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .unwrap();
        let health = owner.vitals().unwrap().total();
        let mut absorbed = 0.0;
        let mut winner_at = None;
        let mut spawned_heroes = std::collections::BTreeSet::new();
        let mut earned_points = 0.0_f32;
        let mut point_events = 0_usize;
        let mut gate_released = false;
        let mut intro_cameras: std::collections::BTreeMap<
            u64,
            std::collections::BTreeSet<(u32, u16)>,
        > = Default::default();
        let mut intro_finished = std::collections::BTreeSet::new();
        let mut combat_positions = HashMap::new();
        let mut pursued = std::collections::BTreeSet::new();
        let mut gate_release_damage = None;
        let mut gate_zero_at = None;
        let mut deadeye_started = None;
        let mut victory_approach = None;
        let mut deadeye_casts = 0;
        let mut outcome_at = None;
        let mut deadeye_kills = std::collections::BTreeSet::new();
        let mut deadeye_hits = Vec::new();
        let mut boss_bodies = std::collections::BTreeSet::new();
        let mut deadeye_bodies = std::collections::BTreeSet::new();
        scene.step_encounter(FIRST_TICK, Some(CASSIDY));
        for tick in FIRST_TICK + 1..=FIRST_TICK + 110_000 {
            if late
                && !victory
                && !gate_released
                && [REAPER, ROADHOG, JUNKRAT, SUMMONER, WITCH]
                    .iter()
                    .all(|hero| intro_finished.contains(hero))
            {
                // Preserve the full natural introduction sequence, then allow
                // one further point of actual attack damage to end the match.
                let damage =
                    crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8569]);
                scene.set_test_gate_capacity(damage + 1.0);
                gate_release_damage = Some(damage);
                assert!(
                    crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8571])
                        > 0.0
                );
                gate_released = true;
                eprintln!(
                    "late gate released at {tick}: heroes={spawned_heroes:X?}, cameras={intro_cameras:X?}, pursued={pursued:X?}"
                );
            }
            scene.step_encounter(tick, Some(CASSIDY));
            assert!(scene.encounter_snapshot().is_some(), "mode fault at {tick}");
            if !scene.encounter_active() && outcome_at.is_none() {
                outcome_at = Some(tick);
                let snapshot = scene.encounter_snapshot().unwrap();
                let mode = &snapshot.instances[&2];
                eprintln!(
                    "outcome at {tick}: winner={:?}, success={:?}, challenge={:?}, active={:?}",
                    mode.variables.get(&7525),
                    mode.variables.get(&8617),
                    mode.variables.get(&11776),
                    mode.states
                        .iter()
                        .filter(|(_, state)| state.active)
                        .map(|(&raw, state)| (raw, &state.kind))
                        .collect::<Vec<_>>()
                );
                eprintln!("final hits: {deadeye_hits:X?}");
            }
            if let Some(outcome) = outcome_at
                && tick == outcome + 4000
            {
                let snapshot = scene.encounter_snapshot().unwrap();
                let mode = &snapshot.instances[&2];
                assert!(
                    scene.encounter_match_result().is_some()
                        && scene.encounter_post_match_wire().unwrap().is_some(),
                    "outcome did not settle: winner={:?}, success={:?}, challenge={:?}, active={:?}",
                    mode.variables.get(&7525),
                    mode.variables.get(&8617),
                    mode.variables.get(&11776),
                    mode.states
                        .iter()
                        .filter(|(_, state)| state.active)
                        .map(|(&raw, state)| (raw, &state.kind))
                        .collect::<Vec<_>>()
                );
            }
            owner
                .set_encounter_active(scene.encounter_active())
                .unwrap();
            if victory
                && victory_approach.is_none()
                && [REAPER, ROADHOG, JUNKRAT, SUMMONER, WITCH]
                    .iter()
                    .all(|hero| intro_finished.contains(hero))
            {
                victory_approach = Some(tick);
            }
            if victory_approach.is_some()
                && mover.pose().position[2] <= -77.0
                && (deadeye_casts == 0 || mover.pose().position[0] <= 80.0)
                && deadeye_started.is_none_or(|start| tick > start + 600)
                && scene
                    .bots()
                    .unwrap()
                    .any(|bot| !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero))
                && owner.snapshot().unwrap().variables[&342]
                    == owner.snapshot().unwrap().variables[&338]
            {
                let snapshot = owner.snapshot().unwrap();
                assert_eq!(
                    snapshot.variables[&342], snapshot.variables[&338],
                    "real passive charge must be full before the final cast"
                );
                deadeye_started = Some(tick);
                deadeye_casts += 1;
                assert!(
                    deadeye_casts <= 4,
                    "normal charge/casts did not clear the Bosses"
                );
                let roster = scene
                    .bots()
                    .unwrap()
                    .filter(|bot| {
                        !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero)
                    })
                    .map(|bot| (bot.entity, bot.spawn.hero, bot.position))
                    .collect::<Vec<_>>();
                eprintln!(
                    "final-phase Deadeye {deadeye_casts} begins at {tick}: position={:?}, bosses={roster:X?}",
                    mover.pose().position
                );
            }
            let mut command = Command {
                frame: tick,
                yaw: mover.pose().yaw,
                pitch: mover.pose().pitch,
                ..Default::default()
            };
            if victory_approach.is_some() {
                let position = mover.pose().position;
                let mut yaw_range = [f64::INFINITY, f64::NEG_INFINITY];
                let mut pitch_range = yaw_range;
                for bot in scene.bots().unwrap().filter(|bot| {
                    !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero)
                }) {
                    let delta = [bot.position[0] - position[0], bot.position[2] - position[2]];
                    let distance = delta[0].hypot(delta[1]);
                    if distance > 0.001 {
                        // Every Boss is ahead in this courtyard segment. Aim
                        // between the angular extremes so a cluster of nearer
                        // bodies does not turn the view away from the balcony.
                        let yaw = delta[0].atan2(delta[1]).rem_euclid(std::f64::consts::TAU);
                        let pitch = (bot.position[1] + eye_height(bot.spawn.hero, false)
                            - position[1]
                            - eye_height(CASSIDY, false))
                        .atan2(distance);
                        yaw_range = [yaw_range[0].min(yaw), yaw_range[1].max(yaw)];
                        pitch_range = [pitch_range[0].min(pitch), pitch_range[1].max(pitch)];
                    }
                }
                if yaw_range[0].is_finite() {
                    command.yaw = ((yaw_range[0] + yaw_range[1]) * 0.5 * 65536.0
                        / std::f64::consts::TAU)
                        .round_ties_even() as i32;
                    command.pitch = (-(pitch_range[0] + pitch_range[1]) * 0.5 * 65536.0
                        / std::f64::consts::TAU)
                        .round_ties_even() as i32;
                }
                if mover.pose().position[2] > -77.0 {
                    // Walk from the actual spawn into the courtyard. No pose
                    // teleport or visibility/collision override is used.
                    command.yaw = 32768;
                    command.pitch = 0;
                    command.forward = i8::MAX;
                } else if deadeye_casts > 0
                    && position[0] > 80.0
                    && deadeye_started.is_some_and(|start| tick > start + 600)
                {
                    command.yaw = -16384;
                    command.pitch = 0;
                    command.forward = i8::MAX;
                }
                if let Some(start) = deadeye_started
                    && (tick == start || tick == start + 350)
                {
                    command.buttons = 0x20;
                }
                mover
                    .step(scene.movement_index(), command, 0.016, |_, _| {})
                    .unwrap();
                owner.set_deadeye_scene(&scene, 0).unwrap();
            }
            owner
                .advance(
                    command,
                    &mover,
                    if victory_approach.is_some() {
                        BodyStep::Posed
                    } else {
                        BodyStep::UnposedTimer
                    },
                )
                .unwrap();
            for shot in owner.take_shots(Some(command), &mover) {
                let PosedShot::Hitscan(shot) = shot else {
                    panic!("final Deadeye must use authored hitscan");
                };
                let (room, queries) = scene.bot_simulation();
                if let Some(hit) = room
                    .hitscan(
                        &queries,
                        *shot,
                        ShotContext {
                            team: 0,
                            quanta: RETAIL_QUANTA,
                            tick,
                            now: Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
                        },
                        |_| true,
                    )
                    .unwrap()
                {
                    let hero = state(room, hit.damage.target).unwrap().bot.spawn.hero;
                    let killed =
                        matches!(hit.notices[0], Some(CombatNotice::Hit { killed: true, .. }));
                    deadeye_hits.push((hit.damage.target, hero, hit.damage.amount, killed));
                    if killed && crate::npc_catalog::is_junkenstein_boss(hero) {
                        deadeye_kills.insert(hero);
                        deadeye_bodies.insert(hit.damage.target);
                    }
                    owner.receive_applied_damage(hit.damage).unwrap();
                }
            }
            let defender = EnemyTarget {
                hero: Some(CASSIDY),
                filter_flags: Some(flags),
                tags: owner.target_tags(),
                health: Some(health),
                velocity: Some([0.0; 3]),
                script_state: crate::script_entity_state::EntityScriptState::Running(Arc::new(
                    owner.snapshot().unwrap().variables.clone(),
                )),
                entity: body,
                team: 0,
                alive: true,
                pose: TargetPose {
                    feet: mover.pose().position,
                    height: 1.8,
                    radius: 0.35,
                    eye: 1.55,
                },
            };
            let (room, queries) = scene.bot_simulation();
            room.set_external_targets(vec![defender]);
            room.finish_tick(
                &queries,
                tick,
                Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
            )
            .unwrap();
            for message in &room.scene_messages {
                let parameter = match message.message {
                    0x0240_0000_0000_06a4 => Some(0x39e),
                    0x0240_0000_0000_06c8 => Some(0x3af),
                    _ => None,
                };
                if let Some(parameter) = parameter {
                    earned_points +=
                        crate::script_expr::to_float(&message.parameters[&parameter]) as f32;
                    point_events += 1;
                }
            }
            for actor in room.states().map(Result::unwrap) {
                if let Some(enemy) = &actor.enemy {
                    if crate::npc_catalog::is_junkenstein_boss(enemy.binding.hero) {
                        boss_bodies.insert(actor.bot.entity);
                    }
                    if spawned_heroes.insert(enemy.binding.hero) {
                        eprintln!(
                            "natural wave hero {:X} first seen at {tick}",
                            enemy.binding.hero
                        );
                    }
                    assert!(
                        enemy.fault.is_none(),
                        "enemy {:x} fault at {tick}: {:?}",
                        enemy.binding.hero,
                        enemy.fault
                    );
                    if late && crate::npc_catalog::is_junkenstein_boss(enemy.binding.hero) {
                        for (&instance_id, intro) in
                            &enemy.scripts.body.snapshot().unwrap().instances
                        {
                            if intro.graph != 0x0ce0 {
                                continue;
                            }
                            if let Some(camera) =
                                intro.states.get(&14).filter(|camera| camera.active)
                            {
                                assert_eq!(camera.activation, 1, "Boss camera restarted");
                                if intro_cameras
                                    .entry(enemy.binding.hero)
                                    .or_default()
                                    .insert((actor.bot.entity, instance_id))
                                {
                                    let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(
                                        &enemy.graphs,
                                        &enemy.codecs,
                                    );
                                    encoder
                                        .full(
                                            &encoder
                                                .project(
                                                    enemy.scripts.body.snapshot().unwrap(),
                                                    crate::script_snapshot_wire::View::Observer,
                                                )
                                                .unwrap(),
                                        )
                                        .unwrap();
                                }
                            }
                            if intro
                                .states
                                .get(&3)
                                .is_some_and(|wait| wait.activation != 0 && !wait.active)
                            {
                                intro_finished.insert(enemy.binding.hero);
                            }
                        }
                        if intro_finished.contains(&enemy.binding.hero) {
                            let start = combat_positions
                                .entry(actor.bot.entity)
                                .or_insert(actor.bot.position);
                            if norm(std::array::from_fn(|axis| {
                                actor.bot.position[axis] - start[axis]
                            })) > 0.5
                            {
                                pursued.insert(enemy.binding.hero);
                            }
                        }
                    }
                }
            }
            // Explicit test-only invulnerability: count real enemy impacts while
            // the defender's pools stay intact. Gate damage is never intercepted.
            absorbed += room
                .take_external_enemy_damage()
                .into_iter()
                .filter(|hit| hit.target == body)
                .map(|hit| hit.amount)
                .sum::<f64>();
            let messages = room.take_defender_messages();
            let sightings = room.take_defender_sightings();
            if !messages.is_empty() {
                owner.set_deadeye_scene(&scene, 0).unwrap();
            }
            for message in messages {
                owner.receive_defender_message(message).unwrap();
            }
            for sighting in sightings {
                owner.receive_sighting(sighting).unwrap();
            }
            if late && (tick - FIRST_TICK) % 5000 == 0 {
                eprintln!(
                    "late replay tick={tick}, gate={}, finished={intro_finished:X?}, casts={deadeye_casts}, charge={:?}",
                    crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8571]),
                    owner.snapshot().unwrap().variables.get(&342),
                );
            }
            assert_eq!(owner.vitals().unwrap().total(), health);
            assert!(
                owner.snapshot().unwrap().initialization_complete(),
                "owner at {tick}: {:?}",
                owner.snapshot().unwrap().diagnostics
            );
            if let Some(start) = deadeye_started
                && deadeye_casts == 4
                && tick == start + 1500
            {
                let remaining = scene
                    .bots()
                    .unwrap()
                    .filter(|bot| {
                        !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero)
                    })
                    .map(|bot| (bot.entity, bot.spawn.hero, bot.position))
                    .collect::<Vec<_>>();
                assert!(
                    remaining.is_empty(),
                    "Deadeye did not clear all Bosses: remaining={remaining:X?}, hits={deadeye_hits:X?}, targets={:?}, visible={:?}",
                    owner.snapshot().unwrap().variables.get(&2551),
                    owner.snapshot().unwrap().variables.get(&5874)
                );
            }
            if late
                && crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8571])
                    == 0.0
            {
                let zero = *gate_zero_at.get_or_insert(tick);
                assert!(
                    tick - zero < 4000 || scene.encounter_match_result().is_some(),
                    "late gate zero did not settle within 64 seconds"
                );
            }
            if let Some(result) = scene.encounter_match_result() {
                assert!(
                    !late || victory || gate_released,
                    "high-capacity gate ended the encounter early"
                );
                assert_eq!(
                    result.fields[7],
                    Some(ComponentValue::I32(if victory { 0 } else { 1 }))
                );
                winner_at.get_or_insert(tick);
                if scene.encounter_post_match_wire().unwrap().is_some() {
                    if victory {
                        assert_eq!(
                            boss_bodies, deadeye_bodies,
                            "every spawned Boss body must die to actual Deadeye damage: {deadeye_hits:X?}"
                        );
                        assert!(
                            crate::script_expr::to_float(
                                &scene.gate_snapshot().unwrap().variables[&8571]
                            ) > 0.0
                        );
                        assert!(!scene.bots().unwrap().any(|bot| !bot.dead
                            && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero)));
                    }
                    if late {
                        assert!(
                            victory
                                || crate::script_expr::to_float(
                                    &scene.gate_snapshot().unwrap().variables[&8569]
                                ) > gate_release_damage.unwrap(),
                            "late loss requires a new actual gate hit"
                        );
                        for hero in [REAPER, ROADHOG, JUNKRAT, SUMMONER, WITCH] {
                            if victory {
                                assert!(
                                    deadeye_kills.contains(&hero),
                                    "Boss {hero:X} must die to actual Deadeye damage: {deadeye_hits:X?}"
                                );
                            }
                            assert!(
                                intro_finished.contains(&hero),
                                "Boss {hero:X} never completed introduction"
                            );
                            assert_eq!(
                                intro_cameras.get(&hero).map(|roots| roots.len()),
                                Some(1),
                                "Boss {hero:X} camera must play once"
                            );
                        }
                        // These two must close their spawn distance. The casters
                        // may already have a valid ranged solution from spawn.
                        for hero in [REAPER, ROADHOG] {
                            assert!(
                                pursued.contains(&hero),
                                "Boss {hero:X} did not move after introduction"
                            );
                        }
                    }
                    assert!(
                        absorbed > 0.0,
                        "no actual enemy hit exercised invulnerability"
                    );
                    assert!(
                        crate::script_expr::to_float(
                            &scene.gate_snapshot().unwrap().variables[&8569]
                        ) > 0.0
                    );
                    let gate_health = crate::script_expr::to_float(
                        &scene.gate_snapshot().unwrap().variables[&8571],
                    );
                    assert!(
                        if victory {
                            gate_health > 0.0
                        } else {
                            gate_health == 0.0
                        },
                        "gate state disagrees with outcome"
                    );
                    assert!(scene.encounter_result_wire().unwrap().is_some());
                    let snapshot = scene.encounter_snapshot().unwrap();
                    let gate_bonus = snapshot
                        .variables
                        .get(&8525)
                        .map(crate::script_expr::to_float)
                        .unwrap_or(0.0);
                    assert_eq!(
                        gate_bonus > 0.0,
                        victory,
                        "gate bonus must follow the actual result"
                    );
                    let expected_score = f64::from((earned_points + gate_bonus as f32) * 1.2_f32);
                    assert_eq!(
                        snapshot.resolved_bindings.get(&(0, 2499)),
                        Some(&crate::script_wire::Value::Float(expected_score)),
                        "natural result score differs from actual point carriers and gate bonus"
                    );
                    if victory {
                        assert!(point_events >= deadeye_bodies.len());
                    }
                    let (projected, _) = scene.encounter_notification_delta(None).unwrap().unwrap();
                    assert_eq!(
                        projected.entity_variables().get(&2499),
                        Some(&crate::script_wire::Value::Float(expected_score))
                    );
                    eprintln!(
                        "natural score: events={point_events}, earned={earned_points}, gate_bonus={gate_bonus}, final={expected_score}"
                    );

                    eprintln!(
                        "natural ending: tick={tick}, health={health}, absorbed={absorbed}, casts={deadeye_casts}, heroes={spawned_heroes:X?}"
                    );
                    return;
                }
            }
        }
        panic!("natural waves did not reach recap: winner_at={winner_at:?}, absorbed={absorbed}");
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_common_enemy_death_updates_authored_team_score() {
        use crate::{
            game_clock::FIRST_TICK,
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            game_heroes::CASSIDY,
            script_enemy::EnemyTarget,
            world::GAME_MODE_ENTITY,
        };
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        let defender = EnemyTarget {
            hero: Some(CASSIDY),
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: 0xa000_0101,
            team: 0,
            pose: TargetPose {
                feet: [87.013671875, 12.65625, -64.34765625],
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        };
        scene.set_junkenstein_defenders(Arc::from([defender.entity]));
        let (initial_projection, _) = scene.encounter_notification_delta(None).unwrap().unwrap();
        let mut score_messages = Vec::new();
        let mut live_score_verified = false;
        let mut killed_at = None;
        let mut first_seen = None;
        let mut before = 0.0;
        for tick in FIRST_TICK..FIRST_TICK + 16000 {
            scene.step_encounter(tick, Some(CASSIDY));
            let snapshot = scene.encounter_snapshot().unwrap();
            let score = snapshot
                .resolved_bindings
                .get(&(0, 2499))
                .or_else(|| snapshot.variables.get(&2499))
                .map(crate::script_expr::to_float)
                .unwrap_or(0.0);
            if let Some(killed_at) = killed_at {
                if tick > killed_at + 200 {
                    assert_eq!(
                        score_messages,
                        [crate::script_wire::Variables::from([(
                            0x39e,
                            crate::script_wire::Value::Float(10.0)
                        )])]
                    );
                    assert_eq!(before, 0.0);
                    assert_eq!(
                        score, 12.0,
                        "authored 10-point Zomnic and Normal 1.2 multiplier"
                    );
                    if !live_score_verified {
                        let (projected, payload) = scene
                            .encounter_notification_delta(Some(&initial_projection))
                            .unwrap()
                            .unwrap();
                        assert_eq!(
                            projected.entity_variables().get(&2499),
                            Some(&crate::script_wire::Value::Float(12.0))
                        );
                        assert_eq!(projected.entity(), GAME_MODE_ENTITY);
                        assert!(payload.bit_len() > 0);
                        live_score_verified = true;
                    }
                    if let Some(result) = scene.encounter_match_result()
                        && scene.encounter_post_match_wire().unwrap().is_some()
                    {
                        assert_eq!(result.fields[7], Some(crate::world::ComponentValue::I32(1)));
                        assert_eq!(
                            snapshot
                                .variables
                                .get(&8525)
                                .map(crate::script_expr::to_float),
                            Some(0.0)
                        );
                        assert!(scene.encounter_result_wire().unwrap().is_some());
                        let (projected, _) =
                            scene.encounter_notification_delta(None).unwrap().unwrap();
                        assert_eq!(
                            projected.entity_variables().get(&2499),
                            Some(&crate::script_wire::Value::Float(12.0))
                        );
                        return;
                    }
                }
            } else {
                before = score;
            }
            let (room, queries) = scene.bot_simulation();
            room.activate_combat();
            room.set_external_targets(vec![defender.clone()]);
            if killed_at.is_none() {
                let common = room
                    .states()
                    .map(Result::unwrap)
                    .find(|state| !state.bot.dead && state.bot.spawn.hero == 0x02e0_0000_0000_0175)
                    .map(|state| state.bot.entity);
                if let Some(entity) = common
                    && tick > *first_seen.get_or_insert(tick) + 20
                {
                    let now = Duration::from_millis(u64::from(tick - FIRST_TICK) * 16);
                    RoomSimulation::damage_bot(
                        room.state_mut(entity).unwrap().unwrap(),
                        10000.0,
                        Death {
                            time: now.as_secs_f64(),
                            frame: tick,
                            killer: defender.entity,
                        },
                        now,
                    );
                    killed_at = Some(tick);
                }
            }
            room.finish_tick(
                &queries,
                tick,
                Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
            )
            .unwrap();
            // Keep the declared defender invulnerable while real enemy attacks destroy the gate.
            room.take_external_enemy_damage();
            for message in &room.scene_messages {
                if message.message == 0x0240_0000_0000_06a4 {
                    assert_eq!(message.target, GAME_MODE_ENTITY);
                    score_messages.push(message.parameters.clone());
                }
            }
            for state in room.states().map(Result::unwrap) {
                if let Some(enemy) = &state.enemy {
                    assert!(
                        enemy.fault.is_none(),
                        "enemy score input fault: {:?}",
                        enemy.fault
                    );
                }
            }
        }
        panic!("score fixture did not retain its earned score through enemy victory");
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn normal_encounter_first_reaper_finishes_intro_and_pursues_live_defender() {
        use crate::{
            game_clock::FIRST_TICK,
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            game_heroes::CASSIDY,
            game_projectiles::{OwnerProjectiles, ProjectileLeases},
            game_replication::BodyStep,
            script_enemy::EnemyTarget,
            script_player::OwnerBodySpawn,
        };
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        let cassidy = scene.heroes().find_hero("Cassidy").unwrap().clone();
        let mut mover = scene.player_spawn_mover(cassidy.body, 0).unwrap();
        let mut owner = scene
            .owner_body_runtime(
                &cassidy,
                OwnerBodySpawn {
                    slot: 0,
                    controller: 0xa000_0100,
                    entity: 0xa000_0101,
                    body_filter: crate::match_scene::team_bit(0).unwrap(),
                    frame: FIRST_TICK,
                    pose: mover.pose(),
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .unwrap();
        let mut projectile_owner = OwnerProjectiles::new(
            0xa000_0101,
            ProjectileLeases::new(0, FIRST_TICK, [0x0400_0000_0000_037d]).unwrap(),
        );
        let owner_id = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        let mut defender = EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: 0xa000_0101,
            team: 0,
            pose: TargetPose {
                feet: mover.pose().position,
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        };
        scene.set_junkenstein_defenders(Arc::from([defender.entity]));
        let mut reaper = None;
        let mut first_pose = None;
        let mut moved = false;
        let mut inflicted = 0.0;
        let mut controlled: Option<(u32, [f64; 3])> = None;
        let mut press_at = None;
        let mut last_reaper_pose = None;
        let mut impacts = 0;
        let mut launches = 0;
        let mut e_target_health = None;

        for tick in FIRST_TICK..FIRST_TICK + 12500 {
            let delta = last_reaper_pose
                .map(|position: [f64; 3]| {
                    [
                        position[0] - defender.pose.feet[0],
                        position[1] + 1.0 - defender.pose.feet[1] - 1.55,
                        position[2] - defender.pose.feet[2],
                    ]
                })
                .unwrap_or([0.0, 0.0, 1.0]);
            let command = crate::commands::Command {
                frame: tick,
                yaw: (delta[0].atan2(delta[2]) / std::f64::consts::TAU * 65536.0).round() as i32,
                pitch: ((-delta[1]).atan2(delta[0].hypot(delta[2])) / std::f64::consts::TAU
                    * 65536.0)
                    .round() as i32,
                buttons: if press_at == Some(tick) { 16 } else { 0 },
                forward: if inflicted > 0.0 && press_at.is_none() {
                    127
                } else {
                    0
                },
                ..Default::default()
            };
            if tick > FIRST_TICK {
                mover
                    .step(scene.movement_index(), command, 0.016, |mover, command| {
                        owner.advance(command, mover, BodyStep::Posed).unwrap();
                        for (frame, change) in owner.take_velocity_changes() {
                            mover.queue_velocity_change(frame, change);
                        }
                        mover.track_modifiers(command.frame, owner.movement_modifiers().unwrap());
                    })
                    .unwrap();
                defender.pose.feet = mover.pose().position;
            }
            // Use actual owner lease admission and the authored flight fields.
            // Transport/create acknowledgments are outside this restricted replay.
            let mut admitted = Vec::new();
            for shot in owner.take_shots(Some(command), &mover) {
                let PosedShot::Projectile(projectile) = shot else {
                    panic!("E must launch a projectile")
                };
                assert_eq!(projectile.shot.graph, 0x01e9);
                assert_eq!(projectile.stun, Stun::from_seconds(0.8));
                let bound = (projectile.ballistics.lifetime
                    / crate::game_clock::TICK_PERIOD.as_secs_f64())
                .ceil() as u32;
                let token = projectile_owner
                    .admit(
                        projectile.shot,
                        projectile.ballistics.definition.unwrap(),
                        bound,
                    )
                    .unwrap();
                let flight = ProjectileFlight::new(
                    projectile.ballistics,
                    projectile.origin,
                    projectile.direction,
                    None,
                )
                .unwrap();
                let flight = if let Some(gravity) = projectile.gravity {
                    flight.with_gravity(gravity)
                } else {
                    flight
                };
                admitted.push(AdmittedFlight {
                    token,
                    flight: flight.with_expiry_blast(projectile.expiry_blast),
                    stun: projectile.stun,
                    hit_effect: Some(crate::script_init::Site {
                        entity: projectile.shot.entity,
                        instance: projectile.shot.instance,
                        graph: projectile.shot.graph,
                        node: Some(7),
                    }),
                });
                launches += 1;
            }
            scene.step_encounter(tick, Some(CASSIDY));
            let (room, queries) = scene.bot_simulation();
            room.activate_combat();
            // Isolate the boss handoff: retire common attackers through normal
            // damage/death, so the unprotected gate cannot end this fixture early.
            let common: Vec<_> = room
                .states()
                .map(Result::unwrap)
                .filter(|s| {
                    !s.bot.dead && !crate::npc_catalog::is_junkenstein_boss(s.bot.spawn.hero)
                })
                .map(|s| s.bot.entity)
                .collect();
            for entity in common {
                let now = Duration::from_millis(u64::from(tick - FIRST_TICK) * 16);
                RoomSimulation::damage_bot(
                    room.state_mut(entity).unwrap().unwrap(),
                    10000.0,
                    Death {
                        time: now.as_secs_f64(),
                        frame: tick,
                        killer: defender.entity,
                    },
                    now,
                );
            }
            room.set_external_targets(vec![defender.clone()]);
            let (_, hits) = room
                .advance_projectiles(
                    &queries,
                    7,
                    owner_id,
                    admitted,
                    ShotContext {
                        team: 0,
                        quanta: RETAIL_QUANTA,
                        tick,
                        now: Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
                    },
                )
                .unwrap();
            for hit in hits {
                assert_eq!(Some(hit.damage.target), reaper, "E hit another enemy");
                assert_eq!(hit.damage.amount, 25.0);
                assert_eq!(
                    state(room, reaper.unwrap()).unwrap().vitals.total(),
                    e_target_health.unwrap() - 25.0,
                    "actual NPC health after E"
                );
                owner.receive_applied_damage(hit.damage).unwrap();
                impacts += 1;
                assert!(controlled.is_none(), "one E must not stun repeatedly");
                let bot = &state(room, reaper.unwrap()).unwrap().bot;
                assert!(bot.is_stunned(tick));
                controlled = Some((tick, bot.position));
                eprintln!("actual E impact tick={tick}, position={:?}", bot.position);
            }
            room.finish_tick(
                &queries,
                tick,
                Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
            )
            .unwrap();
            for state in room.states().map(Result::unwrap) {
                let Some(enemy) = state.enemy.as_ref() else {
                    continue;
                };
                assert!(
                    enemy.fault.is_none(),
                    "tick {tick} hero {:x}: {:?}",
                    enemy.binding.hero,
                    enemy.fault
                );
                if state.bot.spawn.hero == REAPER {
                    last_reaper_pose = Some(state.bot.position);
                    if reaper.is_none() {
                        reaper = Some(state.bot.entity);
                        first_pose = Some(state.bot.position);
                        eprintln!(
                            "first Reaper spawned tick {tick} at {:?}",
                            state.bot.position
                        );
                    }
                    if Some(state.bot.entity) == reaper {
                        moved |= norm(std::array::from_fn(|i| {
                            state.bot.position[i] - first_pose.unwrap()[i]
                        })) > 3.0;
                        if tick % 1000 == 0 {
                            eprintln!(
                                "Reaper tick {tick}: {:?}, handoff {}, exit {}, pause {}, aim {:?}, facing {:?}",
                                state.bot.position,
                                enemy.boss_handoff_complete,
                                enemy.reaper_exit.is_some(),
                                enemy.scripts.boss_pause_active(),
                                enemy.scripts.cinematic_aim(),
                                enemy.scripts.facing_direction()
                            );
                        }
                    }
                }
            }
            let damage = room
                .take_external_enemy_damage()
                .into_iter()
                .filter(|hit| Some(hit.source) == reaper && hit.target == defender.entity)
                .map(|hit| hit.amount)
                .sum::<f64>();
            inflicted += damage;
            if let Some((start, position)) = controlled {
                let state = state(room, reaper.unwrap()).unwrap();
                let enemy = state.enemy.as_ref().unwrap();
                let snapshot = enemy.scripts.body.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                if tick == start {
                    assert_eq!(
                        snapshot
                            .instances
                            .values()
                            .filter(|i| i.graph == 0x224e)
                            .count(),
                        1
                    );
                }
                let encoder =
                    crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                encoder
                    .full(
                        &encoder
                            .project(snapshot, crate::script_snapshot_wire::View::Observer)
                            .unwrap(),
                    )
                    .unwrap();
                if tick < start + 50 {
                    assert_eq!(
                        state.bot.position, position,
                        "controlled Reaper moved at {tick}"
                    );
                    assert_eq!(damage, 0.0, "controlled Reaper fired at {tick}");
                } else if damage > 0.0 {
                    assert_eq!((launches, impacts), (1, 1));
                    assert!(!state.bot.is_stunned(tick));
                    let snapshot = owner.snapshot().unwrap();
                    assert!(
                        snapshot.initialization_complete(),
                        "{:?}",
                        snapshot.diagnostics
                    );
                    let encoder = owner.encoder();
                    encoder
                        .full(
                            &encoder
                                .project(snapshot, crate::script_snapshot_wire::View::Owner)
                                .unwrap(),
                        )
                        .unwrap();
                    eprintln!("Reaper resumed combat at {tick} after actual E impact at {start}");
                    return;
                }
            } else if moved && inflicted > 0.0 && press_at.is_none() {
                let position = last_reaper_pose.unwrap();
                if (position[0] - defender.pose.feet[0]).hypot(position[2] - defender.pose.feet[2])
                    < 6.0
                {
                    press_at = Some(tick + 1);
                    e_target_health = Some(state(room, reaper.unwrap()).unwrap().vitals.total());
                    eprintln!(
                        "actual E press scheduled: tick={}, Cassidy={:?}, Reaper={position:?}",
                        tick + 1,
                        defender.pose.feet
                    );
                }
            }
            assert!(
                scene.encounter_snapshot().is_some(),
                "encounter stopped at tick {tick}"
            );
        }
        assert!(
            reaper.is_some(),
            "normal authored encounter did not spawn Reaper"
        );
        assert!(
            moved,
            "Reaper never left his authored introduction position"
        );
        assert!(inflicted > 0.0, "Reaper never attacked the living defender");
        panic!("Reaper did not resume combat after its control window");
    }

    #[test]
    fn scene_enemy_creation_is_atomic_and_retains_late_join_health_and_skin() {
        use crate::{
            script_enemy::{EnemySpawn, fixture},
            script_wire::Variables,
        };
        let mut room = RoomSimulation::new(Vec::new()).unwrap();
        for fail in [true, false, false] {
            let (graphs, codecs, definition, request) = fixture(fail);
            let next = room.next_bot;
            let count = room.bodies().unwrap().len();
            let result = room.create_enemy(EnemySpawn {
                graphs,
                codecs,
                definition: &definition,
                request: &request,
                mode: 0x0230_0000_0000_019f,
                mode_variables: Arc::new(Variables::new()),
                tick: 1000,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            });
            if fail {
                assert!(result.unwrap_err().starts_with("enemy controller startup:"));
                assert_eq!(room.next_bot, next);
                assert_eq!(room.bodies().unwrap().len(), count);
                assert!(room.take_created().is_empty());
                continue;
            }
            let entity = result.unwrap();
            assert_eq!(entity, next);
            assert_eq!(room.next_bot, next + 2);
            assert_eq!(room.bodies().unwrap().len(), count + 1);
            let updates = room.take_created();
            assert_eq!(updates.len(), 1);
            assert_eq!(room.initial_creates().unwrap().last().unwrap(), &updates[0]);
            let enemy = state(&room, entity).unwrap().enemy.as_ref().unwrap();
            assert_eq!(enemy.binding.controller, entity + 1);
            assert_eq!(
                enemy.scripts.controller.snapshot().unwrap().entity,
                entity + 1
            );
            assert_eq!(room.enemy_projections().unwrap().count(), count + 1);
            let hit = room
                .apply_bot_impact(1, 1000, FIXTURE_CONTEXT, &fixture_impact(entity, 500.0))
                .unwrap()
                .unwrap();
            assert!(matches!(
                hit.notices[0],
                Some(CombatNotice::Hit { killed: true, .. })
            ));
            assert!(state(&room, entity).unwrap().respawn_at.is_none());
            let late = room.initial_creates().unwrap().pop().unwrap();
            let Some(Operation::Create(create)) = late.operation else {
                panic!("late join create")
            };
            assert_eq!(create.skin.guid, 0x0b20_0000_0000_0010);
            assert_ne!(room.initial_creates().unwrap().last().unwrap(), &updates[0]);
        }
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein NPC catalog and graph bundle"]
    fn authored_zomnic_starts_both_components_before_room_publication() {
        use crate::{
            npc_catalog::NpcCatalog,
            script_codecs::CodecTable,
            script_enemy::EnemySpawn,
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation},
            script_wire::{Value, Variables},
        };
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap());
        let catalog =
            NpcCatalog::load(root.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        let definition = catalog.get(0x02e0_0000_0000_0175).unwrap();
        let request = EntityCreation {
            position: [74.0, 12.6, -99.0],
            kind: CreationKind::Hero {
                hero: definition.hero,
                team: 1,
                yaw: 180.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: None,
                skin: None,
            },
        };
        let mut room = RoomSimulation::new(Vec::new()).unwrap();
        let entity = room
            .create_enemy(EnemySpawn {
                graphs,
                codecs,
                definition,
                request: &request,
                mode: 0x0230_0000_0000_019f,
                mode_variables: Arc::new(Variables::new()),
                tick: 4200,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            })
            .unwrap();
        assert_eq!(entity, BOT_ENTITY);
        assert_eq!(room.next_bot, BOT_ENTITY + 2);
        assert_eq!(room.bodies().unwrap().len(), 1);
        assert_eq!(room.world.entities().len(), 1);
        assert_eq!(room.take_created().len(), 1);
        assert_eq!(room.enemy_projections().unwrap().count(), 1);
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_mut().unwrap();
        assert_eq!(enemy.binding.body, entity);
        assert_eq!(enemy.binding.controller, entity + 1);
        assert_eq!(state.vitals.maximum(), 200.0);
        let requests = enemy.scripts.behavior_requests().collect::<Vec<_>>();
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[0].class, "STU_600E3C59");
        assert_eq!(requests[0].priority, 10.0);
        assert_eq!(requests[0].inputs, [2.0, 2.0, 1.0, f64::from(0.2f32), 1.0]);
        assert_eq!(requests[1].class, "STU_B61E6681");
        assert_eq!(requests[1].priority, 0.0);
        assert_eq!(requests[1].inputs, [1.5, 1.0, 0.0, 0.0, 0.0]);
        assert!(
            enemy
                .scripts
                .body
                .snapshot()
                .unwrap()
                .initialization_complete()
        );
        assert!(
            enemy
                .scripts
                .controller
                .snapshot()
                .unwrap()
                .initialization_complete()
        );
    }

    #[test]
    #[ignore = "requires external 104319 encounter body definitions"]
    fn boss_controller_mismatch_is_rejected_before_room_allocation() {
        use crate::{
            game_heroes::HeroCatalog,
            npc_catalog::encounter_definition,
            script_codecs::CodecTable,
            script_enemy::EnemySpawn,
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation},
            script_wire::{Value, Variables},
        };
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap());
        let heroes = HeroCatalog::load(root.join("data/game_heroes_174.json")).unwrap();
        for index in [2, 4, 0x16, 0x40, 0x65] {
            let hero = 0x02e0_0000_0000_0000 | index;
            let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
            for controller in [None, Some(0x0400_0000_0000_ffff)] {
                let mut room = RoomSimulation::new(Vec::new()).unwrap();
                let next = room.next_bot;
                let request = EntityCreation {
                    position: [74.0, 12.6, -99.0],
                    kind: CreationKind::Hero {
                        hero,
                        team: 1,
                        yaw: 180.0,
                        flags: [Value::Bool(false), Value::Bool(false)],
                        controller,
                        skin: None,
                    },
                };
                let result = room.create_enemy(EnemySpawn {
                    graphs: Arc::clone(&graphs),
                    codecs: Arc::clone(&codecs),
                    definition: &definition,
                    request: &request,
                    mode: 0x0230_0000_0000_019f,
                    mode_variables: Arc::new(Variables::new()),
                    tick: 4200,
                    walk: crate::game_bots::BotWalk::default(),
                    gate: None,
                });
                assert_eq!(
                    result.unwrap_err(),
                    "scene enemy creation differs from its validated definition"
                );
                assert_eq!(room.next_bot, next);
                assert_eq!(room.bodies().unwrap().len(), 0);
                assert!(room.take_created().is_empty());
            }
        }
    }

    fn fixture_impact(entity: u32, damage: f64) -> Impact {
        Impact {
            tags: crate::game_hitscan::DamageTags::EMPTY,
            entity,
            distance: 1.0,
            damage,
            critical: false,
            loadout: None,
        }
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein NPC, graph and collision pack"]
    fn authored_zomnic_attack_updates_gate_damage_and_retires_the_pair() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            npc_catalog::NpcCatalog,
            script_codecs::CodecTable,
            script_enemy::{EnemyGate, EnemySpawn, SceneMessage},
            script_expr::to_float,
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation},
            script_snapshot_wire::View,
            script_stream::{BodyStream, DeltaKind},
            script_wire::{Value, Variables},
        };
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&root).unwrap();
        let maps = MapCatalog::load(root.join("data/game_maps_174.json")).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = maps
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        assert!(scene.encounter_result_wire().unwrap().is_none());
        assert!(scene.encounter_outcome_wire(1).unwrap().is_none());
        let projection = scene.gate_projection().unwrap();
        let before = projection
            .encoder
            .project(projection.snapshot, View::Observer)
            .unwrap();
        let mut stream = BodyStream::new(projection.snapshot.entity);
        let baseline = projection.encoder.queue_full(&mut stream, &before).unwrap();
        assert!(!stream.ready_to_send(baseline.last));
        stream.create_arrived();
        assert!(stream.ready_to_send(baseline.last));
        stream.sent(baseline.last);
        stream.arrived(baseline.last);
        let initial_gate_damage = scene
            .gate_snapshot()
            .unwrap()
            .variables
            .get(&8569)
            .cloned()
            .unwrap_or(Value::Null);
        let gate = scene.junkenstein_gate().unwrap();
        let gate = EnemyGate {
            entity: gate.entity,
            position: gate.position,
        };
        let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap());
        let catalog =
            NpcCatalog::load(root.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        let definition = catalog.get(0x02e0_0000_0000_0175).unwrap();
        let variables = Arc::new(scene.encounter_snapshot().unwrap().variables.clone());
        let request = EntityCreation {
            position: [gate.position[0] + 1.0, gate.position[1], gate.position[2]],
            kind: CreationKind::Hero {
                hero: definition.hero,
                team: 1,
                yaw: 0.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: None,
                skin: None,
            },
        };
        let spawn =
            |scene: &mut crate::scene_assets::PreparedScene, request: &EntityCreation, tick| {
                scene
                    .bot_simulation()
                    .0
                    .create_enemy(EnemySpawn {
                        graphs: Arc::clone(&graphs),
                        codecs: Arc::clone(&codecs),
                        definition,
                        request,
                        mode: card.mode,
                        mode_variables: Arc::clone(&variables),
                        tick,
                        walk: crate::game_bots::BotWalk::default(),
                        gate: Some(gate),
                    })
                    .unwrap()
            };
        let entity = spawn(&mut scene, &request, 4200);
        let mut saw_dead = false;
        for tick in 4200..=4800 {
            scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
            let (room, queries) = scene.bot_simulation();
            room.set_external_targets(Vec::new());
            room.finish_tick(
                &queries,
                tick,
                Duration::from_millis(u64::from(tick - 4200) * 16),
            )
            .unwrap();
            if let Some(state) = state(room, entity) {
                assert!(
                    state.enemy.as_ref().unwrap().fault.is_none(),
                    "tick {tick}: {:?}",
                    state.enemy.as_ref().unwrap().fault
                );
                if state.bot.dead {
                    if !saw_dead {
                        assert_eq!(state.vitals.death().unwrap().frame, tick);
                    }
                    saw_dead = true;
                    assert!(state.respawn_at.is_none());
                }
            }
        }
        let (room, _) = scene.bot_simulation();
        assert!(
            saw_dead,
            "Zomnic did not die: gate={gate:?}, actor={:?}",
            state(room, entity).map(|state| (
                state.bot.position,
                state.vitals.total(),
                state.enemy.as_ref().map(|enemy| &enemy.fault),
            ))
        );
        assert!(state(room, entity).is_none());
        assert_eq!(room.take_retired_enemies(), [entity]);
        assert!(!room.take_scripted_health().is_empty());
        let gate = scene.gate_snapshot().unwrap();
        assert!(gate.diagnostics.is_empty(), "{:?}", gate.diagnostics);
        assert_ne!(
            gate.variables.get(&8569),
            Some(&initial_gate_damage),
            "gate damage counter did not change: {:?}",
            gate.variables
        );
        assert_eq!(gate.instances[&1].states[&19].activation, 1);
        let projection = scene.gate_projection().unwrap();
        let after = projection
            .encoder
            .project_next(projection.snapshot, &before)
            .unwrap();
        assert!(after.instance_states(1).unwrap().contains(&0));
        let hit = projection
            .encoder
            .queue_delta(
                &mut stream,
                &after,
                DeltaKind::Observer { resend: true },
                16,
            )
            .unwrap()
            .unwrap();
        stream.sent(hit.last);
        stream.arrived(hit.last);
        assert_eq!(
            scene.take_encounter_voice_requests(),
            [crate::junkenstein_voice::VoiceRequest {
                anchor: 0x8000_0032,
                speaker: crate::world::GAME_MODE_ENTITY,
                stimulus: 0x0ee0_0000_0000_06c7,
                line: 0x0760_0000_0000_8c71,
            }]
        );
        scene.step_encounter(5400, None);
        assert!(!scene.gate_snapshot().unwrap().instances[&1].states[&19].active);
        assert!(scene.take_encounter_voice_requests().is_empty());

        let gate = scene.junkenstein_gate().unwrap().entity;
        for (tick, remaining, expected_state) in [
            (5401, 0.625, 6),
            (5402, 0.375, 7),
            (5403, 0.125, 8),
            (5404, 0.0, 8),
        ] {
            let snapshot = scene.gate_snapshot().unwrap();
            let capacity = to_float(&snapshot.resolved_bindings[&(0, 7786)]);
            assert!(capacity > 0.0);
            let consumed = to_float(&snapshot.variables[&8569]);
            scene.bot_simulation().0.queue_scene_message(SceneMessage {
                target: gate,
                message: 0x0240_0000_0000_06b2,
                parameters: Variables::from([
                    (0x3ac, Value::Float(capacity * (1.0 - remaining) - consumed)),
                    (0x12, Value::Entity(entity)),
                ]),
            });
            scene.step_encounter(tick, None);
            let projection = scene.gate_projection().unwrap();
            assert!(
                projection.snapshot.diagnostics.is_empty(),
                "{:?}",
                projection.snapshot.diagnostics
            );
            assert_eq!(to_float(&projection.snapshot.variables[&8571]), remaining);
            let next = projection
                .encoder
                .project_next(projection.snapshot, stream.base().unwrap().1)
                .unwrap();
            projection.encoder.full(&next).unwrap();
            let states = next.instance_states(1).unwrap();
            assert_eq!(
                states
                    .into_iter()
                    .filter(|raw| [0, 6, 7, 8].contains(raw))
                    .collect::<Vec<_>>(),
                [expected_state],
            );
            if let Some(delta) = projection
                .encoder
                .queue_delta(&mut stream, &next, DeltaKind::Observer { resend: true }, 16)
                .unwrap()
            {
                stream.sent(delta.last);
                stream.arrived(delta.last);
            } else {
                assert_eq!(remaining, 0.0);
            }
        }
        let notices = scene.take_encounter_voice_requests();
        assert_eq!(
            notices
                .iter()
                .map(|notice| notice.stimulus)
                .collect::<Vec<_>>(),
            [0x0ee0_0000_0000_06b8, 0x0ee0_0000_0000_06b9],
        );
        let street = EntityCreation {
            position: [74.0, 12.6, -99.0],
            ..request
        };
        let survivor = spawn(&mut scene, &street, 5405);
        let (room, queries) = scene.bot_simulation();
        let source = crate::world::GAME_MODE_ENTITY;
        let caller = crate::script_init::Site {
            entity: source,
            instance: 2,
            graph: 0x0cba,
            node: Some(9),
        };
        assert!(room.queue_encounter_wave(
            caller,
            survivor + 1,
            crate::script_init::PlayScript {
                policy: 0,
                overrides: Vec::new(),
                graph: 0x0c95,
                source,
                parameters: vec![(
                    0x0a40_0000_0000_03a1,
                    Value::Array(vec![Value::Vector(street.position)])
                )],
                auto_retire: true,
            }
        ));
        room.finish_tick(&queries, 5405, Duration::from_millis((5405 - 4200) * 16))
            .unwrap();
        assert!(room.encounter_body_alive(survivor));
        assert!(
            state(room, survivor)
                .unwrap()
                .enemy
                .as_ref()
                .unwrap()
                .scripts
                .body
                .snapshot()
                .unwrap()
                .instances
                .values()
                .any(|instance| instance.graph == 0x0cbc)
        );
        for tick in 5405..=5900 {
            scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
        }
        let (room, queries) = scene.bot_simulation();
        room.finish_tick(&queries, 5900, Duration::from_millis((5900 - 4200) * 16))
            .unwrap();
        if let Some(survivor) = state(room, survivor) {
            let enemy = survivor.enemy.as_ref().unwrap();
            let snapshot = enemy.scripts.body.snapshot().unwrap();
            panic!(
                "result did not retire live Zomnic: fault={:?} requested={} roots={:?}",
                enemy.fault,
                enemy.scripts.retirement_requested,
                snapshot
                    .instances
                    .values()
                    .map(|instance| (instance.graph, instance.variables.get(&8617)))
                    .collect::<Vec<_>>()
            );
        }
        assert_eq!(room.take_retired_enemies(), [survivor]);
        let mode = scene.encounter_snapshot().unwrap();
        let root = &mode.instances[&2];
        assert_eq!(root.variables[&59], Value::Integer(5));
        assert_eq!(root.states[&24].activation, 1);
        let handoff = root
            .states
            .get(&8)
            .unwrap_or_else(|| panic!("missing authored phase handoff: {:?}", mode.diagnostics));
        assert_eq!(handoff.activation, 1);
        assert_eq!(
            handoff.kind,
            crate::script_init::StateKind::PersistentSend(
                crate::junkenstein_messages::POST_MATCH_PHASE
            ),
        );
        let phase = scene.encounter_post_match_wire().unwrap().unwrap();
        let native_result = scene.encounter_match_result().unwrap();
        assert_eq!(native_result.index, 114);
        assert_eq!(
            native_result.fields[7],
            Some(crate::world::ComponentValue::I32(1)),
            "native recap must identify the enemy winner after gate destruction"
        );
        assert!(matches!(
            native_result.fields[9],
            Some(crate::world::ComponentValue::F32(seconds)) if seconds > 0.0
        ));
        let mut wire = crate::bits::BitReader::new(phase.as_bytes());
        assert_eq!(wire.bits(2).unwrap(), 0);
        assert_eq!(wire.w_u16().unwrap(), 2);
        assert_eq!(wire.bits(2).unwrap(), 0); // existing mode root
        assert_eq!(wire.w_u16().unwrap(), 0);
        let screens = mode
            .instances
            .values()
            .find(|instance| instance.graph == 0x180a)
            .unwrap();
        assert_eq!(
            screens.states.get(&3).map(|state| state.activation),
            Some(1),
            "{:?}",
            mode.diagnostics
        );

        assert_eq!(screens.states[&21].activation, 1);
        assert!((to_float(&screens.variables[&15076]) - 41.1).abs() < 0.0001);
        let Value::Array(sequence) = &screens.variables[&16638] else {
            panic!("the post-match screen sequence must be an array");
        };
        assert_eq!(sequence.len(), 4);
        let Value::Map(summary) = &sequence[3] else {
            panic!("the fourth screen phase must have a configuration map");
        };
        assert!(summary.iter().any(|(key, value)| {
            *key == Value::Integer(16748)
                && matches!(value, Value::Array(graphs) if graphs.first() == Some(&Value::Asset(0x0580_0000_0000_0283)))
        }));

        let (&result_id, results) = mode
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == 0x0cc7)
            .unwrap();
        assert_eq!(results.states[&2].activation, 1);
        assert_eq!(
            results.states[&2].kind,
            crate::script_init::StateKind::PersistentSend(
                crate::junkenstein_messages::RESULT_SUMMARY
            ),
        );
        assert_eq!(mode.variables[&8525], Value::Integer(0));
        let payload = scene.encounter_result_wire().unwrap().unwrap();
        let mut wire = crate::bits::BitReader::new(payload.as_bytes());
        assert_eq!(wire.bits(2).unwrap(), 0);
        assert_eq!(wire.w_u16().unwrap(), result_id);
        assert_eq!(wire.bits(2).unwrap(), 2);
        assert_eq!(wire.bits(16).unwrap(), 0x0cc7);
        assert_eq!(wire.bits(2).unwrap(), 0);
        assert!(wire.bit().unwrap());
        assert_eq!((wire.w_var().unwrap(), wire.w_var().unwrap()), (2, 13));
        let outcome = scene.encounter_outcome_wire(41).unwrap().unwrap();
        let mut wire = crate::bits::BitReader::new(outcome.as_bytes());
        assert_eq!(wire.bits(2).unwrap(), 0);
        assert_eq!(wire.w_u16().unwrap(), 0);
        assert!(!wire.bit().unwrap()); // empty entity variables
        assert!(wire.bit().unwrap()); // frame action present
        assert_eq!(wire.w_u32().unwrap(), 41);
        wire.w_var().unwrap();
        assert_eq!(wire.bits(2).unwrap(), 1);
        wire.bits(8).unwrap();
        assert!(wire.bit().unwrap());
        assert_eq!(wire.w_u16().unwrap(), 2);
        assert_eq!(wire.bits(6).unwrap(), 45);
        assert_eq!(wire.bits(3).unwrap(), 0);
        let gate = scene.gate_snapshot().unwrap();
        assert_eq!(gate.instances[&1].variables[&1303], Value::Integer(3));
        let messages: Vec<_> = scene
            .bot_simulation()
            .0
            .take_defender_messages()
            .into_iter()
            .filter(|message| message.message == crate::junkenstein_messages::ENCOUNTER_RESULT)
            .collect();
        let [result] = messages.as_slice() else {
            panic!("expected one authored match result, received {messages:?}");
        };
        assert_eq!(result.source, crate::world::GAME_MODE_ENTITY);
        assert!(!crate::script_expr::truthy(&result.parameters[&0x3ae]));
        assert_eq!(result.parameters[&0x3b5], Value::Integer(3));
        let hero = scene.heroes().find_hero("Cassidy").unwrap();
        let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
        let pose = mover.pose();
        let mut owner = scene
            .owner_body_runtime(
                hero,
                crate::script_player::OwnerBodySpawn {
                    slot: 0,
                    controller: 0xa000_0100,
                    entity: 0xa000_0101,
                    body_filter: crate::match_scene::team_bit(0).unwrap(),
                    frame: 5900,
                    pose,
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .unwrap();
        assert_eq!(owner.receive_defender_message(result.clone()).unwrap(), 1);
        let snapshot = owner.snapshot().unwrap();
        let defender = snapshot
            .instances
            .values()
            .find(|instance| instance.graph == 0x0ca5)
            .unwrap();
        assert_eq!(defender.states[&32].activation, 1);
        assert_eq!(defender.variables[&8504], Value::Integer(3));
        assert!(
            snapshot.diagnostics.is_empty(),
            "{:?}",
            snapshot.diagnostics
        );
        let encoder = owner.encoder();
        let projected = encoder.project(snapshot, View::Owner).unwrap();
        encoder.full(&projected).unwrap();
        assert_eq!(owner.cinematic_aim(), Some([pose.yaw, pose.pitch]));
        mover.set_cinematic_aim(owner.cinematic_aim());
        mover
            .step(
                scene.movement_index(),
                Command {
                    frame: 5901,
                    yaw: pose.yaw.wrapping_add(1200),
                    pitch: pose.pitch.wrapping_sub(1200),
                    ..Command::default()
                },
                0.016,
                |_, _| {},
            )
            .unwrap();
        assert_eq!(
            [mover.pose().yaw, mover.pose().pitch],
            [pose.yaw, pose.pitch]
        );
        let mut phases = Vec::new();
        let mut previous = Vec::new();
        let mut summary_instance = None;
        let mut screen_projection = None;
        assert!(!scene.encounter_screens_complete());

        for tick in 5901..=8500 {
            scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
            let mode = scene.encounter_snapshot().unwrap();
            let scopes: Vec<_> = mode
                .instances
                .iter()
                .filter_map(|(&id, instance)| {
                    let (scope, 16) = instance.parent? else {
                        return None;
                    };
                    let (manager, 17) = mode.instances.get(&scope)?.parent? else {
                        return None;
                    };
                    (mode.instances[&manager].graph == 0x180a).then_some((id, instance.graph))
                })
                .collect();
            if let Some((id, _)) = scopes.iter().find(|(_, graph)| *graph == 0x0283) {
                assert_eq!(summary_instance.get_or_insert(*id), id);
                assert!(!scene.encounter_screens_complete());
            }
            if scopes != previous {
                let (projection, payload) = scene
                    .encounter_screen_delta(screen_projection.as_ref())
                    .unwrap()
                    .unwrap();
                if previous.is_empty() {
                    let (&manager, instance) = mode
                        .instances
                        .iter()
                        .find(|(_, instance)| {
                            instance.graph == 0x180a && instance.states.contains_key(&4)
                        })
                        .unwrap();
                    assert_eq!(instance.states[&4].activation, 1);
                    assert_eq!(
                        instance.states[&4].kind,
                        crate::script_init::StateKind::PersistentSend(
                            crate::junkenstein_messages::POST_MATCH_SIGNAL
                        )
                    );
                    assert!(projection.instance_states(manager).unwrap().contains(&4));
                    assert_eq!(
                        projection.instance_variables(manager).unwrap().get(&7525),
                        Some(&Value::Integer(1)),
                        "the client screen manager needs the resolved defeat winner"
                    );
                }
                if let Some(&(banner, _)) = scopes.iter().find(|(_, graph)| *graph == 0x027f) {
                    assert_eq!(
                        projection.instance_variables(banner).unwrap()[&7525],
                        Value::Integer(1)
                    );
                }
                if scopes.iter().any(|&(_, graph)| graph == 0x0283) {
                    assert_eq!(
                        projection.entity_variables().get(&10274),
                        Some(&Value::Bool(true)),
                        "the recap presenter needs its authored entity input"
                    );
                }
                let mut wire = crate::bits::BitReader::new(payload.as_bytes());
                assert_eq!(wire.bits(2).unwrap(), 0);
                let mut seen = std::collections::BTreeMap::new();
                let mut id = 0;
                loop {
                    let increment = wire.w_u16().unwrap();
                    if increment == 0 {
                        break;
                    }
                    id += increment;
                    if wire.bit().unwrap() {
                        assert!(!mode.instances.contains_key(&id));
                        seen.insert(id, None);
                    } else if wire.bit().unwrap() {
                        let graph = wire.bits(16).unwrap();
                        assert_eq!(wire.bits(2).unwrap(), 0);
                        let parent = if wire.bit().unwrap() {
                            Some((
                                wire.w_var().unwrap().try_into().unwrap(),
                                wire.w_var().unwrap().try_into().unwrap(),
                            ))
                        } else {
                            None
                        };
                        assert!(!wire.bit().unwrap());
                        if scopes.iter().any(|&(scope, _)| scope == id) {
                            assert_eq!(parent, None);
                        } else {
                            assert_eq!(mode.instances[&id].parent, parent);
                            assert_ne!(
                                parent.map(|(_, raw)| raw),
                                Some(17),
                                "server list row was sent"
                            );
                        }
                        assert_eq!(u64::from(mode.instances[&id].graph), graph);
                        seen.insert(id, Some(graph));
                    }
                }
                for &(id, graph) in &scopes {
                    if !previous.iter().any(|&(previous, _)| previous == id) {
                        assert_eq!(seen[&id], Some(u64::from(graph)));
                    }
                }
                for &(id, _) in &previous {
                    if !scopes.iter().any(|&(current, _)| current == id) {
                        assert_eq!(seen[&id], None);
                    }
                }
                let mut stream = crate::script_stream::Stream::new(crate::world::GAME_MODE_ENTITY);
                let initial = stream.full(scene.mode_ui()).unwrap();
                assert!(stream.arrived(initial.last));
                let chunk = stream.reliable_delta(&payload).unwrap();
                assert!(stream.data_in_flight());
                assert!(stream.arrived(chunk.last));
                assert!(!stream.data_in_flight());
                let packet =
                    crate::world::frame(u32::MAX, u32::MAX, &[], &[], &[&chunk.update]).unwrap();
                assert!(
                    packet.len() <= crate::world::MAX_PAYLOAD,
                    "screen delta too large: {}",
                    packet.len()
                );
                screen_projection = Some(projection);
                phases.push(scopes.clone());
                previous = scopes;
            }
        }
        let graphs: Vec<Vec<_>> = phases
            .iter()
            .map(|phase| {
                let mut graphs: Vec<_> = phase.iter().map(|(_, graph)| *graph).collect();
                graphs.sort_unstable();
                graphs
            })
            .collect();
        assert_eq!(
            graphs,
            [
                vec![0x027f, 0x0d94, 0x17df, 0x181e, 0x184c],
                vec![0x0281, 0x17df, 0x17e2, 0x181d, 0x184c, 0x1f93],
                vec![0x0282, 0x17df, 0x17e2, 0x1818, 0x184d],
                vec![0x0283, 0x0b46, 0x17e3, 0x17e4, 0x184d],
                vec![],
            ]
        );
        for (graph, earlier, later) in [(0x17df, 0, 1), (0x17e2, 1, 2), (0x184d, 2, 3)] {
            let id = |index: usize| {
                phases[index]
                    .iter()
                    .find(|(_, candidate)| *candidate == graph)
                    .unwrap()
                    .0
            };
            assert_eq!(id(earlier), id(later));
        }
        let summary_instance = summary_instance.expect("one authored results graph");
        assert!(scene.encounter_screens_complete());
        assert!(
            !scene
                .encounter_snapshot()
                .unwrap()
                .instances
                .contains_key(&summary_instance)
        );
    }

    const FIXTURE_CONTEXT: ShotContext = ShotContext {
        team: 0,
        quanta: RETAIL_QUANTA,
        tick: 1000,
        now: Duration::from_secs(4),
    };

    fn state(bots: &RoomSimulation, wire_id: u32) -> Option<&BotState> {
        bots.world.get(*bots.by_wire.get(&wire_id)?)
    }

    fn flight_fixture(owner: BodyId, speed: f64) -> AdmittedFlight {
        use crate::game_projectiles::{OwnerProjectiles, ProjectileLeases};

        let frame = FIXTURE_CONTEXT.tick;
        let definition = 0x0400_0000_0000_072c;
        let leases = ProjectileLeases::new(0, frame, [definition]).unwrap();
        let token = OwnerProjectiles::new(owner.entity, leases)
            .admit(
                ShotEvent {
                    entity: owner.entity,
                    instance: 1,
                    graph: 42,
                    raw_state: 0,
                    frame,
                    time_ms: 0,
                    activation: 1,
                    activation_ms: 0,
                    ordinal: 0,
                },
                definition,
                3,
            )
            .unwrap();
        let flight = ProjectileFlight::new(
            ProjectileBallistics {
                speed,
                lifetime: 0.04,
                definition: Some(definition),
                direct: None,
                blast: None,
            },
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            None,
        )
        .unwrap();
        AdmittedFlight {
            token,
            flight,
            stun: None,
            hit_effect: None,
        }
    }

    #[test]
    fn projectile_stun_applies_only_to_surviving_hits_and_expires_exclusively() {
        use crate::{
            collision_cache::{CollisionCache, Mesh, Triangle},
            collision_scene::CollisionScene,
            mover_settings::MoverCatalog,
        };
        let cache = CollisionCache {
            header: serde_json::from_str(
                r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
            )
            .unwrap(),
            meshes: vec![Mesh {
                vertices: vec![[-1.0, 0.0, 0.0], [-1.0, 2.0, 0.0], [-1.0, 0.0, 1.0]],
                triangles: vec![Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                }],
            }],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        let ground = CollisionScene::movement_probe(&cache, &Default::default()).unwrap();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground, None);
        assert_eq!(Stun::from_seconds(f64::from(0.8f32)).unwrap().ticks, 50);
        assert_eq!(Stun::from_seconds(f64::from(1.7f32)).unwrap().ticks, 107);
        for value in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(Stun::from_seconds(value).is_none());
        }
        for amount in [25.0, 1000.0] {
            let bots = fixture_bots();
            let victim = bots[0].entity;
            let unhit = bots[1].entity;
            let mut room = RoomSimulation::new(bots).unwrap();
            let owner = BodyId {
                entity: 0xa000_0101,
                generation: 1,
            };
            let mut launch = flight_fixture(owner, 100.0);
            launch.stun = Stun::from_seconds(0.8);
            launch.flight = ProjectileFlight::new(
                ProjectileBallistics {
                    speed: 100.0,
                    lifetime: 1.0,
                    definition: None,
                    direct: Some(HitDefinition {
                        tags: crate::game_hitscan::DamageTags::EMPTY,
                        amount,
                        loadout: None,
                        critical: 1.0,
                        falloff: None,
                    }),
                    blast: None,
                },
                [0.0, 1.0, 0.0],
                [1.0, 0.0, 0.0],
                None,
            )
            .unwrap();
            let (_, hits) = room
                .advance_projectiles(&queries, 7, owner, vec![launch], FIXTURE_CONTEXT)
                .unwrap();
            assert_eq!(hits.len(), 1);
            let bot = &state(&room, victim).unwrap().bot;
            assert_eq!(bot.is_stunned(1049), amount == 25.0);
            assert!(!bot.is_stunned(1050));
            assert!(!state(&room, unhit).unwrap().bot.is_stunned(1001));
        }
    }

    fn fixture_ground() -> crate::collision_scene::CollisionScene {
        use crate::{
            collision_cache::{CollisionCache, Mesh, Triangle},
            collision_scene::CollisionScene,
        };

        let cache = CollisionCache {
            header: serde_json::from_str(
                r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
            )
            .unwrap(),
            meshes: vec![Mesh {
                vertices: vec![[-1.0, 0.0, 0.0], [-1.0, 2.0, 0.0], [-1.0, 0.0, 1.0]],
                triangles: vec![Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                }],
            }],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        CollisionScene::movement_probe(&cache, &Default::default()).unwrap()
    }

    #[test]
    fn projectile_flights_keep_peer_generation_and_report_terminal_outcomes() {
        use crate::mover_settings::MoverCatalog;

        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground, None);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let mut context = FIXTURE_CONTEXT;
        let owner = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        let other = BodyId {
            entity: 0xa100_0101,
            generation: 1,
        };
        let first = flight_fixture(owner, 1.0);
        let second = flight_fixture(other, 1.0);
        let token = first.token;
        assert_eq!(token, second.token);
        for (connection, owner, flight) in [(7, owner, first), (8, other, second)] {
            let (outcomes, hits) = simulation
                .advance_projectiles(&queries, connection, owner, vec![flight], context)
                .unwrap();
            assert!(outcomes.is_empty() && hits.is_empty());
        }
        let old_handle = simulation.projectiles[&7].order[0];
        let other_handle = simulation.projectiles[&8].order[0];

        context.tick += 3;
        let owner = BodyId {
            generation: 2,
            ..owner
        };
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, owner, Vec::new(), context)
            .unwrap();
        assert!(outcomes.is_empty() && hits.is_empty());
        assert!(simulation.world.get::<AdmittedFlight>(old_handle).is_none());
        assert!(
            simulation
                .world
                .get::<AdmittedFlight>(other_handle)
                .is_some()
        );

        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 8, other, Vec::new(), context)
            .unwrap();
        assert!(hits.is_empty());
        assert!(matches!(
            outcomes.as_slice(),
            [FlightOutcome::Ended { token: observed, travelled }]
                if *observed == token && (*travelled - 0.04).abs() < 1e-12
        ));
        assert!(
            simulation
                .world
                .get::<AdmittedFlight>(other_handle)
                .is_none()
        );

        let too_far = flight_fixture(owner, 10_000_000.0);
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, owner, vec![too_far], context)
            .unwrap();
        assert!(hits.is_empty());
        assert!(
            matches!(outcomes.as_slice(), [FlightOutcome::Failed(observed)] if *observed == token)
        );
        assert!(simulation.projectiles.is_empty());
        assert!(simulation.world.entities().is_empty());
    }

    #[test]
    fn broken_bot_indexes_are_errors_and_do_not_partially_respawn_a_batch() {
        use crate::mover_settings::MoverCatalog;

        let context = FIXTURE_CONTEXT;
        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground, None);
        let mut simulation = RoomSimulation::new(fixture_bots()).unwrap();
        let first = BOT_ENTITY;
        let second = first + 1;
        let first_handle = simulation.by_wire[&first];
        let broken_handle = simulation.by_wire[&second];
        simulation
            .apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
            .unwrap();
        simulation
            .world
            .entity_mut(broken_handle)
            .remove::<BotState>();
        let missing = |error: CombatError| {
            assert!(matches!(error, CombatError::MissingBot { wire, entity }
                if wire == second && entity == broken_handle));
        };
        missing(simulation.bodies().err().unwrap());
        missing(simulation.body_healths().unwrap_err());
        missing(simulation.debug_actors().err().unwrap());
        missing(simulation.for_each_body_mut(|_| {}).unwrap_err());
        missing(simulation.record_poses(&queries, context.tick).unwrap_err());
        missing(
            simulation
                .apply_bot_impact(7, context.tick, context, &fixture_impact(second, 1.0))
                .err()
                .unwrap(),
        );
        let next_bot = simulation.next_bot;
        missing(
            simulation
                .respawn_due(context.tick + 1, context.now + BOT_RESPAWN_TIME)
                .err()
                .unwrap(),
        );
        assert_eq!(simulation.next_bot, next_bot);
        assert_eq!(simulation.by_wire[&first], first_handle);
        assert!(state(&simulation, first).unwrap().bot.dead);

        // A present component with a different wire identity is also invalid,
        // rather than an empty body list or a different target to damage.
        let mut simulation = RoomSimulation::new(fixture_bots()).unwrap();
        simulation.body_mut(first).unwrap().entity = second;
        assert!(
            matches!(simulation.bodies(), Err(CombatError::BotIdentity { wire, actual })
            if wire == first && actual == second)
        );
    }

    #[test]
    fn missing_flight_component_is_explicit_and_retirement_is_idempotent() {
        use crate::mover_settings::MoverCatalog;

        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground, None);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let owner = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        simulation
            .advance_projectiles(
                &queries,
                7,
                owner,
                vec![flight_fixture(owner, 1.0)],
                FIXTURE_CONTEXT,
            )
            .unwrap();
        let entity = simulation.projectiles[&7].order[0];
        simulation
            .world
            .entity_mut(entity)
            .remove::<AdmittedFlight>();
        assert!(matches!(
            simulation.advance_projectiles(&queries, 7, owner, Vec::new(), FIXTURE_CONTEXT),
            Err(CombatError::MissingFlight { connection: 7, entity: missing }) if missing == entity
        ));
        simulation.retire_projectiles(7);
        simulation.retire_projectiles(7);
        simulation.clear_projectiles();
        simulation.clear_projectiles();
        assert!(simulation.world.entities().is_empty());
        assert!(simulation.projectiles.is_empty());
    }

    #[test]
    fn stale_generation_does_not_retire_or_advance_the_replacement_flight() {
        use crate::mover_settings::MoverCatalog;

        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground, None);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let owner = BodyId {
            entity: 0xa000_0102,
            generation: 2,
        };
        let old = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        simulation
            .advance_projectiles(
                &queries,
                7,
                owner,
                vec![flight_fixture(owner, 1.0)],
                FIXTURE_CONTEXT,
            )
            .unwrap();
        let handle = simulation.projectiles[&7].order[0];
        let later = ShotContext {
            tick: FIXTURE_CONTEXT.tick + 3,
            ..FIXTURE_CONTEXT
        };
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, old, vec![flight_fixture(old, 1.0)], later)
            .unwrap();
        assert!(outcomes.is_empty() && hits.is_empty());
        assert_eq!(simulation.projectiles[&7].order, [handle]);
        assert_eq!(simulation.world.entities().len(), 1);
        assert!(matches!(
            simulation.advance_projectiles(
                &queries,
                7,
                BodyId {
                    entity: old.entity,
                    ..owner
                },
                Vec::new(),
                later
            ),
            Err(CombatError::FlightOwnerIdentity { .. })
        ));
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, owner, Vec::new(), later)
            .unwrap();
        assert!(hits.is_empty());
        assert!(
            matches!(outcomes.as_slice(), [FlightOutcome::Ended { travelled, .. }]
            if (*travelled - 0.04).abs() < 1e-12)
        );
        assert!(simulation.world.entities().is_empty());
    }

    #[test]
    fn duplicate_or_exhausted_bot_identities_are_rejected_at_admission() {
        let mut bots = fixture_bots();
        bots[1].entity = bots[0].entity;
        assert!(matches!(
            RoomSimulation::new(bots),
            Err(CombatError::BotIdInUse(BOT_ENTITY))
        ));
        let mut bots = fixture_bots();
        bots[1].entity = u32::MAX;
        assert!(matches!(
            RoomSimulation::new(bots),
            Err(CombatError::BotIdsExhausted)
        ));
    }

    #[test]
    fn bot_damage_history_and_respawn_follow_identity_after_scene_reorder() {
        use crate::game_hitscan::TargetPose;

        let mut bots = RoomSimulation::new(fixture_bots()).unwrap();
        bots.activate_combat();
        let first = BOT_ENTITY;
        let second = BOT_ENTITY + 1;
        let first_handle = bots.by_wire[&first];
        let second_handle = bots.by_wire[&second];
        let context = FIXTURE_CONTEXT;
        let pose = TargetPose {
            feet: state(&bots, first).unwrap().bot.position,
            height: 1.8,
            radius: 0.5,
            eye: 1.6,
        };
        let second_pose = TargetPose {
            feet: state(&bots, second).unwrap().bot.position,
            ..pose
        };
        bots.state_mut(first)
            .unwrap()
            .unwrap()
            .history
            .record(context.tick, pose);
        bots.state_mut(second)
            .unwrap()
            .unwrap()
            .history
            .record(context.tick, second_pose);

        bots.by_wire.swap_indices(0, 1);
        bots.body_mut(first).unwrap().position = [7.0, 0.0, 0.0];
        let result = bots
            .apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
            .unwrap()
            .unwrap();
        let due = context.now + BOT_RESPAWN_TIME;
        let view = ViewFrame::from_command(
            &Command {
                frame: context.tick,
                ..Command::default()
            },
            context.quanta,
        );
        assert_eq!(result.health.entity, first);
        assert_eq!(
            result.notices[1],
            Some(CombatNotice::Elimination {
                killer: 7,
                victim: first
            })
        );
        let actors = bots.debug_actors().unwrap().collect::<Vec<_>>();
        assert_eq!(actors[0].entity, second);
        assert!(actors[0].alive);
        assert_eq!(actors[0].health, Some(100.0));
        assert_eq!(actors[1].entity, first);
        assert_eq!(actors[1].position, [7.0, 0.0, 0.0]);
        assert!(!actors[1].alive);
        assert_eq!(actors[1].health, Some(0.0));
        let target = state(&bots, first).unwrap();
        assert_eq!(target.respawn_at, Some(due));
        assert_eq!(target.history.at(view, second_pose), Some(pose));
        let other = state(&bots, second).unwrap();
        assert_eq!(other.respawn_at, None);
        assert_eq!(other.history.at(view, pose), Some(second_pose));

        let replacements = bots.respawn_due(context.tick + 1, due).unwrap();
        let [replacement] = replacements.as_slice() else {
            panic!("expected one due replacement");
        };
        let fresh = replacement.created.entity;
        assert_eq!(replacement.retired, first);
        assert_eq!(fresh, BOT_ENTITY + 2);
        assert_eq!(
            bots.by_wire.keys().copied().collect::<Vec<_>>(),
            [second, fresh]
        );
        assert!(state(&bots, first).is_none());
        assert!(bots.world.get::<BotState>(first_handle).is_none());
        assert_eq!(bots.by_wire[&second], second_handle);
        let target = state(&bots, fresh).unwrap();
        assert!(!target.bot.dead);
        assert_eq!(target.bot.position, pose.feet);
        assert_eq!(target.vitals.total(), 100.0);
        assert_eq!(target.respawn_at, None);
        assert_eq!(target.history.at(view, pose), None);
        assert_eq!(
            state(&bots, second).unwrap().history.at(view, pose),
            Some(second_pose)
        );
        assert!(
            bots.apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn failed_respawn_batch_preserves_all_due_bodies() {
        let mut bots = RoomSimulation::new(fixture_bots()).unwrap();
        let identities = [BOT_ENTITY, BOT_ENTITY + 1];
        let handles = [bots.by_wire[&identities[0]], bots.by_wire[&identities[1]]];
        let context = FIXTURE_CONTEXT;
        for identity in identities {
            bots.apply_bot_impact(7, context.tick, context, &fixture_impact(identity, 100.0))
                .unwrap();
        }
        let due = context.now + BOT_RESPAWN_TIME;
        let first_free = u32::MAX - 1;
        bots.next_bot = first_free;

        assert!(bots.respawn_due(context.tick + 1, due).is_err());
        assert_eq!(bots.next_bot, first_free);
        assert_eq!(bots.by_wire.keys().copied().collect::<Vec<_>>(), identities);
        for (identity, handle) in identities.into_iter().zip(handles) {
            assert_eq!(bots.by_wire[&identity], handle);
            let target = state(&bots, identity).unwrap();
            assert!(target.bot.dead);
            assert_eq!(target.vitals.total(), 0.0);
            assert_eq!(target.respawn_at, Some(due));
        }
    }

    #[test]
    fn biotic_field_ticks_are_bounded_and_capsule_scoped() {
        let mut field = BioticField::new([0.0; 3]);
        for tick in [100, 111, 112] {
            let (pulse, expired) = field.advance(tick);
            assert!(pulse.is_none() && !expired);
        }
        let (pulse, expired) = field.advance(115);
        assert!(!expired);
        let pulse = pulse.unwrap();
        assert_eq!(pulse.amount, 3.0 * HEAL_PER_TICK);
        let pose = TargetPose {
            feet: [5.5, 0.0, 0.0],
            height: 2.0,
            radius: 0.5,
            eye: 1.55,
        };
        assert!(pulse.reaches(pose));
        assert!(!pulse.reaches(TargetPose {
            feet: [5.501, 0.0, 0.0],
            ..pose
        }));
        assert!(field.advance(115).0.is_none());
        let (tail, expired) = field.advance(900);
        assert!(expired);
        assert!((pulse.amount + tail.unwrap().amount - 312.0 * HEAL_PER_TICK).abs() < 1e-9);
        assert!(field.advance(901).0.is_none());
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range scene"]
    fn biotic_field_replicates_healing_only_for_live_allies_in_range() {
        let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
        let mut scene = SceneAssets::load(runtime)
            .unwrap()
            .prepare(GameMap::practice_range())
            .unwrap();
        let (bots, queries) = scene.bot_simulation();
        let [near, far, dead] = {
            let mut allies = bots
                .bodies()
                .unwrap()
                .filter(|bot| bot.spawn.team == 0)
                .map(|bot| bot.entity);
            std::array::from_fn(|_| allies.next().unwrap())
        };
        let enemy = bots
            .bodies()
            .unwrap()
            .find(|bot| bot.spawn.team == 1)
            .unwrap()
            .entity;
        for (id, position) in [
            (near, [1.0, 0.0, 0.0]),
            (far, [7.0, 0.0, 0.0]),
            (dead, [1.0, 0.0, 0.0]),
            (enemy, [1.0, 0.0, 0.0]),
        ] {
            let state = bots.state_mut(id).unwrap().unwrap();
            state.bot.position = position;
            state.vitals.damage(
                if id == dead { 1_000.0 } else { 20.0 },
                Death {
                    frame: 100,
                    time: 1.6,
                    killer: 1,
                },
            );
        }
        bots.body_mut(dead).unwrap().died(100);
        let mut field = BioticField::new([0.0; 3]);
        field.advance(100);
        let pulse = field.advance(117).0.unwrap();
        bots.by_wire.swap_indices(
            bots.by_wire.get_index_of(&near).unwrap(),
            bots.by_wire.get_index_of(&enemy).unwrap(),
        );
        let updates = bots.heal_bots(&queries, 0, &[pulse]).unwrap();
        assert_eq!(updates.len(), 1);
        assert_eq!(updates[0].entity, near);
        let near = state(bots, near).unwrap();
        let far = state(bots, far).unwrap();
        let enemy = state(bots, enemy).unwrap();
        assert_eq!(
            near.vitals.total(),
            near.bot.spawn.health - 20.0 + pulse.amount
        );
        assert_eq!(far.vitals.total(), far.bot.spawn.health - 20.0);
        assert_eq!(enemy.vitals.total(), enemy.bot.spawn.health - 20.0);
        assert_eq!(state(bots, dead).unwrap().vitals.total(), 0.0);
    }

    #[test]
    fn respawn_encoding_failure_preserves_its_source_and_the_entire_batch() {
        let mut bots = fixture_bots();
        std::sync::Arc::get_mut(&mut bots[1].spawn).unwrap().team = u8::MAX;
        let mut simulation = RoomSimulation::new(bots).unwrap();
        let before = simulation.by_wire.clone();
        let next_bot = simulation.next_bot;
        let context = FIXTURE_CONTEXT;
        for identity in [BOT_ENTITY, BOT_ENTITY + 1] {
            simulation
                .apply_bot_impact(7, context.tick, context, &fixture_impact(identity, 100.0))
                .unwrap();
        }
        let error = simulation
            .respawn_due(context.tick + 1, context.now + BOT_RESPAWN_TIME)
            .err()
            .unwrap();
        assert!(matches!(&error, CombatError::RespawnCreate { wire, .. } if *wire == next_bot + 1));
        assert!(std::error::Error::source(&error).unwrap().is::<io::Error>());
        assert_eq!(simulation.by_wire, before);
        assert_eq!(simulation.next_bot, next_bot);
        assert!(simulation.bodies().unwrap().all(|bot| bot.dead));
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range scene"]
    fn repeated_rifle_hits_retire_and_respawn_only_the_hit_bot() {
        let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
        let mut scene = SceneAssets::load(runtime)
            .unwrap()
            .prepare(GameMap::practice_range())
            .unwrap();
        let (bots, queries) = scene.bot_simulation();
        let slot = bots
            .bodies()
            .unwrap()
            .position(|bot| bot.spawn.team == 1)
            .unwrap();
        let bot = bots.bodies().unwrap().nth(slot).unwrap();
        let entity = bot.entity;
        let [x, y, z] = bot.position;
        let count = bots.bodies().unwrap().len();
        let mut context = ShotContext {
            team: 0,
            quanta: RETAIL_QUANTA,
            tick: 1000,
            now: Duration::from_secs(4),
        };
        // Before combat activation, moving bodies must not seed rewind history.
        let cold_frame = context.tick - 1;
        let mut fallback = queries.target(&state(bots, entity).unwrap().bot);
        fallback.feet[0] += 1.0;
        let view = ViewFrame::from_command(
            &Command {
                frame: cold_frame,
                ..Command::default()
            },
            context.quanta,
        );
        assert!(
            bots.finish_tick(&queries, cold_frame, context.now)
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            state(bots, entity).unwrap().history.at(view, fallback),
            Some(fallback)
        );
        bots.activate_combat();
        bots.finish_tick(&queries, context.tick, context.now)
            .unwrap();
        let mut shot = PosedHitscan {
            source: 0xa000_0101,
            origin: [x - 8.0, y + eye_height(SOLDIER, false), z],
            direction: [1.0, 0.0, 0.0],
            command: Command {
                frame: context.tick,
                yaw: 16384,
                ..Command::default()
            },
            shot_frame: 999,
            reach: 180.0,
            hit: HitDefinition {
                tags: crate::game_hitscan::DamageTags::EMPTY,
                amount: 20.0,
                loadout: None,
                critical: 2.0,
                falloff: None,
            },
        };
        assert!(
            bots.hitscan(&queries, shot, context, |_| false)
                .unwrap()
                .is_none()
        );
        assert!(
            bots.hitscan(&queries, shot, ShotContext { team: 1, ..context }, |_| {
                true
            })
            .unwrap()
            .is_none()
        );
        for index in 0..5 {
            let result = bots
                .hitscan(&queries, shot, context, |_| true)
                .unwrap()
                .unwrap();
            let terminal = index == 4;
            assert_eq!(result.health.entity, entity);
            assert_eq!(
                result.notices[0],
                Some(CombatNotice::Hit {
                    target: entity,
                    dealt: 40.0,
                    critical: true,
                    killed: terminal,
                    shot_frame: shot.shot_frame,
                })
            );
            assert_eq!(
                result.notices[1],
                terminal.then_some(CombatNotice::Elimination {
                    killer: shot.source,
                    victim: entity,
                })
            );
            assert_eq!(
                state(bots, entity).unwrap().vitals.total(),
                160.0 - f64::from(index) * 40.0
            );
            assert_eq!(bots.bodies().unwrap().nth(slot).unwrap().dead, terminal);
        }
        assert!(
            bots.hitscan(&queries, shot, context, |_| true)
                .unwrap()
                .is_none()
        );
        let due = context.now + BOT_RESPAWN_TIME;
        assert!(
            bots.finish_tick(&queries, 1187, due - Duration::from_millis(1))
                .unwrap()
                .is_empty()
        );
        let [replacement] = bots
            .finish_tick(&queries, 1188, due)
            .unwrap()
            .try_into()
            .ok()
            .unwrap();
        assert_eq!(replacement.retired, entity);
        let next = BOT_ENTITY + count as u32;
        assert_eq!(replacement.created.entity, next);
        assert!(matches!(
            replacement.created.operation,
            Some(Operation::Create(_))
        ));
        assert!(!bots.bodies().unwrap().nth(slot).unwrap().dead);
        assert_eq!(state(bots, next).unwrap().vitals.total(), 200.0);
        context.tick = 1188;
        context.now = due;
        assert!(
            bots.hitscan(&queries, shot, context, |_| true)
                .unwrap()
                .is_none()
        );
        shot.command.frame = context.tick;
        assert!(
            bots.hitscan(&queries, shot, context, |visible| visible == next)
                .unwrap()
                .is_some()
        );
        assert_eq!(state(bots, next).unwrap().vitals.total(), 160.0);
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein scene and NPC graphs"]
    fn normal_street_wave_keeps_the_zomnic_body_running_after_setup() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            game_instance::RETAIL_QUANTA,
            game_replication::BodyStep,
            match_scene::team_bit,
            scene_assets::SceneAssets,
            script_init::StateKind,
            script_player::OwnerBodySpawn,
            script_snapshot_wire::{SnapshotEncoder, View},
        };

        const DEFENDERS: [u32; 2] = [0xa000_0101, 0xa000_0102];
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = SceneAssets::load(&runtime)
            .unwrap()
            .prepare_junkenstein(map, card, 0)
            .unwrap();
        let cassidy = scene.heroes().find_hero("Cassidy").unwrap();
        let mover = scene.player_spawn_mover(cassidy.body, 0).unwrap();
        let mut owner = scene
            .owner_body_runtime(
                cassidy,
                OwnerBodySpawn {
                    slot: 0,
                    controller: 0xa000_0100,
                    entity: DEFENDERS[0],
                    body_filter: team_bit(0).unwrap(),
                    frame: 2700,
                    pose: mover.pose(),
                    quanta: RETAIL_QUANTA,
                },
            )
            .unwrap()
            .unwrap();
        let cassidy = cassidy.guid;
        let full_health = owner.vitals().unwrap().total();
        owner.take_health_update();
        let gate_position = scene.junkenstein_gate().unwrap().position;
        let gate_damage_before = scene.gate_snapshot().unwrap().variables.get(&8569).cloned();
        scene.bot_simulation().0.activate_combat();
        let mut first_active = None;
        let mut first_body = None;
        let mut checked_setup = false;
        let mut checked_contacts = false;
        let mut saw_dead = false;
        let mut moved_from_spawn = false;
        let mut spawn_position = None;
        let mut victims = Vec::new();
        let mut checked_hurt_state = false;
        let mut saw_authored_speed = false;
        for tick in 2700..=5800 {
            scene.step_encounter(tick, Some(cassidy));
            let (room, queries) = scene.bot_simulation();
            let position =
                first_body.and_then(|entity| state(room, entity).map(|s| s.bot.position));
            room.set_external_targets(position.map_or_else(Vec::new, |position| {
                DEFENDERS
                    .into_iter()
                    .map(|entity| crate::script_enemy::EnemyTarget {
                        hero: None,
                        filter_flags: None,
                        tags: Default::default(),
                        health: None,
                        velocity: None,
                        script_state: Default::default(),
                        entity,
                        team: 0,
                        pose: TargetPose {
                            feet: position,
                            height: 1.8,
                            radius: 0.35,
                            eye: 1.55,
                        },
                        alive: true,
                    })
                    .collect()
            }));
            // Movement consumes the pre-command modifier; script advancement below
            // refreshes the following frame's value (including authored randomness).
            let applied_scale = first_body
                .and_then(|entity| state(room, entity))
                .and_then(|state| state.enemy.as_ref())
                .map(|enemy| enemy.scripts.movement_speed_scale(tick));
            room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
                .unwrap();
            for hit in room.take_external_enemy_damage() {
                if Some(hit.source) == first_body {
                    assert!(hit.amount > 0.0);
                    if hit.target == DEFENDERS[0] {
                        assert!(!owner.receive_damage(
                            hit.amount,
                            Death {
                                time: f64::from(tick) * 0.016,
                                frame: tick,
                                killer: hit.source,
                            }
                        ));
                    }
                    victims.push(hit.target);
                }
            }
            if victims.contains(&DEFENDERS[0]) {
                owner
                    .advance(
                        Command {
                            frame: tick,
                            ..Default::default()
                        },
                        &mover,
                        BodyStep::UnposedTimer,
                    )
                    .unwrap_or_else(|err| {
                        panic!("Cassidy after taking damage at frame {tick}: {err}")
                    });
                if !checked_hurt_state {
                    let snapshot = owner.snapshot().unwrap();
                    let encoder = owner.encoder();
                    let projected = encoder.project(snapshot, View::Owner).unwrap();
                    encoder.full(&projected).unwrap();
                    checked_hurt_state = snapshot.instances.values().any(|instance| {
                        instance.graph == 0x0033
                            && instance.states.get(&22).is_some_and(|state| state.active)
                    });
                }
            }
            let retired = room.take_retired_enemies();
            if first_body.is_some_and(|entity| retired.contains(&entity)) {
                assert!(checked_setup);
                assert!(checked_contacts);
                assert!(saw_dead);
                assert!(moved_from_spawn);
                assert!(saw_authored_speed);
                assert!(checked_hurt_state);
                assert_eq!(victims, DEFENDERS);
                assert!(owner.vitals().unwrap().total() < full_health);
                assert!(owner.take_health_update().is_some());
                assert_ne!(
                    scene.gate_snapshot().unwrap().variables.get(&8569),
                    gate_damage_before.as_ref(),
                );
                return;
            }
            let first = first_body.or_else(|| {
                room.states()
                    .map(Result::unwrap)
                    .find_map(|state| state.enemy.as_ref().map(|enemy| enemy.binding.body))
            });
            let Some(entity) = first else {
                continue;
            };
            first_body = Some(entity);
            let state = state(room, entity).expect("first wave body retires through the room");
            let initial_position = *spawn_position.get_or_insert(state.bot.position);
            moved_from_spawn |= norm(std::array::from_fn(|i| {
                state.bot.position[i] - initial_position[i]
            })) > 10.0;
            let enemy = state.enemy.as_ref().unwrap();
            assert!(enemy.fault.is_none(), "frame {tick}: {:?}", enemy.fault);
            let horizontal_speed = state.bot.velocity[0].hypot(state.bot.velocity[2]);
            if let Some(scale) = applied_scale {
                assert!(
                    horizontal_speed <= 3.0 * scale + 0.02,
                    "Zomnic speed {horizontal_speed}, applied scale {scale}"
                );
                saw_authored_speed |= horizontal_speed > 2.0;
            }

            if state.bot.dead {
                assert!(
                    norm(std::array::from_fn(
                        |i| state.bot.position[i] - gate_position[i]
                    )) < 3.0
                );
                saw_dead = true;
                assert!(state.respawn_at.is_none());
            }
            let body = enemy.scripts.body.snapshot().unwrap();
            if !checked_contacts
                && let Some((&id, explosion)) = body.instances.iter().find(|(_, instance)| {
                    instance.graph == 0x0c89
                        && instance.states.get(&17).is_some_and(|state| state.active)
                })
            {
                let crate::script_wire::Value::Array(contacts) = &explosion.variables[&375] else {
                    panic!("expected the authored contact array")
                };
                assert!(contacts.starts_with(&DEFENDERS.map(crate::script_wire::Value::Entity)));
                let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                let projected = encoder.project(body, View::Observer).unwrap();
                assert!(projected.instance_states(id).unwrap().contains(&17));
                assert_eq!(
                    projected.instance_variables(id).unwrap().get(&375),
                    explosion.variables.get(&375)
                );
                encoder.full(&projected).unwrap();
                checked_contacts = true;
            }
            if checked_setup {
                continue;
            }
            let Some(sender) = body.instances.values().find_map(|instance| {
                (instance.graph == 0x0cbc)
                    .then(|| instance.states.get(&49))
                    .flatten()
            }) else {
                continue;
            };
            assert!(sender.active);
            assert_eq!(sender.activation, 1);
            assert_eq!(
                sender.kind,
                StateKind::PersistentSend(0x0240_0000_0000_0744)
            );
            let initial_frame = *first_active.get_or_insert(tick);
            if tick - initial_frame == 250 {
                let controller = enemy.scripts.controller.snapshot().unwrap();
                let patrol = controller
                    .instances
                    .values()
                    .find(|instance| instance.graph == 0x0c88)
                    .expect("authored Zomnic behavior graph");
                assert_eq!(
                    patrol.variables.get(&6947),
                    Some(&crate::script_wire::Value::Array(vec![
                        crate::script_wire::Value::Asset(0x0d80_0000_0000_20b5)
                    ]))
                );
                let candidate = &patrol.states[&0];
                assert!(candidate.active);
                assert_eq!(candidate.kind, StateKind::AiCandidate { selected: false });
                let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                encoder
                    .full(&encoder.project(body, View::Observer).unwrap())
                    .unwrap();
                checked_setup = true;
            }
        }
        panic!("the first street wave body did not complete its lifecycle");
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Junkenstein runtime"]
    fn admitted_flashbang_hits_deliver_effects_to_all_boss_pairs() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            game_heroes::HeroCatalog,
            npc_catalog::{NpcCatalog, encounter_definition},
            scene_assets::SceneAssets,
            script_codecs::CodecTable,
            script_enemy::{EnemyGate, EnemySpawn},
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation, Site},
            script_snapshot_wire::{SnapshotEncoder, View},
            script_wire::{Value, Variables},
        };
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let assets = SceneAssets::load(&runtime).unwrap();
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
        let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
        let npcs =
            NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        let maps = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        for (hero, exclusion) in [
            (REAPER, None),
            (ROADHOG, None),
            (SUMMONER, None),
            (JUNKRAT, None),
            (WITCH, None),
            (REAPER, Some(0x0d60_0000_0000_02cbu64)),
            (REAPER, Some(0x0d60_0000_0000_003au64)),
            (REAPER, Some(0x0d60_0000_0000_093du64)),
        ] {
            let graphs = if let Some(tag) = exclusion {
                // Declared immunity input, not a claim about an ordinary Boss's
                // native category/tag inventory. Keep all other loaded data.
                let mut declared: serde_json::Value =
                    serde_json::from_reader(flate2::read::GzDecoder::new(
                        std::fs::File::open(runtime.join("data/game_graphs_174.json.gz")).unwrap(),
                    ))
                    .unwrap();
                let body = format!("0x{:016X}", graphs.enemy_pair(hero).unwrap().body);
                declared["junkenstein"]["entities"][&body]["target_tags"]
                    .as_array_mut()
                    .unwrap()
                    .push(serde_json::json!(format!("0x{tag:016X}")));
                Arc::new(GraphStore::from_json(&serde_json::to_vec(&declared).unwrap()).unwrap())
            } else {
                Arc::clone(&graphs)
            };
            let map = maps
                .game_map(
                    JUNKENSTEIN_MAP,
                    card.mode,
                    "Junkenstein's Revenge",
                    false,
                    &[4, 0],
                )
                .unwrap();
            let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
            let gate = scene.junkenstein_gate().map(|gate| EnemyGate {
                entity: gate.entity,
                position: gate.position,
            });
            let definition = encounter_definition(Some(&npcs), &graphs, &heroes, hero).unwrap();
            let request = EntityCreation {
                position: [74.0, 12.6, -99.0],
                kind: CreationKind::Hero {
                    hero,
                    team: 1,
                    yaw: 180.0,
                    flags: [Value::Bool(false), Value::Bool(false)],
                    controller: Some(graphs.enemy_pair(hero).unwrap().controller),
                    skin: None,
                },
            };
            let (room, queries) = scene.bot_simulation();
            let target = room
                .create_enemy(EnemySpawn {
                    graphs: Arc::clone(&graphs),
                    codecs: Arc::clone(&codecs),
                    definition: &definition,
                    request: &request,
                    mode: card.mode,
                    mode_variables: Arc::new(Variables::from([(
                        6751,
                        Value::Asset(card.difficulty),
                    )])),
                    tick: 1000,
                    walk: Default::default(),
                    gate,
                })
                .unwrap_or_else(|error| panic!("Boss {hero:X}: {error}"));
            let before = state(room, target).unwrap().vitals.total();
            let owner = BodyId {
                entity: 0xa000_0101,
                generation: 1,
            };
            let caller = Site {
                entity: owner.entity,
                instance: 1,
                graph: 0x01e9,
                node: Some(7),
            };
            // Synthetic admitted physical hit, real collision and complete paired
            // NPC adapters. Separate E-input replay verifies the authored launch.
            let mut launch = flight_fixture(owner, 30.0);
            launch.stun = Stun::from_seconds(0.8);
            launch.hit_effect = Some(caller);
            launch.flight = ProjectileFlight::new(
                ProjectileBallistics {
                    speed: 30.0,
                    lifetime: 1.0,
                    definition: Some(0x0400_0000_0000_037d),
                    direct: Some(HitDefinition {
                        amount: 25.0,
                        tags: crate::game_hitscan::DamageTags::EMPTY,
                        loadout: None,
                        critical: 1.0,
                        falloff: None,
                    }),
                    blast: None,
                },
                [74.0, 13.6, -100.0],
                [0.0, 0.0, 1.0],
                None,
            )
            .unwrap();
            let mut impacts = 0;
            for tick in 1000..=1120 {
                let (_, hits) = room
                    .advance_projectiles(
                        &queries,
                        7,
                        owner,
                        if tick == 1000 {
                            vec![std::mem::replace(&mut launch, flight_fixture(owner, 30.0))]
                        } else {
                            vec![]
                        },
                        ShotContext {
                            team: 0,
                            quanta: 0,
                            tick,
                            now: Duration::from_millis(u64::from(tick) * 16),
                        },
                    )
                    .unwrap();
                if !hits.is_empty() {
                    assert_eq!(
                        state(room, target).unwrap().bot.is_stunned(tick),
                        exclusion.is_none(),
                        "Boss {hero:X}, exclusion {exclusion:?}: damage must not bypass control immunity"
                    );
                }
                impacts += hits.len();
                room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
                    .unwrap();
                let actor = state(room, target).unwrap();
                let enemy = actor.enemy.as_ref().unwrap();
                assert!(
                    enemy.fault.is_none(),
                    "Boss {hero:X} at {tick}: {:?}",
                    enemy.fault
                );
                if impacts != 0 {
                    let snapshot = enemy.scripts.body.snapshot().unwrap();
                    assert!(
                        snapshot.initialization_complete(),
                        "{:?}",
                        snapshot.diagnostics
                    );
                    assert!(
                        snapshot
                            .instances
                            .values()
                            .any(|instance| instance.graph == 0x224e),
                        "Boss {hero:X} missing hit effect at {tick}"
                    );
                    let encoder = SnapshotEncoder::new(&graphs, &codecs);
                    encoder
                        .full(&encoder.project(snapshot, View::Observer).unwrap())
                        .unwrap();
                }
            }
            assert_eq!(
                impacts, 1,
                "Boss {hero:X}: admitted hit must resolve exactly once"
            );
            assert_eq!(state(room, target).unwrap().vitals.total(), before - 25.0);
        }
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein enemy graphs and codecs"]
    fn authored_encounter_enemies_publish_their_observer_baselines() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
            game_heroes::HeroCatalog,
            junkenstein_navigation::SHOCK_TIRE,
            npc_catalog::{NpcCatalog, encounter_definition},
            script_codecs::CodecTable,
            script_enemy::EnemySpawn,
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation, StateKind},
            script_snapshot_wire::{SnapshotEncoder, View},
            script_wire::{Value, Variables},
        };

        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
        let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
        let npcs =
            NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = SceneAssets::load(&runtime)
            .unwrap()
            .prepare_junkenstein(map, card, 0)
            .unwrap();
        let (room, _) = scene.bot_simulation();
        let mut witch_targets = [0; 2];
        let mut summoner = None;
        for hero in [
            0x02e0_0000_0000_0177,
            0x02e0_0000_0000_0065,
            SHOCK_TIRE,
            0x02e0_0000_0000_0004,
            0x02e0_0000_0000_0016,
        ] {
            let definition = encounter_definition(Some(&npcs), &graphs, &heroes, hero).unwrap();
            let request = EntityCreation {
                position: [74.0, 12.6, -99.0],
                kind: CreationKind::Hero {
                    hero,
                    team: 1,
                    yaw: 180.0,
                    flags: [Value::Bool(false), Value::Bool(false)],
                    controller: Some(graphs.enemy_pair(hero).unwrap().controller),
                    skin: None,
                },
            };
            let entity = room
                .create_enemy(EnemySpawn {
                    graphs: graphs.clone(),
                    codecs: codecs.clone(),
                    definition: &definition,
                    request: &request,
                    mode: JUNKENSTEIN_REVENGE,
                    mode_variables: Arc::new(Variables::from([(
                        6751,
                        Value::Asset(card.difficulty),
                    )])),
                    tick: 4200,
                    walk: crate::game_bots::BotWalk::default(),
                    gate: None,
                })
                .unwrap_or_else(|error| panic!("hero {hero:#x}: {error}"));
            match hero {
                WITCH => witch_targets[0] = entity,
                JUNKRAT => witch_targets[1] = entity,
                _ => (),
            }
            assert_eq!(room.take_created().len(), 1);
            assert_eq!(room.encounter_possessor(entity), Some(entity + 1));
            let state = room.state_mut(entity).unwrap().unwrap();
            let enemy = state.enemy.as_mut().unwrap();
            let body = enemy.scripts.body.snapshot().unwrap();
            let controller = enemy.scripts.controller.snapshot().unwrap();
            let (snapshot, graph, raw, expected) = match hero {
                0x02e0_0000_0000_0177 => (controller, 0x0c87, 19, true),
                0x02e0_0000_0000_0065 => (body, 0x0232, 22, false),
                SHOCK_TIRE => {
                    assert_eq!(enemy.scripts.ai_control_setting(), Some((20.0, true)));
                    (body, 0x0234, 35, false)
                }
                0x02e0_0000_0000_0004 => (body, 0x0071, 10, false),
                0x02e0_0000_0000_0016 => (body, 0x1571, 47, false),
                _ => unreachable!(),
            };
            let instance = snapshot
                .instances
                .values()
                .find(|i| i.graph == graph)
                .unwrap();
            assert_eq!(
                instance.states[&raw].kind,
                StateKind::BooleanSwitch(expected)
            );

            let encoder = SnapshotEncoder::new(&graphs, &codecs);
            encoder
                .full(&encoder.project(body, View::Observer).unwrap())
                .unwrap();
            if matches!(hero, 0x02e0_0000_0000_0004 | 0x02e0_0000_0000_0016) {
                for tick in 4201..=4320 {
                    enemy
                        .scripts
                        .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, tick)
                        .unwrap_or_else(|error| panic!("hero {hero:#x} at tick {tick}: {error}"));
                }
            }
            if hero == SHOCK_TIRE {
                enemy
                    .scripts
                    .teardown(&enemy.graphs, &enemy.binding, &mut state.vitals)
                    .unwrap();
                assert_eq!(enemy.scripts.ai_control_setting(), Some((5.0, false)));
            }
            if !matches!(hero, WITCH | JUNKRAT) {
                let handle = room.by_wire.shift_remove(&entity).unwrap();
                if hero == SUMMONER {
                    summoner = Some((entity, handle));
                } else {
                    assert!(room.world.despawn(handle));
                }
            }
        }
        let [witch, wounded] = witch_targets;
        let (room, queries) = scene.bot_simulation();
        let defender = 0xa000_0101;
        let hostile = crate::script_enemy::EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: defender,
            team: 0,
            pose: TargetPose {
                feet: [74.0, 12.6, -103.0],
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        };
        room.set_external_targets(vec![hostile.clone()]);
        room.state_mut(wounded).unwrap().unwrap().bot.position = [74.0, 12.6, -97.0];
        RoomSimulation::damage_bot(
            room.state_mut(wounded).unwrap().unwrap(),
            120.0,
            Death {
                time: 69.12,
                frame: 4320,
                killer: defender,
            },
            Duration::from_millis(69_120),
        )
        .unwrap();
        let damaged = state(room, wounded).unwrap().vitals.total();
        assert!(damaged > 0.0, "wounded fixture died from test damage");
        for frame in 4321..=4370 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            let recipient = state(room, wounded).unwrap();
            assert!(
                recipient.enemy.as_ref().unwrap().fault.is_none(),
                "recipient fault at {frame}, health {} from {damaged}, links {:?}: {:?}",
                recipient.vitals.total(),
                room.witch_staff_links,
                recipient.enemy.as_ref().unwrap().fault
            );
        }
        let check_targets = |room: &RoomSimulation, enemy, ally, slot| {
            let witch = state(room, witch).unwrap().enemy.as_ref().unwrap();
            assert!(witch.fault.is_none(), "{:?}", witch.fault);
            let controller = witch.scripts.controller.snapshot().unwrap();
            let control = controller
                .instances
                .values()
                .find(|instance| instance.graph == 0x0cd2)
                .unwrap();
            assert_eq!(control.variables.get(&2299), Some(&Value::Entity(enemy)));
            assert_eq!(control.variables.get(&7126), Some(&Value::Entity(ally)));
            if let Some(slot) = slot {
                let body = witch.scripts.body.snapshot().unwrap();
                assert_eq!(body.variables.get(&30), Some(&Value::Integer(slot)));
            }
        };
        let recipient = state(room, wounded).unwrap();
        let recipient_scripts = &recipient.enemy.as_ref().unwrap().scripts;
        assert!(
            recipient.enemy.as_ref().unwrap().fault.is_none(),
            "{:?}",
            recipient.enemy.as_ref().unwrap().fault
        );
        check_targets(room, defender, wounded, Some(1));
        let healed = recipient.vitals.total();
        assert!(healed > damaged, "staff healed {healed} from {damaged}");
        let &(target, effect) = room.witch_staff_links.get(&witch).expect("staff link");
        assert_eq!(target, wounded);
        let snapshot = recipient_scripts.body.snapshot().unwrap();
        assert_eq!(snapshot.instances[&effect].graph, 0x0079);
        assert_eq!(
            snapshot.instances[&effect].variables.get(&27),
            Some(&Value::Entity(witch))
        );
        let encoder = SnapshotEncoder::new(&graphs, &codecs);
        encoder
            .full(&encoder.project(snapshot, View::Observer).unwrap())
            .unwrap();
        assert!(
            room.take_scripted_health()
                .iter()
                .any(|update| update.entity == wounded)
        );
        room.state_mut(wounded).unwrap().unwrap().bot.position = [58.0, 12.6, -91.0];
        for frame in 4371..=4400 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
        }
        let recipient = state(room, wounded).unwrap();
        assert!(
            recipient.enemy.as_ref().unwrap().fault.is_none(),
            "{:?}",
            recipient.enemy.as_ref().unwrap().fault
        );
        assert!(
            recipient.vitals.total() - healed <= 1.0,
            "staff kept healing outside range"
        );
        assert!(
            !recipient
                .enemy
                .as_ref()
                .unwrap()
                .scripts
                .body
                .snapshot()
                .unwrap()
                .instances
                .contains_key(&effect)
        );
        assert!(
            !room
                .take_external_enemy_damage()
                .iter()
                .any(|hit| hit.source == witch)
        );
        room.set_external_targets(Vec::new());
        let staff_start = state(room, witch).unwrap().bot.position;
        let ally_position = [74.0, 12.6, -80.0];
        assert!(
            norm(std::array::from_fn(
                |axis| ally_position[axis] - staff_start[axis]
            )) > 12.0
        );
        let ally = room.state_mut(wounded).unwrap().unwrap();
        ally.bot.position = ally_position;
        let before_pursuit_heal = ally.vitals.total();
        let staff_tick = (4401..=4700)
            .find(|&frame| {
                room.finish_tick(
                    &queries,
                    frame,
                    Duration::from_millis(u64::from(frame) * 16),
                )
                .unwrap();
                state(room, wounded).unwrap().vitals.total() > before_pursuit_heal
            })
            .unwrap_or_else(|| {
                panic!(
                    "Witch did not reach staff range from {staff_start:?}, stopped at {:?}",
                    state(room, witch).unwrap().bot.position
                )
            });
        let caster = state(room, witch).unwrap();
        assert!(
            norm(std::array::from_fn(
                |axis| caster.bot.position[axis] - staff_start[axis]
            )) > 2.0
        );
        assert_eq!(caster.bot.movement(staff_tick).throttles, [0; 2]);
        room.state_mut(wounded).unwrap().unwrap().vitals.heal(120.0);
        room.state_mut(witch).unwrap().unwrap().bot.position = [74.0, 12.6, -99.0];
        room.set_external_targets(vec![hostile.clone()]);
        let pistol_tick = staff_tick + 80;
        for frame in staff_tick + 1..=pistol_tick {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
        }
        check_targets(room, defender, 0, Some(2));
        let pistol_damage = room
            .take_external_enemy_damage()
            .into_iter()
            .filter(|hit| hit.source == witch && hit.target == defender)
            .map(|hit| hit.amount)
            .collect::<Vec<_>>();
        assert!(!pistol_damage.is_empty(), "Witch did not hit the defender");
        assert!(
            pistol_damage.iter().all(|&amount| amount == 5.0),
            "{pistol_damage:?}"
        );
        let handle = room.by_wire.shift_remove(&wounded).unwrap();
        assert!(room.world.despawn(handle));
        let mut covered = hostile.clone();
        covered.pose.feet = [58.0, 12.6, -91.0];
        let [x, y, z] = state(room, witch).unwrap().bot.position;
        let origin = [x, y + eye_height(WITCH, false), z];
        assert!(
            crate::static_ray::norm(std::array::from_fn(|i| covered.pose.feet[i] - origin[i]))
                < 20.0
        );
        assert!(queries.visible_aim(origin, covered.pose).is_none());
        room.set_external_targets(vec![covered]);
        let cover_tick = pistol_tick + 60;
        for frame in pistol_tick + 1..=cover_tick {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
        }
        check_targets(room, defender, 0, Some(2));
        let caster = state(room, witch).unwrap().enemy.as_ref().unwrap();
        assert_eq!(
            caster.scripts.body.command_buttons() & crate::commands::FIRE,
            0
        );
        assert!(
            !room
                .take_external_enemy_damage()
                .iter()
                .any(|hit| hit.source == witch)
        );
        let pursuit_start = state(room, witch).unwrap().bot.position;
        let mut distant = hostile;
        distant.pose.feet = [74.0, 12.6, -74.0];
        assert!(
            norm(std::array::from_fn(
                |axis| distant.pose.feet[axis] - pursuit_start[axis]
            )) > 20.0
        );
        room.set_external_targets(vec![distant]);
        let mut hit_at = None;
        for frame in cover_tick + 1..=cover_tick + 360 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            if room
                .take_external_enemy_damage()
                .iter()
                .any(|hit| hit.source == witch && hit.target == defender)
            {
                hit_at = Some(frame);
                break;
            }
        }
        let caster = state(room, witch).unwrap();
        let hit_at = hit_at.unwrap_or_else(|| {
            panic!(
                "Witch did not reach pistol range from {pursuit_start:?}, stopped at {:?}: {:?}",
                caster.bot.position,
                caster.enemy.as_ref().unwrap().fault
            )
        });
        assert!(
            norm(std::array::from_fn(
                |axis| caster.bot.position[axis] - pursuit_start[axis]
            )) > 2.0
        );
        assert_eq!(caster.bot.movement(hit_at).throttles, [0; 2]);
        room.set_external_targets(Vec::new());
        room.finish_tick(
            &queries,
            hit_at + 1,
            Duration::from_millis(u64::from(hit_at + 1) * 16),
        )
        .unwrap();
        check_targets(room, 0, 0, None);

        let handle = room.by_wire.shift_remove(&witch).unwrap();
        assert!(room.world.despawn(handle));
        let (summoner, handle) = summoner.unwrap();
        room.by_wire.insert(summoner, handle);
        // This synthetic defender explicitly has a team filter and no catalog tags.
        let mut target = crate::script_enemy::EnemyTarget {
            hero: None,
            filter_flags: Some(0x0080_0001),
            tags: crate::script_init::TargetTagEffects::default()
                .snapshot(Some(&std::collections::BTreeSet::new())),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: defender,
            team: 0,
            pose: TargetPose {
                feet: [74.0, 12.6, -89.0],
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        };
        room.set_external_targets(vec![target.clone()]);
        let mut orb_hit = None;
        for frame in hit_at + 2..=hit_at + 180 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            let enemy = state(room, summoner).unwrap().enemy.as_ref().unwrap();
            assert!(
                enemy.fault.is_none(),
                "Summoner orb at {frame}: {:?}",
                enemy.fault
            );
            if room
                .take_external_enemy_damage()
                .iter()
                .any(|hit| hit.source == summoner && hit.target == defender && hit.amount > 0.0)
            {
                orb_hit = Some(frame);
                break;
            }
        }
        let orb_hit = orb_hit.expect("Summoner orb hit");
        let check_summoner_targets =
            |room: &RoomSimulation, primary: Option<Value>, secondary: Option<Value>| {
                let scripts = &state(room, summoner)
                    .unwrap()
                    .enemy
                    .as_ref()
                    .unwrap()
                    .scripts;
                let controller = scripts.controller.snapshot().unwrap();
                let control = controller
                    .instances
                    .values()
                    .find(|instance| instance.graph == 0x185d)
                    .unwrap();
                assert_eq!(control.variables.get(&8066), primary.as_ref());
                assert_eq!(control.variables.get(&11652), secondary.as_ref());
            };
        check_summoner_targets(room, None, Some(Value::Entity(defender)));
        target.pose.feet = [74.0, 12.6, -96.0];
        room.set_external_targets(vec![target.clone()]);
        let mut beam_ticks = 0;
        for frame in orb_hit + 1..=orb_hit + 80 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            let enemy = state(room, summoner).unwrap().enemy.as_ref().unwrap();
            assert!(
                enemy.fault.is_none(),
                "Summoner beam at {frame}: {:?}",
                enemy.fault
            );
            for hit in room
                .take_external_enemy_damage()
                .into_iter()
                .filter(|hit| hit.source == summoner && hit.target == defender)
            {
                beam_ticks += usize::from((hit.amount - 0.0512).abs() < 1e-6);
            }
        }
        assert!(
            beam_ticks > 20,
            "only {beam_ticks} normal beam damage ticks"
        );
        check_summoner_targets(
            room,
            Some(Value::Entity(defender)),
            Some(Value::Entity(defender)),
        );
        let scripts = &state(room, summoner)
            .unwrap()
            .enemy
            .as_ref()
            .unwrap()
            .scripts;
        let encoder = SnapshotEncoder::new(&graphs, &codecs);
        encoder
            .full(
                &encoder
                    .project(scripts.body.snapshot().unwrap(), View::Observer)
                    .unwrap(),
            )
            .unwrap();

        target.pose.feet = [58.0, 12.6, -91.0];
        room.set_external_targets(vec![target]);
        for frame in orb_hit + 81..=orb_hit + 100 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            let enemy = state(room, summoner).unwrap().enemy.as_ref().unwrap();
            assert!(
                enemy.fault.is_none(),
                "Summoner behind cover at {frame}: {:?}",
                enemy.fault
            );
            assert_eq!(
                enemy.scripts.body.command_buttons()
                    & (crate::commands::FIRE | crate::commands::SECONDARY),
                0
            );
            assert!(
                room.take_external_enemy_damage()
                    .iter()
                    .all(|hit| hit.source != summoner)
            );
        }
        room.set_external_targets(Vec::new());
        room.finish_tick(
            &queries,
            orb_hit + 101,
            Duration::from_millis(u64::from(orb_hit + 101) * 16),
        )
        .unwrap();
        check_summoner_targets(room, Some(Value::Entity(0)), Some(Value::Entity(0)));
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein NPC, graph and collision pack"]
    fn authored_shock_tire_damages_the_gate_and_retires() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            junkenstein_navigation::SHOCK_TIRE,
            npc_catalog::NpcCatalog,
            script_codecs::CodecTable,
            script_enemy::{EnemyGate, EnemySpawn},
            script_expr::to_float,
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation, PlayScript, Site},
            script_wire::Value,
            world::GAME_MODE_ENTITY,
        };
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = SceneAssets::load(&runtime)
            .unwrap()
            .prepare_junkenstein(map, card, 0)
            .unwrap();
        scene.step_encounter(4200, None);
        let gate = scene.junkenstein_gate().unwrap();
        let gate = EnemyGate {
            entity: gate.entity,
            position: gate.position,
        };
        let mut position = gate.position;
        position[0] += 1.0;
        let previous = to_float(&scene.gate_snapshot().unwrap().variables[&8569]);
        let mode_variables = Arc::new(scene.encounter_snapshot().unwrap().variables.clone());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
        let catalog =
            NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        let request = EntityCreation {
            position,
            kind: CreationKind::Hero {
                hero: SHOCK_TIRE,
                team: 1,
                yaw: 0.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: Some(graphs.enemy_pair(SHOCK_TIRE).unwrap().controller),
                skin: None,
            },
        };
        let (room, _) = scene.bot_simulation();
        let body = room
            .create_enemy(EnemySpawn {
                graphs,
                codecs,
                definition: catalog.get(SHOCK_TIRE).unwrap(),
                request: &request,
                mode: card.mode,
                mode_variables,
                tick: 4200,
                walk: crate::game_bots::BotWalk::default(),
                gate: Some(gate),
            })
            .unwrap();
        assert!(room.queue_encounter_wave(
            Site {
                entity: GAME_MODE_ENTITY,
                instance: 1,
                graph: 0x0cba,
                node: Some(9)
            },
            body + 1,
            PlayScript {
                policy: 0,
                overrides: Vec::new(),
                graph: 0x0c95,
                source: GAME_MODE_ENTITY,
                parameters: vec![(
                    0x0a40_0000_0000_03a1,
                    Value::Array(vec![Value::Vector(position)])
                )],
                auto_retire: true,
            },
        ));
        let state = room.state_mut(body).unwrap().unwrap();
        let enemy = state.enemy.as_mut().unwrap();
        enemy
            .scripts
            .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, 4201)
            .unwrap();
        let mut retired_at = None;
        for tick in 4202..=4600 {
            scene.step_encounter(tick, None);
            let (room, queries) = scene.bot_simulation();
            room.set_external_targets(Vec::new());
            room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
                .unwrap();
            if let Some(state) = room.state_mut(body).unwrap() {
                assert!(
                    state.enemy.as_ref().unwrap().fault.is_none(),
                    "tick {tick}: {:?}",
                    state.enemy.as_ref().unwrap().fault
                );
            } else {
                retired_at = Some(tick);
                break;
            }
        }
        // The queued gate damage reaches its script on the next scene tick.
        scene.step_encounter(retired_at.expect("the tire retires after impact") + 1, None);
        assert!(
            scene
                .bot_simulation()
                .0
                .take_retired_enemies()
                .contains(&body)
        );
        assert!(to_float(&scene.gate_snapshot().unwrap().variables[&8569]) > previous);
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein scene and NPC graphs"]
    fn normal_zombardier_wave_fires_at_a_live_defender() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            scene_assets::SceneAssets,
            script_enemy::EnemyTarget,
            script_snapshot_wire::{SnapshotEncoder, View},
        };

        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = SceneAssets::load(runtime)
            .unwrap()
            .prepare_junkenstein(map, card, 0)
            .unwrap();
        let defender = 0xa000_0101;
        let mut spawned = None;
        let mut in_flight = false;
        let mut saw_weapon_input = false;
        let mut checked_projection = false;
        let mut source_retired = false;
        scene.bot_simulation().0.set_external_targets(Vec::new());
        for tick in 2700..=7900 {
            scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
            let (room, queries) = scene.bot_simulation();
            if spawned.is_none() {
                let bot = room
                    .bodies()
                    .unwrap()
                    .find(|bot| bot.spawn.hero == ZOMBARDIER)
                    .map(|bot| (bot.entity, bot.position));
                if let Some((body, position)) = bot {
                    let [x, y, z] = position;
                    spawned = Some((body, position));
                    room.set_external_targets(vec![EnemyTarget {
                        hero: None,
                        filter_flags: None,
                        tags: Default::default(),
                        health: None,
                        velocity: None,
                        script_state: Default::default(),
                        entity: defender,
                        team: 0,
                        pose: TargetPose {
                            feet: [x, y, z + 10.0],
                            height: 1.8,
                            radius: 0.35,
                            eye: 1.55,
                        },
                        alive: true,
                    }]);
                }
            }
            room.finish_tick(
                &queries,
                tick,
                Duration::from_millis(u64::from(tick - 2700) * 16),
            )
            .unwrap();
            let damage = room.take_external_enemy_damage();
            let Some((body, _)) = spawned else { continue };
            in_flight |= room.enemy_projectiles.iter().any(|&handle| {
                room.world
                    .get::<crate::script_enemy::EnemyProjectile>(handle)
                    .is_some_and(|projectile| projectile.source == body)
            });
            source_retired |= room.take_retired_enemies().contains(&body);
            let enemy = state(room, body).and_then(|state| state.enemy.as_ref());
            if let Some(enemy) = enemy {
                assert!(enemy.fault.is_none(), "tick {tick}: {:?}", enemy.fault);
                saw_weapon_input |=
                    enemy.scripts.body.command_buttons() & crate::commands::SECONDARY != 0;
                if in_flight && !checked_projection {
                    let snapshot = enemy.scripts.body.snapshot().unwrap();
                    let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                    encoder
                        .full(&encoder.project(snapshot, View::Observer).unwrap())
                        .unwrap();
                    checked_projection = true;
                }
            } else {
                assert!(
                    source_retired,
                    "source vanished without normal pair retirement"
                );
            }
            if let Some(hit) = damage
                .iter()
                .find(|hit| hit.source == body && hit.target == defender)
            {
                assert!(in_flight);
                assert!(saw_weapon_input);
                assert!(checked_projection);
                assert_eq!(hit.amount, 30.0);
                room.set_external_targets(Vec::new());
                room.finish_tick(
                    &queries,
                    tick + 1,
                    Duration::from_millis(u64::from(tick + 1 - 2700) * 16),
                )
                .unwrap();
                // A launched grenade can outlive the source's eight-second
                // corpse grace. Input cleanup is observable only while bound.
                if let Some(enemy) = state(room, body).and_then(|state| state.enemy.as_ref()) {
                    assert!(enemy.fault.is_none(), "{:?}", enemy.fault);
                    assert_eq!(
                        enemy.scripts.body.command_buttons() & crate::commands::SECONDARY,
                        0
                    );
                } else {
                    assert!(source_retired);
                }
                return;
            }
        }
        panic!(
            "Zombardier did not hit the defender: spawned={spawned:?}, projectile launched={in_flight}"
        );
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein Boss graphs and codecs"]
    fn authored_junkrat_fires_a_grenade_at_the_defender() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
            game_heroes::HeroCatalog,
            npc_catalog::encounter_definition,
            script_codecs::CodecTable,
            script_enemy::{EnemySpawn, EnemyTarget},
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation, PlayScript, Site},
            script_snapshot_wire::{SnapshotEncoder, View},
            script_wire::{Value, Variables},
        };
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
        let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = crate::scene_assets::SceneAssets::load(runtime)
            .unwrap()
            .prepare_junkenstein(map, card, 0)
            .unwrap();
        let definition = encounter_definition(None, &graphs, &heroes, JUNKRAT).unwrap();
        let request = EntityCreation {
            position: [74.0, 12.6, -99.0],
            kind: CreationKind::Hero {
                hero: JUNKRAT,
                team: 1,
                yaw: 180.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: Some(graphs.enemy_pair(JUNKRAT).unwrap().controller),
                skin: None,
            },
        };
        let (room, queries) = scene.bot_simulation();
        let body = room
            .create_enemy(EnemySpawn {
                graphs: Arc::clone(&graphs),
                codecs: Arc::clone(&codecs),
                definition: &definition,
                request: &request,
                mode: JUNKENSTEIN_REVENGE,
                mode_variables: Arc::new(Variables::from([
                    (6751, Value::Asset(card.difficulty)),
                    (8522, Value::Bool(true)),
                    (8566, Value::Bool(true)),
                    (9948, Value::Integer(0)),
                ])),
                tick: 4200,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            })
            .unwrap();
        let caller = Site {
            entity: crate::world::GAME_MODE_ENTITY,
            instance: 1,
            graph: 0x0cba,
            node: Some(9),
        };
        assert!(room.queue_encounter_wave(
            caller,
            body + 1,
            PlayScript {
                policy: 0,
                overrides: Vec::new(),
                graph: 0x0c95,
                source: caller.entity,
                parameters: vec![(
                    0x0a40_0000_0000_03a1,
                    Value::Array(vec![Value::Vector(request.position)])
                )],
                auto_retire: true,
            }
        ));
        let defender = 0xa000_0101;
        room.set_external_targets(vec![EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: defender,
            team: 0,
            pose: TargetPose {
                feet: [74.0, 12.6, -89.0],
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        }]);
        let mut in_flight = false;
        for frame in 4201..=4650 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            assert!(room.take_boss_intro_starts().is_empty());
            in_flight |= !room.enemy_projectiles.is_empty();
            let enemy = state(room, body).unwrap().enemy.as_ref().unwrap();
            assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
            let hits = room.take_external_enemy_damage();
            if hits
                .iter()
                .any(|hit| hit.source == body && hit.target == defender)
            {
                assert!(in_flight);
                assert_eq!(
                    hits.iter().map(|hit| hit.amount).collect::<Vec<_>>(),
                    [10.0, 15.0]
                );
                let enemy = state(room, body).unwrap().enemy.as_ref().unwrap();
                let snapshot = enemy.scripts.body.snapshot().unwrap();
                let encoder = SnapshotEncoder::new(&graphs, &codecs);
                encoder
                    .full(&encoder.project(snapshot, View::Observer).unwrap())
                    .unwrap();
                return;
            }
        }
        panic!("Junkrat never hit the defender, projectile launched={in_flight}");
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein Boss graphs and codecs"]
    fn authored_roadhog_wave_runs_its_intro_and_both_weapon_modes() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
            game_heroes::HeroCatalog,
            npc_catalog::encounter_definition,
            script_codecs::CodecTable,
            script_enemy::EnemySpawn,
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation, PlayScript, Site, StateKind},
            script_snapshot_wire::{SnapshotEncoder, View},
            script_wire::{Value, Variables},
        };

        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
        let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
        let hero = 0x02e0_0000_0000_0040;
        let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
        let request = EntityCreation {
            position: [74.0, 12.6, -99.0],
            kind: CreationKind::Hero {
                hero,
                team: 1,
                yaw: 180.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: Some(graphs.enemy_pair(hero).unwrap().controller),
                skin: None,
            },
        };
        let card = JunkensteinCard::from_index(0x3c).unwrap();
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                card.mode,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = crate::scene_assets::SceneAssets::load(&runtime)
            .unwrap()
            .prepare_junkenstein(map, card, 0)
            .unwrap();
        scene.step_encounter(4200, None);
        let mode_variables = Arc::new(Variables::from([
            (9948, Value::Integer(0)),
            (6751, Value::Asset(card.difficulty)),
        ]));
        let (room, _) = scene.bot_simulation();
        let entity = room
            .create_enemy(EnemySpawn {
                graphs: Arc::clone(&graphs),
                codecs: Arc::clone(&codecs),
                definition: &definition,
                request: &request,
                mode: JUNKENSTEIN_REVENGE,
                mode_variables: mode_variables.clone(),
                tick: 4200,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            })
            .unwrap();
        assert_eq!(room.take_created().len(), 1);
        assert_eq!(room.encounter_possessor(entity), Some(entity + 1));
        let enemy = room
            .state_mut(entity)
            .unwrap()
            .unwrap()
            .enemy
            .as_ref()
            .unwrap();
        let controller = enemy.scripts.controller.snapshot().unwrap();
        for graph in [0x0bc6, 0x0bc8] {
            assert!(
                controller
                    .instances
                    .values()
                    .any(|instance| instance.graph == graph)
            );
        }
        assert!(
            controller.initialization_complete(),
            "{:?}",
            controller.diagnostics
        );
        let body = enemy.scripts.body.snapshot().unwrap();
        let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
        encoder
            .full(&encoder.project(body, View::Observer).unwrap())
            .unwrap();
        assert_eq!(enemy.scripts.outgoing_damage(10.0, &[]), 10.0);

        room.set_mode_variables(mode_variables);
        let caller = Site {
            entity: crate::world::GAME_MODE_ENTITY,
            instance: 1,
            graph: 0x0cba,
            node: Some(9),
        };
        let queue_wave = |room: &mut RoomSimulation, entity: u32| {
            assert!(room.queue_encounter_wave(
                caller,
                entity + 1,
                PlayScript {
                    policy: 0,
                    overrides: Vec::new(),
                    graph: 0x0c95,
                    source: caller.entity,
                    parameters: vec![(
                        0x0a40_0000_0000_03a1,
                        Value::Array(vec![Value::Vector(request.position)]),
                    )],
                    auto_retire: true,
                }
            ));
        };
        queue_wave(room, entity);

        let mut intro_id = None;
        let mut cameras = 0;
        let mut anims = 0;
        let mut ended = false;
        let mut voices = Vec::new();
        for frame in 4201..=5200 {
            scene.step_encounter(frame, None);
            let (room, queries) = scene.bot_simulation();
            room.set_external_targets(Vec::new());
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            let enemy = room
                .state_mut(entity)
                .unwrap()
                .unwrap()
                .enemy
                .as_ref()
                .unwrap();
            assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
            assert_eq!(enemy.scripts.outgoing_damage(10.0, &[]), 8.0);
            assert_eq!(
                enemy
                    .scripts
                    .outgoing_damage(10.0, &[0x0d80_0000_0000_3fce]),
                10.0,
            );
            let body = enemy.scripts.body.snapshot().unwrap();
            let wave = body.instances.values().find(|i| i.graph == 0x0cbc).unwrap();
            assert_eq!(
                wave.states[&60].kind,
                StateKind::OutgoingDamageFilter { registered: true }
            );
            for (&id, intro) in body.instances.iter().filter(|(_, i)| i.graph == 0x0ce0) {
                assert_eq!(*intro_id.get_or_insert(id), id);
                if let Some(animation) = intro.states.get(&7) {
                    anims = anims.max(animation.activation);
                }
                if let Some(camera) = intro.states.get(&14) {
                    if camera.active && cameras == 0 {
                        let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                        encoder
                            .full(&encoder.project(body, View::Observer).unwrap())
                            .unwrap();
                    }
                    cameras = cameras.max(camera.activation);
                }
                ended |= intro
                    .states
                    .get(&3)
                    .is_some_and(|wait| wait.activation == 1 && !wait.active);
            }
            voices.extend(room.take_encounter_voice_requests());
        }
        assert_eq!((anims, cameras, ended), (1, 1, true));
        let cues: Vec<_> = voices
            .iter()
            .filter(|voice| voice.anchor == entity)
            .map(|voice| (voice.speaker, voice.stimulus))
            .collect();
        assert_eq!(
            cues,
            [
                (crate::world::GAME_MODE_ENTITY, 0x0ee0_0000_0000_06b6),
                (entity, 0x0ee0_0000_0000_06bd),
                (crate::world::GAME_MODE_ENTITY, 0x0ee0_0000_0000_06da),
            ]
        );

        let defender = crate::script_enemy::EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: 0xa000_0101,
            team: 0,
            pose: TargetPose {
                feet: [74.0, 12.6, -97.0],
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        };
        let mut empty_magazine = false;
        let mut fired_after_reload = false;
        let mut hit = None;
        let mut last_frame = 5200;
        for frame in 5201..=6300 {
            last_frame = frame;
            scene.step_encounter(frame, None);
            let (room, queries) = scene.bot_simulation();
            room.set_external_targets(vec![defender.clone()]);
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            let damage = room
                .take_external_enemy_damage()
                .into_iter()
                .find(|damage| damage.source == entity && damage.target == defender.entity);
            let enemy = room
                .state_mut(entity)
                .unwrap()
                .unwrap()
                .enemy
                .as_ref()
                .unwrap();
            assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
            let body = enemy.scripts.body.snapshot().unwrap();
            let weapon = body.instances.values().find(|i| i.graph == 0x01c6).unwrap();
            let shots = weapon.states.get(&9).map_or(0, |volley| volley.activation);
            empty_magazine |= weapon.variables[&53] == Value::Integer(0);
            if let Some(damage) = damage {
                if hit.is_none() {
                    assert_eq!(shots, 1);
                    assert_eq!(weapon.variables[&53], Value::Integer(4));
                    let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                    encoder
                        .full(&encoder.project(body, View::Observer).unwrap())
                        .unwrap();
                }
                hit = Some(damage.amount);
                if shots > 5 {
                    fired_after_reload =
                        empty_magazine && weapon.variables[&53] == Value::Integer(4);
                    break;
                }
            }
        }
        let damage = hit.expect("Roadhog hits the nearby defender with the authored shotgun");
        assert!((damage - 1.44).abs() < 1e-6, "damage {damage}");
        assert!(fired_after_reload);

        let mut run_secondary = |distance: f64, direct: bool| {
            for _ in 0..85 {
                last_frame += 1;
                scene.step_encounter(last_frame, None);
                let (room, queries) = scene.bot_simulation();
                room.set_external_targets(Vec::new());
                room.finish_tick(
                    &queries,
                    last_frame,
                    Duration::from_millis(u64::from(last_frame) * 16),
                )
                .unwrap();
                room.take_external_enemy_damage();
            }
            let mut defender = defender.clone();
            defender.pose.feet[2] = request.position[2] + distance;
            for _ in 0..300 {
                last_frame += 1;
                scene.step_encounter(last_frame, None);
                let (room, queries) = scene.bot_simulation();
                room.set_external_targets(vec![defender.clone()]);
                room.finish_tick(
                    &queries,
                    last_frame,
                    Duration::from_millis(u64::from(last_frame) * 16),
                )
                .unwrap();
                let damage = room.take_external_enemy_damage();
                let split_in_flight = room.enemy_projectiles.len() > 1;
                let enemy = room
                    .state_mut(entity)
                    .unwrap()
                    .unwrap()
                    .enemy
                    .as_ref()
                    .unwrap();
                assert!(
                    enemy.fault.is_none(),
                    "frame {last_frame}: {:?}",
                    enemy.fault
                );
                if let Some(hit) = damage
                    .into_iter()
                    .find(|hit| hit.source == entity && hit.target == defender.entity)
                {
                    assert_eq!(split_in_flight, !direct);
                    return hit.amount;
                }
            }
            panic!("Roadhog did not hit the defender at {distance} metres");
        };
        assert!((run_secondary(8.9, true) - 8.0).abs() < 1e-6);
        assert!((run_secondary(12.0, false) - 1.44).abs() < 1e-6);

        let (room, _) = scene.bot_simulation();
        let first_body = room.state_mut(entity).unwrap().unwrap();
        let enemy = first_body.enemy.as_mut().unwrap();
        enemy
            .scripts
            .teardown(&enemy.graphs, &enemy.binding, &mut first_body.vitals)
            .unwrap();
        assert_eq!(enemy.scripts.outgoing_damage(10.0, &[]), 10.0);

        let (original, queries) = scene.bot_simulation();
        let mut room = RoomSimulation::new(Vec::new()).unwrap();
        room.set_spawn_anchors(Arc::clone(original.spawn_anchors.as_ref().unwrap()));
        let repeated = room
            .create_enemy(EnemySpawn {
                graphs,
                codecs,
                definition: &definition,
                request: &request,
                mode: JUNKENSTEIN_REVENGE,
                mode_variables: Arc::new(Variables::from([
                    (9948, Value::Integer(0)),
                    (6751, Value::Asset(card.difficulty)),
                    (8521, Value::Bool(true)),
                    (8566, Value::Bool(true)),
                ])),
                tick: 7000,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            })
            .unwrap();
        queue_wave(&mut room, repeated);
        room.set_external_targets(vec![defender.clone()]);
        let mut hit = false;
        for frame in 7001..=7200 {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            assert!(room.take_boss_intro_starts().is_empty());
            let enemy = state(&room, repeated).unwrap().enemy.as_ref().unwrap();
            assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
            assert!(
                !enemy
                    .scripts
                    .body
                    .snapshot()
                    .unwrap()
                    .instances
                    .values()
                    .any(|instance| instance.graph == 0x0ce0)
            );
            if room.take_external_enemy_damage().iter().any(|impact| {
                impact.source == repeated && impact.target == defender.entity && impact.amount > 0.0
            }) {
                hit = true;
                break;
            }
        }
        assert!(hit, "returning Roadhog did not shoot the defender");
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein Boss graphs and codecs"]
    fn authored_reaper_publishes_an_observer_baseline_and_receives_wave_setup() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
            game_heroes::HeroCatalog,
            npc_catalog::encounter_definition,
            script_codecs::CodecTable,
            script_enemy::EnemySpawn,
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation},
            script_wire::{Value, Variables},
        };
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let codecs =
            Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
        let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
        let hero = 0x02e0_0000_0000_0002;
        let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
        let request = EntityCreation {
            position: crate::junkenstein_navigation::REAPER_BALCONY,
            kind: CreationKind::Hero {
                hero,
                team: 1,
                yaw: 180.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: Some(graphs.enemy_pair(hero).unwrap().controller),
                skin: None,
            },
        };
        let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
            .unwrap()
            .game_map(
                JUNKENSTEIN_MAP,
                JUNKENSTEIN_REVENGE,
                "Junkenstein's Revenge",
                false,
                &[4, 0],
            )
            .unwrap();
        let mut scene = crate::scene_assets::SceneAssets::load(&runtime)
            .unwrap()
            .prepare_junkenstein(map, JunkensteinCard::from_index(0x3c).unwrap(), 0)
            .unwrap();
        scene.step_encounter(4200, None);
        let (room, _) = scene.bot_simulation();
        let entity = room
            .create_enemy(EnemySpawn {
                graphs,
                codecs,
                definition: &definition,
                request: &request,
                mode: JUNKENSTEIN_REVENGE,
                mode_variables: Arc::new(Variables::new()),
                tick: 4200,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            })
            .unwrap();
        assert_eq!(entity, BOT_ENTITY);
        assert_eq!(room.take_created().len(), 1);
        assert_eq!(room.enemy_projections().unwrap().count(), 1);
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_mut().unwrap();
        assert_eq!(enemy.binding.controller, entity + 1);
        assert!(
            enemy
                .scripts
                .controller
                .snapshot()
                .unwrap()
                .initialization_complete()
        );
        assert_eq!(
            enemy.scripts.controller_display_name(),
            Some(0x0de0_0000_0000_7106)
        );
        let defender = 0xa000_0101;
        enemy.scripts.set_defenders(Arc::from([defender]));
        for frame in 4201..=4262 {
            enemy
                .scripts
                .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
                .unwrap_or_else(|error| panic!("Reaper stopped at frame {frame}: {error}"));
        }
        let sightings = enemy.scripts.take_sightings();
        assert_eq!(sightings.len(), 1);
        let sighting = &sightings[0];
        assert_eq!(
            (sighting.source.entity, sighting.target),
            (entity, defender)
        );
        assert_eq!(sighting.parameters[&0x8d], Value::Integer(1));
        assert_eq!(sighting.parameters[&0x5c], Value::Entity(entity));
        assert!(enemy.scripts.take_sightings().is_empty());
        let controller = enemy.binding.controller;
        room.set_mode_variables(Arc::new(Variables::from([
            (9948, Value::Integer(0)),
            (
                6751,
                Value::Asset(JunkensteinCard::from_index(0x3c).unwrap().difficulty),
            ),
        ])));
        assert_eq!(room.encounter_possessor(entity), Some(controller));
        let caller = crate::script_init::Site {
            entity: crate::world::GAME_MODE_ENTITY,
            instance: 1,
            graph: 0x0cba,
            node: Some(9),
        };
        let request = || crate::script_init::PlayScript {
            policy: 0,
            overrides: Vec::new(),
            graph: 0x0c95,
            source: caller.entity,
            parameters: vec![(
                0x0a40_0000_0000_03a1,
                Value::Array(vec![Value::Vector([74.0, 12.6, -99.0])]),
            )],
            auto_retire: true,
        };
        assert!(!room.queue_encounter_wave(caller, entity, request()));
        assert!(room.queue_encounter_wave(caller, controller, request()));
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_mut().unwrap();
        enemy
            .scripts
            .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, 4263)
            .unwrap();
        assert_eq!(state.vitals.maximum(), 1000.0);
        let direction = enemy
            .scripts
            .facing_direction()
            .expect("authored facing record");
        assert!(direction[0].abs() < 1e-6);
        assert_eq!(direction[2], -1.0);
        state.bot.yaw = 0.0;
        state.bot.apply_encounter_facing(direction, 4263);
        assert_eq!(state.bot.movement(4263).yaw, -32768);
        let body = enemy.scripts.body.snapshot().unwrap();
        let wave_body = body
            .instances
            .values()
            .find(|instance| instance.graph == 0x0cbc)
            .unwrap();
        assert!(wave_body.states[&52].active);
        assert_eq!(
            wave_body.states[&52].kind,
            crate::script_init::StateKind::SameTeamContact { registered: true }
        );
        assert_eq!(
            body.instances
                .values()
                .filter(|instance| instance.graph == 0x0ce0)
                .count(),
            1
        );
        assert!(
            body.instances
                .values()
                .any(|instance| instance.graph == 0x0cbc)
        );
        let controller = enemy.scripts.controller.snapshot().unwrap();
        let anchors = enemy.binding.spawn_anchors.as_ref().unwrap();
        let &[target] = anchors.targets(0x0d60_0000_0000_0321).unwrap() else {
            panic!("expected one authored controller target");
        };
        let wave = controller
            .instances
            .values()
            .find(|instance| instance.graph == 0x0c95)
            .unwrap();
        assert_eq!(
            wave.variables[&536],
            Value::Array(vec![Value::Entity(target)])
        );
        let approach = controller
            .instances
            .values()
            .find(|instance| instance.graph == 0x0ce2)
            .unwrap();
        assert_eq!(
            approach.variables[&431],
            Value::Vector([84.9754638671875, 12.68067741394043, -79.8869400024414])
        );
        assert_eq!(approach.variables[&501], Value::Float(10.78968620300293));
        for snapshot in [body, controller] {
            assert!(
                !snapshot.diagnostics.iter().any(|diagnostic| {
                    matches!(
                        diagnostic.issue,
                        crate::script_init::Issue::UnsupportedField("PlayScript target component")
                    )
                }),
                "{:?}",
                snapshot.diagnostics
            );
        }
        let mode = scene.encounter_snapshot().unwrap();
        assert_ne!(mode.variables.get(&8520), Some(&Value::Bool(true)));
        let diagnostics = mode.diagnostics.len();
        scene.step_encounter(4264, None);
        let mode = scene.encounter_snapshot().unwrap();
        assert_eq!(
            mode.diagnostics.len(),
            diagnostics,
            "{:?}",
            mode.diagnostics
        );
        assert_eq!(mode.variables[&8520], Value::Bool(true));
        assert!(scene.bot_simulation().0.take_boss_intro_starts().is_empty());
        let (room, _) = scene.bot_simulation();
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_mut().unwrap();
        for frame in 4264..=4356 {
            enemy
                .scripts
                .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
                .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
        }
        let body = enemy.scripts.body.snapshot().unwrap();
        let intro = body
            .instances
            .values()
            .find(|instance| instance.graph == 0x0ce0)
            .unwrap();
        // The half-second wait must follow 0CE0:76 to the one-second wait.
        assert!(!intro.states[&23].active);
        assert!(intro.states[&15].active);
        assert_eq!(intro.states[&15].activation, 1);
        let encoder =
            crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
        let observer = encoder
            .project(body, crate::script_snapshot_wire::View::Observer)
            .unwrap();
        encoder.full(&observer).unwrap();
        assert!(enemy.scripts.take_voice_requests().is_empty());
        for frame in 4357..=4570 {
            enemy
                .scripts
                .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
                .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
        }
        let body = enemy.scripts.body.snapshot().unwrap();
        let intro = body
            .instances
            .values()
            .find(|instance| instance.graph == 0x0ce0)
            .unwrap();
        assert_eq!(intro.states[&39].activation, 1);
        assert!(!intro.states[&39].active);
        assert_eq!(
            room.take_encounter_voice_requests(),
            [crate::junkenstein_voice::VoiceRequest {
                anchor: entity,
                speaker: crate::world::GAME_MODE_ENTITY,
                stimulus: 0x0ee0_0000_0000_06b5,
                line: 0x0760_0000_0000_8cb1,
            }]
        );
        assert!(room.take_encounter_voice_requests().is_empty());
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_mut().unwrap();
        for frame in 4571..=4763 {
            enemy
                .scripts
                .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
                .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
        }
        let snapshot = enemy.scripts.body.snapshot().unwrap();
        let (&intro_id, intro) = snapshot
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == 0x0ce0)
            .unwrap();
        for (raw, message) in [
            (8, crate::junkenstein_messages::BOSS_PAUSE),
            (14, crate::junkenstein_messages::BOSS_CAMERA),
        ] {
            let state = &intro.states[&raw];
            assert!(state.active);
            assert_eq!(state.activation, 1);
            assert_eq!(
                state.kind,
                crate::script_init::StateKind::PersistentSend(message)
            );
        }
        assert_eq!(intro.variables[&8695], Value::Asset(0x0880_0000_0000_0010));
        assert_eq!(
            intro.states[&2].kind,
            crate::script_init::StateKind::CinematicTimeScale {
                boundary_frame: 4764
            }
        );
        let encoder =
            crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
        encoder
            .full(
                &encoder
                    .project(snapshot, crate::script_snapshot_wire::View::Observer)
                    .unwrap(),
            )
            .unwrap();
        for frame in 4764..=4820 {
            enemy
                .scripts
                .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
                .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
        }
        let snapshot = enemy.scripts.body.snapshot().unwrap();
        let intro = &snapshot.instances[&intro_id];
        assert_eq!(intro.states[&14].activation, 1);
        assert!(!intro.states[&14].active);
        assert!(!intro.states[&2].active);
        assert!(matches!(
            intro.states[&2].kind,
            crate::script_init::StateKind::CinematicTimeScale { boundary_frame }
                if boundary_frame > 4764 && boundary_frame <= 4821
        ));
        assert_eq!(enemy.scripts.cinematic_aim(), None);
        assert_eq!(enemy.scripts.facing_direction(), None);
        assert_eq!(
            room.take_encounter_voice_requests(),
            [crate::junkenstein_voice::VoiceRequest {
                anchor: entity,
                speaker: entity,
                stimulus: 0x0ee0_0000_0000_06bd,
                line: 0x0760_0000_0000_8a2b,
            }]
        );
        assert_eq!(
            room.take_defender_messages(),
            [
                crate::junkenstein_messages::RESET_HIGHLIGHTS,
                crate::junkenstein_messages::BOSS_INTRO_ENDED,
                crate::junkenstein_messages::RESET_HIGHLIGHTS,
            ]
            .map(|message| DefenderMessage {
                target: None,
                source: entity,
                message,
                parameters: crate::script_wire::Variables::new(),
            })
        );

        let (room, queries) = scene.bot_simulation();
        let step = |room: &mut RoomSimulation, tick| {
            room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
                .unwrap();
        };
        room.set_external_targets(Vec::new());
        step(room, 4821);
        room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: defender,
            team: 0,
            pose: TargetPose {
                feet: [87.0, 12.6, -64.0],
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        }]);
        room.set_mode_boss_pause(true);
        step(room, 4822);
        let state = room.state_mut(entity).unwrap().unwrap();
        assert_eq!(
            state.bot.position,
            crate::junkenstein_navigation::REAPER_BALCONY
        );
        assert!(matches!(
            state.enemy.as_ref().unwrap().reaper_exit,
            Some(ReaperExit::Waiting)
        ));
        room.set_mode_boss_pause(false);
        let mut airborne = false;
        let mut landing = None;
        for tick in 4823..=5023 {
            step(room, tick);
            let state = room.state_mut(entity).unwrap().unwrap();
            let enemy = state.enemy.as_ref().unwrap();
            assert!(enemy.fault.is_none(), "{tick}: {:?}", enemy.fault);
            airborne |= state.bot.movement(tick).flags & crate::mover_posture::IN_AIR != 0;
            if enemy.reaper_exit.is_none() {
                landing = Some((tick, state.bot.position));
                break;
            }
        }
        let (landed, position) = landing.expect("Reaper lands on the street");
        assert!(airborne);
        assert!(
            position[1] < crate::junkenstein_navigation::REAPER_BALCONY[1] - 3.0,
            "{position:?}"
        );
        let defender_position = [87.0, 12.6, -64.0];
        let mut in_range = None;
        for tick in landed + 1..=landed + 900 {
            step(room, tick);
            let state = room.state_mut(entity).unwrap().unwrap();
            let range = norm(std::array::from_fn(|axis| {
                state.bot.position[axis] - defender_position[axis]
            }));
            if range <= 20.0 && state.bot.velocity == [0.0; 3] {
                in_range = Some((tick, state.bot.position));
                break;
            }
        }
        let (arrived, in_range) = in_range.expect("Reaper pursues to a visible firing position");
        assert_ne!(in_range, position);
        let fire_state = |room: &mut RoomSimulation| {
            let state = room.state_mut(entity).unwrap().unwrap();
            let enemy = state.enemy.as_ref().unwrap();
            assert!(enemy.fault.is_none(), "{:?}", enemy.fault);
            let snapshot = enemy.scripts.body.snapshot().unwrap();
            let weapon = snapshot
                .instances
                .values()
                .find(|i| i.graph == 0x0019)
                .unwrap();
            (
                weapon.states.get(&6).map_or(0, |volley| volley.activation),
                enemy.scripts.body.command_buttons(),
            )
        };
        assert_eq!(fire_state(room), (1, 1));
        let impacts = room.take_external_enemy_damage();
        assert!(
            !impacts.is_empty(),
            "the authored volley must hit the visible defender"
        );
        assert!(impacts.iter().all(|hit| hit.source == entity
            && hit.target == defender
            && (0.524..=1.75).contains(&hit.amount)));
        {
            let enemy = room
                .state_mut(entity)
                .unwrap()
                .unwrap()
                .enemy
                .as_ref()
                .unwrap();
            let body = enemy.scripts.body.snapshot().unwrap();
            let (&weapon_id, weapon) = body
                .instances
                .iter()
                .find(|(_, i)| i.graph == 0x0019)
                .unwrap();
            assert_eq!(weapon.variables[&198], Value::Integer(8));
            assert_eq!(weapon.variables[&53], Value::Integer(7));
            let encoder =
                crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
            let observer = encoder
                .project(body, crate::script_snapshot_wire::View::Observer)
                .unwrap();
            assert_eq!(
                observer
                    .volley_identity(weapon_id, 6)
                    .map(|(counter, _)| counter),
                Some(1)
            );
            encoder.full(&observer).unwrap();
        }
        let pose = room
            .state_mut(entity)
            .unwrap()
            .unwrap()
            .bot
            .movement(arrived);
        let direction = crate::game_hitscan::aim_direction(&Command {
            yaw: pose.yaw,
            pitch: pose.pitch,
            ..Command::default()
        });
        let delta: [f64; 3] = std::array::from_fn(|axis| defender_position[axis] - in_range[axis]);
        let range = norm(delta);
        assert!(
            delta
                .into_iter()
                .zip(direction)
                .map(|(offset, axis)| offset * axis)
                .sum::<f64>()
                > range * 0.99
        );
        let mut eye = in_range;
        eye[1] += 1.55;
        assert_eq!(queries.hitscan_wall(eye, direction, range).unwrap(), None);
        for tick in arrived + 1..=arrived + 5 {
            step(room, tick);
            let state = room.state_mut(entity).unwrap().unwrap();
            assert_eq!(state.bot.position, in_range);
        }
        let relocated = [89.0, 12.6, -64.0];
        room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: defender,
            team: 0,
            pose: TargetPose {
                feet: relocated,
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        }]);
        room.set_mode_boss_pause(true);
        step(room, arrived + 6);
        assert_eq!(
            room.state_mut(entity).unwrap().unwrap().bot.position,
            in_range
        );
        assert_eq!(fire_state(room), (1, 0));
        room.set_mode_boss_pause(false);
        let resumed = (arrived + 7..=arrived + 300)
            .find(|&tick| {
                step(room, tick);
                let bot = &room.state_mut(entity).unwrap().unwrap().bot;
                bot.position != in_range || bot.movement(tick).yaw != pose.yaw
            })
            .expect("Reaper updates its pursuit after the defender moves");
        room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: defender,
            team: 0,
            pose: TargetPose {
                feet: relocated,
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: false,
        }]);
        step(room, resumed + 1);
        assert_eq!(
            room.state_mut(entity).unwrap().unwrap().bot.velocity,
            [0.0; 3]
        );
        assert_eq!(fire_state(room).1, 0);

        room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: defender,
            team: 0,
            pose: TargetPose {
                feet: defender_position,
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
            alive: true,
        }]);
        let mut fired_after_intro_retired = false;
        let mut last_volley = fire_state(room).0;
        let mut empty_magazine = false;
        let mut reloaded_after_empty = false;
        for tick in resumed + 2..=resumed + 750 {
            step(room, tick);
            let (volley, _) = fire_state(room);
            let enemy = room
                .state_mut(entity)
                .unwrap()
                .unwrap()
                .enemy
                .as_ref()
                .unwrap();
            if !enemy.scripts.boss_intro_finished() && volley > last_volley {
                fired_after_intro_retired = true;
            }
            let weapon = enemy
                .scripts
                .body
                .snapshot()
                .unwrap()
                .instances
                .values()
                .find(|instance| instance.graph == 0x0019)
                .unwrap();
            empty_magazine |= weapon.variables[&53] == Value::Integer(0);
            last_volley = volley;
            if volley > 8 {
                reloaded_after_empty = empty_magazine && weapon.variables[&53] == Value::Integer(7);
                break;
            }
        }
        assert!(
            fired_after_intro_retired,
            "Reaper keeps firing after the intro graph is retired"
        );
        assert!(
            reloaded_after_empty,
            "Reaper fires again after exhausting the initial eight rounds"
        );
    }
}
