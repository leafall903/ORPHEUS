use super::*;

fn fixture() -> Json {
    serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../fixtures/behavior/actor-relative-movement-v1.json"
    )))
    .unwrap()
}

fn vector(value: &Json) -> [f64; 3] {
    std::array::from_fn(|n| value[n].as_f64().unwrap())
}

struct Host(Json);

impl World for Host {
    fn position(&mut self, _: u32) -> Option<[f64; 3]> {
        None
    }
    fn entity_variable(&mut self, _: u32, _: u32) -> Option<Value> {
        None
    }
    fn admits_generic_player_runtime(&self, _: Site) -> bool {
        true
    }
    fn generic_beam_contact(
        &self,
        _: Site,
        _: Option<[f64; 3]>,
        _: Option<[f64; 3]>,
        _: Option<f64>,
        _: f64,
        _: &Json,
    ) -> Option<GenericBeamContact> {
        Some(GenericBeamContact {
            hit: true,
            point: Some(vector(&self.0["point"])),
            normal: Some(vector(&self.0["normal"])),
            ..Default::default()
        })
    }
    fn body_bone_point(&mut self, entity: u32, bone: u64, local: [f64; 3]) -> Option<[f64; 3]> {
        if u64::from(entity) != self.0["actor"].as_u64()? || bone != 0 {
            return None;
        }
        crate::script_expr::body_space_point(
            vector(&self.0["feet"]),
            Some(vector(&self.0["forward"])),
            local,
        )
    }
}

#[test]
fn track_ray_exposes_contact_position_and_normal_separately() {
    let mut host = Host(fixture()["input"].clone());
    let root = graph(
        19,
        vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":plug(&[(1,"m_beginPlug")], &[])}),
            ),
            node(
                1,
                "STUStatescriptStateTrackRay",
                Some(0),
                json!({"m_target":{
                    "m_5ED803CF":var(1,false),"m_F2F8606D":var(2,false),"m_A7C2505B":var(3,false)
                }}),
            ),
        ],
        json!([1]),
        json!([0]),
    );
    let component = Component::start(
        Arc::new(store(vec![root])),
        91,
        Variables::new(),
        vec![Root::new(1, 19)],
        0,
        &mut host,
        MAX_INIT_WORK,
    )
    .unwrap();
    let vars = &component.snapshot().unwrap().instances[&1].variables;
    assert_eq!(vars[&1], Value::Vector(vector(&host.0["point"])));
    assert_eq!(vars[&2], vars[&1]);
    assert_eq!(vars[&3], Value::Vector(vector(&host.0["normal"])));
}

#[test]
fn played_move_to_resolves_a_local_offset_against_the_source_actor() {
    let fixture = fixture();
    let mut host = Host(fixture["input"].clone());
    let source = host.0["actor"].as_u64().unwrap() as u32;
    let target = host.0["recipient"].as_u64().unwrap() as u32;
    let local = vector(&host.0["local"]);
    let speed = host.0["speed"].as_f64().unwrap();
    let roots = graph(19, vec![], json!([]), json!([]));
    let played = graph(
        23,
        vec![
            node(
                0,
                "STU_BB4AC733",
                None,
                json!({"m_B60ABDA4":var(6,false),"m_outPlug":plug(&[(1,"m_beginPlug")], &[])}),
            ),
            node(
                1,
                "STUStatescriptStateMoveTo",
                Some(0),
                json!({"m_destinationEntity":var(6,false),"m_865F3D01":var(3,true),"m_speed":speed}),
            ),
        ],
        json!([1]),
        json!([0]),
    );
    let mut component = Component::start(
        Arc::new(store(vec![roots, played])),
        target,
        Variables::from([(3, Value::Vector(local))]),
        vec![Root::new(1, 19)],
        0,
        &mut host,
        MAX_INIT_WORK,
    )
    .unwrap();
    let caller = Site {
        entity: source,
        instance: 1,
        graph: 37,
        node: Some(2),
    };
    let request = PlayScript {
        graph: 23,
        source,
        parameters: Vec::new(),
        overrides: Vec::new(),
        policy: 1,
        auto_retire: false,
    };
    component
        .receive_play_script(caller, &request, &mut host)
        .unwrap();
    let played = component
        .snapshot()
        .unwrap()
        .instances
        .values()
        .find(|i| i.graph == 23)
        .unwrap();
    assert_eq!(
        played.states[&0].move_request(),
        Some((vector(&fixture["expected"]["world_destination"]), speed))
    );
}
