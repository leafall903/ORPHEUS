use std::collections::BTreeSet;

use orpheus::script_expr::{self, Context, EvalError, Scope, Value, Variable};
use serde_json::{Value as Json, json};

struct FixtureContext<'a>(&'a Json);

impl Context for FixtureContext<'_> {
    fn owner(&mut self) -> Value {
        Value::Entity(u32::try_from(self.0["owner"].as_u64().unwrap()).unwrap())
    }

    fn variable(&mut self, variable: Variable) -> Value {
        assert_eq!(variable.scope, Scope::Instance);
        let input = &self.0["instance_variables"][variable.id.to_string()];
        Value::Entity(u32::try_from(input["entity"].as_u64().unwrap()).unwrap())
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        let parts = self.0["positions"].get(entity.to_string())?;
        Some(serde_json::from_value(parts.clone()).unwrap())
    }

    fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
        let members = self.0["members"].get(entity.to_string())?;
        Some(match members.get(id.to_string()) {
            None | Some(Json::Null) => Value::Null,
            Some(value) => Value::Integer(value.as_i64().unwrap()),
        })
    }
}

fn normalized(result: Result<Value, EvalError>) -> Json {
    match result {
        Ok(Value::Null) => json!({"value":{"kind":"null"}}),
        Ok(Value::Float(value)) => json!({"value":{"kind":"float","value":value}}),
        Ok(Value::Integer(value)) => json!({"value":{"kind":"integer","value":value}}),
        Err(EvalError::UnavailableEntityPosition(entity)) => {
            json!({"error":{"kind":"unavailable_position","entity":entity}})
        }
        Err(EvalError::UnavailableEntityVariable { entity, id }) => {
            json!({"error":{"kind":"unavailable_entity_variable","entity":entity,"id":id}})
        }
        unexpected => panic!("unexpected contract result: {unexpected:?}"),
    }
}

#[test]
fn strict_world_queries_match_shared_contract() {
    let contract: Json = serde_json::from_str(include_str!(
        "../../fixtures/behavior/world-queries-v1.json"
    ))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(
        contract["case_id"],
        "world-queries.synthetic-strict-host.v1"
    );
    assert_eq!(contract["target_build"], 104319);
    assert_eq!(contract["evidence"], "synthetic-strict-host-reference");
    let cases = contract["input"]["cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    assert_eq!(cases.len(), contract["expected"].as_object().unwrap().len());
    let mut ids = BTreeSet::new();
    for case in cases {
        let id = case["id"].as_str().unwrap();
        assert!(ids.insert(id), "duplicate contract case {id}");
        let mut context = FixtureContext(&case["context"]);
        let result = normalized(script_expr::evaluate(&case["config"], &mut context));
        let mut expected = contract["expected"][id].clone();
        if expected["value"]["kind"] == "float" {
            expected["value"]["value"] = json!(expected["value"]["value"].as_f64().unwrap());
        }
        assert_eq!(result, expected, "contract case {id}");
    }
}
