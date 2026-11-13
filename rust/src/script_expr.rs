//! Typed config and expression evaluation for server-side graph initialization.

use std::{error::Error, fmt};

use serde_json::Value as Json;

pub use crate::script_wire::Value;
use crate::{
    accounts::parse_guid,
    script_graph::{asset_guid, target_tag_guid},
    vitals::PoolKind,
};

pub(crate) const EPSILON: f64 = 1.192_092_895_507_812_5e-5;
const MAX_CONFIG_DEPTH: usize = 64;
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
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StackPriority {
    pub level: i8,
    pub above_equals: bool,
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
    pub(crate) fn from_packed(units: i32) -> Self {
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
    Hostile,
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

    fn related_entity(&mut self, _relation: EntityRelation, _entity: u32) -> Option<u32> {
        None
    }

    fn linked_entity(&mut self, _entity: u32, _slot: u32) -> Option<u32> {
        None
    }

    fn entity_component92(&mut self, _entity: u32) -> Option<bool> {
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

#[derive(Debug, Eq, PartialEq)]
pub enum EvalError {
    UnsupportedConfig(String),
    UnsupportedOpcode { opcode: u8, offset: usize },
    MalformedConfig(&'static str),
    MalformedBytecode { offset: usize, reason: &'static str },
    BudgetExceeded { offset: usize, limit: usize },
    UnavailableEntityVariable { entity: u32, id: u32 },
    UnavailableEntityPosition(u32),
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
    Evaluator {
        limit: max_steps,
        steps: 0,
        folds: Vec::new(),
        scoped_parameters: Vec::new(),
        // An unwatched evaluation has no use for the record.
        in_native: !context.watches_natives(),
    }
    .config_with_initial(config, context, 0, initial)
}

/// Native first-config scalar selection with an explicit caller-initial value.
pub fn evaluate_scalar_with_initial(
    config: &Json,
    context: &mut impl Context,
    initial: Value,
) -> Result<Value, EvalError> {
    Evaluator {
        limit: MAX_EXPRESSION_STEPS,
        steps: 0,
        folds: Vec::new(),
        scoped_parameters: Vec::new(),
        in_native: !context.watches_natives(),
    }
    .scalar_config(config, context, 0, initial)
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

pub(crate) fn entity_scalar(value: &Value) -> u32 {
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

fn class(config: &Json) -> Option<&str> {
    config.get("$")?.as_str()
}

pub(crate) fn native_shape(config: &Json, expected: &str, operand: Option<&str>) -> bool {
    class(config) == Some(expected) && native_fields(config, operand.as_slice())
}

pub(crate) fn native_fields(config: &Json, operands: &[&str]) -> bool {
    config.as_object().is_some_and(|fields| {
        fields.iter().all(|(key, value)| match key.as_str() {
            "$" => true,
            "m_EE729DCB" => value.as_u64() == Some(0),
            key => operands.contains(&key),
        })
    })
}

fn is_expression(class: Option<&str>) -> bool {
    matches!(class, Some("STU_919FD47C" | "STUConfigVarExpression"))
}

fn child<'a>(config: &'a Json, field: &str) -> &'a Json {
    config.get(field).unwrap_or(&Json::Null)
}

fn literal_asset(config: &Json, expected: &str, field: &str, kind: u64) -> Option<Value> {
    if class(config) != Some(expected)
        || !native_fields(config, &[field, "m_resourceKey"])
        || !child(config, "m_resourceKey").is_null()
    {
        return None;
    }
    let guid = asset_guid(child(config, field))?;
    (guid >> 48 == kind).then_some(Value::Asset(guid))
}

fn literal_message_map(config: &Json) -> Result<Value, EvalError> {
    let unsupported = || EvalError::UnsupportedConfig("STU_7757D403".into());
    if !native_fields(config, &["m_configVars"]) {
        return Err(unsupported());
    }
    let pairs = array_field(config, "m_configVars")?
        .iter()
        .map(|entry| {
            if class(entry) != Some("STU_98DD1872") || !native_fields(entry, &["m_key", "m_value"])
            {
                return Err(unsupported());
            }
            let key = literal_asset(child(entry, "m_key"), "STU_B70476F3", "m_548E5344", 0x0a90)
                .ok_or_else(unsupported)?;
            let value = literal_asset(
                child(entry, "m_value"),
                "STUConfigVarGameMessage",
                "m_gameMessage",
                0x0240,
            )
            .ok_or_else(unsupported)?;
            Ok((key, value))
        })
        .collect::<Result<_, _>>()?;
    Ok(Value::Map(pairs))
}

fn json_truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(value) => *value,
        Json::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Json::String(value) => !value.is_empty(),
        Json::Array(value) => !value.is_empty(),
        Json::Object(value) => !value.is_empty(),
    }
}

fn number(value: &Json) -> Result<f64, EvalError> {
    match value {
        Json::Null => Some(0.0),
        Json::Bool(value) => Some(f64::from(u8::from(*value))),
        Json::Number(value) => value.as_f64(),
        Json::String(value) => value.trim().parse().ok(),
        _ => None,
    }
    .ok_or(EvalError::MalformedConfig("expected a number"))
}

fn integer(value: &Json) -> Result<i64, EvalError> {
    match value {
        Json::Null => Some(0),
        Json::Bool(value) => Some(i64::from(*value)),
        Json::Number(value) => value
            .as_i64()
            .or_else(|| value.as_u64().map(|value| value as i64))
            .or_else(|| value.as_f64().map(|value| value as i64)),
        Json::String(value) => value.trim().parse().ok(),
        _ => None,
    }
    .ok_or(EvalError::MalformedConfig("expected an integer"))
}

fn identifier(config: &Json) -> Result<u32, EvalError> {
    let value = child(config, "m_identifier");
    if !json_truthy(value) {
        return Ok(0);
    }
    u32::try_from(integer(value)?)
        .map_err(|_| EvalError::MalformedConfig("variable identifier out of range"))
}

fn resource(value: &Json) -> Option<u64> {
    asset_guid(value).or_else(|| {
        let text = value.as_str()?;
        text.starts_with("0x").then(|| parse_guid(text)).flatten()
    })
}

pub(crate) fn target_tag_config(config: &Json) -> Option<u64> {
    if class(config) != Some("STU_357DABBB")
        || !native_fields(config, &["m_7D7C86A1", "m_resourceKey"])
        || !child(config, "m_resourceKey").is_null()
    {
        return None;
    }
    let tag = child(config, "m_7D7C86A1");
    if tag.is_null() {
        Some(0)
    } else {
        target_tag_guid(tag)
    }
}

const ANY_ENTITY_MASK: u64 = 0x3fff_ffff;

fn filter_shape(filter: &Json) -> bool {
    class(filter) == Some("STUConfigVarFilter")
        && native_fields(
            filter,
            &[
                "m_84065FD1",
                "m_1E14CCA8",
                "m_4EA686DB",
                "m_27CF71D5",
                "m_66747E7D",
                "m_89831602",
                "m_9C7BD8B8",
                "m_B486603E",
                "m_8B2A4DF3",
                "m_C1903771",
                "m_93FA1642",
                "m_condition",
                "m_F61726FE",
                "m_B40362FC",
            ],
        )
        && [
            "m_84065FD1",
            "m_1E14CCA8",
            "m_4EA686DB",
            "m_27CF71D5",
            "m_66747E7D",
            "m_89831602",
        ]
        .iter()
        .all(|name| {
            let value = child(filter, name);
            value.is_null() || value.as_u64().is_some()
        })
}

fn filter_masks(filter: &Json, scene_hostile: bool) -> Option<[u64; 3]> {
    if !filter_shape(filter) {
        return None;
    }
    let supported = filter
        .as_object()?
        .iter()
        .all(|(key, value)| match key.as_str() {
            "$" | "m_84065FD1" | "m_1E14CCA8" | "m_4EA686DB" | "m_9C7BD8B8" | "m_B486603E"
            | "m_8B2A4DF3" => true,
            "m_66747E7D" | "m_89831602" if scene_hostile => true,
            "m_condition" if scene_hostile => true,
            "m_C1903771" => value.is_null() || value.as_u64() == Some(0),
            "m_EE729DCB" | "m_27CF71D5" | "m_66747E7D" | "m_89831602" => value.as_u64() == Some(0),
            "m_93FA1642" => value.is_null() || value.as_array().is_some_and(Vec::is_empty),
            "m_condition" | "m_F61726FE" | "m_B40362FC" => value.is_null(),
            _ => false,
        });
    if !supported {
        return None;
    }
    let mask = |field| {
        let value = child(filter, field);
        if value.is_null() {
            Some(0)
        } else {
            value.as_u64()
        }
    };
    Some([
        mask("m_84065FD1")?,
        mask("m_1E14CCA8")?,
        mask("m_4EA686DB")?,
    ])
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum TagCondition {
    Any,
    All,
    Exclude,
}

enum TagCheck {
    Unused,
    Accept,
    Reject,
    Unknown,
}

fn check_tag_set(
    config: &Json,
    condition: TagCondition,
    entity: u32,
    context: &mut impl Context,
) -> Result<TagCheck, EvalError> {
    let tags = if config.is_null() {
        &[]
    } else if native_shape(config, "STU_06610EBC", Some("m_configVars")) {
        array_field(config, "m_configVars")?
    } else {
        std::slice::from_ref(config)
    };
    let mut has_tag = false;
    let mut any_match = false;
    let mut unknown = false;
    for tag in tags {
        let tag = target_tag_config(tag)
            .ok_or_else(|| EvalError::UnsupportedConfig("STU_AC829876".into()))?;
        if tag == 0 {
            continue;
        }
        has_tag = true;
        if any_match {
            // Even a matching Any list must contain only supported operands.
            continue;
        }
        match (condition, context.has_target_tag(entity, tag)) {
            (TagCondition::Any, Some(true)) => any_match = true,
            (TagCondition::All, Some(false)) | (TagCondition::Exclude, Some(true)) => {
                return Ok(TagCheck::Reject);
            }
            (_, None) => unknown = true,
            _ => (),
        }
    }
    Ok(if any_match {
        TagCheck::Accept
    } else if unknown {
        TagCheck::Unknown
    } else if !has_tag {
        TagCheck::Unused
    } else if condition == TagCondition::Any {
        TagCheck::Reject
    } else {
        TagCheck::Accept
    })
}

pub(crate) fn excluded_tags_admit(
    config: &Json,
    presence: impl FnMut(u64) -> Option<bool>,
) -> Result<Option<bool>, EvalError> {
    struct Tags<F>(F);
    impl<F: FnMut(u64) -> Option<bool>> Context for Tags<F> {
        fn has_target_tag(&mut self, _entity: u32, tag: u64) -> Option<bool> {
            self.0(tag)
        }
    }
    Ok(
        match check_tag_set(config, TagCondition::Exclude, 0, &mut Tags(presence))? {
            TagCheck::Unused | TagCheck::Accept => Some(true),
            TagCheck::Reject => Some(false),
            TagCheck::Unknown => None,
        },
    )
}

fn resource_field(class: &str) -> Option<&'static str> {
    match class {
        "STUConfigVarIdentifier" => Some("m_identifier"),
        "STUConfigVarGameMessage" => Some("m_gameMessage"),
        "STUConfigVarVoiceStimulus" => Some("m_voiceStimulus"),
        "STUConfigVarContactSet" => Some("m_contactSet"),
        "STUConfigVarEffect" => Some("m_effect"),
        "STUConfigVarLoadout" => Some("m_loadout"),
        "STUConfigVarHardPoint" => Some("m_hardPoint"),
        "STUConfigVarGameMode" => Some("m_gamemode"),
        "STU_BCD1C634" => Some("m_id"),
        "STU_8A491388" => Some("m_priority"),
        _ => None,
    }
}

/// The class of a config object the reference evaluates through its world's
/// native hook: expr.py `evaluate` falls through to `ctx.native` for every
/// class it does not compute itself.
fn reference_native_class(config: &Json) -> Option<&str> {
    let fields = config.as_object()?;
    let class = class(config).unwrap_or("?");
    let native = match class {
        "STUConfigVarDynamic"
        | "STUConfigVarBool"
        | "STUConfigVarInt"
        | "STUConfigVarFloat"
        | "STUConfigVarLogicalButton"
        | "STU_919FD47C"
        | "STUConfigVarExpression"
        | "STU_B5A0CAF0"
        | "STU_3832D36C"
        | "STU_91EFD5B1"
        | "STU_3ACE35FB"
        | "STU_7197C080"
        | "STU_BE0BD267"
        | "STUConfigVarVec3" => false,
        "STU_B40ACFBA" | "STU_4BD39CFD" => false,
        "STU_45EDF12D" if child(config, "m_key").is_null() => false,
        "STUConfigVarTexture" | "STU_08305297" if child(config, "m_resourceKey").is_null() => false,
        "STUConfigVarVoiceStimulus" => {
            resource(child(config, "m_resourceKey")).is_some_and(|guid| guid != 0)
        }
        _ if resource_field(class).is_some() => false,
        // Any other resource config names its first nonzero GUID text.
        _ if fields.contains_key("m_resourceKey") => !fields.iter().any(|(key, value)| {
            key != "m_resourceKey"
                && value
                    .as_str()
                    .filter(|text| text.starts_with("0x"))
                    .and_then(parse_guid)
                    .is_some_and(|guid| guid != 0)
        }),
        _ => true,
    };
    native.then_some(class)
}

struct Fold {
    count: usize,
    index: usize,
    value: Value,
    item: Option<Value>,
}

struct Evaluator {
    limit: usize,
    steps: usize,
    folds: Vec<Fold>,
    scoped_parameters: Vec<(u32, Value)>,
    /// Inside a native's operands, which the reference does not watch.
    in_native: bool,
}

impl Evaluator {
    fn collect_group(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
        out: &mut Vec<Value>,
    ) -> Result<(), EvalError> {
        if depth >= MAX_CONFIG_DEPTH {
            return Err(EvalError::NestingExceeded);
        }
        if self.steps >= self.limit {
            return Err(EvalError::BudgetExceeded {
                offset: 0,
                limit: self.limit,
            });
        }
        self.steps += 1;
        if native_shape(config, "STU_06610EBC", Some("m_configVars")) {
            if let Some(children) = child(config, "m_configVars").as_array() {
                for config in children {
                    self.collect_group(config, context, depth + 1, out)?;
                }
            }
        } else {
            let value = self.config(config, context, depth + 1)?;
            if value != Value::Null {
                self.flatten_group_value(value, depth + 1, out)?;
            }
        }
        Ok(())
    }

    fn flatten_group_value(
        &mut self,
        value: Value,
        depth: usize,
        out: &mut Vec<Value>,
    ) -> Result<(), EvalError> {
        if depth >= MAX_CONFIG_DEPTH {
            return Err(EvalError::NestingExceeded);
        }
        if self.steps >= self.limit {
            return Err(EvalError::BudgetExceeded {
                offset: 0,
                limit: self.limit,
            });
        }
        self.steps += 1;
        if let Value::Array(items) = value {
            for value in items {
                self.flatten_group_value(value, depth + 1, out)?;
            }
        } else {
            out.push(value);
        }
        Ok(())
    }
    fn charge(&mut self) -> Result<(), EvalError> {
        if self.steps >= self.limit {
            return Err(EvalError::BudgetExceeded {
                offset: 0,
                limit: self.limit,
            });
        }
        self.steps += 1;
        Ok(())
    }

    fn insert_authored_map(
        &mut self,
        buckets: &mut Vec<Option<(u32, Value)>>,
        key: u32,
        value: Value,
    ) -> Result<(), EvalError> {
        if key == 0 {
            return Ok(());
        }
        let width = buckets.len();
        for offset in 0..width {
            self.charge()?;
            let slot = (key as usize + offset) % width;
            if buckets[slot]
                .as_ref()
                .is_none_or(|(existing, _)| *existing == key)
            {
                buckets[slot] = Some((key, value));
                return Ok(());
            }
        }
        // Growth replays the old physical bucket order in the target runtime.
        for (key, value) in std::mem::replace(buckets, vec![None; width * 2])
            .into_iter()
            .flatten()
        {
            self.insert_authored_map(buckets, key, value)?;
        }
        self.insert_authored_map(buckets, key, value)
    }

    fn fold_array(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let mut items = Vec::new();
        self.collect_group(child(config, "m_814F8EC3"), context, depth, &mut items)?;
        let count = items.len();
        self.folds.push(Fold {
            count,
            index: 0,
            value: Value::Null,
            item: items.first().cloned(),
        });
        let result = (|| {
            if count != 0 {
                let value = self.config(child(config, "m_418D9D9D"), context, depth)?;
                self.folds.last_mut().expect("active fold").value = value;
            }
            for (index, item) in items.into_iter().enumerate() {
                self.charge()?;
                let fold = self.folds.last_mut().expect("active fold");
                fold.index = index;
                fold.item = Some(item);
                let initial = fold.value.clone();
                let value =
                    self.config_with_initial(child(config, "m_C239F989"), context, depth, initial)?;
                self.folds.last_mut().expect("active fold").value = value;
            }
            let fold = self.folds.last_mut().expect("active fold");
            fold.index = count;
            fold.item = None;
            let result = child(config, "m_8054375D");
            if result.is_null() {
                Ok(std::mem::replace(&mut fold.value, Value::Null))
            } else {
                self.config(result, context, depth)
            }
        })();
        self.folds.pop();
        result
    }

    fn parameter(&self, context: &mut impl Context, id: u32) -> Value {
        if let Some((_, value)) = self
            .scoped_parameters
            .iter()
            .rev()
            .find(|(key, _)| *key == id)
        {
            return value.clone();
        }
        for fold in self.folds.iter().rev() {
            match id {
                12 => return Value::Integer(fold.index as i64),
                1050 => return Value::Integer(fold.count as i64),
                1293 => return fold.value.clone(),
                1294 => {
                    if let Some(item) = &fold.item {
                        return item.clone();
                    }
                }
                _ => {}
            }
        }
        context.parameter(id)
    }

    fn authored_value(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        self.charge()?;
        if config.is_null() {
            return Ok(Value::Integer(0));
        }
        self.config_with_initial(config, context, depth, Value::Integer(0))
    }

    fn config(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        self.config_with_initial(config, context, depth, Value::Null)
    }

    /// The native +20 selector path reads only the first config of a group.
    fn scalar_config(
        &mut self,
        mut config: &Json,
        context: &mut impl Context,
        mut depth: usize,
        initial: Value,
    ) -> Result<Value, EvalError> {
        loop {
            if depth >= MAX_CONFIG_DEPTH {
                return Err(EvalError::NestingExceeded);
            }
            if config.is_null() {
                return Ok(initial);
            }
            if class(config) != Some("STU_06610EBC") {
                return self.config_with_initial(config, context, depth, initial);
            }
            if !native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                return Err(EvalError::UnsupportedConfig("STU_06610EBC".into()));
            }
            self.charge()?;
            let Some(first) = array_field(config, "m_configVars")?.first() else {
                return Ok(initial);
            };
            config = first;
            depth += 1;
        }
    }

    fn config_with_initial(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
        initial: Value,
    ) -> Result<Value, EvalError> {
        let Some(class) = (!self.in_native)
            .then(|| reference_native_class(config))
            .flatten()
        else {
            return self.config_value(config, context, depth, initial);
        };
        context.reference_native(class);
        self.in_native = true;
        let result = self.config_value(config, context, depth, initial);
        self.in_native = false;
        result
    }

    fn config_value(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
        initial: Value,
    ) -> Result<Value, EvalError> {
        if depth >= MAX_CONFIG_DEPTH {
            return Err(EvalError::NestingExceeded);
        }
        let fields = match config {
            Json::Null => return Ok(Value::Null),
            Json::Bool(value) => return Ok(Value::Bool(*value)),
            Json::Number(value) if value.is_i64() || value.is_u64() => {
                return Ok(Value::Integer(integer(config)?));
            }
            Json::Number(_) => return Ok(Value::Float(number(config)?)),
            Json::String(value) => return Ok(Value::String(value.clone())),
            Json::Array(_) => return Err(EvalError::UnsupportedConfig("untyped array".into())),
            Json::Object(fields) => fields,
        };
        let class = class(config).unwrap_or("?");
        let value = child(config, "m_value");
        let nested = depth + 1;
        let result = match class {
            "STU_CC53F52B" | "STU_7BE6D76D" if native_shape(config, class, Some("m_entity")) => {
                let target = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let entity = match first_scalar(&target) {
                    Some(Value::Entity(entity)) => *entity,
                    _ => 0,
                };
                let axis = if class == "STU_CC53F52B" {
                    BodyAngle::Yaw
                } else {
                    BodyAngle::Pitch
                };
                match context.body_angle(entity, axis) {
                    AngleQuery::Unbound => initial,
                    AngleQuery::Present(degrees) if rounded(degrees).is_finite() => {
                        Value::Float(rounded(degrees))
                    }
                    AngleQuery::Present(_) => {
                        return Err(EvalError::UnsupportedConfig(class.into()));
                    }
                    AngleQuery::Unavailable => context
                        .native(config)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                }
            }
            "STU_309B0570"
                if native_fields(config, &["m_entity", "m_5B94EDB8"])
                    && child(config, "m_5B94EDB8").is_null() =>
            {
                let target = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let entity = match first_scalar(&target) {
                    Some(Value::Entity(entity)) => *entity,
                    _ => 0,
                };
                match context.entity_velocity(entity) {
                    VelocityQuery::Unbound => initial,
                    VelocityQuery::Present(velocity) => {
                        let rounded = velocity.map(rounded);
                        if !velocity.iter().chain(&rounded).all(|v| v.is_finite()) {
                            return Err(EvalError::UnsupportedConfig(class.into()));
                        }
                        Value::Vector(rounded)
                    }
                    VelocityQuery::Unavailable => context
                        .native(config)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                }
            }
            "STU_A753DC79" if native_shape(config, class, Some("m_74A2248F")) => {
                let hash = child(config, "m_74A2248F");
                let key = hash
                    .as_i64()
                    .map(|key| key as u32)
                    .or_else(|| hash.as_u64().map(|key| key as u32))
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                match context.engine_setting(key) {
                    EngineSetting::Present(value) => value,
                    EngineSetting::Missing => initial,
                    EngineSetting::Unavailable => context
                        .native(config)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                }
            }
            "STU_7BD2CA19"
                if native_shape(config, class, Some("m_resourceKey"))
                    && child(config, "m_resourceKey").is_null() =>
            {
                // An explicitly admitted host input precedes the cached base
                // evaluator's no-write result (e.g. the Arcade difficulty).
                context.native(config).unwrap_or(initial)
            }
            "STUConfigVarVoiceStimulus" => {
                if resource(child(config, "m_resourceKey")).is_some_and(|guid| guid != 0) {
                    context
                        .native(config)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?
                } else {
                    Value::Asset(resource(child(config, "m_voiceStimulus")).unwrap_or(0))
                }
            }
            "STUConfigVarDynamic" => context.variable(dynamic(config)?.expect("dynamic class")),
            "STU_4BD39CFD" if native_fields(config, &["m_index", "m_814F8EC3"]) => {
                let index = self.config(child(config, "m_index"), context, nested)?;
                let index = match index {
                    Value::Array(items) => items.into_iter().next().unwrap_or(Value::Null),
                    value => value,
                };
                let index = to_int(&index, 0);
                let container = child(config, "m_814F8EC3");
                let values = if native_shape(container, "STU_06610EBC", Some("m_configVars")) {
                    let mut values = Vec::new();
                    self.collect_group(container, context, nested, &mut values)?;
                    Value::Array(values)
                } else {
                    self.config(container, context, nested)?
                };
                match values {
                    Value::Array(values) if index >= 0 => {
                        values.get(index as usize).cloned().unwrap_or(Value::Null)
                    }
                    _ => Value::Null,
                }
            }
            "STUConfigVarBool" => Value::Bool(json_truthy(value)),
            "STUConfigVarInt" => Value::Integer(i64::from(integer(value)? as i32)),
            "STUConfigVarFloat" => Value::Float(rounded(number(value)?)),
            // Build 104319 selects the failed base evaluator for this class;
            // a fresh destination remains null and the operand is never read.
            "STU_B40ACFBA" if native_fields(config, &["m_52EA7AB9"]) => Value::Null,
            "STUConfigVarLogicalButton" => {
                Value::Integer(integer(child(config, "m_logicalButton"))?)
            }
            "STU_BD6CC023" if native_shape(config, class, Some("m_6CE7A900")) => {
                let team = child(config, "m_6CE7A900");
                Value::Integer(if team.is_null() {
                    0
                } else {
                    i64::from(integer(team)? as i32)
                })
            }
            "STU_D289FA32" if native_shape(config, class, Some("m_team")) => {
                let Value::Integer(team @ 0..=4) =
                    self.config(child(config, "m_team"), context, nested)?
                else {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                };
                let count = context
                    .team_members(team as u8)
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Integer(i64::from(count))
            }
            // The body bindings and expression constants use this explicit null operand.
            "STU_92CA44CC"
                if fields.get("m_value") == Some(&Json::Null)
                    && !json_truthy(child(config, "m_EE729DCB")) =>
            {
                Value::Null
            }
            "STU_58AA0A3B" => {
                // The native predicate tests whether the child produced a non-null value tag.
                Value::Bool(self.config(value, context, nested)? != Value::Null)
            }
            "STU_06610EBC"
                if native_shape(config, class, Some("m_configVars"))
                    && child(config, "m_configVars").is_null() =>
            {
                Value::Array(Vec::new())
            }
            "STU_B3E67A36"
                if native_fields(
                    config,
                    &["m_filter", "m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"],
                ) && !child(config, "m_filter").is_null()
                    && child(config, "m_B486603E").is_null()
                    && child(config, "m_8B2A4DF3").is_null()
                    && (child(child(config, "m_filter"), "m_9C7BD8B8").is_null()
                        && target_tag_config(child(config, "m_9C7BD8B8")).is_some()
                        || child(config, "m_9C7BD8B8").is_null()
                            && context
                                .scene_filter_policy(child(config, "m_filter"))
                                .is_some_and(|policy| {
                                    policy.relation == FilterRelation::FriendlyOther
                                })) =>
            {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                let mut filter = child(config, "m_filter").clone();
                // This supported scene query uses the source's literal required-tag layout.
                if !child(config, "m_9C7BD8B8").is_null()
                    && ["m_B486603E", "m_8B2A4DF3", "m_C1903771"]
                        .iter()
                        .any(|field| !child(&filter, field).is_null())
                {
                    return Err(unsupported());
                }
                if !child(config, "m_9C7BD8B8").is_null() {
                    filter["m_9C7BD8B8"] = child(config, "m_9C7BD8B8").clone();
                }
                let candidates = context.combat_targets(config).ok_or_else(unsupported)?;
                let mut selected = 0;
                for entity in candidates {
                    if self.steps >= self.limit {
                        return Err(EvalError::BudgetExceeded {
                            offset: 0,
                            limit: self.limit,
                        });
                    }
                    self.steps += 1;
                    match self.filter_entity(&filter, entity, context, nested, "STU_B3E67A36")? {
                        Value::Bool(true) => {
                            selected = entity;
                            break;
                        }
                        Value::Bool(false) => (),
                        _ => return Err(unsupported()),
                    }
                }
                Value::Entity(selected)
            }
            "STU_B3E67A36"
                if native_fields(
                    config,
                    &["m_filter", "m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"],
                ) && child(config, "m_filter").is_null()
                    && child(config, "m_8B2A4DF3").is_null() =>
            {
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                let primary = child(config, "m_9C7BD8B8");
                let required = child(config, "m_B486603E");
                let tag = match (primary.is_null(), required.is_null()) {
                    (false, true) => primary,
                    (true, false) => required,
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                };
                let Value::Asset(tag) = self.config(tag, context, nested)? else {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                };
                let entity = (tag >> 48 == 0x0d60)
                    .then(|| context.static_target(tag))
                    .flatten()
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Entity(entity)
            }
            "STU_71A87E92" if native_shape(config, class, Some("m_entity")) => {
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                let value = self.config(child(config, "m_entity"), context, nested)?;
                match first_scalar(&value) {
                    Some(Value::Entity(0) | Value::Null) => initial,
                    Some(Value::Entity(entity)) => Value::Vector(
                        context
                            .known_position(*entity)
                            .filter(|point| point.iter().all(|axis| axis.is_finite()))
                            .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                    ),
                    Some(Value::Vector(point))
                        if *point != [0.0; 3] && point.iter().all(|axis| axis.is_finite()) =>
                    {
                        Value::Vector(*point)
                    }
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                }
            }
            "STU_021D01CD" | "STU_C8C4C20D" if native_shape(config, class, Some("m_entity")) => {
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                let value = self.config(child(config, "m_entity"), context, nested)?;
                let Some(Value::Entity(entity)) = first_scalar(&value) else {
                    // The native transform lookup has no entity for a position literal.
                    return Ok(Value::Null);
                };
                let forward = context
                    .forward(*entity)
                    .filter(|point| point.iter().all(|axis| axis.is_finite()))
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                if class == "STU_021D01CD" {
                    Value::Vector(forward)
                } else {
                    // CF3510, as reconstructed by the 104319 scene reference.
                    let [x, _, z] = forward;
                    Value::Float(if x == 0.0 && z <= 0.0 {
                        0.0
                    } else {
                        rounded(rounded(x.atan2(z)) * rounded(180.0 / std::f64::consts::PI))
                    })
                }
            }
            "STU_B113D362" if native_fields(config, &["m_7C831A8D", "m_C0EDD7EC"]) => {
                let radians = |value: &Value| {
                    rounded(to_float(value) * rounded(std::f64::consts::PI / 180.0))
                };
                let yaw = radians(&self.config(child(config, "m_7C831A8D"), context, nested)?);
                let pitch = radians(&self.config(child(config, "m_C0EDD7EC"), context, nested)?);
                let (sin_yaw, cos_yaw) = yaw.sin_cos();
                let (sin_pitch, cos_pitch) = pitch.sin_cos();
                Value::Vector([
                    rounded(rounded(sin_yaw) * rounded(cos_pitch)),
                    -rounded(sin_pitch),
                    rounded(rounded(cos_yaw) * rounded(cos_pitch)),
                ])
            }
            "STUConfigVarAnimCategory"
                if fields.get("m_category") == Some(&Json::Null)
                    && child(config, "m_resourceKey").is_null() =>
            {
                Value::Null
            }
            "STUConfigVarVec3" => {
                let mut parts = [0.0; 3];
                for (part, (upper, lower)) in
                    parts.iter_mut().zip([("X", "x"), ("Y", "y"), ("Z", "z")])
                {
                    let source = value
                        .get(upper)
                        .or_else(|| value.get(lower))
                        .unwrap_or(&Json::Null);
                    *part = rounded(number(source)?);
                }
                Value::Vector(parts)
            }
            "STU_BE0BD267" if native_fields(config, &["m_min", "m_max"]) => {
                let bound = |name| {
                    let value = child(config, name);
                    if value.is_null() {
                        Ok(0.0)
                    } else {
                        number(value).map(rounded)
                    }
                };
                let (low, high) = (bound("m_min")?, bound("m_max")?);
                if !low.is_finite() || !high.is_finite() {
                    return Err(EvalError::MalformedConfig("random float bounds"));
                }
                let sample = context
                    .random_u64()
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                let unit = (sample >> 11) as f64 / 9007199254740992.0;
                Value::Float(rounded(low + (high - low) * unit))
            }
            "STU_919FD47C" | "STUConfigVarExpression" => {
                return self.expression(config, context, nested);
            }
            "STU_B70476F3" => literal_asset(config, class, "m_548E5344", 0x0a90)
                .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
            "STU_8556841E" => literal_asset(config, class, "m_entityDef", 0x0400)
                .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
            "STUConfigVarBone" => literal_asset(config, class, "m_bone", 0x0880)
                .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
            "STU_1A3FA5B0" if native_shape(config, class, Some("m_entity")) => {
                match self.config(child(config, "m_entity"), context, nested)? {
                    Value::Entity(entity) if valid_entity(entity) => Value::Asset(
                        context
                            .entity_definition(entity)
                            .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                    ),
                    Value::Entity(_) | Value::Null => Value::Asset(0),
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                }
            }
            "STUConfigVarTexture"
                if native_fields(config, &["m_texture", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                Value::Asset(resource(child(config, "m_texture")).unwrap_or(0))
            }
            "STU_08305297"
                if native_fields(config, &["m_C21DB248", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                Value::Asset(resource(child(config, "m_C21DB248")).unwrap_or(0))
            }
            "STU_ABB6C23E" => {
                if !native_shape(config, class, Some("m_resourceKey"))
                    || fields.get("m_resourceKey") != Some(&Json::Null)
                {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                // The native evaluator writes zero with the identifier's asset type.
                Value::Asset(0)
            }
            "STU_105E1BCC"
                if native_fields(config, &["m_graph", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                Value::Asset(resource(child(config, "m_graph")).unwrap_or(0))
            }
            "STU_45B0D2B2" if native_shape(config, class, Some("m_configVars")) => Value::Array(
                array_field(config, "m_configVars")?
                    .iter()
                    .map(|item| self.authored_value(item, context, nested))
                    .collect::<Result<_, _>>()?,
            ),
            "STU_9DB46B98" if native_shape(config, class, Some("m_6525E98B")) => {
                let definition = child(config, "m_6525E98B");
                if definition.is_null() {
                    return Ok(Value::Integer(0));
                }
                if self::class(definition) != Some("STUStatescriptGraphWithOverrides")
                    || !native_fields(definition, &["m_graph", "m_1EB5A024", "m_75408640"])
                {
                    return Err(EvalError::MalformedConfig("inline played graph"));
                }
                let Some(graph) =
                    asset_guid(child(definition, "m_graph")).filter(|guid| *guid != 0)
                else {
                    return Ok(Value::Integer(0));
                };
                let mut buckets = vec![None; 8];
                self.insert_authored_map(&mut buckets, 0x4065, Value::Asset(graph))?;
                for row in array_field(definition, "m_1EB5A024")? {
                    if self::class(row) != Some("STUStatescriptSchemaEntry")
                        || !native_fields(row, &["m_0D09D2D9", "m_value"])
                    {
                        return Err(EvalError::MalformedConfig("inline played graph override"));
                    }
                    let value = self.authored_value(child(row, "m_value"), context, nested)?;
                    let identifier = child(row, "m_0D09D2D9");
                    let key = if identifier.is_null() {
                        0
                    } else {
                        asset_guid(identifier)
                            .map(|guid| guid as u32)
                            .filter(|key| *key <= i32::MAX as u32)
                            .ok_or(EvalError::MalformedConfig("inline played graph key"))?
                    };
                    self.insert_authored_map(&mut buckets, key, value)?;
                }
                Value::Map(
                    buckets
                        .into_iter()
                        .flatten()
                        .map(|(key, value)| (Value::Integer(i64::from(key)), value))
                        .collect(),
                )
            }
            "STU_7042F028" if native_shape(config, class, Some("m_92829780")) => {
                let malformed = || EvalError::MalformedConfig("invalid authored map row");
                let mut buckets = vec![None; 8];
                for row in array_field(config, "m_92829780")? {
                    if self::class(row) != Some("STU_D8196947")
                        || !native_fields(row, &["m_identifier", "m_value"])
                    {
                        return Err(malformed());
                    }
                    let value = self.authored_value(child(row, "m_value"), context, nested)?;
                    let key = asset_guid(child(row, "m_identifier"))
                        .map(|guid| guid as u32)
                        .filter(|&key| key <= i32::MAX as u32)
                        .ok_or_else(malformed)?;
                    self.insert_authored_map(&mut buckets, key, value)?;
                }
                Value::Map(
                    buckets
                        .into_iter()
                        .flatten()
                        .map(|(key, value)| (Value::Integer(i64::from(key)), value))
                        .collect(),
                )
            }
            "STU_A6B80C63"
                if native_fields(
                    config,
                    &["m_814F8EC3", "m_418D9D9D", "m_C239F989", "m_8054375D"],
                ) =>
            {
                self.fold_array(config, context, nested)?
            }
            "STU_7757D403" => literal_message_map(config)?,
            "STU_B5A0CAF0" => context.owner(),
            "STU_25ECB55F" => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                if !native_fields(config, &["m_entity", "m_88B0050A"]) {
                    return Err(unsupported());
                }
                let tag = target_tag_guid(child(config, "m_88B0050A")).ok_or_else(unsupported)?;
                match self.config(child(config, "m_entity"), context, nested)? {
                    Value::Entity(entity) if valid_entity(entity) => Value::Bool(
                        context
                            .has_target_tag(entity, tag)
                            .ok_or_else(unsupported)?,
                    ),
                    Value::Entity(_) | Value::Null => Value::Bool(false),
                    _ => return Err(unsupported()),
                }
            }
            "STU_AC829876" => {
                return match self.entity_filter(config, context, nested) {
                    Err(error @ EvalError::UnsupportedConfig(_)) => match context.native(config) {
                        Some(value @ Value::Bool(_)) => Ok(value),
                        _ => Err(error),
                    },
                    result => result,
                };
            }
            "STU_1ED4F039" if native_shape(config, class, Some("m_entity")) => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                let Value::Entity(entity) =
                    self.config(child(config, "m_entity"), context, nested)?
                else {
                    return Err(unsupported());
                };
                match context.body_on_fire(entity).ok_or_else(unsupported)? {
                    OnFire::Active(active) => Value::Bool(active),
                    OnFire::NoStats => initial,
                }
            }
            "STU_4A101787" | "STU_E95A9C77" if native_shape(config, class, None) => {
                // The retail classes share a native evaluator that writes a typed false.
                Value::Bool(false)
            }
            "STU_A58C1E63" => {
                if !native_fields(config, &["m_source", "m_target", "m_AFBBE9C3"]) {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                let target = self.scalar_config(
                    child(config, "m_target"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let source = self.scalar_config(
                    child(config, "m_source"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let option = self.scalar_config(
                    child(config, "m_AFBBE9C3"),
                    context,
                    nested,
                    Value::Bool(false),
                )?;
                Value::Bool(
                    context
                        .entities_hostile(
                            entity_scalar(&source),
                            entity_scalar(&target),
                            truthy(&option),
                        )
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                )
            }
            "STU_00707E1E" => {
                if !native_shape(config, class, Some("m_00903303")) {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                let selected = self.scalar_config(
                    child(config, "m_00903303"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let entity = entity_scalar(&selected);
                Value::Entity(
                    context
                        .related_entity(EntityRelation::ControllerBodyField, entity)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                )
            }
            "STU_F76C486C" | "STU_F7262B36" | "STU_0AC15F78" | "STU_DB742151" => {
                let (relation, operand) = match class {
                    "STU_F76C486C" => (EntityRelation::OwningController, "m_D63CBEC2"),
                    "STU_F7262B36" => (EntityRelation::CurrentPossessor, "m_801B298F"),
                    "STU_DB742151" => (EntityRelation::AiControlEntity, "m_entity"),
                    _ => (EntityRelation::PossessedBody, "m_entity"),
                };
                if !native_shape(config, class, Some(operand)) {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                let Value::Entity(entity) = self.config(child(config, operand), context, nested)?
                else {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                };
                let related = context
                    .related_entity(relation, entity)
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Entity(related)
            }
            "STU_E72F6348" if native_shape(config, class, Some("m_entity")) => {
                if let Some(value @ Value::Bool(_)) = context.native(config) {
                    return Ok(value);
                }
                let target = self.config_with_initial(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                Value::Bool(match first_scalar(&target) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => context
                        .entity_exists(*entity)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                    _ => false,
                })
            }
            "STU_A37A910D" if native_fields(config, &["m_152B6961", "m_A60150E2"]) => {
                let entity = self.config_with_initial(
                    child(config, "m_152B6961"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let entity = match first_scalar(&entity) {
                    Some(Value::Entity(id)) => *id,
                    _ => 0,
                };
                let slot = self.authored_value(child(config, "m_A60150E2"), context, nested)?;
                Value::Entity(
                    context
                        .linked_entity(entity, to_int(&slot, 0) as u32)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                )
            }
            "STU_D3EB8D87" if native_shape(config, class, Some("m_00903303")) => {
                if let Some(value) = context.native(config) {
                    value
                } else {
                    let unsupported = || EvalError::UnsupportedConfig(class.into());
                    let Value::Entity(entity) =
                        self.config(child(config, "m_00903303"), context, nested)?
                    else {
                        return Err(unsupported());
                    };
                    Value::Bool(if valid_entity(entity) {
                        context.entity_component92(entity).ok_or_else(unsupported)?
                    } else {
                        false
                    })
                }
            }
            "STU_65ABFCCC" if native_shape(config, class, Some("m_target")) => {
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                let value = self.config(child(config, "m_target"), context, nested)?;
                let target = match first_scalar(&value) {
                    Some(&Value::Entity(target)) if valid_entity(target) => target,
                    _ => return Ok(Value::Integer(-1)),
                };
                Value::Integer(i64::from(
                    context
                        .entity_team(target)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                ))
            }
            "STU_60146072" if native_shape(config, class, Some("m_entity")) => {
                match self.config(child(config, "m_entity"), context, nested)? {
                    Value::Entity(entity) if valid_entity(entity) => Value::Float(
                        context
                            .body_health(entity, None, true)
                            .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                    ),
                    Value::Entity(_) | Value::Null => Value::Float(0.0),
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                }
            }
            "STU_CD7F3617" if native_fields(config, &["m_entity", "m_B350E4FD"]) => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                if self.config(child(config, "m_B350E4FD"), context, nested)? != Value::Bool(false)
                {
                    return Err(unsupported());
                }
                match self.config(child(config, "m_entity"), context, nested)? {
                    Value::Entity(entity) if valid_entity(entity) => {
                        Value::String(context.body_display_name(entity).ok_or_else(unsupported)?)
                    }
                    Value::Entity(_) | Value::Null => initial,
                    _ => return Err(unsupported()),
                }
            }
            "STU_F1C45C59" | "STU_005D99E6"
                if native_fields(config, &["m_entity", "m_7E8B666C", "m_7BE02F89"])
                    && child(config, "m_7BE02F89").is_null() =>
            {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                let selector = child(config, "m_7E8B666C");
                let kind = match (
                    child(selector, "$").as_str(),
                    child(selector, "m_7E8B666C").as_u64(),
                ) {
                    (Some("STU_297E1A30"), Some(0)) => PoolKind::Health,
                    (Some("STU_297E1A30"), Some(1)) => PoolKind::Armour,
                    _ => return Err(unsupported()),
                };
                let health = match self.config(child(config, "m_entity"), context, nested)? {
                    Value::Entity(entity) if valid_entity(entity) => context
                        .body_health(entity, Some(kind), class == "STU_005D99E6")
                        .ok_or_else(unsupported)?,
                    Value::Entity(_) | Value::Null => 0.0,
                    _ => return Err(unsupported()),
                };
                Value::Float(health)
            }
            "STU_9B7648C3" if native_shape(config, class, Some("m_entity")) => {
                let target = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let entity = match first_scalar(&target) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => *entity,
                    _ => return Ok(initial),
                };
                match context.total_health(entity) {
                    HealthQuery::Unbound => initial,
                    HealthQuery::Unavailable => context
                        .native(config)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                    HealthQuery::Present(health) if (0.0..=3.402_823_466e38).contains(&health) => {
                        Value::Float(rounded(health))
                    }
                    HealthQuery::Present(_) => {
                        return Err(EvalError::UnsupportedConfig(class.into()));
                    }
                }
            }
            "STU_32E6E4D8" | "STU_7D7D2470" => {
                let target = child(config, "m_entity");
                let value = self.config(target, context, nested)?;
                let entity = match first_scalar(&value) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => *entity,
                    Some(Value::Entity(entity)) => *entity,
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                };
                let health = context
                    .body_health(entity, None, class == "STU_7D7D2470")
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Float(health)
            }
            "STU_93DCDDFD"
                if native_fields(config, &["m_entity", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                let Value::Entity(entity) =
                    self.config(child(config, "m_entity"), context, nested)?
                else {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                };
                Value::Asset(
                    context
                        .entity_hero(entity)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                )
            }
            "STU_783B4CF0" | "STU_68883EAC" => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                if !native_fields(config, &["m_entity", "m_7E8B666C", "m_7BE02F89"])
                    || !child(config, "m_7BE02F89").is_null()
                {
                    return Err(unsupported());
                }
                let selector = child(config, "m_7E8B666C");
                let kind = if selector.is_null() {
                    PoolKind::Health
                } else {
                    if !native_shape(selector, "STU_297E1A30", Some("m_7E8B666C")) {
                        return Err(unsupported());
                    }
                    match child(selector, "m_7E8B666C").as_i64() {
                        Some(0) => PoolKind::Health,
                        Some(1) => PoolKind::Armour,
                        Some(2) => PoolKind::Shields,
                        _ => return Err(unsupported()),
                    }
                };
                let Value::Entity(entity) =
                    self.config(child(config, "m_entity"), context, nested)?
                else {
                    return Err(unsupported());
                };
                let health = context
                    .body_health(entity, Some(kind), class == "STU_68883EAC")
                    .ok_or_else(unsupported)?;
                Value::Float(rounded(health))
            }
            "STU_FDBB0B67" => {
                let target = child(config, "m_entity");
                if !matches!(
                    self::class(target),
                    Some("STU_B5A0CAF0" | "STUConfigVarDynamic")
                ) {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                let Value::Entity(entity) = self.config(target, context, nested)? else {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                };
                if entity == 0 || entity & 0x1fff_ffff == 0x1fff_ffff {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                let scale = context
                    .body_scale(entity)
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Float(scale)
            }
            "STU_3832D36C" => {
                let key = match dynamic(value)? {
                    Some(variable) => Some(variable.id),
                    None => u32::try_from(to_int(&self.config(value, context, nested)?, 0)).ok(),
                };
                key.map_or(Value::Null, |key| context.ruleset(key))
            }
            "STU_91EFD5B1" => {
                let value = self.config(value, context, nested)?;
                if value == Value::Null {
                    self.config(child(config, "m_default"), context, nested)?
                } else {
                    value
                }
            }
            "STU_3ACE35FB" => {
                let Some(variable) = dynamic(child(config, "m_E50CF556"))? else {
                    return Ok(Value::Bool(false));
                };
                let priority = stack_priority(child(config, "m_012B7AF7"))?;
                let top = context
                    .would_stack_to_top(variable, priority)
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Bool(top)
            }
            "STU_45EDF12D" if child(config, "m_key").is_null() => {
                let container = self.config(child(config, "m_814F8EC3"), context, nested)?;
                let value = self.config(value, context, nested)?;
                Value::Bool(contains(&container, &value))
            }
            "STU_7197C080" => {
                let base = to_float(&self.config(child(config, "m_01AFADB6"), context, nested)?);
                let least = to_float(&self.config(child(config, "m_EA70ACC2"), context, nested)?);
                let scalar = child(config, "m_69A20070");
                let scalar = if scalar.is_null() {
                    1.0
                } else {
                    to_float(&self.config(scalar, context, nested)?)
                };
                let product = rounded(base * scalar);
                Value::Float(if product > least { product } else { least })
            }
            _ => {
                if let Some(field) = resource_field(class) {
                    return Ok(Value::Asset(resource(child(config, field)).unwrap_or(0)));
                }
                if fields.contains_key("m_resourceKey") {
                    if let Some(guid) = fields
                        .iter()
                        .filter(|(key, _)| key.as_str() != "m_resourceKey")
                        .filter_map(|(_, value)| resource(value))
                        .find(|&guid| guid != 0)
                    {
                        return Ok(Value::Asset(guid));
                    }
                }
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                if matches!(class, "STU_4FC9D3E1" | "STU_7DCA538E" | "STU_0340A42A")
                    && context.native_uses_initial(config)
                {
                    return Ok(initial);
                }
                match class {
                    "STU_CC623180" => Value::Bool(true),  // IsHUDEnabled
                    "STU_98E9109B" => Value::Bool(false), // CustomGameLogicEnabled
                    "STU_5E75FF05" => Value::Integer(0),  // ReticleType
                    "STU_C76C63AF" => Value::Float(0.0),  // ReticleNumericSetting
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                }
            }
        };
        Ok(result)
    }

    fn entity_filter(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let unsupported = || EvalError::UnsupportedConfig("STU_AC829876".into());
        if !native_fields(config, &["m_entity", "m_filter"]) {
            return Err(unsupported());
        }
        let filter = child(config, "m_filter");
        // Reject malformed host configs, but native D0DA20 resolves the target
        // before invoking mask/tag/relationship services for a known filter.
        if !filter_shape(filter) {
            return Err(unsupported());
        }
        let entity = match self.config(child(config, "m_entity"), context, depth)? {
            Value::Entity(entity) => entity,
            Value::Null => return Ok(Value::Bool(false)),
            _ => return Err(unsupported()),
        };
        if !valid_entity(entity) || context.entity_exists(entity) == Some(false) {
            return Ok(Value::Bool(false));
        }
        self.filter_entity(filter, entity, context, depth, "STU_AC829876")
    }

    fn filter_entity(
        &mut self,
        filter: &Json,
        entity: u32,
        context: &mut impl Context,
        depth: usize,
        diagnostic_class: &str,
    ) -> Result<Value, EvalError> {
        let unsupported = || EvalError::UnsupportedConfig(diagnostic_class.into());
        let policy = context.scene_filter_policy(filter);
        let [any, all, exclude] = filter_masks(filter, policy.is_some()).ok_or_else(unsupported)?;
        if !valid_entity(entity) {
            return Ok(Value::Bool(false));
        }
        let needs_flags =
            (any | all | exclude) != 0 && (any != ANY_ENTITY_MASK || all != 0 || exclude != 0);
        let flags = needs_flags
            .then(|| context.entity_filter_flags(entity))
            .flatten();
        if flags.is_some_and(|flags| {
            (any != ANY_ENTITY_MASK && flags & any == 0)
                || flags & all != all
                || flags & exclude != 0
        }) {
            return Ok(Value::Bool(false));
        }
        if let Some(policy) = policy {
            let Value::Entity(owner) = context.owner() else {
                return Err(unsupported());
            };
            let source = context.entity_filter_flags(owner).ok_or_else(unsupported)?;
            let target = flags
                .or_else(|| context.entity_filter_flags(entity))
                .ok_or_else(unsupported)?;
            let source = (source >> 23) & 31;
            let target = (target >> 23) & 31;
            if source == 0 || target == 0 {
                return Ok(Value::Bool(false));
            }
            let friendly = source.trailing_zeros() == target.trailing_zeros();
            if match policy.relation {
                FilterRelation::Hostile => friendly,
                FilterRelation::FriendlyOther => !friendly || entity == owner,
            } {
                return Ok(Value::Bool(false));
            }
        }
        let mut unknown = needs_flags && flags.is_none();
        let mut has_fact = flags.is_some();
        // D0EC70 collects +58/+60/+68 in order; DB2FD0 applies any/all/excluded.
        // These tag clauses do not change with the relationship policy.
        for (field, condition) in [
            ("m_9C7BD8B8", TagCondition::Any),
            ("m_B486603E", TagCondition::All),
            ("m_8B2A4DF3", TagCondition::Exclude),
        ] {
            match check_tag_set(child(filter, field), condition, entity, context)
                .map_err(|_| unsupported())?
            {
                TagCheck::Unused => (),
                TagCheck::Accept => has_fact = true,
                TagCheck::Reject => return Ok(Value::Bool(false)),
                TagCheck::Unknown => unknown = true,
            }
        }
        if unknown || !has_fact && context.entity_filter_flags(entity).is_none() {
            return Err(unsupported());
        }
        if let Some(policy) = policy.filter(|policy| policy.predicate != FilterPredicate::None) {
            let scoped = policy.predicate == FilterPredicate::Target;
            if scoped {
                self.scoped_parameters.push((8, Value::Entity(entity)));
            }
            let result = self.scalar_config(
                child(filter, "m_condition"),
                context,
                depth + 1,
                if scoped {
                    Value::Bool(true)
                } else {
                    Value::Null
                },
            );
            if scoped {
                self.scoped_parameters.pop();
            }
            return result.map(|value| Value::Bool(truthy(&value)));
        }
        Ok(Value::Bool(true))
    }

    fn expression(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let Expression {
            code,
            dynamics,
            floats,
            configs,
        } = Expression::read(config)?;
        let mut pc = 0;
        let mut stack = Vec::new();
        while pc < code.len() {
            if self.steps >= self.limit {
                return Err(EvalError::BudgetExceeded {
                    offset: pc,
                    limit: self.limit,
                });
            }
            self.steps += 1;
            let opcode = byte(&code[pc], pc)?;
            let width = operand_count(opcode);
            let next = pc + 1 + width;
            let operands = code
                .get(pc + 1..next)
                .ok_or_else(|| malformed(pc, "truncated opcode"))?;
            let mut args = [0; 2];
            for (index, (arg, value)) in args.iter_mut().zip(operands).enumerate() {
                *arg = byte(value, pc + 1 + index)?;
            }
            let arg = args[0];
            let value = match opcode {
                0 => break,
                1 => {
                    pc = jump(pc, arg, code.len())?;
                    continue;
                }
                2 | 3 => {
                    let top = stack
                        .last()
                        .ok_or_else(|| malformed(pc, "expression stack underflow"))?;
                    if truthy(top) != (opcode == 2) {
                        pc = jump(pc, arg, code.len())?;
                    } else {
                        stack.pop();
                        pc = next;
                    }
                    continue;
                }
                4 | 5 => {
                    let flag = truthy(&pop(&mut stack, pc)?);
                    pc = if flag == (opcode == 5) {
                        jump(pc, arg, code.len())?
                    } else {
                        next
                    };
                    continue;
                }
                6 => {
                    let value = table(floats, arg, pc)?;
                    if value.is_null() {
                        return Err(malformed(pc, "expected a float constant"));
                    }
                    Value::Float(rounded(number(value)?))
                }
                7 => Value::Integer(i64::from(identifier(table(dynamics, arg, pc)?)?)),
                8 | 9 => context.variable(stack_var(dynamics, arg, pc)?),
                10 => context.parent_variable(arg, stack_var(dynamics, args[1], pc)?),
                11 | 12 => {
                    let scope = if opcode == 11 {
                        Scope::Instance
                    } else {
                        Scope::Entity
                    };
                    lookup(&pop(&mut stack, pc)?, scope).map_or(Value::Null, |variable| {
                        context.parent_variable(arg, variable)
                    })
                }
                13 => self.parameter(context, identifier(table(dynamics, arg, pc)?)?),
                14 => {
                    let value = configs.get(usize::from(arg)).unwrap_or(&Json::Null);
                    self.config(value, context, depth)?
                }
                15 | 16 => {
                    let (object, key) = pair(&mut stack, pc)?;
                    entity_member(&object, &key, context)?
                }
                17 => {
                    let (object, key) = pair(&mut stack, pc)?;
                    index(object, &key)
                }
                18 => {
                    let length = match pop(&mut stack, pc)? {
                        Value::Array(items) => items.len() as i64,
                        _ => 0,
                    };
                    Value::Integer(length)
                }
                20 => {
                    let start = stack
                        .len()
                        .checked_sub(usize::from(arg))
                        .ok_or_else(|| malformed(pc, "expression stack underflow"))?;
                    Value::Array(stack.split_off(start))
                }
                21 => context.parent_entity(arg),
                22 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    let mut items = into_array(a);
                    items.extend(into_array(b));
                    Value::Array(items)
                }
                23..=28 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    arithmetic(opcode, &a, &b, context)?
                }
                29 => match pop(&mut stack, pc)? {
                    Value::Vector(parts) => Value::Vector(parts.map(|part| -part)),
                    value => Value::Float(rounded(-to_float(&value))),
                },
                30 => Value::Bool(!truthy(&pop(&mut stack, pc)?)),
                31 | 32 => {
                    let scope = if opcode == 31 {
                        Scope::Instance
                    } else {
                        Scope::Entity
                    };
                    lookup(&pop(&mut stack, pc)?, scope)
                        .map_or(Value::Null, |variable| context.variable(variable))
                }
                33..=36 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    let (a, b) = (to_float(&a), to_float(&b));
                    Value::Bool(match opcode {
                        33 => a - EPSILON <= b,
                        34 => a < b - EPSILON,
                        35 => a + EPSILON >= b,
                        36 => a > b + EPSILON,
                        _ => unreachable!(),
                    })
                }
                37 | 38 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    Value::Bool(equal(&a, &b) == (opcode == 37))
                }
                39 => {
                    let value = to_float(&pop(&mut stack, pc)?);
                    Value::Float(if value > 0.0 {
                        rounded(value.sqrt())
                    } else {
                        0.0
                    })
                }
                50 | 51 => {
                    let value = pop(&mut stack, pc)?;
                    Value::Float(
                        vector_position(&value, context)?
                            .map_or_else(|| rounded(to_float(&value).abs()), magnitude),
                    )
                }
                52 => {
                    let [x, _, z] = vector(&pop(&mut stack, pc)?);
                    Value::Float(rounded(x.hypot(z)))
                }
                53 | 54 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    let a = vector_position(&a, context)?.unwrap_or([0.0; 3]);
                    let b = vector_position(&b, context)?.unwrap_or([0.0; 3]);
                    let delta = vector_op(a, b, |a, b| a - b);
                    Value::Float(if opcode == 53 {
                        magnitude(delta)
                    } else {
                        rounded(delta[0].hypot(delta[2]))
                    })
                }
                55 | 56 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    let [ax, ay, az] = vector(&a);
                    let [bx, by, bz] = vector(&b);
                    if opcode == 55 {
                        Value::Float(rounded(ax * bx + ay * by + az * bz))
                    } else {
                        Value::Vector([
                            rounded(ay * bz - az * by),
                            rounded(az * bx - ax * bz),
                            rounded(ax * by - ay * bx),
                        ])
                    }
                }
                57 => {
                    let parts = vector(&pop(&mut stack, pc)?);
                    let length = magnitude(parts);
                    Value::Vector(if length == 0.0 {
                        [0.0; 3]
                    } else {
                        parts.map(|part| rounded(part / length))
                    })
                }
                58 => {
                    let z = to_float(&pop(&mut stack, pc)?);
                    let (x, y) = pair(&mut stack, pc)?;
                    Value::Vector([to_float(&x), to_float(&y), z])
                }
                60..=62 => Value::Float(vector(&pop(&mut stack, pc)?)[usize::from(opcode - 60)]),
                63 | 64 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    let (a, b) = (to_float(&a), to_float(&b));
                    Value::Float(if opcode == 63 {
                        minimum(a, b)
                    } else {
                        maximum(a, b)
                    })
                }
                65 => {
                    let hi = to_float(&pop(&mut stack, pc)?);
                    let (value, lo) = pair(&mut stack, pc)?;
                    let (value, lo) = (to_float(&value), to_float(&lo));
                    Value::Float(maximum(minimum(value, maximum(lo, hi)), minimum(lo, hi)))
                }
                66 => Value::Float(rounded(to_float(&pop(&mut stack, pc)?).floor())),
                67 => Value::Float(rounded(to_float(&pop(&mut stack, pc)?).ceil())),
                68 => Value::Integer(i64::from(round_half_away(to_float(&pop(&mut stack, pc)?)))),
                69 | 70 => {
                    let (a, b) = pair(&mut stack, pc)?;
                    let mut sample = context
                        .random_u64()
                        .ok_or(EvalError::UnsupportedOpcode { opcode, offset: pc })?;
                    if opcode == 70
                        || matches!(
                            (&a, &b),
                            (Value::Integer(_), Value::Integer(_) | Value::Bool(_))
                        )
                    {
                        let (a, b) = (to_int(&a, 0), to_int(&b, 0));
                        let low = i128::from(a.min(b));
                        let count = (i128::from(a.max(b)) - low + 1) as u128;
                        // Discard the incomplete residue range to keep inclusive integer rolls uniform.
                        let reject_below = (1_u128 << 64) % count;
                        while u128::from(sample) < reject_below {
                            if self.steps >= self.limit {
                                return Err(EvalError::BudgetExceeded {
                                    offset: pc,
                                    limit: self.limit,
                                });
                            }
                            self.steps += 1;
                            sample = context
                                .random_u64()
                                .ok_or(EvalError::UnsupportedOpcode { opcode, offset: pc })?;
                        }
                        Value::Integer((low + (u128::from(sample) % count) as i128) as i64)
                    } else {
                        let (a, b) = (to_float(&a), to_float(&b));
                        let unit = (sample >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0);
                        Value::Float(rounded(a + (b - a) * unit))
                    }
                }
                71 => {
                    let t = to_float(&pop(&mut stack, pc)?);
                    let (a, b) = pair(&mut stack, pc)?;
                    let (a, b) = (to_float(&a), to_float(&b));
                    Value::Float(rounded(a + (b - a) * t))
                }
                _ => return Err(EvalError::UnsupportedOpcode { opcode, offset: pc }),
            };
            stack.push(value);
            pc = next;
        }
        Ok(stack.pop().unwrap_or(Value::Null))
    }
}

struct Expression<'a> {
    code: &'a [Json],
    dynamics: &'a [Json],
    floats: &'a [Json],
    configs: &'a [Json],
}

impl<'a> Expression<'a> {
    fn read(config: &'a Json) -> Result<Self, EvalError> {
        let data = child(config, "m_expression");
        Ok(Self {
            code: array_field(data, "m_opcodes")?,
            dynamics: array_field(data, "m_dynamicVars")?,
            floats: array_field(data, "m_D99EF254")?,
            configs: array_field(config, "m_configVars")?,
        })
    }
}

fn array_field<'a>(config: &'a Json, key: &str) -> Result<&'a [Json], EvalError> {
    match child(config, key) {
        Json::Null => Ok(&[]),
        Json::Array(values) => Ok(values),
        _ => Err(EvalError::MalformedConfig("expected an expression table")),
    }
}

fn malformed(offset: usize, reason: &'static str) -> EvalError {
    EvalError::MalformedBytecode { offset, reason }
}

fn byte(value: &Json, offset: usize) -> Result<u8, EvalError> {
    value
        .as_u64()
        .and_then(|value| u8::try_from(value).ok())
        .ok_or_else(|| malformed(offset, "expected a byte"))
}

fn operand_count(opcode: u8) -> usize {
    match opcode {
        10 => 2,
        1..=9 | 11..=14 | 19..=21 => 1,
        _ => 0,
    }
}

fn table(values: &[Json], index: u8, offset: usize) -> Result<&Json, EvalError> {
    values
        .get(usize::from(index))
        .ok_or_else(|| malformed(offset, "expression table index out of range"))
}

fn stack_var(values: &[Json], index: u8, offset: usize) -> Result<Variable, EvalError> {
    dynamic(table(values, index, offset)?)?
        .ok_or_else(|| malformed(offset, "expected a dynamic variable"))
}

fn jump(pc: usize, offset: u8, size: usize) -> Result<usize, EvalError> {
    if offset == 0 {
        return Err(malformed(pc, "non-progressing jump"));
    }
    let target = pc + usize::from(offset);
    if target >= size {
        return Err(malformed(pc, "jump leaves the stream"));
    }
    Ok(target)
}

fn pop(stack: &mut Vec<Value>, pc: usize) -> Result<Value, EvalError> {
    stack
        .pop()
        .ok_or_else(|| malformed(pc, "expression stack underflow"))
}

fn pair(stack: &mut Vec<Value>, pc: usize) -> Result<(Value, Value), EvalError> {
    let b = pop(stack, pc)?;
    Ok((pop(stack, pc)?, b))
}

fn lookup(key: &Value, scope: Scope) -> Option<Variable> {
    Some(Variable {
        scope,
        id: u32::try_from(to_int(key, 0)).ok()?,
    })
}

fn entity_member(
    object: &Value,
    key: &Value,
    context: &mut impl Context,
) -> Result<Value, EvalError> {
    if let Value::IdentifierMap(map) = object {
        return Ok(map
            .entries()
            .find_map(|(entry, value)| {
                equal(&Value::Integer(i64::from(entry)), key).then(|| value.clone())
            })
            .unwrap_or(Value::Null));
    }
    if let Value::Map(entries) = object {
        return Ok(entries
            .iter()
            .find_map(|(entry, value)| equal(entry, key).then(|| value.clone()))
            .unwrap_or(Value::Null));
    }
    let id = match object {
        Value::Entity(id) => *id,
        Value::Array(items) => match items.first() {
            Some(Value::Entity(id)) => *id,
            _ => return Ok(Value::Null),
        },
        _ => return Ok(Value::Null),
    };
    let key = match key {
        Value::Asset(guid) => Some((guid & 0xffff) as u32),
        key => u32::try_from(to_int(key, 0)).ok(),
    };
    let Some(key) = key.filter(|_| valid_entity(id)) else {
        return Ok(Value::Null);
    };
    context
        .entity_member(id, key)
        .ok_or(EvalError::UnavailableEntityVariable {
            entity: id,
            id: key,
        })
}

fn into_array(value: Value) -> Vec<Value> {
    match value {
        Value::Null => Vec::new(),
        Value::Array(items) => items,
        value => vec![value],
    }
}

fn vector(value: &Value) -> [f64; 3] {
    match value {
        Value::Vector(parts) => *parts,
        _ => [0.0; 3],
    }
}

fn vector_position(
    value: &Value,
    context: &mut impl Context,
) -> Result<Option<[f64; 3]>, EvalError> {
    match value {
        Value::Vector(parts) => Ok(Some(*parts)),
        Value::Entity(id) => context
            .position(*id)
            .map(Some)
            .ok_or(EvalError::UnavailableEntityPosition(*id)),
        _ => Ok(None),
    }
}

fn vector_op(a: [f64; 3], b: [f64; 3], op: impl Fn(f64, f64) -> f64) -> [f64; 3] {
    std::array::from_fn(|index| rounded(op(a[index], b[index])))
}

fn magnitude([x, y, z]: [f64; 3]) -> f64 {
    rounded((x * x + y * y + z * z).sqrt())
}

fn minimum(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

fn maximum(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

fn arithmetic(
    opcode: u8,
    a: &Value,
    b: &Value,
    context: &mut impl Context,
) -> Result<Value, EvalError> {
    if matches!(opcode, 23 | 24)
        && matches!(a, Value::Vector(_) | Value::Entity(_))
        && matches!(b, Value::Vector(_) | Value::Entity(_))
    {
        let a = vector_position(a, context)?.expect("vector or entity");
        let b = vector_position(b, context)?.expect("vector or entity");
        return Ok(Value::Vector(vector_op(a, b, |a, b| {
            if opcode == 23 { a + b } else { a - b }
        })));
    }
    if opcode == 25 {
        match (a, b) {
            (Value::Vector(a), Value::Vector(b)) => {
                return Ok(Value::Vector(vector_op(*a, *b, |a, b| a * b)));
            }
            (Value::Vector(parts), scalar) | (scalar, Value::Vector(parts)) => {
                let scalar = to_float(scalar);
                return Ok(Value::Vector(parts.map(|part| rounded(part * scalar))));
            }
            _ => (),
        }
    }
    if let (26, Value::Vector(parts)) = (opcode, a) {
        if let Value::Vector(divisor) = b {
            return Ok(Value::Vector(vector_op(*parts, *divisor, |a, b| {
                if b == 0.0 { 0.0 } else { a / b }
            })));
        }
        let scalar = to_float(b);
        if scalar.abs() < 1.18e-36 {
            return Ok(Value::Vector([0.0; 3]));
        }
        let inverse = rounded(1.0 / scalar);
        return Ok(Value::Vector(parts.map(|part| rounded(part * inverse))));
    }
    let (x, y) = (to_float(a), to_float(b));
    let value = match opcode {
        23 => x + y,
        24 => x - y,
        25 => x * y,
        26 if y != 0.0 && !y.is_nan() => x / y,
        27 if y != 0.0 => x % y,
        28 if x > 0.0 => x.powf(y),
        _ => 0.0,
    };
    Ok(Value::Float(rounded(value)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::BTreeMap, path::PathBuf};

    use serde_json::json;

    use crate::script_graph::GraphStore;

    #[derive(Default)]
    struct Vars {
        instance: BTreeMap<u32, Value>,
        entity: BTreeMap<u32, Value>,
        reads: Vec<Variable>,
    }

    impl Context for Vars {
        fn variable(&mut self, variable: Variable) -> Value {
            self.reads.push(variable);
            let values = match variable.scope {
                Scope::Instance => &self.instance,
                Scope::Entity => &self.entity,
            };
            values.get(&variable.id).cloned().unwrap_or(Value::Null)
        }

        fn parent_variable(&mut self, levels: u8, variable: Variable) -> Value {
            match (levels, variable.scope, variable.id) {
                (1, Scope::Instance, 14152) => Value::Asset(0x02e0_0000_0000_0002),
                _ => Value::Null,
            }
        }

        fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
            (entity == 0xa000_0100).then_some(match id {
                7423 => Value::Integer(23),
                _ => Value::Null,
            })
        }

        fn ruleset(&mut self, id: u32) -> Value {
            match id {
                10265 => Value::Float(1.0),
                _ => Value::Null,
            }
        }

        fn native(&mut self, config: &Json) -> Option<Value> {
            match class(config)? {
                "STU_37F20D24" => Some(Value::Asset(0x0230_0000_0000_0018)),
                _ => None,
            }
        }
    }

    #[test]
    fn inline_played_graph_uses_native_map_order_and_duplicate_low_keys() {
        let inline = |rows| {
            json!({"$":"STU_9DB46B98","m_6525E98B":{
            "$":"STUStatescriptGraphWithOverrides","m_graph":"0x058000000000224E","m_1EB5A024":rows}})
        };
        let row = |key, value| json!({"$":"STUStatescriptSchemaEntry","m_0D09D2D9":format!("0x0D800000{key:08X}"),"m_value":value});
        let config = inline(vec![
            row(1, Json::from(10)),
            row(9, Json::Null),
            row(1, Json::from(20)),
            row(0, Json::from(99)),
        ]);
        assert_eq!(
            evaluate(&config, &mut Vars::default()).unwrap(),
            Value::Map(vec![
                (Value::Integer(1), Value::Integer(20)),
                (Value::Integer(9), Value::Integer(0)),
                (Value::Integer(0x4065), Value::Asset(0x0580_0000_0000_224e))
            ])
        );
        assert!(
            evaluate(
                &inline(vec![row(0x8000_0000u32, Json::from(1))]),
                &mut Vars::default()
            )
            .is_err()
        );
        let mut empty = config;
        empty["m_6525E98B"]["m_graph"] = Json::Null;
        empty["m_6525E98B"]["m_1EB5A024"] = json!([{"m_value":{"$":"ShouldNotEvaluate"}}]);
        assert_eq!(
            evaluate(&empty, &mut Vars::default()).unwrap(),
            Value::Integer(0)
        );
    }

    #[test]
    fn authored_map_growth_reinserts_rows_in_bucket_order() {
        let mut rows: Vec<_> = [15, 31, 1, 2, 3, 4, 5, 6, 8]
            .into_iter()
            .map(|key| {
                json!({
                    "$": "STU_D8196947",
                    "m_identifier": format!("0x0D8000000000{key:04X}"),
                    "m_value": {"$": "STUConfigVarInt", "m_value": key}
                })
            })
            .collect();
        rows.push(json!({
            "$": "STU_D8196947",
            "m_identifier": "0x0D8000000000001F",
            "m_value": null
        }));
        let config = json!({"$": "STU_7042F028", "m_92829780": rows});
        let expected = [15, 1, 2, 3, 4, 5, 6, 8, 31]
            .into_iter()
            .map(|key| {
                (
                    Value::Integer(key),
                    Value::Integer(if key == 31 { 0 } else { key }),
                )
            })
            .collect();
        assert_eq!(
            evaluate(&config, &mut Vars::default()),
            Ok(Value::Map(expected))
        );
        assert!(matches!(
            evaluate_with_budget(&config, &mut Vars::default(), 1),
            Err(EvalError::BudgetExceeded { .. })
        ));
    }

    #[test]
    fn static_queries_preserve_a_world_native_result_before_evaluating_the_operand() {
        struct Native;
        impl Context for Native {
            fn native(&mut self, _config: &Json) -> Option<Value> {
                Some(Value::Null)
            }
        }
        for config in [
            json!({"$":"STU_71A87E92","m_entity":{"$":"unavailable"}}),
            json!({"$":"STU_021D01CD","m_entity":{"$":"unavailable"}}),
            json!({"$":"STU_C8C4C20D","m_entity":{"$":"unavailable"}}),
            json!({"$":"STU_93DCDDFD","m_entity":{"$":"unavailable"}}),
            json!({"$":"STU_B3E67A36","m_filter":null,"m_8B2A4DF3":null,"m_9C7BD8B8":null,"m_B486603E":{"$":"unavailable"}}),
        ] {
            assert_eq!(evaluate(&config, &mut Native), Ok(Value::Null));
        }
    }

    #[test]
    fn entity_team_returns_no_team_for_an_unset_target() {
        let query = json!({"$":"STU_65ABFCCC","m_target":{
            "$":"STUConfigVarDynamic","m_60DB8F99":0,"m_identifier":27
        }});
        let mut world = Vars::default();
        for value in [Value::Null, Value::Entity(0), Value::Array(Vec::new())] {
            world.instance.insert(27, value);
            assert_eq!(evaluate(&query, &mut world), Ok(Value::Integer(-1)));
        }
        world.instance.insert(27, Value::Entity(0xa000_0101));
        assert_eq!(
            evaluate(&query, &mut world),
            Err(EvalError::UnsupportedConfig("STU_65ABFCCC".into()))
        );
    }

    #[test]
    fn transform_heading_matches_reference_axes_and_rejects_unknown_transforms() {
        struct Transform(Option<[f64; 3]>);
        impl Context for Transform {
            fn owner(&mut self) -> Value {
                Value::Entity(17)
            }
            fn forward(&mut self, entity: u32) -> Option<[f64; 3]> {
                assert_eq!(entity, 17);
                self.0
            }
        }
        let query = json!({"$":"STU_C8C4C20D","m_entity":{"$":"STU_B5A0CAF0"}});
        for (forward, degrees) in [
            ([1.0, 0.0, 0.0], 90.0),
            ([-1.0, 0.0, 0.0], -90.0),
            ([0.0, 0.0, 1.0], 0.0),
            ([0.0, 0.0, -1.0], 0.0),
        ] {
            assert_eq!(
                evaluate(&query, &mut Transform(Some(forward))),
                Ok(Value::Float(degrees))
            );
        }
        assert!(evaluate(&query, &mut Transform(None)).is_err());
        assert!(evaluate(&query, &mut Transform(Some([f64::NAN, 0.0, 1.0]))).is_err());
    }

    fn variable(id: u32, scope: Scope) -> Json {
        json!({
            "$": "STUConfigVarDynamic",
            "m_identifier": id,
            "m_60DB8F99": scope == Scope::Entity,
        })
    }

    fn expression(opcodes: Json, dynamics: &[Json], floats: &[f64], configs: &[Json]) -> Json {
        json!({
            "$": "STU_919FD47C",
            "m_configVars": configs,
            "m_expression": {
                "m_opcodes": opcodes,
                "m_dynamicVars": dynamics,
                "m_D99EF254": floats,
            },
        })
    }

    #[test]
    fn shared_body_angle_contract() {
        struct Host {
            result: AngleQuery,
            axis: BodyAngle,
            reads: usize,
        }
        impl Context for Host {
            fn variable(&mut self, _: Variable) -> Value {
                Value::Entity(42)
            }
            fn body_angle(&mut self, entity: u32, axis: BodyAngle) -> AngleQuery {
                assert_eq!(entity, 42);
                assert_eq!(axis, self.axis);
                self.reads += 1;
                self.result
            }
        }
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-body-angle-v1.json"
        ))
        .unwrap();
        let tolerance = fixture["tolerance"].as_f64().unwrap();
        for case in fixture["native_cases"].as_array().unwrap() {
            let class = format!("STU_{}", case["stu"].as_str().unwrap());
            let axis = if class == "STU_CC53F52B" {
                BodyAngle::Yaw
            } else {
                BodyAngle::Pitch
            };
            let result = AngleQuery::from_packed(case["units"].as_i64().unwrap() as i32);
            let mut host = Host {
                result,
                axis,
                reads: 0,
            };
            let config = serde_json::json!({"$":class,"m_EE729DCB":0,
                "m_entity":{"$":"STUConfigVarDynamic","m_identifier":1}});
            let Value::Float(actual) = evaluate(&config, &mut host).unwrap() else {
                panic!("not float");
            };
            assert!(
                (actual - case["actual"].as_f64().unwrap()).abs() <= tolerance,
                "{case}: {actual}"
            );
            assert_eq!(host.reads, 1);
            if axis == BodyAngle::Pitch {
                assert_eq!(
                    (actual as f32).to_bits(),
                    (case["actual"].as_f64().unwrap() as f32).to_bits()
                );
            }
        }
        for (result, expected) in [
            (AngleQuery::Unbound, Some(19.0)),
            (AngleQuery::Unavailable, None),
            (AngleQuery::Present(f64::INFINITY), None),
            (AngleQuery::Present(f64::NAN), None),
        ] {
            let mut host = Host {
                result,
                axis: BodyAngle::Yaw,
                reads: 0,
            };
            let mut config = serde_json::json!({"$":"STU_CC53F52B",
                "m_entity":{"$":"STUConfigVarDynamic","m_identifier":1}});
            let value = evaluate_with_initial(&config, &mut host, Value::Float(19.0));
            if let Some(value_expected) = expected {
                assert_eq!(value.unwrap(), Value::Float(value_expected));
            } else {
                assert!(value.is_err());
            }
            host.reads = 0;
            config["unexpected"] = serde_json::json!(true);
            assert!(evaluate(&config, &mut host).is_err());
            assert_eq!(host.reads, 0);
        }
        assert_eq!(AngleQuery::from_packed(32768), AngleQuery::Unavailable);
        assert_eq!(AngleQuery::from_packed(-32769), AngleQuery::Unavailable);
    }

    #[test]
    fn shared_velocity_contract() {
        fn value(json: &Json) -> Value {
            match json {
                Json::Null => Value::Null,
                Json::Bool(v) => Value::Bool(*v),
                Json::Number(n) => n
                    .as_i64()
                    .map_or_else(|| Value::Float(n.as_f64().unwrap()), Value::Integer),
                Json::Array(items) => Value::Array(items.iter().map(value).collect()),
                Json::Object(fields) if fields.contains_key("entity") => {
                    Value::Entity(json["entity"].as_u64().unwrap().try_into().unwrap())
                }
                Json::Object(fields) if fields.contains_key("vector") => {
                    Value::Vector(std::array::from_fn(|axis| {
                        json["vector"][axis].as_f64().unwrap()
                    }))
                }
                other => panic!("invalid velocity value {other}"),
            }
        }
        fn number(json: &Json) -> f64 {
            json.as_f64()
                .unwrap_or_else(|| match json["float"].as_str().unwrap() {
                    "NaN" => f64::NAN,
                    "Infinity" => f64::INFINITY,
                    other => panic!("invalid number {other}"),
                })
        }
        struct Host {
            target: Value,
            table: Option<std::collections::BTreeMap<u32, Option<[f64; 3]>>>,
        }
        impl Context for Host {
            fn variable(&mut self, var: Variable) -> Value {
                assert_eq!(var.id, 1);
                self.target.clone()
            }
            fn entity_velocity(&mut self, entity: u32) -> VelocityQuery {
                match &self.table {
                    None => VelocityQuery::Unavailable,
                    Some(table) => match table.get(&entity) {
                        None => VelocityQuery::Unbound,
                        Some(None) => VelocityQuery::Unavailable,
                        Some(Some(v)) => VelocityQuery::Present(*v),
                    },
                }
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-velocity-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.mover-velocity-read.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let table = case["table"].as_array().map(|rows| {
                rows.iter()
                    .map(|row| {
                        (
                            row[0].as_u64().unwrap().try_into().unwrap(),
                            row[1]
                                .as_array()
                                .map(|parts| std::array::from_fn(|axis| number(&parts[axis]))),
                        )
                    })
                    .collect()
            });
            let mut host = Host {
                target: value(&case["target"]),
                table,
            };
            let selector = case.get("selector").cloned().unwrap_or_else(
                || json!({"$":"STUConfigVarDynamic","m_identifier":1,"m_60DB8F99":false}),
            );
            let config = json!({"$":"STU_309B0570","m_EE729DCB":0,"m_entity":selector,"m_5B94EDB8":case.get("mover").unwrap_or(&Json::Null)});
            let actual = evaluate_with_initial(&config, &mut host, value(&case["initial"]));
            if let Some(error) = expected.get("error") {
                assert_eq!(
                    actual,
                    Err(EvalError::UnsupportedConfig(error.as_str().unwrap().into())),
                    "{}",
                    case["name"]
                );
            } else {
                assert_eq!(actual, Ok(value(&expected["value"])), "{}", case["name"]);
            }
        }
    }

    #[test]
    fn shared_total_health_contract() {
        fn value(json: &Json) -> Value {
            match json {
                Json::Null => Value::Null,
                Json::Bool(v) => Value::Bool(*v),
                Json::Number(n) => n
                    .as_i64()
                    .map_or_else(|| Value::Float(n.as_f64().unwrap()), Value::Integer),
                Json::Array(items) => Value::Array(items.iter().map(value).collect()),
                Json::Object(fields) if fields.contains_key("bits64") => {
                    Value::Float(f64::from_bits(
                        u64::from_str_radix(json["bits64"].as_str().unwrap(), 16).unwrap(),
                    ))
                }
                Json::Object(fields) if fields.contains_key("entity") => {
                    Value::Entity(json["entity"].as_u64().unwrap().try_into().unwrap())
                }
                Json::Object(fields) if fields.contains_key("vector") => {
                    Value::Vector(std::array::from_fn(|axis| {
                        json["vector"][axis].as_f64().unwrap()
                    }))
                }
                other => panic!("invalid velocity value {other}"),
            }
        }
        fn number(json: &Json) -> f64 {
            if let Some(hex) = json["bits64"].as_str() {
                return f64::from_bits(u64::from_str_radix(hex, 16).unwrap());
            }
            json.as_f64()
                .unwrap_or_else(|| match json["float"].as_str().unwrap() {
                    "NaN" => f64::NAN,
                    "Infinity" => f64::INFINITY,
                    other => panic!("invalid number {other}"),
                })
        }
        struct Host {
            target: Value,
            table: Option<std::collections::BTreeMap<u32, Option<f64>>>,
        }
        impl Context for Host {
            fn variable(&mut self, var: Variable) -> Value {
                assert_eq!(var.id, 1);
                self.target.clone()
            }
            fn total_health(&mut self, entity: u32) -> HealthQuery {
                match &self.table {
                    None => HealthQuery::Unavailable,
                    Some(table) => match table.get(&entity) {
                        None => HealthQuery::Unbound,
                        Some(None) => HealthQuery::Unavailable,
                        Some(Some(v)) => HealthQuery::Present(*v),
                    },
                }
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-health-total-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.total-health-read.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let table = case["table"].as_array().map(|rows| {
                rows.iter()
                    .map(|row| {
                        (
                            row[0].as_u64().unwrap().try_into().unwrap(),
                            (!row[1].is_null()).then(|| number(&row[1])),
                        )
                    })
                    .collect()
            });
            let mut host = Host {
                target: value(&case["target"]),
                table,
            };
            let selector = case.get("selector").cloned().unwrap_or_else(
                || json!({"$":"STUConfigVarDynamic","m_identifier":1,"m_60DB8F99":false}),
            );
            let config = json!({"$":"STU_9B7648C3","m_EE729DCB":0,"m_entity":selector});
            let actual = evaluate_with_initial(&config, &mut host, value(&case["initial"]));
            if let Some(error) = expected.get("error") {
                assert_eq!(
                    actual,
                    Err(EvalError::UnsupportedConfig(error.as_str().unwrap().into())),
                    "{}",
                    case["name"]
                );
            } else {
                assert_eq!(actual, Ok(value(&expected["value"])), "{}", case["name"]);
            }
        }
    }

    #[test]
    fn velocity_selector_is_bounded_and_preserves_explicit_legacy_hosts() {
        struct Legacy;
        impl Context for Legacy {
            fn native(&mut self, _: &Json) -> Option<Value> {
                Some(Value::Vector([9.0, 0.0, 0.0]))
            }
        }
        let mut query =
            json!({"$":"STU_309B0570","m_entity":{"$":"STU_B5A0CAF0"},"m_5B94EDB8":null});
        assert_eq!(
            evaluate(&query, &mut Legacy),
            Ok(Value::Vector([9.0, 0.0, 0.0]))
        );
        query["m_unknown"] = json!(true);
        assert_eq!(
            evaluate(&query, &mut EmptyContext),
            Err(EvalError::UnsupportedConfig("STU_309B0570".into()))
        );
        query.as_object_mut().unwrap().remove("m_unknown");
        for _ in 0..MAX_CONFIG_DEPTH {
            query["m_entity"] =
                json!({"$":"STU_06610EBC","m_configVars":[query["m_entity"].clone()]});
        }
        assert_eq!(
            evaluate_with_budget(&query, &mut Legacy, 1),
            Err(EvalError::BudgetExceeded {
                offset: 0,
                limit: 1
            })
        );
        assert_eq!(
            evaluate(&query, &mut Legacy),
            Err(EvalError::NestingExceeded)
        );
    }

    #[test]
    fn shared_engine_setting_contract() {
        fn value(json: &Json) -> Value {
            match json {
                Json::Null => Value::Null,
                Json::Bool(value) => Value::Bool(*value),
                Json::Number(number) => number
                    .as_i64()
                    .map_or_else(|| Value::Float(number.as_f64().unwrap()), Value::Integer),
                Json::Array(items) => Value::Array(items.iter().map(value).collect()),
                other => panic!("unsupported engine contract value {other}"),
            }
        }
        struct Settings(Option<std::collections::BTreeMap<u32, Value>>);
        impl Context for Settings {
            fn engine_setting(&mut self, key: u32) -> EngineSetting {
                match &self.0 {
                    None => EngineSetting::Unavailable,
                    Some(table) => table.get(&key).map_or(EngineSetting::Missing, |value| {
                        EngineSetting::Present(value.clone())
                    }),
                }
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-engine-setting-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.engine-setting-read.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let mut context = Settings(case["settings"].as_array().map(|rows| {
                rows.iter()
                    .map(|row| (row[0].as_u64().unwrap().try_into().unwrap(), value(&row[1])))
                    .collect()
            }));
            let config = json!({"$":"STU_A753DC79","m_EE729DCB":0,"m_74A2248F":case["hash"]});
            let actual = evaluate_with_initial(&config, &mut context, value(&case["initial"]));
            if let Some(class) = expected.get("error") {
                assert_eq!(
                    actual,
                    Err(EvalError::UnsupportedConfig(class.as_str().unwrap().into())),
                    "{}",
                    case["name"]
                );
            } else {
                assert_eq!(actual, Ok(value(&expected["value"])), "{}", case["name"]);
            }
        }
    }

    #[test]
    fn engine_setting_rejects_unknown_shape_and_keeps_an_explicit_legacy_override() {
        let mut config = json!({"$":"STU_A753DC79","m_EE729DCB":0,"m_74A2248F":1});
        struct Legacy;
        impl Context for Legacy {
            fn native(&mut self, _: &Json) -> Option<Value> {
                Some(Value::Bool(false))
            }
        }
        assert_eq!(
            evaluate_with_initial(&config, &mut Legacy, Value::Integer(73)),
            Ok(Value::Bool(false))
        );
        config["m_unknown"] = json!(1);
        assert_eq!(
            evaluate(&config, &mut EmptyContext),
            Err(EvalError::UnsupportedConfig("STU_A753DC79".into()))
        );
    }

    #[test]
    fn empty_resource_getter_preserves_initial_and_explicit_host_input() {
        let config = json!({"$":"STU_7BD2CA19","m_EE729DCB":0,"m_resourceKey":null});
        assert_eq!(
            evaluate_with_initial(&config, &mut EmptyContext, Value::Integer(-1)),
            Ok(Value::Integer(-1))
        );
        struct Admitted;
        impl Context for Admitted {
            fn native(&mut self, _: &Json) -> Option<Value> {
                Some(Value::Asset(0x0d80_0000_0000_195e))
            }
        }
        assert_eq!(
            evaluate_with_initial(&config, &mut Admitted, Value::Integer(-1)),
            Ok(Value::Asset(0x0d80_0000_0000_195e))
        );
    }

    #[test]
    fn on_fire_without_stats_retains_the_callers_initial_value() {
        struct Stats(Option<OnFire>);
        impl Context for Stats {
            fn owner(&mut self) -> Value {
                Value::Entity(42)
            }
            fn body_on_fire(&mut self, entity: u32) -> Option<OnFire> {
                assert_eq!(entity, 42);
                self.0
            }
        }
        let query = json!({
            "$":"STU_1ED4F039","m_EE729DCB":0,
            "m_entity":{"$":"STU_B5A0CAF0","m_EE729DCB":0},
        });
        let mut missing = Stats(Some(OnFire::NoStats));
        assert_eq!(evaluate(&query, &mut missing), Ok(Value::Null));
        assert_eq!(
            evaluate_with_initial(&query, &mut missing, Value::Integer(0)),
            Ok(Value::Integer(0))
        );
        let default = json!({"$":"STU_91EFD5B1","m_value":query,"m_default":true});
        assert_eq!(evaluate(&default, &mut missing), Ok(Value::Bool(true)));
        for active in [false, true] {
            assert_eq!(
                evaluate_with_initial(
                    &query,
                    &mut Stats(Some(OnFire::Active(active))),
                    Value::Integer(0)
                ),
                Ok(Value::Bool(active))
            );
        }
        assert_eq!(
            evaluate(&query, &mut Stats(None)),
            Err(EvalError::UnsupportedConfig("STU_1ED4F039".into()))
        );
    }

    #[derive(Default)]
    struct Natives {
        reported: Vec<String>,
        controller: u32,
        component92: Option<bool>,
    }

    impl Context for Natives {
        fn owner(&mut self) -> Value {
            Value::Entity(0xa000_0101)
        }

        fn related_entity(&mut self, relation: EntityRelation, entity: u32) -> Option<u32> {
            (relation == EntityRelation::AiControlEntity && entity == 0xa000_0101)
                .then_some(self.controller)
        }

        fn entity_component92(&mut self, entity: u32) -> Option<bool> {
            (entity == self.controller)
                .then_some(self.component92)
                .flatten()
        }

        fn watches_natives(&self) -> bool {
            true
        }

        fn reference_native(&mut self, class: &str) {
            self.reported.push(class.to_owned());
        }
    }

    #[test]
    fn entity_member_bytecode_distinguishes_unset_unknown_and_invalid_entities() {
        let mut context = Vars::default();
        for opcode in [15, 16] {
            for (target, key, expected) in [
                (Value::Entity(0xa000_0100), 7423, Ok(Value::Integer(23))),
                (Value::Entity(0xa000_0100), 7424, Ok(Value::Null)),
                (
                    Value::Entity(0xa000_0101),
                    7423,
                    Err(EvalError::UnavailableEntityVariable {
                        entity: 0xa000_0101,
                        id: 7423,
                    }),
                ),
                (Value::Entity(0), 7423, Ok(Value::Null)),
                (Value::Integer(1), 7423, Ok(Value::Null)),
            ] {
                context.instance.insert(1, target);
                let config = expression(
                    json!([8, 0, 7, 1, opcode, 0]),
                    &[variable(1, Scope::Instance), variable(key, Scope::Entity)],
                    &[],
                    &[],
                );
                assert_eq!(evaluate(&config, &mut context), expected);
            }
        }
    }

    #[test]
    fn position_bytecode_rejects_unknown_entities_without_changing_nonentity_coercion() {
        struct Positions([Value; 2]);
        impl Context for Positions {
            fn variable(&mut self, variable: Variable) -> Value {
                self.0[variable.id as usize].clone()
            }

            fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
                match entity {
                    0xa000_0100 => Some([0.0; 3]),
                    0xa000_0101 => Some([3.0, 4.0, 0.0]),
                    _ => None,
                }
            }
        }
        let dynamics = [variable(0, Scope::Instance), variable(1, Scope::Instance)];
        for opcode in [50, 51] {
            let config = expression(json!([8, 0, opcode, 0]), &dynamics, &[], &[]);
            for (value, expected) in [
                (Value::Entity(0xa000_0100), Ok(Value::Float(0.0))),
                (Value::Entity(0xa000_0101), Ok(Value::Float(5.0))),
                (Value::Float(-7.0), Ok(Value::Float(7.0))),
                (
                    Value::Entity(0),
                    Err(EvalError::UnavailableEntityPosition(0)),
                ),
                (
                    Value::Entity(0xa000_0102),
                    Err(EvalError::UnavailableEntityPosition(0xa000_0102)),
                ),
            ] {
                assert_eq!(
                    evaluate(&config, &mut Positions([value, Value::Null])),
                    expected,
                );
            }
        }
        for opcode in [23, 24, 53, 54] {
            let config = expression(json!([8, 0, 8, 1, opcode, 0]), &dynamics, &[], &[]);
            for unknown in [0, 0xa000_0102] {
                for values in [
                    [Value::Entity(unknown), Value::Entity(0xa000_0100)],
                    [Value::Vector([0.0; 3]), Value::Entity(unknown)],
                ] {
                    assert_eq!(
                        evaluate(&config, &mut Positions(values)),
                        Err(EvalError::UnavailableEntityPosition(unknown)),
                    );
                }
            }
            let expected = match opcode {
                23 => Value::Vector([3.0, 4.0, 0.0]),
                24 => Value::Vector([-3.0, -4.0, 0.0]),
                53 => Value::Float(5.0),
                54 => Value::Float(3.0),
                _ => unreachable!(),
            };
            assert_eq!(
                evaluate(
                    &config,
                    &mut Positions([Value::Entity(0xa000_0100), Value::Entity(0xa000_0101)])
                ),
                Ok(expected),
            );
            // Existing scalar arithmetic and nonentity distance coercions are
            // distinct from a requested entity position being unavailable.
            let expected = match opcode {
                23 => 5.0,
                24 => -1.0,
                _ => 0.0,
            };
            assert_eq!(
                evaluate(
                    &config,
                    &mut Positions([Value::Integer(2), Value::Integer(3)])
                ),
                Ok(Value::Float(expected)),
            );
        }
    }

    #[test]
    fn reference_natives_are_reported_by_their_outermost_class() {
        let report = |config: Json| {
            let mut context = Natives::default();
            let value = evaluate(&config, &mut context).unwrap();
            (value, context.reported)
        };
        // The reference computes value-or-default itself and answers the
        // operand through its world.
        assert_eq!(
            report(json!({
                "$":"STU_91EFD5B1",
                "m_value":{"$":"STU_E95A9C77","m_EE729DCB":0},
                "m_default":{"$":"STUConfigVarBool","m_value":true},
            })),
            (Value::Bool(false), vec!["STU_E95A9C77".to_owned()])
        );
        // Its world never watches a native's operands.
        assert_eq!(
            report(json!({
                "$":"STU_D3EB8D87", "m_EE729DCB":0,
                "m_00903303":{
                    "$":"STU_DB742151", "m_EE729DCB":0,
                    "m_entity":{"$":"STU_B5A0CAF0","m_EE729DCB":0},
                },
            })),
            (Value::Bool(false), vec!["STU_D3EB8D87".to_owned()])
        );
        let query = json!({
            "$":"STU_D3EB8D87",
            "m_00903303":{"$":"STU_DB742151","m_entity":{"$":"STU_B5A0CAF0"}},
        });
        for component92 in [Some(true), Some(false), None] {
            let mut world = Natives {
                controller: 0xc000_0102,
                component92,
                ..Natives::default()
            };
            assert_eq!(
                evaluate(&query, &mut world),
                component92
                    .map(Value::Bool)
                    .ok_or_else(|| EvalError::UnsupportedConfig("STU_D3EB8D87".into()))
            );
        }
        // Resource configs with a GUID are computed; a fieldless one is not.
        assert_eq!(
            report(json!({
                "$":"STUConfigVarIdentifier", "m_identifier":"0x0D80000000000015",
                "m_resourceKey":null,
            })),
            (Value::Asset(0x0d80_0000_0000_0015), Vec::new())
        );
        assert_eq!(
            report(json!({"$":"STU_ABB6C23E","m_EE729DCB":0,"m_resourceKey":null})),
            (Value::Asset(0), vec!["STU_ABB6C23E".to_owned()])
        );
        assert_eq!(
            report(variable(7, Scope::Entity)),
            (Value::Null, Vec::new())
        );
        for (class, field) in [
            ("STUConfigVarTexture", "m_texture"),
            ("STU_08305297", "m_C21DB248"),
        ] {
            assert_eq!(
                report(json!({"$":class,field:null,"m_resourceKey":null})),
                (Value::Asset(0), Vec::new())
            );
        }
    }

    #[test]
    fn enemy_hero_and_health_queries_preserve_known_and_unknown_entities() {
        struct EnemyFacts;
        impl Context for EnemyFacts {
            fn owner(&mut self) -> Value {
                Value::Entity(0xc000_1000)
            }
            fn entity_hero(&mut self, entity: u32) -> Option<u64> {
                (entity == 0xc000_1000).then_some(0x02e0_0000_0000_0175)
            }
            fn entity_definition(&mut self, entity: u32) -> Option<u64> {
                (entity == 0xc000_1000).then_some(0x0400_0000_0000_171c)
            }
            fn body_health(&mut self, entity: u32, _: Option<PoolKind>, _: bool) -> Option<f64> {
                (entity == 0xc000_1000).then_some(123.4)
            }
        }
        let hero = json!({"$":"STU_93DCDDFD","m_entity":{"$":"STU_B5A0CAF0"},"m_resourceKey":null});
        assert_eq!(
            evaluate(&hero, &mut EnemyFacts),
            Ok(Value::Asset(0x02e0_0000_0000_0175))
        );
        assert!(evaluate(&hero, &mut EmptyContext).is_err());
        let definition = json!({"$":"STU_1A3FA5B0","m_entity":{"$":"STU_B5A0CAF0"}});
        assert_eq!(
            evaluate(&definition, &mut EnemyFacts),
            Ok(Value::Asset(0x0400_0000_0000_171c))
        );
        assert_eq!(
            evaluate(&definition, &mut EmptyContext),
            Ok(Value::Asset(0))
        );
        struct UnknownBody;
        impl Context for UnknownBody {
            fn owner(&mut self) -> Value {
                Value::Entity(0xc000_1000)
            }
        }
        assert!(evaluate(&definition, &mut UnknownBody).is_err());
        let maximum = json!({"$":"STU_60146072","m_entity":{"$":"STU_B5A0CAF0"}});
        assert_eq!(evaluate(&maximum, &mut EnemyFacts), Ok(Value::Float(123.4)));
        assert_eq!(evaluate(&maximum, &mut EmptyContext), Ok(Value::Float(0.0)));
        assert_eq!(
            evaluate(
                &json!({"$":"STU_8556841E","m_entityDef":"0x040000000000171C","m_resourceKey":null}),
                &mut EmptyContext
            ),
            Ok(Value::Asset(0x0400_0000_0000_171c))
        );
        let mut keyed = hero;
        keyed["m_resourceKey"] = json!("0x0A90000000000001");
        assert!(evaluate(&keyed, &mut EnemyFacts).is_err());
        assert_eq!(
            evaluate(
                &json!({"$":"STU_9B7648C3","m_entity":{"$":"STU_B5A0CAF0"}}),
                &mut EnemyFacts
            ),
            Ok(Value::Float(f64::from(123.4f32)))
        );
        // The failed base evaluator never evaluates its unimplemented operand.
        assert_eq!(
            evaluate(
                &json!({"$":"STU_B40ACFBA","m_52EA7AB9":{"$":"UnsupportedFixtureOperand"}}),
                &mut EmptyContext
            ),
            Ok(Value::Null)
        );
    }

    #[test]
    fn authored_message_map_looks_up_the_first_matching_key() {
        use crate::{
            bits::BitWriter,
            script_wire::{ValueEncoding, WireError, write_value},
        };

        let row = |key: &str, message: &str| {
            json!({
                "$":"STU_98DD1872", "m_EE729DCB":0,
                "m_key":{"$":"STU_B70476F3","m_548E5344":key,"m_resourceKey":null},
                "m_value":{"$":"STUConfigVarGameMessage","m_gameMessage":message,"m_resourceKey":null},
            })
        };
        let mut literal = json!({
            "$":"STU_7757D403", "m_EE729DCB":0,
            "m_configVars":[
                row("0x0A90000000000042", "0x0240000000000003"),
                row("0x0A90000000000042", "0x0240000000000004"),
            ],
        });
        let mut context = Vars::default();
        let map = evaluate(&literal, &mut context).unwrap();
        assert_eq!(
            evaluate(&literal["m_configVars"][0]["m_key"], &mut context),
            Ok(Value::Asset(0x0a90_0000_0000_0042))
        );
        assert!(truthy(&map));
        context.instance.insert(1, map.clone());
        let lookup = expression(
            json!([8, 0, 8, 1, 17, 0]),
            &[variable(1, Scope::Instance), variable(2, Scope::Instance)],
            &[],
            &[],
        );
        for (key, result) in [
            (0x0a90_0000_0000_0042, Value::Asset(0x0240_0000_0000_0003)),
            (0x0a90_0000_0000_0043, Value::Null),
        ] {
            context.instance.insert(2, Value::Asset(key));
            assert_eq!(evaluate(&lookup, &mut context), Ok(result));
        }
        literal["m_configVars"][0]["m_key"]["m_548E5344"] = json!("0x0240000000000042");
        assert_eq!(
            evaluate(&literal["m_configVars"][0]["m_key"], &mut context),
            Err(EvalError::UnsupportedConfig("STU_B70476F3".into()))
        );
        assert_eq!(
            evaluate(&literal, &mut context),
            Err(EvalError::UnsupportedConfig("STU_7757D403".into()))
        );
        literal["m_configVars"] = Json::Null;
        let empty = evaluate(&literal, &mut context).unwrap();
        assert_eq!(empty, Value::Map(Vec::new()));
        assert!(!truthy(&empty));
        let mut bits = BitWriter::new();
        assert_eq!(
            write_value(&mut bits, &map, ValueEncoding::Explicit),
            Err(WireError::InvalidValue)
        );
        assert_eq!(bits.bit_len(), 0);
    }

    #[test]
    fn fieldless_native_resource_evaluates_to_a_zero_asset() {
        let literal = json!({"$":"STU_ABB6C23E", "m_EE729DCB":0, "m_resourceKey":null});
        let mut context = Vars::default();
        assert_eq!(evaluate(&literal, &mut context), Ok(Value::Asset(0)));

        for (field, value) in [
            ("m_resourceKey", json!("0x0D80000000000015")),
            ("m_EE729DCB", json!(1)),
            ("m_unknown", json!("0x0D80000000000015")),
        ] {
            let mut config = literal.clone();
            config[field] = value;
            assert_eq!(
                evaluate(&config, &mut context),
                Err(EvalError::UnsupportedConfig("STU_ABB6C23E".into()))
            );
        }
    }

    const FILTER_OWNER: u32 = 0xa000_0101;

    #[test]
    fn shared_ally_filter_contract() {
        struct Host<'a>(&'a Json);
        impl Host<'_> {
            fn body(&self, entity: u32) -> Option<&Json> {
                self.0["bodies"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|body| body["entity"] == entity)
            }
        }
        impl Context for Host<'_> {
            fn owner(&mut self) -> Value {
                Value::Entity(123)
            }
            fn variable(&mut self, var: Variable) -> Value {
                assert_eq!(var.id, 2);
                Value::Entity(self.0["target"].as_u64().unwrap().try_into().unwrap())
            }
            fn parameter(&mut self, id: u32) -> Value {
                match id {
                    8 => Value::Entity(999),
                    9 => Value::Entity(self.0["parameter9"].as_u64().unwrap().try_into().unwrap()),
                    10 => Value::Entity(124),
                    _ => Value::Null,
                }
            }
            fn scene_filter_policy(&mut self, filter: &Json) -> Option<SceneFilterPolicy> {
                (filter["m_66747E7D"] == 1 && filter["m_89831602"] == 4).then_some(
                    SceneFilterPolicy {
                        relation: FilterRelation::FriendlyOther,
                        predicate: if child(filter, "m_condition").is_null() {
                            FilterPredicate::None
                        } else {
                            FilterPredicate::Target
                        },
                    },
                )
            }
            fn combat_targets(&mut self, _config: &Json) -> Option<Vec<u32>> {
                Some(
                    std::iter::once(123)
                        .chain(
                            self.0["bodies"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .map(|body| body["entity"].as_u64().unwrap().try_into().unwrap()),
                        )
                        .collect(),
                )
            }
            fn entity_filter_flags(&mut self, entity: u32) -> Option<u64> {
                if entity == 123 {
                    self.0["source_flags"].as_u64()
                } else {
                    self.body(entity)?["flags"].as_u64()
                }
            }
            fn has_target_tag(&mut self, entity: u32, tag: u64) -> Option<bool> {
                if entity == 123 {
                    return Some(false);
                }
                self.body(entity)?["tags"].as_array().map(|tags| {
                    tags.iter()
                        .any(|entry| entry.as_u64().unwrap() + 0x0d60_0000_0000_0000 == tag)
                })
            }
            fn known_position(&mut self, entity: u32) -> Option<[f64; 3]> {
                if entity == 123 {
                    return Some([0.0; 3]);
                }
                let position = self.body(entity)?["position"].as_array()?;
                Some(std::array::from_fn(|axis| position[axis].as_f64().unwrap()))
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-ally-filter-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.ally-filter.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let results = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), results.len());
        for (case, expected) in cases.iter().zip(results) {
            assert_eq!(case["name"], expected["name"]);
            let first = case["kind"] == "first";
            let query = if first {
                json!({"$":"STU_B3E67A36","m_filter":case["filter"]})
            } else {
                json!({"$":"STU_AC829876","m_filter":case["filter"],"m_entity":{
                    "$":"STUConfigVarDynamic","m_identifier":2,"m_60DB8F99":false}})
            };
            let mut host = Host(case);
            let mut evaluator = Evaluator {
                limit: MAX_EXPRESSION_STEPS,
                steps: 0,
                folds: Vec::new(),
                scoped_parameters: Vec::new(),
                in_native: false,
            };
            let actual = evaluator.config(&query, &mut host, 0);
            let expected = if let Some(error) = expected["error"].as_str() {
                Err(EvalError::UnsupportedConfig(error.into()))
            } else if first {
                Ok(Value::Entity(
                    expected["value"].as_u64().unwrap().try_into().unwrap(),
                ))
            } else {
                Ok(Value::Bool(expected["value"].as_bool().unwrap()))
            };
            assert_eq!(actual, expected, "{}", case["name"]);
            assert!(evaluator.scoped_parameters.is_empty(), "{}", case["name"]);
            assert_eq!(evaluator.parameter(&mut host, 8), Value::Entity(999));
        }
    }

    #[test]
    fn shared_combat_target_contract() {
        struct Host<'a>(&'a Json);
        impl Context for Host<'_> {
            fn combat_targets(&mut self, _config: &Json) -> Option<Vec<u32>> {
                self.0["roster_available"].as_bool().unwrap().then(|| {
                    self.0["bodies"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|body| body["entity"].as_u64().unwrap().try_into().unwrap())
                        .collect()
                })
            }
            fn entity_filter_flags(&mut self, entity: u32) -> Option<u64> {
                self.0["bodies"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|body| body["entity"] == entity)?["flags"]
                    .as_u64()
            }
            fn has_target_tag(&mut self, entity: u32, tag: u64) -> Option<bool> {
                self.0["bodies"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|body| body["entity"] == entity)?["tags"]
                    .as_array()
                    .map(|tags| {
                        tags.iter()
                            .any(|entry| entry.as_u64().unwrap() + 0x0d60_0000_0000_0000 == tag)
                    })
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-combat-target-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.combat-target.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        let query = json!({"$":"STU_B3E67A36","m_9C7BD8B8":tag_config(0x0d60_0000_0000_0001),
            "m_filter":{"$":"STUConfigVarFilter","m_84065FD1":1,"m_4EA686DB":16}});
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let actual = evaluate(&query, &mut Host(case));
            let expected = if expected.get("error").is_some() {
                Err(EvalError::UnsupportedConfig("STU_B3E67A36".into()))
            } else {
                Ok(Value::Entity(
                    expected["entity"].as_u64().unwrap().try_into().unwrap(),
                ))
            };
            assert_eq!(actual, expected, "{}", case["name"]);
        }
        let mut evaluator = Evaluator {
            limit: 1,
            steps: 0,
            folds: Vec::new(),
            scoped_parameters: Vec::new(),
            in_native: false,
        };
        let mut host = Host(&cases[5]);
        assert!(matches!(
            evaluator.config(&query, &mut host, 0),
            Err(EvalError::BudgetExceeded { .. })
        ));
    }

    #[test]
    fn shared_hostile_filter_contract() {
        struct Host<'a>(&'a Json);
        impl Context for Host<'_> {
            fn owner(&mut self) -> Value {
                Value::Entity(123)
            }
            fn variable(&mut self, var: Variable) -> Value {
                match var.id {
                    1 => self.0["predicate"]
                        .as_bool()
                        .map_or(Value::Null, Value::Bool),
                    2 => Value::Entity(124),
                    other => panic!("unexpected variable {other}"),
                }
            }
            fn scene_filter_policy(&mut self, filter: &Json) -> Option<SceneFilterPolicy> {
                Some(SceneFilterPolicy {
                    relation: FilterRelation::Hostile,
                    predicate: if child(filter, "m_condition").is_null() {
                        FilterPredicate::None
                    } else {
                        FilterPredicate::Scalar
                    },
                })
            }
            fn entity_filter_flags(&mut self, entity: u32) -> Option<u64> {
                match entity {
                    123 => self.0["source_flags"].as_u64(),
                    124 => self.0["target_flags"].as_u64(),
                    _ => None,
                }
            }
            fn has_target_tag(&mut self, entity: u32, tag: u64) -> Option<bool> {
                assert_eq!(entity, 124);
                self.0["tags"].as_array().map(|tags| {
                    tags.iter()
                        .any(|entry| entry.as_u64().unwrap() + 0x0d60_0000_0000_0000 == tag)
                })
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-hostile-filter-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.hostile-filter.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let query = json!({"$":"STU_AC829876","m_entity":{"$":"STUConfigVarDynamic","m_identifier":2,"m_60DB8F99":false},"m_filter":case["filter"]});
            let actual = evaluate(&query, &mut Host(case));
            let expected = if expected.get("error").is_some() {
                Err(EvalError::UnsupportedConfig("STU_AC829876".into()))
            } else {
                Ok(Value::Bool(expected["value"].as_bool().unwrap()))
            };
            assert_eq!(actual, expected, "{}", case["name"]);
        }
    }
    const REQUIRED_TAG: u64 = 0x0d60_0000_0000_0069;
    const SECOND_TAG: u64 = 0x0d60_0000_0000_0042;
    const EXCLUDED_TAG: u64 = 0x0d60_0000_0000_0041;

    #[derive(Default)]
    struct FilterWorld {
        entity: Option<u32>,
        flags: Option<u64>,
        tags: BTreeMap<u64, bool>,
    }

    impl Context for FilterWorld {
        fn owner(&mut self) -> Value {
            self.entity.map_or(Value::Null, Value::Entity)
        }

        fn entity_filter_flags(&mut self, entity: u32) -> Option<u64> {
            assert_eq!(entity, FILTER_OWNER);
            self.flags
        }

        fn has_target_tag(&mut self, entity: u32, tag: u64) -> Option<bool> {
            assert_eq!(entity, FILTER_OWNER);
            self.tags.get(&tag).copied()
        }
    }

    fn tag_config(tag: u64) -> Json {
        json!({"$":"STU_357DABBB", "m_7D7C86A1":format!("0x{tag:016X}"), "m_resourceKey":null})
    }

    fn tag_list(tags: &[u64]) -> Json {
        json!({
            "$":"STU_06610EBC",
            "m_configVars": tags.iter().copied().map(tag_config).collect::<Vec<_>>(),
        })
    }

    fn entity_filter(exclusion: u64) -> Json {
        json!({
            "$":"STU_AC829876",
            "m_entity":{"$":"STU_B5A0CAF0"},
            "m_filter":{
                "$":"STUConfigVarFilter",
                "m_84065FD1":0x3fff_ffff,
                "m_4EA686DB":exclusion,
            },
        })
    }

    #[test]
    fn entity_filter_distinguishes_rejection_from_unavailable_facts() {
        let mut config = entity_filter(2);
        config["m_filter"]["m_9C7BD8B8"] =
            json!({"$":"STU_357DABBB", "m_7D7C86A1":null, "m_resourceKey":null});
        config["m_filter"]["m_B486603E"] = tag_config(REQUIRED_TAG);
        let mut world = FilterWorld {
            entity: Some(FILTER_OWNER),
            ..Default::default()
        };
        let unknown = Err(EvalError::UnsupportedConfig("STU_AC829876".into()));
        assert_eq!(evaluate(&config, &mut world), unknown);
        world.tags.insert(REQUIRED_TAG, false);
        assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(false)));
        world.tags.insert(REQUIRED_TAG, true);
        assert_eq!(evaluate(&config, &mut world), unknown);
        world.flags = Some(0x100);
        assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(true)));
        world.flags = Some(2 | 0x100);
        world.tags.clear();
        assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(false)));
        config["m_filter"]["m_66747E7D"] = json!(2);
        assert_eq!(evaluate(&config, &mut world), unknown);

        let mut config = entity_filter(0x10);
        config["m_filter"]["m_84065FD1"] = json!(1);
        config["m_filter"]["m_1E14CCA8"] = json!(0x100);
        world.flags = None;
        assert_eq!(evaluate(&config, &mut world), unknown);
        for (flags, accepted) in [(0x101, true), (0x100, false), (1, false), (0x111, false)] {
            world.flags = Some(flags);
            assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(accepted)));
        }
        let mut config = entity_filter(0);
        config["m_filter"]["m_84065FD1"] = json!(0);
        assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(true)));
    }

    #[test]
    fn shared_absent_target_filter_contract() {
        struct Host {
            entity: Value,
            exists: Option<bool>,
        }
        impl Context for Host {
            fn variable(&mut self, _: Variable) -> Value {
                self.entity.clone()
            }
            fn entity_exists(&mut self, _: u32) -> Option<bool> {
                self.exists
            }
            fn entity_filter_flags(&mut self, _: u32) -> Option<u64> {
                panic!("absent target must not query flags")
            }
        }
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/absent-target-filter-v1.json"
        ))
        .unwrap();
        let mut config = json!({"$":"STU_AC829876", "m_filter":fixture["filter"],
            "m_entity":{"$":"STUConfigVarDynamic","m_identifier":393,"m_60DB8F99":0}});
        for case in fixture["cases"].as_array().unwrap() {
            let mut host = Host {
                entity: case["selector"]
                    .as_u64()
                    .map_or(Value::Null, |id| Value::Entity(id as u32)),
                exists: case["known_exists"].as_bool(),
            };
            if case["selector"] == "absent" {
                config["m_entity"] = Json::Null;
            } else {
                config["m_entity"] =
                    json!({"$":"STUConfigVarDynamic","m_identifier":393,"m_60DB8F99":0});
            }
            assert_eq!(
                evaluate(&config, &mut host),
                Ok(Value::Bool(case["result"].as_bool().unwrap()))
            );
        }
        let unsupported = Err(EvalError::UnsupportedConfig("STU_AC829876".into()));
        let mut null = Host {
            entity: Value::Null,
            exists: None,
        };
        for (key, value) in [
            ("unknown", json!(0)),
            ("m_84065FD1", json!("malformed")),
            ("m_EE729DCB", json!(1)),
        ] {
            let mut malformed = config.clone();
            malformed["m_filter"][key] = value;
            assert_eq!(evaluate(&malformed, &mut null), unsupported);
        }
        for exists in [None, Some(true)] {
            let mut live = Host {
                entity: Value::Entity(77),
                exists,
            };
            assert_eq!(evaluate(&config, &mut live), unsupported);
        }
    }

    #[test]
    fn entity_filter_applies_any_all_and_excluded_tag_lists() {
        let mut config = entity_filter(0);
        config["m_filter"]["m_9C7BD8B8"] = tag_list(&[SECOND_TAG, REQUIRED_TAG]);
        config["m_filter"]["m_B486603E"] = tag_list(&[REQUIRED_TAG]);
        config["m_filter"]["m_8B2A4DF3"] = tag_config(EXCLUDED_TAG);
        let mut world = FilterWorld {
            entity: Some(FILTER_OWNER),
            tags: BTreeMap::from([(REQUIRED_TAG, true), (EXCLUDED_TAG, false)]),
            ..Default::default()
        };
        assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(true)));
        world.tags.insert(REQUIRED_TAG, false);
        assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(false)));
        world.tags.insert(REQUIRED_TAG, true);
        world.tags.insert(EXCLUDED_TAG, true);
        assert_eq!(evaluate(&config, &mut world), Ok(Value::Bool(false)));
        world.tags.remove(&EXCLUDED_TAG);
        let unsupported = Err(EvalError::UnsupportedConfig("STU_AC829876".into()));
        assert_eq!(evaluate(&config, &mut world), unsupported);
        world.tags.insert(EXCLUDED_TAG, false);
        config["m_filter"]["m_9C7BD8B8"]["m_configVars"] =
            json!([tag_config(REQUIRED_TAG), {"$":"unresolved-tag-config"}]);
        assert_eq!(evaluate(&config, &mut world), unsupported,);
    }

    #[test]
    fn shared_flashbang_filter_admission_contract() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-filter-admission-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["case_id"], "flashbang.filter-admission.v1");
        assert_eq!(fixture["cases"].as_array().unwrap().len(), 64);
        let excluded: Vec<_> = fixture["excluded_tags"]
            .as_array()
            .unwrap()
            .iter()
            .map(|tag| 0x0d60_0000_0000_0000 | tag.as_u64().unwrap())
            .collect();
        let mut config = entity_filter(fixture["exclude"].as_u64().unwrap());
        config["m_filter"]["m_84065FD1"] = fixture["any"].clone();
        config["m_filter"]["m_1E14CCA8"] = fixture["all"].clone();
        config["m_filter"]["m_8B2A4DF3"] = tag_list(&excluded);
        for case in fixture["cases"].as_array().unwrap() {
            let inventory: std::collections::BTreeSet<_> = case["inventory"]
                .as_array()
                .unwrap()
                .iter()
                .map(|tag| 0x0d60_0000_0000_0000 | tag.as_u64().unwrap())
                .collect();
            let mut world = FilterWorld {
                entity: Some(FILTER_OWNER),
                flags: case["flags"].as_u64(),
                tags: excluded
                    .iter()
                    .map(|tag| (*tag, inventory.contains(tag)))
                    .collect(),
            };
            assert_eq!(
                evaluate(&config, &mut world),
                Ok(Value::Bool(case["accepted"].as_bool().unwrap())),
                "{}",
                case["name"]
            );
        }
    }

    #[test]
    fn shared_native_filter_tag_clauses() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-filter-tag-clauses-v1.json"
        ))
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let mut config = entity_filter(0);
            let mut world = FilterWorld {
                entity: Some(FILTER_OWNER),
                flags: Some(0x40),
                ..Default::default()
            };
            for tag in case["inventory"].as_array().unwrap() {
                world
                    .tags
                    .insert(0x0d60_0000_0000_0000 + tag.as_u64().unwrap(), true);
            }
            for (field, key) in [
                ("m_9C7BD8B8", "any"),
                ("m_B486603E", "all"),
                ("m_8B2A4DF3", "excluded"),
            ] {
                let tags: Vec<_> = case[key]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|tag| 0x0d60_0000_0000_0000 + tag.as_u64().unwrap())
                    .collect();
                for &tag in &tags {
                    world.tags.entry(tag).or_insert(false);
                }
                config["m_filter"][field] = tag_list(&tags);
            }
            assert_eq!(
                evaluate(&config, &mut world),
                Ok(Value::Bool(case["accepted"].as_bool().unwrap())),
                "{}",
                case["name"]
            );
        }
        let mut config = entity_filter(0);
        config["m_filter"]["m_C1903771"] = tag_config(EXCLUDED_TAG);
        let mut world = FilterWorld {
            entity: Some(FILTER_OWNER),
            ..Default::default()
        };
        assert_eq!(
            evaluate(&config, &mut world),
            Err(EvalError::UnsupportedConfig("STU_AC829876".into()))
        );
    }

    #[test]
    fn direct_target_tag_query_reads_membership_on_the_typed_entity() {
        let query = json!({
            "$":"STU_25ECB55F", "m_88B0050A":"0x0D60000000000069", "m_entity":{"$":"STU_B5A0CAF0"}
        });
        let mut world = FilterWorld {
            entity: Some(FILTER_OWNER),
            ..Default::default()
        };
        assert_eq!(
            evaluate(&query, &mut world),
            Err(EvalError::UnsupportedConfig("STU_25ECB55F".into())),
        );
        for present in [true, false] {
            world.tags.insert(REQUIRED_TAG, present);
            assert_eq!(evaluate(&query, &mut world), Ok(Value::Bool(present)));
        }
        world.entity = None;
        assert_eq!(evaluate(&query, &mut world), Ok(Value::Bool(false)));
    }

    #[test]
    fn distinguishes_identifiers_arrays_entities_and_float_comparisons() {
        let id = Value::Asset(5);
        assert!(!equal(&id, &Value::Integer(5)));
        assert!(equal(&id, &Value::Bool(true)));
        assert!(equal(&Value::Integer(1), &Value::Float(1.000001)));
        assert!(!equal(&Value::Integer(1), &Value::Float(1.001)));
        assert!(equal(&Value::Float(0.0), &Value::Null));
        assert!(equal(&Value::Array(vec![id.clone()]), &id));
        assert!(!truthy(&Value::Array(vec![
            Value::Bool(false),
            Value::Bool(true)
        ])));
        assert!(!truthy(&Value::Entity(0xbfff_ffff)));
        assert!(truthy(&Value::Entity(0xa000_0101)));
        assert_eq!(to_int(&Value::Array(vec![Value::Float(-2.5)]), 0), -3);
        assert_eq!(to_int(&id, 9), 9);
        assert_eq!(round_half_away(4_294_967_296.0), 0);
    }

    #[test]
    fn short_circuit_preserves_the_deciding_operand_and_scope_reads() {
        let variables = [variable(1, Scope::Instance), variable(2, Scope::Entity)];
        let mut context = Vars {
            instance: BTreeMap::from([(1, Value::Float(0.0))]),
            entity: BTreeMap::from([(2, Value::Integer(7))]),
            ..Vars::default()
        };
        let and = expression(json!([8, 0, 2, 4, 8, 1, 0]), &variables, &[], &[]);
        assert_eq!(evaluate(&and, &mut context).unwrap(), Value::Float(0.0));
        assert_eq!(
            context.reads,
            [Variable {
                scope: Scope::Instance,
                id: 1
            }]
        );
        context.reads.clear();
        let or = expression(json!([8, 0, 3, 4, 8, 1, 0]), &variables, &[], &[]);
        assert_eq!(evaluate(&or, &mut context).unwrap(), Value::Integer(7));
        assert_eq!(
            context.reads,
            [
                Variable {
                    scope: Scope::Instance,
                    id: 1
                },
                Variable {
                    scope: Scope::Entity,
                    id: 2
                }
            ],
        );
        let lvalue_expression = expression(json!([9, 1, 0]), &variables, &[], &[]);
        assert_eq!(
            lvalue(&lvalue_expression).unwrap(),
            Some(Variable {
                scope: Scope::Entity,
                id: 2
            })
        );
    }

    #[test]
    fn executes_only_the_selected_path_and_bounds_nested_expressions() {
        let untaken = expression(json!([6, 0, 2, 255, 6, 1, 0]), &[], &[1.0, 7.0], &[]);
        assert_eq!(
            evaluate(&untaken, &mut EmptyContext).unwrap(),
            Value::Float(7.0)
        );
        for (opcodes, offset, reason) in [
            (json!([1, 0]), 0, "non-progressing jump"),
            (json!([1, 2]), 0, "jump leaves the stream"),
            (json!([10, 1]), 0, "truncated opcode"),
            (json!([23]), 0, "expression stack underflow"),
            (json!([1, -1]), 1, "expected a byte"),
        ] {
            let config = expression(opcodes, &[], &[], &[]);
            assert_eq!(
                evaluate(&config, &mut EmptyContext),
                Err(EvalError::MalformedBytecode { offset, reason }),
            );
        }
        let inner = expression(json!([6, 0, 0]), &[], &[3.0], &[]);
        let nested = expression(json!([14, 0, 0]), &[], &[], &[inner]);
        assert_eq!(
            evaluate_with_budget(&nested, &mut EmptyContext, 3),
            Err(EvalError::BudgetExceeded {
                offset: 2,
                limit: 3
            }),
        );
        assert_eq!(
            evaluate_with_budget(&nested, &mut EmptyContext, 4).unwrap(),
            Value::Float(3.0)
        );
    }

    #[test]
    fn reports_unsupported_operations_instead_of_returning_a_guessed_null() {
        let map = expression(json!([19, 0, 0]), &[], &[], &[]);
        assert_eq!(
            evaluate(&map, &mut EmptyContext),
            Err(EvalError::UnsupportedOpcode {
                opcode: 19,
                offset: 0
            }),
        );
        assert_eq!(
            evaluate(&json!({"$": "STU_BE0BD267"}), &mut EmptyContext),
            Err(EvalError::UnsupportedConfig("STU_BE0BD267".into())),
        );
    }

    #[test]
    fn retail_constant_boolean_queries_reject_unrecognized_forms() {
        for class in ["STU_4A101787", "STU_E95A9C77"] {
            assert_eq!(
                evaluate(&json!({"$":class, "m_EE729DCB":0}), &mut EmptyContext),
                Ok(Value::Bool(false))
            );
            for invalid in [
                json!({"$":class, "m_EE729DCB":1}),
                json!({"$":class, "m_entity":{"$":"STU_B5A0CAF0"}}),
            ] {
                assert_eq!(
                    evaluate(&invalid, &mut EmptyContext),
                    Err(EvalError::UnsupportedConfig(class.into()))
                );
            }
        }
    }

    #[test]
    fn authored_random_expression_preserves_operand_types_and_inclusive_bounds() {
        struct Samples([u64; 3], usize);

        impl Context for Samples {
            fn random_u64(&mut self) -> Option<u64> {
                let sample = self.0.get(self.1).copied();
                self.1 += 1;
                sample
            }
        }

        let delay = expression(json!([6, 0, 6, 1, 69, 0]), &[], &[6.0, 12.0], &[]);
        let mut samples = Samples([1 << 63, 0, 2], 0);
        assert_eq!(evaluate(&delay, &mut samples), Ok(Value::Float(9.0)));
        let high = json!({"$":"STUConfigVarInt","m_value":3});
        let low = json!({"$":"STUConfigVarInt","m_value":1});
        let inclusive = expression(json!([14, 0, 14, 1, 69, 0]), &[], &[], &[high, low]);
        assert_eq!(evaluate(&inclusive, &mut samples), Ok(Value::Integer(3)));
        assert_eq!(samples.1, 3);

        let integral = expression(json!([6, 0, 6, 1, 70, 0]), &[], &[1.0, 2.0], &[]);
        assert_eq!(
            evaluate(&integral, &mut Samples([0; 3], 0)),
            Ok(Value::Integer(1))
        );
        assert_eq!(
            evaluate_with_budget(&inclusive, &mut Samples([0; 3], 0), 3),
            Err(EvalError::BudgetExceeded {
                offset: 4,
                limit: 3
            }),
        );
    }

    #[test]
    fn random_float_literal_rounds_bounds_and_supports_reverse_ranges() {
        struct Sample(u64);
        impl Context for Sample {
            fn random_u64(&mut self) -> Option<u64> {
                Some(self.0)
            }
        }
        for (low, high, sample, expected) in [
            (6.0, 12.0, 1 << 63, 9.0),
            (12.0, 6.0, 1 << 63, 9.0),
            (6.0, 12.0, 0, 6.0),
            (0.1, 0.1, u64::MAX, f64::from(0.1f32)),
        ] {
            assert_eq!(
                evaluate(
                    &json!({"$":"STU_BE0BD267","m_min":low,"m_max":high}),
                    &mut Sample(sample)
                ),
                Ok(Value::Float(expected))
            );
        }
        for (class, field) in [
            ("STUConfigVarTexture", "m_texture"),
            ("STU_08305297", "m_C21DB248"),
        ] {
            let config = json!({"$":class,field:null,"m_resourceKey":null});
            assert_eq!(evaluate(&config, &mut EmptyContext), Ok(Value::Asset(0)));
            let mut keyed = config;
            keyed["m_resourceKey"] = json!("0x0A90000000000010");
            assert_eq!(
                evaluate(&keyed, &mut EmptyContext),
                Err(EvalError::UnsupportedConfig(class.into()))
            );
        }
    }

    #[test]
    #[ignore = "requires an authorized 104319 graph pack in ORPHEUS_RUNTIME_DIR"]
    fn authored_controller_hud_and_soldier_config_values_match_python() {
        let data = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        )
        .join("data");
        let graphs = GraphStore::load(data.join("game_graphs_174.json.gz")).unwrap();
        let mut context = Vars {
            instance: BTreeMap::from([
                (28, Value::Integer(1)),
                (
                    375,
                    Value::Array(vec![Value::Bool(false), Value::Bool(true)]),
                ),
                (581, Value::Float(1.5)),
                (6884, Value::Float(rounded(0.511))),
                (6885, Value::Float(rounded(0.1))),
                (
                    8449,
                    Value::Array(vec![Value::Integer(1), Value::Integer(2)]),
                ),
                (11938, Value::Integer(7)),
                (17937, Value::Entity(0xa000_0100)),
                (25485, Value::Bool(true)),
            ]),
            entity: BTreeMap::from([
                (31, Value::Integer(1)),
                (9537, Value::Float(0.5)),
                (17153, Value::Bool(true)),
                (17154, Value::Asset(0x02e0_0000_0000_006e)),
                (27534, Value::Bool(true)),
            ]),
            ..Vars::default()
        };
        // Existing Python expr.evaluate against the same target graph fields
        // and scoped fixture variables. The timeout vector includes f32 bits.
        for (graph, rid, field, expected) in [
            (0x13c0, 6, "m_value", Value::Bool(false)),
            (0x13c0, 20, "m_condition", Value::Bool(true)),
            (0x13c1, 3, "m_condition", Value::Integer(7)),
            (0x0c90, 0, "m_16432044", Value::Asset(0x02e0_0000_0000_006e)),
            (0x288a, 10, "m_condition", Value::Bool(true)),
            (0x288b, 14, "m_hero", Value::Asset(0x02e0_0000_0000_0002)),
            (0x20e4, 3, "m_87276FE6", Value::Integer(23)),
            (0x0254, 8, "m_value", Value::Integer(30)),
        ] {
            let node = graphs.graph(graph).unwrap().by_rid(rid).unwrap();
            assert_eq!(
                evaluate(node.field(field).unwrap(), &mut context).unwrap(),
                expected,
                "{graph:04x} rid {rid} {field}"
            );
        }
        for (graph, state, field, expected) in [
            (
                0x0254,
                51,
                "m_timeout",
                Value::Float(f64::from(f32::from_bits(0x3f44_3958))),
            ),
            (0x0257, 12, "m_value", Value::Float(7.5)),
            (0x0258, 0, "m_05BE62DD", Value::Vector([0.0, 1.0, 0.0])),
        ] {
            let node = graphs.graph(graph).unwrap().state(state).unwrap();
            assert_eq!(
                evaluate(node.field(field).unwrap(), &mut context).unwrap(),
                expected,
                "{graph:04x} st {state} {field}"
            );
        }
    }

    #[test]
    fn shared_body_field_contract() {
        fn decode(value: &Json) -> Value {
            if let Some(entity) = value.get("entity").and_then(Json::as_u64) {
                Value::Entity(entity.try_into().unwrap())
            } else if let Some(asset) = value.get("asset").and_then(Json::as_u64) {
                Value::Asset(asset)
            } else if let Some(rows) = value.as_array() {
                Value::Array(rows.iter().map(decode).collect())
            } else if let Some(flag) = value.as_bool() {
                Value::Bool(flag)
            } else if let Some(number) = value.as_i64() {
                Value::Integer(number)
            } else {
                assert!(value.is_null());
                Value::Null
            }
        }
        struct Host<'a>(&'a Json);
        impl Context for Host<'_> {
            fn variable(&mut self, variable: Variable) -> Value {
                assert_eq!(
                    variable,
                    Variable {
                        scope: Scope::Instance,
                        id: 1
                    }
                );
                decode(&self.0["value"])
            }
            fn related_entity(&mut self, relation: EntityRelation, entity: u32) -> Option<u32> {
                assert_eq!(relation, EntityRelation::ControllerBodyField);
                if self.0["available"] == false {
                    return None;
                }
                let row = self.0["targets"].get(entity.to_string())?;
                if row == "absent" || row.get("body").is_some_and(Json::is_null) {
                    Some(0)
                } else {
                    row.get("body")?.as_u64().map(|id| id.try_into().unwrap())
                }
            }
        }
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-body-field-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["case_id"], "statescript.body-field.v1");
        assert_eq!(fixture["target_build"], 104319);
        let cases = fixture["input"]["cases"].as_array().unwrap();
        let results = fixture["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), results.len());
        for (case, expected) in cases.iter().zip(results) {
            assert_eq!(case["name"], expected["name"]);
            let actual = match evaluate(&case["config"], &mut Host(case)) {
                Ok(Value::Entity(entity)) => json!({"entity":entity}),
                Err(EvalError::UnsupportedConfig(class)) => json!({"error":class}),
                value => panic!("{}: {value:?}", case["name"]),
            };
            assert_eq!(actual, expected["output"], "{}", case["name"]);
        }
        // The native catalog establishes this class's parent fallback independently
        // of the query consumer; exercise its normal VM state classification.
        let mut graph = fixture["input"]["graph"].clone();
        graph["nodes"][0]["class"] = fixture["native_fallback"]["class"].clone();
        let index = graph["index"].as_u64().unwrap() as u32;
        let graphs = crate::script_graph::GraphStore::from_json(
            &serde_json::to_vec(&json!({"version":1,"graphs":[graph],"bodies":{},"missing":[]}))
                .unwrap(),
        )
        .unwrap();
        let component = crate::script_init::Component::start(
            std::sync::Arc::new(graphs),
            fixture["input"]["owner"].as_u64().unwrap() as u32,
            crate::script_wire::Variables::new(),
            vec![crate::script_init::Root::listener(1, index, Some(0))],
            0,
            &mut crate::script_init::EmptyWorld,
            crate::script_init::MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let state = &snapshot.instances[&1].states[&0];
        assert!(state.active);
        assert_eq!(
            format!("{:?}", state.kind),
            fixture["native_fallback"]["expected_kind"]
                .as_str()
                .unwrap()
        );
    }
}
