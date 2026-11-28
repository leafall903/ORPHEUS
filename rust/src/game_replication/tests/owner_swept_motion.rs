use super::*;

fn remote_contact_delivery(id: BodyId, world: &mut WorldDelivery, arrived: bool) {
    let update = EntityUpdate {
        entity: id.entity,
        operation: Some(Operation::Create(Box::new(crate::world::Create::new(
            1,
            Vec::new(),
        )))),
        ..Default::default()
    };
    world.create_remote_player(id, update.clone());
    let create = Record {
        update,
        delivery: Delivery::RemotePlayerCreate(id),
    };
    world.acknowledge(outcome(Arc::new(create), arrived), Duration::ZERO);
}

#[test]
fn observer_receives_acknowledged_player_poses_on_skipped_tick_world_sends() {
    use crate::game_clock::{GameClock, Step};

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/observer-skipped-tick-movement-v1.json"
    ))
    .unwrap();
    let missed = fixture["missed_ticks_per_step"].as_u64().unwrap();
    let expected_frames: Vec<u32> =
        serde_json::from_value(fixture["expected_world_send_frames"].clone()).unwrap();
    let mut clock = GameClock::default();
    let start = clock.frame();
    let mut scene = PreparedScene::fixture_for_bots(Vec::new());
    let mut observer = WorldDelivery::new(OWNER);
    observer.started = Some(start - BOT_RECORD_DELAY);
    let visible = avatar(2, 1);
    let pending = avatar(3, 1);
    for (id, owner, slot) in [(visible, OWNER + 0x100, 1), (pending, OWNER + 0x200, 2)] {
        scene
            .simulation_mut()
            .spawn_player_body(fixture_player_state(
                id, owner, slot, 1, [0.0; 3], 200.0, true,
            ))
            .unwrap();
        remote_contact_delivery(id, &mut observer, id == visible);
    }
    for expected_frame in expected_frames {
        let Step::Simulate { frame, send_world } = clock.step(missed, true) else {
            panic!("unexpected clock drain");
        };
        assert_eq!(frame, expected_frame);
        assert!(send_world);
        assert_ne!(frame % SEND_EVERY, 0);
        let position = [f64::from(frame - start), 0.0, 0.0];
        let remote = scene.simulation_mut().player_body_mut(visible).unwrap();
        remote.mover = mover_at(position);
        let updates = observer.movement(&scene, frame).unwrap();
        assert_eq!(updates.len(), 1, "only the acknowledged body is visible");
        assert_eq!(updates[0].entity, visible.entity);
        assert_eq!(updates[0].movement.unwrap().position, position);
    }
}

#[test]
fn hostile_actor_contact_corrects_ordinary_movement_with_or_without_an_owner_script() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/owner-actor-contact-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let start = u32::try_from(input["start_frame"].as_u64().unwrap()).unwrap();
    let last = u32::try_from(input["last_frame"].as_u64().unwrap()).unwrap();
    let hero = crate::accounts::parse_guid(input["hero"].as_str().unwrap()).unwrap();
    let position: [f64; 3] = serde_json::from_value(input["actor_position"].clone()).unwrap();
    let minimum_separation = input["minimum_capsule_separation"].as_f64().unwrap();
    let right = i8::try_from(input["right"].as_i64().unwrap()).unwrap();
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    for (team, scripted, delivered) in [
        (1, true, true),
        (1, false, true),
        (0, true, true),
        (0, false, true),
        (1, true, false),
        (1, false, false),
    ] {
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        let mut world = WorldDelivery::new(OWNER);
        let mut egress = GameEgress::default();
        let id = avatar(1, 1);
        let target = avatar(2, 1);
        world.started = Some(start);
        world.input.processed = Some(start);
        let room = scene.simulation_mut();
        activate_script(&mut world, room, id, hero, Arc::default());
        if !scripted {
            world.cancel_body_script(Some(room));
        }
        room.player_body_mut(id).unwrap().mover = Mover::new(
            MoverSettings {
                accel_air: 35.0,
                gravity_scale: 0.0,
                ..MoverSettings::default()
            },
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        room.spawn_player_body(PlayerBodyState::new(
            target,
            OWNER + 0x100,
            1,
            team,
            7,
            mover_at(position),
            None,
            start,
            None,
        ))
        .unwrap();
        room.enable_movement_contacts(
            false,
            Arc::new(crate::mover_settings::MoverCatalog::default()),
        );
        world.acknowledge(
            outcome(Arc::new(record(id.entity, Delivery::CreateBody(id))), true),
            Duration::ZERO,
        );
        remote_contact_delivery(target, &mut world, delivered);
        assert_eq!(world.actor_contact_ready(target.entity), delivered);
        for frame in start + 1..=last {
            scene
                .simulation_mut()
                .prepare_movement_frame(frame)
                .unwrap();
            world.queue_commands(
                frame,
                &[Command {
                    frame,
                    right,
                    ..Command::default()
                }],
            );
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap();
        }
        let pose = scene.simulation().player_body(id).unwrap().mover.pose();
        let (_, sent) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            last,
            0,
        );
        let corrected = sent.records.contains(&RecordSource::Movement(0));
        let blocked = team == 1 && delivered;
        if blocked {
            assert!(
                pose.position[0] > 0.0 && position[0] - pose.position[0] >= minimum_separation,
                "{pose:?}"
            );
        } else {
            assert!(pose.position[0] > position[0], "{pose:?}");
        }
        assert_eq!(corrected, scripted || blocked);
    }
}

#[test]
fn solo_owner_input_uses_the_npc_pose_committed_earlier_in_the_same_tick() {
    use crate::{
        game_bots::Bot,
        script_init::{ScriptedMotion, SweptDisplacement},
    };

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/npc-contact-after-input-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let tick = u32::try_from(input["tick"].as_u64().unwrap()).unwrap();
    let vector = |key: &str| serde_json::from_value::<[f64; 3]>(input[key].clone()).unwrap();
    let npc_start = vector("npc_position");
    let npc_step = SweptDisplacement::new(vector("npc_displacement")).unwrap();
    let owner_step = SweptDisplacement::new(vector("player_displacement")).unwrap();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let id = avatar(1, 1);
    let settings = MoverSettings::default();
    let run = |move_npc: bool| {
        let mut npc = Bot::fixture_walker(npc_start);
        npc.position = npc_start;
        npc.attach_collision(
            &settings,
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            0x18,
            |_, _, _, _| Some(0.0),
        );
        let wire = npc.entity;
        let mut scene = PreparedScene::fixture_for_bots(vec![npc]);
        let mut world = WorldDelivery::new(OWNER);
        world.started = Some(tick - 1);
        world.create_bot(
            crate::match_scene::bot_create(scene.bots().unwrap().next().unwrap(), None).unwrap(),
        );
        let create = world.ready()[0].tracked();
        world.sent(&eligible(&world));
        world.acknowledge(outcome(create, true), Duration::ZERO);
        assert!(world.actor_contact_ready(wire));

        let room = scene.simulation_mut();
        world.input.processed = Some(tick - 1);
        activate_script(&mut world, room, id, 7, Arc::default());
        room.enable_movement_contacts(false, Arc::default());
        room.prepare_npc_movement_frame(tick).unwrap();
        if move_npc {
            let before = room.movement_contact_frame().unwrap();
            let npc = room.body_mut(wire).unwrap();
            before.stage_bot(npc, tick);
            assert!(npc.apply_scripted_motion(tick, ScriptedMotion::Sweep(npc_step)));
        }
        room.prepare_peer_movement_frame(tick).unwrap();
        assert!(
            room.player_body_mut(id)
                .unwrap()
                .mover
                .queue_swept_displacement(tick, owner_step)
        );
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let room = scene.simulation();
        let owner = room.player_body(id).unwrap().mover.pose();
        assert_eq!(owner.frame, Some(tick));
        let npc_x = room.bodies().unwrap().next().unwrap().position[0];
        (owner.position[0], npc_x)
    };
    let (control_owner, stationary_npc) = run(false);
    let (blocked_owner, moved_npc) = run(true);
    let separation = 2.0 * settings.stand_radius;
    let tolerance = f64::from(crate::contact_triangle::TOUCH);
    assert!(moved_npc < stationary_npc);
    assert!(stationary_npc - control_owner >= separation - tolerance);
    assert!(moved_npc - control_owner < separation - 0.1);
    assert!(moved_npc - blocked_owner >= separation - tolerance);
}

#[test]
fn combat_impulse_uses_the_resolved_owner_pose_with_or_without_a_script() {
    use crate::mover::VelocityChange;

    let id = avatar(1, 1);
    let start = 100;
    let due = start + 1;
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    for scripted in [true, false] {
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        let mut world = WorldDelivery::new(OWNER);
        let mut egress = GameEgress::default();
        world.started = Some(start);
        world.input.processed = Some(start);
        activate_script(&mut world, scene.simulation_mut(), id, 7, Arc::default());
        if !scripted {
            world.cancel_body_script(Some(scene.simulation_mut()));
        }
        world.acknowledge(
            outcome(Arc::new(record(id.entity, Delivery::CreateBody(id))), true),
            Duration::ZERO,
        );
        let mover = &mut scene.simulation_mut().player_body_mut(id).unwrap().mover;
        let before = mover.pose();
        mover.queue_velocity_change(due, VelocityChange::Impulse([3.0, 7.0, 0.0]));
        world.queue_commands(
            due,
            &[Command {
                frame: due,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let pose = scene.simulation().player_body(id).unwrap().mover.pose();
        assert_eq!(pose.frame, Some(due));
        assert!(pose.position[0] > before.position[0] && pose.position[1] > before.position[1]);
        assert!(pose.velocity[1] > 0.0);
        let (_, sent) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            due,
            0,
        );
        assert!(sent.records.contains(&RecordSource::Movement(0)));
        let history = if let Some(script) = world.body_script.as_ref() {
            script.corrections.as_ref().unwrap()
        } else {
            &world.detached_motion.as_ref().unwrap().1
        };
        assert_eq!(history.corrected(), Some(due));
    }
}

#[test]
fn lost_terminal_owner_correction_retries_once_unless_a_newer_pose_was_sent() {
    use crate::{mover::VelocityChange, world::frame};

    #[derive(serde::Deserialize)]
    struct Case {
        start_frame: u32,
        impulse: [f64; 3],
        terminal_frame: u32,
        outstanding_sequence: u32,
        later_packets: u32,
    }
    let case: Case = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/owner-terminal-correction-loss-v1.json"
    ))
    .unwrap();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let id = avatar(1, 1);
    let step = |world: &mut WorldDelivery, scene: &mut PreparedScene, tick| {
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                ..Command::default()
            }],
        );
        world
            .advance_input(scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        assert_eq!(world.input.processed, Some(tick));
    };
    let expected = |tick, ack, pose: Movement| {
        let record = EntityUpdate {
            entity: id.entity,
            movement: Some(pose),
            ..EntityUpdate::default()
        };
        frame(tick, ack, &[], &[], &[&record]).unwrap()
    };

    for (newer_sent, continued_input) in [(false, false), (false, true), (true, false)] {
        let mut world = WorldDelivery::new(OWNER);
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        let mut egress = GameEgress::default();
        world.started = Some(case.start_frame);
        world.input.processed = Some(case.start_frame);
        activate_script(&mut world, scene.simulation_mut(), id, 7, Arc::default());
        if !newer_sent {
            world.cancel_body_script(Some(scene.simulation_mut()));
        }
        world.acknowledge(
            outcome(Arc::new(record(id.entity, Delivery::CreateBody(id))), true),
            Duration::ZERO,
        );
        scene
            .simulation_mut()
            .player_body_mut(id)
            .unwrap()
            .mover
            .queue_velocity_change(case.start_frame + 1, VelocityChange::Impulse(case.impulse));
        for tick in case.start_frame + 1..=case.terminal_frame {
            step(&mut world, &mut scene, tick);
        }
        let terminal = scene.simulation().player_body(id).unwrap().mover.pose();
        assert_eq!(terminal.frame, Some(case.terminal_frame));
        let (original, _) = transmit(
            &mut world,
            None,
            &mut egress,
            case.terminal_frame,
            case.outstanding_sequence,
        );
        assert_eq!(
            original,
            expected(case.terminal_frame, case.terminal_frame, terminal)
        );

        // Advance the receiver's real packet window beyond the unreceived datagram.
        for offset in 1..=case.later_packets {
            let sequence = case.outstanding_sequence + offset;
            let tick = case.terminal_frame + offset;
            if newer_sent && offset == 1 {
                step(&mut world, &mut scene, tick);
                let latest = scene.simulation().player_body(id).unwrap().mover.pose();
                let (packet, _) = transmit(&mut world, None, &mut egress, tick, sequence);
                assert_eq!(latest.frame, Some(tick));
                assert_eq!(packet, expected(tick, tick, latest));
            } else {
                if continued_input && offset <= 4 {
                    step(&mut world, &mut scene, tick);
                }
                world
                    .send_frame(tick, Vec::new(), true, None, &mut egress, |payload| {
                        if continued_input {
                            assert_eq!(read_ack(&payload), case.terminal_frame + offset.min(2));
                        }
                        Ok::<_, ()>(sequence)
                    })
                    .unwrap();
            }
        }
        let next_seq = case.outstanding_sequence + case.later_packets + 1;
        let next_tick = case.terminal_frame + case.later_packets + 1;
        world.acknowledge(egress.acknowledge(next_seq, u64::MAX), Duration::ZERO);
        let (payload, sent) = transmit(&mut world, None, &mut egress, next_tick, next_seq);
        if newer_sent {
            assert!(
                sent.records.is_empty(),
                "a later continuous pose supersedes the lost correction"
            );
        } else {
            assert_eq!(sent.records.len(), 1);
            assert_eq!(
                payload,
                expected(
                    next_tick,
                    case.terminal_frame + if continued_input { 2 } else { 0 },
                    terminal
                )
            );
            transport_outcome(&mut world, &mut egress, next_seq, true);
            let (_, settled) = transmit(&mut world, None, &mut egress, next_tick + 1, next_seq + 1);
            assert!(
                settled.records.is_empty(),
                "the acknowledged correction repeated"
            );
        }
    }
}

#[test]
fn old_owner_correction_cannot_retry_on_a_reused_wire_id() {
    use crate::mover_correction::CorrectionHistory;

    let old = avatar(1, 1);
    let replacement = avatar(1, 2);
    let bind_created_body = |world: &mut WorldDelivery, id: BodyId| {
        world.body = Some(body(id));
        world
            .bodies
            .insert(id.entity, (id.generation, Lifetime::Live));
        world.acknowledge(
            outcome(Arc::new(record(id.entity, Delivery::CreateBody(id))), true),
            Duration::ZERO,
        );
    };
    for loss_before_replacement in [true, false] {
        let mut world = WorldDelivery::new(OWNER);
        let mut egress = GameEgress::default();
        world.started = Some(100);
        bind_created_body(&mut world, old);
        let mut history = CorrectionHistory::new(old.entity, old.generation);
        history.swept_motion(100, RETAIL_QUANTA);
        let (_, terminal) = history.impulse_correction().unwrap();
        history.record(Movement {
            frame: Some(terminal),
            position: [3.0, 1.0, 0.0],
            ..Movement::default()
        });
        world.detached_motion = Some((old, history));
        world.input.processed = Some(terminal);
        let (_, first) = transmit(&mut world, None, &mut egress, terminal, 10);
        assert_eq!(first.records, [RecordSource::Movement(0)]);
        let lost = egress.acknowledge(75, u64::MAX);
        assert_eq!(lost.owner_correction, Some((10, false)));

        // Keep the wire created across the transition so only generation distinguishes the bodies.
        if loss_before_replacement {
            world.acknowledge(lost, Duration::ZERO);
            bind_created_body(&mut world, replacement);
        } else {
            bind_created_body(&mut world, replacement);
            world.acknowledge(lost, Duration::ZERO);
        }
        let current_frame = terminal + 8;
        world.input.processed = Some(current_frame);
        let (packet, next) = transmit(&mut world, None, &mut egress, current_frame, 75);
        assert!(
            next.records.is_empty(),
            "old pose was sent to a new generation"
        );
        assert_eq!(
            read_ack(&packet),
            current_frame,
            "old pose capped the new body's input ACK"
        );
    }
}

#[test]
fn posed_owner_fault_cancels_future_velocity_before_the_recovered_batch_continues() {
    use crate::mover::VelocityChange;

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/owner-script-motion-cancellation-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let input = &fixture["input"];
    let frame = |name: &str| u32::try_from(input[name].as_u64().unwrap()).unwrap();
    let spawn = frame("spawn_frame");
    let warm = frame("warm_pose_frame");
    let first_fault = frame("fault_pose_frame");
    let due = frame("velocity_due_frame");
    let recovered = frame("recovered_command_frame");
    let delta = serde_json::from_value(input["velocity_delta"].clone()).unwrap();
    let actor = input["actor"].as_u64().unwrap();
    assert_eq!(first_fault, warm + 1);
    assert!(first_fault < due && due < recovered);
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };

    for fault_after_pose in [false, true] {
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        let mut world = WorldDelivery::new(OWNER);
        let id = avatar(1, 1);
        let fault = Arc::new(AtomicBool::new(false));
        world.started = Some(spawn);
        world.input.processed = Some(spawn);
        activate_script(
            &mut world,
            scene.simulation_mut(),
            id,
            actor,
            Arc::clone(&fault),
        );
        world.queue_commands(
            warm,
            &[Command {
                frame: warm,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let mover = &mut scene.simulation_mut().player_body_mut(id).unwrap().mover;
        assert_eq!(mover.pose().frame, Some(warm));
        assert_eq!(mover.pose().position[0], 0.0);
        mover.queue_velocity_change(due, VelocityChange::Delta(delta));

        // The fixture still advances its component, but snapshot() becomes
        // unavailable. The first recovered physics pose therefore completes
        // before the owner's posed VM check reports the failure.
        fault.store(fault_after_pose, Ordering::Relaxed);
        world.queue_commands(
            recovered,
            &[Command {
                frame: recovered,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let pose = scene.simulation().player_body(id).unwrap().mover.pose();
        assert_eq!(pose.frame, Some(recovered));
        assert_eq!(world.input.processed, Some(recovered));
        assert!(world.input.work.is_empty());
        assert_eq!(world.body_script.is_none(), fault_after_pose);
        if fault_after_pose {
            let report = world.owner_script_fault.as_ref().unwrap();
            assert_eq!(report.id, id);
            assert_eq!(report.frame, first_fault);
            assert_eq!(report.step, Some(BodyStep::Posed));
            assert_eq!(
                pose.position[0],
                fixture["expected"]["faulted_final_x"].as_f64().unwrap()
            );
            assert_eq!(pose.velocity[0], 0.0);
        } else {
            assert!(world.owner_script_fault.is_none());
            assert!(pose.position[0] > 0.0, "control pose: {pose:?}");
            assert!(pose.velocity[0] > 0.0);
        }
    }
}

#[test]
fn player_displacement_uses_room_actor_contacts_for_the_authoritative_pose() {
    use crate::{mover::VelocityChange, script_init::SweptDisplacement};

    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    let displacement = SweptDisplacement::new([4.0, 0.0, 0.0]).unwrap();
    let target_x = 2.0;
    for (blocker_team, velocity_x) in [(0, 0.0), (1, 0.0), (1, 160.0)] {
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        let room = scene.simulation_mut();
        let mut world = WorldDelivery::new(OWNER);
        let id = avatar(1, 1);
        let mut target = avatar(2, 1);
        target.entity += 0x100;
        world.started = Some(100);
        activate_script(&mut world, room, id, 7, Arc::default());
        remote_contact_delivery(target, &mut world, true);
        room.spawn_player_body(PlayerBodyState::new(
            target,
            OWNER + 0x100,
            1,
            blocker_team,
            7,
            mover_at([target_x, 0.0, 0.0]),
            None,
            100,
            None,
        ))
        .unwrap();
        room.enable_movement_contacts(
            false,
            Arc::new(crate::mover_settings::MoverCatalog::default()),
        );
        room.prepare_movement_frame(101).unwrap();
        let mover = &mut room.player_body_mut(id).unwrap().mover;
        assert!(mover.queue_swept_displacement(101, displacement));
        mover.queue_velocity_change(101, VelocityChange::Delta([velocity_x, 0.0, 0.0]));

        world.queue_commands(
            101,
            &[Command {
                frame: 101,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let moved_x = scene
            .simulation()
            .player_body(id)
            .unwrap()
            .mover
            .pose()
            .position[0];
        if blocker_team == 0 {
            assert_eq!(moved_x, 4.0);
        } else {
            assert!(0.0 < moved_x && moved_x < target_x, "position: {moved_x}");
        }
    }
}

#[test]
fn swept_owner_correction_uses_the_applied_pose_and_outlives_a_script_fault() {
    use crate::script_init::{Relocation, ScriptedMotion, SweptDisplacement};

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/owner-swept-motion-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let frame = |name: &str| u32::try_from(input[name].as_u64().unwrap()).unwrap();
    let requested =
        SweptDisplacement::new(serde_json::from_value(input["displacement"].clone()).unwrap())
            .unwrap();
    let sweep = ScriptedMotion::Sweep(requested);
    let relocation =
        ScriptedMotion::Relocate(Relocation::new(requested.delta().map(f64::from)).unwrap());
    let spawn = frame("spawn_frame");
    let due = frame("due_frame");
    let tail_pose = frame("tail_pose");
    let later_ack = frame("later_ack");
    let generic_actor = input["actor"].as_u64().unwrap();
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };

    for (motion, fault_after_pose) in [
        (sweep, false),
        (sweep, true),
        (relocation, false),
        (relocation, true),
    ] {
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        let mut world = WorldDelivery::new(OWNER);
        let mut egress = GameEgress::default();
        let id = avatar(1, 1);
        let fault = Arc::new(AtomicBool::new(false));
        world.started = Some(spawn);
        world.input.processed = Some(spawn);
        activate_script_without_movement_projection(
            &mut world,
            scene.simulation_mut(),
            id,
            generic_actor,
            Arc::clone(&fault),
        );
        assert!(world.body_script.as_ref().unwrap().corrections.is_none());
        world.queue_commands(
            due - 1,
            &[Command {
                frame: due - 1,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let (pre_move, _) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            spawn + 1,
            0,
        );
        assert_eq!(read_ack(&pre_move), due - 1);
        transport_outcome(&mut world, &mut egress, 0, true);

        fault.store(fault_after_pose, Ordering::Relaxed);
        assert!(
            scene
                .simulation_mut()
                .player_body_mut(id)
                .unwrap()
                .mover
                .queue_scripted_motion(due, motion)
        );
        world.queue_commands(
            due,
            &[Command {
                frame: due,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        assert_eq!(world.body_script.is_none(), fault_after_pose);
        assert_eq!(world.detached_motion.is_some(), fault_after_pose);
        let moved = scene.simulation().player_body(id).unwrap().mover.pose();
        assert_eq!(moved.frame, Some(due));
        assert_eq!(moved.position[0], f64::from(requested.delta()[0]));

        let (lead, blocked) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            spawn + 2,
            1,
        );
        assert_eq!(read_ack(&lead), due);
        assert!(!blocked.records.contains(&RecordSource::Movement(0)));
        transport_outcome(&mut world, &mut egress, 1, true);
        world.acknowledge(
            outcome(Arc::new(record(id.entity, Delivery::CreateBody(id))), true),
            Duration::ZERO,
        );
        let first = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            due,
            2,
        )
        .1;
        assert!(first.records.contains(&RecordSource::Movement(0)));
        let history = if let Some((owner, history)) = &world.detached_motion {
            assert_eq!(*owner, id);
            history
        } else {
            world
                .body_script
                .as_ref()
                .unwrap()
                .corrections
                .as_ref()
                .unwrap()
        };
        assert_eq!(history.corrected(), Some(due));
        transport_outcome(&mut world, &mut egress, 2, true);

        for frame in due + 1..=tail_pose {
            world.queue_commands(
                frame,
                &[Command {
                    frame,
                    ..Command::default()
                }],
            );
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap();
        }
        world.input.processed = Some(later_ack);
        let (capped, last) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            tail_pose,
            3,
        );
        assert_eq!(read_ack(&capped), tail_pose + 2);
        assert!(last.records.contains(&RecordSource::Movement(0)));
        transport_outcome(&mut world, &mut egress, 3, true);
        assert!(world.detached_motion.is_none());
        assert!(
            world
                .body_script
                .as_ref()
                .is_none_or(|script| script.corrections.is_none())
        );
        let (bytes, sent) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            tail_pose + 1,
            4,
        );
        assert_eq!(read_ack(&bytes), later_ack);
        assert!(!sent.records.contains(&RecordSource::Movement(0)));
    }
}
