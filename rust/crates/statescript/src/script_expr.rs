//! Typed config and expression evaluation for server-side graph initialization.

use std::{collections::BTreeMap, error::Error, fmt};

use orpheus_runtime::format::parse_guid;
use serde_json::Value as Json;

pub use crate::script_wire::Value;
use crate::{
    script_graph::{asset_guid, target_tag_guid},
    script_runtime::PoolKind,
};

pub(crate) const EPSILON: f64 = 1.192_092_895_507_812_5e-5;
pub(crate) const MAX_CONFIG_DEPTH: usize = 64;
pub const MAX_EXPRESSION_STEPS: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Scope {
    Instance,
    Entity,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Variable {
    pub scope: Scope,
    pub id: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EntityRelation {
    OwningController,
    CurrentPossessor,
    PossessedBody,
    AiControlEntity,
    ControllerBodyField,
    CreatorBody,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StackPriority {
    pub level: i8,
    pub above_equals: bool,
}

pub(crate) fn authored_stack_variable(config: &Json) -> Result<Variable, EvalError> {
    let malformed = || {
        EvalError::MalformedConfig(
            "expected a stack variable with entity or instance scope and a nonzero u32 id",
        )
    };
    let fields = config
        .as_object()
        .filter(|fields| fields.len() == 2)
        .ok_or_else(malformed)?;
    let scope = match fields.get("scope").and_then(Json::as_str) {
        Some("entity") => Scope::Entity,
        Some("instance") => Scope::Instance,
        _ => return Err(malformed()),
    };
    let id = fields
        .get("id")
        .and_then(Json::as_u64)
        .and_then(|id| u32::try_from(id).ok())
        .filter(|&id| id != 0)
        .ok_or_else(malformed)?;
    Ok(Variable { scope, id })
}

pub(crate) fn authored_stack_priority(config: &Json) -> Result<StackPriority, EvalError> {
    let malformed = || {
        EvalError::MalformedConfig(
            "expected a stack priority with an i8 level and boolean above_equals",
        )
    };
    let fields = config
        .as_object()
        .filter(|fields| fields.len() == 2)
        .ok_or_else(malformed)?;
    let level = fields
        .get("level")
        .and_then(Json::as_i64)
        .and_then(|level| i8::try_from(level).ok())
        .ok_or_else(malformed)?;
    let above_equals = fields
        .get("above_equals")
        .and_then(Json::as_bool)
        .ok_or_else(malformed)?;
    Ok(StackPriority {
        level,
        above_equals,
    })
}

/// Availability of the engine table is distinct from a missing hash or a computed null.
#[derive(Clone, Debug, PartialEq)]
pub enum EngineSetting {
    /// No table service is declared by this host.
    Unavailable,
    /// The table exists, but the lookup did not write a result.
    Missing,
    /// A computed result, including null or false.
    Present(Value),
}

/// A complete host may report an unbound entity without writing a result.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub enum VelocityQuery {
    #[default]
    Unavailable,
    Unbound,
    Present([f64; 3]),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpatialCandidate {
    pub entity: u32,
    pub position: [f64; 3],
    /// Team affiliation. None is an independent participant.
    pub team: Option<u8>,
}

/// The first static surface reached by a bounded spatial ray.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpatialRayHit {
    pub distance: f64,
    pub point: [f64; 3],
    pub normal: [f64; 3],
}

/// The first eligible actor reached by a bounded spatial ray.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpatialActorRayHit {
    pub entity: u32,
    pub distance: f64,
    /// Unit surface normal for the selected capsule contact.
    pub normal: [f64; 3],
}

/// Authored query facts for a body in the current room snapshot.
#[derive(Clone, Debug)]
pub struct RoomEntityFacts {
    /// None means an independent participant.
    pub team: Option<u8>,
    pub alive: bool,
    pub tags: crate::script_init::TargetTagSnapshot,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BodyAngle {
    Yaw,
    Pitch,
}

/// A missing component differs from a host that cannot resolve the query.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub enum AngleQuery {
    #[default]
    Unavailable,
    Unbound,
    Present(f64),
}

impl AngleQuery {
    /// Server mover poses carry signed16 angles; native pitch uses these f32 stages.
    pub fn from_packed(units: i32) -> Self {
        let Ok(units) = i16::try_from(units) else {
            return Self::Unavailable;
        };
        let radians = rounded(f64::from(units) * rounded(std::f64::consts::TAU / 65536.0));
        Self::Present(rounded(radians * rounded(180.0 / std::f64::consts::PI)))
    }
}

/// Total-health lookup availability is separate from a computed zero.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub enum HealthQuery {
    #[default]
    Unavailable,
    Unbound,
    Present(f64),
}

/// Relationship selected by the explicitly admitted scene filter.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum FilterRelation {
    Any,
    Hostile,
    Friendly,
    FriendlyOther,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum FilterPredicate {
    None,
    Scalar,
    Target,
}

/// Host admission of the reference scene's relationship/tag layout.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SceneFilterPolicy {
    pub relation: FilterRelation,
    pub predicate: FilterPredicate,
}

/// Priority assets documented by the 104319 reference evaluator.
pub fn stack_priority(config: &Json) -> Result<StackPriority, EvalError> {
    let asset = child(config, "m_priority");
    if asset.is_null() {
        return Ok(StackPriority {
            level: 0,
            above_equals: true,
        });
    }
    let guid = resource(asset).ok_or(EvalError::MalformedConfig("stack priority asset"))?;
    let (level, above_equals) = match guid as u32 {
        0x0000 | 0x003d => (0, true),
        0x0001 | 0x008e => (0, false),
        0x0002 => (-10, false),
        0x0003 | 0x0066 => (10, false),
        0x0015 => (20, false),
        0x003e => (-5, true),
        0x0051 => (30, false),
        0x0079 => (-5, false),
        0x008d => (2, false),
        0x008f => (1, false),
        0x0090 => (-1, false),
        0x0091 => (3, false),
        0x00a1 => (-20, false),
        0x00b5 | 0x00f9 => (5, false),
        0x00c9 => (-2, false),
        0x00dd => (-10, true),
        0x00f1 => (25, false),
        0x00f2 => (16, false),
        0x00f3 => (99, false),
        0x00f4 => (4, false),
        0x00f6 => (14, true),
        0x00f7 => (9, false),
        0x00fa => (15, false),
        0x00fe => (19, false),
        _ => {
            return Err(EvalError::UnsupportedConfig(format!(
                "stack priority {guid:016X}"
            )));
        }
    };
    Ok(StackPriority {
        level,
        above_equals,
    })
}

/// External reads may register runtime watchers. An unset variable is null.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OnFire {
    Active(bool),
    NoStats,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EquippedCosmetic {
    Spray,
    EmoteAlias,
    EmoteBlendTree,
}

pub trait Context {
    /// Signed native index; -1 means known no team, None means unavailable.
    fn entity_team(&mut self, _entity: u32) -> Option<i8> {
        None
    }
    fn variable(&mut self, _variable: Variable) -> Value {
        Value::Null
    }

    fn parent_variable(&mut self, _levels: u8, _variable: Variable) -> Value {
        Value::Null
    }

    fn parent_entity(&mut self, _levels: u8) -> Value {
        Value::Null
    }

    fn parameter(&mut self, _id: u32) -> Value {
        Value::Null
    }

    fn owner(&mut self) -> Value {
        Value::Null
    }

    /// Some(Null) is a known unset variable. None means the query is unavailable.
    fn entity_member(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }

    /// None means unavailable; a known entity at the origin returns Some([0.0; 3]).
    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
    fn entity_velocity(&mut self, _entity: u32) -> VelocityQuery {
        VelocityQuery::Unavailable
    }

    fn body_angle(&mut self, _entity: u32, _axis: BodyAngle) -> AngleQuery {
        AngleQuery::Unavailable
    }

    fn entity_exists(&mut self, _entity: u32) -> Option<bool> {
        None
    }

    fn known_position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }

    fn forward(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }

    /// View pitch in degrees for the GetViewPitch operand.
    fn pitch(&mut self, _entity: u32) -> Option<f64> {
        None
    }

    /// A position read by an authored room operand.
    fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.position(entity)
    }

    /// A forward vector read by an authored room operand.
    fn authored_forward(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.forward(entity)
    }

    /// Map a body-local point (or bone-local point for a nonzero .088 GUID) to world space.
    fn body_bone_point(&mut self, entity: u32, bone: u64, local: [f64; 3]) -> Option<[f64; 3]> {
        if bone != 0 || !valid_entity(entity) {
            return None;
        }
        let origin = self
            .authored_position(entity)
            .or_else(|| self.known_position(entity))?;
        body_space_point(origin, self.authored_forward(entity), local)
    }

    /// The host's eye anchor for this body, when available.
    fn body_eye_position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }

    /// Hardpoint membership of the body's current model, if that model is known.
    fn body_has_hardpoint(&mut self, _entity: u32, _hardpoint: u64) -> Option<bool> {
        None
    }

    /// A known activity state for the current body. None means unavailable.
    fn body_active(&mut self) -> Option<bool> {
        None
    }

    /// None means the current candidate snapshot is unavailable.
    fn spatial_candidates(&mut self) -> Option<&[SpatialCandidate]> {
        None
    }

    /// None means the room's actor facts are unavailable.
    fn room_entities(&mut self) -> Option<&BTreeMap<u32, RoomEntityFacts>> {
        None
    }

    /// Facts sampled for a specific foreign target when settling a health event.
    fn settled_health_target(
        &self,
        _entity: u32,
    ) -> Option<&crate::script_init::HealthTargetFacts> {
        None
    }

    /// Whether static geometry leaves the segment clear. None is unavailable.
    fn spatial_line_clear(&mut self, _origin: [f64; 3], _target: [f64; 3]) -> Option<bool> {
        None
    }

    /// None is unavailable, Some(None) is an available miss.
    fn spatial_ray_cast(
        &mut self,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _max_distance: f64,
    ) -> Option<Option<SpatialRayHit>> {
        None
    }

    /// None is unavailable, Some(None) is an available miss.
    fn spatial_actor_ray_cast(
        &mut self,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _max_distance: f64,
        _targets: &[u32],
    ) -> Option<Option<SpatialActorRayHit>> {
        None
    }

    /// Ordered, still-bound combat bodies; None means the scene roster is unavailable.
    fn combat_targets(&mut self, _config: &Json) -> Option<Vec<u32>> {
        None
    }

    fn static_target(&mut self, _tag: u64) -> Option<u32> {
        None
    }

    fn random_u64(&mut self) -> Option<u64> {
        None
    }

    fn body_scale(&mut self, _entity: u32) -> Option<f64> {
        None
    }

    fn body_health(
        &mut self,
        _entity: u32,
        _kind: Option<PoolKind>,
        _maximum: bool,
    ) -> Option<f64> {
        None
    }
    /// Current amount in an authored named temporary shield on this body.
    fn temporary_shield_current(&mut self, _identifier: u64) -> Option<f64> {
        None
    }

    fn total_health(&mut self, entity: u32) -> HealthQuery {
        self.body_health(entity, None, false)
            .map_or(HealthQuery::Unavailable, HealthQuery::Present)
    }

    fn body_display_name(&mut self, _entity: u32) -> Option<String> {
        None
    }

    fn entity_hero(&mut self, _entity: u32) -> Option<u64> {
        None
    }

    fn entity_definition(&mut self, _entity: u32) -> Option<u64> {
        None
    }

    fn asset_has_category(&mut self, _asset: u64, _kind: u64, _id: u64) -> Option<bool> {
        None
    }

    /// Some(0) is an empty slot; None means the entity loadout is unavailable.
    fn equipped_cosmetic(
        &mut self,
        _entity: u32,
        _slot: usize,
        _cosmetic: EquippedCosmetic,
    ) -> Option<u64> {
        None
    }

    fn asset_texture(&mut self, _action: u64) -> Option<u64> {
        None
    }

    fn related_entity(&mut self, _relation: EntityRelation, _entity: u32) -> Option<u32> {
        None
    }

    fn linked_entity(&mut self, _entity: u32, _slot: u32) -> Option<u32> {
        None
    }

    fn entity_component92(&mut self, _entity: u32) -> Option<bool> {
        None
    }

    fn button_held(&mut self, _entity: u32, _button: u8) -> Option<bool> {
        None
    }

    /// None leaves a tag fact unknown for this entity.
    fn has_target_tag(&mut self, _entity: u32, _tag: u64) -> Option<bool> {
        None
    }

    /// Admit the scene-specific relationship and tag layout.
    fn scene_filter_policy(&mut self, _filter: &Json) -> Option<SceneFilterPolicy> {
        None
    }

    fn optional_ruleset(&mut self, id: u32) -> Option<Value> {
        let value = self.ruleset(id);
        (value != Value::Null).then_some(value)
    }

    /// None leaves candidate flags unknown. A known absence can report zero.
    fn entity_filter_flags(&mut self, _entity: u32) -> Option<u64> {
        None
    }
    fn entities_hostile(&mut self, _source: u32, _target: u32, _option: bool) -> Option<bool> {
        None
    }

    fn body_on_fire(&mut self, _entity: u32) -> Option<OnFire> {
        None
    }

    fn team_members(&mut self, _team: u8) -> Option<u32> {
        None
    }

    fn ruleset(&mut self, _id: u32) -> Value {
        Value::Null
    }

    /// A rule explicitly qualified by an entity. Only a host with its mode and
    /// body identity can resolve this reference.
    fn entity_ruleset(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }

    fn would_stack_to_top(
        &mut self,
        _variable: Variable,
        _priority: StackPriority,
    ) -> Option<bool> {
        None
    }

    fn engine_setting(&mut self, _key: u32) -> EngineSetting {
        EngineSetting::Unavailable
    }

    /// None means the native is unavailable. Some(Null) is a computed result.
    fn native(&mut self, _config: &Json) -> Option<Value> {
        None
    }

    /// The reference component policy (runtime.py `component.strict`).
    /// Strict keeps every unresolved native a loud error; the normal
    /// policy answers what the reference's `default_native` does: a typed
    /// null with a warn-once log (no report, no stop).
    fn strict(&self) -> bool {
        true
    }

    /// A known native evaluator returned without writing its destination.
    fn native_uses_initial(&mut self, _config: &Json) -> bool {
        false
    }

    /// Whether the evaluation records the natives it reads for a watcher, as
    /// the reference does only for a watched evaluation.
    fn watches_natives(&self) -> bool {
        false
    }

    /// A watched evaluation reached a config class the reference answers
    /// through its world's native hook. Nested operands of that config are
    /// not reported, because the reference world never evaluates them with
    /// the caller's watcher.
    fn reference_native(&mut self, _class: &str) {}
}

#[derive(Default)]
pub struct EmptyContext;

impl Context for EmptyContext {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EvalError {
    UnsupportedConfig(String),
    UnsupportedOpcode { opcode: u8, offset: usize },
    MalformedConfig(&'static str),
    MalformedBytecode { offset: usize, reason: &'static str },
    BudgetExceeded { offset: usize, limit: usize },
    UnavailableEntityVariable { entity: u32, id: u32 },
    UnavailableEntityPosition(u32),
    UnavailableEntityDirection(u32),
    UnavailableBodyEyePosition(u32),
    UnavailableBodyBone { entity: u32, bone: u64 },
    UnavailableBodyHardpoint { entity: u32, hardpoint: u64 },
    UnavailableBodyActivity,
    UnavailableSpatialCandidates,
    UnavailableSpatialVisibility,
    UnavailableSpatialRayCast,
    UnavailableRoomEntityFacts,
    UnavailableStackAdmission,
    NestingExceeded,
}

impl fmt::Display for EvalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedConfig(class) => write!(f, "unsupported statescript config {class}"),
            Self::UnsupportedOpcode { opcode, offset } => {
                write!(
                    f,
                    "unsupported statescript opcode {opcode} at byte {offset}"
                )
            }
            Self::MalformedConfig(reason) => write!(f, "malformed statescript config: {reason}"),
            Self::MalformedBytecode { offset, reason } => {
                write!(
                    f,
                    "malformed statescript bytecode at byte {offset}: {reason}"
                )
            }
            Self::BudgetExceeded { offset, limit } => {
                write!(
                    f,
                    "statescript instruction budget {limit} exhausted at byte {offset}"
                )
            }
            Self::UnavailableEntityVariable { entity, id } => {
                write!(f, "unavailable entity variable {entity:08X}:{id}")
            }
            Self::UnavailableEntityPosition(entity) => {
                write!(f, "unavailable entity position {entity:08X}")
            }
            Self::UnavailableEntityDirection(entity) => {
                write!(f, "unavailable entity direction {entity:08X}")
            }
            Self::UnavailableBodyEyePosition(entity) => {
                write!(f, "unavailable body eye position {entity:08X}")
            }
            Self::UnavailableBodyBone { entity, bone } => {
                write!(
                    f,
                    "unavailable bone transform {bone:016X} for body {entity:08X}"
                )
            }
            Self::UnavailableBodyHardpoint { entity, hardpoint } => {
                write!(
                    f,
                    "unavailable hardpoint {hardpoint:016X} for body {entity:08X}"
                )
            }
            Self::UnavailableBodyActivity => f.write_str("unavailable body activity"),
            Self::UnavailableSpatialCandidates => f.write_str("unavailable spatial candidates"),
            Self::UnavailableSpatialVisibility => f.write_str("unavailable spatial visibility"),
            Self::UnavailableSpatialRayCast => f.write_str("unavailable spatial ray cast"),
            Self::UnavailableRoomEntityFacts => f.write_str("unavailable room entity facts"),
            Self::UnavailableStackAdmission => f.write_str("unavailable stack admission"),
            Self::NestingExceeded => f.write_str("statescript config nesting limit exceeded"),
        }
    }
}

impl Error for EvalError {}

pub fn evaluate(config: &Json, context: &mut impl Context) -> Result<Value, EvalError> {
    evaluate_with_budget(config, context, MAX_EXPRESSION_STEPS)
}

/// The instruction budget is shared by nested expressions.
pub fn evaluate_with_budget(
    config: &Json,
    context: &mut impl Context,
    max_steps: usize,
) -> Result<Value, EvalError> {
    evaluate_with_budget_and_initial(config, context, max_steps, Value::Null)
}

pub(crate) fn evaluate_with_initial(
    config: &Json,
    context: &mut impl Context,
    initial: Value,
) -> Result<Value, EvalError> {
    evaluate_with_budget_and_initial(config, context, MAX_EXPRESSION_STEPS, initial)
}

fn evaluate_with_budget_and_initial(
    config: &Json,
    context: &mut impl Context,
    max_steps: usize,
    initial: Value,
) -> Result<Value, EvalError> {
    if max_steps == 0 {
        return Err(EvalError::MalformedConfig(
            "instruction budget must be positive",
        ));
    }
    Evaluator::new(max_steps, context).config_with_initial(config, context, 0, initial)
}

/// Native first-config scalar selection with an explicit caller-initial value.
pub fn evaluate_scalar_with_initial(
    config: &Json,
    context: &mut impl Context,
    initial: Value,
) -> Result<Value, EvalError> {
    Evaluator::new(MAX_EXPRESSION_STEPS, context).scalar_config(config, context, 0, initial)
}

/// Resolve a radial batch before the caller can admit any health requests.
pub(crate) fn radial_health_changes(
    area: &Json,
    delta: &Json,
    edge_scale: Option<&Json>,
    context: &mut impl Context,
) -> Result<Vec<(u32, f64)>, EvalError> {
    Evaluator::new(MAX_EXPRESSION_STEPS, context)
        .radial_health_changes(area, delta, edge_scale, context)
}

pub fn dynamic(config: &Json) -> Result<Option<Variable>, EvalError> {
    if class(config) != Some("STUConfigVarDynamic") {
        return Ok(None);
    }
    let scope = if json_truthy(child(config, "m_60DB8F99")) {
        Scope::Entity
    } else {
        Scope::Instance
    };
    Ok(Some(Variable {
        scope,
        id: identifier(config)?,
    }))
}

/// Return a direct dynamic variable or the bytecode form of a single variable push.
pub fn lvalue(config: &Json) -> Result<Option<Variable>, EvalError> {
    if let Some(variable) = dynamic(config)? {
        return Ok(Some(variable));
    }
    if !is_expression(class(config)) {
        return Ok(None);
    }
    let parts = Expression::read(config)?;
    if parts.code.len() < 2
        || !matches!(parts.code[0].as_u64(), Some(8 | 9))
        || (parts.code.len() > 2 && parts.code[2].as_u64() != Some(0))
    {
        return Ok(None);
    }
    let index = byte(&parts.code[1], 1)?;
    let entry = table(parts.dynamics, index, 0)?;
    dynamic(entry)
}

pub(crate) enum DestinationPart {
    Index(Json),
    Field(u16),
}

pub(crate) struct Destination {
    pub variable: Variable,
    pub path: Vec<DestinationPart>,
}

/// Decode the native root / index / identifier destination form. Other
/// expression destinations remain explicit errors rather than successful no-ops.
pub(crate) fn destination(config: &Json) -> Result<Option<Destination>, EvalError> {
    if let Some(variable) = lvalue(config)? {
        return Ok(Some(Destination {
            variable,
            path: Vec::new(),
        }));
    }
    if !is_expression(class(config)) {
        return Ok(None);
    }
    let unsupported = || EvalError::UnsupportedConfig("expression destination path".into());
    let parts = Expression::read(config)?;
    if parts.code.len() < 3 || !matches!(parts.code[0].as_u64(), Some(8 | 9)) {
        return Err(unsupported());
    }
    let variable = stack_var(parts.dynamics, byte(&parts.code[1], 1)?, 0)?;
    let mut cursor = 2;
    let mut path = Vec::new();
    while cursor < parts.code.len() {
        let opcode = byte(&parts.code[cursor], cursor)?;
        if opcode == 0 && cursor + 1 == parts.code.len() {
            return Ok(Some(Destination { variable, path }));
        }
        if path.len() >= MAX_CONFIG_DEPTH || cursor + 2 >= parts.code.len() {
            return Err(unsupported());
        }
        let arg = byte(&parts.code[cursor + 1], cursor + 1)?;
        let access = byte(&parts.code[cursor + 2], cursor + 2)?;
        path.push(match (opcode, access) {
            (6, 17) => DestinationPart::Index(serde_json::json!({
                "$":"STUConfigVarFloat", "m_value":table(parts.floats, arg, cursor)?
            })),
            (8 | 9, 17) => DestinationPart::Index(table(parts.dynamics, arg, cursor)?.clone()),
            (7, 15) => {
                let field = identifier(table(parts.dynamics, arg, cursor)?)?;
                let field = u16::try_from(field)
                    .ok()
                    .filter(|field| *field != 0)
                    .ok_or_else(unsupported)?;
                DestinationPart::Field(field)
            }
            _ => return Err(unsupported()),
        });
        cursor += 3;
    }
    Err(unsupported())
}

pub fn valid_entity(id: u32) -> bool {
    id != 0 && (id & 0x1fff_ffff) != 0x1fff_ffff
}

/// Apply the horizontal orientation of a body at its feet to a model-space point.
pub fn body_space_point(
    origin: [f64; 3],
    forward: Option<[f64; 3]>,
    local: [f64; 3],
) -> Option<[f64; 3]> {
    let [x, y, z] = local;
    let [mut wx, wy, mut wz] = origin;
    if x != 0.0 || z != 0.0 {
        let [dx, _, dz] = forward?;
        let length = dx.hypot(dz);
        if !length.is_finite() || length <= 1e-6 {
            return None;
        }
        let (sin, cos) = (dx / length, dz / length);
        wx += x * cos + z * sin;
        wz += z * cos - x * sin;
    }
    let world = [wx, wy + y, wz];
    world.iter().all(|v| v.is_finite()).then_some(world)
}

fn first_scalar(mut value: &Value) -> Option<&Value> {
    while let Value::Array(items) = value {
        value = items.first()?;
    }
    Some(value)
}

pub fn truthy(value: &Value) -> bool {
    match first_scalar(value) {
        Some(Value::Bool(value)) => *value,
        Some(Value::Integer(value)) => *value != 0,
        Some(Value::Float(value)) => *value != 0.0 && !value.is_nan(),
        Some(Value::Asset(guid)) => *guid != 0,
        Some(Value::Entity(id)) => valid_entity(*id),
        Some(Value::Vector(parts)) => parts.iter().any(|&part| part != 0.0),
        Some(Value::Handle { instance, .. }) => *instance != 0,
        Some(Value::Map(entries)) => !entries.is_empty(),
        Some(Value::IdentifierMap(map)) => !map.is_empty(),
        _ => false,
    }
}

pub fn entity_scalar(value: &Value) -> u32 {
    match first_scalar(value) {
        Some(Value::Entity(entity)) => *entity,
        _ => 0,
    }
}

/// F65540 selects only the recursively first value, never a later asset.
pub(crate) fn asset_scalar(value: &Value) -> u64 {
    match first_scalar(value) {
        Some(Value::Asset(guid)) => *guid,
        _ => 0,
    }
}

pub fn authored_number(
    config: Option<&Json>,
    default: f64,
    evaluate: &mut impl FnMut(&Json) -> Result<Value, EvalError>,
) -> Result<f64, EvalError> {
    let Some(config) = config.filter(|value| !value.is_null()) else {
        return Ok(default);
    };
    let value = evaluate(config)?;
    Ok(if value == Value::Null {
        default
    } else {
        to_float(&value)
    })
}

pub fn to_float(value: &Value) -> f64 {
    match first_scalar(value) {
        Some(Value::Bool(value)) => f64::from(u8::from(*value)),
        Some(Value::Integer(value)) => rounded(*value as f64),
        Some(Value::Float(value)) => *value,
        _ => 0.0,
    }
}

pub fn to_int(value: &Value, default: i64) -> i64 {
    match first_scalar(value) {
        Some(Value::Null) => 0,
        Some(Value::Bool(value)) => i64::from(*value),
        Some(Value::Integer(value)) => *value,
        Some(Value::Float(value)) => i64::from(round_half_away(*value)),
        _ => default,
    }
}

fn rounded(value: f64) -> f64 {
    f64::from(value as f32)
}

pub fn round_half_away(value: f64) -> i32 {
    let number = rounded(if value >= 0.0 {
        value + 0.5
    } else {
        value - 0.5
    })
    .trunc();
    if number.is_finite() {
        number.rem_euclid(4_294_967_296.0) as u32 as i32
    } else {
        0
    }
}

fn close(a: f64, b: f64) -> bool {
    rounded(a - b).abs() <= EPSILON
}

pub fn equal(a: &Value, b: &Value) -> bool {
    let (a, b) = if matches!(b, Value::Array(_)) && !matches!(a, Value::Array(_)) {
        (b, a)
    } else {
        (a, b)
    };
    match (a, b) {
        (a, Value::Bool(b)) => truthy(a) == *b,
        (Value::Bool(a), b) => *a == truthy(b),
        (Value::Null, b) => !truthy(b),
        (Value::Integer(_) | Value::Float(_), Value::Null) => !truthy(a),
        (Value::Integer(a), Value::Float(b)) | (Value::Float(b), Value::Integer(a)) => {
            close(*a as f64, *b)
        }
        (Value::Float(a), Value::Float(b)) => close(*a, *b),
        (Value::Vector(a), Value::Vector(b)) => a.iter().zip(b).all(|(&a, &b)| close(a, b)),
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| equal(a, b))
        }
        (Value::Array(a), b) => matches!(a.as_slice(), [item] if equal(item, b)),
        _ => a == b,
    }
}

pub(crate) fn contains(container: &Value, value: &Value) -> bool {
    match container {
        Value::Null => false,
        Value::Array(items) => items.iter().any(|item| equal(item, value)),
        item => equal(item, value),
    }
}

fn index(object: Value, index: &Value) -> Value {
    if let Value::IdentifierMap(map) = object {
        return map
            .entries()
            .find_map(|(key, value)| {
                equal(&Value::Integer(i64::from(key)), index).then(|| value.clone())
            })
            .unwrap_or(Value::Null);
    }
    if let Value::Map(entries) = object {
        return entries
            .into_iter()
            .find_map(|(key, value)| equal(&key, index).then_some(value))
            .unwrap_or(Value::Null);
    }
    if matches!(
        index,
        Value::Null | Value::Bool(_) | Value::Integer(_) | Value::Float(_)
    ) {
        let number = to_int(index, 0);
        return match object {
            Value::Array(items) => usize::try_from(number)
                .ok()
                .and_then(|number| items.into_iter().nth(number))
                .unwrap_or(Value::Null),
            object if number == 0 => object,
            _ => Value::Null,
        };
    }
    match object {
        Value::Array(items) => items
            .into_iter()
            .find(|item| equal(item, index))
            .unwrap_or(Value::Null),
        _ => Value::Null,
    }
}

mod evaluator;
mod helpers;
pub(crate) use helpers::{entity_masks, matches_entity_masks};

use evaluator::Evaluator;
pub(crate) use helpers::named_shield_identifier;
use helpers::*;
pub(crate) use helpers::{client_only_fallback, native_entity_operand, native_target_tag_operand};
pub use helpers::{
    excluded_tags_admit, is_zeroed_owner_selector, native_fields, native_shape, target_tag_config,
};

/// Locate a config group reached directly or through explicit server fallbacks.
pub(crate) fn assignment_group_source(mut config: &Json) -> Option<(&Json, usize)> {
    for wrapper_depth in 0..MAX_CONFIG_DEPTH {
        match class(config) {
            Some("STU_06610EBC") if native_fields(config, &["m_configVars"]) => {
                return Some((child(config, "m_configVars"), wrapper_depth));
            }
            Some("ClientOnly") => config = client_only_fallback(config).ok().flatten()?,
            _ => break,
        }
    }
    None
}

#[cfg(test)]
mod tests;
