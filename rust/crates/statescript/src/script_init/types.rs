//! StateScript runtime snapshots, state payloads, diagnostics, and fault types.

use std::{
    collections::{BTreeMap, HashSet, VecDeque},
    error::Error,
    fmt,
    num::NonZeroU32,
};

use indexmap::IndexMap;

use crate::{
    script_expr::{EvalError, StackPriority, Variable},
    script_runtime::{BOSS_PAUSE, QueueFull},
    script_voice::IntroCue,
    script_wire::{Value, Variables},
};

use super::{
    ABILITY_RUNNING,
    machine::VarKey,
    world::{
        AiBehaviorRequest, AiControlOverride, AiFacing, AiWeaponOverride, Site, TargetTagEffect,
    },
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SprayPlacement {
    /// Point and normal are local to the attachment when one is present.
    pub point: [f32; 3],
    pub normal: [f32; 3],
    /// The original spray direction stays in world space.
    pub direction: [f32; 3],
    pub attachment: Option<SprayAttachment>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SprayAttachment {
    pub entity: u32,
    /// None selects the entity root transform.
    pub key: Option<NonZeroU32>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum StateKind {
    RollMovementParameter,
    ExplosionContact,
    TrackedTargets,
    GenericTrackedTargets,
    ViewTargets {
        next_sample: u32,
    },
    DeadeyeEffects,
    WitchStaffEffect,
    WitchStaffHealing,
    SummonerBeamRay,
    GroundRay,
    SummonerBeamDamage,
    ModifyHealth,
    GenericModifyHealth {
        continuous: bool,
        on_enter: bool,
    },
    Pet {
        entity: Option<u32>,
    },
    ShieldRecharge,
    NamedShieldDecay,
    ForCode,
    AiBehavior {
        record: Option<AiBehaviorRequest>,
    },
    AiControlSetting,
    AiWeaponFire(Option<i64>),
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
    MovementSample {
        flags: u16,
        pose_frame: Option<u32>,
        sampled_at: Option<u32>,
    },
    Anim,
    Effect,
    ClientPulse {
        count: u8,
        fresh_at: Option<u64>,
    },
    EmptyTargets,
    SendMessage,
    ProjectedSelfSend,
    PersistentSend(u64),
    CosmeticEntity,
    SprayPlacement(Option<(u64, SprayPlacement)>),
    ChaseVar,
    Watch,
    Observe {
        sampled_frame: u32,
    },
    TrackEntities {
        sampled_frame: u32,
    },
    Interval {
        period_frames: NonZeroU32,
        next_frame: Option<u32>,
    },
    SpawnSearch,
    RecentInput,
    LivingBodyArray,
    Shockwave {
        managed: bool,
    },
    PlacementSelector {
        confirmed: bool,
        sampled: bool,
    },
    StaticTrace {
        origin: [f64; 3],
        direction: [f64; 3],
        speed: f64,
        /// Authored ray length; the arrival re-cast uses the same parameters,
        /// and static geometry cannot change between begin and arrival.
        length: f64,
        /// Traveled distance along the launch line: the promoted anchor gap,
        /// the begin-time hit distance, or the full length.
        distance: f64,
        /// Resolved static collision category of the authored profile group.
        category: u16,
        /// Rope anchor: the raw hit point, or the validated wall-to-walkable-
        /// top transition when a near-lip candidate promoted. None on a miss.
        anchor: Option<[f64; 3]>,
        /// Unadjusted point on the centre ray, if it made contact.
        raw_hit: Option<[f64; 3]>,
        /// Arrival normal: the hit face, the face behind the promoted lip,
        /// or the probed floor normal for a centre-miss aim assist.
        face: Option<[f64; 3]>,
        promoted: bool,
        /// Promoted standing feet destination on the validated top (the
        /// movement target the wall branch's MoveTo consumes); None keeps
        /// the direct hit's raw point.
        standing: Option<[f64; 3]>,
    },
    MoveTo {
        movement: Option<([f64; 3], f64)>,
    },
    CombatModFilter {
        registered: bool,
    },
    DeflectProjectiles {
        reflect: bool,
        half_angle_deg: f64,
    },
    GenericContactSweep,
    TargetAura {
        effect: u64,
    },
    TargetSubScript {
        child: Option<u16>,
    },
    ConstantAccel,
    WallClimb,
    Grapple,
    RecallHistory,
    RecallRewind,
    BallRoll {
        fireball: bool,
    },
    SpatialQuery,
    PhysicsExplosion,
    HitTracker {
        hit_count: u16,
    },
    RayCast,
    EntityAttach {
        target: u32,
    },
    ModifierControl {
        blocks_input: bool,
    },
    KinematicsControl {
        mode: u8,
    },
    PlacementCast {
        valid: bool,
    },
    TeleportEffect,
    ProximityTrigger {
        triggered: bool,
    },
    EntityStatus {
        mode: u8,
    },
    UxPresenter,
    SyncVars,
    HeroOverride,
    AiTactic,
    FieldDrivenState,
    FilterArray,
    WeaponVolley {
        volleys: u8,
        offset_ms: u64,
        ammo0: i64,
        ammo_var: Option<Variable>,
    },
}

#[derive(Clone, Copy, Debug)]
pub(super) struct BufferedPress {
    pub(super) time_ms: u64,
    pub(super) activation: u16,
    pub(super) button: u8,
}

/// A fixed world anchor and the cable length owned by the active graph state.
/// The mover applies it to the displacement it will pass to its collision sweep.
#[derive(Clone, Copy, Debug)]
pub struct TetherConstraint {
    pub(super) anchor: [f64; 3],
    pub(super) attach_offset: [f64; 3],
    pub(super) rope_len: f64,
    pub(super) min_rope_len: f64,
}

impl TetherConstraint {
    fn radius(self, feet: [f64; 3]) -> [f64; 3] {
        std::array::from_fn(|i| feet[i] - self.anchor[i] + self.attach_offset[i])
    }

    pub fn length(&self) -> f64 {
        self.rope_len
    }

    pub fn attachment_distance(&self, feet: [f64; 3]) -> f64 {
        let [x, y, z] = self.radius(feet);
        x.hypot(y).hypot(z)
    }

    pub(super) fn shorten_at(&mut self, feet: [f64; 3]) {
        let length = self.attachment_distance(feet);
        if length.is_finite() {
            self.rope_len = self.rope_len.min(length.max(self.min_rope_len));
        }
    }

    /// Return a shorter step only when this input would extend a taut cable.
    /// A prior contact correction can put the feet outside: never pull an idle
    /// body to an old position to repair that difference.
    pub fn constrain_step(self, feet: [f64; 3], step: [f64; 3]) -> Option<[f64; 3]> {
        let radius = self.radius(feet);
        let end: [f64; 3] = std::array::from_fn(|i| radius[i] + step[i]);
        let allowed = self
            .rope_len
            .max(radius[0].hypot(radius[1]).hypot(radius[2]));
        let distance = end[0].hypot(end[1]).hypot(end[2]);
        (distance.is_finite() && distance > allowed).then(|| {
            let scale = allowed / distance;
            std::array::from_fn(|i| end[i] * scale - radius[i])
        })
    }
}

#[derive(Clone, Debug)]
pub struct State {
    pub tracked_targets: Vec<u32>,
    pub(super) ground_wave: Option<Box<super::world::GroundWave>>,
    pub(super) grapple: Option<Box<TetherConstraint>>,
    pub(super) target_children: BTreeMap<u32, u16>,
    pub active: bool,
    pub activation: u16,
    pub start_ms: u64,
    pub kind: StateKind,
    pub(crate) voice: Option<Box<crate::script_voice::State>>,
    pub chase: Option<ChasePayload>,
    pub ability_cooldown: Option<AbilityCooldown>,
    pub(super) watch_seen: Option<Value>,
    pub(super) spawn_search: Option<SpawnSearch>,
    pub(super) recent_inputs: Vec<(usize, u64)>,
    pub(super) ending: bool,
    pub(super) pending_change: bool,
    pub(super) pending_timer: bool,
    pub(super) delayed_timers: [bool; 2],
    pub(super) buffered_press: Option<BufferedPress>,
    pub(super) logical_button_binding: Option<u8>,
    pub(super) logical_button_revision: u32,
    pub(super) linked_variable: Option<VarKey>,
    pub(super) ability_stack: Option<VarKey>,
    pub(super) death_generation: Option<u64>,
    pub(super) untrusted_native: bool,
}

impl State {
    pub fn ability_running(&self) -> bool {
        self.active
            && matches!(self.kind, StateKind::Ability { flags } if flags & ABILITY_RUNNING != 0)
    }

    /// Whether a watched evaluation for this state has ever read a native
    /// outside the reference's client-agreed set.
    pub fn reads_untrusted_native(&self) -> bool {
        self.untrusted_native
    }

    pub fn tether(&self) -> Option<TetherConstraint> {
        self.grapple.as_deref().copied().filter(|_| self.active)
    }

    pub fn move_request(&self) -> Option<([f64; 3], f64)> {
        if !self.active {
            return None;
        }
        match self.kind {
            StateKind::MoveTo { movement } => movement,
            _ => None,
        }
    }

    pub(super) fn accepts_move_result(&self, key: MoveToKey) -> bool {
        !self.ending
            && self.activation == key.activation
            && self.start_ms == key.start_ms
            && self.move_request().is_some()
    }

    pub(super) fn dormant(kind: StateKind) -> Self {
        Self {
            tracked_targets: Vec::new(),
            ground_wave: None,
            grapple: None,
            target_children: BTreeMap::new(),
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
            logical_button_binding: None,
            logical_button_revision: 0,
            linked_variable: None,
            ability_stack: None,
            death_generation: None,
            untrusted_native: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SpawnSearchKey {
    pub(super) instance: u16,
    pub(super) raw: u16,
    pub(super) activation: u16,
    pub(super) start_ms: u64,
}

impl SpawnSearchKey {
    pub const fn instance(self) -> u16 {
        self.instance
    }

    pub const fn raw(self) -> u16 {
        self.raw
    }

    pub const fn activation(self) -> u16 {
        self.activation
    }

    pub const fn start_ms(self) -> u64 {
        self.start_ms
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpawnSearch {
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
    pub(super) fn remaining(self, now_ms: u64) -> f64 {
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum InstanceScope {
    Projected,
    ServerOnly,
    Isolated,
}

impl InstanceScope {
    pub(super) fn is_isolated(self) -> bool {
        self == Self::Isolated
    }

    pub(super) fn is_server_only(self) -> bool {
        self != Self::Projected
    }

    pub(super) fn hide_if(self, server_only: bool) -> Self {
        if server_only && self == Self::Projected {
            Self::ServerOnly
        } else {
            self
        }
    }
}

#[derive(Clone, Debug)]
pub struct Instance {
    pub graph: u32,
    pub(super) start_ms: u64,
    pub(super) retire_after_frame: Option<u32>,
    pub(super) scope: InstanceScope,
    /// Parent instance and raw state index, as carried on the wire.
    pub parent: Option<(u16, u16)>,
    pub variables: Variables,
    pub states: IndexMap<u16, State>,
    pub(super) join_inputs: BTreeMap<u16, HashSet<(u16, String)>>,
}

impl Instance {
    pub fn started_at_ms(&self) -> u64 {
        self.start_ms
    }

    pub fn is_server_only(&self) -> bool {
        self.scope.is_server_only()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Issue {
    MissingGraph(u32),
    MissingNode(u16),
    MissingState(u16),
    UnsupportedAction(String),
    UnsupportedState(String),
    UnsupportedMessageRegistration,
    UnsupportedField(&'static str),
    EntityCreation(String),
    InvalidSubScript,
    Expression(EvalError),
    ObservationUnavailable,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Diagnostic {
    pub site: Site,
    pub issue: Issue,
    /// An existing placement or registration guard rejected this node.
    pub registration_rejected: bool,
    /// Retained for server diagnostics without blocking client projection.
    pub server_only: bool,
}

/// The outcome of one owner travel request, reported to the state that
/// authored it through the MoveTo lifecycle.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MoveOutcome {
    /// The body's feet reached the authored destination.
    Arrived,
    /// A posed capsule sweep stopped short of its requested displacement.
    Blocked,
}

/// Activation-scoped identity of the state owning one in-flight move
/// request. Hosts report results with this key alone; it embeds no graph,
/// node or hero identity.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct MoveToKey {
    pub instance: u16,
    pub raw: u16,
    pub activation: u16,
    pub start_ms: u64,
}

/// A read-only checkpoint of a component's random stream.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RandomCheckpoint {
    pub model: &'static str,
    pub initial_seed: u64,
    /// Draws since construction, modulo 2^64.
    pub draws: u64,
}

/// Bounded evidence of node targets encountered by the VM.
#[derive(Clone, Debug, Default)]
pub struct NodeObservations {
    sites: BTreeMap<(u32, u16), bool>,
    truncated: bool,
}

impl NodeObservations {
    /// The observation limit used by ordinary diagnostic builds.
    pub const DEFAULT_SITE_LIMIT: usize = 128;

    #[cfg(feature = "node-observations")]
    pub(super) const SITE_LIMIT: usize = if cfg!(feature = "extended-node-observations") {
        4096
    } else {
        Self::DEFAULT_SITE_LIMIT
    };

    /// Each graph and node location, with whether its client scope skipped it.
    pub fn sites(&self) -> impl ExactSizeIterator<Item = (u32, u16, bool)> + '_ {
        self.sites
            .iter()
            .map(|(&(graph, node), &client_scope_skipped)| (graph, node, client_scope_skipped))
    }

    pub fn truncated(&self) -> bool {
        self.truncated
    }

    #[cfg(feature = "node-observations")]
    pub(super) fn record(&mut self, graph: u32, node: u16, client_scope_skipped: bool) {
        if self.truncated {
            return;
        }
        let full = self.sites.len() == Self::SITE_LIMIT;
        if let std::collections::btree_map::Entry::Vacant(site) = self.sites.entry((graph, node)) {
            if full {
                self.truncated = true;
            } else {
                site.insert(client_scope_skipped);
            }
        }
    }
}

#[derive(Clone, Debug)]
pub struct Snapshot {
    pub entity: u32,
    pub now_ms: u64,
    /// Monotonically incremented whenever any projected field of the snapshot
    /// (variables, instances, states, bindings, callouts, diagnostics) mutates.
    pub revision: u64,
    pub variables: Variables,
    pub instances: IndexMap<u16, Instance>,
    /// Pending state timers and finishes in their per-instance execution order.
    pub pending_timers: IndexMap<u16, Vec<PendingTimer>>,
    /// Stack links by (instance or zero for the entity, variable).
    pub bindings: IndexMap<(u16, u32), Vec<StackBinding>>,
    /// Effective linked values, separate from their stored bases.
    pub resolved_bindings: IndexMap<(u16, u32), Value>,
    pub frame_actions: VecDeque<FrameAction>,
    pub diagnostics: Vec<Diagnostic>,
    /// The component policy this snapshot was produced under. The projection
    /// applies the reference observer rule only to a non-strict snapshot:
    /// an active remote state whose class has no verified payload writer is
    /// omitted from the state table (`observers.py` warn-and-continue),
    /// never projected with an unverified payload. A strict snapshot keeps
    /// the loud error because strict components must not replicate.
    pub strict: bool,
    #[cfg(feature = "node-observations")]
    pub(super) node_observations: NodeObservations,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameCallout {
    pub serial: u32,
    pub instance: u16,
    pub position: u16,
}

#[derive(Clone, Debug, PartialEq)]
pub struct FrameAction {
    pub callout: FrameCallout,
    /// Effective values at dispatch. Owner zero means an entity variable.
    pub values: BTreeMap<(u16, u32), Value>,
}

impl Snapshot {
    /// Dispatch-target evidence, when compiled with node observations enabled.
    pub fn node_observations(&self) -> Option<&NodeObservations> {
        #[cfg(feature = "node-observations")]
        {
            Some(&self.node_observations)
        }
        #[cfg(not(feature = "node-observations"))]
        {
            None
        }
    }

    pub fn boss_pause_active(&self) -> bool {
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
        self.diagnostics
            .iter()
            .all(|diagnostic| diagnostic.issue == Issue::ObservationUnavailable)
    }

    /// True if any active state's projected wire payload depends on `now_ms`
    /// advancing while the rest of the snapshot is unchanged.
    pub fn has_time_sensitive_projection(&self) -> bool {
        self.instances.values().any(|instance| {
            instance.states.values().any(|state| {
                matches!(
                    state.kind,
                    StateKind::ClientPulse {
                        fresh_at: Some(at),
                        ..
                    } if state.active && self.now_ms.saturating_sub(at) <= 600
                )
            })
        })
    }
}

#[derive(Debug, Eq, PartialEq)]
pub enum InitFault {
    InvalidBudget,
    InvalidRoot(u16),
    InstanceIdExhausted,
    MissingPlayerGraph(u32),
    StatusScript {
        graph: u32,
        diagnostic: Box<Diagnostic>,
    },
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
            Self::StatusScript { graph, diagnostic } => write!(
                f,
                "status graph {graph:04X} failed at {:?}: {:?}",
                diagnostic.site, diagnostic.issue
            ),
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

/// The authored presentation inputs sampled when an authoritative shot fires.
#[derive(Clone, Debug)]
pub struct ReportedShot {
    pub event: ShotEvent,
    pub presentation: Vec<(Variable, Value)>,
    /// Active remote-networked `m_subgraphPlug` child state slots of the volley at fire time.
    pub presentation_states: Vec<u16>,
    /// Normalized spread for the authored direct-launch ramp at this round.
    pub normalized_spread: Option<f32>,
}
