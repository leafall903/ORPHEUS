use super::{payload::flush, *};
use crate::{
    game_content::MapCatalog,
    pvp_mode::{MODE_CONTROL, PvpObjectiveRuntime, START_IMMEDIATELY, presentation},
    room_primitives::{ActorSample, BarrierKind, MAP_ENTITY_BIT},
    script_wire::Value,
};

fn advance(scene: &mut PreparedScene, seconds: f64, actors: &[ActorSample]) {
    let now = Duration::from_secs_f64(seconds);
    scene.advance_custom_mode(now, [1, 1]).unwrap();
    let Some(PvpObjectiveRuntime::Control(round)) = scene.simulation_mut().pvp_objective_mut()
    else {
        panic!("control runtime not prepared");
    };
    round.advance(seconds, actors);
    scene.advance_custom_mode(now, [1, 1]).unwrap();
}

fn deliver(world: &mut WorldDelivery, scene: &PreparedScene, team: u8, seconds: f64) {
    for offset in 0..20 {
        let tick = FIRST_TICK + offset + (seconds * 64.0) as u32;
        world.present(scene, tick).unwrap();
        world
            .queue_custom_mode_presentation(scene, tick, team, Duration::from_secs_f64(seconds))
            .unwrap();
        flush(world);
    }
}

#[test]
#[ignore = "requires the prepared 104319 Control maps, graphs and collision"]
fn prepared_control_transitions_and_retires_each_native_presenter() {
    let dir = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let catalog = MapCatalog::load(dir.join("data/game_maps_174.json")).unwrap();
    let assets = SceneAssets::load(&dir).unwrap();
    let card = PlayerCard::default();
    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/control-round-v1.json"
    ))
    .unwrap();
    let expected = &contract["expected"];
    let mut maps = 0;
    for listing in catalog.map_catalog() {
        let Some(mut map) = catalog.game_map(listing.guid, MODE_CONTROL, "Control", false, &[6, 6])
        else {
            continue;
        };
        if map.mode_guid != MODE_CONTROL {
            continue;
        }
        let Some(course) = map.capture_course.as_mut() else {
            continue;
        };
        assert_eq!(course.control.as_ref().unwrap().healing_rooms.len(), 6);
        assert_eq!(course.spawn_candidates.len(), 3);
        assert!(
            course
                .spawn_candidates
                .iter()
                .flatten()
                .all(|spawns| !spawns.is_empty())
        );
        assert!(
            map.objectives
                .iter()
                .all(|point| !point.capture_boxes.is_empty() && !point.outlines.is_empty())
        );
        course.control.as_mut().unwrap().rules.fixed_stage = Some(2);
        let goal = &map.objectives[2];
        let entity = MAP_ENTITY_BIT | goal.placeable.unwrap();
        let position = goal.capture_boxes[0][4..7].try_into().unwrap();
        let doors: Vec<_> = course
            .barriers
            .iter()
            .filter(|entry| {
                entry.kind == BarrierKind::Door && entry.rooms.iter().any(|&(stage, _)| stage == 2)
            })
            .map(|entry| entry.entity)
            .collect();
        assert!(!doors.is_empty());
        let mut scene = assets.prepare_custom(map, 0, START_IMMEDIATELY).unwrap();
        assert_eq!(scene.control_round().unwrap().stage(), 2);
        let assert_doors = |world: &WorldDelivery, index: usize| {
            for &door in &doors {
                let view = &world.objective_views[&door];
                let pose = &view.door_state.as_ref().unwrap().pose;
                assert_eq!(
                    format!("{pose:?}"),
                    expected["opening_door_poses"][index].as_str().unwrap()
                );
                if index < 2 {
                    assert_eq!(
                        view.snapshot.entity_variables()[&0x22a5],
                        Value::Bool(false)
                    );
                    assert_eq!(
                        view.snapshot.state_payload(1, 1).unwrap()["counter"],
                        Value::Integer(2)
                    );
                }
            }
        };
        let mut world = WorldDelivery::new(OWNER);
        world
            .start(
                &scene,
                &PlayerAppearance {
                    entity: OWNER,
                    slot: 0,
                    team: 0,
                    card: &card,
                },
                FIRST_TICK,
            )
            .unwrap();
        flush(&mut world);
        advance(&mut scene, 0.0, &[]);
        deliver(&mut world, &scene, 0, 0.0);
        assert_doors(&world, 0);
        advance(&mut scene, 20.0, &[]);
        deliver(&mut world, &scene, 0, 20.0);
        advance(&mut scene, 30.0, &[]);
        deliver(&mut world, &scene, 0, 30.0);
        assert_doors(&world, 1);
        let objective = &world.objective_views[&entity].snapshot;
        assert_eq!(
            objective.instance_variables(1).unwrap()[&0x3f75],
            Value::Asset(0x0030_2fdb)
        );
        assert_eq!(
            objective.instance_variables(1).unwrap()[&0x27ac],
            Value::Bool(true)
        );
        assert_eq!(
            world.payload_mode_view.as_ref().unwrap().entity_variables()[&0x8006],
            Value::Array(
                scene
                    .map()
                    .capture_course
                    .as_ref()
                    .unwrap()
                    .spawn_candidates[2]
                    .iter()
                    .map(|spawns| Value::Array(
                        spawns
                            .iter()
                            .map(|id| Value::Entity(MAP_ENTITY_BIT | id))
                            .collect()
                    ))
                    .collect()
            )
        );

        for time in [55.0, 60.0, 87.0, 90.0] {
            advance(&mut scene, time, &[]);
            deliver(&mut world, &scene, 0, time);
        }
        assert_doors(&world, 3);
        let objective = &world.objective_views[&entity].snapshot;
        assert_eq!(
            objective.instance_variables(1).unwrap()[&0x27ac],
            Value::Bool(false)
        );
        let actors = [ActorSample::living(0x100, 0, position)];
        for (time, occupants) in [(98.0, &actors[..]), (216.8, &[][..])] {
            advance(&mut scene, time, occupants);
            deliver(&mut world, &scene, 0, time);
        }
        let before = world
            .payload_mode_view
            .as_ref()
            .unwrap()
            .instance_variables(1)
            .unwrap()[&0x1330]
            .clone();
        assert_eq!(before, Value::Float(99.0));
        let old_audio = world.payload_audio.unwrap();
        let old_mode = world.payload_mode_view.as_ref().unwrap().clone();
        advance(&mut scene, 218.0, &[]);
        deliver(&mut world, &scene, 0, 218.0);
        let round = scene.control_round().unwrap();
        let actions = presentation::control::actions(
            &old_audio,
            &world.payload_audio.unwrap(),
            &old_mode,
            round,
            0.0,
            0.0,
        );
        let expected_actions = expected["round_end_mode_actions"].as_array().unwrap();
        assert_eq!(
            actions,
            expected_actions
                .iter()
                .map(|item| (1, item.as_u64().unwrap() as u16))
                .collect::<Vec<_>>()
        );
        let result = world.payload_mode_view.as_ref().unwrap();
        assert!(!result.has_instance(4));
        assert!(result.instance_states(1).unwrap().contains(&59));
        assert_eq!(
            result.instance_variables(1).unwrap()[&0x1332],
            Value::Integer(-1)
        );
        assert_eq!(
            world.objective_views[&entity]
                .snapshot
                .instance_variables(1)
                .unwrap()[&0x3f75],
            Value::Null
        );
        advance(&mut scene, 224.99, &[]);
        deliver(&mut world, &scene, 0, 224.99);
        assert!(!world.payload_mode_view.as_ref().unwrap().has_instance(4));
        advance(&mut scene, 225.0, &[]);
        deliver(&mut world, &scene, 0, 225.0);
        let result = world.payload_mode_view.as_ref().unwrap();
        assert!(result.has_instance(4));
        assert_eq!(
            result
                .state_payload(1, 59)
                .and_then(|payload| payload.get("which")),
            Some(&Value::Bool(false))
        );

        advance(&mut scene, 232.99, &[]);
        deliver(&mut world, &scene, 0, 232.99);
        assert!(world.payload_mode_view.as_ref().unwrap().has_instance(4));
        for time in [233.0, 252.0, 253.0, 263.0, 293.0] {
            advance(&mut scene, time, &[]);
            deliver(&mut world, &scene, 0, time);
            let snapshot = world.payload_mode_view.as_ref().unwrap();
            assert!(!snapshot.has_instance(4));
            if time == 253.0 {
                let effect =
                    crate::accounts::parse_guid(expected["next_round_music"].as_str().unwrap())
                        .unwrap();
                assert_eq!(
                    snapshot
                        .instance_variables(presentation::CHECKPOINT_SOUND_INSTANCE)
                        .unwrap()[&0x6ede],
                    Value::Asset(effect)
                );
            } else {
                assert!(!snapshot.has_instance(presentation::CHECKPOINT_SOUND_INSTANCE));
            }
        }
        for (time, occupants) in [(301.0, &actors[..]), (419.8, &[][..]), (421.0, &[][..])] {
            advance(&mut scene, time, occupants);
            deliver(&mut world, &scene, 0, time);
        }
        let result = world.payload_mode_view.as_ref().unwrap();
        assert_eq!(scene.control_round().unwrap().winner, Some(0));
        assert_eq!(result.instance_variables(1).unwrap()[&0x1330], before);
        assert!(result.instance_states(2).unwrap().contains(&42));
        assert!(result.has_instance(6));
        maps += 1;
    }
    assert_eq!(maps, 6);
}
