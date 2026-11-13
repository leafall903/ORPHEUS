//! Explicit foreign component state captured by the room at a frame boundary.

use std::sync::Arc;

use crate::script_wire::{Value, Variables};

/// A missing/faulted backend differs from an explicitly absent component.
#[derive(Clone, Debug, Default)]
pub enum EntityScriptState {
    #[default]
    Unavailable,
    Absent,
    Running(Arc<Variables>),
}

impl EntityScriptState {
    pub fn read(&self, id: u32) -> Option<Value> {
        match self {
            Self::Unavailable => None,
            Self::Absent => Some(Value::Null),
            Self::Running(variables) => Some(variables.get(&id).cloned().unwrap_or(Value::Null)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::script_expr::{self, Context, EvalError, Variable};
    use serde_json::Value as Json;

    fn value(json: &Json) -> Value {
        match json {
            Json::Null => Value::Null,
            Json::Bool(v) => Value::Bool(*v),
            Json::Number(v) => v
                .as_i64()
                .map_or_else(|| Value::Float(v.as_f64().unwrap()), Value::Integer),
            Json::Array(v) => Value::Array(v.iter().map(value).collect()),
            other => panic!("unsupported contract value {other}"),
        }
    }

    #[test]
    fn shared_foreign_component_contract() {
        struct Host {
            entity: u32,
            state: EntityScriptState,
        }
        impl Context for Host {
            fn variable(&mut self, variable: Variable) -> Value {
                assert_eq!(variable.id, 1);
                Value::Entity(self.entity)
            }
            fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
                assert_eq!(entity, self.entity);
                self.state.read(id)
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-foreign-component-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.foreign-component-read.v1");
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let expected = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), expected.len());
        for (case, expected) in cases.iter().zip(expected) {
            assert_eq!(case["name"], expected["name"]);
            let state = match case["state"].as_str().unwrap() {
                "unavailable" | "faulted" => EntityScriptState::Unavailable,
                "absent" => EntityScriptState::Absent,
                "running" => EntityScriptState::Running(Arc::new(
                    case["variables"]
                        .as_object()
                        .unwrap()
                        .iter()
                        .map(|(id, v)| (id.parse().unwrap(), value(v)))
                        .collect(),
                )),
                other => panic!("unknown component state {other}"),
            };
            let mut host = Host {
                entity: contract["input"]["target"]
                    .as_u64()
                    .unwrap()
                    .try_into()
                    .unwrap(),
                state,
            };
            let actual = script_expr::evaluate(&contract["input"]["config"], &mut host);
            let expected = if expected.get("error").is_some() {
                assert_eq!(expected["error"], "unavailable_entity_variable");
                Err(EvalError::UnavailableEntityVariable {
                    entity: host.entity,
                    id: 7,
                })
            } else {
                Ok(value(&expected["value"]))
            };
            assert_eq!(actual, expected, "{}", case["name"]);
        }
    }
}
