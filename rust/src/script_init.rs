//! Initialization and supported frame events of authored statescript graphs.
//!
//! This covers Entry, typed variable writes, conditional links and static
//! SubScript instances. Diagnostics make a partial snapshot unsuitable for
//! gameplay replication. The component also advances static waits and dispatches
//! scoped message entries. Input, combat and other authored actions still need
//! their corresponding runtime.

use std::{
    collections::{BTreeMap, HashSet},
    error::Error,
    fmt,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};

use indexmap::IndexMap;
use serde_json::Value as Json;

use crate::{
    commands::Command,
    junkenstein_messages::{BOSS_CAMERA, BOSS_PAUSE},
    junkenstein_voice::{IntroCue, gate_cue, intro_cue, victory_cue},
    mover_modifiers::{Modifier, ModifierKey, Operation},
    script_expr::{
        self, Context, EmptyContext, EntityRelation, EvalError, OnFire, Scope, StackPriority,
        Variable, equal, truthy,
    },
    script_graph::{Body, Graph, GraphStore, Node, asset_guid},
    script_runtime::{ButtonEdge, FRAME_MS, QueueFull, Timeline, button_edges, frame_start},
    script_wire::{Value, Variables},
    vitals::PoolKind,
};

pub const MAX_INIT_WORK: usize = 100_000;
const MAX_PLUG_DEPTH: usize = 64;
const MAX_EVENTS_PER_UPDATE: usize = 1000;
const MAX_PENDING_EVENTS: usize = 16_384;
const MAX_PENDING_SHOTS: usize = 256;
const MAX_PLAY_INSTANCES: usize = 1500;
const SCRIPT_SOURCE: u32 = 0x1b;
const ABILITY_RUNNING: u8 = 1;
const ABILITY_PUSHED: u8 = 2;
const ABILITY_HELD: u8 = 4;
const SWITCH_TO_TRUE: u16 = 1;
const SWITCH_TO_FALSE: u16 = 2;
const LINK_PRIORITY: StackPriority = StackPriority {
    level: 0,
    above_equals: true,
};
const ABORT_PLUG: &str = "m_F198FD3A";
const SUBGRAPH_STATES: &str = "m_FF3DAF1E";
const MOVEMENT_BOOLEAN_OUTPUTS: [&str; 12] = [
    "m_105DF1D6",
    "m_49F4B33C",
    "m_56AC7B36",
    "m_16BC4759",
    "m_F620FC2A",
    "m_6306AA29",
    "m_103A8581",
    "m_958FF5C0",
    "m_4C55BB03",
    "m_2EB48682",
    "m_45EB431F",
    "m_BF152E27",
];

#[derive(Clone, Debug)]
pub struct Root {
    pub id: u16,
    pub graph: u32,
    pub variables: Variables,
    run_startup: bool,
    initial_state: Option<u16>,
}

impl Root {
    pub fn new(id: u16, graph: u32) -> Self {
        Self {
            id,
            graph,
            variables: Variables::new(),
            run_startup: true,
            initial_state: None,
        }
    }

    #[cfg(test)]
    pub(crate) fn listener(id: u16, graph: u32, initial_state: Option<u16>) -> Self {
        Self {
            run_startup: false,
            initial_state,
            ..Self::new(id, graph)
        }
    }
}

/// Initial body bindings use their asset order. Entries run after all roots exist.
pub fn body_roots(body: &Body) -> Result<Vec<Root>, InitFault> {
    let mut roots = Vec::new();
    for (offset, binding) in body.graphs.iter().enumerate() {
        let Some(graph) = binding.graph else {
            continue;
        };
        let id = u16::try_from(offset + 1).map_err(|_| InitFault::InstanceIdExhausted)?;
        let mut root = Root::new(id, graph);
        for field in binding.sync.iter().flatten() {
            let key = field
                .get("m_0D09D2D9")
                .and_then(asset_guid)
                .map_or(0, |guid| guid as u16);
            let value = script_expr::evaluate(
                field.get("m_value").unwrap_or(&Json::Null),
                &mut EmptyContext,
            )
            .map_err(|error| InitFault::RootBinding { id, graph, error })?;
            root.variables.insert(u32::from(key), value);
        }
        roots.push(root);
    }
    Ok(roots)
}

/// Initial body bindings, then the manager and equipped weapon slots in asset order.
pub fn player_roots(body: &Body, graphs: &GraphStore) -> Result<Vec<Root>, InitFault> {
    let mut roots = body_roots(body)?;
    let mut id = roots.last().map_or(0, |root| root.id);
    let mut append = |graph, slot: Option<usize>| {
        id = id.checked_add(1).ok_or(InitFault::InstanceIdExhausted)?;
        let mut root = Root::new(id, graph);
        if let Some(slot) = slot {
            root.variables.insert(28, Value::Integer(slot as i64));
        }
        roots.push(root);
        Ok::<_, InitFault>(())
    };
    if let Some(manager) = body.manager {
        append(manager, None)?;
    }
    for (slot, graph) in body.weapons.iter().enumerate() {
        if let Some(graph) = graph {
            append(*graph, Some(slot))?;
        }
    }
    if let Some(root) = roots.iter().find(|root| graphs.graph(root.graph).is_none()) {
        return Err(InitFault::MissingPlayerGraph(root.graph));
    }
    Ok(roots)
}

const OWNER_HEALTH: u32 = 253;
const HUD_HEALTH: u32 = 1732;
const HUD_MAX_HEALTH: u32 = 1737;

pub fn player_entity_variables(body: &Body, health: f64, maximum_health: f64) -> Variables {
    let slots = body
        .weapons
        .iter()
        .map(|weapon| Value::Bool(weapon.is_some()))
        .collect();
    let first = body.weapons.iter().position(Option::is_some).unwrap_or(0);
    Variables::from([
        (29, Value::Array(slots)),
        (16_515, Value::Integer(first as i64)),
        (OWNER_HEALTH, Value::Float(health)),
        (HUD_HEALTH, Value::Float(health)),
        (HUD_MAX_HEALTH, Value::Float(maximum_health)),
        (342, Value::Float(0.0)),
    ])
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct Site {
    pub entity: u32,
    pub instance: u16,
    pub graph: u32,
    pub node: Option<u16>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CreationKind {
    Definition(u64),
    Hero {
        hero: u64,
        team: u8,
        yaw: f64,
        flags: [Value; 2],
        controller: Option<u64>,
        skin: Option<u64>,
    },
}

/// A fully evaluated authored request. The scene owns allocation and rollback.
#[derive(Clone, Debug, PartialEq)]
pub struct EntityCreation {
    pub position: [f64; 3],
    pub kind: CreationKind,
}

/// Evaluated PlayScript inputs for a loaded target component.
#[derive(Debug, PartialEq)]
pub struct PlayScript {
    pub policy: u8,
    pub overrides: Vec<(u64, Value)>,
    pub graph: u32,
    pub source: u32,
    pub parameters: Vec<(u64, Value)>,
    pub auto_retire: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AiWeaponOverride {
    pub target: u32,
    pub slot: i64,
    pub identifier: u64,
    pub enabled: bool,
    pub priority: f64,
    pub prefer_later: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AiControlOverride {
    pub target: u32,
    pub limits: Option<[f64; 2]>,
    pub priority: f64,
    pub prefer_later: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AiFacing {
    pub target: u32,
    pub direction: [f64; 3],
    pub priority: StackPriority,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AiBehaviorRequest {
    pub class: &'static str,
    pub priority: f64,
    pub prefer_later: bool,
    pub flag: bool,
    pub shared: [f64; 2],
    pub inputs: [f64; 5],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TargetTagEffect {
    pub entity: u32,
    pub tag: u64,
    pub present: bool,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct TargetTagSnapshot {
    base: Option<Arc<std::collections::BTreeSet<u64>>>,
    overrides: BTreeMap<u64, bool>,
}

impl TargetTagSnapshot {
    pub(crate) fn presence(&self, tag: u64) -> Option<bool> {
        self.overrides
            .get(&tag)
            .copied()
            .or_else(|| self.base.as_ref().map(|base| base.contains(&tag)))
    }
}

#[derive(Default)]
pub(crate) struct TargetTagEffects(BTreeMap<u64, TargetTagCounts>);

#[derive(Default)]
struct TargetTagCounts {
    grants: u32,
    suppressions: u32,
}

impl TargetTagEffects {
    pub(crate) fn snapshot(
        &self,
        base: Option<&std::collections::BTreeSet<u64>>,
    ) -> TargetTagSnapshot {
        TargetTagSnapshot {
            base: base.cloned().map(Arc::new),
            overrides: self
                .0
                .iter()
                .map(|(&tag, counts)| (tag, counts.suppressions == 0))
                .collect(),
        }
    }

    pub(crate) fn presence(&self, tag: u64) -> Option<bool> {
        self.0.get(&tag).map(|counts| counts.suppressions == 0)
    }

    pub(crate) fn apply(&mut self, effect: TargetTagEffect, active: bool) -> bool {
        let counts = if active {
            self.0.entry(effect.tag).or_default()
        } else {
            let Some(counts) = self.0.get_mut(&effect.tag) else {
                return false;
            };
            counts
        };
        // The authored removal can mask existing grants. A later grant's
        // precedence over an already-active suppression is still unknown.
        if active && effect.present && counts.suppressions != 0 {
            return false;
        }
        let count = if effect.present {
            &mut counts.grants
        } else {
            &mut counts.suppressions
        };
        let next = if active {
            count.checked_add(1)
        } else {
            count.checked_sub(1)
        };
        let Some(next) = next else {
            return false;
        };
        *count = next;
        if counts.grants == 0 && counts.suppressions == 0 {
            self.0.remove(&effect.tag);
        }
        true
    }
}

#[derive(Clone, Copy, Default)]
pub struct BeamContact {
    pub entity: Option<u32>,
    pub hit: bool,
}

/// External authored queries for this initialization. The caller owns mode,
/// ruleset, and entity data and can decline a query without guessing its value.
pub trait World {
    fn edit_velocity(&mut self, _site: Site, _frame: u32, _value: Value, _cap: bool) -> bool {
        false
    }
    fn summoner_beam_contact(&self, _site: Site) -> Option<BeamContact> {
        None
    }

    fn summoner_beam_damage(&mut self, _site: Site, _target: u32, _amount: f64) -> bool {
        false
    }
    fn body_display_name(&self, _site: Site, _entity: u32) -> Option<String> {
        None
    }
    fn objective_route_active(&self, _site: Site) -> Option<bool> {
        None
    }

    fn request_corpse_retirement(&mut self, _site: Site, _entity: u32, _phase_exit: bool) -> bool {
        false
    }
    /// None indicates that the host cannot resolve this entity's presence.
    fn entity_velocity(&self, _entity: u32) -> script_expr::VelocityQuery {
        script_expr::VelocityQuery::Unavailable
    }
    fn body_angle(&self, _entity: u32, _axis: script_expr::BodyAngle) -> script_expr::AngleQuery {
        script_expr::AngleQuery::Unavailable
    }
    fn entity_exists(&self, _entity: u32) -> Option<bool> {
        None
    }
    fn known_position(&self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
    fn emit_contact_effect(
        &mut self,
        _site: Site,
        _source: u32,
        _resource: u64,
        _targets: &[u32],
    ) -> bool {
        false
    }
    fn deadeye_targets(&self, _site: Site, _radius: f64, _angle: f64) -> Option<Vec<u32>> {
        None
    }

    fn deadeye_target_alive(&self, _site: Site, _entity: u32) -> bool {
        false
    }

    fn tracked_targets(
        &mut self,
        _site: Site,
        _origin: u32,
        _offset: [f64; 3],
        _radius: f64,
        _filter: &Json,
    ) -> Option<Vec<u32>> {
        None
    }
    fn witch_staff_targets(&self, _site: Site, _radius: f64) -> Option<Vec<u32>> {
        None
    }
    fn set_witch_staff_effect(&mut self, _site: Site, _effect: Option<(u32, f64)>) -> bool {
        false
    }
    fn heal_from_witch_staff(&mut self, _site: Site, _source: u32, _amount: f64) -> bool {
        false
    }
    fn self_damage(&mut self, _site: Site, _amount: f64) -> bool {
        false
    }
    fn recharge_shields(&mut self, _site: Site, _amount: f64) -> bool {
        false
    }
    fn explosion_damage(&mut self, _site: Site, _target: u32, _amount: f64) -> bool {
        false
    }
    /// Signed native index; -1 means known no team, None means unavailable.
    fn entity_team(&self, _site: Site, _entity: u32) -> Option<i8> {
        None
    }
    fn for_code_transport(&self, _site: Site) -> Option<bool> {
        None
    }
    fn code_changed(&mut self, _entity: u32, _token: u64) -> bool {
        false
    }
    /// Register/update a component-88 request, or remove its state-owned record.
    fn ai_behavior_request(
        &mut self,
        _site: Site,
        _record: AiBehaviorRequest,
        _active: bool,
    ) -> Option<bool> {
        None
    }
    fn entity_hero(&self, _site: Site, _entity: u32) -> Option<u64> {
        None
    }
    fn entity_definition(&self, _site: Site, _entity: u32) -> Option<u64> {
        None
    }
    fn entity_health(
        &self,
        site: Site,
        entity: u32,
        kind: Option<PoolKind>,
        maximum: bool,
    ) -> Option<f64> {
        (entity == site.entity)
            .then(|| self.body_health(site, kind, maximum))
            .flatten()
    }
    fn total_health(&self, site: Site, entity: u32) -> script_expr::HealthQuery {
        self.entity_health(site, entity, None, false).map_or(
            script_expr::HealthQuery::Unavailable,
            script_expr::HealthQuery::Present,
        )
    }
    fn outline_context_available(&self, _site: Site) -> Option<bool> {
        None
    }
    fn set_encounter_model_enabled(&mut self, _site: Site, _enabled: bool) -> bool {
        false
    }
    fn set_cinematic_aim_lock(&mut self, _site: Site, _active: bool) -> bool {
        false
    }
    fn set_captured_aim_lock(&mut self, _site: Site, _degrees: Option<[f64; 2]>) -> bool {
        false
    }
    fn set_same_team_contact_suppressed(&mut self, _site: Site, _suppressed: bool) -> bool {
        false
    }
    fn set_outgoing_damage_filter(&mut self, _site: Site, _adjustment: Option<f64>) -> bool {
        false
    }
    /// None is unavailable; false explicitly declares an absent voice context/manager.
    fn voice_context(&mut self, _site: Site) -> Option<bool> {
        None
    }
    fn voice_manager(&mut self, _site: Site) -> Option<bool> {
        None
    }
    fn voice_clock(&mut self, _site: Site, _now_ms: u64) -> Option<f64> {
        None
    }
    fn voice_stimulus(
        &mut self,
        _site: Site,
        _guid: u64,
    ) -> Option<Option<crate::script_voice::Stimulus>> {
        None
    }
    fn voice_allow(
        &mut self,
        _site: Site,
        _stimulus: crate::script_voice::Stimulus,
    ) -> Option<bool> {
        None
    }
    fn voice_actor_required(&self, _site: Site, _stimulus: crate::script_voice::Stimulus) -> bool {
        false
    }
    fn voice_submit(
        &mut self,
        _site: Site,
        _stimulus: crate::script_voice::Stimulus,
        _targets: &[u32],
        _actor: Option<&Value>,
    ) -> Result<Option<u32>, &'static str> {
        Ok(None)
    }
    fn voice_duration(
        &mut self,
        _site: Site,
        _handle: u32,
        _guid: u64,
        _mode: bool,
    ) -> Option<f64> {
        None
    }
    fn voice_part(&mut self, _site: Site, _number: u16) -> Option<f64> {
        None
    }

    fn play_boss_intro_voice(&mut self, _site: Site, _cue: IntroCue) -> Option<u64> {
        None
    }
    fn play_victory_voice(&mut self, _site: Site, _actor: &Value) -> Option<u64> {
        None
    }
    fn play_gate_voice(
        &mut self,
        _site: Site,
        _stimulus: u64,
        _attacker: Option<u32>,
    ) -> Option<u64> {
        None
    }
    /// Deliver an authored message whose recipients are chosen by the scene.
    fn route_game_message(&mut self, _site: Site, _message: u64, _parameters: &Variables) -> bool {
        false
    }
    fn queue_mode_action(&mut self, _site: Site) -> bool {
        false
    }
    /// Availability of the native auxiliary view owner; false is known absence.
    fn spatial_binding_owner_available(&self, _site: Site) -> Option<bool> {
        None
    }
    fn player_stats_available(&self, _site: Site) -> Option<bool> {
        None
    }
    fn local_combat_context_available(&self, _site: Site) -> Option<bool> {
        None
    }
    fn set_ai_control_setting(&mut self, _site: Site, _scalar: f32, _flag: bool) -> bool {
        false
    }
    fn ai_control_override(
        &mut self,
        _site: Site,
        _record: AiControlOverride,
        _active: bool,
    ) -> Option<bool> {
        None
    }
    fn ai_facing(&mut self, _site: Site, _record: AiFacing, _active: bool) -> Option<bool> {
        None
    }
    /// None: unavailable; false: the declared controller has no matching slot;
    /// true: the record was installed and must be removed on state teardown.
    fn ai_weapon_override(
        &mut self,
        _site: Site,
        _record: AiWeaponOverride,
        _active: bool,
    ) -> Option<bool> {
        None
    }
    /// Bind a new entity synchronously, or return no entity without changing
    /// the output variable. Implementations own fresh IDs and atomic rollback;
    /// no partially initialized body may be returned or published on failure.
    fn create_entity(
        &mut self,
        _site: Site,
        _request: &EntityCreation,
    ) -> Result<Option<u32>, String> {
        Err("entity creation service is unavailable".into())
    }

    fn play_script(&mut self, _site: Site, _target: u32, _request: PlayScript) -> bool {
        false
    }

    fn creation_position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
    fn ruleset(&mut self, _id: u32) -> Option<Value> {
        None
    }

    /// Some(Null) is a known unset variable. None means the query is unavailable.
    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value>;

    /// None means unavailable; a known entity at the origin returns Some([0.0; 3]).
    fn position(&mut self, entity: u32) -> Option<[f64; 3]>;

    fn forward(&self, _entity: u32) -> Option<[f64; 3]> {
        None
    }

    /// The implicit owner's movement sample, if the world knows all its bits.
    fn movement_sample(&self, _site: Site) -> Option<u16> {
        None
    }

    fn engine_setting(&self, _site: Site, _key: u32) -> script_expr::EngineSetting {
        script_expr::EngineSetting::Unavailable
    }

    fn native(&mut self, _site: Site, _config: &Json) -> Option<Value> {
        None
    }

    fn native_uses_initial(&self, _site: Site, _config: &Json) -> bool {
        false
    }

    fn body_scale(&mut self, _site: Site, _target: u32, _override_value: &Value) -> Option<f64> {
        None
    }

    /// A missing kind reads the total across all pools.
    fn body_health(&self, _site: Site, _kind: Option<PoolKind>, _maximum: bool) -> Option<f64> {
        None
    }

    fn add_health_pool(
        &mut self,
        _site: Site,
        _kind: PoolKind,
        _maximum: f64,
        _current: f64,
    ) -> Option<u32> {
        None
    }

    fn health_pool_current(&self, _site: Site, _pool: u32) -> Option<f64> {
        None
    }

    fn remove_health_pool(&mut self, _site: Site, _pool: u32) -> Option<f64> {
        None
    }
    /// The inner None denotes a live component whose body has already died.
    fn add_wave_health(&mut self, _site: Site, _amount: f64) -> Option<Option<u32>> {
        None
    }

    fn has_target_tag(&self, _site: Site, _entity: u32, _tag: u64) -> Option<bool> {
        None
    }

    /// Ordered bound combat bodies for the supported scene query.
    fn combat_targets(&self, _site: Site, _config: &Json) -> Option<Vec<u32>> {
        None
    }

    /// A tag query for a unique scene placement, independent of live body tags.
    fn static_target(&self, _tag: u64) -> Option<u32> {
        None
    }

    fn targets_with_tag(&self, _site: Site, _tag: u64, _excluded: u64) -> Option<Vec<u32>> {
        None
    }

    fn tagged_placements(&self, _site: Site, _tag: u64) -> Option<&[u32]> {
        None
    }

    fn participant_targets(&self, _site: Site, _filter: &Json) -> Option<Vec<u32>> {
        None
    }

    fn encounter_body_alive(&self, _entity: u32) -> Option<bool> {
        None
    }

    fn assign_team(&mut self, _site: Site, _entity: u32, _team: i8) -> bool {
        false
    }

    fn team_members(&self, _team: u8) -> Option<u32> {
        None
    }

    fn set_target_tag_effect(
        &mut self,
        _site: Site,
        _effect: TargetTagEffect,
        _active: bool,
    ) -> bool {
        false
    }

    fn scene_filter_policy(
        &self,
        _site: Site,
        _filter: &Json,
    ) -> Option<script_expr::SceneFilterPolicy> {
        None
    }
    fn entity_filter_flags(&self, _site: Site, _entity: u32) -> Option<u64> {
        None
    }
    fn entities_hostile(
        &self,
        _site: Site,
        _source: u32,
        _target: u32,
        _option: bool,
    ) -> Option<bool> {
        None
    }

    fn body_on_fire(&mut self, _site: Site, _entity: u32) -> Option<OnFire> {
        None
    }

    fn entity_component92(&self, _site: Site, _entity: u32) -> Option<bool> {
        None
    }

    /// None means this relationship is unsupported for the supplied entity.
    fn related_entity(
        &mut self,
        _site: Site,
        _relation: EntityRelation,
        _entity: u32,
    ) -> Option<u32> {
        None
    }

    /// Some(0) denotes a supported lookup with no linked entity in this slot.
    fn linked_entity(&mut self, _site: Site, _entity: u32, _slot: u32) -> Option<u32> {
        None
    }

    /// None leaves an encountered AI candidate explicitly unsupported.
    fn set_display_name(&mut self, _site: Site, _entity: u32, _name: u64, _active: bool) -> bool {
        false
    }

    fn selects_ai_behavior(&mut self, _site: Site) -> Option<bool> {
        None
    }

    /// The world owns delivery outside the current body.
    fn send_message(
        &mut self,
        _source: Site,
        _target: u32,
        _message: u64,
        _parameters: &Variables,
    ) -> bool {
        false
    }
}

#[derive(Default)]
pub struct EmptyWorld;

impl World for EmptyWorld {
    fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }

    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum StateKind {
    RollMovementParameter,
    ExplosionContact,
    TrackedTargets,
    DeadeyeEffects,
    WitchStaffEffect,
    WitchStaffHealing,
    SummonerBeamRay,
    SummonerBeamDamage,
    ModifyHealth,
    ShieldRecharge,
    ForCode,
    AiBehavior {
        record: Option<AiBehaviorRequest>,
    },
    AiControlSetting,
    AiControlOverride {
        record: Option<AiControlOverride>,
    },
    AiFacing(Option<AiFacing>),
    Plain,
    HealthPool {
        pool: Option<u32>,
    },
    HealthCapacity,
    AiWeaponOverride {
        record: Option<AiWeaponOverride>,
    },
    TargetTag {
        effect: Option<TargetTagEffect>,
    },
    DisplayName {
        binding: Option<(u32, u64)>,
    },
    BooleanSwitch(bool),
    DelayedSwitch {
        current: bool,
        undecided: bool,
    },
    Switch {
        case: Option<usize>,
    },
    ActiveQuery,
    GamePadVibration,
    SubScript {
        child: Option<u16>,
    },
    PhaseScriptList,
    PhaseScript,
    PhaseTransition,
    MessageIdle,
    BossPauseReceiver {
        receiving: bool,
    },
    Registered {
        next: Option<(u16, u16)>,
    },
    Wait,
    WaitFrames {
        target: u32,
    },
    CinematicTimeScale {
        boundary_frame: u32,
    },
    AiCandidate {
        selected: bool,
    },
    LogicalButton {
        down: bool,
    },
    ButtonBlock(Option<u8>),
    Stack {
        top: bool,
        under: bool,
    },
    Ability {
        flags: u8,
    },
    MovementMod,
    Defer,
    VoiceStimulus,
    BossIntroVoice(IntroCue),
    GateVoice(u64),
    VictoryVoice,
    SameTeamContact {
        registered: bool,
    },
    OutgoingDamageFilter {
        registered: bool,
    },
    MovementSample(u16),
    Anim,
    Effect,
    ClientPulse {
        count: u8,
        fresh_at: Option<u64>,
    },
    EmptyTargets,
    SendMessage,
    PersistentSend(u64),
    CosmeticEntity,
    ChaseVar,
    Watch,
    SpawnSearch,
    RecentInput,
    LivingBodyArray,
    WeaponVolley {
        volleys: u8,
        offset_ms: u64,
        ammo0: i64,
        ammo_var: Option<Variable>,
    },
}

#[derive(Clone, Copy, Debug)]
struct BufferedPress {
    time_ms: u64,
    activation: u16,
}

#[derive(Clone, Debug)]
pub struct State {
    pub tracked_targets: Vec<u32>,
    deadeye_children: BTreeMap<u32, u16>,
    pub active: bool,
    pub activation: u16,
    pub start_ms: u64,
    pub kind: StateKind,
    pub(crate) voice: Option<Box<crate::script_voice::State>>,
    pub chase: Option<ChasePayload>,
    pub ability_cooldown: Option<AbilityCooldown>,
    watch_seen: Option<Value>,
    spawn_search: Option<SpawnSearch>,
    recent_inputs: Vec<(usize, u64)>,
    ending: bool,
    pending_change: bool,
    pending_timer: bool,
    delayed_timers: [bool; 2],
    buffered_press: Option<BufferedPress>,
    linked_variable: Option<VarKey>,
    ability_stack: Option<VarKey>,
    untrusted_native: bool,
}

impl State {
    pub(crate) fn ability_running(&self) -> bool {
        self.active
            && matches!(self.kind, StateKind::Ability { flags } if flags & ABILITY_RUNNING != 0)
    }

    /// Whether a watched evaluation for this state has ever read a native
    /// outside the reference's client-agreed set.
    pub fn reads_untrusted_native(&self) -> bool {
        self.untrusted_native
    }

    fn dormant(kind: StateKind) -> Self {
        Self {
            tracked_targets: Vec::new(),
            deadeye_children: BTreeMap::new(),
            active: false,
            activation: 0,
            start_ms: 0,
            kind,
            voice: None,
            chase: None,
            ability_cooldown: None,
            watch_seen: None,
            spawn_search: None,
            recent_inputs: Vec::new(),
            ending: false,
            pending_change: false,
            pending_timer: false,
            delayed_timers: [false; 2],
            buffered_press: None,
            linked_variable: None,
            ability_stack: None,
            untrusted_native: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct SpawnSearchKey {
    instance: u16,
    raw: u16,
    activation: u16,
    start_ms: u64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct SpawnSearch {
    pub position: [f64; 3],
    pub direction: [f64; 3],
    pub radius: f64,
    pub half_angle: f64,
    pub minimum: usize,
    pub maximum: usize,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AbilityCooldown {
    pub current: f64,
    pub rate: f64,
    pub last_ms: u64,
}

impl AbilityCooldown {
    fn remaining(self, now_ms: u64) -> f64 {
        let elapsed = (now_ms.saturating_sub(self.last_ms) as f64 * -f64::from(0.001_f32)) as f32;
        let consumed = (f64::from(elapsed) * self.rate) as f32;
        f64::from(((f64::from(consumed) + self.current) as f32).max(0.0))
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ChasePayload {
    pub current: Value,
    pub reached: bool,
    pub remaining_ms: Option<i64>,
    pub last_ms: u64,
}

#[derive(Clone, Debug)]
pub struct Instance {
    pub graph: u32,
    start_ms: u64,
    retire_after_frame: Option<u32>,
    /// Parent instance and raw state index, as carried on the wire.
    pub parent: Option<(u16, u16)>,
    pub variables: Variables,
    pub states: IndexMap<u16, State>,
    join_inputs: BTreeMap<u16, HashSet<(u16, String)>>,
}

#[derive(Debug, Eq, PartialEq)]
pub enum Issue {
    MissingGraph(u32),
    MissingNode(u16),
    MissingState(u16),
    UnsupportedAction(String),
    UnsupportedState(String),
    UnsupportedField(&'static str),
    EntityCreation(String),
    InvalidSubScript,
    Expression(EvalError),
}

#[derive(Debug, Eq, PartialEq)]
pub struct Diagnostic {
    pub site: Site,
    pub issue: Issue,
}

#[derive(Debug)]
pub struct Snapshot {
    pub entity: u32,
    pub(crate) now_ms: u64,
    pub variables: Variables,
    pub instances: IndexMap<u16, Instance>,
    /// Pending state timers and finishes in their per-instance execution order.
    pub pending_timers: IndexMap<u16, Vec<PendingTimer>>,
    /// Stack links by (instance or zero for the entity, variable).
    pub bindings: IndexMap<(u16, u32), Vec<StackBinding>>,
    /// Effective linked values, separate from their stored bases.
    pub resolved_bindings: IndexMap<(u16, u32), Value>,
    pub diagnostics: Vec<Diagnostic>,
}

impl Snapshot {
    pub(crate) fn boss_pause_active(&self) -> bool {
        self.instances.values().any(|instance| {
            let senders: &[u16] = match instance.graph {
                0x0ce0 => &[8],
                0x0c8c => &[25, 31],
                _ => return false,
            };
            senders.iter().any(|raw| {
                instance.states.get(raw).is_some_and(|state| {
                    state.active && state.kind == StateKind::PersistentSend(BOSS_PAUSE)
                })
            })
        })
    }

    /// The reached initialization path was supported. This does not check
    /// dormant paths, later input, timers, messages, or gameplay.
    pub fn initialization_complete(&self) -> bool {
        self.diagnostics.is_empty()
    }
}

#[derive(Debug, Eq, PartialEq)]
pub enum InitFault {
    InvalidBudget,
    InvalidRoot(u16),
    InstanceIdExhausted,
    MissingPlayerGraph(u32),
    RootBinding {
        id: u16,
        graph: u32,
        error: EvalError,
    },
    WorkBudget {
        limit: usize,
        operation: &'static str,
    },
    EventBudget {
        instance: u16,
    },
    PendingEvents {
        limit: usize,
    },
    PendingShots,
    FrameOrder {
        frame: u32,
        now_ms: u64,
    },
    Halted,
    PlugDepth,
}

impl fmt::Display for InitFault {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidBudget => {
                f.write_str("statescript initialization budget must be positive")
            }
            Self::InvalidRoot(id) => write!(f, "invalid or duplicate statescript root {id}"),
            Self::InstanceIdExhausted => {
                f.write_str("statescript initialization instance IDs exhausted")
            }
            Self::MissingPlayerGraph(graph) => {
                write!(f, "player statescript graph {graph:04X} is missing")
            }
            Self::RootBinding { id, graph, error } => {
                write!(
                    f,
                    "statescript root {id} graph {graph:04X} binding failed: {error}"
                )
            }
            Self::WorkBudget { limit, operation } => {
                write!(
                    f,
                    "statescript initialization work budget {limit} exhausted during {operation}"
                )
            }
            Self::EventBudget { instance } => {
                write!(
                    f,
                    "statescript initialization event budget exhausted in instance {instance}"
                )
            }
            Self::PendingEvents { limit } => {
                write!(f, "statescript pending event limit {limit} exceeded")
            }
            Self::PendingShots => f.write_str("statescript shot queue limit exceeded"),
            Self::FrameOrder { frame, now_ms } => {
                write!(f, "statescript frame {frame} starts before {now_ms} ms")
            }
            Self::Halted => f.write_str("statescript component halted after a failed operation"),
            Self::PlugDepth => f.write_str("statescript initialization plug depth exceeded"),
        }
    }
}

impl Error for InitFault {}

impl From<QueueFull> for InitFault {
    fn from(error: QueueFull) -> Self {
        Self::PendingEvents { limit: error.limit }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PendingTimer {
    pub due_ms: u64,
    pub raw_state: u16,
    pub finish: bool,
    pub parameter: u16,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StackBinding {
    pub instance: u16,
    pub raw_state: u16,
    pub priority: StackPriority,
    pub slot: u8,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ShotEvent {
    pub entity: u32,
    pub instance: u16,
    pub graph: u32,
    pub raw_state: u16,
    pub frame: u32,
    pub time_ms: u64,
    pub activation: u16,
    pub activation_ms: u64,
    pub ordinal: u32,
}

/// A single body using the same authored graph engine as one-shot initialization.
pub struct Component {
    graphs: Arc<GraphStore>,
    machine: Machine,
    halted: bool,
}

impl Component {
    pub(crate) fn teardown(&mut self, world: &mut impl World) -> Result<(), InitFault> {
        self.run(world, |vm| {
            let roots = vm
                .machine
                .snapshot
                .instances
                .iter()
                .filter_map(|(&id, instance)| instance.parent.is_none().then_some(id))
                .collect::<Vec<_>>();
            for root in roots {
                vm.destroy(root)?;
            }
            vm.notify_code(1)
        })
    }
    pub fn register_code_observer(&mut self, token: u64, identifier: Option<u64>) {
        if !self.machine.code_observers.contains(&(token, identifier)) {
            self.machine.code_observers.push((token, identifier));
        }
    }

    pub fn unregister_code_observer(&mut self, token: u64) {
        self.machine
            .code_observers
            .retain(|(registered, _)| *registered != token);
    }

    pub fn code_value(
        &mut self,
        identifier: u64,
        initial: Value,
        world: &mut impl World,
    ) -> Result<(bool, Value), InitFault> {
        let mut result = None;
        self.run(world, |vm| {
            result = Some(vm.code_value(identifier, initial)?);
            Ok(())
        })?;
        Ok(result.expect("completed code value operation"))
    }
    pub fn start(
        graphs: Arc<GraphStore>,
        entity: u32,
        entity_variables: Variables,
        roots: Vec<Root>,
        frame: u32,
        world: &mut impl World,
        max_work: usize,
    ) -> Result<Self, InitFault> {
        Self::start_with_options(
            graphs,
            entity,
            entity_variables,
            roots,
            frame,
            world,
            (max_work, false),
        )
    }

    pub fn start_with_public_schema(
        graphs: Arc<GraphStore>,
        entity: u32,
        entity_variables: Variables,
        roots: Vec<Root>,
        frame: u32,
        world: &mut impl World,
        max_work: usize,
    ) -> Result<Self, InitFault> {
        Self::start_with_options(
            graphs,
            entity,
            entity_variables,
            roots,
            frame,
            world,
            (max_work, true),
        )
    }

    fn start_with_options(
        graphs: Arc<GraphStore>,
        entity: u32,
        entity_variables: Variables,
        roots: Vec<Root>,
        frame: u32,
        world: &mut impl World,
        (max_work, public_schemas): (usize, bool),
    ) -> Result<Self, InitFault> {
        let mut machine = Machine::new(entity, entity_variables, frame, max_work)?;
        machine.public_schemas = public_schemas;
        Vm::new(&graphs, world, &mut machine).start(roots)?;
        machine.capture_outputs();
        Ok(Self {
            graphs,
            machine,
            halted: false,
        })
    }

    pub fn snapshot(&self) -> Option<&Snapshot> {
        (!self.halted).then_some(&self.machine.snapshot)
    }

    pub(crate) fn graph_store(&self) -> &GraphStore {
        &self.graphs
    }

    pub fn now_ms(&self) -> u64 {
        self.machine.now_ms
    }

    pub(crate) fn spawn_searches(
        &self,
    ) -> impl Iterator<Item = (SpawnSearchKey, SpawnSearch)> + '_ {
        self.snapshot()
            .into_iter()
            .flat_map(|snapshot| &snapshot.instances)
            .flat_map(|(&instance, state)| {
                state.states.iter().filter_map(move |(&raw, state)| {
                    let search = state.spawn_search.filter(|_| state.active)?;
                    Some((
                        SpawnSearchKey {
                            instance,
                            raw,
                            activation: state.activation,
                            start_ms: state.start_ms,
                        },
                        search,
                    ))
                })
            })
    }

    pub(crate) fn queue_spawn_result(
        &mut self,
        frame: u32,
        key: SpawnSearchKey,
        positions: Vec<[f64; 3]>,
    ) -> Result<(), InitFault> {
        if self.halted {
            return Err(InitFault::Halted);
        }
        let active = self
            .machine
            .snapshot
            .instances
            .get(&key.instance)
            .and_then(|instance| instance.states.get(&key.raw))
            .is_some_and(|state| {
                state.active
                    && state.activation == key.activation
                    && state.start_ms == key.start_ms
                    && state.spawn_search.is_some()
            });
        if !active {
            return Ok(());
        }
        let due_ms = frame_start(frame);
        if due_ms < self.machine.now_ms {
            return Err(InitFault::FrameOrder {
                frame,
                now_ms: self.machine.now_ms,
            });
        }
        self.machine
            .queues
            .push(key.instance, due_ms, Event::SpawnResult { key, positions })?;
        Ok(())
    }

    /// Consume reports only after the body graph path is ready for authority.
    pub fn take_shots(&mut self) -> Option<Vec<ShotEvent>> {
        let shots = std::mem::take(&mut self.machine.shots);
        self.snapshot()?.initialization_complete().then_some(shots)
    }

    pub fn shot_node(&self, shot: &ShotEvent) -> Option<&Node> {
        let snapshot = self.snapshot()?;
        let instance = snapshot.instances.get(&shot.instance)?;
        if snapshot.entity != shot.entity || instance.graph != shot.graph {
            return None;
        }
        self.graphs
            .graph(shot.graph)?
            .state(usize::from(shot.raw_state))
            .filter(|node| node.class == "STUStatescriptStateWeaponVolley")
    }

    pub fn evaluate_shot_config(
        &self,
        shot: &ShotEvent,
        config: &Json,
        world: &mut impl World,
    ) -> Result<Value, EvalError> {
        self.shot_node(shot)
            .ok_or(EvalError::MalformedConfig("shot source"))?;
        self.evaluate_state_config(shot.instance, shot.raw_state, config, world)
    }

    pub fn evaluate_state_config(
        &self,
        instance: u16,
        raw_state: u16,
        config: &Json,
        world: &mut impl World,
    ) -> Result<Value, EvalError> {
        let snapshot = self
            .snapshot()
            .ok_or(EvalError::MalformedConfig("state source"))?;
        let graph = snapshot
            .instances
            .get(&instance)
            .ok_or(EvalError::MalformedConfig("state source"))?
            .graph;
        let node = self
            .graphs
            .graph(graph)
            .and_then(|graph| graph.state(usize::from(raw_state)))
            .ok_or(EvalError::MalformedConfig("state source"))?;
        let mut context = InstanceContext {
            machine: &self.machine,
            world,
            site: Site {
                entity: snapshot.entity,
                instance,
                graph,
                node: Some(node.pos),
            },
            reads: Vec::new(),
            foreign_reads: Vec::new(),
            parameters: None,
            watcher: None,
            read_native: false,
            untrusted_native: false,
        };
        script_expr::evaluate(config, &mut context)
    }

    pub fn resolved_variable(&self, instance: u16, variable: Variable) -> Option<Value> {
        (!self.halted).then(|| self.machine.value(source_key(instance, variable), None))
    }

    pub(crate) fn permits_entity_stack(&self, id: u32, priority: StackPriority) -> Option<bool> {
        (!self.halted).then(|| {
            self.machine
                .allows_stack(VarKey::Entity(id), priority, None)
        })
    }

    pub fn set_entity_variable(
        &mut self,
        id: u32,
        value: Value,
        world: &mut impl World,
    ) -> Result<(), InitFault> {
        self.run(world, |vm| {
            let key = VarKey::Entity(id);
            let old = vm.machine.value(key, None);
            vm.set_base(key, value);
            vm.notify_value(key, &old, None)
        })
    }

    pub(crate) fn set_instance_variable(
        &mut self,
        instance: u16,
        id: u32,
        value: Value,
        world: &mut impl World,
    ) -> Result<(), InitFault> {
        self.run(world, |vm| {
            let key = VarKey::Instance(instance, id);
            let old = vm.machine.value(key, None);
            vm.set_base(key, value);
            vm.notify_value(key, &old, None)
        })
    }

    /// Read the rows published by state lifecycle and dependency events.
    /// Authoritative modifiers require a fully supported runtime path.
    pub fn movement_modifiers(
        &mut self,
        _world: &mut impl World,
    ) -> Result<Option<BTreeMap<ModifierKey, Modifier>>, InitFault> {
        if self.halted {
            return Err(InitFault::Halted);
        }
        Ok(self
            .machine
            .snapshot
            .initialization_complete()
            .then(|| self.machine.movement_modifiers.clone()))
    }

    /// Queue the authored entries of one instance at the current clock.
    pub fn queue_message(
        &mut self,
        instance: u16,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        self.queue_message_at(self.machine.now_ms, instance, message, parameters)
    }

    pub(crate) fn queue_frame_message(
        &mut self,
        frame: u32,
        instance: u16,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        let due_ms = frame_start(frame);
        if due_ms < self.machine.now_ms {
            return Err(InitFault::FrameOrder {
                frame,
                now_ms: self.machine.now_ms,
            });
        }
        self.queue_message_at(due_ms, instance, message, parameters)
    }

    /// Publish a real foreign component slot change at the room frame boundary.
    pub(crate) fn queue_foreign_variable_change(
        &mut self,
        frame: u32,
        entity: u32,
        variable: u32,
    ) -> Result<(), InitFault> {
        let due = frame_start(frame);
        if due < self.machine.now_ms {
            return Err(InitFault::FrameOrder {
                frame,
                now_ms: self.machine.now_ms,
            });
        }
        if self.halted {
            return Err(InitFault::Halted);
        }
        let watchers = self
            .machine
            .foreign_watchers
            .get(&(entity, variable))
            .cloned()
            .unwrap_or_default();
        for key in watchers {
            let Some(state) = self
                .machine
                .snapshot
                .instances
                .get_mut(&key.instance)
                .and_then(|instance| instance.states.get_mut(&key.raw))
                .filter(|state| state.active && !state.ending && !state.pending_change)
            else {
                continue;
            };
            self.machine
                .queues
                .push(key.instance, due, Event::Changed(key.raw))?;
            state.pending_change = true;
        }
        Ok(())
    }

    pub(crate) fn queue_frame_message_from(
        &mut self,
        frame: u32,
        sender: u32,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        let due_ms = frame_start(frame);
        if due_ms < self.machine.now_ms {
            return Err(InitFault::FrameOrder {
                frame,
                now_ms: self.machine.now_ms,
            });
        }
        if self.halted {
            return Err(InitFault::Halted);
        }
        self.machine.queue_entries_from(
            &self.graphs,
            None,
            Some(sender),
            message,
            parameters,
            due_ms,
        )
    }

    fn queue_message_at(
        &mut self,
        due_ms: u64,
        instance: u16,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        if self.halted {
            return Err(InitFault::Halted);
        }
        self.machine
            .queue_entries(&self.graphs, Some(instance), message, parameters, due_ms)
    }

    pub fn run_now(&mut self, world: &mut impl World) -> Result<(), InitFault> {
        self.run(world, |vm| vm.run())
    }

    pub(crate) fn refresh_boss_pause(
        &mut self,
        world: &mut impl World,
        room_pause: bool,
    ) -> Result<(), InitFault> {
        let snapshot = &self.machine.snapshot;
        let pause = room_pause || snapshot.boss_pause_active();
        let receivers: Vec<_> = snapshot.instances.iter().filter_map(|(&id, instance)| {
            let raw = 56;
            let state = instance.states.get(&raw)?;
            (instance.graph == 0x0cbc
                && state.active
                && matches!(state.kind, StateKind::BossPauseReceiver { receiving } if receiving != pause))
                .then_some(StateKey { instance: id, raw })
        }).collect();
        if receivers.is_empty() {
            return Ok(());
        }
        self.run(world, |vm| {
            for key in receivers {
                vm.spend("Boss pause receiver")?;
                let node = vm
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("Boss pause receiver");
                let (exit, enter) = if pause {
                    ("m_33DA5163", "m_452862E6")
                } else {
                    ("m_452862E6", "m_33DA5163")
                };
                vm.exit_subgraph(key.instance, node, exit, 1)?;
                vm.state_mut(key).expect("active Boss pause receiver").kind =
                    StateKind::BossPauseReceiver { receiving: pause };
                vm.follow(key.instance, node, enter, Some(key), 0)?;
            }
            vm.run()
        })
    }

    pub(crate) fn receive_play_script(
        &mut self,
        caller: Site,
        request: &PlayScript,
        world: &mut impl World,
    ) -> Result<(), InitFault> {
        self.run(world, |vm| {
            vm.start_loaded_script(caller, request)?;
            vm.run()
        })
    }

    pub(crate) fn attach_witch_staff(
        &mut self,
        source: u32,
        amount: f64,
        world: &mut impl World,
    ) -> Result<u16, InitFault> {
        let id = u16::try_from(self.machine.next_id).map_err(|_| InitFault::InstanceIdExhausted)?;
        self.run(world, |vm| {
            let variables = Variables::from([(SCRIPT_SOURCE, Value::Entity(source))]);
            vm.create(id, 0x0079, None, variables, Startup::Ordinary)?;
            vm.machine.queue_entries_from(
                vm.graphs,
                Some(id),
                Some(source),
                0x0240_0000_0000_000f,
                Variables::from([(42, Value::Float(amount))]),
                vm.machine.now_ms,
            )?;
            vm.run()
        })?;
        Ok(id)
    }

    pub(crate) fn detach_witch_staff(
        &mut self,
        id: u16,
        world: &mut impl World,
    ) -> Result<(), InitFault> {
        self.run(world, |vm| {
            if vm
                .machine
                .snapshot
                .instances
                .get(&id)
                .is_some_and(|instance| instance.graph == 0x0079)
            {
                vm.destroy(id)?;
            }
            vm.run()
        })
    }

    pub(crate) fn queue_entity_message(
        &mut self,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        self.queue_entity_message_at(None, message, parameters)
    }

    pub(crate) fn queue_entity_message_from(
        &mut self,
        sender: u32,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        if !script_expr::valid_entity(sender) {
            return self.queue_entity_message(message, parameters);
        }
        self.queue_entity_message_at(Some(sender), message, parameters)
    }

    fn queue_entity_message_at(
        &mut self,
        sender: Option<u32>,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        if self.halted {
            return Err(InitFault::Halted);
        }
        self.machine.queue_entries_from(
            &self.graphs,
            None,
            sender,
            message,
            parameters,
            self.machine.now_ms,
        )
    }

    pub(crate) fn queue_entity_frame_message(
        &mut self,
        frame: u32,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, InitFault> {
        let due_ms = frame_start(frame);
        if self.halted {
            return Err(InitFault::Halted);
        }
        if due_ms < self.machine.now_ms {
            return Err(InitFault::FrameOrder {
                frame,
                now_ms: self.machine.now_ms,
            });
        }
        self.machine
            .queue_entries(&self.graphs, None, message, parameters, due_ms)
    }

    pub(crate) fn gate_approach_instance(&self) -> Option<u16> {
        let snapshot = self.snapshot()?;
        if !snapshot.initialization_complete() {
            return None;
        }
        snapshot.instances.iter().find_map(|(&id, instance)| {
            (instance.graph == 0x0c87 && instance.retire_after_frame.is_none()).then_some(id)
        })
    }

    pub(crate) fn queue_gate_arrival(
        &mut self,
        frame: u32,
        instance: u16,
    ) -> Result<(), InitFault> {
        let due_ms = frame_start(frame);
        if due_ms < self.machine.now_ms {
            return Err(InitFault::FrameOrder {
                frame,
                now_ms: self.machine.now_ms,
            });
        }
        self.machine
            .queues
            .push(instance, due_ms, Event::Entry(20))?;
        Ok(())
    }

    pub(crate) fn has_polled_voice(&self) -> bool {
        !self.machine.voice_poll.is_empty()
    }

    /// Explicit +80 phase, separate from ordinary graph ticks and deferred callbacks.
    pub fn poll_voice(&mut self, world: &mut impl World) -> Result<(), InitFault> {
        self.run(world, |vm| vm.poll_voice())
    }

    pub fn notify_voice_failure(
        &mut self,
        instance: u16,
        raw: u16,
        world: &mut impl World,
    ) -> Result<bool, InitFault> {
        let mut notified = false;
        self.run(world, |vm| {
            notified = vm.notify_voice_failure(StateKey { instance, raw })?;
            Ok(())
        })?;
        Ok(notified)
    }

    pub fn advance_frame(&mut self, frame: u32, world: &mut impl World) -> Result<(), InitFault> {
        self.advance(frame, None, world)
    }

    pub fn advance_command(
        &mut self,
        command: Command,
        world: &mut impl World,
    ) -> Result<(), InitFault> {
        self.advance(command.frame, Some(command), world)
    }

    /// Interrupt through authored ability exit paths without treating a living
    /// controlled body as dead. Timers and passive script lifecycles keep running.
    pub(crate) fn set_control_blocked(
        &mut self,
        blocked: bool,
        world: &mut impl World,
    ) -> Result<(), InitFault> {
        if self.machine.control_blocked == blocked {
            return Ok(());
        }
        if !blocked {
            self.machine.control_blocked = false;
            return Ok(());
        }
        self.run(world, |vm| {
            vm.queue_buttons(Command {
                frame: vm.machine.frame,
                ..Default::default()
            })?;
            vm.run()?;
            vm.machine.control_blocked = true;
            vm.machine.held_buttons.clear();
            vm.machine.ability_presses.clear();
            let abilities: Vec<_> = vm
                .machine
                .snapshot
                .instances
                .iter()
                .flat_map(|(&instance, data)| {
                    data.states.iter().filter_map(move |(&raw, state)| {
                        (state.active && matches!(state.kind, StateKind::Ability { .. }))
                            .then_some(StateKey { instance, raw })
                    })
                })
                .collect();
            for key in abilities {
                let node = vm
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("active ability");
                vm.stop_ability(key, node, 0)?;
            }
            for data in vm.machine.snapshot.instances.values_mut() {
                for state in data.states.values_mut() {
                    state.buffered_press = None;
                }
            }
            vm.run()?;
            vm.machine.shots.clear();
            Ok(())
        })
    }

    pub fn set_owner_dead(&mut self, dead: bool) {
        self.machine.owner_dead = dead;
    }

    pub(crate) fn command_buttons(&self) -> u32 {
        self.machine.command_buttons
    }

    fn advance(
        &mut self,
        frame: u32,
        command: Option<Command>,
        world: &mut impl World,
    ) -> Result<(), InitFault> {
        if frame_start(frame) < self.machine.now_ms {
            return Err(InitFault::FrameOrder {
                frame,
                now_ms: self.machine.now_ms,
            });
        }
        self.run(world, |vm| vm.advance_frame(frame, command))
    }

    fn run<W: World>(
        &mut self,
        world: &mut W,
        operation: impl FnOnce(&mut Vm<'_, '_, '_, W>) -> Result<(), InitFault>,
    ) -> Result<(), InitFault> {
        if self.halted {
            return Err(InitFault::Halted);
        }
        self.machine.work_left = self.machine.max_work;
        let result = operation(&mut Vm::new(&self.graphs, world, &mut self.machine));
        self.halted = result.is_err();
        if result.is_ok() {
            self.machine.capture_outputs();
        }
        result
    }
}

pub fn initialize(
    graphs: &GraphStore,
    entity: u32,
    entity_variables: Variables,
    roots: Vec<Root>,
    world: &mut impl World,
    max_work: usize,
) -> Result<Snapshot, InitFault> {
    let mut machine = Machine::new(entity, entity_variables, 0, max_work)?;
    Vm::new(graphs, world, &mut machine).start(roots)?;
    machine.capture_outputs();
    Ok(machine.snapshot)
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum VarKey {
    Entity(u32),
    Instance(u16, u32),
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
struct StateKey {
    instance: u16,
    raw: u16,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum RegisteredClass {
    CinematicAim,
    EntityControl,
    State9028E035,
    State43994F1B,
    State93B8758C,
}

#[derive(Clone, Copy)]
struct RegisteredState {
    class: RegisteredClass,
    server_only: bool,
    suppresses_model: bool,
}

enum LinkValue {
    Fixed(Value),
    Volley(u8),
    Chase,
    Cooldown,
    AbilityStack,
}

struct VariableLink {
    state: StateKey,
    priority: StackPriority,
    output: LinkValue,
    stack: bool,
}

struct ArrayInsertion {
    target: VarKey,
    items: Vec<Value>,
    index: Option<usize>,
    unique: bool,
}

struct Volley {
    start_ms: u64,
    ammo0: i64,
    per_shot: i64,
    rate: f64,
    spread: f64,
    planned: u32,
    reported: u32,
    outputs: [Option<VarKey>; 3],
}

impl Volley {
    fn ammo_at(&self, now_ms: u64) -> i64 {
        let Some(elapsed) = now_ms.checked_sub(self.start_ms) else {
            return self.ammo0;
        };
        let seconds = f64::from(elapsed as f32 * 0.001_f32);
        let shots = ((seconds * self.rate) as f32).floor() as i64;
        self.ammo0
            .saturating_sub(self.per_shot.saturating_mul(shots.saturating_add(1)))
            .max(0)
    }

    fn output(&self, slot: u8, now_ms: u64) -> Value {
        match slot {
            0 => Value::Integer(self.ammo_at(now_ms)),
            1 => Value::Float(self.rate),
            2 => Value::Float(self.spread),
            _ => unreachable!("weapon volley output slot"),
        }
    }
}

#[derive(Clone, Copy)]
struct AbilityPress {
    button: u8,
    time_ms: u64,
    activation: u16,
}

enum Event {
    Entry(u16),
    Assign(VarKey, Value),
    ExitInstance,
    Begin(u16),
    Finish(u16),
    Abort(u16),
    Changed(u16),
    Reevaluate(u16),
    Timer(u16, u16),
    Button(ButtonEdge),
    AbilityCheck(u16),
    CheckTransitions(u16),
    WatchChanged(u16),
    SpawnResult {
        key: SpawnSearchKey,
        positions: Vec<[f64; 3]>,
    },
    Message {
        position: u16,
        sender: Option<u32>,
        parameters: Arc<Variables>,
    },
}

enum Startup {
    Passive,
    Ordinary,
    Played { auto_retire: bool },
}

impl Event {
    fn state(&self) -> Option<u16> {
        match *self {
            Self::SpawnResult { key, .. } => Some(key.raw),
            Self::Entry(_)
            | Self::Assign(..)
            | Self::ExitInstance
            | Self::Message { .. }
            | Self::Button(_) => None,
            Self::Begin(state)
            | Self::Finish(state)
            | Self::Abort(state)
            | Self::Changed(state)
            | Self::Reevaluate(state)
            | Self::AbilityCheck(state)
            | Self::CheckTransitions(state)
            | Self::WatchChanged(state)
            | Self::Timer(state, _) => Some(state),
        }
    }
}

struct Machine {
    public_schemas: bool,
    code_states: Vec<StateKey>,
    code_pending: [Vec<u64>; 2],
    code_observers: Vec<(u64, Option<u64>)>,
    snapshot: Snapshot,
    registered: IndexMap<StateKey, RegisteredState>,
    queues: Timeline<Event>,
    watchers: BTreeMap<VarKey, Vec<StateKey>>,
    foreign_watchers: BTreeMap<(u32, u32), Vec<StateKey>>,
    ticking: BTreeMap<u16, IndexMap<u16, u64>>,
    deferred: BTreeMap<u16, Vec<(f64, u16)>>,
    voice_poll: Vec<StateKey>,
    links: BTreeMap<VarKey, Vec<VariableLink>>,
    volleys: BTreeMap<StateKey, Volley>,
    movement_modifiers: BTreeMap<ModifierKey, Modifier>,
    shots: Vec<ShotEvent>,
    ability_presses: BTreeMap<StateKey, Vec<AbilityPress>>,
    next_id: u32,
    work_left: usize,
    max_work: usize,
    frame: u32,
    now_ms: u64,
    random_state: AtomicU64,
    command_buttons: u32,
    held_buttons: Vec<u8>,
    button_blocks: [usize; 256],
    owner_dead: bool,
    control_blocked: bool,
    disabled_start_entries: HashSet<(u16, u16)>,
}

impl Machine {
    fn random_u64(&self) -> u64 {
        // SplitMix64 keeps the component's stream reproducible across graph calls.
        let state = self
            .random_state
            .fetch_add(0x9e37_79b9_7f4a_7c15, Ordering::Relaxed)
            .wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mixed = (state ^ (state >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        let mixed = (mixed ^ (mixed >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        mixed ^ (mixed >> 31)
    }
    fn new(
        entity: u32,
        variables: Variables,
        frame: u32,
        max_work: usize,
    ) -> Result<Self, InitFault> {
        if max_work == 0 {
            return Err(InitFault::InvalidBudget);
        }
        Ok(Self {
            code_states: Vec::new(),
            code_pending: Default::default(),
            code_observers: Vec::new(),
            snapshot: Snapshot {
                entity,
                now_ms: frame_start(frame),
                variables,
                instances: IndexMap::new(),
                pending_timers: IndexMap::new(),
                bindings: IndexMap::new(),
                resolved_bindings: IndexMap::new(),
                diagnostics: Vec::new(),
            },
            queues: Timeline::new(MAX_PENDING_EVENTS),
            registered: IndexMap::new(),
            watchers: BTreeMap::new(),
            foreign_watchers: BTreeMap::new(),
            ticking: BTreeMap::new(),
            deferred: BTreeMap::new(),
            voice_poll: Vec::new(),
            links: BTreeMap::new(),
            volleys: BTreeMap::new(),
            movement_modifiers: BTreeMap::new(),
            shots: Vec::new(),
            ability_presses: BTreeMap::new(),
            next_id: 1,
            work_left: max_work,
            max_work,
            frame,
            now_ms: frame_start(frame),
            random_state: AtomicU64::new(u64::from(entity) << 32 | u64::from(frame)),
            command_buttons: 0,
            held_buttons: Vec::new(),
            button_blocks: [0; 256],
            owner_dead: false,
            control_blocked: false,
            disabled_start_entries: HashSet::new(),
            public_schemas: false,
        })
    }

    fn capture_outputs(&mut self) {
        self.snapshot.now_ms = self.now_ms;
        self.snapshot.pending_timers.clear();
        for &id in self.snapshot.instances.keys() {
            let timers: Vec<_> = self
                .queues
                .iter(id)
                .filter_map(|(due_ms, event)| {
                    let (raw_state, finish, parameter) = match event {
                        Event::Timer(raw, param) => (*raw, false, *param),
                        Event::Finish(raw) => (*raw, true, 0),
                        _ => return None,
                    };
                    Some(PendingTimer {
                        due_ms,
                        raw_state,
                        finish,
                        parameter,
                    })
                })
                .collect();
            if !timers.is_empty() {
                self.snapshot.pending_timers.insert(id, timers);
            }
        }
        self.snapshot.bindings.clear();
        self.snapshot.resolved_bindings.clear();
        for (&variable, entries) in &self.links {
            let key = match variable {
                VarKey::Entity(id) => (0, id),
                VarKey::Instance(instance, id) => (instance, id),
            };
            let Some(top) = entries.last() else { continue };
            let bindings: Vec<_> = entries
                .iter()
                .filter(|entry| entry.stack)
                .map(|entry| StackBinding {
                    instance: entry.state.instance,
                    raw_state: entry.state.raw,
                    priority: entry.priority,
                    slot: u8::from(matches!(entry.output, LinkValue::AbilityStack)),
                })
                .collect();
            if !bindings.is_empty() {
                self.snapshot.bindings.insert(key, bindings);
            }
            let resolved = self.link_value(top);
            self.snapshot.resolved_bindings.insert(key, resolved);
        }
    }

    fn queue_entries(
        &mut self,
        graphs: &GraphStore,
        recipient: Option<u16>,
        message: u64,
        parameters: Variables,
        due_ms: u64,
    ) -> Result<usize, InitFault> {
        self.queue_entries_from(graphs, recipient, None, message, parameters, due_ms)
    }

    fn queue_entries_from(
        &mut self,
        graphs: &GraphStore,
        recipient: Option<u16>,
        sender: Option<u32>,
        message: u64,
        parameters: Variables,
        due_ms: u64,
    ) -> Result<usize, InitFault> {
        let mut entries = Vec::new();
        for (&id, item) in &self.snapshot.instances {
            if recipient.is_some_and(|recipient| recipient != id) {
                continue;
            }
            let graph = graphs.graph(item.graph).expect("registered graph");
            entries.extend(
                graph
                    .nodes
                    .iter()
                    .filter(|node| {
                        !node.client_only
                            && node
                                .field("m_gameMessage.m_gameMessage")
                                .and_then(asset_guid)
                                == Some(message)
                            && (is_message_entry(node)
                                || (node.is_state()
                                    && node.state.is_some_and(|raw| {
                                        item.states.get(&raw).is_some_and(|state| {
                                            state.active && state.kind == StateKind::MessageIdle
                                        })
                                    })))
                    })
                    .map(|node| (id, node.pos)),
            );
        }
        self.queues.reserve(entries.len())?;
        let parameters = Arc::new(parameters);
        for &(instance, position) in &entries {
            self.queues.push(
                instance,
                due_ms,
                Event::Message {
                    position,
                    sender,
                    parameters: Arc::clone(&parameters),
                },
            )?;
        }
        Ok(entries.len())
    }

    fn value(&self, key: VarKey, below: Option<StateKey>) -> Value {
        self.links
            .get(&key)
            .and_then(|entries| {
                entries
                    .iter()
                    .take_while(|entry| Some(entry.state) != below)
                    .last()
            })
            .map_or_else(
                || stored(&self.snapshot, key),
                |entry| self.link_value(entry),
            )
    }

    fn link_value(&self, entry: &VariableLink) -> Value {
        match &entry.output {
            LinkValue::Fixed(value) => value.clone(),
            LinkValue::Volley(slot) => self.volleys[&entry.state].output(*slot, self.now_ms),
            LinkValue::Chase => self.snapshot.instances[&entry.state.instance].states
                [&entry.state.raw]
                .chase
                .as_ref()
                .expect("linked chase state")
                .current
                .clone(),
            LinkValue::Cooldown => Value::Float(
                self.snapshot.instances[&entry.state.instance].states[&entry.state.raw]
                    .ability_cooldown
                    .expect("linked cooldown state")
                    .remaining(self.now_ms),
            ),
            LinkValue::AbilityStack => Value::Bool(true),
        }
    }

    fn allows_stack(
        &self,
        key: VarKey,
        priority: StackPriority,
        exclude: Option<StateKey>,
    ) -> bool {
        self.links.get(&key).is_none_or(|entries| {
            entries
                .iter()
                .filter(|entry| Some(entry.state) != exclude)
                .all(|entry| {
                    entry.priority.level < priority.level
                        || (entry.priority.level == priority.level && priority.above_equals)
                })
        })
    }
}

struct Vm<'graph, 'world, 'machine, W> {
    graphs: &'graph GraphStore,
    world: &'world mut W,
    machine: &'machine mut Machine,
    parameters: Option<Arc<Variables>>,
    output_write_depth: usize,
}

impl<'graph, 'world, 'machine, W: World> Vm<'graph, 'world, 'machine, W> {
    fn new(
        graphs: &'graph GraphStore,
        world: &'world mut W,
        machine: &'machine mut Machine,
    ) -> Self {
        Self {
            graphs,
            world,
            machine,
            parameters: None,
            output_write_depth: 0,
        }
    }

    fn start(&mut self, roots: Vec<Root>) -> Result<(), InitFault> {
        let mut seen = HashSet::new();
        for root in &roots {
            if root.id == 0 || !seen.insert(root.id) {
                return Err(InitFault::InvalidRoot(root.id));
            }
        }
        for root in roots {
            let startup = if root.run_startup {
                Startup::Ordinary
            } else {
                Startup::Passive
            };
            if self.create(root.id, root.graph, None, root.variables, startup)? {
                if let Some(raw) = root.initial_state {
                    self.post(root.id, Event::Begin(raw))?;
                }
            }
        }
        self.run()
    }

    fn spend(&mut self, operation: &'static str) -> Result<(), InitFault> {
        self.machine.work_left =
            self.machine
                .work_left
                .checked_sub(1)
                .ok_or(InitFault::WorkBudget {
                    limit: self.machine.max_work,
                    operation,
                })?;
        Ok(())
    }

    fn create(
        &mut self,
        id: u16,
        graph: u32,
        parent: Option<(u16, u16)>,
        mut variables: Variables,
        startup: Startup,
    ) -> Result<bool, InitFault> {
        self.spend("instance creation")?;
        let Some(definition) = self.graphs.graph(graph) else {
            self.report(
                Site {
                    entity: self.machine.snapshot.entity,
                    instance: id,
                    graph,
                    node: None,
                },
                Issue::MissingGraph(graph),
            );
            return Ok(false);
        };
        let apply_schema =
            self.machine.public_schemas && !matches!(startup, Startup::Played { .. });
        self.machine.next_id = self.machine.next_id.max(u32::from(id) + 1);
        self.machine.snapshot.instances.insert(
            id,
            Instance {
                graph,
                start_ms: self.machine.now_ms,
                retire_after_frame: matches!(startup, Startup::Played { auto_retire: true })
                    .then(|| self.machine.frame.saturating_add(1)),
                parent,
                variables: if apply_schema {
                    Variables::new()
                } else {
                    std::mem::take(&mut variables)
                },
                states: IndexMap::new(),
                join_inputs: BTreeMap::new(),
            },
        );
        if apply_schema {
            let site = Site {
                entity: self.machine.snapshot.entity,
                instance: id,
                graph,
                node: None,
            };
            let schema = field(&definition.fields, "m_publicSchema");
            let mut defaults = Variables::new();
            if !schema.is_null() {
                if !matches!(
                    field(schema, "$").as_str(),
                    Some("STUStatescriptSchema" | "STU_77990ED7")
                ) || (!field(schema, "m_89EAAC40").is_null()
                    && (field(schema, "$").as_str() != Some("STU_77990ED7")
                        || field(schema, "m_89EAAC40")
                            .as_str()
                            .and_then(crate::accounts::parse_guid)
                            != Some(0x0580_0000_0000_0000 | u64::from(graph))))
                {
                    self.report(site, Issue::UnsupportedField("played graph schema"));
                } else {
                    let rows = field(schema, "m_entries");
                    if !rows.is_null() && !rows.is_array() {
                        self.report(site, Issue::UnsupportedField("played graph schema entries"));
                    }
                    for row in array_field(schema, "m_entries") {
                        self.spend("public schema evaluation")?;
                        if !row.is_object() {
                            self.report(site, Issue::UnsupportedField("played graph schema row"));
                            continue;
                        }
                        let config = field(row, "m_value");
                        if config.is_null() {
                            continue;
                        }
                        let identifier_config = field(row, "m_0D09D2D9");
                        if identifier_config.is_null() {
                            continue;
                        }
                        let Some(identifier) = identifier_config
                            .as_str()
                            .and_then(crate::accounts::parse_guid)
                        else {
                            self.report(
                                site,
                                Issue::UnsupportedField("played graph schema identifier"),
                            );
                            continue;
                        };
                        if identifier == 0 {
                            continue;
                        }
                        if let Some(value) = self.evaluate_at(site, config, None) {
                            defaults.insert(u32::from(identifier as u16), value);
                        }
                    }
                }
            }
            defaults.extend(variables);
            self.machine
                .snapshot
                .instances
                .get_mut(&id)
                .expect("created instance")
                .variables = defaults;
        }
        if !matches!(startup, Startup::Passive | Startup::Played { .. }) {
            for position in definition.entries.iter().flatten() {
                if definition
                    .node(*position)
                    .is_some_and(|node| !node.client_only && is_entry(node))
                {
                    self.post(id, Event::Entry(*position))?;
                }
            }
        }
        Ok(true)
    }

    fn run(&mut self) -> Result<(), InitFault> {
        loop {
            self.update()?;
            self.notify_code(1)?;
            if self
                .machine
                .queues
                .next_due()
                .is_none_or(|due| due > self.machine.now_ms)
            {
                return Ok(());
            }
        }
    }

    fn advance_frame(&mut self, frame: u32, command: Option<Command>) -> Result<(), InitFault> {
        self.machine.frame = frame;
        self.machine.now_ms = frame_start(frame);
        if let Some(command) = command {
            self.queue_buttons(command)?;
        }
        let end = self.machine.now_ms + FRAME_MS - 1;
        loop {
            let due = self
                .machine
                .queues
                .next_due()
                .filter(|&due| due <= end)
                .unwrap_or(end);
            self.machine.now_ms = self.machine.now_ms.max(due);
            self.update()?;
            self.notify_code(1)?;
            if self.machine.now_ms == end
                && self.machine.queues.next_due().is_none_or(|due| due > end)
            {
                return self.report_active_volleys();
            }
        }
    }

    fn queue_buttons(&mut self, mut command: Command) -> Result<(), InitFault> {
        if self.machine.control_blocked {
            command.buttons = 0;
            command.action = 0;
        }
        let edges: Vec<_> = button_edges(
            self.machine.command_buttons,
            command.buttons,
            command.action,
        )
        .collect();
        self.machine
            .queues
            .reserve(edges.len() * self.machine.snapshot.instances.len())?;
        for &id in self.machine.snapshot.instances.keys() {
            for &edge in &edges {
                self.machine
                    .queues
                    .push(id, self.machine.now_ms, Event::Button(edge))?;
            }
        }
        self.machine.command_buttons = command.buttons;
        Ok(())
    }

    fn button_edge(&mut self, id: u16, edge: ButtonEdge) -> Result<(), InitFault> {
        if edge.pressed {
            if !self.machine.held_buttons.contains(&edge.button) {
                self.machine.held_buttons.push(edge.button);
            }
        } else {
            self.machine
                .held_buttons
                .retain(|&button| button != edge.button);
        }
        let graph = self.graph(id);
        for raw in raw_indices(array_field(&graph.fields, "m_680A2CB2")) {
            let Some(node) = graph.state(usize::from(raw)) else {
                continue;
            };
            if node.client_only {
                continue;
            }
            let key = StateKey { instance: id, raw };
            if node.class == "STUStatescriptStateAbility" {
                self.spend("ability button edge")?;
                self.ability_edge(key, node, edge)?;
                continue;
            }
            if node.class != "STUStatescriptStateLogicalButton" {
                continue;
            }
            self.spend("logical button edge")?;
            if self.logical_button_id(id, node) != Some(edge.button)
                || self.logical_button_blocked(key, node, edge.button)
            {
                continue;
            }
            if edge.pressed
                && self.machine.owner_dead
                && !self
                    .evaluate(id, node, field(&node.fields, "m_5AE05408"), None)
                    .is_some_and(|value| truthy(&value))
            {
                continue;
            }
            if edge.pressed {
                let state = self.machine.snapshot.instances[&id]
                    .states
                    .entry(raw)
                    .or_insert_with(|| State::dormant(StateKind::LogicalButton { down: false }));
                state.buffered_press = Some(BufferedPress {
                    time_ms: self.machine.now_ms,
                    activation: state.activation.wrapping_add(u16::from(!state.active)),
                });
            }
            if self.state(key).is_some_and(|state| state.active) {
                self.set_button_down(key, node, edge.pressed, 0)?;
            }
        }
        Ok(())
    }

    fn logical_button_id(&mut self, id: u16, node: &Node) -> Option<u8> {
        let value = self.evaluate(id, node, field(&node.fields, "m_logicalButton"), None)?;
        u8::try_from(script_expr::to_int(&value, 0)).ok()
    }

    fn button_held(&mut self, key: StateKey, node: &Node) -> bool {
        self.logical_button_id(key.instance, node)
            .is_some_and(|button| self.machine.held_buttons.contains(&button))
    }

    fn button_is_blocked(&self, button: u8) -> bool {
        self.machine.control_blocked || self.machine.button_blocks[usize::from(button)] != 0
    }

    fn effective_button_held(&self, button: u8) -> bool {
        self.machine.held_buttons.contains(&button) && !self.button_is_blocked(button)
    }

    fn logical_button_blocked(&mut self, key: StateKey, node: &Node, button: u8) -> bool {
        self.button_is_blocked(button)
            && !self
                .evaluate(key.instance, node, field(&node.fields, "m_53C4B6A6"), None)
                .is_some_and(|value| truthy(&value))
    }

    fn resync_logical_buttons(&mut self, button: u8, depth: usize) -> Result<(), InitFault> {
        let keys: Vec<_> = self
            .machine
            .snapshot
            .instances
            .iter()
            .flat_map(|(&instance, script)| {
                script.states.iter().filter_map(move |(&raw, state)| {
                    (state.active
                        && !state.ending
                        && matches!(state.kind, StateKind::LogicalButton { .. }))
                    .then_some(StateKey { instance, raw })
                })
            })
            .collect();
        for key in keys {
            self.spend("logical button resync")?;
            let Some(state) = self
                .state(key)
                .filter(|state| state.active && !state.ending)
            else {
                continue;
            };
            let StateKind::LogicalButton { down } = state.kind else {
                continue;
            };
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("logical button state");
            if self.logical_button_id(key.instance, node) != Some(button) {
                continue;
            }
            let held = self.machine.held_buttons.contains(&button)
                && !self.logical_button_blocked(key, node, button);
            if held != down {
                self.set_button_down(key, node, held, depth)?;
            }
        }
        Ok(())
    }

    fn release_button_block(&mut self, button: u8, depth: usize) -> Result<(), InitFault> {
        let count = &mut self.machine.button_blocks[usize::from(button)];
        *count -= 1;
        self.resync_logical_buttons(button, depth)
    }

    fn enter_button(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        let state = self.state(key).expect("active logical button");
        let buffer = state
            .buffered_press
            .filter(|press| press.activation == state.activation);
        let button = self.logical_button_id(key.instance, node);
        let blocked = button.is_some_and(|button| self.logical_button_blocked(key, node, button));
        let duration = field(&node.fields, "m_246438AD");
        let early = if let Some(press) = buffer.filter(|_| !duration.is_null() && !blocked) {
            let waited = ((self.machine.now_ms - press.time_ms) as f64 / 1000.0) as f32;
            self.evaluate(key.instance, node, duration, None)
                .is_some_and(|value| f64::from(waited) <= script_expr::to_float(&value))
        } else {
            false
        };
        if early {
            self.follow(key.instance, node, "m_onGoingDownPlug", Some(key), depth)?;
        }
        if !self.state(key).is_some_and(|state| state.active) {
            return Ok(());
        }
        let held = !blocked && self.button_held(key, node);
        let down = held || early;
        self.state_mut(key).expect("active logical button").kind =
            StateKind::LogicalButton { down };
        let path = if down { "m_80AF45FB" } else { "m_1C9EF058" };
        self.follow(key.instance, node, path, Some(key), depth)?;
        if early && !held {
            self.post_after(key.instance, 1, Event::Timer(key.raw, 1))?;
        }
        Ok(())
    }

    fn set_button_down(
        &mut self,
        key: StateKey,
        node: &Node,
        down: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        self.state_mut(key).expect("active logical button").kind =
            StateKind::LogicalButton { down };
        let (previous, edge, next) = if down {
            ("m_1C9EF058", "m_onGoingDownPlug", "m_80AF45FB")
        } else {
            ("m_80AF45FB", "m_onComingUpPlug", "m_1C9EF058")
        };
        self.exit_subgraph(key.instance, node, previous, depth + 1)?;
        self.follow(key.instance, node, edge, Some(key), depth)?;
        if self.state(key).is_some_and(|state| state.active) {
            self.follow(key.instance, node, next, Some(key), depth)?;
        }
        Ok(())
    }

    fn ability_buttons(&mut self, key: StateKey, node: &Node) -> Option<[u8; 3]> {
        let mut buttons = [0; 3];
        for (button, field_name) in
            buttons
                .iter_mut()
                .zip(["m_A5F1A77B", "m_A368407D", "m_8CBD15DA"])
        {
            let value = self.evaluate(key.instance, node, field(&node.fields, field_name), None)?;
            *button = u8::try_from(script_expr::to_int(&value, 0)).ok()?;
        }
        Some(buttons)
    }

    fn ability_edge(
        &mut self,
        key: StateKey,
        node: &Node,
        edge: ButtonEdge,
    ) -> Result<(), InitFault> {
        if !edge.pressed
            || edge.button == 0
            || self.button_is_blocked(edge.button)
            || !self
                .ability_buttons(key, node)
                .is_some_and(|buttons| buttons.contains(&edge.button))
        {
            return Ok(());
        }
        let state = self.state(key);
        let active = state.is_some_and(|state| state.active);
        let activation = state
            .map_or(0, |state| state.activation)
            .wrapping_add(u16::from(!active));
        let now = self.machine.now_ms;
        let presses = self.machine.ability_presses.entry(key).or_default();
        presses.retain(|press| now - press.time_ms < 2000);
        presses.push(AbilityPress {
            button: edge.button,
            time_ms: now,
            activation,
        });
        if active {
            self.post(key.instance, Event::AbilityCheck(key.raw))?;
        }
        Ok(())
    }

    fn take_ability_press(&mut self, key: StateKey, node: &Node, button: u8) -> bool {
        self.ability_press(key, node, button, false)
    }

    fn ability_press(&mut self, key: StateKey, node: &Node, button: u8, retain: bool) -> bool {
        let early = field(&node.fields, "m_246438AD");
        let window = if early.is_null() {
            0.0
        } else {
            let Some(value) = self.evaluate(key.instance, node, early, None) else {
                return false;
            };
            ((script_expr::to_float(&value) * 1000.0) as f32).round()
        };
        let now = self.machine.now_ms;
        let activation = self.state(key).expect("active ability").activation;
        let Some(presses) = self.machine.ability_presses.get_mut(&key) else {
            return false;
        };
        let Some(index) = presses.iter().position(|press| {
            press.button == button
                && (now - press.time_ms) as f32 <= window
                && press.activation <= activation
        }) else {
            return false;
        };
        if !retain {
            presses.remove(index);
        }
        true
    }

    fn ability_flags(&self, key: StateKey) -> Option<u8> {
        let StateKind::Ability { flags } = self.state(key).filter(|state| state.active)?.kind
        else {
            return None;
        };
        Some(flags)
    }

    fn set_ability_flags(&mut self, key: StateKey, flags: u8) {
        self.state_mut(key).expect("active ability").kind = StateKind::Ability { flags };
    }

    fn ability_condition(&mut self, key: StateKey, node: &Node, name: &str) -> bool {
        let config = field(&node.fields, name);
        config.is_null()
            || self
                .evaluate(key.instance, node, config, Some(key))
                .is_some_and(|value| truthy(&value))
    }

    fn ability_stack_allows(&mut self, key: StateKey, node: &Node, name: &str) -> bool {
        let config = field(&node.fields, name);
        if config.is_null() {
            return true;
        }
        let variable = match script_expr::lvalue(field(&node.fields, "m_out_StackVar")) {
            Ok(Some(variable)) => source_key(key.instance, variable),
            Ok(None) => return true,
            Err(error) => {
                self.report(self.site(key.instance, node), Issue::Expression(error));
                return false;
            }
        };
        match script_expr::stack_priority(config) {
            Ok(priority) => {
                self.watch(variable, key);
                self.machine.allows_stack(variable, priority, Some(key))
            }
            Err(error) => {
                self.report(self.site(key.instance, node), Issue::Expression(error));
                false
            }
        }
    }

    fn activate_ability(&mut self, key: StateKey, depth: usize) -> Result<(), InitFault> {
        let Some(flags) = self.ability_flags(key) else {
            return Ok(());
        };
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("ability state");
        if self.machine.owner_dead || self.machine.control_blocked {
            return self.stop_ability(key, node, depth);
        }
        let Some(buttons) = self.ability_buttons(key, node) else {
            return Ok(());
        };
        let active_flag = if ability_is_stacked(node) {
            ABILITY_PUSHED
        } else {
            ABILITY_RUNNING
        };
        if flags & active_flag != 0 {
            self.keep_ability(key, node, flags, buttons, depth)
        } else {
            self.try_start_ability(key, node, flags, buttons, depth)
        }
    }

    fn keep_ability(
        &mut self,
        key: StateKey,
        node: &Node,
        mut flags: u8,
        [start, hold, stop]: [u8; 3],
        depth: usize,
    ) -> Result<(), InitFault> {
        if !self.effective_button_held(start) {
            flags &= !ABILITY_HELD;
            self.set_ability_flags(key, flags);
        }
        let keep_held = field(&node.fields, "m_755F9921");
        if hold == 0
            || (!keep_held.is_null()
                && self
                    .evaluate(key.instance, node, keep_held, Some(key))
                    .is_some_and(|value| truthy(&value)))
        {
            if stop != 0 && self.take_ability_press(key, node, stop) {
                if stop == start {
                    self.set_ability_flags(key, flags | ABILITY_HELD);
                }
                return self.stop_ability(key, node, depth);
            }
        } else if !self.effective_button_held(hold) {
            return self.stop_ability(key, node, depth);
        }
        if !self.ability_condition(key, node, "m_F495731F")
            || !self.ability_stack_allows(key, node, "m_343E99EE")
        {
            self.stop_ability(key, node, depth)?;
        }
        Ok(())
    }

    fn try_start_ability(
        &mut self,
        key: StateKey,
        node: &Node,
        flags: u8,
        [start, hold, _]: [u8; 3],
        depth: usize,
    ) -> Result<(), InitFault> {
        let mut pressed = false;
        if start != 0 {
            pressed = self.ability_press(key, node, start, true);
            if !pressed {
                if !self.effective_button_held(start) {
                    self.set_ability_flags(key, flags & !ABILITY_HELD);
                    return Ok(());
                }
                if flags & ABILITY_HELD != 0 {
                    return Ok(());
                }
            }
        } else if hold != 0 && !self.effective_button_held(hold) {
            return Ok(());
        }
        if self.cooldown_remaining(key) > 0.0
            || !self.ability_condition(key, node, "m_150F0D92")
            || !self.ability_condition(key, node, "m_F495731F")
            || !self.ability_stack_allows(key, node, "m_3C0EB4A6")
        {
            return Ok(());
        }
        if pressed {
            self.take_ability_press(key, node, start);
        }
        self.set_ability_flags(key, flags | if start != 0 { ABILITY_HELD } else { 0 });
        if ability_is_stacked(node) {
            self.push_ability(key, node, depth)
        } else {
            self.start_ability(key, node, depth)
        }
    }

    fn push_ability(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        let id = key.instance;
        let target = match script_expr::lvalue(field(&node.fields, "m_out_StackVar")) {
            Ok(Some(target)) => source_key(id, target),
            Ok(None) => return Ok(()),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        let priority = match script_expr::stack_priority(field(&node.fields, "m_5E561E77")) {
            Ok(priority) => priority,
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        if self.state(key).and_then(|state| state.linked_variable) == Some(target) {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("overlapping ability outputs"),
            );
            return Ok(());
        }
        let state = self.state_mut(key).expect("active ability");
        let StateKind::Ability { flags } = state.kind else {
            unreachable!("active ability")
        };
        state.kind = StateKind::Ability {
            flags: flags | ABILITY_PUSHED,
        };
        state.ability_stack = Some(target);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority,
                output: LinkValue::AbilityStack,
                stack: true,
            },
            depth,
        )
    }

    fn start_ability(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        let id = key.instance;
        let flags = self.ability_flags(key).expect("active ability");
        self.set_ability_flags(key, flags | ABILITY_RUNNING);
        self.follow(id, node, "m_B969AFC9", Some(key), depth)?;
        self.follow(id, node, "m_4E22B5C4", Some(key), depth)?;
        self.ability_vars(id, node, "m_EF3C5C73")
    }

    fn stop_ability(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        self.unlink_ability_stack(key, depth)?;
        let Some(flags) = self
            .ability_flags(key)
            .filter(|flags| flags & ABILITY_RUNNING != 0)
        else {
            return Ok(());
        };
        let id = key.instance;
        self.set_ability_flags(key, flags & !ABILITY_RUNNING);
        self.exit_subgraph(id, node, "m_B969AFC9", depth + 1)?;
        self.follow(id, node, "m_6EEEC0AA", Some(key), depth)?;
        self.ability_vars(id, node, "m_19899EAC")
    }

    fn unlink_ability_stack(&mut self, key: StateKey, depth: usize) -> Result<(), InitFault> {
        let Some(flags) = self
            .ability_flags(key)
            .filter(|flags| flags & ABILITY_PUSHED != 0)
        else {
            return Ok(());
        };
        self.set_ability_flags(key, flags & !ABILITY_PUSHED);
        if let Some(target) = self
            .state_mut(key)
            .and_then(|state| state.ability_stack.take())
        {
            self.remove_link(key, target, None, depth + 1)?;
        }
        Ok(())
    }

    fn cooldown_remaining(&self, key: StateKey) -> f64 {
        self.state(key)
            .and_then(|state| state.ability_cooldown)
            .map_or(0.0, |cooldown| cooldown.remaining(self.machine.now_ms))
    }

    fn restart_cooldown(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let target = match script_expr::lvalue(field(&node.fields, "m_out_CooldownVar")) {
            Ok(Some(target)) => source_key(id, target),
            Ok(None) => return Ok(()),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        let modifier = self.machine.value(VarKey::Entity(3652), None);
        let mut rate = (script_expr::to_float(&modifier) + 1.0) as f32;
        let scalar = field(&node.fields, "m_F7CAF887");
        if !scalar.is_null() {
            let Some(value) = self.evaluate(id, node, scalar, None) else {
                return Ok(());
            };
            rate = (f64::from(rate) * script_expr::to_float(&value)) as f32;
        }
        let current = script_expr::to_float(&stored(&self.machine.snapshot, target)).max(0.0);
        let delay = ((current * 1000.0) as f32 / rate).round();
        if !current.is_finite() || !rate.is_finite() || (rate > 0.0 && !delay.is_finite()) {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("ability cooldown"),
            );
            return Ok(());
        }
        // The reference removes its previous slot before relinking at the same priority.
        if let Some(entries) = self.machine.links.get_mut(&target) {
            entries.retain(|entry| entry.state != key);
        }
        let cooldown = AbilityCooldown {
            current,
            rate: f64::from(rate),
            last_ms: self.machine.now_ms,
        };
        let state = self.state_mut(key).expect("active ability");
        state.linked_variable = Some(target);
        state.ability_cooldown = Some(cooldown);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority: StackPriority {
                    level: 0,
                    above_equals: true,
                },
                output: LinkValue::Cooldown,
                stack: false,
            },
            depth,
        )?;
        self.machine.queues.retain(
            id,
            |_, event| !matches!(event, Event::Timer(raw, 2) if *raw == key.raw),
        );
        if current > 0.0 && rate > 0.0 {
            self.post_after(id, delay.max(1.0) as u64, Event::Timer(key.raw, 2))?;
        }
        Ok(())
    }

    fn finish_cooldown(&mut self, key: StateKey) -> Result<(), InitFault> {
        let remaining = self.cooldown_remaining(key);
        if let Some(target) = self
            .state_mut(key)
            .and_then(|state| state.linked_variable.take())
        {
            self.unlink_output(key, target, Some(Value::Float(remaining)))?;
        }
        Ok(())
    }

    fn ability_vars(&mut self, id: u16, node: &Node, name: &str) -> Result<(), InitFault> {
        for item in array_field(&node.fields, name)
            .iter()
            .filter(|item| item.is_object())
        {
            self.store(id, node, item, false)?;
        }
        Ok(())
    }

    fn update(&mut self) -> Result<(), InitFault> {
        self.spend("component update")?;
        let instances: Vec<_> = self.machine.snapshot.instances.keys().copied().collect();
        for id in instances {
            let mut events = 0;
            loop {
                if !self.machine.snapshot.instances.contains_key(&id) {
                    break;
                }
                while let Some(event) = self.machine.queues.pop_due(id, self.machine.now_ms) {
                    if events == MAX_EVENTS_PER_UPDATE {
                        return Err(InitFault::EventBudget { instance: id });
                    }
                    events += 1;
                    self.spend("event dispatch")?;
                    let key = |raw| StateKey { instance: id, raw };
                    match event {
                        Event::Entry(position) => {
                            if let Some(node) = self.graph(id).node(position) {
                                self.run_node(id, node, None, 0)?;
                            }
                        }
                        Event::Assign(target, value) => self.write(target, value)?,
                        Event::ExitInstance => self.exit_instance(id)?,
                        Event::Begin(raw) => self.begin(key(raw), 0)?,
                        Event::Finish(raw) => self.end(key(raw), true, 0)?,
                        Event::Abort(raw) => self.end(key(raw), false, 0)?,
                        Event::Changed(raw) => self.changed(key(raw))?,
                        Event::Reevaluate(raw) => self.reevaluate(key(raw), 0)?,
                        Event::Timer(raw, parameter) => self.timer(key(raw), parameter)?,
                        Event::Button(edge) => self.button_edge(id, edge)?,
                        Event::AbilityCheck(raw) => self.timer(key(raw), 1)?,
                        Event::CheckTransitions(raw) => {
                            if self.state(key(raw)).is_some_and(|state| state.active) {
                                let node =
                                    self.graph(id).state(usize::from(raw)).expect("chase state");
                                self.try_complete(key(raw), node, 0)?;
                            }
                        }
                        Event::WatchChanged(raw) => self.deliver_watch(key(raw))?,
                        Event::SpawnResult { key, positions } => {
                            self.finish_spawn_search(key, positions)?
                        }
                        Event::Message {
                            position,
                            sender,
                            parameters,
                        } => {
                            self.deliver(id, position, sender, parameters)?;
                        }
                    }
                }
                if !self.machine.snapshot.instances.contains_key(&id) {
                    break;
                }
                self.tick_states(id)?;
                if !self.run_deferred(id)? {
                    break;
                }
            }
            self.retire_played_instance(id)?;
        }
        Ok(())
    }

    fn run_deferred(&mut self, id: u16) -> Result<bool, InitFault> {
        let pending = self.machine.deferred.remove(&id).unwrap_or_default();
        if pending.is_empty() {
            return Ok(false);
        }
        // Swap out the batch before delivery. Registrations made by a callback
        // run after another ordinary event/tick phase in the same update.
        for (_, raw) in pending.into_iter().rev() {
            self.spend("deferred state callback")?;
            let key = StateKey { instance: id, raw };
            if self.state(key).is_some_and(|state| state.active) {
                self.timer(key, 1)?;
            }
        }
        Ok(true)
    }

    fn graph(&self, id: u16) -> &'graph Graph {
        let graph = self.machine.snapshot.instances[&id].graph;
        self.graphs.graph(graph).expect("registered graph")
    }

    fn site(&self, instance: u16, node: &Node) -> Site {
        Site {
            entity: self.machine.snapshot.entity,
            instance,
            graph: self.machine.snapshot.instances[&instance].graph,
            node: Some(node.pos),
        }
    }

    fn report(&mut self, site: Site, issue: Issue) {
        let diagnostic = Diagnostic { site, issue };
        if !self.machine.snapshot.diagnostics.contains(&diagnostic) {
            self.machine.snapshot.diagnostics.push(diagnostic);
        }
    }

    fn post(&mut self, id: u16, event: Event) -> Result<(), InitFault> {
        self.post_after(id, 0, event)
    }

    fn post_after(&mut self, id: u16, delay_ms: u64, event: Event) -> Result<(), InitFault> {
        self.machine
            .queues
            .push(id, self.machine.now_ms.saturating_add(delay_ms), event)?;
        Ok(())
    }

    fn deliver(
        &mut self,
        id: u16,
        position: u16,
        sender: Option<u32>,
        parameters: Arc<Variables>,
    ) -> Result<(), InitFault> {
        let Some(node) = self.graph(id).node(position) else {
            return Ok(());
        };
        self.parameters = Some(parameters);
        let result = self.deliver_entry(id, node, sender);
        self.parameters = None;
        result
    }

    fn deliver_entry(
        &mut self,
        id: u16,
        node: &Node,
        sender: Option<u32>,
    ) -> Result<(), InitFault> {
        let path = if node.is_state() {
            let Some(raw) = node.state else { return Ok(()) };
            let key = StateKey { instance: id, raw };
            let Some(state) = self.state(key).filter(|state| state.active) else {
                return Ok(());
            };
            if state.kind != StateKind::MessageIdle {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("state game message delivery"),
                );
                return Ok(());
            }
            self.exit_subgraph(id, node, "m_33DA5163", 1)?;
            ("m_452862E6", Some(key))
        } else {
            ("m_outPlug", None)
        };
        let site = self.site(id, node);
        for field in array_field(&node.fields, "m_params") {
            if !field.is_object() {
                continue;
            }
            let target = match script_expr::lvalue(field.get("m_out_Var").unwrap_or(&Json::Null)) {
                Ok(Some(target)) => source_key(id, target),
                Ok(None) => continue,
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(());
                }
            };
            let key = parameter_id(field);
            let value = self
                .parameters
                .as_deref()
                .and_then(|params| params.get(&key))
                .cloned()
                .unwrap_or(Value::Null);
            self.write(target, value)?;
        }
        if let Some(sender) = sender.filter(|_| !node.is_state()) {
            for output in ["m_B60ABDA4", "m_FF703F20"] {
                match script_expr::dynamic(field(&node.fields, output)) {
                    Ok(Some(target)) => {
                        self.write(source_key(id, target), Value::Entity(sender))?;
                    }
                    Ok(None) => (),
                    Err(error) => {
                        self.report(site, Issue::Expression(error));
                        return Ok(());
                    }
                }
            }
        }
        self.follow(id, node, path.0, path.1, 0)?;
        Ok(())
    }

    fn play_script(&mut self, id: u16, node: &Node, depth: usize) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let overrides_config = field(&node.fields, "m_69F4B52C");
        let mut resource = None;
        if !overrides_config.is_null() {
            if !script_expr::native_shape(overrides_config, "STU_9DB46B98", Some("m_6525E98B")) {
                self.report(site, Issue::UnsupportedField("PlayScript override config"));
                return Ok(false);
            }
            match self.evaluate(id, node, overrides_config, None) {
                Some(Value::Map(rows)) => {
                    let mut graph = None;
                    let mut overrides = Vec::new();
                    for (key, value) in rows {
                        let Value::Integer(key) = key else {
                            return Ok(false);
                        };
                        if key == 0x4065 {
                            if let Value::Asset(guid) = value {
                                graph = Some(guid);
                            }
                        } else if key != 0 {
                            overrides.push((0x0d80_0000_0000_0000 | key as u64, value));
                        }
                    }
                    resource = graph.map(|graph| (graph, overrides));
                }
                Some(Value::Integer(0)) => (),
                _ => return Ok(false),
            }
        }
        if resource.is_none() {
            let script = field(&node.fields, "m_script");
            if !script.is_null()
                && (script.get("$").and_then(Json::as_str) != Some("STU_105E1BCC")
                    || !script_expr::native_fields(script, &["m_graph", "m_resourceKey"])
                    || !field(script, "m_resourceKey").is_null())
            {
                self.report(site, Issue::UnsupportedField("PlayScript resource"));
                return Ok(false);
            }
            resource = asset_guid(field(script, "m_graph")).map(|graph| (graph, Vec::new()));
        }
        let Some((guid, overrides)) = resource else {
            self.follow(id, node, "m_outPlug", None, depth)?;
            return Ok(true);
        };
        if guid >> 48 != 0x0580
            || guid & 0x0000_ffff_ffff_ffff == 0
            || guid & 0x0000_ffff_ffff_ffff > u64::from(u32::MAX)
        {
            self.report(site, Issue::UnsupportedField("PlayScript graph identity"));
            return Ok(false);
        }
        let graph = (guid & 0x0000_ffff_ffff_ffff) as u32;
        let policy = match field(&node.fields, "m_AA4DE426") {
            Json::Null => 0,
            value => match value
                .as_u64()
                .and_then(|value| u8::try_from(value).ok())
                .filter(|value| *value < 4)
            {
                Some(value) => value,
                None => {
                    self.report(site, Issue::UnsupportedField("PlayScript policy"));
                    return Ok(false);
                }
            },
        };
        let auto_retire = match field(&node.fields, "m_7ABA409C") {
            Json::Null => false,
            value => match value.as_u64().and_then(|value| u8::try_from(value).ok()) {
                Some(value) => value == 0,
                None => {
                    self.report(site, Issue::UnsupportedField("PlayScript wire flag"));
                    return Ok(false);
                }
            },
        };
        if !field(&node.fields, "m_6D7701B7").is_null()
            || !matches!(field(&node.fields, "m_params"), Json::Null | Json::Array(_))
        {
            self.report(
                site,
                Issue::UnsupportedField("PlayScript resource or policy"),
            );
            return Ok(false);
        }
        let Some(target) = self.evaluate(id, node, field(&node.fields, "m_targets"), None) else {
            return Ok(false);
        };
        match target {
            Value::Null => (),
            Value::Entity(target) if !script_expr::valid_entity(target) => (),
            Value::Entity(target) => {
                let Some(params) = self.play_parameters(id, node)? else {
                    return Ok(false);
                };
                let source = match self
                    .machine
                    .value(VarKey::Instance(id, SCRIPT_SOURCE), None)
                {
                    Value::Entity(source) if script_expr::valid_entity(source) => source,
                    _ => site.entity,
                };
                let request = PlayScript {
                    policy,
                    overrides,
                    graph,
                    source,
                    parameters: params,
                    auto_retire,
                };
                if target == site.entity {
                    self.start_loaded_script(site, &request)?;
                } else if !self.world.play_script(site, target, request) {
                    self.report(site, Issue::UnsupportedField("PlayScript target component"));
                    return Ok(false);
                }
            }
            _ => {
                self.report(site, Issue::UnsupportedField("PlayScript target component"));
                return Ok(false);
            }
        }
        if self.machine.snapshot.instances.contains_key(&id) {
            self.follow(id, node, "m_outPlug", None, depth)?;
        }
        Ok(true)
    }

    fn play_parameters(
        &mut self,
        id: u16,
        node: &Node,
    ) -> Result<Option<Vec<(u64, Value)>>, InitFault> {
        let site = self.site(id, node);
        let mut params = Vec::new();
        for row in array_field(&node.fields, "m_params") {
            self.spend("PlayScript parameter")?;
            let identifier = field(row, "m_B5051BCE");
            if row.is_null() || (row.is_object() && identifier.is_null()) {
                continue;
            }
            let Some(parameter) = asset_guid(identifier) else {
                self.report(
                    site,
                    Issue::UnsupportedField("PlayScript parameter identifier"),
                );
                return Ok(None);
            };
            let Some(value) = self.evaluate(id, node, field(row, "m_value"), None) else {
                return Ok(None);
            };
            let mut values = Vec::new();
            let mut pending = match value {
                Value::Array(items) => items,
                Value::Null => Vec::new(),
                item => vec![item],
            };
            pending.reverse();
            while let Some(item) = pending.pop() {
                self.spend("PlayScript parameter item")?;
                match item {
                    Value::Array(items) => pending.extend(items.into_iter().rev()),
                    _ => values.push(item),
                }
            }
            params.push((parameter, Value::Array(values)));
        }
        Ok(Some(params))
    }

    fn start_loaded_script(&mut self, site: Site, request: &PlayScript) -> Result<(), InitFault> {
        let PlayScript {
            graph,
            source,
            parameters,
            auto_retire,
            policy,
            overrides,
        } = request;
        if *policy > 3 {
            self.report(site, Issue::UnsupportedField("PlayScript policy"));
            return Ok(());
        }
        let matches: Vec<_> = self
            .machine
            .snapshot
            .instances
            .iter()
            .filter_map(|(&id, instance)| (instance.graph == *graph).then_some(id))
            .collect();
        if *policy == 0 && !matches.is_empty() {
            return Ok(());
        }
        if *policy == 3 {
            for id in &matches {
                self.destroy(*id)?;
            }
        }
        if self.machine.snapshot.instances.len() > MAX_PLAY_INSTANCES {
            return Ok(());
        }
        if self.graphs.graph(*graph).is_none() {
            self.report(site, Issue::MissingGraph(*graph));
            return Ok(());
        }
        let reused = if *policy == 2 {
            matches.first().copied()
        } else {
            None
        };
        let id = if let Some(id) = reused {
            id
        } else {
            let id =
                u16::try_from(self.machine.next_id).map_err(|_| InitFault::InstanceIdExhausted)?;
            self.create(
                id,
                *graph,
                None,
                Variables::from([(SCRIPT_SOURCE, Value::Entity(*source))]),
                Startup::Played {
                    auto_retire: *auto_retire,
                },
            )?;
            id
        };
        self.queue_play_schema(id, *graph, overrides)?;
        if reused.is_none() {
            let definition = self.graphs.graph(*graph).expect("created played graph");
            for position in definition.entries.iter().flatten() {
                if definition
                    .node(*position)
                    .is_some_and(|node| !node.client_only && is_entry(node))
                {
                    self.post(id, Event::Entry(*position))?;
                }
            }
        }
        self.queue_script_start(id, *graph, site.entity, parameters)
    }

    fn queue_play_schema(
        &mut self,
        id: u16,
        graph: u32,
        overrides: &[(u64, Value)],
    ) -> Result<(), InitFault> {
        let definition = self.graphs.graph(graph).expect("played graph");
        let site = Site {
            entity: self.machine.snapshot.entity,
            instance: id,
            graph,
            node: None,
        };
        let schema = field(&definition.fields, "m_publicSchema");
        if !schema.is_null() {
            if !matches!(
                field(schema, "$").as_str(),
                Some("STUStatescriptSchema" | "STU_77990ED7")
            ) || (!field(schema, "m_89EAAC40").is_null()
                && asset_guid(field(schema, "m_89EAAC40"))
                    != Some(0x0580_0000_0000_0000 | u64::from(graph)))
                || !matches!(field(schema, "m_entries"), Json::Null | Json::Array(_))
            {
                self.report(site, Issue::UnsupportedField("played graph schema"));
                return Ok(());
            }
            for row in array_field(schema, "m_entries") {
                self.spend("played schema evaluation")?;
                if !row.is_object() {
                    self.report(site, Issue::UnsupportedField("played graph schema row"));
                    continue;
                }
                let config = field(row, "m_value");
                let identifier = field(row, "m_0D09D2D9");
                if config.is_null() || identifier.is_null() {
                    continue;
                }
                let Some(key) = asset_guid(identifier) else {
                    self.report(
                        site,
                        Issue::UnsupportedField("played graph schema identifier"),
                    );
                    continue;
                };
                if key != 0 {
                    if let Some(value) = self.evaluate_at(site, config, None) {
                        self.post(
                            id,
                            Event::Assign(VarKey::Instance(id, u32::from(key as u16)), value),
                        )?;
                    }
                }
            }
        }
        for (key, value) in overrides {
            self.spend("played override assignment")?;
            self.post(
                id,
                Event::Assign(VarKey::Instance(id, u32::from(*key as u16)), value.clone()),
            )?;
        }
        Ok(())
    }

    fn queue_script_start(
        &mut self,
        id: u16,
        graph: u32,
        source: u32,
        parameters: &[(u64, Value)],
    ) -> Result<(), InitFault> {
        let definition = self.graphs.graph(graph).expect("created played graph");
        for position in definition.entries.iter().flatten() {
            let Some(node) = definition.node(*position).filter(|node| {
                !node.client_only && node.class == "STU_BB4AC733" && !configured(node, "m_5AEA0A51")
            }) else {
                continue;
            };
            if self
                .machine
                .disabled_start_entries
                .contains(&(id, *position))
            {
                continue;
            }
            if configured(node, "m_FDBDCB70") {
                self.machine.disabled_start_entries.insert((id, *position));
            }
            self.spend("PlayScript start entry")?;
            let rows = array_field(&node.fields, "m_params");
            for (index, (parameter, value)) in parameters.iter().enumerate() {
                for (row_index, row) in rows.iter().enumerate() {
                    self.spend("PlayScript start parameter binding")?;
                    let matches = if *parameter == 0 {
                        row_index == index
                    } else {
                        asset_guid(field(row, "m_B5051BCE")) == Some(*parameter)
                    };
                    let output = field(row, "m_out_Var");
                    if matches && !output.is_null() {
                        self.queue_play_binding(id, node, output, value.clone())?;
                        break;
                    }
                }
            }
            if script_expr::valid_entity(source) {
                self.queue_play_binding(
                    id,
                    node,
                    field(&node.fields, "m_B60ABDA4"),
                    Value::Entity(source),
                )?;
            }
            self.post(id, Event::Entry(*position))?;
        }
        Ok(())
    }

    fn scoped_children(&self, key: StateKey) -> Vec<u16> {
        self.machine
            .snapshot
            .instances
            .iter()
            .filter_map(|(&id, child)| {
                (child.parent == Some((key.instance, key.raw))).then_some(id)
            })
            .collect()
    }

    fn refresh_phase_scripts(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let Ok(Some(target)) = script_expr::dynamic(field(&node.fields, "m_3DC6FAAA")) else {
            self.report(
                site,
                Issue::UnsupportedField("phase script list destination"),
            );
            return Ok(());
        };
        if target.scope != Scope::Instance {
            self.report(
                site,
                Issue::UnsupportedField("phase script list destination"),
            );
            return Ok(());
        }
        let Some(value) = self.evaluate(id, node, field(&node.fields, "m_814F8EC3"), Some(key))
        else {
            return Ok(());
        };
        let mut remaining = self.scoped_children(key);
        let mut added = Vec::new();
        if let Value::Array(items) = value {
            for value in items.into_iter().filter(|item| *item != Value::Null) {
                self.spend("phase script list item")?;
                let mut reused = None;
                for (index, &child) in remaining.iter().enumerate() {
                    self.spend("phase script list match")?;
                    if same(
                        &value,
                        &self.machine.value(VarKey::Instance(child, target.id), None),
                    ) {
                        reused = Some(index);
                        break;
                    }
                }
                if let Some(index) = reused {
                    remaining.remove(index);
                } else {
                    added.push(value);
                }
            }
        }
        for child in remaining {
            self.destroy(child)?;
        }
        for value in added {
            let child =
                u16::try_from(self.machine.next_id).map_err(|_| InitFault::InstanceIdExhausted)?;
            let values = Variables::from([
                (target.id, value),
                (SCRIPT_SOURCE, Value::Entity(site.entity)),
            ]);
            if self.create(
                child,
                site.graph,
                Some((id, key.raw)),
                values,
                Startup::Passive,
            )? {
                self.follow(child, node, "m_C41A9BE2", None, 0)?;
            }
        }
        Ok(())
    }

    fn begin_phase_script(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        if self.machine.snapshot.instances[&id].parent.is_none()
            || !is_self_target(node, "m_targets")
            || !script_expr::native_shape(
                field(&node.fields, "m_69F4B52C"),
                "STU_9DB46B98",
                Some("m_6525E98B"),
            )
            || node
                .field("m_69F4B52C.m_6525E98B")
                .is_some_and(|value| !value.is_null())
        {
            self.report(site, Issue::UnsupportedField("phase script scope"));
            return Ok(());
        }
        let Some(Value::Asset(guid)) =
            self.evaluate(id, node, field(&node.fields, "m_script"), None)
        else {
            return Ok(());
        };
        let Some(graph) = (guid >> 48 == 0x0580)
            .then(|| u32::try_from(guid & 0x0000_ffff_ffff_ffff).ok())
            .flatten()
        else {
            self.report(site, Issue::UnsupportedField("phase script graph"));
            return Ok(());
        };
        let Some(parameters) = self.play_parameters(id, node)? else {
            return Ok(());
        };
        let child =
            u16::try_from(self.machine.next_id).map_err(|_| InitFault::InstanceIdExhausted)?;
        let values = Variables::from([(SCRIPT_SOURCE, Value::Entity(site.entity))]);
        if self.create(
            child,
            graph,
            Some((id, key.raw)),
            values,
            Startup::Played { auto_retire: false },
        )? {
            self.queue_script_start(child, graph, site.entity, &parameters)?;
        }
        Ok(())
    }

    fn queue_play_binding(
        &mut self,
        id: u16,
        node: &Node,
        output: &Json,
        value: Value,
    ) -> Result<(), InitFault> {
        match script_expr::dynamic(output) {
            Ok(Some(target)) if target.id != 0 => {
                self.post(id, Event::Assign(source_key(id, target), value))?;
            }
            Ok(_) if output.is_null() => (),
            Ok(Some(_)) => (),
            Ok(None) => self.report(
                self.site(id, node),
                Issue::UnsupportedField("PlayScript start binding"),
            ),
            Err(error) => self.report(self.site(id, node), Issue::Expression(error)),
        }
        Ok(())
    }

    fn retire_played_instance(&mut self, id: u16) -> Result<(), InitFault> {
        let Some(instance) = self.machine.snapshot.instances.get(&id) else {
            return Ok(());
        };
        if instance
            .retire_after_frame
            .is_none_or(|frame| self.machine.frame <= frame)
            || instance.states.values().any(|state| state.active)
            || self.machine.queues.iter(id).next().is_some()
        {
            return Ok(());
        }
        let graph = self.graph(id);
        for node in graph
            .entries
            .iter()
            .flatten()
            .filter_map(|position| graph.node(*position))
        {
            self.spend("PlayScript retirement entry")?;
            if !is_message_entry(node)
                && !matches!(node.class.as_str(), "STU_BB4AC733" | "STU_8A7E9DA8")
            {
                return Ok(());
            }
        }
        self.destroy(id)
    }

    fn send_game_message(&mut self, id: u16, node: &Node) -> Result<bool, InitFault> {
        let Some(message) = node
            .field("m_gameMessage.m_gameMessage")
            .and_then(asset_guid)
            .filter(|&id| id != 0)
        else {
            return Ok(true);
        };
        let site = self.site(id, node);
        let target = node
            .fields
            .get("m_targets")
            .or_else(|| node.fields.get("m_target"))
            .unwrap_or(&Json::Null);
        let value = if target.is_null() {
            Value::Entity(site.entity)
        } else {
            let Some(value) = self.evaluate(id, node, target, None) else {
                return Ok(false);
            };
            value
        };
        let entities: Vec<_> = match value {
            Value::Entity(entity) if entity != 0 => vec![entity],
            Value::Array(items) => items
                .into_iter()
                .filter_map(|value| {
                    let Value::Entity(entity) = value else {
                        return None;
                    };
                    (entity != 0 && entity & 0x1fff_ffff != 0x1fff_ffff).then_some(entity)
                })
                .collect(),
            _ => Vec::new(),
        };
        // The reference evaluates the parameters before the recipients, but
        // a message without recipients delivers none of them. Skipping them
        // also skips any random draw they would make.
        if entities.is_empty() {
            return Ok(true);
        }
        let Some(parameters) = self.game_message_parameters(id, node)? else {
            return Ok(false);
        };
        for entity in entities {
            self.spend("game message routing")?;
            if entity == site.entity {
                self.machine.queue_entries_from(
                    self.graphs,
                    None,
                    Some(site.entity),
                    message,
                    parameters.clone(),
                    self.machine.now_ms,
                )?;
            } else if !self.world.send_message(site, entity, message, &parameters) {
                self.report(
                    site,
                    Issue::UnsupportedField("external game message delivery"),
                );
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn game_message_parameters(
        &mut self,
        id: u16,
        node: &Node,
    ) -> Result<Option<Variables>, InitFault> {
        let mut parameters = Variables::new();
        for parameter in array_field(&node.fields, "m_params")
            .iter()
            .filter(|value| value.is_object())
        {
            self.spend("game message parameter")?;
            let Some(value) = self.evaluate(id, node, field(parameter, "m_value"), None) else {
                return Ok(None);
            };
            parameters.insert(parameter_id(parameter), value);
        }
        Ok(Some(parameters))
    }

    fn state(&self, key: StateKey) -> Option<&State> {
        self.machine
            .snapshot
            .instances
            .get(&key.instance)?
            .states
            .get(&key.raw)
    }

    fn state_mut(&mut self, key: StateKey) -> Option<&mut State> {
        self.machine
            .snapshot
            .instances
            .get_mut(&key.instance)?
            .states
            .get_mut(&key.raw)
    }

    fn evaluate(
        &mut self,
        id: u16,
        node: &Node,
        config: &Json,
        watcher: Option<StateKey>,
    ) -> Option<Value> {
        let site = self.site(id, node);
        self.evaluate_at(site, config, watcher)
    }

    fn evaluate_at(
        &mut self,
        site: Site,
        config: &Json,
        watcher: Option<StateKey>,
    ) -> Option<Value> {
        self.evaluate_at_with_initial(site, config, watcher, Value::Null)
    }

    fn evaluate_at_with_initial(
        &mut self,
        site: Site,
        config: &Json,
        watcher: Option<StateKey>,
        initial: Value,
    ) -> Option<Value> {
        self.evaluate_selected_at(site, config, watcher, initial, false)
    }

    fn evaluate_selected_at(
        &mut self,
        site: Site,
        config: &Json,
        watcher: Option<StateKey>,
        initial: Value,
        scalar: bool,
    ) -> Option<Value> {
        let mut context = InstanceContext {
            machine: self.machine,
            world: self.world,
            site,
            reads: Vec::new(),
            foreign_reads: Vec::new(),
            parameters: self.parameters.as_deref(),
            watcher,
            read_native: false,
            untrusted_native: false,
        };
        let result = if scalar {
            script_expr::evaluate_scalar_with_initial(config, &mut context, initial)
        } else {
            script_expr::evaluate_with_initial(config, &mut context, initial)
        };
        let reads = context.reads;
        let foreign_reads = context.foreign_reads;
        let read_native = context.read_native;
        if context.untrusted_native {
            // Like the reference, the record outlives the activation.
            if let Some(state) = watcher.and_then(|key| self.state_mut(key)) {
                state.untrusted_native = true;
            }
        }
        if let Some(watcher) = watcher.filter(|&key| {
            self.state(key)
                .is_some_and(|state| state.active && !state.ending)
        }) {
            for key in reads {
                self.watch(key, watcher);
            }
            for key in foreign_reads {
                let watchers = self.machine.foreign_watchers.entry(key).or_default();
                if !watchers.contains(&watcher) {
                    watchers.push(watcher);
                }
            }
            if let Some(start) = self
                .state(watcher)
                .filter(|state| {
                    read_native
                        && matches!(
                            state.kind,
                            StateKind::BooleanSwitch(_)
                                | StateKind::Switch { .. }
                                | StateKind::Watch
                                | StateKind::AiBehavior { .. }
                                | StateKind::ExplosionContact
                                | StateKind::MovementMod
                        )
                })
                .map(|state| state.start_ms)
            {
                self.register_tick(watcher, start);
            }
        }
        match result {
            Ok(value) => Some(value),
            Err(error) => {
                self.report(site, Issue::Expression(error));
                None
            }
        }
    }

    fn watch(&mut self, key: VarKey, watcher: StateKey) {
        let watchers = self.machine.watchers.entry(key).or_default();
        if !watchers.contains(&watcher) {
            watchers.push(watcher);
        }
    }

    fn write(&mut self, key: VarKey, value: Value) -> Result<(), InitFault> {
        let old = self.machine.value(key, None);
        self.set_base(key, value);
        let outputs: Vec<_> = self
            .machine
            .links
            .get(&key)
            .into_iter()
            .flatten()
            .filter_map(|link| match link.output {
                LinkValue::Chase => Some((link.state, false)),
                LinkValue::Cooldown => Some((link.state, true)),
                _ => None,
            })
            .collect();
        for (state, cooldown) in outputs {
            if !self
                .state(state)
                .is_some_and(|state| state.active && state.linked_variable == Some(key))
            {
                continue;
            }
            if self.output_write_depth == MAX_PLUG_DEPTH {
                return Err(InitFault::PlugDepth);
            }
            self.output_write_depth += 1;
            let result = if cooldown {
                let node = self
                    .graph(state.instance)
                    .state(usize::from(state.raw))
                    .expect("ability state");
                self.restart_cooldown(state, node, self.output_write_depth)
            } else {
                self.chase_write(state, key)
            };
            self.output_write_depth -= 1;
            result?;
        }
        self.notify_value(key, &old, None)
    }

    fn set_base(&mut self, key: VarKey, value: Value) {
        match key {
            VarKey::Entity(id) => self.machine.snapshot.variables.insert(id, value),
            VarKey::Instance(instance, id) => self.machine.snapshot.instances[&instance]
                .variables
                .insert(id, value),
        };
    }

    fn notify_value(
        &mut self,
        key: VarKey,
        old: &Value,
        source: Option<StateKey>,
    ) -> Result<(), InitFault> {
        let changed = !same(old, &self.machine.value(key, None));
        let watchers = self.machine.watchers.get(&key).cloned().unwrap_or_default();
        for watcher in watchers {
            let Some(state) = self
                .state(watcher)
                .filter(|state| state.active && !state.ending && !state.pending_change)
            else {
                continue;
            };
            if matches!(state.kind, StateKind::Stack { .. }) && state.linked_variable == Some(key) {
                if let Some(source) = source {
                    let above = self.machine.links.get(&key).is_some_and(|entries| {
                        entries
                            .iter()
                            .skip_while(|entry| entry.state != source)
                            .skip(1)
                            .any(|entry| entry.state == watcher)
                    });
                    if !above {
                        continue;
                    }
                }
            } else if !changed {
                continue;
            }
            self.state_mut(watcher)
                .expect("active watcher")
                .pending_change = true;
            self.post(watcher.instance, Event::Changed(watcher.raw))?;
        }
        Ok(())
    }

    fn follow(
        &mut self,
        id: u16,
        node: &Node,
        path: &str,
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        self.spend("plug traversal")?;
        let mut taken = false;
        let graph = self.graph(id);
        for (position, input) in node.links(path) {
            self.spend("plug target")?;
            let Some(position) = position else {
                // The reference treats an unresolved plug as taken.
                taken = true;
                continue;
            };
            let Some(target) = graph.node(position) else {
                self.report(self.site(id, node), Issue::MissingNode(position));
                taken = true;
                continue;
            };
            if target.client_only {
                continue;
            }
            if target.is_state() {
                if let Some(raw) = target.state {
                    let key = StateKey { instance: id, raw };
                    if target.class == "STU_01178155" && input == "m_inPlug" {
                        let state = self.machine.snapshot.instances[&id]
                            .states
                            .entry(raw)
                            .or_insert_with(|| State::dormant(StateKind::ActiveQuery));
                        let output = if state.active {
                            "m_7273C81B"
                        } else {
                            "m_C6F3744E"
                        };
                        self.follow(id, target, output, Some(key), depth + 1)?;
                        taken = true;
                        continue;
                    }
                    if target.class == "STU_7B9E8909" && input == "m_inPlug" {
                        // The bridge consumes its input even when inactive. Only an
                        // explicit begin request can open it.
                        let state = self.machine.snapshot.instances[&id]
                            .states
                            .entry(raw)
                            .or_insert_with(|| State::dormant(StateKind::Plain));
                        if state.active {
                            self.follow(id, target, "m_outPlug", Some(key), depth + 1)?;
                        }
                        taken = true;
                        continue;
                    }
                    if graph.index == 0x0cba
                        && target.class == "STU_5E805B5A"
                        && input == "m_F4DD996D"
                    {
                        let key = StateKey { instance: id, raw };
                        if self.state(key).is_some_and(|state| state.active) {
                            self.select_recent_input(key, target, depth + 1)?;
                        }
                        taken = true;
                        continue;
                    }
                    if target.class == "STUStatescriptStateAbility" && input == "m_3B7BED2E" {
                        self.stop_ability(key, target, depth + 1)?;
                        taken = true;
                        continue;
                    }
                    let event = if input == ABORT_PLUG {
                        Event::Abort(raw)
                    } else {
                        Event::Begin(raw)
                    };
                    self.post(id, event)?;
                    taken = true;
                }
            } else {
                let caller = if target.is_action() { None } else { caller };
                let linked = if target.class == "STU_1113EBD5"
                    && matches!((graph.index, target.pos), (0x0cc0, 7) | (0x022f, 8 | 12))
                {
                    self.join_inputs(id, target, (node.pos, path), caller, depth + 1)?
                } else {
                    self.run_node(id, target, caller, depth + 1)?
                };
                taken |= linked;
            }
        }
        Ok(taken)
    }

    fn join_inputs(
        &mut self,
        id: u16,
        node: &Node,
        source: (u16, &str),
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        self.spend("join input")?;
        let states = array_field(&node.fields, "m_122DF0CA")
            .iter()
            .map(|raw| raw.as_u64().and_then(|raw| u16::try_from(raw).ok()))
            .collect::<Option<Vec<_>>>();
        let required = field(&node.fields, "m_B24D1794").as_u64();
        let (Some(states), Some(required @ 2..=64)) = (states, required) else {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("join configuration"),
            );
            return Ok(false);
        };
        let inputs = &mut self.machine.snapshot.instances[&id].join_inputs;
        let arrived = inputs.entry(node.pos).or_default();
        if !arrived.insert((source.0, source.1.to_owned())) {
            return Ok(false);
        }
        let count = arrived.len() as u64;
        if count == 1 {
            for raw in states {
                self.spend("join completion check")?;
                let key = StateKey { instance: id, raw };
                if Some(key) != caller && self.state(key).is_some_and(|state| state.active) {
                    self.post(id, Event::CheckTransitions(raw))?;
                }
            }
            return Ok(false);
        }
        if count < required {
            return Ok(false);
        }
        inputs.remove(&node.pos);
        if !self.follow(id, node, "m_outPlug", caller, depth)? {
            return Ok(false);
        }
        for raw in states {
            self.spend("join state abort")?;
            if self
                .state(StateKey { instance: id, raw })
                .is_some_and(|state| state.active)
            {
                self.post(id, Event::Abort(raw))?;
            }
        }
        Ok(true)
    }

    fn run_node(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        self.spend("node execution")?;
        match node.class.as_str() {
            _ if is_entry(node) => self.follow(id, node, "m_outPlug", None, depth),
            "STU_BB4AC733" => self.follow(id, node, "m_outPlug", None, depth),
            "STUStatescriptActionCreateEntity" => self.create_entity(id, node, depth),
            "STU_6E18DEA0"
                if self.graph(id).index == 0x0ca7
                    && field(&node.fields, "m_A9220BE5").is_null() =>
            {
                let handle =
                    self.creation_scalar(id, node, field(&node.fields, "m_EDB2BD2A"), Value::Null)?;
                if !matches!(handle, Some(Value::Null | Value::Integer(0))) {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("gate animation handle"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_523EE38D"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x0c89, 43) | (0x0c93, 11) | (0x0cbc, 36 | 38 | 57 | 58)
                ) =>
            {
                let Some(Value::Entity(entity)) =
                    self.creation_scalar(id, node, field(&node.fields, "m_entity"), Value::Null)?
                else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("corpse retirement target"),
                    );
                    return Ok(false);
                };
                let phase_exit = self.graph(id).index == 0x0cbc && matches!(node.pos, 57 | 58);
                if !self
                    .world
                    .request_corpse_retirement(self.site(id, node), entity, phase_exit)
                {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("corpse retirement service"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_D38D0035" | "STU_6E5C5F66"
                if node.class == "STU_D38D0035"
                    || matches!(
                        (self.graph(id).index, node.pos),
                        (0x0cb3, 1) | (0x0cba, 52) | (0x0c95, 16)
                    ) =>
            {
                // These 104319 handlers forward without changing graph state.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_147B34C6" if (self.graph(id).index, node.pos) == (0x180a, 22) => {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_88DE7AD3" if (self.graph(id).index, node.pos) == (0x0c8c, 72) => {
                // In 104319 this base action forwards the authored phase handoff.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_0F647E55" => {
                let Some(Value::Integer(team @ -1..=4)) =
                    self.evaluate(id, node, field(&node.fields, "m_67465E75"), None)
                else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("entity team index"),
                    );
                    return Ok(false);
                };
                let Some(targets) = self.evaluate(id, node, field(&node.fields, "m_targets"), None)
                else {
                    return Ok(false);
                };
                let targets = match targets {
                    Value::Array(values) => values,
                    value => vec![value],
                };
                let site = self.site(id, node);
                for target in targets {
                    if let Value::Entity(entity) = target {
                        if script_expr::valid_entity(entity)
                            && !self.world.assign_team(site, entity, team as i8)
                        {
                            self.report(site, Issue::UnsupportedField("entity team assignment"));
                            return Ok(false);
                        }
                    }
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_4C3509FB" | "STU_CBE8BDAA" => self.insert_array(id, node, caller, depth),
            "STUStatescriptActionPlayScript" => self.play_script(id, node, depth),
            "STU_1C2CC6D5" => {
                let destination = field(&node.fields, "m_26E3B366");
                match script_expr::dynamic(destination) {
                    Ok(Some(target)) => {
                        if target.id != 0 {
                            self.write(source_key(id, target), Value::Array(Vec::new()))?;
                        }
                    }
                    Ok(None) if destination.is_null() => {}
                    Ok(None) => {
                        self.report(
                            self.site(id, node),
                            Issue::UnsupportedField("array clear target"),
                        );
                        return Ok(false);
                    }
                    Err(error) => {
                        self.report(self.site(id, node), Issue::Expression(error));
                        return Ok(false);
                    }
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_DA54F8F7" => {
                let output = match script_expr::dynamic(field(&node.fields, "m_26E3B366")) {
                    Ok(Some(output)) if output.id != 0 => output,
                    _ => {
                        self.report(
                            self.site(id, node),
                            Issue::UnsupportedField("array removal destination"),
                        );
                        return Ok(false);
                    }
                };
                let target = source_key(id, output);
                if let Value::Array(mut values) = self.machine.value(target, None) {
                    let index = self
                        .evaluate_at_with_initial(
                            self.site(id, node),
                            field(&node.fields, "m_index"),
                            None,
                            Value::Integer(-1),
                        )
                        .map(|value| script_expr::to_int(&value, -1))
                        .unwrap_or(-1);
                    if let Ok(index) = usize::try_from(index) {
                        if index < values.len() {
                            for _ in index..values.len() {
                                self.spend("ordered array removal")?;
                            }
                            values.remove(index);
                            self.write(target, Value::Array(values))?;
                        }
                    }
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STUStatescriptActionSetVar" => {
                if !self.store(id, node, &node.fields, true)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_42578342"
                if node.is_action()
                    && (participant_filter(node).is_some()
                        || (self.graph(id).index, node.pos) == (0x0c95, 9)) =>
            {
                if !self.collect_targets(id, node)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STUStatescriptActionEffect"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x01c7, 74) | (0x0257, 32) | (0x01c6, 37 | 38)
                ) && script_expr::native_shape(
                    field(&node.fields, "m_3E3E203A"),
                    "STU_B5A0CAF0",
                    None,
                ) && asset_guid(field(field(&node.fields, "m_effect"), "m_effect"))
                    == Some(match self.graph(id).index {
                        0x01c7 => 0x0300_0000_0000_1ba9,
                        0x01c6 if node.pos == 37 => 0x0300_0000_0000_03e0,
                        0x01c6 => 0x0300_0000_0000_05a8,
                        _ => 0x0300_0000_0000_330f,
                    })
                    && field(field(&node.fields, "m_effect"), "m_resourceKey").is_null()
                    && field(&node.fields, "m_dataFlow").is_null() =>
            {
                // The Python reference advances these local effects without
                // a server-side effect entity or a graph value to replicate.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_6E5C5F66" | "STUStatescriptActionCriteria"
                if matches!((self.graph(id).index, node.pos), (0x0257, 13 | 9)) =>
            {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_187D5125" => {
                let graph = self.graph(id);
                let abilities: Vec<_> = self.machine.snapshot.instances[&id]
                    .states
                    .iter()
                    .filter_map(|(&raw, state)| {
                        (state.active && matches!(state.kind, StateKind::Ability { .. }))
                            .then(|| graph.state(usize::from(raw)).map(|node| (raw, node)))
                            .flatten()
                    })
                    .collect();
                for (raw, ability) in abilities {
                    self.stop_ability(StateKey { instance: id, raw }, ability, depth + 1)?;
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STUStatescriptActionSendGameMessage" => {
                if !self.send_game_message(id, node)? {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_57692020" if node.is_action() => {
                let site = self.site(id, node);
                let message = node
                    .field("m_gameMessage.m_gameMessage")
                    .and_then(asset_guid);
                let result = (site.graph, node.pos, message)
                    == (
                        0x0c8c,
                        84,
                        Some(crate::junkenstein_messages::ENCOUNTER_RESULT),
                    );
                let victory_view = site.graph == 0x0cec
                    && matches!(site.node, Some(2 | 3))
                    && message == Some(crate::junkenstein_messages::VICTORY_VIEW);
                if (!result && !victory_view && !field(&node.fields, "m_params").is_null())
                    || !field(&node.fields, "m_8B34E9C3").is_null()
                {
                    self.report(site, Issue::UnsupportedField("routed game message targets"));
                    return Ok(false);
                }
                let Some(parameters) = self.game_message_parameters(id, node)? else {
                    return Ok(false);
                };
                let Some(message) = message
                    .filter(|&message| self.world.route_game_message(site, message, &parameters))
                else {
                    self.report(site, Issue::UnsupportedField("routed game message targets"));
                    return Ok(false);
                };
                if result {
                    self.machine.queue_entries_from(
                        self.graphs,
                        None,
                        Some(site.entity),
                        message,
                        parameters,
                        self.machine.now_ms,
                    )?;
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_C43F9944" if (self.graph(id).index, node.pos) == (0x180a, 28) => {
                let site = self.site(id, node);
                let message = crate::junkenstein_messages::POST_MATCH_COMPLETE;
                if node
                    .field("m_gameMessage.m_gameMessage")
                    .and_then(asset_guid)
                    != Some(message)
                    || !field(&node.fields, "m_params").is_null()
                    || !self
                        .world
                        .route_game_message(site, message, &Variables::new())
                {
                    self.report(site, Issue::UnsupportedField("post-match completion"));
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if (self.graph(id).index, node.pos) == (0x0cec, 11)
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_06f2)
                    && is_self_target(node, "m_targets")
                    && field(&node.fields, "m_condition").is_null()
                    && field(&node.fields, "m_params").is_null()
                    && field(&node.fields, "m_8B34E9C3").is_null() =>
            {
                // Client-only cue: the pinned server handler only forwards.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if self.graph(id).index == 0x1188
                    && matches!(node.pos, 58 | 59)
                    && node.is_action()
                    && node.is_networked()
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_0fce)
                    && script_expr::native_shape(
                        field(&node.fields, "m_targets"),
                        "STU_A3DEF368",
                        None,
                    ) =>
            {
                // The authored HUD announcement targets clients. Like the
                // reference client-message action, it has no server effect;
                // the client VM handles presentation from its own UI graph.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if self.graph(id).index == 0x0019
                    && matches!(node.pos, 48 | 50)
                    && is_self_target(node, "m_targets")
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_0173) =>
            {
                // Reload timing comes from the animation state. The message's
                // receiver is client-only.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_100EF8C2"
                if self.graph(id).index == 0x0c8c
                    && node.is_action()
                    && node.is_networked()
                    && node.remote >= 0
                    && matches!(
                        (
                            node.pos,
                            node.field("m_gameMessage.m_gameMessage")
                                .and_then(asset_guid)
                        ),
                        (265, Some(0x0240_0000_0000_06e0))
                            | (267 | 268, Some(0x0240_0000_0000_06e1))
                    ) =>
            {
                if !self.world.queue_mode_action(self.site(id, node)) {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("mode action delivery"),
                    );
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            // These placements resolve to the 104319 base action.
            "STU_0FC6DAEA" if self.graph(id).index == 0x0ca5 && matches!(node.pos, 66..=69) => {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_9F3FE778"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x0ce2, 7) | (0x0019, 38) | (0x0079, 8 | 14)
                ) =>
            {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_C43F9944"
                if (self.graph(id).index, node.pos) == (0x0c8c, 236)
                    && field(&node.fields, "m_condition").is_null()
                    && field(&node.fields, "m_params").is_null()
                    && node
                        .field("m_gameMessage.m_gameMessage")
                        .and_then(asset_guid)
                        == Some(0x0240_0000_0000_08f9) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_C43F9944"
                if self.graph(id).index == 0x0ce0
                    && node.is_action()
                    && field(&node.fields, "m_condition").is_null()
                    && field(&node.fields, "m_params").is_null()
                    && matches!(
                        (
                            node.pos,
                            node.field("m_gameMessage.m_gameMessage")
                                .and_then(asset_guid)
                        ),
                        (71, Some(0x0240_0000_0000_08e1)) | (72..=76, Some(0x0240_0000_0000_08e2))
                    ) =>
            {
                // The 104319 dedicated script VM follows the action's continuation.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            // Python's no_op. Networked effects are remote-synced nodes; like
            // Python, the server never flags their remote slot. The role of a
            // dynamic m_37B02425 is unknown, so those effects stay unsupported.
            "STUStatescriptActionEffect" if field(&node.fields, "m_37B02425").is_null() => {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_CEBDAF17"
                if self.graph(id).index == 0x0b8b
                    && matches!(node.pos, 50 | 51 | 74)
                    && node.server_only
                    && node.remote < 0
                    && is_self_target(node, "m_targets") =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_43B63AD0"
                if self.graph(id).index == 0x0b8b
                    && matches!(node.pos, 14 | 16)
                    && node.is_action()
                    && node.remote < 0 =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_FF3471E9" if (self.graph(id).index, node.pos) == (0x224e, 31) => {
                let [row] = array_field(&node.fields, "m_BC5E91CF") else {
                    return Ok(false);
                };
                if row.get("$").and_then(Json::as_str) != Some("STU_20E18047")
                    || row.get("m_A9561CA0").and_then(Json::as_u64) != Some(1)
                    || !field(row, "m_priority").is_null()
                {
                    return Ok(false);
                }
                let Some(Value::Float(low)) =
                    self.evaluate(id, node, field(row, "m_8AD739CE"), None)
                else {
                    return Ok(false);
                };
                let Some(Value::Float(high)) =
                    self.evaluate(id, node, field(row, "m_8213EB22"), None)
                else {
                    return Ok(false);
                };
                // Slot1 changes horizontal magnitude; a zero lower bound is a cap.
                if low != 0.0
                    || !self.world.edit_velocity(
                        self.site(id, node),
                        self.machine.frame,
                        Value::Float(high),
                        true,
                    )
                {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_FF3471E9" if self.graph(id).index == 0x01f0 && matches!(node.pos, 8 | 11 | 17) => {
                let rows = array_field(&node.fields, "m_BC5E91CF");
                let [row] = rows else { return Ok(false) };
                let cap = node.pos == 17;
                let (class, selector) = if cap {
                    ("STU_748FC65A", "m_A9561CA0")
                } else {
                    ("STU_95A10547", "m_CFEC5F76")
                };
                if row.get("$").and_then(Json::as_str) != Some(class)
                    || row.get(selector).and_then(Json::as_u64) != Some(1)
                    || !field(row, "m_priority").is_null()
                {
                    return Ok(false);
                }
                let Some(value) = self.evaluate(id, node, field(row, "m_9B7A63EA"), None) else {
                    return Ok(false);
                };
                if !self
                    .world
                    .edit_velocity(self.site(id, node), self.machine.frame, value, cap)
                {
                    return Ok(false);
                }
                self.follow(id, node, "m_outPlug", caller, depth)
            }
            "STU_D087B558"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x022f, 121) | (0x01f0, 43)
                ) && is_self_target(node, "m_target")
                    && field(&node.fields, "m_instigator").is_null()
                    && node.field("m_D7DDEEAD.m_value").and_then(Json::as_u64)
                        == Some(if self.graph(id).index == 0x01f0 {
                            120
                        } else {
                            40
                        }) =>
            {
                // Continue through this authored action as the reference fallback does.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_6FEE499E"
                if (self.graph(id).index, node.pos) == (0x01f0, 31)
                    && field(&node.fields, "m_93CDFD2F").as_u64() == Some(1) =>
            {
                // Pinned Python Roll fallback; the native service remains unverified.
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            "STU_CEBDAF17"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x0079, 22) | (0x157c, 44 | 134 | 137 | 148..=150 | 153..=155 | 192 | 193 | 196)
                ) =>
            {
                // The healer is a scene NPC with no player stats record.
                self.follow(id, node, "m_outPlug", caller, depth)?;
                Ok(true)
            }
            // Python's no_op for stat criteria.
            "STUStatescriptActionCriteria" | "STU_6E5C5F66" => {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_545AB3B8" => {
                // The captured 104319 registry has no exact action binding:
                // its Action base forwards without evaluating loadout/target.
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            // The reference has no model for this action: its fallback only
            // follows m_outPlug and reports the path as taken. The native
            // effect, if any, is unverified, so only the payload-free shape
            // every build 104319 instance has is accepted.
            "STU_E9694EFD" if is_bare_header_action(node) => {
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_22926252"
                if field(&node.fields, "m_67B81D25").is_null()
                    && field(&node.fields, "m_EB35D2F1").is_null() =>
            {
                self.post(id, Event::ExitInstance)?;
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            "STU_12F5D52D" => {
                let mut complete = true;
                for item in array_field(&node.fields, "m_143D4C5B") {
                    if item.is_object() {
                        complete = self.store(id, node, item, false)? && complete;
                    }
                }
                if complete {
                    self.follow(id, node, "m_outPlug", None, depth)?;
                }
                Ok(complete)
            }
            "STU_821116A6" => {
                let Some(container) =
                    self.evaluate(id, node, field(&node.fields, "m_814F8EC3"), caller)
                else {
                    return Ok(false);
                };
                let Some(value) = self.evaluate(id, node, field(&node.fields, "m_value"), caller)
                else {
                    return Ok(false);
                };
                if !script_expr::contains(&container, &value) {
                    return self.follow(id, node, "m_falsePlug", caller, depth);
                }
                Ok(self.follow(id, node, "m_truePlug", caller, depth)?
                    || (node.links("m_truePlug").next().is_none()
                        && node.links("m_falsePlug").next().is_none()))
            }
            "STU_3387AB5D" | "STU_7DEB522D" => {
                let condition = if node.class == "STU_7DEB522D" {
                    self.logical_button_id(id, node)
                        .is_some_and(|button| self.effective_button_held(button))
                } else {
                    let active = if (self.graph(id).index, node.pos) == (0x0c87, 21) {
                        self.world.objective_route_active(self.site(id, node))
                    } else {
                        None
                    };
                    if let Some(active) = active {
                        active
                    } else {
                        let Some(value) =
                            self.evaluate(id, node, field(&node.fields, "m_condition"), caller)
                        else {
                            return Ok(false);
                        };
                        truthy(&value)
                    }
                };
                if !condition {
                    return self.follow(id, node, "m_falsePlug", caller, depth);
                }
                Ok(self.follow(id, node, "m_truePlug", caller, depth)?
                    || (node.links("m_truePlug").next().is_none()
                        && node.links("m_falsePlug").next().is_none()))
            }
            "STU_4E2BCAAB" => {
                let value = self.evaluate(id, node, field(&node.fields, "m_AA6E611E"), caller);
                let seconds = value
                    .and_then(pool_number)
                    .filter(|seconds| *seconds >= 0.0 && *seconds < 2_147_483.0);
                let state = caller.or_else(|| {
                    self.machine.snapshot.instances[&id]
                        .parent
                        .map(|(instance, raw)| StateKey { instance, raw })
                });
                let ready = if let Some(seconds) = seconds {
                    let delay = ((seconds * 1000.0) as f32).round() as u64;
                    let ready = state.and_then(|key| self.state(key)).is_some_and(|state| {
                        self.machine.now_ms.saturating_sub(state.start_ms) >= delay
                    });
                    if !ready {
                        if let Some(key) = caller {
                            if let Some(state) = self.state(key) {
                                let due = state.start_ms.saturating_add(delay);
                                self.machine.queues.retain(key.instance,|_,event|!matches!(event,Event::Timer(raw,0) if *raw==key.raw));
                                self.post_after(
                                    key.instance,
                                    due.saturating_sub(self.machine.now_ms),
                                    Event::Timer(key.raw, 0),
                                )?;
                            }
                        }
                    }
                    ready
                } else {
                    false
                };
                if !ready {
                    return self.follow(id, node, "m_falsePlug", caller, depth);
                }
                Ok(self.follow(id, node, "m_truePlug", caller, depth)?
                    || (node.links("m_truePlug").next().is_none()
                        && node.links("m_falsePlug").next().is_none()))
            }
            "STUStatescriptActionSwitch" => {
                let Some(value) = self.evaluate(id, node, field(&node.fields, "m_262AE982"), None)
                else {
                    return Ok(false);
                };
                let mut path = "m_CD38D5DB".to_owned();
                for (number, case) in array_field(&node.fields, "m_FDD34C25").iter().enumerate() {
                    if !case.is_object() {
                        continue;
                    }
                    let Some(candidate) = self.evaluate(id, node, field(case, "m_value"), None)
                    else {
                        return Ok(false);
                    };
                    if equal(&value, &candidate) {
                        path = format!("m_FDD34C25[{number}]");
                        break;
                    }
                }
                self.follow(id, node, &path, None, depth)?;
                self.follow(id, node, "m_outPlug", None, depth)?;
                Ok(true)
            }
            _ => {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedAction(node.class.clone()),
                );
                Ok(false)
            }
        }
    }

    fn assignment_group(
        &mut self,
        id: u16,
        node: &Node,
        config: &Json,
    ) -> Result<Option<Value>, InitFault> {
        let mut pending = vec![(config, 0)];
        let mut values = Vec::new();
        while let Some((config, depth)) = pending.pop() {
            self.spend("assignment config group")?;
            if depth >= 64 {
                self.report(
                    self.site(id, node),
                    Issue::Expression(script_expr::EvalError::NestingExceeded),
                );
                return Ok(None);
            }
            if config.is_null() {
                continue;
            }
            if script_expr::native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                let configs = field(config, "m_configVars");
                if configs.is_null() {
                    continue;
                }
                let Some(configs) = configs.as_array() else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("assignment config group"),
                    );
                    return Ok(None);
                };
                pending.extend(configs.iter().rev().map(|config| (config, depth + 1)));
                continue;
            }
            let Some(value) = self.evaluate(id, node, config, None) else {
                return Ok(None);
            };
            if value == Value::Null {
                continue;
            }
            let mut items = vec![(value, depth)];
            while let Some((value, depth)) = items.pop() {
                self.spend("assignment value array")?;
                if depth >= 64 {
                    self.report(
                        self.site(id, node),
                        Issue::Expression(script_expr::EvalError::NestingExceeded),
                    );
                    return Ok(None);
                }
                if let Value::Array(array) = value {
                    items.extend(array.into_iter().rev().map(|value| (value, depth + 1)));
                } else {
                    values.push(value);
                }
            }
        }
        Ok(Some(Value::Array(values)))
    }

    fn store(
        &mut self,
        id: u16,
        node: &Node,
        fields: &Json,
        indexed: bool,
    ) -> Result<bool, InitFault> {
        let config = field(fields, "m_value");
        let site = self.site(id, node);
        let value = if script_expr::native_shape(config, "STU_06610EBC", Some("m_configVars")) {
            self.assignment_group(id, node, config)?
        } else {
            self.evaluate_at_with_initial(site, config, None, Value::Integer(0))
        };
        let Some(value) = value else { return Ok(false) };
        let destination = match script_expr::destination(field(fields, "m_out_Var")) {
            Ok(Some(target)) => target,
            Ok(None) => return Ok(true),
            Err(error) => {
                self.report(site, Issue::Expression(error));
                return Ok(false);
            }
        };
        let target = source_key(id, destination.variable);
        if !destination.path.is_empty() {
            if self
                .machine
                .links
                .get(&target)
                .is_some_and(|links| !links.is_empty())
            {
                self.report(
                    site,
                    Issue::UnsupportedField("nested destination linked storage"),
                );
                return Ok(false);
            }
            if indexed && (!field(fields, "m_index").is_null() || !field(fields, "m_key").is_null())
            {
                self.report(
                    site,
                    Issue::UnsupportedField("combined expression/index destination"),
                );
                return Ok(false);
            }
            let mut path = Vec::new();
            for part in destination.path {
                path.push(match part {
                    script_expr::DestinationPart::Index(config) => {
                        let Some(index) = self.evaluate(id, node, &config, None) else {
                            return Ok(false);
                        };
                        crate::script_destination::Step::Index(script_expr::to_int(&index, 0) as u16)
                    }
                    script_expr::DestinationPart::Field(field) => crate::script_destination::Step::Field(field),
                });
            }
            let mut root = self.machine.value(target, None);
            let mut cost = 0;
            if let Err(error) =
                crate::script_destination::assign(&mut root, &path, value, &mut cost)
            {
                self.report(site, Issue::UnsupportedField(error));
                return Ok(false);
            }
            self.machine.work_left =
                self.machine
                    .work_left
                    .checked_sub(cost)
                    .ok_or(InitFault::WorkBudget {
                        limit: self.machine.max_work,
                        operation: "nested destination storage",
                    })?;
            self.write(target, root)?;
            return Ok(true);
        }
        if indexed && !field(fields, "m_key").is_null() {
            self.report(site, Issue::UnsupportedField("SetVar map key"));
            return Ok(false);
        }
        let index = field(fields, "m_index");
        if indexed && !index.is_null() {
            let index = if index.is_object() {
                let Some(value) = self.evaluate(id, node, index, None) else {
                    return Ok(false);
                };
                Some(script_expr::to_int(&value, 0))
            } else {
                index
                    .as_i64()
                    .or_else(|| index.as_f64().map(|value| value as i64))
                    .or_else(|| index.as_bool().map(i64::from))
                    .or_else(|| index.as_str()?.trim().parse().ok())
            };
            let Some(index) = index else {
                self.report(site, Issue::UnsupportedField("SetVar array index"));
                return Ok(false);
            };
            if let Ok(index) = usize::try_from(index) {
                let mut items = match self.machine.value(target, None) {
                    Value::Array(items) => items,
                    Value::Null => Vec::new(),
                    value => vec![value],
                };
                if index >= usize::from(u16::MAX) {
                    self.report(
                        site,
                        Issue::UnsupportedField("SetVar array exceeds the wire count"),
                    );
                    return Ok(false);
                }
                if index >= items.len() {
                    let extra = index + 1 - items.len();
                    self.machine.work_left =
                        self.machine
                            .work_left
                            .checked_sub(extra)
                            .ok_or(InitFault::WorkBudget {
                                limit: self.machine.max_work,
                                operation: "SetVar array extension",
                            })?;
                    items.resize(index + 1, Value::Null);
                }
                items[index] = value;
                self.write(target, Value::Array(items))?;
                return Ok(true);
            }
        }
        self.write(target, value)?;
        Ok(true)
    }

    fn client_pulse(&self, id: u16, count: u8) -> StateKind {
        let now = self.machine.now_ms;
        let start = self.machine.snapshot.instances[&id].start_ms;
        StateKind::ClientPulse {
            count: count.wrapping_add(1),
            fresh_at: (now / FRAME_MS > start / FRAME_MS).then_some(now),
        }
    }

    fn begin(&mut self, key: StateKey, depth: usize) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(node) = self.graph(id).state(usize::from(key.raw)) else {
            self.report(
                Site {
                    entity: self.machine.snapshot.entity,
                    instance: id,
                    graph: self.graph(id).index,
                    node: None,
                },
                Issue::MissingState(key.raw),
            );
            return Ok(());
        };
        if let Some(state) = self.state(key).filter(|state| state.active) {
            let kind = state.kind;
            if kind == StateKind::ForCode {
                self.publish_for_code(key, node)?;
            }
            if kind == StateKind::ExplosionContact {
                self.refresh_contact(key, node)?;
            }
            if kind == StateKind::PhaseScriptList {
                self.refresh_phase_scripts(key, node)?;
            }
            if let StateKind::ClientPulse { count, .. } = kind {
                let pulse = self.client_pulse(id, count);
                self.state_mut(key).expect("active client pulser").kind = pulse;
            }
            if configured(node, "m_0DBAFD7F") {
                match kind {
                    StateKind::Wait => {
                        self.machine
                            .queues
                            .retain(id, |_, event| event.state() != Some(key.raw));
                        self.state_mut(key).expect("active wait").start_ms = self.machine.now_ms;
                        self.arm_wait(key, node)?;
                    }
                    StateKind::WaitFrames { .. } => self.arm_frame_wait(key, node)?,
                    _ => (),
                }
            }
            if kind == StateKind::ChaseVar && configured(node, "m_9FF96195") {
                self.finish_chase(key, depth)?;
                self.begin_chase(key, node, depth)?;
            }
            return Ok(());
        }
        let kind = match node.class.as_str() {
            // These client presentation states use the base state lifecycle on the server.
            "STUStatescriptState"
            | "STU_9D7BF987"
            | "STU_B4CD94F4"
            | "STU_90C3BCEA"
            | "STUStatescriptStateDataFlowMapping"
            | "STU_E13B30A8"
            | "STUStatescriptStateUXPresenter" => StateKind::Plain,
            // Build 104319 resolves E9485CAF through the base State typeinfo:
            // registration 160, constructor AA826B0, vtable 8B6BC800.
            "STU_E9485CAF" => StateKind::Plain,
            "STUStatescriptStateAnimatedCamera"
                if (self.graph(id).index, node.pos) == (0x0ca5, 43) =>
            {
                StateKind::Plain
            }
            "STU_96333BBE" | "STU_E8D976BE"
                if matches!(
                    (self.graph(id).index, node.pos, node.class.as_str()),
                    (0x0ced, 0 | 4, "STU_96333BBE") | (0x0ced, 6, "STU_E8D976BE")
                ) && self
                    .world
                    .spatial_binding_owner_available(self.site(id, node))
                    == Some(false) =>
            {
                // 911920 and AE03F0 return before any binding/field access
                // when 978A30 cannot resolve the auxiliary client owner.
                StateKind::Plain
            }
            "STU_33A46033" | "STU_9028E035" | "STU_43994F1B" | "STU_93B8758C" | "STU_9935FBC8"
                if registered_state(self.graph(id).index, node).is_some() =>
            {
                StateKind::Registered { next: None }
            }
            "STU_F08E1453"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x01cb, 85) | (0x0ce0, 39) | (0x0d03, 3)
                ) && is_self_target(node, "m_64C3E07E") =>
            {
                StateKind::Plain
            }
            // The authored zero-valued form inherits the base state reader.
            "STU_D9843704"
                if node.is_networked() && node.remote == -1 && {
                    let value = field(&node.fields, "m_0B528B1E");
                    script_expr::native_shape(value, "STUConfigVarFloat", Some("m_value"))
                        && field(value, "m_value").as_f64() == Some(0.0)
                } =>
            {
                StateKind::Plain
            }
            "STU_BD02E168" if is_client_subgraph_controller(self.graph(id), node) => {
                StateKind::Plain
            }
            // ForCodeSynced publishes to native game-code registration outside
            // the graph. Its VM plugs and empty wire payload use the base state.
            "STU_CD46AF93" | "STU_6A926B21"
                if is_networked_for_code_synced(node)
                    || self.world.for_code_transport(self.site(id, node)) == Some(false) =>
            {
                StateKind::ForCode
            }
            // Tips register their state key with native presentation code.
            // The graph still runs the base state's plugs and lifecycle.
            "STU_F5A99B2A"
                if node.is_networked()
                    && field(&node.fields, "m_tips")
                        .as_array()
                        .is_some_and(|tips| {
                            !tips.is_empty()
                                && tips
                                    .iter()
                                    .all(|tip| field(tip, "$").as_str() == Some("STUTip"))
                        }) =>
            {
                StateKind::Plain
            }
            "STU_7B9E8909" => StateKind::Plain,
            "STU_6A6BBB8D" | "STU_918B3FA9" | "STU_14611C1B" | "STU_200B27C5" | "STU_D52B9026"
            | "STU_4531992F" | "STU_55F72184" | "STU_632B74B9"
                if self.graph(id).index == 0x0c87 =>
            {
                StateKind::Plain
            }
            "STU_724DFBC6" | "STU_531AA630" if self.graph(id).index == 0x0cf3 => StateKind::Plain,
            _ if is_encounter_base_state(self.graph(id).index, node) => StateKind::Plain,
            "STU_B5C87F3D"
                if self
                    .world
                    .local_combat_context_available(self.site(id, node))
                    == Some(false) =>
            {
                StateKind::Plain
            }
            "STU_76F3EAEF"
                if self.graph(id).index == 0x0cbc
                    && matches!(node.pos, 60 | 61 | 62 | 63 | 64 | 110) =>
            {
                StateKind::Plain
            }
            "STU_6CC90892"
                if (self.graph(id).index, node.pos) == (0x0cbc, 129)
                    && is_self_target(node, "m_D7AC2990") =>
            {
                StateKind::SameTeamContact { registered: false }
            }
            "STUStatescriptStateCombatModFilter"
                if asset_guid(field(&node.fields, "m_60703B1E")) == Some(0x0d80_0000_0000_0317)
                    && field(&node.fields, "m_41B10FF2").as_u64() == Some(0) =>
            {
                StateKind::OutgoingDamageFilter { registered: false }
            }
            "STU_40E05F6B" if self.graph(id).index == 0x0ce0 && intro_cue(node).is_some() => {
                StateKind::BossIntroVoice(intro_cue(node).expect("validated Boss intro cue"))
            }
            "STU_40E05F6B"
                if (self.graph(id).index, node.pos) == (0x0c8c, 221) && victory_cue(node) =>
            {
                StateKind::VictoryVoice
            }
            "STU_40E05F6B" if self.graph(id).index == 0x0ca7 && gate_cue(node).is_some() => {
                StateKind::GateVoice(gate_cue(node).expect("validated gate cue"))
            }
            "STU_40E05F6B" => StateKind::VoiceStimulus,
            "STU_DC80F7E6"
                if self.world.outline_context_available(self.site(id, node)) == Some(false) =>
            {
                StateKind::Plain
            }
            "STU_441B50E4"
                if self.graph(id).index == 0x0b8b
                    && matches!(
                        (node.pos, node.field("m_stat").and_then(asset_guid)),
                        (52, Some(0x0860_0000_0000_0466)) | (66, Some(0x0860_0000_0000_05bf))
                    )
                    && node.server_only
                    && field(&node.fields, "m_D32CB484").is_null()
                    && field(&node.fields, "m_stats").is_null()
                    && is_self_target(node, "m_targets")
                    && self.world.player_stats_available(self.site(id, node)) == Some(false) =>
            {
                StateKind::Plain
            }
            "STU_441B50E4"
                if (self.graph(id).index, node.pos) == (0x01a3, 14)
                    && !node.server_only
                    && field(&node.fields, "m_stat").is_null()
                    && field(&node.fields, "m_stats").is_null()
                    && matches!(array_field(&node.fields, "m_D32CB484"),
                        [stat] if asset_guid(stat) == Some(0x0860_0000_0000_037a))
                    && is_self_target(node, "m_targets")
                    && self
                        .world
                        .local_combat_context_available(self.site(id, node))
                        == Some(false) =>
            {
                StateKind::Plain
            }
            "STU_97B1B3EC" if (self.graph(id).index, node.pos) == (0x0c93, 26) => {
                StateKind::AiControlSetting
            }
            "STU_473549CA" | "STU_CF8C7CC5" => StateKind::AiControlOverride { record: None },
            "STU_FC16FF45"
                if matches!((self.graph(id).index, node.pos), (0x0c95, 5) | (0x185d, 57)) =>
            {
                StateKind::AiFacing(None)
            }
            "STU_B61E6681" | "STU_CFA338CB" | "STU_600E3C59" => {
                StateKind::AiBehavior { record: None }
            }
            "STU_066B4605" if self.has_no_ai_control_target(id, node) => StateKind::Plain,
            "STU_066B4605" => StateKind::AiWeaponOverride { record: None },
            "STUStatescriptStateHealthPool" if (self.graph(id).index, node.pos) == (0x0c89, 5) => {
                StateKind::HealthCapacity
            }
            "STUStatescriptStateHealthPool"
                if self.graph(id).index != 0x0cbc && is_base_health_pool(node) =>
            {
                StateKind::HealthPool { pool: None }
            }
            "STUStatescriptStateHealthPool"
                if self.graph(id).index == 0x0cbc && is_wave_health_pool(node) =>
            {
                StateKind::HealthPool { pool: None }
            }
            "STU_B7D2C9C0" => StateKind::DisplayName { binding: None },
            "STU_E56BA8D8" if node.is_networked() && node.remote == -1 => {
                StateKind::TargetTag { effect: None }
            }
            "STU_160F2578"
                if static_target_operand(node).is_some()
                    || participant_filter(node).is_some()
                    || excluded_target_tags(self.graph(id).index, node).is_some() =>
            {
                StateKind::Plain
            }
            "STUStatescriptStateBooleanSwitch" => StateKind::BooleanSwitch(false),
            "STU_B591EBFE" => StateKind::DelayedSwitch {
                current: false,
                undecided: true,
            },
            "STU_DEBD057B" => StateKind::Switch { case: None },
            "STU_01178155" => StateKind::ActiveQuery,
            "STUStatescriptStateGamePadVibration" => StateKind::GamePadVibration,
            "STU_37D754C8" | "STUStatescriptStateSubScript" => StateKind::SubScript { child: None },
            "STUStatescriptStateGameMessageEntry"
                if (self.graph(id).index, node.pos) == (0x0cbc, 133) =>
            {
                StateKind::BossPauseReceiver { receiving: false }
            }
            "STUStatescriptStateGameMessageEntry" => StateKind::MessageIdle,
            "STUStatescriptStateWait" if !configured(node, "m_BF1A93B0") => StateKind::Wait,
            "STU_54FEB21C"
                if (self.graph(id).index, node.pos) == (0x01f0, 14)
                    && ["m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5"]
                        .iter()
                        .all(|name| field(&node.fields, name).is_null())
                    && node.links("m_E5790891").next().is_none() =>
            {
                // With no finish predicate or alternate graph the Python timer
                // cannot finish this state; its parent owns the end plug.
                StateKind::Plain
            }
            "STUStatescriptStatePhysicsExplosion"
                if (self.graph(id).index, node.pos) == (0x01f0, 34) =>
            {
                // This host has no dynamic map rigid bodies. Preserve the
                // networked lifecycle; this does not apply actor damage.
                StateKind::Plain
            }
            "STU_D74D4F47" => StateKind::WaitFrames { target: 0 },
            "STU_2F07848F"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x0cec, 6) | (0x0ce0, 3) | (0x181e, 0)
                ) =>
            {
                StateKind::CinematicTimeScale { boundary_frame: 0 }
            }
            "STU_91EF24A9" => StateKind::AiCandidate { selected: false },
            "STUStatescriptStateLogicalButton" => StateKind::LogicalButton { down: false },
            "STU_E4A30BCF" => StateKind::ButtonBlock(None),
            "STUStatescriptStateStack" | "STU_A97E6999" => StateKind::Stack {
                top: false,
                under: false,
            },
            "STUStatescriptStateAbility" => StateKind::Ability { flags: 0 },
            "STU_316CFEF2" => StateKind::MovementMod,
            "STU_9C9A422A" => StateKind::Defer,
            "STU_75D1C308"
                if self.graph(id).index == 0x01f0
                    && matches!(node.pos, 18 | 22)
                    && field(&node.fields, "m_069C6F7E").as_u64() == Some(1)
                    && field(&node.fields, "m_54613F6A").as_u64()
                        == Some(u64::from(node.pos == 22)) =>
            {
                // Retain the Python fallback's base lifecycle and the native
                // linked wire record. The setting's native physics service is
                // not yet reconstructed; it must not be treated as damage.
                StateKind::RollMovementParameter
            }
            "STU_96F1DBCF" if is_owner_movement_sampler(node) => {
                match self.state(key).map(|state| state.kind) {
                    Some(kind @ StateKind::MovementSample(_)) => kind,
                    _ => StateKind::MovementSample(0),
                }
            }
            "STUStatescriptStateAnim" => StateKind::Anim,
            "STU_B9898052" => {
                let count = match self.state(key).map(|state| state.kind) {
                    Some(StateKind::ClientPulse { count, .. }) => count,
                    _ => 0,
                };
                self.client_pulse(id, count)
            }
            // 0257:11 searches only for the post-throw criteria trace.
            // The field's authoritative recipients are selected separately.
            "STUStatescriptStateTrackTargets"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x0257, 11) | (0x00ca, 90)
                ) =>
            {
                StateKind::EmptyTargets
            }
            "STU_691BFA55" | "STU_7CA2FBAF" => StateKind::Effect,
            "STU_23A00A53" if (self.graph(id).index, node.pos) == (0x0c89, 49) => {
                StateKind::ExplosionContact
            }
            "STUStatescriptStateModifyHealth"
                if (self.graph(id).index, node.pos) == (0x0033, 5) =>
            {
                StateKind::ShieldRecharge
            }
            "STUStatescriptStateModifyHealth"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x0c89, 38 | 46 | 48) | (0x0cbc, 69)
                ) =>
            {
                StateKind::ModifyHealth
            }
            "STUStatescriptStateTrackTargets"
                if matches!(
                    (self.graph(id).index, node.pos),
                    (0x0c89, 47 | 52) | (0x022f, 26 | 86) | (0x006b, 6 | 7 | 121)
                ) =>
            {
                StateKind::TrackedTargets
            }
            "STU_431F3FCC" if (self.graph(id).index, node.pos) == (0x022f, 11) => {
                StateKind::DeadeyeEffects
            }
            "STU_431F3FCC" if (self.graph(id).index, node.pos) == (0x006b, 62) => {
                StateKind::WitchStaffEffect
            }
            "STUStatescriptStateModifyHealth"
                if (self.graph(id).index, node.pos) == (0x0079, 0) =>
            {
                StateKind::WitchStaffHealing
            }
            "STUStatescriptStateTrackRay"
                if self.graph(id).index == 0x157c
                    && matches!(node.pos, 202 | 203)
                    && node.server_only =>
            {
                StateKind::SummonerBeamRay
            }
            "STUStatescriptStateModifyHealth"
                if (self.graph(id).index, node.pos) == (0x157c, 170) && node.server_only =>
            {
                StateKind::SummonerBeamDamage
            }
            "STU_BA4C2296"
                if (self.graph(id).index, node.pos) == (0x157c, 143)
                    && self.world.outline_context_available(self.site(id, node)) == Some(false) =>
            {
                StateKind::Plain
            }
            "STU_431F3FCC"
                if (self.graph(id).index, node.pos) == (0x157c, 200) && node.server_only =>
            {
                // This effect tracks player criteria. The NPC caster has no player stats.
                StateKind::Plain
            }
            "STU_B5C87F3D" if (self.graph(id).index, node.pos) == (0x022f, 85) => {
                // The headless owner has no local combat accumulator.
                StateKind::Plain
            }
            "STU_E3CEF6B9" if (self.graph(id).index, node.pos) == (0x022f, 116) => {
                StateKind::WaitFrames { target: 0 }
            }
            "STU_38EE1100" => persistent_message(self.graph(id).index, node)
                .map_or(StateKind::SendMessage, StateKind::PersistentSend),
            "STUStatescriptStateCosmeticEntity" => StateKind::CosmeticEntity,
            "STUStatescriptStateChaseVar" => StateKind::ChaseVar,
            "STU_77E13026" if (self.graph(id).index, node.pos) == (0x180a, 42) => {
                StateKind::PhaseScriptList
            }
            "STU_CB4905E9" if (self.graph(id).index, node.pos) == (0x180a, 41) => {
                StateKind::PhaseScript
            }
            "STU_4BD2E886" if (self.graph(id).index, node.pos) == (0x180a, 46) => {
                StateKind::PhaseTransition
            }
            "STU_2F4E2E3F" => StateKind::Watch,
            "STU_BA2FCDEB" if self.graph(id).index == 0x0cba => StateKind::SpawnSearch,
            "STU_5E805B5A" if self.graph(id).index == 0x0cba => StateKind::RecentInput,
            "STU_0A6BE10E" if (self.graph(id).index, node.pos) == (0x0cba, 6) => {
                StateKind::LivingBodyArray
            }
            "STUStatescriptStateWeaponVolley" => StateKind::WeaponVolley {
                volleys: 1,
                offset_ms: 0,
                ammo0: 0,
                ammo_var: None,
            },
            _ => {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedState(node.class.clone()),
                );
                return Ok(());
            }
        };
        let previous = self.state(key);
        let activation = previous.map_or(1, |state| state.activation.wrapping_add(1));
        let buffered_press = previous.and_then(|state| state.buffered_press);
        let untrusted_native = previous.is_some_and(|state| state.untrusted_native);
        for raw in raw_indices(array_field(&node.fields, "m_exclusiveStateIndices")) {
            self.end(StateKey { instance: id, raw }, false, depth + 1)?;
            self.drop_begin(id, raw);
        }
        self.machine.snapshot.instances[&id].states.insert(
            key.raw,
            State {
                active: true,
                activation,
                start_ms: self.machine.now_ms,
                buffered_press,
                untrusted_native,
                ..State::dormant(kind)
            },
        );
        match kind {
            StateKind::Registered { .. } => self.register_state(key, node)?,
            StateKind::GamePadVibration => self.post(id, Event::Finish(key.raw))?,
            StateKind::ForCode => self.begin_for_code(key, node)?,
            StateKind::ExplosionContact => self.refresh_contact(key, node)?,
            StateKind::ModifyHealth => self.begin_npc_health_change(key, node)?,
            StateKind::ShieldRecharge => self.begin_shield_recharge(key, node)?,
            StateKind::TrackedTargets => self.refresh_tracked_targets(key, node)?,
            StateKind::DeadeyeEffects => self.refresh_deadeye_effects(key, node)?,
            StateKind::WitchStaffEffect | StateKind::WitchStaffHealing => {
                self.register_tick(key, self.machine.now_ms)
            }
            StateKind::SummonerBeamRay => self.refresh_summoner_beam_ray(key, node)?,
            StateKind::SummonerBeamDamage => self.register_tick(key, self.machine.now_ms),
            StateKind::Plain if node.class == "STU_066B4605" => {
                // The absent AI relation finishes through the current clock's event queue.
                self.post(id, Event::Finish(key.raw))?;
            }
            StateKind::Plain if node.class == "STU_160F2578" => {
                if !self.begin_static_target(key, node)? {
                    return Ok(());
                }
            }
            StateKind::HealthPool { .. } => self.begin_health_pool(key, node)?,
            StateKind::HealthCapacity => self.check_health_capacity(key, node)?,
            StateKind::SameTeamContact { .. } => {
                let site = self.site(id, node);
                if self.world.set_same_team_contact_suppressed(site, true) {
                    self.state_mut(key).expect("active team contact state").kind =
                        StateKind::SameTeamContact { registered: true };
                } else {
                    self.report(site, Issue::UnsupportedField("same-team contact mover"));
                }
            }
            StateKind::OutgoingDamageFilter { .. } => {
                let Some(value) = self.evaluate(id, node, field(&node.fields, "m_50530EAB"), None)
                else {
                    return Ok(());
                };
                let adjustment = match value {
                    Value::Float(number) => number,
                    Value::Integer(number) => number as f64,
                    _ => f64::NAN,
                };
                let site = self.site(id, node);
                if adjustment.is_finite()
                    && self
                        .world
                        .set_outgoing_damage_filter(site, Some(adjustment))
                {
                    self.state_mut(key).expect("active damage filter").kind =
                        StateKind::OutgoingDamageFilter { registered: true };
                } else {
                    self.report(site, Issue::UnsupportedField("outgoing damage filter"));
                }
            }
            StateKind::AiControlSetting => self.update_ai_control_setting(key, node),
            StateKind::AiControlOverride { .. } => self.begin_ai_control_override(key, node)?,
            StateKind::AiFacing(_) => self.update_ai_facing(key, node, None)?,
            StateKind::AiBehavior { .. } => self.update_ai_behavior(key, node)?,
            StateKind::AiWeaponOverride { .. } => self.begin_ai_weapon_override(key, node)?,
            StateKind::TargetTag { .. } => self.begin_target_tag(key, node)?,
            StateKind::DisplayName { .. } => self.begin_display_name(key, node)?,
            StateKind::BooleanSwitch(_) => {
                let Some(value) =
                    self.evaluate(id, node, field(&node.fields, "m_condition"), Some(key))
                else {
                    return Ok(());
                };
                let current = truthy(&value);
                self.state_mut(key).expect("active switch").kind =
                    StateKind::BooleanSwitch(current);
                self.enter_branch(key, node, current, depth)?;
            }
            StateKind::DelayedSwitch { .. } => self.begin_delayed_switch(key, node, depth)?,
            StateKind::Switch { .. } => {
                let Some(case) = self.switch_case(key, node) else {
                    return Ok(());
                };
                self.state_mut(key).expect("active switch").kind = StateKind::Switch { case };
                self.follow(id, node, &case_path(case), Some(key), depth)?;
            }
            StateKind::SubScript { .. } => {
                let child = self.spawn_child(key, node)?;
                self.state_mut(key).expect("active subscript").kind =
                    StateKind::SubScript { child };
            }
            StateKind::MessageIdle | StateKind::BossPauseReceiver { .. } => {
                self.follow(id, node, "m_33DA5163", Some(key), depth)?;
            }
            StateKind::Wait => self.arm_wait(key, node)?,
            StateKind::VoiceStimulus => {
                if !self.begin_voice(key, node)? {
                    return Ok(());
                }
            }
            StateKind::BossIntroVoice(cue) => {
                self.post_after(id, u64::from(cue.delay_ms), Event::Timer(key.raw, 1))?;
            }
            StateKind::GateVoice(_) | StateKind::VictoryVoice => {
                self.post_after(id, 0, Event::Timer(key.raw, 1))?
            }
            StateKind::WaitFrames { .. } => self.arm_frame_wait(key, node)?,
            StateKind::CinematicTimeScale { .. } => self.stamp_cinematic_time_scale(key, node),
            StateKind::AiCandidate { .. } => {
                let site = self.site(id, node);
                if let Some(selected) = self.world.selects_ai_behavior(site) {
                    self.state_mut(key).expect("active AI candidate").kind =
                        StateKind::AiCandidate { selected };
                    if selected {
                        self.follow(id, node, "m_6F92F4BD", Some(key), depth)?;
                    }
                } else {
                    self.report(site, Issue::UnsupportedField("AI behavior selection"));
                }
            }
            StateKind::LogicalButton { .. } => self.enter_button(key, node, depth)?,
            StateKind::ButtonBlock(_) => {
                if let Some(button) = self.logical_button_id(id, node) {
                    self.state_mut(key).expect("active button block").kind =
                        StateKind::ButtonBlock(Some(button));
                    self.machine.button_blocks[usize::from(button)] += 1;
                    self.resync_logical_buttons(button, depth + 1)?;
                }
            }
            StateKind::Stack { .. } => self.begin_stack(key, node, depth)?,
            StateKind::Ability { .. } => {
                self.restart_cooldown(key, node, depth)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::Defer => {
                if let Some(priority) = self
                    .evaluate(id, node, field(&node.fields, "m_priority"), None)
                    .map(|value| script_expr::to_float(&value))
                    .filter(|value| value.is_finite())
                {
                    let pending = self.machine.deferred.entry(id).or_default();
                    pending.push((priority, key.raw));
                    // Stable ties retain registration order; delivery reverses it.
                    pending.sort_by(|a, b| {
                        a.0.partial_cmp(&b.0).expect("validated finite priorities")
                    });
                } else {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("deferred priority"),
                    );
                }
            }
            StateKind::MovementMod => {
                self.refresh_movement_modifier(key, node)?;
                if self
                    .evaluate(id, node, field(&node.fields, "m_485109CB"), Some(key))
                    .is_some_and(|value| script_expr::truthy(&value))
                {
                    self.register_tick(key, self.machine.now_ms);
                }
            }
            StateKind::MovementSample(previous) => {
                self.update_movement_sample(key, node, previous)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::PhaseScriptList => self.refresh_phase_scripts(key, node)?,
            StateKind::PhaseScript => self.begin_phase_script(key, node)?,
            StateKind::PhaseTransition => self.arm_phase_transition(key, node)?,
            StateKind::Anim => self.arm_animation(key, node)?,
            StateKind::SendMessage => {
                self.send_game_message(id, node)?;
            }
            StateKind::PersistentSend(message)
                if self.graph(id).index == 0x0c8c && matches!(node.pos, 17 | 18 | 83 | 126) =>
            {
                if let Some(parameters) = self.game_message_parameters(id, node)? {
                    self.machine.queue_entries_from(
                        self.graphs,
                        None,
                        Some(self.machine.snapshot.entity),
                        message,
                        parameters,
                        self.machine.now_ms,
                    )?;
                }
            }
            StateKind::CosmeticEntity => self.begin_cosmetic(key, node, depth)?,
            StateKind::ChaseVar => self.begin_chase(key, node, depth)?,
            StateKind::Watch => {
                let seen = self.evaluate(id, node, field(&node.fields, "m_B7A3CF78"), Some(key));
                self.state_mut(key).expect("active Watch").watch_seen = seen;
            }
            StateKind::SpawnSearch => self.begin_spawn_search(key, node)?,
            StateKind::RecentInput => (),
            StateKind::LivingBodyArray => {
                if !living_body_array_shape(node) {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("encounter body array filter"),
                    );
                    return Ok(());
                }
                self.refresh_living_body_array(key)?;
                self.register_tick(key, self.machine.now_ms);
            }
            StateKind::WeaponVolley { .. } => self.begin_volley(key, node, depth)?,
            StateKind::Plain
            | StateKind::PersistentSend(_)
            | StateKind::Effect
            | StateKind::ClientPulse { .. }
            | StateKind::EmptyTargets
            | StateKind::ActiveQuery
            | StateKind::RollMovementParameter => (),
        }
        self.follow(id, node, "m_onBeginPlug", Some(key), depth)?;
        self.follow(id, node, "m_subgraphPlug", Some(key), depth)?;
        self.try_complete(key, node, depth)
    }

    fn refresh_living_body_array(&mut self, key: StateKey) -> Result<(), InitFault> {
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("active encounter array filter");
        let Ok(Some(output)) = script_expr::dynamic(field(&node.fields, "m_F2805C84")) else {
            unreachable!("validated encounter array output");
        };
        let Some(source) = self.evaluate(id, node, field(&node.fields, "m_814F8EC3"), Some(key))
        else {
            return Ok(());
        };
        let mut pending = match source {
            Value::Array(values) => values,
            Value::Null => Vec::new(),
            value => vec![value],
        };
        pending.reverse();
        let mut living = Vec::new();
        while let Some(value) = pending.pop() {
            self.spend("encounter body array candidate")?;
            match value {
                Value::Array(values) => pending.extend(values.into_iter().rev()),
                Value::Entity(entity) => match self.world.encounter_body_alive(entity) {
                    Some(true) => living.push(value),
                    Some(false) => (),
                    None => {
                        self.report(
                            self.site(id, node),
                            Issue::UnsupportedField("encounter body lifetime"),
                        );
                        return Ok(());
                    }
                },
                _ => living.push(value),
            }
        }
        let target = source_key(id, output);
        let result = Value::Array(living);
        if self.machine.value(target, None) != result {
            self.write(target, result)?;
        }
        Ok(())
    }

    fn array_scalar(
        &mut self,
        id: u16,
        node: &Node,
        mut config: &Json,
        caller: Option<StateKey>,
        initial: Value,
    ) -> Option<Value> {
        for _ in 0..MAX_PLUG_DEPTH {
            if config["$"].as_str() != Some("STU_06610EBC") {
                return self.evaluate_at_with_initial(self.site(id, node), config, caller, initial);
            }
            config = match array_field(config, "m_configVars").first() {
                Some(config) => config,
                None => return Some(initial),
            };
        }
        self.report(
            self.site(id, node),
            Issue::Expression(EvalError::NestingExceeded),
        );
        None
    }

    fn insert_array(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        let output = field(&node.fields, "m_26E3B366");
        let source = field(&node.fields, "m_value");
        if output.is_null() || source.is_null() {
            self.follow(id, node, "m_outPlug", caller, depth)?;
            return Ok(true);
        }
        let target = match script_expr::dynamic(output) {
            Ok(Some(target)) => target,
            Ok(None)
                if matches!(
                    output["$"].as_str(),
                    Some(
                        "STUConfigVarBool"
                            | "STUConfigVarInt"
                            | "STUConfigVarFloat"
                            | "STUConfigVarLogicalButton"
                            | "STU_BD6CC023"
                            | "STU_297E1A30"
                    )
                ) =>
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                return Ok(true);
            }
            _ => {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("array insertion output target"),
                );
                return Ok(false);
            }
        };
        let index_cfg = field(&node.fields, "m_index");
        let index = if index_cfg.is_null() {
            -1
        } else {
            let Some(value) = self.array_scalar(id, node, index_cfg, caller, Value::Integer(-1))
            else {
                return Ok(false);
            };
            array_insertion_index(&value)
        };
        if index > 200 {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion native limit diagnostic"),
            );
            return Ok(false);
        }
        let key_cfg = field(&node.fields, "m_key");
        if !key_cfg.is_null() {
            let Some(key) = self.array_scalar(id, node, key_cfg, caller, Value::Integer(0)) else {
                return Ok(false);
            };
            if target.id == 0
                || matches!(
                    key,
                    Value::Null
                        | Value::Bool(_)
                        | Value::Integer(_)
                        | Value::Float(_)
                        | Value::Array(_)
                        | Value::Map(_)
                        | Value::IdentifierMap(_)
                )
            {
                self.follow(id, node, "m_outPlug", caller, depth)?;
                return Ok(true);
            }
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion keyed row storage"),
            );
            return Ok(false);
        }
        if target.id == 0 {
            self.follow(id, node, "m_outPlug", caller, depth)?;
            return Ok(true);
        }
        let target = source_key(id, target);
        if self
            .machine
            .links
            .get(&target)
            .is_some_and(|links| !links.is_empty())
        {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion linked variable checkpoint"),
            );
            return Ok(false);
        }
        let before = self.machine.value(target, None);
        let mut items = match stored(&self.machine.snapshot, target) {
            Value::Array(items) => items,
            _ => vec![],
        };
        let original_count = items.len();
        let index = usize::try_from(index).ok();
        if let Some(index) = index {
            while items.len() < index {
                self.spend("array insertion zero gap")?;
                items.push(Value::Integer(0));
            }
        }
        let mut insertion = ArrayInsertion {
            target,
            items,
            index: index.filter(|index| *index < original_count),
            unique: configured(node, "m_8AD67B20"),
        };
        // Providers see each mutation, while watchers see one final release.
        for _ in &insertion.items {
            self.spend("array insertion publication")?;
        }
        self.set_base(target, Value::Array(insertion.items.clone()));
        let diagnostics = self.machine.snapshot.diagnostics.len();
        if node.class == "STU_CBE8BDAA" {
            if let Some(value) = self.evaluate_at_with_initial(
                self.site(id, node),
                source,
                caller,
                Value::Integer(0),
            ) {
                self.insert_array_value(&mut insertion, value, false, 0)?;
            }
        } else {
            self.insert_array_source(id, node, caller, source, &mut insertion, 0)?;
        }
        if self.machine.snapshot.diagnostics.len() != diagnostics {
            return Ok(false);
        }
        self.notify_value(target, &before, None)?;
        if insertion.items.len() > 200 {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("array insertion native limit diagnostic"),
            );
            return Ok(false);
        }
        self.follow(id, node, "m_outPlug", caller, depth)?;
        Ok(true)
    }

    fn insert_array_source(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
        config: &Json,
        insertion: &mut ArrayInsertion,
        depth: usize,
    ) -> Result<bool, InitFault> {
        self.spend("array insertion source config")?;
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        if config.is_null() {
            return Ok(false);
        }
        if config["$"].as_str() == Some("STU_06610EBC") {
            for config in array_field(config, "m_configVars") {
                if self.insert_array_source(id, node, caller, config, insertion, depth + 1)? {
                    return Ok(true);
                }
            }
            return Ok(false);
        }
        if let Ok(Some(variable)) = script_expr::dynamic(config) {
            if source_key(id, variable) == insertion.target && !insertion.items.is_empty() {
                return Ok(true); // Native iterator has reached its own destination start.
            }
        }
        let Some(value) = self.evaluate(id, node, config, caller) else {
            return Ok(true);
        };
        if value != Value::Null {
            self.insert_array_value(insertion, value, true, depth)?;
        }
        Ok(false)
    }

    fn insert_array_value(
        &mut self,
        insertion: &mut ArrayInsertion,
        value: Value,
        flatten: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        self.spend("array insertion source row")?;
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        if flatten {
            if let Value::Array(items) = value {
                for value in items {
                    self.insert_array_value(insertion, value, true, depth + 1)?;
                }
                return Ok(());
            }
        }
        if insertion.unique {
            for item in &insertion.items {
                self.spend("array insertion equality")?;
                if equal(item, &value) {
                    return Ok(());
                }
            }
        }
        if let Some(index) = &mut insertion.index {
            for _ in *index..insertion.items.len() {
                self.spend("array insertion ordered shift")?;
            }
            insertion.items.insert(*index, value);
            *index += 1;
        } else {
            insertion.items.push(value);
        }
        for _ in &insertion.items {
            self.spend("array insertion publication")?;
        }
        self.set_base(insertion.target, Value::Array(insertion.items.clone()));
        Ok(())
    }

    fn select_recent_input(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "m_C51AF683")) else {
            self.report(site, Issue::UnsupportedField("spawn history output"));
            return Ok(());
        };
        let Some(lifetime) =
            self.evaluate(key.instance, node, field(&node.fields, "m_82317B5F"), None)
        else {
            return Ok(());
        };
        if !matches!(lifetime, Value::Integer(_) | Value::Float(_))
            || !script_expr::to_float(&lifetime).is_finite()
        {
            self.report(site, Issue::UnsupportedField("spawn history lifetime"));
            return Ok(());
        }
        let lifetime = script_expr::to_float(&lifetime);
        let Some(values) =
            self.evaluate(key.instance, node, field(&node.fields, "m_814F8EC3"), None)
        else {
            return Ok(());
        };
        let positions = match values {
            Value::Array(values) => values,
            Value::Null => Vec::new(),
            value => vec![value],
        };
        if positions.iter().any(|value| !matches!(value, Value::Vector(point) if point.iter().all(|axis| axis.is_finite()))) {
            self.report(site, Issue::UnsupportedField("spawn history positions"));
            return Ok(());
        }
        let lifetime_ms = (lifetime * 1000.0).round() as i64;
        let now = self.machine.now_ms;
        let history = &mut self
            .state_mut(key)
            .expect("active recent input state")
            .recent_inputs;
        for index in (0..history.len()).rev() {
            if i128::from(now) - i128::from(history[index].1) > i128::from(lifetime_ms) {
                history.swap_remove(index);
            }
        }
        let selected = if positions.is_empty() {
            Value::Null
        } else {
            if history.len() >= positions.len() {
                let oldest = history
                    .iter()
                    .enumerate()
                    .min_by_key(|(_, item)| item.1)
                    .map(|(index, _)| index)
                    .expect("populated history");
                history.swap_remove(oldest);
            }
            let bound = positions.len() as u64;
            let threshold = bound.wrapping_neg() % bound;
            let first = loop {
                self.spend("spawn position random selection")?;
                let draw = self.machine.random_u64();
                if draw >= threshold {
                    break (draw % bound) as usize;
                }
            };
            let history = &mut self
                .state_mut(key)
                .expect("active recent input state")
                .recent_inputs;
            let mut slot = first;
            while history.iter().any(|&(used, _)| used == slot) {
                slot = (slot + 1) % positions.len();
                if slot == first {
                    break;
                }
            }
            if lifetime_ms > 0 {
                if let Some(item) = history.iter_mut().find(|item| item.0 == slot) {
                    item.1 = now;
                } else {
                    history.push((slot, now));
                }
            }
            positions[slot].clone()
        };
        self.write(source_key(key.instance, output), selected)?;
        self.follow(key.instance, node, "m_1405CDCB", Some(key), depth)?;
        Ok(())
    }

    fn creation_scalar(
        &mut self,
        id: u16,
        node: &Node,
        mut config: &Json,
        initial: Value,
    ) -> Result<Option<Value>, InitFault> {
        for _ in 0..MAX_PLUG_DEPTH {
            self.spend("entity creation scalar")?;
            if config.is_null() {
                return Ok(Some(initial));
            }
            if script_expr::native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                config = config
                    .get("m_configVars")
                    .and_then(Json::as_array)
                    .and_then(|values| values.first())
                    .unwrap_or(&Json::Null);
            } else {
                return Ok(self.evaluate_at_with_initial(
                    self.site(id, node),
                    config,
                    None,
                    initial,
                ));
            }
        }
        self.report(
            self.site(id, node),
            Issue::UnsupportedField("entity creation config depth"),
        );
        Ok(None)
    }

    fn create_entity(&mut self, id: u16, node: &Node, depth: usize) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let hero = field(&node.fields, "m_A1A715A6");
        let mut unsupported = vec![
            "m_F7621CAB",
            "m_40AF7E2D",
            "m_EC1E473E",
            "m_3E3E203A",
            "m_888BCD8B",
            "m_AA26E7DD",
            "m_A3A50DF2",
            "m_373DBF32",
            "m_E2BF1226",
            "m_0FDD45BB",
            "m_481C68E7",
        ];
        if hero.is_null() {
            unsupported.extend([
                "m_1C558638",
                "m_3EBF988B",
                "m_F8E56B94",
                "m_DE3B4F01",
                "m_D7F85181",
                "m_246C47AF",
            ]);
        } else {
            unsupported.push("m_entityDef");
        }
        let output = field(&node.fields, "m_E95B313B");
        let target = script_expr::lvalue(output);
        let flags_valid = ["m_A086DB58", "m_294B652A", "m_607F6292"]
            .iter()
            .all(|name| {
                let value = field(&node.fields, name);
                value.is_null() || value.as_bool() == Some(false) || value.as_i64() == Some(0)
            });
        let mode = field(&node.fields, "m_51AB9D21");
        if unsupported
            .iter()
            .any(|name| !field(&node.fields, name).is_null())
            || !flags_valid
            || !(mode.is_null() || mode.as_i64() == Some(5))
            || !matches!(&target, Ok(Some(_))) && !output.is_null()
        {
            self.report(site, Issue::UnsupportedField("entity creation options"));
            return Ok(false);
        }
        let target = target.ok().flatten();
        let Some(position) =
            self.creation_scalar(id, node, field(&node.fields, "m_position"), Value::Null)?
        else {
            return Ok(false);
        };
        let position = match position {
            Value::Vector(position) => Some(position),
            Value::Entity(entity) => self.world.creation_position(entity),
            _ => None,
        };
        let Some(position) = position.filter(|point| point.iter().all(|axis| axis.is_finite()))
        else {
            self.report(site, Issue::UnsupportedField("entity creation transform"));
            return Ok(false);
        };
        let kind = if hero.is_null() {
            let Some(Value::Asset(definition)) = self.creation_scalar(
                id,
                node,
                field(&node.fields, "m_entityDef"),
                Value::Asset(0),
            )?
            else {
                self.report(site, Issue::UnsupportedField("entity creation resource"));
                return Ok(false);
            };
            if definition >> 48 != 0x0400 || definition & 0x0000_ffff_ffff_ffff == 0 {
                self.report(site, Issue::UnsupportedField("entity creation resource"));
                return Ok(false);
            }
            CreationKind::Definition(definition)
        } else {
            let Some(Value::Asset(hero)) = self.creation_scalar(id, node, hero, Value::Asset(0))?
            else {
                self.report(site, Issue::UnsupportedField("hero creation identity"));
                return Ok(false);
            };
            let Some(Value::Integer(team @ 0..=1)) =
                self.creation_scalar(id, node, field(&node.fields, "m_3EBF988B"), Value::Null)?
            else {
                self.report(site, Issue::UnsupportedField("hero creation team"));
                return Ok(false);
            };
            let Some(yaw) = self.creation_scalar(
                id,
                node,
                field(&node.fields, "m_1C558638"),
                Value::Float(0.0),
            )?
            else {
                return Ok(false);
            };
            if hero >> 48 != 0x02e0
                || hero & 0x0000_ffff_ffff_ffff == 0
                || !matches!(yaw, Value::Float(_) | Value::Integer(_))
                || !script_expr::to_float(&yaw).is_finite()
            {
                self.report(
                    site,
                    Issue::UnsupportedField("hero creation identity or transform"),
                );
                return Ok(false);
            }
            let mut flags = [Value::Null, Value::Null];
            for (slot, name) in flags.iter_mut().zip(["m_F8E56B94", "m_DE3B4F01"]) {
                let Some(value) =
                    self.creation_scalar(id, node, field(&node.fields, name), Value::Null)?
                else {
                    return Ok(false);
                };
                *slot = value;
            }
            let mut resources = [None; 2];
            for (slot, (name, kind)) in resources
                .iter_mut()
                .zip([("m_D7F85181", 0x0400), ("m_246C47AF", 0x0a50)])
            {
                let Some(value) =
                    self.creation_scalar(id, node, field(&node.fields, name), Value::Asset(0))?
                else {
                    return Ok(false);
                };
                match value {
                    Value::Null | Value::Asset(0) => (),
                    Value::Asset(asset) if asset >> 48 == kind => *slot = Some(asset),
                    _ => {
                        self.report(site, Issue::UnsupportedField("hero creation resource"));
                        return Ok(false);
                    }
                }
            }
            CreationKind::Hero {
                hero,
                team: team as u8,
                yaw: script_expr::to_float(&yaw),
                flags,
                controller: resources[0],
                skin: resources[1],
            }
        };
        match self
            .world
            .create_entity(site, &EntityCreation { position, kind })
        {
            Ok(Some(entity)) if script_expr::valid_entity(entity) && entity != site.entity => {
                if let Some(target) = target {
                    self.write(source_key(id, target), Value::Entity(entity))?;
                }
            }
            Ok(None) => (),
            result => {
                self.report(
                    site,
                    Issue::EntityCreation(match result {
                        Err(error) => error,
                        _ => "entity creation returned an invalid binding".into(),
                    }),
                );
                return Ok(false);
            }
        }
        self.follow(id, node, "m_outPlug", None, depth)?;
        Ok(true)
    }

    fn begin_spawn_search(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let destination = script_expr::lvalue(field(&node.fields, "m_8FEA3023"));
        if !matches!(destination, Ok(Some(_)))
            || ["m_priority", "m_2B717EC8", "m_1BCC45B9"]
                .iter()
                .any(|name| !field(&node.fields, name).is_null())
            || node.links("m_7D31C943").next().is_some()
        {
            self.report(site, Issue::UnsupportedField("spawn search configuration"));
            return Ok(());
        }
        let request = (|| {
            let mut input = |name, default| {
                self.evaluate(key.instance, node, field(&node.fields, name), None)
                    .map(|value| if value == Value::Null { default } else { value })
            };
            let position = input("m_position", Value::Vector([0.0; 3]))?;
            let Value::Vector(mut direction) = input("m_ADC02DA1", Value::Vector([0.0; 3]))? else {
                return None;
            };
            let radius = script_expr::to_float(&input("m_radius", Value::Float(1_000_000.0))?)
                .min(1_000_000.0);
            let angle = script_expr::to_float(&input("m_A30B383E", Value::Float(360.0))?);
            let minimum = script_expr::to_int(&input("m_EC1B83C7", Value::Integer(1))?, 1).max(1);
            let maximum =
                script_expr::to_int(&input("m_7084DA43", Value::Integer(1))?, 1).max(minimum);
            let scale = script_expr::to_float(&input("m_F649CC6C", Value::Float(1.0))?).max(0.5);
            if truthy(&input("m_DAD5978F", Value::Bool(false))?)
                || script_expr::to_int(&input("m_7762F430", Value::Integer(-1))?, -1) != -1
                || scale != 0.5
                || !(0.0 < radius && radius <= 70.0)
                || maximum > 300
                || !angle.is_finite()
                || !direction.iter().all(|v| v.is_finite())
            {
                return None;
            }
            let position = match position {
                Value::Entity(entity) if script_expr::valid_entity(entity) => {
                    self.world.position(entity)?
                }
                Value::Vector(position) => position,
                _ => return None,
            };
            if !position.iter().all(|v| v.is_finite()) {
                return None;
            }
            let length = direction[0].hypot(direction[1]).hypot(direction[2]);
            if length > 0.0 {
                direction = direction.map(|value| f64::from((value / length) as f32));
            }
            Some(SpawnSearch {
                position,
                direction,
                radius,
                half_angle: f64::from(
                    (angle * f64::from((std::f64::consts::PI / 360.0) as f32)) as f32,
                ),
                minimum: minimum as usize,
                maximum: maximum as usize,
            })
        })();
        let Some(request) = request else {
            self.report(site, Issue::UnsupportedField("spawn search inputs"));
            return Ok(());
        };
        self.state_mut(key).expect("active search").spawn_search = Some(request);
        Ok(())
    }

    fn finish_spawn_search(
        &mut self,
        search: SpawnSearchKey,
        positions: Vec<[f64; 3]>,
    ) -> Result<(), InitFault> {
        let key = StateKey {
            instance: search.instance,
            raw: search.raw,
        };
        let Some(state) = self.state_mut(key).filter(|state| {
            state.active
                && state.activation == search.activation
                && state.start_ms == search.start_ms
        }) else {
            return Ok(());
        };
        let Some(request) = state.spawn_search else {
            return Ok(());
        };
        if !(request.minimum..=request.maximum).contains(&positions.len())
            || !positions.iter().flatten().all(|v| v.is_finite())
        {
            return Ok(());
        }
        state.spawn_search = None;
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("active search graph state");
        let output = script_expr::lvalue(field(&node.fields, "m_8FEA3023"))
            .expect("validated search output")
            .expect("validated search destination");
        self.write(
            source_key(key.instance, output),
            Value::Array(positions.into_iter().map(Value::Vector).collect()),
        )?;
        self.exit_subgraph(key.instance, node, "m_F9D59C3A", 1)?;
        self.post(key.instance, Event::Finish(key.raw))
    }

    fn begin_static_target(&mut self, key: StateKey, node: &Node) -> Result<bool, InitFault> {
        let id = key.instance;
        if participant_filter(node).is_some() {
            return self.collect_targets(id, node);
        }
        let site = self.site(id, node);
        let target = match script_expr::lvalue(field(&node.fields, "m_4AEEE6A1")) {
            Ok(Some(output)) => source_key(id, output),
            Ok(None) => {
                self.report(site, Issue::UnsupportedField("static target output"));
                return Ok(false);
            }
            Err(error) => {
                self.report(site, Issue::Expression(error));
                return Ok(false);
            }
        };
        if let Some((tag, excluded)) = excluded_target_tags(self.graph(id).index, node) {
            let Some(entities) = self.world.targets_with_tag(site, tag, excluded) else {
                self.report(
                    site,
                    Issue::UnsupportedField("tagged targets with exclusions"),
                );
                return Ok(false);
            };
            self.write(
                target,
                Value::Array(entities.into_iter().map(Value::Entity).collect()),
            )?;
            return Ok(true);
        }
        let config = static_target_operand(node).expect("unfiltered static target");
        let Some(value) = self.evaluate(id, node, config, None) else {
            return Ok(false);
        };
        let entity = match value {
            Value::Asset(tag) if tag >> 48 == 0x0d60 => self.world.static_target(tag),
            _ => None,
        };
        let Some(entity) = entity.filter(|&id| script_expr::valid_entity(id)) else {
            self.report(site, Issue::UnsupportedField("unique static target"));
            return Ok(false);
        };
        self.write(target, Value::Array(vec![Value::Entity(entity)]))?;
        Ok(true)
    }

    fn collect_targets(&mut self, id: u16, node: &Node) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let Ok(Some(output)) = script_expr::lvalue(field(&node.fields, "m_4AEEE6A1")) else {
            self.report(
                site,
                Issue::UnsupportedField("participant collection output"),
            );
            return Ok(false);
        };
        let (entities, issue) = if let Some(filter) = participant_filter(node) {
            (
                self.world.participant_targets(site, filter),
                "scene participant filter",
            )
        } else if field(&node.fields, "m_9C7BD8B8").is_null()
            && script_expr::target_tag_config(field(&node.fields, "m_B486603E"))
                == Some(0x0d60_0000_0000_0321)
            && static_target_operand(node).is_some()
        {
            (
                self.world
                    .tagged_placements(site, 0x0d60_0000_0000_0321)
                    .map(<[u32]>::to_vec),
                "scene target collection",
            )
        } else {
            (None, "scene target collection")
        };
        let Some(entities) = entities else {
            self.report(site, Issue::UnsupportedField(issue));
            return Ok(false);
        };
        self.write(
            source_key(id, output),
            Value::Array(entities.into_iter().map(Value::Entity).collect()),
        )?;
        Ok(true)
    }

    fn has_no_ai_control_target(&mut self, id: u16, node: &Node) -> bool {
        let site = self.site(id, node);
        node.is_networked()
            && node.remote == -1
            && script_expr::native_shape(field(&node.fields, "m_00D49978"), "STU_B5A0CAF0", None)
            && self
                .world
                .related_entity(site, EntityRelation::AiControlEntity, site.entity)
                == Some(0)
    }

    fn update_movement_sample(
        &mut self,
        key: StateKey,
        node: &Node,
        previous: u16,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(sample) = self
            .world
            .movement_sample(site)
            .filter(|&mask| mask < 1 << 13)
        else {
            self.report(site, Issue::UnsupportedField("owner movement sample"));
            return Ok(());
        };
        let changed = previous ^ sample;
        if changed == 0 {
            return Ok(());
        }
        self.state_mut(key).expect("active movement sample").kind =
            StateKind::MovementSample(sample);
        for (bit, name) in MOVEMENT_BOOLEAN_OUTPUTS.iter().enumerate() {
            let mask = 1 << bit;
            if changed & mask == 0 {
                continue;
            }
            if let Some(variable) = script_expr::lvalue(field(&node.fields, name))
                .expect("validated movement sample output")
            {
                self.write(
                    source_key(key.instance, variable),
                    Value::Bool(sample & mask != 0),
                )?;
            }
        }
        Ok(())
    }

    fn begin_target_tag(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let add = field(&node.fields, "m_targetTags");
        let suppress = field(&node.fields, "m_44D1BE94");
        let (literal, present) = match (add.is_null(), suppress.is_null()) {
            (false, true) => (add, true),
            (true, false) => (suppress, false),
            _ => {
                self.report(site, Issue::UnsupportedField("target tag operands"));
                return Ok(());
            }
        };
        let Some(tag) = script_expr::target_tag_config(literal).filter(|&tag| tag != 0) else {
            self.report(site, Issue::UnsupportedField("target tag literal"));
            return Ok(());
        };
        let Some(Value::Entity(entity)) =
            self.evaluate(key.instance, node, field(&node.fields, "m_31714AFC"), None)
        else {
            self.report(site, Issue::UnsupportedField("target tag entity"));
            return Ok(());
        };
        let effect = TargetTagEffect {
            entity,
            tag,
            present,
        };
        if !self.world.set_target_tag_effect(site, effect, true) {
            self.report(site, Issue::UnsupportedField("target tag effect"));
            return Ok(());
        }
        self.state_mut(key).expect("active target tag").kind = StateKind::TargetTag {
            effect: Some(effect),
        };
        Ok(())
    }

    fn ai_behavior_value(
        &mut self,
        key: StateKey,
        node: &Node,
        name: &str,
        default: f64,
    ) -> Result<Option<Value>, InitFault> {
        let mut config = field(&node.fields, name);
        for _ in 0..MAX_PLUG_DEPTH {
            self.spend("AI behavior scalar")?;
            if config.is_null() {
                return Ok(Some(Value::Float(default)));
            }
            if script_expr::native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                config = config
                    .get("m_configVars")
                    .and_then(Json::as_array)
                    .and_then(|items| items.first())
                    .unwrap_or(&Json::Null);
            } else {
                return Ok(self
                    .evaluate(key.instance, node, config, Some(key))
                    .map(|value| {
                        if value == Value::Null {
                            Value::Float(default)
                        } else {
                            value
                        }
                    }));
            }
        }
        self.report(
            self.site(key.instance, node),
            Issue::UnsupportedField("AI behavior config depth"),
        );
        Ok(None)
    }

    fn update_ai_behavior(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let previous = match self.state(key).map(|state| state.kind) {
            Some(StateKind::AiBehavior { record }) => record,
            _ => return Ok(()),
        };
        let (priority, prefer_later) = if let Some(previous) = previous {
            (previous.priority, previous.prefer_later)
        } else {
            let cfg = field(&node.fields, "m_priority");
            if cfg.is_null() {
                (0.0, false)
            } else {
                if !script_expr::native_fields(cfg, &["m_priority", "m_resourceKey"])
                    || field(cfg, "$").as_str() != Some("STU_8A491388")
                    || !field(cfg, "m_resourceKey").is_null()
                    || !field(cfg, "m_priority")
                        .as_str()
                        .and_then(crate::accounts::parse_guid)
                        .is_some_and(|guid| guid >> 48 == 0x0c40)
                {
                    self.report(
                        site,
                        Issue::UnsupportedField("AI behavior priority resource"),
                    );
                    return Ok(());
                }
                match script_expr::stack_priority(cfg) {
                    Ok(priority) => (f64::from(priority.level), priority.above_equals),
                    Err(error) => {
                        self.report(site, Issue::Expression(error));
                        return Ok(());
                    }
                }
            }
        };
        let Some(flag) = self.ai_behavior_value(key, node, "m_88881C38", 0.0)? else {
            return Ok(());
        };
        let mut shared = [0.0; 2];
        for (number, name) in shared.iter_mut().zip(["m_36998F8A", "m_44064F45"]) {
            let Some(value) = self.ai_behavior_value(key, node, name, 0.0)? else {
                return Ok(());
            };
            *number = script_expr::to_float(&value);
        }
        let (class, fields): (&'static str, &[(&str, f64)]) = match node.class.as_str() {
            "STU_B61E6681" => ("STU_B61E6681", &[("m_203D6B00", 0.0), ("m_weight", 0.0)]),
            "STU_CFA338CB" => (
                "STU_CFA338CB",
                &[
                    ("m_DC8A3A89", 0.0),
                    ("m_503D6044", 0.0),
                    ("m_157D72D1", 0.0),
                ],
            ),
            "STU_600E3C59" => (
                "STU_600E3C59",
                &[
                    ("m_D655C99A", 2.0),
                    ("m_width", 2.0),
                    ("m_weight", 0.0),
                    ("", 0.0),
                    ("m_B802CBBA", 1.0),
                ],
            ),
            _ => unreachable!("AI behavior class"),
        };
        let mut inputs = [0.0; 5];
        for (index, (name, default)) in fields.iter().enumerate() {
            if name.is_empty() {
                continue;
            }
            let Some(value) = self.ai_behavior_value(key, node, name, *default)? else {
                return Ok(());
            };
            inputs[index] = script_expr::to_float(&value);
        }
        if class == "STU_CFA338CB" {
            inputs[2] =
                f64::from((inputs[2] * f64::from((std::f64::consts::PI / 180.0) as f32)) as f32);
        }
        if class == "STU_600E3C59" {
            inputs[3] = f64::from(match field(&node.fields, "m_DD792FF0").as_u64() {
                Some(0) => 1.0f32 / 60.0,
                Some(1) => 0.05,
                Some(2) => 0.1,
                Some(4) => 0.5,
                _ => 0.2,
            });
        }
        if shared
            .iter()
            .chain(inputs.iter())
            .any(|value| !value.is_finite())
        {
            self.report(site, Issue::UnsupportedField("AI behavior finite inputs"));
            return Ok(());
        }
        let record = AiBehaviorRequest {
            class,
            priority,
            prefer_later,
            flag: truthy(&flag),
            shared,
            inputs,
        };
        match self.world.ai_behavior_request(site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI behavior").kind = StateKind::AiBehavior {
                    record: Some(record),
                }
            }
            Some(false) => (),
            None => self.report(site, Issue::UnsupportedField("AI behavior component")),
        }
        Ok(())
    }

    fn refresh_contact(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(value) = self.evaluate(
            key.instance,
            node,
            field(&node.fields, "m_53FCEDBF"),
            Some(key),
        ) else {
            return Ok(());
        };
        let targets = match value {
            Value::Array(items) => items,
            Value::Null => Vec::new(),
            value => vec![value],
        };
        let targets = targets
            .into_iter()
            .map(|value| match value {
                Value::Entity(entity) => Some(entity),
                _ => None,
            })
            .collect::<Option<Vec<_>>>();
        let source = self.evaluate(key.instance, node, field(&node.fields, "m_63CB0D07"), None);
        let resource = self.evaluate(
            key.instance,
            node,
            field(&node.fields, "m_contactSet"),
            None,
        );
        let expected = 0x0120_0000_0000_0015;
        if node.remote < 0
            || source != Some(Value::Entity(site.entity))
            || resource != Some(Value::Asset(expected))
            || !targets.is_some_and(|targets| {
                self.world
                    .emit_contact_effect(site, site.entity, expected, &targets)
            })
        {
            self.report(site, Issue::UnsupportedField("contact effect service"));
        }
        Ok(())
    }

    fn begin_npc_health_change(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(target) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_FA3ECC43"),
            Value::Null,
        )?
        else {
            return Ok(());
        };
        if node.pos == 48 && target == Value::Array(Vec::new()) {
            return Ok(());
        }
        let config = field(&node.fields, "m_modifyHealth");
        let Value::Entity(target) = target else {
            self.report(site, Issue::UnsupportedField("NPC damage target"));
            return Ok(());
        };
        if (site.graph, node.pos) == (0x0cbc, 69) {
            let source =
                self.creation_scalar(key.instance, node, field(config, "m_724CBA57"), Value::Null)?;
            let amount = self
                .creation_scalar(key.instance, node, field(config, "m_amount"), Value::Null)?
                .and_then(pool_number);
            if target != site.entity
                || source != Some(Value::Entity(site.entity))
                || amount != Some(10_000.0)
                || field(config, "m_93CDFD2F").as_u64() != Some(1)
                || !field(config, "m_instigator").is_null()
                || field(&node.fields, "m_8B707686").as_u64() != Some(1)
            {
                self.report(site, Issue::UnsupportedField("Junkenstein cleanup damage"));
                return Ok(());
            }
            if !self.machine.owner_dead {
                if !self.world.self_damage(site, 10_000.0) {
                    self.report(site, Issue::UnsupportedField("NPC self damage service"));
                }
                self.publish_health_totals(site)?;
            }
            return Ok(());
        }
        let self_hit = node.pos == 46 && target == site.entity;
        let other_hit =
            node.pos == 38 && target != site.entity && script_expr::valid_entity(target);
        if !(self_hit || other_hit)
            || field(config, "m_93CDFD2F").as_u64() != Some(1)
            || !field(&node.fields, "m_95F1FB0B").is_null()
        {
            self.report(site, Issue::UnsupportedField("NPC health service"));
            return Ok(());
        }
        for name in ["m_instigator", "m_724CBA57"] {
            if self.creation_scalar(key.instance, node, field(config, name), Value::Null)?
                != Some(Value::Entity(site.entity))
            {
                self.report(site, Issue::UnsupportedField("NPC damage source"));
                return Ok(());
            }
        }
        let Some(amount) = self
            .creation_scalar(key.instance, node, field(config, "m_amount"), Value::Null)?
            .and_then(pool_number)
            .filter(|amount| {
                (0.0..=f64::from(f32::MAX)).contains(amount) && (!self_hit || *amount > 0.0)
            })
        else {
            self.report(site, Issue::UnsupportedField("NPC damage amount"));
            return Ok(());
        };
        let amount = f64::from(amount as f32);
        if other_hit {
            if self.world.explosion_damage(site, target, amount) {
                self.post(key.instance, Event::Finish(key.raw))?;
            } else {
                self.report(
                    site,
                    Issue::UnsupportedField("NPC explosion damage service"),
                );
            }
            return Ok(());
        }
        if !self.world.self_damage(site, amount) {
            self.report(site, Issue::UnsupportedField("NPC self damage service"));
        }
        self.publish_health_totals(site)?;
        Ok(())
    }

    fn begin_shield_recharge(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let config = field(&node.fields, "m_modifyHealth");
        if field(&node.fields, "m_DD792FF0").as_u64() != Some(3)
            || field(&node.fields, "m_7B2B3F5C").as_u64() != Some(1)
            || field(config, "m_93CDFD2F").as_u64() != Some(0)
        {
            self.report(
                site,
                Issue::UnsupportedField("shield recharge configuration"),
            );
            return Ok(());
        }
        for target in [
            field(&node.fields, "m_FA3ECC43"),
            field(config, "m_724CBA57"),
        ] {
            if self.creation_scalar(key.instance, node, target, Value::Null)?
                != Some(Value::Entity(site.entity))
            {
                self.report(site, Issue::UnsupportedField("shield recharge target"));
                return Ok(());
            }
        }
        let interval = self
            .creation_scalar(
                key.instance,
                node,
                field(&node.fields, "m_95F1FB0B"),
                Value::Null,
            )?
            .and_then(pool_number);
        if interval != Some(1.0) || !self.world.recharge_shields(site, 0.0) {
            self.report(site, Issue::UnsupportedField("shield recharge service"));
            return Ok(());
        }
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    fn tick_shield_recharge(&mut self, key: StateKey, elapsed_ms: u64) -> Result<(), InitFault> {
        if self.machine.owner_dead {
            return Ok(());
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("shield recharge");
        let site = self.site(key.instance, node);
        let config = field(&node.fields, "m_modifyHealth");
        let rate = self
            .creation_scalar(key.instance, node, field(config, "m_amount"), Value::Null)?
            .and_then(pool_number)
            .filter(|rate| (0.0..=f64::from(f32::MAX)).contains(rate));
        let Some(rate) = rate else {
            self.report(site, Issue::UnsupportedField("shield recharge rate"));
            return Ok(());
        };
        if !self
            .world
            .recharge_shields(site, rate * elapsed_ms as f64 / 1000.0)
        {
            self.report(site, Issue::UnsupportedField("shield recharge service"));
        }
        self.publish_health_totals(site)
    }

    fn refresh_summoner_beam_ray(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(contact) = self.world.summoner_beam_contact(site) else {
            self.report(site, Issue::UnsupportedField("Summoner beam ray service"));
            return Ok(());
        };
        let outputs = field(&node.fields, "m_target");
        for (name, value) in [
            (
                "m_692DFF72",
                contact.entity.map_or(Value::Null, Value::Entity),
            ),
            ("m_750377AA", Value::Bool(contact.hit)),
        ] {
            if let Some(output) = script_expr::lvalue(field(outputs, name))
                .map_err(|_| InitFault::InvalidRoot(key.instance))?
            {
                self.write(source_key(key.instance, output), value)?;
            }
        }
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    fn tick_summoner_beam_damage(
        &mut self,
        key: StateKey,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead || elapsed_ms == 0 {
            return Ok(());
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("Summoner beam damage");
        let site = self.site(key.instance, node);
        let target = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_FA3ECC43"),
            Value::Null,
        )?;
        let Some(Value::Entity(target)) = target.filter(
            |value| matches!(value, Value::Entity(entity) if script_expr::valid_entity(*entity)),
        ) else {
            return Ok(());
        };
        let rate = self
            .creation_scalar(
                key.instance,
                node,
                &node.fields["m_modifyHealth"]["m_amount"],
                Value::Null,
            )?
            .and_then(pool_number)
            .filter(|amount| amount.is_finite() && *amount >= 0.0);
        if !rate.is_some_and(|rate| {
            self.world.summoner_beam_damage(
                site,
                target,
                f64::from((rate * elapsed_ms as f64 / 1000.0) as f32),
            )
        }) {
            self.report(
                site,
                Issue::UnsupportedField("Summoner beam damage service"),
            );
        }
        Ok(())
    }

    fn refresh_witch_staff_effect(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let params = array_field(&node.fields, "m_params");
        if asset_guid(&node.fields["m_A76A8CF6"]["m_A76A8CF6"]) != Some(0x04a0_0000_0000_0019)
            || params.len() != 1
            || asset_guid(field(&params[0], "m_B5051BCE")) != Some(0x0a40_0000_0000_002a)
        {
            self.report(
                site,
                Issue::UnsupportedField("Witch staff effect resource or parameters"),
            );
            return Ok(());
        }
        let target = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_targets"),
            Value::Null,
        )?;
        let effect =
            if let Some(Value::Entity(target)) = target.filter(|_| !self.machine.owner_dead) {
                let amount = self
                    .creation_scalar(
                        key.instance,
                        node,
                        field(&params[0], "m_value"),
                        Value::Null,
                    )?
                    .and_then(pool_number)
                    .filter(|amount| amount.is_finite() && *amount >= 0.0);
                amount.map(|amount| (target, amount))
            } else {
                None
            };
        if !self.world.set_witch_staff_effect(site, effect) {
            self.report(site, Issue::UnsupportedField("Witch staff effect service"));
        }
        Ok(())
    }

    fn tick_witch_staff_healing(
        &mut self,
        key: StateKey,
        elapsed_ms: u64,
    ) -> Result<(), InitFault> {
        if self.machine.owner_dead || elapsed_ms == 0 {
            return Ok(());
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("Witch staff healing");
        let site = self.site(key.instance, node);
        let Value::Entity(source) = self
            .machine
            .value(VarKey::Instance(key.instance, SCRIPT_SOURCE), None)
        else {
            self.report(site, Issue::UnsupportedField("Witch staff healing source"));
            return Ok(());
        };
        let amount = self
            .creation_scalar(
                key.instance,
                node,
                &node.fields["m_modifyHealth"]["m_amount"],
                Value::Null,
            )?
            .and_then(pool_number)
            .filter(|amount| amount.is_finite() && *amount >= 0.0);
        if !amount.is_some_and(|amount| {
            self.world.heal_from_witch_staff(
                site,
                source,
                f64::from((amount * elapsed_ms as f64 / 1000.0) as f32),
            )
        }) {
            self.report(site, Issue::UnsupportedField("Witch staff healing service"));
        }
        self.publish_health_totals(site)
    }

    fn refresh_tracked_targets(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        if matches!((site.graph, site.node), (0x006b, Some(6 | 7 | 121))) {
            let radius = self
                .creation_scalar(
                    key.instance,
                    node,
                    field(&node.fields, "m_radius"),
                    Value::Null,
                )?
                .and_then(pool_number)
                .filter(|radius| *radius > 0.0 && radius.is_finite());
            if let Some(targets) =
                radius.and_then(|radius| self.world.witch_staff_targets(site, radius))
            {
                return self.publish_tracked_targets(key, node, targets);
            }
        }
        let Some(Value::Entity(origin)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_8DCA77B6"),
            Value::Null,
        )?
        else {
            self.report(site, Issue::UnsupportedField("tracked target origin"));
            return Ok(());
        };
        let Some(Value::Vector(offset)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_05BE62DD"),
            Value::Vector([0.0; 3]),
        )?
        else {
            self.report(site, Issue::UnsupportedField("tracked target offset"));
            return Ok(());
        };
        let Some(radius) = self
            .creation_scalar(
                key.instance,
                node,
                field(&node.fields, "m_radius"),
                Value::Null,
            )?
            .and_then(pool_number)
            .filter(|radius| *radius >= 0.0 && radius.is_finite())
        else {
            self.report(site, Issue::UnsupportedField("tracked target radius"));
            return Ok(());
        };
        if matches!((site.graph, site.node), (0x022f, Some(26 | 86))) {
            let Some(angle) = self
                .creation_scalar(
                    key.instance,
                    node,
                    field(&node.fields, "m_A30B383E"),
                    Value::Null,
                )?
                .and_then(pool_number)
                .filter(|angle| (0.0..=360.0).contains(angle))
            else {
                self.report(site, Issue::UnsupportedField("Deadeye target angle"));
                return Ok(());
            };
            let targets = (origin == site.entity && offset == [0.0; 3])
                .then(|| self.world.deadeye_targets(site, radius, angle))
                .flatten();
            let Some(targets) = targets else {
                self.report(site, Issue::UnsupportedField("Deadeye target service"));
                return Ok(());
            };
            return self.publish_tracked_targets(key, node, targets);
        }
        let filter = field(&node.fields, "m_filter");
        let Some(candidates) = self
            .world
            .tracked_targets(site, origin, offset, radius, filter)
        else {
            self.report(site, Issue::UnsupportedField("tracked target service"));
            return Ok(());
        };
        let mut targets = Vec::new();
        for candidate in candidates {
            self.spend("tracked target condition")?;
            if !script_expr::valid_entity(candidate) {
                self.report(site, Issue::UnsupportedField("tracked target identity"));
                return Ok(());
            }
            let condition = field(filter, "m_condition");
            let accepted = if condition.is_null() {
                true
            } else {
                let previous = self
                    .parameters
                    .replace(Arc::new(Variables::from([(8, Value::Entity(candidate))])));
                let value = self.evaluate(key.instance, node, condition, None);
                self.parameters = previous;
                let Some(value) = value else {
                    return Ok(());
                };
                truthy(&value)
            };
            if accepted {
                targets.push(candidate);
            }
        }
        self.publish_tracked_targets(key, node, targets)
    }

    fn publish_tracked_targets(
        &mut self,
        key: StateKey,
        node: &Node,
        targets: Vec<u32>,
    ) -> Result<(), InitFault> {
        if let Some(output) = script_expr::lvalue(field(&node.fields, "m_8A2905A0"))
            .map_err(|_| InitFault::InvalidRoot(key.instance))?
        {
            self.write(
                source_key(key.instance, output),
                Value::Array(
                    targets
                        .iter()
                        .map(|target| Value::Entity(*target))
                        .collect(),
                ),
            )?;
        }
        self.state_mut(key)
            .expect("tracked target state")
            .tracked_targets = targets;
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    fn refresh_deadeye_effects(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        const TARGET_GRAPH: u32 = 0x01ee;
        const REAPPLY: u64 = 0x0240_0000_0000_000f;
        let site = self.site(key.instance, node);
        let selected = match stored(&self.machine.snapshot, VarKey::Entity(2551)) {
            Value::Array(values) if self.graphs.graph(TARGET_GRAPH).is_some() => values
                .into_iter()
                .filter_map(|value| match value {
                    Value::Entity(entity) if self.world.deadeye_target_alive(site, entity) => {
                        Some(entity)
                    }
                    _ => None,
                })
                .collect::<Vec<_>>(),
            _ => Vec::new(),
        };
        let mut children = std::mem::take(
            &mut self
                .state_mut(key)
                .expect("Deadeye effect state")
                .deadeye_children,
        );
        let mut removed = Vec::new();
        children.retain(|entity, child| {
            if !self.machine.snapshot.instances.contains_key(child) {
                return false;
            }
            if selected.contains(entity) {
                return true;
            }
            removed.push(*child);
            false
        });
        for child in removed {
            self.destroy(child)?;
        }
        for entity in selected {
            let child = if let Some(&child) = children.get(&entity) {
                child
            } else {
                let child = u16::try_from(self.machine.next_id)
                    .map_err(|_| InitFault::InstanceIdExhausted)?;
                let variables = Variables::from([
                    (536, Value::Entity(entity)),
                    (SCRIPT_SOURCE, Value::Entity(site.entity)),
                ]);
                if !self.create(child, TARGET_GRAPH, None, variables, Startup::Ordinary)? {
                    continue;
                }
                children.insert(entity, child);
                child
            };
            self.machine.queue_entries_from(
                self.graphs,
                Some(child),
                Some(site.entity),
                REAPPLY,
                Variables::from([(92, Value::Entity(entity))]),
                self.machine.now_ms,
            )?;
        }
        self.state_mut(key)
            .expect("Deadeye effect state")
            .deadeye_children = children;
        self.register_tick(key, self.machine.now_ms);
        Ok(())
    }

    fn begin_for_code(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        if !matches!(field(&node.fields, "m_DC897C87"), Json::Null)
            && !matches!(field(&node.fields, "m_DC897C87").as_u64(), Some(0 | 1))
        {
            self.report(
                self.site(key.instance, node),
                Issue::UnsupportedField("for-code notification channel"),
            );
            return Ok(());
        }
        if !field(&node.fields, "m_values").is_null()
            && !field(&node.fields, "m_values")
                .as_array()
                .is_some_and(|rows| rows.iter().all(Json::is_object))
        {
            self.report(
                self.site(key.instance, node),
                Issue::UnsupportedField("for-code value rows"),
            );
            return Ok(());
        }
        self.machine.code_states.push(key);
        self.register_tick(key, self.machine.now_ms);
        self.publish_for_code(key, node)
    }

    fn publish_for_code(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let channel = field(&node.fields, "m_DC897C87").as_u64().unwrap_or(1) as usize;
        if channel > 1 {
            return Ok(());
        }
        for row in array_field(&node.fields, "m_values") {
            self.spend("native code value update")?;
            let config = field(row, "m_value");
            if !config.is_null() {
                self.evaluate(key.instance, node, config, None);
            }
            if let Some(identifier) = field(row, "m_identifier")
                .as_str()
                .and_then(crate::accounts::parse_guid)
                .filter(|id| *id != 0)
            {
                self.machine.code_pending[channel].push(identifier);
            }
        }
        if channel == 0 {
            self.notify_code(0)?;
        }
        Ok(())
    }

    fn notify_code(&mut self, channel: usize) -> Result<(), InitFault> {
        let pending = std::mem::take(&mut self.machine.code_pending[channel]);
        let mut selected = Vec::new();
        for identifier in pending {
            for (token, filter) in self.machine.code_observers.clone() {
                self.spend("native code observer selection")?;
                if filter.is_none_or(|filter| filter == identifier) && !selected.contains(&token) {
                    selected.push(token);
                }
            }
        }
        for token in selected {
            self.spend("native code notification")?;
            if !self.world.code_changed(self.machine.snapshot.entity, token) {
                self.report(
                    Site {
                        entity: self.machine.snapshot.entity,
                        instance: 0,
                        graph: 0,
                        node: None,
                    },
                    Issue::UnsupportedField("native code observer"),
                );
            }
        }
        Ok(())
    }

    fn unregister_for_code(&mut self, key: StateKey) {
        if let Some(index) = self
            .machine
            .code_states
            .iter()
            .position(|registered| *registered == key)
        {
            self.machine.code_states.swap_remove(index);
        }
    }

    fn code_value(
        &mut self,
        identifier: u64,
        mut initial: Value,
    ) -> Result<(bool, Value), InitFault> {
        for key in self.machine.code_states.clone() {
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("registered code state");
            for row in array_field(&node.fields, "m_values") {
                self.spend("native code value lookup")?;
                if field(row, "m_identifier")
                    .as_str()
                    .and_then(crate::accounts::parse_guid)
                    == Some(identifier)
                {
                    let config = field(row, "m_value");
                    let failed_base = field(config, "$").as_str() == Some("STU_B40ACFBA")
                        && script_expr::native_fields(config, &["m_52EA7AB9"]);
                    if !config.is_null() && !failed_base {
                        if let Some(value) = self.evaluate(key.instance, node, config, None) {
                            initial = value;
                        }
                    }
                    return Ok((true, initial));
                }
            }
            initial = Value::Null;
        }
        Ok((false, initial))
    }

    fn begin_ai_control_override(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(Value::Entity(target)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_00D49978"),
            Value::Null,
        )?
        else {
            self.report(site, Issue::UnsupportedField("AI control target"));
            return Ok(());
        };
        let priority = field(&node.fields, "m_priority");
        let (priority, prefer_later) = if priority.is_null() {
            (9999.0, false)
        } else {
            match script_expr::stack_priority(priority) {
                Ok(value) => (f64::from(value.level), value.above_equals),
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(());
                }
            }
        };
        let limits = if node.class == "STU_473549CA" {
            let mut limits = [f64::from(std::f32::consts::PI); 2];
            for (index, name) in ["m_49EEBB65", "m_FD4D6C19"].into_iter().enumerate() {
                if !field(&node.fields, name).is_null() {
                    let Some(value) = self.creation_scalar(
                        key.instance,
                        node,
                        field(&node.fields, name),
                        Value::Float(limits[index]),
                    )?
                    else {
                        return Ok(());
                    };
                    limits[index] = script_expr::to_float(&value);
                }
            }
            if limits.iter().any(|value| !value.is_finite()) {
                self.report(site, Issue::UnsupportedField("AI control limits"));
                return Ok(());
            }
            Some(limits)
        } else {
            None
        };
        let record = AiControlOverride {
            target,
            limits,
            priority,
            prefer_later,
        };
        match self.world.ai_control_override(site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI control state").kind =
                    StateKind::AiControlOverride {
                        record: Some(record),
                    }
            }
            Some(false) => (),
            None => self.report(site, Issue::UnsupportedField("AI control component")),
        }
        Ok(())
    }

    fn update_ai_control_setting(&mut self, key: StateKey, node: &Node) {
        let id = key.instance;
        let Some(scalar) = self.evaluate(id, node, field(&node.fields, "m_0468C6D4"), Some(key))
        else {
            return;
        };
        let Some(flag) = self.evaluate(id, node, field(&node.fields, "m_D6E83CD4"), Some(key))
        else {
            return;
        };
        let site = self.site(id, node);
        if !self.world.set_ai_control_setting(
            site,
            (script_expr::to_float(&scalar) as f32).max(0.1),
            truthy(&flag),
        ) {
            self.report(
                site,
                Issue::UnsupportedField("AI control setting component"),
            );
        }
    }

    fn update_ai_facing(
        &mut self,
        key: StateKey,
        node: &Node,
        previous: Option<AiFacing>,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let (target, priority) = if let Some(previous) = previous {
            (previous.target, previous.priority)
        } else {
            let priority = field(&node.fields, "m_priority");
            if !field(&node.fields, "m_baseEntity").is_null()
                || asset_guid(field(priority, "m_priority")) != Some(0x0c40_0000_0000_0003)
                || !field(priority, "m_resourceKey").is_null()
            {
                self.report(site, Issue::UnsupportedField("AI facing options"));
                return Ok(());
            }
            let Some(Value::Entity(target)) =
                self.evaluate(key.instance, node, field(&node.fields, "m_00D49978"), None)
            else {
                self.report(site, Issue::UnsupportedField("AI facing target"));
                return Ok(());
            };
            (
                target,
                script_expr::stack_priority(priority).expect("validated priority asset"),
            )
        };
        let Some(direction) =
            self.evaluate(key.instance, node, field(&node.fields, "m_7CCD8B4B"), None)
        else {
            return Ok(());
        };
        let direction = match direction {
            Value::Vector(direction) if direction.iter().all(|axis| axis.is_finite()) => direction,
            _ => {
                self.report(site, Issue::UnsupportedField("AI facing direction"));
                return Ok(());
            }
        };
        let record = AiFacing {
            target,
            direction,
            priority,
        };
        match self.world.ai_facing(site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI facing").kind =
                    StateKind::AiFacing(Some(record));
                self.register_tick(key, self.machine.now_ms);
            }
            Some(false) => self.post(key.instance, Event::Abort(key.raw))?,
            None => self.report(site, Issue::UnsupportedField("AI facing component")),
        }
        Ok(())
    }

    fn begin_display_name(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let target = field(&node.fields, "m_14EDDDA7");
        let (name, shape) = match (site.graph, node.pos) {
            (0x0cf3, 21..=25) => (
                0x7105 + u64::from(node.pos - 21),
                script_expr::native_shape(target, "STU_B5A0CAF0", None),
            ),
            (0x0cbc, 20 | 21 | 22 | 27 | 109) | (0x0d1d, 6 | 11 | 73) => {
                let name = match (site.graph, node.pos) {
                    (0x0cbc, 20) => 0x4ccc,
                    (0x0cbc, 21) => 0x4ccd,
                    (0x0cbc, 22) => 0x4cce,
                    (0x0cbc, 27) => 0x4cd3,
                    (0x0cbc, 109) => 0x6abf,
                    (0x0d1d, 6) => 0x5768,
                    (0x0d1d, 11) => 0x5769,
                    _ => 0x5e06,
                };
                (
                    name,
                    script_expr::native_shape(target, "STU_DB742151", Some("m_entity")),
                )
            }
            _ => (0, false),
        };
        let name = 0x0de0_0000_0000_0000 | name;
        if !shape || asset_guid(field(&node.fields, "m_overrideName")) != Some(name) {
            self.report(
                site,
                Issue::UnsupportedField("display name override options"),
            );
            return Ok(());
        }
        let Some(Value::Entity(entity)) = self.evaluate(key.instance, node, target, None) else {
            self.report(
                site,
                Issue::UnsupportedField("display name override target"),
            );
            return Ok(());
        };
        if !script_expr::valid_entity(entity)
            || self.world.entity_component92(site, entity) == Some(false)
        {
            return Ok(());
        }
        if !self.world.set_display_name(site, entity, name, true) {
            self.report(
                site,
                Issue::UnsupportedField("display name override target"),
            );
            return Ok(());
        }
        self.state_mut(key).expect("active display name").kind = StateKind::DisplayName {
            binding: Some((entity, name)),
        };
        Ok(())
    }

    fn begin_ai_weapon_override(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let Some(Value::Entity(target)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_00D49978"),
            Value::Null,
        )?
        else {
            self.report(site, Issue::UnsupportedField("AI weapon target"));
            return Ok(());
        };
        let Some(slot) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_D66E7D68"),
            Value::Integer(9999),
        )?
        else {
            return Ok(());
        };
        let Some(Value::Asset(identifier)) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_F2810377"),
            Value::Asset(0),
        )?
        else {
            self.report(site, Issue::UnsupportedField("AI weapon identifier"));
            return Ok(());
        };
        let Some(enabled) = self.creation_scalar(
            key.instance,
            node,
            field(&node.fields, "m_F8585ABF"),
            Value::Null,
        )?
        else {
            return Ok(());
        };
        let priority = field(&node.fields, "m_priority");
        let (priority, prefer_later) = if priority.is_null() {
            (9999.0, false)
        } else {
            let priority = match script_expr::stack_priority(priority) {
                Ok(priority) => priority,
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(());
                }
            };
            (f64::from(priority.level), priority.above_equals)
        };
        let record = AiWeaponOverride {
            target,
            slot: script_expr::to_int(&slot, 9999),
            identifier,
            enabled: truthy(&enabled),
            priority,
            prefer_later,
        };
        match self.world.ai_weapon_override(site, record, true) {
            Some(true) => {
                self.state_mut(key).expect("active AI weapon state").kind =
                    StateKind::AiWeaponOverride {
                        record: Some(record),
                    }
            }
            Some(false) => (),
            None => self.report(site, Issue::UnsupportedField("AI weapon control component")),
        }
        Ok(())
    }

    fn check_health_capacity(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let target = self.evaluate(key.instance, node, field(&node.fields, "m_FA3ECC43"), None);
        let amount = self
            .evaluate(key.instance, node, field(&node.fields, "m_amount"), None)
            .and_then(pool_number);
        if target != Some(Value::Entity(site.entity))
            || field(&node.fields, "m_7E8B666C").as_u64() != Some(0)
            || !amount.is_some_and(|amount| {
                amount > 0.0
                    && self.world.body_health(site, Some(PoolKind::Health), true) == Some(amount)
            })
        {
            self.report(
                site,
                Issue::UnsupportedField("authored NPC health capacity"),
            );
        }
        Ok(())
    }

    fn begin_health_pool(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        if !matches!(
            self.evaluate(id, node, field(&node.fields, "m_FA3ECC43"), None),
            Some(Value::Entity(target)) if target == site.entity
        ) {
            self.report(site, Issue::UnsupportedField("health pool target"));
            return Ok(());
        }
        if site.graph == 0x0cbc {
            return self.begin_wave_health(key, node, site);
        }
        let Some(maximum) = self
            .evaluate(id, node, field(&node.fields, "m_amount"), Some(key))
            .and_then(pool_number)
        else {
            self.report(site, Issue::UnsupportedField("health pool capacity"));
            return Ok(());
        };
        let initial = field(&node.fields, "m_BCFDD2E9");
        let current = if initial.is_null() {
            maximum
        } else {
            let Some(current) = self.evaluate(id, node, initial, None).and_then(pool_number) else {
                self.report(site, Issue::UnsupportedField("health pool initial amount"));
                return Ok(());
            };
            current
        };
        let kind = pool_kind(field(&node.fields, "m_7E8B666C")).expect("validated pool kind");
        let Some(pool) = self.world.add_health_pool(site, kind, maximum, current) else {
            self.report(site, Issue::UnsupportedField("health pool registration"));
            return Ok(());
        };
        self.state_mut(key).expect("active health pool").kind =
            StateKind::HealthPool { pool: Some(pool) };
        self.register_tick(key, self.machine.now_ms);
        self.publish_health_pool(key, node, current)
    }

    fn begin_wave_health(
        &mut self,
        key: StateKey,
        node: &Node,
        site: Site,
    ) -> Result<(), InitFault> {
        const DIFFICULTY: u32 = 9948;
        let amount = field(&node.fields, "m_amount");
        let actual = self
            .world
            .entity_variable(crate::world::GAME_MODE_ENTITY, DIFFICULTY);
        let local = self
            .machine
            .value(VarKey::Instance(key.instance, DIFFICULTY), None);
        let Some(Value::Integer(difficulty @ 0..=3)) = actual else {
            self.report(site, Issue::UnsupportedField("wave health difficulty"));
            return Ok(());
        };
        if Some(local) != actual
            || self
                .world
                .body_health(site, Some(PoolKind::Health), true)
                .is_none_or(|health| health <= 0.0)
        {
            self.report(
                site,
                Issue::UnsupportedField("wave health difficulty or base health"),
            );
            return Ok(());
        }
        let table = array_field(field(amount, "m_814F8EC3"), "m_configVars");
        let Some(amount) = self
            .evaluate(key.instance, node, &table[difficulty as usize], None)
            .and_then(pool_number)
        else {
            self.report(site, Issue::UnsupportedField("wave health amount"));
            return Ok(());
        };
        if amount == 0.0 && node.pos == 43 && difficulty == 0 {
            return Ok(());
        }
        if amount <= 0.0 {
            self.report(site, Issue::UnsupportedField("wave health amount"));
            return Ok(());
        }
        let Some(pool) = self.world.add_wave_health(site, amount) else {
            self.report(site, Issue::UnsupportedField("wave health registration"));
            return Ok(());
        };
        if let Some(pool) = pool {
            self.state_mut(key).expect("active wave health pool").kind =
                StateKind::HealthPool { pool: Some(pool) };
            self.publish_health_totals(site)?;
        }
        Ok(())
    }

    fn publish_health_pool(
        &mut self,
        key: StateKey,
        node: &Node,
        current: f64,
    ) -> Result<(), InitFault> {
        let output = script_expr::lvalue(field(&node.fields, "m_CEAB19E8"))
            .expect("validated health pool output");
        if let Some(output) = output {
            let output = source_key(key.instance, output);
            let current = Value::Float(current);
            if !same(&self.machine.value(output, None), &current) {
                self.write(output, current)?;
            }
        }
        self.publish_health_totals(self.site(key.instance, node))
    }

    fn publish_health_totals(&mut self, site: Site) -> Result<(), InitFault> {
        for (variable, maximum) in [
            (OWNER_HEALTH, false),
            (HUD_HEALTH, false),
            (HUD_MAX_HEALTH, true),
        ] {
            if let Some(amount) = self.world.body_health(site, None, maximum) {
                let key = VarKey::Entity(variable);
                let value = Value::Float(amount);
                if !same(&self.machine.value(key, None), &value) {
                    self.write(key, value)?;
                }
            }
        }
        Ok(())
    }

    fn begin_stack(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(value) = self.evaluate(id, node, field(&node.fields, "m_value"), Some(key)) else {
            return Ok(());
        };
        let target = match script_expr::lvalue(field(&node.fields, "m_out_Var")) {
            Ok(Some(target)) => source_key(id, target),
            Ok(None) => return Ok(()),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        let priority = match script_expr::stack_priority(field(&node.fields, "m_priority")) {
            Ok(priority) => priority,
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        self.state_mut(key).expect("active stack").linked_variable = Some(target);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority,
                output: LinkValue::Fixed(value),
                stack: true,
            },
            depth,
        )
    }

    fn begin_cosmetic(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let target = match script_expr::lvalue(field(&node.fields, "m_1DEF59FA")) {
            Ok(Some(target)) => source_key(key.instance, target),
            Ok(None) => return Ok(()),
            Err(error) => {
                self.report(self.site(key.instance, node), Issue::Expression(error));
                return Ok(());
            }
        };
        self.state_mut(key)
            .expect("active cosmetic")
            .linked_variable = Some(target);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority: LINK_PRIORITY,
                stack: false,
                output: LinkValue::Fixed(Value::Handle {
                    kind: 1,
                    instance: u32::from(key.instance),
                    state: key.raw,
                    extra: 0,
                }),
            },
            depth,
        )
    }

    fn begin_chase(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        let id = key.instance;
        let target = match script_expr::lvalue(field(&node.fields, "m_out_Var")) {
            Ok(target) => target.map(|target| source_key(id, target)),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        let initial = field(&node.fields, "m_EB0FC261");
        let current = if initial.is_null() {
            target.map_or(Value::Null, |target| self.machine.value(target, None))
        } else {
            let Some(value) = self.evaluate(id, node, initial, None) else {
                return Ok(());
            };
            value
        };
        let current = chase_value(&current);
        let remaining_ms = if configured(node, "m_5BEFF040") {
            let Some(value) = self.evaluate(id, node, field(&node.fields, "m_5BEFF040"), None)
            else {
                return Ok(());
            };
            Some((script_expr::to_float(&value) * 1000.0) as i64)
        } else {
            None
        };
        let now = self.machine.now_ms;
        let state = self.state_mut(key).expect("active chase");
        state.chase = Some(ChasePayload {
            current: current.clone(),
            reached: false,
            remaining_ms,
            last_ms: now,
        });
        state.linked_variable = target;
        if let Some(target) = target {
            let old = self.machine.value(target, None);
            self.set_base(target, current);
            self.insert_link(
                target,
                VariableLink {
                    state: key,
                    priority: LINK_PRIORITY,
                    output: LinkValue::Chase,
                    stack: false,
                },
                depth,
            )?;
            self.notify_value(target, &old, None)?;
        }
        self.register_tick(key, now);
        Ok(())
    }

    fn tick_chase(&mut self, key: StateKey) -> Result<(), InitFault> {
        let id = key.instance;
        let now = self.machine.now_ms;
        let Some(state) = self.state_mut(key) else {
            return Ok(());
        };
        let target = state.linked_variable;
        let Some(chase) = state.chase.as_mut() else {
            return Ok(());
        };
        let elapsed = (now - chase.last_ms) as i64;
        chase.last_ms = now;
        if elapsed == 0 || chase.reached {
            return Ok(());
        }
        let mut next = chase.clone();
        let before = target.map(|target| self.machine.value(target, None));
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("chase state");
        let Some(destination) =
            self.evaluate(id, node, field(&node.fields, "m_destination"), Some(key))
        else {
            return Ok(());
        };
        let destination = chase_value(&destination);
        if let Some(remaining) = next.remaining_ms {
            if remaining <= elapsed {
                next.current = destination;
                next.reached = true;
            } else {
                let share = f64::from((elapsed as f64 / remaining as f64) as f32);
                next.current = chase_lerp(next.current, destination, share);
                next.remaining_ms = Some(remaining - elapsed);
            }
        } else {
            let rate = field(&node.fields, "m_9B0385B4");
            let rate = if rate.is_null() {
                1.0
            } else {
                let Some(value) = self.evaluate(id, node, rate, Some(key)) else {
                    return Ok(());
                };
                script_expr::to_float(&value)
            };
            if rate > 0.0 {
                let per_ms = (rate * f64::from(0.001_f32)) as f32;
                let distance = (f64::from(per_ms) * elapsed as f64) as f32;
                let step = f64::from((f64::from(distance) + script_expr::EPSILON) as f32);
                (next.current, next.reached) = chase_step(next.current, destination, step);
            }
        }
        let reached = next.reached;
        self.state_mut(key).expect("active chase").chase = Some(next);
        if let (Some(target), Some(before)) = (target, before) {
            self.notify_value(target, &before, None)?
        }
        if reached {
            self.post(id, Event::CheckTransitions(key.raw))?;
            if !configured(node, "m_CB671004") {
                self.post(id, Event::Finish(key.raw))?
            }
        }
        Ok(())
    }

    fn chase_write(&mut self, key: StateKey, target: VarKey) -> Result<(), InitFault> {
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("chase state");
        match field(&node.fields, "m_1E10B2B1").as_i64().unwrap_or(0) {
            0 => self.unlink_single_output(key, self.output_write_depth)?,
            1 => {
                let value = chase_value(&stored(&self.machine.snapshot, target));
                if let Some(chase) = self.state_mut(key).and_then(|state| state.chase.as_mut()) {
                    chase.current = value;
                }
            }
            _ => (),
        }
        Ok(())
    }

    fn finish_chase(&mut self, key: StateKey, depth: usize) -> Result<(), InitFault> {
        let Some(state) = self.state_mut(key) else {
            return Ok(());
        };
        let Some(target) = state.linked_variable.take() else {
            return Ok(());
        };
        let current = state.chase.as_ref().map(|chase| chase.current.clone());
        self.remove_link(key, target, current, depth)
    }

    fn volley_config(&mut self, id: u16, node: &Node, name: &str, default: Value) -> Option<Value> {
        let config = field(&node.fields, name);
        if config.is_null() {
            Some(default)
        } else {
            self.evaluate(id, node, config, None)
        }
    }

    fn begin_volley(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        let id = key.instance;
        let names = [
            "m_A17BE89B",
            "m_A55BC904",
            "m_5F40D9D3",
            "m_numShotsPerSecond",
        ];
        let mut config = [
            Value::Integer(1),
            Value::Integer(1),
            Value::Integer(1),
            Value::Float(1.0),
        ];
        for (value, name) in config.iter_mut().zip(names) {
            let Some(found) = self.volley_config(id, node, name, value.clone()) else {
                return Ok(());
            };
            *value = found;
        }
        let [max, min, per, rate] = config;
        let (Ok(maximum), Ok(minimum)) = (
            u32::try_from(script_expr::to_int(&max, 0).max(0)),
            u32::try_from(script_expr::to_int(&min, 0).max(0)),
        ) else {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("weapon volley shot count"),
            );
            return Ok(());
        };
        let per_shot = script_expr::to_int(&per, 0).max(0);
        let rate = if rate == Value::Null {
            1.0
        } else {
            script_expr::to_float(&rate).max(1e-5)
        };
        if !rate.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("weapon volley rate"),
            );
            return Ok(());
        }
        let mut outputs = [None; 3];
        for (output, name) in outputs
            .iter_mut()
            .zip(["m_24E351A4", "m_17770E7E", "m_3FFC9EB9"])
        {
            let cfg = field(&node.fields, name);
            if cfg.is_null() {
                continue;
            }
            *output = match script_expr::lvalue(cfg) {
                Ok(Some(var)) => Some(var),
                Ok(None) => {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("weapon volley output"),
                    );
                    return Ok(());
                }
                Err(error) => {
                    self.report(self.site(id, node), Issue::Expression(error));
                    return Ok(());
                }
            };
        }
        let [ammo_var, _, _] = outputs;
        let outputs = outputs.map(|output| output.map(|var| source_key(id, var)));
        for (index, output) in outputs.iter().enumerate() {
            if output.is_some() && outputs[..index].contains(output) {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("duplicate weapon volley output"),
                );
                return Ok(());
            }
        }
        let ammo0 = outputs[0].map_or_else(
            || i64::from(maximum).saturating_mul(per_shot),
            |var| script_expr::to_int(&self.machine.value(var, None), 0).max(0),
        );
        let spread = outputs[2].map_or(0.0, |var| {
            script_expr::to_float(&self.machine.value(var, None)).clamp(0.0, 1.0)
        });
        if !spread.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("weapon volley spread"),
            );
            return Ok(());
        }
        let planned = if per_shot == 0 {
            maximum
        } else {
            (ammo0 / per_shot).min(i64::from(maximum)) as u32
        };
        let start_ms = self.machine.now_ms;
        self.state_mut(key).expect("active volley").kind = StateKind::WeaponVolley {
            volleys: 1,
            offset_ms: 0,
            ammo0,
            ammo_var,
        };
        self.machine.volleys.insert(
            key,
            Volley {
                start_ms,
                ammo0,
                per_shot,
                rate,
                spread,
                planned,
                reported: 0,
                outputs,
            },
        );
        for (slot, (target, base)) in outputs
            .into_iter()
            .zip([
                Value::Integer(ammo0),
                Value::Float(rate),
                Value::Float(spread),
            ])
            .enumerate()
        {
            let Some(target) = target else { continue };
            let before = self.machine.value(target, None);
            self.set_base(target, base);
            self.insert_link(
                target,
                VariableLink {
                    state: key,
                    priority: LINK_PRIORITY,
                    stack: false,
                    output: LinkValue::Volley(slot as u8),
                },
                depth,
            )?;
            self.notify_value(target, &before, None)?;
        }
        if per_shot == 0 || i64::from(minimum) < ammo0 / per_shot {
            self.post_after(
                id,
                shot_delay(minimum.saturating_sub(1), rate),
                Event::Timer(key.raw, 1),
            )?;
        }
        self.post_after(
            id,
            shot_delay(planned.saturating_sub(1), rate),
            Event::Timer(key.raw, 2),
        )
    }

    fn report_active_volleys(&mut self) -> Result<(), InitFault> {
        let active: Vec<_> = self
            .machine
            .snapshot
            .instances
            .iter()
            .flat_map(|(&id, instance)| {
                instance.states.iter().filter_map(move |(&raw, state)| {
                    (state.active && matches!(state.kind, StateKind::WeaponVolley { .. }))
                        .then_some(StateKey { instance: id, raw })
                })
            })
            .collect();
        for key in active {
            self.report_volley(key)?
        }
        Ok(())
    }

    fn report_volley(&mut self, key: StateKey) -> Result<(), InitFault> {
        if self.machine.owner_dead {
            return Ok(());
        }
        let Some(activation) = self.state(key).map(|state| state.activation) else {
            return Ok(());
        };
        let instance = &self.machine.snapshot.instances[&key.instance];
        let Some(volley) = self.machine.volleys.get_mut(&key) else {
            return Ok(());
        };
        while volley.reported < volley.planned {
            let time_ms = volley
                .start_ms
                .saturating_add(shot_delay(volley.reported, volley.rate));
            if time_ms > self.machine.now_ms {
                break;
            }
            if self.machine.shots.len() == MAX_PENDING_SHOTS {
                return Err(InitFault::PendingShots);
            }
            self.machine.shots.push(ShotEvent {
                entity: self.machine.snapshot.entity,
                instance: key.instance,
                graph: instance.graph,
                raw_state: key.raw,
                frame: (time_ms / FRAME_MS) as u32,
                time_ms,
                activation,
                activation_ms: volley.start_ms,
                ordinal: volley.reported,
            });
            volley.reported += 1;
        }
        Ok(())
    }

    fn finish_volley(&mut self, key: StateKey, depth: usize) -> Result<(), InitFault> {
        self.report_volley(key)?;
        let Some(volley) = self.machine.volleys.get(&key) else {
            return Ok(());
        };
        let outputs = volley.outputs;
        let at = self.machine.now_ms.saturating_add(1);
        let values = [
            volley.output(0, at),
            volley.output(1, at),
            volley.output(2, at),
        ];
        for (target, value) in outputs.into_iter().zip(values) {
            if let Some(target) = target {
                self.remove_link(key, target, Some(value), depth)?
            }
        }
        self.machine.volleys.remove(&key);
        Ok(())
    }

    fn insert_link(
        &mut self,
        target: VarKey,
        link: VariableLink,
        depth: usize,
    ) -> Result<(), InitFault> {
        let old = self.machine.value(target, None);
        match target {
            VarKey::Entity(id) => self
                .machine
                .snapshot
                .variables
                .entry(id)
                .or_insert(Value::Null),
            VarKey::Instance(instance, id) => self.machine.snapshot.instances[&instance]
                .variables
                .entry(id)
                .or_insert(Value::Null),
        };
        let entries = self.machine.links.entry(target).or_default();
        entries.retain(|entry| entry.state != link.state);
        let at = entries
            .iter()
            .position(|entry| {
                entry.priority.level > link.priority.level
                    || (entry.priority.level == link.priority.level && !link.priority.above_equals)
            })
            .unwrap_or(entries.len());
        entries.insert(at, link);
        self.notify_value(target, &old, None)?;
        self.sync_stack(target, depth)
    }

    fn sync_stack(&mut self, variable: VarKey, depth: usize) -> Result<(), InitFault> {
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        let Some(entries) = self.machine.links.get(&variable) else {
            return Ok(());
        };
        let states: Vec<_> = entries
            .iter()
            .filter(|entry| entry.stack)
            .map(|entry| entry.state)
            .collect();
        for key in states {
            self.spend("stack position change")?;
            let Some(state) = self
                .state(key)
                .filter(|state| state.active && !state.ending)
            else {
                continue;
            };
            let top = self
                .machine
                .links
                .get(&variable)
                .and_then(|entries| entries.last());
            if let StateKind::Ability { flags } = state.kind {
                if flags & ABILITY_PUSHED != 0 && state.ability_stack == Some(variable) {
                    let node = self
                        .graph(key.instance)
                        .state(usize::from(key.raw))
                        .expect("ability state");
                    if top.is_some_and(|entry| {
                        entry.state == key && matches!(entry.output, LinkValue::AbilityStack)
                    }) {
                        if flags & ABILITY_RUNNING == 0 {
                            self.start_ability(key, node, depth + 1)?;
                        }
                    } else {
                        self.stop_ability(key, node, depth + 1)?;
                    }
                }
                continue;
            }
            if state.linked_variable != Some(variable) {
                continue;
            }
            let top = top.is_some_and(|entry| entry.state == key);
            let kind = StateKind::Stack { top, under: !top };
            if state.kind == kind {
                continue;
            }
            self.state_mut(key).expect("active stack").kind = kind;
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("stack state");
            let (old, next) = if top {
                ("m_underSubgraphPlug", "m_topSubgraphPlug")
            } else {
                ("m_topSubgraphPlug", "m_underSubgraphPlug")
            };
            self.exit_subgraph(key.instance, node, old, depth + 1)?;
            self.follow(key.instance, node, next, Some(key), depth)?;
        }
        Ok(())
    }

    fn unlink_single_output(&mut self, key: StateKey, depth: usize) -> Result<(), InitFault> {
        let Some(target) = self
            .state_mut(key)
            .and_then(|state| state.linked_variable.take())
        else {
            return Ok(());
        };
        self.remove_link(key, target, None, depth)
    }

    fn remove_link(
        &mut self,
        key: StateKey,
        target: VarKey,
        bake: Option<Value>,
        depth: usize,
    ) -> Result<(), InitFault> {
        self.unlink_output(key, target, bake)?;
        self.sync_stack(target, depth)
    }

    fn unlink_output(
        &mut self,
        key: StateKey,
        target: VarKey,
        bake: Option<Value>,
    ) -> Result<(), InitFault> {
        let old = self.machine.value(target, None);
        if let Some(value) = bake {
            self.set_base(target, value);
        }
        if let Some(entries) = self.machine.links.get_mut(&target) {
            entries.retain(|entry| entry.state != key);
            if entries.is_empty() {
                self.machine.links.remove(&target);
            }
        }
        self.notify_value(target, &old, None)
    }

    fn update_stack(&mut self, key: StateKey) -> Result<(), InitFault> {
        let Some(target) = self.state(key).and_then(|state| state.linked_variable) else {
            return Ok(());
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("stack state");
        let value = field(&node.fields, "m_value");
        if value.is_null() {
            return Ok(());
        }
        let Some(value) = self.evaluate(id, node, value, Some(key)) else {
            return Ok(());
        };
        let old = self.machine.value(target, None);
        if let Some(LinkValue::Fixed(current)) = self
            .machine
            .links
            .get_mut(&target)
            .and_then(|entries| entries.iter_mut().find(|entry| entry.state == key))
            .map(|entry| &mut entry.output)
        {
            if !same(current, &value) {
                *current = value;
                self.notify_value(target, &old, Some(key))?;
            }
        }
        Ok(())
    }

    // Publish on activation, dependency changes and explicit polling, rather
    // than rerolling random-valued rows whenever a mover reads its properties.
    fn refresh_movement_modifier(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let id = key.instance;
        let mut modifiers = BTreeMap::new();
        for (slot, config) in array_field(&node.fields, "m_BC5E91CF").iter().enumerate() {
            self.spend("movement modifier")?;
            let shape = config.get("$").and_then(Json::as_str);
            let (operation, default) = match shape {
                Some("STU_4CB0950F") => (Operation::Add, 0.0),
                Some("STU_D8CC6B10") => (Operation::Multiply, 1.0),
                Some("STU_56DEE1BB") => (Operation::Set, 0.0),
                Some("STU_C61711C8") => (Operation::Minimum, f64::from(f32::MAX)),
                Some("STU_F197CFBF") => (Operation::Maximum, f64::from(f32::MIN_POSITIVE)),
                _ => {
                    self.report(
                        self.site(id, node),
                        Issue::UnsupportedField("movement modifier operation"),
                    );
                    continue;
                }
            };
            let Some(index) = config
                .get("m_A9561CA0")
                .and_then(Json::as_u64)
                .and_then(|value| u8::try_from(value).ok())
            else {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("movement modifier index"),
                );
                continue;
            };
            let Some(value) = self.movement_number(key, node, field(config, "m_9B7A63EA"), default)
            else {
                continue;
            };
            let Some(priority) = self.movement_number(key, node, field(config, "m_priority"), 0.0)
            else {
                continue;
            };
            modifiers.insert(
                ModifierKey {
                    instance: id,
                    state: key.raw,
                    slot,
                },
                Modifier {
                    index,
                    operation,
                    value,
                    priority,
                },
            );
        }
        self.machine
            .movement_modifiers
            .retain(|entry, _| entry.instance != id || entry.state != key.raw);
        self.machine.movement_modifiers.extend(modifiers);
        Ok(())
    }

    fn movement_number(
        &mut self,
        key: StateKey,
        node: &Node,
        config: &Json,
        default: f64,
    ) -> Option<f64> {
        if config.is_null() {
            return Some(default);
        }
        let value = self.evaluate(key.instance, node, config, Some(key))?;
        Some(match value {
            Value::Bool(_) | Value::Integer(_) | Value::Float(_) => {
                f64::from(script_expr::to_float(&value) as f32)
            }
            _ => default,
        })
    }

    fn register_tick(&mut self, key: StateKey, start_ms: u64) {
        self.machine
            .ticking
            .entry(key.instance)
            .or_default()
            .entry(key.raw)
            .or_insert(start_ms);
    }

    fn tick_states(&mut self, id: u16) -> Result<(), InitFault> {
        let now = self.machine.now_ms;
        let due: Vec<_> = self
            .machine
            .ticking
            .get(&id)
            .into_iter()
            .flatten()
            .filter_map(|(&raw, &last)| (last != now).then_some(raw))
            .collect();
        for raw in due {
            self.spend("polled state tick")?;
            if let Some(last) = self
                .machine
                .ticking
                .get_mut(&id)
                .and_then(|states| states.get_mut(&raw))
            {
                let elapsed_ms = now.saturating_sub(*last);
                *last = now;
                let key = StateKey { instance: id, raw };
                match self
                    .state(key)
                    .filter(|state| state.active)
                    .map(|state| state.kind)
                {
                    Some(StateKind::BooleanSwitch(_)) => {
                        self.check_switch(key, 0)?;
                    }
                    Some(StateKind::DelayedSwitch { .. }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("delayed switch");
                        self.recheck_delayed_switch(key, node, 0)?;
                    }
                    Some(StateKind::HealthPool { pool: Some(pool) }) => {
                        let node = self.graph(id).state(usize::from(raw)).expect("health pool");
                        if let Some(current) =
                            self.world.health_pool_current(self.site(id, node), pool)
                        {
                            self.publish_health_pool(key, node, current)?;
                        }
                    }
                    Some(StateKind::ShieldRecharge) => {
                        self.tick_shield_recharge(key, elapsed_ms)?
                    }
                    Some(StateKind::WitchStaffHealing) => {
                        self.tick_witch_staff_healing(key, elapsed_ms)?
                    }
                    Some(StateKind::SummonerBeamRay) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("Summoner beam ray");
                        self.refresh_summoner_beam_ray(key, node)?;
                    }
                    Some(StateKind::SummonerBeamDamage) => {
                        self.tick_summoner_beam_damage(key, elapsed_ms)?
                    }
                    Some(StateKind::WitchStaffEffect) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("Witch staff effect");
                        self.refresh_witch_staff_effect(key, node)?;
                    }
                    Some(StateKind::MovementMod) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("movement modifier");
                        self.refresh_movement_modifier(key, node)?;
                    }
                    Some(StateKind::MovementSample(previous)) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("movement sample");
                        self.update_movement_sample(key, node, previous)?;
                    }
                    Some(StateKind::Switch { .. }) => self.post(id, Event::Reevaluate(raw))?,
                    Some(StateKind::ForCode) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("for-code state");
                        self.publish_for_code(key, node)?;
                    }
                    Some(StateKind::TrackedTargets) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("tracked target state");
                        self.refresh_tracked_targets(key, node)?;
                    }
                    Some(StateKind::DeadeyeEffects) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("Deadeye effect state");
                        self.refresh_deadeye_effects(key, node)?;
                    }
                    Some(StateKind::ExplosionContact) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("contact state");
                        self.refresh_contact(key, node)?;
                    }
                    Some(StateKind::AiBehavior { record: Some(_) }) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("AI behavior state");
                        self.update_ai_behavior(key, node)?;
                    }
                    Some(StateKind::Ability { .. }) => self.activate_ability(key, 0)?,
                    Some(StateKind::ChaseVar) => self.tick_chase(key)?,
                    Some(StateKind::Watch) => self.check_watch(key)?,
                    Some(StateKind::LivingBodyArray) => self.refresh_living_body_array(key)?,
                    Some(StateKind::AiFacing(Some(record))) => {
                        let node = self
                            .graph(id)
                            .state(usize::from(raw))
                            .expect("AI facing state");
                        self.update_ai_facing(key, node, Some(record))?;
                    }
                    Some(StateKind::WaitFrames { target }) if target <= self.machine.frame => {
                        self.post(id, Event::Finish(raw))?;
                    }
                    _ => (),
                }
            }
        }
        Ok(())
    }

    fn clear_tick(&mut self, key: StateKey) {
        if let Some(states) = self.machine.ticking.get_mut(&key.instance) {
            states.shift_remove(&key.raw);
            if states.is_empty() {
                self.machine.ticking.remove(&key.instance);
            }
        }
    }

    fn arm_wait(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(seconds) = self.evaluate(id, node, field(&node.fields, "m_timeout"), Some(key))
        else {
            return Ok(());
        };
        let milliseconds = ((script_expr::to_float(&seconds) * 1000.0) as f32).round();
        if !milliseconds.is_finite() {
            self.report(self.site(id, node), Issue::UnsupportedField("Wait timeout"));
            return Ok(());
        }
        if milliseconds <= 0.0 {
            self.post(id, Event::Finish(key.raw))
        } else {
            self.post_after(id, milliseconds as u64, Event::Timer(key.raw, 1))
        }
    }

    fn arm_animation(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let id = key.instance;
        let duration = field(&node.fields, "m_86701C3E");
        if duration.is_null() {
            let client_removes_zombardier_animation = (self.graph(id).index, node.pos)
                == (0x0ca1, 58)
                && field(&node.fields, "m_154DBA91")["$"] == "STUConfigVarBool"
                && field(&node.fields, "m_154DBA91")["m_value"].as_u64() == Some(1)
                && is_unlinked_plug(&node.fields, "m_onEndPlug", "STUStatescriptOutputPlug");
            // Without m_playOverDuration the length is the animation asset's,
            // which the reference does not know either: it arms no timer and
            // no finish, so the state stays on until a transition or its
            // parent ends it. Accept the untimed idle shape and Zombardier's
            // client-removed firing animation when they have no timer links.
            if (!field(&node.fields, "m_154DBA91").is_null()
                && !client_removes_zombardier_animation)
                || !is_unlinked_plug(&node.fields, "m_A3B86BB0", "STUStatescriptOutputPlug")
                || !is_unlinked_plug(&node.fields, "m_onFinishedPlug", "STUStatescriptOutputPlug")
            {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("animation asset duration"),
                );
            }
            return Ok(());
        }
        let Some(seconds) = self.evaluate(id, node, duration, None) else {
            return Ok(());
        };
        let delay = ((script_expr::to_float(&seconds) * 1000.0) as f32).round();
        if !delay.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("animation duration"),
            );
            return Ok(());
        }
        let removes_self = field(&node.fields, "m_154DBA91");
        let client_ends = if removes_self.is_null() {
            false
        } else {
            let Some(value) = self.evaluate(id, node, removes_self, None) else {
                return Ok(());
            };
            truthy(&value)
        };
        let delay_ms = delay.max(0.0) as u64;
        self.post_after(id, delay_ms, Event::Timer(key.raw, 1))?;
        if !client_ends {
            self.post_after(id, delay_ms, Event::Finish(key.raw))?;
        }
        Ok(())
    }

    fn arm_frame_wait(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(value) = self.evaluate(id, node, field(&node.fields, "m_3016B9A1"), None) else {
            return Ok(());
        };
        let count = script_expr::to_int(&value, 0) as u32;
        let target = self.machine.frame.wrapping_add(count);
        self.state_mut(key).expect("active frame wait").kind = StateKind::WaitFrames { target };
        self.register_tick(key, self.machine.now_ms);
        if target <= self.machine.frame {
            self.post(id, Event::Finish(key.raw))
        } else {
            Ok(())
        }
    }

    fn stamp_cinematic_time_scale(&mut self, key: StateKey, node: &Node) {
        let Some(delay) =
            self.evaluate(key.instance, node, field(&node.fields, "m_17D12F6B"), None)
        else {
            return;
        };
        let delay = script_expr::to_int(&delay, 0).max(0) as u32;
        let boundary_frame = self.machine.frame.wrapping_add(delay);
        self.state_mut(key)
            .expect("cinematic time scale state")
            .kind = StateKind::CinematicTimeScale { boundary_frame };
    }

    fn arm_phase_transition(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let mut durations = [0; 3];
        for (duration, name) in durations
            .iter_mut()
            .zip(["m_AD8C28BA", "m_C69C2A73", "m_D7ED647E"])
        {
            let Some(value) = self
                .evaluate(key.instance, node, field(&node.fields, name), None)
                .and_then(pool_number)
                .filter(|value| *value >= 0.0 && *value < 2_147_483.0)
            else {
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField("phase transition duration"),
                );
                return Ok(());
            };
            *duration = (value * 1000.0).round() as u64;
        }
        let [enter, hold, exit] = durations;
        self.post_after(key.instance, enter, Event::Timer(key.raw, 1))?;
        self.post_after(key.instance, enter + hold + exit, Event::Abort(key.raw))
    }

    fn timer(&mut self, key: StateKey, parameter: u16) -> Result<(), InitFault> {
        let Some(state) = self.state(key).filter(|state| state.active) else {
            return Ok(());
        };
        let kind = state.kind;
        if kind == StateKind::Wait && parameter == 1 {
            self.post(key.instance, Event::Finish(key.raw))?;
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("active timer state");
        if kind == StateKind::VoiceStimulus {
            return self.voice_timer(key, node, parameter);
        }
        if kind == StateKind::Defer && parameter == 1 {
            self.follow(key.instance, node, "m_FD0C0646", Some(key), 0)?;
            if self.state(key).is_some_and(|state| state.active) {
                self.follow(key.instance, node, "m_8E484B2E", Some(key), 0)?;
            }
            return Ok(());
        }
        if matches!(kind, StateKind::LogicalButton { .. })
            && parameter == 1
            && !self.button_held(key, node)
        {
            self.set_button_down(key, node, false, 0)?;
        }
        if kind == StateKind::PhaseTransition && parameter == 1 {
            self.follow(key.instance, node, "m_82A8A67E", Some(key), 0)?;
        }
        if kind == StateKind::Anim && parameter == 1 {
            self.follow(key.instance, node, "m_A3B86BB0", Some(key), 0)?;
        }
        if let StateKind::BossIntroVoice(cue) = kind {
            if parameter == 1 {
                let site = self.site(key.instance, node);
                if let Some(duration_ms) = self.world.play_boss_intro_voice(site, cue) {
                    self.post_after(key.instance, duration_ms, Event::Finish(key.raw))?;
                } else {
                    self.report(site, Issue::UnsupportedField("Boss intro voice service"));
                }
            }
        }
        if kind == StateKind::VictoryVoice && parameter == 1 {
            let site = self.site(key.instance, node);
            if let Some(actor) =
                self.evaluate(key.instance, node, field(&node.fields, "m_8C8C5285"), None)
            {
                if let Some(duration_ms) = self.world.play_victory_voice(site, &actor) {
                    self.post_after(key.instance, duration_ms, Event::Finish(key.raw))?;
                } else {
                    self.report(site, Issue::UnsupportedField("victory voice service"));
                }
            }
        }
        if let StateKind::GateVoice(stimulus) = kind {
            if parameter == 1 {
                let attacker = field(&node.fields, "m_8C8C5285");
                let attacker = if attacker.is_null() {
                    None
                } else {
                    match self.evaluate(key.instance, node, attacker, None) {
                        Some(Value::Entity(entity)) => Some(entity),
                        _ => None,
                    }
                };
                let site = self.site(key.instance, node);
                if let Some(duration_ms) = self.world.play_gate_voice(site, stimulus, attacker) {
                    self.post_after(key.instance, duration_ms, Event::Finish(key.raw))?;
                } else {
                    self.report(site, Issue::UnsupportedField("gate voice service"));
                }
            }
        }
        if let StateKind::DelayedSwitch { current, undecided } = kind {
            let branch = match parameter {
                SWITCH_TO_TRUE => Some(true),
                SWITCH_TO_FALSE => Some(false),
                _ => None,
            };
            if let Some(branch) = branch {
                self.state_mut(key)
                    .expect("active delayed switch")
                    .delayed_timers[usize::from(branch)] = false;
                if undecided || current != branch {
                    self.set_delayed_branch(key, node, branch, 0)?;
                }
            }
        }
        if matches!(kind, StateKind::Ability { .. }) && matches!(parameter, 1 | 2) {
            self.activate_ability(key, 0)?;
        }
        if matches!(kind, StateKind::WeaponVolley { .. }) {
            match parameter {
                1 => {
                    self.follow(key.instance, node, "m_onMinShotsPlug", Some(key), 0)?;
                }
                2 => self.post(key.instance, Event::Finish(key.raw))?,
                _ => (),
            }
        }
        self.try_complete(key, node, 0)
    }

    fn spawn_child(&mut self, key: StateKey, node: &Node) -> Result<Option<u16>, InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let Some(guid) = node
            .field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph")
            .and_then(asset_guid)
        else {
            self.report(site, Issue::InvalidSubScript);
            return Ok(None);
        };
        let graph = u32::from(guid as u16);
        if self.graphs.graph(graph).is_none() {
            self.report(site, Issue::MissingGraph(graph));
            return Ok(None);
        }
        let mut vars = Variables::new();
        let mut entity_vars = Variables::new();
        if (site.graph, node.pos, graph) == (0x0c95, 7, 0x0ce2) {
            let schema = node.field("m_36F7B64C.m_36F7B64C").unwrap_or(&Json::Null);
            if field(schema, "$").as_str() != Some("STU_29C4C486")
                || !field(schema, "m_CB2634D7").is_null()
                || field(schema, "m_75408640").as_u64() != Some(0)
                || !field(schema, "m_17BB822A").is_array()
            {
                self.report(site, Issue::UnsupportedField("subscript schema override"));
                return Ok(None);
            }
            for row in array_field(schema, "m_17BB822A") {
                self.spend("subscript schema override")?;
                let identifier = asset_guid(field(row, "m_0D09D2D9")).filter(|id| {
                    matches!(id, 0x0d80_0000_0000_01af | 0x0d80_0000_0000_01f5)
                        && field(row, "$").as_str() == Some("STU_6364CD4F")
                });
                let Some(identifier) = identifier else {
                    self.report(site, Issue::UnsupportedField("subscript schema override"));
                    return Ok(None);
                };
                let Some(value) = self.evaluate(id, node, field(row, "m_value"), None) else {
                    return Ok(None);
                };
                vars.insert(u32::from(identifier as u16), value);
            }
        }
        for item in array_field(&node.fields, "m_89402D74") {
            if !item.is_object() {
                continue;
            }
            let target = match script_expr::lvalue(field(item, "m_out_Var")) {
                Ok(Some(target)) => target,
                Ok(None) => continue,
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(None);
                }
            };
            let config = field(item, "m_value");
            let value = if script_expr::native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                self.assignment_group(id, node, config)?
            } else {
                self.evaluate(id, node, config, None)
            };
            let Some(value) = value else {
                return Ok(None);
            };
            match target.scope {
                Scope::Instance => vars.insert(target.id, value),
                Scope::Entity => entity_vars.insert(target.id, value),
            };
        }
        let child =
            u16::try_from(self.machine.next_id).map_err(|_| InitFault::InstanceIdExhausted)?;
        if self.create(child, graph, Some((id, key.raw)), vars, Startup::Ordinary)? {
            // The reference sets child binding bases directly before Entry.
            self.machine.snapshot.variables.extend(entity_vars);
            return Ok(Some(child));
        }
        Ok(None)
    }

    fn try_complete(&mut self, key: StateKey, node: &Node, depth: usize) -> Result<(), InitFault> {
        for (index, _) in array_field(&node.fields, "m_transitionPlug")
            .iter()
            .enumerate()
        {
            if self.follow(
                key.instance,
                node,
                &format!("m_transitionPlug[{index}]"),
                Some(key),
                depth,
            )? {
                self.post(key.instance, Event::Abort(key.raw))?;
                break;
            }
        }
        Ok(())
    }

    fn changed(&mut self, key: StateKey) -> Result<(), InitFault> {
        if let Some(state) = self.state_mut(key) {
            state.pending_change = false;
            if state.active && matches!(state.kind, StateKind::Switch { .. }) {
                self.post(key.instance, Event::Reevaluate(key.raw))?;
            } else if state.active
                && !state.pending_timer
                && matches!(
                    state.kind,
                    StateKind::BooleanSwitch(_)
                        | StateKind::DelayedSwitch { .. }
                        | StateKind::AiBehavior { record: Some(_) }
                )
            {
                state.pending_timer = true;
                self.post(key.instance, Event::Reevaluate(key.raw))?;
            } else if state.active && matches!(state.kind, StateKind::Stack { .. }) {
                self.update_stack(key)?;
            } else if state.active && state.kind == StateKind::AiControlSetting {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("AI control setting state");
                self.update_ai_control_setting(key, node);
            } else if state.active && state.kind == StateKind::MovementMod {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("movement modifier");
                self.refresh_movement_modifier(key, node)?;
            } else if state.active && state.kind == StateKind::ExplosionContact {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("contact state");
                self.refresh_contact(key, node)?;
            } else if state.active && state.kind == StateKind::PhaseScriptList {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("phase script list");
                self.refresh_phase_scripts(key, node)?;
            } else if state.active && state.kind == StateKind::Watch {
                self.check_watch(key)?;
            } else if state.active && state.kind == StateKind::LivingBodyArray {
                self.refresh_living_body_array(key)?;
            } else if state.active && matches!(state.kind, StateKind::HealthPool { pool: Some(_) })
            {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("active health pool");
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField("changing health pool capacity"),
                );
            }
        }
        if self.state(key).is_some_and(|state| state.active) {
            let graph = self.graph(key.instance);
            if graph.index == 0x0c8c {
                if let Some(node) = graph.state(usize::from(key.raw)) {
                    if matches!(node.pos, 33 | 275) {
                        self.try_complete(key, node, 0)?;
                    }
                }
            }
        }
        Ok(())
    }

    fn check_watch(&mut self, key: StateKey) -> Result<(), InitFault> {
        let Some(seen) = self
            .state(key)
            .filter(|state| state.active)
            .and_then(|state| state.watch_seen.clone())
        else {
            return Ok(());
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("Watch state");
        if self
            .evaluate(id, node, field(&node.fields, "m_B7A3CF78"), Some(key))
            .is_some_and(|value| !equal(&value, &seen))
        {
            self.post(id, Event::WatchChanged(key.raw))?;
        }
        Ok(())
    }

    fn deliver_watch(&mut self, key: StateKey) -> Result<(), InitFault> {
        let Some(seen) = self
            .state(key)
            .filter(|state| state.active)
            .and_then(|state| state.watch_seen.clone())
        else {
            return Ok(());
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("Watch state");
        match script_expr::lvalue(field(&node.fields, "m_48A8CDEC")) {
            Ok(Some(target)) => self.write(source_key(id, target), seen)?,
            Ok(None) => (),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        }
        self.follow(id, node, "m_A70DB860", Some(key), 0)?;
        let seen = self.evaluate(id, node, field(&node.fields, "m_B7A3CF78"), Some(key));
        if let Some(state) = self.state_mut(key) {
            state.watch_seen = seen
        }
        self.try_complete(key, node, 0)
    }

    fn reevaluate(&mut self, key: StateKey, depth: usize) -> Result<(), InitFault> {
        let Some(state) = self.state_mut(key).filter(|state| state.active) else {
            return Ok(());
        };
        state.pending_timer = false;
        let kind = state.kind;
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("switch state");
        let evaluated = if matches!(kind, StateKind::DelayedSwitch { .. }) {
            self.recheck_delayed_switch(key, node, depth)?;
            true
        } else if matches!(kind, StateKind::AiBehavior { record: Some(_) }) {
            self.update_ai_behavior(key, node)?;
            true
        } else if let StateKind::Switch { case } = kind {
            let Some(next) = self.switch_case(key, node) else {
                return Ok(());
            };
            if next != case {
                self.exit_subgraph(key.instance, node, &case_path(case), depth + 1)?;
                self.state_mut(key).expect("active switch").kind = StateKind::Switch { case: next };
                self.follow(key.instance, node, &case_path(next), Some(key), depth)?;
            }
            true
        } else {
            self.check_switch(key, depth)?
        };
        if evaluated {
            self.try_complete(key, node, depth)?;
        }
        Ok(())
    }

    fn check_switch(&mut self, key: StateKey, depth: usize) -> Result<bool, InitFault> {
        let Some(StateKind::BooleanSwitch(current)) = self
            .state(key)
            .filter(|state| state.active)
            .map(|state| state.kind)
        else {
            return Ok(false);
        };
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("active state node");
        if let Some(value) = self.evaluate(id, node, field(&node.fields, "m_condition"), Some(key))
        {
            let next = truthy(&value);
            if next != current {
                self.exit_subgraph(id, node, branch_path(current), depth + 1)?;
                self.state_mut(key).expect("active switch").kind = StateKind::BooleanSwitch(next);
                self.enter_branch(key, node, next, depth)?;
            }
            return Ok(true);
        }
        Ok(false)
    }

    fn begin_delayed_switch(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let Some(undecided) = self.evaluate(id, node, field(&node.fields, "m_308B8A80"), None)
        else {
            return Ok(());
        };
        let undecided = truthy(&undecided);
        let current = if undecided {
            false
        } else {
            let initial = field(&node.fields, "m_418D9D9D");
            let config = if initial.is_null() {
                field(&node.fields, "m_BD83C8E6")
            } else {
                initial
            };
            let Some(initial) = self.evaluate(id, node, config, Some(key)) else {
                return Ok(());
            };
            truthy(&initial)
        };
        self.state_mut(key).expect("active delayed switch").kind =
            StateKind::DelayedSwitch { current, undecided };
        self.register_tick(key, self.machine.now_ms);
        if !undecided {
            self.enter_branch(key, node, current, depth)?;
        }
        self.recheck_delayed_switch(key, node, depth)
    }

    fn recheck_delayed_switch(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(StateKind::DelayedSwitch { current, undecided }) = self
            .state(key)
            .filter(|state| state.active)
            .map(|state| state.kind)
        else {
            return Ok(());
        };
        let id = key.instance;
        let condition = field(&node.fields, "m_BD83C8E6");
        let false_override = field(&node.fields, "m_E8FC060D");
        let requested = if current {
            let Some(holds_false) =
                self.delayed_false_condition(key, node, condition, false_override)
            else {
                return Ok(());
            };
            holds_false.then_some(false)
        } else {
            let Some(holds_true) = self.evaluate(id, node, condition, Some(key)) else {
                return Ok(());
            };
            if truthy(&holds_true) {
                Some(true)
            } else if undecided {
                let Some(holds_false) =
                    self.delayed_false_condition(key, node, condition, false_override)
                else {
                    return Ok(());
                };
                holds_false.then_some(false)
            } else {
                None
            }
        };
        let Some(branch) = requested else {
            if !undecided {
                self.cancel_delayed_timer(key, !current);
            }
            return Ok(());
        };
        let delay_field = if branch { "m_7130456B" } else { "m_ED60694A" };
        let Some(delay) = self.evaluate(id, node, field(&node.fields, delay_field), None) else {
            return Ok(());
        };
        let seconds = script_expr::to_float(&delay);
        let milliseconds = ((seconds as f32) * 1000.0).trunc();
        if !milliseconds.is_finite() {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("delayed switch timeout"),
            );
            return Ok(());
        }
        if seconds > 0.0 {
            self.arm_delayed_timer(key, branch, milliseconds as u64)?;
        }
        if undecided {
            self.cancel_delayed_timer(key, !branch);
        }
        if seconds <= 0.0 {
            self.set_delayed_branch(key, node, branch, depth)?;
        }
        Ok(())
    }

    fn delayed_false_condition(
        &mut self,
        key: StateKey,
        node: &Node,
        condition: &Json,
        override_config: &Json,
    ) -> Option<bool> {
        let invert = override_config.is_null();
        let config = if invert { condition } else { override_config };
        self.evaluate(key.instance, node, config, Some(key))
            .map(|value| truthy(&value) != invert)
    }

    fn arm_delayed_timer(
        &mut self,
        key: StateKey,
        branch: bool,
        delay_ms: u64,
    ) -> Result<(), InitFault> {
        let index = usize::from(branch);
        if !self
            .state(key)
            .expect("active delayed switch")
            .delayed_timers[index]
        {
            let parameter = if branch {
                SWITCH_TO_TRUE
            } else {
                SWITCH_TO_FALSE
            };
            self.post_after(key.instance, delay_ms, Event::Timer(key.raw, parameter))?;
            self.state_mut(key)
                .expect("active delayed switch")
                .delayed_timers[index] = true;
        }
        Ok(())
    }

    fn cancel_delayed_timer(&mut self, key: StateKey, branch: bool) {
        let pending = &mut self
            .state_mut(key)
            .expect("active delayed switch")
            .delayed_timers[usize::from(branch)];
        if std::mem::take(pending) {
            let parameter = if branch {
                SWITCH_TO_TRUE
            } else {
                SWITCH_TO_FALSE
            };
            self.machine.queues.retain(key.instance, |_, event| {
                !matches!(event, Event::Timer(raw, param) if *raw == key.raw && *param == parameter)
            });
        }
    }

    fn set_delayed_branch(
        &mut self,
        key: StateKey,
        node: &Node,
        next: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        if let StateKind::DelayedSwitch {
            current,
            undecided: false,
        } = self.state(key).expect("active delayed switch").kind
        {
            self.exit_subgraph(key.instance, node, branch_path(current), depth + 1)?;
        }
        self.state_mut(key).expect("active delayed switch").kind = StateKind::DelayedSwitch {
            current: next,
            undecided: false,
        };
        self.enter_branch(key, node, next, depth)
    }

    fn switch_case(&mut self, key: StateKey, node: &Node) -> Option<Option<usize>> {
        let value = self.evaluate(
            key.instance,
            node,
            field(&node.fields, "m_262AE982"),
            Some(key),
        )?;
        for (index, case) in array_field(&node.fields, "m_05706D1C").iter().enumerate() {
            if case.is_object() {
                let candidate = self.evaluate(key.instance, node, field(case, "m_value"), None)?;
                if equal(&value, &candidate) {
                    return Some(Some(index));
                }
            }
        }
        Some(None)
    }

    fn enter_branch(
        &mut self,
        key: StateKey,
        node: &Node,
        branch: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        let output = if branch {
            "m_onTruePlug"
        } else {
            "m_onFalsePlug"
        };
        self.follow(key.instance, node, output, Some(key), depth)?;
        if self.state(key).is_some_and(|state| state.active) {
            self.follow(key.instance, node, branch_path(branch), Some(key), depth)?;
        }
        Ok(())
    }

    fn register_state(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        let entry = registered_state(site.graph, node).expect("validated linked state");
        if entry.suppresses_model && !self.world.set_encounter_model_enabled(site, false) {
            self.report(site, Issue::UnsupportedField("encounter model control"));
            return Ok(());
        }
        if entry.class == RegisteredClass::CinematicAim {
            let accepted = if (site.graph, site.node) == (0x224e, Some(34)) {
                let mut degrees = [0.0; 2];
                for (out, name) in degrees.iter_mut().zip(["m_A53161C8", "m_AB37A892"]) {
                    let Some(value) = self.evaluate_at_with_initial(
                        site,
                        field(&node.fields, name),
                        None,
                        Value::Float(0.0),
                    ) else {
                        return Ok(());
                    };
                    *out = script_expr::to_float(&value);
                }
                self.world.set_captured_aim_lock(site, Some(degrees))
            } else {
                self.world.set_cinematic_aim_lock(site, true)
            };
            if !accepted {
                self.report(site, Issue::UnsupportedField("cinematic aim lock pose"));
                return Ok(());
            }
        }
        self.machine.registered.insert(key, entry);
        self.update_state_links(entry.class)
    }

    fn unregister_state(&mut self, key: StateKey, node: &Node) -> Result<(), InitFault> {
        let Some(entry) = self.machine.registered.shift_remove(&key) else {
            return Ok(());
        };
        if entry.class == RegisteredClass::CinematicAim {
            let site = self.site(key.instance, node);
            if (site.graph, site.node) == (0x224e, Some(34)) {
                self.world.set_captured_aim_lock(site, None);
            } else {
                self.world.set_cinematic_aim_lock(site, false);
            }
        }
        if entry.suppresses_model
            && !self
                .machine
                .registered
                .values()
                .any(|state| state.suppresses_model)
        {
            let site = self.site(key.instance, node);
            if !self.world.set_encounter_model_enabled(site, true) {
                self.report(site, Issue::UnsupportedField("encounter model control"));
            }
        }
        self.state_mut(key).expect("registered state").kind = StateKind::Registered { next: None };
        self.update_state_links(entry.class)
    }

    fn update_state_links(&mut self, class: RegisteredClass) -> Result<(), InitFault> {
        let mut next = None;
        for (&key, entry) in self.machine.registered.iter().rev() {
            if entry.class != class {
                continue;
            }
            self.machine.work_left =
                self.machine
                    .work_left
                    .checked_sub(1)
                    .ok_or(InitFault::WorkBudget {
                        limit: self.machine.max_work,
                        operation: "linked state registry",
                    })?;
            self.machine.snapshot.instances[&key.instance].states[&key.raw].kind =
                StateKind::Registered { next };
            if !entry.server_only {
                next = Some((key.instance, key.raw));
            }
        }
        Ok(())
    }

    fn end(&mut self, key: StateKey, finished: bool, depth: usize) -> Result<(), InitFault> {
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        let Some(state) = self
            .state_mut(key)
            .filter(|state| state.active && !state.ending)
        else {
            return Ok(());
        };
        state.ending = true;
        let kind = state.kind;
        let id = key.instance;
        let node = self
            .graph(id)
            .state(usize::from(key.raw))
            .expect("active state node");
        self.machine
            .queues
            .retain(id, |_, event| event.state() != Some(key.raw));
        self.clear_tick(key);
        self.machine
            .voice_poll
            .retain(|registered| *registered != key);
        self.exit_subgraph(id, node, "m_subgraphPlug", depth + 1)?;
        match kind {
            StateKind::VoiceStimulus => {
                self.world.voice_context(self.site(id, node));
            }
            StateKind::ForCode => {
                self.unregister_for_code(key);
                self.publish_for_code(key, node)?;
            }
            StateKind::Registered { .. } => self.unregister_state(key, node)?,
            StateKind::CinematicTimeScale { .. } => self.stamp_cinematic_time_scale(key, node),
            StateKind::SameTeamContact { registered: true } => {
                self.world
                    .set_same_team_contact_suppressed(self.site(id, node), false);
            }
            StateKind::OutgoingDamageFilter { registered: true } => {
                self.world
                    .set_outgoing_damage_filter(self.site(id, node), None);
            }
            StateKind::AiBehavior {
                record: Some(record),
            } => {
                self.world
                    .ai_behavior_request(self.site(id, node), record, false);
            }
            StateKind::AiControlSetting => {
                self.world
                    .set_ai_control_setting(self.site(id, node), 5.0, false);
            }
            StateKind::AiControlOverride {
                record: Some(record),
            } => {
                self.world
                    .ai_control_override(self.site(id, node), record, false);
            }
            StateKind::AiFacing(Some(record)) => {
                self.world.ai_facing(self.site(id, node), record, false);
            }
            StateKind::AiWeaponOverride {
                record: Some(record),
            } => {
                self.world
                    .ai_weapon_override(self.site(id, node), record, false);
            }
            StateKind::DisplayName {
                binding: Some((entity, name)),
            } => {
                self.world
                    .set_display_name(self.site(id, node), entity, name, false);
            }
            StateKind::Defer => {
                if let Some(pending) = self.machine.deferred.get_mut(&id) {
                    pending.retain(|(_, raw)| *raw != key.raw);
                }
                self.exit_subgraph(id, node, "m_8E484B2E", depth + 1)?;
            }
            StateKind::MovementMod => {
                self.machine
                    .movement_modifiers
                    .retain(|entry, _| entry.instance != key.instance || entry.state != key.raw);
            }
            StateKind::MovementSample(previous) => {
                self.update_movement_sample(key, node, previous)?;
            }
            StateKind::TargetTag {
                effect: Some(effect),
            } => {
                self.world
                    .set_target_tag_effect(self.site(id, node), effect, false);
            }
            StateKind::HealthPool { pool: Some(pool) } => {
                if let Some(current) = self.world.remove_health_pool(self.site(id, node), pool) {
                    self.publish_health_pool(key, node, current)?;
                }
            }
            StateKind::BooleanSwitch(branch) => {
                self.exit_subgraph(id, node, branch_path(branch), depth + 1)?
            }
            StateKind::DelayedSwitch {
                current,
                undecided: false,
            } => self.exit_subgraph(id, node, branch_path(current), depth + 1)?,
            StateKind::Switch { case } => {
                self.exit_subgraph(id, node, &case_path(case), depth + 1)?
            }
            StateKind::PhaseScriptList | StateKind::PhaseScript => {
                for child in self.scoped_children(key) {
                    self.destroy(child)?;
                }
            }
            StateKind::WitchStaffEffect => {
                self.world.set_witch_staff_effect(self.site(id, node), None);
            }
            StateKind::DeadeyeEffects => {
                let children = std::mem::take(
                    &mut self
                        .state_mut(key)
                        .expect("ending Deadeye effect state")
                        .deadeye_children,
                );
                for child in children.into_values() {
                    if self.machine.snapshot.instances.contains_key(&child) {
                        self.destroy(child)?;
                    }
                }
            }
            StateKind::SubScript { child: Some(child) } => self.destroy(child)?,
            StateKind::MessageIdle | StateKind::BossPauseReceiver { .. } => {
                self.exit_subgraph(id, node, "m_33DA5163", depth + 1)?;
                self.exit_subgraph(id, node, "m_452862E6", depth + 1)?;
            }
            StateKind::AiCandidate { selected: true } => {
                self.exit_subgraph(id, node, "m_6F92F4BD", depth + 1)?;
            }
            StateKind::ButtonBlock(Some(button)) => {
                self.state_mut(key).expect("ending button block").kind =
                    StateKind::ButtonBlock(None);
                self.release_button_block(button, depth + 1)?;
            }
            StateKind::LogicalButton { .. } => {
                self.exit_subgraph(id, node, "m_80AF45FB", depth + 1)?;
                self.exit_subgraph(id, node, "m_1C9EF058", depth + 1)?;
            }
            StateKind::Stack { .. } => {
                self.exit_subgraph(id, node, "m_topSubgraphPlug", depth + 1)?;
                self.exit_subgraph(id, node, "m_underSubgraphPlug", depth + 1)?;
                self.unlink_single_output(key, depth + 1)?;
            }
            StateKind::CosmeticEntity => self.unlink_single_output(key, depth + 1)?,
            StateKind::ChaseVar => self.finish_chase(key, depth + 1)?,
            StateKind::WeaponVolley { .. } => self.finish_volley(key, depth + 1)?,
            StateKind::Ability { flags } => {
                self.set_ability_flags(key, flags & !ABILITY_HELD);
                self.stop_ability(key, node, depth + 1)?;
                self.finish_cooldown(key)?;
            }
            _ => (),
        }
        let state = self.state_mut(key).expect("ending state");
        if kind == StateKind::TrackedTargets {
            state.tracked_targets.clear();
        }
        state.active = false;
        state.ending = false;
        state.pending_change = false;
        state.pending_timer = false;
        state.delayed_timers = [false; 2];
        if matches!(kind, StateKind::SubScript { .. }) {
            state.kind = StateKind::SubScript { child: None };
        }
        if matches!(kind, StateKind::HealthPool { .. }) {
            state.kind = StateKind::HealthPool { pool: None };
        }
        if matches!(kind, StateKind::DisplayName { .. }) {
            state.kind = StateKind::DisplayName { binding: None };
        }
        if matches!(kind, StateKind::TargetTag { .. }) {
            state.kind = StateKind::TargetTag { effect: None };
        }
        if matches!(kind, StateKind::AiWeaponOverride { .. }) {
            state.kind = StateKind::AiWeaponOverride { record: None };
        }
        if matches!(kind, StateKind::AiControlOverride { .. }) {
            state.kind = StateKind::AiControlOverride { record: None };
        }
        if matches!(kind, StateKind::AiFacing(_)) {
            state.kind = StateKind::AiFacing(None);
        }
        if matches!(kind, StateKind::SameTeamContact { .. }) {
            state.kind = StateKind::SameTeamContact { registered: false };
        }
        if matches!(kind, StateKind::OutgoingDamageFilter { .. }) {
            state.kind = StateKind::OutgoingDamageFilter { registered: false };
        }
        if matches!(kind, StateKind::AiBehavior { .. }) {
            state.kind = StateKind::AiBehavior { record: None };
        }
        if matches!(kind, StateKind::LogicalButton { .. }) {
            state.kind = StateKind::LogicalButton { down: false };
        }
        if matches!(kind, StateKind::Stack { .. }) {
            state.kind = StateKind::Stack {
                top: false,
                under: false,
            };
        }
        self.machine.watchers.retain(|_, watchers| {
            watchers.retain(|watcher| *watcher != key);
            !watchers.is_empty()
        });
        self.machine.foreign_watchers.retain(|_, watchers| {
            watchers.retain(|watcher| *watcher != key);
            !watchers.is_empty()
        });
        let ending = if finished {
            "m_onFinishedPlug"
        } else {
            "m_onAbortPlug"
        };
        if kind != StateKind::VoiceStimulus {
            self.follow(id, node, ending, Some(key), depth)?;
        }
        self.follow(id, node, "m_onEndPlug", Some(key), depth)?;
        Ok(())
    }

    fn exit_subgraph(
        &mut self,
        id: u16,
        node: &Node,
        path: &str,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(plug) = node.field(path) else {
            return Ok(());
        };
        for raw in raw_indices(array_field(plug, SUBGRAPH_STATES)) {
            self.end(StateKey { instance: id, raw }, false, depth)?;
            self.drop_begin(id, raw);
        }
        Ok(())
    }

    fn drop_begin(&mut self, id: u16, raw: u16) {
        let now = self.machine.now_ms;
        self.machine.queues.retain(id, |due, event| {
            due != now || !matches!(event, Event::Begin(state) if *state == raw)
        });
    }

    fn exit_instance(&mut self, id: u16) -> Result<(), InitFault> {
        let parent = self.machine.snapshot.instances[&id].parent;
        self.destroy(id)?;
        let Some((instance, raw)) = parent else {
            return Ok(());
        };
        let key = StateKey { instance, raw };
        let Some(state) = self.state_mut(key).filter(|state| {
            state.active && !state.ending && state.kind == StateKind::SubScript { child: Some(id) }
        }) else {
            return Ok(());
        };
        state.kind = StateKind::SubScript { child: None };
        let node = self
            .graph(instance)
            .state(usize::from(raw))
            .expect("active subscript");
        if !configured(node, "m_24AB04BB") {
            self.post(instance, Event::Finish(raw))?;
        }
        Ok(())
    }

    fn destroy(&mut self, id: u16) -> Result<(), InitFault> {
        let mut pending = vec![(id, false)];
        while let Some((id, children_done)) = pending.pop() {
            if !children_done {
                self.spend("instance teardown")?;
                let mut effects = Vec::new();
                for state in self.machine.snapshot.instances[&id].states.values_mut() {
                    state.ending = true;
                    effects.extend(std::mem::take(&mut state.deadeye_children).into_values());
                }
                pending.push((id, true));
                pending.extend(
                    effects
                        .into_iter()
                        .filter(|child| self.machine.snapshot.instances.contains_key(child))
                        .map(|child| (child, false)),
                );
                pending.extend(self.machine.snapshot.instances.iter().rev().filter_map(
                    |(&child, instance)| {
                        instance
                            .parent
                            .is_some_and(|(parent, _)| parent == id)
                            .then_some((child, false))
                    },
                ));
                continue;
            }
            let outputs: Vec<_> = self.machine.snapshot.instances[&id]
                .states
                .iter()
                .filter_map(|(&raw, state)| {
                    (state.linked_variable.is_some()
                        || state.ability_stack.is_some()
                        || (state.active
                            && matches!(
                                state.kind,
                                StateKind::ForCode | StateKind::AiControlSetting
                            ))
                        || matches!(
                            state.kind,
                            StateKind::Registered { .. }
                                | StateKind::SameTeamContact { registered: true }
                                | StateKind::OutgoingDamageFilter { registered: true }
                                | StateKind::WeaponVolley { .. }
                                | StateKind::ButtonBlock(Some(_))
                                | StateKind::HealthPool { pool: Some(_) }
                                | StateKind::DisplayName { binding: Some(_) }
                                | StateKind::TargetTag { effect: Some(_) }
                                | StateKind::AiWeaponOverride { record: Some(_) }
                                | StateKind::AiControlOverride { record: Some(_) }
                                | StateKind::AiFacing(Some(_))
                                | StateKind::AiBehavior { record: Some(_) }
                                | StateKind::VoiceStimulus
                        ))
                    .then_some((raw, state.kind))
                })
                .collect();
            for (raw, kind) in outputs {
                let key = StateKey { instance: id, raw };
                if kind == StateKind::ForCode {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("for-code state");
                    self.unregister_for_code(key);
                    self.publish_for_code(key, node)?;
                } else if matches!(kind, StateKind::Registered { .. }) {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("linked state");
                    self.unregister_state(key, node)?;
                } else if kind == StateKind::VoiceStimulus {
                    let node = self.graph(id).state(usize::from(raw)).expect("voice state");
                    if self.state(key).is_some_and(|state| state.active) {
                        self.world.voice_context(self.site(id, node));
                    }
                    self.world.voice_context(self.site(id, node));
                } else if let StateKind::SameTeamContact { registered: true } = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("team contact state");
                    self.world
                        .set_same_team_contact_suppressed(self.site(id, node), false);
                } else if let StateKind::OutgoingDamageFilter { registered: true } = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("outgoing damage filter state");
                    self.world
                        .set_outgoing_damage_filter(self.site(id, node), None);
                } else if let StateKind::AiBehavior {
                    record: Some(record),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI behavior state");
                    self.world
                        .ai_behavior_request(self.site(id, node), record, false);
                } else if kind == StateKind::AiControlSetting {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI control setting state");
                    self.world
                        .set_ai_control_setting(self.site(id, node), 5.0, false);
                } else if let StateKind::AiControlOverride {
                    record: Some(record),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI control state");
                    self.world
                        .ai_control_override(self.site(id, node), record, false);
                } else if let StateKind::AiFacing(Some(record)) = kind {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI facing state");
                    self.world.ai_facing(self.site(id, node), record, false);
                } else if let StateKind::AiWeaponOverride {
                    record: Some(record),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("AI weapon state");
                    self.world
                        .ai_weapon_override(self.site(id, node), record, false);
                } else if let StateKind::DisplayName {
                    binding: Some((entity, name)),
                } = kind
                {
                    let node = self
                        .graph(id)
                        .state(usize::from(raw))
                        .expect("display name");
                    self.world
                        .set_display_name(self.site(id, node), entity, name, false);
                } else if let StateKind::TargetTag {
                    effect: Some(effect),
                } = kind
                {
                    let node = self.graph(id).state(usize::from(raw)).expect("target tag");
                    self.world
                        .set_target_tag_effect(self.site(id, node), effect, false);
                } else if let StateKind::ButtonBlock(Some(button)) = kind {
                    self.state_mut(key).expect("destroyed button block").kind =
                        StateKind::ButtonBlock(None);
                    self.release_button_block(button, 0)?;
                } else if let StateKind::HealthPool { pool: Some(pool) } = kind {
                    let node = self.graph(id).state(usize::from(raw)).expect("health pool");
                    let site = self.site(id, node);
                    self.world.remove_health_pool(site, pool);
                    self.publish_health_totals(site)?;
                } else if matches!(kind, StateKind::WeaponVolley { .. }) {
                    self.finish_volley(key, 0)?;
                } else if kind == StateKind::ChaseVar {
                    self.finish_chase(key, 0)?;
                } else if matches!(kind, StateKind::Ability { .. }) {
                    self.unlink_ability_stack(key, 0)?;
                    self.finish_cooldown(key)?;
                } else {
                    self.unlink_single_output(key, 0)?;
                }
            }
            self.machine.snapshot.instances.shift_remove(&id);
            self.machine
                .disabled_start_entries
                .retain(|(instance, _)| *instance != id);
            self.machine.queues.remove(id);
            self.machine.ticking.remove(&id);
            self.machine.deferred.remove(&id);
            self.machine.voice_poll.retain(|key| key.instance != id);
            self.machine
                .ability_presses
                .retain(|key, _| key.instance != id);
            self.machine.watchers.retain(|key, watchers| {
                if matches!(key, VarKey::Instance(instance, _) if *instance == id) {
                    return false;
                }
                watchers.retain(|watcher| watcher.instance != id);
                !watchers.is_empty()
            });
            self.machine.foreign_watchers.retain(|_, watchers| {
                watchers.retain(|watcher| watcher.instance != id);
                !watchers.is_empty()
            });
        }
        Ok(())
    }
}

fn participant_filter(node: &Node) -> Option<&Json> {
    let filter = field(&node.fields, "m_filter");
    (filter.get("$").and_then(Json::as_str) == Some("STUConfigVarFilter")
        && ["m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"]
            .iter()
            .all(|name| field(&node.fields, name).is_null()))
    .then_some(filter)
}

fn excluded_target_tags(graph: u32, node: &Node) -> Option<(u64, u64)> {
    if (graph, node.pos) != (0x00ca, 33)
        || !field(&node.fields, "m_filter").is_null()
        || !field(&node.fields, "m_B486603E").is_null()
    {
        return None;
    }
    let tag = script_expr::target_tag_config(field(&node.fields, "m_9C7BD8B8"))?;
    let excluded = script_expr::target_tag_config(field(&node.fields, "m_8B2A4DF3"))?;
    Some((tag, excluded))
}

fn static_target_operand(node: &Node) -> Option<&Json> {
    if !field(&node.fields, "m_filter").is_null() || !field(&node.fields, "m_8B2A4DF3").is_null() {
        return None;
    }
    let primary = field(&node.fields, "m_9C7BD8B8");
    let required = field(&node.fields, "m_B486603E");
    match (primary.is_null(), required.is_null()) {
        (false, true) => Some(primary),
        (true, false) => Some(required),
        _ => None,
    }
}

fn is_entry(node: &Node) -> bool {
    matches!(node.class.as_str(), "STUStatescriptEntry" | "STU_571CC72E")
}

fn is_message_entry(node: &Node) -> bool {
    node.class == "STUStatescriptEntryGameMessage"
}

fn registered_state(graph: u32, node: &Node) -> Option<RegisteredState> {
    let placement = (graph, node.pos);
    if !node.is_networked() {
        return None;
    }
    let suppresses_model = matches!(placement, (0x01cb, 20) | (0x0ce0, 17) | (0x0d03, 2));
    let class = match node.class.as_str() {
        "STU_9935FBC8" if placement == (0x224e, 34) && captured_aim_lock_shape(node) => {
            RegisteredClass::CinematicAim
        }
        "STU_9935FBC8"
            if matches!(placement, (0x0ce0, 45) | (0x0ca5, 70))
                && cinematic_aim_lock_shape(node) =>
        {
            RegisteredClass::CinematicAim
        }
        "STU_9028E035" if matches!(placement, (0x0ca5, 46 | 73) | (0x0ce0, 38) | (0x0d03, 1)) => {
            RegisteredClass::State9028E035
        }
        "STU_43994F1B"
            if matches!(placement, (0x0ce0, 29) | (0x0d03, 5))
                && is_self_target(node, "m_4A5CA514")
                && field(&node.fields, "m_contactSet").is_null() =>
        {
            RegisteredClass::State43994F1B
        }
        "STU_93B8758C"
            if matches!(placement, (0x0ce0, 30) | (0x0d03, 4))
                && is_self_target(node, "m_4A5CA514")
                && field(&node.fields, "m_A5B7A147").is_null() =>
        {
            RegisteredClass::State93B8758C
        }
        "STU_33A46033" if suppresses_model || placement == (0x0ca5, 61) => {
            let fields = &node.fields;
            let flag = |name, expected| {
                let config = field(fields, name);
                script_expr::native_shape(config, "STUConfigVarBool", Some("m_value"))
                    && (field(config, "m_value").as_bool() == Some(expected)
                        || field(config, "m_value").as_u64() == Some(u64::from(expected)))
            };
            let model_flag = if suppresses_model {
                field(fields, "m_D82DB14B").is_null()
            } else {
                flag("m_D82DB14B", false)
            };
            if !flag("m_7E42F868", suppresses_model)
                || !model_flag
                || [
                    "m_069FD7A5",
                    "m_72FD7742",
                    "m_381879C1",
                    "m_C3E68133",
                    "m_3C443404",
                    "m_DFD8E2EA",
                    "m_6771D268",
                ]
                .iter()
                .any(|name| !field(fields, name).is_null())
            {
                return None;
            }
            RegisteredClass::EntityControl
        }
        _ => return None,
    };
    Some(RegisteredState {
        class,
        server_only: node.server_only,
        suppresses_model,
    })
}

fn captured_aim_lock_shape(node: &Node) -> bool {
    let fields = &node.fields;
    field(fields, "m_93CDFD2F").as_u64() == Some(1)
        && field(fields, "m_4BE66EFF").is_null()
        && [("m_A53161C8", 501), ("m_AB37A892", 502)]
            .iter()
            .all(|(name, id)| {
                matches!(script_expr::dynamic(field(fields,name)), Ok(Some(variable))
                if variable.scope == Scope::Instance && variable.id == *id)
            })
        && ["m_C33684DE", "m_8FA7FF85", "m_D6119DFB", "m_83221709"]
            .iter()
            .all(|name| {
                let config = field(fields, name);
                script_expr::native_shape(config, "STUConfigVarInt", Some("m_value"))
                    && field(config, "m_value").as_i64() == Some(0)
            })
}

fn cinematic_aim_lock_shape(node: &Node) -> bool {
    let fields = &node.fields;
    let self_angle = |name, class| {
        let config = field(fields, name);
        script_expr::native_shape(config, class, Some("m_entity"))
            && script_expr::native_shape(field(config, "m_entity"), "STU_B5A0CAF0", None)
    };
    field(fields, "m_93CDFD2F").as_u64() == Some(1)
        && field(fields, "m_4BE66EFF").is_null()
        && self_angle("m_A53161C8", "STU_CC53F52B")
        && self_angle("m_AB37A892", "STU_7BE6D76D")
        && ["m_C33684DE", "m_8FA7FF85", "m_D6119DFB", "m_83221709"]
            .iter()
            .all(|name| {
                let config = field(fields, name);
                script_expr::native_shape(config, "STUConfigVarInt", Some("m_value"))
                    && field(config, "m_value").as_i64() == Some(0)
            })
}

fn is_self_target(node: &Node, name: &str) -> bool {
    script_expr::native_shape(field(&node.fields, name), "STU_B5A0CAF0", None)
}

fn persistent_message(graph: u32, node: &Node) -> Option<u64> {
    let message = node
        .field("m_gameMessage.m_gameMessage")
        .and_then(asset_guid)?;
    let fields = &node.fields;
    let target = field(fields, "m_target");
    if (graph, node.pos, message) == (0x0cc7, 39, crate::junkenstein_messages::RESULT_SUMMARY)
        || (graph, node.pos, message) == (0x0cc7, 82, crate::junkenstein_messages::RESULT_SUMMARY)
    {
        return (field(target, "$").as_str() == Some("STU_9B213168")
            && script_expr::native_shape(field(target, "m_targets"), "STU_A3DEF368", None)
            && field(fields, "m_67FD6A60").as_u64() == Some(1)
            && field(fields, "m_priority").is_null())
        .then_some(message);
    }
    if (graph, node.pos, message) == (0x0cbc, 122, 0x0240_0000_0000_0744) {
        // The self-targeted receiver at 0D24:2 is client-only.
        return (field(target, "$").as_str() == Some("STU_9B213168")
            && script_expr::native_shape(field(target, "m_targets"), "STU_B5A0CAF0", None)
            && field(fields, "m_67FD6A60").as_u64() == Some(1)
            && field(fields, "m_params").is_null()
            && field(fields, "m_priority").is_null())
        .then_some(message);
    }
    let notify = match (graph, node.pos, message) {
        (0x0c8c, 17 | 126, 0x0240_0000_0000_0694) | (0x0c8c, 83, 0x0240_0000_0000_06a8)
            if field(fields, "m_params").is_null() =>
        {
            1
        }
        (0x0c8c, 18, crate::junkenstein_messages::POST_MATCH_PHASE) => 1,
        (0x180a, 7, crate::junkenstein_messages::POST_MATCH_SIGNAL)
            if field(fields, "m_params").is_null() =>
        {
            1
        }
        (0x0ce0, 15, BOSS_PAUSE) | (0x0c8c, 76 | 92, BOSS_PAUSE)
            if field(fields, "m_params").is_null() =>
        {
            1
        }
        (0x0ced, 9, 0x0240_0000_0000_0721) if field(fields, "m_params").is_null() => 1,
        (0x0ce0, 21, BOSS_CAMERA) => {
            let [focus, bone] = array_field(fields, "m_params") else {
                return None;
            };
            if parameter_id(focus) != 8
                || !script_expr::native_shape(field(focus, "m_value"), "STU_B5A0CAF0", None)
                || parameter_id(bone) != 0x3b6
                || !matches!(script_expr::dynamic(field(bone, "m_value")), Ok(Some(variable)) if variable.id == 8695)
            {
                return None;
            }
            0
        }
        _ => return None,
    };
    (field(target, "$").as_str() == Some("STU_1D719351")
        && field(target, "m_111E4A0E").as_u64() == Some(0)
        && field(target, "m_1199BFE0").as_u64() == Some(notify)
        && field(target, "m_6A8DB059").as_u64() == Some(1)
        && field(fields, "m_67FD6A60").as_u64() == Some(notify)
        && field(fields, "m_priority").is_null())
    .then_some(message)
}

fn living_body_array_shape(node: &Node) -> bool {
    let filter = field(&node.fields, "m_filter");
    filter.get("$").and_then(Json::as_str) == Some("STUConfigVarFilter")
        && filter.get("m_84065FD1").and_then(Json::as_u64) == Some(0x3fff_ffff)
        && filter.get("m_4EA686DB").and_then(Json::as_u64) == Some(0x10)
        && filter.as_object().is_some_and(|fields| {
            fields.iter().all(|(name, value)| {
                matches!(name.as_str(), "$" | "m_84065FD1" | "m_4EA686DB")
                    || value.is_null()
                    || value.as_u64() == Some(0)
            })
        })
        && !configured(node, "m_17A88AEF")
        && !configured(node, "m_C485751C")
        && is_unlinked_plug(&node.fields, "m_A70DB860", "STUStatescriptOutputPlug")
        && matches!(
            script_expr::dynamic(field(&node.fields, "m_F2805C84")),
            Ok(Some(target)) if target.id != 0
        )
}

/// Whether the field is a plug of the given class with an empty link list.
fn is_unlinked_plug(fields: &Json, name: &str, class: &str) -> bool {
    field(fields, name).as_object().is_some_and(|plug| {
        plug.len() == 2
            && plug.get("$").and_then(Json::as_str) == Some(class)
            && plug
                .get("links")
                .and_then(Json::as_array)
                .is_some_and(Vec::is_empty)
    })
}

/// The authored build 104319 shape of an action without a payload: the node
/// header with no remote sync variables and two unlinked plugs. Every
/// `STU_E9694EFD` node in the target build has exactly this shape.
fn is_bare_header_action(node: &Node) -> bool {
    const FIELDS: [&str; 8] = [
        "m_2BBEEAB8",
        "m_8BF03679",
        "m_A2287776",
        "m_ADEB6E05",
        "m_AED90719",
        "m_BF5B22B7",
        "m_inPlug",
        "m_outPlug",
    ];
    let header = |name, value| field(&node.fields, name).as_u64() == Some(value);
    node.state.is_none()
        && node.remote < 0
        && !node.server_only
        && node.fields.as_object().is_some_and(|fields| {
            fields.len() == FIELDS.len() && FIELDS.iter().all(|name| fields.contains_key(*name))
        })
        && header("m_2BBEEAB8", 0)
        && header("m_ADEB6E05", 1)
        && header("m_A2287776", 0)
        && header("m_AED90719", 0)
        && field(&node.fields, "m_8BF03679").is_null()
        && field(&node.fields, "m_BF5B22B7").is_null()
        && is_unlinked_plug(&node.fields, "m_inPlug", "STUStatescriptInputPlug")
        && is_unlinked_plug(&node.fields, "m_outPlug", "STUStatescriptOutputPlug")
}

// These authored placements resolve to the PC base State.
fn is_encounter_base_state(graph: u32, node: &Node) -> bool {
    !node.server_only
        && matches!(
            (graph, node.pos, node.class.as_str()),
            (0x0033, 30, "STU_87621906")
                | (0x006b, 38, "STU_0DB9E66B")
                | (0x0079, 42, "STU_0DB9E66B")
                | (0x01c9, 65, "STU_0DB9E66B")
                | (0x224e, 25, "STU_0DB9E66B")
                | (0x0aaa, 2, "STU_200B27C5")
                | (0x0aaa, 3, "STU_AF1A4047")
                | (0x0aaa, 4, "STU_55F72184")
                | (0x0aaa, 5, "STU_E834E718")
                | (0x0aaa, 10, "STU_632B74B9")
                | (0x0aaa, 17 | 24, "STU_D3CDCF04")
                | (0x0aaa, 28, "STU_D52B9026")
                | (0x0aaa, 30, "STU_724DFBC6")
                | (0x0aaa, 33, "STU_93088159")
                | (0x0bc6, 6, "STU_632B74B9")
                | (0x0bc6, 7, "STU_E834E718")
                | (0x0bc6, 8, "STU_55F72184")
                | (0x0bc6, 9, "STU_200B27C5")
                | (0x0bc6, 12, "STU_AF1A4047")
                | (0x0bc6, 14 | 25, "STU_E0D2F3AA")
                | (0x0bc6, 27, "STU_D52B9026")
                | (0x0bc6, 28, "STU_724DFBC6")
                | (0x0cb5, 2, "STU_200B27C5")
                | (0x0cb5, 3, "STU_E834E718")
                | (0x0cb5, 10, "STU_55F72184")
                | (0x0cb5, 12, "STU_D52B9026")
                | (0x0cb5, 15, "STU_B17E88C9")
                | (0x0cb5, 16, "STU_632B74B9")
                | (0x0cb5, 19 | 31, "STU_724DFBC6")
                | (0x0cb5, 20, "STU_4531992F")
                | (0x0cba, 28, "STU_93088159")
                | (0x0cd2, 17 | 27 | 29 | 51, "STU_724DFBC6")
                | (0x0cd2, 18 | 53, "STU_632B74B9")
                | (0x0cd2, 19 | 26, "STU_B17E88C9")
                | (0x0cd2, 21, "STU_D52B9026")
                | (0x0cd2, 22 | 31, "STU_55F72184")
                | (0x0cd2, 23 | 30, "STU_E834E718")
                | (0x0cd2, 24 | 52, "STU_200B27C5")
                | (0x0cd2, 37, "STU_AF1A4047")
                | (0x0cd2, 38, "STU_E0D2F3AA")
                | (0x0cd2, 44 | 49, "STU_D3CDCF04")
                | (0x185d, 2 | 41 | 49, "STU_200B27C5")
                | (0x185d, 3 | 40, "STU_E834E718")
                | (0x185d, 8 | 39, "STU_55F72184")
                | (0x185d, 15 | 38, "STU_D52B9026")
                | (0x185d, 22 | 36, "STU_B17E88C9")
                | (0x185d, 23 | 35, "STU_632B74B9")
                | (0x185d, 24 | 34, "STU_724DFBC6")
                | (0x185d, 31, "STU_E0D2F3AA")
                | (0x185d, 46, "STU_AF1A4047")
                | (0x185d, 47, "STU_F4BBA185")
                | (0x185d, 53, "STU_93088159")
                | (0x0ce0, 6 | 40, "STU_93088159")
                | (0x0ce0, 42 | 43, "STU_14611C1B")
                | (0x0d03, 8 | 10, "STU_93088159")
                | (0x0d03, 15 | 16, "STU_14611C1B")
        )
}

fn is_client_subgraph_controller(graph: &Graph, node: &Node) -> bool {
    // The controller is replicated, but its specialized branch starts on the client.
    const BRANCH: &str = "m_5B10D01F";
    let plug = field(&node.fields, BRANCH);
    node.is_networked()
        && field(plug, "$").as_str() == Some("STU_904BFCEC")
        && array_field(plug, "links").len() == 1
        && node
            .links(BRANCH)
            .next()
            .and_then(|(position, _)| graph.node(position?))
            .is_some_and(|target| target.client_only && !target.server_only)
}

fn is_owner_movement_sampler(node: &Node) -> bool {
    node.is_networked()
        && node.remote == -1
        && [
            "m_entity",
            "m_A10CC74B",
            "m_0B1AA8CA",
            "m_4F8F2F3F",
            "m_8BF03679",
            "m_BF5B22B7",
        ]
        .iter()
        .all(|name| field(&node.fields, name).is_null())
        && ["m_E37BC6C2", "m_2102C541"]
            .iter()
            .all(|path| node.links(path).next().is_none())
        && MOVEMENT_BOOLEAN_OUTPUTS
            .iter()
            .all(|name| script_expr::lvalue(field(&node.fields, name)).is_ok())
}

fn is_networked_for_code_synced(node: &Node) -> bool {
    node.is_networked()
        && node.remote >= 0
        && field(&node.fields, "m_DC897C87").as_u64() == Some(1)
        && field(&node.fields, "m_598E32D5").as_u64() == Some(0)
        && field(&node.fields, "m_0582B0E4").as_u64() == Some(0)
        && field(&node.fields, "m_0B1AA8CA").is_null()
        && field(&node.fields, "m_values")
            .as_array()
            .is_some_and(|values| {
                !values.is_empty()
                    && values.iter().all(|value| {
                        field(value, "$").as_str() == Some("STUStatescriptForCodeValue")
                            && field(field(value, "m_value"), "$").as_str().is_some()
                    })
            })
}

fn is_base_health_pool(node: &Node) -> bool {
    let config = |name| field(&node.fields, name);
    let is_owner = |value| script_expr::native_shape(value, "STU_B5A0CAF0", None);
    let flag = |name, expected| {
        let value = config(name);
        script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
            && match field(value, "m_value") {
                Json::Bool(flag) => *flag == expected,
                Json::Number(flag) => flag.as_u64() == Some(u64::from(expected)),
                _ => false,
            }
    };
    let source = config("m_69A20070");
    node.is_networked()
        && [
            "m_7BE02F89",
            "m_C99FAFDD",
            "m_FF79E783",
            "m_AF1104D0",
            "m_BFA825EA",
        ]
        .into_iter()
        .all(|name| config(name).is_null())
        && pool_kind(config("m_7E8B666C")).is_some()
        && is_owner(config("m_FA3ECC43"))
        && script_expr::native_shape(source, "STU_FEDD683C", Some("m_entity"))
        && is_owner(field(source, "m_entity"))
        && flag("m_4895B8B6", true)
        && flag("m_64839023", false)
        && flag("m_0E8A5C8E", false)
        && (config("m_CEAB19E8").is_null()
            || matches!(script_expr::lvalue(config("m_CEAB19E8")), Ok(Some(_))))
}

fn is_wave_health_pool(node: &Node) -> bool {
    let config = |name| field(&node.fields, name);
    let amount = config("m_amount");
    let source = field(amount, "m_814F8EC3");
    let pool_source = config("m_69A20070");
    matches!(node.pos, 1 | 4 | 5 | 26 | 43 | 113)
        && node.is_networked()
        && pool_kind(config("m_7E8B666C")) == Some(PoolKind::Health)
        && script_expr::native_shape(pool_source, "STU_FEDD683C", Some("m_entity"))
        && script_expr::native_shape(field(pool_source, "m_entity"), "STU_B5A0CAF0", None)
        && ["m_64839023", "m_0E8A5C8E"].into_iter().all(|name| {
            let value = config(name);
            script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
                && (matches!(field(value, "m_value"), Json::Bool(false))
                    || field(value, "m_value").as_u64() == Some(0))
        })
        && [
            "m_7BE02F89",
            "m_BCFDD2E9",
            "m_4895B8B6",
            "m_CEAB19E8",
            "m_C99FAFDD",
            "m_FF79E783",
            "m_AF1104D0",
            "m_BFA825EA",
        ]
        .into_iter()
        .all(|name| config(name).is_null())
        && field(amount, "$").as_str() == Some("STU_4BD39CFD")
        && script_expr::native_fields(amount, &["m_814F8EC3", "m_index"])
        && script_expr::dynamic(field(amount, "m_index"))
            == Ok(Some(Variable {
                scope: Scope::Instance,
                id: 9948,
            }))
        && script_expr::native_shape(source, "STU_06610EBC", Some("m_configVars"))
        && array_field(source, "m_configVars").len() == 4
        && array_field(source, "m_configVars").iter().all(|value| {
            script_expr::native_shape(value, "STUConfigVarInt", Some("m_value"))
                && field(value, "m_value").as_u64().is_some()
        })
}

fn pool_kind(value: &Json) -> Option<PoolKind> {
    match value.as_u64()? {
        0 => Some(PoolKind::Health),
        1 => Some(PoolKind::Armour),
        2 => Some(PoolKind::Shields),
        _ => None,
    }
}

fn pool_number(value: Value) -> Option<f64> {
    match value {
        Value::Float(amount) => Some(amount),
        Value::Integer(amount) => Some(amount as f64),
        _ => None,
    }
}

fn ability_is_stacked(node: &Node) -> bool {
    !field(&node.fields, "m_out_StackVar").is_null() && !field(&node.fields, "m_5E561E77").is_null()
}

fn configured(node: &Node, name: &str) -> bool {
    match field(&node.fields, name) {
        Json::Null | Json::Bool(false) => false,
        Json::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Json::String(value) => !value.is_empty(),
        Json::Array(value) => !value.is_empty(),
        Json::Object(value) => !value.is_empty(),
        Json::Bool(true) => true,
    }
}

fn parameter_id(config: &Json) -> u32 {
    config
        .get("m_B5051BCE")
        .and_then(asset_guid)
        .map_or(0, |guid| u32::from(guid as u16))
}

fn field<'a>(fields: &'a Json, name: &str) -> &'a Json {
    fields.get(name).unwrap_or(&Json::Null)
}

fn array_field<'a>(fields: &'a Json, name: &str) -> &'a [Json] {
    field(fields, name).as_array().map_or(&[], Vec::as_slice)
}

fn raw_indices(values: &[Json]) -> impl Iterator<Item = u16> + '_ {
    values
        .iter()
        .filter_map(|value| u16::try_from(value.as_u64()?).ok())
}

fn case_path(case: Option<usize>) -> String {
    case.map_or_else(
        || "m_A5EF385D".into(),
        |index| format!("m_05706D1C[{index}]"),
    )
}

fn branch_path(branch: bool) -> &'static str {
    if branch {
        "m_trueSubgraphPlug"
    } else {
        "m_falseSubgraphPlug"
    }
}

fn array_insertion_index(mut value: &Value) -> i32 {
    while let Value::Array(items) = value {
        let Some(first) = items.first() else {
            return -1;
        };
        value = first;
    }
    match value {
        Value::Null => 0,
        Value::Bool(value) => i32::from(*value),
        Value::Integer(value) => *value as i32,
        Value::Float(value) => {
            let value = *value as f32;
            let rounded = (if value < 0.0 {
                value - 0.5
            } else {
                value + 0.5
            })
            .trunc();
            if rounded.is_finite() && (-2147483648.0..2147483648.0).contains(&f64::from(rounded)) {
                rounded as i32
            } else {
                i32::MIN
            }
        }
        _ => -1,
    }
}

fn same(a: &Value, b: &Value) -> bool {
    a == b || matches!((a, b), (Value::Float(a), Value::Float(b)) if a.is_nan() && b.is_nan())
}

fn source_key(instance: u16, variable: Variable) -> VarKey {
    match variable.scope {
        Scope::Instance => VarKey::Instance(instance, variable.id),
        Scope::Entity => VarKey::Entity(variable.id),
    }
}

fn stored(snapshot: &Snapshot, key: VarKey) -> Value {
    match key {
        VarKey::Entity(id) => snapshot.variables.get(&id),
        VarKey::Instance(instance, id) => snapshot
            .instances
            .get(&instance)
            .and_then(|instance| instance.variables.get(&id)),
    }
    .cloned()
    .unwrap_or(Value::Null)
}

fn shot_delay(ordinal: u32, rate: f64) -> u64 {
    ((f64::from(ordinal) / rate) as f32 * 1000.0).round() as u64
}

fn chase_value(value: &Value) -> Value {
    match value {
        Value::Vector(_) => value.clone(),
        _ => Value::Float(script_expr::to_float(value)),
    }
}

fn chase_vector(value: Value) -> [f64; 3] {
    match value {
        Value::Vector(vector) => vector,
        _ => [script_expr::to_float(&value), 0.0, 0.0],
    }
}

fn chase_lerp(current: Value, destination: Value, share: f64) -> Value {
    let interpolate = |from: f64, to: f64| f64::from((from + (to - from) * share) as f32);
    if let (Value::Float(current), Value::Float(destination)) = (&current, &destination) {
        return Value::Float(interpolate(*current, *destination));
    }
    let current = chase_vector(current);
    let destination = chase_vector(destination);
    Value::Vector(std::array::from_fn(|axis| {
        interpolate(current[axis], destination[axis])
    }))
}

fn chase_step(current: Value, destination: Value, step: f64) -> (Value, bool) {
    if let (Value::Float(current), Value::Float(destination)) = (&current, &destination) {
        let delta = f64::from((*destination - *current) as f32);
        return if delta.abs() <= step {
            (Value::Float(*destination), true)
        } else {
            let next = *current + if delta > 0.0 { step } else { -step };
            (Value::Float(f64::from(next as f32)), false)
        };
    }
    let current = chase_vector(current);
    let destination = chase_vector(destination);
    let delta: [f64; 3] =
        std::array::from_fn(|axis| f64::from((destination[axis] - current[axis]) as f32));
    let length = f64::from((delta.iter().map(|value| value * value).sum::<f64>().sqrt()) as f32);
    if length <= step {
        (Value::Vector(destination), true)
    } else {
        let next = std::array::from_fn(|axis| {
            f64::from((current[axis] + delta[axis] * step / length) as f32)
        });
        (Value::Vector(next), false)
    }
}

enum VoiceFault {
    Service(&'static str),
    Reported,
    Init(InitFault),
}

struct VoiceHost<'a, 'g, 'w, 'm, W> {
    vm: &'a mut Vm<'g, 'w, 'm, W>,
    key: StateKey,
    node: &'a Node,
}

impl<W: World> VoiceHost<'_, '_, '_, '_, W> {
    fn check(&mut self) -> Result<Site, VoiceFault> {
        self.vm
            .spend("voice service callback")
            .map_err(VoiceFault::Init)?;
        if !self
            .vm
            .state(self.key)
            .is_some_and(|state| state.active && !state.ending)
        {
            return Err(VoiceFault::Service("voice stimulus owner retired"));
        }
        Ok(self.vm.site(self.key.instance, self.node))
    }
}

impl<W: World> crate::script_voice::Host for VoiceHost<'_, '_, '_, '_, W> {
    type Error = VoiceFault;
    fn context(&mut self) -> Result<bool, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_context(site)
            .ok_or(VoiceFault::Service("voice stimulus context"))
    }
    fn manager(&mut self) -> Result<bool, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_manager(site)
            .ok_or(VoiceFault::Service("voice manager lookup"))
    }
    fn clock(&mut self) -> Result<f64, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_clock(site, self.vm.machine.now_ms)
            .ok_or(VoiceFault::Service("voice global clock"))
    }
    fn scalar(&mut self, field_name: &'static str, initial: Value) -> Result<Value, VoiceFault> {
        let site = self.check()?;
        self.vm
            .evaluate_selected_at(
                site,
                field(&self.node.fields, field_name),
                Some(self.key),
                initial,
                true,
            )
            .ok_or(VoiceFault::Reported)
    }
    fn load(&mut self, guid: u64) -> Result<Option<crate::script_voice::Stimulus>, VoiceFault> {
        let site = self.check()?;
        let stimulus = self
            .vm
            .world
            .voice_stimulus(site, guid)
            .ok_or(VoiceFault::Service("voice stimulus resource"))?;
        if stimulus.is_some_and(|stimulus| stimulus.guid != guid) {
            return Err(VoiceFault::Service("voice stimulus resource"));
        }
        Ok(stimulus)
    }
    fn allow(&mut self, stimulus: crate::script_voice::Stimulus) -> Result<bool, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_allow(site, stimulus)
            .ok_or(VoiceFault::Service("voice stimulus permission"))
    }
    fn targets(&mut self) -> Result<Vec<u32>, VoiceFault> {
        self.check()?;
        let values = self
            .vm
            .assignment_group(
                self.key.instance,
                self.node,
                field(&self.node.fields, "m_targets"),
            )
            .map_err(VoiceFault::Init)?
            .ok_or(VoiceFault::Reported)?;
        let mut targets = Vec::new();
        let Value::Array(values) = values else {
            return Err(VoiceFault::Service("voice target iterator"));
        };
        for value in values {
            self.vm
                .spend("voice target candidate")
                .map_err(VoiceFault::Init)?;
            if let Value::Entity(entity) = value {
                if script_expr::valid_entity(entity) && !targets.contains(&entity) {
                    targets.push(entity);
                }
            }
        }
        Ok(targets)
    }
    fn submit(
        &mut self,
        stimulus: crate::script_voice::Stimulus,
        targets: &[u32],
    ) -> Result<u32, VoiceFault> {
        let site = self.check()?;
        let actor = if self.vm.world.voice_actor_required(site, stimulus) {
            Some(
                self.vm
                    .evaluate(
                        self.key.instance,
                        self.node,
                        field(&self.node.fields, "m_8C8C5285"),
                        None,
                    )
                    .ok_or(VoiceFault::Reported)?,
            )
        } else {
            None
        };
        self.vm
            .world
            .voice_submit(site, stimulus, targets, actor.as_ref())
            .map_err(VoiceFault::Service)?
            .ok_or(VoiceFault::Service("voice stimulus submission"))
    }
    fn duration(&mut self, handle: u32, guid: u64, mode: bool) -> Result<f64, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_duration(site, handle, guid, mode)
            .ok_or(VoiceFault::Service("voice duration status"))
    }
    fn part(&mut self, number: u16) -> Result<f64, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_part(site, number)
            .ok_or(VoiceFault::Service("voice next part"))
    }
}

impl<W: World> Vm<'_, '_, '_, W> {
    fn fail_voice_states(&mut self) {
        let keys = self
            .machine
            .snapshot
            .instances
            .iter()
            .flat_map(|(&instance, state)| {
                state.states.iter().filter_map(move |(&raw, state)| {
                    (state.kind == StateKind::VoiceStimulus).then_some(StateKey { instance, raw })
                })
            })
            .collect::<Vec<_>>();
        for key in keys {
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("voice state");
            self.world.voice_context(self.site(key.instance, node));
            self.machine
                .queues
                .retain(key.instance, |_, event| event.state() != Some(key.raw));
            if let Some(state) = self.state_mut(key) {
                state.active = false;
                state.ending = false;
            }
        }
        self.machine.voice_poll.clear();
    }

    fn voice_fault(&mut self, key: StateKey, error: VoiceFault) -> Result<(), InitFault> {
        self.fail_voice_states();
        match error {
            VoiceFault::Init(error) => return Err(error),
            VoiceFault::Reported => (),
            VoiceFault::Service(service) => {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("voice state");
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField(service),
                );
            }
        }
        Ok(())
    }

    fn begin_voice(&mut self, key: StateKey, node: &Node) -> Result<bool, InitFault> {
        let result = crate::script_voice::begin(&mut VoiceHost {
            vm: self,
            key,
            node,
        });
        match result {
            Ok(Some(voice)) => {
                self.state_mut(key).expect("active voice state").voice = Some(Box::new(voice));
                self.machine.voice_poll.push(key);
            }
            Ok(None) => (),
            Err(error) => {
                self.voice_fault(key, error)?;
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn poll_voice(&mut self) -> Result<(), InitFault> {
        let registry = self.machine.voice_poll.clone();
        for &key in &registry {
            self.spend("voice stimulus poll")?;
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("registered voice state");
            let Some(mut voice) = self
                .state(key)
                .filter(|state| state.active && !state.ending)
                .and_then(|state| state.voice.as_deref())
                .copied()
            else {
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField("voice stimulus uninitialized poll"),
                );
                continue;
            };
            let result = crate::script_voice::poll(
                &mut voice,
                &mut VoiceHost {
                    vm: self,
                    key,
                    node,
                },
            );
            if let Some(state) = self.state_mut(key) {
                *state.voice.as_deref_mut().expect("registered voice cache") = voice;
            }
            match result {
                Ok(actions) => {
                    for action in actions {
                        match action {
                            crate::script_voice::Action::Timer(parameter) => self.post_after(
                                key.instance,
                                0,
                                Event::Timer(key.raw, u16::from(parameter)),
                            )?,
                            crate::script_voice::Action::Finish => {
                                self.post(key.instance, Event::Finish(key.raw))?
                            }
                        }
                    }
                }
                Err(error) => {
                    self.voice_fault(key, error)?;
                    return Ok(());
                }
            }
            if registry != self.machine.voice_poll {
                self.voice_fault(key, VoiceFault::Service("voice poll registry mutation"))?;
                break;
            }
        }
        Ok(())
    }

    fn voice_timer(&mut self, key: StateKey, node: &Node, parameter: u16) -> Result<(), InitFault> {
        use crate::script_voice::Host;
        match (VoiceHost {
            vm: self,
            key,
            node,
        })
        .context()
        {
            Ok(false) => return Ok(()),
            Ok(true) => (),
            Err(error) => return self.voice_fault(key, error),
        }
        if parameter == 1 {
            let output = field(&node.fields, "m_FC2F7AAA");
            if !output.is_null() {
                let Ok(Some(output)) = script_expr::dynamic(output) else {
                    return self.voice_fault(key, VoiceFault::Service("voice duration output"));
                };
                if output.id != 0 {
                    let duration = self
                        .state(key)
                        .and_then(|state| state.voice.as_deref())
                        .map_or(Value::Null, |voice| Value::Float(voice.duration));
                    self.write(source_key(key.instance, output), duration)?;
                }
            }
        }
        if matches!(parameter, 1 | 2) {
            self.follow(key.instance, node, "m_onFinishedPlug", Some(key), 0)?;
        }
        Ok(())
    }

    fn notify_voice_failure(&mut self, key: StateKey) -> Result<bool, InitFault> {
        if !self.machine.voice_poll.contains(&key)
            || !self
                .state(key)
                .is_some_and(|state| state.active && !state.ending)
        {
            return Ok(false);
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("registered voice state");
        self.follow(key.instance, node, "m_F96FC964", Some(key), 0)?;
        Ok(true)
    }
}

struct InstanceContext<'a, W> {
    machine: &'a Machine,
    world: &'a mut W,
    site: Site,
    reads: Vec<VarKey>,
    foreign_reads: Vec<(u32, u32)>,
    parameters: Option<&'a Variables>,
    watcher: Option<StateKey>,
    read_native: bool,
    untrusted_native: bool,
}

/// The reference's TRUSTED_NATIVES (driver.py): config classes whose server
/// answer is the owner client's own for a human player's body. A polled
/// switch that reads any other native is kept off the owner stream.
const CLIENT_AGREED_NATIVES: [&str; 9] = [
    "STU_1FF67CA6",
    "STU_310D6529",
    "STU_37F20D24",
    "STU_4A101787",
    "STU_A77AC2AC",
    "STU_A3DEF368",
    "STU_DB742151",
    "STU_295D117D",
    "STU_D3EB8D87",
];

impl<W: World> InstanceContext<'_, W> {
    fn read(&mut self, key: VarKey) -> Value {
        if !self.reads.contains(&key) {
            self.reads.push(key);
        }
        let below = self.watcher.filter(|watcher| {
            self.machine
                .snapshot
                .instances
                .get(&watcher.instance)
                .and_then(|instance| instance.states.get(&watcher.raw))
                .is_some_and(|state| {
                    matches!(state.kind, StateKind::Stack { .. })
                        && state.linked_variable == Some(key)
                })
        });
        self.machine.value(key, below)
    }
}

impl<W: World> Context for InstanceContext<'_, W> {
    fn variable(&mut self, variable: Variable) -> Value {
        self.read(source_key(self.site.instance, variable))
    }

    fn parent_variable(&mut self, levels: u8, variable: Variable) -> Value {
        let mut id = self.site.instance;
        for _ in 0..levels {
            let Some((parent, _)) = self.machine.snapshot.instances[&id].parent else {
                return Value::Null;
            };
            id = parent;
        }
        self.read(source_key(id, variable))
    }

    fn parent_entity(&mut self, _levels: u8) -> Value {
        self.owner()
    }

    fn parameter(&mut self, id: u32) -> Value {
        self.parameters
            .and_then(|parameters| parameters.get(&id))
            .cloned()
            .unwrap_or(Value::Null)
    }

    fn owner(&mut self) -> Value {
        Value::Entity(self.site.entity)
    }

    fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity == self.site.entity {
            Some(self.read(VarKey::Entity(id)))
        } else {
            self.read_native = true;
            if !self.foreign_reads.contains(&(entity, id)) {
                self.foreign_reads.push((entity, id));
            }
            self.world.entity_variable(entity, id)
        }
    }

    fn ruleset(&mut self, id: u32) -> Value {
        match self.read(VarKey::Entity(id)) {
            Value::Null => self.world.ruleset(id).unwrap_or(Value::Integer(0)),
            value => value,
        }
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.world.position(entity)
    }
    fn entity_velocity(&mut self, entity: u32) -> script_expr::VelocityQuery {
        self.read_native = true;
        self.world.entity_velocity(entity)
    }
    fn body_angle(&mut self, entity: u32, axis: script_expr::BodyAngle) -> script_expr::AngleQuery {
        self.read_native = true;
        self.world.body_angle(entity, axis)
    }

    fn entity_exists(&mut self, entity: u32) -> Option<bool> {
        self.read_native = true;
        self.world.entity_exists(entity)
    }

    fn known_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.read_native = true;
        self.world.known_position(entity)
    }

    fn forward(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.world.forward(entity)
    }

    fn combat_targets(&mut self, config: &Json) -> Option<Vec<u32>> {
        self.read_native = true;
        self.world.combat_targets(self.site, config)
    }
    fn static_target(&mut self, tag: u64) -> Option<u32> {
        self.world.static_target(tag)
    }

    fn random_u64(&mut self) -> Option<u64> {
        Some(self.machine.random_u64())
    }

    fn body_scale(&mut self, entity: u32) -> Option<f64> {
        if entity != self.site.entity {
            return None;
        }
        self.read_native = true;
        let scale_override = self.read(VarKey::Entity(31_210));
        self.world.body_scale(self.site, entity, &scale_override)
    }

    fn body_health(&mut self, entity: u32, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
        self.read_native = true;
        self.world.entity_health(self.site, entity, kind, maximum)
    }

    fn total_health(&mut self, entity: u32) -> script_expr::HealthQuery {
        self.read_native = true;
        self.world.total_health(self.site, entity)
    }
    fn body_display_name(&mut self, entity: u32) -> Option<String> {
        self.read_native = true;
        self.world.body_display_name(self.site, entity)
    }

    fn entity_team(&mut self, entity: u32) -> Option<i8> {
        self.read_native = true;
        self.world.entity_team(self.site, entity)
    }

    fn entity_hero(&mut self, entity: u32) -> Option<u64> {
        self.read_native = true;
        self.world.entity_hero(self.site, entity)
    }
    fn entity_definition(&mut self, entity: u32) -> Option<u64> {
        self.read_native = true;
        self.world.entity_definition(self.site, entity)
    }

    fn engine_setting(&mut self, key: u32) -> script_expr::EngineSetting {
        self.read_native = true;
        self.world.engine_setting(self.site, key)
    }

    fn native(&mut self, config: &Json) -> Option<Value> {
        self.read_native = true;
        self.world.native(self.site, config)
    }

    fn native_uses_initial(&mut self, config: &Json) -> bool {
        self.read_native = true;
        self.world.native_uses_initial(self.site, config)
    }

    fn watches_natives(&self) -> bool {
        self.watcher.is_some()
    }

    fn reference_native(&mut self, class: &str) {
        if !CLIENT_AGREED_NATIVES.contains(&class) {
            self.untrusted_native = true;
        }
    }

    fn has_target_tag(&mut self, entity: u32, tag: u64) -> Option<bool> {
        self.read_native = true;
        self.world.has_target_tag(self.site, entity, tag)
    }

    fn scene_filter_policy(&mut self, filter: &Json) -> Option<script_expr::SceneFilterPolicy> {
        self.read_native = true;
        self.world.scene_filter_policy(self.site, filter)
    }
    fn entity_filter_flags(&mut self, entity: u32) -> Option<u64> {
        self.read_native = true;
        self.world.entity_filter_flags(self.site, entity)
    }
    fn entities_hostile(&mut self, source: u32, target: u32, option: bool) -> Option<bool> {
        self.read_native = true;
        self.world
            .entities_hostile(self.site, source, target, option)
    }

    fn body_on_fire(&mut self, entity: u32) -> Option<OnFire> {
        self.read_native = true;
        self.world.body_on_fire(self.site, entity)
    }

    fn team_members(&mut self, team: u8) -> Option<u32> {
        self.read_native = true;
        self.world.team_members(team)
    }

    fn entity_component92(&mut self, entity: u32) -> Option<bool> {
        self.read_native = true;
        self.world.entity_component92(self.site, entity)
    }

    fn related_entity(&mut self, relation: EntityRelation, entity: u32) -> Option<u32> {
        self.read_native = true;
        self.world.related_entity(self.site, relation, entity)
    }

    fn linked_entity(&mut self, entity: u32, slot: u32) -> Option<u32> {
        self.read_native = true;
        self.world.linked_entity(self.site, entity, slot)
    }

    fn would_stack_to_top(&mut self, variable: Variable, priority: StackPriority) -> Option<bool> {
        let key = source_key(self.site.instance, variable);
        self.read(key);
        Some(self.machine.allows_stack(key, priority, None))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_voice_lifecycle_contract() {
        use crate::script_voice::Stimulus;
        fn float(json: &Json) -> Option<f64> {
            json.as_f64().or_else(|| match json["float"].as_str()? {
                "NaN" => Some(f64::NAN),
                "Infinity" => Some(f64::INFINITY),
                "-Infinity" => Some(f64::NEG_INFINITY),
                _ => None,
            })
        }
        fn bits(value: f64) -> Json {
            json!({"bits64":format!("{:016x}",value.to_bits())})
        }
        fn value(json: &Json) -> Value {
            match json {
                Json::Null => Value::Null,
                Json::Bool(v) => Value::Bool(*v),
                Json::Number(n) => n
                    .as_i64()
                    .map_or_else(|| Value::Float(n.as_f64().unwrap()), Value::Integer),
                Json::Array(items) => Value::Array(items.iter().map(value).collect()),
                Json::Object(fields) if fields.contains_key("asset") => Value::Asset(
                    u64::from_str_radix(
                        json["asset"].as_str().unwrap().trim_start_matches("0x"),
                        16,
                    )
                    .unwrap(),
                ),
                Json::Object(_) => {
                    Value::Entity(json["entity"].as_u64().unwrap().try_into().unwrap())
                }
                _ => panic!("invalid voice fixture value"),
            }
        }
        struct Host {
            case: Json,
            events: Vec<Json>,
            clock: Option<f64>,
            indices: BTreeMap<String, usize>,
        }
        impl Host {
            fn selected(&mut self, key: &str, sequence: Json) -> Json {
                let sequence = sequence.as_array().unwrap();
                let index = self.indices.entry(key.into()).or_default();
                let result = sequence[(*index).min(sequence.len() - 1)].clone();
                *index += 1;
                result
            }
            fn availability(&self, key: &str) -> Option<bool> {
                match self.case[key].as_str().unwrap_or("present") {
                    "present" => Some(true),
                    "absent" => Some(false),
                    "unavailable" => None,
                    _ => panic!("invalid host availability"),
                }
            }
        }
        impl World for Host {
            fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _: u32) -> Option<[f64; 3]> {
                None
            }
            fn voice_context(&mut self, _: Site) -> Option<bool> {
                self.events.push(json!(["context"]));
                self.availability("context")
            }
            fn voice_manager(&mut self, _: Site) -> Option<bool> {
                self.events.push(json!(["manager"]));
                self.availability("manager")
            }
            fn voice_clock(&mut self, _: Site, _: u64) -> Option<f64> {
                self.events.push(json!(["clock"]));
                self.clock
            }
            fn voice_stimulus(&mut self, _: Site, guid: u64) -> Option<Option<Stimulus>> {
                self.events.push(json!(["load", format!("0x{guid:016X}")]));
                let sequence = self
                    .case
                    .get("resource")
                    .cloned()
                    .unwrap_or(json!(["present"]));
                match self.selected("resource", sequence).as_str().unwrap() {
                    "present" => Some(Some(Stimulus {
                        guid,
                        playable: self.case["playable"].as_bool().unwrap_or(true),
                        mode: self.case["mode"].as_bool().unwrap_or(false),
                    })),
                    "absent" => Some(None),
                    "unavailable" => None,
                    _ => panic!("invalid resource availability"),
                }
            }
            fn voice_allow(&mut self, _: Site, stimulus: Stimulus) -> Option<bool> {
                self.events.push(json!(["allow", stimulus.mode]));
                Some(self.case["allow"].as_bool().unwrap_or(true))
            }
            fn voice_submit(
                &mut self,
                _: Site,
                _: Stimulus,
                targets: &[u32],
                _: Option<&Value>,
            ) -> Result<Option<u32>, &'static str> {
                self.events.push(json!(["submit", targets]));
                Ok(Some(
                    self.case
                        .get("handle")
                        .map_or(17, |v| v.as_u64().unwrap().try_into().unwrap()),
                ))
            }
            fn voice_duration(&mut self, _: Site, handle: u32, _: u64, mode: bool) -> Option<f64> {
                self.events.push(json!(["duration", handle, mode]));
                let sequence = self.case.get("duration").cloned().unwrap_or(json!([0.25]));
                float(&self.selected("duration", sequence))
            }
            fn voice_part(&mut self, _: Site, number: u16) -> Option<f64> {
                self.events.push(json!(["part", number]));
                let sequence = self.case["parts"]
                    .get(number.to_string())
                    .cloned()
                    .unwrap_or(json!([0.0]));
                float(&self.selected(&format!("part-{number}"), sequence))
            }
        }
        fn observation(component: &Component, host: &mut Host, outcome: Json) -> Json {
            let instance = component.machine.snapshot.instances.get(&1);
            let state = instance.and_then(|instance| instance.states.get(&0));
            let voice=state.and_then(|state|state.voice.as_deref()).map(|voice|json!({
                "handle":voice.handle,"attempts":voice.attempts,"duration":bits(voice.duration),"part":voice.part,
                "started_at":bits(voice.started_at),"delay":bits(voice.delay),"cooldown":bits(voice.cooldown),
                "retry_at":bits(voice.retry_at),"submitted":voice.submitted,"failed":voice.failed,
            }));
            let values = instance.map(|instance| {
                [79, 80, 81, 82, 83]
                    .into_iter()
                    .map(|key| {
                        let value = match instance.variables.get(&key) {
                            Some(Value::Integer(v)) => json!(v),
                            Some(Value::Float(v)) => bits(*v),
                            None | Some(Value::Null) => Json::Null,
                            other => panic!("unexpected voice output {other:?}"),
                        };
                        (key.to_string(), value)
                    })
                    .collect::<serde_json::Map<_, _>>()
            });
            json!({"events":std::mem::take(&mut host.events),"exists":instance.is_some(),"active":state.map(|state|state.active),
                "registered":component.machine.voice_poll.len(),"voice":voice,"values":values,"outcome":outcome})
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-voice-lifecycle-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.voice-lifecycle.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let mut graph = contract["input"]["graph"].clone();
            if let Some(fields) = case["fields"].as_object() {
                graph["nodes"][0]["fields"]
                    .as_object_mut()
                    .unwrap()
                    .extend(fields.clone());
            }
            let index = graph["index"].as_u64().unwrap().try_into().unwrap();
            let graphs = Arc::new(store(vec![graph]));
            let owner = contract["input"]["owner"]
                .as_u64()
                .unwrap()
                .try_into()
                .unwrap();
            let mut root = Root::listener(1, index, Some(0));
            for key in [80, 81, 82, 83] {
                root.variables.insert(key, Value::Integer(0));
            }
            root.variables.insert(
                1,
                value(case.get("targets").unwrap_or(&json!([{"entity":owner}]))),
            );
            if let Some(variables) = case["variables"].as_object() {
                for (key, selected) in variables {
                    root.variables.insert(key.parse().unwrap(), value(selected));
                }
            }
            let mut host = Host {
                case: case.clone(),
                events: Vec::new(),
                clock: case.get("clock").map_or(Some(100.0), float),
                indices: BTreeMap::new(),
            };
            let mut component = None;
            let mut root = Some(root);
            let mut actual = Vec::new();
            for step in case["steps"].as_array().unwrap() {
                if let Some(clock) = step.get("clock") {
                    host.clock = float(clock);
                }
                let mut outcome = Json::Null;
                let key = StateKey {
                    instance: 1,
                    raw: 0,
                };
                let op = step["op"].as_str().unwrap();
                if op == "begin" && component.is_none() {
                    component = Some(
                        Component::start(
                            Arc::clone(&graphs),
                            owner,
                            Variables::new(),
                            vec![root.take().unwrap()],
                            0,
                            &mut host,
                            MAX_INIT_WORK,
                        )
                        .unwrap(),
                    );
                } else {
                    let component = component.as_mut().unwrap();
                    match op {
                        "begin" => component.run(&mut host, |vm| vm.begin(key, 0)).unwrap(),
                        "advance" => {
                            let frame = step["frame"].as_u64().unwrap().try_into().unwrap();
                            component.advance_frame(frame, &mut host).unwrap();
                        }
                        "drain" => component
                            .run(&mut host, |vm| {
                                vm.update()?;
                                vm.notify_code(1)
                            })
                            .unwrap(),
                        "poll" => component.poll_voice(&mut host).unwrap(),
                        "failure" => {
                            outcome =
                                json!(component.notify_voice_failure(1, 0, &mut host).unwrap())
                        }
                        "abort" | "finish" => component
                            .run(&mut host, |vm| vm.end(key, op == "finish", 0))
                            .unwrap(),
                        "destroy" => component.run(&mut host, |vm| vm.destroy(1)).unwrap(),
                        "set_output" => component
                            .run(&mut host, |vm| {
                                vm.write(
                                    VarKey::Instance(1, 79),
                                    Value::Float(step["value"].as_f64().unwrap()),
                                )
                            })
                            .unwrap(),
                        "timer" => component
                            .run(&mut host, |vm| {
                                vm.timer(
                                    key,
                                    step["parameter"].as_u64().unwrap().try_into().unwrap(),
                                )
                            })
                            .unwrap(),
                        _ => panic!("unknown voice operation {op}"),
                    }
                }
                let component = component.as_ref().unwrap();
                if let Some(diagnostic) = component.machine.snapshot.diagnostics.last() {
                    let (feature, class) = match &diagnostic.issue {
                        Issue::UnsupportedField(feature) => (*feature, "STU_40E05F6B"),
                        Issue::Expression(EvalError::UnsupportedConfig(class)) => {
                            ("world query", class.as_str())
                        }
                        _ => panic!(
                            "{}: unexpected voice diagnostic {diagnostic:?}",
                            case["name"]
                        ),
                    };
                    actual.push(json!({"error":feature,"class":class,"events":std::mem::take(&mut host.events)}));
                    break;
                }
                actual.push(observation(component, &mut host, outcome));
            }
            let expected = expected["steps"].as_array().unwrap();
            assert_eq!(actual.len(), expected.len(), "{}", case["name"]);
            for (index, (actual, expected)) in actual.iter().zip(expected).enumerate() {
                assert_eq!(actual, expected, "{} step {index}", case["name"]);
            }
        }
    }
    use std::path::PathBuf;

    use serde_json::json;

    fn node(position: u16, class: &str, state: Option<u16>, mut fields: Json) -> Json {
        fields["m_2BBEEAB8"] = json!(state.is_some());
        fields["m_ADEB6E05"] = json!(
            class.contains("Action")
                || matches!(
                    class,
                    "STU_12F5D52D"
                        | "STU_22926252"
                        | "STU_DA54F8F7"
                        | "STU_4C3509FB"
                        | "STU_CBE8BDAA"
                )
        );
        json!({
            "pos":position, "class":class, "uid":position, "rid":position, "remote":-1,
            "state":state, "client_only":false, "server_only":false, "fields":fields,
        })
    }

    fn graph(index: u32, nodes: Vec<Json>, states: Json, entries: Json) -> Json {
        json!({
            "index":index, "items":nodes.len(), "nodes":nodes, "states":states,
            "entries":entries, "remote":[], "sync_vars":[], "containers":{},
            "unknown_nodes":[], "fields":{},
            "header":{
                "m_nodesBitCount":4, "m_statesBitCount":4, "m_remoteSyncNodesBitCount":4,
                "m_syncVarsBitCount":4
            }
        })
    }

    fn store(graphs: Vec<Json>) -> GraphStore {
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version":1, "graphs":graphs, "bodies":{}, "missing":[]
            }))
            .unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn shared_nested_destination_contract() {
        use crate::script_destination::{Step, assign};
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/nested-destination-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["case_id"], "statescript.nested-destination.v1");
        for case in fixture["storage_cases"].as_array().unwrap() {
            let mut root = match case["initial_type"].as_u64().unwrap() {
                255 => Value::Null,
                2 => Value::Integer(19),
                3 => Value::Float(f64::from(f32::from_bits(19))),
                _ => unreachable!(),
            };
            let index = case["index"].as_u64().unwrap() as u16;
            let result = assign(
                &mut root,
                &[Step::Index(index), Step::Field(27606)],
                Value::Integer(123),
                &mut 0,
            )
            .unwrap();
            assert_eq!(result, case["result"].as_bool().unwrap());
            let Value::Array(items) = root else {
                panic!("array coercion")
            };
            assert_eq!(items.len(), case["array_count"].as_u64().unwrap() as usize);
            for (position, item) in items.iter().enumerate() {
                if position == usize::from(index) {
                    let Value::IdentifierMap(map) = item else {
                        panic!("field coercion")
                    };
                    assert_eq!(map.capacity(), case["map_width"].as_u64().unwrap() as usize);
                    assert_eq!(
                        map.pairs(),
                        vec![(Value::Integer(27606), Value::Integer(123))]
                    );
                } else {
                    assert_eq!(item, &Value::Integer(0));
                }
            }
        }
        for sequence in fixture["map_sequences"].as_array().unwrap() {
            let mut root = Value::Null;
            for step in sequence["steps"].as_array().unwrap() {
                let key = step["key"].as_u64().unwrap() as u16;
                let value = step["value"].as_i64().unwrap();
                assert!(
                    assign(
                        &mut root,
                        &[Step::Field(key)],
                        Value::Integer(value),
                        &mut 0
                    )
                    .unwrap()
                );
                let Value::IdentifierMap(map) = &root else {
                    unreachable!()
                };
                assert_eq!(map.capacity(), step["capacity"].as_u64().unwrap() as usize);
                let expected: Vec<_> = step["entries"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|pair| {
                        (
                            Value::Integer(pair[0].as_i64().unwrap()),
                            Value::Integer(pair[1].as_i64().unwrap()),
                        )
                    })
                    .collect();
                assert_eq!(map.pairs(), expected, "native bucket order");
                let mut native = crate::bits::BitWriter::new();
                let mut logical = crate::bits::BitWriter::new();
                crate::script_wire::write_value(
                    &mut native,
                    &root,
                    crate::script_wire::ValueEncoding::Compact,
                )
                .unwrap();
                crate::script_wire::write_value(
                    &mut logical,
                    &Value::Map(expected),
                    crate::script_wire::ValueEncoding::Compact,
                )
                .unwrap();
                assert_eq!(native, logical, "identifier-map wire representation");
            }
        }
        for case in fixture["path_cases"].as_array().unwrap() {
            let entity = case["scope"] == 1;
            let index = case["index"].as_f64().unwrap();
            let output = json!({"$":"STU_919FD47C","m_expression":{
                "m_opcodes":[8,0,8,1,17,7,2,15,0], "m_D99EF254":[],
                "m_dynamicVars":[var(31528,entity),var(39,false),var(27606,false)]}});
            let mut declared = output.clone();
            declared["m_expression"]["m_opcodes"] = fixture["path_opcodes"].clone();
            declared["m_expression"]["m_dynamicVars"] =
                json!([var(31528, entity), var(27606, false)]);
            declared["m_expression"]["m_D99EF254"] = json!([index]);
            let parsed = script_expr::destination(&declared).unwrap().unwrap();
            assert_eq!(
                parsed.variable.id,
                case["variable"].as_u64().unwrap() as u32
            );
            assert_eq!(
                parsed.variable.scope,
                if entity {
                    script_expr::Scope::Entity
                } else {
                    script_expr::Scope::Instance
                }
            );
            let mut steps = Vec::new();
            for part in parsed.path {
                steps.push(match part {
                    script_expr::DestinationPart::Index(config) => {
                        let value =
                            script_expr::evaluate(&config, &mut crate::script_expr::EmptyContext)
                                .unwrap();
                        json!([script_expr::to_int(&value, 0) as u16, 0])
                    }
                    script_expr::DestinationPart::Field(field) => json!([0, field]),
                });
            }
            assert_eq!(json!(steps), case["path"]);
            // The actual VM path uses a live local index instead of the
            // constant index used by the restricted native path oracle.
            let graphs = Arc::new(store(vec![graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                    ),
                    node(
                        1,
                        "STU_12F5D52D",
                        None,
                        json!({"m_143D4C5B":[
                    {"m_out_Var":var(39,false),"m_value":{"$":"STUConfigVarFloat","m_value":index}},
                    {"m_out_Var":output,"m_value":{"$":"STUConfigVarInt","m_value":123}}
                ],"m_outPlug":plug(&[(2,"m_inPlug")], &[])}),
                    ),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(900,true),"m_value":output}),
                    ),
                ],
                json!([]),
                json!([0]),
            )]));
            let component = Component::start(
                graphs,
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            let index = case["path"][0][0].as_u64().unwrap();
            assert_eq!(
                snapshot.variables[&900],
                if index < 1000 {
                    Value::Integer(123)
                } else {
                    Value::Null
                }
            );
        }
    }

    #[test]
    fn nested_destination_writes_notify_existing_readers() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/nested-destination-v1.json"
        ))
        .unwrap();
        for entity in [false, true] {
            let output = json!({"$":"STU_919FD47C","m_expression":{
                "m_opcodes":[8,0,8,1,17,7,2,15,0],"m_D99EF254":[],
                "m_dynamicVars":[var(31528,entity),var(39,false),var(27606,false)]}});
            let graphs = Arc::new(store(vec![graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                    node(
                        1,
                        "STUStatescriptStateBooleanSwitch",
                        Some(0),
                        json!({"m_condition":output}),
                    ),
                    node(
                        2,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"$":"STUConfigVarGameMessage","m_gameMessage":"0x024000000000000F"},
                    "m_outPlug":plug(&[(3,"m_inPlug")],&[])}),
                    ),
                    node(
                        3,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":output,"m_value":var(900,true)}),
                    ),
                ],
                json!([1]),
                json!([0, 2]),
            )]));
            let mut component = Component::start(
                graphs,
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert_eq!(
                component.snapshot().unwrap().instances[&1].states[&0].kind,
                StateKind::BooleanSwitch(false)
            );
            for value in fixture["host_watcher_values"].as_array().unwrap() {
                let value = value.as_i64().unwrap();
                component
                    .set_entity_variable(900, Value::Integer(value), &mut EmptyWorld)
                    .unwrap();
                assert_eq!(
                    component
                        .queue_entity_message(0x0240_0000_0000_000f, Variables::new())
                        .unwrap(),
                    1
                );
                component.run_now(&mut EmptyWorld).unwrap();
                let snapshot = component.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                assert_eq!(
                    snapshot.instances[&1].states[&0].kind,
                    StateKind::BooleanSwitch(value != 0)
                );
            }
        }
    }

    #[test]
    fn nested_destination_keeps_unavailable_storage_explicit() {
        let output = json!({"$":"STU_919FD47C","m_expression":{
            "m_opcodes":[8,0,6,0,17,7,1,15,0],"m_D99EF254":[0],
            "m_dynamicVars":[var(31528,false),var(27606,false)]}});
        let graphs = Arc::new(store(vec![graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateStack",
                    Some(0),
                    json!({
                "m_out_Var":var(31528,false),"m_value":{"$":"STUConfigVarInt","m_value":19},
                "m_priority":{"$":"STUConfigVarFloat","m_value":0},
                "m_onBeginPlug":plug(&[(2,"m_inPlug")],&[])}),
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":output,"m_value":{"$":"STUConfigVarInt","m_value":123}}),
                ),
            ],
            json!([1]),
            json!([0]),
        )]));
        let component = Component::start(
            graphs,
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot
                .diagnostics
                .iter()
                .any(|diagnostic| diagnostic.issue
                    == Issue::UnsupportedField("nested destination linked storage"))
        );
        assert_eq!(
            component.resolved_variable(
                1,
                script_expr::Variable {
                    scope: script_expr::Scope::Instance,
                    id: 31528
                }
            ),
            Some(Value::Integer(19))
        );
        let mut legacy = Value::Map(vec![(Value::Integer(1), Value::Integer(9))]);
        let before = legacy.clone();
        assert!(
            crate::script_destination::assign(
                &mut legacy,
                &[crate::script_destination::Step::Field(2)],
                Value::Integer(123),
                &mut 0
            )
            .is_err()
        );
        assert_eq!(legacy, before);
    }

    #[test]
    fn target_tag_snapshot_preserves_unknown_base_and_effect_lifetimes() {
        let tag = 0x0d60_0000_0000_0001;
        let mut effects = TargetTagEffects::default();
        let grant = TargetTagEffect {
            entity: 123,
            tag,
            present: true,
        };
        let suppress = TargetTagEffect {
            present: false,
            ..grant
        };
        let base = std::collections::BTreeSet::from([tag]);
        assert_eq!(effects.snapshot(None).presence(tag), None);
        assert_eq!(effects.snapshot(Some(&base)).presence(tag), Some(true));
        assert!(effects.apply(grant, true));
        let granted = effects.snapshot(None);
        assert!(effects.apply(suppress, true));
        assert_eq!(effects.snapshot(Some(&base)).presence(tag), Some(false));
        assert_eq!(granted.presence(tag), Some(true));
        assert!(!effects.apply(grant, true));
        assert!(effects.apply(suppress, false));
        assert_eq!(effects.snapshot(None).presence(tag), Some(true));
        assert!(effects.apply(grant, false));
        assert_eq!(effects.snapshot(None).presence(tag), None);
        assert_eq!(effects.snapshot(Some(&base)).presence(tag + 1), Some(false));
    }

    #[test]
    fn movement_modifier_values_follow_lifecycle_and_dependencies_without_read_side_effects() {
        for per_frame in [false, true] {
            let graphs = Arc::new(store(vec![graph(
                1,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                    node(
                        1,
                        "STU_316CFEF2",
                        Some(0),
                        json!({
                        "m_485109CB":per_frame,
                        "m_BC5E91CF":[
                            {"$":"STU_D8CC6B10","m_A9561CA0":0,"m_9B7A63EA":var(77,true)},
                            {"$":"STU_D8CC6B10","m_A9561CA0":1,"m_9B7A63EA":
                                {"$":"STU_BE0BD267","m_min":0.8,"m_max":1.2}}
                        ]}),
                    ),
                    node(
                        2,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"m_gameMessage":"0x0240000000000002"},
                    "m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                    ),
                    node(
                        3,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                    "m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                ],
                json!([1]),
                json!([0, 2, 3]),
            )]));
            let mut component = Component::start(
                graphs,
                42,
                Variables::from([(77, Value::Float(2.0))]),
                vec![Root::new(1, 1)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let read = |component: &mut Component| {
                let values = component
                    .movement_modifiers(&mut EmptyWorld)
                    .unwrap()
                    .unwrap();
                assert_eq!(values.len(), 2);
                let row = |slot| {
                    values[&ModifierKey {
                        instance: 1,
                        state: 0,
                        slot,
                    }]
                        .value
                };
                (row(0), row(1))
            };
            let initial = read(&mut component);
            assert_eq!(initial.0, 2.0);
            assert_eq!(read(&mut component), initial);
            component.advance_frame(1, &mut EmptyWorld).unwrap();
            let next = read(&mut component);
            assert_eq!(next.0, 2.0);
            assert_eq!(next.1 == initial.1, !per_frame);
            component
                .set_entity_variable(77, Value::Float(3.0), &mut EmptyWorld)
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            let changed = read(&mut component);
            assert_eq!(changed.0, 3.0);
            assert_ne!(changed.1, next.1);
            assert_eq!(read(&mut component), changed);
            component
                .queue_message(1, 0x0240_0000_0000_0002, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            assert!(
                component
                    .movement_modifiers(&mut EmptyWorld)
                    .unwrap()
                    .unwrap()
                    .is_empty()
            );
            component
                .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            let restarted = read(&mut component);
            assert_eq!(restarted.0, 3.0);
            assert_ne!(restarted.1, changed.1);
        }
    }

    #[test]
    fn authored_encounter_cleanup_damages_only_a_living_owner_once() {
        #[derive(Default)]
        struct Damage(Vec<f64>);
        impl World for Damage {
            fn self_damage(&mut self, site: Site, amount: f64) -> bool {
                assert_eq!((site.entity, site.graph, site.node), (42, 0x0cbc, Some(69)));
                self.0.push(amount);
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let owner = json!({"$":"STU_B5A0CAF0"});
        let mut authored = graph(
            0x0cbc,
            vec![node(
                69,
                "STUStatescriptStateModifyHealth",
                Some(0),
                json!({
                    "m_FA3ECC43":owner,
                    "m_8B707686":1,
                    "m_modifyHealth":{
                        "m_724CBA57":owner,
                        "m_93CDFD2F":1,
                        "m_amount":{"$":"STUConfigVarInt","m_value":10000}
                    }
                }),
            )],
            json!([69]),
            json!([]),
        );
        authored["items"] = json!(70);
        authored["header"]["m_nodesBitCount"] = json!(7);
        let graphs = Arc::new(store(vec![authored]));
        for already_dead in [false, true] {
            let mut world = Damage::default();
            let mut component = Component::start(
                Arc::clone(&graphs),
                42,
                Variables::new(),
                vec![Root::listener(1, 0x0cbc, None)],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            component.set_owner_dead(already_dead);
            let key = StateKey {
                instance: 1,
                raw: 0,
            };
            component.run(&mut world, |vm| vm.begin(key, 0)).unwrap();
            for frame in 1..=4 {
                component.advance_frame(frame, &mut world).unwrap();
            }
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(world.0, if already_dead { vec![] } else { vec![10_000.0] });
        }
    }

    #[test]
    fn shared_foreign_notification_contract() {
        struct Host {
            target: u32,
            value: bool,
        }
        impl World for Host {
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
                (entity == self.target && id == 6604).then_some(Value::Bool(self.value))
            }
        }
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-foreign-notification-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["case_id"], "statescript.foreign-notification.v1");
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["target_build"], 104319);
        let cases = fixture["input"]["cases"].as_array().unwrap();
        let results = fixture["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), results.len());
        for (case, expected) in cases.iter().zip(results) {
            assert_eq!(case["name"], expected["name"]);
            let owner = u32::try_from(fixture["input"]["owner"].as_u64().unwrap()).unwrap();
            let target = u32::try_from(fixture["input"]["target"].as_u64().unwrap()).unwrap();
            let mut world = Host {
                target,
                value: case["initial"].as_bool().unwrap(),
            };
            let mut root = Root::new(1, 0x0c8c);
            root.variables.insert(1, Value::Entity(target));
            let mut component = Component::start(
                Arc::new(store(
                    fixture["input"]["graphs"].as_array().unwrap().clone(),
                )),
                owner,
                Variables::new(),
                vec![root],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = |component: &Component| {
                let view = component.snapshot().unwrap();
                json!({"active":view.instances[&1].states[&0].active,
                    "complete":view.variables.get(&9)==Some(&Value::Bool(true)),
                    "children":view.instances.values().filter(|item|item.graph==0x7000).count(),
                    "subscriptions":component.machine.foreign_watchers.get(&(target,6604)).map_or(0,Vec::len)})
            };
            let mut actual = vec![snapshot(&component)];
            for step in case["steps"].as_array().unwrap() {
                let frame = u32::try_from(step["frame"].as_u64().unwrap()).unwrap();
                match step["op"].as_str().unwrap() {
                    "set" => {
                        let value = step["value"].as_bool().unwrap();
                        if value != world.value {
                            world.value = value;
                            component
                                .queue_foreign_variable_change(frame, target, 6604)
                                .unwrap();
                            // Duplicate room notifications coalesce at the state.
                            component
                                .queue_foreign_variable_change(frame, target, 6604)
                                .unwrap();
                        }
                    }
                    "abort" => component
                        .machine
                        .queues
                        .push(1, frame_start(frame), Event::Abort(0))
                        .unwrap(),
                    "poll" => (),
                    other => panic!("unknown step {other}"),
                }
                component.advance_frame(frame, &mut world).unwrap();
                actual.push(snapshot(&component));
            }
            assert_eq!(json!(actual), expected["output"], "{}", case["name"]);
        }
    }

    #[test]
    fn final_wave_join_counts_distinct_edges_and_releases_all_four_states() {
        let mut nodes = vec![node(
            0,
            "STUStatescriptEntry",
            None,
            json!({"m_outPlug":plug(&[(2,"m_beginPlug"),(3,"m_beginPlug"),(4,"m_beginPlug")], &[])}),
        )];
        nodes.push(node(1, "STUStatescriptEntry", None, json!({})));
        for raw in 0..4 {
            let mut links = vec![(7, "m_inPlug")];
            if raw == 0 {
                links.push((7, "m_inPlug"));
            }
            nodes.push(node(
                raw + 2,
                "STUStatescriptState",
                Some(raw),
                json!({"m_transitionPlug":[plug(&links, &[])]}),
            ));
        }
        nodes.push(node(
            6,
            "STUStatescriptActionSetVar",
            None,
            json!({"m_out_Var":var(8493,true),"m_value":{"$":"STUConfigVarBool","m_value":true}}),
        ));
        nodes.push(node(
            7,
            "STU_1113EBD5",
            None,
            json!({"m_122DF0CA":[0,1,2,3],"m_B24D1794":4,"m_outPlug":plug(&[(6,"m_inPlug")], &[])}),
        ));
        let graphs = Arc::new(store(vec![graph(
            0x0cc0,
            nodes,
            json!([2, 3, 4, 5]),
            json!([0]),
        )]));
        let mut component = Component::start(
            graphs,
            1,
            Variables::from([(8493, Value::Bool(false))]),
            vec![Root::new(1, 0x0cc0)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snap = component.snapshot().unwrap();
        assert_eq!(snap.variables[&8493], Value::Bool(false));
        assert_eq!(snap.instances[&1].join_inputs[&7].len(), 3);
        assert!(snap.instances[&1].states.values().all(|state| state.active));

        component
            .machine
            .queues
            .push(1, frame_start(1), Event::CheckTransitions(0))
            .unwrap();
        component.advance_frame(1, &mut EmptyWorld).unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&8493],
            Value::Bool(false)
        );
        component
            .machine
            .queues
            .push(1, frame_start(2), Event::Begin(3))
            .unwrap();
        component.advance_frame(2, &mut EmptyWorld).unwrap();
        let snap = component.snapshot().unwrap();
        assert!(snap.initialization_complete(), "{:?}", snap.diagnostics);
        assert_eq!(snap.variables[&8493], Value::Bool(true));
        assert!(snap.instances[&1].join_inputs.is_empty());
        assert!(
            snap.instances[&1]
                .states
                .values()
                .all(|state| !state.active)
        );
    }

    #[test]
    fn final_wave_completion_rechecks_the_active_mode_subscript() {
        let mut mode = graph(
            0x0c8c,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(33,"m_beginPlug")], &[])}),
                ),
                node(
                    1,
                    "STU_3387AB5D",
                    None,
                    json!({
                        "m_condition":var(8493,true), "m_truePlug":plug(&[(2,"m_inPlug")], &[])
                    }),
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(8494,true), "m_value":{"$":"STUConfigVarBool","m_value":true}
                    }),
                ),
                node(
                    33,
                    "STU_37D754C8",
                    Some(0),
                    json!({
                        "m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{"m_graph":"0x0580000000000CC0"}}},
                        "m_transitionPlug":[plug(&[(1,"m_inPlug")], &[])]
                    }),
                ),
            ],
            json!([33]),
            json!([0]),
        );
        mode["items"] = json!(34);
        let graphs = Arc::new(store(vec![
            mode,
            graph(0x0cc0, vec![], json!([]), json!([])),
        ]));
        let mut component = Component::start(
            graphs,
            1,
            Variables::from([(8493, Value::Bool(false)), (8494, Value::Bool(false))]),
            vec![Root::new(1, 0x0c8c)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().instances[&1].states[&0].active);
        component
            .machine
            .queues
            .push(
                1,
                frame_start(1),
                Event::Assign(VarKey::Entity(8493), Value::Bool(true)),
            )
            .unwrap();
        component.advance_frame(1, &mut EmptyWorld).unwrap();
        let snap = component.snapshot().unwrap();
        assert!(snap.initialization_complete(), "{:?}", snap.diagnostics);
        assert_eq!(snap.variables[&8494], Value::Bool(true));
        assert!(!snap.instances[&1].states[&0].active);
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein mode graphs"]
    fn authored_victory_voice_releases_the_client_outcome_action() {
        #[derive(Default)]
        struct VictoryWorld {
            actors: Vec<Value>,
            actions: Vec<Site>,
        }

        impl World for VictoryWorld {
            fn play_victory_voice(&mut self, site: Site, actor: &Value) -> Option<u64> {
                assert_eq!(site.node, Some(221));
                self.actors.push(actor.clone());
                Some(13_138)
            }

            fn queue_mode_action(&mut self, site: Site) -> bool {
                self.actions.push(site);
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let raw = graphs
            .graph(0x0c8c)
            .unwrap()
            .node(221)
            .unwrap()
            .state
            .unwrap();
        let actor = Value::Entity(0xc000_1010);
        let mut root = Root::listener(2, 0x0c8c, Some(raw));
        root.variables.insert(8504, Value::Float(4.5));
        let mut world = VictoryWorld::default();
        let mut component = Component::start(
            graphs,
            crate::world::GAME_MODE_ENTITY,
            Variables::from([(8602, actor.clone())]),
            vec![root],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(world.actions.is_empty());
        assert_eq!(world.actors, [actor]);
        for frame in 1..=300 {
            component.advance_frame(frame, &mut world).unwrap();
        }
        assert_eq!(world.actions.len(), 1);
        assert_eq!(world.actions[0].node, Some(265));
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
    }

    #[test]
    fn shared_victory_view_route_contract() {
        use crate::junkenstein_messages::DefenderMessage;
        #[derive(Default)]
        struct RouteWorld(Vec<DefenderMessage>);
        impl World for RouteWorld {
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn route_game_message(&mut self, site: Site, message: u64, params: &Variables) -> bool {
                let Some(request) = DefenderMessage::victory_view(site, message, params) else {
                    return false;
                };
                self.0.push(request);
                true
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-victory-view-route-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.victory-view-route.v1");
        assert_eq!(contract["target_build"], 104319);
        for case in contract["input"]["cases"].as_array().unwrap() {
            let position = u16::try_from(case["position"].as_u64().unwrap()).unwrap();
            let mut action = node(
                position,
                case["class"].as_str().unwrap(),
                None,
                case["fields"].clone(),
            );
            action["fields"]["m_ADEB6E05"] = json!(true);
            action["fields"]["m_outPlug"] = json!({"$":"Plug", "links":[[5,"m_inPlug"]]});
            let mut model = graph(
                0x0cec,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":{"$":"Plug","links":[[position,"m_inPlug"]]}}),
                    ),
                    action,
                    node(
                        5,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({
                            "m_out_Var":{"$":"STUConfigVarDynamic","m_60DB8F99":1,"m_identifier":9},
                            "m_value":{"$":"STUConfigVarBool","m_value":true}
                        }),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            model["items"] = json!(position.max(5) + 1);
            let graphs = Arc::new(store(vec![model]));
            let mut root = Root::new(1, 0x0cec);
            root.variables.insert(
                8602,
                Value::Entity(
                    u32::try_from(contract["input"]["subject"].as_u64().unwrap()).unwrap(),
                ),
            );
            let mut world = RouteWorld::default();
            let component = Component::start(
                graphs,
                crate::world::GAME_MODE_ENTITY,
                Variables::new(),
                vec![root],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables.get(&9),
                Some(&Value::Bool(
                    case["expected"]["forwarded"].as_bool().unwrap()
                ))
            );
            if case["expected"]["parameters"].is_null() {
                assert!(world.0.is_empty());
                continue;
            }
            assert_eq!(world.0.len(), 1);
            let request = &world.0[0];
            assert_eq!(request.source, crate::world::GAME_MODE_ENTITY);
            assert_eq!(request.message, crate::junkenstein_messages::VICTORY_VIEW);
            let params: serde_json::Map<String, Json> = request
                .parameters
                .iter()
                .map(|(&key, value)| {
                    (
                        key.to_string(),
                        match value {
                            Value::Bool(value) => json!(value),
                            Value::Entity(value) => json!({"entity":value}),
                            other => panic!("unexpected parameter {other:?}"),
                        },
                    )
                })
                .collect();
            assert_eq!(Json::Object(params), case["expected"]["parameters"]);
        }
    }

    #[test]
    fn shared_spatial_owner_absence_contract() {
        struct Host(Option<bool>);
        impl World for Host {
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn spatial_binding_owner_available(&self, _site: Site) -> Option<bool> {
                self.0
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-spatial-owner-absence-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.spatial-owner-absence.v1");
        assert_eq!(contract["target_build"], 104319);
        for native_only in [false, true] {
            for case in contract["input"]["cases"].as_array().unwrap() {
                let definition = if native_only {
                    &contract["input"]["native_only"]
                } else {
                    &contract["input"]
                };
                let position = u16::try_from(if native_only {
                    definition["position"].as_u64().unwrap()
                } else {
                    case["position"].as_u64().unwrap()
                })
                .unwrap();
                let mut model = graph(
                    0x0ced,
                    vec![node(
                        position,
                        definition["class"].as_str().unwrap(),
                        Some(0),
                        definition["fields"].clone(),
                    )],
                    json!([position]),
                    json!([]),
                );
                model["items"] = json!(position + 1);
                let graphs = Arc::new(store(vec![model]));
                let mut world = Host(match case["service"].as_str().unwrap() {
                    "absent" => Some(false),
                    "present" => Some(true),
                    "unknown" => None,
                    _ => panic!("unknown service"),
                });
                let mut component = Component::start(
                    graphs,
                    0xa000_0101,
                    Variables::new(),
                    vec![Root::listener(1, 0x0ced, Some(0))],
                    0,
                    &mut world,
                    MAX_INIT_WORK,
                )
                .unwrap();
                let snapshot = component.snapshot().unwrap();
                let actual = if snapshot.initialization_complete() {
                    let state = &snapshot.instances[&1].states[&0];
                    let before = json!([state.active, state.activation]);
                    component
                        .machine
                        .queues
                        .push(1, frame_start(1), Event::Abort(0))
                        .unwrap();
                    component.advance_frame(1, &mut world).unwrap();
                    let state = &component.snapshot().unwrap().instances[&1].states[&0];
                    json!({"status":"ok","begin":before,"end":[state.active,state.activation]})
                } else {
                    assert!(snapshot.diagnostics.iter().any(|issue| matches!(&issue.issue,Issue::UnsupportedState(class) if class==definition["class"].as_str().unwrap())));
                    json!({"status":"unavailable"})
                };
                assert_eq!(
                    actual, case["expected"],
                    "{} native_only={native_only}",
                    case["name"]
                );
            }
        }
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein mode graphs"]
    fn authored_victory_view_child_finishes_the_mode_phase() {
        #[derive(Default)]
        struct EndWorld(Vec<crate::junkenstein_messages::DefenderMessage>);
        impl World for EndWorld {
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn play_victory_voice(&mut self, _site: Site, _actor: &Value) -> Option<u64> {
                Some(100)
            }
            fn queue_mode_action(&mut self, _site: Site) -> bool {
                true
            }
            fn route_game_message(&mut self, site: Site, message: u64, params: &Variables) -> bool {
                let Some(request) = crate::junkenstein_messages::DefenderMessage::victory_view(
                    site, message, params,
                ) else {
                    return false;
                };
                self.0.push(request);
                true
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let raw = graphs
            .graph(0x0c8c)
            .unwrap()
            .node(73)
            .unwrap()
            .state
            .unwrap();
        let mut root = Root::listener(2, 0x0c8c, Some(raw));
        root.variables.insert(8504, Value::Float(4.5));
        let mut world = EndWorld::default();
        let mut component = Component::start(
            graphs,
            crate::world::GAME_MODE_ENTITY,
            Variables::from([(8602, Value::Entity(0xa000_0101))]),
            vec![root],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        for frame in 1..=80 {
            component.advance_frame(frame, &mut world).unwrap();
        }
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let mode = &snapshot.instances[&2];
        assert_eq!(mode.variables.get(&59), Some(&Value::Integer(5)));
        assert!(!mode.states[&raw].active);
        assert!(
            !snapshot
                .instances
                .values()
                .any(|instance| instance.graph == 0x0cec)
        );
        assert_eq!(
            world
                .0
                .iter()
                .map(|message| message.parameters[&0x390].clone())
                .collect::<Vec<_>>(),
            [Value::Bool(true), Value::Bool(false)]
        );
    }

    #[test]
    fn public_schema_evaluates_before_assignment_and_inherits_to_children() {
        let entry = |links: &[(u16, &str)]| {
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(links,&[])}),
            )
        };
        let set = |position, output, value| {
            node(
                position,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(output,true),"m_value":value}),
            )
        };
        let mut parent = graph(
            42,
            vec![
                entry(&[(1, "m_inPlug"), (2, "m_beginPlug")]),
                set(1, 100, var(2, false)),
                node(
                    2,
                    "STU_37D754C8",
                    Some(0),
                    json!({
                        "m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{"m_graph":"0x058000000000002B"}}}
                    }),
                ),
            ],
            json!([2]),
            json!([0]),
        );
        let mut child = graph(
            43,
            vec![entry(&[(1, "m_inPlug")]), set(1, 101, var(4, false))],
            json!([]),
            json!([0]),
        );
        let row =
            |id, value| json!({"m_0D09D2D9":format!("0x0D8000000000{id:04X}"),"m_value":value});
        parent["fields"]["m_publicSchema"] = json!({"$":"STU_77990ED7",
            "m_89EAAC40":"0x058000000000002A","m_entries":[
                row(1,json!(10)),row(2,json!(20)),row(3,var(1,false)),
                {"m_0D09D2D9":null,"m_value":999}]});
        child["fields"]["m_publicSchema"] =
            json!({"$":"STUStatescriptSchema","m_entries":[row(4,json!(40))]});
        let graphs = Arc::new(store(vec![parent, child]));
        let mut root = Root::new(1, 42);
        root.variables.insert(2, Value::Integer(30));
        for enabled in [false, true] {
            let mut world = EmptyWorld;
            let start = if enabled {
                Component::start_with_public_schema
            } else {
                Component::start
            };
            let component = start(
                Arc::clone(&graphs),
                42,
                Variables::new(),
                vec![root.clone()],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(snapshot.variables[&100], Value::Integer(30));
            assert_eq!(
                snapshot.variables[&101],
                if enabled {
                    Value::Integer(40)
                } else {
                    Value::Null
                }
            );
            if enabled {
                assert_eq!(snapshot.instances[&1].variables[&1], Value::Integer(10));
                assert_eq!(snapshot.instances[&1].variables[&3], Value::Null);
                assert!(!snapshot.instances[&1].variables.contains_key(&0));
            }
        }
    }

    #[test]
    fn public_schema_rejects_malformed_rows_entries_and_foreign_reference() {
        for schema in [
            json!({"$":"STUStatescriptSchema","m_entries":{}}),
            json!({"$":"STUStatescriptSchema","m_entries":[7]}),
            json!({"$":"STU_77990ED7","m_89EAAC40":"0x058000000000002B"}),
        ] {
            let mut fixture = graph(42, vec![], json!([]), json!([]));
            fixture["fields"]["m_publicSchema"] = schema;
            let component = Component::start_with_public_schema(
                Arc::new(store(vec![fixture])),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(!component.snapshot().unwrap().initialization_complete());
        }
    }

    fn plug(links: &[(u16, &str)], states: &[u16]) -> Json {
        json!({"$":"Plug","links":links,"m_FF3DAF1E":states})
    }

    fn var(id: u32, entity: bool) -> Json {
        json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":entity})
    }

    #[test]
    fn ai_behavior_inputs_update_from_variables_and_native_reads_and_clean_up() {
        #[derive(Default)]
        struct BehaviorWorld {
            records: Vec<(AiBehaviorRequest, bool)>,
            health: f64,
        }
        impl World for BehaviorWorld {
            fn ai_behavior_request(
                &mut self,
                _: Site,
                record: AiBehaviorRequest,
                active: bool,
            ) -> Option<bool> {
                self.records.push((record, active));
                Some(true)
            }
            fn body_health(&self, _: Site, _: Option<PoolKind>, _: bool) -> Option<f64> {
                Some(self.health)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        for class in ["STU_B61E6681", "STU_CFA338CB", "STU_600E3C59"] {
            for abort in [false, true] {
                let mut world = BehaviorWorld {
                    health: 100.0,
                    ..Default::default()
                };
                let data = graph(
                    42,
                    vec![
                        node(
                            0,
                            "STUStatescriptEntry",
                            None,
                            json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                        ),
                        node(
                            1,
                            class,
                            Some(0),
                            json!({"m_weight":var(100,true),"m_88881C38":{"$":"STUConfigVarBool","m_value":true},
                        "m_36998F8A":{"$":"STU_32E6E4D8","m_entity":{"$":"STU_B5A0CAF0"}},
                        "m_157D72D1":{"$":"STUConfigVarFloat","m_value":90},"m_DD792FF0":2}),
                        ),
                        node(
                            2,
                            "STUStatescriptEntryGameMessage",
                            None,
                            json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                        "m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                        ),
                    ],
                    json!([1]),
                    json!([0, 2]),
                );
                let mut component = Component::start(
                    Arc::new(store(vec![data])),
                    0xc000_1001,
                    Variables::from([(100, Value::Float(2.0))]),
                    vec![Root::new(1, 42)],
                    0,
                    &mut world,
                    MAX_INIT_WORK,
                )
                .unwrap();
                assert!(component.snapshot().unwrap().initialization_complete());
                let first = world.records[0].0;
                assert_eq!(first.priority, 0.0);
                assert!(first.flag);
                assert_eq!(first.shared, [100.0, 0.0]);
                match class {
                    "STU_B61E6681" => assert_eq!(first.inputs, [0.0, 2.0, 0.0, 0.0, 0.0]),
                    "STU_CFA338CB" => assert_eq!(
                        first.inputs,
                        [
                            0.0,
                            0.0,
                            f64::from(
                                (90.0 * f64::from((std::f64::consts::PI / 180.0) as f32)) as f32
                            ),
                            0.0,
                            0.0
                        ]
                    ),
                    _ => assert_eq!(first.inputs, [2.0, 2.0, 2.0, f64::from(0.1f32), 1.0]),
                }
                component
                    .set_entity_variable(100, Value::Float(4.0), &mut world)
                    .unwrap();
                component.run_now(&mut world).unwrap();
                if class != "STU_CFA338CB" {
                    let changed = world.records.last().unwrap().0;
                    assert_eq!(
                        changed.inputs[if class == "STU_B61E6681" { 1 } else { 2 }],
                        4.0
                    );
                }
                world.health = 50.0;
                component.advance_frame(1, &mut world).unwrap();
                assert_eq!(world.records.last().unwrap().0.shared, [50.0, 0.0]);
                if abort {
                    component
                        .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                        .unwrap();
                    component.run_now(&mut world).unwrap();
                }
                Vm::new(&component.graphs, &mut world, &mut component.machine)
                    .destroy(1)
                    .unwrap();
                assert_eq!(
                    world.records.iter().filter(|(_, active)| !*active).count(),
                    1
                );
            }
        }
    }

    #[test]
    fn junkenstein_base_states_keep_plugs_abort_and_reentry_within_authored_scope() {
        for (index, class) in [
            (0x0c87, "STU_6A6BBB8D"),
            (0x0c87, "STU_918B3FA9"),
            (0x0c87, "STU_14611C1B"),
            (0x0c87, "STU_200B27C5"),
            (0x0c87, "STU_D52B9026"),
            (0x0c87, "STU_4531992F"),
            (0x0c87, "STU_55F72184"),
            (0x0c87, "STU_632B74B9"),
            (0x0cf3, "STU_724DFBC6"),
            (0x0cf3, "STU_531AA630"),
        ] {
            let fixture = |index| {
                graph(
                    index,
                    vec![
                        node(
                            0,
                            "STUStatescriptEntry",
                            None,
                            json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                        ),
                        node(
                            1,
                            class,
                            Some(0),
                            json!({"m_onBeginPlug":plug(&[(3,"m_inPlug")],&[]),
                    "m_onEndPlug":plug(&[(4,"m_inPlug")],&[]),"m_subgraphPlug":plug(&[(2,"m_beginPlug")],&[1])}),
                        ),
                        node(2, "STUStatescriptState", Some(1), json!({})),
                        node(
                            3,
                            "STUStatescriptActionSetVar",
                            None,
                            json!({"m_out_Var":var(100,true),"m_value":true}),
                        ),
                        node(
                            4,
                            "STUStatescriptActionSetVar",
                            None,
                            json!({"m_out_Var":var(101,true),"m_value":true}),
                        ),
                        node(
                            5,
                            "STUStatescriptEntryGameMessage",
                            None,
                            json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000002"},
                    "m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                        ),
                        node(
                            6,
                            "STUStatescriptEntryGameMessage",
                            None,
                            json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                    "m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                        ),
                    ],
                    json!([1, 2]),
                    json!([0, 5, 6]),
                )
            };
            let mut component = Component::start(
                Arc::new(store(vec![fixture(index)])),
                42,
                Variables::new(),
                vec![Root::new(1, index)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{class}: {:?}",
                snapshot.diagnostics
            );
            assert_eq!(snapshot.variables[&100], Value::Bool(true));
            assert!(snapshot.instances[&1].states[&1].active);
            component
                .queue_message(1, 0x0240_0000_0000_0002, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert_eq!(snapshot.variables[&101], Value::Bool(true));
            assert!(
                snapshot.instances[&1]
                    .states
                    .values()
                    .all(|state| !state.active)
            );
            component
                .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            let state = &component.snapshot().unwrap().instances[&1].states[&0];
            assert!(state.active);
            assert_eq!(state.activation, 2);
            let foreign = Component::start(
                Arc::new(store(vec![fixture(42)])),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(
                foreign
                    .snapshot()
                    .unwrap()
                    .diagnostics
                    .iter()
                    .any(|diagnostic| diagnostic.issue == Issue::UnsupportedState(class.into()))
            );
        }
    }

    #[test]
    fn shared_flashbang_base_state_contract() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-base-state-v1.json"
        ))
        .unwrap();
        for (index, supported) in [(0x224e, true), (42, false)] {
            let mut data = graph(
                index,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(25,"m_beginPlug")],&[])}),
                    ),
                    node(1, "STUStatescriptState", Some(1), json!({})),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100,true),"m_value":true}),
                    ),
                    node(
                        3,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101,true),"m_value":true}),
                    ),
                    node(
                        25,
                        "STU_0DB9E66B",
                        Some(0),
                        json!({
                            "m_targets":{"$":"unsupported-if-evaluated"},
                            "m_loadout":{"$":"unsupported-if-evaluated"},
                            "m_43B917C6":{"$":"unsupported-if-evaluated"},
                            "m_onBeginPlug":plug(&[(2,"m_inPlug")],&[]),
                            "m_onEndPlug":plug(&[(3,"m_inPlug")],&[]),
                            "m_subgraphPlug":plug(&[(1,"m_beginPlug")],&[1]),
                        }),
                    ),
                    node(
                        26,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"m_gameMessage":"0x0240000000000002"},
                    "m_outPlug":plug(&[(25,ABORT_PLUG)],&[])}),
                    ),
                    node(
                        27,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                    "m_outPlug":plug(&[(25,"m_beginPlug")],&[])}),
                    ),
                ],
                json!([25, 1]),
                json!([0, 26, 27]),
            );
            data["items"] = json!(28);
            data["remote"] = json!([25]);
            data["nodes"][4]["remote"] = json!(0);
            let graphs = Arc::new(store(vec![data]));
            let codecs = crate::script_codecs::CodecTable::from_json(
                r#"{"classes":{"0DB9E66B":{"name":"STU_0DB9E66B","codec":"none","stec":false,"status":"VERIFIED","layout":[]}}}"#,
            ).unwrap();
            let mut component = Component::start(
                Arc::clone(&graphs),
                42,
                Variables::new(),
                vec![Root::new(1, index)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            if !supported {
                assert!(
                    component
                        .snapshot()
                        .unwrap()
                        .diagnostics
                        .iter()
                        .any(|d| d.issue == Issue::UnsupportedState("STU_0DB9E66B".into()))
                );
                continue;
            }
            for step in fixture["steps"].as_array().unwrap() {
                if let Some(message) = step["message"].as_u64() {
                    component
                        .queue_message(1, message, Variables::new())
                        .unwrap();
                    component.run_now(&mut EmptyWorld).unwrap();
                }
                let snapshot = component.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                let states = &snapshot.instances[&1].states;
                assert_eq!(states[&0].kind, StateKind::Plain);
                for raw in [0, 1] {
                    assert_eq!(states[&raw].active, step["active"].as_bool().unwrap());
                }
                assert_eq!(
                    states[&0].activation,
                    u16::try_from(step["activation"].as_u64().unwrap()).unwrap()
                );
                assert_eq!(snapshot.variables.get(&100), Some(&Value::Bool(true)));
                if step["ended"] == true {
                    assert_eq!(snapshot.variables.get(&101), Some(&Value::Bool(true)));
                }
                let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(&graphs, &codecs);
                let projection = encoder
                    .project(snapshot, crate::script_snapshot_wire::View::Observer)
                    .unwrap();
                encoder.full(&projection).unwrap();
            }
        }
    }

    #[test]
    fn ai_controller_base_states_are_limited_to_the_authored_graph_scope() {
        for (index, class, supported) in [
            (0x0c87, "STU_6A6BBB8D", true),
            (42, "STU_6A6BBB8D", false),
            (0x0cf3, "STU_531AA630", true),
            (42, "STU_531AA630", false),
        ] {
            let data = graph(
                index,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                    node(1, class, Some(0), json!({})),
                ],
                json!([1]),
                json!([0]),
            );
            let component = Component::start(
                Arc::new(store(vec![data])),
                0xc000_1001,
                Variables::new(),
                vec![Root::new(1, index)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert_eq!(
                component.snapshot().unwrap().initialization_complete(),
                supported
            );
        }
    }

    #[test]
    fn ai_behavior_rejects_priority_resources_of_an_unrelated_type() {
        let data = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STU_B61E6681",
                    Some(0),
                    json!({"m_priority":{"$":"STU_8A491388","m_priority":"0x0D80000000000003","m_resourceKey":null}}),
                ),
            ],
            json!([1]),
            json!([0]),
        );
        let component = Component::start(
            Arc::new(store(vec![data])),
            0xc000_1001,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(matches!(
            component.snapshot().unwrap().diagnostics.as_slice(),
            [Diagnostic {
                issue: Issue::UnsupportedField("AI behavior priority resource"),
                ..
            }]
        ));
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein defender graph"]
    fn authored_defender_binds_message_source_and_checks_facing() {
        const OWNER: u32 = 0xa000_0101;
        const SENDER: u32 = 0xc000_1000;
        struct ReceiverWorld([f64; 3]);
        impl World for ReceiverWorld {
            fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
                Some(match entity {
                    OWNER => [0.0, 0.0, 0.0],
                    SENDER => [1.0, 0.0, 0.0],
                    _ => panic!("unexpected receiver position query for {entity:X}"),
                })
            }
            fn forward(&self, entity: u32) -> Option<[f64; 3]> {
                (entity == OWNER).then_some(self.0)
            }
            fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
                (site.entity == OWNER
                    && script_expr::native_shape(config, "STU_65ABFCCC", Some("m_target")))
                .then_some(Value::Integer(0))
            }
            fn has_target_tag(&self, _: Site, entity: u32, tag: u64) -> Option<bool> {
                (entity == SENDER && tag == 0x0d60_0000_0000_0326).then_some(false)
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        for direction in [-1.0, 1.0] {
            let mut world = ReceiverWorld([direction, 0.0, 0.0]);
            let mut defender = Component::start(
                Arc::clone(&graphs),
                OWNER,
                Variables::new(),
                vec![Root::listener(1, 0x0ca5, None)],
                4200,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(defender.snapshot().unwrap().instances[&1].states.is_empty());
            let parameters =
                Variables::from([(0x8d, Value::Integer(1)), (0x5c, Value::Entity(SENDER))]);
            assert_eq!(
                defender
                    .queue_entity_message_from(SENDER, 0x0240_0000_0000_00bd, parameters)
                    .unwrap(),
                1
            );
            defender.run_now(&mut world).unwrap();
            let snapshot = defender.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            let values = &snapshot.instances[&1].variables;
            assert_eq!(values[&40], Value::Entity(SENDER));
            assert_eq!(values[&47], Value::Integer(1));
            assert_eq!(values[&536], Value::Entity(SENDER));
            assert_eq!(
                values.get(&1725),
                (direction > 0.0).then_some(&Value::Integer(0))
            );
        }
    }

    #[test]
    fn entity_message_queues_each_live_root_with_shared_parameters() {
        let data = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
            "m_B60ABDA4":var(40,false),"m_FF703F20":var(41,false),
            "m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(100,false),
            "m_value":{"$":"STUConfigVarExpression","m_configVars":[],"m_expression":{"m_opcodes":[13,0,0],"m_dynamicVars":[{"m_identifier":4}],"m_D99EF254":[]}}}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![data])),
            0xc000_1000,
            Variables::new(),
            vec![Root::new(1, 42), Root::new(2, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert_eq!(
            component
                .queue_entity_message_from(
                    0xc000_1001,
                    0x0240_0000_0000_0003,
                    Variables::from([(4, Value::Integer(17))])
                )
                .unwrap(),
            2
        );
        component.run_now(&mut EmptyWorld).unwrap();
        for instance in [1, 2] {
            let values = &component.snapshot().unwrap().instances[&instance].variables;
            assert_eq!(values[&100], Value::Integer(17));
            assert_eq!(values[&40], Value::Entity(0xc000_1001));
            assert_eq!(values[&41], Value::Entity(0xc000_1001));
        }
    }

    #[test]
    fn ai_override_records_are_removed_once_on_abort_and_instance_destruction() {
        #[derive(Default)]
        struct AiWorld {
            control: Vec<(AiControlOverride, bool)>,
            weapon: Vec<(AiWeaponOverride, bool)>,
        }
        impl World for AiWorld {
            fn ai_control_override(
                &mut self,
                _: Site,
                record: AiControlOverride,
                active: bool,
            ) -> Option<bool> {
                self.control.push((record, active));
                Some(true)
            }
            fn ai_weapon_override(
                &mut self,
                _: Site,
                record: AiWeaponOverride,
                active: bool,
            ) -> Option<bool> {
                self.weapon.push((record, active));
                Some(true)
            }
            fn related_entity(
                &mut self,
                _: Site,
                _: script_expr::EntityRelation,
                _: u32,
            ) -> Option<u32> {
                Some(0xc000_1001)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        for class in ["STU_473549CA", "STU_CF8C7CC5", "STU_066B4605"] {
            for abort in [false, true] {
                let fields = json!({"m_00D49978":{"$":"STU_B5A0CAF0"},"m_D66E7D68":{"$":"STUConfigVarInt","m_value":2},
                    "m_F2810377":{"$":"STUConfigVarIdentifier","m_identifier":"0x0D80000000000010"},
                    "m_F8585ABF":{"$":"STUConfigVarBool","m_value":true}});
                let fixture = graph(
                    42,
                    vec![
                        node(
                            0,
                            "STUStatescriptEntry",
                            None,
                            json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                        ),
                        node(1, class, Some(0), fields),
                        node(
                            2,
                            "STUStatescriptEntryGameMessage",
                            None,
                            json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                        "m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                        ),
                    ],
                    json!([1]),
                    json!([0, 2]),
                );
                let mut world = AiWorld::default();
                let mut component = Component::start(
                    Arc::new(store(vec![fixture])),
                    0xc000_1000,
                    Variables::new(),
                    vec![Root::new(1, 42)],
                    0,
                    &mut world,
                    MAX_INIT_WORK,
                )
                .unwrap();
                assert!(
                    component.snapshot().unwrap().initialization_complete(),
                    "{:?}",
                    component.snapshot().unwrap().diagnostics
                );
                if abort {
                    component
                        .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                        .unwrap();
                    component.run_now(&mut world).unwrap();
                }
                Vm::new(&component.graphs, &mut world, &mut component.machine)
                    .destroy(1)
                    .unwrap();
                if class == "STU_066B4605" {
                    assert_eq!(world.weapon.len(), 2);
                    assert_eq!(world.weapon[0].0.slot, 2);
                    assert_eq!(world.weapon[0].0.identifier, 0x0d80_0000_0000_0010);
                    assert!(world.weapon[0].0.enabled);
                    assert!(world.weapon[0].1);
                    assert_eq!(world.weapon[1], (world.weapon[0].0, false));
                } else {
                    assert_eq!(world.control.len(), 2);
                    assert_eq!(
                        world.control[0].0.limits,
                        if class == "STU_473549CA" {
                            Some([f64::from(std::f32::consts::PI); 2])
                        } else {
                            None
                        }
                    );
                    assert_eq!(world.control[0].0.priority, 9999.0);
                    assert!(world.control[0].1);
                    assert_eq!(world.control[1], (world.control[0].0, false));
                }
            }
        }
    }

    fn parent_var(id: u32) -> Json {
        json!({"$":"STU_919FD47C","m_expression":{
            "m_opcodes":[10,1,0,0], "m_dynamicVars":[var(id, false)]
        }})
    }

    #[test]
    fn entity_creation_preserves_failure_and_no_entity_outputs_before_following() {
        struct Creator {
            requests: Vec<EntityCreation>,
            result: Result<Option<u32>, String>,
        }
        impl World for Creator {
            fn create_entity(
                &mut self,
                site: Site,
                request: &EntityCreation,
            ) -> Result<Option<u32>, String> {
                assert_eq!((site.entity, site.graph, site.node), (42, 0x0cba, Some(1)));
                self.requests.push(request.clone());
                self.result.clone()
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        for hero in [false, true] {
            for result in [
                Ok(Some(0xc000_0000)),
                Ok(None),
                Ok(Some(0)),
                Ok(Some(42)),
                Err("allocation failed".into()),
            ] {
                for bad in [
                    "none", "options", "identity", "position", "team", "resource",
                ] {
                    let bad_options = bad != "none";
                    let mut fields = json!({"m_position":var(100,true),"m_E95B313B":var(102,true),
                        "m_outPlug":plug(&[(2,"m_inPlug")], &[]),"m_51AB9D21":5});
                    if hero {
                        fields["m_A1A715A6"] = var(101, true);
                        fields["m_3EBF988B"] = json!(1);
                        fields["m_1C558638"] = json!(15.0);
                        fields["m_F8E56B94"] = json!(true);
                        fields["m_DE3B4F01"] = var(104, true);
                        fields["m_D7F85181"] = var(105, true);
                        fields["m_246C47AF"] = var(106, true);
                    } else {
                        // Native scalar groups evaluate their first config only.
                        fields["m_entityDef"] = json!({"$":"STU_06610EBC","m_configVars":[var(101,true),{"$":"unsupported"}]});
                    }
                    if bad == "options" {
                        fields["m_AA26E7DD"] = json!(false);
                    }
                    if bad == "team" {
                        fields["m_3EBF988B"] = json!(2);
                    }
                    let graphs = store(vec![graph(
                        0x0cba,
                        vec![
                            node(
                                0,
                                "STUStatescriptEntry",
                                None,
                                json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                            ),
                            node(1, "STUStatescriptActionCreateEntity", None, fields),
                            node(
                                2,
                                "STUStatescriptActionSetVar",
                                None,
                                json!({"m_out_Var":var(103,true),"m_value":var(102,true)}),
                            ),
                        ],
                        json!([]),
                        json!([0]),
                    )]);
                    let mut world = Creator {
                        requests: vec![],
                        result: result.clone(),
                    };
                    let snapshot = initialize(
                        &graphs,
                        42,
                        Variables::from([
                            (
                                100,
                                Value::Vector(if bad == "position" {
                                    [f64::NAN, 2.0, 3.0]
                                } else {
                                    [1.0, 2.0, 3.0]
                                }),
                            ),
                            (
                                101,
                                Value::Asset(if bad == "identity" {
                                    0x0580_0000_0000_0175
                                } else if !hero && bad == "resource" {
                                    0
                                } else if hero {
                                    0x02e0_0000_0000_0175
                                } else {
                                    0x0400_0000_0000_16be
                                }),
                            ),
                            (102, Value::Integer(99)),
                            (104, Value::Asset(0x0d80_0000_0000_195e)),
                            (
                                105,
                                Value::Asset(if bad == "resource" {
                                    0x0a50_0000_0000_16c0
                                } else {
                                    0x0400_0000_0000_16c0
                                }),
                            ),
                            (106, Value::Null),
                        ]),
                        vec![Root::new(1, 0x0cba)],
                        &mut world,
                        MAX_INIT_WORK,
                    )
                    .unwrap();
                    let success =
                        !bad_options && matches!(result, Ok(Some(0xc000_0000)) | Ok(None));
                    assert_eq!(snapshot.initialization_complete(), success);
                    assert_eq!(world.requests.len(), usize::from(!bad_options));
                    let expected = if !bad_options && result == Ok(Some(0xc000_0000)) {
                        Value::Entity(0xc000_0000)
                    } else {
                        Value::Integer(99)
                    };
                    assert_eq!(snapshot.variables[&102], expected);
                    assert_eq!(snapshot.variables.get(&103), success.then_some(&expected));
                    if let Some(request) = world.requests.first() {
                        assert_eq!(request.position, [1.0, 2.0, 3.0]);
                        assert_eq!(
                            request.kind,
                            if hero {
                                CreationKind::Hero {
                                    hero: 0x02e0_0000_0000_0175,
                                    team: 1,
                                    yaw: 15.0,
                                    flags: [Value::Bool(true), Value::Asset(0x0d80_0000_0000_195e)],
                                    controller: Some(0x0400_0000_0000_16c0),
                                    skin: None,
                                }
                            } else {
                                CreationKind::Definition(0x0400_0000_0000_16be)
                            }
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn participant_collection_writes_before_following_and_preserves_order() {
        struct Participants(Option<Vec<u32>>);
        impl World for Participants {
            fn participant_targets(&self, site: Site, _: &Json) -> Option<Vec<u32>> {
                assert_eq!((site.graph, site.node), (0x0cba, Some(1)));
                self.0.clone()
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        for entities in [None, Some(vec![]), Some(vec![0xc000_1002, 0xc000_1000])] {
            let mut collect = node(
                1,
                "STU_42578342",
                None,
                json!({
                    "m_filter":{"$":"STUConfigVarFilter"},"m_4AEEE6A1":var(100,true),
                    "m_outPlug":plug(&[(2,"m_inPlug")], &[]),
                }),
            );
            collect["fields"]["m_ADEB6E05"] = json!(true);
            let graphs = store(vec![graph(
                0x0cba,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                    ),
                    collect,
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101,true),"m_value":var(100,true)}),
                    ),
                ],
                json!([]),
                json!([0]),
            )]);
            let snapshot = initialize(
                &graphs,
                42,
                Variables::new(),
                vec![Root::new(1, 0x0cba)],
                &mut Participants(entities.clone()),
                MAX_INIT_WORK,
            )
            .unwrap();
            if let Some(entities) = entities {
                let expected = Value::Array(entities.into_iter().map(Value::Entity).collect());
                assert_eq!(snapshot.variables[&100], expected);
                assert_eq!(snapshot.variables[&101], expected);
                assert!(snapshot.initialization_complete());
            } else {
                assert!(!snapshot.variables.contains_key(&100));
                assert!(!snapshot.variables.contains_key(&101));
                assert!(matches!(
                    snapshot.diagnostics.as_slice(),
                    [Diagnostic {
                        issue: Issue::UnsupportedField("scene participant filter"),
                        ..
                    }]
                ));
            }
        }
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein spawner graph"]
    fn authored_spawner_filters_dead_and_removed_bodies_in_order() {
        struct Bodies(BTreeMap<u32, bool>);
        impl World for Bodies {
            fn encounter_body_alive(&self, entity: u32) -> Option<bool> {
                Some(self.0.get(&entity).copied().unwrap_or(false))
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let [first, dead, second, removed] = [0xc000_1000, 0xc000_1002, 0xc000_1004, 0xc000_1006];
        let next = 0xc000_1008;
        let mut world = Bodies(BTreeMap::from([
            (first, true),
            (dead, false),
            (second, true),
        ]));
        let mut root = Root::listener(1, 0x0cba, Some(1));
        root.variables.insert(38, Value::Entity(next));
        root.variables.insert(889, Value::Integer(0));
        root.variables.insert(
            8399,
            Value::Array(vec![
                Value::Entity(first),
                Value::Entity(dead),
                Value::Entity(second),
                Value::Entity(removed),
            ]),
        );
        let mut component = Component::start(
            graphs,
            0xd000_0010,
            Variables::new(),
            vec![root],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            snapshot.instances[&1].variables[&8399],
            Value::Array(vec![Value::Entity(first), Value::Entity(second)])
        );
        world.0.insert(first, false);
        component.advance_frame(1, &mut world).unwrap();
        assert_eq!(
            component.snapshot().unwrap().instances[&1].variables[&8399],
            Value::Array(vec![Value::Entity(second)])
        );
        world.0.remove(&second);
        component.advance_frame(2, &mut world).unwrap();
        assert_eq!(
            component.snapshot().unwrap().instances[&1].variables[&8399],
            Value::Array(vec![])
        );
        world.0.insert(next, true);
        component
            .run(&mut world, |vm| {
                let append = vm.graph(1).node(5).unwrap();
                assert!(vm.run_node(1, append, None, 0)?);
                vm.run()
            })
            .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let spawner = &snapshot.instances[&1];
        assert_eq!(
            spawner.variables[&8399],
            Value::Array(vec![Value::Entity(next)])
        );
        assert_eq!(spawner.variables[&889], Value::Float(1.0));
        assert!(spawner.states[&0].active);
    }

    #[test]
    fn played_schema_queues_defaults_before_overrides_and_respects_instance_policy() {
        let mut callee = graph(
            43,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(900,true),"m_value":var(7,false)}),
                ),
                node(
                    2,
                    "STU_BB4AC733",
                    None,
                    json!({"m_B60ABDA4":var(28,false),"m_outPlug":plug(&[(3,"m_inPlug")],&[])}),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(902,true),"m_value":var(7,false)}),
                ),
            ],
            json!([]),
            json!([0, 2]),
        );
        let plus_one = json!({"$":"STU_919FD47C","m_configVars":null,"m_expression":{
            "m_D99EF254":[1],"m_dynamicVars":[var(7,false)],"m_fragments":null,
            "m_opcodes":[8,0,6,0,23,0]}});
        callee["fields"]["m_publicSchema"] = json!({"$":"STUStatescriptSchema","m_entries":[
            {"m_0D09D2D9":"0x0D80000000000007","m_value":10},
            {"m_0D09D2D9":"0x0D80000000000008","m_value":plus_one}]});
        let mut component = Component::start(
            Arc::new(store(vec![callee])),
            0xa000_0101,
            Variables::new(),
            vec![],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let site = Site {
            entity: 0xa000_0201,
            instance: 1,
            graph: 42,
            node: Some(0),
        };
        let request = |policy, value, source, auto_retire| PlayScript {
            policy,
            graph: 43,
            source,
            auto_retire,
            parameters: vec![],
            overrides: vec![(0x0d80_0000_0000_0007, Value::Integer(value))],
        };
        component
            .receive_play_script(site, &request(0, 40, 0xa000_0301, false), &mut EmptyWorld)
            .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&900], Value::Integer(40));
        assert_eq!(snapshot.variables[&902], Value::Integer(40));
        assert_eq!(
            script_expr::to_float(&snapshot.instances[&1].variables[&8]),
            1.0
        );
        component
            .receive_play_script(site, &request(0, 77, 0xa000_0401, true), &mut EmptyWorld)
            .unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&902],
            Value::Integer(40)
        );
        component
            .receive_play_script(site, &request(2, 99, 0xa000_0401, true), &mut EmptyWorld)
            .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&900], Value::Integer(40));
        assert_eq!(snapshot.variables[&902], Value::Integer(99));
        assert_eq!(
            script_expr::to_float(&snapshot.instances[&1].variables[&8]),
            41.0
        );
        assert_eq!(
            snapshot.instances[&1].variables[&SCRIPT_SOURCE],
            Value::Entity(0xa000_0301)
        );
        assert_eq!(
            snapshot.instances[&1].variables[&28],
            Value::Entity(site.entity)
        );
        assert_eq!(snapshot.instances[&1].retire_after_frame, None);
        component
            .receive_play_script(site, &request(1, 70, 0xa000_0401, false), &mut EmptyWorld)
            .unwrap();
        assert_eq!(component.snapshot().unwrap().instances.len(), 2);
        component
            .receive_play_script(site, &request(2, 80, 0xa000_0401, false), &mut EmptyWorld)
            .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.instances[&1].variables[&7], Value::Integer(80));
        assert_eq!(snapshot.instances[&2].variables[&7], Value::Integer(70));
        component
            .receive_play_script(site, &request(3, 90, 0xa000_0501, true), &mut EmptyWorld)
            .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.instances.keys().copied().collect::<Vec<_>>(), [3]);
        assert_eq!(snapshot.variables[&900], Value::Integer(90));
        assert_eq!(
            snapshot.instances[&3].variables[&SCRIPT_SOURCE],
            Value::Entity(0xa000_0501)
        );
        assert_eq!(snapshot.instances[&3].retire_after_frame, Some(1));
    }

    #[test]
    fn inline_play_resource_takes_priority_and_preserves_caller_values() {
        let inline = json!({"$":"STU_9DB46B98","m_6525E98B":{
            "$":"STUStatescriptGraphWithOverrides","m_graph":"0x058000000000002B",
            "m_1EB5A024":[{"$":"STUStatescriptSchemaEntry","m_0D09D2D9":"0x0D80000000000007","m_value":var(37,false)}]}});
        let caller = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionPlayScript",
                    None,
                    json!({
                "m_69F4B52C":inline,"m_script":{"$":"STU_105E1BCC","m_graph":"0x058000000000002C"},
                "m_AA4DE426":2,"m_7ABA409C":255,"m_targets":{"$":"STU_B5A0CAF0"}}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let callee = graph(
            43,
            vec![
                node(
                    0,
                    "STU_BB4AC733",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(900,true),"m_value":var(7,false)}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let mut root = Root::new(1, 42);
        root.variables.insert(37, Value::Integer(123));
        let component = Component::start(
            Arc::new(store(vec![caller, callee])),
            0xa000_0101,
            Variables::new(),
            vec![root],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.variables[&900], Value::Integer(123));
        assert_eq!(snapshot.instances[&2].retire_after_frame, None);
    }

    #[test]
    fn empty_play_resource_follows_output_and_replacing_the_caller_stops_its_output() {
        for replace in [false, true] {
            let action = json!({
                "m_69F4B52C":{"$":"STU_9DB46B98","m_6525E98B":null},
                "m_script":if replace {json!({"$":"STU_105E1BCC","m_graph":"0x058000000000002A"})} else {Json::Null},
                "m_AA4DE426":3,"m_targets":{"$":"STU_B5A0CAF0"},
                "m_outPlug":plug(&[(2,"m_inPlug")],&[])});
            let caller = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"m_gameMessage":"0x0240000000000009"},
                    "m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                    ),
                    node(1, "STUStatescriptActionPlayScript", None, action),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(900,true),"m_value":true}),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            let mut component = Component::start(
                Arc::new(store(vec![caller])),
                0xa000_0101,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            component
                .queue_message(1, 0x0240_0000_0000_0009, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables.get(&900),
                if replace {
                    None
                } else {
                    Some(&Value::Bool(true))
                }
            );
            assert_eq!(
                snapshot.instances.keys().copied().collect::<Vec<_>>(),
                if replace { vec![2] } else { vec![1] }
            );
        }
    }

    #[test]
    fn deferred_callbacks_order_priorities_and_keep_new_work_in_the_next_batch() {
        let append = |pos, digit| {
            node(
                pos,
                "STUStatescriptActionSetVar",
                None,
                json!({
            "m_out_Var":var(100,true),"m_value":{"$":"STU_919FD47C","m_configVars":null,
            "m_expression":{"m_D99EF254":[10,digit],"m_dynamicVars":[var(100,true)],
                "m_fragments":null,"m_opcodes":[8,0,6,0,25,6,1,23,0]}}}),
            )
        };
        for case in 0..6 {
            // Signed zero priorities compare equal, just like the reference.
            let low = if case < 3 { 1.0 } else { -1.0 };
            let first = if case < 3 { 2.0 } else { 0.0 };
            let second = if case < 3 { 2.0 } else { -0.0 };
            let mode = case % 3;
            let cancel = mode == 1;
            let mut entry = vec![
                (1, "m_beginPlug"),
                (2, "m_beginPlug"),
                (3, "m_beginPlug"),
                (9, "m_inPlug"),
            ];
            if cancel {
                entry.push((1, ABORT_PLUG));
            }
            let mut high = vec![(12, "m_inPlug")];
            if mode == 2 {
                high.push((1, ABORT_PLUG));
            }
            let mut fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&entry,&[])}),
                    ),
                    node(
                        1,
                        "STU_9C9A422A",
                        Some(0),
                        json!({"m_priority":low,"m_FD0C0646":plug(&[(10,"m_inPlug")],&[]),"m_8E484B2E":plug(&[(5,"m_beginPlug")],&[4])}),
                    ),
                    node(
                        2,
                        "STU_9C9A422A",
                        Some(1),
                        json!({"m_priority":first,"m_FD0C0646":plug(&[(11,"m_inPlug"),(4,"m_beginPlug")],&[])}),
                    ),
                    node(
                        3,
                        "STU_9C9A422A",
                        Some(2),
                        json!({"m_priority":second,"m_FD0C0646":plug(&high,&[])}),
                    ),
                    node(
                        4,
                        "STU_9C9A422A",
                        Some(3),
                        json!({"m_priority":100,"m_FD0C0646":plug(&[(13,"m_inPlug")],&[])}),
                    ),
                    node(5, "STUStatescriptState", Some(4), json!({})),
                    node(
                        6,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000009"},"m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                    ),
                    append(9, 9),
                    append(10, 1),
                    append(11, 2),
                    append(12, 3),
                    append(13, 4),
                ],
                json!([1, 2, 3, 4, 5]),
                json!([0, 6]),
            );
            fixture["items"] = json!(14);
            let mut component = Component::start(
                Arc::new(store(vec![fixture])),
                42,
                Variables::from([(100, Value::Integer(0))]),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                script_expr::to_float(&snapshot.variables[&100]),
                if cancel { 9324.0 } else { 93214.0 }
            );
            assert_eq!(
                snapshot.instances[&1]
                    .states
                    .get(&4)
                    .is_some_and(|state| state.active),
                mode == 0
            );
            component
                .queue_message(1, 0x0240_0000_0000_0009, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            assert!(
                !component.snapshot().unwrap().instances[&1]
                    .states
                    .get(&4)
                    .is_some_and(|state| state.active)
            );
        }
    }

    #[test]
    fn deferred_phase_runs_after_ticks_and_is_bounded_when_it_requeues() {
        #[derive(Default)]
        struct Probe(Vec<&'static str>);
        impl World for Probe {
            fn native(&mut self, _: Site, config: &Json) -> Option<Value> {
                match config["$"].as_str()? {
                    "ProbeTick" => {
                        self.0.push("tick");
                        Some(Value::Bool(false))
                    }
                    "ProbeDeferred" => {
                        self.0.push("defer");
                        Some(Value::Integer(1))
                    }
                    _ => None,
                }
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateBooleanSwitch",
                    Some(0),
                    json!({"m_condition":{"$":"ProbeTick"}}),
                ),
                node(
                    2,
                    "STU_9C9A422A",
                    Some(1),
                    json!({"m_FD0C0646":plug(&[(3,"m_inPlug"),(2,ABORT_PLUG)],&[])}),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(100,true),"m_value":{"$":"ProbeDeferred"}}),
                ),
                node(
                    4,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000009"},"m_outPlug":plug(&[(2,"m_beginPlug")],&[])}),
                ),
            ],
            json!([1, 2]),
            json!([0, 4]),
        );
        let mut world = Probe::default();
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        world.0.clear();
        component
            .queue_frame_message(1, 1, 0x0240_0000_0000_0009, Variables::new())
            .unwrap();
        component.advance_frame(1, &mut world).unwrap();
        assert_eq!(world.0, ["tick", "defer", "tick"]);
        let cycle = graph(
            43,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STU_9C9A422A",
                    Some(0),
                    json!({"m_FD0C0646":plug(&[(1,ABORT_PLUG)],&[]),"m_onEndPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
            ],
            json!([1]),
            json!([0]),
        );
        assert!(matches!(
            Component::start(
                Arc::new(store(vec![cycle])),
                42,
                Variables::new(),
                vec![Root::new(1, 43)],
                0,
                &mut EmptyWorld,
                100
            ),
            Err(InitFault::WorkBudget { .. })
        ));
    }

    fn array_insert_fixture(
        initial: Value,
        value: Value,
        index: Option<Value>,
        unique: bool,
        single: bool,
        extra: Json,
    ) -> Result<Component, InitFault> {
        let mut fields = json!({"m_26E3B366":var(100,true), "m_value":var(102,true),
            "m_index":index.as_ref().map(|_| var(103,true)), "m_8AD67B20":unique,
            "m_outPlug":plug(&[(2,"m_inPlug")],&[])});
        for (key, value) in extra.as_object().unwrap() {
            fields[key] = value.clone();
        }
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    if single {
                        "STU_CBE8BDAA"
                    } else {
                        "STU_4C3509FB"
                    },
                    None,
                    fields,
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(101,true),"m_value":true}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let mut variables = Variables::from([(100, initial), (102, value)]);
        if let Some(index) = index {
            variables.insert(103, index);
        }
        Component::start(
            Arc::new(store(vec![fixture])),
            123,
            variables,
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
    }

    #[test]
    fn engine_setting_watchers_poll_the_live_host_after_startup() {
        struct Host(script_expr::EngineSetting);
        impl World for Host {
            fn engine_setting(&self, site: Site, key: u32) -> script_expr::EngineSetting {
                assert_eq!((site.entity, site.graph, key), (123, 42, 1));
                self.0.clone()
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateBooleanSwitch",
                    Some(0),
                    json!({
                "m_condition":{"$":"STU_A753DC79","m_EE729DCB":0,"m_74A2248F":1},
                "m_trueSubgraphPlug":plug(&[(2,"m_beginPlug")],&[1]),
                "m_falseSubgraphPlug":plug(&[(3,"m_beginPlug")],&[2])}),
                ),
                node(2, "STUStatescriptState", Some(1), json!({})),
                node(3, "STUStatescriptState", Some(2), json!({})),
            ],
            json!([1, 2, 3]),
            json!([0]),
        );
        let mut host = Host(script_expr::EngineSetting::Present(Value::Bool(false)));
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            123,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut host,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().instances[&1].states[&2].active);
        for (frame, value, expected) in [
            (
                1,
                script_expr::EngineSetting::Present(Value::Bool(true)),
                true,
            ),
            (
                2,
                script_expr::EngineSetting::Present(Value::Bool(false)),
                false,
            ),
            (
                3,
                script_expr::EngineSetting::Present(Value::Bool(true)),
                true,
            ),
            (4, script_expr::EngineSetting::Missing, false),
        ] {
            host.0 = value;
            component.advance_frame(frame, &mut host).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.instances[&1].states[&0].kind,
                StateKind::BooleanSwitch(expected)
            );
            assert_eq!(snapshot.instances[&1].states[&1].active, expected);
            assert_eq!(snapshot.instances[&1].states[&2].active, !expected);
        }
    }

    #[test]
    fn velocity_watchers_poll_live_mover_changes() {
        struct Host(script_expr::VelocityQuery);
        impl World for Host {
            fn entity_velocity(&self, entity: u32) -> script_expr::VelocityQuery {
                assert_eq!(entity, 123);
                self.0
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateBooleanSwitch",
                    Some(0),
                    json!({
                "m_condition":{"$":"STU_919FD47C","m_configVars":[{"$":"STU_309B0570","m_entity":{"$":"STU_B5A0CAF0"},"m_5B94EDB8":null}],"m_expression":{"m_opcodes":[14,0,50,0]}},
                "m_trueSubgraphPlug":plug(&[(2,"m_beginPlug")],&[1]),
                "m_falseSubgraphPlug":plug(&[(3,"m_beginPlug")],&[2])}),
                ),
                node(2, "STUStatescriptState", Some(1), json!({})),
                node(3, "STUStatescriptState", Some(2), json!({})),
            ],
            json!([1, 2, 3]),
            json!([0]),
        );
        let mut host = Host(script_expr::VelocityQuery::Present([0.0; 3]));
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            123,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut host,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().instances[&1].states[&2].active);
        for (frame, value, expected) in [
            (
                1,
                script_expr::VelocityQuery::Present([1.0, 0.0, 0.0]),
                true,
            ),
            (2, script_expr::VelocityQuery::Present([0.0; 3]), false),
            (
                3,
                script_expr::VelocityQuery::Present([1.0, 0.0, 0.0]),
                true,
            ),
            (4, script_expr::VelocityQuery::Unbound, false),
        ] {
            host.0 = value;
            component.advance_frame(frame, &mut host).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.instances[&1].states[&0].kind,
                StateKind::BooleanSwitch(expected)
            );
            assert_eq!(snapshot.instances[&1].states[&1].active, expected);
            assert_eq!(snapshot.instances[&1].states[&2].active, !expected);
        }
    }

    #[test]
    fn total_health_watchers_poll_damage_death_and_unbinding() {
        struct Host(script_expr::HealthQuery);
        impl World for Host {
            fn total_health(&self, _site: Site, entity: u32) -> script_expr::HealthQuery {
                assert_eq!(entity, 123);
                self.0
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateBooleanSwitch",
                    Some(0),
                    json!({
                "m_condition":{"$":"STU_9B7648C3","m_entity":{"$":"STU_B5A0CAF0"}},
                "m_trueSubgraphPlug":plug(&[(2,"m_beginPlug")],&[1]),
                "m_falseSubgraphPlug":plug(&[(3,"m_beginPlug")],&[2])}),
                ),
                node(2, "STUStatescriptState", Some(1), json!({})),
                node(3, "STUStatescriptState", Some(2), json!({})),
            ],
            json!([1, 2, 3]),
            json!([0]),
        );
        let mut host = Host(script_expr::HealthQuery::Present(0.0));
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            123,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut host,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().instances[&1].states[&2].active);
        for (frame, value, expected) in [
            (1, script_expr::HealthQuery::Present(50.0), true),
            (2, script_expr::HealthQuery::Present(0.0), false),
            (3, script_expr::HealthQuery::Present(50.0), true),
            (4, script_expr::HealthQuery::Unbound, false),
        ] {
            host.0 = value;
            component.advance_frame(frame, &mut host).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.instances[&1].states[&0].kind,
                StateKind::BooleanSwitch(expected)
            );
            assert_eq!(snapshot.instances[&1].states[&1].active, expected);
            assert_eq!(snapshot.instances[&1].states[&2].active, !expected);
        }
    }

    #[test]
    fn foreign_component_watchers_poll_updated_room_snapshots() {
        struct Host(crate::script_entity_state::EntityScriptState);
        impl World for Host {
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
                assert_eq!((entity, id), (0xa000_0100, 7));
                self.0.read(id)
            }
        }
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateBooleanSwitch",
                    Some(0),
                    json!({
                "m_condition":{"$":"STU_919FD47C","m_configVars":[{"$":"STUConfigVarInt","m_value":7}],"m_expression":{"m_opcodes":[8,0,14,0,15,0],"m_dynamicVars":[var(99,true)]}},
                "m_trueSubgraphPlug":plug(&[(2,"m_beginPlug")],&[1]),
                "m_falseSubgraphPlug":plug(&[(3,"m_beginPlug")],&[2])}),
                ),
                node(2, "STUStatescriptState", Some(1), json!({})),
                node(3, "STUStatescriptState", Some(2), json!({})),
            ],
            json!([1, 2, 3]),
            json!([0]),
        );
        let live = |value| {
            crate::script_entity_state::EntityScriptState::Running(Arc::new(Variables::from([(
                7, value,
            )])))
        };
        let mut host = Host(live(Value::Bool(false)));
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            123,
            Variables::from([(99, Value::Entity(0xa000_0100))]),
            vec![Root::new(1, 42)],
            0,
            &mut host,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().instances[&1].states[&2].active);
        for (frame, value, expected) in [
            (1, live(Value::Bool(true)), true),
            (2, live(Value::Bool(false)), false),
            (3, live(Value::Bool(true)), true),
            (
                4,
                crate::script_entity_state::EntityScriptState::Absent,
                false,
            ),
        ] {
            host.0 = value;
            component.advance_frame(frame, &mut host).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.instances[&1].states[&0].kind,
                StateKind::BooleanSwitch(expected)
            );
            assert_eq!(snapshot.instances[&1].states[&1].active, expected);
            assert_eq!(snapshot.instances[&1].states[&2].active, !expected);
        }
    }

    #[test]
    fn shared_contains_condition_contract() {
        fn value(json: &Json) -> Value {
            match json {
                Json::Null => Value::Null,
                Json::Bool(v) => Value::Bool(*v),
                Json::Number(n) => n
                    .as_i64()
                    .map_or_else(|| Value::Float(n.as_f64().unwrap()), Value::Integer),
                Json::Array(items) => Value::Array(items.iter().map(value).collect()),
                other => Value::Entity(other["entity"].as_u64().unwrap().try_into().unwrap()),
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-contains-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(
            contract["case_id"],
            "statescript.container-contains-condition.v1"
        );
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let links = case["links"].as_bool().unwrap();
            let fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                    ),
                    node(
                        1,
                        "STU_821116A6",
                        None,
                        json!({"m_814F8EC3":var(100,true),"m_value":var(101,true),"m_truePlug":plug(if links {&[(2,"m_inPlug")]} else {&[]},&[]),"m_falsePlug":plug(if links {&[(3,"m_inPlug")]} else {&[]},&[])}),
                    ),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(90,true),"m_value":{"$":"STUConfigVarInt","m_value":19}}),
                    ),
                    node(
                        3,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(90,true),"m_value":{"$":"STUConfigVarInt","m_value":23}}),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            let snapshot = initialize(
                &store(vec![fixture]),
                123,
                Variables::from([
                    (100, value(&case["container"])),
                    (101, value(&case["value"])),
                ]),
                vec![Root::new(1, 42)],
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(
                snapshot.diagnostics.is_empty(),
                "{}: {:?}",
                case["name"],
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables.get(&90).cloned().unwrap_or(Value::Null),
                value(&expected["marker"]),
                "{}",
                case["name"]
            );
        }
    }

    #[test]
    fn shared_corpse_retirement_contract() {
        struct Host {
            accept: bool,
            requests: Vec<(u32, bool)>,
        }
        impl World for Host {
            fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _: u32) -> Option<[f64; 3]> {
                None
            }
            fn request_corpse_retirement(
                &mut self,
                _: Site,
                entity: u32,
                phase_exit: bool,
            ) -> bool {
                self.requests.push((entity, phase_exit));
                self.accept
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-corpse-retirement-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(
            contract["case_id"],
            "statescript.corpse-retirement-action.v1"
        );
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let index = case["graph"].as_u64().unwrap() as u32;
            let position: u16 = case["position"].as_u64().unwrap().try_into().unwrap();
            let target = match &case["target"] {
                Json::Null => Value::Null,
                Json::Number(n) => Value::Integer(n.as_i64().unwrap()),
                json => Value::Entity(json["entity"].as_u64().unwrap().try_into().unwrap()),
            };
            let mut fixture = graph(
                index,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(position,"m_inPlug")],&[])}),
                    ),
                    node(
                        position,
                        "STU_523EE38D",
                        None,
                        json!({"m_entity":var(1,true),"m_outPlug":plug(&[(99,"m_inPlug")],&[])}),
                    ),
                    node(
                        99,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(90,true),"m_value":{"$":"STUConfigVarBool","m_value":true}}),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            fixture["items"] = json!(100);
            fixture["header"]["m_nodesBitCount"] = json!(7);
            let mut host = Host {
                accept: case["accept"].as_bool().unwrap(),
                requests: Vec::new(),
            };
            let snapshot = initialize(
                &store(vec![fixture]),
                contract["input"]["owner"]
                    .as_u64()
                    .unwrap()
                    .try_into()
                    .unwrap(),
                Variables::from([(1, target)]),
                vec![Root::new(1, index)],
                &mut host,
                MAX_INIT_WORK,
            )
            .unwrap();
            let requests: Vec<_> = expected["requests"]
                .as_array()
                .unwrap()
                .iter()
                .map(|row| (row[0].as_u64().unwrap() as u32, row[1].as_bool().unwrap()))
                .collect();
            assert_eq!(host.requests, requests, "{}", case["name"]);
            assert_eq!(
                snapshot.variables.get(&90) == Some(&Value::Bool(true)),
                expected["followed"].as_bool().unwrap(),
                "{}",
                case["name"]
            );
            assert_eq!(
                !snapshot.diagnostics.is_empty(),
                expected["failed"].as_bool().unwrap(),
                "{}: {:?}",
                case["name"],
                snapshot.diagnostics
            );
        }
    }

    #[test]
    fn shared_unkeyed_array_contract() {
        fn value(json: &Json) -> Value {
            match json {
                Json::Null => Value::Null,
                Json::Bool(value) => Value::Bool(*value),
                Json::Number(number) => number
                    .as_i64()
                    .map_or_else(|| Value::Float(number.as_f64().unwrap()), Value::Integer),
                Json::Array(items) => Value::Array(items.iter().map(value).collect()),
                Json::Object(fields) if fields.len() == 1 && fields.contains_key("entity") => {
                    Value::Entity(fields["entity"].as_u64().unwrap().try_into().unwrap())
                }
                Json::Object(fields) if fields.len() == 1 && fields.contains_key("float") => {
                    Value::Float(match fields["float"].as_str().unwrap() {
                        "NaN" => f64::NAN,
                        "Infinity" => f64::INFINITY,
                        other => panic!("unsupported float token {other}"),
                    })
                }
                other => panic!("unsupported contract value {other}"),
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-array-insert-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.unkeyed-array-insert.v1");
        assert_eq!(contract["target_build"], 104319);
        assert_eq!(contract["input"]["output_scope"], "entity");
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["states"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let mut extra = json!({});
            for (input, field) in [
                ("source_config", "m_value"),
                ("index_config", "m_index"),
                ("key_config", "m_key"),
            ] {
                if let Some(config) = case.get(input) {
                    extra[field] = config.clone();
                }
            }
            let component = array_insert_fixture(
                value(&case["current"]),
                value(&case["source"]),
                case["index_present"]
                    .as_bool()
                    .unwrap()
                    .then(|| value(&case["index"])),
                case["unique"].as_u64().unwrap() != 0,
                case["single"].as_bool().unwrap(),
                extra,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{}: {:?}",
                case["name"],
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables[&100],
                value(&expected["value"]),
                "{}",
                case["name"]
            );
            assert_eq!(
                snapshot.variables.get(&101) == Some(&Value::Bool(true)),
                expected["followed"].as_bool().unwrap(),
                "{}",
                case["name"]
            );
        }
    }

    #[test]
    fn unkeyed_array_insertion_matches_native_merge_single_value_and_index_rules() {
        let ints =
            |items: &[i64]| Value::Array(items.iter().copied().map(Value::Integer).collect());
        let nested = Value::Array(vec![
            Value::Integer(2),
            Value::Array(vec![Value::Integer(3), Value::Null]),
            Value::Integer(4),
        ]);
        for (initial, source, index, unique, single, expected) in [
            (
                ints(&[1]),
                nested.clone(),
                None,
                false,
                false,
                Value::Array(vec![
                    Value::Integer(1),
                    Value::Integer(2),
                    Value::Integer(3),
                    Value::Null,
                    Value::Integer(4),
                ]),
            ),
            (
                ints(&[1, 5]),
                nested.clone(),
                Some(Value::Integer(1)),
                false,
                true,
                Value::Array(vec![Value::Integer(1), nested, Value::Integer(5)]),
            ),
            (
                Value::Integer(99),
                Value::Integer(7),
                None,
                false,
                false,
                ints(&[7]),
            ),
            (
                ints(&[1, 2, 3]),
                ints(&[7, 8]),
                Some(Value::Integer(1)),
                false,
                false,
                ints(&[1, 7, 8, 2, 3]),
            ),
            (
                ints(&[1, 2]),
                Value::Integer(7),
                Some(Value::Integer(4)),
                false,
                false,
                ints(&[1, 2, 0, 0, 7]),
            ),
            (
                Value::Integer(99),
                Value::Null,
                Some(Value::Integer(3)),
                false,
                false,
                ints(&[0, 0, 0]),
            ),
            (
                ints(&[1]),
                ints(&[0, 2, 2, 3]),
                Some(Value::Integer(3)),
                true,
                false,
                ints(&[1, 0, 0, 2, 3]),
            ),
            (
                ints(&[1]),
                Value::Array(vec![
                    Value::Float(1.00001),
                    Value::Bool(true),
                    Value::Entity(1),
                ]),
                None,
                true,
                false,
                Value::Array(vec![Value::Integer(1), Value::Entity(1)]),
            ),
            (
                ints(&[1, 2, 3]),
                Value::Integer(7),
                Some(Value::Float(1.5)),
                false,
                false,
                ints(&[1, 2, 7, 3]),
            ),
            (
                ints(&[1, 2, 3]),
                Value::Integer(7),
                Some(Value::Float(-0.5)),
                false,
                false,
                ints(&[1, 2, 3, 7]),
            ),
            (
                ints(&[1, 2, 3]),
                Value::Integer(7),
                Some(Value::Float(f64::NAN)),
                false,
                false,
                ints(&[1, 2, 3, 7]),
            ),
            (
                ints(&[1, 2, 3]),
                Value::Integer(7),
                Some(Value::Float(f64::INFINITY)),
                false,
                false,
                ints(&[1, 2, 3, 7]),
            ),
            (
                ints(&[1, 2, 3]),
                Value::Integer(7),
                Some(Value::Null),
                false,
                false,
                ints(&[7, 1, 2, 3]),
            ),
            (
                ints(&[1, 2, 3]),
                Value::Integer(7),
                Some(ints(&[])),
                false,
                false,
                ints(&[1, 2, 3, 7]),
            ),
            (
                ints(&[1]),
                Value::Null,
                None,
                false,
                true,
                Value::Array(vec![Value::Integer(1), Value::Null]),
            ),
        ] {
            let component =
                array_insert_fixture(initial, source, index, unique, single, json!({})).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(snapshot.variables[&100], expected);
            assert_eq!(snapshot.variables[&101], Value::Bool(true));
        }
    }

    #[test]
    fn array_insert_providers_see_prior_rows_and_self_reference_stops_the_iterator() {
        let expression = json!({"$":"STU_919FD47C","m_configVars":null,"m_expression":{
            "m_D99EF254":null,"m_dynamicVars":[var(100,true)],"m_fragments":null,"m_opcodes":[8,0,0]}});
        for (source, expected) in [
            (
                json!({"$":"STU_06610EBC","m_configVars":[7,expression,9]}),
                vec![7, 7, 9],
            ),
            (
                json!({"$":"STU_06610EBC","m_configVars":[7,var(100,true),{"$":"UnknownAfterSelf"}]}),
                vec![7],
            ),
        ] {
            let component = array_insert_fixture(Value::Integer(99),Value::Null,None,false,false,
                json!({"m_value":source,"m_index":{"$":"STU_06610EBC","m_configVars":[-1,{"$":"UnknownSecondIndex"}]}})).unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables[&100],
                Value::Array(expected.into_iter().map(Value::Integer).collect())
            );
            assert_eq!(snapshot.variables[&101], Value::Bool(true));
        }
    }

    #[test]
    fn array_insert_skips_absent_invalid_and_zero_targets_but_keeps_storage_limits_explicit() {
        for extra in [
            json!({"m_26E3B366":null}),
            json!({"m_value":null}),
            json!({"m_26E3B366":{"$":"STUConfigVarInt","m_value":1}}),
            json!({"m_26E3B366":var(0,true)}),
            json!({"m_key":0}),
        ] {
            let component = array_insert_fixture(
                Value::Integer(99),
                Value::Null,
                None,
                false,
                false,
                json!({"m_value":{"$":"UnknownSource"}})
                    .as_object()
                    .unwrap()
                    .iter()
                    .chain(extra.as_object().unwrap().iter())
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect(),
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(snapshot.variables[&100], Value::Integer(99));
            assert_eq!(snapshot.variables[&101], Value::Bool(true));
        }
        for (extra, feature, length) in [
            (
                json!({"m_index":201}),
                "array insertion native limit diagnostic",
                None,
            ),
            (
                json!({"m_index":200}),
                "array insertion native limit diagnostic",
                Some(201),
            ),
            (
                json!({"m_key":{"$":"STUConfigVarIdentifier","m_identifier":"0x0D80000000000011"}}),
                "array insertion keyed row storage",
                None,
            ),
        ] {
            let component = array_insert_fixture(
                Value::Integer(99),
                Value::Integer(7),
                None,
                false,
                false,
                extra,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert_eq!(snapshot.diagnostics.len(), 1, "{:?}", snapshot.diagnostics);
            assert_eq!(
                snapshot.diagnostics[0].issue,
                Issue::UnsupportedField(feature)
            );
            assert!(!snapshot.variables.contains_key(&101));
            match length {
                Some(length) => assert!(
                    matches!(&snapshot.variables[&100],Value::Array(values) if values.len()==length)
                ),
                None => assert_eq!(snapshot.variables[&100], Value::Integer(99)),
            }
        }
    }

    #[test]
    fn array_insert_failed_native_preserves_scalar_defaults_and_single_value_zero() {
        let failed = json!({"$":"STU_7BD2CA19","m_EE729DCB":0,"m_resourceKey":null});
        for (extra, single, expected) in [
            (json!({"m_index":failed.clone()}), false, vec![1, 7]),
            (json!({"m_key":failed.clone()}), false, vec![1]),
            (json!({"m_value":failed.clone()}), true, vec![1, 0]),
            (json!({"m_value":failed}), false, vec![1]),
        ] {
            let component = array_insert_fixture(
                Value::Array(vec![Value::Integer(1)]),
                Value::Integer(7),
                None,
                false,
                single,
                extra,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables[&100],
                Value::Array(expected.into_iter().map(Value::Integer).collect())
            );
            assert_eq!(snapshot.variables[&101], Value::Bool(true));
        }
    }

    #[test]
    fn array_insert_work_depth_and_linked_storage_are_bounded() {
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    "STU_4C3509FB",
                    None,
                    json!({"m_26E3B366":var(100,true),"m_value":var(102,true)}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let mut nested = Value::Integer(7);
        for _ in 0..MAX_PLUG_DEPTH {
            nested = Value::Array(vec![nested]);
        }
        assert!(matches!(
            Component::start(
                Arc::new(store(vec![fixture])),
                123,
                Variables::from([(102, nested)]),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK
            ),
            Err(InitFault::PlugDepth)
        ));
        let component = array_insert_fixture(
            Value::Integer(99),
            Value::Array(vec![Value::Integer(7); 200]),
            None,
            false,
            false,
            json!({}),
        )
        .unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&100],
            Value::Array(vec![Value::Integer(7); 200])
        );
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    "STU_4C3509FB",
                    None,
                    json!({"m_26E3B366":var(100,true),"m_value":var(102,true)}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        assert!(matches!(
            Component::start(
                Arc::new(store(vec![fixture])),
                123,
                Variables::from([(102, Value::Array(vec![Value::Integer(7); 200]))]),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                100
            ),
            Err(InitFault::WorkBudget { .. })
        ));
        let mut component = array_insert_fixture(
            Value::Integer(99),
            Value::Integer(7),
            None,
            false,
            false,
            json!({}),
        )
        .unwrap();
        let target = VarKey::Entity(100);
        component.machine.links.insert(
            target,
            vec![VariableLink {
                state: StateKey {
                    instance: 1,
                    raw: 0,
                },
                priority: StackPriority {
                    level: 0,
                    above_equals: true,
                },
                output: LinkValue::Fixed(Value::Integer(4)),
                stack: true,
            }],
        );
        component
            .run(&mut EmptyWorld, |vm| {
                let node = vm.graph(1).node(1).unwrap();
                assert!(!vm.insert_array(1, node, None, 0)?);
                Ok(())
            })
            .unwrap();
        assert_eq!(
            component
                .snapshot()
                .unwrap()
                .diagnostics
                .last()
                .unwrap()
                .issue,
            Issue::UnsupportedField("array insertion linked variable checkpoint")
        );
    }

    #[test]
    fn ordered_array_removal_keeps_order_and_ignores_missing_or_out_of_range_values() {
        for (values, index, expected) in [
            (
                Value::Array(vec![
                    Value::Integer(1),
                    Value::Integer(2),
                    Value::Integer(3),
                ]),
                1,
                Value::Array(vec![Value::Integer(1), Value::Integer(3)]),
            ),
            (
                Value::Array(vec![Value::Integer(1)]),
                -1,
                Value::Array(vec![Value::Integer(1)]),
            ),
            (
                Value::Array(vec![Value::Integer(1)]),
                9,
                Value::Array(vec![Value::Integer(1)]),
            ),
            (Value::Null, 1, Value::Null),
        ] {
            let fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                    ),
                    node(
                        1,
                        "STU_DA54F8F7",
                        None,
                        json!({"m_26E3B366":var(100,true),"m_index":index,"m_outPlug":plug(&[(2,"m_inPlug")],&[])}),
                    ),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101,true),"m_value":true}),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            let component = Component::start(
                Arc::new(store(vec![fixture])),
                42,
                Variables::from([(100, values)]),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(snapshot.variables[&100], expected);
            assert_eq!(snapshot.variables[&101], Value::Bool(true));
        }
    }

    #[test]
    fn reused_play_script_does_not_replay_once_only_start_entries() {
        let callee = graph(
            43,
            vec![
                node(
                    0,
                    "STU_BB4AC733",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(900,true),"m_value":var(7,false)}),
                ),
                node(
                    2,
                    "STU_BB4AC733",
                    None,
                    json!({"m_FDBDCB70":1,"m_outPlug":plug(&[(3,"m_inPlug")],&[])}),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(901,true),"m_value":var(7,false)}),
                ),
            ],
            json!([]),
            json!([0, 2]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![callee])),
            0xa000_0101,
            Variables::new(),
            vec![],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let site = Site {
            entity: 0xa000_0201,
            instance: 1,
            graph: 42,
            node: None,
        };
        for (policy, value, once) in [(1, 40, 40), (2, 99, 40), (3, 100, 100)] {
            component
                .receive_play_script(
                    site,
                    &PlayScript {
                        policy,
                        overrides: vec![(7, Value::Integer(value))],
                        graph: 43,
                        source: site.entity,
                        parameters: vec![],
                        auto_retire: false,
                    },
                    &mut EmptyWorld,
                )
                .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(snapshot.variables[&900], Value::Integer(value));
            assert_eq!(snapshot.variables[&901], Value::Integer(once));
            assert_eq!(component.machine.disabled_start_entries.len(), 1);
        }
    }

    #[test]
    fn play_script_binds_named_arrays_and_retires_only_after_pending_work() {
        const OWNER: u32 = 0xc000_1000;
        const SOURCE: u32 = 0xc000_1001;
        const REPLAY: u64 = 0x0240_0000_0000_0009;
        let caller = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(2,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000009"},
                           "m_outPlug":plug(&[(2,"m_inPlug")], &[])}),
                ),
                node(
                    2,
                    "STUStatescriptActionPlayScript",
                    None,
                    json!({
                        "m_script":{"$":"STU_105E1BCC","m_graph":"0x058000000000002B","m_resourceKey":null},
                        "m_69F4B52C":{"$":"STU_9DB46B98","m_6525E98B":null},
                        "m_targets":{"$":"STU_B5A0CAF0"},
                        "m_7ABA409C":0,
                        "m_params":[{"m_B5051BCE":"0x0A400000000003A1","m_value":var(37,false)}],
                    }),
                ),
            ],
            json!([]),
            json!([0, 1]),
        );
        let callee = graph(
            43,
            vec![
                node(
                    0,
                    "STU_BB4AC733",
                    None,
                    json!({
                        "m_B60ABDA4":var(28,false),
                        "m_params":[{"m_B5051BCE":"0x0A400000000003A1","m_out_Var":var(37,false)}],
                        "m_outPlug":plug(&[(1,"m_inPlug"),(2,"m_beginPlug")], &[]),
                    }),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(38,false),"m_value":var(37,false)}),
                ),
                node(
                    2,
                    "STUStatescriptStateWait",
                    Some(0),
                    json!({"m_timeout":0.05}),
                ),
                node(
                    3,
                    "STU_BB4AC733",
                    None,
                    json!({"m_5AEA0A51":1,
                           "m_outPlug":plug(&[(4,"m_inPlug")], &[])}),
                ),
                node(
                    4,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(99,false),"m_value":true}),
                ),
            ],
            json!([2]),
            json!([0, 3]),
        );
        let values = Value::Array(vec![Value::Vector([1.0, 2.0, 3.0]), Value::Integer(4)]);
        let mut root = Root::new(1, 42);
        root.variables.insert(SCRIPT_SOURCE, Value::Entity(SOURCE));
        root.variables.insert(37, values.clone());
        let mut component = Component::start(
            Arc::new(store(vec![caller, callee])),
            OWNER,
            Variables::new(),
            vec![root],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let played = &snapshot.instances[&2];
        assert_eq!(played.parent, None);
        assert_eq!(played.variables[&SCRIPT_SOURCE], Value::Entity(SOURCE));
        assert_eq!(played.variables[&28], Value::Entity(OWNER));
        assert_eq!(played.variables[&38], values);
        assert!(!played.variables.contains_key(&99));
        component
            .queue_message(1, REPLAY, Variables::new())
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        assert_eq!(component.snapshot().unwrap().instances.len(), 2);
        component.advance_frame(2, &mut EmptyWorld).unwrap();
        assert!(component.snapshot().unwrap().instances.contains_key(&2));
        component.advance_frame(4, &mut EmptyWorld).unwrap();
        assert!(!component.snapshot().unwrap().instances.contains_key(&2));
        component
            .queue_message(1, REPLAY, Variables::new())
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        assert!(component.snapshot().unwrap().instances.contains_key(&3));
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein PlayScript callers"]
    fn authored_boss_play_script_requests_resolve_the_controller_and_body() {
        const MODE: u32 = 0xd000_0010;
        const BODY: u32 = 0xc000_1000;
        const CONTROLLER: u32 = BODY + 1;
        #[derive(Default)]
        struct Pair(Vec<(Site, u32, PlayScript)>);
        impl World for Pair {
            fn related_entity(
                &mut self,
                _site: Site,
                relation: EntityRelation,
                entity: u32,
            ) -> Option<u32> {
                match (relation, entity) {
                    (EntityRelation::CurrentPossessor, BODY) => Some(CONTROLLER),
                    (EntityRelation::PossessedBody, CONTROLLER) => Some(BODY),
                    _ => None,
                }
            }
            fn play_script(&mut self, site: Site, target: u32, request: PlayScript) -> bool {
                self.0.push((site, target, request));
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let position = Value::Vector([71.5, 18.5, -105.0]);
        let mut world = Pair::default();
        for (graph, owner, call, variables) in [
            (
                0x0cba,
                MODE,
                9,
                Variables::from([
                    (38, Value::Entity(BODY)),
                    (889, Value::Integer(0)),
                    (8370, position.clone()),
                ]),
            ),
            (
                0x0c95,
                CONTROLLER,
                2,
                Variables::from([(SCRIPT_SOURCE, Value::Entity(MODE))]),
            ),
        ] {
            let mut root = Root::listener(1, graph, None);
            root.variables = variables;
            let mut component = Component::start(
                Arc::clone(&graphs),
                owner,
                Variables::new(),
                vec![root],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            component
                .run(&mut world, |vm| {
                    let node = vm.graph(1).node(call).unwrap();
                    assert!(vm.run_node(1, node, None, 0)?);
                    vm.run()
                })
                .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            if graph == 0x0cba {
                assert_eq!(
                    snapshot.instances[&1].variables[&8399],
                    Value::Array(vec![Value::Entity(BODY)])
                );
            }
        }
        assert_eq!(world.0.len(), 2);
        assert_eq!(
            world.0[0],
            (
                Site {
                    entity: MODE,
                    instance: 1,
                    graph: 0x0cba,
                    node: Some(9)
                },
                CONTROLLER,
                PlayScript {
                    policy: 0,
                    overrides: Vec::new(),
                    graph: 0x0c95,
                    source: MODE,
                    parameters: vec![(0x0a40_0000_0000_03a1, Value::Array(vec![position]))],
                    auto_retire: true,
                }
            )
        );
        assert_eq!(
            world.0[1],
            (
                Site {
                    entity: CONTROLLER,
                    instance: 1,
                    graph: 0x0c95,
                    node: Some(2)
                },
                BODY,
                PlayScript {
                    policy: 0,
                    overrides: Vec::new(),
                    graph: 0x0cbc,
                    source: MODE,
                    parameters: vec![],
                    auto_retire: true,
                }
            )
        );
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein Boss intro graphs"]
    fn authored_boss_pause_keeps_the_receiver_until_the_last_sender_retires() {
        #[derive(Default)]
        struct Aim(Vec<(Site, bool)>);
        impl World for Aim {
            fn set_cinematic_aim_lock(&mut self, site: Site, active: bool) -> bool {
                self.0.push((site, active));
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = Aim::default();
        let mut component = Component::start(
            graphs,
            0xc000_1000,
            Variables::new(),
            vec![
                Root::listener(1, 0x0ce0, Some(8)),
                Root::listener(2, 0x0cbc, Some(56)),
            ],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.refresh_boss_pause(&mut world, false).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert!(snapshot.boss_pause_active());
        assert!(snapshot.instances[&2].states[&57].active);
        assert_eq!(world.0.len(), 1);
        assert_eq!(world.0[0].0.node, Some(45));
        assert!(world.0[0].1);
        let codecs = crate::script_codecs::CodecTable::load(
            runtime.join("data/statescript_codecs_174.json"),
        )
        .unwrap();
        let encoder =
            crate::script_snapshot_wire::SnapshotEncoder::new(component.graph_store(), &codecs);
        encoder
            .full(
                &encoder
                    .project(snapshot, crate::script_snapshot_wire::View::Observer)
                    .unwrap(),
            )
            .unwrap();

        component.run(&mut world, |vm| vm.exit_instance(1)).unwrap();
        component.refresh_boss_pause(&mut world, true).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(!snapshot.boss_pause_active());
        assert!(snapshot.instances[&2].states[&57].active);
        assert_eq!(snapshot.instances[&2].states[&57].activation, 1);
        component.refresh_boss_pause(&mut world, false).unwrap();
        assert!(!component.snapshot().unwrap().instances[&2].states[&57].active);
        assert_eq!(world.0, [(world.0[0].0, true), (world.0[0].0, false)]);
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein Boss intro graph"]
    fn authored_boss_body_voice_waits_and_cancels_with_the_instance() {
        #[derive(Default)]
        struct Voice(Vec<(Site, IntroCue)>);
        impl World for Voice {
            fn play_boss_intro_voice(&mut self, site: Site, cue: IntroCue) -> Option<u64> {
                self.0.push((site, cue));
                Some(3591)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = Voice::default();
        let mut component = Component::start(
            graphs,
            0xc000_1000,
            Variables::new(),
            vec![
                Root::listener(1, 0x0ce0, Some(49)),
                Root::listener(2, 0x0ce0, Some(49)),
            ],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.advance_frame(11, &mut world).unwrap();
        assert!(world.0.is_empty());
        component.run(&mut world, |vm| vm.exit_instance(2)).unwrap();
        component.advance_frame(12, &mut world).unwrap();
        let &[(site, cue)] = world.0.as_slice() else {
            panic!("expected one Boss voice submission")
        };
        assert_eq!(site.instance, 1);
        assert_eq!(site.node, Some(77));
        assert_eq!(cue.stimulus, 0x0ee0_0000_0000_06bd);
        assert!(!cue.narrator);
        let snapshot = component.snapshot().unwrap();
        assert!(snapshot.instances[&1].states[&49].active);
        let codecs = crate::script_codecs::CodecTable::load(
            runtime.join("data/statescript_codecs_174.json"),
        )
        .unwrap();
        let encoder =
            crate::script_snapshot_wire::SnapshotEncoder::new(component.graph_store(), &codecs);
        for view in [
            crate::script_snapshot_wire::View::Owner,
            crate::script_snapshot_wire::View::Observer,
        ] {
            encoder
                .full(&encoder.project(snapshot, view).unwrap())
                .unwrap();
        }
        component.advance_frame(237, &mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(world.0.len(), 1);
        assert!(!snapshot.instances[&1].states[&49].active);
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein wave body graph"]
    fn authored_boss_team_contact_releases_on_end_and_instance_retirement() {
        #[derive(Default)]
        struct Contacts(Vec<(Site, bool)>);
        impl World for Contacts {
            fn set_same_team_contact_suppressed(&mut self, site: Site, suppressed: bool) -> bool {
                self.0.push((site, suppressed));
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let body = 0xc000_1000;
        let mut world = Contacts::default();
        let mut component = Component::start(
            graphs,
            body,
            Variables::new(),
            vec![Root::listener(1, 0x0cbc, Some(52))],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let key = StateKey {
            instance: 1,
            raw: 52,
        };
        let site = Site {
            entity: body,
            instance: 1,
            graph: 0x0cbc,
            node: Some(129),
        };
        assert_eq!(world.0, [(site, true)]);
        component
            .run(&mut world, |vm| vm.end(key, false, 0))
            .unwrap();
        assert_eq!(world.0, [(site, true), (site, false)]);
        assert_eq!(
            component.snapshot().unwrap().instances[&1].states[&52].kind,
            StateKind::SameTeamContact { registered: false }
        );
        component.run(&mut world, |vm| vm.begin(key, 0)).unwrap();
        component.run(&mut world, |vm| vm.exit_instance(1)).unwrap();
        assert_eq!(
            world.0,
            [(site, true), (site, false), (site, true), (site, false)]
        );
        assert!(component.snapshot().unwrap().initialization_complete());
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein boss intro graphs"]
    fn authored_boss_facing_polls_and_retires_with_its_state() {
        const BODY: u32 = 0xc000_1000;
        const ANCHOR: u32 = 0x8000_0001;
        struct Facing {
            forward: [f64; 3],
            updates: Vec<(AiFacing, bool)>,
        }
        impl World for Facing {
            fn related_entity(
                &mut self,
                _: Site,
                relation: EntityRelation,
                owner: u32,
            ) -> Option<u32> {
                (relation == EntityRelation::PossessedBody && owner == BODY + 1).then_some(BODY)
            }
            fn forward(&self, entity: u32) -> Option<[f64; 3]> {
                (entity == ANCHOR).then_some(self.forward)
            }
            fn ai_facing(&mut self, _: Site, record: AiFacing, active: bool) -> Option<bool> {
                assert_eq!(record.target, BODY);
                self.updates.push((record, active));
                Some(true)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = Facing {
            forward: [0.0, 0.0, 1.0],
            updates: Vec::new(),
        };
        let mut root = Root::listener(1, 0x0c95, Some(2));
        root.variables
            .insert(8370, Value::Array(vec![Value::Entity(ANCHOR)]));
        let mut component = Component::start(
            graphs,
            BODY + 1,
            Variables::new(),
            vec![root],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert_eq!(world.updates[0].0.direction[2], -1.0);
        world.forward = [1.0, 0.0, 0.0];
        component.advance_frame(1, &mut world).unwrap();
        let (current, active) = world.updates[1];
        assert!(active);
        assert!((current.direction[0] + 1.0).abs() < 1e-6);
        let key = StateKey {
            instance: 1,
            raw: 2,
        };
        component
            .run(&mut world, |vm| vm.end(key, false, 0))
            .unwrap();
        assert_eq!(world.updates[2], (current, false));
        component.run(&mut world, |vm| vm.begin(key, 0)).unwrap();
        assert_eq!(world.updates[3], (current, true));
        component.run(&mut world, |vm| vm.exit_instance(1)).unwrap();
        assert_eq!(world.updates[4], (current, false));
        assert!(component.snapshot().unwrap().initialization_complete());
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein boss intro graphs"]
    fn authored_boss_control_links_active_instances_and_releases_the_model() {
        struct Model(Vec<bool>);
        impl World for Model {
            fn set_encounter_model_enabled(&mut self, _: Site, enabled: bool) -> bool {
                self.0.push(enabled);
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = Model(Vec::new());
        let mut component = Component::start(
            Arc::clone(&graphs),
            0xc000_1000,
            Variables::new(),
            vec![
                Root::listener(1, 0x0ce0, Some(10)),
                Root::listener(2, 0x0d03, Some(1)),
            ],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(world.0, [false, false]);
        for (source, successor) in [(10, 1), (20, 4), (21, 3), (27, 0)] {
            assert_eq!(
                snapshot.instances[&1].states[&source].kind,
                StateKind::Registered {
                    next: Some((2, successor))
                }
            );
            assert!(snapshot.instances[&2].states[&successor].active);
        }
        assert!(snapshot.instances[&1].states[&31].active);
        assert!(snapshot.instances[&2].states[&12].active);
        let codecs = crate::script_codecs::CodecTable::load(
            runtime.join("data/statescript_codecs_174.json"),
        )
        .unwrap();
        let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(&graphs, &codecs);
        for view in [
            crate::script_snapshot_wire::View::Owner,
            crate::script_snapshot_wire::View::Observer,
        ] {
            let projected = encoder.project(snapshot, view).unwrap();
            encoder.full(&projected).unwrap();
        }
        component.run(&mut world, |vm| vm.exit_instance(2)).unwrap();
        assert_eq!(world.0, [false, false]);
        for source in [10, 20, 21, 27] {
            assert_eq!(
                component.snapshot().unwrap().instances[&1].states[&source].kind,
                StateKind::Registered { next: None }
            );
        }
        component
            .run(&mut world, |vm| {
                vm.end(
                    StateKey {
                        instance: 1,
                        raw: 10,
                    },
                    false,
                    0,
                )
            })
            .unwrap();
        assert_eq!(world.0, [false, false, true]);
        assert!(active(component.snapshot().unwrap(), 1).is_empty());
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein wave health graph"]
    fn authored_boss_wave_health_scales_with_difficulty_and_retires() {
        struct Health {
            vitals: crate::vitals::Vitals,
            difficulty: i64,
        }
        impl World for Health {
            fn local_combat_context_available(&self, _: Site) -> Option<bool> {
                Some(false)
            }
            fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
                (entity == crate::world::GAME_MODE_ENTITY).then_some({
                    if id == 9948 {
                        Value::Integer(self.difficulty)
                    } else {
                        Value::Null
                    }
                })
            }
            fn body_health(&self, _: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
                Some(self.vitals.amount(kind, maximum))
            }
            fn add_wave_health(&mut self, _: Site, amount: f64) -> Option<Option<u32>> {
                self.vitals.add_wave_health(amount).map(Some)
            }
            fn remove_health_pool(&mut self, _: Site, pool: u32) -> Option<f64> {
                self.vitals.remove_pool(pool)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
        }
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let tables: [(u16, [f64; 4]); 6] = [
            (0, [750.0, 1000.0, 1350.0, 1750.0]),
            (1, [3200.0, 3600.0, 3800.0, 4000.0]),
            (2, [2000.0, 2500.0, 2800.0, 3000.0]),
            (8, [1400.0, 1500.0, 1900.0, 2300.0]),
            (13, [0.0, 100.0, 100.0, 100.0]),
            (47, [1400.0, 1600.0, 1900.0, 2200.0]),
        ];
        let maximum = graphs
            .graph(0x0cbc)
            .unwrap()
            .node(53)
            .unwrap()
            .field("m_condition.m_configVars[1]")
            .unwrap();
        for (raw, table) in tables {
            for (difficulty, bonus) in table.into_iter().enumerate() {
                let mut world = Health {
                    vitals: crate::vitals::Vitals::new(250.0, 0.0, 0.0),
                    difficulty: difficulty as i64,
                };
                let mut root = Root::listener(1, 0x0cbc, None);
                root.variables
                    .insert(9948, Value::Integer(difficulty as i64));
                let mut component = Component::start(
                    Arc::clone(&graphs),
                    0xc000_1000,
                    Variables::from([(HUD_MAX_HEALTH, Value::Float(250.0))]),
                    vec![root],
                    0,
                    &mut world,
                    MAX_INIT_WORK,
                )
                .unwrap();
                // Isolate the real health handler from the wave's later presentation states.
                component
                    .run(&mut world, |vm| {
                        let node = vm.graph(1).state(usize::from(raw)).unwrap();
                        assert!(is_wave_health_pool(node));
                        vm.machine.snapshot.instances[&1].states.insert(
                            raw,
                            State {
                                active: true,
                                ..State::dormant(StateKind::HealthPool { pool: None })
                            },
                        );
                        vm.begin_health_pool(StateKey { instance: 1, raw }, node)
                    })
                    .unwrap();
                let snapshot = component.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                assert_eq!(world.vitals.maximum(), 250.0 + bonus);
                assert_eq!(world.vitals.total(), 250.0 + bonus);
                assert_eq!(
                    component.evaluate_state_config(1, raw, maximum, &mut world),
                    Ok(Value::Float(250.0 + bonus))
                );
                assert_eq!(
                    snapshot.variables[&HUD_MAX_HEALTH],
                    Value::Float(250.0 + bonus)
                );
                component
                    .run(&mut world, |vm| {
                        vm.end(StateKey { instance: 1, raw }, false, 0)
                    })
                    .unwrap();
                assert_eq!(world.vitals.maximum(), 250.0);
                assert_eq!(world.vitals.total(), 250.0);
            }
        }
    }

    fn recent_input_fixture() -> Component {
        let entries = [(1, "m_beginPlug"), (2, "m_F4DD996D"), (3, ABORT_PLUG)]
            .map(|(position, input)| node(position, "STUStatescriptEntryGameMessage", None,
                json!({"m_gameMessage":{"m_gameMessage":format!("0x024000000000{position:04X}")},
                    "m_outPlug":plug(&[(0,input)], &[])})));
        let mut nodes = vec![node(
            0,
            "STU_5E805B5A",
            Some(0),
            json!({
                "m_C51AF683":var(101,true), "m_82317B5F":var(103,true),
                "m_814F8EC3":var(100,true), "m_1405CDCB":plug(&[(4,"m_inPlug")], &[]),
            }),
        )];
        nodes.extend(entries);
        nodes.push(node(
            4,
            "STUStatescriptActionSetVar",
            None,
            json!({"m_out_Var":var(102,true),"m_value":true}),
        ));
        Component::start(
            Arc::new(store(vec![graph(
                0x0cba,
                nodes,
                json!([0]),
                json!([1, 2, 3]),
            )])),
            42,
            Variables::from([(103, Value::Float(1.0))]),
            vec![Root::new(1, 0x0cba)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap()
    }

    fn recent_input_message(component: &mut Component, message: u64) {
        component
            .queue_message(1, 0x0240_0000_0000_0000 | message, Variables::new())
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
    }

    #[test]
    fn recent_input_avoids_history_expires_and_resets_on_reentry() {
        let mut component = recent_input_fixture();
        let positions = (0..3)
            .map(|i| Value::Vector([f64::from(i), 0.0, 0.0]))
            .collect::<Vec<_>>();
        component
            .set_entity_variable(100, Value::Array(positions.clone()), &mut EmptyWorld)
            .unwrap();
        recent_input_message(&mut component, 2);
        assert!(!component.snapshot().unwrap().variables.contains_key(&101));
        recent_input_message(&mut component, 1);
        let mut selected = Vec::new();
        for _ in 0..3 {
            recent_input_message(&mut component, 2);
            let snapshot = component.snapshot().unwrap();
            let position = snapshot.variables[&101].clone();
            assert!(positions.contains(&position));
            assert!(!selected.contains(&position));
            selected.push(position);
            assert_eq!(snapshot.variables[&102], Value::Bool(true));
        }
        recent_input_message(&mut component, 2);
        assert_eq!(component.snapshot().unwrap().variables[&101], selected[0]);
        component.advance_frame(67, &mut EmptyWorld).unwrap();
        recent_input_message(&mut component, 2);
        assert_eq!(
            component.snapshot().unwrap().instances[&1].states[&0]
                .recent_inputs
                .len(),
            1
        );
        recent_input_message(&mut component, 3);
        recent_input_message(&mut component, 1);
        assert!(
            component.snapshot().unwrap().instances[&1].states[&0]
                .recent_inputs
                .is_empty()
        );
    }

    #[test]
    fn recent_input_handles_shrinking_empty_and_zero_lifetime_candidates() {
        let mut component = recent_input_fixture();
        recent_input_message(&mut component, 1);
        let positions = (0..5)
            .map(|i| Value::Vector([f64::from(i), 0.0, 0.0]))
            .collect::<Vec<_>>();
        component
            .set_entity_variable(100, Value::Array(positions), &mut EmptyWorld)
            .unwrap();
        for _ in 0..5 {
            recent_input_message(&mut component, 2);
        }
        let only = Value::Vector([9.0, 0.0, 0.0]);
        component
            .set_entity_variable(100, Value::Array(vec![only.clone()]), &mut EmptyWorld)
            .unwrap();
        recent_input_message(&mut component, 2);
        assert_eq!(component.snapshot().unwrap().variables[&101], only);
        component
            .set_entity_variable(100, Value::Array(vec![]), &mut EmptyWorld)
            .unwrap();
        recent_input_message(&mut component, 2);
        assert_eq!(component.snapshot().unwrap().variables[&101], Value::Null);
        component
            .set_entity_variable(103, Value::Float(-1.0), &mut EmptyWorld)
            .unwrap();
        component
            .set_entity_variable(100, Value::Array(vec![only.clone()]), &mut EmptyWorld)
            .unwrap();
        recent_input_message(&mut component, 2);
        assert_eq!(component.snapshot().unwrap().variables[&101], only);
        assert!(
            component.snapshot().unwrap().instances[&1].states[&0]
                .recent_inputs
                .is_empty()
        );
        component
            .set_entity_variable(103, Value::Float(0.0), &mut EmptyWorld)
            .unwrap();
        recent_input_message(&mut component, 2);
        assert!(
            component.snapshot().unwrap().instances[&1].states[&0]
                .recent_inputs
                .is_empty()
        );
    }

    #[test]
    fn bridge_forwards_input_only_between_begin_and_abort() {
        let message = |position, input| {
            node(
                position,
                "STUStatescriptEntryGameMessage",
                None,
                json!({
                    "m_gameMessage":{"m_gameMessage":format!("0x024000000000{position:04X}")},
                    "m_outPlug":plug(&[(0,input)], &[]),
                }),
            )
        };
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STU_7B9E8909",
                    Some(0),
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(100,true), "m_value":true}),
                ),
                message(2, "m_inPlug"),
                message(3, "m_beginPlug"),
                message(4, ABORT_PLUG),
            ],
            json!([0]),
            json!([2, 3, 4]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        for (message, active, activation, forwarded) in [
            (2, false, 0, false),
            (3, true, 1, false),
            (2, true, 1, true),
            (2, true, 1, true),
            (4, false, 1, false),
            (2, false, 1, false),
        ] {
            component
                .set_entity_variable(100, Value::Bool(false), &mut EmptyWorld)
                .unwrap();
            component
                .queue_message(1, 0x0240_0000_0000_0000 | message, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            let snapshot = component.snapshot().unwrap();
            let state = &snapshot.instances[&1].states[&0];
            assert_eq!((state.active, state.activation), (active, activation));
            assert_eq!(snapshot.variables[&100], Value::Bool(forwarded));
            assert!(snapshot.initialization_complete());
        }
    }

    #[test]
    fn client_subgraph_controller_keeps_its_branch_off_the_server() {
        for client_only in [true, false] {
            let mut target = node(2, "STUStatescriptState", Some(1), json!({}));
            target["client_only"] = json!(client_only);
            let graphs = store(vec![graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1, "m_beginPlug")], &[])}),
                    ),
                    node(
                        1,
                        "STU_BD02E168",
                        Some(0),
                        json!({"m_5B10D01F":{
                            "$":"STU_904BFCEC","links":[[2, "m_beginPlug"]],"m_FF3DAF1E":[1]
                        }}),
                    ),
                    target,
                ],
                json!([1, 2]),
                json!([0]),
            )]);
            let snapshot = initialize(
                &graphs,
                0xa000_0100,
                Variables::new(),
                vec![Root::new(1, 42)],
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let states = &snapshot.instances[&1].states;
            assert!(!states.contains_key(&1));
            if client_only {
                assert!(snapshot.initialization_complete());
                assert!(states[&0].active);
                assert_eq!(states[&0].kind, StateKind::Plain);
            } else {
                assert!(!states.contains_key(&0));
                assert!(matches!(
                    snapshot.diagnostics.as_slice(),
                    [Diagnostic { issue: Issue::UnsupportedState(class), .. }]
                        if class == "STU_BD02E168"
                ));
            }
        }
    }

    #[test]
    fn networked_for_code_state_keeps_its_subgraph_lifetime() {
        for (server_only, flags) in [(false, 0), (true, 0), (false, 1)] {
            let mut for_code = node(
                1,
                "STU_CD46AF93",
                Some(0),
                json!({
                    "m_DC897C87":1, "m_598E32D5":flags, "m_0582B0E4":0,
                    "m_values":[{
                        "$":"STUStatescriptForCodeValue",
                        "m_identifier":"0x0D80000000000042",
                        "m_value":var(100,true),
                    }],
                    "m_subgraphPlug":plug(&[(2,"m_beginPlug")], &[1]),
                }),
            );
            for_code["remote"] = json!(0);
            for_code["server_only"] = json!(server_only);
            let mut fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")], &[])}),
                    ),
                    for_code,
                    node(2, "STUStatescriptState", Some(1), json!({})),
                    node(
                        3,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                            "m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                            "m_outPlug":plug(&[(1,ABORT_PLUG)], &[]),
                        }),
                    ),
                ],
                json!([1, 2]),
                json!([0, 3]),
            );
            fixture["remote"] = json!([1]);
            let mut component = Component::start(
                Arc::new(store(vec![fixture])),
                42,
                Variables::from([(100, Value::Integer(5))]),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            let states = &snapshot.instances[&1].states;
            if server_only || flags != 0 {
                assert!(states.is_empty());
                assert!(matches!(
                    snapshot.diagnostics.as_slice(),
                    [Diagnostic { issue: Issue::UnsupportedState(class), .. }]
                        if class == "STU_CD46AF93"
                ));
                continue;
            }
            assert!(snapshot.initialization_complete());
            assert!(states[&0].active && states[&1].active);
            assert_eq!(states[&0].kind, StateKind::ForCode);
            assert!(!snapshot.variables.contains_key(&0x42));
            component
                .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            let snapshot = component.snapshot().unwrap();
            let states = &snapshot.instances[&1].states;
            assert!(!states[&0].active && !states[&1].active);
        }
    }

    #[test]
    fn for_code_values_preserve_first_match_swap_removal_and_deferred_observer_deduplication() {
        #[derive(Default)]
        struct CodeWorld(Vec<u64>);
        impl World for CodeWorld {
            fn for_code_transport(&self, _: Site) -> Option<bool> {
                Some(false)
            }
            fn code_changed(&mut self, _: u32, token: u64) -> bool {
                self.0.push(token);
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let a = 0x0d80_0000_0000_0001;
        let b = 0x0d80_0000_0000_0002;
        let row = |id: u64, value: Json| json!({"$":"STUStatescriptForCodeValue","m_identifier":format!("0x{id:016X}"),"m_value":value});
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug"),(2,"m_beginPlug"),(3,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STU_6A926B21",
                    Some(0),
                    json!({"m_DC897C87":1,"m_values":[row(a,Json::Null)]}),
                ),
                node(
                    2,
                    "STU_6A926B21",
                    Some(1),
                    json!({"m_DC897C87":1,"m_values":[row(b,json!({"$":"STUConfigVarBool","m_value":true})),row(a,var(100,true))]}),
                ),
                node(
                    3,
                    "STU_6A926B21",
                    Some(2),
                    json!({"m_DC897C87":1,"m_values":[row(a,json!({"$":"STUConfigVarFloat","m_value":9}))]}),
                ),
                node(
                    4,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},"m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                ),
            ],
            json!([1, 2, 3]),
            json!([0, 4]),
        );
        let mut world = CodeWorld::default();
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::from([(100, Value::Float(4.0))]),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
        assert_eq!(
            component
                .code_value(a, Value::Integer(7), &mut world)
                .unwrap(),
            (true, Value::Integer(7))
        );
        assert_eq!(
            component
                .code_value(b, Value::Integer(7), &mut world)
                .unwrap(),
            (true, Value::Bool(true))
        );
        assert_eq!(
            component
                .code_value(a + 10, Value::Integer(7), &mut world)
                .unwrap(),
            (false, Value::Null)
        );
        for observer in [(1, None), (2, Some(a)), (3, Some(b)), (1, Some(a))] {
            component.register_code_observer(observer.0, observer.1);
        }
        component.advance_frame(1, &mut world).unwrap();
        assert_eq!(world.0, [1, 2, 3]);
        world.0.clear();
        component
            .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(world.0, [1, 2]);
        assert_eq!(
            component.code_value(a, Value::Null, &mut world).unwrap(),
            (true, Value::Float(9.0))
        );
        component.unregister_code_observer(1);
        world.0.clear();
        component.advance_frame(2, &mut world).unwrap();
        world.0.sort();
        assert_eq!(world.0, [2, 3]);
        component.teardown(&mut world).unwrap();
        assert!(component.machine.code_states.is_empty());
    }

    #[test]
    fn state_elapsed_condition_rearms_one_timer_and_completes_at_the_authored_age() {
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptState",
                    Some(0),
                    json!({"m_transitionPlug":[plug(&[(2,"m_inPlug")],&[])]}),
                ),
                node(
                    2,
                    "STU_4E2BCAAB",
                    None,
                    json!({"m_AA6E611E":{"$":"STUConfigVarFloat","m_value":0.03},"m_truePlug":plug(&[(3,"m_inPlug")],&[])}),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(100,true),"m_value":true}),
                ),
                node(
                    4,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
            ],
            json!([1]),
            json!([0, 4]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        for _ in 0..2 {
            component
                .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
        }
        let timers = &component.snapshot().unwrap().pending_timers[&1];
        assert_eq!(timers.len(), 1);
        assert_eq!(timers[0].due_ms, 30);
        assert_eq!(timers[0].parameter, 0);
        component.advance_frame(1, &mut EmptyWorld).unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&100],
            Value::Bool(true)
        );
        assert!(!component.snapshot().unwrap().instances[&1].states[&0].active);
        assert!(component.snapshot().unwrap().pending_timers.is_empty());
    }

    #[test]
    fn movement_sample_updates_only_changed_authored_outputs() {
        struct SampleWorld(Option<u16>);
        impl World for SampleWorld {
            fn movement_sample(&self, _: Site) -> Option<u16> {
                self.0
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({
                        "m_outPlug":plug(&[(1,"m_beginPlug")], &[])
                    }),
                ),
                node(
                    1,
                    "STU_96F1DBCF",
                    Some(0),
                    json!({
                        "m_49F4B33C":var(100,true),
                        "m_103A8581":var(101,false),
                        "m_subgraphPlug":plug(&[(2,"m_beginPlug")], &[1])
                    }),
                ),
                node(
                    2,
                    "STUStatescriptStateBooleanSwitch",
                    Some(1),
                    json!({
                        "m_condition":var(100,true)
                    }),
                ),
            ],
            json!([1, 2]),
            json!([0]),
        );
        let graphs = Arc::new(store(vec![fixture]));
        let start = |world: &mut SampleWorld| {
            Component::start(
                Arc::clone(&graphs),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                world,
                MAX_INIT_WORK,
            )
            .unwrap()
        };
        let mut world = SampleWorld(Some(2));
        let mut component = start(&mut world);
        let snapshot = component.snapshot().unwrap();
        assert!(snapshot.initialization_complete());
        let owner = &snapshot.instances[&1];
        assert_eq!(owner.states[&0].kind, StateKind::MovementSample(2));
        assert_eq!(owner.states[&1].kind, StateKind::BooleanSwitch(true));
        assert_eq!(snapshot.variables[&100], Value::Bool(true));
        assert!(!owner.variables.contains_key(&101));

        world.0 = Some(1 | (1 << 6));
        component.advance_frame(1, &mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        let owner = &snapshot.instances[&1];
        assert_eq!(owner.states[&0].kind, StateKind::MovementSample(65));
        assert_eq!(owner.states[&1].kind, StateKind::BooleanSwitch(false));
        assert_eq!(snapshot.variables[&100], Value::Bool(false));
        assert_eq!(owner.variables[&101], Value::Bool(true));

        world.0 = Some(0);
        component.advance_frame(2, &mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(
            snapshot.instances[&1].states[&0].kind,
            StateKind::MovementSample(0)
        );
        assert_eq!(snapshot.instances[&1].variables[&101], Value::Bool(false));

        let unsupported = start(&mut SampleWorld(None));
        assert!(matches!(
            unsupported.snapshot().unwrap().diagnostics.as_slice(),
            [Diagnostic {
                issue: Issue::UnsupportedField("owner movement sample"),
                ..
            }]
        ));
    }

    #[test]
    fn instance_exit_drains_in_order_and_finishes_the_linked_parent() {
        let entry = |links: &[(u16, &str)]| {
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(links, &[])}),
            )
        };
        let mark = |position, identifier| {
            node(
                position,
                "STUStatescriptActionSetVar",
                None,
                json!({
                    "m_out_Var":var(identifier,true), "m_value":true
                }),
            )
        };
        let subscript = |position, raw, child: u16, keep_parent| {
            node(
                position,
                "STU_37D754C8",
                Some(raw),
                json!({
                    "m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{
                        "m_graph":format!("0x058000000000{child:04X}")
                    }}},
                    "m_24AB04BB":keep_parent
                }),
            )
        };
        let child = graph(
            43,
            vec![
                entry(&[
                    (1, "m_beginPlug"),
                    (2, "m_beginPlug"),
                    (3, "m_inPlug"),
                    (4, "m_beginPlug"),
                ]),
                node(
                    1,
                    "STUStatescriptStateWait",
                    Some(0),
                    json!({
                        "m_timeout":{"$":"STUConfigVarFloat","m_value":1.0},
                        "m_onBeginPlug":plug(&[(5,"m_inPlug")], &[]),
                        "m_onAbortPlug":plug(&[(6,"m_inPlug")], &[]),
                        "m_onEndPlug":plug(&[(6,"m_inPlug")], &[])
                    }),
                ),
                subscript(2, 1, 44, false),
                node(
                    3,
                    "STU_22926252",
                    None,
                    json!({"m_outPlug":plug(&[(7,"m_inPlug")], &[])}),
                ),
                node(
                    4,
                    "STUStatescriptState",
                    Some(2),
                    json!({"m_onBeginPlug":plug(&[(8,"m_inPlug")], &[])}),
                ),
                mark(5, 100),
                mark(6, 101),
                mark(7, 102),
                mark(8, 103),
            ],
            json!([1, 2, 4]),
            json!([0]),
        );
        let grandchild = graph(
            44,
            vec![entry(&[(1, "m_inPlug")]), mark(1, 104)],
            json!([]),
            json!([0]),
        );
        for keep_parent in [false, true] {
            let mut owner = subscript(1, 0, 43, keep_parent);
            owner["fields"]["m_onFinishedPlug"] = plug(&[(2, "m_inPlug")], &[]);
            owner["fields"]["m_onAbortPlug"] = plug(&[(3, "m_inPlug")], &[]);
            let parent = graph(
                42,
                vec![
                    entry(&[(1, "m_beginPlug")]),
                    owner,
                    mark(2, 105),
                    mark(3, 106),
                ],
                json!([1]),
                json!([0]),
            );
            let snapshot = initialize(
                &store(vec![parent, child.clone(), grandchild.clone()]),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(snapshot.instances.keys().copied().collect::<Vec<_>>(), [1]);
            assert!(snapshot.pending_timers.is_empty());
            let state = &snapshot.instances[&1].states[&0];
            assert_eq!(state.active, keep_parent);
            assert_eq!(state.kind, StateKind::SubScript { child: None });
            let mut expected =
                Variables::from([(100, Value::Bool(true)), (102, Value::Bool(true))]);
            if !keep_parent {
                expected.insert(105, Value::Bool(true));
            }
            assert_eq!(snapshot.variables, expected);
        }
    }

    #[test]
    fn target_tag_releases_on_abort_and_subscript_teardown() {
        #[derive(Default)]
        struct TagWorld(Vec<(TargetTagEffect, bool)>);

        impl World for TagWorld {
            fn set_target_tag_effect(
                &mut self,
                _: Site,
                effect: TargetTagEffect,
                active: bool,
            ) -> bool {
                self.0.push((effect, active));
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        const ABORT: u64 = 0x0240_0000_0000_0002;
        const RESTART: u64 = 0x0240_0000_0000_0003;
        let entry = |position, message: Option<u64>, input| {
            let (class, mut fields) = match message {
                Some(message) => (
                    "STUStatescriptEntryGameMessage",
                    json!({"m_gameMessage":{"m_gameMessage":format!("0x{message:016X}")}}),
                ),
                None => ("STUStatescriptEntry", json!({})),
            };
            fields["m_outPlug"] = plug(&[(1, input)], &[]);
            node(position, class, None, fields)
        };
        let parent = graph(
            42,
            vec![
                entry(0, None, "m_beginPlug"),
                node(
                    1,
                    "STU_37D754C8",
                    Some(0),
                    json!({
                        "m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{"m_graph":"0x058000000000002B"}}}
                    }),
                ),
                entry(2, Some(ABORT), ABORT_PLUG),
            ],
            json!([1]),
            json!([0, 2]),
        );
        let child = graph(
            43,
            vec![
                entry(0, None, "m_beginPlug"),
                node(
                    1,
                    "STU_E56BA8D8",
                    Some(0),
                    json!({
                        "m_31714AFC":{"$":"STU_B5A0CAF0"},
                        "m_44D1BE94":{"$":"STU_357DABBB","m_7D7C86A1":"0x0D60000000000042"}
                    }),
                ),
                entry(2, Some(ABORT), ABORT_PLUG),
                entry(3, Some(RESTART), "m_beginPlug"),
            ],
            json!([1]),
            json!([0, 2, 3]),
        );
        let effect = TargetTagEffect {
            entity: 42,
            tag: 0x0d60_0000_0000_0042,
            present: false,
        };
        let mut world = TagWorld::default();
        let mut component = Component::start(
            Arc::new(store(vec![parent, child])),
            effect.entity,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
        assert_eq!(world.0, [(effect, true)]);

        for (id, message) in [(2, ABORT), (2, RESTART), (1, ABORT)] {
            assert_eq!(
                component
                    .queue_message(id, message, Variables::new())
                    .unwrap(),
                1
            );
            component.run_now(&mut world).unwrap();
        }
        assert!(!component.snapshot().unwrap().instances.contains_key(&2));
        assert_eq!(
            world.0,
            [
                (effect, true),
                (effect, false),
                (effect, true),
                (effect, false)
            ]
        );
    }

    #[test]
    fn display_name_binds_and_releases_with_the_authored_state() {
        #[derive(Default)]
        struct NameWorld(Vec<(u32, u64, bool)>);
        impl World for NameWorld {
            fn entity_component92(&self, _: Site, _: u32) -> Option<bool> {
                Some(true)
            }
            fn set_display_name(&mut self, _: Site, entity: u32, name: u64, active: bool) -> bool {
                self.0.push((entity, name, active));
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        const ABORT: u64 = 0x0240_0000_0000_0002;
        let mut graph = graph(
            0x0cf3,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(22,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({
                        "m_gameMessage":{"m_gameMessage":format!("0x{ABORT:016X}")},
                        "m_outPlug":plug(&[(22,ABORT_PLUG)],&[])
                    }),
                ),
                node(
                    22,
                    "STU_B7D2C9C0",
                    Some(0),
                    json!({
                        "m_14EDDDA7":{"$":"STU_B5A0CAF0"},
                        "m_overrideName":"0x0DE0000000007106"
                    }),
                ),
            ],
            json!([22]),
            json!([0, 1]),
        );
        graph["items"] = json!(23);
        let mut world = NameWorld::default();
        let mut component = Component::start(
            Arc::new(store(vec![graph])),
            42,
            Variables::new(),
            vec![Root::new(1, 0x0cf3)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
        assert_eq!(world.0, [(42, 0x0de0_0000_0000_7106, true)]);
        assert_eq!(
            component.queue_message(1, ABORT, Variables::new()).unwrap(),
            1
        );
        component.run_now(&mut world).unwrap();
        assert_eq!(
            world.0,
            [
                (42, 0x0de0_0000_0000_7106, true),
                (42, 0x0de0_0000_0000_7106, false)
            ]
        );
    }

    #[test]
    fn base_health_pool_keeps_remaining_amount_before_on_end_and_restarts_fresh() {
        use crate::vitals::{Death, Vitals};

        struct PoolWorld(Vitals);
        impl World for PoolWorld {
            fn body_health(&self, _: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
                Some(self.0.amount(kind, maximum))
            }

            fn add_health_pool(
                &mut self,
                _: Site,
                kind: PoolKind,
                maximum: f64,
                current: f64,
            ) -> Option<u32> {
                self.0.add_pool(kind, maximum, current)
            }

            fn health_pool_current(&self, _: Site, pool: u32) -> Option<f64> {
                self.0.pool_current(pool)
            }

            fn remove_health_pool(&mut self, _: Site, pool: u32) -> Option<f64> {
                self.0.remove_pool(pool)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let entry = |position, class, message, input| {
            node(
                position,
                class,
                None,
                json!({
                    "m_gameMessage": {"m_gameMessage":message},
                    "m_outPlug": plug(&[(1, input)], &[])
                }),
            )
        };
        let flag = |value| json!({"$":"STUConfigVarBool","m_value":value});
        let owner = json!({"$":"STU_B5A0CAF0"});
        let fixture = graph(
            42,
            vec![
                entry(0, "STUStatescriptEntry", "", "m_beginPlug"),
                node(
                    1,
                    "STUStatescriptStateHealthPool",
                    Some(0),
                    json!({
                        "m_FA3ECC43":owner, "m_7E8B666C":0,
                        "m_amount":var(100,true), "m_BCFDD2E9":var(101,true),
                        "m_69A20070":{"$":"STU_FEDD683C","m_entity":owner},
                        "m_4895B8B6":flag(true), "m_64839023":flag(false), "m_0E8A5C8E":flag(false),
                        "m_CEAB19E8":var(102,true),
                        "m_onEndPlug":plug(&[(4,"m_inPlug")], &[])
                    }),
                ),
                entry(
                    2,
                    "STUStatescriptEntryGameMessage",
                    "0x0240000000000002",
                    ABORT_PLUG,
                ),
                entry(
                    3,
                    "STUStatescriptEntryGameMessage",
                    "0x0240000000000003",
                    "m_beginPlug",
                ),
                node(
                    4,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(101,true), "m_value":var(102,true)
                    }),
                ),
            ],
            json!([1]),
            json!([0, 2, 3]),
        );
        let mut world = PoolWorld(Vitals::empty());
        let mut without_output = fixture.clone();
        without_output["nodes"][1]["fields"]["m_CEAB19E8"] = Json::Null;
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::from([(100, Value::Float(200.0)), (101, Value::Float(175.0))]),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
        assert_eq!((world.0.total(), world.0.maximum()), (175.0, 200.0));
        world.0.damage(
            25.0,
            Death {
                time: 0.0,
                frame: 0,
                killer: 43,
            },
        );
        component
            .queue_message(1, 0x0240_0000_0000_0002, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&101], Value::Float(150.0));
        assert_eq!(snapshot.variables[&OWNER_HEALTH], Value::Float(0.0));
        assert!(!snapshot.instances[&1].states[&0].active);
        assert_eq!(world.0.total(), 0.0);
        component
            .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(
            snapshot.instances[&1].states[&0].kind,
            StateKind::HealthPool { pool: Some(2) }
        );
        assert_eq!(snapshot.variables[&OWNER_HEALTH], Value::Float(150.0));
        assert_eq!(world.0.pool_current(1), None);
        assert_eq!(world.0.pool_current(2), Some(150.0));
        component
            .set_entity_variable(100, Value::Float(225.0), &mut world)
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(world.0.maximum(), 200.0);
        assert!(
            component
                .snapshot()
                .unwrap()
                .diagnostics
                .iter()
                .any(|diagnostic| {
                    diagnostic.issue == Issue::UnsupportedField("changing health pool capacity")
                })
        );
        let mut world = PoolWorld(Vitals::empty());
        let mut component = Component::start(
            Arc::new(store(vec![without_output])),
            42,
            Variables::from([(100, Value::Float(200.0)), (101, Value::Float(175.0))]),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
        assert_eq!(
            component.snapshot().unwrap().variables[&OWNER_HEALTH],
            Value::Float(175.0)
        );
        world.0.damage(
            25.0,
            Death {
                time: 0.0,
                frame: 0,
                killer: 43,
            },
        );
        component.advance_frame(1, &mut world).unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&OWNER_HEALTH],
            Value::Float(150.0)
        );
        component
            .queue_message(1, 0x0240_0000_0000_0002, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(world.0.total(), 0.0);
        assert_eq!(
            component.snapshot().unwrap().variables[&OWNER_HEALTH],
            Value::Float(0.0)
        );
    }

    fn delayed_switch_fixture(mut fields: Json) -> Arc<GraphStore> {
        fields["m_onTruePlug"] = plug(&[(6, "m_inPlug")], &[]);
        fields["m_onFalsePlug"] = plug(&[(7, "m_inPlug")], &[]);
        fields["m_trueSubgraphPlug"] = plug(&[(2, "m_beginPlug")], &[1]);
        fields["m_falseSubgraphPlug"] = plug(&[(3, "m_beginPlug")], &[2]);
        let entry = |position, input| {
            node(
                position,
                "STUStatescriptEntryGameMessage",
                None,
                json!({
                    "m_gameMessage":{"m_gameMessage":format!("0x024000000000{position:04X}")},
                    "m_outPlug":plug(&[(1,input)], &[]),
                }),
            )
        };
        let record_branch = |position, branch| {
            node(
                position,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(103,true), "m_value":branch}),
            )
        };
        Arc::new(store(vec![graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1, "m_beginPlug")], &[])}),
                ),
                node(1, "STU_B591EBFE", Some(0), fields),
                node(2, "STUStatescriptState", Some(1), json!({})),
                node(3, "STUStatescriptState", Some(2), json!({})),
                entry(4, ABORT_PLUG),
                entry(5, "m_beginPlug"),
                record_branch(6, true),
                record_branch(7, false),
            ],
            json!([1, 2, 3]),
            json!([0, 4, 5]),
        )]))
    }

    fn assert_delayed_switch(
        component: &Component,
        branch: Option<bool>,
        timer: Option<(u64, u16)>,
    ) {
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let states = &snapshot.instances[&1].states;
        assert!(states[&0].active);
        assert_eq!(
            states[&0].kind,
            StateKind::DelayedSwitch {
                current: branch == Some(true),
                undecided: branch.is_none(),
            },
        );
        assert_eq!(
            states.get(&1).is_some_and(|state| state.active),
            branch == Some(true)
        );
        assert_eq!(
            states.get(&2).is_some_and(|state| state.active),
            branch == Some(false)
        );
        if let Some(branch) = branch {
            assert_eq!(snapshot.variables[&103], Value::Bool(branch));
        }
        let expected: Vec<_> = timer
            .map(|(due_ms, parameter)| PendingTimer {
                due_ms,
                raw_state: 0,
                finish: false,
                parameter,
            })
            .into_iter()
            .collect();
        assert_eq!(
            snapshot
                .pending_timers
                .get(&1)
                .map_or(&[][..], Vec::as_slice),
            expected,
        );
    }

    #[test]
    fn delayed_switch_uses_initial_override_and_asymmetric_interruptible_timers() {
        let mut component = Component::start(
            delayed_switch_fixture(json!({
                "m_418D9D9D": true,
                "m_BD83C8E6": var(100,true),
                "m_E8FC060D": var(101,true),
                "m_7130456B": 0.0259,
                "m_ED60694A": 0.04075,
            })),
            42,
            Variables::from([(100, Value::Bool(false)), (101, Value::Bool(false))]),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let write = |component: &mut Component, id, value| {
            component
                .set_entity_variable(id, Value::Bool(value), &mut EmptyWorld)
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
        };

        assert_delayed_switch(&component, Some(true), None);
        write(&mut component, 101, true);
        assert_delayed_switch(&component, Some(true), Some((40, SWITCH_TO_FALSE)));
        component.advance_frame(1, &mut EmptyWorld).unwrap();
        assert_delayed_switch(&component, Some(true), Some((40, SWITCH_TO_FALSE)));
        write(&mut component, 101, false);
        component.advance_frame(2, &mut EmptyWorld).unwrap();
        assert_delayed_switch(&component, Some(true), None);
        write(&mut component, 101, true);
        assert_delayed_switch(&component, Some(true), Some((87, SWITCH_TO_FALSE)));
        for frame in 3..=5 {
            component.advance_frame(frame, &mut EmptyWorld).unwrap();
        }
        assert_delayed_switch(&component, Some(false), None);
        write(&mut component, 101, false);
        write(&mut component, 100, true);
        assert_delayed_switch(&component, Some(false), Some((120, SWITCH_TO_TRUE)));
        for frame in 6..=7 {
            component.advance_frame(frame, &mut EmptyWorld).unwrap();
        }
        assert_delayed_switch(&component, Some(true), None);
    }

    #[test]
    fn delayed_switch_polls_undecided_inputs_and_clears_timers_on_abort() {
        #[derive(Default)]
        struct Conditions {
            positive: bool,
            negative: bool,
        }

        impl World for Conditions {
            fn native(&mut self, _site: Site, config: &Json) -> Option<Value> {
                match config["$"].as_str()? {
                    "test-delayed-positive" => Some(Value::Bool(self.positive)),
                    "test-delayed-negative" => Some(Value::Bool(self.negative)),
                    _ => None,
                }
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let mut world = Conditions::default();
        let mut component = Component::start(
            delayed_switch_fixture(json!({
                "m_308B8A80": true,
                "m_418D9D9D": true,
                "m_BD83C8E6": {"$":"test-delayed-positive"},
                "m_E8FC060D": {"$":"test-delayed-negative"},
                "m_7130456B": 0.075,
                "m_ED60694A": 0.001,
            })),
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert_delayed_switch(&component, None, None);
        assert!(!component.snapshot().unwrap().variables.contains_key(&103));
        world.positive = true;
        component.advance_frame(0, &mut world).unwrap();
        assert_delayed_switch(&component, None, Some((90, SWITCH_TO_TRUE)));
        world.positive = false;
        component.advance_frame(1, &mut world).unwrap();
        assert_delayed_switch(&component, None, Some((90, SWITCH_TO_TRUE)));
        world.negative = true;
        component.advance_frame(2, &mut world).unwrap();
        assert_delayed_switch(&component, None, Some((48, SWITCH_TO_FALSE)));
        component.advance_frame(3, &mut world).unwrap();
        assert_delayed_switch(&component, Some(false), None);

        world.positive = true;
        component.advance_frame(4, &mut world).unwrap();
        assert_delayed_switch(&component, Some(false), Some((154, SWITCH_TO_TRUE)));
        component
            .queue_message(1, 0x0240_0000_0000_0004, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(snapshot.pending_timers.is_empty());
        assert!(!snapshot.instances[&1].states[&0].active);
        assert!(!snapshot.instances[&1].states[&2].active);

        world = Conditions::default();
        component
            .queue_message(1, 0x0240_0000_0000_0005, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        component.advance_frame(10, &mut world).unwrap();
        assert_delayed_switch(&component, None, None);
        assert_eq!(
            component.snapshot().unwrap().instances[&1].states[&0].activation,
            2
        );
    }

    #[test]
    fn startup_queues_states_rechecks_scoped_writes_and_starts_children_after_roots() {
        let mut client_only = node(6, "UnportedClientAction", None, json!({}));
        client_only["client_only"] = json!(true);
        let first = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({
                        "m_outPlug":plug(&[(1,"m_beginPlug"),(2,"m_beginPlug")], &[])
                    }),
                ),
                node(
                    1,
                    "STUStatescriptStateBooleanSwitch",
                    Some(0),
                    json!({
                        "m_condition":var(100, true),
                        "m_falseSubgraphPlug":plug(&[(3,"m_beginPlug")], &[2]),
                        "m_trueSubgraphPlug":plug(&[(4,"m_beginPlug")], &[3]),
                    }),
                ),
                node(
                    2,
                    "STUStatescriptState",
                    Some(1),
                    json!({
                        "m_onBeginPlug":plug(&[(5,"m_inPlug"),(6,"m_inPlug")], &[])
                    }),
                ),
                node(3, "STUStatescriptState", Some(2), json!({})),
                node(
                    4,
                    "STU_37D754C8",
                    Some(3),
                    json!({
                        "m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{"m_graph":"0x058000000000002B"}}},
                        "m_89402D74":[{"m_out_Var":var(4, false), "m_value":var(9, false)}]
                    }),
                ),
                node(
                    5,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(100, true), "m_value":{"$":"STUConfigVarBool","m_value":true}
                    }),
                ),
                client_only,
            ],
            json!([1, 2, 3, 4]),
            json!([0]),
        );
        let child = graph(
            43,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STU_12F5D52D",
                    None,
                    json!({
                        "m_143D4C5B":[
                            {"m_out_Var":var(12, true),"m_value":parent_var(9)},
                            {"m_out_Var":var(14, true),"m_value":var(4, false)},
                            {"m_out_Var":var(15, true),"m_value":var(13, true)}
                        ]
                    }),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let second = graph(
            44,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(13, true), "m_value":{"$":"STUConfigVarInt","m_value":99}
                    }),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let graphs = store(vec![first, child, second]);
        let mut root = Root::new(7, 42);
        root.variables.insert(9, Value::Integer(3));
        let snapshot = initialize(
            &graphs,
            0xa000_0100,
            Variables::from([(100, Value::Bool(false))]),
            vec![root, Root::new(1, 44)],
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            snapshot.instances.keys().copied().collect::<Vec<_>>(),
            [7, 1, 8]
        );
        assert_eq!(snapshot.instances[&8].parent, Some((7, 3)));
        assert_eq!(snapshot.instances[&8].variables[&4], Value::Integer(3));
        let states = &snapshot.instances[&7].states;
        assert_eq!(states[&0].kind, StateKind::BooleanSwitch(true));
        assert!(!states[&2].active);
        assert_eq!(states[&2].activation, 1);
        assert_eq!(states[&3].kind, StateKind::SubScript { child: Some(8) });
        assert_eq!(snapshot.variables[&12], Value::Integer(3));
        assert_eq!(snapshot.variables[&14], Value::Integer(3));
        assert_eq!(snapshot.variables[&15], Value::Integer(99));
    }

    #[test]
    fn assignments_collect_config_groups_and_preserve_array_null_members() {
        let group = |children| json!({"$":"STU_06610EBC","m_configVars":children,"m_EE729DCB":0});
        let graphs = store(vec![graph(
            1,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(42,true), "m_value":group(Json::Null),
                        "m_outPlug":plug(&[(2,"m_inPlug")], &[])
                    }),
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(43,true),
                        "m_value":group(json!([null, {"$":"STUConfigVarInt","m_value":7}, group(json!([var(44,true)]))]))
                    }),
                ),
            ],
            json!([]),
            json!([0]),
        )]);
        let snapshot = initialize(
            &graphs,
            42,
            Variables::from([(44, Value::Array(vec![Value::Null, Value::Integer(9)]))]),
            vec![Root::new(1, 1)],
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.variables[&42], Value::Array(Vec::new()));
        assert_eq!(
            snapshot.variables[&43],
            Value::Array(vec![Value::Integer(7), Value::Null, Value::Integer(9)])
        );
    }

    #[test]
    fn encounter_base_action_forwards_and_clear_replaces_a_scoped_array() {
        let graphs = store(vec![graph(
            0x0c8c,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STU_D38D0035",
                    None,
                    json!({"m_outPlug":plug(&[(2,"m_inPlug")], &[])}),
                ),
                node(
                    2,
                    "STU_1C2CC6D5",
                    None,
                    json!({"m_26E3B366":var(42,false),"m_outPlug":plug(&[(3,"m_inPlug")], &[])}),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(43,true),"m_value":var(42,false)}),
                ),
            ],
            json!([]),
            json!([0]),
        )]);
        let populated = Value::Array(vec![Value::Integer(9)]);
        let mut root = Root::new(1, 0x0c8c);
        root.variables.insert(42, populated.clone());
        let snapshot = initialize(
            &graphs,
            42,
            Variables::from([(42, populated.clone())]),
            vec![root],
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.variables[&42], populated);
        assert_eq!(
            snapshot.instances[&1].variables[&42],
            Value::Array(Vec::new())
        );
        assert_eq!(snapshot.variables[&43], Value::Array(Vec::new()));
    }

    #[test]
    fn unsupported_branches_are_reported_and_cyclic_actions_are_bounded() {
        let diagnostics = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({
                        "m_outPlug":plug(&[(1,"m_inPlug"),(2,"m_beginPlug")], &[])
                    }),
                ),
                node(
                    1,
                    "UnknownAction",
                    None,
                    json!({"m_outPlug":plug(&[(3,"m_inPlug")], &[])}),
                ),
                node(2, "STUStatescriptState", Some(0), json!({})),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(42, true), "m_value":true
                    }),
                ),
            ],
            json!([2]),
            json!([0]),
        );
        let cycle = graph(
            43,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(1, false), "m_value":true, "m_outPlug":plug(&[(1,"m_inPlug")], &[])
                    }),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let graphs = store(vec![diagnostics, cycle]);
        let run = |graph, budget| {
            initialize(
                &graphs,
                42,
                Variables::new(),
                vec![Root::new(1, graph)],
                &mut EmptyWorld,
                budget,
            )
        };
        let snapshot = run(42, MAX_INIT_WORK).unwrap();
        assert!(!snapshot.initialization_complete());
        assert!(snapshot.instances[&1].states[&0].active);
        assert!(!snapshot.variables.contains_key(&42));
        assert_eq!(snapshot.diagnostics[0].site.node, Some(1));
        assert_eq!(
            snapshot.diagnostics[0].issue,
            Issue::UnsupportedAction("UnknownAction".into())
        );
        assert!(matches!(
            run(43, 9),
            Err(InitFault::WorkBudget { limit: 9, .. })
        ));
        assert!(matches!(run(43, MAX_INIT_WORK), Err(InitFault::PlugDepth)));
    }

    #[test]
    fn effect_action_continues_its_output_without_server_state() {
        let run = |dynamic: Json| {
            let fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                    ),
                    node(
                        1,
                        "STUStatescriptActionEffect",
                        None,
                        json!({
                            "m_effect":{"$":"STUConfigVarEffect","m_effect":"0x0300000000000001"},
                            "m_dataFlow":[{"$":"STU_13D35171","m_answer":var(7, false)}],
                            "m_37B02425":dynamic,
                            "m_outPlug":plug(&[(2,"m_inPlug")], &[]),
                        }),
                    ),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100, true), "m_value":true}),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            initialize(
                &store(vec![fixture]),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap()
        };
        let snapshot = run(Json::Null);
        assert!(snapshot.initialization_complete());
        assert_eq!(
            snapshot.variables,
            Variables::from([(100, Value::Bool(true))])
        );
        assert!(snapshot.instances[&1].variables.is_empty());
        assert!(snapshot.instances[&1].states.is_empty());

        let snapshot = run(var(9, true));
        assert!(snapshot.variables.is_empty());
        assert_eq!(snapshot.diagnostics.len(), 1);
        assert_eq!(snapshot.diagnostics[0].site.node, Some(1));
        assert_eq!(
            snapshot.diagnostics[0].issue,
            Issue::UnsupportedAction("STUStatescriptActionEffect".into())
        );
    }

    #[test]
    fn criteria_actions_continue_their_output() {
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionCriteria",
                    None,
                    json!({
                        "m_identifier":{"$":"STUConfigVarIdentifier","m_identifier":"0x0D80000000000001"},
                        "m_targets":{"$":"test-unanswered"},
                        "m_outPlug":plug(&[(2,"m_inPlug")], &[]),
                    }),
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(100, true), "m_value":true}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let snapshot = initialize(
            &store(vec![fixture]),
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            snapshot.variables,
            Variables::from([(100, Value::Bool(true))])
        );
        assert!(snapshot.instances[&1].states.is_empty());
    }

    #[test]
    fn shared_unbound_loadout_action_contract() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/unbound-loadout-action-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["class"], "STU_545AB3B8");
        for case in fixture["cases"].as_array().unwrap() {
            let has_output = case["out_plug_present"].as_bool().unwrap();
            let graphs = Arc::new(store(vec![graph(
                42,
                vec![
                    node(0, "STUStatescriptEntry", None, json!({})),
                    node(
                        1,
                        "STU_545AB3B8",
                        None,
                        json!({
                            "m_target":{"$":"unsupported-if-evaluated"},
                            "m_loadout":{"$":"unsupported-if-evaluated"},
                            "m_0B897113":{"$":"unsupported-if-evaluated"},
                            "m_outPlug":plug(if has_output { &[(2,"m_inPlug")][..] } else { &[] }, &[]),
                        }),
                    ),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100,true),"m_value":true}),
                    ),
                ],
                json!([]),
                json!([]),
            )]));
            let mut component = Component::start(
                graphs,
                42,
                Variables::new(),
                vec![Root::listener(1, 42, None)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            for _ in 0..2 {
                component
                    .set_entity_variable(100, Value::Bool(false), &mut EmptyWorld)
                    .unwrap();
                component
                    .run(&mut EmptyWorld, |vm| {
                        let action = vm.graph(1).node(1).unwrap();
                        assert_eq!(
                            vm.run_node(1, action, None, 0)?,
                            case["return"].as_u64() == Some(1)
                        );
                        vm.run()
                    })
                    .unwrap();
                let snapshot = component.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                assert_eq!(snapshot.variables[&100], Value::Bool(has_output));
                assert!(snapshot.instances[&1].states.is_empty());
            }
        }
    }

    #[test]
    fn payload_free_header_action_passes_through_like_the_reference_fallback() {
        let authored = json!({
            "m_2BBEEAB8":0, "m_8BF03679":null, "m_A2287776":0, "m_ADEB6E05":1,
            "m_AED90719":0, "m_BF5B22B7":null,
            "m_inPlug":{"$":"STUStatescriptInputPlug","links":[]},
            "m_outPlug":{"$":"STUStatescriptOutputPlug","links":[]},
        });
        let run = |fields: &Json, state: Json, remote: i32| {
            // The helper sets the header flags; the authored values replace them.
            let mut action = node(1, "STU_E9694EFD", None, json!({}));
            action["fields"] = fields.clone();
            action["state"] = state;
            action["remote"] = json!(remote);
            let fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_inPlug"),(2,"m_inPlug")], &[])}),
                    ),
                    action,
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100, true), "m_value":true}),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            initialize(
                &store(vec![fixture]),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap()
        };
        let snapshot = run(&authored, Json::Null, -1);
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            snapshot.variables,
            Variables::from([(100, Value::Bool(true))])
        );

        let mut linked = authored.clone();
        linked["m_outPlug"]["links"] = json!([[2, "m_inPlug"]]);
        let mut operand = authored.clone();
        operand["m_value"] = json!({"$":"STUConfigVarBool","m_value":true});
        let mut synced = authored.clone();
        synced["m_8BF03679"] = json!([]);
        for (fields, state, remote) in [
            (&linked, Json::Null, -1),
            (&operand, Json::Null, -1),
            (&synced, Json::Null, -1),
            (&authored, json!(0), -1),
            (&authored, Json::Null, 0),
        ] {
            let snapshot = run(fields, state.clone(), remote);
            assert_eq!(
                snapshot.diagnostics.len(),
                1,
                "{fields} state {state} remote {remote}"
            );
            assert_eq!(snapshot.diagnostics[0].site.node, Some(1));
            assert_eq!(
                snapshot.diagnostics[0].issue,
                Issue::UnsupportedAction("STU_E9694EFD".into())
            );
        }
    }

    #[test]
    fn animation_without_an_authored_duration_stays_on_without_a_timer() {
        let run = |overrides: Json| {
            let mut fields = json!({
                "m_86701C3E":null,
                "m_154DBA91":null,
                "m_A3B86BB0":{"$":"STUStatescriptOutputPlug","links":[]},
                "m_onFinishedPlug":{"$":"STUStatescriptOutputPlug","links":[]},
                "m_onEndPlug":plug(&[(2,"m_inPlug")], &[]),
            });
            for (name, value) in overrides.as_object().unwrap() {
                fields[name] = value.clone();
            }
            let fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")], &[])}),
                    ),
                    node(1, "STUStatescriptStateAnim", Some(0), fields),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100, true), "m_value":true}),
                    ),
                ],
                json!([1]),
                json!([0]),
            );
            Component::start(
                Arc::new(store(vec![fixture])),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                100,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap()
        };
        let mut component = run(json!({}));
        for frame in 101..=1000 {
            component.advance_frame(frame, &mut EmptyWorld).unwrap();
        }
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.instances[&1].states[&0].kind, StateKind::Anim);
        assert_eq!(active(snapshot, 1), [0]);
        assert!(snapshot.pending_timers.is_empty());
        assert!(snapshot.variables.is_empty());

        // A consumer of the animation's end or a client removal flag would
        // need the asset's length.
        for rejected in [
            json!({"m_154DBA91":{"$":"STUConfigVarBool","m_value":true}}),
            json!({"m_A3B86BB0":{"$":"STUStatescriptOutputPlug","links":[[2,"m_inPlug"]]}}),
            json!({"m_A3B86BB0":null}),
            json!({"m_onFinishedPlug":{"$":"STUStatescriptOutputPlug","links":[[2,"m_inPlug"]]}}),
            json!({"m_onFinishedPlug":null}),
        ] {
            let component = run(rejected.clone());
            let snapshot = component.snapshot().unwrap();
            assert_eq!(snapshot.diagnostics.len(), 1, "{rejected}");
            assert_eq!(snapshot.diagnostics[0].site.node, Some(1));
            assert_eq!(
                snapshot.diagnostics[0].issue,
                Issue::UnsupportedField("animation asset duration")
            );
        }
    }

    #[test]
    fn game_message_without_recipients_leaves_its_parameters_unevaluated() {
        struct Recipients(Value);

        impl World for Recipients {
            fn native(&mut self, _site: Site, config: &Json) -> Option<Value> {
                (config["$"] == "test-recipients").then(|| self.0.clone())
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let message = json!({"$":"STUConfigVarGameMessage","m_gameMessage":"0x0240000000000001"});
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptActionSendGameMessage",
                    None,
                    json!({
                        "m_gameMessage":message,
                        "m_targets":{"$":"test-recipients"},
                        "m_params":[{"m_B5051BCE":"0x0A40000000000123",
                            "m_value":{"$":"test-unanswered"}}],
                        "m_outPlug":plug(&[(2,"m_inPlug")], &[]),
                    }),
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(100, true), "m_value":true}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let graphs = store(vec![fixture]);
        let run = |targets| {
            initialize(
                &graphs,
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                &mut Recipients(targets),
                MAX_INIT_WORK,
            )
            .unwrap()
        };
        for targets in [
            Value::Null,
            Value::Entity(0),
            Value::Array(vec![
                Value::Null,
                Value::Entity(0),
                Value::Entity(0x1fff_ffff),
            ]),
        ] {
            let snapshot = run(targets);
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables,
                Variables::from([(100, Value::Bool(true))])
            );
        }
        // A routed message still needs its unanswered parameter.
        let snapshot = run(Value::Entity(42));
        assert!(snapshot.variables.is_empty());
        assert_eq!(snapshot.diagnostics.len(), 1);
        assert_eq!(snapshot.diagnostics[0].site.node, Some(1));
        assert_eq!(
            snapshot.diagnostics[0].issue,
            Issue::Expression(EvalError::UnsupportedConfig("test-unanswered".into()))
        );
    }

    #[test]
    fn queued_messages_abort_and_rearm_waits_without_crossing_instance_scope() {
        let message_node = |position, message, input| {
            node(
                position,
                "STUStatescriptEntryGameMessage",
                None,
                json!({
                    "m_gameMessage": {"m_gameMessage":message},
                    "m_params": [{"m_B5051BCE":"0x0A40000000000004","m_out_Var":var(4,false)}],
                    "m_outPlug": plug(&[(1,input)],&[])
                }),
            )
        };
        let timed = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({
                        "m_outPlug":plug(&[(1,"m_beginPlug"),(5,"m_beginPlug")],&[])
                    }),
                ),
                node(
                    1,
                    "STUStatescriptStateWait",
                    Some(0),
                    json!({
                        "m_timeout":{"$":"STUConfigVarFloat","m_value":0.02},
                        "m_0DBAFD7F":1,
                        "m_onFinishedPlug":plug(&[(2,"m_inPlug")],&[]),
                        "m_onAbortPlug":plug(&[(3,"m_inPlug")],&[]),
                    }),
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(10,false), "m_value": true
                    }),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(10,false), "m_value": false
                    }),
                ),
                message_node(4, "0x0240000000000001", "m_beginPlug"),
                node(
                    5,
                    "STU_D74D4F47",
                    Some(1),
                    json!({
                        "m_3016B9A1":{"$":"STUConfigVarInt","m_value":1},
                        "m_onFinishedPlug":plug(&[(6,"m_inPlug")],&[]),
                    }),
                ),
                node(
                    6,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(11,false), "m_value": true
                    }),
                ),
                message_node(7, "0x0240000000000002", ABORT_PLUG),
            ],
            json!([1, 5]),
            json!([0, 4, 7]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![timed])),
            42,
            Variables::new(),
            vec![Root::new(7, 42), Root::new(1, 42)],
            1,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let timer = |due_ms| PendingTimer {
            due_ms,
            raw_state: 0,
            finish: false,
            parameter: 1,
        };
        let snapshot = component.snapshot().unwrap();
        assert!(snapshot.initialization_complete());
        assert_eq!(snapshot.pending_timers[&7], [timer(36)]);
        assert_eq!(snapshot.pending_timers[&1], [timer(36)]);
        assert_eq!(
            snapshot.instances[&7].states[&1].kind,
            StateKind::WaitFrames { target: 2 }
        );

        component.advance_frame(1, &mut EmptyWorld).unwrap();
        assert_eq!(component.now_ms(), 31);
        assert_eq!(
            component
                .queue_message(
                    7,
                    0x0240_0000_0000_0001,
                    Variables::from([(4, Value::Integer(15))])
                )
                .unwrap(),
            1
        );
        component.run_now(&mut EmptyWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.pending_timers[&7], [timer(51)]);
        assert_eq!(snapshot.pending_timers[&1], [timer(36)]);
        assert_eq!(snapshot.instances[&7].variables[&4], Value::Integer(15));
        assert!(!snapshot.instances[&1].variables.contains_key(&4));
        assert_eq!(snapshot.instances[&7].states[&0].activation, 1);
        assert_eq!(snapshot.instances[&7].states[&0].start_ms, 31);
        assert!(matches!(
            component.advance_frame(1, &mut EmptyWorld),
            Err(InitFault::FrameOrder { .. })
        ));
        component.advance_frame(2, &mut EmptyWorld).unwrap();
        assert_eq!(
            component
                .queue_message(7, 0x0240_0000_0000_0002, Variables::new())
                .unwrap(),
            1
        );
        component.run_now(&mut EmptyWorld).unwrap();
        component.advance_frame(3, &mut EmptyWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(snapshot.pending_timers.is_empty());
        assert_eq!(snapshot.instances[&7].variables[&10], Value::Bool(false));
        assert_eq!(snapshot.instances[&1].variables[&10], Value::Bool(true));
        assert_eq!(snapshot.instances[&7].variables[&11], Value::Bool(true));
        assert_eq!(snapshot.instances[&1].variables[&11], Value::Bool(true));

        for _ in 0..=MAX_EVENTS_PER_UPDATE {
            component
                .queue_message(7, 0x0240_0000_0000_0001, Variables::new())
                .unwrap();
        }
        assert_eq!(
            component.run_now(&mut EmptyWorld),
            Err(InitFault::EventBudget { instance: 7 })
        );
        assert!(component.snapshot().is_none());
        assert_eq!(component.run_now(&mut EmptyWorld), Err(InitFault::Halted));
    }

    struct ReferenceWorld;

    impl World for ReferenceWorld {
        fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
            // Values observed in the Python fixtures, for this test only.
            match (site.graph, config.get("$")?.as_str()?) {
                (0x0c18, "STU_AC829876") => Some(Value::Bool(true)),
                (0x0c18, "STU_4A101787" | "STU_A77AC2AC") => Some(Value::Null),
                (0x288a, "STU_37F20D24") => Some(Value::Null),
                (0x0b92, "STU_71A87E92" | "STU_2200A66D") if matches!(site.node, Some(7 | 11)) => {
                    Some(Value::Null)
                }
                _ => None,
            }
        }

        fn selects_ai_behavior(&mut self, site: Site) -> Option<bool> {
            (site.graph == 0x0b92 && site.node == Some(1)).then_some(false)
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }

    #[test]
    fn ordered_stack_entries_use_the_value_below_and_restore_the_hidden_base() {
        let message = |position, message, target| {
            node(
                position,
                "STUStatescriptEntryGameMessage",
                None,
                json!({
                    "m_gameMessage":{"m_gameMessage":message},
                    "m_outPlug":plug(&[(target,ABORT_PLUG)],&[])
                }),
            )
        };
        let expression = json!({"$":"STU_919FD47C","m_expression":{
            "m_opcodes":[8,0,6,0,23,0],
            "m_dynamicVars":[var(100,true)],"m_D99EF254":[1]
        }});
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug"),(2,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateStack",
                    Some(0),
                    json!({
                        "m_out_Var":var(100,true), "m_value":10,
                        "m_priority":{"m_priority":"0x0C40000000000001"}
                    }),
                ),
                node(
                    2,
                    "STUStatescriptStateStack",
                    Some(1),
                    json!({
                        "m_out_Var":var(100,true), "m_value":expression
                    }),
                ),
                message(3, "0x0240000000000001", 1),
                message(4, "0x0240000000000002", 2),
            ],
            json!([1, 2]),
            json!([0, 3, 4]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::from([(100, Value::Integer(5))]),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let variable = Variable {
            scope: Scope::Entity,
            id: 100,
        };
        assert_eq!(
            component.resolved_variable(1, variable),
            Some(Value::Float(11.0))
        );
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&100], Value::Integer(5));
        assert_eq!(
            snapshot.bindings[&(0, 100)],
            [
                StackBinding {
                    instance: 1,
                    raw_state: 0,
                    slot: 0,
                    priority: StackPriority {
                        level: 0,
                        above_equals: false
                    }
                },
                StackBinding {
                    instance: 1,
                    raw_state: 1,
                    slot: 0,
                    priority: StackPriority {
                        level: 0,
                        above_equals: true
                    }
                }
            ]
        );
        assert_eq!(snapshot.resolved_bindings[&(0, 100)], Value::Float(11.0));
        assert_eq!(
            snapshot.instances[&1].states[&0].kind,
            StateKind::Stack {
                top: false,
                under: true
            }
        );
        assert_eq!(
            snapshot.instances[&1].states[&1].kind,
            StateKind::Stack {
                top: true,
                under: false
            }
        );
        component
            .set_entity_variable(100, Value::Integer(7), &mut EmptyWorld)
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        assert_eq!(
            component.resolved_variable(1, variable),
            Some(Value::Float(11.0))
        );
        for (message, expected) in [(1, Value::Float(8.0)), (2, Value::Integer(7))] {
            component
                .queue_message(1, 0x0240_0000_0000_0000 | message, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
            assert_eq!(component.resolved_variable(1, variable), Some(expected));
        }
        assert!(component.snapshot().unwrap().bindings.is_empty());
    }

    #[test]
    fn shared_stack_admission_contract() {
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-stack-admission-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.stack-admission.v1");
        let priority =
            script_expr::stack_priority(&json!({"m_priority":contract["input"]["candidate"]}))
                .unwrap();
        for case in contract["input"]["cases"].as_array().unwrap() {
            let mut nodes = vec![node(1, "STUStatescriptState", Some(0), json!({}))];
            let mut starts = vec![(1, "m_beginPlug")];
            let mut stops = Vec::new();
            let mut states = vec![1];
            for (i, priority) in case["priorities"].as_array().unwrap().iter().enumerate() {
                let pos = u16::try_from(i + 2).unwrap();
                nodes.push(node(
                    pos,
                    "STUStatescriptStateStack",
                    Some(pos - 1),
                    json!({
                        "m_out_Var":var(900,true),"m_value":case["values"][i],
                        "m_priority":{"m_priority":priority}
                    }),
                ));
                starts.push((pos, "m_beginPlug"));
                stops.push((pos, "m_F198FD3A"));
                states.push(pos);
            }
            nodes.push(node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&starts,&[])}),
            ));
            let message = u16::try_from(nodes.len()).unwrap();
            nodes.push(node(message,"STUStatescriptEntryGameMessage",None,json!({
                "m_gameMessage":{"m_gameMessage":"0x0240000000000001"}, "m_outPlug":plug(&stops,&[])
            })));
            let model = graph(42, nodes, json!(states), json!([0, message]));
            let mut component = Component::start(
                Arc::new(store(vec![model])),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            if case["release"] == true {
                component
                    .queue_message(1, 0x0240_0000_0000_0001, Variables::new())
                    .unwrap();
                component.run_now(&mut EmptyWorld).unwrap();
            }
            assert!(component.snapshot().unwrap().initialization_complete());
            let expected = case["expected"].as_bool().unwrap();
            assert_eq!(
                component.permits_entity_stack(900, priority),
                Some(expected),
                "{}",
                case["name"]
            );
            let query = json!({"$":"STU_3ACE35FB","m_E50CF556":var(900,true),"m_012B7AF7":{"m_priority":contract["input"]["candidate"]}});
            assert_eq!(
                component
                    .evaluate_state_config(1, 0, &query, &mut EmptyWorld)
                    .unwrap(),
                Value::Bool(expected)
            );
        }
    }

    #[test]
    fn shared_ability_press_window_contract() {
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-ability-press-window-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.ability-press-window.v1");
        for case in contract["input"]["cases"].as_array().unwrap() {
            let mut model = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                    node(
                        1,
                        "STUStatescriptStateAbility",
                        Some(0),
                        json!({
                            "m_A5F1A77B":36, "m_246438AD":contract["input"]["window_seconds"],
                            "m_150F0D92":var(900,true),
                            "m_EF3C5C73":[{"m_out_Var":var(902,true),"m_value":true}]
                        }),
                    ),
                ],
                json!([1]),
                json!([0]),
            );
            model["fields"]["m_680A2CB2"] = json!([0]);
            let mut component = Component::start(
                Arc::new(store(vec![model])),
                42,
                Variables::from([(900, Value::Bool(false)), (902, Value::Bool(false))]),
                vec![Root::new(1, 42)],
                0,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let mut observed = Vec::new();
            for frame in 1..=contract["input"]["frames"].as_u64().unwrap() as u32 {
                if u64::from(frame) == case["ready_frame"].as_u64().unwrap() {
                    component
                        .set_entity_variable(900, Value::Bool(true), &mut EmptyWorld)
                        .unwrap();
                }
                component
                    .advance_command(
                        Command {
                            frame,
                            buttons: if case["press_frames"]
                                .as_array()
                                .unwrap()
                                .contains(&json!(frame))
                            {
                                16
                            } else {
                                0
                            },
                            ..Default::default()
                        },
                        &mut EmptyWorld,
                    )
                    .unwrap();
                assert!(component.snapshot().unwrap().initialization_complete());
                if contract["input"]["sample_frames"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(frame))
                {
                    observed
                        .push(component.snapshot().unwrap().variables[&902] == Value::Bool(true));
                }
            }
            assert_eq!(json!(observed), case["expected"], "{}", case["name"]);
        }
    }

    #[test]
    fn control_block_interrupts_authored_channels_releases_buttons_and_recovers() {
        let mut fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug"),(2,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateAbility",
                    Some(0),
                    json!({
                "m_out_CooldownVar":var(60,true),"m_A5F1A77B":35,
                "m_EF3C5C73":[{"m_out_Var":var(62,true),"m_value":true}],
                "m_19899EAC":[{"m_out_Var":var(62,true),"m_value":false}]}),
                ),
                node(
                    2,
                    "STUStatescriptStateLogicalButton",
                    Some(1),
                    json!({
                "m_logicalButton":{"$":"STUConfigVarLogicalButton","m_logicalButton":20}}),
                ),
            ],
            json!([1, 2]),
            json!([0]),
        );
        fixture["fields"]["m_680A2CB2"] = json!([0, 1]);
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::from([(3652, Value::Float(1.0)), (60, Value::Float(0.0))]),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let command = |frame| Command {
            frame,
            buttons: 8 | 1,
            ..Default::default()
        };
        component
            .advance_command(command(1), &mut EmptyWorld)
            .unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&62],
            Value::Bool(true)
        );
        assert_eq!(
            component.snapshot().unwrap().instances[&1].states[&1].kind,
            StateKind::LogicalButton { down: true }
        );
        component
            .set_control_blocked(true, &mut EmptyWorld)
            .unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&62],
            Value::Bool(false)
        );
        assert_eq!(
            component.snapshot().unwrap().instances[&1].states[&1].kind,
            StateKind::LogicalButton { down: false }
        );
        assert!(component.machine.held_buttons.is_empty());
        for frame in 2..=20 {
            component
                .advance_command(command(frame), &mut EmptyWorld)
                .unwrap();
            assert_eq!(
                component.snapshot().unwrap().variables[&62],
                Value::Bool(false)
            );
            assert!(component.machine.held_buttons.is_empty());
        }
        component
            .set_control_blocked(false, &mut EmptyWorld)
            .unwrap();
        component
            .advance_command(command(21), &mut EmptyWorld)
            .unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&62],
            Value::Bool(true)
        );
        assert_eq!(
            component.snapshot().unwrap().instances[&1].states[&1].kind,
            StateKind::LogicalButton { down: true }
        );
        assert!(component.snapshot().unwrap().initialization_complete());
    }

    #[test]
    fn ability_cooldown_restarts_and_stack_displacement_stops_the_running_ability() {
        let message = |position, message, target, input| {
            node(
                position,
                "STUStatescriptEntryGameMessage",
                None,
                json!({
                    "m_gameMessage":{"m_gameMessage":message},
                    "m_outPlug":plug(&[(target, input)], &[]),
                }),
            )
        };
        let mut fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateAbility",
                    Some(0),
                    json!({
                        "m_out_CooldownVar":var(60,true),
                        "m_out_StackVar":var(61,true),
                        "m_5E561E77":{"m_priority":"0x0C40000000000001"},
                        "m_A5F1A77B":35,
                        "m_EF3C5C73":[{"m_out_Var":var(60,true), "m_value":0.1},
                                      {"m_out_Var":var(62,true), "m_value":true}],
                        "m_19899EAC":[{"m_out_Var":var(62,true), "m_value":false}]
                    }),
                ),
                node(
                    2,
                    "STUStatescriptStateStack",
                    Some(1),
                    json!({
                        "m_out_Var":var(61,true),
                        "m_value":7,
                        "m_priority":{"m_priority":"0x0C40000000000003"}
                    }),
                ),
                message(3, "0x0240000000000001", 2, "m_beginPlug"),
                message(4, "0x0240000000000002", 1, ABORT_PLUG),
            ],
            json!([1, 2]),
            json!([0, 3, 4]),
        );
        fixture["fields"]["m_680A2CB2"] = json!([0]);
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::from([(3652, Value::Float(1.0)), (60, Value::Float(0.032))]),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let timer = |component: &Component| {
            component
                .snapshot()
                .unwrap()
                .pending_timers
                .get(&1)
                .and_then(|timers| timers.first().copied())
        };
        assert_eq!(
            timer(&component),
            Some(PendingTimer {
                due_ms: 16,
                raw_state: 0,
                finish: false,
                parameter: 2
            })
        );
        for frame in 0..=1 {
            component
                .advance_command(
                    Command {
                        frame,
                        buttons: 8,
                        ..Command::default()
                    },
                    &mut EmptyWorld,
                )
                .unwrap();
        }
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&62], Value::Bool(true));
        assert_eq!(
            snapshot.instances[&1].states[&0].kind,
            StateKind::Ability { flags: 7 }
        );
        assert_eq!(snapshot.resolved_bindings[&(0, 61)], Value::Bool(true));
        assert_eq!(snapshot.bindings[&(0, 61)][0].slot, 1);
        assert_eq!(timer(&component).map(|timer| timer.due_ms), Some(66));
        let remaining = snapshot.resolved_bindings[&(0, 60)].clone();
        for message in [1, 2] {
            component
                .queue_message(1, 0x0240_0000_0000_0000 | message, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
        }
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&62], Value::Bool(false));
        assert_eq!(snapshot.variables[&60], remaining);
        assert_eq!(snapshot.resolved_bindings[&(0, 61)], Value::Integer(7));
        assert_eq!(snapshot.bindings[&(0, 61)][0].slot, 0);
        assert!(!snapshot.instances[&1].states[&0].active);
        assert!(timer(&component).is_none());
    }

    #[test]
    fn switch_exits_old_branch_and_active_query_does_not_begin_its_target() {
        let set = |position, variable, value| {
            node(
                position,
                "STUStatescriptActionSetVar",
                None,
                json!({
                    "m_out_Var":var(variable,true), "m_value":value,
                }),
            )
        };
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({
                        "m_outPlug":plug(&[(1,"m_beginPlug"),(2,"m_inPlug"),(3,"m_beginPlug")],&[])
                    }),
                ),
                node(
                    1,
                    "STU_DEBD057B",
                    Some(0),
                    json!({
                        "m_262AE982":var(100,true),
                        "m_05706D1C":[
                            {"$":"Plug", "links":[[4,"m_beginPlug"]], "m_FF3DAF1E":[3], "m_value":2},
                            {"$":"Plug", "links":[[5,"m_beginPlug"]], "m_FF3DAF1E":[4], "m_value":2}
                        ],
                        "m_A5EF385D":plug(&[(6,"m_beginPlug")], &[5]),
                    }),
                ),
                node(
                    2,
                    "STU_01178155",
                    Some(1),
                    json!({
                        "m_7273C81B":plug(&[(7,"m_inPlug")],&[]),
                        "m_C6F3744E":plug(&[(8,"m_inPlug")],&[]),
                    }),
                ),
                node(
                    3,
                    "STUStatescriptStateGamePadVibration",
                    Some(2),
                    json!({
                        "m_onBeginPlug":plug(&[(9,"m_inPlug")],&[]),
                        "m_onFinishedPlug":plug(&[(10,"m_inPlug")],&[]),
                    }),
                ),
                node(4, "STUStatescriptState", Some(3), json!({})),
                node(5, "STUStatescriptState", Some(4), json!({})),
                node(6, "STUStatescriptState", Some(5), json!({})),
                set(7, 101, 1),
                set(8, 101, 0),
                set(9, 102, 1),
                set(10, 102, 2),
                node(
                    11,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({
                        "m_gameMessage":{"m_gameMessage":"0x0240000000000001"},
                        "m_outPlug":plug(&[(2,"m_beginPlug")],&[]),
                    }),
                ),
                node(
                    12,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({
                        "m_gameMessage":{"m_gameMessage":"0x0240000000000002"},
                        "m_outPlug":plug(&[(2,"m_inPlug")],&[]),
                    }),
                ),
            ],
            json!([1, 2, 3, 4, 5, 6]),
            json!([0, 11, 12]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let initial = component.snapshot().unwrap();
        let states = &initial.instances[&1].states;
        assert_eq!(states[&0].kind, StateKind::Switch { case: None });
        assert!(!states[&1].active);
        assert_eq!(states[&1].activation, 0);
        assert!(!states[&2].active);
        assert!(states[&5].active);
        assert_eq!(initial.variables[&101], Value::Integer(0));
        assert_eq!(initial.variables[&102], Value::Integer(2));
        component
            .set_entity_variable(100, Value::Integer(2), &mut EmptyWorld)
            .unwrap();
        for message in [1, 2] {
            component
                .queue_message(1, 0x0240_0000_0000_0000 | message, Variables::new())
                .unwrap();
            component.run_now(&mut EmptyWorld).unwrap();
        }
        let updated = component.snapshot().unwrap();
        let states = &updated.instances[&1].states;
        assert_eq!(states[&0].kind, StateKind::Switch { case: Some(0) });
        assert!(states[&1].active);
        assert_eq!(states[&1].activation, 1);
        assert!(states[&3].active);
        assert!(!states.contains_key(&4));
        assert!(!states[&5].active);
        assert_eq!(updated.variables[&101], Value::Integer(1));
        assert!(updated.initialization_complete());
    }

    #[derive(Default)]
    struct SprintWorld {
        forward: i8,
    }

    impl World for SprintWorld {
        fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
            if site.graph != 0x0255 {
                return None;
            }
            match config.get("$")?.as_str()? {
                "STU_D3EB8D87" | "STU_A77AC2AC" => Some(Value::Bool(false)),
                "STU_310D6529" => {
                    let unit = (1.0_f64 / 127.0) as f32;
                    let forward = f64::from(f32::from(self.forward) * unit);
                    Some(Value::Vector([0.0, forward, 0.0]))
                }
                _ => None,
            }
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }

    #[test]
    #[ignore = "requires a local 1.74 graph pack"]
    fn authored_sprint_stack_gates_track_input_and_reveal_the_base_on_release() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = SprintWorld::default();
        let mut component = Component::start(
            graphs,
            0xa000_0101,
            Variables::new(),
            vec![Root::new(5, 0x0255)],
            1029,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let var = |component: &Component, id| {
            component.resolved_variable(
                5,
                Variable {
                    scope: Scope::Instance,
                    id,
                },
            )
        };
        assert_eq!(var(&component, 9570), Some(Value::Bool(true)));
        for (frame, forward, buttons, throttle, held) in [
            (1030, 63, 0, Value::Null, Value::Null),
            (1031, 64, 0, Value::Bool(true), Value::Null),
            (1032, 127, 0x881, Value::Bool(true), Value::Bool(true)),
            (1033, 0, 0, Value::Null, Value::Null),
        ] {
            world.forward = forward;
            component
                .advance_command(
                    Command {
                        frame,
                        forward,
                        buttons,
                        ..Command::default()
                    },
                    &mut world,
                )
                .unwrap();
            assert_eq!(var(&component, 2333), Some(throttle));
            assert_eq!(var(&component, 7681), Some(held));
        }
        component
            .set_entity_variable(9535, Value::Bool(false), &mut world)
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(var(&component, 9570), Some(Value::Null));
        assert!(
            !component
                .snapshot()
                .unwrap()
                .bindings
                .contains_key(&(5, 9570))
        );
    }

    #[test]
    #[ignore = "requires a local 1.74 graph pack"]
    fn isolated_soldier_sprint_ability_matches_the_authored_toggle_and_modifier_states() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = SprintWorld::default();
        let mut component = Component::start(
            graphs,
            0xa000_0101,
            Variables::new(),
            vec![Root::new(5, 0x0255)],
            1029,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let rows = [
            (1030, 0, 8, 0, None),
            (1031, 63, 8, 0, None),
            (1032, 64, 8, 0, None),
            (1033, 64, 8, 5, Some(1)),
            (1034, 127, 0, 1, Some(1)),
            (1035, 127, 8, 4, Some(0)),
            (1036, 127, 8, 4, Some(0)),
            (1037, 127, 0, 0, Some(0)),
            (1038, 127, 8, 5, Some(1)),
            (1039, 63, 0, 0, Some(0)),
            (1040, 127, 0, 0, Some(0)),
            (1041, 127, 9, 0, Some(0)),
        ];
        for (frame, forward, buttons, flags, hud) in rows {
            world.forward = forward;
            component
                .advance_command(
                    Command {
                        frame,
                        forward,
                        buttons,
                        ..Command::default()
                    },
                    &mut world,
                )
                .unwrap();
            let snapshot = component.snapshot().unwrap();
            let instance = &snapshot.instances[&5];
            assert_eq!(
                instance.states[&1].kind,
                StateKind::Ability { flags },
                "frame {frame}"
            );
            assert_eq!(
                instance.variables.get(&699).cloned(),
                hud.map(Value::Integer),
                "frame {frame}"
            );
            for raw in [0, 20] {
                assert_eq!(
                    instance.states.get(&raw).is_some_and(|state| state.active),
                    flags & 1 != 0,
                    "frame {frame}, state {raw}"
                );
            }
            // The activation passes the payload-free STU_E9694EFD action as
            // the reference's fallback does, so the body stays complete.
            assert!(
                snapshot.initialization_complete(),
                "frame {frame}: {:?}",
                snapshot.diagnostics
            );
            let projected = component.movement_modifiers(&mut world).unwrap().unwrap();
            let modifiers: Vec<_> = projected
                .iter()
                .map(|(key, modifier)| {
                    (
                        key.state,
                        modifier.index,
                        modifier.operation,
                        modifier.value,
                        modifier.priority,
                    )
                })
                .collect();
            if flags & 1 == 0 {
                assert!(modifiers.is_empty());
            } else {
                assert_eq!(
                    modifiers,
                    [
                        (0, 0, Operation::Add, 0.5, 0.0),
                        (20, 21, Operation::Set, 0.0, 0.0)
                    ]
                );
            }
        }
    }

    fn active(snapshot: &Snapshot, id: u16) -> Vec<u16> {
        snapshot.instances[&id]
            .states
            .iter()
            .filter_map(|(&raw, state)| state.active.then_some(raw))
            .collect()
    }

    #[test]
    fn volley_reports_exact_times_and_bakes_linked_ammo_on_finish() {
        let weapon = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateWeaponVolley",
                    Some(0),
                    json!({
                        "m_A17BE89B":3,
                        "m_numShotsPerSecond":9,
                        "m_24E351A4":var(53,false),
                        "m_3FFC9EB9":var(231,false),
                        "m_FEC435C6":180
                    }),
                ),
            ],
            json!([1]),
            json!([0]),
        );
        let mut root = Root::new(1, 42);
        root.variables.insert(53, Value::Integer(30));
        root.variables.insert(231, Value::Float(0.25));
        let mut component = Component::start(
            Arc::new(store(vec![weapon])),
            42,
            Variables::new(),
            vec![root],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let mut shots = Vec::new();
        for frame in 0..=14 {
            component.advance_frame(frame, &mut EmptyWorld).unwrap();
            shots.extend(component.take_shots().unwrap());
            if frame == 6 {
                let snapshot = component.snapshot().unwrap();
                assert!(snapshot.bindings.is_empty());
                assert_eq!(snapshot.instances[&1].variables[&53], Value::Integer(30));
                assert_eq!(snapshot.resolved_bindings[&(1, 53)], Value::Integer(29));
                assert_eq!(snapshot.resolved_bindings[&(1, 231)], Value::Float(0.25));
            }
        }
        assert_eq!(
            shots
                .iter()
                .map(|shot| (shot.frame, shot.time_ms, shot.ordinal))
                .collect::<Vec<_>>(),
            [(0, 0, 0), (6, 111, 1), (13, 222, 2)]
        );
        assert!(
            shots
                .iter()
                .all(|shot| shot.activation == 1 && shot.activation_ms == 0)
        );
        let node = component.shot_node(&shots[2]).unwrap();
        assert_eq!(
            component.evaluate_shot_config(
                &shots[2],
                node.field("m_FEC435C6").unwrap(),
                &mut EmptyWorld
            ),
            Ok(Value::Integer(180))
        );
        let snapshot = component.snapshot().unwrap();
        assert!(snapshot.pending_timers.is_empty());
        assert!(snapshot.resolved_bindings.is_empty());
        assert!(!snapshot.instances[&1].states[&0].active);
        assert_eq!(snapshot.instances[&1].variables[&53], Value::Integer(27));
    }

    struct RifleWorld(Arc<GraphStore>);

    impl World for RifleWorld {
        fn ruleset(&mut self, id: u32) -> Option<Value> {
            match id {
                0x2538 | 0x2819 | 0x32e8 => Some(Value::Float(1.0)),
                0x260f => Some(Value::Bool(true)),
                0x281a | 0x32e0 | 0x32e9 => Some(Value::Bool(false)),
                _ => None,
            }
        }

        fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
            if entity != 0xd000_0010 {
                return None;
            }
            self.0
                .mode_variables(0x0230_0000_0000_0018)
                .map(|values| values.get(&id).copied().map_or(Value::Null, Value::from))
        }

        fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
            match config.get("$")?.as_str()? {
                "STU_1FF67CA6" => Some(Value::Entity(site.entity)),
                "STU_310D6529" => Some(Value::Vector([0.0; 3])),
                "STU_37F20D24" => Some(Value::Asset(0x0230_0000_0000_0018)),
                "STU_A3DEF368" if self.0.mode_variables(0x0230_0000_0000_0018).is_some() => {
                    Some(Value::Entity(0xd000_0010))
                }
                "STU_D3EB8D87" | "STU_A77AC2AC" => Some(Value::Null),
                "STU_4A101787" => Some(Value::Bool(false)),
                _ => None,
            }
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
    }

    #[test]
    #[ignore = "requires a local 1.74 graph pack"]
    fn authored_helix_uses_its_six_second_cooldown_before_the_next_volley() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let body = &graphs.bodies()[&0x0400_0000_0000_03cf];
        let mut world = RifleWorld(Arc::clone(&graphs));
        let mut component = Component::start(
            Arc::clone(&graphs),
            0xa000_0101,
            player_entity_variables(body, 200.0, 200.0),
            player_roots(body, &graphs).unwrap(),
            1000,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.advance_frame(1000, &mut world).unwrap();
        let mut shots = Vec::new();
        let cooldown = Variable {
            scope: Scope::Instance,
            id: 61,
        };
        for frame in 1001..=1411 {
            let buttons = if matches!(frame, 1030 | 1031 | 1040 | 1041 | 1410 | 1411) {
                2
            } else {
                0
            };
            component
                .advance_command(
                    Command {
                        frame,
                        buttons,
                        ..Command::default()
                    },
                    &mut world,
                )
                .unwrap();
            shots.extend(std::mem::take(&mut component.machine.shots));
            let (flags, last_ms, remaining, due) = match frame {
                1029 => (0, 16000, 0.0, None),
                1030 => (5, 16480, 5.985000133514404, Some(22480)),
                1040 => (1, 16480, 5.824999809265137, Some(22480)),
                1404 => (1, 16480, 0.0009999275207519531, Some(22480)),
                1405 => (1, 16480, 0.0, None),
                1410 => (1, 22560, 5.985000133514404, Some(28560)),
                _ => continue,
            };
            let snapshot = component.snapshot().unwrap();
            let ability = &snapshot.instances[&11].states[&31];
            assert_eq!(ability.kind, StateKind::Ability { flags }, "frame {frame}");
            assert_eq!(
                ability.ability_cooldown,
                Some(AbilityCooldown {
                    current: if frame == 1029 { 0.0 } else { 6.0 },
                    rate: 1.0,
                    last_ms,
                }),
                "frame {frame}"
            );
            assert_eq!(
                component.resolved_variable(11, cooldown),
                Some(Value::Float(remaining)),
                "frame {frame}"
            );
            assert_eq!(
                snapshot
                    .pending_timers
                    .get(&11)
                    .into_iter()
                    .flatten()
                    .find(|timer| timer.raw_state == 31 && timer.parameter == 2)
                    .map(|timer| timer.due_ms),
                due,
                "frame {frame}"
            );
        }
        assert_eq!(
            shots
                .iter()
                .map(|shot| (
                    shot.instance,
                    shot.raw_state,
                    shot.time_ms,
                    shot.activation,
                    shot.ordinal
                ))
                .collect::<Vec<_>>(),
            [(11, 16, 16480, 1, 0), (11, 16, 22560, 2, 0)]
        );
        let shot = &shots[0];
        let node = component.shot_node(shot).unwrap();
        let rocket =
            crate::projectile_flight::ProjectileBallistics::from_authored(node, |config| {
                component.evaluate_shot_config(shot, config, &mut world)
            })
            .unwrap()
            .unwrap();
        assert_eq!(rocket.definition, Some(0x0400_0000_0000_072c));
        assert_eq!((rocket.speed, rocket.lifetime), (50.0, 2.0));
        let direct = rocket.direct.unwrap();
        assert_eq!(
            (direct.amount, direct.loadout),
            (40.0, Some(0x0b90_0000_0000_008e))
        );
        let blast = rocket.blast.unwrap();
        assert_eq!(
            (blast.hit.amount, blast.hit.loadout),
            (80.0, Some(0x0b90_0000_0000_008e))
        );
        assert_eq!(blast.rings, [(1.0, 1.0), (3.0, 0.5)]);
    }

    #[test]
    #[ignore = "requires a local 1.74 graph pack"]
    fn authored_soldier_rifle_holds_releases_and_reloads_on_the_reference_clock() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let body = &graphs.bodies()[&0x0400_0000_0000_03cf];
        let mut world = RifleWorld(Arc::clone(&graphs));
        let mut component = Component::start(
            Arc::clone(&graphs),
            0xa000_0101,
            player_entity_variables(body, 200.0, 200.0),
            player_roots(body, &graphs).unwrap(),
            999,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.advance_frame(999, &mut world).unwrap();
        let mut shots = Vec::new();
        let ammo = Variable {
            scope: Scope::Instance,
            id: 53,
        };
        let reload = Variable {
            scope: Scope::Instance,
            id: 51,
        };
        for frame in 1000..=1201 {
            let buttons = match frame {
                1030..=1073 | 1200 => 1,
                1095 => 0x400,
                _ => 0,
            };
            component
                .advance_command(
                    Command {
                        frame,
                        buttons,
                        ..Command::default()
                    },
                    &mut world,
                )
                .unwrap();
            shots.extend(std::mem::take(&mut component.machine.shots));
            let expected = match frame {
                1029 => Some(30),
                1030 | 1036 => Some(29),
                1074 | 1095 | 1141 => Some(23),
                1142 | 1188 => Some(30),
                1201 => Some(29),
                _ => None,
            };
            if let Some(expected) = expected {
                assert_eq!(
                    component.resolved_variable(11, ammo),
                    Some(Value::Integer(expected)),
                    "frame {frame}, rifle states {:?}, diagnostics {:?}",
                    active(component.snapshot().unwrap(), 11),
                    component
                        .snapshot()
                        .unwrap()
                        .diagnostics
                        .iter()
                        .filter(|d| d.site.graph == 0x0254)
                        .collect::<Vec<_>>()
                );
            }
            match frame {
                1095 | 1142 => assert_eq!(
                    component.resolved_variable(11, reload),
                    Some(Value::Bool(true))
                ),
                1188 => assert_eq!(
                    component.resolved_variable(11, reload),
                    Some(Value::Bool(false))
                ),
                _ => (),
            }
        }
        assert_eq!(
            shots
                .iter()
                .map(|shot| (shot.time_ms, shot.activation, shot.ordinal))
                .collect::<Vec<_>>(),
            [
                (16480, 1, 0),
                (16591, 1, 1),
                (16702, 1, 2),
                (16813, 1, 3),
                (16924, 1, 4),
                (17036, 1, 5),
                (17147, 1, 6),
                (19200, 2, 0),
            ]
        );
        assert!(
            shots
                .iter()
                .all(|shot| shot.instance == 11 && shot.raw_state == 10)
        );
        let shot = &shots[0];
        let node = component.shot_node(shot).unwrap();
        let mut evaluate = |config: &Json| component.evaluate_shot_config(shot, config, &mut world);
        let hit = crate::game_hitscan::HitDefinition::from_authored(
            node.field("m_6396149F").unwrap(),
            &mut evaluate,
        )
        .unwrap()
        .unwrap();
        assert_eq!(hit.amount, 20.0);
        assert_eq!(hit.loadout, Some(0x0b90_0000_0000_008d));
        assert_eq!(hit.critical, 2.0);
        assert_eq!(
            hit.falloff,
            Some(crate::game_hitscan::DamageFalloff {
                start: 30.0,
                end: 50.0,
                floor: f64::from(0.3_f32),
            })
        );
        assert_eq!(
            evaluate(node.field("m_FEC435C6").unwrap()),
            Ok(Value::Integer(180))
        );
        assert!(component.take_shots().is_none());
    }

    #[test]
    fn animation_callback_routes_a_typed_self_message_to_the_live_receiver() {
        let message = json!({"$":"STUConfigVarGameMessage","m_gameMessage":"0x0240000000000001"});
        let source = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateAnim",
                    Some(0),
                    json!({
                        "m_86701C3E":{"$":"STUConfigVarFloat","m_value":0.025},
                        "m_A3B86BB0":plug(&[(2,"m_inPlug")],&[]),
                        "m_onFinishedPlug":plug(&[(3,"m_inPlug")],&[])
                    }),
                ),
                node(
                    2,
                    "STUStatescriptActionSendGameMessage",
                    None,
                    json!({
                        "m_gameMessage":message,
                        "m_params":[{"m_B5051BCE":"0x0A40000000000123","m_value":true}]
                    }),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(101,false),"m_value":true}),
                ),
            ],
            json!([1]),
            json!([0]),
        );
        let receiver = graph(
            43,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateGameMessageEntry",
                    Some(0),
                    json!({
                        "m_gameMessage":message,
                        "m_33DA5163":plug(&[(2,"m_beginPlug")],&[1]),
                        "m_452862E6":plug(&[(3,"m_beginPlug")],&[2]),
                        "m_params":[{"m_B5051BCE":"0x0A40000000000123","m_out_Var":var(7,false)}]
                    }),
                ),
                node(2, "STUStatescriptState", Some(1), json!({})),
                node(3, "STU_691BFA55", Some(2), json!({})),
            ],
            json!([1, 2, 3]),
            json!([0]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![source, receiver])),
            42,
            Variables::new(),
            vec![Root::new(1, 42), Root::new(2, 43)],
            100,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let initial = component.snapshot().unwrap();
        assert_eq!(initial.instances[&1].states[&0].kind, StateKind::Anim);
        assert_eq!(active(initial, 2), [0, 1]);
        assert_eq!(
            initial.pending_timers[&1],
            [
                PendingTimer {
                    due_ms: 1625,
                    raw_state: 0,
                    finish: false,
                    parameter: 1
                },
                PendingTimer {
                    due_ms: 1625,
                    raw_state: 0,
                    finish: true,
                    parameter: 0
                },
            ]
        );
        component.advance_frame(100, &mut EmptyWorld).unwrap();
        assert_eq!(active(component.snapshot().unwrap(), 2), [0, 1]);
        component.advance_frame(101, &mut EmptyWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.instances[&1].variables[&101], Value::Bool(true));
        assert!(!snapshot.instances[&1].states[&0].active);
        assert_eq!(active(snapshot, 2), [0, 2]);
        assert_eq!(snapshot.instances[&2].variables[&7], Value::Bool(true));
        assert_eq!(snapshot.instances[&2].states[&2].kind, StateKind::Effect);
        assert_eq!(snapshot.instances[&2].states[&2].start_ms, 1625);
    }

    #[test]
    fn logical_button_block_resyncs_until_last_owner_exits() {
        let button = json!({"$":"STUConfigVarLogicalButton","m_logicalButton":20});
        let message = |position, key, target, input| {
            node(
                position,
                "STUStatescriptEntryGameMessage",
                None,
                json!({
                    "m_gameMessage":{"m_gameMessage":format!("0x024000000000{key:04X}")},
                    "m_outPlug":plug(&[(target,input)],&[]),
                }),
            )
        };
        let mut listener = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptStateLogicalButton",
                    Some(0),
                    json!({"m_logicalButton":button}),
                ),
                node(
                    1,
                    "STUStatescriptStateLogicalButton",
                    Some(1),
                    json!({
                        "m_logicalButton":button,"m_53C4B6A6":true,
                    }),
                ),
                node(
                    2,
                    "STU_E4A30BCF",
                    Some(2),
                    json!({"m_logicalButton":button}),
                ),
                node(
                    3,
                    "STU_7DEB522D",
                    None,
                    json!({
                        "m_logicalButton":button,
                        "m_truePlug":plug(&[(4,"m_inPlug")],&[]),
                        "m_falsePlug":plug(&[(5,"m_inPlug")],&[]),
                    }),
                ),
                node(
                    4,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(42,false),"m_value":true}),
                ),
                node(
                    5,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(42,false),"m_value":false}),
                ),
                node(
                    6,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(0,"m_beginPlug"),(1,"m_beginPlug")],&[])}),
                ),
                message(7, 1, 2, "m_beginPlug"),
                message(8, 2, 2, "m_F198FD3A"),
                message(9, 3, 3, "m_inPlug"),
            ],
            json!([0, 1, 2]),
            json!([6, 7, 8, 9]),
        );
        listener["fields"]["m_680A2CB2"] = json!([0, 1]);
        let other_block = graph(
            43,
            vec![
                node(
                    0,
                    "STU_E4A30BCF",
                    Some(0),
                    json!({"m_logicalButton":button}),
                ),
                message(1, 1, 0, "m_beginPlug"),
                message(2, 2, 3, "m_inPlug"),
                node(3, "STU_22926252", None, json!({})),
            ],
            json!([0]),
            json!([1, 2]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![listener, other_block])),
            42,
            Variables::new(),
            vec![Root::new(1, 42), Root::new(2, 43)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        component
            .advance_command(
                Command {
                    frame: 1,
                    buttons: 1,
                    ..Command::default()
                },
                &mut EmptyWorld,
            )
            .unwrap();
        let send = |component: &mut Component, id, key: u64| {
            assert_eq!(
                component
                    .queue_message(id, 0x0240_0000_0000_0000 | key, Variables::new())
                    .unwrap(),
                1
            );
            component.run_now(&mut EmptyWorld).unwrap();
        };
        let check = |component: &mut Component, held| {
            send(component, 1, 3);
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            let instance = &snapshot.instances[&1];
            assert_eq!(instance.variables[&42], Value::Bool(held));
            assert_eq!(
                instance.states[&0].kind,
                StateKind::LogicalButton { down: held }
            );
            assert_eq!(
                instance.states[&1].kind,
                StateKind::LogicalButton { down: true }
            );
        };
        check(&mut component, true);
        send(&mut component, 1, 1);
        check(&mut component, false);
        send(&mut component, 2, 1);
        send(&mut component, 1, 2);
        check(&mut component, false);
        send(&mut component, 2, 2);
        assert!(!component.snapshot().unwrap().instances.contains_key(&2));
        check(&mut component, true);
    }

    #[test]
    fn buffered_action_press_uses_the_shared_clock_and_releases_after_one_millisecond() {
        let mut fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({
                        "m_gameMessage":{"m_gameMessage":"0x0240000000000001"},
                        "m_outPlug":plug(&[(1,"m_beginPlug")],&[]),
                    }),
                ),
                node(
                    1,
                    "STUStatescriptStateLogicalButton",
                    Some(0),
                    json!({
                        "m_logicalButton":{"$":"STUConfigVarLogicalButton","m_logicalButton":15},
                        "m_246438AD":{"$":"STUConfigVarFloat","m_value":0.03},
                        "m_onGoingDownPlug":plug(&[(2,"m_inPlug")],&[]),
                        "m_onComingUpPlug":plug(&[(3,"m_inPlug")],&[]),
                    }),
                ),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(10,false), "m_value": true,
                    }),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(10,false), "m_value": false,
                    }),
                ),
            ],
            json!([1]),
            json!([0]),
        );
        fixture["fields"]["m_680A2CB2"] = json!([0]);
        let graphs = Arc::new(store(vec![fixture]));
        for (dead, delay) in [(false, 0), (false, 2), (true, 0)] {
            let mut component = Component::start(
                Arc::clone(&graphs),
                42,
                Variables::new(),
                vec![Root::new(1, 42)],
                100,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            component.set_owner_dead(dead);
            component
                .advance_command(
                    Command {
                        frame: 100,
                        action: 1,
                        ..Command::default()
                    },
                    &mut EmptyWorld,
                )
                .unwrap();
            if delay != 0 {
                component
                    .advance_frame(100 + delay, &mut EmptyWorld)
                    .unwrap();
            }
            let buffered = !dead && delay == 0;
            assert_eq!(
                component
                    .queue_message(1, 0x0240_0000_0000_0001, Variables::new())
                    .unwrap(),
                1
            );
            component.run_now(&mut EmptyWorld).unwrap();
            let snapshot = component.snapshot().unwrap();
            let instance = &snapshot.instances[&1];
            assert_eq!(instance.states[&0].activation, 1);
            assert_eq!(
                instance.states[&0].kind,
                StateKind::LogicalButton { down: buffered }
            );
            if buffered {
                assert_eq!(instance.variables[&10], Value::Bool(true));
                assert_eq!(
                    snapshot.pending_timers[&1],
                    [PendingTimer {
                        due_ms: 1616,
                        raw_state: 0,
                        finish: false,
                        parameter: 1
                    }]
                );
                component.advance_frame(101, &mut EmptyWorld).unwrap();
                let snapshot = component.snapshot().unwrap();
                assert_eq!(
                    snapshot.instances[&1].states[&0].kind,
                    StateKind::LogicalButton { down: false }
                );
                assert_eq!(snapshot.instances[&1].variables[&10], Value::Bool(false));
                assert!(snapshot.pending_timers.is_empty());
            } else {
                assert!(instance.variables.get(&10).is_none());
                assert!(snapshot.pending_timers.is_empty());
            }
        }
    }

    #[test]
    #[ignore = "requires a local 1.74 graph pack"]
    fn soldier_sprint_graph_reads_primary_crouch_and_melee_edges_as_in_python() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut component = Component::start(
            graphs,
            0xa000_0101,
            Variables::new(),
            vec![Root::new(5, 0x0255)],
            1029,
            &mut ReferenceWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let sprint = |snapshot: &Snapshot| {
            [4, 7, 16].map(|raw| {
                (
                    snapshot.instances[&5].states[&raw].kind,
                    snapshot.instances[&5].states[&raw].activation,
                )
            })
        };
        let up = StateKind::LogicalButton { down: false };
        let down = StateKind::LogicalButton { down: true };
        assert_eq!(sprint(component.snapshot().unwrap()), [(up, 1); 3]);
        component
            .advance_command(
                Command {
                    frame: 1030,
                    buttons: 0x881,
                    ..Command::default()
                },
                &mut ReferenceWorld,
            )
            .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(sprint(snapshot), [(down, 1); 3]);
        assert_eq!(snapshot.instances[&5].variables[&1919], Value::Bool(true));
        assert!(!snapshot.initialization_complete());
        component
            .advance_command(
                Command {
                    frame: 1031,
                    ..Command::default()
                },
                &mut ReferenceWorld,
            )
            .unwrap();
        assert_eq!(sprint(component.snapshot().unwrap()), [(up, 1); 3]);
    }

    #[test]
    #[ignore = "requires a local 1.74 Practice Range NPC graph pack with graph 0B92"]
    fn firing_placement_reset_and_timer_boundaries_match_python() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        assert!(
            graphs.graph(0x0b92).is_some(),
            "the runtime pack lacks Practice Range graph 0B92"
        );
        let mut component = Component::start(
            graphs,
            0xa100_0000,
            Variables::new(),
            vec![Root::new(1, 0x0b92)],
            100,
            &mut ReferenceWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        // Python BotWorld with no selected AI target, frame 100, scoped RESET.
        assert_eq!(
            component
                .queue_message(1, 0x0240_0000_0000_0013, Variables::new())
                .unwrap(),
            1
        );
        component.run_now(&mut ReferenceWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(active(snapshot, 1), [5, 12]);
        assert_eq!(snapshot.instances[&1].variables[&889], Value::Bool(false));
        assert_eq!(
            snapshot.pending_timers[&1],
            [
                PendingTimer {
                    due_ms: 2100,
                    raw_state: 5,
                    finish: false,
                    parameter: 1
                },
                PendingTimer {
                    due_ms: 4350,
                    raw_state: 12,
                    finish: false,
                    parameter: 1
                },
            ]
        );
        for frame in 101..=130 {
            component.advance_frame(frame, &mut ReferenceWorld).unwrap();
        }
        assert_eq!(component.now_ms(), 2095);
        assert_eq!(active(component.snapshot().unwrap(), 1), [5, 12]);
        component.advance_frame(131, &mut ReferenceWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(active(snapshot, 1), [12, 0]);
        assert_eq!(
            snapshot.instances[&1].states[&0].kind,
            StateKind::AiCandidate { selected: false }
        );
        assert_eq!(snapshot.instances[&1].states[&0].start_ms, 2100);
        assert_eq!(snapshot.instances[&1].variables[&889], Value::Bool(false));
        for frame in 132..=270 {
            component.advance_frame(frame, &mut ReferenceWorld).unwrap();
        }
        assert_eq!(component.now_ms(), 4335);
        assert_eq!(active(component.snapshot().unwrap(), 1), [12, 0]);
        component.advance_frame(271, &mut ReferenceWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(active(snapshot, 1), [0]);
        assert_eq!(snapshot.instances[&1].variables[&889], Value::Bool(true));
        assert!(snapshot.pending_timers.is_empty());
    }

    #[test]
    #[ignore = "requires an authorized 104319 graph pack in ORPHEUS_RUNTIME_DIR"]
    fn controller_and_training_bot_supported_startup_edges_match_python() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs = GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap();
        let run = |entity, roots| {
            initialize(
                &graphs,
                entity,
                Variables::new(),
                roots,
                &mut ReferenceWorld,
                MAX_INIT_WORK,
            )
        };
        let hud = run(0xa000_0100, vec![Root::new(1, 0x20e4)]).unwrap();
        assert!(hud.initialization_complete(), "{:?}", hud.diagnostics);
        assert_eq!(active(&hud, 1), [25]);

        let mut controller = Root::new(1, 0x13c1);
        controller.variables.insert(8419, Value::Bool(true));
        let owner = run(0xa000_0100, vec![controller]).unwrap();
        let instances: Vec<_> = owner
            .instances
            .iter()
            .map(|(&id, instance)| (id, instance.graph, instance.parent))
            .collect();
        // Python Component with the practice controller and v8419 enabled.
        assert_eq!(
            instances,
            [
                (1, 0x13c1, None),
                (2, 0x20e4, Some((1, 42))),
                (3, 0x288a, Some((1, 43))),
                (4, 0x288b, Some((3, 0))),
                (5, 0x288d, Some((3, 1))),
            ]
        );
        assert_eq!(active(&owner, 2), [25]);
        assert!(!owner.initialization_complete());

        // Only the enriched pack includes the NPC body bindings.
        if let Some(body) = graphs.entity_body(0x0400_0000_0000_1403) {
            let roots = body_roots(body).unwrap();
            assert_eq!(
                roots
                    .iter()
                    .map(|root| (root.id, root.graph))
                    .collect::<Vec<_>>(),
                [
                    (1, 0x0033),
                    (2, 0x0ba8),
                    (3, 0x0be2),
                    (4, 0x0ba3),
                    (5, 0x174f),
                    (6, 0x2206)
                ],
            );
            assert_eq!(roots[0].variables[&199], Value::Integer(200));
            let bot = run(0xa100_0000, vec![Root::new(1, 0x0c18)]).unwrap();
            assert_eq!(bot.instances[&2].graph, 0x0d24);
            assert_eq!(bot.instances[&2].parent, Some((1, 3)));
            assert_eq!(active(&bot, 2), [56, 76]);
            assert!(!bot.initialization_complete());
        }
    }

    #[test]
    fn ai_control_state_finishes_at_begin_time_only_for_an_absent_owner_relation() {
        const OWNER: u32 = 0xa000_0101;
        const FRAME: u32 = 100;
        struct AiWorld(Option<u32>);
        impl World for AiWorld {
            fn related_entity(
                &mut self,
                site: Site,
                relation: EntityRelation,
                entity: u32,
            ) -> Option<u32> {
                assert_eq!(site.node, Some(1));
                assert_eq!(relation, EntityRelation::AiControlEntity);
                assert_eq!(entity, OWNER);
                self.0
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let owner = json!({"$":"STU_B5A0CAF0","m_EE729DCB":0});
        for (target, relation, finishes) in [
            (owner.clone(), Some(0), true),
            (owner.clone(), None, false),
            (owner, Some(OWNER + 1), false),
            (var(100, true), Some(0), false),
        ] {
            let graphs = Arc::new(store(vec![graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")], &[])}),
                    ),
                    node(
                        1,
                        "STU_066B4605",
                        Some(0),
                        json!({
                            "m_00D49978":target,
                            "m_D66E7D68":{"$":"STUConfigVarInt","m_value":0},
                            "m_F2810377":{"$":"STUConfigVarIdentifier","m_identifier":"0x0D8000000000023A"},
                            "m_F8585ABF":{"$":"STUConfigVarBool","m_value":0},
                            "m_priority":{"$":"STU_8A491388","m_priority":"0x0C40000000000002"},
                            "m_onBeginPlug":plug(&[(2,"m_inPlug")], &[]),
                            "m_onFinishedPlug":plug(&[(3,"m_inPlug")], &[]),
                            "m_onAbortPlug":plug(&[(4,"m_inPlug")], &[]),
                        }),
                    ),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100,false), "m_value":true}),
                    ),
                    node(
                        3,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101,false), "m_value":var(100,false)}),
                    ),
                    node(
                        4,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101,false), "m_value":false}),
                    ),
                ],
                json!([1]),
                json!([0]),
            )]));
            let component = Component::start(
                graphs,
                OWNER,
                Variables::new(),
                vec![Root::new(1, 42)],
                FRAME,
                &mut AiWorld(relation),
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            let instance = &snapshot.instances[&1];
            assert_eq!(component.now_ms(), frame_start(FRAME));
            assert!(snapshot.pending_timers.is_empty());
            if finishes {
                assert!(snapshot.initialization_complete());
                let state = &instance.states[&0];
                assert!(!state.active);
                assert_eq!(state.activation, 1);
                assert_eq!(state.start_ms, frame_start(FRAME));
                assert_eq!(instance.variables[&101], Value::Bool(true));
            } else {
                assert!(matches!(
                    snapshot.diagnostics.as_slice(),
                    [Diagnostic {
                        issue: Issue::UnsupportedField(
                            "AI weapon control component" | "AI weapon target"
                        ),
                        ..
                    }]
                ));
                assert!(instance.states[&0].active);
                assert!(!instance.variables.contains_key(&101));
            }
        }
    }
    #[test]
    fn missing_world_queries_report_expression_sites_without_writing_guessed_values() {
        let foreign = 0xa000_0101;
        for (opcodes, expected) in [
            (
                json!([8, 0, 50, 0]),
                EvalError::UnavailableEntityPosition(foreign),
            ),
            (
                json!([8, 0, 7, 1, 15, 0]),
                EvalError::UnavailableEntityVariable {
                    entity: foreign,
                    id: 100,
                },
            ),
        ] {
            let config = json!({"$":"STU_919FD47C","m_expression":{
                "m_opcodes":opcodes,"m_dynamicVars":[var(99, true), var(100, true)]
            }});
            let fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                    ),
                    node(
                        1,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101, true), "m_value":config}),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            let snapshot = initialize(
                &store(vec![fixture]),
                0xa000_0100,
                Variables::from([(99, Value::Entity(foreign))]),
                vec![Root::new(1, 42)],
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(!snapshot.initialization_complete());
            assert!(!snapshot.variables.contains_key(&101));
            assert_eq!(
                snapshot.diagnostics,
                [Diagnostic {
                    site: Site {
                        entity: 0xa000_0100,
                        instance: 1,
                        graph: 42,
                        node: Some(1),
                    },
                    issue: Issue::Expression(expected),
                }],
            );
        }
    }

    #[test]
    fn own_entity_member_keeps_known_unset_values_and_reactive_variable_reads() {
        let entity = 0xa000_0100;
        let condition = json!({"$":"STU_919FD47C","m_expression":{
            "m_opcodes":[8,0,7,1,15,0],"m_dynamicVars":[var(99,true),var(100,true)]
        }});
        let fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateBooleanSwitch",
                    Some(0),
                    json!({"m_condition":condition}),
                ),
            ],
            json!([1]),
            json!([0]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            entity,
            Variables::from([(99, Value::Entity(entity))]),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        for (value, expected) in [
            (None, false),
            (Some(Value::Bool(true)), true),
            (Some(Value::Null), false),
        ] {
            if let Some(value) = value {
                component
                    .set_entity_variable(100, value, &mut EmptyWorld)
                    .unwrap();
                component.run_now(&mut EmptyWorld).unwrap();
            }
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.instances[&1].states[&0].kind,
                StateKind::BooleanSwitch(expected),
            );
        }
    }

    #[test]
    fn shared_charge_contract() {
        fn guid(value: &Json) -> u64 {
            u64::from_str_radix(value.as_str().unwrap().trim_start_matches("0x"), 16).unwrap()
        }
        fn decode(value: &Json) -> Value {
            if let Some(asset) = value.get("asset") {
                Value::Asset(guid(asset))
            } else if let Some(entity) = value.get("entity").and_then(Json::as_u64) {
                Value::Entity(entity.try_into().unwrap())
            } else if let Some(flag) = value.as_bool() {
                Value::Bool(flag)
            } else if let Some(number) = value.as_f64() {
                Value::Float(number)
            } else {
                assert!(value.is_null());
                Value::Null
            }
        }
        struct Host<'a> {
            owner: u32,
            target: u32,
            case: &'a Json,
        }
        impl World for Host<'_> {
            fn position(&mut self, _: u32) -> Option<[f64; 3]> {
                None
            }
            fn entities_hostile(
                &self,
                _: Site,
                source: u32,
                target: u32,
                option: bool,
            ) -> Option<bool> {
                if source != self.owner
                    || target != self.target
                    || option
                    || self.case["context"] == false
                {
                    return None;
                }
                crate::junkenstein_host::ordinary_hostility(
                    self.case["source_flags"].as_u64().unwrap_or(0x800001),
                    self.case["target_flags"].as_u64().unwrap_or(0x1000001),
                )
            }
            fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
                (entity == self.target).then(|| {
                    if id == 602 {
                        Value::Bool(self.case["no_charge"].as_bool().unwrap_or(false))
                    } else {
                        Value::Null
                    }
                })
            }
            fn has_target_tag(&self, _: Site, entity: u32, tag: u64) -> Option<bool> {
                (entity == self.target).then(|| {
                    self.case["target_tags"]
                        .as_array()
                        .is_some_and(|rows| rows.iter().any(|row| guid(row) == tag))
                })
            }
        }
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-charge-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["case_id"], "statescript.charge.v1");
        assert_eq!(fixture["target_build"], 104319);
        let cases = fixture["input"]["cases"].as_array().unwrap();
        let results = fixture["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), results.len());
        for (case, expected) in cases.iter().zip(results) {
            assert_eq!(case["name"], expected["name"]);
            if case["kind"] == "corpse" {
                let actual = crate::junkenstein_host::common_corpse_due(
                    guid(&case["hero"]),
                    case["death_frame"]
                        .as_u64()
                        .map(|value| u32::try_from(value).unwrap()),
                    u32::try_from(case["frame"].as_u64().unwrap()).unwrap(),
                );
                assert_eq!(
                    json!({"retire":actual}),
                    expected["output"],
                    "{}",
                    case["name"]
                );
                continue;
            }
            let owner = fixture["input"]["owner"].as_u64().unwrap() as u32;
            let target = fixture["input"]["target"].as_u64().unwrap() as u32;
            let mut world = Host {
                owner,
                target,
                case,
            };
            let graphs = Arc::new(store(vec![fixture["input"]["graph"].clone()]));
            let mut variables = Variables::from([
                (338, Value::Float(200.0)),
                (342, Value::Float(0.0)),
                (2426, Value::Bool(false)),
                (842, Value::Bool(false)),
            ]);
            if let Some(initial) = case["initial"].as_object() {
                for (key, value) in initial {
                    variables.insert(key.parse().unwrap(), decode(value));
                }
            }
            let mut root = Root::new(1, 0x0b8b);
            root.variables.insert(393, Value::Entity(target));
            root.variables.insert(1, decode(&case["variable"]));
            let mut component = Component::start(
                graphs,
                owner,
                variables,
                vec![root],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let actual = match case["kind"].as_str().unwrap() {
                "query" => {
                    match component.evaluate_state_config(1, 0, &case["query"], &mut world) {
                        Ok(Value::Bool(value)) => json!({"hostile":value}),
                        Err(EvalError::UnsupportedConfig(class)) => json!({"error":class}),
                        result => panic!("{}: {result:?}", case["name"]),
                    }
                }
                "tags" => {
                    use crate::{
                        game_hitscan::{DamageTags, HitDefinition, HitscanShot, TargetPose},
                        projectile_flight::{Blast, ProjectileBallistics, ProjectileFlight},
                    };
                    let hit = HitDefinition::from_authored(&case["hit"], |cfg| {
                        component.evaluate_state_config(1, 0, cfg, &mut world)
                    })
                    .unwrap()
                    .unwrap();
                    if case["name"] == "damage-tags-budget-sixteen" {
                        let mut oversized = case["hit"].clone();
                        let rows = oversized["m_modifyHealth"]["m_148F3152"]
                            .as_array_mut()
                            .unwrap();
                        rows.push(rows[0].clone());
                        assert!(
                            HitDefinition::from_authored(&oversized, |cfg| component
                                .evaluate_state_config(1, 0, cfg, &mut world))
                            .unwrap()
                            .is_none(),
                            "over-budget tags must reject the payload, never truncate it"
                        );
                    }
                    let expected_tags = expected["output"]["tags"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(guid)
                        .collect::<Vec<_>>();
                    assert_eq!(hit.tags.as_slice(), expected_tags, "{}", case["name"]);
                    let pose = TargetPose {
                        feet: [0.0, 0.0, 3.0],
                        height: 2.0,
                        radius: 0.5,
                        eye: 1.55,
                    };
                    let shot =
                        HitscanShot::with_direction([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 100.0, hit);
                    let impact = shot
                        .resolve([(target, pose)], |_, _, _| Ok(None))
                        .unwrap()
                        .unwrap();
                    assert_eq!(impact.tags, hit.tags, "hitscan must retain tags");
                    let mut flight = ProjectileFlight::new(
                        ProjectileBallistics {
                            speed: 50.0,
                            lifetime: 2.0,
                            definition: None,
                            direct: Some(hit),
                            blast: Some(Blast {
                                hit: HitDefinition {
                                    tags: DamageTags::EMPTY,
                                    ..hit
                                },
                                rings: vec![(1.0, 1.0)],
                            }),
                        },
                        [0.0, 1.0, 0.0],
                        [0.0, 0.0, 1.0],
                        None,
                    )
                    .unwrap();
                    assert!(
                        flight
                            .advance(0, &[(target, pose)], |_, _, _| Ok(None))
                            .unwrap()
                            .is_none()
                    );
                    let end = flight
                        .advance(100, &[(target, pose)], |_, _, _| Ok(None))
                        .unwrap()
                        .unwrap();
                    assert_eq!(
                        end.impacts[0].tags, hit.tags,
                        "direct projectile must retain its payload tags"
                    );
                    assert!(end.impacts.len() > 1);
                    assert!(
                        end.impacts[1..]
                            .iter()
                            .all(|impact| impact.tags == DamageTags::EMPTY),
                        "blast must retain its own tags"
                    );
                    json!({"tags":hit.tags.as_slice().iter().map(|guid|format!("0x{guid:016X}")).collect::<Vec<_>>()})
                }
                "flow" => {
                    let mut observed = Vec::new();
                    let mut last_frame = 0;
                    for step in case["steps"].as_array().unwrap() {
                        match step["op"].as_str().unwrap() {
                            "set" => {
                                for (key, value) in step["values"].as_object().unwrap() {
                                    component
                                        .set_entity_variable(
                                            key.parse().unwrap(),
                                            decode(value),
                                            &mut world,
                                        )
                                        .unwrap();
                                }
                                component.run_now(&mut world).unwrap();
                            }
                            "advance" => {
                                let frame = step["frame"].as_u64().unwrap() as u32;
                                for selected in last_frame + 1..=frame {
                                    component.advance_frame(selected, &mut world).unwrap();
                                }
                                assert!(frame > last_frame);
                                last_frame = frame;
                            }
                            "damage" => {
                                component
                                    .queue_message(
                                        1,
                                        0x0240_0000_0000_0012,
                                        Variables::from([
                                            (0x13, Value::Float(step["amount"].as_f64().unwrap())),
                                            (0x15, Value::Entity(target)),
                                        ]),
                                    )
                                    .unwrap();
                                component.run_now(&mut world).unwrap();
                            }
                            "poll" => component.run_now(&mut world).unwrap(),
                            "observe" => (),
                            op => panic!("unknown operation {op}"),
                        }
                        let snapshot = component.snapshot().unwrap();
                        assert!(
                            snapshot.initialization_complete(),
                            "{}: {:?}",
                            case["name"],
                            snapshot.diagnostics
                        );
                        observed.push(json!({"charge_bits":format!("{:016x}",script_expr::to_float(&snapshot.variables[&342]).to_bits()),"waiting":snapshot.instances[&1].states.get(&4).is_some_and(|state|state.active)}));
                    }
                    json!({"steps":observed})
                }
                kind => panic!("unknown case kind {kind}"),
            };
            assert_eq!(actual, expected["output"], "{}", case["name"]);
        }
    }
}
