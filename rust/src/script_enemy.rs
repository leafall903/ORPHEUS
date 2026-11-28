//! Staged body/controller startup for authored scene enemies.

use crate::{
    commands::{Command, FIRE, SECONDARY},
    game_combat::RoomScriptRequests,
    game_content::{TUTORIAL_MODE, is_junkenstein_mode},
    game_hitscan::{HitDefinition, HitscanShot, TargetPose, aim_direction},
    mover::sample_movement_flags,
    npc_catalog::NpcDefinition,
    projectile_flight::{ProjectileBallistics, ProjectileFlight, authored_gravity},
    pve_encounter::messages::{
        BOSS_INTRO_ENDED, BOSS_INTRO_STARTED, DefenderMessage, DefenderSighting, SIGHTING,
        SIGHTING_TAGS, SightingSource,
    },
    pve_encounter::navigation::{
        BossTarget, GrenadeArc, JUNKRAT, REAPER, ROADHOG, SHOCK_TIRE, SUMMONER, WITCH, ZOMBARDIER,
    },
    pve_encounter::voice::{IntroCue, VoiceRequest},
    script_expr::{EntityRelation, OnFire, native_shape},
    script_graph::{EnemyPair, GraphStore, asset_guid},
    script_init::{
        self, AiWeaponOverride, BeamContact, Component, InitFault, MAX_INIT_WORK, PlayScript,
        RoomMessage, Site, TargetTagEffect, TargetTagEffects, World,
    },
    script_wire::{Value, Variables},
    vitals::{NamedPoolLoss, PoolKind, Vitals},
    world::{GAME_MODE_ENTITY, Movement},
};
use indexmap::IndexMap;
use serde_json::Value as Json;
use std::{
    collections::{BTreeSet, HashSet, VecDeque},
    sync::Arc,
};

#[derive(Clone, Default)]
pub(crate) struct AiControlRecords {
    overrides: IndexMap<(u32, u16, u16), script_init::AiControlOverride>,
    facing: IndexMap<Site, script_init::AiFacing>,
    cinematic_aim: IndexMap<Site, [i32; 2]>,
    room_boss_pause: bool,
    control_blocked: bool,
    weapons: IndexMap<Site, AiWeaponOverride>,
    display_names: IndexMap<Site, u64>,
    model_flags: u64,
    same_team_contact_suppressions: HashSet<Site>,
    outgoing_damage_filters: IndexMap<Site, OutgoingDamageFilter>,
    velocity_changes: Vec<(u32, crate::mover::VelocityChange)>,
    scripted_motion: Option<(u32, script_init::ScriptedMotion)>,
    defenders: Arc<[u32]>,
    sightings: Vec<DefenderSighting>,
    defender_messages: Vec<DefenderMessage>,
    boss_intro_starts: Vec<Variables>,
    voice_requests: Vec<VoiceRequest>,
    voice_variation: u64,
    scripted_damage: Vec<EnemyDamage>,
    local_health_changes: Vec<LocalHealthChange>,
    script_requests: RoomScriptRequests,
    objective_route_active: Option<bool>,
    weapon_target: Option<BossTarget>,
    summoner_targets: [Option<u32>; 2],
    summoner_rays: [BeamContact; 2],
    witch_ally: Option<u32>,
    witch_switch: Option<(u16, u16)>,
    witch_target_distance: Option<f64>,
    witch_staff_effect: Option<(u32, f64)>,
    witch_staff_sources: IndexMap<u32, WitchStaffSource>,
    ai_control_setting: Option<(f32, bool)>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct LocalHealthChange {
    pub(crate) source: u32,
    pub(crate) delta: f64,
    pub(crate) killed: bool,
    pub(crate) named_pool_losses: Option<Vec<NamedPoolLoss>>,
}

#[derive(Clone)]
pub(crate) struct WitchStaffSource {
    pub controller: u32,
    pub name: String,
    pub health: [f64; 2],
    pub capacity: [f64; 2],
}

#[derive(Clone)]
struct OutgoingDamageFilter {
    adjustment: f64,
    excluded_tags: Vec<u64>,
}

#[derive(Default)]
pub(crate) struct EnemyWeaponShots {
    pub hitscan: Vec<EnemyHitscanVolley>,
    pub projectiles: Vec<EnemyProjectile>,
}

#[derive(bevy_ecs::component::Component)]
pub(crate) struct EnemyProjectile {
    pub source: u32,
    pub team: u8,
    pub flight: ProjectileFlight,
    pub damage_tags: Arc<[u64]>,
    pub split: Option<ProjectilePattern>,
}

pub(crate) struct ProjectilePattern {
    ballistics: ProjectileBallistics,
    gravity: Option<f32>,
    directions: Vec<[f64; 3]>,
    damage_tags: Arc<[u64]>,
}

impl ProjectilePattern {
    pub(crate) fn definition_and_lifetime(&self) -> Option<(u64, f64)> {
        Some((self.ballistics.definition?, self.ballistics.lifetime))
    }

    pub(crate) fn launch(
        self,
        source: u32,
        team: u8,
        origin: [f64; 3],
        frame: u32,
    ) -> impl Iterator<Item = EnemyProjectile> {
        self.directions.into_iter().filter_map(move |direction| {
            let mut flight =
                ProjectileFlight::new(self.ballistics.clone(), origin, direction, Some(frame))?;
            if let Some(gravity) = self.gravity {
                flight = flight.with_gravity(gravity);
            }
            Some(EnemyProjectile {
                source,
                team,
                flight,
                damage_tags: self.damage_tags.clone(),
                split: None,
            })
        })
    }
}

pub(crate) struct EnemyHitscanVolley {
    pub pellets: Vec<HitscanShot>,
    pub damage_tags: Vec<u64>,
}

#[derive(Clone)]
struct PairDelivery {
    target: u32,
    input: PairInput,
}

#[derive(Clone)]
enum PairInput {
    Message {
        sender: u32,
        message: u64,
        parameters: Variables,
        required_graph: Option<u32>,
    },
    PlayScript {
        caller: Site,
        request: PlayScript,
    },
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct EnemyGate {
    pub entity: u32,
    pub position: [f64; 3],
}

#[derive(Clone)]
pub(crate) struct SceneMessage {
    pub target: u32,
    pub message: u64,
    pub parameters: Variables,
}

#[derive(Clone)]
pub(crate) struct EnemyTarget {
    pub hero: Option<u64>,
    pub filter_flags: Option<u64>,
    pub tags: script_init::TargetTagSnapshot,
    pub health: Option<f64>,
    pub velocity: Option<[f64; 3]>,
    pub script_state: crate::script_entity_state::EntityScriptState,
    pub entity: u32,
    pub team: u8,
    pub pose: TargetPose,
    pub alive: bool,
}

#[derive(Clone)]
pub(crate) struct EnemyDamage {
    pub source: u32,
    pub target: u32,
    pub amount: f64,
    pub impact: Option<crate::game_hitscan::Impact>,
}

#[derive(Clone)]
pub(crate) struct EnemyScripts {
    pub body: Component,
    pub controller: Component,
    control_records: AiControlRecords,
    behavior_records: indexmap::IndexMap<(u32, u16, u16), script_init::AiBehaviorRequest>,
    target_tag_effects: TargetTagEffects,
    messages: VecDeque<PairDelivery>,
    scene_messages: Vec<SceneMessage>,
    pub(crate) retirement_requested: bool,
    movement_modifiers: crate::mover_modifiers::ModifierTimeline,
}

pub(crate) struct EnemySpawn<'a> {
    pub graphs: Arc<GraphStore>,
    pub codecs: Arc<crate::script_codecs::CodecTable>,
    pub definition: &'a NpcDefinition,
    pub request: &'a script_init::EntityCreation,
    pub mode: u64,
    pub mode_variables: Arc<Variables>,
    pub tick: u32,
    pub walk: crate::game_bots::BotWalk,
    pub gate: Option<EnemyGate>,
}

pub(crate) struct EnemyBinding {
    pub mode_voice_set: Option<u64>,
    pub body: u32,
    pub controller: u32,
    pub hero: u64,
    pub pair: EnemyPair,
    pub mode: u64,
    pub mode_variables: Arc<Variables>,
    pub team: u8,
    pub skin: Option<u64>,
    pub pose: Movement,
    pub gate: Option<EnemyGate>,
    pub roster: Option<Arc<Vec<EnemyTarget>>>,
    pub spatial_frame: Option<Arc<crate::game_combat::RoomSpatialFrame>>,
    /// Explicit room retirement facts; absence from a target roster alone is unknown.
    pub retired_entities: Arc<BTreeSet<u32>>,
    /// Authoritative room clock, independent of optional movement wire fields.
    pub frame: u32,
    pub now_secs: f64,
    pub spawn_anchors: Option<Arc<crate::scene_assets::SpawnAnchors>>,
}

pub(crate) struct EnemyWorld<'a> {
    retirement_requested: &'a mut bool,
    target_tag_effects: &'a mut TargetTagEffects,
    messages: &'a mut VecDeque<PairDelivery>,
    pub behavior_records:
        &'a mut indexmap::IndexMap<(u32, u16, u16), script_init::AiBehaviorRequest>,
    pub control_records: &'a mut AiControlRecords,
    pub binding: &'a EnemyBinding,
    pub graphs: &'a GraphStore,
    pub vitals: &'a mut Vitals,
    pub body_variables: &'a Variables,
    pub controller_variables: &'a Variables,
}

mod policy;
pub(crate) use policy::projectile_pattern;
mod world;

#[cfg(test)]
pub(crate) use policy::fixture;

#[cfg(test)]
mod tests;
