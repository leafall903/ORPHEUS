use super::{
    payload::{flush, send},
    *,
};
use crate::{
    game_content::MapCatalog,
    pvp_mode::{MODE_ASSAULT, PvpObjectiveRuntime, START_IMMEDIATELY},
    room_primitives::{ActorSample, MAP_ENTITY_BIT},
    script_wire::Value as ScriptValue,
};

fn advance(scene: &mut PreparedScene, at: u64, actors: &[ActorSample]) {
    let duration = Duration::from_secs(at);
    scene.advance_custom_mode(duration, [1, 1]).unwrap();
    let Some(PvpObjectiveRuntime::Assault(round)) = scene.simulation_mut().pvp_objective_mut()
    else {
        panic!("missing capture course");
    };
    round.advance(duration.as_secs_f64(), actors);
    scene.advance_custom_mode(duration, [1, 1]).unwrap();
}

fn deliver(world: &mut WorldDelivery, scene: &PreparedScene, team: u8, at: u64) {
    for offset in 0..10 {
        world.present(scene, FIRST_TICK + offset).unwrap();
        world
            .queue_custom_mode_presentation(
                scene,
                FIRST_TICK + offset,
                team,
                Duration::from_secs(at),
            )
            .unwrap();
        flush(world);
    }
}

#[test]
#[ignore = "requires the prepared 104319 Hybrid maps, scripts and collision"]
fn prepared_hybrid_retires_capture_before_switching_to_the_payload() {
    use crate::pvp_mode::MODE_HYBRID;
    use crate::room_primitives::BarrierKind;

    let dir = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let catalog = MapCatalog::load(dir.join("data/game_maps_174.json")).unwrap();
    let assets = SceneAssets::load(&dir).unwrap();
    let card = PlayerCard::default();
    let mut count = 0;
    let mut scripted_starts = 0;
    let mut signed_rooms = 0;
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/hybrid-round-v1.json"
    ))
    .unwrap();
    let sign_state = &fixture["expected"]["restricted_sign"];
    let assert_signs = |world: &WorldDelivery, signs: &[u32]| {
        for entity in signs {
            assert!(world.identity_ready(*entity));
            let sign = &world.objective_views[entity].snapshot;
            let flags = sign.instance_variables(1).unwrap();
            for (key, field) in [(0x0877, "hide_blue"), (0x0878, "hide_red")] {
                assert_eq!(
                    flags[&key],
                    ScriptValue::Bool(sign_state[field].as_bool().unwrap())
                );
            }
            assert_eq!(
                sign.entity_variables()[&0x0786],
                ScriptValue::Bool(sign_state["disabled"].as_bool().unwrap())
            );
            let state = sign_state["visible_state"].as_u64().unwrap() as u16;
            assert_eq!(
                sign.instance_states(1).unwrap(),
                [state].into_iter().collect()
            );
        }
    };
    for listing in catalog.map_catalog() {
        let Some(map) = catalog.game_map(listing.guid, MODE_HYBRID, "Hybrid", false, &[6, 6])
        else {
            continue;
        };
        let Some(route) = &map.payload_route else {
            continue;
        };
        let signs: Vec<_> = route
            .barriers
            .iter()
            .filter(|entry| {
                entry.kind == BarrierKind::RestrictedSign && entry.rooms.contains(&(2, 1))
            })
            .map(|entry| entry.entity)
            .collect();
        if !signs.is_empty() {
            assert_eq!(signs.len(), 3);
            signed_rooms += 1;
        }
        assert_eq!(route.stage_seconds, [180.0, 120.0]);
        assert_eq!(route.later_spawns.len(), 2);
        assert!(route.later_spawns.iter().all(|spawns| {
            [0, 1]
                .into_iter()
                .all(|team| spawns.iter().any(|spawn| spawn.team == Some(team)))
        }));
        let point = map
            .objectives
            .iter()
            .find(|goal| goal.kind == "point")
            .unwrap();
        let point_id = MAP_ENTITY_BIT | point.placeable.unwrap();
        let rim_id = MAP_ENTITY_BIT | point.outline.unwrap();
        let point_position = point.capture_boxes[0][4..7].try_into().unwrap();
        let capture_seconds = point.capture_seconds.unwrap().ceil() as u64;
        let cart = map
            .objectives
            .iter()
            .find(|goal| goal.kind == "payload")
            .unwrap();
        let cart_id = MAP_ENTITY_BIT | cart.placeable.unwrap();
        let unlock_delay = cart.unlock_delay;
        let unlock_duration = cart.unlock_seconds;
        let auto_drive = cart.auto_drive;
        let mut scene = assets.prepare_custom(map, 0, START_IMMEDIATELY).unwrap();
        if !signs.is_empty() {
            let graph = scene.payload_presentation().unwrap().0.graph(0xf6).unwrap();
            let state = sign_state["visible_state"].as_u64().unwrap() as usize;
            let slot = sign_state["remote_slot"].as_u64().unwrap() as usize;
            assert_eq!(
                graph.remote_positions()[slot],
                graph.state(state).unwrap().pos
            );
        }
        let mut attack = WorldDelivery::new(OWNER);
        let mut defend = WorldDelivery::new(OWNER);
        for (world, team) in [(&mut attack, 1), (&mut defend, 0)] {
            world
                .start(
                    &scene,
                    &PlayerAppearance {
                        entity: OWNER,
                        slot: 0,
                        team,
                        card: &card,
                    },
                    FIRST_TICK,
                )
                .unwrap();
            flush(world);
            assert!(world.identity_ready(cart_id));
            world
                .queue_custom_mode_presentation(&scene, FIRST_TICK, team, Duration::ZERO)
                .unwrap();
            assert_eq!(world.mode.latest_data(), 0);
            let ready = world.ready();
            let initial_pose = ready
                .iter()
                .find_map(|record| {
                    (record.record.update.entity == cart_id)
                        .then_some(record.record.update.movement)
                        .flatten()
                })
                .expect("locked cart pose before the mode baseline");
            assert_eq!(
                initial_pose.position,
                scene.payload_round().unwrap().pose().position
            );
            assert_eq!(initial_pose.velocity, [0.0; 3]);
            flush(world);
            deliver(world, &scene, team, 0);
            assert!(signs.iter().all(|&entity| !world.identity_ready(entity)));
        }
        let step = |scene: &mut PreparedScene, at: u64, actors: &[ActorSample]| {
            scene
                .advance_custom_mode(Duration::from_secs(at), [1, 1])
                .unwrap();
            let Some(PvpObjectiveRuntime::Hybrid(round)) =
                scene.simulation_mut().pvp_objective_mut()
            else {
                panic!("missing authored hybrid round");
            };
            round.advance(at as f64, actors);
            scene
                .advance_custom_mode(Duration::from_secs(at), [1, 1])
                .unwrap();
        };
        step(&mut scene, 0, &[]);
        step(&mut scene, 30, &[]);
        for (world, team) in [(&mut attack, 1), (&mut defend, 0)] {
            deliver(world, &scene, team, 30);
            assert!(world.identity_ready(rim_id));
            assert_eq!(
                world.objective_views[&cart_id].snapshot.entity_variables()[&0x02bb],
                ScriptValue::Bool(false)
            );
            let values = world.payload_mode_view.as_ref().unwrap().entity_variables();
            let ScriptValue::Array(goals) = &values[&0x3058] else {
                panic!("missing hybrid goals")
            };
            assert_eq!(goals.len(), 3);
            assert_eq!(goals[0], ScriptValue::Entity(point_id));
        }

        step(&mut scene, 75, &[]);
        assert_eq!(scene.payload_round().unwrap().deadline, 375.0);
        let attacker = ActorSample::living(0xa000_8500, 1, point_position);
        let defender = ActorSample::living(0xa000_8600, 0, point_position);
        step(&mut scene, 76, &[attacker, defender]);
        assert_eq!(scene.capture_round().unwrap().current().progress, 0.0);
        deliver(&mut attack, &scene, 1, 76);
        deliver(&mut defend, &scene, 0, 76);
        let captured_at = 76 + capture_seconds;
        step(&mut scene, captured_at, &[attacker]);
        assert_eq!(scene.payload_round().unwrap().deadline, 555.0);
        attack
            .queue_custom_mode_presentation(&scene, FIRST_TICK, 1, Duration::from_secs(captured_at))
            .unwrap();
        let retired = send(&mut attack, point_id);
        let capture_mode = attack.mode.latest_data();
        attack.acknowledge(outcome(Arc::clone(&retired), false), Duration::ZERO);
        attack
            .queue_custom_mode_presentation(&scene, FIRST_TICK, 1, Duration::from_secs(captured_at))
            .unwrap();
        assert_eq!(attack.mode.latest_data(), capture_mode);
        let retried = send(&mut attack, point_id);
        assert!(Arc::ptr_eq(&retired, &retried));
        attack.acknowledge(outcome(retried, true), Duration::ZERO);
        flush(&mut attack);

        for (world, team) in [(&mut attack, 1), (&mut defend, 0)] {
            deliver(world, &scene, team, captured_at);
            assert!(!world.identity_ready(rim_id));
            let point = &world.objective_views[&point_id].snapshot;
            assert_eq!(
                point.entity_variables()[&0x28a0],
                ScriptValue::Array(vec![ScriptValue::Entity(attacker.entity)])
            );
            assert!(
                !world.objective_views[&point_id]
                    .capture_state
                    .unwrap()
                    .waypoint
            );
            assert_eq!(world.payload_audio.unwrap().stage, 1);
            assert!(world.payload_progress_bound);
            assert!(
                world.objective_views[&cart_id]
                    .snapshot
                    .instance_states(1)
                    .unwrap()
                    .contains(&137)
            );
        }

        let unlocking_at = captured_at + unlock_delay.ceil() as u64;
        step(&mut scene, unlocking_at, &[]);
        assert_eq!(scene.payload_round().unwrap().mover.distance, 0.0);
        let mut late = WorldDelivery::new(OWNER);
        late.start(
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
        deliver(&mut late, &scene, 0, unlocking_at);
        assert!(!late.identity_ready(rim_id));
        assert!(late.objective_views[&point_id].capture_state.is_none());
        let view = &late.objective_views[&cart_id].snapshot;
        assert!(view.instance_states(1).unwrap().contains(&21));
        assert_eq!(
            view.instance_variables(1).unwrap()[&0x0854],
            ScriptValue::Bool(true)
        );
        assert_eq!(
            view.instance_variables(1).unwrap()[&0x30a6],
            ScriptValue::Integer(1)
        );
        assert_eq!(late.payload_audio.unwrap().stage, 1);

        let moving_at = captured_at + (unlock_delay + unlock_duration).ceil() as u64;
        step(&mut scene, moving_at + 1, &[]);
        let moved = scene.payload_round().unwrap().mover.distance;
        if auto_drive {
            assert!(moved > 0.0);
            scripted_starts += 1;
        } else {
            assert_eq!(moved, 0.0);
        }

        // Both checkpoints are reached by moving through their real rail nodes.
        let mut at = moving_at + 1;
        for stage in 1..=2 {
            while scene.payload_round().unwrap().stage < stage {
                at += 1;
                assert!(
                    at < moving_at + 400,
                    "payload failed to reach checkpoint in {}",
                    listing.name
                );
                let position = scene.payload_round().unwrap().pose().position;
                step(
                    &mut scene,
                    at,
                    &[ActorSample::living(attacker.entity, 1, position)],
                );
                if scene.payload_round().unwrap().gate_impact_started.is_some() && stage == 2 {
                    deliver(&mut attack, &scene, 1, at);
                }
            }
            deliver(&mut attack, &scene, 1, at);
            deliver(&mut defend, &scene, 0, at);
            if stage == 1 {
                assert_eq!(scene.payload_round().unwrap().deadline, 675.0);
                assert_eq!(attack.payload_audio.unwrap().stage, 2);
                assert_eq!(defend.payload_audio.unwrap().stage, 2);
                if !signs.is_empty() {
                    assert!(signs.iter().all(|&entity| !attack.identity_ready(entity)));
                    let now = Duration::from_secs(at);
                    let change_team =
                        |world: &mut WorldDelivery, scene: &mut PreparedScene, team| {
                            world.change_viewer_team(team, scene.simulation_mut());
                            world.queue_player_seat(
                                crate::match_scene::player_seat_update(OWNER, team, 0, false, true)
                                    .unwrap(),
                            );
                        };
                    change_team(&mut attack, &mut scene, 0);
                    attack
                        .queue_custom_mode_presentation(&scene, FIRST_TICK, 0, now)
                        .unwrap();
                    assert!(
                        attack
                            .ready()
                            .iter()
                            .all(|record| !signs.contains(&record.record.update.entity))
                    );
                    flush(&mut attack);

                    attack
                        .queue_custom_mode_presentation(&scene, FIRST_TICK, 0, now)
                        .unwrap();
                    let intro = attack
                        .ready()
                        .into_iter()
                        .find(|record| record.record.update.entity == signs[0])
                        .unwrap();
                    assert!(matches!(
                        intro.record.update.operation,
                        Some(Operation::Placeable(_))
                    ));
                    let index = intro.index;
                    let first = intro.tracked();
                    attack.sent(&[index]);
                    attack.acknowledge(outcome(Arc::clone(&first), false), now);
                    assert!(
                        attack
                            .ready()
                            .iter()
                            .any(|record| Arc::ptr_eq(&first, &record.tracked()))
                    );
                    deliver(&mut attack, &scene, 0, at);
                    assert_signs(&attack, &signs);
                    assert_signs(&defend, &signs);

                    change_team(&mut attack, &mut scene, 1);
                    deliver(&mut attack, &scene, 1, at);
                    assert!(signs.iter().all(|&entity| !attack.identity_ready(entity)));
                    assert_signs(&defend, &signs);
                }
            }
        }
        assert_eq!(attack.payload_audio.unwrap().winner, Some(1));
        assert_eq!(defend.payload_audio.unwrap().phase, MatchPhase::Complete);
        assert_eq!(scene.payload_round().unwrap().stage, 2);
        assert!(signs.iter().all(|&entity| !defend.identity_ready(entity)));
        count += 1;
    }
    assert_eq!((count, scripted_starts), (10, 6));
    assert_eq!(signed_rooms, 2);
}

fn room_shapes_at(scene: &mut PreparedScene, at: u64) -> usize {
    let (simulation, queries) = scene.bot_simulation();
    simulation
        .finish_tick(
            &queries,
            FIRST_TICK + at as u32 * 60,
            Duration::from_secs(at),
        )
        .unwrap();
    simulation.payload_contact_world().unwrap().shapes().len()
}

#[test]
#[ignore = "requires the prepared 104319 Assault maps, scripts and collision"]
fn prepared_capture_course_acknowledges_point_switch_and_late_join() {
    let dir = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let catalog = MapCatalog::load(dir.join("data/game_maps_174.json")).unwrap();
    let assets = SceneAssets::load(&dir).unwrap();
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/assault-round-v1.json"
    ))
    .unwrap();
    let waypoint = u64::from_str_radix(
        fixture["presentation"]["waypoint_type"]
            .as_str()
            .unwrap()
            .trim_start_matches("0x"),
        16,
    )
    .unwrap();
    let card = PlayerCard::default();
    let mut maps = 0;
    for listing in catalog.map_catalog() {
        let Some(map) = catalog.game_map(listing.guid, MODE_ASSAULT, "Assault", false, &[6, 6])
        else {
            continue;
        };
        let Some(course) = &map.capture_course else {
            continue;
        };
        assert!(!course.room_models.is_empty());
        assert!(!course.later_spawns[0].is_empty());
        let points: [crate::game_content::MapObjective; 2] =
            map.objectives.clone().try_into().unwrap();
        assert!(
            points
                .iter()
                .all(|point| !point.capture_boxes.is_empty() && point.decay_seconds.is_some())
        );
        let ids = points
            .each_ref()
            .map(|point| MAP_ENTITY_BIT | point.placeable.unwrap());
        let rims = points
            .each_ref()
            .map(|point| MAP_ENTITY_BIT | point.outline.unwrap());
        let mut scene = assets.prepare_custom(map, 0, START_IMMEDIATELY).unwrap();
        assert!(scene.simulation().payload_contact_world().is_some());
        let mut attack = WorldDelivery::new(OWNER);
        let mut defend = WorldDelivery::new(OWNER);
        for (world, team) in [(&mut attack, 1), (&mut defend, 0)] {
            world
                .start(
                    &scene,
                    &PlayerAppearance {
                        entity: OWNER,
                        slot: 0,
                        team,
                        card: &card,
                    },
                    FIRST_TICK,
                )
                .unwrap();
            flush(world);
            world
                .queue_custom_mode_presentation(&scene, FIRST_TICK, team, Duration::ZERO)
                .unwrap();
            assert_eq!(world.mode.latest_data(), 0);
            let descriptor = send(world, ids[0]);
            world.acknowledge(outcome(Arc::clone(&descriptor), false), Duration::ZERO);
            world
                .queue_custom_mode_presentation(&scene, FIRST_TICK, team, Duration::ZERO)
                .unwrap();
            let retried = send(world, ids[0]);
            assert!(Arc::ptr_eq(&descriptor, &retried));
            world.acknowledge(outcome(retried, true), Duration::ZERO);
            flush(world);
            deliver(world, &scene, team, 0);
            assert_eq!(world.custom_mode_view.unwrap().0, MatchPhase::Waiting);
            assert!(
                !world.objective_views[&ids[0]]
                    .capture_state
                    .unwrap()
                    .waypoint
            );
        }
        advance(&mut scene, 0, &[]);
        advance(&mut scene, 30, &[]);
        let closed_room_shapes = room_shapes_at(&mut scene, 30);
        for (world, team, text) in [(&mut attack, 1, 0x10c4), (&mut defend, 0, 0x10c5)] {
            deliver(world, &scene, team, 30);
            let state = world.objective_views[&ids[0]]
                .snapshot
                .instance_variables(1)
                .unwrap();
            assert_eq!(state[&0x27ac], ScriptValue::Bool(true));
            assert_eq!(state[&0x3f75], ScriptValue::Asset(waypoint));
            assert_eq!(state[&0x2f61], ScriptValue::Integer(3));
            assert!(world.identity_ready(rims[0]));
            assert!(!world.identity_ready(rims[1]));
            assert_eq!(
                world.payload_mode_view.as_ref().unwrap().entity_variables()[&0x0783],
                ScriptValue::Asset(0x0de0_0000_0000_0000 | text)
            );
        }
        advance(&mut scene, 75, &[]);
        let attacker = ActorSample::living(
            0xa000_8500,
            1,
            points[0].capture_boxes[0][4..7].try_into().unwrap(),
        );
        let a_seconds = points[0].capture_seconds.unwrap() as u64;
        let b_seconds = points[1].capture_seconds.unwrap() as u64;
        for segment in [1, 2] {
            let at = 75 + (a_seconds * segment).div_ceil(3);
            advance(&mut scene, at, &[attacker]);
            for (world, team) in [(&mut attack, 1), (&mut defend, 0)] {
                deliver(world, &scene, team, at);
                assert!(world.payload_progress_bound);
                let point = &world.objective_views[&ids[0]].snapshot;
                assert_eq!(
                    point.instance_variables(1).unwrap()[&0x27ac],
                    ScriptValue::Bool(false)
                );
                let fields = point.entity_variables();
                assert_eq!(
                    fields[&0x266d],
                    ScriptValue::Array(vec![ScriptValue::Entity(attacker.entity)])
                );
                let ScriptValue::Float(progress) = fields[&0x268a] else {
                    panic!("missing progress")
                };
                assert!(progress + 1e-9 >= segment as f64 / 3.0);
            }
        }
        assert!(room_shapes_at(&mut scene, 75 + (a_seconds * 2).div_ceil(3)) < closed_room_shapes);
        let switch_at = 75 + a_seconds;
        advance(&mut scene, switch_at, &[attacker]);
        assert_eq!(scene.capture_round().unwrap().stage, 1);
        assert_eq!(scene.capture_round().unwrap().deadline, 495.0);
        attack
            .queue_custom_mode_presentation(&scene, FIRST_TICK, 1, Duration::from_secs(switch_at))
            .unwrap();
        let retire = send(&mut attack, ids[0]);
        attack.acknowledge(outcome(Arc::clone(&retire), false), Duration::ZERO);
        attack
            .queue_custom_mode_presentation(&scene, FIRST_TICK, 1, Duration::from_secs(switch_at))
            .unwrap();
        assert!(
            !attack.objective_views[&ids[1]]
                .capture_state
                .unwrap()
                .waypoint
        );
        let retry = send(&mut attack, ids[0]);
        assert!(Arc::ptr_eq(&retire, &retry));
        attack.acknowledge(outcome(retry, true), Duration::ZERO);
        flush(&mut attack);
        deliver(&mut attack, &scene, 1, switch_at);
        deliver(&mut defend, &scene, 0, switch_at);
        let mut late = WorldDelivery::new(OWNER);
        late.start(
            &scene,
            &PlayerAppearance {
                entity: OWNER,
                slot: 0,
                team: 1,
                card: &card,
            },
            FIRST_TICK,
        )
        .unwrap();
        deliver(&mut late, &scene, 1, switch_at);
        for world in [&attack, &defend, &late] {
            assert_eq!(
                world.payload_mode_view.as_ref().unwrap().entity_variables()[&0x2937],
                ScriptValue::Entity(ids[1])
            );
            assert!(
                !world.objective_views[&ids[0]]
                    .capture_state
                    .unwrap()
                    .waypoint
            );
            assert!(
                world.objective_views[&ids[0]]
                    .capture_state
                    .unwrap()
                    .completed
            );
            assert!(world.objective_views[&ids[1]].capture_state.unwrap().active);
            assert!(!world.identity_ready(rims[0]));
            assert!(world.identity_ready(rims[1]));
        }
        let attacker = ActorSample::living(
            attacker.entity,
            1,
            points[1].capture_boxes[0][4..7].try_into().unwrap(),
        );
        advance(&mut scene, switch_at + b_seconds, &[attacker]);
        for (world, team) in [(&mut attack, 1), (&mut defend, 0), (&mut late, 1)] {
            deliver(world, &scene, team, switch_at + b_seconds);
            assert_eq!(world.custom_mode_view.unwrap().0, MatchPhase::Complete);
            assert_eq!(
                world.payload_mode_view.as_ref().unwrap().entity_variables()[&0x1d65],
                ScriptValue::Integer(1)
            );
            assert_eq!(
                world.payload_mode_view.as_ref().unwrap().entity_variables()[&0x3101],
                ScriptValue::Array(vec![ScriptValue::Integer(0), ScriptValue::Integer(2)])
            );
            assert!(
                !world.objective_views[&ids[1]]
                    .capture_state
                    .unwrap()
                    .waypoint
            );
            assert!(!world.identity_ready(rims[1]));
        }
        maps += 1;
    }
    assert_eq!(maps, 6);
}
