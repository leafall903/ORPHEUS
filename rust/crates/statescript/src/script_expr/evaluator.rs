use std::sync::OnceLock;

use super::{helpers::*, *};

#[derive(serde::Deserialize)]
struct WheelSlots {
    emote: [String; 4],
    voice: [String; 4],
    spray: [String; 4],
}

fn wheel_slot(class: &str, action: Value) -> Option<i64> {
    static SLOTS: OnceLock<Option<WheelSlots>> = OnceLock::new();
    let slots = SLOTS
        .get_or_init(|| serde_json::from_str(include_str!("wheel_slots.json")).ok())
        .as_ref()?;
    let actions = match class {
        "STU_3E7F59D8" => &slots.emote,
        "STU_955C40AE" => &slots.voice,
        "STU_2E708EC1" => &slots.spray,
        _ => return None,
    };
    let action = match &action {
        Value::Asset(action) => *action,
        Value::Array(items) => match items.as_slice() {
            [Value::Asset(action)] => *action,
            _ => return Some(-1),
        },
        _ => return Some(-1),
    };
    Some(
        actions
            .iter()
            .position(|guid| orpheus_runtime::format::parse_guid(guid) == Some(action))
            .map_or(-1, |slot| slot as i64),
    )
}

pub(super) struct Fold {
    count: usize,
    index: usize,
    value: Value,
    item: Option<Value>,
}

struct SpatialRay {
    origin: [f64; 3],
    direction: [f64; 3],
    max_distance: f64,
}

struct SpatialCone {
    direction: [f64; 3],
    angle_limit: f64,
}

impl SpatialCone {
    fn scale_direction(vector: [f64; 3]) -> Option<[f64; 3]> {
        if !vector.iter().all(|part| part.is_finite()) {
            return None;
        }
        let largest = vector.into_iter().map(f64::abs).fold(0.0, f64::max);
        (largest > 0.0).then(|| vector.map(|part| part / largest))
    }

    fn contains(&self, offset: [f64; 3]) -> bool {
        let Some([x, y, z]) = Self::scale_direction(offset) else {
            return false;
        };
        let [a, b, c] = self.direction;
        // Scaling each vector keeps the cross and dot finite without changing
        // the angle. atan2 distinguishes narrow cones and exact right angles.
        let cross = (b * z - c * y).hypot(c * x - a * z).hypot(a * y - b * x);
        let dot = a * x + b * y + c * z;
        cross.atan2(dot) <= self.angle_limit
    }
}

#[derive(Clone, Copy)]
enum TeamRelation {
    Ally,
    Opponent,
}

impl TeamRelation {
    fn matches(self, source: Option<u8>, target: Option<u8>) -> bool {
        let same_team = source.is_some() && source == target;
        match self {
            Self::Ally => same_team,
            Self::Opponent => !same_team,
        }
    }
}

#[derive(Default)]
struct RoomEntityFilter {
    relation: Option<(u32, TeamRelation)>,
    alive: Option<bool>,
    tags: Option<RoomTagFilter>,
}

impl RoomEntityFilter {
    fn needs_facts(&self) -> bool {
        self.alive.is_some() || self.tags.is_some()
    }
}

#[derive(Default)]
struct RoomTagFilter {
    all: Vec<u64>,
    any: Vec<u64>,
    none: Vec<u64>,
}

#[derive(Clone, Copy)]
enum RoomTagClause {
    All,
    Any,
    None,
}

fn health_amount(
    target: &Json,
    value: &Value,
    kind: Option<PoolKind>,
    maximum: bool,
    context: &mut impl Context,
) -> Option<f64> {
    match value {
        Value::Entity(0) => Some(0.0),
        Value::Entity(entity) if valid_entity(*entity) => {
            context.body_health(*entity, kind, maximum)
        }
        // A target slot can be reset before it has been bound to an entity.
        Value::Null | Value::Integer(0) if matches!(dynamic(target), Ok(Some(variable)) if variable.id != 0) => {
            Some(0.0)
        }
        _ => None,
    }
}

pub(super) struct Evaluator {
    pub(super) limit: usize,
    pub(super) steps: usize,
    pub(super) folds: Vec<Fold>,
    pub(super) scoped_parameters: Vec<(u32, Value)>,
    /// Inside a native's operands, which the reference does not watch.
    pub(super) in_native: bool,
}

impl Evaluator {
    pub(super) fn new(limit: usize, context: &impl Context) -> Self {
        Self {
            limit,
            steps: 0,
            folds: Vec::new(),
            scoped_parameters: Vec::new(),
            // An unwatched evaluation has no use for the record.
            in_native: !context.watches_natives(),
        }
    }

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

    fn filter_array(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let mut items = Vec::new();
        self.collect_group(child(config, "m_814F8EC3"), context, depth, &mut items)?;
        let condition = child(config, "m_condition");
        let mut filtered = Vec::with_capacity(items.len());
        for item in items {
            self.charge()?;
            if condition.is_null() {
                filtered.push(item);
                continue;
            }
            self.scoped_parameters.push((142, item.clone()));
            let result = self.scalar_config(condition, context, depth + 1, Value::Bool(false));
            self.scoped_parameters.pop();
            if truthy(&result?) {
                filtered.push(item);
            }
        }
        Ok(Value::Array(filtered))
    }

    fn transform_entity_vector(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let entity =
            self.scalar_config(child(config, "m_entity"), context, depth, Value::Entity(0))?;
        let vector = match self.scalar_config(
            child(config, "m_64E06CA4"),
            context,
            depth,
            Value::Vector([0.0; 3]),
        )? {
            Value::Vector(v) => v,
            Value::Null => [0.0; 3],
            _ => return Err(EvalError::MalformedConfig("STU_588FF314 vector operand")),
        };
        let to_world = truthy(&self.scalar_config(
            child(config, "m_1FDD7084"),
            context,
            depth,
            Value::Bool(false),
        )?);
        let include_position = child(config, "m_AEE599AA").as_u64() == Some(1);
        let forward_raw = match entity {
            Value::Entity(id) if valid_entity(id) => context.authored_forward(id),
            _ => None,
        }
        .unwrap_or([0.0, 0.0, 1.0]);
        let horiz_len = forward_raw[0].hypot(forward_raw[2]);
        let (fx, fz) = if horiz_len > 1e-6 {
            (forward_raw[0] / horiz_len, forward_raw[2] / horiz_len)
        } else {
            (0.0, 1.0)
        };
        // Orthonormal yaw basis matching STU_021D01CD (forward=[sin,0,cos]) and STU_66F3B89E (right=[cos,0,-sin]).
        let right = [fz, 0.0, -fx];
        let forward = [fx, 0.0, fz];
        let transformed = if to_world {
            let mut out = [
                vector[0] * right[0] + vector[2] * forward[0],
                vector[1],
                vector[0] * right[2] + vector[2] * forward[2],
            ];
            if include_position {
                if let Value::Entity(id) = entity {
                    if valid_entity(id) {
                        if let Some(pos) = context.authored_position(id) {
                            for axis in 0..3 {
                                out[axis] += pos[axis];
                            }
                        }
                    }
                }
            }
            out
        } else {
            let mut rel = vector;
            if include_position {
                if let Value::Entity(id) = entity {
                    if valid_entity(id) {
                        if let Some(pos) = context.authored_position(id) {
                            for axis in 0..3 {
                                rel[axis] -= pos[axis];
                            }
                        }
                    }
                }
            }
            [
                rel[0] * right[0] + rel[2] * right[2],
                rel[1],
                rel[0] * forward[0] + rel[2] * forward[2],
            ]
        };
        Ok(Value::Vector(transformed.map(|c| f64::from(c as f32))))
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

    pub(super) fn parameter(&self, context: &mut impl Context, id: u32) -> Value {
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

    fn authored_pairs(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let unsupported = || EvalError::UnsupportedConfig("STU_7757D403".into());
        if !native_fields(config, &["m_configVars"]) {
            return Err(unsupported());
        }
        let mut pairs = Vec::new();
        for row in array_field(config, "m_configVars")? {
            self.charge()?;
            if class(row) != Some("STU_98DD1872") || !native_fields(row, &["m_key", "m_value"]) {
                return Err(unsupported());
            }
            let value = self.scalar_config(child(row, "m_value"), context, depth, Value::Null)?;
            let key = self
                .scalar_config(child(row, "m_key"), context, depth, Value::Null)
                .map_err(|error| match error {
                    EvalError::UnsupportedConfig(_) => unsupported(),
                    other => other,
                })?;
            if !matches!(key, Value::Asset(guid) if guid != 0) {
                return Err(unsupported());
            }
            pairs.push((key, value));
        }
        Ok(Value::Map(pairs))
    }

    pub(super) fn config(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        self.config_with_initial(config, context, depth, Value::Null)
    }

    /// The native +20 selector path reads only the first config of a group.
    pub(super) fn scalar_config(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
        initial: Value,
    ) -> Result<Value, EvalError> {
        self.config_in_mode(config, context, depth, initial, true)
    }

    pub(super) fn config_with_initial(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
        initial: Value,
    ) -> Result<Value, EvalError> {
        self.config_in_mode(config, context, depth, initial, false)
    }

    fn config_in_mode(
        &mut self,
        mut config: &Json,
        context: &mut impl Context,
        mut depth: usize,
        initial: Value,
        scalar: bool,
    ) -> Result<Value, EvalError> {
        loop {
            let selected = match class(config) {
                Some("ClientOnly") => {
                    if depth >= MAX_CONFIG_DEPTH {
                        return Err(EvalError::NestingExceeded);
                    }
                    let fallback = client_only_fallback(config)?;
                    self.charge()?;
                    fallback
                }
                Some("STU_06610EBC") if scalar => {
                    if depth >= MAX_CONFIG_DEPTH {
                        return Err(EvalError::NestingExceeded);
                    }
                    if !native_shape(config, "STU_06610EBC", Some("m_configVars")) {
                        return Err(EvalError::UnsupportedConfig("STU_06610EBC".into()));
                    }
                    self.charge()?;
                    array_field(config, "m_configVars")?.first()
                }
                _ => break,
            };
            let Some(next) = selected else {
                return Ok(initial);
            };
            config = next;
            depth += 1;
        }
        if scalar {
            if depth >= MAX_CONFIG_DEPTH {
                return Err(EvalError::NestingExceeded);
            }
            if config.is_null() {
                return Ok(initial);
            }
        }
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
            "STU_CC53F52B" | "STU_7BE6D76D" | "STU_40AC5AE4"
                if native_shape(config, class, Some("m_entity")) =>
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
            "STUConfigVarUInt" => Value::Integer(integer(value)?),
            "STUConfigVarFloat" => Value::Float(rounded(number(value)?)),
            "STUConfigVarHero" => Value::Asset(resource(child(config, "m_hero")).unwrap_or(0)),
            "STUConfigVarMap" => Value::Asset(resource(child(config, "m_map")).unwrap_or(0)),
            "STUConfigVarModelLook" => {
                Value::Asset(resource(child(config, "m_modelLook")).unwrap_or(0))
            }
            "STUConfigVarSkinTheme" => {
                Value::Asset(resource(child(config, "m_skinTheme")).unwrap_or(0))
            }
            "STUConfigVarStat" => Value::Asset(resource(child(config, "m_stat")).unwrap_or(0)),
            "STUConfigVarGamePadVibration" => {
                Value::Asset(resource(child(config, "m_vibration")).unwrap_or(0))
            }
            "STUConfigVarCelebration" => {
                Value::Asset(resource(child(config, "m_celebration")).unwrap_or(0))
            }
            "STUConfigVarGameRuleset" => Value::Asset(
                resource(child(config, "m_gameRuleset"))
                    .or_else(|| resource(child(config, "m_ruleset")))
                    .unwrap_or(0),
            ),
            "STUConfigVarResourceKey" => {
                Value::Asset(resource(child(config, "m_resourceKey")).unwrap_or(0))
            }
            "STUConfigVarGetLootBoxAsset" => Value::Asset(
                resource(child(config, "m_lootBox"))
                    .or_else(|| resource(child(config, "m_asset")))
                    .unwrap_or(0),
            ),
            "STUConfigVarGetGameModeName" => {
                let gm = child(config, "m_gamemode");
                if gm.is_object() {
                    self.config(gm, context, nested)?
                } else {
                    Value::Asset(resource(gm).unwrap_or(0))
                }
            }
            "STUConfigVarEntityReference" => {
                let target = child(config, "m_entity");
                if !target.is_null() {
                    self.scalar_config(target, context, nested, Value::Entity(0))?
                } else if !value.is_null() {
                    self.scalar_config(value, context, nested, Value::Entity(0))?
                } else {
                    Value::Entity(0)
                }
            }
            "STUConfigVarColor" => {
                let r = value.get("R").and_then(Json::as_f64).unwrap_or(0.0);
                let g = value.get("G").and_then(Json::as_f64).unwrap_or(0.0);
                let b = value.get("B").and_then(Json::as_f64).unwrap_or(0.0);
                Value::Vector([rounded(r), rounded(g), rounded(b)])
            }
            // Blink / teleport effect descriptor (`STUStatescriptStateBlinkEffect`):
            // holds `m_58D56DD4` (`STUConfigVarEffect`) and optional `m_3E3E203A` (owner entity).
            "STU_B8F4347B" => {
                let eff = child(config, "m_58D56DD4");
                if !eff.is_null() {
                    self.config(eff, context, nested)?
                } else {
                    Value::Asset(0)
                }
            }
            // Entity collision/capsule radius query (`STU_F7FD94F9(m_A83C2C26 | m_entity)`).
            // Used in Reinhardt Charge/Pin, Doomfist Rocket Punch (`0x0D06:17,156,157`), and melee reach math.
            "STU_F7FD94F9" => {
                let target_cfg = {
                    let primary = child(config, "m_A83C2C26");
                    if !primary.is_null() {
                        primary
                    } else {
                        child(config, "m_entity")
                    }
                };
                let entity = self.scalar_config(target_cfg, context, nested, Value::Entity(0))?;
                let valid =
                    matches!(first_scalar(&entity), Some(Value::Entity(e)) if valid_entity(*e));
                Value::Float(if valid { 0.55 } else { 0.0 })
            }
            // Entity facing/aim direction (`STU_D32169F6(m_entity)`, 39x across Doomfist/heroes).
            "STU_D32169F6" => {
                let entity = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let dir = match first_scalar(&entity) {
                    Some(Value::Entity(e)) if valid_entity(*e) => context
                        .authored_forward(*e)
                        .filter(|parts| parts.iter().all(|p| p.is_finite()))
                        .unwrap_or([0.0, 0.0, 1.0]),
                    _ => [0.0, 0.0, 1.0],
                };
                Value::Vector(dir)
            }
            // Transform world vector `m_5E57229B` into the local frame of forward vector
            // `m_794397AC` (`STU_9B5656C5`, 91x including Soldier: 76 Tactical Visor).
            "STU_9B5656C5" => {
                let vec_a = vector(&self.config(child(config, "m_5E57229B"), context, nested)?);
                let vec_b = vector(&self.config(child(config, "m_794397AC"), context, nested)?);
                let len_b = magnitude(vec_b);
                if len_b <= EPSILON {
                    Value::Vector([0.0; 3])
                } else {
                    let fwd = [vec_b[0] / len_b, vec_b[1] / len_b, vec_b[2] / len_b];
                    let ref_up = if fwd[1].abs() < 0.99 {
                        [0.0, 1.0, 0.0]
                    } else {
                        [0.0, 0.0, 1.0]
                    };
                    let rx = ref_up[1] * fwd[2] - ref_up[2] * fwd[1];
                    let ry = ref_up[2] * fwd[0] - ref_up[0] * fwd[2];
                    let rz = ref_up[0] * fwd[1] - ref_up[1] * fwd[0];
                    let rlen = rx.hypot(ry).hypot(rz).max(EPSILON);
                    let right = [rx / rlen, ry / rlen, rz / rlen];
                    let up = [
                        fwd[1] * right[2] - fwd[2] * right[1],
                        fwd[2] * right[0] - fwd[0] * right[2],
                        fwd[0] * right[1] - fwd[1] * right[0],
                    ];
                    let dot = |u: [f64; 3], v: [f64; 3]| u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
                    Value::Vector([
                        rounded(dot(vec_a, right)),
                        rounded(dot(vec_a, up)),
                        rounded(dot(vec_a, fwd)),
                    ])
                }
            }
            // Rotate vector `m_CFEC5F76` by `m_ED584F48` degrees around axis `m_220789B0`
            // or cardinal axis `m_C3681302` (`STU_DADD0A1D`, Mei Ice Wall rotation).
            "STU_DADD0A1D" => {
                let v = vector(&self.config(child(config, "m_CFEC5F76"), context, nested)?);
                let angle_deg =
                    to_float(&self.config(child(config, "m_ED584F48"), context, nested)?);
                let axis_cfg = child(config, "m_220789B0");
                let raw_axis = if !axis_cfg.is_null() {
                    vector(&self.config(axis_cfg, context, nested)?)
                } else {
                    match child(config, "m_C3681302").as_u64() {
                        Some(0) => [1.0, 0.0, 0.0],
                        Some(2) => [0.0, 0.0, 1.0],
                        _ => [0.0, 1.0, 0.0],
                    }
                };
                let alen = magnitude(raw_axis);
                if alen <= EPSILON || !angle_deg.is_finite() {
                    Value::Vector(v)
                } else {
                    let k = [raw_axis[0] / alen, raw_axis[1] / alen, raw_axis[2] / alen];
                    let (s, c) = angle_deg.to_radians().sin_cos();
                    let kdotv = k[0] * v[0] + k[1] * v[1] + k[2] * v[2];
                    let cross = [
                        k[1] * v[2] - k[2] * v[1],
                        k[2] * v[0] - k[0] * v[2],
                        k[0] * v[1] - k[1] * v[0],
                    ];
                    Value::Vector([
                        rounded(v[0] * c + cross[0] * s + k[0] * kdotv * (1.0 - c)),
                        rounded(v[1] * c + cross[1] * s + k[1] * kdotv * (1.0 - c)),
                        rounded(v[2] * c + cross[2] * s + k[2] * kdotv * (1.0 - c)),
                    ])
                }
            }
            // Array IndexOf (`STU_9FA47317(m_814F8EC3, m_153EC56B)` -> index or -1).
            "STU_9FA47317" => {
                let array_val = self.config(child(config, "m_814F8EC3"), context, nested)?;
                let needle = self.config(child(config, "m_153EC56B"), context, nested)?;
                let idx = match array_val {
                    Value::Array(items) => items
                        .iter()
                        .position(|item| equal(item, &needle))
                        .map_or(-1, |i| i as i64),
                    _ => -1,
                };
                Value::Integer(idx)
            }
            "STU_BD2D717B" if native_shape(config, class, Some("m_B257A3A4")) => {
                Value::Bool(integer(child(config, "m_B257A3A4"))? != 0)
            }
            "STU_8529C204" if native_fields(config, &["m_out_Var", "m_value"]) => {
                self.config(child(config, "m_value"), context, nested)?
            }
            "STU_0762B33A"
                if native_fields(
                    config,
                    &["m_32B00625", "m_3661BC45", "m_5023B9B0", "m_E26E5E09"],
                ) =>
            {
                self.clamp_direction(config, context, nested)?
            }
            // Build 104319 selects the failed base evaluator for this class;
            // a fresh destination remains null and the operand is never read.
            "STU_B40ACFBA" if native_fields(config, &["m_52EA7AB9"]) => Value::Null,
            "STUConfigVarLogicalButton" => {
                Value::Integer(integer(child(config, "m_logicalButton"))?)
            }
            "STU_A2A35433" if native_fields(config, &["m_entity", "m_logicalButton"]) => {
                let target = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let button = self.scalar_config(
                    child(config, "m_logicalButton"),
                    context,
                    nested,
                    Value::Integer(0),
                )?;
                let Some(Value::Entity(entity)) = first_scalar(&target) else {
                    return Ok(Value::Bool(false));
                };
                if !valid_entity(*entity) {
                    return Ok(Value::Bool(false));
                }
                let Ok(button) = u8::try_from(to_int(&button, -1)) else {
                    return Ok(Value::Bool(false));
                };
                Value::Bool(
                    context
                        .button_held(*entity, button)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                )
            }
            "STU_BD6CC023" if native_shape(config, class, Some("m_6CE7A900")) => {
                let team = child(config, "m_6CE7A900");
                Value::Integer(if team.is_null() {
                    0
                } else {
                    i64::from(integer(team)? as i32)
                })
            }
            "STU_038C32E8" if native_shape(config, class, Some("m_3CE40E49")) => {
                let variant = child(config, "m_3CE40E49");
                Value::Integer(if variant.is_null() {
                    0
                } else {
                    i64::from(integer(variant)? as i32)
                })
            }
            "STU_ED807E07" if native_shape(config, class, Some("m_entity")) => {
                let target = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                match first_scalar(&target) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => {
                        context.native(config).unwrap_or(Value::Float(1.0))
                    }
                    _ => Value::Float(0.0),
                }
            }
            "STU_082FA44E" if native_fields(config, &["m_entity", "m_DBF61E9A"]) => {
                let target = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                match first_scalar(&target) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => {
                        context.native(config).unwrap_or(Value::Float(0.0))
                    }
                    _ => Value::Float(0.0),
                }
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
            "STU_06610EBC" if native_shape(config, class, Some("m_configVars")) => {
                let mut items = Vec::new();
                self.collect_group(config, context, nested, &mut items)?;
                Value::Array(items)
            }
            "STU_6EDA44A7" if native_fields(config, &["m_entity", "m_BD631144", "m_height"]) => {
                let target = self.scalar_config(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let Some(Value::Entity(entity)) = first_scalar(&target) else {
                    return Ok(Value::Vector([0.0; 3]));
                };
                if !valid_entity(*entity) {
                    return Ok(Value::Vector([0.0; 3]));
                }
                let origin = context.authored_position(*entity).unwrap_or([0.0; 3]);
                let [fx, _, fz] = context.authored_forward(*entity).unwrap_or([0.0, 0.0, 1.0]);
                let planar = fx.hypot(fz);
                let (fx, fz) = if planar > 1e-6 {
                    (fx / planar, fz / planar)
                } else {
                    (0.0, 1.0)
                };
                let (rx, rz) = (fz, -fx);
                let offset = match self.config(child(config, "m_BD631144"), context, nested)? {
                    Value::Vector(v) => v,
                    _ => [0.0; 3],
                };
                Value::Vector([
                    rounded(origin[0] + rx * offset[0] + fx * offset[2]),
                    rounded(origin[1] + offset[1]),
                    rounded(origin[2] + rz * offset[0] + fz * offset[2]),
                ])
            }
            "STU_B3E67A36" | "STU_EB216A0B"
                if native_fields(
                    config,
                    &["m_filter", "m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"],
                ) && !child(config, "m_filter").is_null()
                    && (class == "STU_EB216A0B" || child(config, "m_B486603E").is_null())
                    && child(config, "m_8B2A4DF3").is_null()
                    && (child(child(config, "m_filter"), "m_9C7BD8B8").is_null()
                        && (target_tag_config(child(config, "m_9C7BD8B8")).is_some()
                            || (class == "STU_EB216A0B"
                                && target_tag_config(child(config, "m_B486603E")).is_some()))
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
                } else if class == "STU_EB216A0B" && !child(config, "m_B486603E").is_null() {
                    filter["m_B486603E"] = child(config, "m_B486603E").clone();
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
                    match self.filter_entity(&filter, entity, context, nested, class)? {
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
            "STU_B3E67A36" | "STU_EB216A0B"
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
                let operand = match (primary.is_null(), required.is_null()) {
                    (false, true) => primary,
                    (true, false) => required,
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                };
                match self.config(operand, context, nested)? {
                    Value::Asset(tag) if tag >> 48 == 0x0d60 => {
                        let entity = context
                            .static_target(tag)
                            .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                        Value::Entity(entity)
                    }
                    value if class == "STU_EB216A0B" && !primary.is_null() => value,
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                }
            }
            "EntityPosition" | "EntityDirection" => {
                let operand = fields
                    .get("entity")
                    .filter(|_| fields.len() == 2)
                    .ok_or(EvalError::MalformedConfig("expected one entity operand"))?;
                self.charge()?;
                let Value::Entity(entity @ 1..) = self.config(operand, context, nested)? else {
                    return Err(EvalError::MalformedConfig("expected a bound entity"));
                };
                let (value, unavailable) = if class == "EntityPosition" {
                    (
                        context.authored_position(entity),
                        EvalError::UnavailableEntityPosition(entity),
                    )
                } else {
                    (
                        context.authored_forward(entity),
                        EvalError::UnavailableEntityDirection(entity),
                    )
                };
                Value::Vector(
                    value
                        .filter(|parts| parts.iter().all(|part| part.is_finite()))
                        .ok_or(unavailable)?,
                )
            }
            "STU_2200A66D" => {
                let operand = native_entity_operand(config, class).ok_or(
                    EvalError::MalformedConfig("expected one native direction entity operand"),
                )?;
                let native = context.native(config);
                if matches!(&native, Some(Value::Null)) {
                    // A host can explicitly report that this query has no target.
                    return Ok(Value::Null);
                }
                self.charge()?;
                let Value::Entity(entity) = self.config(operand, context, nested)? else {
                    return Err(EvalError::MalformedConfig("expected a bound entity"));
                };
                if !valid_entity(entity) {
                    return Err(EvalError::MalformedConfig("expected a bound entity"));
                }
                // Preserve existing host-specific numeric adapters before using
                // the room's authoritative pitched direction.
                let direction = match native {
                    Some(Value::Vector(direction)) => Some(direction),
                    Some(_) => None,
                    None => context.authored_forward(entity),
                };
                Value::Vector(
                    direction
                        .filter(|parts| parts.iter().all(|part| part.is_finite()))
                        .ok_or(EvalError::UnavailableEntityDirection(entity))?,
                )
            }
            "STU_8CBDD81C" => {
                let operand = native_entity_operand(config, class).ok_or(
                    EvalError::MalformedConfig("expected one native body position operand"),
                )?;
                self.charge()?;
                let Value::Entity(entity) = self.config(operand, context, nested)? else {
                    return Err(EvalError::MalformedConfig("expected a bound entity"));
                };
                if !valid_entity(entity) {
                    return Err(EvalError::MalformedConfig("expected a bound entity"));
                }
                Value::Vector(
                    context
                        .authored_position(entity)
                        .filter(|pos| pos.iter().all(|axis| axis.is_finite()))
                        .ok_or(EvalError::UnavailableEntityPosition(entity))?,
                )
            }
            "STU_21A13145" => {
                let operand = native_entity_operand(config, class).ok_or(
                    EvalError::MalformedConfig("expected one native eye position entity operand"),
                )?;
                self.charge()?;
                let Value::Entity(entity) = self.config(operand, context, nested)? else {
                    return Err(EvalError::MalformedConfig("expected a bound entity"));
                };
                if !valid_entity(entity) {
                    return Err(EvalError::MalformedConfig("expected a bound entity"));
                }
                Value::Vector(
                    context
                        .body_eye_position(entity)
                        .filter(|parts| parts.iter().all(|part| part.is_finite()))
                        .ok_or(EvalError::UnavailableBodyEyePosition(entity))?,
                )
            }
            "STU_719E45B8" => {
                let Some((entity, hardpoint)) = fields
                    .get("m_entity")
                    .zip(fields.get("m_hardPoint"))
                    .filter(|_| native_fields(config, &["m_entity", "m_hardPoint"]))
                else {
                    return Err(EvalError::MalformedConfig(
                        "expected hardpoint entity and resource operands",
                    ));
                };
                let Some(Value::Asset(hardpoint)) =
                    literal_asset(hardpoint, "STUConfigVarHardPoint", "m_hardPoint", 0x0dc0)
                else {
                    return Err(EvalError::MalformedConfig(
                        "expected a literal model hardpoint",
                    ));
                };
                self.charge()?;
                let entity = match self.config(entity, context, nested)? {
                    Value::Entity(entity) if valid_entity(entity) => entity,
                    _ => return Err(EvalError::MalformedConfig("expected a bound entity")),
                };
                Value::Bool(
                    context
                        .body_has_hardpoint(entity, hardpoint)
                        .ok_or(EvalError::UnavailableBodyHardpoint { entity, hardpoint })?,
                )
            }
            "BodyActive" => {
                if fields.len() != 1 {
                    return Err(EvalError::MalformedConfig("BodyActive takes no operands"));
                }
                self.charge()?;
                Value::Bool(
                    context
                        .body_active()
                        .ok_or(EvalError::UnavailableBodyActivity)?,
                )
            }
            "EntityMatches" => self.entity_matches(fields, context, nested)?,
            "EntitiesInRadius" => self.entities_in_radius(fields, context, nested)?,
            "SegmentClear" => self.segment_clear(fields, context, nested)?,
            "RayCast" => self.ray_cast(fields, context, nested)?,
            "EntityRayCast" => self.entity_ray_cast(fields, context, nested)?,
            "StackAllows" => {
                let (variable, priority) = fields
                    .get("variable")
                    .zip(fields.get("priority"))
                    .filter(|_| fields.len() == 3)
                    .ok_or(EvalError::MalformedConfig(
                        "expected StackAllows variable and priority",
                    ))?;
                let variable = authored_stack_variable(variable)?;
                let priority = authored_stack_priority(priority)?;
                self.charge()?;
                let admitted = context
                    .would_stack_to_top(variable, priority)
                    .ok_or(EvalError::UnavailableStackAdmission)?;
                Value::Bool(admitted)
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
            "STU_66F3B89E" if native_shape(config, class, Some("m_entity")) => {
                let entity = self.config(child(config, "m_entity"), context, nested)?;
                let Some(Value::Entity(entity)) = first_scalar(&entity) else {
                    return Ok(Value::Null);
                };
                let [x, _, z] = context
                    .forward(*entity)
                    .ok_or(EvalError::UnavailableEntityDirection(*entity))?;
                let planar = x.hypot(z);
                if !planar.is_finite() || planar == 0.0 {
                    return Err(EvalError::UnavailableEntityDirection(*entity));
                }
                // The authored right vector uses body yaw, independent of pitch.
                Value::Vector([rounded(z / planar), 0.0, rounded(-x / planar)])
            }
            "STU_021D01CD" | "STU_C8C4C20D" | "STU_40AC5AE4"
                if native_shape(config, class, Some("m_entity")) =>
            {
                if let Some(value) = context.native(config) {
                    return Ok(value);
                }
                let value = self.config(child(config, "m_entity"), context, nested)?;
                let Some(Value::Entity(entity)) = first_scalar(&value) else {
                    // The native transform lookup has no entity for a position literal.
                    return Ok(Value::Null);
                };
                if class == "STU_40AC5AE4" {
                    // GetViewPitch, as reconstructed by the 104319 scene
                    // reference (driver 40AC5AE4): the entity's pitch in
                    // degrees, straight from its mover pose.
                    let pitch = context
                        .pitch(*entity)
                        .filter(|degrees| degrees.is_finite())
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                    return Ok(Value::Float(rounded(pitch)));
                }
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
            "STU_3E7F59D8" | "STU_955C40AE" | "STU_2E708EC1"
                if native_fields(config, &["m_548E5344", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                let action = self.config(child(config, "m_548E5344"), context, nested)?;
                Value::Integer(
                    wheel_slot(class, action)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                )
            }
            "STU_17B517B9" | "STU_DF7C7C63" | "STU_250DEABD" => {
                let (cosmetic, owner) = match class {
                    "STU_250DEABD"
                        if native_fields(config, &["m_target", "m_slot", "m_resourceKey"])
                            && child(config, "m_resourceKey").is_null()
                            && native_shape(
                                child(config, "m_target"),
                                "STU_33980609",
                                Some("m_entity"),
                            ) =>
                    {
                        (
                            EquippedCosmetic::EmoteAlias,
                            child(child(config, "m_target"), "m_entity"),
                        )
                    }
                    "STU_DF7C7C63" | "STU_17B517B9"
                        if native_fields(config, &["m_entity", "m_slot", "m_resourceKey"])
                            && child(config, "m_resourceKey").is_null() =>
                    {
                        let cosmetic = if class == "STU_17B517B9" {
                            EquippedCosmetic::Spray
                        } else {
                            EquippedCosmetic::EmoteBlendTree
                        };
                        (cosmetic, child(config, "m_entity"))
                    }
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                };
                let entity = self.config(owner, context, nested)?;
                let slot = self.config(child(config, "m_slot"), context, nested)?;
                match (entity, slot) {
                    (Value::Entity(entity), Value::Integer(slot)) if valid_entity(entity) => {
                        let asset = usize::try_from(slot)
                            .ok()
                            .filter(|&slot| slot < 4)
                            .map(|slot| {
                                context
                                    .equipped_cosmetic(entity, slot, cosmetic)
                                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))
                            })
                            .transpose()?
                            .unwrap_or(0);
                        Value::Asset(asset)
                    }
                    (Value::Null | Value::Entity(_), Value::Null | Value::Integer(_)) => {
                        Value::Asset(0)
                    }
                    _ => return Err(EvalError::MalformedConfig("equipped cosmetic operands")),
                }
            }
            "STU_1ECB66A0"
                if native_fields(config, &["m_548E5344", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                let action =
                    self.scalar_config(child(config, "m_548E5344"), context, nested, Value::Null)?;
                let texture = match action {
                    Value::Asset(action)
                        if crate::script_graph::is_communication_action(action) =>
                    {
                        context
                            .asset_texture(action)
                            .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?
                    }
                    _ => 0,
                };
                Value::Asset(texture)
            }
            "STU_4460419A" if native_fields(config, &["m_548E5344", "m_D929182B"]) => {
                let category = child(config, "m_D929182B");
                let pair = child(category, "m_value");
                if !native_shape(category, "STU_A1353B54", Some("m_value"))
                    || !native_fields(pair, &["m_type", "m_id"])
                    || helpers::class(pair) != Some("STU_BCD1C634")
                {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                let category_id = resource(child(pair, "m_id")).filter(|&id| id != 0);
                let category_kind = resource(child(pair, "m_type")).filter(|&kind| kind != 0);
                let (Some(kind), Some(id)) = (category_kind, category_id) else {
                    return Err(EvalError::MalformedConfig("asset category identifier"));
                };
                match self.config(child(config, "m_548E5344"), context, nested)? {
                    Value::Null | Value::Asset(0) => Value::Bool(false),
                    Value::Asset(asset) => Value::Bool(
                        context
                            .asset_has_category(asset, kind, id)
                            .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                    ),
                    _ => return Err(EvalError::MalformedConfig("asset category operand")),
                }
            }
            "STU_8556841E" => {
                if native_fields(config, &["m_entityDef", "m_resourceKey"])
                    && config.get("m_resourceKey") == Some(&Json::Null)
                    && config.get("m_entityDef") == Some(&Json::Null)
                {
                    Value::Asset(0)
                } else {
                    literal_asset(config, class, "m_entityDef", 0x0400)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?
                }
            }
            "STU_20DB8AB5"
                if native_fields(config, &["m_C1903771", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                if child(config, "m_C1903771").is_null() {
                    Value::Asset(0)
                } else {
                    literal_asset(config, class, "m_C1903771", 0x0690)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?
                }
            }
            // 3-component vector / Euler angle builder (`m_3DCE8650`, `m_447C0E2E`, `m_25252423`).
            // Used by Hanzo Wall Climb/Lunge (010C:109), Reinhardt (00F8:1), and cosmetic anchors.
            "STU_A4F22D6B"
                if native_fields(config, &["m_3DCE8650", "m_447C0E2E", "m_25252423"]) =>
            {
                let x = to_float(&self.config(child(config, "m_3DCE8650"), context, nested)?);
                let y = to_float(&self.config(child(config, "m_447C0E2E"), context, nested)?);
                let z = to_float(&self.config(child(config, "m_25252423"), context, nested)?);
                Value::Vector([rounded(x), rounded(y), rounded(z)])
            }
            // Hardpoint world position / basis vectors for any entity (self, hooked targets, pets).
            "STU_FC99C77E" => {
                let target = self.config_with_initial(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let pos = match first_scalar(&target) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => context
                        .body_eye_position(*entity)
                        .or_else(|| context.authored_position(*entity))
                        .unwrap_or([0.0; 3]),
                    _ => [0.0; 3],
                };
                Value::Vector(pos.map(rounded))
            }
            "STU_24087C9E" | "STU_021D01CD" => {
                let target = self.config_with_initial(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let fwd = match first_scalar(&target) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => {
                        context.authored_forward(*entity).unwrap_or([0.0, 0.0, 1.0])
                    }
                    _ => [0.0, 0.0, 1.0],
                };
                Value::Vector(fwd.map(rounded))
            }
            "STU_EE66D6C3" => {
                let _ = self.config_with_initial(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                Value::Vector([0.0, 1.0, 0.0])
            }
            "STU_66F3B89E" => {
                let target = self.config_with_initial(
                    child(config, "m_entity"),
                    context,
                    nested,
                    Value::Entity(0),
                )?;
                let fwd = match first_scalar(&target) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => {
                        context.authored_forward(*entity).unwrap_or([0.0, 0.0, 1.0])
                    }
                    _ => [0.0, 0.0, 1.0],
                };
                let len = fwd[0].hypot(fwd[2]);
                let right = if len > 1e-6 {
                    [fwd[2] / len, 0.0, -fwd[0] / len]
                } else {
                    [1.0, 0.0, 0.0]
                };
                Value::Vector(right.map(rounded))
            }
            "STUConfigVarAnimation"
                if native_fields(config, &["m_animation", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                if child(config, "m_animation").is_null() {
                    Value::Asset(0)
                } else {
                    literal_asset(config, class, "m_animation", 0x0a00)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?
                }
            }
            "STUConfigVarBone"
                if native_fields(config, &["m_bone", "m_resourceKey"])
                    && child(config, "m_resourceKey").is_null() =>
            {
                if child(config, "m_bone").is_null() {
                    Value::Asset(0)
                } else {
                    literal_asset(config, class, "m_bone", 0x0880)
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?
                }
            }
            // Fixed simulation step duration in seconds (16ms retail tick = 0.016s).
            // Verified in Moira 0F76 (`3.0 * STU_4B02509E() / 0.016`) and Brigitte 1248
            // (`Wait(var[10349] + STU_4B02509E())`).
            "STU_4B02509E" if native_shape(config, class, None) => Value::Float(0.016),
            // Target stealth/invisibility check against observer (`IsStealthedFrom(entity, observer)`).
            // Used by Zenyatta Discord Orb (0176) and targeted abilities to ignore invisible enemies.
            "STU_7C005F8F" if native_fields(config, &["m_entity", "m_82B6345B"]) => {
                let _ = self.config(child(config, "m_entity"), context, nested)?;
                let _ = self.config(child(config, "m_82B6345B"), context, nested)?;
                Value::Bool(false)
            }
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
            "STU_951CAB4C" if native_fields(config, &["m_814F8EC3", "m_condition"]) => {
                self.filter_array(config, context, nested)?
            }
            "STU_588FF314"
                if native_fields(
                    config,
                    &[
                        "m_entity",
                        "m_64E06CA4",
                        "m_1FDD7084",
                        "m_AEE599AA",
                        "m_hardPoint",
                    ],
                ) =>
            {
                self.transform_entity_vector(config, context, nested)?
            }
            "STU_7757D403" => self.authored_pairs(config, context, nested)?,
            "STU_B5A0CAF0" => context.owner(),
            "STU_25ECB55F" => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                let (tag, entity) = native_target_tag_operand(config).ok_or_else(unsupported)?;
                match self.config(entity, context, nested)? {
                    Value::Entity(entity) if valid_entity(entity) => {
                        match context.has_target_tag(entity, tag) {
                            Some(present) => Value::Bool(present),
                            // scene.py `_has_target_tag` answers NotImplemented for an
                            // entity whose tag component never arrived; the reference
                            // then falls through to `default_native`, which warns and
                            // writes null under the normal policy.
                            None if !context.strict() => Value::Null,
                            None => return Err(unsupported()),
                        }
                    }
                    Value::Entity(_) | Value::Null => Value::Bool(false),
                    Value::Array(items) => {
                        let mut any_match = false;
                        for item in items {
                            if let Value::Entity(entity) = item {
                                if valid_entity(entity)
                                    && context.has_target_tag(entity, tag).unwrap_or(false)
                                {
                                    any_match = true;
                                    break;
                                }
                            }
                        }
                        Value::Bool(any_match)
                    }
                    _ => return Err(unsupported()),
                }
            }
            "STU_AC829876" => {
                return match self.entity_filter(config, context, nested) {
                    Err(error @ EvalError::UnsupportedConfig(_)) => match context.native(config) {
                        Some(value @ Value::Bool(_)) => Ok(value),
                        // Every fall-through the reference `_filter_entity` reports as
                        // NotImplemented lands on the same `default_native` null
                        // (scene.py:2700..2706) under the normal policy.
                        _ if !context.strict() => Ok(Value::Null),
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
            "STU_4A101787" | "STU_E95A9C77" | "STU_555DD49F"
                if native_shape(config, class, None) =>
            {
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
                let entity = match self.config(child(config, operand), context, nested)? {
                    Value::Entity(entity) => entity,
                    Value::Null if relation == EntityRelation::AiControlEntity => 0,
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                };
                let related = context
                    .related_entity(relation, entity)
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Entity(related)
            }
            "STU_2B2E2F6D" => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                if !native_fields(config, &["m_81A364DE", "m_7BDB02D1"])
                    || child(config, "m_7BDB02D1").as_u64() != Some(0)
                {
                    return Err(unsupported());
                }
                let Value::Entity(entity) =
                    self.config(child(config, "m_81A364DE"), context, nested)?
                else {
                    return Err(unsupported());
                };
                if entity == 0 {
                    return Ok(Value::Entity(0));
                }
                if !valid_entity(entity) {
                    return Err(unsupported());
                }
                let creator = context
                    .related_entity(EntityRelation::CreatorBody, entity)
                    .filter(|&creator| creator == 0 || (valid_entity(creator) && creator != entity))
                    .ok_or_else(unsupported)?;
                Value::Entity(creator)
            }
            "STU_967463AB" => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                if !native_fields(config, &["m_92D12BE6", "m_C8655030"]) {
                    return Err(unsupported());
                }
                let owner = config.get("m_92D12BE6").ok_or_else(unsupported)?;
                let definition = config.get("m_C8655030").ok_or_else(unsupported)?;
                let Value::Asset(definition) =
                    self.scalar_config(definition, context, nested, Value::Asset(0))?
                else {
                    return Err(unsupported());
                };
                if definition >> 48 != 0x0400 {
                    return Err(unsupported());
                }
                match self.scalar_config(owner, context, nested, Value::Entity(0))? {
                    Value::Entity(0) | Value::Null => initial,
                    Value::Entity(owner) if valid_entity(owner) => context
                        .native(config)
                        .filter(|value| matches!(value, Value::Integer(count) if *count >= 0))
                        .ok_or_else(unsupported)?,
                    _ => return Err(unsupported()),
                }
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
                    Value::Entity(_) | Value::Integer(0) | Value::Null => Value::Float(0.0),
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
                let health = match &value {
                    Value::Array(items)
                        if items.is_empty()
                            && matches!(dynamic(target), Ok(Some(variable)) if variable.id != 0) =>
                    {
                        0.0
                    }
                    _ => first_scalar(&value)
                        .and_then(|value| {
                            health_amount(target, value, None, class == "STU_7D7D2470", context)
                        })
                        .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?,
                };
                Value::Float(health)
            }
            "STU_9D240539" => {
                let _ = self.config(child(config, "m_entity"), context, nested)?;
                Value::Null
            }
            "STU_C1F83245" => {
                let target = child(config, "m_entity");
                let value = self.config(target, context, nested)?;
                let entity = match first_scalar(&value) {
                    Some(Value::Entity(entity)) if valid_entity(*entity) => Some(*entity),
                    _ => None,
                };
                let pos = entity
                    .and_then(|entity| {
                        context
                            .body_eye_position(entity)
                            .or_else(|| context.authored_position(entity))
                    })
                    .unwrap_or([0.0; 3]);
                Value::Vector(pos)
            }
            "STU_D0E0DA18" => {
                let value = self.config(child(config, "m_3ABD589C"), context, nested)?;
                let angles = match value {
                    Value::Vector(v) if v.iter().all(|a| a.is_finite()) => {
                        let len = v[0].hypot(v[1]).hypot(v[2]);
                        if len > 1e-9 {
                            let yaw = rounded(v[0].atan2(v[2]).to_degrees());
                            let pitch = rounded((v[1] / len).clamp(-1.0, 1.0).asin().to_degrees());
                            [yaw, pitch, 0.0]
                        } else {
                            [0.0; 3]
                        }
                    }
                    _ => [0.0; 3],
                };
                Value::Vector(angles)
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
            "STU_783B4CF0"
                if !child(config, "m_7BE02F89").is_null()
                    && native_fields(config, &["m_entity", "m_7E8B666C", "m_7BE02F89"])
                    && native_shape(child(config, "m_entity"), "STU_B5A0CAF0", None) =>
            {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                let selector = child(config, "m_7E8B666C");
                if !native_shape(selector, "STU_297E1A30", Some("m_7E8B666C"))
                    || child(selector, "m_7E8B666C").as_u64() != Some(2)
                {
                    return Err(unsupported());
                }
                let identifier =
                    named_shield_identifier(child(config, "m_7BE02F89")).ok_or_else(unsupported)?;
                let amount = context
                    .temporary_shield_current(identifier)
                    .ok_or_else(unsupported)?;
                Value::Float(rounded(amount))
            }
            "STU_783B4CF0" | "STU_68883EAC" => {
                let unsupported = || EvalError::UnsupportedConfig(class.into());
                let shield_id = child(config, "m_7BE02F89");
                let empty_shield_id = shield_id.is_null()
                    || (self::class(shield_id) == Some("STUConfigVarIdentifier")
                        && native_fields(shield_id, &["m_identifier", "m_resourceKey"])
                        && child(shield_id, "m_identifier").is_null()
                        && child(shield_id, "m_resourceKey").is_null());
                if !native_fields(config, &["m_entity", "m_7E8B666C", "m_7BE02F89"])
                    || !empty_shield_id
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
                let target = child(config, "m_entity");
                let value = self.config(target, context, nested)?;
                let health =
                    health_amount(target, &value, Some(kind), class == "STU_68883EAC", context)
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
                let entity = match self.config(target, context, nested)? {
                    Value::Entity(entity) => entity,
                    Value::Null => 0,
                    _ => return Err(EvalError::UnsupportedConfig(class.into())),
                };
                if entity & 0x1fff_ffff == 0x1fff_ffff {
                    return Err(EvalError::UnsupportedConfig(class.into()));
                }
                let scale = context
                    .body_scale(entity)
                    .ok_or_else(|| EvalError::UnsupportedConfig(class.into()))?;
                Value::Float(scale)
            }
            "STU_88687E54" => {
                // Ground-below-point check (used by Doomfist Seismic Slam `0x0C51:109`,
                // `0x0B2F:82`, `0x142C:105`): true if terrain lies within `m_83C99CE5`
                // meters beneath `m_A8D12F28`.
                let origin = match self.scalar_config(
                    child(config, "m_A8D12F28"),
                    context,
                    nested,
                    Value::Null,
                )? {
                    Value::Vector(pos) if pos.iter().all(|c| c.is_finite()) => Some(pos),
                    Value::Entity(e) if valid_entity(e) => context.authored_position(e),
                    _ => None,
                };
                let max_dist = match self.scalar_config(
                    child(config, "m_83C99CE5"),
                    context,
                    nested,
                    Value::Float(50.0),
                )? {
                    Value::Float(d) if d.is_finite() && d > 0.0 => d,
                    Value::Integer(d) if d > 0 => d as f64,
                    _ => 50.0,
                };
                let grounded = origin.is_some_and(|pos| {
                    context
                        .spatial_ray_cast(pos, [0.0, -1.0, 0.0], max_dist)
                        .is_none_or(|hit| hit.is_some())
                });
                Value::Bool(grounded)
            }
            "STU_295F1CDE" if native_shape(config, class, None) => {
                // 104319 CC9A80/C1BBB0: barrier health multiplier for this actor's ruleset.
                self.charge()?;
                match context.optional_ruleset(0x254b) {
                    None | Some(Value::Null) => Value::Float(1.0),
                    Some(value @ (Value::Integer(_) | Value::Float(_)))
                        if rounded(to_float(&value)).is_finite() =>
                    {
                        Value::Float(rounded(to_float(&value)))
                    }
                    _ => {
                        return Err(EvalError::MalformedConfig(
                            "invalid barrier health multiplier",
                        ));
                    }
                }
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
                    // Every other unhandled native is a world the context cannot
                    // answer. The reference `default_native` (expr.py:349) warns
                    // and writes a typed null under the normal policy; only a
                    // strict component turns it into a report.
                    _ if !context.strict() => Value::Null,
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
        let operand = self.config(child(config, "m_entity"), context, depth)?;
        let entity = match first_scalar(&operand) {
            Some(Value::Entity(entity)) => *entity,
            Some(Value::Null) | None => return Ok(Value::Bool(false)),
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
        if let Some(policy) = policy.filter(|policy| policy.relation != FilterRelation::Any) {
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
                FilterRelation::Any => false,
                FilterRelation::Hostile => friendly,
                FilterRelation::Friendly => !friendly,
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

    fn entity_matches(
        &mut self,
        fields: &serde_json::Map<String, Json>,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let filter = fields.get("filter");
        let entity = fields
            .get("entity")
            .filter(|_| fields.len() == 2 + usize::from(filter.is_some()))
            .ok_or(EvalError::MalformedConfig(
                "expected an entity and optional filter",
            ))?;
        self.charge()?;
        let entity = self.room_filter_entity(entity, context, depth, "expected a bound entity")?;
        let filter = self.room_entity_filter(filter, context, depth)?;
        let sampled_target = context.settled_health_target(entity);
        let settled_status = sampled_target.and_then(|facts| facts.room_status);
        let settled_tags = sampled_target
            .filter(|_| filter.tags.is_some())
            .map(|facts| facts.tags.clone());
        let entities = context
            .room_entities()
            .ok_or(EvalError::UnavailableRoomEntityFacts)?;
        let source = filter
            .relation
            .map(|(source, relation)| {
                let facts = entities
                    .get(&source)
                    .ok_or(EvalError::UnavailableRoomEntityFacts)?;
                Ok::<_, EvalError>((source, facts.team, relation))
            })
            .transpose()?;
        let live_target = entities.get(&entity);
        let Some((target_team, target_alive)) = settled_status
            .map(|status| (status.team, status.alive))
            .or_else(|| live_target.map(|facts| (facts.team, facts.alive)))
        else {
            return Ok(Value::Bool(false));
        };
        if source.is_some_and(|(source, team, relation)| {
            source == entity || !relation.matches(team, target_team)
        }) {
            return Ok(Value::Bool(false));
        }
        let tags = settled_tags
            .as_ref()
            .or_else(|| live_target.map(|facts| &facts.tags));
        self.room_facts_match(&filter, target_alive, tags)
            .map(Value::Bool)
    }

    fn room_filter_entity(
        &mut self,
        expression: &Json,
        context: &mut impl Context,
        depth: usize,
        error: &'static str,
    ) -> Result<u32, EvalError> {
        match self.config(expression, context, depth)? {
            Value::Entity(entity) if valid_entity(entity) => Ok(entity),
            _ => Err(EvalError::MalformedConfig(error)),
        }
    }

    fn room_entity_filter(
        &mut self,
        filter: Option<&Json>,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<RoomEntityFilter, EvalError> {
        let Some(filter) = filter else {
            return Ok(RoomEntityFilter::default());
        };
        let malformed = EvalError::MalformedConfig("expected a relation and source filter");
        let fields = filter.as_object().ok_or_else(|| malformed.clone())?;
        if fields
            .keys()
            .any(|key| !matches!(key.as_str(), "relation" | "source" | "alive" | "tags"))
        {
            return Err(malformed);
        }
        let relation = match (fields.get("relation"), fields.get("source")) {
            (None, None) => None,
            (Some(relation), Some(source)) => {
                let relation = match relation.as_str() {
                    Some("ally") => TeamRelation::Ally,
                    Some("opponent") => TeamRelation::Opponent,
                    _ => return Err(malformed),
                };
                let source = self.room_filter_entity(
                    source,
                    context,
                    depth,
                    "expected a bound filter source",
                )?;
                Some((source, relation))
            }
            _ => return Err(malformed),
        };
        let alive = fields
            .get("alive")
            .map(|alive| {
                alive.as_bool().ok_or(EvalError::MalformedConfig(
                    "expected a boolean alive filter",
                ))
            })
            .transpose()?;
        let tags = fields
            .get("tags")
            .map(|tags| self.room_tag_filter(tags))
            .transpose()?;
        Ok(RoomEntityFilter {
            relation,
            alive,
            tags,
        })
    }

    fn room_tag_filter(&mut self, tags: &Json) -> Result<RoomTagFilter, EvalError> {
        let mut result = RoomTagFilter::default();
        let tags = tags.as_object().ok_or(EvalError::MalformedConfig(
            "expected a room tag filter object",
        ))?;
        for (key, values) in tags {
            let clause = match key.as_str() {
                "all" => &mut result.all,
                "any" => &mut result.any,
                "none" => &mut result.none,
                _ => {
                    return Err(EvalError::MalformedConfig(
                        "expected all, any or none tag lists",
                    ));
                }
            };
            let values = values.as_array().ok_or(EvalError::MalformedConfig(
                "expected an array of room tag GUIDs",
            ))?;
            for tag in values {
                self.charge()?;
                let tag =
                    asset_guid(tag)
                        .filter(|&tag| tag != 0)
                        .ok_or(EvalError::MalformedConfig(
                            "expected a nonzero room tag GUID",
                        ))?;
                clause.push(tag);
            }
        }
        Ok(result)
    }

    fn room_facts_match(
        &mut self,
        filter: &RoomEntityFilter,
        entity_alive: bool,
        entity_tags: Option<&crate::script_init::TargetTagSnapshot>,
    ) -> Result<bool, EvalError> {
        if filter.alive.is_some_and(|alive| alive != entity_alive) {
            return Ok(false);
        }
        let Some(tags) = &filter.tags else {
            return Ok(true);
        };
        let entity_tags = entity_tags.ok_or(EvalError::UnavailableRoomEntityFacts)?;
        let mut unresolved = false;
        for (clause, tags) in [
            (RoomTagClause::All, &tags.all),
            (RoomTagClause::Any, &tags.any),
            (RoomTagClause::None, &tags.none),
        ] {
            match self.room_tag_clause(clause, tags, entity_tags)? {
                Some(false) => return Ok(false),
                None => unresolved = true,
                Some(true) => (),
            }
        }
        if unresolved {
            Err(EvalError::UnavailableRoomEntityFacts)
        } else {
            Ok(true)
        }
    }

    fn room_tag_clause(
        &mut self,
        clause: RoomTagClause,
        tags: &[u64],
        facts: &crate::script_init::TargetTagSnapshot,
    ) -> Result<Option<bool>, EvalError> {
        if tags.is_empty() {
            return Ok(Some(true));
        }
        let mut unresolved = false;
        for &tag in tags {
            self.charge()?;
            match (clause, facts.presence(tag)) {
                (RoomTagClause::All, Some(false)) | (RoomTagClause::None, Some(true)) => {
                    return Ok(Some(false));
                }
                (RoomTagClause::Any, Some(true)) => return Ok(Some(true)),
                (_, None) => unresolved = true,
                _ => (),
            }
        }
        Ok((!unresolved).then_some(!matches!(clause, RoomTagClause::Any)))
    }

    fn entities_in_radius(
        &mut self,
        fields: &serde_json::Map<String, Json>,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        self.select_in_radius(fields, context, depth, false, |entity, _, _| {
            Value::Entity(entity)
        })
        .map(Value::Array)
    }

    fn segment_clear(
        &mut self,
        fields: &serde_json::Map<String, Json>,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let Some((origin, target)) = fields
            .get("origin")
            .zip(fields.get("target"))
            .filter(|_| fields.len() == 3)
        else {
            return Err(EvalError::MalformedConfig(
                "expected SegmentClear origin and target",
            ));
        };
        let origin = self.segment_endpoint(origin, context, depth)?;
        let target = self.segment_endpoint(target, context, depth)?;
        self.spatial_line_clear(context, origin, target)
            .map(Value::Bool)
    }

    fn clamp_direction(
        &mut self,
        config: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let mut angles = [[0.0; 2]; 2];
        for (field, limit_basis) in ["m_32B00625", "m_3661BC45"].into_iter().zip(&mut angles) {
            let Value::Vector([x, y, z]) = self.config(child(config, field), context, depth)?
            else {
                return Ok(Value::Null);
            };
            let norm_squared = x * x + y * y + z * z;
            if ![x, y, z].into_iter().all(f64::is_finite)
                || norm_squared <= 100.0 * f64::from(f32::MIN_POSITIVE)
            {
                return Ok(Value::Null);
            }
            // Match the Python reference's near-unit normalization threshold.
            let y = if (norm_squared - 1.0).abs() > 0.008 {
                rounded(y / norm_squared.sqrt())
            } else {
                y
            };
            *limit_basis = [rounded(x.atan2(z)), rounded((-y).clamp(-1.0, 1.0).asin())];
        }

        let limits = [
            self.config(child(config, "m_5023B9B0"), context, depth)?,
            self.config(child(config, "m_E26E5E09"), context, depth)?,
        ];
        let [mut direction, reference] = angles;
        for ((angle, center), limit) in direction.iter_mut().zip(reference).zip(limits) {
            let degrees = match limit {
                Value::Integer(value @ 0..) => value as f64,
                Value::Float(value) if value.is_finite() && value >= 0.0 => value,
                _ => return Ok(Value::Null),
            };
            let limit = degrees.to_radians();
            let delta = *angle - center;
            let turn = std::f64::consts::TAU;
            let shortest = delta - turn * (delta / turn).round_ties_even();
            if shortest.abs() > limit {
                *angle = rounded(center + limit.copysign(shortest));
            }
        }

        let [yaw, pitch] = direction;
        let horizontal = pitch.cos();
        Ok(Value::Vector([
            rounded(yaw.sin() * horizontal),
            rounded(-pitch.sin()),
            rounded(yaw.cos() * horizontal),
        ]))
    }

    fn ray_cast(
        &mut self,
        fields: &serde_json::Map<String, Json>,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let Some(((origin, direction), max_distance)) = fields
            .get("origin")
            .zip(fields.get("direction"))
            .zip(fields.get("max_distance"))
            .filter(|_| fields.len() == 4)
        else {
            return Err(EvalError::MalformedConfig(
                "expected RayCast origin, direction, and max_distance",
            ));
        };
        let SpatialRay {
            origin,
            direction,
            max_distance,
        } = self.ray_parameters(origin, direction, max_distance, context, depth)?;
        self.charge()?;
        let hit = context
            .spatial_ray_cast(origin, direction, max_distance)
            .ok_or(EvalError::UnavailableSpatialRayCast)?;
        let Some(hit) = hit else {
            return Ok(Value::Array(Vec::new()));
        };
        if !hit.distance.is_finite()
            || hit.distance <= 0.0
            || hit.distance > max_distance
            || !hit.point.iter().all(|axis| axis.is_finite())
            || SpatialCone::scale_direction(hit.normal).is_none()
        {
            return Err(EvalError::UnavailableSpatialRayCast);
        }
        Ok(Value::Array(vec![
            Value::Float(hit.distance),
            Value::Vector(hit.point),
            Value::Vector(hit.normal),
        ]))
    }

    fn ray_parameters(
        &mut self,
        origin: &Json,
        direction: &Json,
        max_distance: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<SpatialRay, EvalError> {
        let origin = self.segment_endpoint(origin, context, depth)?;
        let direction_error = EvalError::MalformedConfig("expected a finite nonzero ray direction");
        let Value::Vector(direction) = self.config(direction, context, depth)? else {
            return Err(direction_error);
        };
        let scaled = SpatialCone::scale_direction(direction).ok_or(direction_error)?;
        let magnitude = scaled.iter().map(|axis| axis * axis).sum::<f64>().sqrt();
        let direction = scaled.map(|axis| axis / magnitude);
        let max_distance = match self.config(max_distance, context, depth)? {
            Value::Float(value) => value,
            Value::Integer(value) => value as f64,
            _ => {
                return Err(EvalError::MalformedConfig(
                    "expected a finite positive ray distance",
                ));
            }
        };
        if !max_distance.is_finite() || max_distance <= 0.0 {
            return Err(EvalError::MalformedConfig(
                "expected a finite positive ray distance",
            ));
        }
        Ok(SpatialRay {
            origin,
            direction,
            max_distance,
        })
    }

    fn entity_ray_cast(
        &mut self,
        fields: &serde_json::Map<String, Json>,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<Value, EvalError> {
        let filter = fields.get("filter");
        let Some(((origin, direction), max_distance)) = fields
            .get("origin")
            .zip(fields.get("direction"))
            .zip(fields.get("max_distance"))
            .filter(|_| fields.len() == 4 + usize::from(filter.is_some()))
        else {
            return Err(EvalError::MalformedConfig(
                "expected EntityRayCast origin, direction, max_distance, and optional filter",
            ));
        };
        let SpatialRay {
            origin,
            direction,
            max_distance,
        } = self.ray_parameters(origin, direction, max_distance, context, depth)?;
        let filter = self.room_entity_filter(filter, context, depth)?;
        let mut candidates = self.select_room_candidates(&filter, context, |_| Some(()))?;
        candidates.sort_unstable_by_key(|(candidate, _)| candidate.entity);
        for group in candidates.chunk_by(|a, b| a.0.entity == b.0.entity) {
            let candidate = group[0].0;
            if group
                .iter()
                .any(|(row, _)| row.position != candidate.position || row.team != candidate.team)
            {
                return Err(EvalError::UnavailableSpatialCandidates);
            }
        }
        candidates.dedup_by_key(|(candidate, _)| candidate.entity);
        let targets = candidates
            .into_iter()
            .map(|(candidate, _)| candidate.entity)
            .collect::<Vec<_>>();
        for _ in &targets {
            self.charge()?;
        }
        self.charge()?;
        let hit = context
            .spatial_actor_ray_cast(origin, direction, max_distance, &targets)
            .ok_or(EvalError::UnavailableSpatialRayCast)?;
        let Some(hit) = hit else {
            return Ok(Value::Array(Vec::new()));
        };
        if !hit.distance.is_finite()
            || hit.distance < 0.0
            || hit.distance > max_distance
            || targets.binary_search(&hit.entity).is_err()
        {
            return Err(EvalError::UnavailableSpatialRayCast);
        }
        let point = std::array::from_fn(|axis| origin[axis] + direction[axis] * hit.distance);
        if !point.iter().all(|part| part.is_finite()) {
            return Err(EvalError::UnavailableSpatialRayCast);
        }
        Ok(Value::Array(vec![
            Value::Entity(hit.entity),
            Value::Float(hit.distance),
            Value::Vector(point),
        ]))
    }

    fn segment_endpoint(
        &mut self,
        operand: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<[f64; 3], EvalError> {
        match self.config(operand, context, depth)? {
            Value::Vector(endpoint) if endpoint.iter().all(|axis| axis.is_finite()) => Ok(endpoint),
            _ => Err(EvalError::MalformedConfig(
                "expected a finite segment endpoint",
            )),
        }
    }

    fn spatial_line_clear(
        &mut self,
        context: &mut impl Context,
        origin: [f64; 3],
        target: [f64; 3],
    ) -> Result<bool, EvalError> {
        self.charge()?;
        context
            .spatial_line_clear(origin, target)
            .ok_or(EvalError::UnavailableSpatialVisibility)
    }

    pub(super) fn radial_health_changes(
        &mut self,
        area: &Json,
        delta: &Json,
        edge_scale: Option<&Json>,
        context: &mut impl Context,
    ) -> Result<Vec<(u32, f64)>, EvalError> {
        let area = area
            .as_object()
            .filter(|fields| fields.get("$").and_then(Json::as_str) == Some("EntitiesInRadius"))
            .ok_or(EvalError::MalformedConfig(
                "expected an EntitiesInRadius area",
            ))?;
        let delta = self
            .finite_operand(delta, context)?
            .filter(|delta| *delta != 0.0)
            .ok_or(EvalError::MalformedConfig(
                "expected a finite nonzero radial health delta",
            ))?;
        let edge_scale = match edge_scale {
            Some(operand) => self
                .finite_operand(operand, context)?
                .filter(|edge| (0.0..=1.0).contains(edge))
                .ok_or(EvalError::MalformedConfig(
                    "expected a radial health edge scale from zero to one",
                ))?,
            None => 1.0,
        };
        let mut changes =
            self.select_in_radius(area, context, 1, true, |entity, distance, radius| {
                let scale = if radius == 0.0 {
                    1.0
                } else {
                    let fraction = distance / radius;
                    (1.0 - fraction) + fraction * edge_scale
                };
                (entity, delta * scale)
            })?;
        changes.retain(|&(_, change)| change != 0.0);
        Ok(changes)
    }

    fn finite_operand(
        &mut self,
        operand: &Json,
        context: &mut impl Context,
    ) -> Result<Option<f64>, EvalError> {
        Ok(match self.config(operand, context, 0)? {
            Value::Integer(value) => Some(value as f64),
            Value::Float(value) if value.is_finite() => Some(value),
            _ => None,
        })
    }

    fn select_in_radius<T>(
        &mut self,
        fields: &serde_json::Map<String, Json>,
        context: &mut impl Context,
        depth: usize,
        require_consistent_distance: bool,
        mut project: impl FnMut(u32, f64, f64) -> T,
    ) -> Result<Vec<T>, EvalError> {
        let filter = fields.get("filter");
        let cone = fields.get("cone");
        let visibility = fields.get("line_of_sight");
        let expected_fields = 3
            + usize::from(filter.is_some())
            + usize::from(cone.is_some())
            + usize::from(visibility.is_some());
        let Some((center, radius)) = fields
            .get("center")
            .zip(fields.get("radius"))
            .filter(|_| fields.len() == expected_fields)
        else {
            return Err(EvalError::MalformedConfig("expected a center and radius"));
        };
        let line_of_sight = match visibility {
            None => false,
            Some(Json::Bool(required)) => *required,
            _ => {
                return Err(EvalError::MalformedConfig(
                    "expected a boolean line_of_sight",
                ));
            }
        };
        self.charge()?;
        let center = match self.config(center, context, depth)? {
            Value::Vector(center) if center.iter().all(|axis| axis.is_finite()) => center,
            _ => {
                return Err(EvalError::MalformedConfig(
                    "expected a finite center vector",
                ));
            }
        };
        let radius = match self.config(radius, context, depth)? {
            Value::Integer(value) if value >= 0 => value as f64,
            Value::Float(value) if value.is_finite() && value >= 0.0 => value,
            _ => {
                return Err(EvalError::MalformedConfig(
                    "expected a finite nonnegative radius",
                ));
            }
        };
        let filter = self.room_entity_filter(filter, context, depth)?;

        let cone = cone
            .map(|cone| self.spatial_cone(cone, context, depth))
            .transpose()?;
        let mut matches = self.select_room_candidates(&filter, context, |candidate| {
            let offset = std::array::from_fn(|axis| candidate.position[axis] - center[axis]);
            let [dx, dy, dz] = offset;
            let distance = dx.hypot(dy).hypot(dz);
            (distance <= radius && cone.as_ref().is_none_or(|cone| cone.contains(offset)))
                .then_some(distance)
        })?;
        matches.sort_unstable_by_key(|(candidate, _)| candidate.entity);
        if !line_of_sight {
            if require_consistent_distance {
                for group in matches.chunk_by(|a, b| a.0.entity == b.0.entity) {
                    let distance = group[0].1;
                    if group.iter().any(|(_, other)| *other != distance) {
                        return Err(EvalError::UnavailableSpatialCandidates);
                    }
                }
            }
            matches.dedup_by_key(|(candidate, _)| candidate.entity);
            return Ok(matches
                .into_iter()
                .map(|(candidate, distance)| project(candidate.entity, distance, radius))
                .collect());
        }

        // Own only the surviving segments before calling into the host again.
        let mut segments = Vec::new();
        for group in matches.chunk_by(|a, b| a.0.entity == b.0.entity) {
            let (candidate, distance) = group[0];
            if group
                .iter()
                .any(|(row, _)| row.position != candidate.position)
            {
                return Err(EvalError::UnavailableSpatialVisibility);
            }
            segments.push((candidate.entity, candidate.position, distance));
        }
        let mut hits = Vec::new();
        for (entity, position, distance) in segments {
            if self.spatial_line_clear(context, center, position)? {
                hits.push(project(entity, distance, radius));
            }
        }
        Ok(hits)
    }

    fn select_room_candidates<T>(
        &mut self,
        filter: &RoomEntityFilter,
        context: &mut impl Context,
        mut select: impl FnMut(SpatialCandidate) -> Option<T>,
    ) -> Result<Vec<(SpatialCandidate, T)>, EvalError> {
        let candidates = context
            .spatial_candidates()
            .ok_or(EvalError::UnavailableSpatialCandidates)?;
        let mut source_team: Option<Option<u8>> = None;
        let mut matches = Vec::new();
        for &candidate in candidates {
            let SpatialCandidate {
                entity,
                position,
                team,
            } = candidate;
            self.charge()?;
            if !valid_entity(entity) || !position.iter().all(|axis| axis.is_finite()) {
                return Err(EvalError::UnavailableSpatialCandidates);
            }
            if filter.relation.is_some_and(|(source, _)| entity == source) {
                if source_team
                    .replace(team)
                    .is_some_and(|previous| previous != team)
                {
                    return Err(EvalError::UnavailableSpatialCandidates);
                }
                continue;
            }
            if let Some(sample) = select(candidate) {
                matches.push((candidate, sample));
            }
        }
        if let Some((_, relation)) = filter.relation {
            let source_team = source_team.ok_or(EvalError::UnavailableSpatialCandidates)?;
            matches.retain(|(candidate, _)| relation.matches(source_team, candidate.team));
        }
        if filter.needs_facts() {
            let entities = context
                .room_entities()
                .ok_or(EvalError::UnavailableRoomEntityFacts)?;
            if filter
                .relation
                .is_some_and(|(source, _)| !entities.get(&source).is_some_and(|facts| facts.alive))
            {
                return Err(EvalError::UnavailableRoomEntityFacts);
            }
            let mut filtered = Vec::with_capacity(matches.len());
            for (candidate, sample) in matches {
                let facts = entities
                    .get(&candidate.entity)
                    .ok_or(EvalError::UnavailableRoomEntityFacts)?;
                if facts.alive && self.room_facts_match(filter, facts.alive, Some(&facts.tags))? {
                    filtered.push((candidate, sample));
                }
            }
            matches = filtered;
        }
        Ok(matches)
    }

    fn spatial_cone(
        &mut self,
        cone: &Json,
        context: &mut impl Context,
        depth: usize,
    ) -> Result<SpatialCone, EvalError> {
        let Some((direction, half_angle)) = cone
            .as_object()
            .filter(|cone| cone.len() == 2)
            .and_then(|cone| cone.get("direction").zip(cone.get("half_angle_degrees")))
        else {
            return Err(EvalError::MalformedConfig(
                "expected a cone direction and half angle",
            ));
        };
        let direction = match self.config(direction, context, depth)? {
            Value::Vector(vector) => SpatialCone::scale_direction(vector),
            _ => None,
        }
        .ok_or(EvalError::MalformedConfig(
            "expected a finite nonzero cone direction",
        ))?;
        let invalid_angle =
            EvalError::MalformedConfig("expected a cone half angle from 0 to 180 degrees");
        let degrees = match self.config(half_angle, context, depth)? {
            Value::Integer(value) => value as f64,
            Value::Float(value) => value,
            _ => return Err(invalid_angle),
        };
        if !(0.0..=180.0).contains(&degrees) {
            return Err(invalid_angle);
        }
        let angle = degrees.to_radians();
        // Allow a relative rounding margin at an inclusive boundary.
        let angle_limit = angle + angle * (4.0 * f64::EPSILON);
        Ok(SpatialCone {
            direction,
            angle_limit,
        })
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
                19 => {
                    let start = stack
                        .len()
                        .checked_sub(usize::from(arg) * 2)
                        .ok_or_else(|| malformed(pc, "expression stack underflow"))?;
                    let mut items = stack.split_off(start).into_iter();
                    let pairs = std::iter::from_fn(|| {
                        let key = Value::Integer(to_int(&items.next()?, 0));
                        Some((key, items.next()?))
                    });
                    Value::Map(pairs.collect())
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
                46..=48 => {
                    // The degree operators round the result after applying trig.
                    let degrees = to_float(&pop(&mut stack, pc)?);
                    if degrees.is_infinite() {
                        return Err(malformed(pc, "infinite degree operand"));
                    }
                    let radians = degrees.to_radians();
                    let value = match opcode {
                        46 => radians.sin(),
                        47 => radians.cos(),
                        48 => radians.tan(),
                        _ => unreachable!(),
                    };
                    Value::Float(rounded(value))
                }
                49 => {
                    let (y, x) = pair(&mut stack, pc)?;
                    Value::Float(rounded(to_float(&y).atan2(to_float(&x)).to_degrees()))
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
