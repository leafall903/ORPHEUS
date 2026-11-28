use serde::Deserialize;

use super::*;

#[derive(Deserialize)]
struct Contract {
    schema_version: u8,
    case_id: String,
    evidence: String,
    input: Json,
    expected: [ExpectedInstance; 2],
}

#[derive(Deserialize)]
struct ExpectedInstance {
    graph: u32,
    instance: u16,
    variables: BTreeMap<u32, i64>,
}

struct LocalOnlyWorld;

impl World for LocalOnlyWorld {
    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }

    fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }

    fn send_message(
        &mut self,
        _site: Site,
        _target: u32,
        _message: u64,
        _parameters: &Variables,
    ) -> bool {
        panic!("local messages must not send through the World")
    }

    fn route_game_message(&mut self, _site: Site, _message: u64, _parameters: &Variables) -> bool {
        panic!("local messages must not route through the World")
    }
}

#[test]
fn authored_local_message_snapshots_parameters_and_uses_declared_receiver_order() {
    let contract: Contract = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/script-local-message-v1.json"
    )))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    assert_eq!(contract.case_id, "script-local-message.synthetic.v1");
    assert_eq!(contract.evidence, "synthetic-rust");

    let bundle = serde_json::to_vec(&contract.input).unwrap();
    let graphs = GraphStore::from_json(&bundle).unwrap();
    let roots = contract
        .expected
        .iter()
        .map(|expected| Root::new(expected.instance, expected.graph))
        .collect();
    let snapshot = initialize(
        &graphs,
        4242,
        Variables::new(),
        roots,
        &mut LocalOnlyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();

    assert!(
        snapshot.diagnostics.is_empty(),
        "{:?}",
        snapshot.diagnostics
    );
    assert!(snapshot.variables.is_empty());

    // The sender overwrites its source variable in the continuation. Both
    // receivers must get the earlier value. The second receiver also reads
    // the first receiver's write, even though it has a smaller node position.
    for expected in contract.expected {
        let instance = &snapshot.instances[&expected.instance];
        assert_eq!(instance.graph, expected.graph);
        for (variable, value) in &expected.variables {
            assert_eq!(
                instance.variables.get(variable),
                Some(&Value::Integer(*value))
            );
        }
        assert_eq!(
            instance.variables.len(),
            expected.variables.len(),
            "unexpected writes in graph {}: {:?}",
            expected.graph,
            instance.variables
        );
    }
}

#[test]
fn message_receiver_resolves_all_bindings_before_writes_or_state_exit() {
    #[derive(Deserialize)]
    struct BindingFixture {
        entity: u32,
        sender: u32,
        message: u64,
        parameters: BTreeMap<u32, i64>,
        initial: BTreeMap<u32, i64>,
        outputs: Vec<(String, u32)>,
        child_abort_write: i64,
        malformed_issue: String,
        cases: Vec<BindingCase>,
        accepted: BTreeMap<u32, Option<i64>>,
    }

    #[derive(Deserialize)]
    struct BindingCase {
        name: String,
        stateful: bool,
        malformed: Option<Malformed>,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "snake_case")]
    enum Malformed {
        Parameter,
        Sender,
    }

    const GRAPH: u32 = 913;
    const ROOT: u16 = 1;
    const CONTINUED: u32 = 23;
    const SENDER_OUTPUT: u32 = 28;
    const SECOND_SENDER_OUTPUT: u32 = 29;

    let fixture: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/script-local-message-v1.json"
    )))
    .unwrap();
    let fixture: BindingFixture =
        serde_json::from_value(fixture["receiver_bindings"].clone()).unwrap();
    let parameters: Variables = fixture
        .parameters
        .into_iter()
        .map(|(key, value)| (key, Value::Integer(value)))
        .collect();

    for case in fixture.cases {
        let mut fields = json!({
            "m_gameMessage": {"m_gameMessage": format!("0x{:016X}", fixture.message)},
            "m_params": fixture.outputs.iter().map(|(key, variable)| json!({
                "m_B5051BCE": key, "m_out_Var": var(*variable, false)
            })).collect::<Vec<_>>(),
        });
        if case.stateful {
            fields["m_33DA5163"] = plug(&[(2, "m_beginPlug")], &[1]);
            fields["m_452862E6"] = plug(&[(1, "m_inPlug")], &[]);
        } else {
            fields["m_outPlug"] = plug(&[(1, "m_inPlug")], &[]);
            fields["m_B60ABDA4"] = var(SENDER_OUTPUT, false);
            fields["m_FF703F20"] = var(SECOND_SENDER_OUTPUT, false);
        }
        match case.malformed {
            Some(Malformed::Parameter) => {
                fields["m_params"][1]["m_out_Var"]["m_identifier"] = json!(-1)
            }
            Some(Malformed::Sender) => fields["m_FF703F20"]["m_identifier"] = json!(-1),
            None => (),
        }
        let class = if case.stateful {
            "STUStatescriptStateGameMessageEntry"
        } else {
            "STUStatescriptEntryGameMessage"
        };
        let continuation = node(
            1,
            "STUStatescriptActionSetVar",
            None,
            json!({"m_out_Var": var(CONTINUED, false), "m_value": true}),
        );
        let mut nodes = vec![
            node(0, class, case.stateful.then_some(0), fields),
            continuation,
        ];
        let (states, entries) = if case.stateful {
            nodes.extend([
                node(
                    2,
                    "STUStatescriptState",
                    Some(1),
                    json!({"m_onAbortPlug": plug(&[(3, "m_inPlug")], &[])}),
                ),
                node(
                    3,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var": var(21, false), "m_value": fixture.child_abort_write}),
                ),
            ]);
            (json!([0, 2]), json!([]))
        } else {
            (json!([]), json!([0]))
        };
        let graph = graph(GRAPH, nodes, states, entries);
        let mut root = Root::listener(ROOT, GRAPH, case.stateful.then_some(0));
        root.variables = fixture
            .initial
            .iter()
            .map(|(&key, &value)| (key, Value::Integer(value)))
            .collect();
        let mut component = Component::start(
            Arc::new(store(vec![graph])),
            fixture.entity,
            Variables::new(),
            vec![root],
            0,
            &mut LocalOnlyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let before = component.snapshot().unwrap();
        assert!(before.diagnostics.is_empty(), "{}", case.name);
        if case.stateful {
            assert!(before.instances[&ROOT].states[&1].active, "{}", case.name);
        }

        assert_eq!(
            component
                .queue_entity_message_from(fixture.sender, fixture.message, parameters.clone())
                .unwrap(),
            1,
            "{}",
            case.name
        );
        component.run_now(&mut LocalOnlyWorld).unwrap();

        let snapshot = component.snapshot().unwrap();
        let instance = &snapshot.instances[&ROOT];
        let rejected = case.malformed.is_some();
        for (&variable, &value) in &fixture.initial {
            let expected = if rejected {
                Value::Integer(value)
            } else {
                fixture.accepted[&variable].map_or(Value::Null, Value::Integer)
            };
            assert_eq!(
                instance.variables.get(&variable),
                Some(&expected),
                "{}",
                case.name
            );
        }
        if rejected {
            assert!(
                matches!(
                    snapshot.diagnostics.as_slice(),
                    [Diagnostic { issue: Issue::Expression(crate::script_expr::EvalError::MalformedConfig(issue)), .. }]
                        if *issue == fixture.malformed_issue
                ),
                "{}: {:?}",
                case.name,
                snapshot.diagnostics
            );
        } else {
            assert!(snapshot.diagnostics.is_empty(), "{}", case.name);
        }
        assert_eq!(
            instance.variables.get(&CONTINUED),
            (!rejected).then_some(&Value::Bool(true)),
            "{}",
            case.name
        );
        let expected_sender =
            (!case.stateful && !rejected).then_some(Value::Entity(fixture.sender));
        for key in [SENDER_OUTPUT, SECOND_SENDER_OUTPUT] {
            assert_eq!(
                instance.variables.get(&key),
                expected_sender.as_ref(),
                "{}",
                case.name
            );
        }
        if case.stateful {
            assert_eq!(instance.states[&1].active, rejected, "{}", case.name);
        }
    }
}
#[test]
fn body_death_exposes_only_the_terminal_hits_authored_tags_to_the_death_entry() {
    let fixture = graph(
        67,
        vec![node(
            0,
            "STUStatescriptEntryGameMessage",
            None,
            json!({
                "m_gameMessage":{"m_gameMessage":"0x024000000000003E"},
                "m_params":[{"m_B5051BCE":"0x0A4000000000003D","m_out_Var":var(607,true)}]
            }),
        )],
        json!([]),
        json!([0]),
    );
    let mut component = Component::start(
        Arc::new(store(vec![fixture])),
        41,
        Variables::new(),
        vec![Root::new(1, 67)],
        10,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let mut tags = HitTags::EMPTY;
    assert!(tags.push(0x0d80_0000_0000_3a01));
    assert!(tags.push(0x0d80_0000_0000_4af2));
    assert_eq!(
        component
            .queue_body_death_with_tags(42, false, None, tags, None)
            .unwrap(),
        1
    );
    component.run_now(&mut EmptyWorld).unwrap();
    assert_eq!(
        component.snapshot().unwrap().variables[&607],
        Value::Array(vec![
            Value::Asset(0x0d80_0000_0000_3a01),
            Value::Asset(0x0d80_0000_0000_4af2),
        ])
    );
    assert_eq!(component.queue_body_death(0, false, None, None).unwrap(), 1);
    component.run_now(&mut EmptyWorld).unwrap();
    assert_eq!(
        component.snapshot().unwrap().variables[&607],
        Value::Array(vec![])
    );
}
