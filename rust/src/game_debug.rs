//! Read-only simulation facts copied by the game worker for local diagnostics.

#[cfg(any(test, feature = "inspector"))]
use std::collections::BTreeMap;

#[cfg(any(test, feature = "inspector"))]
use indexmap::IndexMap;
use serde::Serialize;

#[cfg(any(test, feature = "inspector"))]
use crate::{
    game_timing::WorkerTiming,
    script_init::{Snapshot as ScriptSnapshot, StateKind},
    script_wire::{Value as ScriptValue, Variables},
};

#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_RAYS: usize = 64;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_CONTACT_NORMALS: usize = 64;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_GROUND_PROBES: usize = 64;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_MOVER_NORMALS: usize = 8;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_ROUTES: usize = 64;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_ROUTE_WAYPOINTS: usize = 32;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_SPAWN_CANDIDATES: usize = 64;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_VM_ENTITIES: usize = 32;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_VM_INSTANCES: usize = 32;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_VM_STATES: usize = 64;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_VM_VARS: usize = 32;
#[cfg(any(test, feature = "inspector"))]
pub(crate) const MAX_DEBUG_STEP_BUDGET: u32 = 60;
#[cfg(any(test, feature = "inspector"))]
const MAX_DEBUG_STRING_CHARS: usize = 64;

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum RoomControlCommand {
    Pause,
    Resume,
    Step(u32),
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub(crate) struct RoomControlStatus {
    pub paused: bool,
    pub pending_steps: u32,
    pub sim_tick: u32,
    pub stepped_ticks: u64,
    pub max_step_budget: u32,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct AiPolicy {
    pub generation: u64,
    pub abi_version: u32,
    pub digest: String,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Snapshot {
    pub tick: u32,
    pub rooms: Vec<Room>,
    pub truncated: bool,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub control_enabled: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timing: Option<WorkerTiming>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ai_policy: Option<AiPolicy>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub faults: Vec<RoomFault>,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct RoomFault {
    pub room: String,
    pub stage: &'static str,
    pub connection: Option<u32>,
    pub tick: u32,
    pub reason: &'static str,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Room {
    pub id: String,
    pub map: String,
    pub name: String,
    pub actors: Vec<Actor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub encounter: Option<Encounter>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ai_generation: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub control: Option<RoomControlStatus>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub physics: Option<PhysicsOverlay>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub navigation: Option<NavigationOverlay>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub scripts: Vec<EntityScriptSnapshot>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub scripts_truncated: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Encounter {
    pub elapsed_ms: u64,
    pub countdown_started: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Debug, Serialize)]
pub(crate) struct PhysicsOverlay {
    pub rays: Vec<DebugRay>,
    pub contact_normals: Vec<DebugContactNormal>,
    pub ground_probes: Vec<DebugGroundProbe>,
    pub truncated: PhysicsTruncation,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub(crate) struct PhysicsTruncation {
    pub rays: bool,
    pub contact_normals: bool,
    pub ground_probes: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Serialize)]
pub(crate) struct DebugRay {
    pub tick: u32,
    pub source: u32,
    pub kind: &'static str,
    pub origin: [f64; 3],
    pub end: [f64; 3],
    pub hit: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target: Option<u32>,
    pub critical: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Serialize)]
pub(crate) struct DebugContactNormal {
    pub tick: u32,
    pub entity: u32,
    pub point: [f64; 3],
    pub normal: [f64; 3],
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Serialize)]
pub(crate) struct DebugGroundProbe {
    pub tick: u32,
    pub entity: u32,
    pub origin: [f64; 3],
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hit: Option<[f64; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub normal: Option<[f64; 3]>,
    pub grounded: bool,
    pub non_ground_under: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Debug, Serialize)]
pub(crate) struct NavigationOverlay {
    pub routes: Vec<DebugBotRoute>,
    pub spawn_candidates: Vec<DebugSpawnCandidate>,
    pub truncated: NavigationTruncation,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub(crate) struct NavigationTruncation {
    pub routes: bool,
    pub spawn_candidates: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Debug, Serialize)]
pub(crate) struct DebugBotRoute {
    pub entity: u32,
    pub kind: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub patrol_index: Option<usize>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub waypoints: Vec<[f64; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target: Option<[f64; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_entity: Option<u32>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub path: Vec<[f64; 3]>,
    pub truncated: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Serialize)]
pub(crate) struct DebugSpawnCandidate {
    pub tick: u32,
    pub instance: u16,
    pub state: u16,
    pub origin: [f64; 3],
    pub candidate: [f64; 3],
    pub accepted: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub landed: Option<[f64; 3]>,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Debug, Serialize)]
pub(crate) struct EntityScriptSnapshot {
    pub entity: u32,
    pub kind: &'static str,
    pub now_ms: u64,
    pub diagnostics: usize,
    pub instances: Vec<ScriptInstanceSummary>,
    pub instances_truncated: bool,
    pub variables: Vec<ScriptVarSummary>,
    pub variables_truncated: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Debug, Serialize)]
pub(crate) struct ScriptInstanceSummary {
    pub id: u16,
    pub graph: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent: Option<[u16; 2]>,
    pub active_states: Vec<ScriptStateSummary>,
    pub states_truncated: bool,
    pub variables: Vec<ScriptVarSummary>,
    pub variables_truncated: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Copy, Debug, Serialize)]
pub(crate) struct ScriptStateSummary {
    pub raw: u16,
    pub kind: &'static str,
    pub activation: u16,
    pub start_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub flags: Option<u8>,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Clone, Debug, Serialize)]
pub(crate) struct ScriptVarSummary {
    pub id: u32,
    pub value: String,
    pub resolved: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Actor {
    pub entity: u32,
    pub generation: Option<u64>,
    pub kind: ActorKind,
    pub team: u8,
    pub position: [f64; 3],
    pub velocity: [f64; 3],
    pub alive: bool,
    pub health: Option<f64>,
    pub script_active: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_contact_clip: Option<ContactClip>,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub(crate) struct ContactClip {
    pub tick: u32,
    pub proxy_id: u32,
    pub category: u16,
    pub fraction: f32,
    pub before: [f64; 3],
    pub intended: [f64; 3],
    pub after: [f64; 3],
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ActorKind {
    Player,
    Bot,
}

#[cfg(any(test, feature = "inspector"))]
pub(crate) fn summarize_script_snapshot(
    entity: u32,
    kind: &'static str,
    snapshot: &ScriptSnapshot,
) -> EntityScriptSnapshot {
    let mut raw_instances: Vec<_> = snapshot.instances.iter().collect();
    raw_instances.sort_unstable_by_key(|&(&id, _)| id);
    let instances_truncated = raw_instances.len() > MAX_DEBUG_VM_INSTANCES;
    let instances = raw_instances
        .into_iter()
        .take(MAX_DEBUG_VM_INSTANCES)
        .map(|(&id, instance)| {
            let mut active: Vec<_> = instance
                .states
                .iter()
                .filter(|(_, state)| state.active)
                .collect();
            active.sort_unstable_by_key(|&(&raw, _)| raw);
            let states_truncated = active.len() > MAX_DEBUG_VM_STATES;
            let active_states = active
                .into_iter()
                .take(MAX_DEBUG_VM_STATES)
                .map(|(&raw, state)| {
                    let (kind, flags) = state_kind_summary(state.kind);
                    ScriptStateSummary {
                        raw,
                        kind,
                        activation: state.activation,
                        start_ms: state.start_ms,
                        flags,
                    }
                })
                .collect();
            let (variables, variables_truncated) =
                collect_variables(&instance.variables, &snapshot.resolved_bindings, id);
            ScriptInstanceSummary {
                id,
                graph: format!("0x{:04X}", instance.graph),
                parent: instance.parent.map(|(parent, state)| [parent, state]),
                active_states,
                states_truncated,
                variables,
                variables_truncated,
            }
        })
        .collect();
    let (variables, variables_truncated) =
        collect_variables(&snapshot.variables, &snapshot.resolved_bindings, 0);
    EntityScriptSnapshot {
        entity,
        kind,
        now_ms: snapshot.now_ms,
        diagnostics: snapshot.diagnostics.len(),
        instances,
        instances_truncated,
        variables,
        variables_truncated,
    }
}

#[cfg(any(test, feature = "inspector"))]
fn collect_variables(
    base: &Variables,
    resolved_bindings: &IndexMap<(u16, u32), ScriptValue>,
    scope: u16,
) -> (Vec<ScriptVarSummary>, bool) {
    let mut merged: BTreeMap<u32, (String, bool)> = BTreeMap::new();
    for (&id, value) in base {
        merged.insert(id, (format_debug_value(value), false));
    }
    for (&(binding_scope, id), value) in resolved_bindings {
        if binding_scope == scope {
            merged.insert(id, (format_debug_value(value), true));
        }
    }
    let truncated = merged.len() > MAX_DEBUG_VM_VARS;
    let variables = merged
        .into_iter()
        .take(MAX_DEBUG_VM_VARS)
        .map(|(id, (value, resolved))| ScriptVarSummary {
            id,
            value,
            resolved,
        })
        .collect();
    (variables, truncated)
}

#[cfg(any(test, feature = "inspector"))]
fn format_debug_value(value: &ScriptValue) -> String {
    match value {
        ScriptValue::Null => "null".to_owned(),
        ScriptValue::Bool(value) => value.to_string(),
        ScriptValue::Integer(value) => value.to_string(),
        ScriptValue::Float(value) => {
            if value.is_finite() {
                format!("{value:.3}")
            } else {
                format!("{value}")
            }
        }
        ScriptValue::Asset(guid) => format!("0x{guid:016X}"),
        ScriptValue::Entity(entity) => format!("entity:{entity:08X}"),
        ScriptValue::Vector([x, y, z]) => format!("[{x:.3}, {y:.3}, {z:.3}]"),
        ScriptValue::Handle {
            kind,
            instance,
            state,
            extra,
        } => format!("handle({kind},{instance},{state},{extra})"),
        ScriptValue::String(text) => format_debug_string(text),
        ScriptValue::Array(items) => format!("array[{}]", items.len()),
        ScriptValue::Map(items) => format!("map[{}]", items.len()),
        ScriptValue::IdentifierMap(_) => "id_map".to_owned(),
    }
}

#[cfg(any(test, feature = "inspector"))]
fn format_debug_string(text: &str) -> String {
    if text.chars().count() <= MAX_DEBUG_STRING_CHARS {
        return text.to_owned();
    }
    let mut out = String::new();
    for ch in text.chars().take(MAX_DEBUG_STRING_CHARS) {
        out.push(ch);
    }
    out.push_str("...");
    out
}

#[cfg(any(test, feature = "inspector"))]
fn state_kind_summary(kind: StateKind) -> (&'static str, Option<u8>) {
    match kind {
        StateKind::RollMovementParameter => ("RollMovementParameter", None),
        StateKind::ExplosionContact => ("ExplosionContact", None),
        StateKind::TrackedTargets => ("TrackedTargets", None),
        StateKind::DeadeyeEffects => ("DeadeyeEffects", None),
        StateKind::WitchStaffEffect => ("WitchStaffEffect", None),
        StateKind::WitchStaffHealing => ("WitchStaffHealing", None),
        StateKind::SummonerBeamRay => ("SummonerBeamRay", None),
        StateKind::SummonerBeamDamage => ("SummonerBeamDamage", None),
        StateKind::ModifyHealth => ("ModifyHealth", None),
        StateKind::ShieldRecharge => ("ShieldRecharge", None),
        StateKind::ForCode => ("ForCode", None),
        StateKind::AiBehavior { .. } => ("AiBehavior", None),
        StateKind::AiControlSetting => ("AiControlSetting", None),
        StateKind::AiControlOverride { .. } => ("AiControlOverride", None),
        StateKind::AiFacing(_) => ("AiFacing", None),
        StateKind::Plain => ("Plain", None),
        StateKind::HealthPool { .. } => ("HealthPool", None),
        StateKind::HealthCapacity => ("HealthCapacity", None),
        StateKind::AiWeaponOverride { .. } => ("AiWeaponOverride", None),
        StateKind::TargetTag { .. } => ("TargetTag", None),
        StateKind::DisplayName { .. } => ("DisplayName", None),
        StateKind::BooleanSwitch(_) => ("BooleanSwitch", None),
        StateKind::DelayedSwitch { .. } => ("DelayedSwitch", None),
        StateKind::Switch { .. } => ("Switch", None),
        StateKind::ActiveQuery => ("ActiveQuery", None),
        StateKind::GamePadVibration => ("GamePadVibration", None),
        StateKind::SubScript { .. } => ("SubScript", None),
        StateKind::PhaseScriptList => ("PhaseScriptList", None),
        StateKind::PhaseScript => ("PhaseScript", None),
        StateKind::PhaseTransition => ("PhaseTransition", None),
        StateKind::MessageIdle => ("MessageIdle", None),
        StateKind::BossPauseReceiver { .. } => ("BossPauseReceiver", None),
        StateKind::Registered { .. } => ("Registered", None),
        StateKind::Wait => ("Wait", None),
        StateKind::WaitFrames { .. } => ("WaitFrames", None),
        StateKind::CinematicTimeScale { .. } => ("CinematicTimeScale", None),
        StateKind::AiCandidate { .. } => ("AiCandidate", None),
        StateKind::LogicalButton { .. } => ("LogicalButton", None),
        StateKind::ButtonBlock(_) => ("ButtonBlock", None),
        StateKind::Stack { .. } => ("Stack", None),
        StateKind::Ability { flags } => ("Ability", Some(flags)),
        StateKind::MovementMod => ("MovementMod", None),
        StateKind::Defer => ("Defer", None),
        StateKind::VoiceStimulus => ("VoiceStimulus", None),
        StateKind::BossIntroVoice(_) => ("BossIntroVoice", None),
        StateKind::GateVoice(_) => ("GateVoice", None),
        StateKind::VictoryVoice => ("VictoryVoice", None),
        StateKind::SameTeamContact { .. } => ("SameTeamContact", None),
        StateKind::OutgoingDamageFilter { .. } => ("OutgoingDamageFilter", None),
        StateKind::MovementSample(_) => ("MovementSample", None),
        StateKind::Anim => ("Anim", None),
        StateKind::Effect => ("Effect", None),
        StateKind::ClientPulse { .. } => ("ClientPulse", None),
        StateKind::EmptyTargets => ("EmptyTargets", None),
        StateKind::SendMessage => ("SendMessage", None),
        StateKind::PersistentSend(_) => ("PersistentSend", None),
        StateKind::CosmeticEntity => ("CosmeticEntity", None),
        StateKind::ChaseVar => ("ChaseVar", None),
        StateKind::Watch => ("Watch", None),
        StateKind::SpawnSearch => ("SpawnSearch", None),
        StateKind::RecentInput => ("RecentInput", None),
        StateKind::LivingBodyArray => ("LivingBodyArray", None),
        StateKind::Shockwave { .. } => ("Shockwave", None),
        StateKind::PlacementSelector { .. } => ("PlacementSelector", None),
        StateKind::MoveTo { .. } => ("MoveTo", None),
        StateKind::WeaponVolley { .. } => ("WeaponVolley", None),
    }
}
