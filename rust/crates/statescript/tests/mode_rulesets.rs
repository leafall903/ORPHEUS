use std::sync::Arc;

use orpheus_statescript::{
    script_graph::GraphStore,
    script_init::{Component, MAX_INIT_WORK, Root, World},
    script_wire::{Value, Variables},
};
use serde::Deserialize;
use serde_json::{Value as Json, json};

type Lookup = (String, String, u32);

#[derive(Deserialize)]
struct Contract {
    schema_version: u8,
    case_id: String,
    evidence: String,
    input: Input,
    expected: Expected,
}

#[derive(Deserialize)]
struct Input {
    bundle: Json,
    lookups: Vec<Lookup>,
    expressions: Vec<Expression>,
}

#[derive(Deserialize)]
struct Expression {
    lookup: usize,
    entity_value: Json,
}

#[derive(Deserialize)]
struct Expected {
    // [] means no rule. [null] means a rule with an explicit null value.
    lookups: Vec<Vec<Json>>,
    expressions: Vec<Json>,
}

fn scalar(value: &Json) -> Value {
    match value {
        Json::Null => Value::Null,
        Json::Bool(value) => Value::Bool(*value),
        Json::String(asset) => Value::Asset(guid(asset)),
        Json::Number(value) => value
            .as_i64()
            .map_or_else(|| Value::Float(value.as_f64().unwrap()), Value::Integer),
        _ => panic!("expected a scalar fixture value"),
    }
}

fn guid(value: &str) -> u64 {
    u64::from_str_radix(value.strip_prefix("0x").unwrap(), 16).unwrap()
}

struct RulesetWorld<'a> {
    graphs: &'a GraphStore,
    mode: u64,
    body: u64,
}

impl World for RulesetWorld<'_> {
    fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
        None
    }

    fn position(&mut self, _: u32) -> Option<[f64; 3]> {
        None
    }

    fn ruleset(&mut self, id: u32) -> Option<Value> {
        self.graphs.mode_ruleset_value(self.mode, self.body, id)
    }
}

#[test]
fn mode_rulesets_share_typed_defaults_overrides_and_expression_precedence() {
    let fixture: Contract = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/script-mode-rulesets-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture.schema_version, 1);
    assert_eq!(fixture.case_id, "script-mode-rulesets.synthetic.v1");
    assert_eq!(fixture.evidence, "synthetic-rust");

    let input = fixture.input;
    let expected = fixture.expected;
    let graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&input.bundle).unwrap()).unwrap());
    assert_eq!(input.lookups.len(), expected.lookups.len());
    for ((mode, body, id), expected) in input.lookups.iter().zip(expected.lookups) {
        let expected = match expected.as_slice() {
            [] => None,
            [value] => Some(scalar(value)),
            _ => panic!("expected at most one lookup result"),
        };
        assert_eq!(
            graphs.mode_ruleset_value(guid(mode), guid(body), *id),
            expected,
            "mode {mode}, body {body}, rule {id}"
        );
    }

    assert_eq!(input.expressions.len(), expected.expressions.len());
    for (case, expected) in input.expressions.into_iter().zip(expected.expressions) {
        let (mode, body, id) = &input.lookups[case.lookup];
        let mut world = RulesetWorld {
            graphs: &graphs,
            mode: guid(mode),
            body: guid(body),
        };
        let component = Component::start(
            Arc::clone(&graphs),
            1001,
            Variables::from([(*id, scalar(&case.entity_value))]),
            vec![Root::listener(1, 911, None)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let expression = json!({"$":"STU_3832D36C", "m_value":id});
        let value = component
            .evaluate_state_config(1, 0, &expression, &mut world)
            .unwrap();
        assert_eq!(value, scalar(&expected), "ruleset expression for {id}");
    }
}
