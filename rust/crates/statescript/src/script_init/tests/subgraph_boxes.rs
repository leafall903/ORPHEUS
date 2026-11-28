//! State-group (subgraph box) schema and launch semantics.
//!
//! The 104319 extraction records `m_items` entries that are not nodes in the
//! per-graph `containers` map (position -> class tag), and states point back
//! at their group through `m_0B1AA8CA: {"item": N}`. Widowmaker's hook graph
//! 0x005A is the concrete consumer: the grapple trace node references item 94
//! and the launch LogicalButton references item 79. A group routes when a
//! plug link names the box position and the pack carries begin edges on it;
//! a tag-only box keeps the loud missing-node report, because the record
//! carries no routing and inventing edges is not restoration. (The real
//! 104319 export has no group edges for graph 90 at all — pinned by the
//! runtime state_groups tests — so the enriched form is exercised here as a
//! synthetic contract; it is a forward-compatible option, not a pending
//! extraction artifact: her hook relaunch never needs a group edge, the
//! re-begin entry is the ability press path (see the full-kit scenario in
//! `game_replication::tests::owner_walk`). These contracts renumber every
//! position, state slot,
//! variable, instance and container item and drive the same authored chain
//! (Wait finish -> box -> trace -> onEnd -> BooleanSwitch subgraph branches
//! -> MoveTo) through the generic route.

use super::*;
use crate::script_expr::SpatialRayHit;
use crate::script_init::world::{Site, World};

const CAST: &str = "0x069000000000BEEF";
const HARDPOINT: &str = "0x0DC000000000F00D";

/// One complete numbering; every contract runs each shape twice under both.
struct Numbering {
    graph: u32,
    instance: u16,
    entry: u16,
    wait: u16,
    launch_box: u16,
    trace: u16,
    trace_group: u16,
    switch: u16,
    moved: u16,
    miss: u16,
    states: [u16; 4],
    timeout: u32,
    hit: u32,
    point: u32,
    point_now: u32,
    direction: u32,
    normal: u32,
    entity: u32,
    flagged: u32,
    missed: u32,
}

const NUMBERS: [Numbering; 2] = [
    Numbering {
        graph: 9441,
        instance: 3,
        entry: 0,
        wait: 1,
        launch_box: 2,
        trace: 3,
        trace_group: 4,
        switch: 5,
        moved: 6,
        miss: 7,
        states: [1, 0, 2, 3],
        timeout: 300,
        hit: 301,
        point: 302,
        point_now: 303,
        direction: 304,
        normal: 305,
        entity: 306,
        flagged: 307,
        missed: 308,
    },
    Numbering {
        graph: 5077,
        instance: 6,
        entry: 11,
        wait: 4,
        launch_box: 20,
        trace: 9,
        trace_group: 31,
        switch: 14,
        moved: 25,
        miss: 17,
        states: [3, 1, 0, 2],
        timeout: 771,
        hit: 402,
        point: 118,
        point_now: 519,
        direction: 640,
        normal: 73,
        entity: 254,
        flagged: 880,
        missed: 12,
    },
];

struct HookWorld {
    hit: Option<SpatialRayHit>,
}

impl World for HookWorld {
    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
    fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }
    fn body_hardpoint_position(&self, entity: u32, hardpoint: u64) -> Option<[f64; 3]> {
        (entity == 123 && Some(hardpoint) == u64::from_str_radix(&HARDPOINT[2..], 16).ok())
            .then_some([0.0, 1.5, 0.0])
    }
    fn body_eye_position(&self, _entity: u32) -> Option<[f64; 3]> {
        Some([0.0, 1.5, 0.0])
    }
    fn authored_forward(&self, _entity: u32) -> Option<[f64; 3]> {
        Some([0.0, 0.0, 1.0])
    }
    fn owner_collision_category(&self, _site: Site) -> Option<u16> {
        Some(0x08)
    }
    fn profiled_static_ray(
        &self,
        _site: Site,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _max_distance: f64,
        _category: u16,
    ) -> Option<Option<SpatialRayHit>> {
        Some(self.hit)
    }
}

/// The authored-shaped fragment. `box_links` serializes the launch box with
/// its begin edge; when false it uses the current reference-extractor form
/// (class tag only, launch dropped). `orphan_group` points the trace's group
/// reference at an undeclared item.
fn fragment(number: &Numbering, box_links: bool, orphan_group: bool) -> Json {
    let dynamic = |id: u32| {
        json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":false,
               "m_EE729DCB":0})
    };
    let filter = json!({
        "$":"STUConfigVarFilter","m_93FA1642":null,"m_84065FD1":1073741823,
        "m_1E14CCA8":0,"m_4EA686DB":0,"m_27CF71D5":0,"m_66747E7D":0,"m_89831602":0,
        "m_condition":null,"m_9C7BD8B8":null,"m_B486603E":null,"m_8B2A4DF3":null,
        "m_F61726FE":null,"m_B40362FC":null,"m_EE729DCB":0,
        "m_C1903771":{"$":"STU_20DB8AB5","m_resourceKey":null,"m_EE729DCB":0,
                      "m_C1903771":CAST}
    });
    let begin =
        |target: u16| json!({"$":"STUStatescriptOutputPlug","links":[[target,"m_beginPlug"]]});
    let wait = node(
        number.wait,
        "STUStatescriptStateWait",
        Some(number.states[0]),
        json!({
            "m_timeout": dynamic(number.timeout),
            "m_onFinishedPlug": begin(number.launch_box),
        }),
    );
    let mut trace = node(
        number.trace,
        "STU_4BBC5D09",
        Some(number.states[1]),
        json!({
            "m_2BBEEAB8": true,
            "m_0B1AA8CA": {"item": if orphan_group { 63 } else { number.trace_group }},
            "m_66B658A5": filter,
            "m_F745334E": {"$":"STUConfigVarHardPoint","m_resourceKey":null,"m_EE729DCB":0,
                           "m_hardPoint":HARDPOINT},
            "m_2AFC083F": {"$":"STUConfigVarInt","m_value":8,"m_EE729DCB":0},
            "m_speed": {"$":"STUConfigVarInt","m_value":2000,"m_EE729DCB":0},
            "m_9E1A7C58": dynamic(number.hit),
            "m_5ED803CF": dynamic(number.point),
            "m_F2F8606D": dynamic(number.point_now),
            "m_64801077": dynamic(number.direction),
            "m_D7A2A03B": dynamic(number.normal),
            "m_1FFD7AC0": dynamic(number.entity),
            "m_9169D109": dynamic(number.flagged),
            "m_onEndPlug": begin(number.switch),
        }),
    );
    trace["server_only"] = json!(false);
    trace["remote"] = json!(0);
    let moved = node(
        number.moved,
        "STUStatescriptStateMoveTo",
        Some(number.states[3]),
        json!({
            "m_2BBEEAB8": true, "m_B060F688": 0,
            "m_865F3D01": {"$":"STUConfigVarVec3","m_value":{"X":4,"Y":0,"Z":0}},
            "m_speed": {"$":"STUConfigVarInt","m_value":10,"m_EE729DCB":0},
        }),
    );
    let mut state_slots: Vec<Option<u16>> = Vec::new();
    for (raw, position) in
        number
            .states
            .iter()
            .copied()
            .zip([number.wait, number.trace, number.switch, number.moved])
    {
        let raw = usize::from(raw);
        if state_slots.len() <= raw {
            state_slots.resize(raw + 1, None);
        }
        state_slots[raw] = Some(position);
    }
    let mut fixture = graph(
        number.graph,
        vec![
            node(
                number.entry,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug": {"$":"STUStatescriptOutputPlug","links":[]}}),
            ),
            wait,
            trace,
            node(
                number.switch,
                "STUStatescriptStateBooleanSwitch",
                Some(number.states[2]),
                json!({
                    "m_condition": dynamic(number.hit),
                    "m_trueSubgraphPlug": {
                        "$":"STU_904BFCEC","m_FF3DAF1E":[1],
                        "links":[[number.moved,"m_beginPlug"]]
                    },
                    "m_falseSubgraphPlug": {
                        "$":"STU_904BFCEC","m_FF3DAF1E":[2],
                        "links":[[number.miss,"m_inPlug"]]
                    },
                }),
            ),
            moved,
            node(
                number.miss,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var": var(number.missed, true), "m_value": true}),
            ),
        ],
        serde_json::to_value(&state_slots).unwrap(),
        json!([number.entry]),
    );
    fixture["items"] = json!(
        [
            number.entry,
            number.wait,
            number.launch_box,
            number.trace,
            number.trace_group,
            number.switch,
            number.moved,
            number.miss,
        ]
        .into_iter()
        .max()
        .unwrap_or_default()
            + 1
    );
    let launch_box = if box_links {
        json!({"class":"STUStatescriptSubGraph",
               "links":[[number.trace,"m_beginPlug"]]})
    } else {
        json!("STUStatescriptSubGraph")
    };
    let mut containers = json!({
        format!("{}", number.launch_box): launch_box,
        format!("{}", number.trace_group): "STUStatescriptSubGraph",
    });
    if orphan_group {
        containers
            .as_object_mut()
            .unwrap()
            .remove(&format!("{}", number.trace_group));
    }
    fixture["containers"] = containers;
    fixture
}

fn run(fragment: Json, number: &Numbering, hit: Option<SpatialRayHit>) -> (Component, HookWorld) {
    let mut world = HookWorld { hit };
    let mut component = Component::start(
        Arc::new(store(vec![fragment])),
        123,
        Variables::new(),
        vec![Root::new(number.instance, number.graph)],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    component
        .set_instance_variable(
            number.instance,
            number.timeout,
            Value::Float(0.0),
            &mut world,
        )
        .unwrap();
    // The Wait is the entry's authored successor; begin it explicitly so the
    // box edge (not an entry begin) is the launch under test.
    component
        .begin_state(number.instance, number.states[0], &mut world)
        .unwrap();
    for frame in 1..=5 {
        component.advance_frame(frame, &mut world).unwrap();
    }
    (component, world)
}

fn hit_at(distance: f64) -> SpatialRayHit {
    SpatialRayHit {
        point: [0.0, 1.5, distance],
        normal: [0.0, 0.0, -1.0],
        distance,
    }
}

fn states(component: &Component, instance: u16) -> Vec<(u16, bool, bool, bool)> {
    let snapshot = component.snapshot().unwrap();
    snapshot.instances[&instance]
        .states
        .iter()
        .map(|(&raw, state)| {
            (
                raw,
                state.active,
                matches!(state.kind, StateKind::StaticTrace { .. }),
                matches!(state.kind, StateKind::MoveTo { .. }),
            )
        })
        .collect()
}

#[test]
fn subgraph_box_launches_the_trace_and_the_hit_case_begins_move_to() {
    for number in &NUMBERS {
        let (component, _world) = run(fragment(number, true, false), number, Some(hit_at(6.0)));
        let states = states(&component, number.instance);
        assert!(
            states
                .iter()
                .any(|(raw, _, trace, _)| { *raw == number.states[1] && *trace }),
            "the box-launched trace must materialize: {states:?}"
        );
        assert!(
            states
                .iter()
                .any(|(raw, _, _, move_to)| { *raw == number.states[3] && *move_to }),
            "the hit case must begin MoveTo through the authored switch subgraph branch: {states:?}"
        );
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "a resolved box launch reports no gap: {:?}",
            snapshot.diagnostics
        );
    }
}

#[test]
fn subgraph_box_miss_routes_the_switch_false_branch_without_move_to() {
    for number in &NUMBERS {
        let (component, _world) = run(fragment(number, true, false), number, None);
        let states = states(&component, number.instance);
        assert!(
            states
                .iter()
                .any(|(raw, _, trace, _)| { *raw == number.states[1] && *trace }),
            "a miss still launches and completes the trace: {states:?}"
        );
        assert!(
            !states
                .iter()
                .any(|(raw, active, _, _)| *raw == number.states[3] && *active),
            "the miss branch must not begin MoveTo: {states:?}"
        );
        let snapshot = component.snapshot().unwrap();
        assert_eq!(
            snapshot.variables.get(&number.missed).cloned(),
            Some(Value::Bool(true)),
            "the false-branch SetVar must run"
        );
    }
}

#[test]
fn tag_only_container_keeps_the_missing_node_report_and_never_launches() {
    // The current reference-extractor form: containers record the class tag
    // only. The wait edge into the box reports the dropped launch instead of
    // silently materializing nothing, and the trace state never begins.
    for number in &NUMBERS {
        let (component, _world) = run(fragment(number, false, false), number, Some(hit_at(6.0)));
        let states = states(&component, number.instance);
        assert!(
            !states
                .iter()
                .any(|(raw, _, trace, _)| { *raw == number.states[1] && *trace }),
            "a tag-only box may not launch the trace: {states:?}"
        );
        let snapshot = component.snapshot().unwrap();
        assert!(
            !snapshot.initialization_complete(),
            "the dropped launch must stay loud"
        );
        assert!(
            snapshot.diagnostics.iter().any(|diagnostic| {
                diagnostic.issue == Issue::MissingNode(number.launch_box)
                    && diagnostic.site.instance == number.instance
                    && diagnostic.site.node == Some(number.wait)
            }),
            "{:?}",
            snapshot.diagnostics
        );
    }
}

#[test]
fn state_group_reference_without_a_declared_container_is_reported() {
    // Referential integrity: the reference extractor emits `{"item":N}` only
    // for entries it recorded as containers, so a missing declaration is a
    // broken bundle and must appear in the inspection report.
    for number in &NUMBERS {
        let store = store(vec![fragment(number, true, true)]);
        let dangling: Vec<_> = store
            .dangling_links()
            .filter(|link| link.graph == number.graph)
            .collect();
        assert_eq!(dangling.len(), 1, "{dangling:?}");
        assert_eq!(dangling[0].source, number.trace);
        assert_eq!(dangling[0].plug, "m_0B1AA8CA");
        assert_eq!(dangling[0].target, 63);
        assert_eq!(
            store
                .graph(number.graph)
                .unwrap()
                .container(number.launch_box)
                .map(|container| container.class.as_str()),
            Some("STUStatescriptSubGraph"),
            "the launch box remains declared"
        );
    }
}

#[test]
fn nested_launch_boxes_route_to_the_deepest_begin_edge() {
    // The wait edge enters the launch box, which forwards into the trace
    // group box, which owns the trace's begin edge. Routing must walk the
    // whole nesting (bounded), not stop at the first box.
    for number in &NUMBERS {
        let mut fragment = fragment(number, true, false);
        fragment["containers"][format!("{}", number.launch_box)]["links"] =
            json!([[number.trace_group, "m_beginPlug"]]);
        fragment["containers"][format!("{}", number.trace_group)] = json!({
            "class": "STUStatescriptSubGraph",
            "links": [[number.trace, "m_beginPlug"]],
        });
        let (component, _world) = run(fragment, number, Some(hit_at(6.0)));
        let states = states(&component, number.instance);
        assert!(
            states
                .iter()
                .any(|(raw, _, trace, _)| { *raw == number.states[1] && *trace }),
            "the double-nested box chain must still launch the trace: {states:?}"
        );
    }
}

#[test]
fn launch_box_cycle_faults_on_plug_depth_instead_of_looping() {
    // Two boxes whose recorded begin edges point at each other form a
    // routing cycle. The shared plug-depth budget must turn that into a
    // loud fault instead of an unbounded walk.
    for number in &NUMBERS {
        let mut fragment = fragment(number, true, false);
        fragment["containers"][format!("{}", number.launch_box)]["links"] =
            json!([[number.trace_group, "m_beginPlug"]]);
        fragment["containers"][format!("{}", number.trace_group)] = json!({
            "class": "STUStatescriptSubGraph",
            "links": [[number.launch_box, "m_beginPlug"]],
        });
        let mut world = HookWorld {
            hit: Some(hit_at(6.0)),
        };
        let mut component = Component::start(
            Arc::new(store(vec![fragment])),
            123,
            Variables::new(),
            vec![Root::new(number.instance, number.graph)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component
            .set_instance_variable(
                number.instance,
                number.timeout,
                Value::Float(0.0),
                &mut world,
            )
            .unwrap();
        component
            .begin_state(number.instance, number.states[0], &mut world)
            .unwrap();
        let fault = (1..=5)
            .find_map(|frame| component.advance_frame(frame, &mut world).err())
            .expect("the box cycle must fault while routing the launch edge");
        assert_eq!(fault, InitFault::PlugDepth);
    }
}

/// One graph whose entry begins an unmodeled class through the plain
/// lifecycle plugs. The class name is synthetic on purpose: the policy, not
/// any hero or graph, decides what happens to it.
fn unmodeled_fragment(position: u16, raw: u16) -> Json {
    let mut fixture = graph(
        9500,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug": {
                    "$":"STUStatescriptOutputPlug","links":[[position,"m_beginPlug"]],
                    "m_FF3DAF1E":[]
                }}),
            ),
            node(
                position,
                "fixture.unmodeled",
                Some(raw),
                json!({
                    "m_beginPlug": {"$":"STUStatescriptInputPlug","links":[]},
                    "m_onEndPlug": {"$":"STUStatescriptOutputPlug","links":[]}
                }),
            ),
        ],
        json!([null, null, position]),
        json!([0]),
    );
    fixture["items"] = json!(usize::from(position) + 1);
    fixture
}

#[test]
fn the_normal_policy_runs_an_unmodeled_class_as_the_plain_base_lifecycle() {
    // runtime.py `state_type`: outside a strict component an unmodeled class
    // gets the plain base State. It must materialize active with no report,
    // and the wire state stays plain (the codec table has no record).
    let mut world = HookWorld { hit: None };
    let mut component = Component::start_with_policy(
        Arc::new(store(vec![unmodeled_fragment(7, 2)])),
        123,
        Variables::new(),
        vec![Root::new(4, 9500)],
        0,
        &mut world,
        (MAX_INIT_WORK, false),
    )
    .unwrap();
    component.advance_frame(1, &mut world).unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "the base lifecycle fallback reports nothing: {:?}",
        snapshot.diagnostics
    );
    let state = snapshot.instances[&4]
        .states
        .get(&2)
        .expect("the unmodeled class materializes like the reference base State");
    assert!(state.active);
    assert!(matches!(state.kind, StateKind::Plain));
}

#[test]
fn the_strict_policy_still_reports_the_unmodeled_class() {
    let mut world = HookWorld { hit: None };
    let component = Component::start(
        Arc::new(store(vec![unmodeled_fragment(7, 2)])),
        123,
        Variables::new(),
        vec![Root::new(4, 9500)],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let snapshot = component.snapshot().unwrap();
    assert!(
        matches!(
            &snapshot.diagnostics[..],
            [Diagnostic { issue: Issue::UnsupportedState(class), .. }] if class == "fixture.unmodeled"
        ),
        "{:?}",
        snapshot.diagnostics
    );
}
