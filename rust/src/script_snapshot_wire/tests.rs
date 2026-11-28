use super::encode::write_mask;
mod arming;
mod callouts;
mod owner_instance_visibility;
use super::*;
use crate::script_expr::Scope;
use crate::script_init::{
    Diagnostic, EmptyWorld, Issue, MAX_INIT_WORK, MovementSample, Root, Site, Snapshot, initialize,
};
use crate::script_stream::{BodyStream, ChangeKey, DeltaKind};
use serde_json::{Value as Json, json};

mod voice;

const ENTITY: u32 = 0xa000_0101;
pub(crate) const CODECS: &str = r#"{"classes":{
    "12340001":{"name":"STUStatescriptState","codec":"none","stec":false,"status":"VERIFIED","layout":[]},
    "12340002":{"name":"STUStatescriptStateBooleanSwitch","codec":"switch","stec":false,"status":"VERIFIED",
        "layout":[{"op":"bits","f":"current","n":1}]},
    "B591EBFE":{"name":"STU_B591EBFE","codec":"two_bits","stec":false,"status":"VERIFIED",
        "layout":[{"op":"bits","f":"a","n":1},{"op":"if","c":"!a",
                   "then":[{"op":"bits","f":"b","n":1}],"else":[]}]},
    "12340003":{"name":"STU_37D754C8","codec":"subscript","stec":false,"status":"VERIFIED",
        "layout":[{"op":"if","c":"H","then":[{"op":"w_u16","f":"child"},{"op":"bits","f":"flag","n":1}],"else":[]}]},
    "12340004":{"name":"STUStatescriptStateGameMessageEntry","codec":"message","stec":false,"status":"VERIFIED",
        "layout":[{"op":"if","c":"H","then":[{"op":"bits","f":"has_sender","n":1}],"else":[]},
                  {"op":"bits","f":"stacked","n":1}]},
    "12340005":{"name":"STUStatescriptStateWait","codec":"none","stec":false,"status":"VERIFIED","layout":[]},
    "594C2D80":{"name":"STUStatescriptStateStack","codec":"stack","stec":false,"status":"VERIFIED",
        "layout":[{"op":"if","c":"H","then":[{"op":"bits","f":"top","n":1},
                  {"op":"bits","f":"under","n":1}],"else":[]}]},
    "B480A974":{"name":"STUStatescriptStateLogicalButton","codec":"button","stec":true,"status":"VERIFIED",
        "layout":[{"op":"if","c":"H","then":[{"op":"bits","f":"counter","n":4}],"else":[]}]},
    "C38B92B4":{"name":"STUStatescriptStateAbility","codec":"ability","stec":false,"status":"VERIFIED",
        "layout":[{"op":"bits","f":"flags","n":8},{"op":"link","f":"link"},
                  {"op":"bits","f":"has_cur","n":1},{"op":"if","c":"has_cur",
                   "then":[{"op":"f32","f":"cur"},{"op":"bits","f":"has_rate","n":1},
                           {"op":"if","c":"has_rate","then":[{"op":"f32","f":"rate"}],"else":[]},
                           {"op":"var_b","f":"t"}],"else":[]}]},
    "2F4E2E3F":{"name":"STU_2F4E2E3F","codec":"none","stec":false,"status":"VERIFIED","layout":[]},
    "6AE5D1A1":{"name":"STUStatescriptStateChaseVar","codec":"chase","stec":false,"status":"VERIFIED",
        "layout":[{"op":"bits","f":"vector","n":1},{"op":"bits","f":"has_remaining","n":1},
                  {"op":"bits","f":"reached","n":1},{"op":"f32","f":"x"},
                  {"op":"if","c":"vector","then":[{"op":"f32","f":"y"},{"op":"f32","f":"z"}],"else":[]},
                  {"op":"if","c":"has_remaining","then":[{"op":"bits","f":"big","n":1},
                   {"op":"if","c":"big","then":[{"op":"bits","f":"remaining","n":32}],
                    "else":[{"op":"bits","f":"remaining","n":16}]}],"else":[]},
                  {"op":"bits","f":"has_last","n":1},{"op":"if","c":"has_last",
                   "then":[{"op":"var_b","f":"t"}],"else":[]}]},
    "EED6D952":{"name":"STUStatescriptStateWeaponVolley","codec":"volley","stec":true,"status":"VERIFIED",
        "layout":[{"op":"if","c":"H","then":[{"op":"signed_var","f":"t1"},
                  {"op":"bits","f":"has_offset","n":1},{"op":"if","c":"has_offset",
                   "then":[{"op":"signed_var","f":"offset"},{"op":"w_u16","f":"subindex"}],"else":[]}],
                   "else":[]},
                  {"op":"bits","f":"has_volleys","n":1},{"op":"if","c":"has_volleys",
                   "then":[{"op":"w_u16","f":"volleys"}],"else":[]},{"op":"bits","f":"counter","n":6}]},
    "D74D4F47":{"name":"STU_D74D4F47","codec":"frames","stec":false,"status":"VERIFIED",
        "layout":[{"op":"bits","f":"target","n":32}]},
    "96F1DBCF":{"name":"STU_96F1DBCF","codec":"flags13","stec":false,"status":"VERIFIED",
        "layout":[{"op":"bits","f":"low","n":6},{"op":"bits","f":"more","n":1},
                  {"op":"if","c":"more","then":[{"op":"bits","f":"high","n":7}],"else":[]}]},
    "77E13026":{"name":"STU_77E13026","codec":"hits","stec":false,"status":"VERIFIED",
        "layout":[{"op":"w_u16","f":"n"},{"op":"repeat","count":"n","f":"hits","body":[]},
                  {"op":"bits","n":1,"f":"flag"}]},
    "41E3C7A7":{"name":"STUStatescriptStateTrackTargets","codec":"targets","stec":false,"status":"VERIFIED",
        "layout":[{"op":"bits","n":1,"f":"has_list"},{"op":"if","c":"has_list",
                   "then":[{"op":"w_u16","f":"n"},{"op":"repeat","count":"n","f":"targets","body":[]}],
                   "else":[]}]},
    "9028E035":{"name":"STU_9028E035","codec":"link","stec":false,"status":"VERIFIED",
        "layout":[{"op":"link","f":"link"}]}
}}"#;

fn node(position: u16, class: &str, state: Option<u16>, mut fields: Json) -> Json {
    fields["m_2BBEEAB8"] = json!(state.is_some());
    fields["m_ADEB6E05"] = json!(false);
    json!({
        "pos":position, "class":class, "uid":position, "rid":position,
        "remote":-1, "state":state, "client_only":false, "server_only":false,
        "fields":fields,
    })
}

fn graph(
    index: u32,
    nodes: Vec<Json>,
    states: Json,
    entries: Json,
    remote: Json,
    sync: Json,
) -> Json {
    json!({
        "index":index, "items":nodes.len(), "nodes":nodes, "states":states,
        "entries":entries, "remote":remote, "sync_vars":sync, "containers":{},
        "unknown_nodes":[], "fields":{"m_9CEC6985":["0x0D800000000001A4"]},
        "header":{
            "m_nodesBitCount":4, "m_statesBitCount":4,
            "m_remoteSyncNodesBitCount":4, "m_syncVarsBitCount":4
        }
    })
}

#[test]
fn projected_self_send_matches_ordinary_send_wire_lifecycle() {
    use crate::script_init::{ABORT_PLUG, Component};
    use std::sync::Arc;

    const SEND_CLASS: &str = "STU_38EE1100";
    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-projected-message-topics-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let topic = &input["topic"];
    let abort = &input["abort_message"];
    let abort_id = crate::script_graph::asset_guid(abort).unwrap();
    let sender_graph = u32::try_from(input["sender_graph"].as_u64().unwrap()).unwrap();
    let receiver_graph = u32::try_from(input["receiver_graph"].as_u64().unwrap()).unwrap();
    let parameter = &input["parameters"][0];
    let parameter_id = parameter["id"].as_str().unwrap();
    let parameter_variable = parameter["variable"].as_u64().unwrap();

    let mut registry: Json = serde_json::from_str(CODECS).unwrap();
    registry["classes"]["38EE1100"] = json!({
        "name":SEND_CLASS,"codec":"send","stec":false,"status":"VERIFIED","layout":[]
    });
    let codecs = CodecTable::from_json(&registry.to_string()).unwrap();

    let capture = |projected| {
        let fields = if projected {
            json!({
                "m_gameMessage":{"m_gameMessage":topic},
                "m_target":{"$":"STU_9B213168","m_targets":{"$":"STU_B5A0CAF0"}},
                "m_67FD6A60":1,
                "m_params":[{"m_B5051BCE":parameter_id,"m_value":{"$":"UnevaluatedWireOperand"}}]
            })
        } else {
            json!({})
        };
        let mut send = node(1, SEND_CLASS, Some(0), fields);
        send["remote"] = json!(0);
        let sender = graph(
            sender_graph,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({
                        "m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]]}
                    }),
                ),
                send,
                node(
                    2,
                    "STUStatescriptEntryGameMessage",
                    None,
                    json!({
                        "m_gameMessage":{"m_gameMessage":abort},
                        "m_outPlug":{"$":"Plug","links":[[1,ABORT_PLUG]]}
                    }),
                ),
            ],
            json!([1]),
            json!([0, 2]),
            json!([1]),
            json!([]),
        );
        let mut receiver = node(
            0,
            "STUStatescriptEntryGameMessage",
            None,
            json!({
                "m_gameMessage":{"m_gameMessage":topic},
                "m_outPlug":{"$":"Plug","links":[]},
                "m_params":[{"m_B5051BCE":parameter_id,"m_out_Var":{
                    "$":"STUConfigVarDynamic","m_identifier":parameter_variable,"m_60DB8F99":true
                }}]
            }),
        );
        receiver["client_only"] = json!(true);
        let receiver = graph(
            receiver_graph,
            vec![receiver],
            json!([]),
            json!([0]),
            json!([]),
            json!([]),
        );
        let mut bundle = json!({"version":1,"graphs":[sender,receiver],"bodies":{},"missing":[]});
        if projected {
            bundle["runtime_state_metadata"] = json!({"projected_message_topics":[topic]});
        }
        let graphs =
            Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
        let mut component = Component::start(
            Arc::clone(&graphs),
            ENTITY,
            Variables::new(),
            vec![Root::new(1, sender_graph)],
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        let active = component.snapshot().unwrap().clone();
        assert!(active.initialization_complete(), "{:?}", active.diagnostics);
        let state = &active.instances[&1].states[&0];
        assert!(state.active);
        assert_eq!(
            state.kind,
            if projected {
                StateKind::ProjectedSelfSend
            } else {
                StateKind::SendMessage
            }
        );
        assert_eq!(
            component
                .queue_message(1, abort_id, Variables::new())
                .unwrap(),
            1
        );
        component.run_now(&mut EmptyWorld).unwrap();
        let retired = component.snapshot().unwrap();
        assert!(
            retired.initialization_complete(),
            "{:?}",
            retired.diagnostics
        );
        let encoder = SnapshotEncoder::new(&graphs, &codecs);
        [
            (View::Owner, DeltaKind::Correction(1)),
            (View::Observer, DeltaKind::Observer { resend: false }),
        ]
        .map(|(view, kind)| {
            let before = encoder.project(&active, view).unwrap();
            assert!(before.instances[&1].states[&0].payload.is_empty());
            let after = encoder.project_next(retired, &before).unwrap();
            assert!(after.instances[&1].states.is_empty());
            let delta = encoder
                .differences(&before, &after, &BTreeSet::new(), &BTreeSet::from([1]))
                .unwrap();
            assert!(delta.keys.contains(&ChangeKey::State(1, 0)));
            (
                encoder.full(&before).unwrap(),
                encoder.delta_frame(&after, &delta, kind, 16).unwrap(),
            )
        })
    };

    for ((on, off), (control_on, control_off)) in capture(true).into_iter().zip(capture(false)) {
        for (actual, control) in [(on, control_on), (off, control_off)] {
            assert_eq!(actual.bit_len(), control.bit_len());
            assert_eq!(actual.as_bytes(), control.as_bytes());
        }
    }
}

#[test]
fn runtime_class_metadata_preserves_owner_and_observer_wire_format() {
    const PLAIN: &str = "SyntheticRuntimePlain";
    const CLIENT: &str = "SyntheticClientState";

    let bundle = |mapped| {
        let mut starts = vec![json!([1, "m_beginPlug"])];
        if mapped {
            starts.push(json!([2, "m_beginPlug"]));
        }
        let mut root = graph(
            42,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({
                        "m_outPlug":{"$":"Plug","links":starts}
                    }),
                ),
                node(
                    1,
                    if mapped { PLAIN } else { "STUStatescriptState" },
                    Some(0),
                    json!({}),
                ),
                node(2, CLIENT, Some(1), json!({})),
            ],
            json!([1, 2]),
            json!([0]),
            json!([1, 2]),
            json!([]),
        );
        root["nodes"][1]["remote"] = json!(0);
        root["nodes"][2]["remote"] = json!(1);
        let mut data = json!({"version":1,"graphs":[root],"bodies":{},"missing":[]});
        if mapped {
            data["runtime_state_metadata"] = json!({
                "plain_classes":[PLAIN],
                "client_states":[{"graph":42,"state":1,"rid":2,"class":CLIENT}]
            });
        }
        GraphStore::from_json(&serde_json::to_vec(&data).unwrap()).unwrap()
    };
    let builtin = bundle(false);
    let mapped = bundle(true);
    let start = |graphs| {
        initialize(
            graphs,
            ENTITY,
            Variables::new(),
            vec![Root::new(1, 42)],
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap()
    };
    let builtin_snapshot = start(&builtin);
    let mapped_snapshot = start(&mapped);
    assert!(
        mapped_snapshot.initialization_complete(),
        "{:?}",
        mapped_snapshot.diagnostics
    );
    assert!(mapped_snapshot.instances[&1].states[&0].active);
    assert!(!mapped_snapshot.instances[&1].states.contains_key(&1));

    let mapped_graph = mapped.graph(42).unwrap();
    let builtin_graph = builtin.graph(42).unwrap();
    assert_eq!(
        mapped_graph.owner_positions(),
        builtin_graph.owner_positions()
    );
    assert_eq!(
        mapped_graph.remote_positions(),
        builtin_graph.remote_positions()
    );
    assert_eq!(mapped_graph.owner_positions(), [1, 2]);
    assert_eq!(mapped_graph.remote_positions(), [1, 2]);
    assert_eq!(mapped_graph.state(0).unwrap().class, PLAIN);
    assert_eq!(mapped_graph.state(1).unwrap().class, CLIENT);

    let mut registry: Json = serde_json::from_str(CODECS).unwrap();
    registry["classes"]["F3410001"] = json!({
        "name":PLAIN,"codec":"none","stec":false,"status":"VERIFIED","layout":[]
    });
    let codecs = CodecTable::from_json(&registry.to_string()).unwrap();
    let builtin_encoder = SnapshotEncoder::new(&builtin, &codecs);
    let mapped_encoder = SnapshotEncoder::new(&mapped, &codecs);
    let original_codecs = CodecTable::from_json(CODECS).unwrap();
    let unmapped_encoder = SnapshotEncoder::new(&mapped, &original_codecs);
    for view in [View::Owner, View::Observer] {
        let baseline = builtin_encoder
            .full(&builtin_encoder.project(&builtin_snapshot, view).unwrap())
            .unwrap();
        let projected = mapped_encoder.project(&mapped_snapshot, view).unwrap();
        assert_eq!(
            projected.instances[&1]
                .states
                .keys()
                .copied()
                .collect::<Vec<_>>(),
            [0]
        );
        let encoded = mapped_encoder.full(&projected).unwrap();
        assert_eq!(encoded.bit_len(), baseline.bit_len());
        assert_eq!(encoded.as_bytes(), baseline.as_bytes());
        assert!(matches!(
            unmapped_encoder.project(&mapped_snapshot, view),
            Err(SnapshotError::UnsupportedState { class, .. }) if class == PLAIN
        ));
        // A normal-policy (reference production) snapshot omits the
        // writerless state and still projects the instance; it must never
        // serialize an unverified payload for it. In this fixture the
        // writerless state is the only projected entry, so the state table
        // comes back empty while the instance itself survives.
        let mut relaxed = mapped_snapshot.clone();
        relaxed.strict = false;
        let projected = unmapped_encoder.project(&relaxed, view).unwrap();
        assert!(
            projected.instances.contains_key(&1),
            "the instance must still project"
        );
        assert!(
            projected.instances[&1].states.is_empty(),
            "the writerless state must be omitted, not projected with an unverified payload: {:?}",
            projected.instances[&1].states.keys().collect::<Vec<_>>()
        );
    }
}

fn fixture() -> (GraphStore, Snapshot) {
    let root = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({
                    "m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"],[2,"m_beginPlug"],
                        [3,"m_beginPlug"],[4,"m_beginPlug"]],"m_FF3DAF1E":[]}
                }),
            ),
            node(
                1,
                "STUStatescriptStateBooleanSwitch",
                Some(0),
                json!({
                    "m_condition":{"$":"STUConfigVarBool","m_value":true}
                }),
            ),
            node(
                2,
                "STU_37D754C8",
                Some(1),
                json!({
                    "m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{"m_graph":"0x058000000000002B"}}}
                }),
            ),
            node(
                3,
                "STUStatescriptState",
                Some(2),
                json!({"m_45216F79":null}),
            ),
            node(4, "STUStatescriptStateGameMessageEntry", Some(3), json!({})),
        ],
        json!([1, 2, 3, 4]),
        json!([0]),
        json!([null, 3, 1, 2, 4, 1]),
        json!([[100, 0, 0], [100, 0, 0], [200, 1, 0], [null, 0, 0]]),
    );
    let child = graph(43, vec![], json!([]), json!([]), json!([]), json!([]));
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":[root,child], "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap();
    let entity = Variables::from([
        (7, Value::Asset(0x0580_0000_0000_0042)),
        (55, Value::Integer(-12)),
        (253, Value::Float(200.0)),
        (420, Value::Integer(666)),
    ]);
    let mut root = Root::new(1, 42);
    root.variables.extend([
        (100, Value::Integer(17)),
        (200, Value::Bool(true)),
        (300, Value::Float(3.5)),
        (400, Value::Null),
        (420, Value::Integer(2)),
    ]);
    let snapshot = initialize(
        &graphs,
        ENTITY,
        entity,
        vec![root],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    (graphs, snapshot)
}

fn check(bits: &BitWriter, length: usize, hex: &str) {
    let actual: String = bits
        .as_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    assert_eq!((bits.bit_len(), actual.as_str()), (length, hex));
}

#[test]
#[ignore = "requires the separately prepared 104319 Junkenstein runtime"]
fn mission_notifications_include_authored_entity_inputs() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        scene_assets::SceneAssets,
    };
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&root).unwrap();
    let maps = MapCatalog::load(root.join("data/game_maps_174.json")).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = maps
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let scene = assets.prepare_junkenstein(map, card, 0).unwrap();
    let snapshot = scene.encounter_snapshot().unwrap();
    let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
    let encoder = SnapshotEncoder::new(assets.graphs(), &codecs);
    let projected = encoder.mission_notifications(snapshot).unwrap().unwrap();
    for variable in [6751, 8439] {
        assert_eq!(
            projected.variables.values.get(&variable),
            snapshot.variables.get(&variable)
        );
        assert!(projected.variables.values.contains_key(&variable));
    }
    let graph = assets.graphs().graph(0x1188).unwrap();
    for variable in projected.variables.values.keys() {
        assert!(
            graph
                .sync_vars
                .iter()
                .any(|var| var.0 == Some(*variable) && var.1 == 1)
        );
    }
    let mut states_only = projected.clone();
    states_only.variables = ProjectedVariables::default();
    let initial = encoder.mounted_delta(None, &projected).unwrap().unwrap();
    let without_inputs = encoder.mounted_delta(None, &states_only).unwrap().unwrap();
    assert_ne!(
        (initial.bit_len(), initial.as_bytes()),
        (without_inputs.bit_len(), without_inputs.as_bytes())
    );
    assert!(
        encoder
            .mounted_delta(Some(&projected), &projected)
            .unwrap()
            .is_none()
    );
    let mut changed = projected.clone();
    changed.variables.values.insert(6751, Value::Asset(1));
    assert!(
        encoder
            .mounted_delta(Some(&projected), &changed)
            .unwrap()
            .is_some()
    );
}

#[test]
fn baseline_matches_python_for_both_views_and_enters_the_acknowledged_stream() {
    let (graphs, snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    assert_eq!(owner.entity(), ENTITY);
    assert_eq!(owner.instance_count(), 2);
    assert_eq!(owner.instances[&2].parent, Some((1, 1)));
    assert_eq!(
        owner.variables.values.keys().copied().collect::<Vec<_>>(),
        [7, 55]
    );
    assert_eq!(
        owner.instances[&1]
            .variables
            .values
            .keys()
            .copied()
            .collect::<Vec<_>>(),
        [100, 200, 300]
    );
    assert!(owner.instances[&1].states.contains_key(&2));
    assert!(!observer.instances[&1].states.contains_key(&2));

    check(
        &encoder.full(&owner).unwrap(),
        250,
        "081500102b00142000e820031610e246e8012811840cc5128000006040000000",
    );
    check(
        &encoder.full(&observer).unwrap(),
        257,
        "081500102b00142000e820031610e246e8012811840cc512800000604000f83b00",
    );
    let mut stream = BodyStream::new(ENTITY);
    let chunk = encoder.queue_full(&mut stream, &owner).unwrap();
    assert_eq!(chunk.update.entity, ENTITY);
    assert!(chunk.resend);
    assert!(!stream.ready_to_send(chunk.last));
    assert!(stream.base().is_none());
    check(
        chunk.update.chunk.as_ref().unwrap(),
        286,
        "000004a08f500100b102400102800e326001216e841e801241c8502c0108000006040000",
    );
    stream.create_arrived();
    assert!(stream.ready_to_send(chunk.last));
    assert!(stream.arrived(chunk.last).advanced);
    let (_, base) = stream.base().unwrap();
    assert_eq!(base.view(), View::Owner);
    assert!(base.instances.values().all(|item| item.states.is_empty()));
    assert!(!owner.instances[&1].states.is_empty());
}

#[test]
fn wire_identity_tracks_child_replacement_and_retirement_in_both_views() {
    use super::identity_test_reader::{IdentityError, IdentityReader};
    let (graphs, mut snapshot) = fixture();
    snapshot.variables.clear();
    snapshot.bindings.clear();
    snapshot.resolved_bindings.clear();
    for instance in snapshot.instances.values_mut() {
        instance.states.clear();
        instance.variables.clear();
    }
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    for view in [View::Owner, View::Observer] {
        let mut current = snapshot.clone();
        let projected = encoder.project(&current, view).unwrap().without_events();
        let mut stream = BodyStream::new(ENTITY);
        let baseline = encoder.queue_full(&mut stream, &projected).unwrap();
        stream.create_arrived();
        let mut reader = IdentityReader::default();
        reader
            .apply(
                baseline.update.chunk.as_ref().unwrap(),
                true,
                view == View::Owner,
            )
            .unwrap();
        reader.verify(&current, &graphs);
        assert!(stream.arrived(baseline.last).advanced);

        let child = current.instances.shift_remove(&2).unwrap();
        current.instances.insert(3, child.clone());
        let next = encoder
            .project_next(&current, &projected)
            .unwrap()
            .without_events();
        let kind = match view {
            View::Owner => DeltaKind::InitialOwner(100),
            View::Observer => DeltaKind::Observer { resend: true },
        };
        let chunk = encoder
            .queue_delta(&mut stream, &next, kind, 16)
            .unwrap()
            .unwrap();
        stream.sent(chunk.last);
        assert!(stream.lost(chunk.last).retry);
        reader
            .apply(
                chunk.update.chunk.as_ref().unwrap(),
                false,
                view == View::Owner,
            )
            .unwrap();
        reader.verify(&current, &graphs);
        assert!(reader.was_removed(2));
        assert!(stream.arrived(chunk.last).advanced);

        current.instances.shift_remove(&3).unwrap();
        let next = encoder
            .project_next(&current, &projected)
            .unwrap()
            .without_events();
        let kind = match view {
            View::Owner => DeltaKind::Correction(116),
            View::Observer => DeltaKind::Observer { resend: true },
        };
        let chunk = encoder
            .queue_delta(&mut stream, &next, kind, 16)
            .unwrap()
            .unwrap();
        reader
            .apply(
                chunk.update.chunk.as_ref().unwrap(),
                false,
                view == View::Owner,
            )
            .unwrap();
        reader.verify(&current, &graphs);
        assert!(reader.was_removed(3));
        assert!(stream.arrived(chunk.last).advanced);

        // A forged snapshot reusing a retired ID must not be accepted by the reader.
        current.instances.insert(2, child);
        let next = encoder
            .project_next(&current, &projected)
            .unwrap()
            .without_events();
        let kind = match view {
            View::Owner => DeltaKind::Correction(132),
            View::Observer => DeltaKind::Observer { resend: true },
        };
        let chunk = encoder
            .queue_delta(&mut stream, &next, kind, 16)
            .unwrap()
            .unwrap();
        assert_eq!(
            reader.apply(
                chunk.update.chunk.as_ref().unwrap(),
                false,
                view == View::Owner,
            ),
            Err(IdentityError::ReintroducedRemoved(2))
        );
    }
}

#[test]
fn owner_frames_withhold_switches_polled_on_natives_the_client_may_answer_differently() {
    let untrusted = json!({"$":"STU_E95A9C77","m_EE729DCB":0});
    let fixture = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({
                    "m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"],[2,"m_beginPlug"],
                        [3,"m_beginPlug"],[4,"m_beginPlug"]],"m_FF3DAF1E":[]}
                }),
            ),
            node(
                1,
                "STUStatescriptStateBooleanSwitch",
                Some(0),
                json!({
                    "m_condition":untrusted,
                    "m_falseSubgraphPlug":{"$":"Plug","links":[[5,"m_beginPlug"]],
                        "m_FF3DAF1E":[4]}
                }),
            ),
            node(
                2,
                "STUStatescriptStateBooleanSwitch",
                Some(1),
                json!({"m_condition":{"$":"STU_4A101787","m_EE729DCB":0}}),
            ),
            node(
                3,
                "STUStatescriptStateBooleanSwitch",
                Some(2),
                json!({
                    "m_condition":{"$":"STUConfigVarDynamic","m_identifier":100,
                        "m_60DB8F99":false}
                }),
            ),
            node(4, "STU_DEBD057B", Some(3), json!({"m_262AE982":untrusted})),
            node(5, "STUStatescriptState", Some(4), json!({})),
        ],
        json!([1, 2, 3, 4, 5]),
        json!([0]),
        json!([1]),
        json!([]),
    );
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":[fixture], "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap();
    let snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 42)],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    let states = &snapshot.instances[&1].states;
    assert!(states.values().all(|state| state.active));
    let flagged: Vec<_> = (0..5)
        .map(|raw| states[&raw].reads_untrusted_native())
        .collect();
    assert_eq!(flagged, [true, false, false, true, false]);
    let mut raw: Json = serde_json::from_str(CODECS).unwrap();
    raw["classes"]["DEBD057B"] = json!({
        "name":"STU_DEBD057B","codec":"none","stec":false,"status":"VERIFIED","layout":[]
    });
    let codecs = CodecTable::from_json(&raw.to_string()).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    // The live subgraph of a withheld switch is still sent.
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    assert_eq!(owner.instance_states(1), Some(BTreeSet::from([1, 2, 4])));
    // Observer frames carry every remote state that is on.
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    assert_eq!(observer.instance_states(1), Some(BTreeSet::from([0])));
}

#[test]
fn a_switch_withheld_once_stays_off_owner_frames_after_it_begins_again() {
    use crate::script_init::Component;
    use std::sync::Arc;

    let variable = json!({"$":"STUConfigVarDynamic","m_identifier":100,"m_60DB8F99":false});
    let message = |position, message: &str, input, params: Json| {
        node(
            position,
            "STUStatescriptEntryGameMessage",
            None,
            json!({
                "m_gameMessage":{"m_gameMessage":message},
                "m_params":params,
                "m_outPlug":{"$":"Plug","links":[[1,input]],"m_FF3DAF1E":[]}
            }),
        )
    };
    // The native answers only while the variable is unset.
    let fixture = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]],"m_FF3DAF1E":[]}}),
            ),
            node(
                1,
                "STUStatescriptStateBooleanSwitch",
                Some(0),
                json!({
                    "m_condition":{
                        "$":"STU_91EFD5B1",
                        "m_value":variable,
                        "m_default":{"$":"STU_E95A9C77","m_EE729DCB":0},
                    }
                }),
            ),
            message(2, "0x0240000000000001", "m_F198FD3A", json!([])),
            message(
                3,
                "0x0240000000000002",
                "m_beginPlug",
                json!([{"m_B5051BCE":"0x0A40000000000004","m_out_Var":variable}]),
            ),
        ],
        json!([1]),
        json!([0, 2, 3]),
        json!([1]),
        json!([]),
    );
    let graphs = Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version":1, "graphs":[fixture], "bodies":{}, "missing":[]
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    let mut component = Component::start(
        Arc::clone(&graphs),
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 42)],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let switch = |component: &Component| {
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let state = &snapshot.instances[&1].states[&0];
        let owner = encoder.project(snapshot, View::Owner).unwrap();
        (
            state.active,
            state.activation,
            state.kind,
            state.reads_untrusted_native(),
            owner.instance_states(1).unwrap().contains(&0),
        )
    };
    assert_eq!(
        switch(&component),
        (true, 1, StateKind::BooleanSwitch(false), true, false)
    );
    for (guid, expected) in [
        (
            0x0240_0000_0000_0001,
            (false, 1, StateKind::BooleanSwitch(false), true, false),
        ),
        // Like the reference's per-instance record, the flag survives
        // the end, although this activation reads only the variable.
        (
            0x0240_0000_0000_0002,
            (true, 2, StateKind::BooleanSwitch(true), true, false),
        ),
    ] {
        let parameter = Variables::from([(4, Value::Bool(true))]);
        assert_eq!(component.queue_message(1, guid, parameter).unwrap(), 1);
        component.run_now(&mut EmptyWorld).unwrap();
        assert_eq!(switch(&component), expected);
    }
}

#[test]
fn unavailable_authored_observation_does_not_block_snapshot_projection() {
    use crate::script_init::{Component, World};
    use std::sync::Arc;

    const GRAPH: u32 = 123;
    const TARGET: u32 = 91;
    const TARGET_VARIABLE: u32 = 17;
    const OUTPUT: u32 = 18;
    const POSITION: [f64; 3] = [3.0, 2.0, 1.0];

    struct PositionWorld(Option<[f64; 3]>);

    impl World for PositionWorld {
        fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
            None
        }

        fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
            assert_eq!(entity, TARGET);
            self.0
        }
    }

    let variable = |id| json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":true});
    let mut observe = node(
        1,
        "SyntheticObservePosition",
        Some(0),
        json!({
            "value":{"$":"EntityPosition","entity":variable(TARGET_VARIABLE)},
            "output":variable(OUTPUT)
        }),
    );
    observe["server_only"] = json!(true);
    observe["state_behavior"] = json!("observe");
    let body = graph(
        GRAPH,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"],[2,"m_beginPlug"]]}}),
            ),
            observe,
            node(2, "STUStatescriptState", Some(1), json!({})),
        ],
        json!([1, 2]),
        json!([0]),
        json!([2]),
        json!([]),
    );
    let graphs = Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version":1, "graphs":[body], "bodies":{}, "missing":[]
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    let mut world = PositionWorld(None);
    let mut component = Component::start(
        Arc::clone(&graphs),
        ENTITY,
        Variables::from([
            (TARGET_VARIABLE, Value::Entity(TARGET)),
            (OUTPUT, Value::Vector([0.0; 3])),
        ]),
        vec![Root::new(1, GRAPH)],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);

    for (frame, available, expected) in [(1, None, [0.0; 3]), (2, Some(POSITION), POSITION)] {
        world.0 = available;
        component.advance_frame(frame, &mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.diagnostics.len(), 1);
        let diagnostic = &snapshot.diagnostics[0];
        assert_eq!(diagnostic.site.node, Some(1));
        assert_eq!(diagnostic.issue, Issue::ObservationUnavailable);
        assert!(snapshot.initialization_complete());

        for view in [View::Owner, View::Observer] {
            let projected = encoder
                .project(snapshot, view)
                .expect("transient authored observation should not block projection");
            assert_eq!(projected.variables.values[&OUTPUT], Value::Vector(expected));
            assert!(projected.instances[&1].states.contains_key(&1));
            assert!(!projected.instances[&1].states.contains_key(&0));
        }
    }
}

#[test]
fn unsupported_active_states_and_initialization_diagnostics_are_explicit() {
    let (graphs, mut snapshot) = fixture();
    let mut raw: Json = serde_json::from_str(CODECS).unwrap();
    raw["classes"].as_object_mut().unwrap().remove("12340001");
    let codecs = CodecTable::from_json(&raw.to_string()).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    assert!(matches!(
        encoder.project(&snapshot, View::Owner),
        Err(SnapshotError::UnsupportedState {
            site: StateSite {
                instance: 1,
                graph: 42,
                state: 2
            },
            ..
        })
    ));
    assert!(encoder.project(&snapshot, View::Observer).is_ok());
    let first = Diagnostic {
        site: Site {
            entity: ENTITY,
            instance: 1,
            graph: 42,
            node: None,
        },
        issue: Issue::UnsupportedAction("fixture".into()),
        registration_rejected: false,
        server_only: false,
    };
    let last = Diagnostic {
        site: Site {
            entity: ENTITY,
            instance: 1,
            graph: 42,
            node: Some(7),
        },
        issue: Issue::UnsupportedState("later".into()),
        registration_rejected: true,
        server_only: false,
    };
    snapshot.diagnostics.extend([
        first.clone(),
        Diagnostic {
            server_only: true,
            ..first.clone()
        },
        Diagnostic {
            issue: Issue::ObservationUnavailable,
            ..first.clone()
        },
        last.clone(),
    ]);
    let error = encoder.project(&snapshot, View::Observer).unwrap_err();
    assert_eq!(
        error.to_string(),
        format!(
            "body {ENTITY:08X} has 2 initialization diagnostics: \
             002A:-:UnsupportedAction(\"fixture\"), 002A:7:UnsupportedState(\"later\")"
        )
    );
    let SnapshotError::Incomplete {
        entity,
        diagnostics,
    } = error
    else {
        panic!("expected initialization diagnostics");
    };
    assert_eq!(entity, ENTITY);
    assert_eq!(diagnostics, [first, last]);
}

#[test]
fn stack_projection_rejects_missing_values_and_invalid_owner_binding_sources() {
    let (graphs, mut snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let baseline = encoder.project(&snapshot, View::Owner).unwrap();
    snapshot.bindings.insert((0, 55), vec![invalid_binding()]);
    for view in [View::Owner, View::Observer] {
        assert!(matches!(
            encoder.project(&snapshot, view),
            Err(SnapshotError::MissingBoundValue {
                owner: 0,
                variable: 55,
            })
        ));
    }
    snapshot
        .resolved_bindings
        .insert((0, 55), Value::Bool(true));
    assert!(matches!(
        encoder.project_next(&snapshot, &baseline),
        Err(SnapshotError::InvalidBinding {
            owner: 0,
            variable: 55,
            ..
        })
    ));
}

/// Raw state 2 is not a stack, so projecting this binding fails.
fn invalid_binding() -> StackBinding {
    StackBinding {
        instance: 1,
        raw_state: 2,
        slot: 0,
        priority: crate::script_expr::StackPriority {
            level: 0,
            above_equals: true,
        },
    }
}

fn table() -> Value {
    Value::Map(vec![(Value::Asset(1), Value::Integer(2))])
}

fn set_variable(snapshot: &mut Snapshot, owner: u16, variable: u32, value: Option<Value>) {
    let values = match owner {
        0 => &mut snapshot.variables,
        id => &mut snapshot.instances.get_mut(&id).unwrap().variables,
    };
    match value {
        Some(value) => values.insert(variable, value),
        None => values.shift_remove(&variable),
    };
}

#[test]
fn unbound_maps_are_omitted_like_null_in_both_scopes_and_views() {
    let (graphs, mut snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    for owner in [0, 1] {
        for view in [View::Owner, View::Observer] {
            let mut full = |value| {
                set_variable(&mut snapshot, owner, 77, value);
                let projected = encoder.project(&snapshot, view).unwrap();
                let values = match owner {
                    0 => &projected.variables.values,
                    id => &projected.instances[&id].variables.values,
                };
                assert!(!values.contains_key(&77));
                let bits = encoder.full(&projected).unwrap();
                (bits.bit_len(), bits.as_bytes().to_vec())
            };
            let omitted = full(None);
            assert_eq!(full(Some(table())), omitted);
            assert_eq!(full(Some(Value::Null)), omitted);
        }
    }
}

#[test]
fn bound_maps_are_refused_before_their_bindings_are_checked() {
    let (graphs, mut snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    snapshot.bindings.insert((1, 77), vec![invalid_binding()]);
    set_variable(&mut snapshot, 1, 77, Some(Value::Null));
    snapshot.resolved_bindings.insert((1, 77), table());
    for view in [View::Owner, View::Observer] {
        let error = encoder.project(&snapshot, view).unwrap_err();
        assert!(matches!(
            error,
            SnapshotError::UnsupportedMap {
                owner: 1,
                variable: 77,
            }
        ));
        assert_eq!(
            error.to_string(),
            "variable 1:77 holds a map without a verified wire form"
        );
    }
}

#[test]
fn deltas_preserve_typed_variables_with_the_same_expression_truthiness() {
    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-typed-delta-v1.json"
    ))
    .unwrap();
    let variable = contract["input"]["variable"].as_u64().unwrap() as u32;
    let (graphs, mut snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let parse = |raw: &Json| match raw {
        Json::Null => None,
        Json::Bool(value) => Some(Value::Bool(*value)),
        Json::Number(value) => Some(Value::Integer(value.as_i64().unwrap())),
        _ => panic!("unexpected fixture value: {raw}"),
    };
    for (view, kind) in [
        (View::Owner, DeltaKind::InitialOwner(1000)),
        (View::Observer, DeltaKind::Observer { resend: true }),
    ] {
        for transition in contract["input"]["transitions"].as_array().unwrap() {
            let before = parse(&transition["from"]);
            let after = parse(&transition["to"]);
            assert!(crate::script_expr::equal(
                before.as_ref().unwrap_or(&Value::Null),
                after.as_ref().unwrap_or(&Value::Null),
            ));
            set_variable(&mut snapshot, 1, variable, before);
            let mut stream = BodyStream::new(ENTITY);
            stream.create_arrived();
            let base = encoder
                .queue_full(&mut stream, &encoder.project(&snapshot, view).unwrap())
                .unwrap();
            assert!(stream.arrived(base.last).advanced);
            set_variable(&mut snapshot, 1, variable, after.clone());
            let next = encoder
                .project_next(&snapshot, stream.base().unwrap().1)
                .unwrap();
            let delta = encoder
                .queue_delta(&mut stream, &next, kind, 16)
                .unwrap()
                .unwrap();
            assert!(
                stream.touched().contains(&ChangeKey::Variable(1, variable)),
                "{transition}"
            );
            assert!(stream.arrived(delta.last).advanced);
            assert_eq!(
                stream
                    .base()
                    .unwrap()
                    .1
                    .instance_variables(1)
                    .unwrap()
                    .get(&variable),
                after.as_ref(),
            );
        }
    }
}

#[test]
fn a_variable_becoming_a_map_deltas_like_it_becoming_null() {
    let (graphs, mut snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut deltas = |view, kind, value| {
        set_variable(&mut snapshot, 1, 77, Some(Value::Integer(5)));
        let mut stream = BodyStream::new(ENTITY);
        let baseline = encoder
            .queue_full(&mut stream, &encoder.project(&snapshot, view).unwrap())
            .unwrap();
        stream.create_arrived();
        stream.arrived(baseline.last);
        set_variable(&mut snapshot, 1, 77, Some(value));
        let next = encoder.project(&snapshot, view).unwrap();
        let chunk = encoder
            .queue_delta(&mut stream, &next, kind, 16)
            .unwrap()
            .unwrap();
        let keys: Vec<_> = stream.pending().map(|(_, _, keys)| keys.clone()).collect();
        let bits = chunk.update.chunk.unwrap();
        (bits.bit_len(), bits.as_bytes().to_vec(), keys)
    };
    for (view, kind) in [
        (View::Owner, DeltaKind::InitialOwner(1000)),
        (View::Observer, DeltaKind::Observer { resend: false }),
    ] {
        let map = deltas(view, kind, table());
        assert!(map.2[0].contains(&ChangeKey::Variable(1, 77)));
        assert_eq!(map, deltas(view, kind, Value::Null));
    }
}

#[test]
fn initial_owner_delta_matches_python_stack_bindings_and_payloads() {
    let entry = |targets: &[u16]| {
        let links: Vec<_> = targets.iter().map(|&id| (id, "m_beginPlug")).collect();
        node(
            0,
            "STUStatescriptEntry",
            None,
            json!({"m_outPlug":{"$":"Plug","links":links}}),
        )
    };
    let stack = |position, raw, variable, entity, value, priority: Option<&str>| {
        node(
            position,
            "STUStatescriptStateStack",
            Some(raw),
            json!({
                "m_out_Var":{"$":"STUConfigVarDynamic","m_identifier":variable,"m_60DB8F99":entity},
                "m_value":value,
                "m_priority":{"m_priority":priority},
            }),
        )
    };
    let mut root = Root::new(1, 42);
    root.variables.insert(100, Value::Integer(9));
    let main = graph(
        42,
        vec![
            entry(&[1, 2, 3, 4]),
            stack(1, 0, 100, false, 17, Some("0x0C40000000000001")),
            stack(2, 1, 7, true, 31, None),
            node(
                3,
                "STUStatescriptStateLogicalButton",
                Some(2),
                json!({"m_logicalButton":{"$":"STUConfigVarLogicalButton","m_logicalButton":20}}),
            ),
            node(
                4,
                "STUStatescriptStateAbility",
                Some(3),
                json!({"m_A5F1A77B":{"$":"STUConfigVarLogicalButton","m_logicalButton":35}}),
            ),
        ],
        json!([1, 2, 3, 4]),
        json!([0]),
        json!([1, 3, 4]),
        json!([[100, 0, 1]]),
    );
    let mut other = graph(
        43,
        vec![
            entry(&[1]),
            stack(1, 0, 7, true, 24, Some("0x0C40000000000002")),
        ],
        json!([1]),
        json!([0]),
        json!([1]),
        json!([]),
    );
    other["header"]["m_statesBitCount"] = json!(6);
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":[main,other], "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap();
    let snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::from([(7, Value::Integer(4))]),
        vec![root, Root::new(2, 43)],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    assert_eq!(snapshot.variables[&7], Value::Integer(4));
    assert_eq!(owner.variables.values[&7], Value::Integer(31));
    assert_eq!(
        owner.instances[&1].variables.values[&100],
        Value::Integer(17)
    );
    assert_eq!(owner.variables.bindings[&7].len(), 2);
    assert!(observer.variables.bindings.is_empty());
    assert!(observer.instances[&1].variables.bindings.is_empty());
    // Python body_full_frame, remote_full_frame, then owner_delta at CmFD 1001.
    check(
        &encoder.full(&owner).unwrap(),
        111,
        "081500102b00003a900f004a0400",
    );
    check(
        &encoder.full(&observer).unwrap(),
        126,
        "081500102b00003a900f004a047f000c",
    );
    let mut stream = BodyStream::new(ENTITY);
    let full = encoder.queue_full(&mut stream, &owner).unwrap();
    stream.create_arrived();
    assert!(stream.arrived(full.last).advanced);
    let (_, baseline) = stream.base().unwrap();
    assert!(baseline.variables.bindings.is_empty());
    assert!(baseline.instances[&1].variables.bindings.is_empty());
    let kind = DeltaKind::InitialOwner(1001);
    let delta = encoder
        .differences(baseline, &owner, &BTreeSet::new(), &BTreeSet::new())
        .unwrap();
    assert_eq!(delta.variables.values[&7], Value::Integer(31));
    assert_eq!(
        delta.instances[&1].variables.values[&100],
        Value::Integer(17)
    );
    check(
        &encoder.delta_frame(&owner, &delta, kind, 16).unwrap(),
        222,
        "2a7d40af40003a908f89004e0189008012116000f0db11009b981800",
    );
    let mut unbound = owner.clone();
    unbound.variables.bindings.clear();
    unbound
        .instances
        .get_mut(&1)
        .unwrap()
        .variables
        .bindings
        .clear();
    let delta = encoder
        .differences(&owner, &unbound, &BTreeSet::new(), &BTreeSet::new())
        .unwrap();
    assert_eq!(delta.variables.values[&7], Value::Integer(31));
    assert_eq!(
        delta.instances[&1].variables.values[&100],
        Value::Integer(17)
    );
    assert!(delta.variables.bindings.is_empty());
    assert!(delta.instances[&1].variables.bindings.is_empty());
}

fn after_body_change(mut snapshot: ProjectedSnapshot) -> ProjectedSnapshot {
    snapshot.variables.values.swap_remove(&55);
    let root = snapshot.instances.get_mut(&1).unwrap();
    root.variables.values.insert(100, Value::Integer(99));
    root.variables.values.swap_remove(&200);
    root.states
        .get_mut(&0)
        .unwrap()
        .payload
        .insert("current".into(), Value::Bool(false));
    root.states.remove(&1);
    root.states.remove(&2);
    snapshot.instances.remove(&2);
    snapshot
}

#[test]
fn ability_cooldown_and_push_binding_match_python_bytes() {
    use crate::script_expr::StackPriority;
    use crate::script_init::AbilityCooldown;

    let definition = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]]}}),
            ),
            node(
                1,
                "STUStatescriptStateAbility",
                Some(0),
                json!({"m_A5F1A77B":{"$":"STUConfigVarLogicalButton","m_logicalButton":35}}),
            ),
        ],
        json!([1]),
        json!([0]),
        json!([1]),
        json!([]),
    );
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1,"graphs":[definition],"bodies":{},"missing":[]
        }))
        .unwrap(),
    )
    .unwrap();
    let mut snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::from([(7, Value::Bool(false))]),
        vec![Root::new(1, 42)],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let state = &mut snapshot.instances[&1].states[&0];
    state.kind = StateKind::Ability { flags: 3 };
    state.ability_cooldown = Some(AbilityCooldown {
        current: 1.25,
        rate: 0.5,
        last_ms: 15_990,
    });
    snapshot.resolved_bindings.insert((0, 7), Value::Bool(true));
    snapshot.bindings.insert(
        (0, 7),
        vec![StackBinding {
            instance: 1,
            raw_state: 0,
            slot: 1,
            priority: StackPriority {
                level: 20,
                above_equals: false,
            },
        }],
    );
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    let mut stream = BodyStream::new(ENTITY);
    stream.create_arrived();
    let full = encoder.queue_full(&mut stream, &owner).unwrap();
    stream.arrived(full.last);
    let initial = encoder
        .queue_delta(&mut stream, &owner, DeltaKind::InitialOwner(1001), 16)
        .unwrap()
        .unwrap();
    // Python owner_delta and remote_full_frame; the observer has neither
    // a pushed binding nor the owner's cooldown timestamp.
    check(
        initial.update.chunk.as_ref().unwrap(),
        245,
        "04000010add207c00a4007300928602800e0010100407f020000fc50490c00",
    );
    check(
        &encoder.full(&observer).unwrap(),
        146,
        "081500001d40003c200000e84f0000801f0000",
    );

    snapshot.bindings[&(0, 7)][0].slot = 0;
    assert!(matches!(
        encoder.project(&snapshot, View::Owner),
        Err(SnapshotError::InvalidBinding {
            owner: 0,
            variable: 7,
            ..
        })
    ));
}

#[test]
fn delayed_switch_projects_reference_bits_and_owner_timer() {
    use crate::script_init::PendingTimer;

    let mut delayed = node(
        1,
        "STU_B591EBFE",
        Some(0),
        json!({"m_BD83C8E6": {"$": "STUConfigVarBool", "m_value": false}}),
    );
    delayed["remote"] = json!(0);
    let graph = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug": {"$": "Plug", "links": [[1, "m_beginPlug"]]}}),
            ),
            delayed,
        ],
        json!([1]),
        json!([0]),
        json!([1]),
        json!([]),
    );
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version": 1, "graphs": [graph], "bodies": {}, "missing": []
        }))
        .unwrap(),
    )
    .unwrap();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let codec = codecs.codec("STU_B591EBFE").unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 42)],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();

    // The authorized 104319 layout gives identical state bits in owner and
    // observer records. Only the owner receives the pending branch timer.
    for (current, undecided, width, byte) in [
        (false, false, 2, 0),
        (false, true, 2, 2),
        (true, false, 1, 1),
    ] {
        snapshot.instances[&1].states[&0].kind = StateKind::DelayedSwitch { current, undecided };
        snapshot.pending_timers.clear();
        if undecided {
            snapshot.pending_timers.insert(
                1,
                vec![PendingTimer {
                    due_ms: 16_064,
                    raw_state: 0,
                    finish: false,
                    parameter: 1,
                }],
            );
        }
        let owner = encoder.project(&snapshot, View::Owner).unwrap();
        let observer = encoder.project(&snapshot, View::Observer).unwrap();
        let owner = &owner.instances[&1];
        let observer = &observer.instances[&1];
        assert_eq!(owner.events.len(), usize::from(undecided));
        if undecided {
            assert_eq!(owner.events[0].parameter, 1);
            assert_eq!(owner.events[0].due_ms, 16_064);
        }
        assert!(observer.events.is_empty());
        for (state, mode) in [
            (
                &owner.states[&0],
                PayloadMode::Owner {
                    full: false,
                    frame_time: 16_000,
                    frame_ms: 16,
                },
            ),
            (&observer.states[&0], PayloadMode::Remote { full: false }),
            (&observer.states[&0], PayloadMode::Remote { full: true }),
        ] {
            let bits = codec.payload(&state.payload, mode).unwrap();
            assert_eq!(
                (bits.bit_len(), bits.as_bytes()),
                (width, [byte].as_slice())
            );
        }
    }
}

#[test]
fn cinematic_boundary_projects_on_enter_and_release_for_relocated_state() {
    use crate::script_init::Component;
    use std::sync::Arc;

    const CLASS: &str = "STU_2F07848F";
    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-cinematic-boundary-v1.json"
    ))
    .unwrap();
    let input = &contract["input"];
    let graph_index = u32::try_from(input["graph"].as_u64().unwrap()).unwrap();
    let release_frame = u32::try_from(input["release_frame"].as_u64().unwrap()).unwrap();
    let mut registry: Json = serde_json::from_str(CODECS).unwrap();
    registry["classes"]["2F07848F"] = json!({
        "name": CLASS, "codec": "value_slot", "stec": true, "status": "VERIFIED",
        "layout": [{"op": "bits", "f": "which", "n": 1},
                   {"op": "bits", "f": "value", "n": 32}]
    });
    let codecs = CodecTable::from_json(&registry.to_string()).unwrap();
    let codec = codecs.codec(CLASS).unwrap();

    for case in input["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let mut cinematic = node(
            1,
            CLASS,
            Some(0),
            json!({
                "m_17D12F6B": case["delay"],
                "m_D8989B37": {"$": "STUConfigVarInt", "m_value": 9},
                "m_89C63AB3": {"$": "STUConfigVarInt", "m_value": 5},
                "m_E75DF5AC": case["scale"],
            }),
        );
        let remote = if case["remote"] == true {
            cinematic["remote"] = json!(0);
            json!([1])
        } else {
            json!([])
        };
        let fixture = graph(
            graph_index,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug": {"$": "Plug", "links": [[1, "m_beginPlug"]]}}),
                ),
                cinematic,
            ],
            json!([1]),
            json!([0]),
            remote,
            json!([]),
        );
        let graphs = Arc::new(
            GraphStore::from_json(
                &serde_json::to_vec(&json!({
                    "version": 1, "graphs": [fixture], "bodies": {}, "missing": []
                }))
                .unwrap(),
            )
            .unwrap(),
        );
        let mut world = EmptyWorld;
        let mut component = Component::start(
            Arc::clone(&graphs),
            ENTITY,
            Variables::new(),
            vec![Root::new(1, graph_index)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let Some(boundaries) = case["boundaries"].as_array() else {
            let snapshot = component.snapshot().unwrap();
            assert!(
                matches!(snapshot.diagnostics.as_slice(),
                         [Diagnostic { issue: Issue::UnsupportedState(class), .. }] if class == CLASS),
                "{name}: {:?}",
                snapshot.diagnostics
            );
            assert!(!snapshot.instances[&1].states.contains_key(&0), "{name}");
            continue;
        };
        let encoder = SnapshotEncoder::new(&graphs, &codecs);
        for (active, expected) in [true, false].into_iter().zip(boundaries) {
            let boundary = expected.as_u64().unwrap();
            if !active {
                component.advance_frame(release_frame, &mut world).unwrap();
                component.debug_end_state(1, 0, &mut world).unwrap();
            }
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.diagnostics.is_empty(),
                "{name}: {:?}",
                snapshot.diagnostics
            );
            for (view, mode) in [
                (
                    View::Owner,
                    PayloadMode::Owner {
                        full: true,
                        frame_time: 0,
                        frame_ms: 16,
                    },
                ),
                (View::Observer, PayloadMode::Remote { full: true }),
            ] {
                let projected = encoder.project(snapshot, view).unwrap();
                let state = &projected.instances[&1].states[&0];
                assert_eq!(state.active, active, "{name}");
                let bits = codec.payload(&state.payload, mode).unwrap();
                assert_eq!(bits.bit_len(), 33, "{name}");
                let mut reader = crate::bits::BitReader::new(bits.as_bytes());
                assert_eq!(reader.bit().unwrap(), active, "{name}");
                assert_eq!(reader.bits(32).unwrap(), boundary, "{name}");
            }
        }
    }
}

#[test]
fn movement_sample_uses_the_owner_flags_codec() {
    struct Sample;
    impl crate::script_init::World for Sample {
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }

        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }

        fn movement_sample(&self, _: Site) -> Option<MovementSample> {
            Some(MovementSample {
                flags: 0x842,
                air_ticks: 1,
                pose_frame: None,
            })
        }
    }

    let fixture = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({
                    "m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]]}
                }),
            ),
            node(1, "STU_96F1DBCF", Some(0), json!({})),
        ],
        json!([1]),
        json!([0]),
        json!([]),
        json!([]),
    );
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({"version":1,"graphs":[fixture],"bodies":{},"missing":[]}))
            .unwrap(),
    )
    .unwrap();
    let snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 42)],
        &mut Sample,
        MAX_INIT_WORK,
    )
    .unwrap();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    assert!(observer.instances[&1].states.is_empty());
    let payload = codecs
        .codec("STU_96F1DBCF")
        .unwrap()
        .payload(
            &owner.instances[&1].states[&0].payload,
            PayloadMode::Owner {
                full: true,
                frame_time: 0,
                frame_ms: 16,
            },
        )
        .unwrap();
    assert_eq!(
        (payload.bit_len(), payload.as_bytes()),
        (14, [0xc2, 0x10].as_slice())
    );
}

#[test]
fn initial_owner_and_corrections_match_python_bytes_in_both_views() {
    let (graphs, snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    let mut stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut stream, &owner).unwrap();
    stream.create_arrived();
    stream.arrived(baseline.last);
    let initial = encoder
        .queue_delta(&mut stream, &owner, DeltaKind::InitialOwner(1000), 16)
        .unwrap()
        .unwrap();
    check(
        initial.update.chunk.as_ref().unwrap(),
        127,
        "040000b0a5d007e80800f02726313100",
    );
    stream.sent(initial.last);
    stream.arrived(initial.last);
    let owner = after_body_change(owner);
    let correction = encoder
        .queue_delta(&mut stream, &owner, DeltaKind::Correction(1016), 16)
        .unwrap()
        .unwrap();
    check(
        correction.update.chunk.as_ref().unwrap(),
        181,
        "06000010e9f007c00944d0cd0f80903121ff00b8480c00",
    );
    assert!(stream.touched().contains(&ChangeKey::Instance(2)));
    assert!(stream.touched().contains(&ChangeKey::Variable(1, 200)));

    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    let mut stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut stream, &observer).unwrap();
    stream.create_arrived();
    stream.arrived(baseline.last);
    let kind = DeltaKind::Observer { resend: false };
    assert!(
        encoder
            .queue_delta(&mut stream, &observer, kind, 16)
            .unwrap()
            .is_none()
    );
    assert!(matches!(
        encoder.queue_delta(&mut stream, &owner, kind, 16),
        Err(SnapshotError::DifferentStream)
    ));
    let observer = after_body_change(observer);
    let delta = encoder
        .queue_delta(&mut stream, &observer, kind, 16)
        .unwrap()
        .unwrap();
    check(
        delta.update.chunk.as_ref().unwrap(),
        140,
        "04000080864004ddfc00081913f20f003601",
    );
}

#[test]
fn chased_values_and_watch_match_python_owner_and_observer_frames() {
    use crate::script_init::Component;
    use std::sync::Arc;

    let var = |id, entity| json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":entity});
    let vector = json!({"$":"STUConfigVarVec3","m_value":{"X":1.0,"Y":2.0,"Z":3.0}});
    let definition = graph(
        42,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":{"$":"Plug","links":[
                    [1,"m_beginPlug"],[2,"m_beginPlug"],[3,"m_beginPlug"]
                ]}}),
            ),
            node(
                1,
                "STUStatescriptStateChaseVar",
                Some(0),
                json!({"m_out_Var":var(100,false),"m_EB0FC261":0.5,"m_destination":3.0,
                       "m_5BEFF040":0.125}),
            ),
            node(
                2,
                "STUStatescriptStateChaseVar",
                Some(1),
                json!({"m_out_Var":var(7,true),"m_EB0FC261":vector,"m_destination":vector,
                       "m_5BEFF040":70.0}),
            ),
            node(
                3,
                "STU_2F4E2E3F",
                Some(2),
                json!({"m_B7A3CF78":var(7,true)}),
            ),
        ],
        json!([1, 2, 3]),
        json!([0]),
        json!([1, 2, 3]),
        json!([[100, 0, 1]]),
    );
    let graphs = Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version":1,"graphs":[definition],"bodies":{},"missing":[]
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut world = EmptyWorld;
    let mut component = Component::start(
        Arc::clone(&graphs),
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 42)],
        1000,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let owner = encoder
        .project(component.snapshot().unwrap(), View::Owner)
        .unwrap();
    // The same graph run by the Python VM, then written by body_full_frame,
    // owner_delta and remote_full_frame. The two durations use distinct widths.
    check(
        &encoder.full(&owner).unwrap(),
        199,
        "081500001df00100007f000000800000808000880000003f00",
    );
    let mut stream = BodyStream::new(ENTITY);
    stream.create_arrived();
    let baseline = encoder.queue_full(&mut stream, &owner).unwrap();
    stream.arrived(baseline.last);
    component.advance_frame(1001, &mut world).unwrap();
    let snapshot = component.snapshot().unwrap();
    let owner = encoder.project(snapshot, View::Owner).unwrap();
    let delta = encoder
        .queue_delta(&mut stream, &owner, DeltaKind::InitialOwner(1001), 16)
        .unwrap()
        .unwrap();
    check(
        delta.update.chunk.as_ref().unwrap(),
        364,
        "040000e82740a50f54112000620ad7e30faf14aec71f5e00051c0000e00f00000010000010d0a888008002260600",
    );
    let observer = encoder.project(snapshot, View::Observer).unwrap();
    check(
        &encoder.full(&observer).unwrap(),
        388,
        "081500001df00100007f00000080000080800088295c8f3fac14aec71f5e001e0000e00f00000010000010d0a888000003",
    );
}

#[test]
fn a_new_volley_reseeds_ammo_after_an_unacknowledged_stop() {
    const RIFLE: u16 = 11;
    const VOLLEY: u16 = 10;
    const AMMO: u32 = 53;
    let definition = graph(
        0x0254,
        vec![node(
            0,
            "STUStatescriptStateWeaponVolley",
            Some(VOLLEY),
            json!({}),
        )],
        json!([
            null, null, null, null, null, null, null, null, null, null, 0
        ]),
        json!([]),
        json!([0]),
        json!([[AMMO, 0, 1]]),
    );
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":[definition], "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut root = Root::new(RIFLE, 0x0254);
    root.variables.insert(AMMO, Value::Integer(30));
    let mut snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::new(),
        vec![root],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let idle = encoder.project(&snapshot, View::Owner).unwrap();
    let observer_idle = encoder.project(&snapshot, View::Observer).unwrap();
    let (_, fixture) = fixture();
    let mut state = fixture.instances[&1].states[&0].clone();
    let mut volley = |active, activation, start_frame, ammo0, ammo| {
        state.active = active;
        state.activation = activation;
        state.start_ms = u64::from(start_frame) * 16;
        state.kind = StateKind::WeaponVolley {
            volleys: 1,
            offset_ms: 0,
            ammo0,
            ammo_var: Some(Variable {
                scope: Scope::Instance,
                id: AMMO,
            }),
        };
        snapshot.instances[&RIFLE]
            .states
            .insert(VOLLEY, state.clone());
        snapshot
            .resolved_bindings
            .insert((RIFLE, AMMO), Value::Integer(ammo));
        encoder.project(&snapshot, View::Owner).unwrap()
    };
    let first = volley(true, 1, 1031_u32, 30, 29);
    let stopped = volley(false, 1, 1031_u32, 30, 29);
    let ended = volley(false, 3, 1050_u32, 28, 27);
    let second = volley(true, 2, 1042_u32, 29, 28);
    let observer_second = encoder.project(&snapshot, View::Observer).unwrap();
    let mut stream = BodyStream::new(ENTITY);
    stream.create_arrived();
    let baseline = encoder.queue_full(&mut stream, &idle).unwrap();
    stream.arrived(baseline.last);
    let initial = encoder
        .queue_delta(&mut stream, &idle, DeltaKind::InitialOwner(1001), 16)
        .unwrap()
        .unwrap();
    stream.sent(initial.last);
    stream.arrived(initial.last);

    // Python owner_delta and chunk vectors from the same minimal 0254 layout.
    let intro = ChangeKey::VolleyIntroduction(RIFLE, VOLLEY);
    let key = ChangeKey::Variable(RIFLE, AMMO);
    for (frame, state, bits, bytes, ammo, introduced, dropped) in [
        (
            1031,
            &first,
            137,
            "06000050e60e08105980e2811c0082ec0000",
            30,
            true,
            false,
        ),
        (
            1032,
            &stopped,
            137,
            "08000050e61008105980d2810c0882ec0000",
            29,
            false,
            true,
        ),
        (
            1042,
            &second,
            137,
            "08000450e62408105980d2811c0084ec0000",
            29,
            true,
            true,
        ),
        (
            1043,
            &second,
            137,
            "08000850e62608105980d2811c0884ec0000",
            29,
            true,
            false,
        ),
        (
            1044,
            &second,
            114,
            "0e0000e0e42808b45880c201d90100",
            28,
            false,
            false,
        ),
        (
            1050,
            &ended,
            137,
            "10000050e63408105980b2810c0086ec0000",
            27,
            true,
            false,
        ),
    ] {
        let chunk = encoder
            .queue_delta(&mut stream, state, DeltaKind::Correction(frame), 16)
            .unwrap()
            .unwrap();
        check(chunk.update.chunk.as_ref().unwrap(), bits, bytes);
        let sent = stream.sent(chunk.last).unwrap();
        assert_eq!(sent.changes.contains(&intro), introduced);
        assert!(sent.changes.contains(&key));
        assert_eq!(
            sent.snapshot.instances[&RIFLE].variables.values[&AMMO],
            Value::Integer(ammo)
        );
        assert!(stream.sent(chunk.last).is_none());
        if dropped {
            let lost = stream.lost(chunk.last);
            assert!(!lost.retry);
            assert_eq!(lost.owner_report, Some(false));
        } else {
            assert!(stream.arrived(chunk.last).advanced);
        }
        assert!(!stream.touched().contains(&intro));
    }

    let mut stream = BodyStream::new(ENTITY);
    stream.create_arrived();
    let baseline = encoder.queue_full(&mut stream, &observer_idle).unwrap();
    stream.arrived(baseline.last);
    let delta = encoder
        .queue_delta(
            &mut stream,
            &observer_second,
            DeltaKind::Observer { resend: false },
            16,
        )
        .unwrap()
        .unwrap();
    check(
        delta.update.chunk.as_ref().unwrap(),
        78,
        "040000a08205281c9c00",
    );
}

#[test]
fn observer_delta_removes_an_instance_introduced_by_an_unacknowledged_chunk() {
    use super::identity_test_reader::IdentityReader;
    let (graphs, snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    let mut stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut stream, &observer).unwrap();
    let mut reader = IdentityReader::default();
    reader
        .apply(baseline.update.chunk.as_ref().unwrap(), true, false)
        .unwrap();
    stream.create_arrived();
    stream.arrived(baseline.last);
    let mut temporary = observer.clone();
    temporary
        .instances
        .insert(3, temporary.instances[&2].clone());
    let kind = DeltaKind::Observer { resend: false };
    let created = encoder
        .queue_delta(&mut stream, &temporary, kind, 16)
        .unwrap()
        .unwrap();
    check(
        created.update.chunk.as_ref().unwrap(),
        92,
        "040000808371058002040000",
    );
    stream.sent(created.last);
    stream.lost(created.last);
    let removed = encoder
        .queue_delta(&mut stream, &observer, kind, 16)
        .unwrap()
        .unwrap();
    check(removed.update.chunk.as_ref().unwrap(), 52, "04000400810900");
    reader
        .apply(removed.update.chunk.as_ref().unwrap(), false, false)
        .unwrap();
    assert!(reader.was_removed(3));
    reader.verify(&snapshot, &graphs);
    stream.arrived(removed.last);
    assert!(
        encoder
            .queue_delta(&mut stream, &observer, kind, 16)
            .unwrap()
            .is_none()
    );
}

#[test]
fn wire_identity_accepts_cumulative_descriptor_repetition_and_rejects_conflict() {
    use super::identity_test_reader::{IdentityError, IdentityReader};
    let (graphs, snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    let mut stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut stream, &observer).unwrap();
    stream.create_arrived();
    let mut reader = IdentityReader::default();
    reader
        .apply(baseline.update.chunk.as_ref().unwrap(), true, false)
        .unwrap();
    let mut fresh_after_baseline = reader.clone();
    stream.arrived(baseline.last);
    let mut with_child = observer.clone();
    with_child
        .instances
        .insert(3, with_child.instances[&2].clone());
    let kind = DeltaKind::Observer { resend: false };
    let first = encoder
        .queue_delta(&mut stream, &with_child, kind, 16)
        .unwrap()
        .unwrap();
    stream.sent(first.last);
    // The packet arrives but its acknowledgement is lost.
    reader
        .apply(first.update.chunk.as_ref().unwrap(), false, false)
        .unwrap();
    assert!(!stream.lost(first.last).retry);
    let repeated = encoder
        .queue_delta(&mut stream, &with_child, kind, 16)
        .unwrap()
        .unwrap();
    reader
        .apply(repeated.update.chunk.as_ref().unwrap(), false, false)
        .unwrap();
    fresh_after_baseline
        .apply(repeated.update.chunk.as_ref().unwrap(), false, false)
        .unwrap();
    let mut expected = snapshot.clone();
    expected.instances.insert(3, expected.instances[&2].clone());
    reader.verify(&expected, &graphs);
    fresh_after_baseline.verify(&expected, &graphs);

    let mut conflicting = with_child.clone();
    conflicting.instances.get_mut(&3).unwrap().graph = 42;
    let conflict = encoder
        .queue_delta(&mut stream, &conflicting, kind, 16)
        .unwrap()
        .unwrap();
    assert_eq!(
        reader.apply(conflict.update.chunk.as_ref().unwrap(), false, false),
        Err(IdentityError::ConflictingDescriptor(3))
    );
}

#[test]
fn wire_identity_rejects_gapped_delta_before_mutating_and_accepts_covering_delta() {
    use super::identity_test_reader::{IdentityError, IdentityReader};
    let (graphs, snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    let kind = DeltaKind::Observer { resend: false };

    let mut gapped_stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut gapped_stream, &observer).unwrap();
    gapped_stream.create_arrived();
    gapped_stream.arrived(baseline.last);

    let mut reader = IdentityReader::default();
    reader
        .apply(baseline.update.chunk.as_ref().unwrap(), true, false)
        .unwrap();
    let baseline_state = reader.clone();

    let mut with_three = observer.clone();
    with_three
        .instances
        .insert(3, with_three.instances[&2].clone());
    let first = encoder
        .queue_delta(&mut gapped_stream, &with_three, kind, 16)
        .unwrap()
        .unwrap();
    gapped_stream.arrived(first.last);

    let mut with_four = with_three.clone();
    with_four
        .instances
        .insert(4, with_four.instances[&2].clone());
    let gapped = encoder
        .queue_delta(&mut gapped_stream, &with_four, kind, 16)
        .unwrap()
        .unwrap();
    assert_eq!(
        reader.apply(gapped.update.chunk.as_ref().unwrap(), false, false),
        Err(IdentityError::OutOfSequence {
            first: 3,
            applied: 1,
        })
    );
    assert_eq!(reader, baseline_state);

    let mut covering_stream = BodyStream::new(ENTITY);
    let covering_baseline = encoder.queue_full(&mut covering_stream, &observer).unwrap();
    covering_stream.create_arrived();
    covering_stream.arrived(covering_baseline.last);
    let lost_first = encoder
        .queue_delta(&mut covering_stream, &with_three, kind, 16)
        .unwrap()
        .unwrap();
    covering_stream.sent(lost_first.last);
    covering_stream.lost(lost_first.last);
    let covering = encoder
        .queue_delta(&mut covering_stream, &with_four, kind, 16)
        .unwrap()
        .unwrap();
    reader
        .apply(covering.update.chunk.as_ref().unwrap(), false, false)
        .unwrap();

    let mut expected = snapshot.clone();
    expected.instances.insert(3, expected.instances[&2].clone());
    expected.instances.insert(4, expected.instances[&2].clone());
    reader.verify(&expected, &graphs);
}

#[test]
fn owner_timer_replacement_preserves_signed_time_steps_and_fifo_ties() {
    let (graphs, snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut owner = encoder.project(&snapshot, View::Owner).unwrap();
    owner.instances.get_mut(&1).unwrap().states.clear();
    let mut stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut stream, &owner).unwrap();
    stream.create_arrived();
    stream.arrived(baseline.last);
    owner.instances.get_mut(&1).unwrap().events = vec![
        ProjectedEvent {
            due_ms: 16000,
            raw_state: 0,
            finish: false,
            parameter: 1,
        },
        ProjectedEvent {
            due_ms: 16004,
            raw_state: 1,
            finish: false,
            parameter: 0,
        },
        ProjectedEvent {
            due_ms: 16004,
            raw_state: 2,
            finish: true,
            parameter: 1,
        },
        ProjectedEvent {
            due_ms: 15992,
            raw_state: 3,
            finish: false,
            parameter: 7,
        },
    ];
    let delta = encoder
        .queue_delta(&mut stream, &owner, DeltaKind::InitialOwner(1000), 16)
        .unwrap()
        .unwrap();
    check(
        delta.update.chunk.as_ref().unwrap(),
        163,
        "040000f0a7d007780189b801022084860894130300",
    );
}

#[test]
fn larger_masks_preserve_group_presence_before_slot_bits() {
    let mut bits = BitWriter::new();
    write_mask(&mut bits, 22, &BTreeSet::from([0, 8, 17]));
    check(&bits, 25, "0f081000");
}

#[test]
fn later_snapshots_keep_initial_entity_scope_exclusions_after_graph_removal() {
    let (graphs, mut snapshot) = fixture();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let initial = encoder.project(&snapshot, View::Observer).unwrap();
    snapshot.instances.clear();
    let next = encoder.project_next(&snapshot, &initial).unwrap();
    assert!(!next.variables.values.contains_key(&420));
    assert!(!next.variables.values.contains_key(&OWNER_HEALTH));
    assert!(
        encoder
            .project(&snapshot, View::Observer)
            .unwrap()
            .variables
            .values
            .contains_key(&420)
    );
}

#[test]
fn initialized_waits_supply_owner_timers_and_remote_frame_targets() {
    let timed = graph(
        44,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({
                    "m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"],[2,"m_beginPlug"]],
                        "m_FF3DAF1E":[]}
                }),
            ),
            node(
                1,
                "STUStatescriptStateWait",
                Some(0),
                json!({
                    "m_timeout":{"$":"STUConfigVarFloat","m_value":1.0}
                }),
            ),
            node(
                2,
                "STU_D74D4F47",
                Some(1),
                json!({
                    "m_3016B9A1":{"$":"STUConfigVarInt","m_value":3}
                }),
            ),
        ],
        json!([1, 2]),
        json!([0]),
        json!([1, 2]),
        json!([]),
    );
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":[timed], "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap();
    let snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 44)],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert_eq!(snapshot.pending_timers[&1].len(), 1);
    assert_eq!(snapshot.pending_timers[&1][0].due_ms, 1000);
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    let observer = encoder.project(&snapshot, View::Observer).unwrap();
    assert!(observer.instances[&1].events.is_empty());
    check(&encoder.full(&observer).unwrap(), 72, "08160000fc00000000");
    let mut stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut stream, &owner).unwrap();
    stream.create_arrived();
    stream.arrived(baseline.last);
    assert!(stream.base().unwrap().1.instances[&1].events.is_empty());
    let update = encoder
        .queue_delta(&mut stream, &owner, DeltaKind::InitialOwner(1), 16)
        .unwrap()
        .unwrap();
    check(
        update.update.chunk.as_ref().unwrap(),
        151,
        "040000302701b604801f0000004800863d3000",
    );
}

#[test]
fn tracked_entity_children_and_descendants_stay_out_of_both_wire_views() {
    use std::sync::Arc;

    use crate::script_init::Component;

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-target-children-v1.json"
    ))
    .unwrap();
    let mut bundle = contract["input"]["bundle"].clone();
    let child = &mut bundle["graphs"][1];
    child["items"] = json!(5);
    child["states"].as_array_mut().unwrap().push(json!(4));
    child["nodes"][0]["fields"]["m_outPlug"]["links"]
        .as_array_mut()
        .unwrap()
        .push(json!([4, "m_beginPlug"]));
    child["nodes"].as_array_mut().unwrap().push(node(
        4,
        "STU_37D754C8",
        Some(1),
        json!({"m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{"m_graph":"0x0580000000000075"}}}}),
    ));
    bundle["graphs"].as_array_mut().unwrap().push(graph(
        117,
        Vec::new(),
        json!([]),
        json!([]),
        json!([]),
        json!([]),
    ));
    let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut component = Component::start(
        Arc::clone(&graphs),
        ENTITY,
        Variables::from([(1, Value::Array(vec![Value::Entity(19)]))]),
        vec![Root::listener(1, 115, None), Root::listener(2, 116, None)],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    component.begin_state(1, 0, &mut EmptyWorld).unwrap();
    let before = component.snapshot().unwrap().clone();
    assert!(!before.instances[&2].is_server_only());
    component.advance_frame(1, &mut EmptyWorld).unwrap();
    let mut after = component.snapshot().unwrap().clone();
    assert!(after.initialization_complete(), "{:?}", after.diagnostics);

    let (&child, instance) = after
        .instances
        .iter()
        .find(|(id, instance)| **id != 2 && instance.graph == 116)
        .unwrap();
    assert!(instance.is_server_only());
    assert!(instance.parent.is_none());
    assert_eq!(instance.variables[&7], Value::Entity(19));
    assert!(!after.pending_timers[&child].is_empty());
    let descendant = after
        .instances
        .values()
        .find(|instance| instance.graph == 117)
        .unwrap();
    assert!(descendant.is_server_only());
    assert_eq!(descendant.parent, Some((child, 1)));

    let hidden_diagnostic = Diagnostic {
        site: Site {
            entity: ENTITY,
            instance: child,
            graph: 116,
            node: None,
        },
        issue: Issue::UnsupportedField("synthetic private diagnostic"),
        registration_rejected: false,
        server_only: true,
    };
    after.diagnostics.push(hidden_diagnostic.clone());
    for view in [View::Owner, View::Observer] {
        let baseline = encoder.project(&before, view).unwrap();
        let full = encoder.project(&after, view).unwrap();
        let next = encoder.project_next(&after, &baseline).unwrap();
        for projected in [&baseline, &full, &next] {
            assert_eq!(
                projected.instances.keys().copied().collect::<Vec<_>>(),
                [1, 2]
            );
            encoder.full(projected).unwrap();
        }
    }

    // Diagnostics survive child removal, but remain private to the server.
    component.queue_message(1, 49, Variables::new()).unwrap();
    component.run_now(&mut EmptyWorld).unwrap();
    let mut after = component.snapshot().unwrap().clone();
    assert!(!after.instances.contains_key(&child));
    after.diagnostics.push(hidden_diagnostic);
    assert!(encoder.project(&after, View::Owner).is_ok());
}

#[test]
fn authored_server_meter_leaves_both_wire_views_and_public_child_identity_unchanged() {
    use std::sync::Arc;

    use crate::script_init::{Component, PlayScript, player_roots};

    const BODY: u64 = 0x0400_0000_0000_0a01;
    const PUBLIC: u32 = 445;
    const PUBLIC_CHILD: u32 = 446;
    const PRIVATE_CHILD: u32 = 447;
    const METER: u32 = 444;

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-authored-meter-v1.json"
    ))
    .unwrap();
    let mut bundle = contract["input"]["bundle"].clone();
    let body = &mut bundle["bodies"][format!("0x{BODY:016X}")];
    body["server_graphs"] = std::mem::take(&mut body["graphs"]);
    body["graphs"] = json!([{"graph": PUBLIC, "m_75408640": 0}]);

    let child_graph = format!("0x{:016X}", 0x0580_0000_0000_0000 | u64::from(PUBLIC_CHILD));
    bundle["graphs"].as_array_mut().unwrap().extend([
        graph(
            PUBLIC,
            vec![node(
                0,
                "STU_37D754C8",
                Some(0),
                json!({"m_36F7B64C":{"m_36F7B64C":{"m_C974E369":{"m_graph":child_graph}}}}),
            )],
            json!([0]),
            json!([]),
            json!([0]),
            json!([]),
        ),
        graph(
            PUBLIC_CHILD,
            vec![],
            json!([]),
            json!([]),
            json!([]),
            json!([]),
        ),
        graph(
            PRIVATE_CHILD,
            vec![],
            json!([]),
            json!([]),
            json!([]),
            json!([]),
        ),
    ]);
    let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    let start = |roots| {
        Component::start(
            Arc::clone(&graphs),
            ENTITY,
            Variables::from([(55, Value::Integer(-12))]),
            roots,
            0,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap()
    };
    let mut control = start(vec![Root::new(1, PUBLIC)]);
    let mut with_meter = start(player_roots(&graphs.bodies()[&BODY], &graphs).unwrap());
    let control_start = control.snapshot().unwrap().clone();
    let meter_start = with_meter.snapshot().unwrap().clone();
    let (&meter_id, meter) = meter_start
        .instances
        .iter()
        .find(|(_, instance)| instance.graph == METER)
        .unwrap();
    assert!(meter.is_server_only());
    assert_eq!(meter.variables[&71], Value::Float(0.0));
    assert_eq!(meter.variables[&75], Value::Float(12.0));
    assert!(!meter_start.pending_timers[&meter_id].is_empty());

    with_meter
        .receive_play_script(
            Site {
                entity: ENTITY,
                graph: METER,
                instance: meter_id,
                node: None,
            },
            &PlayScript {
                policy: 1,
                overrides: vec![],
                graph: PRIVATE_CHILD,
                source: ENTITY,
                parameters: vec![],
                auto_retire: false,
            },
            &mut EmptyWorld,
        )
        .unwrap();

    for component in [&mut control, &mut with_meter] {
        component.advance_frame(3, &mut EmptyWorld).unwrap();
        component.begin_state(1, 0, &mut EmptyWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(snapshot.instances[&1].graph, PUBLIC);
        assert_eq!(snapshot.instances[&2].graph, PUBLIC_CHILD);
        assert_eq!(snapshot.instances[&2].parent, Some((1, 0)));
    }
    let control_end = control.snapshot().unwrap();
    let meter_end = with_meter.snapshot().unwrap();
    assert_eq!(
        meter_end.instances[&meter_id].variables[&71],
        Value::Float(1.0)
    );
    assert!(
        meter_end
            .instances
            .values()
            .any(|instance| { instance.graph == PRIVATE_CHILD && instance.is_server_only() })
    );

    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    for view in [View::Owner, View::Observer] {
        let encode = |start: &Snapshot, end: &Snapshot| {
            let initial = encoder.project(start, view).unwrap();
            assert_eq!(initial.instances.keys().copied().collect::<Vec<_>>(), [1]);
            let next = encoder.project_next(end, &initial).unwrap();
            assert_eq!(next.instances.keys().copied().collect::<Vec<_>>(), [1, 2]);
            let full = encoder.full(&initial).unwrap();
            let mut stream = BodyStream::new(ENTITY);
            let baseline = encoder.queue_full(&mut stream, &initial).unwrap();
            stream.create_arrived();
            assert!(stream.arrived(baseline.last).advanced);
            let kind = match view {
                View::Owner => DeltaKind::InitialOwner(3),
                View::Observer => DeltaKind::Observer { resend: true },
            };
            let update = encoder
                .queue_delta(&mut stream, &next, kind, 16)
                .unwrap()
                .unwrap()
                .update
                .chunk
                .unwrap();
            (initial, next, full, update)
        };
        let (control_initial, control_next, control_full, control_update) =
            encode(&control_start, control_end);
        let (meter_initial, meter_next, meter_full, meter_update) = encode(&meter_start, meter_end);
        assert_eq!(meter_initial, control_initial);
        assert_eq!(meter_next, control_next);
        for (expected, actual) in [(control_full, meter_full), (control_update, meter_update)] {
            assert_eq!(actual.bit_len(), expected.bit_len(), "{view:?}");
            assert_eq!(actual.as_bytes(), expected.as_bytes(), "{view:?}");
        }
    }
}

#[test]
fn view_tracker_encodes_the_current_owner_target_list() {
    use crate::script_init::{Component, ViewTargetQuery};

    struct Targets(Vec<u32>);
    impl crate::script_init::World for Targets {
        fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
            None
        }

        fn position(&mut self, _: u32) -> Option<[f64; 3]> {
            None
        }

        fn view_targets(&self, _: Site, _: &ViewTargetQuery<'_>) -> Option<Vec<u32>> {
            Some(self.0.clone())
        }
    }

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-view-targets-v1.json"
    ))
    .unwrap();
    let mut tracker = node(1, "STUStatescriptStateTrackTargets", Some(0), json!({}));
    tracker["fields"] = fixture["input"]["field_shape"].clone();
    let index = 607;
    let graphs = std::sync::Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version":1,"bodies":{},"missing":[],
                "graphs":[graph(
                    index,
                    vec![
                        node(0, "STUStatescriptEntry", None,
                             json!({"m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]]}})),
                        tracker,
                    ],
                    json!([1]), json!([0]), json!([]), json!([]),
                )]
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    let mut world = Targets(serde_json::from_value(fixture["input"]["targets"].clone()).unwrap());
    let owner = serde_json::from_value(fixture["input"]["owner"].clone()).unwrap();
    let period: u32 = serde_json::from_value(fixture["expected"]["period_frames"].clone()).unwrap();
    let mut component = Component::start(
        std::sync::Arc::clone(&graphs),
        owner,
        Variables::new(),
        vec![Root::new(1, index)],
        10,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let before = component.snapshot().unwrap().clone();
    world.0.clear();
    component.advance_frame(10 + period, &mut world).unwrap();
    let expected: Vec<(u32, i64)> =
        serde_json::from_value(fixture["expected"]["wire_targets"].clone()).unwrap();
    let expected = Value::Array(
        expected
            .into_iter()
            .map(|(entity, age)| Value::Array(vec![Value::Entity(entity), Value::Integer(age)]))
            .collect(),
    );
    let base = encoder.project(&before, View::Owner).unwrap();
    assert_eq!(
        base.instances[&1].states[&0].payload.get("targets"),
        Some(&expected)
    );
    let next = encoder
        .project_next(component.snapshot().unwrap(), &base)
        .unwrap();
    assert_eq!(
        next.instances[&1].states[&0].payload.get("targets"),
        Some(&Value::Array(Vec::new()))
    );
    assert_ne!(
        encoder.full(&base).unwrap().as_bytes(),
        encoder.full(&next).unwrap().as_bytes()
    );
    let observer = encoder.project(&before, View::Observer).unwrap();
    assert!(observer.instances[&1].states.is_empty());
}

#[test]
fn quick_melee_hit_list_projects_empty_hits_payload() {
    let mut melee = graph(
        0x0043,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({
                    "m_outPlug":{"$":"Plug","links":[[1,"m_inPlug"]],"m_FF3DAF1E":[]}
                }),
            ),
            {
                let mut stat = node(
                    1,
                    "STU_CEBDAF17",
                    None,
                    json!({
                        "m_targets":{"$":"STU_B5A0CAF0","m_EE729DCB":0},
                        "m_stat":"0x0860000000000001",
                        "m_amount":{"$":"STUConfigVarInt","m_value":1},
                        "m_outPlug":{"$":"Plug","links":[[11,"m_beginPlug"]],"m_FF3DAF1E":[]}
                    }),
                );
                stat["fields"]["m_ADEB6E05"] = json!(true);
                stat
            },
            node(
                11,
                "STUStatescriptStateTrackTargets",
                Some(0),
                json!({
                    "m_95FFC3C5":1,
                    "m_8DCA77B6":{"$":"STU_B5A0CAF0"},
                    "m_05BE62DD":{"$":"STUConfigVarVec3","m_value":{"X":0,"Y":1,"Z":1}},
                    "m_D59188D5":{"$":"STU_21A13145","m_entity":{"$":"STU_B5A0CAF0"}},
                    "m_radius":1,
                    "m_8A2905A0":{"$":"STUConfigVarDynamic","m_identifier":30,"m_60DB8F99":0},
                    "m_24A4AB8A":{"$":"STUConfigVarDynamic","m_identifier":31,"m_60DB8F99":0},
                    "m_filter":{"$":"STUConfigVarFilter","m_84065FD1":64,"m_4EA686DB":20,"m_89831602":1},
                    "m_3D667CE3":{"$":"STUConfigVarFilter","m_C1903771":{
                        "$":"STU_20DB8AB5","m_C1903771":"0x06900000000000AA"
                    }},
                    "m_subgraphPlug":{"$":"Plug","links":[[60,"m_beginPlug"]],"m_FF3DAF1E":[1]}
                }),
            ),
            {
                let mut hits = node(
                    60,
                    "STU_77E13026",
                    Some(1),
                    json!({
                        "m_814F8EC3":{"$":"STUConfigVarDynamic","m_identifier":32,"m_60DB8F99":0},
                        "m_3DC6FAAA":{"$":"STUConfigVarDynamic","m_identifier":33,"m_60DB8F99":0},
                    }),
                );
                hits["remote"] = json!(0);
                hits
            },
        ],
        json!([11, 60]),
        json!([0]),
        json!([60]),
        json!([]),
    );
    melee["items"] = json!(61);
    melee["header"]["m_nodesBitCount"] = json!(6);
    let graphs = GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":[melee], "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap();
    struct NoStatsWorld;
    impl crate::script_init::World for NoStatsWorld {
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<crate::script_wire::Value> {
            None
        }

        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }

        fn player_stats_available(&self, _site: Site) -> Option<bool> {
            Some(false)
        }
    }
    let snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 0x0043)],
        &mut NoStatsWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert_eq!(
        snapshot.instances[&1].states[&0].kind,
        StateKind::EmptyTargets
    );
    assert_eq!(snapshot.instances[&1].states[&1].kind, StateKind::Plain);
    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    for (view, expected) in [
        (View::Owner, BTreeSet::from([0, 1])),
        (View::Observer, BTreeSet::from([1])),
    ] {
        let projected = encoder.project(&snapshot, view).unwrap();
        assert_eq!(projected.instance_states(1).unwrap(), expected);
        let frame = encoder.full(&projected).unwrap();
        assert!(frame.bit_len() > 0);
    }
}

#[test]
#[ignore = "requires the prepared 104319 Junkenstein boss intro graphs"]
fn authored_boss_intro_receiver_publishes_one_active_camera() {
    use crate::script_init::Component;
    use std::{path::PathBuf, sync::Arc};

    const BOSS: u32 = 0xc000_1000;
    const BONE: u64 = 0x0880_0000_0000_0010;
    let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut component = Component::start(
        Arc::clone(&graphs),
        ENTITY,
        Variables::new(),
        vec![Root::listener(1, 0x0ca5, Some(11))],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let owner = encoder
        .project(component.snapshot().unwrap(), View::Owner)
        .unwrap();
    let mut stream = BodyStream::new(ENTITY);
    let baseline = encoder.queue_full(&mut stream, &owner).unwrap();
    stream.create_arrived();
    stream.arrived(baseline.last);
    let (_, base) = stream.base().unwrap();
    for _ in 0..2 {
        component
            .queue_entity_message_from(
                BOSS,
                0x0240_0000_0000_06ab,
                Variables::from([(8, Value::Entity(BOSS)), (0x3b6, Value::Asset(BONE))]),
            )
            .unwrap();
        component.run_now(&mut EmptyWorld).unwrap();
        let snapshot = component.snapshot().unwrap();
        let instance = &snapshot.instances[&1];
        assert!(instance.states[&17].active);
        assert_eq!(instance.states[&17].activation, 1);
        assert_eq!(instance.variables[&38], Value::Entity(BOSS));
        assert_eq!(instance.variables[&8695], Value::Asset(BONE));
        let owner = encoder.project_next(snapshot, &owner).unwrap();
        let delta = encoder
            .differences(base, &owner, &BTreeSet::new(), &BTreeSet::new())
            .unwrap();
        encoder
            .delta_frame(&owner, &delta, DeltaKind::InitialOwner(1000), 16)
            .unwrap();
    }
}

#[test]
#[ignore = "requires the separately prepared 104319 graph and codec tables"]
fn actual_shared_ui_graph_baselines_match_python() {
    use sha2::{Digest, Sha256};
    use std::path::{Path, PathBuf};

    let root = std::env::var_os("ORPHEUS_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).to_owned());
    let graphs = GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap();
    let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    for (graph, expected_owner, expected_observer, delta_bits, expected_delta) in [
        (
            0x20e4,
            "8b5be8f21a99210445094b65c6ba62621a489da48170c97ef9062a99127ec0f6",
            "7e3658bf64c37a631561b21b33fff4f68119e038896d153ee84d7f4cb6e29649",
            73,
            "3fee631d2eede1aa07fd6b2505734ac71f1f6c0e015524a62d04419db413a3ee",
        ),
        (
            0x0cb6,
            "6de347e8a7097efa974f5fc61cce965c69998e849c11c11a3c96165dd8408f48",
            "e62bc520a0acf2f9064de7de9b053ba46c3861c4b4c481f606e357560eca6eea",
            76,
            "3a927d71037d9d56bb2e3d0ef1a532f252df632e590875823dca787120dd5153",
        ),
    ] {
        let snapshot = initialize(
            &graphs,
            ENTITY,
            Variables::new(),
            vec![Root::new(1, graph)],
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        for (view, expected_bits, expected) in [
            (View::Owner, 37, expected_owner),
            (View::Observer, 38, expected_observer),
        ] {
            let projection = encoder.project(&snapshot, view).unwrap();
            let frame = encoder.full(&projection).unwrap();
            assert_eq!(frame.bit_len(), expected_bits);
            assert_eq!(format!("{:x}", Sha256::digest(frame.as_bytes())), expected);
        }
        let owner = encoder.project(&snapshot, View::Owner).unwrap();
        let mut stream = BodyStream::new(ENTITY);
        let baseline = encoder.queue_full(&mut stream, &owner).unwrap();
        stream.create_arrived();
        stream.arrived(baseline.last);
        let (_, base) = stream.base().unwrap();
        let delta = encoder
            .differences(base, &owner, &BTreeSet::new(), &BTreeSet::new())
            .unwrap();
        let frame = encoder
            .delta_frame(&owner, &delta, DeltaKind::InitialOwner(1000), 16)
            .unwrap();
        assert_eq!(frame.bit_len(), delta_bits);
        assert_eq!(
            format!("{:x}", Sha256::digest(frame.as_bytes())),
            expected_delta
        );
    }
}

#[test]
fn emote_presentation_projects_activation_and_removal_using_authored_scope() {
    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/wheel-emote-presentation-v1.json"
    ))
    .unwrap();
    check_emote_presentation_projection(&fixture["rust_animation_mode"], false, true);
    let mut resource = fixture["rust_cosmetic_resource"].clone();
    check_emote_presentation_projection(&resource, true, true);
    resource["ready_plug"]["links"] = json!([[1, "m_abortPlug"]]);
    check_emote_presentation_projection(&resource, true, false);
}

fn check_emote_presentation_projection(mode: &Json, observed: bool, supported: bool) {
    use crate::script_init::Component;
    use std::sync::Arc;

    let class = mode["class"].as_str().unwrap();
    let graph_id = u32::try_from(mode["graph"].as_u64().unwrap()).unwrap();
    let fields = if observed {
        json!({
            "m_3DE788AE":mode["ready_plug"],
            "m_18D94B00":{"$":"client_resource_expression"},
        })
    } else {
        json!({"m_4DA577AC":{"$":"STUConfigVarInt","m_value":mode["literal_mode"]}})
    };
    let mut state = node(1, class, Some(0), fields);
    if observed {
        state["remote"] = json!(0);
    }
    let graph = graph(
        graph_id,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]],"m_FF3DAF1E":[]}}),
            ),
            state,
        ],
        json!([1]),
        json!([0]),
        if observed { json!([1]) } else { json!([]) },
        json!([]),
    );
    let graphs = Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version": 1, "graphs": [graph], "bodies": {}, "missing": [],
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    let codecs = CodecTable::from_json(
        &json!({"classes":{(class.trim_start_matches("STU_")):{
            "name":class,"codec":mode["codec"],"stec":false,"status":"VERIFIED","layout":[],
        }}})
        .to_string(),
    )
    .unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let mut component = Component::start(
        Arc::clone(&graphs),
        ENTITY,
        Variables::new(),
        vec![Root::new(1, graph_id)],
        0,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let snapshot = component.snapshot().unwrap();
    if !supported {
        assert!(matches!(
            snapshot.diagnostics.as_slice(),
            [Diagnostic { issue: Issue::UnsupportedState(rejected), .. }] if rejected == class
        ));
        assert!(snapshot.instances[&1].states.is_empty());
        return;
    }
    let owner = encoder.project(snapshot, View::Owner).unwrap();
    let state = &owner.instances[&1].states[&0];
    assert!(state.active && state.payload.is_empty() && state.link.is_none());
    let observer = encoder.project(snapshot, View::Observer).unwrap();
    assert_eq!(
        observer.instances[&1].states.get(&0),
        observed.then_some(state)
    );

    component.debug_end_state(1, 0, &mut EmptyWorld).unwrap();
    for view in [View::Owner, View::Observer] {
        let ended = encoder
            .project(component.snapshot().unwrap(), view)
            .unwrap();
        assert!(ended.instances[&1].states.is_empty());
    }
}

#[test]
fn synthetic_two_instance_registered_state_link_encodes_for_owner_and_observer() {
    let registered_graph = |index,
                            items,
                            state_pos,
                            state_idx,
                            kind,
                            remote_idx: Option<usize>,
                            remote_bits| {
        let mut state_node = node(
            state_pos,
            "STU_9028E035",
            Some(state_idx),
            json!({"m_type": kind}),
        );
        let entry_node = node(
            0,
            "STUStatescriptEntry",
            None,
            json!({
                "m_outPlug": {"$": "Plug", "links": [[state_pos, "m_beginPlug"]], "m_FF3DAF1E": []}
            }),
        );
        let mut states = vec![Json::Null; usize::from(state_idx) + 1];
        states[usize::from(state_idx)] = json!(state_pos);
        let mut remote = Vec::new();
        if let Some(index) = remote_idx {
            state_node["remote"] = json!(index);
            remote.resize(index + 1, Json::Null);
            remote[index] = json!(state_pos);
        }
        let mut built = graph(
            index,
            vec![entry_node, state_node],
            Json::Array(states),
            json!([0]),
            Json::Array(remote),
            json!([]),
        );
        built["items"] = json!(items);
        built["header"]["m_statesBitCount"] = json!(4);
        built["header"]["m_remoteSyncNodesBitCount"] = json!(remote_bits);
        built
    };

    let load_graphs = |observer_slots: bool| {
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version": 1,
                "graphs": [
                    registered_graph(0x0ce0, 39, 38, 10, 0, observer_slots.then_some(0), 1),
                    registered_graph(0x0d03, 2, 1, 1, 1, observer_slots.then_some(2), 3),
                ],
                "bodies": {},
                "missing": []
            }))
            .unwrap(),
        )
        .unwrap()
    };

    let graphs = load_graphs(false);
    let snapshot = initialize(
        &graphs,
        ENTITY,
        Variables::new(),
        vec![Root::new(1, 0x0ce0), Root::new(2, 0x0d03)],
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert!(
        snapshot.diagnostics.is_empty(),
        "{:?}",
        snapshot.diagnostics
    );
    assert!(snapshot.instances[&1].states[&10].active);
    assert_eq!(
        snapshot.instances[&1].states[&10].kind,
        StateKind::Registered { next: Some((2, 1)) }
    );
    assert!(snapshot.instances[&2].states[&1].active);
    assert_eq!(
        snapshot.instances[&2].states[&1].kind,
        StateKind::Registered { next: None }
    );

    let codecs = CodecTable::from_json(CODECS).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = encoder.project(&snapshot, View::Owner).unwrap();
    let native_observer = encoder.project(&snapshot, View::Observer).unwrap();
    assert!(native_observer.instances[&1].states.is_empty());
    assert!(native_observer.instances[&2].states.is_empty());

    // These invented remote slots exercise observer link encoding independently
    // of the native state registration, which requires local state slots.
    let observer_graphs = load_graphs(true);
    let observer_encoder = SnapshotEncoder::new(&observer_graphs, &codecs);
    let observer = observer_encoder.project(&snapshot, View::Observer).unwrap();
    assert_eq!(
        observer.instances[&1].states[&10].link,
        Some(ProjectedStateLink {
            instance: 2,
            index: 2,
            width: 3,
        })
    );
    assert_eq!(observer.instances[&2].states[&1].link, None);
    assert_eq!(
        owner.instances[&1].states[&10].link,
        Some(ProjectedStateLink {
            instance: 2,
            index: 1,
            width: 4,
        })
    );
    assert_eq!(owner.instances[&2].states[&1].link, None);

    let mut obs_stream = BodyStream::new(ENTITY);
    let obs_full = observer_encoder
        .queue_full(&mut obs_stream, &observer)
        .unwrap();
    let obs_bits = obs_full.update.chunk.as_ref().unwrap();
    assert!(obs_bits.bit_len() > 0 && !obs_bits.as_bytes().is_empty());

    let mut own_stream = BodyStream::new(ENTITY);
    let own_full = encoder.queue_full(&mut own_stream, &owner).unwrap();
    own_stream.create_arrived();
    own_stream.arrived(own_full.last);
    let own_delta = encoder
        .queue_delta(&mut own_stream, &owner, DeltaKind::InitialOwner(1), 16)
        .unwrap()
        .unwrap();
    let own_full_bits = own_full.update.chunk.as_ref().unwrap();
    let own_delta_bits = own_delta.update.chunk.as_ref().unwrap();
    assert!(own_full_bits.bit_len() > 0 && !own_full_bits.as_bytes().is_empty());
    assert!(own_delta_bits.bit_len() > 0 && !own_delta_bits.as_bytes().is_empty());

    if let Some(path) = std::env::var_os("ORPHEUS_STATE_LINK_WIRE_PROBE") {
        let chunk = |bits: &BitWriter| json!({"data": bits.as_bytes(), "bits": bits.bit_len()});
        std::fs::write(
            path,
            serde_json::to_vec(&json!({
                "observer_full": chunk(obs_bits),
                "owner_full": chunk(own_full_bits),
                "owner_delta": chunk(own_delta_bits),
            }))
            .unwrap(),
        )
        .unwrap();
    }
}
