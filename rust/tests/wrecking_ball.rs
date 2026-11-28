use std::{path::PathBuf, sync::Arc};

use orpheus::{
    contact_world::ContactWorld,
    mover::Mover,
    mover_settings::MoverSettings,
    script_codecs::CodecTable,
    script_expr::{SpatialRayHit, VelocityQuery},
    script_graph::GraphStore,
    script_init::{Component, MAX_INIT_WORK, MovementSample, Root, Site, VelocityOp, World},
    script_modifiers::MoverProfile,
    script_snapshot_wire::SnapshotEncoder,
    script_wire::{Value, Variables},
};
use serde_json::{Value as Json, json};

fn prepared_runtime_root() -> PathBuf {
    PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("ORPHEUS_RUNTIME_DIR"))
}

fn dyn_var(identifier: u32, scope: u8) -> Json {
    json!({
        "$": "STUConfigVarDynamic",
        "m_identifier": identifier,
        "m_60DB8F99": scope
    })
}

fn plug_link(pos: u16, plug: &str) -> Json {
    json!({ "$": "STUStatescriptBasePlug", "links": [[pos, plug]] })
}

fn node_json(pos: u16, class: &str, state: Option<u16>, rid: u16, mut fields: Json) -> Json {
    if state.is_some() {
        if let Some(map) = fields.as_object_mut() {
            map.entry("m_2BBEEAB8").or_insert_with(|| json!(1));
        }
    }
    json!({
        "pos": pos,
        "class": class,
        "uid": 0,
        "rid": rid,
        "remote": -1,
        "state": state,
        "client_only": 0,
        "server_only": 0,
        "fields": fields
    })
}

fn graph_store(
    index: u64,
    nodes: Vec<Json>,
    states: Vec<u16>,
    entries: Vec<u16>,
) -> Arc<GraphStore> {
    let items = nodes.len();
    let bundle = json!({
        "version": 1,
        "bodies": {},
        "missing": [],
        "graphs": [{
            "index": index,
            "items": items,
            "states": states,
            "entries": entries,
            "remote": [],
            "sync_vars": [],
            "containers": {},
            "unknown_nodes": [],
            "fields": {},
            "header": {
                "m_nodesBitCount": 8,
                "m_statesBitCount": 8,
                "m_remoteSyncNodesBitCount": 4,
                "m_syncVarsBitCount": 8
            },
            "nodes": nodes
        }]
    });
    Arc::new(GraphStore::from_json(bundle.to_string().as_bytes()).unwrap())
}

type TargetImpulse = (u32, Option<[f64; 3]>);

#[derive(Default)]
struct WbTestWorld {
    owner: u32,
    dead: bool,
    positions: Vec<(u32, [f64; 3])>,
    forward: [f64; 3],
    velocity: [f64; 3],
    velocity_unavailable: bool,
    grounded: bool,
    ray_hit: Option<SpatialRayHit>,
    attachment: Option<(u64, [f64; 3])>,
    bone: Option<(u64, [f64; 3])>,
    cone_targets: Vec<u32>,
    surface_distances: Vec<(u32, f64)>,
    last_swept_visible: Option<bool>,
    contact_hits: Vec<(f64, Vec<TargetImpulse>)>,
    velocity_ops: Vec<Vec<VelocityOp>>,
}

impl WbTestWorld {
    fn tracked_samples(&self) -> Vec<orpheus::script_init::TrackedTarget> {
        self.cone_targets
            .iter()
            .map(|&entity| orpheus::script_init::TrackedTarget {
                entity,
                surface_distance: self
                    .surface_distances
                    .iter()
                    .find(|r| r.0 == entity)
                    .map(|r| r.1),
            })
            .collect()
    }
}

impl World for WbTestWorld {
    fn admits_generic_player_runtime(&self, _site: Site) -> bool {
        true
    }

    fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }

    fn known_position(&self, entity: u32) -> Option<[f64; 3]> {
        self.positions
            .iter()
            .find(|(e, _)| *e == entity)
            .map(|(_, p)| *p)
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.known_position(entity)
    }

    fn body_eye_position(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.owner && self.dead {
            return None;
        }
        let p = self.known_position(entity)?;
        Some([p[0], p[1] + 1.4, p[2]])
    }

    fn body_hardpoint_position(&self, entity: u32, id: u64) -> Option<[f64; 3]> {
        self.attachment
            .filter(|(key, _)| entity == self.owner && *key == id)
            .map(|(_, point)| point)
    }

    fn body_bone_point(&mut self, entity: u32, id: u64, local: [f64; 3]) -> Option<[f64; 3]> {
        let (bone, origin) = self.bone?;
        (entity == self.owner && bone == id).then(|| std::array::from_fn(|i| origin[i] + local[i]))
    }

    fn body_scale(&mut self, _site: Site, _entity: u32, _override_value: &Value) -> Option<f64> {
        Some(1.0)
    }

    fn play_script(
        &mut self,
        _site: Site,
        _target: u32,
        _request: orpheus::script_init::PlayScript,
    ) -> bool {
        true
    }

    fn player_stats_available(&self, _site: Site) -> Option<bool> {
        Some(false)
    }

    fn voice_context(&mut self, _site: Site) -> Option<bool> {
        Some(false)
    }

    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        (entity == self.owner).then_some(self.forward)
    }

    fn entity_velocity(&self, entity: u32) -> VelocityQuery {
        if entity == self.owner && !self.velocity_unavailable {
            VelocityQuery::Present(self.velocity)
        } else {
            VelocityQuery::Unavailable
        }
    }

    fn movement_sample(&self, site: Site) -> Option<MovementSample> {
        (site.entity == self.owner).then_some(MovementSample {
            flags: if self.grounded { 1 } else { 0 },
            air_ticks: if self.grounded { 0 } else { 10 },
            pose_frame: Some(1),
        })
    }

    fn owner_collision_category(&self, site: Site) -> Option<u16> {
        (site.entity == self.owner).then_some(0x08)
    }

    fn profiled_static_ray(
        &self,
        site: Site,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
        category: u16,
    ) -> Option<Option<SpatialRayHit>> {
        assert_eq!(Some(category), self.owner_collision_category(site));
        self.spatial_ray_cast(site, 0, origin, direction, max_distance)
    }

    fn spatial_ray_cast(
        &self,
        _site: Site,
        _vm_frame: u32,
        _origin: [f64; 3],
        _direction: [f64; 3],
        _max_distance: f64,
    ) -> Option<Option<SpatialRayHit>> {
        Some(self.ray_hit)
    }

    fn cone_targets_directed(
        &self,
        _site: Site,
        _origin: u32,
        _offset: [f64; 3],
        _radius: f64,
        _angle: f64,
        _cylinder_radius: f64,
        _yaw_offset_deg: Option<f64>,
        _pitch_offset_deg: Option<f64>,
        _visible: bool,
        _filter: &Json,
    ) -> Option<Vec<orpheus::script_init::TrackedTarget>> {
        Some(self.tracked_samples())
    }

    fn swept_tracked_targets(
        &mut self,
        _site: Site,
        _origin: u32,
        _start_offset: [f64; 3],
        _end_offset: Option<[f64; 3]>,
        _local_space: bool,
        _radius: f64,
        visible: bool,
        _filter: &Json,
    ) -> Option<Vec<orpheus::script_init::TrackedTarget>> {
        self.last_swept_visible = Some(visible);
        Some(self.tracked_samples())
    }

    fn queue_modify_health_with_meta(
        &mut self,
        _site: Site,
        target: u32,
        delta: f64,
        impulse: Option<[f64; 3]>,
        _loadout: Option<u64>,
        _tags: orpheus::script_init::HitTags,
    ) -> bool {
        self.contact_hits.push((-delta, vec![(target, impulse)]));
        true
    }

    fn apply_velocity_ops(&mut self, _site: Site, _frame: u32, ops: &[VelocityOp]) -> bool {
        self.velocity_ops.push(ops.to_vec());
        true
    }
}

#[test]
fn roll_mover_profile_override_and_camera_state_without_speed_hack() {
    let roll_component = json!({
        "$": "STUCharacterMoverComponent",
        "m_1972C687": 2.0,
        "m_7A70E3E3": 0.5,
        "m_4A576BC2": 1.0,
        "m_03E04168": 1.25,
        "m_915FF368": 0.5,
        "m_D97B3735": 0.25,
        "m_D4AE9D05": 0.45,
        "m_5C6B4602": 8,
        "m_6B7FDB90": 10.0,
        "m_E54438F7": 1.0,
        "m_AEBAD1B0": 45.0,
        "m_08FFAA0A": 0.5,
        "m_CFD3E635": 10.0,
        "m_61D6B4E9": 10.0,
        "m_19452F15": 10.0,
        "m_B470B8DA": 3.0,
        "m_AA6F4CE1": 3.0,
        "m_0DDD18C3": 3.0,
        "m_DAA0618B": 4.0,
        "m_E631BA84": 2.0,
        "m_D24BF4E6": 0.5,
        "m_E0A0FE25": 1.0,
        "m_6F25AA23": 18.0,
        "m_4E03E892": 2.0,
        "m_142DA8D4": 0.0,
        "m_031FE7EE": 18.0,
        "m_8895A9C8": 2.0,
        "m_EC289987": 6.0,
        "m_C2C40969": 1.75,
        "m_B74245E8": 30.0,
        "m_975F21A5": 0.0,
        "m_B99EC983": 1.75,
        "m_CEF4898C": 0.0,
        "m_D4A3C694": 1
    });
    let profile = MoverProfile::from_stu_component(&roll_component)
        .expect("34-field retail mover component should parse");
    assert_eq!(profile.run_forward, 10.0);
    assert_eq!(profile.accel_ground, 4.0);
    let settings = MoverSettings::from(profile);
    assert_eq!(settings.run_forward, 10.0);

    // StateScript `STU_316CFEF2` with `STU_ED2B22BD` exposes `active_mover_override()`
    // and cleans up on end; `STU_582E0D5F` (3rd-person camera) must NOT inject a fake SPEED_SLOT.
    let graphs = graph_store(
        0x1203,
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({
                    "m_outPlug": {
                        "$": "STUStatescriptOutputPlug",
                        "links": [[1, "m_beginPlug"], [2, "m_beginPlug"]]
                    }
                }),
            ),
            node_json(
                1,
                "STU_316CFEF2",
                Some(0),
                1,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_BC5E91CF": [{
                        "$": "STU_ED2B22BD",
                        "m_9B7A63EA": roll_component
                    }]
                }),
            ),
            node_json(
                2,
                "STU_582E0D5F",
                Some(1),
                2,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] }
                }),
            ),
        ],
        vec![1, 2],
        vec![0],
    );
    let owner = 0xa000_0101;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 0.0, 0.0])],
        forward: [0.0, 0.0, 1.0],
        ..Default::default()
    };
    let mut comp = Component::start(
        graphs,
        owner,
        Variables::new(),
        vec![Root::new(1, 0x1203)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let active_override = comp
        .active_mover_override()
        .expect("Roll mover override active");
    assert_eq!(active_override.run_forward, 10.0);
    let mods = comp
        .movement_modifiers(&mut world)
        .unwrap()
        .unwrap_or_default();
    assert!(
        mods.is_empty(),
        "STU_582E0D5F must not inject fake speed modifiers: {mods:?}"
    );

    // Ending `STU_316CFEF2` clears the mover override back to None.
    comp.debug_end_state(1, 0, &mut world).unwrap();
    assert!(comp.active_mover_override().is_none());

    let contact = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let _mover = Mover::new(MoverSettings::from(profile), contact, [0.0; 3], 0, 0);
}

#[test]
fn fireball_knockback_vertical_popup_and_array_remove_value() {
    let graphs = graph_store(
        0x1203,
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({
                    "m_outPlug": {
                        "$": "STUStatescriptOutputPlug",
                        "links": [[1, "m_beginPlug"], [2, "m_inPlug"]]
                    }
                }),
            ),
            node_json(
                1,
                "STUStatescriptStateModifyHealth",
                Some(0),
                1,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_FA3ECC43": dyn_var(10, 0),
                    "m_modifyHealth": {
                        "$": "STUStatescriptModifyHealth",
                        "m_93CDFD2F": 1,
                        "m_amount": { "$": "STUConfigVarFloat", "m_value": 50.0 },
                        "m_E33EFEE1": { "$": "STUConfigVarFloat", "m_value": 22.0 },
                        "m_583C208B": { "$": "STUConfigVarFloat", "m_value": 5.0 },
                        "m_9B350209": { "$": "STUConfigVarBool", "m_value": true }
                    }
                }),
            ),
            node_json(
                2,
                "STU_DA54F8F7",
                None,
                2,
                json!({
                    "m_inPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_26E3B366": dyn_var(20, 0),
                    "m_value": dyn_var(21, 0)
                }),
            ),
        ],
        vec![1],
        vec![0],
    );
    let owner = 0xa000_0101;
    let enemy = 0xa000_0201;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 0.0, 0.0]), (enemy, [0.0, 0.0, 3.0])],
        forward: [0.0, 0.0, 1.0],
        ..Default::default()
    };
    let mut root = Root::new(1, 0x1203);
    root.variables.insert(10, Value::Entity(enemy));
    root.variables.insert(
        20,
        Value::Array(vec![Value::Entity(enemy), Value::Entity(0xa000_0202)]),
    );
    root.variables
        .insert(21, Value::Array(vec![Value::Entity(enemy)]));

    let comp = Component::start(
        graphs,
        owner,
        Variables::new(),
        vec![root],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();

    assert_eq!(world.contact_hits.len(), 1);
    let (damage, hits) = &world.contact_hits[0];
    assert_eq!(*damage, 50.0);
    let (hit_entity, impulse) = hits[0];
    assert_eq!(hit_entity, enemy);
    let imp = impulse.expect("Fireball must apply knockback impulse");
    assert!(
        imp[2] > 10.0,
        "Fireball must knock enemy AWAY (+Z), got {imp:?}"
    );
    assert!(
        (imp[1] - 5.0).abs() < 1e-6,
        "Fireball vertical pop-up must be 5.0, got {imp:?}"
    );

    let snap = comp.snapshot().unwrap();
    assert_eq!(
        snap.instances[&1].variables[&20],
        Value::Array(vec![Value::Entity(0xa000_0202)])
    );
}

#[test]
fn grapple_trace_uses_the_selected_attachment_below_datum_and_accepts_vertical_aim() {
    let graph = 0x180;
    let store = graph_store(
        graph,
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({ "m_outPlug": plug_link(1, "m_beginPlug") }),
            ),
            node_json(
                1,
                "STU_4BBC5D09",
                Some(0),
                1,
                json!({
                    "m_speed": {"$":"STUConfigVarFloat","m_value":100.0},
                    "m_2AFC083F": {"$":"STUConfigVarFloat","m_value":25.0},
                    "m_F745334E": {"$":"STUConfigVarHardPoint","m_hardPoint":"0x0DC0000000000042"},
                    "m_F2F8606D": dyn_var(1,0),
                    "m_64801077": dyn_var(2,0),
                    "m_9E1A7C58": dyn_var(3,0)
                }),
            ),
        ],
        vec![1],
        vec![0],
    );
    let origin = [4.0, -23.0, 8.0];
    let target = [4.0, -18.0, 8.0];
    let owner = 0xa000_0101;
    let attachment = 0x0dc0_0000_0000_0042;
    for (sample, aim, expected_hit) in [
        (Some((attachment, origin)), [0.0, 1.0, 0.0], true),
        (None, [0.0, 1.0, 0.0], false),
        (Some((attachment, origin)), [0.0; 3], false),
        (
            Some((attachment, [4.0, -23.0, f64::NAN])),
            [0.0, 1.0, 0.0],
            false,
        ),
    ] {
        let mut world = WbTestWorld {
            owner,
            positions: vec![(owner, [0.0; 3])],
            forward: aim,
            attachment: sample,
            ray_hit: Some(SpatialRayHit {
                point: target,
                normal: [0.0, -1.0, 0.0],
                distance: 5.0,
            }),
            ..Default::default()
        };
        let mut comp = Component::start(
            store.clone(),
            owner,
            Variables::new(),
            vec![Root::new(1, graph as u32)],
            1,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snap = comp.snapshot().unwrap();
        let vars = &snap.instances[&1].variables;
        if expected_hit {
            assert_eq!(vars.get(&1), Some(&Value::Vector(origin)));
            assert_eq!(vars.get(&2), Some(&Value::Vector(aim)));
        } else {
            assert!(!matches!(vars.get(&1), Some(Value::Vector(_))));
        }
        for frame in 1..=10 {
            comp.advance_frame(frame, &mut world).unwrap();
        }
        let snap = comp.snapshot().unwrap();
        assert_eq!(
            snap.instances[&1].variables.get(&3),
            Some(&Value::Bool(expected_hit))
        );
        if expected_hit {
            assert_eq!(
                snap.instances[&1].variables.get(&1),
                Some(&Value::Vector(target))
            );
        }
    }
}

#[test]
fn grapple_trace_preserves_anchor_and_pendulum_tether_constrains_rope() {
    let graph = 0x101;
    let graphs = graph_store(
        u64::from(graph),
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({ "m_outPlug": plug_link(1, "m_beginPlug") }),
            ),
            node_json(
                1,
                "STU_4BBC5D09",
                Some(0),
                1,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_onFinishedPlug": {
                        "$": "STUStatescriptOutputPlug",
                        "links": [[2, "m_beginPlug"], [3, "m_beginPlug"]]
                    },
                    "m_speed": { "$": "STUConfigVarFloat", "m_value": 100.0 },
                    "m_2AFC083F": { "$": "STUConfigVarFloat", "m_value": 25.0 },
                    "m_F745334E": { "$": "STUConfigVarHardPoint", "m_hardPoint": "0x0DC0000000000042" },
                    "m_5ED803CF": dyn_var(431, 0),
                    "m_F2F8606D": dyn_var(432, 0),
                    "m_D7A2A03B": dyn_var(433, 0),
                    "m_13BE2D3D": dyn_var(434, 0),
                    "m_9E1A7C58": dyn_var(435, 0),
                    "m_1FFD7AC0": dyn_var(436, 0),
                    "m_86CF0002": dyn_var(437, 0)
                }),
            ),
            node_json(
                2,
                "STU_8B4B379C",
                Some(1),
                2,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_865F3D01": dyn_var(431, 0),
                    "m_3BC31C57": { "$": "STUConfigVarVec3", "m_value": { "X": 0.0, "Y": 0.7, "Z": 0.0 } },
                    "m_189141AE": { "$": "STUConfigVarFloat", "m_value": 6.0 },
                    "m_5BEFF040": { "$": "STUConfigVarFloat", "m_value": 10.0 }
                }),
            ),
            node_json(
                3,
                "STU_316CFEF2",
                Some(2),
                3,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_BC5E91CF": [{
                        "$": "STU_4CB0950F",
                        "m_9B7A63EA": { "$": "STUConfigVarFloat", "m_value": 1.0 }
                    }]
                }),
            ),
        ],
        vec![1, 2, 3],
        vec![0],
    );
    let owner = 0xa000_0101;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 0.0, 0.0])],
        forward: [0.0, 0.0, 1.0],
        ray_hit: Some(SpatialRayHit {
            point: [0.25, 0.75, 10.0],
            normal: [0.0, 0.0, -1.0],
            distance: 10.0,
        }),
        attachment: Some((0x0dc0_0000_0000_0042, [0.25, 0.75, 0.0])),
        ..Default::default()
    };
    let mut comp = Component::start(
        graphs,
        owner,
        Variables::new(),
        vec![Root::new(1, graph)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();

    assert_eq!(
        comp.snapshot().unwrap().instances[&1].variables.get(&432),
        Some(&Value::Vector([0.25, 0.75, 0.0]))
    );
    for frame in 1..=12 {
        comp.advance_frame(frame, &mut world).unwrap();
    }
    let snap = comp.snapshot().unwrap();
    assert!(!snap.instances[&1].states[&0].active);
    assert!(
        snap.instances[&1].states[&1].active,
        "pendulum tether should be active"
    );
    assert!(
        matches!(snap.instances[&1].variables.get(&431), Some(Value::Vector(v)) if (v[2] - 10.0).abs() < 0.1),
        "anchor var[431] must be preserved after STU_4BBC5D09 finishes: {:?}",
        snap.instances[&1].variables.get(&431)
    );
    assert_eq!(
        snap.instances[&1].variables.get(&435),
        Some(&Value::Bool(true))
    );
    assert_eq!(
        snap.instances[&1].variables.get(&436),
        Some(&Value::Entity(0))
    );
    assert_eq!(
        snap.instances[&1].variables.get(&437),
        Some(&Value::Asset(0))
    );
    assert!(
        world.velocity_ops.is_empty(),
        "casting from rest moved the owner"
    );
    // The VM publishes scope and length. The mover supplies the proposed
    // displacement after composing current input, gravity and other impulses.
    let cable = comp.active_tether().unwrap();
    assert!(
        cable
            .constrain_step(world.positions[0].1, [0.0; 3])
            .is_none()
    );
    world.positions[0].1[2] = 8.0;
    comp.advance_frame(13, &mut world).unwrap();
    assert!(
        comp.active_tether()
            .unwrap()
            .constrain_step(world.positions[0].1, [0.064, 0.0, 0.0])
            .is_none()
    );
    world.positions[0].1[2] = -2.0;
    world.velocity_unavailable = true;
    comp.advance_frame(14, &mut world).unwrap();
    assert!(
        world.velocity_ops.is_empty(),
        "VM wrote a stale velocity impulse"
    );
    let cable = comp.active_tether().unwrap();
    let dt = 0.016;
    let [vx, vy, vz] = cable
        .constrain_step(world.positions[0].1, [8.0 * dt, 0.0, -5.0 * dt])
        .expect("outward input needs a shorter step")
        .map(|axis| axis / dt);
    assert!(vx.hypot(vy).hypot(vz) <= 8.0_f64.hypot(5.0));
    assert!(-0.25 * vx - 0.05 * vy - 12.0 * vz <= 1e-8);
    assert!(vx > 7.0, "tangential motion was lost");
    assert!(
        cable
            .constrain_step(world.positions[0].1, [0.0; 3])
            .is_none(),
        "a corrected idle pose launched the owner"
    );
    let mods = comp
        .movement_modifiers(&mut world)
        .unwrap()
        .unwrap_or_default();
    assert!(
        mods.values()
            .any(|m| m.index == 0 && (m.value - 1.0).abs() < 1e-6),
        "STU_4CB0950F without m_A9561CA0 must default to SPEED_SLOT (0): {mods:?}"
    );
}

#[test]
fn chase_resolves_a_bone_relative_target_before_interpolating() {
    use orpheus::script_expr::{Scope, Variable};

    let graphs = graph_store(
        0x105,
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({ "m_outPlug": plug_link(1, "m_beginPlug") }),
            ),
            node_json(
                1,
                "STUStatescriptStateChaseVar",
                Some(0),
                1,
                json!({
                    "m_out_Var": dyn_var(9, 0),
                    "m_EB0FC261": {"$":"STUConfigVarVec3","m_value":{"X":40,"Y":3,"Z":20}},
                    "m_destination": {"$":"STUConfigVarVec3","m_value":{"X":1,"Y":2,"Z":3}},
                    "m_destinationBone": {"$":"STUConfigVarBone","m_bone":"0x0880000000000067"},
                    "m_destinationObject": {"$":"STU_B5A0CAF0"},
                    "m_9B0385B4": {"$":"STUConfigVarFloat","m_value":10000.0}
                }),
            ),
        ],
        vec![1],
        vec![0],
    );
    let mut world = WbTestWorld {
        owner: 0xa000_0101,
        ..Default::default()
    };
    let mut comp = Component::start(
        graphs,
        world.owner,
        Variables::new(),
        vec![Root::new(1, 0x105)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let position = Variable {
        scope: Scope::Instance,
        id: 9,
    };
    comp.advance_frame(2, &mut world).unwrap();
    assert_eq!(
        comp.resolved_variable(1, position),
        Some(Value::Vector([40., 3., 20.]))
    );
    world.bone = Some((0x0880_0000_0000_0067, [41., 5., 22.]));
    comp.advance_frame(3, &mut world).unwrap();
    assert_eq!(
        comp.resolved_variable(1, position),
        Some(Value::Vector([42., 7., 25.]))
    );
}

#[test]
fn adaptive_shield_counts_targets_and_switches_by_zero_based_index() {
    let graphs = graph_store(
        0x1566,
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({ "m_outPlug": plug_link(1, "m_beginPlug") }),
            ),
            node_json(
                1,
                "STUStatescriptStateTrackTargets",
                Some(0),
                1,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_transitionPlug": [plug_link(2, "m_inPlug")],
                    "m_radius": { "$": "STUConfigVarFloat", "m_value": 10.0 },
                    "m_A30B383E": { "$": "STUConfigVarFloat", "m_value": 360.0 },
                    "m_8A2905A0": dyn_var(100, 0),
                    "m_CEE20F6F": dyn_var(101, 0),
                    "m_1B89EA79": dyn_var(102, 0)
                }),
            ),
            node_json(
                2,
                "STUStatescriptActionSwitch",
                None,
                2,
                json!({
                    "m_inPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_F0F0720B": 1,
                    "m_262AE982": dyn_var(102, 0),
                    "m_FDD34C25": [
                        plug_link(3, "m_inPlug"),
                        plug_link(3, "m_inPlug"),
                        plug_link(4, "m_inPlug")
                    ]
                }),
            ),
            node_json(
                3,
                "STUStatescriptActionSetVar",
                None,
                3,
                json!({
                    "m_inPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_out_Var": dyn_var(200, 0),
                    "m_value": { "$": "STUConfigVarInt", "m_value": 99 }
                }),
            ),
            node_json(
                4,
                "STUStatescriptActionSetVar",
                None,
                4,
                json!({
                    "m_inPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_out_Var": dyn_var(200, 0),
                    "m_value": { "$": "STUConfigVarInt", "m_value": 2 }
                }),
            ),
        ],
        vec![1],
        vec![0],
    );
    let owner = 0xa000_0101;
    let e1 = 0xa000_0201;
    let e2 = 0xa000_0202;
    let mut world = WbTestWorld {
        owner,
        positions: vec![
            (owner, [0.0, 0.0, 0.0]),
            (e1, [3.0, 0.0, 0.0]),
            (e2, [0.0, 0.0, 5.0]),
        ],
        forward: [0.0, 0.0, 1.0],
        cone_targets: vec![e1, e2],
        surface_distances: vec![(e1, 2.625), (e2, 4.75)],
        ..Default::default()
    };
    let comp = Component::start(
        graphs,
        owner,
        Variables::new(),
        vec![Root::new(1, 0x1566)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let snap = comp.snapshot().unwrap();
    assert_eq!(
        snap.instances[&1].variables.get(&102),
        Some(&Value::Integer(2))
    );
    assert_eq!(
        snap.instances[&1].variables.get(&200),
        Some(&Value::Integer(2))
    );
    assert!(matches!(
        snap.instances[&1].variables.get(&101),
        Some(Value::Array(arr)) if arr == &[Value::Float(2.625),Value::Float(4.75)]
    ));
}

#[test]
fn piledriver_raycast_and_discrete_modify_health_finish_loop() {
    let graphs = graph_store(
        0x160a,
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({
                    "m_outPlug": {
                        "$": "STUStatescriptOutputPlug",
                        "links": [[1, "m_beginPlug"], [2, "m_beginPlug"]]
                    }
                }),
            ),
            node_json(
                1,
                "STUStatescriptStateRayCast",
                Some(0),
                1,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_startPosWS": { "$": "STUConfigVarVec3", "m_value": { "X": 0.0, "Y": 5.0, "Z": 0.0 } },
                    "m_dirWS": { "$": "STUConfigVarVec3", "m_value": { "X": 0.0, "Y": -1.0, "Z": 0.0 } },
                    "m_castLength": { "$": "STUConfigVarFloat", "m_value": 10.0 },
                    "m_out_HitSomething": dyn_var(50, 0)
                }),
            ),
            node_json(
                2,
                "STUStatescriptStateModifyHealth",
                Some(1),
                2,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_onFinishedPlug": plug_link(3, "m_inPlug"),
                    "m_8B707686": 1,
                    "m_FA3ECC43": dyn_var(10, 0),
                    "m_modifyHealth": {
                        "$": "STUStatescriptModifyHealth",
                        "m_93CDFD2F": 1,
                        "m_amount": { "$": "STUConfigVarFloat", "m_value": 100.0 },
                        "m_E33EFEE1": { "$": "STUConfigVarFloat", "m_value": 5.0 },
                        "m_583C208B": { "$": "STUConfigVarFloat", "m_value": 11.0 }
                    }
                }),
            ),
            node_json(
                3,
                "STUStatescriptActionSetVar",
                None,
                3,
                json!({
                    "m_inPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_out_Var": dyn_var(51, 0),
                    "m_value": { "$": "STUConfigVarBool", "m_value": true }
                }),
            ),
        ],
        vec![1, 2],
        vec![0],
    );
    let owner = 0xa000_0101;
    let enemy = 0xa000_0201;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 0.0, 0.0]), (enemy, [0.0, 0.0, 2.0])],
        forward: [0.0, 0.0, 1.0],
        ray_hit: Some(SpatialRayHit {
            point: [0.0, 0.0, 0.0],
            normal: [0.0, 1.0, 0.0],
            distance: 5.0,
        }),
        ..Default::default()
    };
    let mut root = Root::new(1, 0x160a);
    root.variables.insert(10, Value::Entity(enemy));
    let comp = Component::start(
        graphs,
        owner,
        Variables::new(),
        vec![root],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let snap = comp.snapshot().unwrap();
    assert_eq!(
        snap.instances[&1].variables.get(&50),
        Some(&Value::Bool(true))
    );
    assert_eq!(
        snap.instances[&1].variables.get(&51),
        Some(&Value::Bool(true)),
        "discrete ModifyHealth must post Finish and follow m_onFinishedPlug"
    );
    let (damage, hits) = &world.contact_hits[0];
    assert_eq!(*damage, 100.0);
    let imp = hits[0].1.expect("Piledriver must emit knockback");
    assert!(
        (imp[1] - 11.0).abs() < 1e-6,
        "Piledriver pop-up must be 11.0, got {imp:?}"
    );
}

#[test]
#[ignore = "requires the prepared build 104319 runtime"]
fn real_pack_wrecking_ball_graphs_and_mine_armed_wire_frame() {
    let root = prepared_runtime_root();
    let graphs_path = root.join("data/game_graphs_174.json.gz");
    let codecs_path = root.join("data/statescript_codecs_174.json");
    let graphs = Arc::new(GraphStore::load(&graphs_path).unwrap());
    let codecs = CodecTable::load(&codecs_path).unwrap();

    // All Wrecking Ball hero & Minefield graphs exist in the 1.74 pack.
    for index in [
        0x1203u32, 0x14e4, 0x1514, 0x1566, 0x160a, 0x1517, 0x151a, 0x261e,
    ] {
        assert!(
            graphs.graph(index).is_some(),
            "missing Wrecking Ball graph 0x{index:04X} in runtime pack"
        );
    }

    // 1. Minefield armed wire frame (`0x151A` states 3 & 17 + companion `0x261E`) encodes cleanly.
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let bits = encoder
        .armed_projectile_frame(0x0400_0000_0000_2578)
        .unwrap()
        .expect("requires the projectile .003 bindings in the 104319 pack");
    assert!(
        bits.bit_len() > 32,
        "mine_armed_frame produced too few bits: {}",
        bits.bit_len()
    );

    // 2. Real `0x1566` Adaptive Shield initializes cleanly with zero VM faults.
    let owner = 0xa000_0101;
    let mut world = WbTestWorld {
        owner,
        positions: vec![
            (owner, [0.0, 0.0, 0.0]),
            (0xa000_0201, [2.0, 0.0, 0.0]),
            (0xa000_0202, [0.0, 0.0, 2.0]),
        ],
        forward: [0.0, 0.0, 1.0],
        cone_targets: vec![0xa000_0201, 0xa000_0202],
        ..Default::default()
    };
    let mut comp = Component::start(
        Arc::clone(&graphs),
        owner,
        Variables::new(),
        vec![Root::new(1, 0x1566)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    comp.advance_frame(1, &mut world).unwrap();
    assert!(comp.snapshot().is_some());

    // 3. Real `0x1203` Roll `STU_316CFEF2` -> `STU_ED2B22BD` -> `m_9B7A63EA` parses into a full 10.0 m/s MoverProfile!
    let g1203 = graphs.graph(0x1203).unwrap();
    let roll_mover = g1203
        .nodes
        .iter()
        .filter(|n| n.class == "STU_316CFEF2")
        .filter_map(|n| n.field("m_BC5E91CF")?.as_array())
        .flatten()
        .filter(|item| item.get("$").and_then(Json::as_str) == Some("STU_ED2B22BD"))
        .filter_map(|item| item.get("m_9B7A63EA"))
        .find_map(MoverProfile::from_stu_component)
        .expect("real 0x1203 STU_ED2B22BD must parse into retail MoverProfile");
    assert!(
        (roll_mover.run_forward - 10.0).abs() < 1e-4,
        "real Wrecking Ball Roll speed must be 10.0 m/s, got {}",
        roll_mover.run_forward
    );
}

#[test]
#[ignore = "requires the prepared build 104319 runtime"]
fn real_pack_piledriver_full_lifecycle_releases_locks_without_diagnostics() {
    let root = prepared_runtime_root();
    let graphs_path = root.join("data/game_graphs_174.json.gz");
    let codecs_path = root.join("data/statescript_codecs_174.json");
    let graphs = Arc::new(GraphStore::load(&graphs_path).unwrap());
    let codecs = CodecTable::load(&codecs_path).unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let owner = 0xa000_0101;
    let enemy = 0xa000_0201;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 6.0, 0.0]), (enemy, [0.0, 0.0, 2.0])],
        forward: [0.0, 0.0, 1.0],
        grounded: false,
        ray_hit: None, // high in air (>3.5m above floor) -> RayCast_90 misses -> var[9570] = true
        cone_targets: vec![enemy],
        surface_distances: vec![(enemy, 1.7)],
        ..Default::default()
    };
    let initial = Variables::from([(417, Value::Bool(true)), (545, Value::Bool(false))]);
    let mut comp = Component::start(
        Arc::clone(&graphs),
        owner,
        initial,
        vec![Root::new(1, 0x160a)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    let mut frame = 1u32;
    for _ in 0..5 {
        frame += 1;
        comp.advance_frame(frame, &mut world).unwrap();
        encoder
            .project(
                comp.snapshot().unwrap(),
                orpheus::script_snapshot_wire::View::Owner,
            )
            .expect("pre-crouch owner projection");
    }
    let crouch_mask = orpheus::script_runtime::held_button_mask(12).unwrap();
    for step in 0..80 {
        frame += 1;
        if step >= 50 && world.positions[0].1[1] > 0.0 {
            world.positions[0].1[1] = (world.positions[0].1[1] - 1.5).max(0.0);
            if world.positions[0].1[1] == 0.0 {
                world.grounded = true;
                comp.set_entity_variable(545, Value::Bool(true), &mut world)
                    .unwrap();
            }
        }
        let cmd = orpheus::commands::Command {
            frame,
            buttons: if step < 2 { crouch_mask } else { 0 },
            ..Default::default()
        };
        comp.advance_command(cmd, &mut world).unwrap();
        encoder
            .project(
                comp.snapshot().unwrap(),
                orpheus::script_snapshot_wire::View::Owner,
            )
            .unwrap_or_else(|err| {
                panic!("owner project failed at step={step} frame={frame}: {err}")
            });
    }
    let mods_end = comp
        .movement_modifiers(&mut world)
        .unwrap()
        .unwrap_or_default();
    let snap = comp.snapshot().unwrap();
    assert!(
        snap.diagnostics.is_empty(),
        "Piledriver must finish with zero diagnostics, got {:?}",
        snap.diagnostics
    );
    assert!(
        mods_end.is_empty(),
        "Piledriver must release all movement locks after landing, got {:?}",
        mods_end
            .values()
            .map(|m| (m.index, m.value))
            .collect::<Vec<_>>()
    );
}

#[test]
#[ignore = "requires the prepared build 104319 runtime"]
fn real_pack_roll_transitions_from_fold_anim_to_looping_ball_anim() {
    let root = prepared_runtime_root();
    let graphs_path = root.join("data/game_graphs_174.json.gz");
    let graphs = Arc::new(GraphStore::load(&graphs_path).unwrap());
    let owner = 0xa000_0101;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 0.0, 0.0])],
        forward: [0.0, 0.0, 1.0],
        grounded: true,
        ..Default::default()
    };
    let mut comp = Component::start(
        Arc::clone(&graphs),
        owner,
        Variables::from([(1216, Value::Bool(true))]),
        vec![Root::new(1, 0x1203)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    // Advance 300 ticks (~4.8s): one-shot Anim_41 (state 25) must finish and
    // hand off to looping Anim_40 (state 24), which stays active indefinitely.
    for frame in 2..=300u32 {
        comp.advance_frame(frame, &mut world).unwrap();
    }
    let snap = comp.snapshot().unwrap();
    assert!(
        snap.diagnostics.is_empty(),
        "Roll must have zero diagnostics, got {:?}",
        snap.diagnostics
    );
    let states = &snap.instances[&1].states;
    assert!(
        !states.get(&25).is_some_and(|s| s.active),
        "one-shot fold Anim_41 (state 25) must finish instead of sticking forever"
    );
    assert!(
        states.get(&24).is_some_and(|s| s.active),
        "looping Ball pose Anim_40 (state 24) must stay active indefinitely"
    );
    assert!(
        comp.active_mover_override().is_some(),
        "Roll MoverProfile override must remain active while rolling"
    );
}

#[test]
#[ignore = "requires the prepared build 104319 runtime"]
fn real_pack_adaptive_shield_enforces_los_and_survives_owner_death() {
    let root = prepared_runtime_root();
    let graphs_path = root.join("data/game_graphs_174.json.gz");
    let graphs = Arc::new(GraphStore::load(&graphs_path).unwrap());
    let owner = 0xa000_0101;
    let enemy = 0xa000_0201;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 0.0, 0.0]), (enemy, [2.0, 0.0, 0.0])],
        forward: [0.0, 0.0, 1.0],
        grounded: true,
        cone_targets: vec![enemy],
        ..Default::default()
    };
    let mut comp = Component::start(
        Arc::clone(&graphs),
        owner,
        Variables::new(),
        vec![Root::new(1, 0x1566)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    comp.advance_frame(2, &mut world).unwrap();
    assert_eq!(
        world.last_swept_visible,
        Some(true),
        "0x1566:20 TrackTargets_19 has m_32AFE9AA=1 and must pass visible=true to swept_tracked_targets"
    );
    assert_eq!(
        comp.snapshot().unwrap().instances[&1].variables.get(&11),
        Some(&Value::Integer(1)),
        "live enemy count must appear in 0x1566 var[11] without 48ms lag"
    );

    // Owner dies (body_eye_position returns None): 1566:20 must clear targets
    // cleanly without faulting with UnavailableBodyEyePosition.
    world.dead = true;
    comp.set_owner_dead(true);
    for frame in 3..=10u32 {
        comp.advance_frame(frame, &mut world).unwrap();
    }
    let snap = comp.snapshot().unwrap();
    assert!(
        snap.diagnostics.is_empty(),
        "Adaptive Shield must not emit UnavailableBodyEyePosition on owner death: {:?}",
        snap.diagnostics
    );
    assert_eq!(
        snap.instances[&1].variables.get(&11),
        Some(&Value::Integer(0)),
        "var[11] target count must clear to 0 when owner dies"
    );
}

#[test]
fn modify_health_on_target_enter_hits_each_new_target_once_until_it_leaves() {
    let graphs = graph_store(
        0x1203,
        vec![
            node_json(
                0,
                "STUStatescriptEntry",
                None,
                0,
                json!({
                    "m_outPlug": {
                        "$": "STUStatescriptOutputPlug",
                        "links": [[1, "m_beginPlug"], [2, "m_beginPlug"]]
                    }
                }),
            ),
            node_json(
                1,
                "STUStatescriptStateTrackTargets",
                Some(0),
                1,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_radius": { "$": "STUConfigVarFloat", "m_value": 3.0 },
                    "m_A30B383E": { "$": "STUConfigVarInt", "m_value": 70 },
                    "m_8A2905A0": dyn_var(375, 0)
                }),
            ),
            node_json(
                2,
                "STUStatescriptStateModifyHealth",
                Some(1),
                2,
                json!({
                    "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                    "m_DD792FF0": 1,
                    "m_7B2B3F5C": 0,
                    "m_8B707686": 0,
                    "m_FA3ECC43": dyn_var(375, 0),
                    "m_modifyHealth": {
                        "$": "STUStatescriptModifyHealth",
                        "m_93CDFD2F": 1,
                        "m_amount": { "$": "STUConfigVarFloat", "m_value": 50.0 },
                        "m_E33EFEE1": { "$": "STUConfigVarFloat", "m_value": 22.0 },
                        "m_583C208B": { "$": "STUConfigVarFloat", "m_value": 5.0 },
                        "m_10399AE7": { "$": "STUConfigVarInt", "m_value": 1 }
                    }
                }),
            ),
        ],
        vec![1, 2],
        vec![0],
    );
    let owner = 0xa000_0101;
    let enemy = 0xa000_0201;
    let mut world = WbTestWorld {
        owner,
        positions: vec![(owner, [0.0, 0.0, 0.0]), (enemy, [0.0, 0.0, 2.0])],
        forward: [0.0, 0.0, 1.0],
        grounded: true,
        ..Default::default()
    };
    let mut comp = Component::start(
        graphs,
        owner,
        Variables::new(),
        vec![Root::new(1, 0x1203)],
        1,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    // Spawn with empty targets: no damage yet.
    comp.advance_frame(2, &mut world).unwrap();
    assert!(world.contact_hits.is_empty());

    // Enemy enters tracked array: exactly one 50-damage hit with knockback.
    world.cone_targets = vec![enemy];
    for frame in 3..=6 {
        comp.advance_frame(frame, &mut world).unwrap();
    }
    assert_eq!(world.contact_hits.len(), 1, "must hit once per entry");
    assert_eq!(world.contact_hits[0].0, 50.0);
    assert!(world.contact_hits[0].1[0].1.is_some());

    // Enemy leaves, then re-enters on a second pass: hits once more.
    world.cone_targets.clear();
    comp.advance_frame(7, &mut world).unwrap();
    world.cone_targets = vec![enemy];
    comp.advance_frame(8, &mut world).unwrap();
    comp.advance_frame(9, &mut world).unwrap();
    assert_eq!(
        world.contact_hits.len(),
        2,
        "leaving and re-entering must trigger a fresh hit"
    );
}
