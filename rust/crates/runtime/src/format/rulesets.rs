//! Authored ruleset values and engine settings scoped to a mode.

use std::{collections::BTreeMap, io};

use serde::{Deserialize, Deserializer};
use serde_json::{Value as Json, value::RawValue};

use super::asset_guid;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum RuleValue {
    Null,
    Bool(bool),
    Integer(i64),
    Float(f64),
    Asset(u64),
}

type RuleValues = BTreeMap<(Option<u64>, u32), RuleValue>;

/// Validated mode profiles from the bundle or a local rules file.
#[derive(Debug, Default)]
pub struct ModeRulesets(BTreeMap<u64, ModeProfile>);

#[derive(Debug)]
struct ModeProfile {
    rules: RuleValues,
    engine_settings: Option<BTreeMap<u32, RuleValue>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ModeRulesetDefinition {
    mode: Json,
    #[serde(default)]
    rules: Vec<RuleDefinition>,
    #[serde(default, deserialize_with = "setting_table")]
    engine_settings: Option<Vec<EngineSettingDefinition>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuleDefinition {
    id: u32,
    #[serde(default, deserialize_with = "body_scope")]
    body: Option<u64>,
    value: Box<RawValue>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EngineSettingDefinition {
    id: u32,
    value: Box<RawValue>,
}

impl<'de> Deserialize<'de> for ModeRulesets {
    fn deserialize<D: Deserializer<'de>>(decoder: D) -> Result<Self, D::Error> {
        Self::compile(Vec::deserialize(decoder)?).map_err(serde::de::Error::custom)
    }
}

impl ModeRulesets {
    fn compile(definitions: Vec<ModeRulesetDefinition>) -> io::Result<Self> {
        let mut modes = BTreeMap::new();
        for ModeRulesetDefinition {
            mode,
            rules,
            engine_settings,
        } in definitions
        {
            let mode = nonzero_guid(&mode)
                .ok_or_else(|| invalid("mode ruleset requires a nonzero canonical mode GUID"))?;
            let mut values = RuleValues::new();
            for RuleDefinition {
                id,
                body: scope,
                value,
            } in rules
            {
                let value = rule_value(&value).ok_or_else(|| {
                    invalid(format!(
                        "ruleset {mode:016X}:{id} requires null, a Boolean, a signed integer, a finite float or a canonical asset GUID"
                    ))
                })?;
                if values.insert((scope, id), value).is_some() {
                    return Err(invalid(format!(
                        "duplicate rule {id} in mode {mode:016X} for body {scope:X?}"
                    )));
                }
            }
            let profile = ModeProfile {
                rules: values,
                engine_settings: engine_settings
                    .map(|settings| compile_engine_settings(mode, settings))
                    .transpose()?,
            };
            if modes.insert(mode, profile).is_some() {
                return Err(invalid(format!("duplicate mode ruleset {mode:016X}")));
            }
        }
        Ok(Self(modes))
    }

    pub fn value(&self, mode: u64, body: u64, id: u32) -> Option<RuleValue> {
        let values = &self.0.get(&mode)?.rules;
        values
            .get(&(Some(body), id))
            .or_else(|| values.get(&(None, id)))
            .copied()
    }

    pub fn engine_settings(&self, mode: u64) -> Option<&BTreeMap<u32, RuleValue>> {
        self.0.get(&mode)?.engine_settings.as_ref()
    }
}

fn compile_engine_settings(
    mode: u64,
    settings: Vec<EngineSettingDefinition>,
) -> io::Result<BTreeMap<u32, RuleValue>> {
    let mut table = BTreeMap::new();
    for EngineSettingDefinition { id, value } in settings {
        let value = scalar(&value).ok_or_else(|| {
            invalid(format!(
                "engine setting {mode:016X}:{id} requires null, a Boolean, a signed integer or a finite float"
            ))
        })?;
        if table.insert(id, value).is_some() {
            return Err(invalid(format!(
                "duplicate engine setting {id} in mode {mode:016X}"
            )));
        }
    }
    Ok(table)
}

fn setting_table<'de, D: Deserializer<'de>>(
    decoder: D,
) -> Result<Option<Vec<EngineSettingDefinition>>, D::Error> {
    Vec::deserialize(decoder).map(Some)
}

fn body_scope<'de, D: Deserializer<'de>>(decoder: D) -> Result<Option<u64>, D::Error> {
    let guid = Json::deserialize(decoder)?;
    nonzero_guid(&guid)
        .map(Some)
        .ok_or_else(|| serde::de::Error::custom("rule body requires a nonzero canonical GUID"))
}

fn nonzero_guid(value: &Json) -> Option<u64> {
    asset_guid(value).filter(|&guid| guid != 0)
}

fn rule_value(value: &RawValue) -> Option<RuleValue> {
    if value.get().starts_with('"') {
        let guid = serde_json::from_str(value.get()).ok()?;
        nonzero_guid(&guid).map(RuleValue::Asset)
    } else {
        scalar(value)
    }
}

fn scalar(value: &RawValue) -> Option<RuleValue> {
    let value = value.get();
    match value {
        "null" => Some(RuleValue::Null),
        "true" => Some(RuleValue::Bool(true)),
        "false" => Some(RuleValue::Bool(false)),
        _ if value.contains(['.', 'e', 'E']) => value
            .parse::<f64>()
            .ok()
            .filter(|value| value.is_finite())
            .map(RuleValue::Float),
        _ => value.parse::<i64>().ok().map(RuleValue::Integer),
    }
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}
