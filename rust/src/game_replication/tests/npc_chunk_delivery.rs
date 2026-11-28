use super::*;
use crate::{script_enemy::fixture, script_snapshot_wire::SceneProjection, world::frame};

#[test]
fn placed_actor_script_survives_health_updates_and_retires_with_its_actor() {
    use crate::script_snapshot_wire::{StaticSceneBaseline, View};
    use crate::world::{Create, health};

    let (graphs, codecs, _, _) = fixture(false);
    let actor = 0xc010_0011;
    let script = Component::start(
        Arc::clone(&graphs),
        actor,
        crate::script_wire::Variables::new(),
        vec![Root::new(1, 42)],
        FIRST_TICK,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let snapshot = encoder
        .project(script.snapshot().unwrap(), View::Observer)
        .unwrap();
    let baseline = StaticSceneBaseline {
        payload: encoder.full(&snapshot).unwrap(),
        snapshot,
    };
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(FIRST_TICK);
    let create = EntityUpdate {
        entity: actor,
        operation: Some(Operation::Create(Box::new(Create::new(1, vec![])))),
        ..Default::default()
    };
    world.create_bot(create.clone());
    world.present_static_scene_baseline(&baseline).unwrap();
    assert!(!world.scene_views.contains_key(&actor));
    let mut egress = GameEgress::default();
    transmit(&mut world, None, &mut egress, FIRST_TICK, 0);
    transport_outcome(&mut world, &mut egress, 0, true);

    world.present_static_scene_baseline(&baseline).unwrap();
    let queued = world.ready()[0].tracked();
    assert!(matches!(queued.delivery, Delivery::SceneScript { last: 1 }));
    transmit(&mut world, None, &mut egress, FIRST_TICK + 1, 1);
    world.queue_health(EntityUpdate {
        entity: actor,
        operation: Some(Operation::Update(vec![health(40.0, 50.0, 0.0, 0.0)])),
        ..Default::default()
    });
    transport_outcome(&mut world, &mut egress, 1, false);
    world.present_static_scene_baseline(&baseline).unwrap();
    let pending = world.ready();
    assert_eq!(pending.len(), 2);
    assert!(
        pending
            .iter()
            .any(|item| Arc::ptr_eq(&item.tracked(), &queued))
    );
    transmit(&mut world, None, &mut egress, FIRST_TICK + 2, 2);
    transport_outcome(&mut world, &mut egress, 2, true);
    world.present_static_scene_baseline(&baseline).unwrap();
    assert!(world.scene_views[&actor].base().is_some());
    assert!(world.ready().is_empty());

    world.retire_enemy(actor);
    world.present_static_scene_baseline(&baseline).unwrap();
    assert!(!world.scene_views.contains_key(&actor));
    transmit(&mut world, None, &mut egress, FIRST_TICK + 3, 3);
    transport_outcome(&mut world, &mut egress, 3, true);
    world.create_bot(create);
    transmit(&mut world, None, &mut egress, FIRST_TICK + 4, 4);
    transport_outcome(&mut world, &mut egress, 4, true);
    world.present_static_scene_baseline(&baseline).unwrap();
    assert_eq!(world.ready().len(), 1);
    assert!(!Arc::ptr_eq(&world.ready()[0].tracked(), &queued));
    world.retire_enemy(actor);
    assert!(!world.scene_views.contains_key(&actor));
    assert!(
        world
            .ready()
            .iter()
            .all(|item| item.record.update.chunk.is_none())
    );
}

#[test]
fn crowded_observer_frames_rotate_walking_npc_records() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/npc-script-packet-isolation-v1.json"
    ))
    .unwrap();
    let input = &fixture["movement_budget"];
    let count = input["actors"].as_u64().unwrap() as usize;
    let spare = input["bytes_available_before_movement"].as_u64().unwrap() as usize;
    let throttles = serde_json::from_value(input["walking_throttles"].clone()).unwrap();
    let movements: Vec<_> = (0..count)
        .map(|index| EntityUpdate {
            entity: 0xc000_1000 + index as u32,
            movement: Some(Movement {
                position: [90.0 + index as f64, -2.0, -40.0],
                yaw: 12000,
                velocity: [1.0, 0.0, -3.0],
                throttles,
                ..Movement::default()
            }),
            ..Default::default()
        })
        .collect();
    let mut world = WorldDelivery::new(OWNER);
    let mut egress = GameEgress::default();
    let message = crate::game_messages::EncodedMessage::from_bytes(vec![
        1;
        crate::world::MAX_PAYLOAD
            - spare
    ]);
    let mut visited = 0;
    for sequence in 0..count as u32 {
        let tick = 1000 + sequence;
        let ordinal = egress.queue_reliable(message.clone()).unwrap();
        let mut payload = Vec::new();
        let send = world
            .send_frame(tick, movements.clone(), false, None, &mut egress, |bytes| {
                payload = bytes;
                Ok::<_, ()>(sequence)
            })
            .unwrap();
        let selected = send.records.len();
        assert!((1..count).contains(&selected));
        let expected: Vec<_> = movements
            .iter()
            .cycle()
            .skip(visited)
            .take(selected)
            .collect();
        assert_eq!(
            payload,
            frame(
                tick,
                world.command_ack(tick),
                &[(ordinal, message.clone())],
                &[],
                &expected
            )
            .unwrap()
        );
        transport_outcome(&mut world, &mut egress, sequence, true);
        visited += selected;
        if visited >= count {
            return;
        }
    }
    panic!("not all NPCs got movement room in a crowded stream");
}

#[test]
fn scripted_respawn_admits_owner_movement_and_weapon_contact_at_the_same_ack() {
    use crate::world::Create;

    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/respawn-weapon-visibility-v1.json"
    ))
    .unwrap();
    let input = &contract["input"];
    let number = |key: &str| u32::try_from(input[key].as_u64().unwrap()).unwrap();
    let retired = number("retired");
    let replacement = number("replacement");
    let tick = number("first_tick");
    let expected = |phase: &str| contract["expected"][phase].as_bool().unwrap();
    let mut replacement_bot = crate::game_bots::Bot::fixture_walker([4.0, 0.0, 0.0]);
    replacement_bot.entity = replacement;
    replacement_bot.position = [2.0, 0.0, 0.0];
    let mut simulation = RoomSimulation::new(vec![replacement_bot]).unwrap();
    simulation.enable_movement_contacts(
        false,
        Arc::new(crate::mover_settings::MoverCatalog::default()),
    );
    simulation.prepare_movement_frame(tick).unwrap();
    let contacts = simulation.movement_contact_frame().unwrap();
    let check = |world: &WorldDelivery, phase: &str| {
        let admitted = expected(phase);
        assert_eq!(world.actor_contact_ready(replacement), admitted, "{phase}");
        assert_eq!(
            world
                .owner_movement_blockers(&contacts, world.owner(), 0)
                .len(),
            usize::from(admitted),
            "{phase}"
        );
        assert!(
            world
                .owner_movement_blockers(&contacts, world.owner(), 1)
                .is_empty()
        );
    };
    let (graphs, codecs, _, _) = fixture(false);
    let script = Component::start(
        Arc::clone(&graphs),
        replacement,
        crate::script_wire::Variables::new(),
        vec![Root::new(1, 42)],
        tick,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let start = |owner| {
        let mut world = WorldDelivery::new(owner);
        world.started = Some(tick);
        world.bots.insert(retired, BotLifetime::Live);
        world.created.insert(retired);
        world.replace_bot(BotReplacement {
            retired,
            created: EntityUpdate {
                entity: replacement,
                operation: Some(Operation::Create(Box::new(Create::new(1, vec![])))),
                ..EntityUpdate::default()
            },
            waits_for_presentation: true,
        });
        world
            .present_scene_script(SceneProjection {
                snapshot: script.snapshot().unwrap(),
                encoder: SnapshotEncoder::new(&graphs, &codecs),
            })
            .unwrap();
        world
    };
    let mut fast = start(OWNER);
    let mut slow = start(OWNER + 1);
    let mut fast_egress = GameEgress::default();
    let mut slow_egress = GameEgress::default();
    for (world, egress) in [(&mut fast, &mut fast_egress), (&mut slow, &mut slow_egress)] {
        assert!(!world.actor_contact_ready(retired));
        check(world, "weapon_target_before_create_ack");
        transmit(world, None, egress, tick, 0);
        transport_outcome(world, egress, 0, true);
        assert!(world.identity_ready(replacement));
        check(world, "weapon_target_after_create_ack");
        transmit(world, None, egress, tick + 1, 1);
    }
    transport_outcome(&mut fast, &mut fast_egress, 1, true);
    transport_outcome(&mut slow, &mut slow_egress, 1, false);
    check(&fast, "weapon_target_after_animation_ack");
    check(&slow, "weapon_target_after_animation_loss");
    transmit(&mut slow, None, &mut slow_egress, tick + 2, 2);
    transport_outcome(&mut slow, &mut slow_egress, 2, true);
    check(&slow, "weapon_target_after_animation_ack");
}

#[test]
fn npc_script_chunks_and_reliable_messages_advance_on_separate_frames() {
    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/npc-script-packet-isolation-v1.json"
    ))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["case_id"], "npc-script-packet-isolation-v1");
    assert_eq!(contract["evidence"], "rust-synthetic");
    let input = &contract["input"];
    let npc = input["npc"].as_u64().unwrap() as u32;
    let placeable = input["placeable"].as_u64().unwrap() as u32;
    let start = input["first_tick"].as_u64().unwrap() as u32;
    let first = input["first_sequence"].as_u64().unwrap() as u32;

    let (graphs, codecs, _, _) = fixture(false);
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(start);
    world.bots.insert(npc, BotLifetime::Live);
    world.gate = Some(placeable);
    world.created.extend([npc, placeable]);
    for entity in [npc, placeable] {
        let script = Component::start(
            Arc::clone(&graphs),
            entity,
            crate::script_wire::Variables::new(),
            vec![Root::new(1, 42)],
            start,
            &mut EmptyWorld,
            MAX_INIT_WORK,
        )
        .unwrap();
        world
            .present_scene_script(SceneProjection {
                snapshot: script.snapshot().unwrap(),
                encoder: SnapshotEncoder::new(&graphs, &codecs),
            })
            .unwrap();
    }
    let ready = world.ready();
    assert_eq!(ready.len(), 2);
    let npc_chunk = ready[0].tracked();
    let placeable_chunk = ready[1].tracked();

    let mut egress = GameEgress::default();
    let message = crate::game_messages::EncodedMessage::from_bytes(vec![1, 2]);
    let frames = input["frames"].as_array().unwrap();
    let expected_frames = contract["expected"]["frames"].as_array().unwrap();
    assert_eq!(frames.len(), expected_frames.len());
    for (index, (step, expected)) in frames.iter().zip(expected_frames).enumerate() {
        let offset = index as u32;
        let tick = start + offset;
        let sequence = first + offset;
        if let Some(ordinal) = step["queue_reliable"].as_u64() {
            assert_eq!(
                u64::from(egress.queue_reliable(message.clone()).unwrap()),
                ordinal
            );
        }
        if step["queue_unreliable"].as_bool() == Some(true) {
            egress.queue_unreliable(message.clone());
        }
        let expected_messages: Vec<_> = expected["reliable_ordinal"]
            .as_u64()
            .map(|ordinal| (ordinal as u32, message.clone()))
            .into_iter()
            .collect();
        let expected_records: Vec<_> = [&npc_chunk, &placeable_chunk]
            .into_iter()
            .filter(|record| {
                Some(u64::from(record.update.entity)) == expected["script_entity"].as_u64()
            })
            .map(|record| &record.update)
            .collect();
        let unreliable = if expected["unreliable"].as_bool() == Some(true) {
            std::slice::from_ref(&message)
        } else {
            &[]
        };
        let expected_payload = frame(
            tick,
            world.command_ack(tick),
            &expected_messages,
            unreliable,
            &expected_records,
        )
        .unwrap();

        if step["transport_retries"].as_bool() == Some(true) {
            // Failed transport must preserve the reserved turn and both queues.
            let failed = world.send_frame(tick, vec![], false, None, &mut egress, |payload| {
                assert_eq!(payload, expected_payload);
                Err(())
            });
            assert!(matches!(failed, Err(SendError::Transport(()))));
        }

        let (payload, sent) = transmit(&mut world, None, &mut egress, tick, sequence);
        assert_eq!(payload, expected_payload);
        assert_eq!(sent.records.len(), expected_records.len());
        transport_outcome(
            &mut world,
            &mut egress,
            sequence,
            step["arrived"].as_bool().unwrap(),
        );
        let npc_base = world.scene_views[&npc].base().is_some();
        assert_eq!(Some(npc_base), expected["npc_base"].as_bool());
        if npc_base {
            assert!(world.ready().is_empty());
        } else {
            assert!(Arc::ptr_eq(&npc_chunk, &world.ready()[0].tracked()));
        }
    }
    assert!(world.scene_views[&placeable].base().is_some());
    assert!(egress.delivered_through(2));
}
