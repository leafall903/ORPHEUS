use super::*;

const ACTION: &str = "STU_FF3471E9";
const OWNER: u32 = 735;
const MARKER: u32 = 901;

struct ImpulseHost {
    accepts: bool,
    attempts: Vec<(Site, u32, f64, [f64; 3])>,
    edits: Vec<(Site, u32, Value, bool)>,
}

impl World for ImpulseHost {
    fn edit_velocity(&mut self, site: Site, frame: u32, value: Value, cap: bool) -> bool {
        self.edits.push((site, frame, value, cap));
        self.accepts
    }

    fn apply_velocity_impulse(
        &mut self,
        site: Site,
        frame: u32,
        vertical: f64,
        delta: [f64; 3],
    ) -> bool {
        self.attempts.push((site, frame, vertical, delta));
        self.accepts
    }

    fn position(&mut self, _: u32) -> Option<[f64; 3]> {
        None
    }

    fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
        None
    }
}

#[test]
fn ordered_velocity_impulse_admits_both_operands_atomically() {
    let contract: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/script-vertical-velocity-impulse-v1.json"
    )))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["case_id"], "script-vertical-velocity-impulse-v1");
    assert_eq!(contract["evidence"], "rust-synthetic");
    let input = &contract["input"];
    let delta: [f64; 3] = serde_json::from_value(input["delta"].clone()).unwrap();
    let frame = input["frame"].as_u64().unwrap() as u32;
    let vertical = input["vertical_reset"].as_f64().unwrap();

    for case in contract["expected"]["cases"].as_array().unwrap() {
        let index = case["graph"].as_u64().unwrap() as u32;
        let pos = case["position"].as_u64().unwrap() as u16;
        let kind = case["kind"].as_str().unwrap();
        let mut rows = [
            json!({
                "$": "STU_9AF0FA80",
                "m_A9561CA0": 2,
                "m_9B7A63EA": {"$":"STUConfigVarInt","m_value":input["vertical_reset"]},
            }),
            json!({
                "$": "STU_4635C5E2",
                "m_CFEC5F76": 1,
                "m_9B7A63EA": {
                    "$":"STUConfigVarVec3","m_value":{"X":delta[0],"Y":delta[1],"Z":delta[2]},
                },
            }),
        ];
        match kind {
            "reverse_rows" => rows.swap(0, 1),
            "wrong_operand" => rows[1]["m_9B7A63EA"] = json!(3),
            "prioritized" => rows[0]["m_priority"] = json!(10),
            _ => {}
        }
        let nodes = vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(pos,"m_inPlug")],&[])}),
            ),
            node(
                pos,
                ACTION,
                None,
                json!({
                    "m_BC5E91CF": rows,
                    "m_outPlug":plug(&[(pos + 1,"m_inPlug")],&[])
                }),
            ),
            node(
                pos + 1,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(MARKER,true),"m_value":true}),
            ),
        ];
        let mut graph = graph(index, nodes, json!([]), json!([0]));
        graph["nodes"][1]["fields"]["m_ADEB6E05"] = json!(true);
        graph["items"] = json!(pos + 2);
        let mut host = ImpulseHost {
            accepts: kind != "no_host",
            attempts: Vec::new(),
            edits: Vec::new(),
        };
        let component = Component::start(
            Arc::new(store(vec![graph])),
            OWNER,
            Variables::new(),
            vec![Root::new(1, index)],
            frame,
            &mut host,
            MAX_INIT_WORK,
        )
        .unwrap();
        let site = Site {
            entity: OWNER,
            instance: 1,
            graph: index,
            node: Some(pos),
        };
        let snapshot = component.snapshot().unwrap();
        let expected_issue = match kind {
            "accepted" => None,
            "no_host" => Some(Issue::UnsupportedField("velocity impulse service")),
            "wrong_operand" => Some(Issue::Expression(EvalError::MalformedConfig(
                "velocity impulse operands",
            ))),
            "reverse_rows" | "prioritized" => Some(Issue::UnsupportedAction(ACTION.into())),
            _ => panic!("unknown fixture case {kind}"),
        };
        let issues: Vec<_> = snapshot
            .diagnostics
            .iter()
            .map(|diagnostic| (diagnostic.site, diagnostic.issue.clone()))
            .collect();
        assert_eq!(
            issues.as_slice(),
            expected_issue.map(|issue| (site, issue)).as_slice()
        );
        let expected_attempt =
            matches!(kind, "accepted" | "no_host").then_some((site, frame, vertical, delta));
        assert_eq!(
            host.attempts.as_slice(),
            expected_attempt.as_slice(),
            "{kind}"
        );
        assert_eq!(
            snapshot.variables.get(&MARKER),
            (kind == "accepted").then_some(&Value::Bool(true)),
            "{kind}",
        );
    }
}

#[test]
fn single_row_velocity_edits_follow_the_field_schema_on_a_synthetic_graph() {
    let fixture: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/script-local-velocity-edit-v1.json"
    )))
    .unwrap();
    let graph_id = fixture["input"]["graph"].as_u64().unwrap() as u32;
    let frame = fixture["input"]["frame"].as_u64().unwrap() as u32;
    for case in fixture["cases"].as_array().unwrap() {
        let valid = case["supported"].as_bool().unwrap();
        let nodes = vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug": plug(&[(3, "m_inPlug")], &[])}),
            ),
            node(
                3,
                ACTION,
                None,
                json!({"m_BC5E91CF": [case["row"]], "m_outPlug": plug(&[(4, "m_inPlug")], &[])}),
            ),
            node(
                4,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(MARKER,true),"m_value":true}),
            ),
        ];
        let mut host = ImpulseHost {
            accepts: true,
            attempts: Vec::new(),
            edits: Vec::new(),
        };
        let mut test_graph = graph(graph_id, nodes, json!([]), json!([0]));
        test_graph["items"] = json!(5);
        test_graph["nodes"][1]["fields"]["m_ADEB6E05"] = json!(true);
        let component = Component::start(
            Arc::new(store(vec![test_graph])),
            OWNER,
            Variables::new(),
            vec![Root::new(1, graph_id)],
            frame,
            &mut host,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(
            snapshot.initialization_complete(),
            valid,
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            snapshot.variables.get(&MARKER),
            valid.then_some(&Value::Bool(true))
        );
        assert_eq!(host.edits.len(), usize::from(valid));
        assert!(host.attempts.is_empty());
        if let Some((site, applied, value, cap)) = host.edits.pop() {
            let expected = if let Some(cap) = case["cap"].as_f64() {
                Value::Float(cap)
            } else {
                Value::Vector(serde_json::from_value(case["vector"].clone()).unwrap())
            };
            assert_eq!((site.graph, site.node, applied), (graph_id, Some(3), frame));
            assert_eq!(cap, case["cap"].is_f64());
            assert_eq!(value, expected);
        }
    }
}
