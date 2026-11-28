use super::*;
use crate::{
    game_content::MapCatalog,
    game_instance::RETAIL_QUANTA,
    pvp_mode::{MODE_ESCORT, PvpObjectiveRuntime, START_IMMEDIATELY},
    room_primitives::{ActorSample, MAP_ENTITY_BIT},
};

pub(super) fn send(world: &mut WorldDelivery, entity: u32) -> Arc<Record> {
    let ready = world
        .ready()
        .into_iter()
        .find(|ready| ready.record.update.entity == entity && ready.record.update.chunk.is_some())
        .expect("objective chunk ready");
    let record = ready.tracked();
    world.sent(&[ready.index]);
    record
}

pub(super) fn flush(world: &mut WorldDelivery) {
    let ready = world.ready();
    let records: Vec<_> = ready.iter().map(|record| record.tracked()).collect();
    let indices: Vec<_> = ready.iter().map(|record| record.index).collect();
    world.sent(&indices);
    for record in records {
        world.acknowledge(outcome(record, true), Duration::ZERO);
    }
}

#[test]
#[ignore = "requires the prepared 104319 Escort maps, scripts and collision"]
fn prepared_payload_uses_authored_routes_and_acknowledged_objective_frames() {
    use crate::script_wire::Value as ScriptValue;

    let case: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/escort-assembly-seat-pose-v1.json"
    ))
    .unwrap();
    let headings = &case["expected"]["setup_heading"];
    let setup_heading = |team: &str| {
        ScriptValue::Asset(crate::accounts::parse_guid(headings[team].as_str().unwrap()).unwrap())
    };
    let attack_member = case["expected"]["round_context_attack_member"]
        .as_i64()
        .unwrap();
    let seat_fields = &case["expected"]["live_seat"];
    let dir = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let catalog = MapCatalog::load(dir.join("data/game_maps_174.json")).unwrap();
    let assets = SceneAssets::load(&dir).unwrap();
    let mut count = 0;
    let mut slopes = 0;
    for listing in catalog.map_catalog() {
        let Some(map) = catalog.game_map(listing.guid, MODE_ESCORT, "Escort", false, &[6, 6])
        else {
            continue;
        };
        let Some(route) = &map.payload_route else {
            continue;
        };
        assert!(route.nodes.len() > route.stage_seconds.len() + 1);
        assert_eq!(route.stage_seconds, [240.0, 150.0, 90.0]);
        let mut scene = assets.prepare_custom(map, 0, START_IMMEDIATELY).unwrap();
        let round = scene.payload_round().unwrap();
        let cart = MAP_ENTITY_BIT | round.payload_placeable.unwrap();
        let checkpoint = MAP_ENTITY_BIT | round.checkpoints[0].1;
        let contacts = scene.simulation().payload_contact_world().unwrap();
        assert!(contacts.layer_start() > 0);
        assert!(contacts.shape(contacts.layer_start() as u32).is_some());
        let card = PlayerCard::default();
        let mut world = WorldDelivery::new(OWNER);
        let mut tick = FIRST_TICK;
        let mut advance = |world: &mut WorldDelivery, scene: &PreparedScene, now| {
            tick += 1;
            world
                .queue_custom_mode_presentation(scene, tick, 1, Duration::from_secs(now))
                .unwrap();
        };
        let viewer = PlayerAppearance {
            entity: OWNER,
            slot: 0,
            team: 1,
            card: &card,
        };
        world.start(&scene, &viewer, FIRST_TICK).unwrap();
        advance(&mut world, &scene, 0);
        assert!(world.objective_views.is_empty());
        flush(&mut world);
        advance(&mut world, &scene, 0);
        assert_eq!(world.objective_views.len(), 3);
        assert_eq!(world.mode.latest_data(), 0);
        let first = send(&mut world, checkpoint);
        world.acknowledge(outcome(Arc::clone(&first), false), Duration::ZERO);
        advance(&mut world, &scene, 0);
        let retry = send(&mut world, checkpoint);
        assert!(Arc::ptr_eq(&first, &retry));
        world.acknowledge(outcome(retry, true), Duration::ZERO);
        flush(&mut world);
        advance(&mut world, &scene, 0);
        assert_eq!(world.mode.latest_data(), 1);
        assert!(!world.objective_views.contains_key(&cart));
        flush(&mut world);
        advance(&mut world, &scene, 0);
        assert_eq!(world.objective_views[&cart].stream.latest_data(), 1);
        flush(&mut world);
        world.present(&scene, FIRST_TICK).unwrap();
        flush(&mut world);
        scene.advance_custom_mode(Duration::ZERO, [0, 1]).unwrap();
        advance(&mut world, &scene, 0);
        flush(&mut world);
        assert_eq!(world.custom_mode_view.unwrap().0, MatchPhase::Assemble);
        let mut reopen = pick_hero(OWNER, 0);
        let hex = case["input"]["reopen_pick_parameters"].as_str().unwrap();
        reopen.fields.insert(
            0x80,
            Value::Blob(
                (0..hex.len())
                    .step_by(2)
                    .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
                    .collect(),
            ),
        );
        let first_hero = 0x02e0_0000_0000_006e;
        let replacement = 0x02e0_0000_0000_0002;
        for (message, confirmed) in [
            (pick_hero(OWNER, first_hero), Some(first_hero)),
            (reopen, None),
            (pick_hero(OWNER, replacement), Some(replacement)),
        ] {
            world.queue_game_message(FIRST_TICK + 16, Duration::ZERO, message);
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap();
            assert!(world.active_body_id().is_none());
            assert!(world.select_open);
            assert_eq!(
                world.assembly_choice.filter(|_| world.assembly_confirmed),
                confirmed
            );
            flush(&mut world);
        }
        scene
            .advance_custom_mode(Duration::from_secs(30), [0, 1])
            .unwrap();
        advance(&mut world, &scene, 30);
        world
            .advance_assembly_selection(
                &mut scene,
                &viewer,
                FIRST_TICK + 16,
                RETAIL_QUANTA,
                Duration::from_secs(30),
            )
            .unwrap();
        assert!(world.active_body_id().is_none());
        flush(&mut world);
        assert_eq!(world.custom_mode_view.unwrap().0, MatchPhase::Setup);
        world
            .advance_assembly_selection(
                &mut scene,
                &viewer,
                FIRST_TICK + 17,
                RETAIL_QUANTA,
                Duration::from_secs(30),
            )
            .unwrap();
        assert_eq!(
            world.body.expect("setup releases the selected hero").hero,
            replacement
        );
        flush(&mut world);
        let vars = world.payload_mode_view.as_ref().unwrap().entity_variables();
        assert_eq!(vars[&0x0783], setup_heading("attack"));
        let ScriptValue::Map(context) = &vars[&0x59dc] else {
            panic!("missing round context")
        };
        assert!(context.contains(&(ScriptValue::Integer(attack_member), ScriptValue::Integer(1))));
        let (graphs, _) = scene.payload_presentation().unwrap();
        let defend = crate::pvp_mode::presentation::mode(
            graphs,
            scene.map(),
            scene.payload_round().unwrap(),
            MatchPhase::Setup,
            0,
            30.0,
            0,
        )
        .unwrap();
        assert_eq!(defend.entity_variables()[&0x0783], setup_heading("defense"));
        let Some(PvpObjectiveRuntime::Escort(round)) = scene.simulation_mut().pvp_objective_mut()
        else {
            panic!()
        };
        round.advance(75.0, &[]);
        scene
            .advance_custom_mode(Duration::from_secs(75), [0, 1])
            .unwrap();
        advance(&mut world, &scene, 75);
        assert_eq!(world.custom_mode_view.unwrap().0, MatchPhase::Setup);
        flush(&mut world);
        advance(&mut world, &scene, 75);
        assert_eq!(world.custom_mode_view.unwrap().0, MatchPhase::Active);
        flush(&mut world);
        advance(&mut world, &scene, 75);
        assert!(world.payload_progress_bound);
        flush(&mut world);
        let Some(PvpObjectiveRuntime::Escort(round)) = scene.simulation_mut().pvp_objective_mut()
        else {
            panic!()
        };
        let position = round.mover.pose().position;
        round.advance(
            76.0,
            &[ActorSample {
                entity: 177,
                team: 1,
                position,
                alive: true,
            }],
        );
        assert!(round.mover.distance >= 1.39);
        advance(&mut world, &scene, 76);
        assert!(
            world
                .ready()
                .iter()
                .any(|record| record.record.update.entity == cart
                    && record.record.update.movement.is_some())
        );
        flush(&mut world);
        let mut elapsed = 76.0;
        let mut checked_slope = false;
        while scene.payload_round().unwrap().stage == 0 && elapsed < 314.0 {
            elapsed += 0.125;
            let Some(PvpObjectiveRuntime::Escort(round)) =
                scene.simulation_mut().pvp_objective_mut()
            else {
                panic!()
            };
            let position = round.pose().position;
            round.advance(
                elapsed,
                &[ActorSample {
                    entity: 177,
                    team: 1,
                    position,
                    alive: true,
                }],
            );
            let pitch = i32::from(round.pose().pitch);
            if !checked_slope && pitch != 0 {
                advance(&mut world, &scene, elapsed as u64);
                let movement = world
                    .ready()
                    .iter()
                    .find(|record| {
                        record.record.update.entity == cart
                            && record.record.update.movement.is_some()
                    })
                    .and_then(|record| record.record.update.movement)
                    .expect("the cart sends a pose when the track changes pitch");
                assert_eq!((movement.pitch, movement.body_pitch), (0, pitch));
                flush(&mut world);
                checked_slope = true;
                slopes += 1;
            }
        }
        assert_eq!(scene.payload_round().unwrap().stage, 1, "{}", listing.name);
        let at = Duration::from_secs_f64(elapsed);
        advance(&mut world, &scene, at.as_secs());
        assert!(world.payload_mode_view.as_ref().unwrap().has_graph(0x224e));
        flush(&mut world);

        let mut late = WorldDelivery::new(OWNER + 0x100);
        late.start(
            &scene,
            &PlayerAppearance {
                entity: OWNER + 0x100,
                slot: 1,
                team: 0,
                card: &card,
            },
            FIRST_TICK,
        )
        .unwrap();
        flush(&mut late);
        for frame in 0..3 {
            late.queue_custom_mode_presentation(&scene, FIRST_TICK + frame, 0, at)
                .unwrap();
            flush(&mut late);
        }
        let mode = late.payload_mode_view.as_ref().unwrap().entity_variables();
        assert_eq!(mode[&0x2934], crate::script_wire::Value::Integer(1));
        assert_eq!(
            mode[&0x0783],
            crate::script_wire::Value::Asset(0x0de0_0000_0000_00f7)
        );
        let cart_values = |viewer: &WorldDelivery| {
            viewer.objective_views[&cart]
                .snapshot
                .instance_variables(1)
                .unwrap()[&0x1d9e]
                .clone()
        };
        let expected = cart_values(&late);
        assert_eq!(cart_values(&world), expected);

        world.change_viewer_team(0, scene.simulation_mut());
        let player_seat = |slot, clear| {
            crate::match_scene::player_seat_update(OWNER, 0, slot, false, clear).unwrap()
        };
        world.queue_player_seat(player_seat(0, true));
        world.queue_player_seat(player_seat(1, false));
        world
            .queue_custom_mode_presentation(&scene, FIRST_TICK + 9, 0, at)
            .unwrap();
        assert_eq!(cart_values(&world), expected);
        let ready = world
            .ready()
            .into_iter()
            .find(|record| matches!(record.record.delivery, Delivery::PlayerSeat { .. }))
            .unwrap();
        let seat = ready.tracked();
        let Some(Operation::Update(components)) = &seat.update.operation else {
            panic!()
        };
        let expected_indices = [
            seat_fields["team_component"].as_u64().unwrap(),
            seat_fields["seat_component"].as_u64().unwrap(),
            seat_fields["reset_components"][0].as_u64().unwrap(),
            seat_fields["reset_components"][1].as_u64().unwrap(),
        ];
        assert_eq!(
            components
                .iter()
                .map(|component| u64::from(component.index))
                .collect::<Vec<_>>(),
            expected_indices
        );
        world.sent(&[ready.index]);
        world.acknowledge(outcome(seat, true), at);
        world
            .queue_custom_mode_presentation(&scene, FIRST_TICK + 10, 0, at)
            .unwrap();
        let refresh = send(&mut world, cart);
        assert_eq!(
            cart_values(&world),
            crate::script_wire::Value::Array(Vec::new())
        );
        world
            .queue_custom_mode_presentation(&scene, FIRST_TICK + 11, 0, at)
            .unwrap();
        assert_eq!(
            cart_values(&world),
            crate::script_wire::Value::Array(Vec::new())
        );
        assert_eq!(cart_values(&late), expected);
        world.acknowledge(outcome(refresh, true), at);
        world
            .queue_custom_mode_presentation(&scene, FIRST_TICK + 12, 0, at)
            .unwrap();
        assert_eq!(cart_values(&world), expected);
        count += 1;
    }
    assert_eq!(count, 6);
    assert!(slopes > 0);
}
