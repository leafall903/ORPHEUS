use super::*;

pub(super) fn class(config: &Json) -> Option<&str> {
    config.get("$")?.as_str()
}

pub(crate) fn client_only_fallback(config: &Json) -> Result<Option<&Json>, EvalError> {
    let fields = config
        .as_object()
        .filter(|fields| {
            fields.contains_key("value")
                && fields
                    .keys()
                    .all(|key| matches!(key.as_str(), "$" | "value" | "server_fallback"))
        })
        .ok_or(EvalError::MalformedConfig("invalid client-only operand"))?;
    Ok(fields.get("server_fallback"))
}

pub fn native_shape(config: &Json, expected: &str, operand: Option<&str>) -> bool {
    class(config) == Some(expected) && native_fields(config, operand.as_slice())
}

/// Recognize the owner selector form with one owner and only zero literals.
pub fn is_zeroed_owner_selector(config: &Json) -> bool {
    if class(config) != Some("STU_ED45C5E8") {
        return false;
    }
    let mut owners = 0;
    config.as_object().is_some_and(|fields| {
        fields.iter().all(|(key, value)| match key.as_str() {
            "$" => true,
            "m_EE729DCB" => value.as_u64() == Some(0),
            _ if native_shape(value, "STU_B5A0CAF0", None) => {
                owners += 1;
                true
            }
            _ => {
                ["STUConfigVarBool", "STUConfigVarFloat"]
                    .into_iter()
                    .any(|class| native_shape(value, class, Some("m_value")))
                    && value["m_value"].as_f64() == Some(0.0)
            }
        })
    }) && owners == 1
}

pub(crate) fn native_entity_operand<'a>(config: &'a Json, expected: &str) -> Option<&'a Json> {
    config
        .get("m_entity")
        .filter(|_| native_shape(config, expected, Some("m_entity")))
}

pub(crate) fn native_target_tag_operand(config: &Json) -> Option<(u64, &Json)> {
    if class(config) != Some("STU_25ECB55F") || !native_fields(config, &["m_entity", "m_88B0050A"])
    {
        return None;
    }
    let tag = target_tag_guid(child(config, "m_88B0050A"))?;
    Some((tag, child(config, "m_entity")))
}

pub(crate) fn named_shield_identifier(config: &Json) -> Option<u64> {
    if class(config) != Some("STUConfigVarIdentifier")
        || !native_fields(config, &["m_identifier", "m_resourceKey"])
        || !child(config, "m_resourceKey").is_null()
    {
        return None;
    }
    child(config, "m_identifier")
        .as_str()
        .and_then(orpheus_runtime::format::parse_guid)
        .filter(|id| id >> 52 == 0x0d8 && *id as u32 != 0)
}

pub fn native_fields(config: &Json, operands: &[&str]) -> bool {
    config.as_object().is_some_and(|fields| {
        fields.iter().all(|(key, value)| match key.as_str() {
            "$" => true,
            "m_EE729DCB" => value.as_u64() == Some(0),
            key => operands.contains(&key),
        })
    })
}

pub(super) fn is_expression(class: Option<&str>) -> bool {
    matches!(class, Some("STU_919FD47C" | "STUConfigVarExpression"))
}

pub(super) fn child<'a>(config: &'a Json, field: &str) -> &'a Json {
    config.get(field).unwrap_or(&Json::Null)
}

pub(super) fn literal_asset(
    config: &Json,
    expected: &str,
    field: &str,
    kind: u64,
) -> Option<Value> {
    if class(config) != Some(expected)
        || !native_fields(config, &[field, "m_resourceKey"])
        || !child(config, "m_resourceKey").is_null()
    {
        return None;
    }
    let guid = asset_guid(child(config, field))?;
    (guid >> 48 == kind).then_some(Value::Asset(guid))
}

pub(super) fn json_truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(value) => *value,
        Json::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Json::String(value) => !value.is_empty(),
        Json::Array(value) => !value.is_empty(),
        Json::Object(value) => !value.is_empty(),
    }
}

pub(super) fn number(value: &Json) -> Result<f64, EvalError> {
    match value {
        Json::Null => Some(0.0),
        Json::Bool(value) => Some(f64::from(u8::from(*value))),
        Json::Number(value) => value.as_f64(),
        Json::String(value) => value.trim().parse().ok(),
        _ => None,
    }
    .ok_or(EvalError::MalformedConfig("expected a number"))
}

pub(super) fn integer(value: &Json) -> Result<i64, EvalError> {
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

pub(super) fn identifier(config: &Json) -> Result<u32, EvalError> {
    let value = child(config, "m_identifier");
    if !json_truthy(value) {
        return Ok(0);
    }
    u32::try_from(integer(value)?)
        .map_err(|_| EvalError::MalformedConfig("variable identifier out of range"))
}

pub(super) fn resource(value: &Json) -> Option<u64> {
    asset_guid(value).or_else(|| {
        let text = value.as_str()?;
        text.starts_with("0x").then(|| parse_guid(text)).flatten()
    })
}

pub fn target_tag_config(config: &Json) -> Option<u64> {
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

pub(super) const ANY_ENTITY_MASK: u64 = 0x3fff_ffff;

pub(super) fn filter_shape(filter: &Json) -> bool {
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

pub(super) fn filter_masks(filter: &Json, scene_hostile: bool) -> Option<[u64; 3]> {
    if !filter_shape(filter) {
        return None;
    }
    let supported = filter
        .as_object()?
        .iter()
        .all(|(key, value)| match key.as_str() {
            "$" | "m_84065FD1" | "m_1E14CCA8" | "m_4EA686DB" | "m_9C7BD8B8" | "m_B486603E"
            | "m_8B2A4DF3" => true,
            "m_27CF71D5" | "m_66747E7D" | "m_89831602" if scene_hostile => true,
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
    entity_masks(filter)
}

pub(crate) fn entity_masks(filter: &Json) -> Option<[u64; 3]> {
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

pub(crate) fn matches_entity_masks([any, all, exclude]: [u64; 3], flags: u64) -> bool {
    (any | all | exclude) == 0
        || ((any == ANY_ENTITY_MASK || flags & any != 0)
            && flags & all == all
            && flags & exclude == 0)
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum TagCondition {
    Any,
    All,
    Exclude,
}

pub(super) enum TagCheck {
    Unused,
    Accept,
    Reject,
    Unknown,
}

pub(super) fn check_tag_set(
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

pub fn excluded_tags_admit(
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

pub(super) fn resource_field(class: &str) -> Option<&'static str> {
    match class {
        "STUConfigVarIdentifier" => Some("m_identifier"),
        "STUConfigVarGameMessage" => Some("m_gameMessage"),
        "STUConfigVarAnimAlias" => Some("m_animAlias"),
        "STUConfigVarDataFlow" => Some("m_5A6D4FBD"),
        "STUConfigVarUXDisplayText" => Some("m_displayText"),
        "STUConfigVarVoiceStimulus" => Some("m_voiceStimulus"),
        "STUConfigVarContactSet" => Some("m_contactSet"),
        "STUConfigVarEffect" => Some("m_effect"),
        "STUConfigVarLoadout" => Some("m_loadout"),
        "STUConfigVarHardPoint" => Some("m_hardPoint"),
        "STUConfigVarGameMode" => Some("m_gamemode"),
        "STU_BCD1C634" => Some("m_id"),
        "STU_8A491388" => Some("m_priority"),
        "STU_357DABBB" => Some("m_7D7C86A1"),
        _ => None,
    }
}

/// The class of a config object the reference evaluates through its world's
/// native hook: expr.py `evaluate` falls through to `ctx.native` for every
/// class it does not compute itself.
pub(super) fn reference_native_class(config: &Json) -> Option<&str> {
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
        | "STUConfigVarVec3"
        | "EntityPosition"
        | "EntityDirection"
        | "BodyActive"
        | "EntityMatches"
        | "StackAllows"
        | "EntitiesInRadius"
        | "SegmentClear"
        | "RayCast"
        | "EntityRayCast" => false,
        "STU_B40ACFBA" | "STU_4BD39CFD" | "STU_8529C204" | "STU_BD2D717B" | "STU_EB216A0B" => false,
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

pub(super) struct Expression<'a> {
    pub(super) code: &'a [Json],
    pub(super) dynamics: &'a [Json],
    pub(super) floats: &'a [Json],
    pub(super) configs: &'a [Json],
}

impl<'a> Expression<'a> {
    pub(super) fn read(config: &'a Json) -> Result<Self, EvalError> {
        let data = child(config, "m_expression");
        Ok(Self {
            code: array_field(data, "m_opcodes")?,
            dynamics: array_field(data, "m_dynamicVars")?,
            floats: array_field(data, "m_D99EF254")?,
            configs: array_field(config, "m_configVars")?,
        })
    }
}

pub(super) fn array_field<'a>(config: &'a Json, key: &str) -> Result<&'a [Json], EvalError> {
    match child(config, key) {
        Json::Null => Ok(&[]),
        Json::Array(values) => Ok(values),
        _ => Err(EvalError::MalformedConfig("expected an expression table")),
    }
}

pub(super) fn malformed(offset: usize, reason: &'static str) -> EvalError {
    EvalError::MalformedBytecode { offset, reason }
}

pub(super) fn byte(value: &Json, offset: usize) -> Result<u8, EvalError> {
    value
        .as_u64()
        .and_then(|value| u8::try_from(value).ok())
        .ok_or_else(|| malformed(offset, "expected a byte"))
}

pub(super) fn operand_count(opcode: u8) -> usize {
    match opcode {
        10 => 2,
        1..=9 | 11..=14 | 19..=21 => 1,
        _ => 0,
    }
}

pub(super) fn table(values: &[Json], index: u8, offset: usize) -> Result<&Json, EvalError> {
    values
        .get(usize::from(index))
        .ok_or_else(|| malformed(offset, "expression table index out of range"))
}

pub(super) fn stack_var(values: &[Json], index: u8, offset: usize) -> Result<Variable, EvalError> {
    dynamic(table(values, index, offset)?)?
        .ok_or_else(|| malformed(offset, "expected a dynamic variable"))
}

pub(super) fn jump(pc: usize, offset: u8, size: usize) -> Result<usize, EvalError> {
    if offset == 0 {
        return Err(malformed(pc, "non-progressing jump"));
    }
    let target = pc + usize::from(offset);
    if target >= size {
        return Err(malformed(pc, "jump leaves the stream"));
    }
    Ok(target)
}

pub(super) fn pop(stack: &mut Vec<Value>, pc: usize) -> Result<Value, EvalError> {
    stack
        .pop()
        .ok_or_else(|| malformed(pc, "expression stack underflow"))
}

pub(super) fn pair(stack: &mut Vec<Value>, pc: usize) -> Result<(Value, Value), EvalError> {
    let b = pop(stack, pc)?;
    Ok((pop(stack, pc)?, b))
}

pub(super) fn lookup(key: &Value, scope: Scope) -> Option<Variable> {
    Some(Variable {
        scope,
        id: u32::try_from(to_int(key, 0)).ok()?,
    })
}

pub(super) fn entity_member(
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

pub(super) fn into_array(value: Value) -> Vec<Value> {
    match value {
        Value::Null => Vec::new(),
        Value::Array(items) => items,
        value => vec![value],
    }
}

pub(super) fn vector(value: &Value) -> [f64; 3] {
    match value {
        Value::Vector(parts) => *parts,
        _ => [0.0; 3],
    }
}

pub(super) fn vector_position(
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

pub(super) fn vector_op(a: [f64; 3], b: [f64; 3], op: impl Fn(f64, f64) -> f64) -> [f64; 3] {
    std::array::from_fn(|index| rounded(op(a[index], b[index])))
}

pub(super) fn magnitude([x, y, z]: [f64; 3]) -> f64 {
    rounded((x * x + y * y + z * z).sqrt())
}

pub(super) fn minimum(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

pub(super) fn maximum(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

pub(super) fn arithmetic(
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
