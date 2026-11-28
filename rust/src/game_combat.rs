//! Match-authoritative direct damage and training bot lifetimes.
//!
//! The projectile and body scripts supply shot events. The match supplies
//! the command and pose at consumption, and owns identity-gated delivery.
//! Hit selection, damage, and respawn order follow ow174/game/combat.py.

use std::{
    collections::{BTreeMap, HashMap},
    io,
    sync::Arc,
    time::Duration,
};

use bevy_ecs::{component::Component, entity::Entity, world::World};
use indexmap::IndexMap;

use crate::{
    actor_voice::ActorVoiceCatalog,
    collision_index::IndexError,
    collision_scene::CollisionScene,
    commands::Command,
    game_bots::{BOT_ENTITY, BOT_RECORD_DELAY, BOT_RESPAWN_TIME, Bot},
    game_combat_messages::CombatNotice,
    game_hitscan::{
        HitDefinition, HitscanShot, HurtPose, Impact, TargetHistory, TargetPose, ViewFrame,
    },
    game_projectiles::ProjectileToken,
    game_replication::BodyId,
    match_scene::bot_create,
    mover::Mover,
    projectile_flight::{ProjectileBallistics, ProjectileFlight},
    pve_encounter::messages::DefenderMessage,
    pve_encounter::navigation::{
        GateApproach, JUNKRAT, REAPER, ROADHOG, ReaperExit, SUMMONER, WITCH, ZOMBARDIER,
    },
    scene_assets::BotQueries,
    script_expr::{RoomEntityFacts, SpatialCandidate},
    script_graph::RoomSpatialNeeds,
    script_init::{HealthTargetFacts, ShotEvent},
    static_ray::{Vec3, capsule_entry_hit, norm, segment_separation},
    vitals::{Death, NamedPoolLoss},
    world::{EntityUpdate, Operation},
};

#[cfg(any(test, feature = "inspector"))]
use crate::game_debug::{Actor, ActorKind};
#[cfg(test)]
use crate::scene_assets::MOVER_CATEGORY;

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
    #[error("player {wire:08X} references missing ECS body {entity:?}")]
    MissingPlayerBody { wire: u32, entity: Entity },
    #[error("player index {wire:08X} references body with wire ID {actual:08X}")]
    PlayerBodyIdentity { wire: u32, actual: u32 },
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
    #[error("gate {wire:08X} references missing ECS component {entity:?}")]
    MissingGate { wire: u32, entity: Entity },
    #[error("gate index {wire:08X} references gate with wire ID {actual:08X}")]
    GateIdentity { wire: u32, actual: u32 },
    #[error("gate {0:08X} is already registered")]
    GateAlreadyRegistered(u32),
    #[error("encounter references missing ECS component {entity:?}")]
    MissingEncounter { entity: Entity },
    #[error("encounter is already registered")]
    EncounterAlreadyRegistered,
    #[error("encounter requires a registered Junkenstein gate")]
    MissingEncounterGate,
    #[error("invalid Junkenstein gate definition: {0}")]
    InvalidGate(&'static str),
    #[error("invalid Junkenstein encounter state: {0}")]
    InvalidEncounter(&'static str),
}

pub(crate) fn eye_height(_hero: u64, crouched: bool) -> f64 {
    crate::mover_settings::MoverSettings::default().eye_height(crouched)
}

pub(crate) fn script_eye_position(_hero: u64, mover: &Mover) -> Option<[f64; 3]> {
    let mut position = mover.pose().position;
    position[1] += mover.eye_height() + f64::from(mover.spring_offset());
    position.into_iter().all(f64::is_finite).then_some(position)
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
    AllyHitscan(Box<PosedHitscan>, crate::game_hitscan::HitTargetPolicy),
    Projectile(Box<PosedProjectile>),
    BioticField(PosedBioticField),
}

pub(crate) const BIOTIC_CANISTER: u64 = 0x0400_0000_0000_072d;
pub(crate) const BIOTIC_FIELD_ENTITY: u64 = 0x0400_0000_0000_03dc;
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

    fn total_at(pulses: &[Self], target: TargetPose) -> f64 {
        pulses
            .iter()
            .filter(|pulse| pulse.reaches(target))
            .map(|pulse| pulse.amount)
            .sum()
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
    pub shot: ShotEvent,
    pub ballistics: ProjectileBallistics,
    pub origin: [f64; 3],
    pub direction: [f64; 3],
    pub predicted: bool,
    pub target_policy: crate::game_hitscan::HitTargetPolicy,
    pub piercing: bool,
    pub impact_spawn: Option<crate::game_projectiles::ImpactSpawn>,
    pub post_bounce_fuse: Option<f64>,
    pub sticky_delay: Option<f64>,
    pub direct_effect: Option<u64>,
    pub blast_effect: Option<u64>,
    pub presentation: Option<Arc<ObservedShot>>,
}

#[derive(Clone, Copy, Debug, Default)]
pub(super) struct HealthPackState {
    pub(super) ready_at_tick: u32,
    pub(super) hacked_by: Option<(u8, u32)>,
}

/// Room-authoritative per-actor combat status effects driven by retail `m_277A4ECA`
/// effect GUIDs (anti-heal / heal-boost, sleep, freeze buildup, lingering HoT).
#[derive(Clone, Copy, Debug, Default)]
pub(super) struct ActorStatus {
    pub(super) anti_heal_until: Option<u32>,
    pub(super) heal_boost_until: Option<u32>,
    pub(super) sleep_until: Option<u32>,
    pub(super) stunned_until: Option<u32>,
    pub(super) hacked_until: Option<u32>,
    pub(super) invulnerable_until: Option<u32>,
    pub(super) cc_immune_until: Option<u32>,
    pub(super) chill_stacks: u8,
    pub(super) chill_last_tick: u32,
    pub(super) hot_ticks: u16,
    pub(super) hot_per_tick: f64,
    pub(super) hot_source: u32,
    pub(super) dot_ticks: u16,
    pub(super) dot_per_tick: f64,
    pub(super) dot_source: u32,
    /// Root effect (e.g. Junkrat Steel Trap): clamps horizontal velocity without blocking weapon fire.
    pub(super) rooted_until: Option<u32>,
    /// Two-stage pull tether `(puller_wire, target_pos, expires_tick)` (e.g. Roadhog Chain Hook).
    pub(super) hook_pull: Option<(u32, Vec3, u32)>,
    /// Continuous beam charge level `0.0..=2.0` (level 1 -> 2 -> 3 ramp, e.g. Symmetra LMB).
    pub(super) beam_charge: f32,
    pub(super) beam_last_hit_tick: u32,
}

/// Fallen ally soul anchor spawned on player death (10s window) for Mercy Resurrect.
#[derive(Clone, Copy, Debug)]
pub(super) struct SoulAnchor {
    pub(super) body: BodyId,
    pub(super) team: u8,
    pub(super) position: Vec3,
    pub(super) expires_tick: u32,
}

/// Active Echo Duplicate transform: reverts cleanly to `original_body` at 0 HP or `expires_tick`.
#[derive(Clone, Copy, Debug)]
pub(super) struct DuplicateState {
    pub(super) original_body: u64,
    pub(super) expires_tick: u32,
}

/// Gliding Self-Destruct bomb (D.Va Q): glides forward and detonates for 1000 LOS damage.
#[derive(Clone, Copy, Debug)]
pub(super) struct SelfDestructBomb {
    pub(super) owner: u32,
    pub(super) team: u8,
    pub(super) position: Vec3,
    pub(super) velocity: Vec3,
    pub(super) detonate_tick: u32,
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
}

#[derive(Clone)]
pub(crate) struct AppliedDamage {
    pub source: u32,
    /// Actual pool loss, including armour reduction and remaining health limits.
    pub amount: f64,
    pub named_pool_losses: Vec<NamedPoolLoss>,
    pub tags: crate::game_hitscan::DamageTags,
    pub target_facts: HealthTargetFacts,
}

#[derive(Clone)]
pub(crate) struct BotReplacement {
    pub retired: u32,
    pub created: EntityUpdate,
    pub waits_for_presentation: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SeatedBotInfo {
    pub slot: u8,
    pub controller: u32,
    pub body: u32,
    pub hero: u64,
    pub team: u8,
    pub name: String,
}

mod deployables;
pub(crate) use deployables::{extract_play_script_stun_ticks, next_pet_wire_id};
mod encounter;
mod enemy;
mod environment;
mod ground_wave;
mod health;
mod health_events;
#[cfg(feature = "lua-mods")]
mod mod_bridge;
mod movement;
pub(crate) use movement::MovementContactFrame;
mod observed_projectiles;
mod player;
mod script_requests;
pub(crate) use script_requests::ActorVoiceQuery;
pub(crate) mod status_scripts;
mod stock_bot;
mod view_targeting;
pub(crate) use encounter::{
    ENCOUNTER_INSTANCE, EncounterRegistration, EncounterWorld, GateState, GateWorld,
};
use encounter::{EncounterComponent, GateHandle};
#[cfg(test)]
pub(crate) use encounter::{EncounterPhase, GateDamageStage, GateHealth};
use enemy::{BotState, EnemyState};
use health::HealthEffect;
use health_events::{HealthDamageEvidence, HealthEventContext, SampledBody};
pub(crate) use observed_projectiles::{ObservedProjectileEvent, ObservedShot};
pub(crate) use player::{PlayerBodyEvent, PlayerBodyHandle, PlayerBodyState, PlayerDeath};
use script_requests::{ActorHealthSnapshot, PlayerRoomCue, RoomScriptTarget};
pub(crate) use script_requests::{RoomScriptRequest, RoomScriptRequests};

#[derive(Component)]
pub(crate) struct AdmittedFlight {
    pub stun: Option<Stun>,
    pub hit_effect: Option<crate::script_init::Site>,
    pub token: ProjectileToken,
    pub launch_frame: u32,
    pub flight: ProjectileFlight,
    pub impact_spawn: Option<crate::game_projectiles::ImpactSpawn>,
    pub presentation: Option<Arc<ObservedShot>>,
}

#[derive(Clone)]
pub(crate) struct DeathSourceFacts {
    pub(crate) target: HealthTargetFacts,
    pub(crate) velocity: Vec3,
}

pub(crate) enum FlightOutcome {
    Ended {
        token: ProjectileToken,
        travelled: f64,
        impact: bool,
    },
    Failed(ProjectileToken),
}

struct FlightBatch {
    owner: BodyId,
    order: Vec<Entity>,
}

/// One room frame's living candidates, optional body facts and static collision view.
#[derive(Clone)]
pub(crate) struct RoomSpatialFrame {
    candidates: Vec<SpatialCandidate>,
    actor_capsules: Option<BTreeMap<u32, TargetPose>>,
    entity_facts: Option<BTreeMap<u32, RoomEntityFacts>>,
    script_targets: HashMap<u32, RoomScriptTarget>,
    dead_voice: HashMap<u32, script_requests::DeathVoiceBody>,
    player_wires: HashMap<u32, u32>,
    ground: Arc<CollisionScene>,
    breakables: Option<Arc<crate::collision_scene::BreakableState>>,
    actor_voice: Option<Arc<ActorVoiceCatalog>>,
    map_guid: Option<u64>,
    retired: Arc<std::collections::BTreeSet<u32>>,
    #[cfg(any(test, feature = "inspector"))]
    captured_tick: u32,
    #[cfg(any(test, feature = "inspector"))]
    debug_rays: Arc<crate::game_debug::DebugRayHistory>,
}

// The stock impact-sphere and ground-wave target filters select living damageable bodies.
pub(crate) const DAMAGEABLE_BODY_MASK: u64 = 1 << 6;

impl RoomSpatialFrame {
    pub(crate) fn is_retired(&self, wire: u32) -> bool {
        self.retired.contains(&wire)
    }

    pub(crate) fn body_generation(&self, wire: u32) -> Option<u64> {
        Some(self.script_targets.get(&wire)?.entity.to_bits())
    }

    pub(crate) fn candidates(&self) -> &[SpatialCandidate] {
        &self.candidates
    }

    pub(crate) fn empty_view_targets(
        &self,
        query: &crate::script_init::ViewTargetQuery<'_>,
    ) -> Option<Vec<u32>> {
        self.actor_sample(query.source)?;
        (query.limit == 0
            || self
                .candidates
                .iter()
                .all(|c| c.entity == query.source || self.actor_sample(c.entity).is_none()))
        .then(Vec::new)
    }

    pub(crate) fn actor_pose(&self, entity: u32) -> Option<(Vec3, [i32; 2])> {
        let target = self.script_targets.get(&entity)?;
        Some((target.position, target.angles))
    }

    pub(crate) fn actor_bone_point(
        &self,
        graphs: &crate::script_graph::GraphStore,
        entity: u32,
        bone: u64,
        local: Vec3,
    ) -> Option<Vec3> {
        let target = self.script_targets.get(&entity)?;
        let model_point = if bone == 0 {
            local
        } else {
            let (body, skin) = target.model?;
            graphs.bone_reference_point(body, skin, bone, local)?
        };
        let forward = crate::game_hitscan::direction_for_angles(target.angles[0], 0);
        crate::script_expr::body_space_point(target.position, Some(forward), model_point)
    }

    pub(crate) fn actor_velocity(&self, entity: u32) -> Option<Vec3> {
        Some(self.script_targets.get(&entity)?.velocity)
    }

    pub(crate) fn actor_variable(&self, entity: u32, id: u32) -> Option<crate::script_wire::Value> {
        let target = self.script_targets.get(&entity)?;
        target
            .script_state
            .read(id)
            .or(Some(crate::script_wire::Value::Null))
    }

    pub(crate) fn actor_alive(&self, entity: u32) -> bool {
        self.script_targets.contains_key(&entity)
    }

    pub(crate) fn measured_actor_pose(&self, entity: u32) -> Option<TargetPose> {
        self.actor_capsules.as_ref()?.get(&entity).copied()
    }

    pub(crate) fn actor_target_pose(&self, entity: u32, position: Vec3) -> TargetPose {
        self.measured_actor_pose(entity).unwrap_or(TargetPose {
            feet: position,
            height: 1.8,
            radius: 0.4,
            eye: 1.5,
        })
    }

    pub(crate) fn aim_point_visible(&self, origin: Vec3, target: TargetPose) -> bool {
        let [bottom, top] = target.segment();
        let delta = [
            target.feet[0] - origin[0],
            (target.feet[1] + target.eye).min(top[1]) - origin[1],
            target.feet[2] - origin[2],
        ];
        let length = crate::static_ray::norm(delta);
        if length <= 1e-9 {
            return true;
        }
        let direction = delta.map(|axis| axis / length);
        let Some(reach) = crate::static_ray::capsule_entry_distance(
            origin,
            direction,
            length,
            bottom,
            top,
            target.radius,
        ) else {
            return false;
        };
        reach <= 1e-9
            || self
                .ground
                .checked_cast_ray_with_breakables(
                    origin,
                    direction,
                    reach,
                    self.terrain_category(),
                    self.breakables.as_deref(),
                )
                .is_some_and(|hit| hit.is_none_or(|hit| hit.distance + 1e-9 >= reach))
    }

    pub(crate) fn target_aim_direction(&self, origin: Vec3, entity: u32) -> Option<Vec3> {
        let (candidate, _) = self.actor_sample(entity)?;
        let target = self.actor_target_pose(entity, candidate.position);
        let [_, top] = target.segment();
        let delta = [
            target.feet[0] - origin[0],
            (target.feet[1] + target.eye).min(top[1]) - origin[1],
            target.feet[2] - origin[2],
        ];
        let distance = crate::static_ray::norm(delta);
        (distance > 0.0 && self.aim_point_visible(origin, target))
            .then(|| delta.map(|axis| axis / distance))
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn cone_targets(
        &self,
        source: u32,
        origin: Vec3,
        forward: Vec3,
        radius: f64,
        angle: f64,
        cylinder_radius: f64,
        visible: bool,
        relation: u64,
    ) -> Option<Vec<u32>> {
        let own_team = self.actor_sample(source)?.0.team;
        let check_cone = angle < 359.999;
        // Retail `m_A30B383E` is already authored as a half-angle in degrees
        // (e.g. Winston Tesla Cannon = 40 deg each side = 80 deg total cone;
        // Moira Biotic Grasp RMB = 0 deg with `m_16BDE607` lock-on cylinder radius).
        let half_angle_rad = angle.to_radians();
        let cyl = if cylinder_radius.is_finite() && cylinder_radius > 0.0 {
            cylinder_radius
        } else {
            0.0
        };
        let mut matches = Vec::new();
        for candidate in &self.candidates {
            let team_ok = match relation {
                1 => own_team.is_some() && candidate.team == own_team,
                2 => {
                    candidate.entity != source && (own_team.is_none() || candidate.team != own_team)
                }
                _ => candidate.entity != source,
            };
            if !team_ok {
                continue;
            }
            let target = self.actor_target_pose(candidate.entity, candidate.position);
            let [bottom, top] = target.segment();
            let centre_y = 0.5 * (bottom[1] + top[1]);
            let delta = [
                target.feet[0] - origin[0],
                centre_y - origin[1],
                target.feet[2] - origin[2],
            ];
            let distance = crate::static_ray::norm(delta);
            if distance <= 1e-6 || distance > radius + target.radius {
                continue;
            }
            if check_cone {
                let projection: f64 = delta.iter().zip(forward).map(|(a, b)| a * b).sum();
                if projection <= 0.0 {
                    continue;
                }
                let perp_sq = (distance * distance - projection * projection).max(0.0);
                let allowed_radius =
                    target.radius + cyl + projection * half_angle_rad.tan().max(0.0);
                if perp_sq > allowed_radius * allowed_radius {
                    continue;
                }
            }
            if visible && !self.aim_point_visible(origin, target) {
                continue;
            }
            matches.push((distance, candidate.entity));
        }
        matches.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        Some(matches.into_iter().map(|(_, entity)| entity).collect())
    }

    pub(crate) fn actor_eye_position(&self, entity: u32) -> Option<Vec3> {
        self.actor_sample(entity)?.1.eye_position
    }

    pub(crate) fn actor_controller(&self, body: u32) -> Option<u32> {
        Some(self.actor_sample(body)?.1.controller.unwrap_or(0))
    }

    pub(crate) fn actor_filter_flags(&self, entity: u32) -> Option<u64> {
        let target = self.actor_sample(entity)?.1;
        Some(
            target.filter_flags?
                | if target.health {
                    DAMAGEABLE_BODY_MASK
                } else {
                    0
                },
        )
    }

    pub(crate) fn actor_health(
        &self,
        entity: u32,
        kind: Option<crate::vitals::PoolKind>,
        maximum: bool,
    ) -> Option<f64> {
        let amount = self
            .script_targets
            .get(&entity)?
            .health_snapshot?
            .amount(kind, maximum);
        (amount.is_finite() && amount >= 0.0).then_some(amount)
    }

    /// Opaque room-local identity from the same frozen actor admission as queries.
    pub(crate) fn actor_identity(&self, entity: u32) -> Option<u64> {
        Some(self.actor_sample(entity)?.1.entity.to_bits())
    }

    fn actor_sample(&self, entity: u32) -> Option<(&SpatialCandidate, &RoomScriptTarget)> {
        let target = self.script_targets.get(&entity)?;
        let candidate = self.candidates.get(target.candidate_index?)?;
        (candidate.entity == entity).then_some((candidate, target))
    }

    pub(crate) fn entity_facts(&self) -> Option<&BTreeMap<u32, RoomEntityFacts>> {
        self.entity_facts.as_ref()
    }

    pub(crate) fn voice_line_duration(&self, line: u64) -> Option<f64> {
        Some(self.actor_voice.as_deref()?.line_duration(line))
    }

    pub(crate) fn terrain_category(&self) -> u16 {
        crate::scene_assets::weapon_terrain_category(&self.ground)
    }

    pub(crate) fn line_clear(&self, origin: Vec3, target: Vec3) -> Option<bool> {
        self.ground.segment_clear_with_breakables(
            origin,
            target,
            self.terrain_category(),
            self.breakables.as_deref(),
        )
    }

    pub(crate) fn hostile_contact_targets(
        &self,
        source: u32,
        centre: Vec3,
        origin: Vec3,
        radius: f64,
        category: u16,
    ) -> Option<Vec<crate::script_init::ContactTarget>> {
        let source = self.actor_sample(source)?.0;
        let capsules = self.actor_capsules.as_ref()?;
        if !centre.into_iter().chain(origin).all(f64::is_finite)
            || !radius.is_finite()
            || radius <= 0.0
        {
            return None;
        }
        if let Some(breakables) = self.breakables.as_deref() {
            for placement in self
                .ground
                .breakables_in_sphere(centre, radius, Some(breakables))
            {
                breakables.mark_broken(placement);
            }
        }
        let mut targets = Vec::new();
        for candidate in &self.candidates {
            if candidate.entity == source.entity
                || source.team.is_some_and(|team| candidate.team == Some(team))
                || !self.script_targets.get(&candidate.entity)?.health
            {
                continue;
            }
            let pose = *capsules.get(&candidate.entity)?;
            let surface = HurtPose::from(pose).nearest_surface(centre, origin)?;
            if surface.distance > radius {
                continue;
            }
            if !surface
                .visible(|direction, reach| {
                    self.ground
                        .checked_cast_ray_with_breakables(
                            origin,
                            direction,
                            reach,
                            category,
                            self.breakables.as_deref(),
                        )
                        .map(|hit| hit.map(|hit| hit.distance))
                        .ok_or(())
                })
                .ok()?
            {
                continue;
            }
            if targets.len() == script_requests::MAX_BODY_SCRIPT_REQUESTS {
                return None;
            }
            targets.push(crate::script_init::ContactTarget {
                entity: candidate.entity,
                feet: pose.feet,
            });
        }
        Some(targets)
    }

    pub(crate) fn spray_placement(
        &self,
        origin: Vec3,
        direction: Vec3,
        reach: f64,
    ) -> Option<Option<crate::script_init::SprayPlacement>> {
        const SPRAY_SURFACE: u16 = 0x40;
        self.ground
            .checked_cast_ray_with_breakables(
                origin,
                direction,
                reach,
                SPRAY_SURFACE,
                self.breakables.as_deref(),
            )
            .map(|hit| {
                hit.map(|hit| crate::script_init::SprayPlacement {
                    direction: direction.map(|value| value as f32),
                    point: hit.point.map(|value| value as f32),
                    normal: hit.normal.map(|value| value as f32),
                    attachment: None,
                })
            })
    }

    pub(crate) fn ray_cast(
        &self,
        site: crate::script_init::Site,
        vm_frame: u32,
        origin: Vec3,
        direction: Vec3,
        max_distance: f64,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        self.profile_ray_cast(
            site,
            vm_frame,
            origin,
            direction,
            max_distance,
            self.terrain_category(),
        )
    }

    #[cfg(test)]
    pub(crate) fn hostile_sphere_candidates(
        &self,
        source: u32,
        centre: Vec3,
        radius: f64,
    ) -> Option<Vec<u32>> {
        if !radius.is_finite() || radius < 0.0 || !centre.into_iter().all(f64::is_finite) {
            return None;
        }
        let team = self.actor_sample(source)?.0.team;
        let poses = self.actor_capsules.as_ref()?;
        let mut result = Vec::new();
        for actor in &self.candidates {
            if actor.entity == source || team.is_some_and(|t| actor.team == Some(t)) {
                continue;
            }
            let capsule = poses.get(&actor.entity)?;
            let [a, b] = capsule.segment();
            if crate::static_ray::segment_distance(centre, a, b) <= radius + capsule.radius {
                result.push(actor.entity);
            }
        }
        Some(result)
    }

    pub(crate) fn profile_ray_cast(
        &self,
        site: crate::script_init::Site,
        vm_frame: u32,
        origin: Vec3,
        direction: Vec3,
        max_distance: f64,
        category: u16,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        #[cfg(not(any(test, feature = "inspector")))]
        let _ = (site, vm_frame);
        let result = self
            .ground
            .checked_cast_ray_with_breakables(
                origin,
                direction,
                max_distance,
                category,
                self.breakables.as_deref(),
            )
            .map(|hit| {
                hit.map(|hit| crate::script_expr::SpatialRayHit {
                    distance: hit.distance,
                    point: hit.point,
                    normal: hit.normal,
                })
            });
        #[cfg(any(test, feature = "inspector"))]
        {
            let (hit, end) = match result {
                Some(Some(hit)) => ("world", hit.point),
                Some(None) => (
                    "none",
                    crate::static_ray::along(origin, direction, max_distance),
                ),
                None => (
                    "unavailable",
                    crate::static_ray::along(origin, direction, max_distance),
                ),
            };
            self.record_script_ray(site, vm_frame, "script_static", [origin, end], (hit, None));
        }
        result
    }

    pub(crate) fn profiled_static_ray(
        &self,
        origin: Vec3,
        direction: Vec3,
        max_distance: f64,
        category: u16,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        self.ground
            .checked_cast_ray(origin, direction, max_distance, category)
            .map(|hit| {
                hit.map(|hit| crate::script_expr::SpatialRayHit {
                    distance: hit.distance,
                    point: hit.point,
                    normal: hit.normal,
                })
            })
    }

    pub(crate) fn actor_ray_cast(
        &self,
        site: crate::script_init::Site,
        vm_frame: u32,
        origin: Vec3,
        direction: Vec3,
        max_distance: f64,
        targets: &[u32],
    ) -> Option<Option<crate::script_expr::SpatialActorRayHit>> {
        self.actor_beam_cast(
            site,
            vm_frame,
            origin,
            direction,
            max_distance,
            0.0,
            targets,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn actor_beam_cast(
        &self,
        site: crate::script_init::Site,
        vm_frame: u32,
        origin: Vec3,
        direction: Vec3,
        max_distance: f64,
        beam_radius: f64,
        targets: &[u32],
    ) -> Option<Option<crate::script_expr::SpatialActorRayHit>> {
        #[cfg(not(any(test, feature = "inspector")))]
        let _ = (site, vm_frame);
        #[cfg(any(test, feature = "inspector"))]
        let mut occluding_point = None;
        let extra_radius = if beam_radius.is_finite() && beam_radius > 0.0 {
            beam_radius
        } else {
            0.0
        };
        // Keep availability and actor admission atomic: a partially checked
        // candidate list still returns unavailable, including in diagnostics.
        let result = (|| {
            let capsules = self.actor_capsules.as_ref()?;
            let wall = self.ground.checked_cast_ray_with_breakables(
                origin,
                direction,
                max_distance,
                self.terrain_category(),
                self.breakables.as_deref(),
            )?;
            let mut nearest: Option<crate::script_expr::SpatialActorRayHit> = None;
            for &entity in targets {
                let target = self.script_targets.get(&entity)?;
                let candidate = self.candidates.get(target.candidate_index?)?;
                let pose = capsules.get(&entity)?;
                if candidate.entity != entity
                    || candidate.position != pose.feet
                    || !pose.feet.into_iter().all(f64::is_finite)
                    || !pose.height.is_finite()
                    || pose.height < 0.0
                    || !pose.radius.is_finite()
                    || pose.radius <= 0.0
                {
                    return None;
                }
                let [bottom, top] = pose.segment();
                if !bottom.into_iter().chain(top).all(f64::is_finite) {
                    return None;
                }
                let Some(hit) = capsule_entry_hit(
                    origin,
                    direction,
                    max_distance,
                    bottom,
                    top,
                    pose.radius + extra_radius,
                ) else {
                    continue;
                };
                let distance = hit.distance;
                if wall.is_some_and(|wall| wall.distance < distance) {
                    #[cfg(any(test, feature = "inspector"))]
                    {
                        occluding_point = wall.map(|wall| wall.point);
                    }
                    continue;
                }
                if nearest.as_ref().is_none_or(|hit| {
                    distance < hit.distance || (distance == hit.distance && entity < hit.entity)
                }) {
                    nearest = Some(crate::script_expr::SpatialActorRayHit {
                        entity,
                        distance,
                        normal: hit.normal,
                    });
                }
            }
            Some(nearest)
        })();
        #[cfg(any(test, feature = "inspector"))]
        {
            let (hit, end, target) = match result {
                Some(Some(hit)) => (
                    "actor",
                    crate::static_ray::along(origin, direction, hit.distance),
                    Some(hit.entity),
                ),
                Some(None) => match occluding_point {
                    Some(point) => ("world", point, None),
                    None => (
                        "none",
                        crate::static_ray::along(origin, direction, max_distance),
                        None,
                    ),
                },
                None => (
                    "unavailable",
                    crate::static_ray::along(origin, direction, max_distance),
                    None,
                ),
            };
            self.record_script_ray(site, vm_frame, "script_actor", [origin, end], (hit, target));
        }
        result
    }

    #[cfg(any(test, feature = "inspector"))]
    fn record_script_ray(
        &self,
        site: crate::script_init::Site,
        vm_frame: u32,
        kind: &'static str,
        [origin, end]: [Vec3; 2],
        (hit, target): (&'static str, Option<u32>),
    ) {
        if !origin.into_iter().chain(end).all(f64::is_finite) {
            return;
        }
        self.debug_rays.push(crate::game_debug::DebugRay {
            tick: self.captured_tick,
            source: site.entity,
            kind,
            origin,
            end,
            hit,
            target,
            critical: false,
            surface: None,
            script: Some(crate::game_debug::DebugScriptRay {
                graph: site.graph,
                instance: site.instance,
                node: site.node,
                vm_frame,
            }),
        });
    }
}

struct RoomSpatialQueries {
    free_for_all: bool,
    ground: Arc<CollisionScene>,
    breakables: Option<Arc<crate::collision_scene::BreakableState>>,
    needs: RoomSpatialNeeds,
    actor_movers: Option<Arc<crate::mover_settings::MoverCatalog>>,
    actor_voice: Option<Arc<ActorVoiceCatalog>>,
}

#[derive(Clone, Copy, Debug)]
struct EliminationStreak {
    last_frame: u32,
    expires_at: u32,
    count: u32,
}

/// Room-authoritative bot bodies, player bodies, and in-flight projectiles.
pub(crate) struct RoomSimulation {
    world: World,
    // Wire lookup also preserves the authored spawner order across respawns.
    by_wire: IndexMap<u32, Entity>,
    players_by_wire: IndexMap<u32, Entity>,
    players_by_owner: IndexMap<u32, PlayerBodyHandle>,
    team_member_counts: Option<crate::game_instance::TeamMemberCounts>,
    free_for_all: bool,
    map_guid: Option<u64>,
    combat_voice: Arc<crate::combat_voice::CombatVoiceCatalog>,
    body_contact_sets: BTreeMap<u64, u16>,
    received_damage_feedback: bool,
    hurt_voice_history: HashMap<u32, (u32, usize)>,
    spatial_queries: Option<RoomSpatialQueries>,
    spatial_frame: Option<(u32, Arc<RoomSpatialFrame>)>,
    movement_contacts: Option<movement::MovementContacts>,
    environment: crate::map_environment::MapEnvironment,
    ability_rules: Arc<crate::hero_ability_rules::HeroAbilityRules>,
    health_events: health_events::RoomHealthEvents,
    // Generation maps live for the room's lifetime so reconnects cannot reset history.
    player_generations: HashMap<u32, u64>,
    latest_wire_generations: HashMap<u32, u64>,
    next_bot: u32,
    combat_active: bool,
    player_targets_active: bool,
    protected_hero: Option<u64>,
    created: Vec<EntityUpdate>,
    player_events: Vec<PlayerBodyEvent>,
    player_deaths: Vec<player::PlayerDeath>,
    observer_player_health: Vec<(u32, EntityUpdate)>,
    scene_messages: Vec<crate::script_enemy::SceneMessage>,
    external_targets: Option<Vec<crate::script_enemy::EnemyTarget>>,
    external_enemy_damage: Vec<crate::script_enemy::EnemyDamage>,
    scripted_health: Vec<EntityUpdate>,
    retired_enemies: Vec<u32>,
    retired_entity_ids: Arc<std::collections::BTreeSet<u32>>,
    defender_messages: Vec<DefenderMessage>,
    mode_voice_requests: Vec<crate::pve_encounter::voice::VoiceRequest>,
    player_cues: Vec<PlayerRoomCue>,
    mode_voice_set: Option<u64>,
    mode_boss_pause: bool,
    spawn_anchors: Option<Arc<crate::scene_assets::SpawnAnchors>>,
    script_graphs: Option<(Arc<crate::script_graph::GraphStore>, u64)>,
    script_codecs: Option<Arc<crate::script_codecs::CodecTable>>,
    stock_bots: Option<stock_bot::StockBotCatalog>,
    gate_handle: Option<GateHandle>,
    encounter_entity: Option<Entity>,
    // Handle lists preserve launch order within the worker's ordered peer turns.
    projectiles: HashMap<u32, FlightBatch>,
    server_projectile_wires: HashMap<Entity, u32>,
    enemy_projectiles: Vec<Entity>,
    projectile_reports: observed_projectiles::RoomProjectileReports,
    witch_staff_links: IndexMap<u32, (u32, u16)>,
    room_pets: IndexMap<u32, deployables::RoomPet>,
    room_deployables: IndexMap<u32, deployables::RoomDeployable>,
    actor_statuses: HashMap<u32, ActorStatus>,
    status_scripts: IndexMap<(u32, u32), status_scripts::ActiveStatusScript>,
    soul_anchors: Vec<SoulAnchor>,
    active_duplicates: HashMap<u32, DuplicateState>,
    pending_duplicate_reverts: HashMap<u32, u64>,
    self_destruct_bombs: Vec<SelfDestructBomb>,
    health_pack_states: Vec<HealthPackState>,
    jump_pad_cooldowns: HashMap<u32, u32>,
    scheduled_voices: Vec<(u32, u32, u64, u64)>,
    bot_reply_cooldowns: HashMap<u32, u32>,
    banter_scheduled: bool,
    scoreboard: crate::game_progression::MatchScoreboard,
    stats_catalog: Option<(Arc<crate::game_stats::StatsCatalog>, u64)>,
    replay: crate::game_replay::HighlightRecorder,
    voice_chat: crate::game_replay::VoiceChatRouter,
    interactive_props: crate::room_primitives::InteractivePropRuntime,
    interactive_prop_floor_y: f64,
    pvp_objective: Option<crate::pvp_mode::PvpObjectiveRuntime>,
    pvp_completion_emitted: bool,
    pending_match_winner: Option<u8>,
    last_pvp_payload_pos: Option<[f64; 3]>,
    last_pvp_hud_key: Option<(u8, i32, u8, u8)>,
    completed_career_records: Vec<(u32, crate::game_progression::CareerMatchRecord)>,
    player_names: HashMap<u32, String>,
    #[cfg(feature = "lua-mods")]
    mod_host: Option<Arc<std::sync::Mutex<crate::mod_script::ModHost>>>,
    #[cfg(feature = "lua-mods")]
    mod_state: mod_bridge::RoomModState,
    #[cfg(any(test, feature = "inspector"))]
    debug_rays: Arc<crate::game_debug::DebugRayHistory>,
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
            spatial_frame: self
                .spatial_frame
                .as_ref()
                .map(|(_, frame)| Arc::clone(frame)),
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
            stock_script: None,
            base_target_tags: Default::default(),
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
    #[cfg(test)]
    pub(crate) fn set_external_targets(
        &mut self,
        mut targets: Vec<crate::script_enemy::EnemyTarget>,
    ) {
        targets.sort_unstable_by_key(|target| target.entity);
        self.external_targets = Some(targets);
    }
    #[cfg(test)]
    pub(crate) fn take_external_enemy_damage(&mut self) -> Vec<crate::script_enemy::EnemyDamage> {
        std::mem::take(&mut self.external_enemy_damage)
    }

    /// Synthetic post-AI ingress for testing the real room/owner damage boundary.
    #[cfg(test)]
    pub(crate) fn queue_test_external_enemy_damage(
        &mut self,
        hit: crate::script_enemy::EnemyDamage,
    ) {
        self.external_enemy_damage.push(hit);
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
        let mut updates = self
            .states()
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
            .collect::<io::Result<Vec<_>>>()?;
        updates.extend(self.initial_deployable_creates());
        Ok(updates)
    }

    pub(crate) fn tracks_scene_entity(&self, entity: u32) -> bool {
        self.by_wire.contains_key(&entity)
            || self.room_pets.contains_key(&entity)
            || self.room_deployables.contains_key(&entity)
    }

    pub(crate) fn load_bot_target_tags(&mut self, graphs: &crate::script_graph::GraphStore) {
        static EMPTY_TAGS: std::sync::LazyLock<std::collections::BTreeSet<u64>> =
            std::sync::LazyLock::new(std::collections::BTreeSet::new);
        for &entity in self.by_wire.values() {
            if let Some(mut state) = self.world.get_mut::<BotState>(entity) {
                let body = graphs.entity_body(state.bot.spawn.body);
                state.base_target_tags =
                    crate::script_init::TargetTagEffects::default().snapshot(Some(
                        body.and_then(|body| body.target_tags.as_ref())
                            .unwrap_or(&EMPTY_TAGS),
                    ));
            }
        }
    }

    pub(crate) fn set_script_graphs(
        &mut self,
        graphs: Arc<crate::script_graph::GraphStore>,
        mode: u64,
    ) {
        self.script_graphs = Some((graphs, mode));
    }

    pub(crate) fn set_script_codecs(&mut self, codecs: Arc<crate::script_codecs::CodecTable>) {
        self.script_codecs = Some(codecs);
    }

    pub(crate) fn enable_stock_bot_scripts(
        &mut self,
        graphs: Arc<crate::script_graph::GraphStore>,
        codecs: Arc<crate::script_codecs::CodecTable>,
        mode: u64,
    ) {
        self.script_graphs = Some((Arc::clone(&graphs), mode));
        self.script_codecs = Some(Arc::clone(&codecs));
        let catalog = stock_bot::StockBotCatalog {
            graphs,
            codecs,
            mode,
        };
        let entities: Vec<_> = self.by_wire.values().copied().collect();
        for entity in entities {
            let mut state = self
                .world
                .get_mut::<BotState>(entity)
                .expect("live bot handle");
            if state.enemy.is_none() && state.stock_script.is_none() {
                state.stock_script = stock_bot::StockBotScript::start(
                    &catalog,
                    &state.bot,
                    crate::game_clock::FIRST_TICK,
                    stock_bot::BodyEntry::Initial,
                );
            }
        }
        self.stock_bots = Some(catalog);
    }

    pub(crate) fn seat_bots(&mut self, map: &crate::game_content::GameMap, player_count: usize) {
        if map.free_for_all {
            return;
        }
        let first = map
            .team_sizes
            .iter()
            .map(|&count| usize::from(count))
            .sum::<usize>()
            .max(player_count);
        let entities: Vec<_> = self.by_wire.values().copied().collect();
        let mut offset = 0usize;
        for entity in entities {
            let state = self
                .world
                .get_mut::<BotState>(entity)
                .expect("live bot handle")
                .into_inner();
            if state.enemy.is_some() {
                continue;
            }
            let seat = first.saturating_add(offset);
            offset += 1;
            state.bot.slot = (seat < crate::game_instance::PARTICIPANTS)
                .then(|| u8::try_from(seat).expect("participant slot fits in u8"));
        }
    }

    pub(crate) fn seated_bots(&self) -> Vec<SeatedBotInfo> {
        self.by_wire
            .values()
            .filter_map(|&entity| {
                let state = self.world.get::<BotState>(entity)?;
                if state.enemy.is_some() {
                    return None;
                }
                let slot = state.bot.slot?;
                let controller = state.bot.controller()?;
                Some(SeatedBotInfo {
                    slot,
                    controller,
                    body: state.bot.entity,
                    hero: state.bot.spawn.hero,
                    team: state.bot.spawn.team,
                    name: state.bot.spawn.name.clone(),
                })
            })
            .collect()
    }

    pub(crate) fn is_bot_dead(&self, wire: u32) -> bool {
        self.by_wire
            .get(&wire)
            .and_then(|&entity| self.world.get::<BotState>(entity))
            .is_some_and(|state| state.bot.dead)
    }

    pub(crate) fn tracks_living_actor(&self, wire: u32) -> bool {
        self.by_wire
            .get(&wire)
            .and_then(|&entity| self.world.get::<BotState>(entity))
            .is_some_and(|state| !state.bot.dead)
            || self
                .players_by_wire
                .get(&wire)
                .and_then(|&entity| self.world.get::<PlayerBodyState>(entity))
                .is_some_and(|state| state.id.entity == wire && state.alive())
    }

    fn advance_stock_bots(
        &mut self,
        queries: &BotQueries<'_>,
        tick: u32,
    ) -> Vec<observed_projectiles::StockShotBatch> {
        let mut launches = Vec::new();
        let contacts = self.movement_contact_frame();
        let entities: Vec<_> = self.by_wire.values().copied().collect();
        for entity in entities {
            let state = self
                .world
                .get_mut::<BotState>(entity)
                .expect("live bot handle")
                .into_inner();
            if let Some(script) = state.stock_script.as_mut() {
                let bot = &mut state.bot;
                let prior_walk = script.walk_request();
                if let Some((destination, speed)) = prior_walk
                    .filter(|_| !bot.dead && !bot.is_stunned(tick) && !bot.patrol_active())
                {
                    if let Some(contacts) = &contacts {
                        contacts.stage_bot(bot, tick);
                    }
                    queries.step_ai_walk(bot, tick, destination, speed);
                }
                script.advance(tick, bot);
                if prior_walk.is_some() && !bot.patrol_active() && script.walk_request().is_none() {
                    bot.end_walk_request(tick);
                }
                launches.extend(script.take_projectiles(tick, &state.bot));
            }
        }
        launches
    }

    pub(crate) fn enemy_projections(
        &self,
    ) -> Result<impl Iterator<Item = crate::script_snapshot_wire::SceneProjection<'_>>, CombatError>
    {
        Ok(self.checked_states()?.into_iter().filter_map(|state| {
            if let Some(enemy) = state.enemy.as_ref().filter(|enemy| enemy.fault.is_none()) {
                return Some(crate::script_snapshot_wire::SceneProjection {
                    snapshot: enemy.scripts.body.snapshot()?,
                    encoder: crate::script_snapshot_wire::SnapshotEncoder::new(
                        &enemy.graphs,
                        &enemy.codecs,
                    ),
                });
            }
            state
                .stock_script
                .as_ref()
                .and_then(stock_bot::StockBotScript::projection)
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
        if let Some(entity) = self.encounter_entity {
            if let Some(mut encounter) = self.world.get_mut::<EncounterComponent>(entity) {
                encounter.boss_pause = active;
            }
        }
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
            players_by_wire: IndexMap::new(),
            players_by_owner: IndexMap::new(),
            team_member_counts: None,
            free_for_all: false,
            map_guid: None,
            combat_voice: Arc::default(),
            body_contact_sets: BTreeMap::new(),
            received_damage_feedback: false,
            hurt_voice_history: HashMap::new(),
            spatial_queries: None,
            spatial_frame: None,
            movement_contacts: None,
            environment: Default::default(),
            ability_rules: crate::hero_ability_rules::HeroAbilityRules::default_shared(),
            health_events: health_events::RoomHealthEvents::default(),
            player_generations: HashMap::new(),
            latest_wire_generations: HashMap::new(),
            next_bot,
            combat_active: false,
            player_targets_active: false,
            protected_hero: None,
            created: Vec::new(),
            player_events: Vec::new(),
            player_deaths: Vec::new(),
            observer_player_health: Vec::new(),
            scene_messages: Vec::new(),
            external_targets: None,
            external_enemy_damage: Vec::new(),
            scripted_health: Vec::new(),
            retired_enemies: Vec::new(),
            retired_entity_ids: Arc::default(),
            defender_messages: Vec::new(),
            mode_voice_requests: Vec::new(),
            player_cues: Vec::new(),
            mode_voice_set: None,
            mode_boss_pause: false,
            spawn_anchors: None,
            script_graphs: None,
            script_codecs: None,
            stock_bots: None,
            gate_handle: None,
            encounter_entity: None,
            projectiles: HashMap::new(),
            server_projectile_wires: HashMap::new(),
            enemy_projectiles: Vec::new(),
            projectile_reports: Default::default(),
            witch_staff_links: IndexMap::new(),
            room_pets: IndexMap::new(),
            room_deployables: IndexMap::new(),
            actor_statuses: HashMap::new(),
            status_scripts: IndexMap::new(),
            soul_anchors: Vec::new(),
            active_duplicates: HashMap::new(),
            pending_duplicate_reverts: HashMap::new(),
            self_destruct_bombs: Vec::new(),
            health_pack_states: Vec::new(),
            jump_pad_cooldowns: HashMap::new(),
            scheduled_voices: Vec::new(),
            bot_reply_cooldowns: HashMap::new(),
            banter_scheduled: false,
            scoreboard: crate::game_progression::MatchScoreboard::default(),
            stats_catalog: None,
            replay: crate::game_replay::HighlightRecorder::default(),
            voice_chat: crate::game_replay::VoiceChatRouter::default(),
            interactive_props: crate::room_primitives::InteractivePropRuntime::default(),
            interactive_prop_floor_y: 0.0,
            pvp_objective: None,
            pvp_completion_emitted: false,
            pending_match_winner: None,
            last_pvp_payload_pos: None,
            last_pvp_hud_key: None,
            completed_career_records: Vec::new(),
            player_names: HashMap::new(),
            #[cfg(feature = "lua-mods")]
            mod_host: None,
            #[cfg(feature = "lua-mods")]
            mod_state: mod_bridge::RoomModState::default(),
            #[cfg(any(test, feature = "inspector"))]
            debug_rays: Arc::default(),
        })
    }

    #[cfg(test)]
    pub(crate) fn interactive_props(&self) -> &crate::room_primitives::InteractivePropRuntime {
        &self.interactive_props
    }

    #[cfg(any(test, feature = "lua-mods"))]
    pub(crate) fn interactive_props_mut(
        &mut self,
    ) -> &mut crate::room_primitives::InteractivePropRuntime {
        &mut self.interactive_props
    }

    #[cfg(test)]
    pub(crate) fn set_interactive_prop_floor_y(&mut self, floor_y: f64) {
        if floor_y.is_finite() {
            self.interactive_prop_floor_y = floor_y;
        }
    }

    #[cfg(test)]
    pub(crate) fn take_prop_cues(&mut self) -> Vec<crate::room_primitives::PropCueEvent> {
        self.interactive_props.take_emitted_cues()
    }

    #[cfg(test)]
    pub(crate) fn pvp_objective(&self) -> Option<&crate::pvp_mode::PvpObjectiveRuntime> {
        self.pvp_objective.as_ref()
    }

    pub(crate) fn set_pvp_objective(&mut self, runtime: crate::pvp_mode::PvpObjectiveRuntime) {
        self.pvp_objective = Some(runtime);
    }

    pub(crate) fn set_player_names(&mut self, names: HashMap<u32, String>) {
        self.player_names = names;
    }

    fn step_interactive_props_and_pvp(&mut self, tick: u32, now: Duration) {
        const MELEE_BUTTON_BIT: u32 = 0x0800;
        let now_secs = now.as_secs_f64();
        let mut interact_positions = Vec::new();
        let mut melee_centers = Vec::new();
        #[cfg(feature = "lua-mods")]
        let mut button_edges = Vec::new();
        let has_props = !self.interactive_props.props.is_empty()
            || !self.interactive_props.buttons.is_empty()
            || !self.room_pets.is_empty()
            || !self.room_deployables.is_empty();
        #[cfg(feature = "lua-mods")]
        let has_mods = self.mod_host.is_some();
        #[cfg(not(feature = "lua-mods"))]
        let has_mods = false;
        let mut teleports: Vec<(BodyId, u32, Vec3)> = Vec::new();
        if has_props || has_mods {
            let tp_candidates: Vec<(BodyId, u8, u32, Vec3)> = self
                .players_by_wire
                .values()
                .filter_map(|&entity| {
                    let state = self.world.get::<PlayerBodyState>(entity)?;
                    (state.alive()
                        && state.pending_pressed_buttons
                            & crate::room_primitives::INTERACT_BUTTON_BIT
                            != 0)
                        .then(|| {
                            (
                                state.id,
                                state.team,
                                state.frame,
                                state.mover.pose().position,
                            )
                        })
                })
                .collect();
            for (id, team, frame, pos) in tp_candidates {
                if let Some(exit) = self.teleporter_exit_for(pos, team) {
                    teleports.push((id, frame, exit));
                }
            }
            for (id, frame, exit) in teleports {
                if let Some(reloc) = crate::script_init::Relocation::new(exit) {
                    if let Some(state) = self.player_body_mut(id) {
                        state.mover.queue_scripted_motion(
                            frame.saturating_add(1),
                            crate::script_init::ScriptedMotion::Teleport(reloc),
                        );
                    }
                }
            }
            for (&wire, &entity) in &self.players_by_wire {
                if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
                    let pressed = std::mem::take(&mut state.pending_pressed_buttons);
                    let released = std::mem::take(&mut state.pending_released_buttons);
                    if (pressed == 0 && released == 0) || !state.alive() {
                        continue;
                    }
                    #[cfg(feature = "lua-mods")]
                    button_edges.push((wire, pressed, released));
                    #[cfg(not(feature = "lua-mods"))]
                    let _ = wire;
                    if has_props && pressed != 0 {
                        let pose = state.mover.pose();
                        if pressed & crate::room_primitives::INTERACT_BUTTON_BIT != 0 {
                            interact_positions.push(pose.position);
                        }
                        if pressed & MELEE_BUTTON_BIT != 0 {
                            let [fx, fy, fz] =
                                crate::game_hitscan::aim_direction(&crate::commands::Command {
                                    yaw: pose.yaw,
                                    pitch: pose.pitch,
                                    ..Default::default()
                                });
                            let eye = [
                                pose.position[0],
                                pose.position[1]
                                    + state.mover.eye_height()
                                    + f64::from(state.mover.spring_offset()),
                                pose.position[2],
                            ];
                            melee_centers.push([
                                eye[0] + fx * 1.5,
                                eye[1] + fy * 1.5,
                                eye[2] + fz * 1.5,
                            ]);
                        }
                    }
                }
            }
            if has_props {
                for center in melee_centers {
                    self.interactive_props
                        .hit_props_in_sphere(center, 2.0, 11.0);
                }
                if !self.interactive_props.props.is_empty() {
                    self.interactive_props.step(
                        crate::game_clock::TICK_PERIOD.as_secs_f64(),
                        self.interactive_prop_floor_y,
                        &[],
                    );
                }
                for pos in interact_positions {
                    self.interactive_props.press_nearby_button(pos, now_secs);
                }
            }
            #[cfg(feature = "lua-mods")]
            if has_mods {
                self.step_mod_host(&button_edges, tick, now);
            }
            #[cfg(not(feature = "lua-mods"))]
            let _ = tick;
        }
        if let Some(pvp) = &mut self.pvp_objective {
            let mut actors = Vec::with_capacity(self.players_by_wire.len() + self.by_wire.len());
            let mut wire_to_owner: HashMap<u32, u32> = HashMap::new();
            for (&wire, &entity) in &self.players_by_wire {
                if let Some(state) = self.world.get::<PlayerBodyState>(entity) {
                    wire_to_owner.insert(wire, state.owner);
                    actors.push(crate::room_primitives::ActorSample {
                        entity: wire,
                        team: state.team,
                        position: state.mover.pose().position,
                        alive: state.alive(),
                    });
                }
            }
            for (&wire, &entity) in &self.by_wire {
                if let Some(state) = self.world.get::<BotState>(entity) {
                    actors.push(crate::room_primitives::ActorSample {
                        entity: wire,
                        team: state.bot.spawn.team,
                        position: state.bot.position,
                        alive: !state.bot.dead && state.vitals.death().is_none(),
                    });
                }
            }
            // High-HP autonomous companions (e.g. Ashe's B.O.B. >= 800 HP) contest and
            // capture PvP objectives just like retail Overwatch.
            for (&wire, pet) in &self.room_pets {
                if pet.attach.is_none()
                    && pet
                        .vitals
                        .as_ref()
                        .is_some_and(|v| v.death().is_none() && v.maximum() >= 800.0)
                {
                    actors.push(crate::room_primitives::ActorSample {
                        entity: wire,
                        team: pet.team,
                        position: pet.position,
                        alive: true,
                    });
                }
            }
            pvp.advance(now_secs, &actors);
            let dt = crate::game_clock::TICK_PERIOD.as_secs_f64();
            for wire in pvp.objective_occupants(&actors) {
                if let Some(&owner) = wire_to_owner.get(&wire) {
                    self.scoreboard.record_objective_time(owner, dt, now_secs);
                }
            }
            if !self.pvp_completion_emitted {
                if let Some(winner) = pvp.winner() {
                    self.pvp_completion_emitted = true;
                    self.pending_match_winner = Some(winner);
                }
            }
            // Replicate live payload movement pose whenever the cart moves >= 1cm.
            if let Some((pos, update)) = pvp.payload_entity_update() {
                let moved = self.last_pvp_payload_pos.is_none_or(|prev| {
                    (pos[0] - prev[0])
                        .hypot(pos[1] - prev[1])
                        .hypot(pos[2] - prev[2])
                        >= 0.01
                });
                if moved {
                    self.last_pvp_payload_pos = Some(pos);
                    self.scripted_health.push(update);
                }
            }
            // Replicate live GAME_MODE_ENTITY HUD countdown & phase whenever state changes.
            let hud_key = pvp.hud_state_key(now_secs);
            if self.last_pvp_hud_key != Some(hud_key) {
                self.last_pvp_hud_key = Some(hud_key);
                if let Some((graphs, _)) = &self.script_graphs {
                    let vars = pvp.hud_variables(now_secs);
                    if let Ok(payload) = crate::match_scene::mode_ui(graphs, &vars) {
                        if let Ok(chunk) = crate::script_wire::chunk(&payload, 0, 1) {
                            self.scripted_health.push(EntityUpdate {
                                entity: crate::world::GAME_MODE_ENTITY,
                                chunk: Some(chunk),
                                ..EntityUpdate::default()
                            });
                        }
                    }
                }
            }
        }
    }

    pub(crate) fn take_match_winner(&mut self) -> Option<u8> {
        self.pending_match_winner.take()
    }

    pub(crate) fn record_completed_career_matches(
        &mut self,
        winner: u8,
        mode_name: &str,
        now_secs: f64,
    ) {
        let Some((catalog, mode)) = self.stats_catalog.as_ref() else {
            return;
        };
        let participants: Vec<(u32, u8)> = self
            .players_by_wire
            .values()
            .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
            .map(|state| (state.owner, state.team))
            .collect();
        for (owner, team) in participants {
            let won = team == winner;
            if let Some(rec) = self
                .scoreboard
                .build_career_record(catalog, *mode, mode_name, owner, won, false, now_secs)
            {
                self.completed_career_records.push((owner, rec));
            }
        }
    }

    pub(crate) fn take_completed_career_records(
        &mut self,
    ) -> Vec<(u32, crate::game_progression::CareerMatchRecord)> {
        std::mem::take(&mut self.completed_career_records)
    }

    pub(crate) fn set_ability_rules(
        &mut self,
        rules: Arc<crate::hero_ability_rules::HeroAbilityRules>,
    ) {
        self.ability_rules = rules;
    }

    #[cfg(test)]
    pub(crate) fn set_spawn_anchors(&mut self, anchors: Arc<crate::scene_assets::SpawnAnchors>) {
        self.spawn_anchors = Some(anchors);
    }

    pub(crate) fn spawn_anchors(&self) -> Option<&Arc<crate::scene_assets::SpawnAnchors>> {
        self.spawn_anchors.as_ref()
    }

    pub(crate) fn set_free_for_all(&mut self, free_for_all: bool) {
        self.free_for_all = free_for_all;
    }

    pub(crate) fn enable_received_damage_feedback(&mut self) {
        self.received_damage_feedback = true;
    }

    pub(crate) fn set_combat_voice(
        &mut self,
        catalog: Arc<crate::combat_voice::CombatVoiceCatalog>,
    ) {
        self.combat_voice = catalog;
    }

    pub(crate) fn set_body_contact_sets(&mut self, sets: BTreeMap<u64, u16>) {
        self.body_contact_sets = sets;
    }

    pub(crate) fn set_stats_catalog(
        &mut self,
        catalog: Arc<crate::game_stats::StatsCatalog>,
        mode_guid: u64,
    ) {
        self.stats_catalog = Some((catalog, mode_guid));
    }

    pub(crate) fn scoreboard_mut(&mut self) -> &mut crate::game_progression::MatchScoreboard {
        &mut self.scoreboard
    }

    pub(crate) fn replay(&self) -> &crate::game_replay::HighlightRecorder {
        &self.replay
    }

    pub(crate) fn voice_chat_mut(&mut self) -> &mut crate::game_replay::VoiceChatRouter {
        &mut self.voice_chat
    }

    pub(super) fn advance_progression_and_replay(&mut self, tick: u32) {
        let dt = crate::game_clock::TICK_PERIOD.as_secs_f64();
        let now_secs = f64::from(tick) * dt;
        self.scoreboard.tick_on_fire(dt, now_secs);
        let owner_fire_states: Vec<(u32, bool)> = self
            .players_by_owner
            .keys()
            .map(|&owner| {
                (
                    owner,
                    self.scoreboard
                        .player(owner)
                        .is_some_and(|b| b.on_fire.on_fire),
                )
            })
            .collect();
        for (owner, on_fire) in owner_fire_states {
            if let Some(&handle) = self.players_by_owner.get(&owner) {
                if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(handle.entity) {
                    if let Some(runtime) = state.runtime.as_mut() {
                        runtime.set_on_fire(on_fire);
                    }
                }
            }
        }
        if let Some((catalog, mode)) = &self.stats_catalog {
            let updates = self
                .scoreboard
                .collect_board_updates(catalog, *mode, tick, 2);
            for (_, update) in updates {
                self.scripted_health.push(update);
            }
        }
        // Record compact 30Hz actor snapshots for Killcam & POTG playback.
        if tick % 2 == 0 && !self.players_by_wire.is_empty() {
            let mut poses = Vec::with_capacity(self.players_by_wire.len() + self.by_wire.len());
            for (&wire, &entity) in &self.players_by_wire {
                if let Some(p) = self.world.get::<PlayerBodyState>(entity) {
                    let mp = p.mover.pose();
                    poses.push(crate::game_replay::ReplayActorPose {
                        entity: wire,
                        team: p.team,
                        hero: p.hero,
                        position: mp.position,
                        yaw: mp.yaw,
                        pitch: mp.pitch,
                        health: p.vitals().map_or(0.0, |v| v.total() as f32),
                        alive: p.alive(),
                    });
                }
            }
            for (&wire, &entity) in &self.by_wire {
                if let Some(s) = self.world.get::<BotState>(entity) {
                    let mp = s.bot.movement(tick);
                    poses.push(crate::game_replay::ReplayActorPose {
                        entity: wire,
                        team: s.bot.spawn.team,
                        hero: s.bot.spawn.hero,
                        position: mp.position,
                        yaw: mp.yaw,
                        pitch: mp.pitch,
                        health: s.vitals.total() as f32,
                        alive: !s.bot.dead,
                    });
                }
            }
            self.replay.record_frame(tick, poses);
        }
    }

    pub(super) fn schedule_bot_voice_reply(
        &mut self,
        bot_wire: u32,
        stimulus: u64,
        delay_ticks: u32,
        now_tick: u32,
    ) {
        if self
            .bot_reply_cooldowns
            .get(&bot_wire)
            .is_some_and(|&until| now_tick < until)
        {
            return;
        }
        let Some(hero) = self
            .by_wire
            .get(&bot_wire)
            .and_then(|&entity| self.world.get::<BotState>(entity))
            .filter(|s| !s.bot.dead)
            .map(|s| s.bot.spawn.hero)
        else {
            return;
        };
        let Some(catalog) = self
            .spatial_queries
            .as_ref()
            .and_then(|q| q.actor_voice.as_deref())
        else {
            return;
        };
        let pick = (now_tick as usize).wrapping_add(bot_wire as usize);
        let Some((resolved_stimulus, line)) = catalog
            .stimulus_options(hero, 0, stimulus)
            .and_then(|opts| opts.choose(&mut |count| pick % count))
        else {
            return;
        };
        self.bot_reply_cooldowns
            .insert(bot_wire, now_tick.saturating_add(62 * 6));
        self.scheduled_voices.push((
            now_tick.saturating_add(delay_ticks),
            bot_wire,
            resolved_stimulus,
            line,
        ));
    }

    pub(super) fn trigger_nearby_bot_hello_reply(&mut self, speaker_wire: u32, tick: u32) {
        let Some(speaker_pos) = self
            .players_by_wire
            .get(&speaker_wire)
            .and_then(|&entity| self.world.get::<PlayerBodyState>(entity))
            .map(|p| p.mover.pose().position)
        else {
            return;
        };
        let mut nearest: Option<(f64, u32)> = None;
        for (&wire, &entity) in &self.by_wire {
            let Some(state) = self.world.get::<BotState>(entity) else {
                continue;
            };
            if state.bot.dead {
                continue;
            }
            let d = (state.bot.position[0] - speaker_pos[0])
                .hypot(state.bot.position[1] - speaker_pos[1])
                .hypot(state.bot.position[2] - speaker_pos[2]);
            if d <= 12.0 && nearest.is_none_or(|(prev, _)| d < prev) {
                nearest = Some((d, wire));
            }
        }
        if let Some((_, bot_wire)) = nearest {
            self.schedule_bot_voice_reply(bot_wire, crate::actor_voice::STIMULUS_HELLO, 28, tick);
        }
    }

    pub(super) fn advance_scheduled_voices(&mut self, tick: u32) {
        if !self.banter_scheduled && self.players_by_wire.len() >= 2 && tick > 62 * 3 {
            if let Some(catalog) = self
                .spatial_queries
                .as_ref()
                .and_then(|q| q.actor_voice.as_deref())
            {
                let active: Vec<(u32, u64)> = self
                    .players_by_wire
                    .values()
                    .filter_map(|&e| self.world.get::<PlayerBodyState>(e))
                    .filter(|p| p.alive())
                    .map(|p| (p.id.entity, p.hero))
                    .collect();
                let heroes: Vec<u64> = active.iter().map(|&(_, h)| h).collect();
                if let Some(conv) = catalog.matching_conversation(self.map_guid, &heroes) {
                    let mut cursor = tick.saturating_add(62);
                    for turn in &conv.turns {
                        if let Some(&(wire, _)) = active.iter().find(|&&(_, h)| h == turn.hero) {
                            self.scheduled_voices
                                .push((cursor, wire, turn.stimulus, turn.line));
                            let dur =
                                catalog.line_duration(turn.line) + turn.delay_after_secs.max(0.5);
                            cursor = cursor.saturating_add((dur * 62.5).ceil() as u32);
                        }
                    }
                    self.banter_scheduled = true;
                }
            }
        }
        if self.scheduled_voices.is_empty() {
            return;
        }
        let mut due = Vec::new();
        self.scheduled_voices.retain(|entry| {
            if entry.0 <= tick {
                due.push(*entry);
                false
            } else {
                true
            }
        });
        for (_, speaker_wire, stimulus, line) in due {
            let Some(source) = self.sample_health_body(speaker_wire) else {
                continue;
            };
            let source_id = source.identity();
            let listeners: Vec<(u32, BodyId)> = self
                .players_by_wire
                .values()
                .filter_map(|&entity| self.world.get::<PlayerBodyState>(entity))
                .filter(|p| p.alive())
                .map(|p| (p.owner, p.id))
                .collect();
            for (owner, listener) in listeners {
                self.player_cues.push(PlayerRoomCue {
                    owner,
                    listener,
                    source: source_id,
                    notice: CombatNotice::Voice {
                        anchor: speaker_wire,
                        speaker: speaker_wire,
                        listener: listener.entity,
                        stimulus,
                        line,
                    },
                });
            }
        }
    }

    pub(crate) fn enable_spatial_queries(
        &mut self,
        free_for_all: bool,
        ground: Arc<CollisionScene>,
        needs: RoomSpatialNeeds,
    ) {
        self.free_for_all = free_for_all;
        self.spatial_queries = Some(RoomSpatialQueries {
            free_for_all,
            ground,
            breakables: None,
            needs,
            actor_movers: None,
            actor_voice: None,
        });
    }

    pub(crate) fn set_breakables(
        &mut self,
        breakables: Arc<crate::collision_scene::BreakableState>,
    ) {
        if let Some(queries) = &mut self.spatial_queries {
            queries.breakables = Some(breakables);
        }
    }

    pub(crate) fn set_map_guid(&mut self, map: u64) {
        self.map_guid = Some(map);
    }

    pub(crate) fn enable_actor_voice(&mut self, catalog: Arc<ActorVoiceCatalog>) {
        if let Some(queries) = &mut self.spatial_queries {
            queries.actor_voice = Some(catalog);
        }
    }

    pub(crate) fn enable_spatial_actor_rays(
        &mut self,
        movers: Arc<crate::mover_settings::MoverCatalog>,
    ) {
        if let Some(queries) = &mut self.spatial_queries {
            queries.actor_movers = Some(movers);
        }
    }

    /// Capture room state without changing the room's published frame.
    pub(crate) fn capture_spatial_frame(
        &self,
        frame: u32,
    ) -> Result<Option<Arc<RoomSpatialFrame>>, CombatError> {
        let Some(queries) = &self.spatial_queries else {
            return Ok(None);
        };
        let capacity = self.players_by_wire.len() + self.by_wire.len();
        let mut candidates = Vec::with_capacity(capacity);
        let actor_queries = queries
            .actor_movers
            .as_ref()
            .map(|movers| BotQueries::new(queries.free_for_all, movers, &queries.ground, None));
        let mut actor_capsules = actor_queries.as_ref().map(|_| BTreeMap::new());
        let mut entity_facts = (queries.needs >= RoomSpatialNeeds::EntityFacts).then(BTreeMap::new);
        let include_tags = queries.needs == RoomSpatialNeeds::EntityTags;
        let mut script_targets = HashMap::with_capacity(capacity);
        let mut dead_voice = HashMap::new();
        let mut player_wires = HashMap::with_capacity(self.players_by_wire.len());
        let actor_voice = queries.actor_voice.as_ref().map(Arc::clone);
        for (state, &entity) in self.states().zip(self.by_wire.values()) {
            let state = state?;
            let bot = &state.bot;
            let alive = !bot.dead && state.vitals.death().is_none();
            let team = (!queries.free_for_all).then_some(bot.spawn.team);
            if !alive && actor_voice.is_some() && state.stock_script.is_some() {
                dead_voice.insert(
                    bot.entity,
                    script_requests::DeathVoiceBody {
                        entity,
                        team,
                        voice: (bot.spawn.hero, 0),
                        listener: false,
                    },
                );
            }
            if let Some(facts) = &mut entity_facts {
                let tags = if include_tags {
                    state.target_tags()
                } else {
                    Default::default()
                };
                facts.insert(bot.entity, RoomEntityFacts { team, alive, tags });
            }
            if alive {
                let pose = bot.movement(frame);
                if let Some((capsules, actor_queries)) =
                    actor_capsules.as_mut().zip(actor_queries.as_ref())
                {
                    // Use the same collision proxy dimensions as other room
                    // queries, including their catalog fallback policy.
                    let mut capsule = actor_queries.target(bot);
                    capsule.feet = pose.position;
                    capsules.insert(bot.entity, capsule);
                }
                script_targets.insert(
                    bot.entity,
                    RoomScriptTarget {
                        entity,
                        candidate_index: Some(candidates.len()),
                        position: pose.position,
                        angles: [pose.yaw, pose.pitch],
                        velocity: bot.velocity,
                        controller: bot.controller(),
                        model: Some((
                            bot.spawn.body,
                            state
                                .enemy
                                .as_ref()
                                .and_then(|enemy| enemy.binding.skin)
                                .unwrap_or(0),
                        )),
                        eye_position: None,
                        filter_flags: state.native_filter_flags(),
                        script_state: state.script_state(),
                        health: true,
                        health_snapshot: Some(ActorHealthSnapshot::from_vitals(&state.vitals)),
                        messages: state.enemy.as_ref().is_some_and(|enemy| {
                            enemy.fault.is_none() && !enemy.scripts.retirement_requested
                        }) || state
                            .stock_script
                            .as_ref()
                            .is_some_and(|script| script.accepts_room_messages()),
                        cue_listener: false,
                        voice: actor_voice
                            .as_ref()
                            .and_then(|_| state.stock_script.as_ref().map(|_| (bot.spawn.hero, 0))),
                    },
                );
                candidates.push(SpatialCandidate {
                    entity: bot.entity,
                    position: pose.position,
                    team,
                });
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
            player_wires.insert(state.owner, wire);
            let alive = state.alive();
            let team = (!queries.free_for_all).then_some(state.team);
            if !alive && actor_voice.is_some() {
                dead_voice.insert(
                    wire,
                    script_requests::DeathVoiceBody {
                        entity,
                        team,
                        voice: (state.hero, state.selected_model.map_or(0, |(_, skin)| skin)),
                        listener: true,
                    },
                );
            }
            if let Some(facts) = &mut entity_facts {
                let tags = if include_tags {
                    state
                        .runtime
                        .as_ref()
                        .map_or_else(Default::default, |runtime| runtime.target_tags())
                } else {
                    Default::default()
                };
                facts.insert(wire, RoomEntityFacts { team, alive, tags });
            }
            if alive {
                let pose = state.mover.pose();
                if let Some(capsules) = &mut actor_capsules {
                    capsules.insert(wire, state.target_pose());
                }
                script_targets.insert(
                    wire,
                    RoomScriptTarget {
                        entity,
                        candidate_index: Some(candidates.len()),
                        position: pose.position,
                        angles: [pose.yaw, pose.pitch],
                        velocity: pose.velocity,
                        controller: Some(state.owner),
                        model: state.selected_model,
                        eye_position: state.script_eye_position(),
                        filter_flags: state.native_filter_flags(),
                        script_state: state
                            .runtime
                            .as_ref()
                            .and_then(|runtime| runtime.snapshot())
                            .map_or(
                                crate::script_entity_state::EntityScriptState::Unavailable,
                                |snapshot| {
                                    crate::script_entity_state::EntityScriptState::Running(
                                        Arc::new(snapshot.variables.clone()),
                                    )
                                },
                            ),
                        health: state.vitals().is_some(),
                        health_snapshot: state.vitals().map(ActorHealthSnapshot::from_vitals),
                        messages: state.runtime.is_some(),
                        cue_listener: true,
                        voice: actor_voice.as_ref().map(|_| {
                            (state.hero, state.selected_model.map_or(0, |(_, skin)| skin))
                        }),
                    },
                );
                candidates.push(SpatialCandidate {
                    entity: wire,
                    position: pose.position,
                    team,
                });
            }
        }
        for (&wire, pet) in &self.room_pets {
            if pet.vitals.as_ref().is_none_or(|v| v.death().is_none()) {
                script_targets.insert(
                    wire,
                    RoomScriptTarget {
                        entity: pet.ecs_entity,
                        controller: None,
                        model: Some((pet.definition, 0)),
                        candidate_index: None,
                        position: pet.position,
                        angles: [pet.yaw, 0],
                        velocity: [0.0; 3],
                        eye_position: None,
                        filter_flags: None,
                        script_state: crate::script_entity_state::EntityScriptState::Unavailable,
                        health: pet.vitals.is_some(),
                        health_snapshot: pet.vitals.as_ref().map(ActorHealthSnapshot::from_vitals),
                        messages: pet.component.is_some(),
                        cue_listener: false,
                        voice: None,
                    },
                );
            }
        }
        for (&wire, dep) in &self.room_deployables {
            if dep.vitals.as_ref().is_none_or(|v| v.death().is_none()) {
                script_targets.insert(
                    wire,
                    RoomScriptTarget {
                        entity: dep.ecs_entity,
                        controller: None,
                        model: Some((dep.definition, 0)),
                        candidate_index: None,
                        position: dep.position,
                        angles: [dep.yaw, 0],
                        velocity: [0.0; 3],
                        eye_position: None,
                        filter_flags: None,
                        script_state: crate::script_entity_state::EntityScriptState::Unavailable,
                        health: dep.vitals.is_some(),
                        health_snapshot: dep.vitals.as_ref().map(ActorHealthSnapshot::from_vitals),
                        messages: dep.component.is_some(),
                        cue_listener: false,
                        voice: None,
                    },
                );
            }
        }

        Ok(Some(Arc::new(RoomSpatialFrame {
            candidates,
            actor_capsules,
            entity_facts,
            script_targets,
            dead_voice,
            player_wires,
            ground: Arc::clone(&queries.ground),
            breakables: queries.breakables.as_ref().map(Arc::clone),
            actor_voice,
            map_guid: self.map_guid,
            retired: Arc::clone(&self.retired_entity_ids),
            #[cfg(any(test, feature = "inspector"))]
            captured_tick: frame,
            #[cfg(any(test, feature = "inspector"))]
            debug_rays: Arc::clone(&self.debug_rays),
        })))
    }

    #[cfg(test)]
    pub(crate) fn spatial_frame(&self) -> Option<Arc<RoomSpatialFrame>> {
        self.spatial_frame
            .as_ref()
            .map(|(_, frame)| Arc::clone(frame))
    }

    /// Read the frozen room frame without the owner's captured body generation.
    pub(crate) fn owner_startup_spatial_frame(&self, owner: u32) -> Option<Arc<RoomSpatialFrame>> {
        let mut frame = Arc::clone(&self.spatial_frame.as_ref()?.1);
        let Some(&wire) = frame.player_wires.get(&owner) else {
            return Some(frame);
        };
        let view = Arc::make_mut(&mut frame);
        view.player_wires.remove(&owner);
        if let Some(capsules) = &mut view.actor_capsules {
            capsules.remove(&wire);
        }
        if let Some(facts) = &mut view.entity_facts {
            facts.remove(&wire);
        }
        if let Some(removed) = view.script_targets.remove(&wire) {
            if let Some(index) = removed.candidate_index {
                view.candidates.remove(index);
                for target in view.script_targets.values_mut() {
                    if let Some(idx) = &mut target.candidate_index {
                        if *idx > index {
                            *idx -= 1;
                        }
                    }
                }
            }
        }
        Some(frame)
    }

    /// Freeze room queries at the first use of this room frame.
    pub(crate) fn prepare_spatial_frame(&mut self, frame: u32) -> Result<(), CombatError> {
        if self
            .spatial_frame
            .as_ref()
            .is_some_and(|(previous, _)| *previous == frame)
        {
            return Ok(());
        }
        let Some(spatial) = self.capture_spatial_frame(frame)? else {
            return Ok(());
        };
        for &entity in self.players_by_wire.values() {
            if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
                if let Some(runtime) = state.runtime.as_mut() {
                    runtime.set_spatial_frame(Arc::clone(&spatial));
                }
            }
        }
        for &entity in self.by_wire.values() {
            if let Some(mut state) = self.world.get_mut::<BotState>(entity) {
                if let Some(enemy) = state.enemy.as_mut() {
                    enemy.binding.spatial_frame = Some(Arc::clone(&spatial));
                }
                if let Some(script) = state.stock_script.as_mut() {
                    script.set_spatial_frame(Arc::clone(&spatial));
                }
            }
        }
        self.spatial_frame = Some((frame, spatial));
        Ok(())
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

    #[cfg(test)]
    pub(crate) fn observed_bot_death(
        &self,
        wire: u32,
    ) -> Result<Option<(crate::vitals::Death, Duration)>, CombatError> {
        Ok(self
            .states()
            .collect::<Result<Vec<_>, _>>()?
            .iter()
            .find(|state| state.bot.entity == wire)
            .and_then(|state| state.vitals.death().zip(state.respawn_at)))
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
    fn record_debug_ray(&self, ray: crate::game_debug::DebugRay) {
        self.debug_rays.push(ray);
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_physics(
        &self,
    ) -> Result<Option<crate::game_debug::PhysicsOverlay>, CombatError> {
        use crate::game_debug::{
            MAX_DEBUG_CONTACT_NORMALS, MAX_DEBUG_GROUND_PROBES, PhysicsOverlay, PhysicsTruncation,
        };

        let (rays, rays_truncated) = self.debug_rays.snapshot();
        let mut contact_normals = Vec::new();
        let mut ground_probes = Vec::new();
        let mut truncated = PhysicsTruncation {
            rays: rays_truncated,
            contact_normals: false,
            ground_probes: false,
        };

        self.push_player_debug_physics(
            &mut contact_normals,
            &mut truncated.contact_normals,
            &mut ground_probes,
            &mut truncated.ground_probes,
        );

        for state in self.states() {
            let state = state?;
            for contact in state.bot.debug_contact_normals() {
                if contact_normals.len() == MAX_DEBUG_CONTACT_NORMALS {
                    truncated.contact_normals = true;
                    break;
                }
                contact_normals.push(contact);
            }
            if let Some(mover) = state
                .enemy
                .as_ref()
                .and_then(|enemy| enemy.reaper_exit.as_ref())
                .and_then(|exit| exit.debug_mover())
            {
                if let Some(probe) = mover.debug_ground_probe(state.bot.entity) {
                    if ground_probes.len() == MAX_DEBUG_GROUND_PROBES {
                        truncated.ground_probes = true;
                    } else {
                        ground_probes.push(probe);
                    }
                }
                for contact in mover.debug_contact_normals(state.bot.entity) {
                    if contact_normals.len() == MAX_DEBUG_CONTACT_NORMALS {
                        truncated.contact_normals = true;
                        break;
                    }
                    contact_normals.push(contact);
                }
            }
        }

        if rays.is_empty()
            && contact_normals.is_empty()
            && ground_probes.is_empty()
            && truncated == PhysicsTruncation::default()
        {
            return Ok(None);
        }

        Ok(Some(PhysicsOverlay {
            rays,
            contact_normals,
            ground_probes,
            truncated,
        }))
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_navigation(
        &self,
    ) -> Result<Option<crate::game_debug::NavigationOverlay>, CombatError> {
        use crate::game_debug::{MAX_DEBUG_ROUTES, NavigationOverlay, NavigationTruncation};

        let mut routes = Vec::new();
        let mut truncated = NavigationTruncation::default();
        let push_route =
            |routes: &mut Vec<_>,
             truncated: &mut bool,
             route: Option<crate::game_debug::DebugBotRoute>| {
                if let Some(route) = route {
                    if routes.len() == MAX_DEBUG_ROUTES {
                        *truncated = true;
                    } else {
                        routes.push(route);
                    }
                }
            };

        for state in self.states() {
            let state = state?;
            push_route(&mut routes, &mut truncated.routes, state.bot.debug_route());
            if let Some(enemy) = state.enemy.as_ref() {
                push_route(
                    &mut routes,
                    &mut truncated.routes,
                    enemy
                        .gate_approach
                        .as_ref()
                        .and_then(|approach| approach.debug_route(state.bot.entity)),
                );
                push_route(
                    &mut routes,
                    &mut truncated.routes,
                    enemy
                        .boss_pursuit
                        .as_ref()
                        .and_then(|pursuit| pursuit.debug_route(state.bot.entity)),
                );
            }
        }

        let mut spawn_candidates = Vec::new();
        self.push_encounter_debug_spawn_candidates(
            &mut spawn_candidates,
            &mut truncated.spawn_candidates,
        );

        if routes.is_empty()
            && spawn_candidates.is_empty()
            && truncated == NavigationTruncation::default()
        {
            return Ok(None);
        }

        Ok(Some(NavigationOverlay {
            routes,
            spawn_candidates,
            truncated,
        }))
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_scripts(
        &self,
    ) -> Result<(Vec<crate::game_debug::EntityScriptSnapshot>, bool), CombatError> {
        use crate::game_debug::{MAX_DEBUG_VM_ENTITIES, summarize_script_snapshot};

        let mut scripts = Vec::new();
        self.push_player_debug_scripts(&mut scripts);
        for state in self.states() {
            let state = state?;
            if let Some(enemy) = state.enemy.as_ref() {
                if let Some(snapshot) = enemy.scripts.body.snapshot() {
                    scripts.push(summarize_script_snapshot(
                        enemy.binding.body,
                        "enemy_body",
                        snapshot,
                    ));
                }
                if let Some(snapshot) = enemy.scripts.controller.snapshot() {
                    scripts.push(summarize_script_snapshot(
                        enemy.binding.controller,
                        "enemy_controller",
                        snapshot,
                    ));
                }
            }
        }
        self.push_encounter_debug_scripts(&mut scripts);
        scripts.sort_unstable_by(|a, b| a.entity.cmp(&b.entity).then_with(|| a.kind.cmp(b.kind)));
        let truncated = scripts.len() > MAX_DEBUG_VM_ENTITIES;
        scripts.truncate(MAX_DEBUG_VM_ENTITIES);
        Ok((scripts, truncated))
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_actors(&self) -> Result<impl ExactSizeIterator<Item = Actor>, CombatError> {
        let states = self.states().collect::<Result<Vec<_>, _>>()?;
        let players = self.player_debug_actors();
        Ok(states
            .into_iter()
            .map(|state| {
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
            })
            .chain(players)
            .collect::<Vec<_>>()
            .into_iter())
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
    ) -> Vec<crate::pve_encounter::messages::DefenderSighting> {
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
            let state = self
                .world
                .get_mut::<BotState>(handle)
                .expect("live bot handle")
                .into_inner();
            let Some(enemy) = state.enemy.as_mut().filter(|enemy| enemy.fault.is_none()) else {
                continue;
            };
            let body = &mut enemy.scripts.body;
            if body.snapshot().is_some_and(|snapshot| {
                snapshot
                    .instances
                    .values()
                    .any(|instance| instance.graph == 0x0cbc)
            }) {
                if let Err(error) = body.queue_entity_message_from(
                    crate::world::GAME_MODE_ENTITY,
                    crate::pve_encounter::messages::ENCOUNTER_RESULT,
                    parameters.clone(),
                ) {
                    eprintln!(
                        "enemy {} result delivery failed: {error}",
                        enemy.binding.body
                    );
                    enemy.halt(&mut state.bot, error.to_string());
                }
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

    pub(crate) fn queue_mode_voice(&mut self, request: crate::pve_encounter::voice::VoiceRequest) {
        self.mode_voice_requests.push(request);
    }

    pub(crate) fn take_encounter_voice_requests(
        &mut self,
    ) -> Vec<crate::pve_encounter::voice::VoiceRequest> {
        let mut requests = self.take_gate_voice_requests();
        requests.extend(std::mem::take(&mut self.mode_voice_requests));
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
            if state.bot.spawn_hitbox_ready(tick) {
                let state = state.into_inner();
                state.history.record(tick, queries.target(&state.bot));
            }
        }
        self.record_player_poses(tick);
        Ok(())
    }

    pub(super) fn actor_combat_modifiers(&self, wire: u32) -> (f64, f64, f64, f64, bool) {
        if let Some(&entity) = self.players_by_wire.get(&wire) {
            if let Some(player) = self.world.get::<PlayerBodyState>(entity) {
                if let Some(runtime) = player.runtime.as_deref() {
                    return runtime.combat_modifiers();
                }
            }
        }
        (1.0, 1.0, 1.0, 1.0, false)
    }

    pub(super) fn check_player_deflect(
        &self,
        target_wire: u32,
        incoming_dir: Vec3,
    ) -> Option<(bool, Vec3, Vec3)> {
        let &entity = self.players_by_wire.get(&target_wire)?;
        let player = self.world.get::<PlayerBodyState>(entity)?;
        if !player.alive() {
            return None;
        }
        let deflect = player.runtime.as_deref()?.deflect_state()?;
        let pose = player.mover.pose();
        let aim = crate::game_hitscan::direction_for_angles(pose.yaw, pose.pitch);
        let in_len = crate::static_ray::norm(incoming_dir);
        if in_len > 1e-5 {
            // Incoming projectile travels toward defender, so `-incoming_dir` points back at shooter.
            let dot =
                (-incoming_dir[0] * aim[0] - incoming_dir[1] * aim[1] - incoming_dir[2] * aim[2])
                    / in_len;
            let min_dot = deflect.half_angle_deg.to_radians().cos();
            if dot < min_dot {
                return None;
            }
        }
        let eye = player.script_eye_position().unwrap_or(pose.position);
        Some((deflect.reflect, eye, aim))
    }

    pub(super) fn is_ana_direct_rifle(
        &self,
        source_wire: u32,
        damage: f64,
        geometry: Option<crate::game_hitscan::ImpactGeometry>,
    ) -> bool {
        const ANA_BODY: u64 = 0x0400_0000_0000_1424;
        if damage < 50.0
            || matches!(
                geometry,
                Some(crate::game_hitscan::ImpactGeometry::Blast { .. })
            )
        {
            return false;
        }
        self.players_by_wire
            .get(&source_wire)
            .and_then(|&e| self.world.get::<PlayerBodyState>(e))
            .is_some_and(|p| p.body_template == ANA_BODY)
    }

    pub(super) fn actor_team(&self, wire: u32) -> Option<u8> {
        if let Some(&entity) = self.by_wire.get(&wire) {
            return self.world.get::<BotState>(entity).map(|s| s.bot.spawn.team);
        }
        if let Some(&entity) = self.players_by_wire.get(&wire) {
            return self.world.get::<PlayerBodyState>(entity).map(|s| s.team);
        }
        None
    }

    #[cfg(test)]
    pub(crate) fn hitscan(
        &mut self,
        queries: &BotQueries<'_>,
        shot: PosedHitscan,
        context: ShotContext,
        visible: impl Fn(u32) -> bool,
    ) -> Result<Option<BotHit>, CombatError> {
        self.hitscan_with_policy(
            queries,
            shot,
            crate::game_hitscan::HitTargetPolicy::default(),
            context,
            visible,
        )
    }

    pub(crate) fn hitscan_with_policy(
        &mut self,
        queries: &BotQueries<'_>,
        shot: PosedHitscan,
        policy: crate::game_hitscan::HitTargetPolicy,
        context: ShotContext,
        visible: impl Fn(u32) -> bool,
    ) -> Result<Option<BotHit>, CombatError> {
        let view = ViewFrame::from_command(&shot.command, context.quanta);
        let ray = HitscanShot::with_direction(shot.origin, shot.direction, shot.reach, shot.hit);
        // Scene order breaks exact ray ties.
        #[cfg(feature = "lua-mods")]
        let ffa = queries.free_for_all() || self.mod_friendly_fire();
        #[cfg(not(feature = "lua-mods"))]
        let ffa = queries.free_for_all();
        let mut candidates = Vec::with_capacity(self.by_wire.len() + self.players_by_wire.len());
        for state in self.states() {
            let state = state?;
            let bot = &state.bot;
            if bot.dead
                || bot.entity == shot.source
                || !bot.spawn_hitbox_ready(context.tick)
                || !visible(bot.entity)
            {
                continue;
            }
            let same_team = !ffa && bot.spawn.team == context.team;
            let allowed = if same_team {
                policy.affects_allies
            } else {
                policy.affects_enemies
            };
            if !allowed {
                continue;
            }
            if let Some(pose) = state.history.at(view, queries.target(bot)) {
                candidates.push((bot.entity, queries.hurt_pose(bot.spawn.body, pose)));
            }
        }
        self.push_player_hitscan_candidates_with_policy(
            shot.source,
            context.team,
            policy,
            queries,
            view,
            &visible,
            &mut candidates,
        );
        if self
            .detonate_owner_deployable_ray(
                shot.source,
                context.team,
                ffa,
                ray.origin,
                ray.direction,
                ray.reach,
                context.tick,
                context.now,
            )
            .is_some()
        {
            return Ok(None);
        }
        let barrier_probe = if policy.affects_enemies
            && (!self.room_pets.is_empty() || !self.room_deployables.is_empty())
        {
            self.probe_barrier_ray(
                Some(context.team),
                ffa,
                ray.origin,
                ray.direction,
                ray.reach,
            )
        } else {
            None
        };
        let effective_reach = barrier_probe.map_or(ray.reach, |(_, _, d, _)| d);
        let ray =
            HitscanShot::with_direction(shot.origin, shot.direction, effective_reach, shot.hit);
        #[cfg(any(test, feature = "inspector"))]
        let mut blocking_wall = None;
        let mut last_wall = None;
        let mut breakable_hits = Vec::new();
        let Some(impact) = ray.resolve_combat(candidates, |origin, direction, reach| {
            let weapon_hit = queries.combat_weapon_ray(origin, direction, reach)?;
            breakable_hits = weapon_hit.breakables;
            last_wall = weapon_hit.wall;
            #[cfg(any(test, feature = "inspector"))]
            {
                blocking_wall = weapon_hit.wall.filter(|hit| hit.distance < reach);
            }
            Ok(weapon_hit.wall.map(|hit| hit.distance))
        })?
        else {
            let wall = if last_wall.is_some() || !breakable_hits.is_empty() {
                for (dist, placement) in breakable_hits {
                    let limit = last_wall.map_or(ray.reach, |w| w.distance);
                    if dist <= limit {
                        queries.mark_breakable(placement);
                    }
                }
                last_wall
            } else {
                queries
                    .combat_weapon_ray(ray.origin, ray.direction, ray.reach)
                    .ok()
                    .and_then(|hit| {
                        for (dist, placement) in hit.breakables {
                            let limit = hit.wall.map_or(ray.reach, |w| w.distance);
                            if dist <= limit {
                                queries.mark_breakable(placement);
                            }
                        }
                        hit.wall
                    })
            };
            let wall_dist = wall.map_or(ray.reach, |s| s.distance);
            if let Some((b_wire, b_is_pet, b_dist, _)) = barrier_probe {
                if b_dist <= wall_dist {
                    let dmg = shot.hit.damage_at(b_dist, false);
                    let (out_dmg, _, _, _, _) = self.actor_combat_modifiers(shot.source);
                    self.damage_barrier_or_deployable(
                        b_wire,
                        b_is_pet,
                        dmg * out_dmg,
                        shot.source,
                        context.tick,
                        context.now,
                    );
                }
            }
            #[cfg(any(test, feature = "inspector"))]
            {
                let wall = blocking_wall.or(wall);
                let (hit, end) = match wall {
                    Some(surface) => ("world", surface.point),
                    None => (
                        "none",
                        crate::static_ray::along(ray.origin, ray.direction, ray.reach),
                    ),
                };
                self.record_debug_ray(crate::game_debug::DebugRay {
                    tick: context.tick,
                    source: shot.source,
                    kind: "hitscan",
                    origin: ray.origin,
                    end,
                    hit,
                    target: None,
                    critical: false,
                    surface: wall.map(|surface| crate::game_debug::DebugRaySurface {
                        geometry: queries.combat_ray_geometry(),
                        mask: surface.mask,
                        surface_flags: surface.surface,
                        normal: surface.normal,
                        cache_source: queries.combat_ray_source(surface),
                    }),
                    script: None,
                });
            }
            let prop_reach = wall.map_or(ray.reach, |surface| surface.distance);
            self.interactive_props
                .hit_prop_by_ray(ray.origin, ray.direction, prop_reach, 12.0);
            return Ok(None);
        };
        self.interactive_props
            .hit_prop_by_ray(ray.origin, ray.direction, impact.distance, 12.0);
        for (dist, placement) in breakable_hits {
            if dist <= impact.distance {
                queries.mark_breakable(placement);
            }
        }
        #[cfg(any(test, feature = "inspector"))]
        self.record_debug_ray(crate::game_debug::DebugRay {
            tick: context.tick,
            source: shot.source,
            kind: "hitscan",
            origin: ray.origin,
            end: crate::static_ray::along(ray.origin, ray.direction, impact.distance),
            hit: "actor",
            target: Some(impact.entity),
            critical: impact.critical,
            surface: None,
            script: None,
        });
        let is_ally_heal = policy.healing
            || (policy.affects_allies
                && !ffa
                && self.actor_team(impact.entity) == Some(context.team));
        let is_ana_rifle = self.is_ana_direct_rifle(shot.source, impact.damage, impact.geometry);
        if is_ally_heal {
            let death = Death {
                time: context.now.as_secs_f64(),
                frame: context.tick,
                killer: shot.source,
            };
            let instant_heal = if is_ana_rifle {
                let rem = impact.damage * 0.75;
                let entry = self.actor_statuses.entry(impact.entity).or_default();
                entry.hot_ticks = 37;
                entry.hot_per_tick = rem / 37.0;
                entry.hot_source = shot.source;
                impact.damage * 0.25
            } else {
                impact.damage
            };
            if let Some(outcome) = self.apply_health(
                impact.entity,
                HealthEffect::Heal(instant_heal),
                death,
                context.now,
                None,
            )? {
                if outcome.delta > 0.0 {
                    self.deliver_applied_heal(shot.source, impact.entity, outcome.delta);
                }
            }
            return Ok(None);
        }
        if is_ana_rifle {
            let mut dot_impact = impact;
            dot_impact.damage = impact.damage * 0.25;
            let rem = impact.damage * 0.75;
            let entry = self.actor_statuses.entry(impact.entity).or_default();
            entry.dot_ticks = 37;
            entry.dot_per_tick = rem / 37.0;
            entry.dot_source = shot.source;
            if self.players_by_wire.contains_key(&impact.entity) {
                return self.apply_player_impact(
                    shot.source,
                    shot.shot_frame,
                    context,
                    &dot_impact,
                );
            }
            return self.apply_bot_impact(shot.source, shot.shot_frame, context, &dot_impact);
        }
        if self.players_by_wire.contains_key(&impact.entity) {
            if let Some((reflect, _, _)) = self.check_player_deflect(impact.entity, shot.direction)
            {
                if reflect && impact.damage > 0.0 && shot.source != impact.entity {
                    let death = Death {
                        time: context.now.as_secs_f64(),
                        frame: context.tick,
                        killer: impact.entity,
                    };
                    let _ = self.apply_health(
                        shot.source,
                        HealthEffect::Damage(impact.damage),
                        death,
                        context.now,
                        None,
                    );
                }
                return Ok(None);
            }
            return self.apply_player_impact(shot.source, shot.shot_frame, context, &impact);
        }
        self.apply_bot_impact(shot.source, shot.shot_frame, context, &impact)
    }

    #[cfg(test)]
    pub(crate) fn heal_bots(
        &mut self,
        queries: &BotQueries<'_>,
        team: u8,
        pulses: &[HealingPulse],
    ) -> Result<Vec<EntityUpdate>, CombatError> {
        let start = self.scripted_health.len();
        self.heal_allies(queries, team, None, None, pulses)?;
        Ok(self.scripted_health.split_off(start))
    }

    pub(crate) fn heal_allies(
        &mut self,
        queries: &BotQueries<'_>,
        team: u8,
        skip_player_wire: Option<u32>,
        field_owner: Option<BodyId>,
        pulses: &[HealingPulse],
    ) -> Result<(), CombatError> {
        if queries.free_for_all() || pulses.is_empty() {
            return Ok(());
        }
        self.activate_combat();
        let source = field_owner
            .filter(|&id| self.player_body(id).is_some())
            .and_then(|id| self.sample_health_body(id.entity));
        #[cfg(feature = "lua-mods")]
        let healing_scale = self.mod_healing_scale();
        let source_out_heal =
            field_owner.map_or(1.0, |id| self.actor_combat_modifiers(id.entity).2);
        let current_tick = self.spatial_frame.as_ref().map_or(0, |(tick, _)| *tick);
        let mut credited_heals = Vec::new();
        for (&wire, &entity) in &self.by_wire {
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
            let amount = HealingPulse::total_at(pulses, queries.target(bot))
                * source_out_heal
                * status_scale;
            #[cfg(feature = "lua-mods")]
            let amount = amount * healing_scale;
            let healed = state.vitals.heal(amount);
            if healed <= 0.0 {
                continue;
            }
            if let Some(script) = &mut state.stock_script {
                script.on_heal(state.vitals.total());
            }
            self.scripted_health.push(bot_health_update(state, false));
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
        self.heal_player_bodies(team, skip_player_wire, source, pulses);
        Ok(())
    }

    fn weapon_targets(
        &self,
        queries: &BotQueries<'_>,
        tick: u32,
    ) -> Result<Vec<WeaponTarget>, CombatError> {
        let mut targets = Vec::with_capacity(self.by_wire.len() + self.players_by_wire.len());
        for state in self.states() {
            let bot = &state?.bot;
            if bot.spawn_hitbox_ready(tick) {
                targets.push(WeaponTarget {
                    wire: bot.entity,
                    team: bot.spawn.team,
                    pose: queries.hurt_pose(bot.spawn.body, queries.target(bot)),
                });
            }
        }
        self.extend_player_weapon_targets(queries, &mut targets);
        Ok(targets)
    }

    fn npc_weapon_targets(
        &self,
        queries: &BotQueries<'_>,
        tick: u32,
    ) -> Result<Vec<WeaponTarget>, CombatError> {
        let targets = self.weapon_targets(queries, tick)?;
        #[cfg(test)]
        let targets = {
            let mut targets = targets;
            targets.extend(
                self.external_targets
                    .iter()
                    .flatten()
                    .filter(|target| target.alive)
                    .map(|target| WeaponTarget {
                        wire: target.entity,
                        team: target.team,
                        pose: target.pose.into(),
                    }),
            );
            targets
        };
        Ok(targets)
    }

    pub(crate) fn advance_projectiles(
        &mut self,
        queries: &BotQueries<'_>,
        connection: u32,
        owner: BodyId,
        launches: Vec<AdmittedFlight>,
        context: ShotContext,
        visible: impl Fn(u32) -> bool,
    ) -> Result<(Vec<FlightOutcome>, Vec<BotHit>), CombatError> {
        if !self.player_body_generation_current(owner) {
            return Ok((Vec::new(), Vec::new()));
        }
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
        self.spawn_admitted_launches(launches, &mut order);
        let mut outcomes = Vec::new();
        let mut hits = Vec::new();
        if order.is_empty() {
            return Ok((outcomes, hits));
        }

        self.activate_combat();
        let mut continuing = Vec::with_capacity(order.len());
        #[cfg(feature = "lua-mods")]
        let ffa = queries.free_for_all() || self.mod_friendly_fire();
        #[cfg(not(feature = "lua-mods"))]
        let ffa = queries.free_for_all();
        let all_weapon_targets = self.weapon_targets(queries, context.tick)?;
        let mut killed_in_tick: Vec<u32> = Vec::new();
        let mut candidates: Vec<(u32, crate::game_hitscan::HurtPose)> =
            Vec::with_capacity(all_weapon_targets.len());
        for entity in order {
            let (policy, piercing) = self
                .world
                .get::<AdmittedFlight>(entity)
                .map_or((Default::default(), false), |active| {
                    (active.flight.target_policy(), active.flight.is_piercing())
                });
            candidates.clear();
            for target in &all_weapon_targets {
                if !visible(target.wire)
                    || target.wire == owner.entity
                    || killed_in_tick.contains(&target.wire)
                {
                    continue;
                }
                let same_team = !ffa && target.team == context.team;
                let allowed = if same_team {
                    policy.affects_allies
                } else {
                    policy.affects_enemies
                };
                if allowed {
                    candidates.push((target.wire, target.pose.clone()));
                }
            }
            // The shooter can receive splash (or self-heal splash) without blocking its own flight.
            let shooter = if self
                .world
                .get::<AdmittedFlight>(entity)
                .is_some_and(|active| active.flight.affects_shooter())
            {
                self.player_body(owner)
                    .filter(|state| state.alive())
                    .map(|state| {
                        (
                            owner.entity,
                            queries.hurt_pose(state.body_template, state.target_pose()),
                        )
                    })
            } else {
                None
            };
            let check_barriers = !piercing
                && policy.affects_enemies
                && (!self.room_pets.is_empty() || !self.room_deployables.is_empty());
            let mut hit_barrier: Option<(u32, bool, f64)> = None;
            let (
                token,
                launch_frame,
                stun,
                hit_effect,
                impact_spawn,
                blast_radius,
                direct_effect,
                blast_effect,
                proj_barrier_damage,
                proj_dir,
                piercing_impacts,
                result,
            ) = {
                let room_pets = &self.room_pets;
                let room_deployables = &self.room_deployables;
                let barrier_probe_fn = |origin: Vec3, direction: Vec3, reach: f64| {
                    if check_barriers {
                        Self::probe_barrier_ray_in(
                            room_pets,
                            room_deployables,
                            Some(context.team),
                            ffa,
                            origin,
                            direction,
                            reach,
                        )
                    } else {
                        None
                    }
                };
                let mut active = self
                    .world
                    .get_mut::<AdmittedFlight>(entity)
                    .ok_or(CombatError::MissingFlight { connection, entity })?;
                let blast_radius = active.flight.blast_radius();
                let direct_effect = active.flight.direct_effect();
                let blast_effect = active.flight.blast_effect();
                let proj_barrier_damage = active.flight.barrier_impact_damage();
                let proj_dir = active.flight.velocity();
                let result = active.flight.advance_combat(
                    context.tick,
                    &candidates,
                    shooter,
                    |origin, direction, reach, radius| {
                        let world_hit =
                            queries.projectile_wall(origin, direction, reach, radius)?;
                        let limit = world_hit.map_or(reach, |w| w.distance);
                        if let Some((b_wire, b_is_pet, b_dist, b_normal)) =
                            barrier_probe_fn(origin, direction, limit)
                        {
                            hit_barrier = Some((b_wire, b_is_pet, b_dist));
                            return Ok(Some(crate::static_ray::SurfaceHit {
                                distance: b_dist,
                                normal: b_normal,
                            }));
                        }
                        Ok(world_hit)
                    },
                );
                let piercing_impacts = active.flight.drain_piercing_impacts();
                (
                    active.token,
                    active.launch_frame,
                    active.stun,
                    active.hit_effect,
                    active.impact_spawn,
                    blast_radius,
                    direct_effect,
                    blast_effect,
                    proj_barrier_damage,
                    proj_dir,
                    piercing_impacts,
                    result,
                )
            };
            if let (Some((b_wire, b_is_pet, _)), Ok(Some(end))) = (hit_barrier, &result) {
                if end.impact && end.contact.is_none() && proj_barrier_damage > 0.0 {
                    let (out_dmg, _, _, _, _) = self.actor_combat_modifiers(owner.entity);
                    self.damage_barrier_or_deployable(
                        b_wire,
                        b_is_pet,
                        proj_barrier_damage * out_dmg,
                        owner.entity,
                        context.tick,
                        context.now,
                    );
                }
            }
            for impact in &piercing_impacts {
                let is_ally_heal = policy.healing
                    || (policy.affects_allies
                        && !ffa
                        && self.actor_team(impact.entity) == Some(context.team));
                if is_ally_heal {
                    let death = Death {
                        time: context.now.as_secs_f64(),
                        frame: context.tick,
                        killer: owner.entity,
                    };
                    if let Some(outcome) = self.apply_health(
                        impact.entity,
                        HealthEffect::Heal(impact.damage),
                        death,
                        context.now,
                        None,
                    )? {
                        if outcome.delta > 0.0 {
                            self.deliver_applied_heal(owner.entity, impact.entity, outcome.delta);
                        }
                    }
                } else if self.players_by_wire.contains_key(&impact.entity) {
                    if let Some(hit) =
                        self.apply_player_impact(owner.entity, launch_frame, context, impact)?
                    {
                        hits.push(hit);
                    }
                } else if let Some(hit) =
                    self.apply_bot_impact(owner.entity, launch_frame, context, impact)?
                {
                    hits.push(hit);
                }
                if let Some(effect_guid) = direct_effect {
                    self.apply_projectile_status_effect(
                        effect_guid,
                        owner.entity,
                        impact.entity,
                        is_ally_heal,
                        context.tick,
                    );
                }
            }
            let mut completed_impact = false;
            let mut completed_point = None;
            match result {
                Ok(None) => {
                    continuing.push(entity);
                    continue;
                }
                Ok(Some(end)) => {
                    completed_impact = end.impact;
                    completed_point = Some(end.point);
                    if end.impact || (end.expired && impact_spawn.is_some()) {
                        if let Some(spawn) = impact_spawn {
                            let yaw = self
                                .player_body(owner)
                                .map(|state| state.mover.pose().yaw)
                                .unwrap_or(0);
                            self.spawn_impact_deployable(
                                owner.entity,
                                context.team,
                                spawn,
                                end.point,
                                yaw,
                                context.tick,
                            );
                        }
                        if end.impact && blast_radius > 0.0 {
                            queries.break_in_sphere(end.point, blast_radius);
                        }
                        if end.impact {
                            self.interactive_props.hit_props_in_sphere(
                                end.point,
                                blast_radius.max(1.5),
                                14.0,
                            );
                        }
                    }
                    #[cfg(any(test, feature = "inspector"))]
                    self.record_debug_ray(crate::game_debug::DebugRay {
                        tick: context.tick,
                        source: owner.entity,
                        kind: "projectile",
                        origin: crate::static_ray::along(
                            end.point,
                            {
                                let len = crate::static_ray::norm(proj_dir);
                                if len > 1e-6 {
                                    proj_dir.map(|v| v / len)
                                } else {
                                    [0.0, 0.0, 1.0]
                                }
                            },
                            -end.travelled.min(5.0),
                        ),
                        end: end.point,
                        hit: if end.contact.is_some() {
                            "actor"
                        } else if end.expired {
                            "none"
                        } else {
                            "world"
                        },
                        target: end.contact,
                        critical: end.impacts.iter().any(|i| i.critical),
                        surface: None,
                        script: None,
                    });
                    #[cfg(feature = "inspector")]
                    {
                        let surface = if end.expired {
                            "expiry"
                        } else if end.contact.is_some() {
                            "actor"
                        } else {
                            "world"
                        };
                        crate::owner_trace::playtest(format_args!(
                            "projectile event=ended peer={connection} owner={:08x} token={token:?} tick={} surface={surface} contact={:?} point={:?} travelled_s={} impacts={}",
                            owner.entity,
                            context.tick,
                            end.contact,
                            end.point,
                            end.travelled,
                            end.impacts.len()
                        ));
                    }
                    for impact in &end.impacts {
                        #[cfg(feature = "inspector")]
                        crate::owner_trace::playtest(format_args!(
                            "projectile event=impact peer={connection} owner={:08x} token={token:?} tick={} target={:08x} raw_damage={} distance={}",
                            owner.entity,
                            context.tick,
                            impact.entity,
                            impact.damage,
                            impact.distance
                        ));
                        if let Some(delta) = impact.impulse {
                            self.queue_combat_impulse(impact.entity, context.tick, delta)?;
                        }
                        let is_ally_heal = policy.healing
                            || (policy.affects_allies
                                && (impact.entity == owner.entity
                                    || (!ffa
                                        && self.actor_team(impact.entity) == Some(context.team))));
                        let effect_guid = match impact.geometry {
                            Some(crate::game_hitscan::ImpactGeometry::Blast { .. }) => {
                                blast_effect.or(direct_effect)
                            }
                            _ => direct_effect.or(blast_effect),
                        };
                        // A declared recipient graph owns the status lifecycle. Legacy
                        // flights may carry only the old physical control descriptor.
                        let stun = stun.filter(|_| {
                            !effect_guid.is_some_and(|effect| {
                                crate::script_control::LEGACY_PROJECTILE_CONTROL
                                    .has_effect_marker(&crate::script_wire::Value::Asset(effect))
                                    && self.ability_rules.status_script(effect).is_some()
                            })
                        });
                        if impact.damage == 0.0 {
                            if let Some(effect_guid) = effect_guid {
                                self.apply_projectile_status_effect(
                                    effect_guid,
                                    owner.entity,
                                    impact.entity,
                                    is_ally_heal,
                                    context.tick,
                                );
                            }
                            continue;
                        }
                        let is_ana_rifle =
                            self.is_ana_direct_rifle(owner.entity, impact.damage, impact.geometry);
                        if is_ally_heal {
                            let death = Death {
                                time: context.now.as_secs_f64(),
                                frame: context.tick,
                                killer: owner.entity,
                            };
                            let instant_heal = if is_ana_rifle {
                                let rem = impact.damage * 0.75;
                                let entry = self.actor_statuses.entry(impact.entity).or_default();
                                entry.hot_ticks = 37;
                                entry.hot_per_tick = rem / 37.0;
                                entry.hot_source = owner.entity;
                                impact.damage * 0.25
                            } else {
                                impact.damage
                            };
                            if let Some(outcome) = self.apply_health(
                                impact.entity,
                                HealthEffect::Heal(instant_heal),
                                death,
                                context.now,
                                None,
                            )? {
                                if outcome.delta > 0.0 {
                                    self.deliver_applied_heal(
                                        owner.entity,
                                        impact.entity,
                                        outcome.delta,
                                    );
                                }
                            }
                            if let Some(effect_guid) = effect_guid {
                                self.apply_projectile_status_effect(
                                    effect_guid,
                                    owner.entity,
                                    impact.entity,
                                    true,
                                    context.tick,
                                );
                            }
                            continue;
                        }
                        if is_ana_rifle {
                            let mut dot_impact = *impact;
                            dot_impact.damage = impact.damage * 0.25;
                            let rem = impact.damage * 0.75;
                            let entry = self.actor_statuses.entry(impact.entity).or_default();
                            entry.dot_ticks = 37;
                            entry.dot_per_tick = rem / 37.0;
                            entry.dot_source = owner.entity;
                            if self.players_by_wire.contains_key(&impact.entity) {
                                if let Some(hit) = self.apply_player_impact(
                                    owner.entity,
                                    launch_frame,
                                    context,
                                    &dot_impact,
                                )? {
                                    hits.push(hit);
                                }
                            } else if let Some(hit) = self.apply_bot_impact(
                                owner.entity,
                                launch_frame,
                                context,
                                &dot_impact,
                            )? {
                                hits.push(hit);
                            }
                            continue;
                        }
                        if self.players_by_wire.contains_key(&impact.entity) {
                            if impact.entity != owner.entity {
                                if let Some((reflect, _, _)) =
                                    self.check_player_deflect(impact.entity, proj_dir)
                                {
                                    if reflect && impact.damage > 0.0 {
                                        let death = Death {
                                            time: context.now.as_secs_f64(),
                                            frame: context.tick,
                                            killer: impact.entity,
                                        };
                                        let _ = self.apply_health(
                                            owner.entity,
                                            HealthEffect::Damage(impact.damage),
                                            death,
                                            context.now,
                                            None,
                                        );
                                    }
                                    continue;
                                }
                            }
                            if let Some(hit) = self.apply_player_impact(
                                owner.entity,
                                launch_frame,
                                context,
                                impact,
                            )? {
                                if hit.notices[1].is_some() {
                                    killed_in_tick.push(impact.entity);
                                }
                                hits.push(hit);
                            }
                            if let Some(stun) = stun {
                                self.apply_actor_stun(impact.entity, context.tick, stun.ticks);
                            }
                            if let Some(effect_guid) = effect_guid {
                                self.apply_projectile_status_effect(
                                    effect_guid,
                                    owner.entity,
                                    impact.entity,
                                    false,
                                    context.tick,
                                );
                            }
                            continue;
                        }
                        if let Some(hit) =
                            self.apply_bot_impact(owner.entity, launch_frame, context, impact)?
                        {
                            if hit.notices[1].is_some() {
                                killed_in_tick.push(impact.entity);
                            }
                            if let Some(stun) = stun {
                                if let Some(state) = self.state_mut(impact.entity)? {
                                    let mut control_allowed = true;
                                    let reaction =
                                        hit_effect.filter(|_| !state.bot.dead).and_then(|caller| {
                                            state
                                                .enemy
                                                .as_mut()
                                                .filter(|enemy| enemy.fault.is_none())
                                                .map(|enemy| (caller, enemy))
                                        });
                                    if let Some((caller, enemy)) = reaction {
                                        match enemy.scripts.apply_legacy_projectile_control(
                                            &enemy.graphs,
                                            &enemy.binding,
                                            caller,
                                        ) {
                                            Ok(allowed) => control_allowed = allowed,
                                            Err(error) => {
                                                control_allowed = false;
                                                enemy.halt(&mut state.bot, error);
                                            }
                                        }
                                    }
                                    if control_allowed {
                                        state.bot.stun_for(context.tick, stun.ticks);
                                    }
                                }
                            }
                            if let Some(effect_guid) = effect_guid {
                                self.apply_projectile_status_effect(
                                    effect_guid,
                                    owner.entity,
                                    impact.entity,
                                    false,
                                    context.tick,
                                );
                            }
                            hits.push(hit);
                        }
                    }
                    outcomes.push(FlightOutcome::Ended {
                        token,
                        travelled: end.travelled,
                        impact: end.impact,
                    });
                }
                // A query exceeding the bounded ray budget cancels only this
                // flight. Broken geometry and ECS ownership are room faults.
                Err(error) => {
                    #[cfg(feature = "inspector")]
                    crate::owner_trace::playtest(format_args!(
                        "projectile event=failed peer={connection} owner={:08x} token={token:?} tick={} reason={error:?}",
                        owner.entity, context.tick
                    ));
                    if matches!(error, IndexError::RayTraversalLimit) {
                        outcomes.push(FlightOutcome::Failed(token));
                    } else {
                        return Err(error.into());
                    }
                }
            }
            self.complete_observed_projectile(
                entity,
                context.tick,
                completed_impact,
                completed_point,
            );
            if let Some(wire) = self.server_projectile_wires.remove(&entity) {
                self.retired_enemies.push(wire);
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
                self.complete_observed_projectile(entity, 0, false, None);
                if let Some(wire) = self.server_projectile_wires.remove(&entity) {
                    self.retired_enemies.push(wire);
                }
                // Cleanup must remain usable while retiring a damaged room.
                self.world.despawn(entity);
            }
        }
    }

    pub(crate) fn clear_projectiles(&mut self) {
        let entities: Vec<_> = self
            .projectiles
            .drain()
            .flat_map(|(_, batch)| batch.order)
            .collect();
        for entity in entities {
            self.complete_observed_projectile(entity, 0, false, None);
            if let Some(wire) = self.server_projectile_wires.remove(&entity) {
                self.retired_enemies.push(wire);
            }
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
        if self.state_mut(impact.entity)?.is_none() {
            // An impact carrying a retired wire identity must not damage its
            // replacement body.
            return Ok(None);
        }
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
        let Some(state) = self.state_mut(impact.entity)? else {
            return Ok(None);
        };
        let Some(result) = Self::damage_bot_with_hit(
            state,
            damage_amount,
            Death {
                time: context.now.as_secs_f64(),
                frame: context.tick,
                killer: source,
            },
            context.now,
            Some(impact),
        ) else {
            return Ok(None);
        };
        let entity = state.bot.entity;
        let victim_team = state.bot.spawn.team;
        let health = bot_health_update(state, result.killed);
        let hit = BotHit {
            damage: AppliedDamage {
                source,
                amount: result.dealt,
                named_pool_losses: result.named_pool_losses,
                tags: impact.tags,
                target_facts: state.health_target_facts(),
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
                    loadout: impact.loadout.unwrap_or(0),
                    fire: 100,
                    critical: impact.critical,
                }),
            ],
        };
        self.scripted_health.push(health);
        if let Some(attacker_owner) = self
            .player_handle_for_wire(source)
            .and_then(|id| self.player_body(id).map(|p| p.owner))
        {
            self.scoreboard.record_damage(
                attacker_owner,
                entity,
                result.dealt,
                impact.critical,
                context.now.as_secs_f64(),
            );
        }
        self.record_health_applied(
            source,
            entity,
            -result.dealt,
            result.killed,
            Some(HealthDamageEvidence {
                impact: Some((impact, &hit.damage.target_facts)),
                named_pool_losses: &hit.damage.named_pool_losses,
                frame: Some(context.tick),
            }),
        );
        self.record_actor_hurt_voice(
            entity,
            true,
            source,
            impact.critical,
            result.killed,
            context.tick,
        );
        if result.killed {
            self.record_enemy_elimination(source, entity, victim_team, context.tick);
            #[cfg(feature = "lua-mods")]
            self.mod_on_kill(source, entity, impact.critical, context.tick, context.now);
        }
        Ok(Some(hit))
    }

    #[cfg(test)]
    fn damage_bot(
        state: &mut BotState,
        amount: f64,
        source: Death,
        now: Duration,
    ) -> Option<crate::vitals::Damage> {
        Self::damage_bot_with_hit(state, amount, source, now, None)
    }

    #[cfg(test)]
    pub(crate) fn kill_bot_for_test(&mut self, entity: u32, tick: u32, now: Duration) {
        self.combat_active = true;
        let state = self
            .state_mut(entity)
            .expect("test bot state lookup")
            .expect("test bot entity");
        Self::damage_bot(
            state,
            10_000.0,
            Death {
                killer: 0xa000_0100,
                frame: tick,
                time: now.as_secs_f64(),
            },
            now,
        );
    }

    fn damage_bot_with_hit(
        state: &mut BotState,
        amount: f64,
        source: Death,
        now: Duration,
        impact: Option<&Impact>,
    ) -> Option<crate::vitals::Damage> {
        if state.bot.dead {
            return None;
        }
        let result = state.vitals.damage(amount, source);
        if let Some(script) = &mut state.stock_script {
            let tags = impact.map_or(&crate::script_init::HitTags::EMPTY, |hit| &hit.tags);
            script.on_damage(state.vitals.total(), source, tags);
        }
        if result.killed {
            state.bot.died(source.frame);
            if let Some(script) = &mut state.stock_script {
                script.on_death(source, impact);
            }
            if let Some(enemy) = &mut state.enemy {
                if let Err(error) = enemy.scripts.queue_death(source, impact) {
                    enemy.halt(&mut state.bot, format!("enemy death input: {error}"));
                }
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
        self.prepare_movement_frame(tick)?;
        self.prepare_spatial_frame(tick)?;
        let motion_frame = self.movement_contact_frame();
        self.states().try_for_each(|state| state.map(|_| ()))?;
        self.validate_gate_and_encounter_handles()?;
        if !self.players_by_wire.is_empty() {
            let defenders: Arc<[u32]> = Arc::from(self.collect_junkenstein_defenders());
            self.set_junkenstein_defenders(Arc::clone(&defenders));
            self.set_player_defenders(defenders);
        }
        let stock_launches = self.advance_stock_bots(queries, tick);
        if !stock_launches.is_empty() {
            self.activate_combat();
        }
        if !self.combat_active {
            self.advance_deployables(tick);
            self.collect_local_health_events();
            self.settle_authored_requests(tick, now)?;
            self.settle_environment(tick, now)?;
            self.step_interactive_props_and_pvp(tick, now);
            self.flush_health_events();
            return self.respawn_due(tick, now);
        }
        let states = self.checked_states()?;
        let has_enemies = states.iter().any(|state| state.enemy.is_some());
        let external_and_players = if has_enemies {
            self.collect_player_enemy_targets()
        } else {
            Vec::new()
        };
        let has_roster = has_enemies
            && (self.player_targets_active
                || !self.players_by_wire.is_empty()
                || self.external_targets.is_some()
                || !stock_launches.is_empty()
                || !self.enemy_projectiles.is_empty());
        let roster =
            has_roster.then(|| {
                Arc::new(
                    external_and_players
                        .iter()
                        .cloned()
                        .chain(states.iter().copied().map(|state| {
                            crate::script_enemy::EnemyTarget {
                                hero: Some(state.bot.spawn.hero),
                                filter_flags: Some(
                                    1 | (0x0080_0000 << state.bot.spawn.team)
                                        | if state.bot.dead { 0x10 } else { 0 },
                                ),
                                tags: state.target_tags(),
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
        let npc_hitscan_targets = if has_roster {
            self.npc_weapon_targets(queries, tick)?
        } else {
            Vec::new()
        };
        let mut retired = Vec::new();
        let mut launched = Vec::new();
        let mut enemy_damage = Vec::new();
        #[cfg(any(test, feature = "inspector"))]
        let mut recorded_rays = Vec::new();
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
        let mode_boss_pause = self.encounter_boss_pause();
        for &entity in self.by_wire.values() {
            let mut state = self
                .world
                .get_mut::<BotState>(entity)
                .expect("live bot handle");
            let BotState {
                bot, vitals, enemy, ..
            } = &mut *state;
            if let Some(enemy) = enemy.as_mut().filter(|enemy| enemy.fault.is_none()) {
                if let Some(frame) = &motion_frame {
                    frame.stage_bot(bot, tick);
                }
                if bot.dead || vitals.death().is_some() {
                    enemy.scripts.clear_pending_script_motion();
                }
                if let Some(motion) = enemy
                    .scripts
                    .take_scripted_motion(tick)
                    .filter(|_| !bot.dead && vitals.death().is_none())
                {
                    if !enemy.objective_mover_attached {
                        enemy.objective_mover_attached = queries.attach_objective_mover(bot);
                    }
                    if enemy.objective_mover_attached {
                        bot.apply_scripted_motion(tick, motion);
                    }
                }
                enemy.binding.frame = tick;
                enemy.binding.now_secs = now.as_secs_f64();
                enemy.binding.retired_entities = Arc::clone(&self.retired_entity_ids);
                bot.queue_velocity_changes(enemy.scripts.take_velocity_changes());
                let stunned = bot.is_stunned(tick);
                enemy.scripts.set_control_blocked(stunned);
                let other_pause = pause_senders > usize::from(enemy.scripts.boss_pause_active());
                let room_pause = mode_boss_pause || other_pause;
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
                        enemy.halt(bot, error.to_string());
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
                        enemy.halt(bot, error.to_string());
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
                    enemy.halt(bot, error.to_string());
                    continue;
                }
                if let Err(error) = enemy.advance_boss_weapon(
                    bot,
                    tick,
                    queries,
                    room_pause || stunned || vitals.death().is_some(),
                    &external_and_players,
                ) {
                    bot.stop_objective(tick);
                    enemy.halt(bot, error.to_string());
                    continue;
                }
                let navigation_pose = bot.movement(tick);
                enemy.binding.pose = navigation_pose;
                enemy.binding.roster = roster.clone();
                // Sample the facing already held at entry without overriding the
                // bot's navigation pose if the script releases it this update.
                if let Some([yaw, pitch]) = enemy
                    .scripts
                    .authored_facing(enemy.binding.body)
                    .filter(|_| {
                        !bot.dead
                            && vitals.death().is_none()
                            && enemy.scripts.cinematic_aim().is_none()
                    })
                {
                    enemy.binding.pose.yaw = yaw;
                    enemy.binding.pose.pitch = pitch;
                }
                let advanced = enemy
                    .scripts
                    .advance(&enemy.graphs, &enemy.binding, vitals, tick);
                enemy.binding.pose = navigation_pose;
                if let Err(error) = advanced {
                    eprintln!("enemy {} halted: {error}", enemy.binding.body);
                    bot.stop_objective(tick);
                    enemy.halt(bot, error);
                } else if let Some(aim) = enemy.scripts.cinematic_aim() {
                    bot.apply_encounter_aim(aim, tick);
                } else if let Some(aim) = enemy.scripts.authored_facing(enemy.binding.body) {
                    bot.apply_encounter_aim(aim, tick);
                } else if crate::npc_catalog::is_junkenstein_boss(enemy.binding.hero) {
                    if let Some(direction) = enemy.scripts.facing_direction() {
                        bot.apply_encounter_facing(direction, tick);
                    }
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
                            let targets = npc_hitscan_targets.iter().filter_map(|target| {
                                target.hostile_to(
                                    bot.entity,
                                    enemy.binding.team,
                                    queries.free_for_all(),
                                )
                            });
                            match pellet.resolve_combat(targets, |origin, direction, reach| {
                                queries.hitscan_wall(origin, direction, reach)
                            }) {
                                Ok(Some(impact)) => {
                                    #[cfg(any(test, feature = "inspector"))]
                                    recorded_rays.push(crate::game_debug::DebugRay {
                                        tick,
                                        source: bot.entity,
                                        kind: "hitscan",
                                        origin: pellet.origin,
                                        end: crate::static_ray::along(
                                            pellet.origin,
                                            pellet.direction,
                                            impact.distance,
                                        ),
                                        hit: "actor",
                                        target: Some(impact.entity),
                                        critical: impact.critical,
                                        surface: None,
                                        script: None,
                                    });
                                    enemy_damage.push(crate::script_enemy::EnemyDamage {
                                        source: bot.entity,
                                        target: impact.entity,
                                        impact: Some(impact),
                                        amount: enemy
                                            .scripts
                                            .outgoing_damage(impact.damage, &volley.damage_tags),
                                    });
                                }
                                Ok(None) => {
                                    #[cfg(any(test, feature = "inspector"))]
                                    {
                                        let (hit, end) = match queries
                                            .hitscan_wall(
                                                pellet.origin,
                                                pellet.direction,
                                                pellet.reach,
                                            )
                                            .ok()
                                            .flatten()
                                        {
                                            Some(distance) => (
                                                "world",
                                                crate::static_ray::along(
                                                    pellet.origin,
                                                    pellet.direction,
                                                    distance,
                                                ),
                                            ),
                                            None => (
                                                "none",
                                                crate::static_ray::along(
                                                    pellet.origin,
                                                    pellet.direction,
                                                    pellet.reach,
                                                ),
                                            ),
                                        };
                                        recorded_rays.push(crate::game_debug::DebugRay {
                                            tick,
                                            source: bot.entity,
                                            kind: "hitscan",
                                            origin: pellet.origin,
                                            end,
                                            hit,
                                            target: None,
                                            critical: false,
                                            surface: None,
                                            script: None,
                                        });
                                    }
                                }
                                Err(_) => {}
                            }
                        }
                    }
                }
                self.scene_messages
                    .extend(enemy.scripts.take_scene_messages());
                enemy_damage.extend(enemy.scripts.take_scripted_damage());
                if vitals.death().is_some() && !bot.dead {
                    bot.died(tick);
                    enemy.scripts.clear_pending_script_motion();
                }
                if let Some(health) = vitals.take_cumulative_update() {
                    self.scripted_health.push(EntityUpdate {
                        entity: bot.entity,
                        operation: Some(Operation::Update(vec![health])),
                        ..Default::default()
                    });
                }
                if enemy.fault.is_none() {
                    if let Err(error) =
                        enemy
                            .scripts
                            .queue_common_corpse_retirement(&enemy.binding, vitals, tick)
                    {
                        enemy.halt(bot, error);
                    }
                }
                if enemy.fault.is_none() && enemy.scripts.retirement_requested {
                    if let Err(error) =
                        enemy
                            .scripts
                            .teardown(&enemy.graphs, &enemy.binding, vitals)
                    {
                        enemy.halt(bot, error);
                    } else {
                        retired.push(bot.entity);
                        self.scene_messages
                            .extend(enemy.scripts.take_scene_messages());
                    }
                }
            }
        }
        #[cfg(any(test, feature = "inspector"))]
        for ray in recorded_rays {
            self.record_debug_ray(ray);
        }
        self.collect_local_health_events();
        // +80 voice callbacks are a separate phase after all ordinary actor updates.
        // Select through read-only facts before taking a mutable actor reference.
        let voiced =
            self.states()
                .try_fold(Vec::new(), |mut bodies, state| -> Result<_, CombatError> {
                    let state = state?;
                    if let Some(enemy) = state.enemy.as_ref() {
                        if enemy.fault.is_none() && enemy.scripts.has_polled_voice() {
                            bodies.push(state.bot.entity);
                        }
                    }
                    Ok(bodies)
                })?;
        for body in voiced {
            let BotState {
                bot, vitals, enemy, ..
            } = self.state_mut(body)?.expect("checked voiced body");
            if let Some(enemy) = enemy.as_mut().filter(|enemy| enemy.fault.is_none()) {
                if let Err(error) = enemy
                    .scripts
                    .poll_voice(&enemy.graphs, &enemy.binding, vitals)
                {
                    bot.stop_objective(tick);
                    enemy.halt(bot, error);
                }
            }
        }
        self.collect_local_health_events();
        self.advance_enemy_projectiles(queries, tick, &mut enemy_damage)?;
        self.spawn_stock_shots(stock_launches);
        self.enemy_projectiles.extend(
            launched
                .into_iter()
                .map(|projectile| self.world.spawn(projectile).id()),
        );
        for hit in enemy_damage {
            if !self.settle_health_with_impact(
                hit.target,
                HealthEffect::Damage(hit.amount),
                Death {
                    time: now.as_secs_f64(),
                    frame: tick,
                    killer: hit.source,
                },
                now,
                hit.impact.as_ref(),
            )? {
                self.external_enemy_damage.push(hit);
            }
        }
        self.advance_deployables(tick);
        self.settle_authored_requests(tick, now)?;
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
            self.collect_local_health_events();
        }
        self.settle_environment(tick, now)?;
        self.step_interactive_props_and_pvp(tick, now);
        let replacements = self.respawn_due(tick, now);
        self.record_poses(queries, tick)?;
        let replacements = replacements?;
        self.flush_health_events();
        Ok(replacements)
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
            if let Some(state) = self.state_mut(target)? {
                if let Some(enemy) = state.enemy.as_mut().filter(|enemy| enemy.fault.is_none()) {
                    if let Err(error) = enemy.scripts.sync_witch_staff(
                        &enemy.graphs,
                        &enemy.binding,
                        &mut state.vitals,
                        Some((source, instance)),
                        None,
                    ) {
                        enemy.halt(&mut state.bot, error);
                    }
                }
            }
        }
        for (source, (target, caster, amount)) in desired {
            if let Some(state) = self.state_mut(target)?.filter(|state| !state.bot.dead) {
                if let Some(enemy) = state.enemy.as_mut().filter(|enemy| enemy.fault.is_none()) {
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
                            enemy.halt(&mut state.bot, error);
                        }
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
        tick: u32,
        damage: &mut Vec<crate::script_enemy::EnemyDamage>,
    ) -> Result<(), CombatError> {
        use crate::script_enemy::{EnemyDamage, EnemyProjectile};

        if self.enemy_projectiles.is_empty() {
            return Ok(());
        }
        let targets = self.npc_weapon_targets(queries, tick)?;
        let mut children = Vec::new();
        let mut impulses = Vec::new();
        let mut candidates = Vec::with_capacity(targets.len());
        for handle in std::mem::take(&mut self.enemy_projectiles) {
            let (prev_pos, seed_source, seed_team) = {
                let seed = self
                    .world
                    .get::<EnemyProjectile>(handle)
                    .expect("live enemy projectile");
                (seed.flight.position(), seed.source, seed.team)
            };
            let shooter_pose = {
                let seed = self
                    .world
                    .get::<EnemyProjectile>(handle)
                    .expect("live enemy projectile");
                if seed.flight.affects_shooter() {
                    self.by_wire
                        .get(&seed.source)
                        .and_then(|&body| self.world.get::<BotState>(body))
                        .filter(|state| !state.bot.dead && state.vitals.death().is_none())
                        .map(|state| {
                            (
                                seed.source,
                                queries.hurt_pose(state.bot.spawn.body, queries.target(&state.bot)),
                            )
                        })
                } else {
                    None
                }
            };
            candidates.clear();
            candidates.extend(targets.iter().filter_map(|target| {
                target.hostile_to(seed_source, seed_team, queries.free_for_all())
            }));
            let mut projectile = self
                .world
                .get_mut::<EnemyProjectile>(handle)
                .expect("live enemy projectile");
            let blast_radius = projectile.flight.blast_radius();
            let advanced = projectile.flight.advance_combat(
                tick,
                &candidates,
                shooter_pose,
                |origin, direction, reach, radius| {
                    queries.projectile_wall_readonly(origin, direction, reach, radius)
                },
            );
            let next_pos = projectile.flight.position();
            let step_delta = [
                next_pos[0] - prev_pos[0],
                next_pos[1] - prev_pos[1],
                next_pos[2] - prev_pos[2],
            ];
            let step_dist = crate::static_ray::norm(step_delta);
            let barrier_hit = if step_dist > 1e-5
                && (!self.room_pets.is_empty() || !self.room_deployables.is_empty())
            {
                let dir = step_delta.map(|v| v / step_dist);
                self.intercept_barrier_ray(
                    Some(seed_team),
                    queries.free_for_all(),
                    prev_pos,
                    dir,
                    step_dist,
                    15.0,
                    seed_source,
                    tick,
                    Duration::from_secs_f64(
                        f64::from(tick) * crate::game_clock::TICK_PERIOD.as_secs_f64(),
                    ),
                )
                .map(|(_, dist)| crate::static_ray::along(prev_pos, dir, dist))
            } else {
                None
            };
            if let Some(hit_pt) = barrier_hit {
                self.complete_observed_projectile(handle, tick, true, Some(hit_pt));
                self.world.despawn(handle);
                continue;
            }
            let mut projectile = self
                .world
                .get_mut::<EnemyProjectile>(handle)
                .expect("live enemy projectile");
            let mut prop_blast_point = None;
            let mut impact_hit = false;
            let mut end_point = None;
            match advanced {
                Ok(None) => {
                    self.enemy_projectiles.push(handle);
                    continue;
                }
                Ok(Some(end)) => {
                    impact_hit = end.impact;
                    end_point = Some(end.point);
                    if end.impact {
                        if blast_radius > 0.0 {
                            queries.break_in_sphere(end.point, blast_radius);
                        }
                        prop_blast_point = Some(end.point);
                    }
                    let source = projectile.source;
                    let team = projectile.team;
                    #[cfg(feature = "inspector")]
                    crate::owner_trace::playtest(format_args!(
                        "npc-projectile event=ended source={source:08x} tick={tick} contact={:?} point={:?} travelled_s={} expired={} impacts={}",
                        end.contact,
                        end.point,
                        end.travelled,
                        end.expired,
                        end.impacts.len()
                    ));
                    if end.expired {
                        if let Some(split) = projectile.split.take() {
                            children.extend(split.launch(source, team, end.point, tick));
                        }
                    }
                    let tags = projectile.damage_tags.clone();
                    let shooter = self
                        .by_wire
                        .get(&source)
                        .and_then(|&body| self.world.get::<BotState>(body))
                        .and_then(|state| state.enemy.as_ref());
                    for impact in end.impacts {
                        if let Some((reflect, _eye, _aim)) =
                            self.check_player_deflect(impact.entity, step_delta)
                        {
                            if reflect && impact.damage > 0.0 && source != impact.entity {
                                // Genji Deflect reflects enemy projectile damage back to the shooter.
                                let reflected_amount = impact.damage;
                                let mut reflected = impact;
                                reflected.entity = source;
                                damage.push(EnemyDamage {
                                    source: impact.entity,
                                    target: source,
                                    impact: Some(reflected),
                                    amount: reflected_amount,
                                });
                            }
                            continue;
                        }
                        if let Some(delta) = impact.impulse {
                            impulses.push((impact.entity, delta));
                        }
                        if impact.damage > 0.0 {
                            damage.push(EnemyDamage {
                                source,
                                target: impact.entity,
                                impact: Some(impact),
                                amount: shooter.map_or(impact.damage, |enemy| {
                                    enemy.scripts.outgoing_damage(impact.damage, &tags)
                                }),
                            });
                        }
                    }
                }
                Err(_error) => {
                    #[cfg(feature = "inspector")]
                    crate::owner_trace::playtest(format_args!(
                        "npc-projectile event=failed source={:08x} tick={tick} reason={_error:?}",
                        projectile.source
                    ));
                }
            }
            self.complete_observed_projectile(handle, tick, impact_hit, end_point);
            self.world.despawn(handle);
            if let Some(point) = prop_blast_point {
                self.interactive_props
                    .hit_props_in_sphere(point, blast_radius.max(1.5), 14.0);
            }
        }
        self.enemy_projectiles.extend(
            children
                .into_iter()
                .map(|projectile| self.world.spawn(projectile).id()),
        );
        for (target, delta) in impulses {
            self.queue_combat_impulse(target, tick, delta)?;
        }
        Ok(())
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
                state.base_target_tags.clone(),
                state.bot.entity,
                created,
            ));
        }
        let mut replacements = Vec::with_capacity(prepared.len());
        for (slot, previous, fresh, base_target_tags, retired, created) in prepared {
            let fresh_wire = fresh.entity;
            if !self.world.despawn(previous) {
                return Err(CombatError::MissingBot {
                    wire: retired,
                    entity: previous,
                });
            }
            self.actor_statuses.remove(&retired);
            self.status_scripts.retain(|&(wire, _), _| wire != retired);
            let mut next_state = BotState::new(fresh, Some(tick));
            next_state.base_target_tags = base_target_tags;
            if let Some(catalog) = &self.stock_bots {
                next_state.stock_script = stock_bot::StockBotScript::start(
                    catalog,
                    &next_state.bot,
                    tick,
                    stock_bot::BodyEntry::Respawn,
                );
            }
            let waits_for_presentation = next_state
                .stock_script
                .as_ref()
                .and_then(stock_bot::StockBotScript::projection)
                .is_some();
            let entity = self.world.spawn(next_state).id();
            self.by_wire
                .replace_index(slot, fresh_wire)
                .map_err(|_| CombatError::BotIdInUse(fresh_wire))?;
            *self
                .by_wire
                .get_index_mut(slot)
                .ok_or(CombatError::MissingBotSlot(slot))?
                .1 = entity;
            #[cfg(feature = "inspector")]
            crate::owner_trace::playtest(format_args!(
                "bot-respawn event=created tick={tick} old={retired:08x} new={fresh_wire:08x}"
            ));
            replacements.push(BotReplacement {
                retired,
                created,
                waits_for_presentation,
            });
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

    #[cfg(test)]
    pub(crate) fn reset_test_bodies(&mut self) {
        for entity in self.by_wire.drain(..).map(|(_, entity)| entity) {
            self.world.despawn(entity);
        }
        self.clear_players();
        self.clear_projectiles();
        for entity in self.enemy_projectiles.drain(..) {
            self.world.despawn(entity);
        }
        self.projectile_reports = Default::default();
        self.player_generations.clear();
        self.latest_wire_generations.clear();
        self.next_bot = BOT_ENTITY;
        self.combat_active = false;
        self.player_targets_active = false;
        self.protected_hero = None;
        self.created.clear();
        self.player_events.clear();
        self.player_deaths.clear();
        self.observer_player_health.clear();
        self.scene_messages.clear();
        self.external_targets = None;
        self.external_enemy_damage.clear();
        self.scripted_health.clear();
        self.retired_enemies.clear();
        self.retired_entity_ids = Arc::default();
        self.defender_messages.clear();
        self.mode_voice_requests.clear();
        self.player_cues.clear();
        self.witch_staff_links.clear();
        self.interactive_props = Default::default();
        self.interactive_prop_floor_y = 0.0;
        self.pvp_objective = None;
        self.pvp_completion_emitted = false;
        self.pending_match_winner = None;
        self.last_pvp_payload_pos = None;
        self.last_pvp_hud_key = None;
        self.completed_career_records.clear();
        self.soul_anchors.clear();
        self.active_duplicates.clear();
        self.pending_duplicate_reverts.clear();
        self.self_destruct_bombs.clear();
        #[cfg(feature = "lua-mods")]
        {
            self.mod_host = None;
            self.mod_state = mod_bridge::RoomModState::default();
        }
        #[cfg(any(test, feature = "inspector"))]
        {
            self.debug_rays.clear();
        }
        self.debug_assert_encounter_invariants();
    }
}

fn bot_health_update(state: &mut BotState, killed: bool) -> EntityUpdate {
    let mut components = vec![state.vitals.cumulative_component()];
    state.vitals.clear_changed();
    if killed {
        if let Ok(filter) = crate::match_scene::bot_filter_component(state.bot.spawn.team, true) {
            components.push(filter);
        }
    }
    EntityUpdate {
        entity: state.bot.entity,
        operation: Some(Operation::Update(components)),
        ..EntityUpdate::default()
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

struct WeaponTarget {
    wire: u32,
    team: u8,
    pose: crate::game_hitscan::HurtPose,
}

impl WeaponTarget {
    fn hostile_to(
        &self,
        source: u32,
        team: u8,
        free_for_all: bool,
    ) -> Option<(u32, crate::game_hitscan::HurtPose)> {
        eligible_target(self.wire, true, self.team, source, team, free_for_all)
            .then(|| (self.wire, self.pose.clone()))
    }
}

fn eligible_target(
    entity: u32,
    alive: bool,
    target_team: u8,
    source: u32,
    source_team: u8,
    free_for_all: bool,
) -> bool {
    alive && entity != source && (free_for_all || target_team != source_team)
}

#[cfg(test)]
fn eligible_bot(bot: &Bot, source: u32, team: u8, free_for_all: bool) -> bool {
    eligible_target(
        bot.entity,
        !bot.dead,
        bot.spawn.team,
        source,
        team,
        free_for_all,
    )
}

#[cfg(test)]
mod tests;
