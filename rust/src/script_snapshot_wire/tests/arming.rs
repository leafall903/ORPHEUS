use super::*;

#[test]
fn armed_projectile_follows_its_own_roots_wait_gate_and_remote_subgraph() {
    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/projectile-impact-spawn-v1.json"
    ))
    .unwrap();
    let source = &fixture["arming"];
    let companion = source["roots"][0].as_u64().unwrap() as u32;
    let primary = source["roots"][1].as_u64().unwrap() as u32;
    let guid = crate::script_graph::asset_guid(&source["entity"]).unwrap();
    let begin = |positions: &[u16]| {
        json!({"$":"STUStatescriptOutputPlug",
        "links":positions.iter().map(|p| json!([p,"m_beginPlug"])).collect::<Vec<_>>()})
    };
    let mut active = graph(
        primary,
        vec![
            node(
                0,
                "STUStatescriptEntryGameMessage",
                None,
                json!({"m_outPlug":begin(&[1])}),
            ),
            node(
                1,
                "STUStatescriptStateWait",
                Some(0),
                json!({"m_subgraphPlug":begin(&[2])}),
            ),
            node(
                2,
                "STUStatescriptStateWait",
                Some(1),
                json!({"m_timeout":{"$":"STUConfigVarFloat","m_value":source["wait_seconds"]},"m_onFinishedPlug":begin(&[3])}),
            ),
            node(
                3,
                "STUStatescriptStateBooleanSwitch",
                Some(2),
                json!({"m_condition":{"$":"STUConfigVarBool","m_value":false},"m_falseSubgraphPlug":begin(&[4,5])}),
            ),
            node(4, "STUStatescriptStateTrackTargets", Some(3), json!({})),
            node(
                5,
                "STU_691BFA55",
                Some(4),
                json!({"m_effect":{"m_effect":source["effect"]},"m_subgraphPlug":begin(&[6])}),
            ),
            node(
                6,
                "STU_691BFA55",
                Some(5),
                json!({"m_effect":{"m_effect":source["nested_effect"]}}),
            ),
            node(
                7,
                "STU_691BFA55",
                Some(6),
                json!({"m_effect":{"m_effect":source["nested_effect"]}}),
            ),
        ],
        json!([1, 2, 3, 4, 5, 6, 7]),
        json!([0]),
        json!([5, 6, 7]),
        json!([]),
    );
    for (slot, pos) in [5usize, 6, 7].into_iter().enumerate() {
        active["nodes"][pos]["remote"] = json!(slot);
    }
    let mut bundle = json!({
        "version":1,"missing":[],"bodies":{},"persistent_projectiles":{(source["entity"].as_str().unwrap()):{
            "name":"synthetic deployable","graphs":[
                {"graph":companion,"m_75408640":0},{"graph":primary,"m_75408640":0}],
            "components":["STUProjectileVisualComponent","STUPetComponent","STUStatescriptComponent"]}},
        "graphs":[graph(companion,vec![],json!([]),json!([]),json!([]),json!([])),active],
    });
    let read = |raw: &Json| {
        let store = GraphStore::from_json(raw.to_string().as_bytes()).unwrap();
        store
            .persistent_projectile(guid)
            .and_then(|definition| ArmingEffects::from_definition(&store, definition))
    };
    let effects = read(&bundle).unwrap();
    assert_eq!(
        u64::from(effects.ticks),
        source["expected_ticks"].as_u64().unwrap()
    );
    let snapshot = effects.snapshot(45);
    assert_eq!(snapshot.instances[&1].graph, companion);
    assert!(snapshot.instances[&1].states.is_empty());
    let active = &snapshot.instances[&2];
    assert_eq!(active.graph, primary);
    assert_eq!(
        json!(active.states.keys().collect::<Vec<_>>()),
        source["expected_effect_slots"]
    );
    bundle["graphs"][1]["nodes"][3]["fields"]["m_condition"]["m_value"] = json!(true);
    assert!(
        read(&bundle).is_none(),
        "closed gate must not activate the effects"
    );
    bundle["graphs"][1]["nodes"][3]["fields"]["m_condition"]["m_value"] = json!(false);
    bundle["graphs"][1]["nodes"][3]["fields"]["m_falseSubgraphPlug"] = begin(&[5]);
    assert!(
        read(&bundle).is_none(),
        "unrelated timed cosmetics do not imply proximity arming"
    );
    bundle["graphs"][1]["nodes"][3]["fields"]["m_falseSubgraphPlug"] = begin(&[4, 5]);
    bundle["persistent_projectiles"]
        .as_object_mut()
        .unwrap()
        .clear();
    assert!(
        read(&bundle).is_none(),
        "missing .003 data must not imply projectile behavior"
    );
}
