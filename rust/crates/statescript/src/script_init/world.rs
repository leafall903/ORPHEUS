//! World query traits and parameter payloads for StateScript execution.

use std::{collections::BTreeMap, sync::Arc};

use serde_json::Value as Json;

use crate::{
    script_entity_state::EntityScriptState,
    script_expr::{self, EntityRelation, OnFire, RoomEntityFacts, SpatialCandidate, StackPriority},
    script_graph::{HostEventKind, MAX_ROOM_MESSAGE_PARAMETERS, Node},
    script_runtime::PoolKind,
    script_voice::IntroCue,
    script_wire::{Value, Variables},
};

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Site {
    pub entity: u32,
    pub instance: u16,
    pub graph: u32,
    pub node: Option<u16>,
}

/// One authored hostile body overlap, resolved at the current posed input.
#[derive(Clone, Copy, Debug)]
pub struct ContactVolume {
    pub source_frame: u32,
    pub offset: [f64; 3],
    pub sight_origin: [f64; 3],
    pub radius: f64,
    pub profile: u64,
}

#[derive(Clone, Copy, Debug)]
pub struct ContactTarget {
    pub entity: u32,
    pub feet: [f64; 3],
}

/// A tracked entity and the surface range measured during the same spatial query.
#[derive(Clone, Copy, Debug)]
pub struct TrackedTarget {
    pub entity: u32,
    pub surface_distance: Option<f64>,
}

impl From<u32> for TrackedTarget {
    fn from(entity: u32) -> Self {
        Self {
            entity,
            surface_distance: None,
        }
    }
}

/// The body, aperture, collision profile and ranking requested by a view tracker.
#[derive(Debug)]
pub struct ViewTargetQuery<'a> {
    pub source: u32,
    pub offset: [f64; 3],
    /// When set, the vector is a world-space eye origin and the heading is in degrees.
    pub eye_heading: Option<f64>,
    pub range: f64,
    pub aperture: f64,
    pub limit: usize,
    pub profile: u64,
    pub masks: [u64; 3],
    pub ranking: &'a Json,
    pub sight: Option<&'a ViewSightProfile>,
}

/// Room collision settings for a configured view query. Dynamic blockers use
/// the room's actor flags, which are distinct from asset receiver masks.
#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ViewSightProfile {
    pub static_mask: u16,
    pub block_actor_flags: u64,
    pub samples: Vec<f64>,
}

/// Authoritative movement facts captured from a single owner pose.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MovementSample {
    pub flags: u16,
    pub air_ticks: u32,
    /// Source frame of the movement record, when known.
    pub pose_frame: Option<u32>,
}

/// A finite, nonzero movement request bounded to 32 world units.
/// The host applies it through its ordinary collision and movement path.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SweptDisplacement {
    delta: [f32; 3],
}

impl SweptDisplacement {
    pub fn new(delta: [f64; 3]) -> Option<Self> {
        const MAX_DISTANCE: f64 = 32.0;
        let length = delta.into_iter().fold(0.0_f64, f64::hypot);
        if !length.is_finite() || length <= 0.0 || length > MAX_DISTANCE {
            return None;
        }
        let delta = delta.map(|axis| axis as f32);
        let length = delta
            .into_iter()
            .fold(0.0_f64, |length, axis| length.hypot(f64::from(axis)));
        (length.is_finite() && length > 0.0 && length <= MAX_DISTANCE).then_some(Self { delta })
    }

    pub fn delta(self) -> [f32; 3] {
        self.delta
    }
}

/// A finite destination whose distance is checked against the consuming body's pose.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Relocation {
    destination: [f64; 3],
}

impl Relocation {
    pub fn new(destination: [f64; 3]) -> Option<Self> {
        destination
            .into_iter()
            .all(|axis| axis.is_finite() && (axis as f32).is_finite())
            .then_some(Self { destination })
    }

    /// Keep the requested feet coordinates; only finite displacements within the
    /// swept-motion reach bound are admissible for `ScriptedMotion::Relocate`.
    pub fn destination_from(self, origin: [f64; 3]) -> Option<[f64; 3]> {
        let delta = std::array::from_fn(|axis| self.destination[axis] - origin[axis]);
        SweptDisplacement::new(delta).map(|_| self.destination)
    }

    /// Long-range teleport destination (Reaper Shadow Step, Sombra Translocator,
    /// Symmetra Teleporter, Tracer Recall, Lua Mods) without the 32m cap.
    pub fn teleport_destination(self) -> Option<[f64; 3]> {
        const MAX_AXIS: f64 = 4096.0;
        self.destination
            .into_iter()
            .all(|v| v.is_finite() && v.abs() <= MAX_AXIS)
            .then_some(self.destination)
    }
}

/// One scheduled body motion, with distinct path and destination collision policies.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ScriptedMotion {
    Sweep(SweptDisplacement),
    Relocate(Relocation),
    Teleport(Relocation),
}

/// A bounded, typed message evaluated by the sending StateScript VM.
#[derive(Clone, Debug, PartialEq)]
pub struct RoomMessage {
    pub id: u64,
    pub parameters: Variables,
}

impl RoomMessage {
    pub(super) fn is_valid(&self) -> bool {
        self.id != 0
            && self.parameters.len() <= MAX_ROOM_MESSAGE_PARAMETERS
            && self.parameters.iter().all(|(&slot, value)| {
                slot != 0
                    && u16::try_from(slot).is_ok()
                    && match value {
                        Value::Null | Value::Bool(_) | Value::Integer(_) | Value::Asset(_) => true,
                        Value::Float(value) => value.is_finite(),
                        Value::Entity(entity) => script_expr::valid_entity(*entity),
                        Value::Vector(parts) => parts.iter().all(|part| part.is_finite()),
                        _ => false,
                    }
            })
    }
}

/// An authored cue admitted by the room host for explicit listeners.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RoomCue {
    pub stimulus: u64,
    pub line: u64,
    pub speaker: RoomCueSpeaker,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RoomCueSpeaker {
    Actor,
    Mode,
}

impl RoomCue {
    pub const MAX_TARGETS: usize = 64;
}

// Host admission bound, independent of the size of a native tag list.
pub(super) const MAX_HIT_TAGS: usize = 16;

/// Bounded, ordered asset tags supplied by a settled hit.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct HitTags {
    values: [u64; MAX_HIT_TAGS],
    len: usize,
}

impl HitTags {
    pub const EMPTY: Self = Self {
        values: [0; MAX_HIT_TAGS],
        len: 0,
    };

    pub fn as_slice(&self) -> &[u64] {
        &self.values[..self.len]
    }

    pub fn push(&mut self, guid: u64) -> bool {
        if self.len == MAX_HIT_TAGS {
            return false;
        }
        self.values[self.len] = guid;
        self.len += 1;
        true
    }
}

/// Damage drained from a named pool during settlement.
#[derive(Clone, Debug, PartialEq)]
pub struct NamedPoolLoss {
    pub identifier: u64,
    pub amount: f64,
}

/// Encode the drained named pools in settlement order with native float precision.
pub fn named_pool_records(losses: &[NamedPoolLoss]) -> Value {
    Value::Array(
        losses
            .iter()
            .filter_map(|loss| {
                let amount = f64::from(loss.amount as f32);
                (amount.is_finite() && amount > 0.0).then(|| {
                    Value::Map(vec![
                        (Value::Integer(16056), Value::Asset(loss.identifier)),
                        (Value::Integer(499), Value::Float(amount)),
                    ])
                })
            })
            .collect(),
    )
}

/// Authoritative notification emitted by the simulation after a health change.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum HostEvent {
    HealthApplied {
        source: Option<u32>,
        target: u32,
        delta: f64,
        killed: bool,
        /// Optional asset list for parameter 5. An unavailable list leaves the
        /// parameter absent and clears a declared binding through message delivery.
        hit_tags: Option<HitTags>,
    },
}

impl HostEvent {
    pub(super) fn source(self) -> Option<u32> {
        let Self::HealthApplied { source, .. } = self;
        source
    }

    pub(super) fn target(self) -> u32 {
        let Self::HealthApplied { target, .. } = self;
        target
    }

    pub(super) fn bindings_for(
        self,
        receiver: u32,
        context: &HostHealthContext,
    ) -> Option<(HostEventKind, Variables)> {
        match self {
            Self::HealthApplied {
                source,
                target,
                delta,
                killed,
                hit_tags,
            } => {
                if !script_expr::valid_entity(target)
                    || source.is_some_and(|source| !script_expr::valid_entity(source))
                    || (receiver != target && source != Some(receiver))
                    || !delta.is_finite()
                    || delta == 0.0
                    || (killed && delta > 0.0)
                {
                    return None;
                }
                let mut parameters = Variables::from([
                    (1, source.map_or(Value::Null, Value::Entity)),
                    (2, Value::Entity(target)),
                    (3, Value::Float(delta)),
                    (4, Value::Bool(killed)),
                ]);
                if let Some(tags) = hit_tags {
                    parameters.insert(
                        5,
                        Value::Array(tags.as_slice().iter().copied().map(Value::Asset).collect()),
                    );
                }
                if let Some(losses) = context.named_pool_losses.as_deref().filter(|_| delta < 0.0) {
                    parameters.insert(6, named_pool_records(losses));
                }
                if let Some(critical) = context.critical_hit.filter(|_| delta < 0.0) {
                    parameters.insert(7, Value::Bool(critical));
                }
                if let Some(frame) = context.damage_frame.filter(|_| delta < 0.0) {
                    parameters.insert(8, Value::Integer(i64::from(frame)));
                }
                Some((HostEventKind::HealthApplied, parameters))
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RoomEntityStatus {
    /// None means an independent participant.
    pub team: Option<u8>,
    /// Liveness after the authoritative health mutation.
    pub alive: bool,
}

/// A room-resolved body identity. Generations are opaque to StateScript.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RoomBodyIdentity {
    pub wire: u32,
    pub generation: u64,
}

/// Host-only context scoped to the immediate health event delivery.
#[derive(Clone, Default)]
pub struct HostHealthContext {
    pub source_body: Option<RoomBodyIdentity>,
    pub target_body: Option<RoomBodyIdentity>,
    pub target_facts: Option<Arc<HealthTargetFacts>>,
    /// Optional settlement records for damage parameter 6. None leaves it absent.
    pub named_pool_losses: Option<Arc<[NamedPoolLoss]>>,
    /// Confirmed impact classification for damage parameter 7.
    pub critical_hit: Option<bool>,
    /// Authoritative room frame of damage settlement for parameter 8.
    pub damage_frame: Option<u32>,
}

/// Room-sampled facts for the original target of a settled health event.
#[derive(Clone, Debug)]
pub struct HealthTargetFacts {
    pub target: u32,
    pub filter_flags: Option<u64>,
    /// None means no room affiliation or liveness was sampled.
    pub room_status: Option<RoomEntityStatus>,
    pub tags: TargetTagSnapshot,
    pub script_state: EntityScriptState,
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
#[derive(Clone, Debug, PartialEq)]
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

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum LandingSelection {
    Unhandled,
    Invalid,
    Point([f64; 3]),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TargetTagEffect {
    pub entity: u32,
    pub tag: u64,
    pub present: bool,
}

#[derive(Clone, Debug, Default)]
pub struct TargetTagSnapshot {
    base: Option<Arc<std::collections::BTreeSet<u64>>>,
    overrides: BTreeMap<u64, bool>,
}

impl TargetTagSnapshot {
    pub(super) fn has_known_base(&self) -> bool {
        self.base.is_some()
    }

    pub fn presence(&self, tag: u64) -> Option<bool> {
        self.overrides
            .get(&tag)
            .copied()
            .or_else(|| self.base.as_ref().map(|base| base.contains(&tag)))
    }
}

#[derive(Clone, Default)]
pub struct TargetTagEffects(BTreeMap<u64, TargetTagCounts>);

#[derive(Clone, Default)]
pub(super) struct TargetTagCounts {
    grants: u32,
    suppressions: u32,
}

impl TargetTagEffects {
    pub fn snapshot(&self, base: Option<&std::collections::BTreeSet<u64>>) -> TargetTagSnapshot {
        TargetTagSnapshot {
            base: base.cloned().map(Arc::new),
            overrides: self
                .0
                .iter()
                .map(|(&tag, counts)| (tag, counts.suppressions == 0))
                .collect(),
        }
    }

    pub fn presence(&self, tag: u64) -> Option<bool> {
        self.0.get(&tag).map(|counts| counts.suppressions == 0)
    }

    pub fn apply(&mut self, effect: TargetTagEffect, active: bool) -> bool {
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

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct GenericBeamContact {
    pub entity: Option<u32>,
    pub hit: bool,
    pub point: Option<[f64; 3]>,
    pub normal: Option<[f64; 3]>,
    pub distance: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VelocityAxis {
    All,
    Horizontal,
    Vertical,
    VerticalUp,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PetSpawn {
    pub definition: u64,
    pub attach: Option<u32>,
    pub position: Option<[f64; 3]>,
    pub bone_attached: bool,
    pub possessable: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DeflectState {
    pub reflect: bool,
    pub half_angle_deg: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CombatModEntry {
    pub stat: u64,
    pub multiplicative: bool,
    pub value: f64,
    pub cc_immune: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum VelocityOp {
    AddVector([f64; 3]),
    SetVector([f64; 3]),
    SetHorizontalVector([f64; 2]),
    SetSpeed {
        axis: VelocityAxis,
        value: f64,
    },
    CapSpeed {
        axis: VelocityAxis,
        max: f64,
    },
    ScaleSpeed {
        axis: VelocityAxis,
        factor: f64,
    },
    ClampSpeed {
        axis: VelocityAxis,
        min: f64,
        max: f64,
    },
}

/// Geometry captured once when a travelling ground wave begins.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GroundWave {
    pub centre: [f64; 3],
    pub sight_origin: [f64; 3],
    pub yaw: f64,
    pub aperture: f64,
    pub distance: f64,
    pub speed: f64,
    pub max_rise: f64,
    pub max_drop: f64,
    pub ground_range: f64,
}

impl GroundWave {
    pub fn reach(&self, elapsed_ms: u64) -> f64 {
        (self.speed * elapsed_ms as f64 / 1000.0).min(self.distance)
    }
}

/// External authored queries for this initialization. The caller owns mode,
/// ruleset, and entity data and can decline a query without guessing its value.
/// The mover dimensions the hook promotion measures a lip against, taken
/// from the owner body's own character-mover settings (standing capsule,
/// step-down band, walkable slope cosine).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct HookPullGeometry {
    pub radius: f64,
    pub segment: f64,
    pub step_down: f64,
    pub slope_cos: f64,
}

pub trait World {
    fn edit_velocity(&mut self, _site: Site, _frame: u32, _value: Value, _cap: bool) -> bool {
        false
    }

    /// Queue a horizontal velocity cap for the frame after the action ran.
    fn cap_horizontal_velocity(&mut self, _site: Site, _frame: u32, _cap: f64) -> bool {
        false
    }

    /// Queue a vertical reset and a world-space impulse as one movement edit.
    fn apply_velocity_impulse(
        &mut self,
        _site: Site,
        _frame: u32,
        _vertical: f64,
        _delta: [f64; 3],
    ) -> bool {
        false
    }

    /// Whether generic `STU_FF3471E9` velocity ops should route through
    /// `apply_velocity_ops` for this site.
    fn admits_velocity_ops(&self, site: Site) -> bool {
        self.admits_generic_player_runtime(site)
    }

    /// Queue an ordered list of authored `STU_FF3471E9` velocity operations.
    fn apply_velocity_ops(&mut self, _site: Site, _frame: u32, _ops: &[VelocityOp]) -> bool {
        false
    }

    /// Queue a horizontal swept blink along current movement throttle (or facing).
    fn queue_blink(&mut self, _site: Site, _frame: u32, _distance: f64) -> bool {
        false
    }

    fn summoner_beam_contact(&self, _site: Site) -> Option<BeamContact> {
        None
    }

    fn generic_beam_contact(
        &self,
        _site: Site,
        _origin: Option<[f64; 3]>,
        _direction: Option<[f64; 3]>,
        _length: Option<f64>,
        _radius: f64,
        _filter: &Json,
    ) -> Option<GenericBeamContact> {
        None
    }

    fn queue_modify_health(
        &mut self,
        site: Site,
        target: u32,
        delta: f64,
        _impulse: Option<[f64; 3]>,
    ) -> bool {
        if delta != 0.0 {
            self.queue_health_change(site, target, delta)
        } else {
            false
        }
    }

    fn spawn_pet(&mut self, _site: Site, _spawn: PetSpawn) -> Option<u32> {
        None
    }

    fn despawn_pet(&mut self, _site: Site, _entity: u32) -> bool {
        false
    }

    fn request_possess_entity(&mut self, _site: Site, _target: u32) -> bool {
        false
    }

    /// Echo Duplicate (`STUStatescriptStateCreateEntity` with `STU_0AC15F78`):
    /// copy `target`'s hero body for the duplicate duration.
    fn duplicate_target_entity(&mut self, _site: Site, _target: u32) -> bool {
        false
    }

    fn set_combat_mod(&mut self, _site: Site, _entry: Option<CombatModEntry>) -> bool {
        false
    }

    fn set_deflect_state(&mut self, _site: Site, _state: Option<DeflectState>) -> bool {
        false
    }

    fn set_target_aura(
        &mut self,
        _site: Site,
        _targets: &[u32],
        _effect: u64,
        _param: Option<f64>,
        _elapsed_ms: u64,
    ) -> bool {
        false
    }

    fn record_recall_sample(&mut self, _site: Site, _window_secs: f64) -> bool {
        false
    }

    fn execute_recall(&mut self, _site: Site, _frame: u32) -> Option<[f64; 3]> {
        None
    }

    fn set_ball_roll_state(&mut self, _site: Site, _active: bool, _max_speed: f64) -> bool {
        false
    }

    fn apply_physics_explosion(
        &mut self,
        _site: Site,
        _center: [f64; 3],
        _radius: f64,
        _impulse: f64,
        _direction: Option<[f64; 3]>,
    ) -> bool {
        false
    }

    fn summoner_beam_damage(&mut self, _site: Site, _target: u32, _amount: f64) -> bool {
        false
    }
    fn body_display_name(&self, _site: Site, _entity: u32) -> Option<String> {
        None
    }
    /// Whether this site's map physics backend has dynamic rigid bodies.
    /// None means the host cannot resolve the site or its backend.
    fn has_dynamic_map_rigid_bodies(&self, _site: Site) -> Option<bool> {
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
    #[allow(clippy::too_many_arguments)]
    fn cone_targets(
        &self,
        _site: Site,
        _origin: u32,
        _offset: [f64; 3],
        _radius: f64,
        _angle: f64,
        _visible: bool,
        _filter: &Json,
    ) -> Option<Vec<u32>> {
        None
    }

    #[allow(clippy::too_many_arguments)]
    fn cone_targets_with_cylinder(
        &self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        angle: f64,
        _cylinder_radius: f64,
        visible: bool,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        self.cone_targets(site, origin, offset, radius, angle, visible, filter)
    }

    #[allow(clippy::too_many_arguments)]
    fn cone_targets_directed(
        &self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        angle: f64,
        cylinder_radius: f64,
        _yaw_deg: Option<f64>,
        _pitch_deg: Option<f64>,
        visible: bool,
        filter: &Json,
    ) -> Option<Vec<TrackedTarget>> {
        self.cone_targets_with_cylinder(
            site,
            origin,
            offset,
            radius,
            angle,
            cylinder_radius,
            visible,
            filter,
        )
        .map(|ids| ids.into_iter().map(Into::into).collect())
    }

    fn entity_alive(&self, _site: Site, _entity: u32) -> bool {
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

    #[allow(clippy::too_many_arguments)]
    fn swept_tracked_targets(
        &mut self,
        site: Site,
        origin: u32,
        start_offset: [f64; 3],
        _end_offset: Option<[f64; 3]>,
        _local_space: bool,
        radius: f64,
        _visible: bool,
        filter: &Json,
    ) -> Option<Vec<TrackedTarget>> {
        Some(
            self.tracked_targets(site, origin, start_offset, radius, filter)?
                .into_iter()
                .map(TrackedTarget::from)
                .collect(),
        )
    }

    /// None leaves the view query unavailable. An empty list is a completed query.
    fn view_targets(&self, _site: Site, _query: &ViewTargetQuery<'_>) -> Option<Vec<u32>> {
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
    /// Admit a signed health request for application by the world.
    fn queue_health_change(&mut self, _site: Site, _target: u32, _delta: f64) -> bool {
        false
    }
    /// Admit every signed change together or leave the host queue unchanged.
    fn queue_health_changes(&mut self, _site: Site, _changes: &[(u32, f64)]) -> bool {
        false
    }

    fn contact_volume_targets(
        &self,
        _site: Site,
        _volume: ContactVolume,
    ) -> Option<Vec<ContactTarget>> {
        None
    }

    fn queue_contact_damage(
        &mut self,
        _site: Site,
        _source_frame: u32,
        _damage: f64,
        _changes: &[(u32, Option<[f64; 3]>)],
    ) -> bool {
        false
    }
    /// Admit movement for the bound body at the supplied future simulation frame.
    /// None means unavailable; Some(false) is an ordinary supported refusal.
    fn queue_scripted_motion(
        &mut self,
        _site: Site,
        _frame: u32,
        _motion: ScriptedMotion,
    ) -> Option<bool> {
        None
    }
    /// Compatibility entry for hosts and graphs that only schedule swept motion.
    fn queue_swept_displacement(
        &mut self,
        site: Site,
        frame: u32,
        displacement: SweptDisplacement,
    ) -> Option<bool> {
        self.queue_scripted_motion(site, frame, ScriptedMotion::Sweep(displacement))
    }
    /// Restore the owner's shield pools. A zero amount checks support without changing health.
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
    /// The current body supports the static protected-entity rule with an enabled baseline.
    fn supports_static_model_protection(&self, _site: Site) -> bool {
        false
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
    fn queue_modify_health_with_meta(
        &mut self,
        site: Site,
        target: u32,
        delta: f64,
        impulse: Option<[f64; 3]>,
        _loadout: Option<u64>,
        _tags: HitTags,
    ) -> bool {
        self.queue_modify_health(site, target, delta, impulse)
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

    fn boss_intro_cue(&self, _node: &Node) -> Option<IntroCue> {
        None
    }
    fn play_boss_intro_voice(&mut self, _site: Site, _cue: IntroCue) -> Option<u64> {
        None
    }
    fn is_victory_voice(&self, _node: &Node) -> bool {
        false
    }
    fn play_victory_voice(&mut self, _site: Site, _actor: &Value) -> Option<u64> {
        None
    }
    fn gate_voice_cue(&self, _node: &Node) -> Option<u64> {
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
    /// Whether the native voice-category service is available to this VM.
    fn voice_category_service_available(&self, _site: Site) -> Option<bool> {
        None
    }
    /// Whether the bound controller exposes a player stats record to this VM.
    /// A missing service also rules out its own record.
    fn player_stats_available(&self, site: Site) -> Option<bool> {
        self.player_stats_service_available(site)
            .filter(|&available| !available)
    }

    /// Whether any player stats are exposed to this VM's bound controller.
    /// None means the host cannot resolve this binding or its service.
    fn player_stats_service_available(&self, _site: Site) -> Option<bool> {
        None
    }
    /// Whether the local combat accumulator is available to this state's owner.
    /// `None` means the host cannot establish availability for this binding.
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
    /// Admit or release an authored self-facing claim, keyed by its originating site.
    fn set_facing_claim(&mut self, _site: Site, _record: AiFacing, _active: bool) -> Option<bool> {
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

    /// View pitch in degrees for GetViewPitch operands (the hook-line and
    /// wheel-aim accessors). None means the pose does not expose a pitch.
    fn pitch(&self, _entity: u32) -> Option<f64> {
        None
    }

    /// Authored operands may use the room's actor sample instead of a profile-specific lookup.
    fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.position(entity)
    }

    fn authored_forward(&self, entity: u32) -> Option<[f64; 3]> {
        self.forward(entity)
    }

    /// The current world sample's eye anchor for a known body.
    fn body_eye_position(&self, _entity: u32) -> Option<[f64; 3]> {
        None
    }

    /// World-space position of an authored attachment on this body's selected model.
    fn body_hardpoint_position(&self, _entity: u32, _hardpoint: u64) -> Option<[f64; 3]> {
        None
    }

    /// A zero bone selects the body frame. A named bone requires its selected model's transform.
    fn body_bone_point(&mut self, entity: u32, bone: u64, local: [f64; 3]) -> Option<[f64; 3]> {
        if bone != 0 || !crate::script_expr::valid_entity(entity) {
            return None;
        }
        let origin = self
            .authored_position(entity)
            .or_else(|| self.known_position(entity))?;
        crate::script_expr::body_space_point(origin, self.authored_forward(entity), local)
    }

    /// Opaque identity of a live actor in the current room sample. Compare
    /// tokens only for the same entity ID within this room.
    fn room_actor_identity(&self, _entity: u32) -> Option<u64> {
        None
    }

    /// A known model can confirm presence or absence. None means unavailable.
    /// Outer None means no query service. Inner None is a completed miss.
    fn spray_placement(
        &self,
        _reach: f64,
        _command_frame: Option<u32>,
    ) -> Option<Option<super::types::SprayPlacement>> {
        None
    }

    fn equipped_cosmetic(
        &self,
        _entity: u32,
        _slot: usize,
        _cosmetic: script_expr::EquippedCosmetic,
    ) -> Option<u64> {
        None
    }

    fn body_has_hardpoint(&self, _site: Site, _entity: u32, _hardpoint: u64) -> Option<bool> {
        None
    }

    /// Activity of the component's own body at this site. None means unavailable.
    fn body_active(&self, _site: Site) -> Option<bool> {
        None
    }

    /// None means the current candidate snapshot is unavailable.
    fn spatial_candidates(&self) -> Option<&[SpatialCandidate]> {
        None
    }

    /// Includes registered bodies that are dead. None means no facts are available.
    fn room_entities(&self) -> Option<&BTreeMap<u32, RoomEntityFacts>> {
        None
    }

    /// Whether static geometry leaves the segment clear. None is unavailable.
    fn spatial_line_clear(&self, _origin: [f64; 3], _target: [f64; 3]) -> Option<bool> {
        None
    }

    /// Profile 002C selects static ability collision in build 104319.
    fn ability_ground_ray(
        &self,
        _site: Site,
        _frame: u32,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _length: f64,
    ) -> Option<Option<script_expr::SpatialRayHit>> {
        None
    }

    /// None is unavailable, Some(None) is an available miss.
    fn spatial_ray_cast(
        &self,
        _site: Site,
        _vm_frame: u32,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _max_distance: f64,
    ) -> Option<Option<script_expr::SpatialRayHit>> {
        None
    }

    /// A static cast using the owner's own collision category.
    fn profiled_static_ray(
        &self,
        _site: Site,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _max_distance: f64,
        _category: u16,
    ) -> Option<Option<script_expr::SpatialRayHit>> {
        None
    }

    /// The mover geometry a hook launch measures a ledge lip against: the
    /// owner's own capsule sizes, step band and walkable slope (T-0013 uses
    /// only these, never a hero table or a new constant). Worlds with no
    /// character mover answer None and the trace keeps the direct pull.
    fn hook_pull_geometry(&self, _site: Site) -> Option<HookPullGeometry> {
        None
    }

    /// The owner body's mover collision category (the team's proxy bit), as
    /// the movement pipeline carries it. None when the body has no mover.
    fn owner_collision_category(&self, _site: Site) -> Option<u16> {
        None
    }

    /// None is unavailable, Some(None) is an available miss.
    fn spatial_actor_ray_cast(
        &self,
        _site: Site,
        _vm_frame: u32,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _max_distance: f64,
        _targets: &[u32],
    ) -> Option<Option<script_expr::SpatialActorRayHit>> {
        None
    }

    /// The implicit owner's pose flags and air duration, if the host can read them.
    fn movement_sample(&self, _site: Site) -> Option<MovementSample> {
        None
    }

    fn engine_setting(&self, _site: Site, _key: u32) -> script_expr::EngineSetting {
        script_expr::EngineSetting::Unavailable
    }

    /// Whether the process has a local presentation command at this site.
    /// Remote player and AI commands do not supply this fact.
    fn local_command_present(&self, _site: Site) -> Option<bool> {
        None
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

    /// Acquire a temporary shield and return its release handle. Concurrent
    /// nonstacking registrations for one identifier share a balance but
    /// release independently.
    fn add_temporary_shield(
        &mut self,
        _site: Site,
        _identifier: u64,
        _amount: f64,
        _stacked: bool,
    ) -> Option<u32> {
        None
    }

    fn temporary_shield_current(&self, _site: Site, _identifier: u64) -> Option<f64> {
        None
    }

    /// Drain only the named pool and return its remaining amount.
    fn drain_temporary_shield(
        &mut self,
        _site: Site,
        _identifier: u64,
        _amount: f64,
    ) -> Option<f64> {
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
    /// Resolve a scene filter during delivery of a settled health target.
    fn settled_scene_filter_policy(
        &self,
        site: Site,
        filter: &Json,
        _target: &HealthTargetFacts,
    ) -> Option<script_expr::SceneFilterPolicy> {
        self.scene_filter_policy(site, filter)
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

    /// Resolve hostility using a target sampled at health settlement.
    fn settled_entities_hostile(
        &self,
        site: Site,
        source: u32,
        target: &HealthTargetFacts,
        option: bool,
    ) -> Option<bool> {
        self.entities_hostile(site, source, target.target, option)
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

    /// Speed for walking under this host's authored AI navigation.
    fn ai_walk_speed(&self, _site: Site) -> Option<f64> {
        None
    }

    fn selects_ai_behavior(&mut self, _site: Site) -> Option<bool> {
        None
    }

    /// Resolve the generation in the room snapshot used to admit script effects.
    fn room_body_generation(&self, _wire: u32) -> Option<u64> {
        None
    }

    /// Admit an authored message for a different body. Delivery happens later.
    fn queue_room_message(&mut self, _site: Site, _target: u32, _message: RoomMessage) -> bool {
        false
    }

    /// Admit a cue for a bounded set of listeners. Delivery happens later.
    fn queue_room_cue(&mut self, _site: Site, _targets: &[u32], _cue: RoomCue) -> bool {
        false
    }

    /// Whether this actor has a room-backed voice catalog.
    fn queue_equipped_voice(
        &mut self,
        _site: Site,
        _slot: usize,
        _choose: &mut dyn FnMut(usize) -> usize,
    ) -> bool {
        false
    }

    fn actor_voice_available(&self, _site: Site) -> bool {
        false
    }

    /// Resolve an animation duration in seconds when `m_playOverDuration` is omitted.
    fn animation_duration(
        &mut self,
        _site: Site,
        _blend_tree: u64,
        _alias: u64,
        _category: u64,
    ) -> Option<f64> {
        None
    }

    /// Resolve a criterion to a cue for the actor and its audience.
    fn queue_actor_voice(
        &mut self,
        _site: Site,
        _criterion: u64,
        _choose: &mut dyn FnMut(usize) -> usize,
    ) {
    }

    /// Resolve the speaker specified by the graph independently of the script owner.
    fn queue_actor_voice_with_audience(
        &mut self,
        site: Site,
        criterion: u64,
        speaker: u32,
        _subject: Option<u32>,
        _audience: Option<&[u32]>,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        if speaker == site.entity {
            self.queue_actor_voice(site, criterion, choose);
        }
    }

    /// An owner death continuation may speak on its own still-current body generation.
    fn queue_owner_death_voice(
        &mut self,
        site: Site,
        criterion: u64,
        subject: Option<u32>,
        audience: Option<&[u32]>,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        if self.actor_voice_available(site) {
            self.queue_actor_voice_with_audience(
                site,
                criterion,
                site.entity,
                subject,
                audience,
                choose,
            );
        }
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

    fn ground_wave_targets(
        &self,
        _site: Site,
        _wave: &GroundWave,
        _reach: f64,
    ) -> Option<Vec<u32>> {
        None
    }

    fn shockwave_targets(&mut self, _site: Site, _reach: f64) -> Option<Vec<u32>> {
        None
    }

    fn modify_health(&mut self, _site: Site, _elapsed_ms: u64) -> bool {
        false
    }

    fn select_landing(&mut self, _site: Site) -> LandingSelection {
        LandingSelection::Unhandled
    }

    /// Whether this host runs live player bodies that admit generic verified-codec
    /// retail states and actions beyond strict synthetic test fixtures.
    fn admits_generic_player_runtime(&self, _site: Site) -> bool {
        false
    }

    /// Whether a state-managed self-targeted `STU_38EE1100` registration for `topic`
    /// has only client-side receivers and should be projected as `ProjectedSelfSend`.
    fn accepts_client_self_message(&self, _site: Site, _topic: u64) -> bool {
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
