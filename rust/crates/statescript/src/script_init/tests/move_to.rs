use super::*;

#[test]
fn world_move_to_refreshes_the_authored_request_without_restarting() {
    let graphs = Arc::new(store(vec![graph(
        73,
        vec![node(
            0,
            "STUStatescriptStateMoveTo",
            Some(0),
            json!({
                "m_B060F688": 0, "m_speed": var(817, true), "m_865F3D01": var(819, true),
                "m_destinationBone": var(820, true)
            }),
        )],
        json!([0]),
        json!([]),
    )]));
    let mut world = EmptyWorld;
    let mut component = Component::start(
        graphs,
        71,
        Variables::new(),
        vec![Root::new(9, 73)],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    fn request(component: &Component) -> (u16, u64, Option<([f64; 3], f64)>) {
        let state = &component.snapshot().unwrap().instances[&9].states[&0];
        assert!(state.active);
        (state.activation, state.start_ms, state.move_request())
    }
    component
        .set_entity_variable(817, Value::Float(10.0), &mut world)
        .unwrap();
    component
        .set_entity_variable(819, Value::Vector([4.0, 0.0, 1.0]), &mut world)
        .unwrap();
    component
        .set_entity_variable(820, Value::Asset(0), &mut world)
        .unwrap();
    component.begin_state(9, 0, &mut world).unwrap();
    let (activation, start, initial) = request(&component);
    assert_eq!(initial, Some(([4.0, 0.0, 1.0], 10.0)));

    component
        .set_entity_variable(817, Value::Float(8.0), &mut world)
        .unwrap();
    component
        .set_entity_variable(819, Value::Vector([2.0, 3.0, 5.0]), &mut world)
        .unwrap();
    component.advance_frame(1, &mut world).unwrap();
    assert_eq!(
        request(&component),
        (activation, start, Some(([2.0, 3.0, 5.0], 8.0)))
    );

    component
        .set_entity_variable(817, Value::Float(0.0), &mut world)
        .unwrap();
    component.advance_frame(2, &mut world).unwrap();
    assert_eq!(request(&component), (activation, start, None));
}

#[test]
fn move_result_only_ends_the_activation_that_requested_it() {
    let graphs = Arc::new(store(vec![graph(
        73,
        vec![
            node(
                0,
                "STUStatescriptStateMoveTo",
                Some(0),
                json!({
                    "m_B060F688":0, "m_speed":10,
                    "m_865F3D01":{"$":"STUConfigVarVec3","m_value":{"X":4,"Y":0,"Z":0}},
                    "m_onFinishedPlug":plug(&[(1,"m_inPlug")], &[]),
                    "m_onAbortPlug":plug(&[(2,"m_inPlug")], &[])
                }),
            ),
            node(
                1,
                "STUStatescriptActionSetVar",
                None,
                json!({
                    "m_out_Var":var(73,true), "m_value":1
                }),
            ),
            node(
                2,
                "STUStatescriptActionSetVar",
                None,
                json!({
                    "m_out_Var":var(73,true), "m_value":2
                }),
            ),
        ],
        json!([0]),
        json!([]),
    )]));
    let mut world = EmptyWorld;
    let mut component = Component::start(
        graphs,
        71,
        Variables::new(),
        vec![Root::new(9, 73)],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let key = |component: &Component| {
        let state = &component.snapshot().unwrap().instances[&9].states[&0];
        assert!(state.move_request().is_some());
        MoveToKey {
            instance: 9,
            raw: 0,
            activation: state.activation,
            start_ms: state.start_ms,
        }
    };
    component.begin_state(9, 0, &mut world).unwrap();
    let old = key(&component);
    component
        .queue_move_result(3, old, MoveOutcome::Arrived)
        .unwrap();
    component.debug_end_state(9, 0, &mut world).unwrap();
    component.advance_frame(1, &mut world).unwrap();
    component.begin_state(9, 0, &mut world).unwrap();
    let current = key(&component);
    assert_ne!(old, current);
    component
        .queue_move_result(3, old, MoveOutcome::Arrived)
        .unwrap();
    component.advance_frame(3, &mut world).unwrap();
    assert_eq!(key(&component), current);
    assert_eq!(
        component.snapshot().unwrap().variables.get(&73),
        Some(&Value::Integer(2))
    );

    component
        .queue_move_result(4, current, MoveOutcome::Arrived)
        .unwrap();
    component
        .queue_move_result(4, current, MoveOutcome::Blocked)
        .unwrap();
    component.advance_frame(4, &mut world).unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(snapshot.instances[&9].states[&0].move_request().is_none());
    assert_eq!(snapshot.variables.get(&73), Some(&Value::Integer(1)));
}
