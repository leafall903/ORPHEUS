use super::*;

#[derive(Debug, PartialEq)]
struct VoiceRequest {
    cue: u64,
    speaker: u32,
    subject: Option<u32>,
    audience: Option<Vec<u32>>,
}

#[derive(Default)]
struct VoiceHost(Vec<VoiceRequest>);

#[test]
fn only_authoritative_death_can_voice_through_wait_in_the_same_life() {
    let died = 0x0240_0000_0000_003e;
    let criterion = 0x0d80_0000_0000_1234;
    let enter = |pos, message, dest| {
        node(
            pos,
            "STUStatescriptEntryGameMessage",
            None,
            json!({
                "m_gameMessage": {"m_gameMessage": format!("0x{message:016X}")},
                "m_outPlug": plug(&[(dest,"m_beginPlug")],&[]),
            }),
        )
    };
    let wait = |pos, state| {
        node(
            pos,
            "STUStatescriptStateWait",
            Some(state),
            json!({
                "m_BF1A93B0":0, "m_0DBAFD7F":0,
                "m_timeout":{"$":"STUConfigVarFloat","m_value":0.05},
                "m_onFinishedPlug":plug(&[(4,"m_inPlug")],&[]),
            }),
        )
    };
    let g = graph(
        74,
        vec![
            enter(0, died, 1),
            wait(1, 0),
            enter(2, 12, 3),
            wait(3, 1),
            node(
                4,
                "STUStatescriptActionCriteria",
                None,
                json!({
                    "m_identifier":{"$":"STUConfigVarIdentifier","m_identifier":format!("0x{criterion:016X}")},
                    "m_targets":{"$":"STU_B5A0CAF0"},
                }),
            ),
        ],
        json!([1, 3]),
        json!([0, 2]),
    );
    let graphs = Arc::new(store(vec![g]));
    for (official, revive, expected) in [(true, false, 1), (false, false, 0), (true, true, 0)] {
        let mut host = VoiceHost::default();
        let mut body = Component::start(
            Arc::clone(&graphs),
            91,
            Variables::new(),
            vec![Root::listener(1, 74, None)],
            0,
            &mut host,
            MAX_INIT_WORK,
        )
        .unwrap();
        body.queue_entity_message(12, Variables::new()).unwrap();
        body.run_now(&mut host).unwrap();
        body.set_owner_dead(true);
        if official {
            body.queue_body_death(82, false, None, None).unwrap();
        } else {
            body.queue_entity_message_from(82, died, Variables::new())
                .unwrap();
        }
        body.run_now(&mut host).unwrap();
        assert!(host.0.is_empty());
        if revive {
            body.set_owner_dead(false);
            body.set_owner_dead(true);
        }
        for frame in 1..=5 {
            body.advance_frame(frame, &mut host).unwrap();
        }
        assert_eq!(
            host.0.len(),
            expected,
            "official={official} revive={revive}"
        );
        assert!(host.0.iter().all(|r| r.cue == criterion && r.speaker == 91));
    }
}

impl World for VoiceHost {
    fn position(&mut self, _: u32) -> Option<[f64; 3]> {
        None
    }
    fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
        None
    }
    fn actor_voice_available(&self, _: Site) -> bool {
        true
    }
    fn queue_actor_voice_with_audience(
        &mut self,
        _: Site,
        cue: u64,
        speaker: u32,
        subject: Option<u32>,
        audience: Option<&[u32]>,
        _: &mut dyn FnMut(usize) -> usize,
    ) {
        self.0.push(VoiceRequest {
            cue,
            speaker,
            subject,
            audience: audience.map(<[_]>::to_vec),
        });
    }
}

// Run the actual criterion evaluator in isolation from unrelated graph branches.
fn evaluate_criterion(fields: Json, vars: Variables) -> Vec<VoiceRequest> {
    let mut host = VoiceHost::default();
    let graph = graph(
        9,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
            ),
            node(1, "STUStatescriptActionCriteria", None, fields),
        ],
        json!([]),
        json!([0]),
    );
    let mut root = Root::new(1, 9);
    root.variables = vars;
    Component::start(
        Arc::new(store(vec![graph])),
        91,
        Variables::new(),
        vec![root],
        0,
        &mut host,
        MAX_INIT_WORK,
    )
    .unwrap();
    host.0
}

#[test]
fn authored_voice_resolves_speaker_subject_and_listener_independently() {
    let fields = json!({"m_identifier":{"$":"STUConfigVarIdentifier","m_identifier":"0x0D80000000000091"},
        "m_targets":var(3,false), "m_8C8C5285":{"$":"STU_B5A0CAF0"}, "m_297E0B63":var(9,false)});
    let vars = Variables::from([
        (3, Value::Array(vec![Value::Entity(82), Value::Entity(82)])),
        (9, Value::Entity(84)),
    ]);
    assert_eq!(
        evaluate_criterion(fields.clone(), vars),
        vec![VoiceRequest {
            cue: 0x0D80_0000_0000_0091,
            speaker: 82,
            subject: Some(91),
            audience: Some(vec![84])
        }]
    );
    assert!(evaluate_criterion(fields, Variables::new()).is_empty());
}

#[test]
#[ignore = "requires separately prepared 104319 runtime with crowd control graphs"]
fn prepared_control_voice_keeps_the_speaker_named_by_the_retail_node() {
    let dir = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = GraphStore::load(dir.join("data/game_graphs_174.json.gz")).unwrap();
    let action = graphs.graph(0x224e).unwrap().node(83).unwrap();
    assert_eq!(action.class, "STUStatescriptActionCriteria");
    let vars = Variables::from([(28714, Value::Entity(82))]);
    assert_eq!(
        evaluate_criterion(action.fields.clone(), vars),
        vec![VoiceRequest {
            cue: 0x0D80_0000_0000_1EFE,
            speaker: 82,
            subject: Some(91),
            audience: None
        }]
    );
    assert!(evaluate_criterion(action.fields.clone(), Variables::new()).is_empty());
}

#[test]
#[ignore = "requires separately prepared 104319 runtime with dynamite and shared body graphs"]
fn prepared_damage_over_time_tag_selects_the_victims_authored_criterion() {
    let dir = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let stock = GraphStore::load(dir.join("data/game_graphs_174.json.gz")).unwrap();
    let body = stock.graph(0x01ca).unwrap();
    let mut entry = body.node(6).unwrap().fields.clone();
    let mut member = body.node(192).unwrap().fields.clone();
    let mut voice = body.node(193).unwrap().fields.clone();
    // Isolate the original message bindings and tag branch from unrelated
    // preconditions on the room and hero. The caller supplies the settled tags.
    entry["m_outPlug"] = plug(&[(1, "m_inPlug")], &[]);
    member["m_truePlug"] = plug(&[(2, "m_inPlug")], &[]);
    member["m_falsePlug"] = plug(&[], &[]);
    voice["m_outPlug"] = plug(&[], &[]);
    let g = graph(
        84,
        vec![
            node(0, "STUStatescriptEntryGameMessage", None, entry),
            node(1, "STU_821116A6", None, member),
            node(2, "STUStatescriptActionCriteria", None, voice),
        ],
        json!([]),
        json!([0]),
    );
    let mut host = VoiceHost::default();
    let mut component = Component::start(
        Arc::new(store(vec![g])),
        91,
        Variables::new(),
        vec![Root::new(1, 84)],
        0,
        &mut host,
        MAX_INIT_WORK,
    )
    .unwrap();
    let modify = stock.graph(0x150f).unwrap().node(1).unwrap();
    let tags = modify
        .field("m_modifyHealth.m_148F3152")
        .unwrap()
        .as_array()
        .unwrap()
        .iter()
        .map(|tag| Value::Asset(asset_guid(&tag["m_EE3055BD"]["m_identifier"]).unwrap()))
        .collect();
    for tags in [Vec::new(), tags, Vec::new()] {
        component
            .queue_entity_message_from(
                82,
                0x0240_0000_0000_0011,
                Variables::from([
                    (0x3d, Value::Array(tags)),
                    (0x12, Value::Entity(82)),
                    (0x13, Value::Float(2.0)),
                    (0x65, Value::Vector([1.0, 0.0, 0.0])),
                ]),
            )
            .unwrap();
        component.run_now(&mut host).unwrap();
    }
    assert_eq!(
        host.0,
        vec![VoiceRequest {
            cue: 0x0d80_0000_0000_3176,
            speaker: 91,
            subject: None,
            audience: None
        }]
    );
}

#[test]
fn unsupported_status_does_not_halt_or_replace_the_original_body() {
    let base = graph(70, vec![], json!([]), json!([]));
    let bad = graph(
        71,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
            ),
            node(1, "STUStatescriptActionUnknownPublicTest", None, json!({})),
        ],
        json!([]),
        json!([0]),
    );
    let mut host = VoiceHost::default();
    let mut component = Component::start(
        Arc::new(store(vec![base, bad])),
        91,
        Variables::new(),
        vec![Root::new(1, 70)],
        0,
        &mut host,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert!(matches!(
        component.attach_status_script(71, 82, Variables::new(), &mut host),
        Err(InitFault::StatusScript { graph: 71, .. })
    ));
    component.advance_frame(1, &mut host).unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(snapshot.initialization_complete());
    assert_eq!(snapshot.instances.len(), 1);
    assert_eq!(snapshot.instances[&1].graph, 70);
}

#[test]
fn foreign_play_runs_on_the_named_body_even_when_local_instance_ids_collide() {
    let base = graph(12, vec![], json!([]), json!([]));
    let reaction = graph(
        13,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_inPlug")],&[])}),
            ),
            node(
                1,
                "STUStatescriptActionCriteria",
                None,
                json!({
                    "m_identifier":{"$":"STUConfigVarIdentifier","m_identifier":"0x0D80000000000091"},
                    "m_targets":{"$":"STU_B5A0CAF0"},
                }),
            ),
        ],
        json!([]),
        json!([0]),
    );
    let mut host = VoiceHost::default();
    let mut anchor = Root::new(0, 12);
    anchor.server_only = true;
    let mut receiver = Component::start(
        Arc::new(store(vec![base, reaction])),
        91,
        Variables::new(),
        vec![anchor],
        0,
        &mut host,
        MAX_INIT_WORK,
    )
    .unwrap();
    let caller = Site {
        entity: 82,
        instance: 1,
        graph: 75,
        node: Some(3),
    };
    let request = PlayScript {
        source: 82,
        graph: 13,
        overrides: vec![],
        parameters: vec![],
        policy: 1,
        auto_retire: false,
    };
    receiver
        .receive_room_play_script(caller, &request, &mut host)
        .unwrap();
    assert_eq!(
        host.0,
        vec![VoiceRequest {
            cue: 0x0d80_0000_0000_0091,
            speaker: 91,
            subject: None,
            audience: None
        }]
    );
    let instance = receiver
        .snapshot()
        .unwrap()
        .instances
        .values()
        .find(|i| i.graph == 13)
        .unwrap();
    assert!(!instance.is_server_only());
}
