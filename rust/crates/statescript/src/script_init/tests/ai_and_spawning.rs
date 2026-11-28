use super::*;
use crate::script_runtime::PoolKind;

#[test]
fn ai_weapon_fire_exposes_only_active_self_targeted_slots() {
    let fixture: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/ai-weapon-fire-slot-v1.json"
    )))
    .unwrap();
    assert_eq!(fixture["case_id"], "ai-weapon-fire-slot-v1");
    for row in fixture["input"].as_array().unwrap() {
        let target = if row["self_target"] == true {
            json!({"$": "STU_B5A0CAF0"})
        } else {
            var(51, true)
        };
        let graphs = Arc::new(store(vec![graph(
            301,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug": plug(&[(1, "m_beginPlug")], &[])}),
                ),
                node(
                    1,
                    "STU_D7F9E1C3",
                    Some(0),
                    json!({
                        "m_14EDDDA7": target,
                        "m_20BCEE62": {"$": "STUConfigVarBool", "m_value": row["enabled"]},
                        "m_A60150E2": {"$": "STUConfigVarInt", "m_value": row["slot"]}
                    }),
                ),
            ],
            json!([1]),
            json!([0]),
        )]));
        let mut world = ReferenceWorld;
        let mut body = Component::start(
            graphs,
            0xa100_0010,
            Variables::from([(51, Value::Entity(0xa100_0011))]),
            vec![Root::new(1, 301)],
            20,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert_eq!(
            json!(body.ai_fire_slots().collect::<Vec<_>>()),
            row["held_slots"],
            "{row}"
        );
        body.debug_end_state(1, 0, &mut world).unwrap();
        assert_eq!(
            json!(body.ai_fire_slots().collect::<Vec<_>>()),
            fixture["expected_after_exit"]
        );
    }
}

#[test]
fn authored_ai_navigation_begin_and_exit_bind_only_the_current_destination() {
    struct WalkWorld(Option<f64>);
    impl World for WalkWorld {
        fn position(&mut self, _: u32) -> Option<[f64; 3]> {
            None
        }

        fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
            None
        }

        fn ai_walk_speed(&self, _: Site) -> Option<f64> {
            self.0
        }
    }
    let fixture: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/ai-navigation-destination-v1.json"
    )))
    .unwrap();
    for input in fixture["cases"].as_array().unwrap() {
        let destination = serde_json::from_value(input["destination"].clone()).unwrap();
        let mut world = WalkWorld(input["walk_speed"].as_f64());
        let data = graph(
            input["graph"].as_u64().unwrap() as u32,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug": plug(&[(1, "m_beginPlug")], &[])}),
                ),
                node(
                    1,
                    "STU_ECAEE401",
                    Some(0),
                    json!({
                        "m_6F4283C8": fixture["navigation_mode"],
                        "m_subgraphPlug": plug(&[(2, "m_beginPlug")], &[1]),
                    }),
                ),
                node(
                    2,
                    "STU_194D744E",
                    Some(1),
                    json!({
                        "m_89AABA94": 0,
                        "m_BD2A95EE": var(51, true),
                    }),
                ),
            ],
            json!([1, 2]),
            json!([0]),
        );
        let index = input["graph"].as_u64().unwrap() as u32;
        let mut component = Component::start(
            Arc::new(store(vec![data])),
            0xc000_1001,
            Variables::from([(51, Value::Vector(destination))]),
            vec![Root::new(1, index)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let requests = |body: &Component| {
            body.snapshot().unwrap().instances[&1]
                .states
                .values()
                .filter_map(State::move_request)
                .collect::<Vec<_>>()
        };
        let expected = world
            .0
            .map(|speed| (destination, speed))
            .into_iter()
            .collect::<Vec<_>>();
        assert_eq!(requests(&component), expected);
        if !expected.is_empty() {
            component.debug_end_state(1, 0, &mut world).unwrap();
            assert!(requests(&component).is_empty());
        }
    }
}

#[test]
fn ai_behavior_inputs_update_from_variables_and_native_reads_and_clean_up() {
    #[derive(Default)]
    struct BehaviorWorld {
        records: Vec<(AiBehaviorRequest, bool)>,
        health: f64,
    }
    impl World for BehaviorWorld {
        fn ai_behavior_request(
            &mut self,
            _: Site,
            record: AiBehaviorRequest,
            active: bool,
        ) -> Option<bool> {
            self.records.push((record, active));
            Some(true)
        }
        fn body_health(&self, _: Site, _: Option<PoolKind>, _: bool) -> Option<f64> {
            Some(self.health)
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }
    for class in ["STU_B61E6681", "STU_CFA338CB", "STU_600E3C59"] {
        for abort in [false, true] {
            let mut world = BehaviorWorld {
                health: 100.0,
                ..Default::default()
            };
            let data = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                    node(
                        1,
                        class,
                        Some(0),
                        json!({"m_weight":var(100,true),"m_88881C38":{"$":"STUConfigVarBool","m_value":true},
                    "m_36998F8A":{"$":"STU_32E6E4D8","m_entity":{"$":"STU_B5A0CAF0"}},
                    "m_157D72D1":{"$":"STUConfigVarFloat","m_value":90},"m_DD792FF0":2}),
                    ),
                    node(
                        2,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                    "m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                    ),
                ],
                json!([1]),
                json!([0, 2]),
            );
            let mut component = Component::start(
                Arc::new(store(vec![data])),
                0xc000_1001,
                Variables::from([(100, Value::Float(2.0))]),
                vec![Root::new(1, 42)],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(component.snapshot().unwrap().initialization_complete());
            let first = world.records[0].0;
            assert_eq!(first.priority, 0.0);
            assert!(first.flag);
            assert_eq!(first.shared, [100.0, 0.0]);
            match class {
                "STU_B61E6681" => assert_eq!(first.inputs, [0.0, 2.0, 0.0, 0.0, 0.0]),
                "STU_CFA338CB" => assert_eq!(
                    first.inputs,
                    [
                        0.0,
                        0.0,
                        f64::from((90.0 * f64::from((std::f64::consts::PI / 180.0) as f32)) as f32),
                        0.0,
                        0.0
                    ]
                ),
                _ => assert_eq!(first.inputs, [2.0, 2.0, 2.0, f64::from(0.1f32), 1.0]),
            }
            component
                .set_entity_variable(100, Value::Float(4.0), &mut world)
                .unwrap();
            component.run_now(&mut world).unwrap();
            if class != "STU_CFA338CB" {
                let changed = world.records.last().unwrap().0;
                assert_eq!(
                    changed.inputs[if class == "STU_B61E6681" { 1 } else { 2 }],
                    4.0
                );
            }
            world.health = 50.0;
            component.advance_frame(1, &mut world).unwrap();
            assert_eq!(world.records.last().unwrap().0.shared, [50.0, 0.0]);
            if abort {
                component
                    .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                    .unwrap();
                component.run_now(&mut world).unwrap();
            }
            Vm::new(&component.graphs, &mut world, &mut component.machine)
                .destroy(1)
                .unwrap();
            assert_eq!(
                world.records.iter().filter(|(_, active)| !*active).count(),
                1
            );
        }
    }
}

#[test]
fn junkenstein_base_states_keep_plugs_abort_and_reentry_within_authored_scope() {
    for (index, class) in [
        (0x0c87, "STU_6A6BBB8D"),
        (0x0c87, "STU_918B3FA9"),
        (0x0c87, "STU_14611C1B"),
        (0x0c87, "STU_200B27C5"),
        (0x0c87, "STU_D52B9026"),
        (0x0c87, "STU_4531992F"),
        (0x0c87, "STU_55F72184"),
        (0x0c87, "STU_632B74B9"),
        (0x0cf3, "STU_724DFBC6"),
        (0x0cf3, "STU_531AA630"),
    ] {
        let fixture = |index| {
            graph(
                index,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                    node(
                        1,
                        class,
                        Some(0),
                        json!({"m_onBeginPlug":plug(&[(3,"m_inPlug")],&[]),
                "m_onEndPlug":plug(&[(4,"m_inPlug")],&[]),"m_subgraphPlug":plug(&[(2,"m_beginPlug")],&[1])}),
                    ),
                    node(2, "STUStatescriptState", Some(1), json!({})),
                    node(
                        3,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100,true),"m_value":true}),
                    ),
                    node(
                        4,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101,true),"m_value":true}),
                    ),
                    node(
                        5,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000002"},
                "m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                    ),
                    node(
                        6,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                "m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                ],
                json!([1, 2]),
                json!([0, 5, 6]),
            )
        };
        let mut component = Component::start(
            Arc::new(store(vec![fixture(index)])),
            42,
            Variables::new(),
            vec![Root::new(1, index)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{class}: {:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.variables[&100], Value::Bool(true));
        assert!(snapshot.instances[&1].states[&1].active);
        component
            .queue_message(1, 0x0240_0000_0000_0002, Variables::new())
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&101], Value::Bool(true));
        assert!(
            snapshot.instances[&1]
                .states
                .values()
                .all(|state| !state.active)
        );
        component
            .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        let state = &component.snapshot().unwrap().instances[&1].states[&0];
        assert!(state.active);
        assert_eq!(state.activation, 2);
        let foreign = Component::start(
            Arc::new(store(vec![fixture(42)])),
            42,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(
            foreign
                .snapshot()
                .unwrap()
                .diagnostics
                .iter()
                .any(|diagnostic| diagnostic.issue == Issue::UnsupportedState(class.into()))
        );
    }
}

#[test]
fn ai_controller_base_states_are_limited_to_the_authored_graph_scope() {
    for (index, class, supported) in [
        (0x0c87, "STU_6A6BBB8D", true),
        (42, "STU_6A6BBB8D", false),
        (0x0cf3, "STU_531AA630", true),
        (42, "STU_531AA630", false),
    ] {
        let data = graph(
            index,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                ),
                node(1, class, Some(0), json!({})),
            ],
            json!([1]),
            json!([0]),
        );
        let component = Component::start(
            Arc::new(store(vec![data])),
            0xc000_1001,
            Variables::new(),
            vec![Root::new(1, index)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert_eq!(
            component.snapshot().unwrap().initialization_complete(),
            supported
        );
    }
}

#[test]
fn ai_behavior_rejects_priority_resources_of_an_unrelated_type() {
    let data = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
            ),
            node(
                1,
                "STU_B61E6681",
                Some(0),
                json!({"m_priority":{"$":"STU_8A491388","m_priority":"0x0D80000000000003","m_resourceKey":null}}),
            ),
        ],
        json!([1]),
        json!([0]),
    );
    let component = Component::start(
        Arc::new(store(vec![data])),
        0xc000_1001,
        Variables::new(),
        vec![Root::new(1, 42)],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert!(matches!(
        component.snapshot().unwrap().diagnostics.as_slice(),
        [Diagnostic {
            issue: Issue::UnsupportedField("AI behavior priority resource"),
            ..
        }]
    ));
}

#[test]
#[ignore = "requires the external 104319 Junkenstein defender graph"]
fn authored_defender_binds_message_source_and_checks_facing() {
    const OWNER: u32 = 0xa000_0101;
    const SENDER: u32 = 0xc000_1000;
    struct ReceiverWorld([f64; 3]);
    impl World for ReceiverWorld {
        fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
            Some(match entity {
                OWNER => [0.0, 0.0, 0.0],
                SENDER => [1.0, 0.0, 0.0],
                _ => panic!("unexpected receiver position query for {entity:X}"),
            })
        }
        fn forward(&self, entity: u32) -> Option<[f64; 3]> {
            (entity == OWNER).then_some(self.0)
        }
        fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
            (site.entity == OWNER
                && script_expr::native_shape(config, "STU_65ABFCCC", Some("m_target")))
            .then_some(Value::Integer(0))
        }
        fn has_target_tag(&self, _: Site, entity: u32, tag: u64) -> Option<bool> {
            (entity == SENDER && tag == 0x0d60_0000_0000_0326).then_some(false)
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }

    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    for direction in [-1.0, 1.0] {
        let mut world = ReceiverWorld([direction, 0.0, 0.0]);
        let mut defender = Component::start(
            Arc::clone(&graphs),
            OWNER,
            Variables::new(),
            vec![Root::listener(1, 0x0ca5, None)],
            4200,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(defender.snapshot().unwrap().instances[&1].states.is_empty());
        let parameters =
            Variables::from([(0x8d, Value::Integer(1)), (0x5c, Value::Entity(SENDER))]);
        assert_eq!(
            defender
                .queue_entity_message_from(SENDER, 0x0240_0000_0000_00bd, parameters)
                .unwrap(),
            1
        );
        defender.run_now(&mut world).unwrap();
        let snapshot = defender.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let values = &snapshot.instances[&1].variables;
        assert_eq!(values[&40], Value::Entity(SENDER));
        assert_eq!(values[&47], Value::Integer(1));
        assert_eq!(values[&536], Value::Entity(SENDER));
        assert_eq!(
            values.get(&1725),
            (direction > 0.0).then_some(&Value::Integer(0))
        );
    }
}

#[test]
fn entity_message_queues_each_live_root_with_shared_parameters() {
    let data = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntryGameMessage",
                None,
                json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
        "m_B60ABDA4":var(40,false),"m_FF703F20":var(41,false),
        "m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
            ),
            node(
                1,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(100,false),
        "m_value":{"$":"STUConfigVarExpression","m_configVars":[],"m_expression":{"m_opcodes":[13,0,0],"m_dynamicVars":[{"m_identifier":4}],"m_D99EF254":[]}}}),
            ),
        ],
        json!([]),
        json!([0]),
    );
    let mut component = Component::start(
        Arc::new(store(vec![data])),
        0xc000_1000,
        Variables::new(),
        vec![Root::new(1, 42), Root::new(2, 42)],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert_eq!(
        component
            .queue_entity_message_from(
                0xc000_1001,
                0x0240_0000_0000_0003,
                Variables::from([(4, Value::Integer(17))])
            )
            .unwrap(),
        2
    );
    component.run_now(&mut EmptyWorld).unwrap();
    for instance in [1, 2] {
        let values = &component.snapshot().unwrap().instances[&instance].variables;
        assert_eq!(values[&100], Value::Integer(17));
        assert_eq!(values[&40], Value::Entity(0xc000_1001));
        assert_eq!(values[&41], Value::Entity(0xc000_1001));
    }
}

#[test]
fn ai_override_records_are_removed_once_on_abort_and_instance_destruction() {
    #[derive(Default)]
    struct AiWorld {
        control: Vec<(AiControlOverride, bool)>,
        weapon: Vec<(AiWeaponOverride, bool)>,
    }
    impl World for AiWorld {
        fn ai_control_override(
            &mut self,
            _: Site,
            record: AiControlOverride,
            active: bool,
        ) -> Option<bool> {
            self.control.push((record, active));
            Some(true)
        }
        fn ai_weapon_override(
            &mut self,
            _: Site,
            record: AiWeaponOverride,
            active: bool,
        ) -> Option<bool> {
            self.weapon.push((record, active));
            Some(true)
        }
        fn related_entity(
            &mut self,
            _: Site,
            _: script_expr::EntityRelation,
            _: u32,
        ) -> Option<u32> {
            Some(0xc000_1001)
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }
    for class in ["STU_473549CA", "STU_CF8C7CC5", "STU_066B4605"] {
        for abort in [false, true] {
            let fields = json!({"m_00D49978":{"$":"STU_B5A0CAF0"},"m_D66E7D68":{"$":"STUConfigVarInt","m_value":2},
                "m_F2810377":{"$":"STUConfigVarIdentifier","m_identifier":"0x0D80000000000010"},
                "m_F8585ABF":{"$":"STUConfigVarBool","m_value":true}});
            let fixture = graph(
                42,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
                    ),
                    node(1, class, Some(0), fields),
                    node(
                        2,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                    "m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                    ),
                ],
                json!([1]),
                json!([0, 2]),
            );
            let mut world = AiWorld::default();
            let mut component = Component::start(
                Arc::new(store(vec![fixture])),
                0xc000_1000,
                Variables::new(),
                vec![Root::new(1, 42)],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(
                component.snapshot().unwrap().initialization_complete(),
                "{:?}",
                component.snapshot().unwrap().diagnostics
            );
            if abort {
                component
                    .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
                    .unwrap();
                component.run_now(&mut world).unwrap();
            }
            Vm::new(&component.graphs, &mut world, &mut component.machine)
                .destroy(1)
                .unwrap();
            if class == "STU_066B4605" {
                assert_eq!(world.weapon.len(), 2);
                assert_eq!(world.weapon[0].0.slot, 2);
                assert_eq!(world.weapon[0].0.identifier, 0x0d80_0000_0000_0010);
                assert!(world.weapon[0].0.enabled);
                assert!(world.weapon[0].1);
                assert_eq!(world.weapon[1], (world.weapon[0].0, false));
            } else {
                assert_eq!(world.control.len(), 2);
                assert_eq!(
                    world.control[0].0.limits,
                    if class == "STU_473549CA" {
                        Some([f64::from(std::f32::consts::PI); 2])
                    } else {
                        None
                    }
                );
                assert_eq!(world.control[0].0.priority, 9999.0);
                assert!(world.control[0].1);
                assert_eq!(world.control[1], (world.control[0].0, false));
            }
        }
    }
}

#[test]
fn entity_creation_preserves_failure_and_no_entity_outputs_before_following() {
    struct Creator {
        requests: Vec<EntityCreation>,
        result: Result<Option<u32>, String>,
    }
    impl World for Creator {
        fn create_entity(
            &mut self,
            site: Site,
            request: &EntityCreation,
        ) -> Result<Option<u32>, String> {
            assert_eq!((site.entity, site.graph, site.node), (42, 0x0cba, Some(1)));
            self.requests.push(request.clone());
            self.result.clone()
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }
    for hero in [false, true] {
        for result in [
            Ok(Some(0xc000_0000)),
            Ok(None),
            Ok(Some(0)),
            Ok(Some(42)),
            Err("allocation failed".into()),
        ] {
            for bad in [
                "none", "options", "identity", "position", "team", "resource",
            ] {
                let bad_options = bad != "none";
                let mut fields = json!({"m_position":var(100,true),"m_E95B313B":var(102,true),
                    "m_outPlug":plug(&[(2,"m_inPlug")], &[]),"m_51AB9D21":5});
                if hero {
                    fields["m_A1A715A6"] = var(101, true);
                    fields["m_3EBF988B"] = json!(1);
                    fields["m_1C558638"] = json!(15.0);
                    fields["m_F8E56B94"] = json!(true);
                    fields["m_DE3B4F01"] = var(104, true);
                    fields["m_D7F85181"] = var(105, true);
                    fields["m_246C47AF"] = var(106, true);
                } else {
                    // Native scalar groups evaluate their first config only.
                    fields["m_entityDef"] = json!({"$":"STU_06610EBC","m_configVars":[var(101,true),{"$":"unsupported"}]});
                }
                if bad == "options" {
                    fields["m_AA26E7DD"] = json!(false);
                }
                if bad == "team" {
                    fields["m_3EBF988B"] = json!(2);
                }
                let graphs = store(vec![graph(
                    0x0cba,
                    vec![
                        node(
                            0,
                            "STUStatescriptEntry",
                            None,
                            json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                        ),
                        node(1, "STUStatescriptActionCreateEntity", None, fields),
                        node(
                            2,
                            "STUStatescriptActionSetVar",
                            None,
                            json!({"m_out_Var":var(103,true),"m_value":var(102,true)}),
                        ),
                    ],
                    json!([]),
                    json!([0]),
                )]);
                let mut world = Creator {
                    requests: vec![],
                    result: result.clone(),
                };
                let snapshot = initialize(
                    &graphs,
                    42,
                    Variables::from([
                        (
                            100,
                            Value::Vector(if bad == "position" {
                                [f64::NAN, 2.0, 3.0]
                            } else {
                                [1.0, 2.0, 3.0]
                            }),
                        ),
                        (
                            101,
                            Value::Asset(if bad == "identity" {
                                0x0580_0000_0000_0175
                            } else if !hero && bad == "resource" {
                                0
                            } else if hero {
                                0x02e0_0000_0000_0175
                            } else {
                                0x0400_0000_0000_16be
                            }),
                        ),
                        (102, Value::Integer(99)),
                        (104, Value::Asset(0x0d80_0000_0000_195e)),
                        (
                            105,
                            Value::Asset(if bad == "resource" {
                                0x0a50_0000_0000_16c0
                            } else {
                                0x0400_0000_0000_16c0
                            }),
                        ),
                        (106, Value::Null),
                    ]),
                    vec![Root::new(1, 0x0cba)],
                    &mut world,
                    MAX_INIT_WORK,
                )
                .unwrap();
                let success = !bad_options && matches!(result, Ok(Some(0xc000_0000)) | Ok(None));
                assert_eq!(snapshot.initialization_complete(), success);
                assert_eq!(world.requests.len(), usize::from(!bad_options));
                let expected = if !bad_options && result == Ok(Some(0xc000_0000)) {
                    Value::Entity(0xc000_0000)
                } else {
                    Value::Integer(99)
                };
                assert_eq!(snapshot.variables[&102], expected);
                assert_eq!(snapshot.variables.get(&103), success.then_some(&expected));
                if let Some(request) = world.requests.first() {
                    assert_eq!(request.position, [1.0, 2.0, 3.0]);
                    assert_eq!(
                        request.kind,
                        if hero {
                            CreationKind::Hero {
                                hero: 0x02e0_0000_0000_0175,
                                team: 1,
                                yaw: 15.0,
                                flags: [Value::Bool(true), Value::Asset(0x0d80_0000_0000_195e)],
                                controller: Some(0x0400_0000_0000_16c0),
                                skin: None,
                            }
                        } else {
                            CreationKind::Definition(0x0400_0000_0000_16be)
                        }
                    );
                }
            }
        }
    }
}

#[test]
fn participant_collection_writes_before_following_and_preserves_order() {
    struct Participants(Option<Vec<u32>>);
    impl World for Participants {
        fn participant_targets(&self, site: Site, _: &Json) -> Option<Vec<u32>> {
            assert_eq!((site.graph, site.node), (0x0cba, Some(1)));
            self.0.clone()
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }
    for entities in [None, Some(vec![]), Some(vec![0xc000_1002, 0xc000_1000])] {
        let mut collect = node(
            1,
            "STU_42578342",
            None,
            json!({
                "m_filter":{"$":"STUConfigVarFilter"},"m_4AEEE6A1":var(100,true),
                "m_outPlug":plug(&[(2,"m_inPlug")], &[]),
            }),
        );
        collect["fields"]["m_ADEB6E05"] = json!(true);
        let graphs = store(vec![graph(
            0x0cba,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                collect,
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(101,true),"m_value":var(100,true)}),
                ),
            ],
            json!([]),
            json!([0]),
        )]);
        let snapshot = initialize(
            &graphs,
            42,
            Variables::new(),
            vec![Root::new(1, 0x0cba)],
            &mut Participants(entities.clone()),
            MAX_INIT_WORK,
        )
        .unwrap();
        if let Some(entities) = entities {
            let expected = Value::Array(entities.into_iter().map(Value::Entity).collect());
            assert_eq!(snapshot.variables[&100], expected);
            assert_eq!(snapshot.variables[&101], expected);
            assert!(snapshot.initialization_complete());
        } else {
            assert!(!snapshot.variables.contains_key(&100));
            assert!(!snapshot.variables.contains_key(&101));
            assert!(matches!(
                snapshot.diagnostics.as_slice(),
                [Diagnostic {
                    issue: Issue::UnsupportedField("scene participant filter"),
                    ..
                }]
            ));
        }
    }
}

#[test]
fn tagged_placement_collection_uses_the_required_field_outside_legacy_graphs() {
    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../../../fixtures/behavior/script-tagged-placement-collection-v1.json"
    ))
    .unwrap();
    let tag = fixture["tag"].as_str().unwrap();
    let tag_id = orpheus_runtime::format::parse_guid(tag).unwrap();
    let placements = |rows: &Json| {
        rows.as_array().map(|rows| {
            rows.iter()
                .map(|row| u32::try_from(row.as_u64().unwrap()).unwrap())
                .collect::<Vec<_>>()
        })
    };
    struct Placements {
        tag: u64,
        entities: Option<Vec<u32>>,
        queries: std::cell::Cell<usize>,
    }
    impl World for Placements {
        fn tagged_placements(&self, _: Site, tag: u64) -> Option<&[u32]> {
            self.queries.set(self.queries.get() + 1);
            assert_eq!(tag, self.tag);
            self.entities.as_deref()
        }
        fn position(&mut self, _: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
            None
        }
    }
    for (index, case) in (71..).zip(fixture["cases"].as_array().unwrap()) {
        let mut fields = json!({
            "m_4AEEE6A1":var(100,true),
            "m_B486603E":{"$":"STU_357DABBB","m_7D7C86A1":tag},
            "m_outPlug":plug(&[(2,"m_inPlug")], &[]),
        });
        let malformed = case["extra_exclusion"] == true;
        if malformed {
            fields["m_8B2A4DF3"] = fields["m_B486603E"].clone();
        }
        let mut collect = node(1, "STU_42578342", None, fields);
        collect["fields"]["m_ADEB6E05"] = json!(true);
        let graphs = store(vec![graph(
            index,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1,"m_inPlug")], &[])}),
                ),
                collect,
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(101,true),"m_value":var(100,true)}),
                ),
            ],
            json!([]),
            json!([0]),
        )]);
        let mut world = Placements {
            tag: tag_id,
            entities: placements(&case["inventory"]),
            queries: std::cell::Cell::new(0),
        };
        let snapshot = initialize(
            &graphs,
            42,
            Variables::new(),
            vec![Root::new(1, index)],
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let expected = placements(&case["expected"])
            .map(|rows| Value::Array(rows.into_iter().map(Value::Entity).collect()));
        assert_eq!(world.queries.get(), usize::from(!malformed));
        assert_eq!(snapshot.initialization_complete(), expected.is_some());
        assert_eq!(snapshot.variables.get(&100), expected.as_ref());
        assert_eq!(snapshot.variables.get(&101), expected.as_ref());
    }
}

#[test]
fn paired_target_tags_use_admitted_query_scope_at_any_placement() {
    use std::cell::Cell;

    const OWNER: u32 = 52;
    const OUTPUT: u32 = 610;
    const BEGAN: u32 = 611;
    struct TargetTags {
        site: Site,
        include: u64,
        exclude: u64,
        targets: Option<Vec<u32>>,
        reads: Cell<usize>,
    }
    impl World for TargetTags {
        fn targets_with_tag(&self, site: Site, include: u64, exclude: u64) -> Option<Vec<u32>> {
            assert_eq!(
                (site, include, exclude),
                (self.site, self.include, self.exclude)
            );
            self.reads.set(self.reads.get() + 1);
            self.targets.clone()
        }
        fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
            None
        }
        fn position(&mut self, _: u32) -> Option<[f64; 3]> {
            None
        }
    }

    let contract: Json = serde_json::from_str(include_str!(
        "../../../../../../fixtures/behavior/script-tagged-target-pair-v1.json"
    ))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(
        contract["case_id"],
        "script-tagged-target-pair.synthetic.v1"
    );
    let input = &contract["input"];
    let index = u32::try_from(input["graph"].as_u64().unwrap()).unwrap();
    let position = u16::try_from(input["position"].as_u64().unwrap()).unwrap();
    let marker = position + 1;
    for case in input["cases"].as_array().unwrap() {
        let mut fields = json!({
            "m_4AEEE6A1": var(OUTPUT, true),
            "m_9C7BD8B8": {"$": "STU_357DABBB", "m_7D7C86A1": input["include_tag"]},
            "m_8B2A4DF3": {"$": "STU_357DABBB", "m_7D7C86A1": input["exclude_tag"]},
            "m_onBeginPlug": plug(&[(marker, "m_inPlug")], &[]),
        });
        if let Some(overrides) = case["fields"].as_object() {
            fields.as_object_mut().unwrap().extend(overrides.clone());
        }
        let mut query = node(position, "STU_160F2578", Some(0), fields);
        query["client_only"] = json!(case["client_only"].as_bool().unwrap_or(false));
        query["server_only"] = json!(case["server_only"].as_bool().unwrap_or(false));
        let mut model = graph(
            index,
            vec![
                query,
                node(
                    marker,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var": var(BEGAN, true), "m_value": true,
                    }),
                ),
            ],
            json!([position]),
            json!([]),
        );
        model["items"] = json!(marker + 1);
        let graphs = store(vec![model]);
        let mut world = TargetTags {
            site: Site {
                entity: OWNER,
                instance: 1,
                graph: index,
                node: Some(position),
            },
            include: crate::script_graph::asset_guid(&input["include_tag"]).unwrap(),
            exclude: crate::script_graph::asset_guid(&input["exclude_tag"]).unwrap(),
            targets: case["targets"].as_array().map(|rows| {
                rows.iter()
                    .map(|row| u32::try_from(row.as_u64().unwrap()).unwrap())
                    .collect()
            }),
            reads: Cell::new(0),
        };
        let snapshot = initialize(
            &graphs,
            OWNER,
            Variables::from([(OUTPUT, Value::Integer(777))]),
            vec![Root::listener(1, index, Some(0))],
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let outcome = case["outcome"].as_str().unwrap();
        let result = match snapshot.diagnostics.as_slice() {
            [] if snapshot.variables.contains_key(&BEGAN) => "resolved",
            [] => "skipped",
            [
                Diagnostic {
                    issue: Issue::UnsupportedField("tagged targets with exclusions"),
                    ..
                },
            ] => "unavailable",
            [
                Diagnostic {
                    issue: Issue::UnsupportedState(class),
                    ..
                },
            ] if class == "STU_160F2578" => "unsupported",
            [
                Diagnostic {
                    issue: Issue::UnsupportedField("static target output"),
                    ..
                },
            ] => "invalid_output",
            [
                Diagnostic {
                    issue:
                        Issue::Expression(script_expr::EvalError::MalformedConfig(
                            "variable identifier out of range",
                        )),
                    ..
                },
            ] => "invalid_expression",
            other => panic!("unexpected diagnostics for {}: {other:?}", case["name"]),
        };
        assert_eq!(result, outcome, "{}", case["name"]);
        assert_eq!(
            world.reads.get(),
            usize::from(matches!(outcome, "resolved" | "unavailable"))
        );
        let expected = if outcome == "resolved" {
            Value::Array(
                world
                    .targets
                    .unwrap()
                    .into_iter()
                    .map(Value::Entity)
                    .collect(),
            )
        } else {
            Value::Integer(777)
        };
        assert_eq!(
            snapshot.variables.get(&OUTPUT),
            Some(&expected),
            "{}",
            case["name"]
        );
    }
}

#[test]
#[ignore = "requires the prepared 104319 Junkenstein spawner graph"]
fn authored_spawner_filters_dead_and_removed_bodies_in_order() {
    struct Bodies(BTreeMap<u32, bool>);
    impl World for Bodies {
        fn encounter_body_alive(&self, entity: u32) -> Option<bool> {
            Some(self.0.get(&entity).copied().unwrap_or(false))
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    let [first, dead, second, removed] = [0xc000_1000, 0xc000_1002, 0xc000_1004, 0xc000_1006];
    let next = 0xc000_1008;
    let mut world = Bodies(BTreeMap::from([
        (first, true),
        (dead, false),
        (second, true),
    ]));
    let mut root = Root::listener(1, 0x0cba, Some(1));
    root.variables.insert(38, Value::Entity(next));
    root.variables.insert(889, Value::Integer(0));
    root.variables.insert(
        8399,
        Value::Array(vec![
            Value::Entity(first),
            Value::Entity(dead),
            Value::Entity(second),
            Value::Entity(removed),
        ]),
    );
    let mut component = Component::start(
        graphs,
        0xd000_0010,
        Variables::new(),
        vec![root],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert_eq!(
        snapshot.instances[&1].variables[&8399],
        Value::Array(vec![Value::Entity(first), Value::Entity(second)])
    );
    world.0.insert(first, false);
    component.advance_frame(1, &mut world).unwrap();
    assert_eq!(
        component.snapshot().unwrap().instances[&1].variables[&8399],
        Value::Array(vec![Value::Entity(second)])
    );
    world.0.remove(&second);
    component.advance_frame(2, &mut world).unwrap();
    assert_eq!(
        component.snapshot().unwrap().instances[&1].variables[&8399],
        Value::Array(vec![])
    );
    world.0.insert(next, true);
    component
        .run(&mut world, |vm| {
            let append = vm.graph(1).node(5).unwrap();
            assert!(vm.run_node(1, append, None, 0)?);
            vm.run()
        })
        .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    let spawner = &snapshot.instances[&1];
    assert_eq!(
        spawner.variables[&8399],
        Value::Array(vec![Value::Entity(next)])
    );
    assert_eq!(spawner.variables[&889], Value::Float(1.0));
    assert!(spawner.states[&0].active);
}

#[test]
fn remote_play_script_cannot_replace_an_unrelated_local_scope() {
    let fixture: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/cross-entity-script-scope-v1.json"
    )))
    .unwrap();
    let number = |field: &str| fixture[field].as_u64().unwrap();
    let receiver = u32::try_from(number("receiver")).unwrap();
    let remote = u32::try_from(number("remote_caller")).unwrap();
    let graph_id = u32::try_from(number("graph")).unwrap();
    let local_id = u16::try_from(number("local_instance")).unwrap();
    let initial = Value::Integer(fixture["initial_value"].as_i64().unwrap());
    let next = Value::Integer(fixture["next_value"].as_i64().unwrap());

    let mut component = Component::start(
        Arc::new(store(vec![graph(graph_id, vec![], json!([]), json!([]))])),
        receiver,
        Variables::new(),
        vec![],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    component
        .run(&mut EmptyWorld, |vm| {
            assert!(vm.create(
                local_id,
                graph_id,
                None,
                Variables::from([(7, initial.clone())]),
                Startup::Ordinary,
                types::InstanceScope::ServerOnly,
            )?);
            vm.run()
        })
        .unwrap();

    let request = PlayScript {
        policy: u8::try_from(number("replacement_policy")).unwrap(),
        graph: graph_id,
        source: remote,
        auto_retire: false,
        parameters: vec![],
        overrides: vec![(0x0d80_0000_0000_0007, next.clone())],
    };
    component
        .receive_play_script(
            Site {
                entity: remote,
                instance: local_id,
                graph: 31,
                node: None,
            },
            &request,
            &mut EmptyWorld,
        )
        .unwrap();

    let snapshot = component.snapshot().unwrap();
    assert!(snapshot.initialization_complete());
    let local = &snapshot.instances[&local_id];
    assert!(local.is_server_only());
    assert_eq!(local.variables[&7], initial);
    assert_eq!(snapshot.instances.len(), 2);
    let projected = snapshot
        .instances
        .values()
        .find(|instance| !instance.is_server_only())
        .unwrap();
    assert_eq!(projected.variables[&7], next);
}

#[test]
fn played_schema_queues_defaults_before_overrides_and_respects_instance_policy() {
    let mut callee = graph(
        43,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
            ),
            node(
                1,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(900,true),"m_value":var(7,false)}),
            ),
            node(
                2,
                "STU_BB4AC733",
                None,
                json!({"m_B60ABDA4":var(28,false),"m_outPlug":plug(&[(3,"m_inPlug")],&[])}),
            ),
            node(
                3,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(902,true),"m_value":var(7,false)}),
            ),
        ],
        json!([]),
        json!([0, 2]),
    );
    let plus_one = json!({"$":"STU_919FD47C","m_configVars":null,"m_expression":{
        "m_D99EF254":[1],"m_dynamicVars":[var(7,false)],"m_fragments":null,
        "m_opcodes":[8,0,6,0,23,0]}});
    callee["fields"]["m_publicSchema"] = json!({"$":"STUStatescriptSchema","m_entries":[
        {"m_0D09D2D9":"0x0D80000000000007","m_value":10},
        {"m_0D09D2D9":"0x0D80000000000008","m_value":plus_one}]});
    let mut component = Component::start(
        Arc::new(store(vec![callee])),
        0xa000_0101,
        Variables::new(),
        vec![],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let site = Site {
        entity: 0xa000_0201,
        instance: 1,
        graph: 42,
        node: Some(0),
    };
    let request = |policy, value, source, auto_retire| PlayScript {
        policy,
        graph: 43,
        source,
        auto_retire,
        parameters: vec![],
        overrides: vec![(0x0d80_0000_0000_0007, Value::Integer(value))],
    };
    component
        .receive_play_script(site, &request(0, 40, 0xa000_0301, false), &mut EmptyWorld)
        .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert_eq!(snapshot.variables[&900], Value::Integer(40));
    assert_eq!(snapshot.variables[&902], Value::Integer(40));
    assert_eq!(
        script_expr::to_float(&snapshot.instances[&1].variables[&8]),
        1.0
    );
    component
        .receive_play_script(site, &request(0, 77, 0xa000_0401, true), &mut EmptyWorld)
        .unwrap();
    assert_eq!(
        component.snapshot().unwrap().variables[&902],
        Value::Integer(40)
    );
    component
        .receive_play_script(site, &request(2, 99, 0xa000_0401, true), &mut EmptyWorld)
        .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert_eq!(snapshot.variables[&900], Value::Integer(40));
    assert_eq!(snapshot.variables[&902], Value::Integer(99));
    assert_eq!(
        script_expr::to_float(&snapshot.instances[&1].variables[&8]),
        41.0
    );
    assert_eq!(
        snapshot.instances[&1].variables[&SCRIPT_SOURCE],
        Value::Entity(0xa000_0301)
    );
    assert_eq!(
        snapshot.instances[&1].variables[&28],
        Value::Entity(site.entity)
    );
    assert_eq!(snapshot.instances[&1].retire_after_frame, None);
    component
        .receive_play_script(site, &request(1, 70, 0xa000_0401, false), &mut EmptyWorld)
        .unwrap();
    assert_eq!(component.snapshot().unwrap().instances.len(), 2);
    component
        .receive_play_script(site, &request(2, 80, 0xa000_0401, false), &mut EmptyWorld)
        .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert_eq!(snapshot.instances[&1].variables[&7], Value::Integer(80));
    assert_eq!(snapshot.instances[&2].variables[&7], Value::Integer(70));
    component
        .receive_play_script(site, &request(3, 90, 0xa000_0501, true), &mut EmptyWorld)
        .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert_eq!(snapshot.instances.keys().copied().collect::<Vec<_>>(), [3]);
    assert_eq!(snapshot.variables[&900], Value::Integer(90));
    assert_eq!(
        snapshot.instances[&3].variables[&SCRIPT_SOURCE],
        Value::Entity(0xa000_0501)
    );
    assert_eq!(snapshot.instances[&3].retire_after_frame, Some(1));
}

#[test]
fn inline_play_resource_takes_priority_and_preserves_caller_values() {
    let inline = json!({"$":"STU_9DB46B98","m_6525E98B":{
        "$":"STUStatescriptGraphWithOverrides","m_graph":"0x058000000000002B",
        "m_1EB5A024":[{"$":"STUStatescriptSchemaEntry","m_0D09D2D9":"0x0D80000000000007","m_value":var(37,false)}]}});
    let caller = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
            ),
            node(
                1,
                "STUStatescriptActionPlayScript",
                None,
                json!({
            "m_69F4B52C":inline,"m_script":{"$":"STU_105E1BCC","m_graph":"0x058000000000002C"},
            "m_AA4DE426":2,"m_7ABA409C":255,"m_targets":{"$":"STU_B5A0CAF0"}}),
            ),
        ],
        json!([]),
        json!([0]),
    );
    let callee = graph(
        43,
        vec![
            node(
                0,
                "STU_BB4AC733",
                None,
                json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
            ),
            node(
                1,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(900,true),"m_value":var(7,false)}),
            ),
        ],
        json!([]),
        json!([0]),
    );
    let mut root = Root::new(1, 42);
    root.variables.insert(37, Value::Integer(123));
    let component = Component::start(
        Arc::new(store(vec![caller, callee])),
        0xa000_0101,
        Variables::new(),
        vec![root],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert_eq!(snapshot.variables[&900], Value::Integer(123));
    assert_eq!(snapshot.instances[&2].retire_after_frame, None);
}

#[test]
fn empty_play_resource_follows_output_and_replacing_the_caller_stops_its_output() {
    for replace in [false, true] {
        let action = json!({
            "m_69F4B52C":{"$":"STU_9DB46B98","m_6525E98B":null},
            "m_script":if replace {json!({"$":"STU_105E1BCC","m_graph":"0x058000000000002A"})} else {Json::Null},
            "m_AA4DE426":3,"m_targets":{"$":"STU_B5A0CAF0"},
            "m_outPlug":plug(&[(2,"m_inPlug")],&[])});
        let caller = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({
                "m_gameMessage":{"m_gameMessage":"0x0240000000000009"},
                "m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
                ),
                node(1, "STUStatescriptActionPlayScript", None, action),
                node(
                    2,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({"m_out_Var":var(900,true),"m_value":true}),
                ),
            ],
            json!([]),
            json!([0]),
        );
        let mut component = Component::start(
            Arc::new(store(vec![caller])),
            0xa000_0101,
            Variables::new(),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        component
            .queue_message(1, 0x0240_0000_0000_0009, Variables::new())
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            snapshot.variables.get(&900),
            if replace {
                None
            } else {
                Some(&Value::Bool(true))
            }
        );
        assert_eq!(
            snapshot.instances.keys().copied().collect::<Vec<_>>(),
            if replace { vec![2] } else { vec![1] }
        );
    }
}

#[test]
fn deferred_callbacks_order_priorities_and_keep_new_work_in_the_next_batch() {
    let append = |pos, digit| {
        node(
            pos,
            "STUStatescriptActionSetVar",
            None,
            json!({
        "m_out_Var":var(100,true),"m_value":{"$":"STU_919FD47C","m_configVars":null,
        "m_expression":{"m_D99EF254":[10,digit],"m_dynamicVars":[var(100,true)],
            "m_fragments":null,"m_opcodes":[8,0,6,0,25,6,1,23,0]}}}),
        )
    };
    for case in 0..6 {
        // Signed zero priorities compare equal, just like the reference.
        let low = if case < 3 { 1.0 } else { -1.0 };
        let first = if case < 3 { 2.0 } else { 0.0 };
        let second = if case < 3 { 2.0 } else { -0.0 };
        let mode = case % 3;
        let cancel = mode == 1;
        let mut entry = vec![
            (1, "m_beginPlug"),
            (2, "m_beginPlug"),
            (3, "m_beginPlug"),
            (9, "m_inPlug"),
        ];
        if cancel {
            entry.push((1, ABORT_PLUG));
        }
        let mut high = vec![(12, "m_inPlug")];
        if mode == 2 {
            high.push((1, ABORT_PLUG));
        }
        let mut fixture = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&entry,&[])}),
                ),
                node(
                    1,
                    "STU_9C9A422A",
                    Some(0),
                    json!({"m_priority":low,"m_FD0C0646":plug(&[(10,"m_inPlug")],&[]),"m_8E484B2E":plug(&[(5,"m_beginPlug")],&[4])}),
                ),
                node(
                    2,
                    "STU_9C9A422A",
                    Some(1),
                    json!({"m_priority":first,"m_FD0C0646":plug(&[(11,"m_inPlug"),(4,"m_beginPlug")],&[])}),
                ),
                node(
                    3,
                    "STU_9C9A422A",
                    Some(2),
                    json!({"m_priority":second,"m_FD0C0646":plug(&high,&[])}),
                ),
                node(
                    4,
                    "STU_9C9A422A",
                    Some(3),
                    json!({"m_priority":100,"m_FD0C0646":plug(&[(13,"m_inPlug")],&[])}),
                ),
                node(5, "STUStatescriptState", Some(4), json!({})),
                node(
                    6,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000009"},"m_outPlug":plug(&[(1,ABORT_PLUG)],&[])}),
                ),
                append(9, 9),
                append(10, 1),
                append(11, 2),
                append(12, 3),
                append(13, 4),
            ],
            json!([1, 2, 3, 4, 5]),
            json!([0, 6]),
        );
        fixture["items"] = json!(14);
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::from([(100, Value::Integer(0))]),
            vec![Root::new(1, 42)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            script_expr::to_float(&snapshot.variables[&100]),
            if cancel { 9324.0 } else { 93214.0 }
        );
        assert_eq!(
            snapshot.instances[&1]
                .states
                .get(&4)
                .is_some_and(|state| state.active),
            mode == 0
        );
        component
            .queue_message(1, 0x0240_0000_0000_0009, Variables::new())
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        assert!(
            !component.snapshot().unwrap().instances[&1]
                .states
                .get(&4)
                .is_some_and(|state| state.active)
        );
    }
}

#[test]
fn deferred_phase_runs_after_ticks_and_is_bounded_when_it_requeues() {
    #[derive(Default)]
    struct Probe(Vec<&'static str>);
    impl World for Probe {
        fn native(&mut self, _: Site, config: &Json) -> Option<Value> {
            match config["$"].as_str()? {
                "ProbeTick" => {
                    self.0.push("tick");
                    Some(Value::Bool(false))
                }
                "ProbeDeferred" => {
                    self.0.push("defer");
                    Some(Value::Integer(1))
                }
                _ => None,
            }
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }
    let fixture = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
            ),
            node(
                1,
                "STUStatescriptStateBooleanSwitch",
                Some(0),
                json!({"m_condition":{"$":"ProbeTick"}}),
            ),
            node(
                2,
                "STU_9C9A422A",
                Some(1),
                json!({"m_FD0C0646":plug(&[(3,"m_inPlug"),(2,ABORT_PLUG)],&[])}),
            ),
            node(
                3,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":var(100,true),"m_value":{"$":"ProbeDeferred"}}),
            ),
            node(
                4,
                "STUStatescriptEntryGameMessage",
                None,
                json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000009"},"m_outPlug":plug(&[(2,"m_beginPlug")],&[])}),
            ),
        ],
        json!([1, 2]),
        json!([0, 4]),
    );
    let mut world = Probe::default();
    let mut component = Component::start(
        Arc::new(store(vec![fixture])),
        42,
        Variables::new(),
        vec![Root::new(1, 42)],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    world.0.clear();
    component
        .queue_frame_message(1, 1, 0x0240_0000_0000_0009, Variables::new())
        .unwrap();
    component.advance_frame(1, &mut world).unwrap();
    assert_eq!(world.0, ["tick", "defer", "tick"]);
    let cycle = graph(
        43,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_beginPlug")],&[])}),
            ),
            node(
                1,
                "STU_9C9A422A",
                Some(0),
                json!({"m_FD0C0646":plug(&[(1,ABORT_PLUG)],&[]),"m_onEndPlug":plug(&[(1,"m_beginPlug")],&[])}),
            ),
        ],
        json!([1]),
        json!([0]),
    );
    assert!(matches!(
        Component::start(
            Arc::new(store(vec![cycle])),
            42,
            Variables::new(),
            vec![Root::new(1, 43)],
            0,
            &mut EmptyWorld,
            100
        ),
        Err(InitFault::WorkBudget { .. })
    ));
}
