use super::{
    payload::{flush, send},
    *,
};
use crate::{
    game_content::MapCatalog,
    pvp_mode::{
        MODE_ASSAULT, MODE_CONTROL, MODE_ESCORT, MODE_HYBRID, PvpObjectiveRuntime,
        START_IMMEDIATELY,
    },
    room_primitives::ActorSample,
    script_wire::Value as ScriptValue,
    world::GAME_MODE_ENTITY,
};

fn advance(scene: &mut PreparedScene, at: f64, actors: &[ActorSample]) {
    let now = Duration::from_secs_f64(at);
    scene.advance_custom_mode(now, [1, 1]).unwrap();
    scene
        .simulation_mut()
        .pvp_objective_mut()
        .unwrap()
        .advance(at, actors);
    scene.advance_custom_mode(now, [1, 1]).unwrap();
}

fn deliver(world: &mut WorldDelivery, scene: &PreparedScene, team: u8, at: f64) {
    for offset in 0..24 {
        let tick = FIRST_TICK + (at * 64.0) as u32 + offset;
        world.present(scene, tick).unwrap();
        world
            .queue_custom_mode_presentation(scene, tick, team, Duration::from_secs_f64(at))
            .unwrap();
        flush(world);
    }
}

fn assert_hud(
    world: &WorldDelivery,
    active: bool,
    remaining: f64,
    fraction: f64,
    stack: u16,
    burn: u16,
) {
    let mode = world.payload_mode_view.as_ref().expect("mounted mode");
    let entity = mode.entity_variables();
    assert_eq!(entity[&0x0d40], ScriptValue::Bool(active));
    assert_eq!(entity[&0x1b3b], ScriptValue::Float(remaining));
    assert_eq!(
        mode.instance_variables(1).unwrap()[&0x1e25],
        ScriptValue::Float(5.0)
    );
    let ScriptValue::Map(context) = &entity[&0x59dc] else {
        panic!("missing HUD map")
    };
    assert!(context.contains(&(ScriptValue::Integer(0x59da), ScriptValue::Float(fraction))));
    assert_eq!(mode.state_payload(1, stack).is_some(), active);
    assert_eq!(mode.state_payload(1, 177).is_some(), active);
    assert_eq!(mode.state_payload(1, burn).is_some(), active);
}

#[test]
#[ignore = "requires the prepared 104319 objective maps, graphs and collision"]
fn prepared_objective_overtime_delivers_hud_countdown_retry_and_late_join() {
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let catalog = MapCatalog::load(root.join("data/game_maps_174.json")).unwrap();
    let assets = SceneAssets::load(&root).unwrap();
    let card = PlayerCard::default();
    for (mode_id, name, hybrid_payload) in [
        (MODE_ASSAULT, "Assault", false),
        (MODE_ESCORT, "Escort", false),
        (MODE_HYBRID, "Hybrid", false),
        (MODE_HYBRID, "Hybrid", true),
        (MODE_CONTROL, "Control", false),
    ] {
        let map = catalog
            .map_catalog()
            .into_iter()
            .filter_map(|listing| catalog.game_map(listing.guid, mode_id, name, false, &[6, 6]))
            .find(|map| {
                map.mode_guid == mode_id
                    && match mode_id {
                        MODE_ESCORT => map.payload_route.is_some(),
                        MODE_HYBRID => map.payload_route.is_some() && map.capture_course.is_some(),
                        _ => map.capture_course.is_some(),
                    }
            })
            .expect("prepared objective course");
        let mut scene = assets.prepare_custom(map, 0, START_IMMEDIATELY).unwrap();
        let start_peer = |scene: &PreparedScene, team| {
            let mut peer = WorldDelivery::new(OWNER);
            peer.start(
                scene,
                &PlayerAppearance {
                    entity: OWNER,
                    slot: 0,
                    team,
                    card: &card,
                },
                FIRST_TICK,
            )
            .unwrap();
            peer
        };
        let mut peer = start_peer(&scene, 1);
        let mut other = start_peer(&scene, 0);
        for at in [0.0, 30.0, 75.0, 90.0] {
            advance(&mut scene, at, &[]);
            deliver(&mut peer, &scene, 1, at);
            deliver(&mut other, &scene, 0, at);
        }
        if hybrid_payload {
            let capture = scene.capture_round().unwrap().current();
            let attacker = ActorSample::living(0x100, 1, capture.spec.position);
            let at = 90.0 + capture.spec.capture_seconds + 0.1;
            advance(&mut scene, at, &[attacker]);
            deliver(&mut peer, &scene, 1, at);
            deliver(&mut other, &scene, 0, at);
        }
        let (touch, position, stack, burn) =
            match scene.simulation_mut().pvp_objective_mut().unwrap() {
                PvpObjectiveRuntime::Assault(round) => {
                    (round.deadline, round.current().spec.position, 131, 136)
                }
                PvpObjectiveRuntime::Escort(round) => {
                    (round.deadline, round.pose().position, 131, 136)
                }
                PvpObjectiveRuntime::Hybrid(round) if hybrid_payload => {
                    let at = round.escort.deadline;
                    round.advance(at - 3.0, &[]);
                    (at, round.escort.pose().position, 131, 136)
                }
                PvpObjectiveRuntime::Hybrid(round) => (
                    round.capture.deadline,
                    round.capture.current().spec.position,
                    131,
                    136,
                ),
                PvpObjectiveRuntime::Control(round) => {
                    let position = round.stages[round.stage()].point.position;
                    round.advance(
                        90.0 + round.stages[round.stage()].point.capture_seconds + 0.1,
                        &[ActorSample::living(0x101, 0, position)],
                    );
                    assert_eq!(round.owner, 0);
                    (round.last_update + 118.8, position, 80, 81)
                }
            };
        let contest = [
            ActorSample::living(0x100, 1, position),
            ActorSample::living(0x101, 0, position),
        ];
        advance(&mut scene, touch - 2.0, &contest);
        advance(&mut scene, touch - 1.0, &[]);
        deliver(&mut peer, &scene, 1, touch - 1.0);
        deliver(&mut other, &scene, 0, touch - 1.0);
        assert_hud(&peer, false, 0.0, 0.0, stack, burn);

        // Control's threshold is derived from floating point score accumulation.
        advance(&mut scene, touch + 0.001, &[]);
        let now = Duration::from_secs_f64(touch + 0.001);
        peer.queue_custom_mode_presentation(&scene, FIRST_TICK, 1, now)
            .unwrap();
        let enter = send(&mut peer, GAME_MODE_ENTITY);
        peer.acknowledge(outcome(Arc::clone(&enter), false), now);
        let retry = send(&mut peer, GAME_MODE_ENTITY);
        assert!(Arc::ptr_eq(&enter, &retry));
        peer.acknowledge(outcome(retry, true), now);
        assert_hud(&peer, true, 5.0, 1.0, stack, burn);
        assert!(peer.payload_cues.contains(&crate::pvp_mode::CUE_OVERTIME));
        deliver(&mut other, &scene, 0, touch + 0.001);
        assert_hud(&other, true, 5.0, 1.0, stack, burn);

        advance(&mut scene, touch + 2.01, &[]);
        deliver(&mut peer, &scene, 1, touch + 2.01);
        assert_hud(&peer, true, 3.0, 0.6, stack, burn);
        let mut late = start_peer(&scene, 0);
        deliver(&mut late, &scene, 0, touch + 2.01);
        assert_hud(&late, true, 3.0, 0.6, stack, burn);

        let exit = if mode_id == MODE_HYBRID && !hybrid_payload {
            let PvpObjectiveRuntime::Hybrid(round) =
                scene.simulation_mut().pvp_objective_mut().unwrap()
            else {
                unreachable!("selected Hybrid course");
            };
            round.capture.points[0].progress = 0.99;
            let at = touch + 3.0;
            advance(&mut scene, at, &contest[..1]);
            assert!(scene.payload_round().unwrap().unlocked_objective());
            at
        } else {
            let at = touch + 5.01;
            advance(&mut scene, at, &[]);
            at
        };
        deliver(&mut peer, &scene, 1, exit);
        assert_hud(&peer, false, 0.0, 0.0, stack, burn);
    }
}
