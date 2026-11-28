//! Prepared offline contract for the authored Widowmaker hook graph:
//! the production command path (logical buttons 11 and 35) drives the real
//! 104319 listener through wait → static trace → hit gate → movement
//! request, with a test-only collision adapter standing in for map
//! geometry. A miss must not launch the move. Nothing is suppressed: the
//! run asserts an empty diagnostic set.

use super::*;
use crate::script_expr::SpatialRayHit;
use crate::script_init::world::{HookPullGeometry, Site, World};

/// The reference capsule the T13f neighbourhood is measured with (her
/// standing mover: r=0.5, segment=0.7071, step_down=0.5, walkable slope
/// cos 45) — the same numbers the production mover settings carry.
fn hook_capsule() -> HookPullGeometry {
    HookPullGeometry {
        radius: 0.5,
        segment: std::f64::consts::FRAC_1_SQRT_2,
        step_down: 0.5,
        slope_cos: std::f64::consts::FRAC_1_SQRT_2,
    }
}

struct ChainWorld {
    unavailable: bool,
    hit: Option<SpatialRayHit>,
    scene: Vec<([f64; 3], [f64; 3])>,
    forward: [f64; 3],
    geometry: Option<HookPullGeometry>,
}

/// Ray-versus-boxes for the synthetic fan fixtures: the first box surface
/// along the ray, answer normal against the entered face.
fn scene_ray(
    scene: &[([f64; 3], [f64; 3])],
    origin: [f64; 3],
    direction: [f64; 3],
    max_distance: f64,
) -> Option<SpatialRayHit> {
    let mut best: Option<SpatialRayHit> = None;
    for &(lo, hi) in scene {
        let mut t_min = 0.0;
        let mut t_max = max_distance;
        let mut normal = [0.0; 3];
        let mut alive = true;
        for axis in 0..3 {
            if direction[axis].abs() < 1e-12 {
                if origin[axis] < lo[axis] || origin[axis] > hi[axis] {
                    alive = false;
                    break;
                }
                continue;
            }
            let inverse = 1.0 / direction[axis];
            let (mut enter, mut leave) = (
                (lo[axis] - origin[axis]) * inverse,
                (hi[axis] - origin[axis]) * inverse,
            );
            if enter > leave {
                std::mem::swap(&mut enter, &mut leave);
            }
            if enter > t_min {
                t_min = enter;
                normal = [0.0; 3];
                normal[axis] = -direction[axis].signum();
            }
            t_max = t_max.min(leave);
            if t_min > t_max {
                alive = false;
                break;
            }
        }
        if !alive || t_min <= 1e-9 {
            continue;
        }
        let point = std::array::from_fn(|axis| origin[axis] + direction[axis] * t_min);
        let hit = SpatialRayHit {
            distance: t_min,
            point,
            normal,
        };
        if best.is_none_or(|best| hit.distance < best.distance) {
            best = Some(hit);
        }
    }
    best
}

impl ChainWorld {
    fn cast(
        &self,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
    ) -> Option<Option<SpatialRayHit>> {
        if self.unavailable {
            return None;
        }
        if self.scene.is_empty() {
            return Some(self.hit);
        }
        Some(scene_ray(&self.scene, origin, direction, max_distance))
    }
}

impl World for ChainWorld {
    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        Some([0.0, 0.0, 0.0])
    }
    fn known_position(&self, _entity: u32) -> Option<[f64; 3]> {
        Some([0.0, 0.0, 0.0])
    }
    fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }
    fn body_hardpoint_position(&self, entity: u32, hardpoint: u64) -> Option<[f64; 3]> {
        (entity == 123 && hardpoint == 0x0dc0_0000_0000_0055).then_some([0.0, 1.55, 0.0])
    }
    fn body_eye_position(&self, _entity: u32) -> Option<[f64; 3]> {
        Some([0.0, 1.55, 0.0])
    }
    fn forward(&self, _entity: u32) -> Option<[f64; 3]> {
        Some(self.forward)
    }
    fn pitch(&self, _entity: u32) -> Option<f64> {
        Some(
            self.forward[1].atan2(
                (self.forward[0] * self.forward[0] + self.forward[2] * self.forward[2]).sqrt(),
            ),
        )
    }
    fn authored_forward(&self, _entity: u32) -> Option<[f64; 3]> {
        Some(self.forward)
    }
    fn body_angle(
        &self,
        entity: u32,
        axis: crate::script_expr::BodyAngle,
    ) -> crate::script_expr::AngleQuery {
        use crate::script_expr::{AngleQuery, BodyAngle};
        match (entity, axis) {
            (0, _) => AngleQuery::Unbound,
            (123, BodyAngle::Pitch) => {
                AngleQuery::Present(self.pitch(entity).unwrap().to_degrees())
            }
            _ => AngleQuery::Unavailable,
        }
    }
    fn owner_collision_category(&self, _site: Site) -> Option<u16> {
        Some(0x08)
    }
    fn hook_pull_geometry(&self, _site: Site) -> Option<HookPullGeometry> {
        self.geometry
    }
    fn profiled_static_ray(
        &self,
        _site: Site,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
        _category: u16,
    ) -> Option<Option<SpatialRayHit>> {
        self.cast(origin, direction, max_distance)
    }
}

fn hook_component(hit: Option<SpatialRayHit>, length: f64) -> Component {
    let root = std::path::PathBuf::from(
        std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("ORPHEUS_RUNTIME_DIR"),
    );
    let packed = std::fs::read(root.join("data/game_graphs_174.json.gz")).unwrap();
    let mut bundle: Json =
        serde_json::from_reader(flate2::read::GzDecoder::new(packed.as_slice())).unwrap();
    bundle["bodies"] = json!({});
    if length > 0.0 {
        for graph in bundle["graphs"]
            .as_array_mut()
            .expect("graphs array")
            .iter_mut()
        {
            if graph["index"].as_u64() != Some(0x005A) {
                continue;
            }
            for node in graph["nodes"]
                .as_array_mut()
                .expect("nodes array")
                .iter_mut()
            {
                if node["class"].as_str() == Some("STU_4BBC5D09") {
                    node["fields"]["m_2AFC083F"]["m_value"] = json!(length as i64);
                }
            }
        }
    }
    let store = GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap();
    let mut world = ChainWorld {
        unavailable: false,
        hit,
        scene: Vec::new(),
        forward: [0.0, 0.0, 1.0],
        geometry: None,
    };
    Component::start(
        Arc::new(store),
        123,
        Variables::new(),
        vec![Root::new(1, 90)],
        0,
        &mut world,
        MAX_INIT_WORK,
    )
    .expect("the isolated hook graph initializes clean")
}

/// The hook press holds logical button 11 (the authored listener, command
/// mask 0x040) and the ability press window 35 (mask 0x008).
fn press_hook(component: &mut Component, world: &mut ChainWorld, frames: u32) {
    for frame in 1..=frames {
        component
            .advance_command(
                Command {
                    frame,
                    buttons: 0x040 | 0x008,
                    ..Command::default()
                },
                world,
            )
            .expect("production press never faults the hook chain");
    }
}

/// The one accepted fall-through on the authored hook chain: a remote
/// presentation attachment (the cosmetic hook line, effect at a hardpoint)
/// that the captured reference also leaves unsupported. It has no gameplay
/// outcome and its client state rides the remote sync ring.
fn presentation_only_diagnostic(diagnostic: &crate::script_init::types::Diagnostic) -> bool {
    matches!(
        &diagnostic.issue,
        crate::script_init::types::Issue::UnsupportedState(class)
            if class == "STU_9932D728"
    ) && !diagnostic.registration_rejected
        && !diagnostic.server_only
}

fn chain_world(hit: Option<SpatialRayHit>) -> ChainWorld {
    ChainWorld {
        unavailable: false,
        hit,
        scene: Vec::new(),
        forward: [0.0, 0.0, 1.0],
        geometry: None,
    }
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_unavailable_recast_does_not_reuse_a_hit() {
    let mut world = chain_world(Some(SpatialRayHit {
        point: [0.0, 1.55, 6.0],
        normal: [0.0, 0.0, -1.0],
        distance: 6.0,
    }));
    let mut component = hook_component(world.hit, 0.0);
    press_hook(&mut component, &mut world, 60);
    let first = &component.snapshot().unwrap().instances[&1];
    assert_eq!(first.variables.get(&435), Some(&Value::Bool(true)));
    let movement = first.states[&1].clone();
    assert!(movement.active);
    component
        .queue_move_result(
            61,
            crate::script_init::MoveToKey {
                instance: 1,
                raw: 1,
                activation: movement.activation,
                start_ms: movement.start_ms,
            },
            crate::script_init::MoveOutcome::Arrived,
        )
        .unwrap();
    component
        .advance_command(
            Command {
                frame: 61,
                ..Command::default()
            },
            &mut world,
        )
        .unwrap();
    assert!(!component.snapshot().unwrap().instances[&1].states[&1].active);
    world.unavailable = true;
    component.begin_state(1, 0, &mut world).unwrap();
    let recast = &component.snapshot().unwrap().instances[&1];
    assert_eq!(recast.variables.get(&435), Some(&Value::Bool(false)));
    assert_eq!(recast.states[&1].activation, movement.activation);
    assert!(!recast.states[&1].active);
    assert_eq!(
        recast.variables.get(&431),
        Some(&Value::Vector([0.0, 1.55, 6.0]))
    );
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_press_with_hit_launches_the_authored_move() {
    let mut world = chain_world(Some(SpatialRayHit {
        point: [0.0, 1.5, 6.0],
        normal: [0.0, 0.0, -1.0],
        distance: 6.0,
    }));
    let mut component = hook_component(world.hit, 0.0);
    press_hook(&mut component, &mut world, 60);
    let snapshot = component
        .snapshot()
        .expect("clean chain keeps the snapshot");
    assert!(
        snapshot
            .diagnostics
            .iter()
            .all(presentation_only_diagnostic),
        "{:?}",
        snapshot.diagnostics
    );
    let states = &snapshot.instances[&1].states;
    let trace = states.get(&0).expect("the trace state materialized");
    assert!(
        !trace.active,
        "the 6 m hit completes within the pressed frames"
    );
    let move_to = states
        .get(&1)
        .expect("the hit branch launches the authored MoveTo state");
    assert!(move_to.active, "the move is in flight");
    let crate::script_init::StateKind::MoveTo { movement } = move_to.kind else {
        panic!("the hit branch owns a movement request");
    };
    let (destination, distance) = movement.expect("the requested destination is carried");
    assert!(destination.iter().all(|axis| axis.is_finite()));
    assert!(distance.is_finite() && distance > 0.0);
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_press_without_hit_never_launches_the_move() {
    let mut world = chain_world(None);
    let mut component = hook_component(None, 0.0);
    press_hook(&mut component, &mut world, 60);
    let snapshot = component
        .snapshot()
        .expect("clean chain keeps the snapshot");
    assert!(
        snapshot
            .diagnostics
            .iter()
            .all(presentation_only_diagnostic),
        "{:?}",
        snapshot.diagnostics
    );
    let states = &snapshot.instances[&1].states;
    assert!(
        states.get(&0).is_some_and(|state| !state.active),
        "the miss path still runs the trace to completion"
    );
    assert!(
        !states.contains_key(&1),
        "the false branch never begins the MoveTo state"
    );
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_press_promotes_a_lip_only_the_doubled_capsule_step_reaches() {
    // The reference CoverageTests pattern ported onto the authored graph
    // with a synthetic 40 m rope (the pack's node length is patched, the
    // graph itself is unspliced): the aim passes the face plane 0.47 m
    // above the 6 m lip of a wall standing 30 m out. The centre ray and
    // one capsule-sized pitch step (0.716 degrees at the rope length)
    // sail past; only the doubled step meets the face under the rim, and
    // the anchor's 0.47 m lateral error — inside one capsule radius at
    // the 30.4 m line — pays the tolerance, so the rim promotes.
    // The reference CoverageTests aim: the centre ray passes the face
    // plane 0.46 m ABOVE the 6 m lip of the wall standing 30 m out, one
    // capsule-sized pitch step (atan(r/reach), reach 40 m) still crosses
    // the plane above the rim, and only the doubled step meets the face
    // 0.30 m under it — inside the one-radius window. The anchor's 0.46 m
    // lateral error against the original aim, measured at its own 30.5 m
    // line, pays the one-radius tolerance, so the rim promotes. (Fixture
    // CoverageTests.measure_ledge_fan: pitch -0.162916; the k=-2 ray.)
    let elevation = f64::atan(4.92 / 30.0);
    let mut world = ChainWorld {
        unavailable: false,
        hit: None,
        scene: vec![
            // the wall's front face stands at z=30; the floor runs right up
            // to it (reference fixture: a wall never stands over a hole)
            ([-30.0, -1.0, -20.0], [30.0, 0.0, 30.0]),
            ([-30.0, 0.0, 30.0], [30.0, 6.0, 50.0]),
        ],
        forward: [0.0, elevation.sin(), elevation.cos()],
        geometry: Some(hook_capsule()),
    };
    let mut component = hook_component(None, 40.0);
    press_hook(&mut component, &mut world, 60);
    let snapshot = component.snapshot().expect("the chain keeps the snapshot");
    let states = &snapshot.instances[&1].states;
    let move_to = states
        .get(&1)
        .expect("the hit branch launches the authored MoveTo state");
    assert!(move_to.active, "the promoted walk is in flight");
    let crate::script_init::StateKind::MoveTo { movement } = move_to.kind else {
        panic!("the promoted hit owns a movement request");
    };
    let (destination, speed) = movement.expect("the attachment destination is carried");
    // grapple.py `request_finish` T-0013: the ROPE sticks on the lip line
    // (published in the anchor var 431/1220); the MOVE walks the body to
    // the validated standing point — a footprint (2r = 0.7 m) behind the
    // lip, so the arriving capsule is fully supported instead of being
    // pushed off the wall.
    for (axis, value) in [(1, 6.0), (2, 31.0)] {
        assert!(
            (destination[axis] - value).abs() <= 1e-2,
            "the walk stands on the cap behind the lip edge, got {destination:?}"
        );
    }
    assert!(speed > 0.0);
    let anchor = snapshot.instances[&1]
        .variables
        .get(&1220)
        .expect("the final attach variable");
    assert_eq!(
        anchor,
        &Value::Vector([0.0, 6.0, 30.0]),
        "the rope sticks on the lip line, not the point the aim sailed past"
    );
}
