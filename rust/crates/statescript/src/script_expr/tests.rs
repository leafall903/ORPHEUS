use super::{evaluator::Evaluator, *};
use std::{collections::BTreeMap, path::PathBuf};

use serde_json::json;

use crate::script_graph::GraphStore;

mod body_points;
mod degree_trigonometry;
mod direction_limits;
mod entity_ray_cast;

#[test]
fn charge_cone_atan2_uses_y_then_x_and_returns_degrees() {
    for (y, x, expected) in [(0.0, 1.0, 0.0), (1.0, 0.0, 90.0), (-1.0, 0.0, -90.0)] {
        let config = expression(json!([6, 0, 6, 1, 49, 0]), &[], &[y, x], &[]);
        assert_eq!(
            evaluate(&config, &mut Vars::default()).unwrap(),
            Value::Float(expected)
        );
    }
}

#[test]
fn barrier_health_scale_uses_the_actor_ruleset() {
    struct Rules {
        key: u32,
        value: Value,
    }
    impl Context for Rules {
        fn ruleset(&mut self, id: u32) -> Value {
            assert_eq!(id, self.key);
            self.value.clone()
        }
    }

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../../fixtures/behavior/statescript-barrier-health-scale-v1.json"
    ))
    .unwrap();
    let key = fixture["input"]["rule"]
        .as_u64()
        .unwrap()
        .try_into()
        .unwrap();
    let config = json!({"$": "STU_295F1CDE", "m_EE729DCB": 0});
    let values = fixture["input"]["values"].as_array().unwrap();
    let expected = fixture["expected"].as_array().unwrap();
    assert_eq!(values.len(), expected.len());
    for (value, expected) in values.iter().zip(expected) {
        let value = match value {
            Json::Null => Value::Null,
            Json::Number(value) => value
                .as_i64()
                .map_or_else(|| Value::Float(value.as_f64().unwrap()), Value::Integer),
            Json::Bool(value) => Value::Bool(*value),
            _ => panic!("unexpected ruleset test value"),
        };
        let result = evaluate(&config, &mut Rules { key, value });
        if let Some(expected) = expected.as_f64() {
            assert_eq!(result, Ok(Value::Float(expected)));
        } else {
            assert_eq!(
                result,
                Err(EvalError::MalformedConfig(
                    "invalid barrier health multiplier"
                ))
            );
        }
    }
}

#[test]
fn wheel_selection_resolves_authored_slot_and_equipped_spray() {
    struct Equipped {
        slot: usize,
        spray: u64,
    }

    impl Context for Equipped {
        fn owner(&mut self) -> Value {
            Value::Entity(0xa000_0101)
        }

        fn equipped_cosmetic(
            &mut self,
            entity: u32,
            slot: usize,
            cosmetic: EquippedCosmetic,
        ) -> Option<u64> {
            assert_eq!(cosmetic, EquippedCosmetic::Spray);
            (entity == 0xa000_0101).then_some(if slot == self.slot { self.spray } else { 0 })
        }
    }

    let fixture: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/wheel-selection-ingress-v1.json"
    )))
    .unwrap();
    let cases = &fixture["slot_cases"];
    let spray = &cases["spray"];
    let parse_asset =
        |value: &Json| orpheus_runtime::format::parse_guid(value.as_str().unwrap()).unwrap();
    let mut host = Equipped {
        slot: usize::try_from(spray["slot"].as_u64().unwrap()).unwrap(),
        spray: parse_asset(&spray["equipped"]),
    };
    let selection = |class: &str, guid: &Json| {
        json!({
            "$":class,
            "m_548E5344":{"$":"STU_B70476F3","m_548E5344":guid,"m_resourceKey":null}
        })
    };
    for (kind, class) in [("emote", "STU_3E7F59D8"), ("voice", "STU_955C40AE")] {
        let case = &cases[kind];
        assert_eq!(
            evaluate(&selection(class, &case["action"]), &mut host),
            Ok(Value::Integer(case["index"].as_i64().unwrap()))
        );
    }
    for case in spray["cases"].as_array().unwrap() {
        let equipped = json!({
            "$":"STU_17B517B9","m_entity":{"$":"STU_B5A0CAF0"},
            "m_slot":selection("STU_2E708EC1", &case["action"]),"m_resourceKey":null
        });
        assert_eq!(
            evaluate(&equipped, &mut host),
            Ok(Value::Asset(parse_asset(&case["expected"])))
        );
    }
}

#[test]
fn emote_lookups_read_the_selected_body_slot() {
    struct Equipped {
        alias: u64,
        blend_tree: u64,
    }
    impl Context for Equipped {
        fn owner(&mut self) -> Value {
            Value::Entity(0xa000_0101)
        }

        fn equipped_cosmetic(
            &mut self,
            entity: u32,
            slot: usize,
            cosmetic: EquippedCosmetic,
        ) -> Option<u64> {
            if entity != 0xa000_0101 {
                return None;
            }
            Some(match (cosmetic, slot) {
                (EquippedCosmetic::EmoteAlias, 1) => self.alias,
                (EquippedCosmetic::EmoteBlendTree, 1) => self.blend_tree,
                _ => 0,
            })
        }
    }
    let fixture: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/wheel-emote-presentation-v1.json"
    )))
    .unwrap();
    let asset = |key| {
        orpheus_runtime::format::parse_guid(fixture["rust_asset_lookup"][key].as_str().unwrap())
            .unwrap()
    };
    let mut context = Equipped {
        alias: asset("alias"),
        blend_tree: asset("blend_tree"),
    };
    let owner = json!({"$":"STU_B5A0CAF0"});
    let slot = json!({"$":"STU_3E7F59D8","m_548E5344":{
        "$":"STU_B70476F3","m_548E5344":fixture["input"]["chosen_action"]
    }});
    let alias =
        json!({"$":"STU_250DEABD","m_target":{"$":"STU_33980609","m_entity":owner},"m_slot":slot});
    let no_animation = json!({"$":"STUConfigVarAnimation","m_animation":null,"m_resourceKey":null});
    assert_eq!(evaluate(&no_animation, &mut context), Ok(Value::Asset(0)));
    let mut blend = json!({"$":"STU_DF7C7C63","m_resourceKey":null,"m_entity":owner,"m_slot":slot});
    assert_eq!(
        evaluate(&alias, &mut context),
        Ok(Value::Asset(context.alias))
    );
    assert_eq!(
        evaluate(&blend, &mut context),
        Ok(Value::Asset(context.blend_tree))
    );
    blend["m_slot"] = json!({"$":"STUConfigVarInt","m_value":-1});
    assert_eq!(evaluate(&blend, &mut context), Ok(Value::Asset(0)));
}

struct CatalogContext(GraphStore);

impl Context for CatalogContext {
    fn asset_has_category(&mut self, asset: u64, kind: u64, id: u64) -> Option<bool> {
        self.0.asset_has_category(asset, kind, id)
    }

    fn asset_texture(&mut self, action: u64) -> Option<u64> {
        self.0.asset_texture(action)
    }
}

#[test]
fn authored_asset_category_requires_the_exact_identifier_pair() {
    let asset = "0x0A9000000000AAAA";
    let kind = "0x0C8800000000BBBB";
    let id = "0x0D8000000000CCCC";
    let mut bundle = json!({"version":1, "graphs":[], "bodies":{}, "missing":[]});
    let config = |selected, category_kind, category_id| {
        json!({
            "$":"STU_4460419A",
            "m_548E5344":{"$":"STU_B70476F3", "m_548E5344":selected,"m_resourceKey":null},
            "m_D929182B":{"$":"STU_A1353B54","m_value":{
                "$":"STU_BCD1C634","m_type":category_kind,"m_id":category_id
            }}
        })
    };
    let load = |bundle: &Json| {
        CatalogContext(GraphStore::from_json(&serde_json::to_vec(bundle).unwrap()).unwrap())
    };
    assert_eq!(
        evaluate(&config(asset, kind, id), &mut load(&bundle)),
        Err(EvalError::UnsupportedConfig("STU_4460419A".into()))
    );
    bundle["asset_categories"] = json!({asset: [{"type":kind,"id":id}]});
    let mut context = load(&bundle);
    assert_eq!(
        evaluate(&config(asset, kind, id), &mut context),
        Ok(Value::Bool(true))
    );
    assert_eq!(
        evaluate(&config(asset, "0x0C8800000000BBAA", id), &mut context),
        Ok(Value::Bool(false))
    );
    assert_eq!(
        evaluate(&config("0x0A9000000000AAAB", kind, id), &mut context),
        Ok(Value::Bool(false))
    );
}

#[test]
fn authored_texture_lookup_distinguishes_exact_keys_misses_and_missing_data() {
    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../../fixtures/behavior/script-asset-texture-v1.json"
    ))
    .unwrap();
    let mut bundle = json!({"version":1, "graphs":[], "bodies":{}, "missing":[]});
    let config = |selected: &str| {
        json!({
            "$":"STU_1ECB66A0", "m_EE729DCB":0, "m_resourceKey":null,
            "m_548E5344":{"$":"STUConfigVarIdentifier","m_identifier":selected}
        })
    };
    let load = |bundle: &Json| {
        GraphStore::from_json(&serde_json::to_vec(bundle).unwrap()).map(CatalogContext)
    };
    let assets = fixture["assets"].as_object().unwrap();
    let (first, _) = assets.iter().next().unwrap();
    assert_eq!(
        evaluate(&config(first), &mut load(&bundle).unwrap()),
        Err(EvalError::UnsupportedConfig("STU_1ECB66A0".into()))
    );
    bundle["asset_textures"] = fixture["assets"].clone();
    let mut context = load(&bundle).unwrap();
    for (action, texture) in assets {
        let texture = crate::script_graph::asset_guid(texture).unwrap();
        assert_eq!(
            evaluate(&config(action), &mut context),
            Ok(Value::Asset(texture))
        );
    }
    for key in ["missing_action", "invalid_action"] {
        assert_eq!(
            evaluate(&config(fixture[key].as_str().unwrap()), &mut context),
            Ok(Value::Asset(0))
        );
    }
    bundle["asset_textures"][first] = fixture["invalid_action"].clone();
    assert!(load(&bundle).is_err());
}

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
        "../../../../../fixtures/behavior/statescript-body-angle-v1.json"
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
        "../../../../../fixtures/behavior/statescript-velocity-v1.json"
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
            Json::Object(fields) if fields.contains_key("bits64") => Value::Float(f64::from_bits(
                u64::from_str_radix(json["bits64"].as_str().unwrap(), 16).unwrap(),
            )),
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
        "../../../../../fixtures/behavior/statescript-health-total-v1.json"
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
fn creator_body_selector_uses_the_typed_host_relation() {
    struct Host {
        target: Value,
        creator: Option<u32>,
        requests: Vec<u32>,
    }
    impl Context for Host {
        fn variable(&mut self, variable: Variable) -> Value {
            assert_eq!(
                variable,
                Variable {
                    scope: Scope::Instance,
                    id: 1
                }
            );
            self.target.clone()
        }

        fn related_entity(&mut self, relation: EntityRelation, entity: u32) -> Option<u32> {
            assert_eq!(relation, EntityRelation::CreatorBody);
            self.requests.push(entity);
            self.creator
        }
    }

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../../fixtures/behavior/statescript-creator-body-v1.json"
    ))
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let target = case["entity"].as_u64().map_or_else(
            || Value::Integer(case["operand"].as_i64().unwrap()),
            |entity| Value::Entity(entity as u32),
        );
        let mut host = Host {
            target,
            creator: case["creator"].as_u64().map(|entity| entity as u32),
            requests: Vec::new(),
        };
        let mut config = json!({
            "$": "STU_2B2E2F6D",
            "m_EE729DCB": 0,
            "m_81A364DE": {"$": "STUConfigVarDynamic", "m_identifier": 1},
        });
        if let Some(selector) = case.get("selector") {
            config["m_7BDB02D1"] = selector.clone();
        }
        let expected = case["result"].as_u64().map_or_else(
            || Err(EvalError::UnsupportedConfig("STU_2B2E2F6D".into())),
            |entity| Ok(Value::Entity(entity as u32)),
        );
        assert_eq!(evaluate(&config, &mut host), expected, "{}", case["name"]);
        let expected_requests: Vec<u32> = serde_json::from_value(case["requests"].clone()).unwrap();
        assert_eq!(host.requests, expected_requests, "{}", case["name"]);
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
    let mut query = json!({"$":"STU_309B0570","m_entity":{"$":"STU_B5A0CAF0"},"m_5B94EDB8":null});
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
        query["m_entity"] = json!({"$":"STU_06610EBC","m_configVars":[query["m_entity"].clone()]});
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
        "../../../../../fixtures/behavior/statescript-engine-setting-v1.json"
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
fn qualified_ruleset_reads_the_rule_for_the_entity_without_reinterpreting_its_value_as_a_key() {
    struct Rules(Option<Value>);
    impl Context for Rules {
        fn owner(&mut self) -> Value {
            Value::Entity(46)
        }
        fn entity_ruleset(&mut self, entity: u32, id: u32) -> Option<Value> {
            assert_eq!((entity, id), (46, 902));
            self.0.clone()
        }
        fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
            assert_eq!((entity, id), (46, 902));
            Some(Value::Integer(613))
        }
        fn ruleset(&mut self, id: u32) -> Value {
            assert_eq!(id, 613);
            Value::Bool(false)
        }
    }
    let member = expression(
        json!([14, 0, 7, 0, 15, 0]),
        &[variable(902, Scope::Instance)],
        &[],
        &[json!({"$":"STU_B5A0CAF0"})],
    );
    let rule = json!({"$":"STU_3832D36C","m_value":member});
    assert_eq!(
        evaluate(&rule, &mut Rules(Some(Value::Float(0.25)))),
        Ok(Value::Float(0.25))
    );
    assert_eq!(evaluate(&rule, &mut Rules(None)), Ok(Value::Bool(false)));
    assert_eq!(
        evaluate(&member, &mut Rules(Some(Value::Float(0.25)))),
        Ok(Value::Integer(613))
    );
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
    assert_eq!(
        evaluate(
            &json!({"$":"STU_8556841E","m_entityDef":null,"m_resourceKey":null}),
            &mut EmptyContext
        ),
        Ok(Value::Asset(0))
    );
    for config in [
        json!({"$":"STU_8556841E"}),
        json!({"$":"STU_8556841E","m_entityDef":null}),
        json!({"$":"STU_8556841E","m_resourceKey":null}),
    ] {
        assert!(evaluate(&config, &mut EmptyContext).is_err(), "{config}");
    }
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
fn body_health_queries_return_zero_only_for_explicit_unbound_targets() {
    struct HealthHost {
        variable: u32,
        target: Value,
        reads: Vec<(u32, Option<PoolKind>, bool)>,
    }

    impl Context for HealthHost {
        fn variable(&mut self, variable: Variable) -> Value {
            assert_eq!(variable.id, self.variable);
            self.target.clone()
        }

        fn body_health(
            &mut self,
            entity: u32,
            kind: Option<PoolKind>,
            maximum: bool,
        ) -> Option<f64> {
            self.reads.push((entity, kind, maximum));
            None
        }
    }

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../../fixtures/behavior/script-health-target-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let expected = &fixture["expected"];
    let target_variable = u32::try_from(input["target_variable"].as_u64().unwrap()).unwrap();
    let unbound = u32::try_from(input["unbound_entity"].as_u64().unwrap()).unwrap();
    let unbound_integer = input["unbound_integer"].as_i64().unwrap();
    let unavailable = u32::try_from(input["unavailable_entity"].as_u64().unwrap()).unwrap();
    let unbound_value = Value::Float(expected["unbound_result"].as_f64().unwrap());
    for query_case in input["queries"].as_array().unwrap() {
        let class = query_case["class"].as_str().unwrap();
        let kind = match &query_case["pool"] {
            Json::Null => None,
            Json::String(pool) if pool == "armour" => Some(PoolKind::Armour),
            pool => panic!("unsupported fixture pool {pool}"),
        };
        let maximum = query_case["maximum"].as_bool().unwrap();
        let mut query = json!({"$":class,"m_entity":variable(target_variable, Scope::Instance)});
        if kind.is_some() {
            query["m_7E8B666C"] = json!({"$":"STU_297E1A30","m_7E8B666C":1});
        }
        let mut host = HealthHost {
            variable: target_variable,
            target: Value::Null,
            reads: Vec::new(),
        };
        for target in [
            Value::Null,
            Value::Entity(unbound),
            Value::Integer(unbound_integer),
        ] {
            host.target = target;
            assert_eq!(
                evaluate(&query, &mut host),
                Ok(unbound_value.clone()),
                "{class}"
            );
            assert_eq!(
                json!(host.reads.len()),
                expected["unbound_provider_reads"],
                "{class}"
            );
        }

        let unsupported = Err(EvalError::UnsupportedConfig(class.into()));
        host.target = Value::Bool(false);
        assert_eq!(evaluate(&query, &mut host), unsupported);
        host.target = Value::Integer(i64::from(unavailable));
        assert_eq!(evaluate(&query, &mut host), unsupported);
        let mut missing_target = query.clone();
        missing_target.as_object_mut().unwrap().remove("m_entity");
        assert_eq!(evaluate(&missing_target, &mut host), unsupported);
        if kind.is_some() {
            host.target = Value::Entity(unbound);
            let mut malformed_selector = query.clone();
            malformed_selector["m_7E8B666C"]["m_7E8B666C"] = input["invalid_pool_selector"].clone();
            assert_eq!(evaluate(&malformed_selector, &mut host), unsupported);
        }
        assert!(host.reads.is_empty(), "{class}");

        host.target = Value::Entity(unavailable);
        assert_eq!(evaluate(&query, &mut host), unsupported);
        assert_eq!(
            json!(host.reads.len()),
            expected["unavailable_provider_reads"]
        );
        assert_eq!(host.reads, [(unavailable, kind, maximum)]);
    }
}

#[test]
fn owned_entity_count_distinguishes_absent_owner_and_unavailable_index() {
    struct OwnershipProbe {
        owner: Value,
        native_count: Option<i64>,
        native_reads: u64,
    }

    impl Context for OwnershipProbe {
        fn owner(&mut self) -> Value {
            Value::Entity(11)
        }

        fn variable(&mut self, _: Variable) -> Value {
            self.owner.clone()
        }

        fn native(&mut self, config: &Json) -> Option<Value> {
            assert_eq!(class(config), Some("STU_967463AB"));
            self.native_reads += 1;
            self.native_count.map(Value::Integer)
        }
    }

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../../fixtures/behavior/statescript-owned-entity-count-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let initial = Value::Integer(input["initial"].as_i64().unwrap());
    let slot = u32::try_from(input["owner_variable"].as_u64().unwrap()).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let mut query = json!({
            "$":"STU_967463AB",
            "m_92D12BE6":variable(slot, Scope::Instance),
            "m_C8655030":{"$":"STU_8556841E","m_entityDef":input["definition"]}
        });
        if let Some(operand) = case.get("definition_operand") {
            query["m_C8655030"] = operand.clone();
        }
        if case["missing_owner"] == true {
            query.as_object_mut().unwrap().remove("m_92D12BE6");
        }
        if case["related_owner"] == true {
            query["m_92D12BE6"] = json!({"$":"STU_DB742151","m_entity":{"$":"STU_B5A0CAF0"}});
        }
        let mut host = OwnershipProbe {
            owner: match &case["owner"] {
                Json::Null => Value::Null,
                Json::Bool(value) => Value::Bool(*value),
                value => Value::Entity(u32::try_from(value.as_u64().unwrap()).unwrap()),
            },
            native_count: case["native_count"].as_i64(),
            native_reads: 0,
        };
        let expected = match case["error"].as_str() {
            Some(class) => Err(EvalError::UnsupportedConfig(class.into())),
            None => Ok(Value::Integer(case["value"].as_i64().unwrap())),
        };
        assert_eq!(
            evaluate_with_initial(&query, &mut host, initial.clone()),
            expected,
            "{name}"
        );
        assert_eq!(
            host.native_reads,
            case["native_reads"].as_u64().unwrap(),
            "{name}"
        );
    }
}

#[test]
fn authored_message_map_looks_up_the_first_matching_key() {
    use crate::script_wire::{ValueEncoding, WireError, write_value};
    use orpheus_wire::bits::BitWriter;

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
fn authored_pairs_preserve_nested_parameter_maps_in_expression_order() {
    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../../fixtures/behavior/script-authored-pairs-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let display_slot = input["display_slot"].as_u64().unwrap();
    let style_slot = input["style_slot"].as_u64().unwrap();
    let style = input["style"].as_i64().unwrap();
    let display_text = crate::script_graph::asset_guid(&input["display_text"]).unwrap();
    let params = expression(
        json!([7, 0, 14, 0, 7, 1, 14, 1, 19, 2, 0]),
        &[
            variable(display_slot as u32, Scope::Instance),
            variable(style_slot as u32, Scope::Instance),
        ],
        &[],
        &[
            json!({"$":"STUConfigVarUXDisplayText", "m_displayText":input["display_text"]}),
            json!({"$":"STUConfigVarInt", "m_value":style}),
        ],
    );
    let mut table = json!({
        "$":"STU_7757D403",
        "m_configVars":[{
            "$":"STU_98DD1872",
            "m_key":{"$":"STU_B70476F3", "m_548E5344":input["selection"]},
            "m_value":params
        }],
    });
    let mut context = Vars::default();
    let selection = crate::script_graph::asset_guid(&input["selection"]).unwrap();
    assert_eq!(
        evaluate(&table, &mut context),
        Ok(Value::Map(vec![(
            Value::Asset(selection),
            Value::Map(vec![
                (
                    Value::Integer(display_slot as i64),
                    Value::Asset(display_text)
                ),
                (Value::Integer(style_slot as i64), Value::Integer(style))
            ])
        )]))
    );
    table["m_configVars"][0]["m_value"]["m_expression"]["m_opcodes"] = json!([19, 1, 0]);
    assert!(matches!(
        evaluate(&table, &mut context),
        Err(EvalError::MalformedBytecode {
            offset: 0,
            reason: "expression stack underflow",
        })
    ));
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
            (filter["m_66747E7D"] == 1 && filter["m_89831602"] == 4).then_some(SceneFilterPolicy {
                relation: FilterRelation::FriendlyOther,
                predicate: if child(filter, "m_condition").is_null() {
                    FilterPredicate::None
                } else {
                    FilterPredicate::Target
                },
            })
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
        "../../../../../fixtures/behavior/statescript-ally-filter-v1.json"
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
        "../../../../../fixtures/behavior/statescript-combat-target-v1.json"
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
        "../../../../../fixtures/behavior/statescript-hostile-filter-v1.json"
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
        "../../../../../fixtures/behavior/absent-target-filter-v1.json"
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
        "../../../../../fixtures/behavior/flashbang-filter-admission-v1.json"
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
        "../../../../../fixtures/behavior/statescript-filter-tag-clauses-v1.json"
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
fn authored_spatial_queries_read_current_entity_vectors() {
    #[derive(serde::Deserialize)]
    struct Sample {
        position: [f64; 3],
        direction: [f64; 3],
    }

    #[derive(serde::Deserialize)]
    struct Input {
        entity: u32,
        position: Json,
        direction: Json,
        samples: [Sample; 2],
    }

    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u8,
        case_id: String,
        evidence: String,
        input: Input,
        expected: [Sample; 2],
    }

    struct Host {
        operand: Value,
        position: Option<[f64; 3]>,
        direction: Option<[f64; 3]>,
        reads: Vec<(&'static str, u32)>,
    }

    impl Context for Host {
        fn variable(&mut self, variable: Variable) -> Value {
            assert_eq!(variable.scope, Scope::Instance);
            assert_eq!(variable.id, 61);
            self.operand.clone()
        }

        fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
            self.reads.push(("legacy_position", entity));
            Some([3.0, 4.0, 0.0])
        }

        fn forward(&mut self, entity: u32) -> Option<[f64; 3]> {
            self.reads.push(("legacy_forward", entity));
            Some([0.0, 0.0, 1.0])
        }

        fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
            self.reads.push(("authored_position", entity));
            self.position
        }

        fn authored_forward(&mut self, entity: u32) -> Option<[f64; 3]> {
            self.reads.push(("authored_forward", entity));
            self.direction
        }

        fn known_position(&mut self, _: u32) -> Option<[f64; 3]> {
            panic!("the transform operands do not use the known-position query");
        }
    }

    let contract: Contract = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/statescript-entity-spatial-v1.json"
    )))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    assert_eq!(contract.case_id, "statescript-entity-spatial.synthetic.v1");
    assert_eq!(contract.evidence, "synthetic-rust");

    let Input {
        entity,
        position,
        direction,
        samples,
    } = contract.input;
    let mut host = Host {
        operand: Value::Entity(entity),
        position: None,
        direction: None,
        reads: Vec::new(),
    };
    let expected_reads = [("authored_position", entity), ("authored_forward", entity)];
    for (sample, expected) in samples.into_iter().zip(contract.expected) {
        host.position = Some(sample.position);
        host.direction = Some(sample.direction);
        assert_eq!(
            evaluate(&position, &mut host),
            Ok(Value::Vector(expected.position))
        );
        assert_eq!(
            evaluate(&direction, &mut host),
            Ok(Value::Vector(expected.direction))
        );
        assert_eq!(std::mem::take(&mut host.reads), expected_reads);
    }

    let legacy_position = expression(
        json!([8, 0, 50, 0]),
        &[variable(61, Scope::Instance)],
        &[],
        &[],
    );
    let legacy_forward = json!({"$": "STU_021D01CD", "m_entity": &direction["entity"]});
    for (host_position, host_direction) in [
        (None, None),
        (Some([f64::NAN, 0.0, 0.0]), Some([0.0, f64::INFINITY, 0.0])),
    ] {
        host.position = host_position;
        host.direction = host_direction;
        assert_eq!(
            evaluate(&position, &mut host),
            Err(EvalError::UnavailableEntityPosition(entity))
        );
        assert_eq!(
            evaluate(&direction, &mut host),
            Err(EvalError::UnavailableEntityDirection(entity))
        );
        assert_eq!(std::mem::take(&mut host.reads), expected_reads);
        assert_eq!(evaluate(&legacy_position, &mut host), Ok(Value::Float(5.0)));
        assert_eq!(
            evaluate(&legacy_forward, &mut host),
            Ok(Value::Vector([0.0, 0.0, 1.0]))
        );
        assert_eq!(
            std::mem::take(&mut host.reads),
            [("legacy_position", entity), ("legacy_forward", entity)]
        );
    }

    for config in [&position, &direction] {
        host.operand = Value::Entity(entity);
        for malformed in [
            json!({"$": &config["$"]}),
            json!({"$": &config["$"], "entity": &config["entity"], "extra": null}),
        ] {
            assert!(matches!(
                evaluate(&malformed, &mut host),
                Err(EvalError::MalformedConfig(_))
            ));
        }
        for invalid_entity in [Value::Entity(0), Value::Integer(i64::from(entity))] {
            host.operand = invalid_entity;
            assert!(matches!(
                evaluate(config, &mut host),
                Err(EvalError::MalformedConfig(_))
            ));
        }
        host.operand = Value::Entity(entity);
        let limited = json!({
            "$": &config["$"],
            "entity": {"$": "ClientOnly", "value": null, "server_fallback": &config["entity"]}
        });
        assert_eq!(
            evaluate_with_budget(&limited, &mut host, 1),
            Err(EvalError::BudgetExceeded {
                offset: 0,
                limit: 1
            })
        );
        assert!(host.reads.is_empty());
    }
}

#[test]
fn native_pitched_direction_reads_known_entity_pose_without_a_placement_gate() {
    struct Host {
        entity: Value,
        direction: Option<[f64; 3]>,
        override_value: Option<Value>,
        reads: Vec<u32>,
    }

    impl Context for Host {
        fn variable(&mut self, variable: Variable) -> Value {
            assert_eq!(
                variable,
                Variable {
                    scope: Scope::Instance,
                    id: 61
                }
            );
            self.entity.clone()
        }

        fn authored_forward(&mut self, entity: u32) -> Option<[f64; 3]> {
            self.reads.push(entity);
            self.direction
        }

        fn forward(&mut self, _: u32) -> Option<[f64; 3]> {
            panic!("pitched direction must not use the horizontal-facing query")
        }

        fn native(&mut self, _: &Json) -> Option<Value> {
            self.override_value.clone()
        }
    }

    let contract: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/statescript-native-direction-v1.json"
    )))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(
        contract["case_id"],
        "statescript-native-direction.synthetic.v1"
    );
    let config = &contract["input"]["config"];
    let mut host = Host {
        entity: Value::Null,
        direction: None,
        override_value: None,
        reads: Vec::new(),
    };
    for (sample, expected) in contract["input"]["samples"]
        .as_array()
        .unwrap()
        .iter()
        .zip(contract["expected"].as_array().unwrap())
    {
        let entity = sample["entity"].as_u64().unwrap() as u32;
        let direction: [f64; 3] = serde_json::from_value(sample["direction"].clone()).unwrap();
        host.entity = Value::Entity(entity);
        host.direction = Some(direction);
        assert_eq!(
            evaluate(config, &mut host),
            Ok(Value::Vector(
                serde_json::from_value(expected.clone()).unwrap()
            ))
        );
        assert_eq!(std::mem::take(&mut host.reads), [entity]);
        let mut with_header = config.clone();
        with_header["m_EE729DCB"] = json!(0);
        assert_eq!(
            evaluate(&with_header, &mut host),
            Ok(Value::Vector(direction))
        );
        assert_eq!(std::mem::take(&mut host.reads), [entity]);
    }

    host.entity = Value::Entity(17);
    for direction in [
        None,
        Some([f64::NAN, 0.0, 1.0]),
        Some([0.0, f64::INFINITY, 1.0]),
    ] {
        host.direction = direction;
        assert_eq!(
            evaluate(config, &mut host),
            Err(EvalError::UnavailableEntityDirection(17))
        );
        assert_eq!(std::mem::take(&mut host.reads), [17]);
    }

    for entity in [
        Value::Null,
        Value::Entity(0),
        Value::Integer(17),
        Value::Array(vec![Value::Entity(17)]),
    ] {
        host.entity = entity;
        assert!(matches!(
            evaluate(config, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
        assert!(host.reads.is_empty());
    }
    host.entity = Value::Entity(17);
    for malformed in [
        json!({"$":"STU_2200A66D"}),
        json!({"$":"STU_2200A66D", "m_entity":config["m_entity"], "extra":null}),
        json!({"$":"STU_2200A66D", "m_entity":config["m_entity"], "m_EE729DCB":1}),
    ] {
        assert!(matches!(
            evaluate(&malformed, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
        assert!(host.reads.is_empty());
    }
    let nested = json!({
        "$":"STU_2200A66D", "m_entity":{
            "$":"ClientOnly", "value":null, "server_fallback":config["m_entity"]
        }
    });
    assert_eq!(
        evaluate_with_budget(&nested, &mut host, 1),
        Err(EvalError::BudgetExceeded {
            offset: 0,
            limit: 1
        })
    );
    assert!(host.reads.is_empty());

    host.override_value = Some(Value::Vector([0.0, -0.5, 1.0]));
    assert_eq!(
        evaluate(config, &mut host),
        Ok(Value::Vector([0.0, -0.5, 1.0]))
    );
    assert!(host.reads.is_empty());
    for invalid_override in [Value::Bool(false), Value::Vector([0.0, f64::INFINITY, 1.0])] {
        host.override_value = Some(invalid_override);
        assert_eq!(
            evaluate(config, &mut host),
            Err(EvalError::UnavailableEntityDirection(17))
        );
        assert!(host.reads.is_empty());
    }
    host.override_value = Some(Value::Null);
    let host_owned_selector = json!({
        "$":"STU_2200A66D", "m_entity":{"$":"STU_SyntheticHostSelector"}
    });
    assert_eq!(evaluate(&host_owned_selector, &mut host), Ok(Value::Null));
    assert!(host.reads.is_empty());
    host.override_value = None;
    assert!(matches!(
        evaluate(&host_owned_selector, &mut host),
        Err(EvalError::UnsupportedConfig(_))
    ));
    assert!(host.reads.is_empty());
}

#[test]
fn target_tag_array_and_capsule_radius_m_entity_fallback_evaluate_cleanly() {
    struct DoomfistHost;
    impl Context for DoomfistHost {
        fn owner(&mut self) -> Value {
            Value::Entity(0xa000_0001)
        }
        fn variable(&mut self, variable: Variable) -> Value {
            match variable.id {
                10 => Value::Array(vec![Value::Entity(0xa000_0002), Value::Entity(0xa000_0003)]),
                11 => Value::Array(Vec::new()),
                _ => Value::Null,
            }
        }
        fn has_target_tag(&mut self, entity: u32, tag: u64) -> Option<bool> {
            Some(entity == 0xa000_0003 && tag == 0x0d60_0000_0000_003a)
        }
    }

    let mut host = DoomfistHost;
    let radius_cfg = json!({
        "$": "STU_F7FD94F9",
        "m_A83C2C26": null,
        "m_entity": { "$": "STU_B5A0CAF0", "m_EE729DCB": 0 }
    });
    assert_eq!(evaluate(&radius_cfg, &mut host), Ok(Value::Float(0.55)));

    let tag_hit = json!({
        "$": "STU_25ECB55F",
        "m_entity": { "$": "STUConfigVarDynamic", "m_60DB8F99": 0, "m_identifier": 10 },
        "m_88B0050A": "0x0D6000000000003A",
        "m_EE729DCB": 0
    });
    assert_eq!(evaluate(&tag_hit, &mut host), Ok(Value::Bool(true)));

    let tag_empty = json!({
        "$": "STU_25ECB55F",
        "m_entity": { "$": "STUConfigVarDynamic", "m_60DB8F99": 0, "m_identifier": 11 },
        "m_88B0050A": "0x0D6000000000003A",
        "m_EE729DCB": 0
    });
    assert_eq!(evaluate(&tag_empty, &mut host), Ok(Value::Bool(false)));
}

#[test]
fn native_eye_position_resolves_the_selected_body_and_requires_a_finite_anchor() {
    struct Host {
        owner: u32,
        foreign: Value,
        anchors: BTreeMap<u32, [f64; 3]>,
        lookups: Vec<u32>,
    }

    impl Context for Host {
        fn owner(&mut self) -> Value {
            Value::Entity(self.owner)
        }

        fn variable(&mut self, variable: Variable) -> Value {
            assert_eq!(variable.scope, Scope::Instance);
            assert_eq!(variable.id, 37);
            self.foreign.clone()
        }

        fn body_eye_position(&mut self, entity: u32) -> Option<[f64; 3]> {
            self.lookups.push(entity);
            self.anchors.get(&entity).copied()
        }

        fn position(&mut self, _: u32) -> Option<[f64; 3]> {
            panic!("an eye anchor must not use the generic position lookup");
        }

        fn native(&mut self, _: &Json) -> Option<Value> {
            panic!("an eye anchor must use the dedicated host method");
        }
    }

    let contract: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/statescript-native-eye-position-v1.json"
    )))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(
        contract["case_id"],
        "statescript-native-eye-position.synthetic.v1"
    );
    assert_eq!(contract["evidence"], "synthetic-rust");
    let input = &contract["input"];
    let owner = serde_json::from_value(input["owner"].clone()).unwrap();
    let foreign = serde_json::from_value(input["foreign"].clone()).unwrap();
    let anchors: [[f64; 3]; 2] = serde_json::from_value(input["anchors"].clone()).unwrap();
    let mut host = Host {
        owner,
        foreign: Value::Entity(foreign),
        anchors: BTreeMap::from_iter([owner, foreign].into_iter().zip(anchors)),
        lookups: Vec::new(),
    };
    let selectors = input["selectors"].as_array().unwrap();
    let expected: [[f64; 3]; 2] =
        serde_json::from_value(contract["expected"]["positions"].clone()).unwrap();
    for (selector, position) in selectors.iter().zip(expected) {
        let config = json!({"$":"STU_21A13145", "m_entity":selector, "m_EE729DCB":0});
        assert_eq!(evaluate(&config, &mut host), Ok(Value::Vector(position)));
    }
    let lookups: Vec<u32> =
        serde_json::from_value(contract["expected"]["lookups"].clone()).unwrap();
    assert_eq!(std::mem::take(&mut host.lookups), lookups);

    let config = json!({"$":"STU_21A13145", "m_entity":selectors[1]});
    host.anchors.remove(&foreign);
    assert_eq!(
        evaluate(&config, &mut host),
        Err(EvalError::UnavailableBodyEyePosition(foreign))
    );
    host.anchors.insert(foreign, [f64::NAN, 0.0, 0.0]);
    assert_eq!(
        evaluate(&config, &mut host),
        Err(EvalError::UnavailableBodyEyePosition(foreign))
    );
    assert_eq!(std::mem::take(&mut host.lookups), [foreign, foreign]);

    for malformed in [
        json!({"$":"STU_21A13145"}),
        json!({"$":"STU_21A13145", "m_entity":selectors[0], "m_EE729DCB":1}),
        json!({"$":"STU_21A13145", "m_entity":selectors[0], "extra":null}),
    ] {
        assert_eq!(
            evaluate(&malformed, &mut host),
            Err(EvalError::MalformedConfig(
                "expected one native eye position entity operand"
            ))
        );
    }
    for unbound in [Value::Null, Value::Entity(0), Value::Entity(0x1fff_ffff)] {
        host.foreign = unbound;
        assert_eq!(
            evaluate(&config, &mut host),
            Err(EvalError::MalformedConfig("expected a bound entity"))
        );
    }
    assert!(host.lookups.is_empty());
    host.foreign = Value::Entity(foreign);
    let wrapped = json!({"$":"ClientOnly", "value":null, "server_fallback":config});
    assert_eq!(
        evaluate_with_budget(&wrapped, &mut host, 1),
        Err(EvalError::BudgetExceeded {
            offset: 0,
            limit: 1
        })
    );
    assert!(host.lookups.is_empty());
}

#[test]
fn body_activity_reads_a_known_boolean_and_rejects_unavailable_or_malformed_inputs() {
    struct Host {
        active: Option<bool>,
        reads: usize,
    }

    impl Context for Host {
        fn body_active(&mut self) -> Option<bool> {
            self.reads += 1;
            self.active
        }

        fn watches_natives(&self) -> bool {
            true
        }

        fn reference_native(&mut self, class: &str) {
            panic!("authored body activity was treated as reference-native: {class}");
        }
    }

    let config = json!({"$": "BodyActive"});
    let mut host = Host {
        active: Some(true),
        reads: 0,
    };
    assert_eq!(
        evaluate_with_budget(&config, &mut host, 1),
        Ok(Value::Bool(true))
    );
    host.active = Some(false);
    assert_eq!(evaluate(&config, &mut host), Ok(Value::Bool(false)));
    host.active = None;
    assert_eq!(
        evaluate(&config, &mut host),
        Err(EvalError::UnavailableBodyActivity)
    );
    assert_eq!(host.reads, 3);
    assert_eq!(
        evaluate(&json!({"$": "BodyActive", "entity": null}), &mut host),
        Err(EvalError::MalformedConfig("BodyActive takes no operands"))
    );
    let wrapped = json!({"$":"ClientOnly", "value":null, "server_fallback":config});
    assert_eq!(
        evaluate_with_budget(&wrapped, &mut host, 1),
        Err(EvalError::BudgetExceeded {
            offset: 0,
            limit: 1
        })
    );
    assert_eq!(host.reads, 3);
}

#[test]
fn authored_stack_admission_passes_strict_scoped_inputs_to_the_context() {
    #[derive(Default)]
    struct StackContext {
        admitted: Option<bool>,
        calls: Vec<(Variable, StackPriority)>,
    }

    impl Context for StackContext {
        fn would_stack_to_top(
            &mut self,
            variable: Variable,
            priority: StackPriority,
        ) -> Option<bool> {
            self.calls.push((variable, priority));
            self.admitted
        }

        fn reference_native(&mut self, class: &str) {
            panic!("authored stack admission was treated as a native expression: {class}");
        }
    }

    let entity = json!({
        "$":"StackAllows",
        "variable":{"scope":"entity","id":u32::MAX},
        "priority":{"level":-128,"above_equals":false}
    });
    let instance = json!({
        "$":"StackAllows",
        "variable":{"scope":"instance","id":7},
        "priority":{"level":127,"above_equals":true}
    });
    let mut context = StackContext {
        admitted: Some(true),
        ..Default::default()
    };
    assert_eq!(evaluate(&entity, &mut context), Ok(Value::Bool(true)));
    context.admitted = Some(false);
    assert_eq!(evaluate(&instance, &mut context), Ok(Value::Bool(false)));
    assert_eq!(
        context.calls,
        [
            (
                Variable {
                    scope: Scope::Entity,
                    id: u32::MAX
                },
                StackPriority {
                    level: -128,
                    above_equals: false
                }
            ),
            (
                Variable {
                    scope: Scope::Instance,
                    id: 7
                },
                StackPriority {
                    level: 127,
                    above_equals: true
                }
            )
        ]
    );
    assert_eq!(
        evaluate(&entity, &mut EmptyContext),
        Err(EvalError::UnavailableStackAdmission)
    );

    context.calls.clear();
    for invalid in [
        json!({"$":"StackAllows","priority":entity["priority"]}),
        json!({"$":"StackAllows","variable":entity["variable"]}),
        json!({ "$":"StackAllows","variable":entity["variable"],"priority":entity["priority"],"extra":true }),
    ] {
        assert!(matches!(
            evaluate(&invalid, &mut context),
            Err(EvalError::MalformedConfig(_))
        ));
    }
    for invalid in [
        Json::Null,
        json!({"scope":"entity"}),
        json!({"scope":"parent","id":1}),
        json!({"scope":"entity","id":0}),
        json!({"scope":"entity","id":1.0}),
        json!({"scope":"instance","id":u64::from(u32::MAX) + 1}),
        json!({"scope":"entity","id":1,"extra":false}),
    ] {
        let mut config = entity.clone();
        config["variable"] = invalid;
        assert!(matches!(
            evaluate(&config, &mut context),
            Err(EvalError::MalformedConfig(_))
        ));
    }
    for invalid in [
        Json::Null,
        json!({"level":1}),
        json!({"level":-129,"above_equals":false}),
        json!({"level":128,"above_equals":false}),
        json!({"level":1.0,"above_equals":true}),
        json!({"level":1,"above_equals":1}),
        json!({"level":1,"above_equals":false,"extra":0}),
    ] {
        let mut config = entity.clone();
        config["priority"] = invalid;
        assert!(matches!(
            evaluate(&config, &mut context),
            Err(EvalError::MalformedConfig(_))
        ));
    }
    assert!(context.calls.is_empty());
}

#[test]
fn authored_room_predicates_share_radius_relation_and_tag_checks() {
    use crate::script_init::{TargetTagEffect, TargetTagEffects};
    use std::collections::BTreeSet;

    #[derive(serde::Deserialize)]
    struct Actor {
        id: u32,
        team: Option<u8>,
        alive: bool,
        tags: Option<Vec<String>>,
        #[serde(default)]
        granted: Vec<String>,
        #[serde(default)]
        suppressed: Vec<String>,
    }

    #[derive(serde::Deserialize)]
    struct Input {
        entity: Json,
        source: Json,
        center: Json,
        actors: Vec<Actor>,
        candidates: Vec<(u32, [f64; 3])>,
    }

    #[derive(serde::Deserialize)]
    struct Predicate {
        entity: u32,
        source: Option<u32>,
        filter: Option<Json>,
        result: Option<bool>,
    }

    #[derive(serde::Deserialize)]
    struct Radius {
        source: Option<u32>,
        filter: Json,
        result: Option<Vec<u32>>,
        #[serde(default)]
        without_facts: bool,
        missing_facts_entity: Option<u32>,
        #[serde(default)]
        spatial_unavailable: bool,
    }

    #[derive(serde::Deserialize)]
    struct Expected {
        predicates: Vec<Predicate>,
        radii: Vec<Radius>,
    }

    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u8,
        case_id: String,
        evidence: String,
        input: Input,
        expected: Expected,
    }

    struct Host {
        entity: Value,
        source: u32,
        facts: BTreeMap<u32, RoomEntityFacts>,
        provide_facts: bool,
        candidates: Vec<SpatialCandidate>,
        metadata_reads: usize,
        rays: usize,
    }

    impl Context for Host {
        fn variable(&mut self, variable: Variable) -> Value {
            assert_eq!(variable.scope, Scope::Instance);
            match variable.id {
                1 => self.entity.clone(),
                2 => Value::Entity(self.source),
                3 => Value::Vector([0.0; 3]),
                id => panic!("unknown fixture operand {id}"),
            }
        }

        fn spatial_candidates(&mut self) -> Option<&[SpatialCandidate]> {
            Some(&self.candidates)
        }

        fn room_entities(&mut self) -> Option<&BTreeMap<u32, RoomEntityFacts>> {
            self.metadata_reads += 1;
            self.provide_facts.then_some(&self.facts)
        }

        fn spatial_line_clear(&mut self, _origin: [f64; 3], _target: [f64; 3]) -> Option<bool> {
            self.rays += 1;
            Some(true)
        }
    }

    let contract: Contract = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/statescript-room-entity-filter-v1.json"
    )))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    assert_eq!(
        contract.case_id,
        "statescript-room-entity-filter.synthetic.v1"
    );
    assert_eq!(contract.evidence, "synthetic-rust");

    let input = contract.input;
    let facts: BTreeMap<_, _> = input
        .actors
        .into_iter()
        .map(|actor| {
            let authored = actor.tags.map(|tags| {
                tags.iter()
                    .map(|tag| parse_guid(tag).unwrap())
                    .collect::<BTreeSet<_>>()
            });
            let mut effects = TargetTagEffects::default();
            for (present, tags) in [(true, actor.granted), (false, actor.suppressed)] {
                for tag in tags {
                    assert!(effects.apply(
                        TargetTagEffect {
                            entity: actor.id,
                            tag: parse_guid(&tag).unwrap(),
                            present,
                        },
                        true,
                    ));
                }
            }
            (
                actor.id,
                RoomEntityFacts {
                    team: actor.team,
                    alive: actor.alive,
                    tags: effects.snapshot(authored.as_ref()),
                },
            )
        })
        .collect();
    let candidates = input
        .candidates
        .into_iter()
        .map(|(entity, position)| SpatialCandidate {
            entity,
            position,
            team: facts[&entity].team,
        })
        .collect();
    let mut host = Host {
        entity: Value::Null,
        source: 0,
        facts,
        provide_facts: true,
        candidates,
        metadata_reads: 0,
        rays: 0,
    };

    let predicate = json!({"$":"EntityMatches","entity":input.entity});
    for case in contract.expected.predicates {
        host.entity = Value::Entity(case.entity);
        host.source = case.source.unwrap_or_default();
        let mut query = predicate.clone();
        if let Some(mut filter) = case.filter {
            if case.source.is_some() {
                filter["source"] = input.source.clone();
            }
            query["filter"] = filter;
        }
        assert_eq!(
            evaluate(&query, &mut host),
            case.result
                .map(Value::Bool)
                .ok_or(EvalError::UnavailableRoomEntityFacts),
            "entity {} predicate {query}",
            case.entity,
        );
    }

    for case in contract.expected.radii {
        host.source = case.source.unwrap_or_default();
        host.provide_facts = !case.without_facts;
        let removed_facts = case
            .missing_facts_entity
            .map(|entity| (entity, host.facts.remove(&entity).unwrap()));
        let mut filter = case.filter;
        if case.source.is_some() {
            filter["source"] = input.source.clone();
        }
        let mut query = json!({
            "$":"EntitiesInRadius",
            "center":input.center,
            "radius":5,
            "filter":filter,
        });
        if case.result.is_none() && !case.spatial_unavailable {
            // Unknown fact aborts the whole query before any visibility requests.
            query["line_of_sight"] = Json::Bool(true);
        }
        host.metadata_reads = 0;
        host.rays = 0;
        let expected = case
            .result
            .map(|ids| Value::Array(ids.into_iter().map(Value::Entity).collect()))
            .ok_or(if case.spatial_unavailable {
                EvalError::UnavailableSpatialCandidates
            } else {
                EvalError::UnavailableRoomEntityFacts
            });
        assert_eq!(evaluate(&query, &mut host), expected, "radius {query}");
        assert_eq!(host.rays, 0);
        if case.without_facts || case.spatial_unavailable {
            assert_eq!(host.metadata_reads, 0, "radius {query}");
        }
        if let Some((entity, facts)) = removed_facts {
            host.facts.insert(entity, facts);
        }
    }

    host.entity = Value::Entity(34);
    host.source = 700;
    host.metadata_reads = 0;
    let mut left = predicate.clone();
    left["filter"] = json!({"alive":true});
    let mut right = predicate.clone();
    right["filter"] = json!({"relation":"ally","source":input.source});
    let combined = expression(json!([14, 0, 2, 4, 14, 1, 0]), &[], &[], &[left, right]);
    assert_eq!(evaluate(&combined, &mut host), Ok(Value::Bool(false)));
    assert_eq!(host.metadata_reads, 1);
    host.entity = Value::Entity(41);
    assert_eq!(
        evaluate(&combined, &mut host),
        Err(EvalError::UnavailableRoomEntityFacts)
    );
    assert_eq!(host.metadata_reads, 3);

    for filter in [
        Json::Null,
        json!({"relation":"ally"}),
        json!({"source":input.source}),
        json!({"alive":0}),
        json!({"tags":{"all":["0x0000000000000000"]}}),
        json!({"tags":{"any":"0xF100000000000001"}}),
        json!({"tags":{"unknown":[]}}),
        json!({"depth":2}),
    ] {
        let mut query = predicate.clone();
        query["filter"] = filter;
        assert!(matches!(
            evaluate(&query, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
    }
    for invalid in [
        Value::Entity(0),
        Value::Entity(0x1fff_ffff),
        Value::Integer(41),
    ] {
        host.entity = invalid;
        assert!(matches!(
            evaluate(&predicate, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
    }

    host.entity = Value::Entity(41);
    host.provide_facts = false;
    assert_eq!(
        evaluate(&predicate, &mut host),
        Err(EvalError::UnavailableRoomEntityFacts)
    );
    host.provide_facts = true;
    let mut query = predicate.clone();
    query["filter"] = json!({"tags":{"all":["0xF100000000000001"]}});
    assert_eq!(
        evaluate_with_budget(&query, &mut host, 2),
        Err(EvalError::BudgetExceeded {
            offset: 0,
            limit: 2
        })
    );
    host.facts.clear();
    assert_eq!(evaluate(&predicate, &mut host), Ok(Value::Bool(false)));
}

#[test]
fn radial_health_uses_measured_distance_and_preserves_ordinary_duplicate_rules() {
    struct Host(Vec<SpatialCandidate>);

    impl Context for Host {
        fn variable(&mut self, _: Variable) -> Value {
            Value::Vector([0.0; 3])
        }

        fn spatial_candidates(&mut self) -> Option<&[SpatialCandidate]> {
            Some(&self.0)
        }
    }

    let candidate = |entity, x| SpatialCandidate {
        entity,
        position: [x, 0.0, 0.0],
        team: None,
    };
    let mut host = Host(vec![
        candidate(29, 8.0),
        candidate(19, 4.0),
        candidate(13, 0.0),
        candidate(19, 4.0),
    ]);
    let mut area = json!({
        "$":"EntitiesInRadius",
        "center":variable(1, Scope::Instance),
        "radius":8,
    });
    assert_eq!(
        radial_health_changes(&area, &json!(-32), Some(&json!(0.25)), &mut host),
        Ok(vec![(13, -32.0), (19, -20.0), (29, -8.0)])
    );
    assert_eq!(
        radial_health_changes(&area, &json!(3.5), Some(&json!(0)), &mut host),
        Ok(vec![(13, 3.5), (19, 1.75)])
    );
    assert_eq!(
        radial_health_changes(&area, &json!(1), None, &mut host),
        Ok(vec![(13, 1.0), (19, 1.0), (29, 1.0)])
    );
    area["radius"] = json!(0);
    assert_eq!(
        radial_health_changes(&area, &json!(1), Some(&json!(0)), &mut host),
        Ok(vec![(13, 1.0)])
    );
    area["radius"] = json!(8);
    host.0.push(candidate(19, 5.0));
    assert_eq!(
        radial_health_changes(&area, &json!(1), None, &mut host),
        Err(EvalError::UnavailableSpatialCandidates)
    );
    assert_eq!(
        evaluate(&area, &mut host),
        Ok(Value::Array(vec![
            Value::Entity(13),
            Value::Entity(19),
            Value::Entity(29)
        ]))
    );
}

#[test]
fn authored_radius_query_filters_and_bounds_spatial_candidates() {
    type CandidateRows = Vec<(u32, [f64; 3])>;
    type RelationRows = Vec<(u32, [f64; 3], Option<u8>)>;

    #[derive(serde::Deserialize)]
    struct ConeQuery {
        direction: [f64; 3],
        half_angle_degrees: Json,
        opponent_of: Option<u32>,
    }

    #[derive(serde::Deserialize)]
    struct Input {
        config: Json,
        center: [f64; 3],
        candidates: CandidateRows,
        budget_after_first_candidate: usize,
        large_radius: Json,
        large_candidates: CandidateRows,
        relation_source: Json,
        relation_candidates: RelationRows,
        relation_queries: Vec<(String, u32)>,
        cone_direction: Json,
        cone_candidates: RelationRows,
        cone_queries: Vec<ConeQuery>,
        visibility_responses: Vec<([f64; 3], bool)>,
    }

    #[derive(serde::Deserialize)]
    struct Expected {
        matches: Vec<u32>,
        large_matches: Vec<u32>,
        relation_matches: Vec<Option<Vec<u32>>>,
        cone_matches: Vec<Vec<u32>>,
        visible_matches: Vec<u32>,
    }

    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u8,
        case_id: String,
        evidence: String,
        input: Input,
        expected: Expected,
    }

    struct Host {
        center: [f64; 3],
        source: u32,
        direction: [f64; 3],
        candidates: Option<Vec<SpatialCandidate>>,
        visibility: Vec<([f64; 3], bool)>,
        rays: Vec<([f64; 3], [f64; 3])>,
    }

    impl Context for Host {
        fn variable(&mut self, variable: Variable) -> Value {
            assert_eq!(variable.scope, Scope::Instance);
            match variable.id {
                61 => Value::Vector(self.center),
                62 => Value::Entity(self.source),
                63 => Value::Vector(self.direction),
                64 => Value::Float(f64::INFINITY),
                id => panic!("unexpected variable {id}"),
            }
        }

        fn spatial_candidates(&mut self) -> Option<&[SpatialCandidate]> {
            self.candidates.as_deref()
        }

        fn spatial_line_clear(&mut self, origin: [f64; 3], target: [f64; 3]) -> Option<bool> {
            self.rays.push((origin, target));
            self.visibility
                .iter()
                .find(|(point, _)| *point == target)
                .map(|(_, clear)| *clear)
        }
    }

    let contract: Contract = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/statescript-entities-in-radius-v1.json"
    )))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    assert_eq!(
        contract.case_id,
        "statescript-entities-in-radius.synthetic.v1"
    );
    assert_eq!(contract.evidence, "synthetic-rust");

    let Input {
        config,
        center,
        candidates,
        budget_after_first_candidate,
        large_radius,
        large_candidates,
        relation_source,
        relation_candidates,
        relation_queries,
        cone_direction,
        cone_candidates,
        cone_queries,
        visibility_responses,
    } = contract.input;
    let spatial_candidates = |rows: CandidateRows| {
        rows.into_iter()
            .map(|(entity, position)| SpatialCandidate {
                entity,
                position,
                team: None,
            })
            .collect()
    };
    let entities =
        |matches: Vec<u32>| Value::Array(matches.into_iter().map(Value::Entity).collect());
    let mut host = Host {
        center,
        source: 0,
        direction: [0.0; 3],
        candidates: Some(spatial_candidates(candidates)),
        visibility: Vec::new(),
        rays: Vec::new(),
    };
    assert_eq!(
        evaluate(&config, &mut host),
        Ok(entities(contract.expected.matches))
    );
    assert_eq!(
        evaluate_with_budget(&config, &mut host, budget_after_first_candidate),
        Err(EvalError::BudgetExceeded {
            offset: 0,
            limit: budget_after_first_candidate
        })
    );

    host.candidates = Some(Vec::new());
    assert_eq!(
        evaluate_with_budget(&config, &mut host, 1),
        Ok(Value::Array(Vec::new()))
    );
    host.candidates = None;
    assert_eq!(
        evaluate(&config, &mut host),
        Err(EvalError::UnavailableSpatialCandidates)
    );

    host.candidates = Some(spatial_candidates(large_candidates));
    let mut large_config = config.clone();
    large_config["radius"] = large_radius;
    assert_eq!(
        evaluate(&large_config, &mut host),
        Ok(entities(contract.expected.large_matches))
    );

    for invalid in [
        SpatialCandidate {
            entity: 0,
            position: center,
            team: None,
        },
        SpatialCandidate {
            entity: 42,
            position: [0.0, f64::NAN, 0.0],
            team: None,
        },
    ] {
        host.candidates = Some(vec![
            SpatialCandidate {
                entity: 17,
                position: center,
                team: None,
            },
            invalid,
        ]);
        assert_eq!(
            evaluate(&config, &mut host),
            Err(EvalError::UnavailableSpatialCandidates)
        );
    }

    let team_candidates = |rows: RelationRows| {
        rows.into_iter()
            .map(|(entity, position, team)| SpatialCandidate {
                entity,
                position,
                team,
            })
            .collect()
    };
    let limit = relation_candidates.len();
    host.candidates = Some(team_candidates(relation_candidates));
    let filter = |relation: &str| json!({"relation": relation, "source": &relation_source});
    let mut filtered = config;
    assert_eq!(
        relation_queries.len(),
        contract.expected.relation_matches.len()
    );
    for ((relation, source), expected) in relation_queries
        .into_iter()
        .zip(contract.expected.relation_matches)
    {
        filtered["filter"] = filter(&relation);
        host.source = source;
        assert_eq!(
            evaluate(&filtered, &mut host),
            expected
                .map(entities)
                .ok_or(EvalError::UnavailableSpatialCandidates),
            "{relation} relative to {source}"
        );
    }

    // An independent source has no allies, but the whole snapshot still costs work.
    host.source = 25;
    filtered["filter"] = filter("ally");
    assert_eq!(
        evaluate_with_budget(&filtered, &mut host, limit),
        Err(EvalError::BudgetExceeded { offset: 0, limit })
    );

    host.source = 0;
    assert!(matches!(
        evaluate(&filtered, &mut host),
        Err(EvalError::MalformedConfig(_))
    ));
    host.source = 31;
    for malformed in [
        Json::Null,
        filter("unknown"),
        json!({"relation":"ally", "source":31}),
        json!({"relation":"ally", "source":&relation_source, "extra":null}),
    ] {
        filtered["filter"] = malformed;
        assert!(matches!(
            evaluate(&filtered, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
    }

    host.candidates.as_mut().unwrap().push(SpatialCandidate {
        entity: 31,
        position: center,
        team: Some(1),
    });
    filtered["filter"] = filter("ally");
    assert_eq!(
        evaluate(&filtered, &mut host),
        Err(EvalError::UnavailableSpatialCandidates)
    );

    let limit = cone_candidates.len();
    host.candidates = Some(team_candidates(cone_candidates));
    let cone = |degrees: Json| json!({"direction": &cone_direction, "half_angle_degrees": degrees});
    assert_eq!(cone_queries.len(), contract.expected.cone_matches.len());
    for (query, expected) in cone_queries.into_iter().zip(contract.expected.cone_matches) {
        host.direction = query.direction;
        filtered["cone"] = cone(query.half_angle_degrees);
        if let Some(source) = query.opponent_of {
            host.source = source;
            filtered["filter"] = filter("opponent");
        } else {
            filtered.as_object_mut().unwrap().remove("filter");
        }
        assert_eq!(
            evaluate(&filtered, &mut host),
            Ok(entities(expected)),
            "{filtered}"
        );
    }
    filtered.as_object_mut().unwrap().remove("filter");

    host.direction = [0.0, -1.0, 0.0];
    filtered["cone"] = cone(json!(0));
    assert_eq!(evaluate(&filtered, &mut host), Ok(Value::Array(Vec::new())));
    assert_eq!(
        evaluate_with_budget(&filtered, &mut host, limit),
        Err(EvalError::BudgetExceeded { offset: 0, limit })
    );

    for direction in [[0.0; 3], [0.0, f64::NAN, 1.0], [0.0, f64::INFINITY, 1.0]] {
        host.direction = direction;
        assert!(matches!(
            evaluate(&filtered, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
    }
    host.direction = [0.0, 0.0, 1.0];
    for malformed in [
        Json::Null,
        json!({"direction": &cone_direction}),
        json!({"direction": &cone_direction, "half_angle_degrees": 45, "extra": null}),
        json!({"direction": 1, "half_angle_degrees": 45}),
        cone(json!(-1)),
        cone(json!(180.1)),
        cone(json!(true)),
        cone(variable(64, Scope::Instance)),
    ] {
        filtered["cone"] = malformed;
        assert!(matches!(
            evaluate(&filtered, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
    }

    assert!(
        host.rays.is_empty(),
        "omission must leave visibility unused"
    );
    host.direction = [0.0, 0.0, 1.0];
    host.source = 31;
    filtered["cone"] = cone(json!(45));
    filtered["filter"] = filter("opponent");
    filtered["line_of_sight"] = json!(false);
    assert_eq!(
        evaluate(&filtered, &mut host),
        Ok(entities(vec![15, 17, 23]))
    );
    assert!(host.rays.is_empty(), "false must leave visibility unused");

    for malformed in [Json::Null, json!(1), json!("true")] {
        filtered["line_of_sight"] = malformed;
        assert!(matches!(
            evaluate(&filtered, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
    }
    assert!(host.rays.is_empty());

    filtered["line_of_sight"] = json!(true);
    let expected_rays: Vec<_> = visibility_responses
        .iter()
        .map(|&(target, _)| (center, target))
        .collect();
    assert_eq!(
        evaluate(&filtered, &mut host),
        Err(EvalError::UnavailableSpatialVisibility)
    );
    assert_eq!(std::mem::take(&mut host.rays), expected_rays[..1]);

    host.visibility = visibility_responses.clone();
    assert_eq!(
        evaluate(&filtered, &mut host),
        Ok(entities(contract.expected.visible_matches))
    );
    assert_eq!(std::mem::take(&mut host.rays), expected_rays);

    // An unavailable final ray cannot return the visible prefix.
    host.visibility.pop().unwrap();
    assert_eq!(
        evaluate(&filtered, &mut host),
        Err(EvalError::UnavailableSpatialVisibility)
    );
    assert_eq!(std::mem::take(&mut host.rays), expected_rays);

    host.visibility = visibility_responses;
    // One query step, every candidate, then one ray before exhaustion.
    let limit = limit + 2;
    assert_eq!(
        evaluate_with_budget(&filtered, &mut host, limit),
        Err(EvalError::BudgetExceeded { offset: 0, limit })
    );
    assert_eq!(std::mem::take(&mut host.rays), expected_rays[..1]);

    let conflicting_position = [0.0, 0.0, 3.0];
    host.visibility.push((conflicting_position, true));
    host.candidates.as_mut().unwrap().push(SpatialCandidate {
        entity: 23,
        position: conflicting_position,
        team: Some(1),
    });
    assert_eq!(
        evaluate(&filtered, &mut host),
        Err(EvalError::UnavailableSpatialVisibility)
    );
    assert!(host.rays.is_empty(), "conflicts precede visibility reads");
}

#[test]
fn client_only_operand_reads_only_the_selected_server_fallback() {
    struct NoClientReads;

    impl Context for NoClientReads {
        fn watches_natives(&self) -> bool {
            true
        }

        fn reference_native(&mut self, _: &str) {
            panic!("the skipped operand must not register a native read");
        }

        fn variable(&mut self, _: Variable) -> Value {
            panic!("the skipped operand must not read variables");
        }

        fn random_u64(&mut self) -> Option<u64> {
            panic!("the skipped operand must not consume randomness");
        }
    }

    let opaque = json!({"$":"ClientOnly", "value":variable(4, Scope::Instance)});
    let mut ctx = NoClientReads;
    assert_eq!(evaluate(&opaque, &mut ctx), Ok(Value::Null));
    assert_eq!(
        evaluate_with_initial(&opaque, &mut ctx, Value::Integer(16)),
        Ok(Value::Integer(16))
    );
    let group =
        |first| json!({"$":"STU_06610EBC", "m_configVars":[first, {"$":"ClientPresentation"}]});
    assert_eq!(
        evaluate_scalar_with_initial(&group(opaque.clone()), &mut ctx, Value::Integer(16)),
        Ok(Value::Integer(16))
    );
    let mut explicit = opaque;
    explicit["server_fallback"] = json!(8);
    assert_eq!(evaluate(&explicit, &mut ctx), Ok(Value::Integer(8)));
    explicit["server_fallback"] = group(json!(9));
    assert_eq!(
        evaluate_scalar_with_initial(&explicit, &mut ctx, Value::Integer(16)),
        Ok(Value::Integer(9))
    );
}

#[test]
fn client_only_operand_is_an_explicit_budgeted_declaration() {
    let wrapper = |value| json!({"$":"ClientOnly", "value":false, "server_fallback": value});
    let nested = wrapper(wrapper(json!(8)));
    assert!(matches!(
        evaluate_with_budget(&nested, &mut EmptyContext, 1),
        Err(EvalError::BudgetExceeded { .. })
    ));
    assert_eq!(
        evaluate_with_budget(&nested, &mut EmptyContext, 2),
        Ok(Value::Integer(8))
    );
    let too_deep = (0..=MAX_CONFIG_DEPTH).fold(json!(8), |value, _| wrapper(value));
    assert_eq!(
        evaluate(&too_deep, &mut EmptyContext),
        Err(EvalError::NestingExceeded)
    );
    for malformed in [
        json!({"$":"ClientOnly", "server_fallback":8}),
        json!({"$":"ClientOnly", "value":false, "serverFallback":8}),
    ] {
        assert_eq!(
            evaluate(&malformed, &mut EmptyContext),
            Err(EvalError::MalformedConfig("invalid client-only operand"))
        );
    }
    let unknown = json!({"$":"ClientPresentation"});
    assert_eq!(
        evaluate(&wrapper(unknown.clone()), &mut EmptyContext),
        evaluate(&unknown, &mut EmptyContext)
    );
    assert!(matches!(
        evaluate(&unknown, &mut EmptyContext),
        Err(EvalError::UnsupportedConfig(_))
    ));
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
    let unknown = expression(json!([255, 0]), &[], &[], &[]);
    assert_eq!(
        evaluate(&unknown, &mut EmptyContext),
        Err(EvalError::UnsupportedOpcode {
            opcode: 255,
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
    let data =
        PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"))
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
        "../../../../../fixtures/behavior/statescript-body-field-v1.json"
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

#[test]
fn segment_clear_queries_finite_endpoints_in_order_and_costs_one_step() {
    const ORIGIN: Variable = Variable {
        scope: Scope::Instance,
        id: 71,
    };
    const TARGET: Variable = Variable {
        scope: Scope::Entity,
        id: 26,
    };

    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u8,
        case_id: String,
        evidence: String,
        input: Input,
        expected: Expected,
    }

    #[derive(serde::Deserialize)]
    struct Input {
        query: Json,
        world: Vec<Sample>,
    }

    #[derive(serde::Deserialize)]
    struct Sample {
        origin: [f64; 3],
        target: [f64; 3],
        visibility: Option<bool>,
    }

    #[derive(serde::Deserialize)]
    struct Expected {
        outputs: Vec<Option<bool>>,
        query_steps: usize,
        shape_error: String,
        endpoint_error: String,
    }

    #[derive(Debug, PartialEq)]
    enum Call {
        Read(Variable),
        Segment([f64; 3], [f64; 3]),
    }

    struct Host {
        origin: Value,
        target: Value,
        reply: Option<bool>,
        calls: Vec<Call>,
    }

    impl Context for Host {
        fn variable(&mut self, variable: Variable) -> Value {
            self.calls.push(Call::Read(variable));
            match variable {
                ORIGIN => self.origin.clone(),
                TARGET => self.target.clone(),
                _ => panic!("unexpected fixture operand {variable:?}"),
            }
        }

        fn spatial_line_clear(&mut self, origin: [f64; 3], target: [f64; 3]) -> Option<bool> {
            self.calls.push(Call::Segment(origin, target));
            self.reply
        }
    }

    let contract: Contract = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/statescript-segment-clear-v1.json"
    )))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    assert_eq!(contract.case_id, "statescript-segment-clear.synthetic.v1");
    assert_eq!(contract.evidence, "synthetic-rust");
    let Input { query, world } = contract.input;
    let Expected {
        outputs,
        query_steps,
        shape_error,
        endpoint_error,
    } = contract.expected;
    assert_eq!(world.len(), outputs.len());
    assert_eq!(query_steps, 1);
    let wrapped = json!({"$":"ClientOnly","value":null,"server_fallback":query});

    for (sample, expected) in world.into_iter().zip(outputs) {
        let mut host = Host {
            origin: Value::Vector(sample.origin),
            target: Value::Vector(sample.target),
            reply: sample.visibility,
            calls: Vec::new(),
        };
        assert_eq!(
            evaluate_with_budget(&query, &mut host, query_steps),
            expected
                .map(Value::Bool)
                .ok_or(EvalError::UnavailableSpatialVisibility)
        );
        assert_eq!(
            std::mem::take(&mut host.calls),
            [
                Call::Read(ORIGIN),
                Call::Read(TARGET),
                Call::Segment(sample.origin, sample.target),
            ]
        );
        // The wrapper consumes a unit before the segment reaches the host.
        let limit = query_steps;
        assert_eq!(
            evaluate_with_budget(&wrapped, &mut host, limit),
            Err(EvalError::BudgetExceeded { offset: 0, limit })
        );
        assert_eq!(host.calls, [Call::Read(ORIGIN), Call::Read(TARGET)]);
    }

    let mut host = Host {
        origin: Value::Vector([1.0, 2.0, 3.0]),
        target: Value::Vector([-3.0, -2.0, -1.0]),
        reply: Some(true),
        calls: Vec::new(),
    };
    for missing in ["origin", "target"] {
        let mut malformed = query.clone();
        malformed.as_object_mut().unwrap().remove(missing);
        assert!(matches!(
            evaluate(&malformed, &mut host),
            Err(EvalError::MalformedConfig(reason)) if reason == shape_error
        ));
        assert!(host.calls.is_empty());
    }
    let mut extra = query.clone();
    extra["unexpected"] = Json::Null;
    assert!(matches!(
        evaluate(&extra, &mut host),
        Err(EvalError::MalformedConfig(reason)) if reason == shape_error
    ));
    assert!(host.calls.is_empty());

    let origin = host.origin.clone();
    let target = host.target.clone();
    for malformed in [
        Value::Null,
        Value::Integer(1),
        Value::Vector([1.0, f64::NAN, 3.0]),
        Value::Vector([1.0, 2.0, f64::INFINITY]),
    ] {
        host.origin = malformed.clone();
        host.target = target.clone();
        assert!(matches!(
            evaluate(&query, &mut host),
            Err(EvalError::MalformedConfig(reason)) if reason == endpoint_error
        ));
        assert_eq!(std::mem::take(&mut host.calls), [Call::Read(ORIGIN)]);

        host.origin = origin.clone();
        host.target = malformed;
        assert!(matches!(
            evaluate(&query, &mut host),
            Err(EvalError::MalformedConfig(reason)) if reason == endpoint_error
        ));
        assert_eq!(
            std::mem::take(&mut host.calls),
            [Call::Read(ORIGIN), Call::Read(TARGET)]
        );
    }
}

#[test]
fn ray_cast_distinguishes_hits_misses_invalid_inputs_and_unavailable_geometry() {
    use serde::Deserialize;

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
        query: Json,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: i64,
        hit: Hit,
    }

    #[derive(Deserialize)]
    struct Hit {
        distance: f64,
        point: [f64; 3],
        normal: [f64; 3],
    }

    #[derive(Deserialize)]
    struct Expected {
        normalized_direction: [f64; 3],
        hit: (f64, [f64; 3], [f64; 3]),
        miss: Vec<()>,
        query_steps: usize,
    }

    #[derive(Debug, PartialEq)]
    enum Call {
        Read(Variable),
        Cast([f64; 3], [f64; 3], f64),
    }

    struct Host {
        operands: [Value; 3],
        reply: Option<Option<SpatialRayHit>>,
        calls: Vec<Call>,
    }

    const VARIABLES: [Variable; 3] = [
        Variable {
            scope: Scope::Instance,
            id: 40,
        },
        Variable {
            scope: Scope::Entity,
            id: 41,
        },
        Variable {
            scope: Scope::Instance,
            id: 42,
        },
    ];

    impl Context for Host {
        fn variable(&mut self, variable: Variable) -> Value {
            self.calls.push(Call::Read(variable));
            let index = VARIABLES.iter().position(|&item| item == variable).unwrap();
            self.operands[index].clone()
        }

        fn spatial_ray_cast(
            &mut self,
            origin: [f64; 3],
            direction: [f64; 3],
            max_distance: f64,
        ) -> Option<Option<SpatialRayHit>> {
            self.calls.push(Call::Cast(origin, direction, max_distance));
            self.reply
        }
    }

    let contract: Contract = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/statescript-ray-cast-v1.json"
    )))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    assert_eq!(contract.case_id, "statescript-ray-cast.synthetic.v1");
    assert_eq!(contract.evidence, "synthetic-rust");
    let Input {
        query,
        origin,
        direction,
        max_distance,
        hit,
    } = contract.input;
    let Expected {
        normalized_direction,
        hit: (distance, point, normal),
        miss,
        query_steps,
    } = contract.expected;
    assert!(miss.is_empty());
    assert_eq!(query_steps, 1);
    let hit = SpatialRayHit {
        distance: hit.distance,
        point: hit.point,
        normal: hit.normal,
    };
    let operands = [
        Value::Vector(origin),
        Value::Vector(direction),
        Value::Integer(max_distance),
    ];
    let mut host = Host {
        operands: operands.clone(),
        reply: None,
        calls: Vec::new(),
    };
    let unavailable = Err(EvalError::UnavailableSpatialRayCast);
    for (reply, expected) in [
        (
            Some(Some(hit)),
            Ok(Value::Array(vec![
                Value::Float(distance),
                Value::Vector(point),
                Value::Vector(normal),
            ])),
        ),
        (Some(None), Ok(Value::Array(Vec::new()))),
        (None, unavailable.clone()),
        (
            Some(Some(SpatialRayHit {
                distance: 7.0,
                ..hit
            })),
            unavailable.clone(),
        ),
        (
            Some(Some(SpatialRayHit {
                point: [f64::NAN; 3],
                ..hit
            })),
            unavailable.clone(),
        ),
        (
            Some(Some(SpatialRayHit {
                normal: [0.0; 3],
                ..hit
            })),
            unavailable,
        ),
    ] {
        host.reply = reply;
        assert_eq!(
            evaluate_with_budget(&query, &mut host, query_steps),
            expected
        );
        assert_eq!(
            std::mem::take(&mut host.calls),
            [
                Call::Read(VARIABLES[0]),
                Call::Read(VARIABLES[1]),
                Call::Read(VARIABLES[2]),
                Call::Cast(origin, normalized_direction, max_distance as f64),
            ]
        );
    }

    let wrapped = json!({"$":"ClientOnly","value":null,"server_fallback":query});
    assert_eq!(
        evaluate_with_budget(&wrapped, &mut host, query_steps),
        Err(EvalError::BudgetExceeded {
            offset: 0,
            limit: query_steps
        })
    );
    assert_eq!(std::mem::take(&mut host.calls), VARIABLES.map(Call::Read));

    for (index, invalid) in [
        (0, Value::Vector([f64::NAN, 0.0, 0.0])),
        (1, Value::Vector([0.0; 3])),
        (1, Value::Vector([f64::INFINITY, 0.0, 0.0])),
        (2, Value::Integer(0)),
        (2, Value::Float(-1.0)),
        (2, Value::Float(f64::INFINITY)),
        (2, Value::Bool(true)),
    ] {
        host.operands = operands.clone();
        host.operands[index] = invalid;
        assert!(matches!(
            evaluate(&query, &mut host),
            Err(EvalError::MalformedConfig(_))
        ));
        let expected_reads: Vec<_> = VARIABLES[..=index]
            .iter()
            .copied()
            .map(Call::Read)
            .collect();
        assert_eq!(std::mem::take(&mut host.calls), expected_reads);
    }
    let mut malformed = query.clone();
    malformed["unexpected"] = Json::Null;
    assert!(matches!(
        evaluate(&malformed, &mut host),
        Err(EvalError::MalformedConfig(_))
    ));
    assert!(host.calls.is_empty());
}

/// Reference-parity host for the component policy (runtime.py 685..693 and
/// 1295..1307): with `strict` false every unresolved native lands on the
/// `default_native` warn-and-null path; with `strict` true every one of
/// them is a report. The tag data and filter flags stay absent either way,
/// exactly like a body whose components never arrived.
#[derive(Clone, Copy)]
struct PolicyHost {
    strict: bool,
}

impl PolicyHost {
    fn evaluate(&self, config: &Json) -> Result<Value, EvalError> {
        let mut host = *self;
        crate::script_expr::evaluate(config, &mut host)
    }
}

impl Context for PolicyHost {
    fn strict(&self) -> bool {
        self.strict
    }
    fn owner(&mut self) -> Value {
        Value::Entity(123)
    }
    fn has_target_tag(&mut self, _entity: u32, _tag: u64) -> Option<bool> {
        None
    }
    fn entity_filter_flags(&mut self, _entity: u32) -> Option<u64> {
        None
    }
}

/// The four authored operand shapes pinned from the real 104319 report
/// sites (bare tutorial predicate, bare mode-entity reader, owner target-tag
/// query with the authored guid string, owner filter with the full any-mask
/// and one excluded state tag). Positions and identifiers carry no meaning
/// here; only the shapes do.
fn policy_probe_queries() -> [(&'static str, Json); 4] {
    [
        (
            "tutorial predicate",
            json!({"$":"STU_A77AC2AC","m_EE729DCB":0}),
        ),
        (
            "mode entity reader",
            json!({"$":"STU_A3DEF368","m_EE729DCB":0}),
        ),
        (
            "owner target tag",
            json!({"$":"STU_25ECB55F","m_EE729DCB":0,
            "m_88B0050A":"0x0D60000000000042",
            "m_entity":{"$":"STU_B5A0CAF0","m_EE729DCB":0}}),
        ),
        (
            "owner entity filter",
            json!({"$":"STU_AC829876","m_EE729DCB":0,
            "m_entity":{"$":"STU_B5A0CAF0","m_EE729DCB":0},
            "m_filter":{"$":"STUConfigVarFilter","m_93FA1642":null,
                "m_84065FD1":1073741823,"m_1E14CCA8":0,"m_4EA686DB":0,
                "m_27CF71D5":0,"m_66747E7D":0,"m_89831602":0,"m_condition":null,
                "m_9C7BD8B8":{"$":"STU_357DABBB","m_resourceKey":null,"m_EE729DCB":0,
                    "m_7D7C86A1":null},
                "m_B486603E":null,
                "m_8B2A4DF3":{"$":"STU_357DABBB","m_resourceKey":null,"m_EE729DCB":0,
                    "m_7D7C86A1":"0x0D60000000000069"},
                "m_F61726FE":null,"m_B40362FC":null,"m_EE729DCB":0}}),
        ),
    ]
}

#[test]
fn the_normal_policy_answers_unresolved_natives_as_typed_null() {
    for (label, query) in policy_probe_queries() {
        assert_eq!(
            PolicyHost { strict: false }.evaluate(&query),
            Ok(Value::Null),
            "{label} must land on the reference default_native null"
        );
    }
}

#[test]
fn the_strict_policy_keeps_every_unresolved_native_a_report() {
    for (label, query) in policy_probe_queries() {
        let class = class(&query).unwrap_or_default();
        assert!(
            matches!(
                PolicyHost { strict: true }.evaluate(&query),
                Err(EvalError::UnsupportedConfig(ref rejected)) if rejected == class
            ),
            "{label} must stay loud under the strict policy: {:?}",
            PolicyHost { strict: true }.evaluate(&query)
        );
    }
}

#[test]
fn the_normal_policy_nulls_the_tag_query_but_keeps_a_resolved_answer() {
    // The tag query answers null only while the component is absent; a body
    // that does carry its (empty) tag set keeps answering the boolean, in
    // both policies (scene.py 1674..1677 reads the list, not the policy).
    struct Tagged(PolicyHost, bool);
    impl Context for Tagged {
        fn strict(&self) -> bool {
            self.0.strict
        }
        fn owner(&mut self) -> Value {
            Value::Entity(123)
        }
        fn has_target_tag(&mut self, _entity: u32, tag: u64) -> Option<bool> {
            Some(self.1 && tag == 0x0d60_0000_0000_0042)
        }
    }
    let query = json!({"$":"STU_25ECB55F","m_EE729DCB":0,
        "m_88B0050A":"0x0D60000000000042",
        "m_entity":{"$":"STU_B5A0CAF0","m_EE729DCB":0}});
    for strict in [false, true] {
        for (present, answer) in [(true, true), (false, false)] {
            let mut host = Tagged(PolicyHost { strict }, present);
            assert_eq!(
                crate::script_expr::evaluate(&query, &mut host),
                Ok(Value::Bool(answer)),
                "resolved tag data answers the boolean under both policies (strict {strict})"
            );
        }
    }
}

#[test]
fn track_b2_config_vars_evaluate_generically() {
    struct Host;
    impl Context for Host {
        fn static_target(&mut self, tag: u64) -> Option<u32> {
            (tag == 0x0D60_0000_0000_02AD).then_some(77)
        }
        fn variable(&mut self, variable: Variable) -> Value {
            if variable.id == 61 {
                Value::Integer(42)
            } else {
                Value::Null
            }
        }
    }
    let mut host = Host;
    assert_eq!(
        evaluate(
            &json!({"$": "STUConfigVarAnimAlias", "m_animAlias": "0x0C800000000000A9"}),
            &mut host
        ),
        Ok(Value::Asset(0x0C80_0000_0000_00A9))
    );
    assert_eq!(
        evaluate(
            &json!({"$": "STUConfigVarDataFlow", "m_5A6D4FBD": "0x04800000000005E2"}),
            &mut host
        ),
        Ok(Value::Asset(0x0480_0000_0000_05E2))
    );
    assert_eq!(
        evaluate(&json!({"$": "STU_BD2D717B", "m_B257A3A4": 1}), &mut host),
        Ok(Value::Bool(true))
    );
    assert_eq!(
        evaluate(&json!({"$": "STU_BD2D717B", "m_B257A3A4": 0}), &mut host),
        Ok(Value::Bool(false))
    );
    assert_eq!(
        evaluate(
            &json!({
                "$": "STU_8529C204",
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": 61},
                "m_value": {"$": "STUConfigVarInt", "m_value": 9}
            }),
            &mut host
        ),
        Ok(Value::Integer(9))
    );
    assert_eq!(
        evaluate(
            &json!({
                "$": "STU_EB216A0B",
                "m_filter": null,
                "m_8B2A4DF3": null,
                "m_9C7BD8B8": null,
                "m_B486603E": {"$": "STU_357DABBB", "m_7D7C86A1": "0x0D600000000002AD"}
            }),
            &mut host
        ),
        Ok(Value::Entity(77))
    );
}
