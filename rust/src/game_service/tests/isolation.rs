//! Public encrypted worker acceptance for room fault containment.

use super::*;
use crate::game_service::room::InjectedFault;
use serde_json::Value as Json;

fn roster(count: usize) -> Vec<MatchPlayer> {
    (0..count).map(|_| player().pop().unwrap()).collect()
}

fn room_id(value: &Json) -> MatchId {
    serde_json::from_value(value.clone()).unwrap()
}

fn join(server: &GameService, ticket: &Handoff, schemas: &Schemas) -> Client {
    let mut client = Client::new(server.local_addr(), ticket);
    client.handshake();
    let packet = client.receive();
    assert!(client.window.accept(packet.seq));
    assert_eq!(messages(schemas, &packet)[0].message.id, 20300);
    client.send(ACK, Vec::new());
    client
}

fn ping(client: &mut Client, schemas: &Schemas, marker: u64) {
    let bytes = encode(
        schemas,
        21610,
        &Fields::from([
            (0x78, Value::Unsigned(marker)),
            (0x80, Value::Array(vec![Value::Unsigned(25)])),
        ]),
    )
    .unwrap();
    let mut payload = BitWriter::new();
    write_channel(&mut payload, &[], &[bytes]).unwrap();
    client.send(ACK, payload.into_bytes());
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        assert!(
            Instant::now() < deadline,
            "surviving room did not answer its new ping"
        );
        let packet = client.receive();
        if packet.kind != KIND_FRAME || !client.window.accept(packet.seq) {
            continue;
        }
        let found = messages(schemas, &packet).iter().any(|item| {
            item.message.id == 20306
                && item.message.fields.get(&0x78) == Some(&Value::Unsigned(marker))
        });
        client.send(ACK, Vec::new());
        if found {
            break;
        }
    }
}

fn inject(server: &GameService, id: MatchId, stage: RoomStage, fault: InjectedFault) {
    assert!(
        server
            .request(|reply| Request::Inject {
                id,
                stage,
                fault,
                reply
            })
            .unwrap()
    );
}

fn faults(server: &GameService) -> Vec<FaultRecord> {
    server.request(|reply| Request::Faults { reply }).unwrap()
}

fn wait_for_fault(server: &GameService) -> Vec<FaultRecord> {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let records = faults(server);
        if !records.is_empty() {
            return records;
        }
        assert!(
            Instant::now() < deadline,
            "room fault was not observed by the worker"
        );
        thread::sleep(Duration::from_millis(1));
    }
}

fn rejects_stale_syn(client: &mut Client) {
    // Discard datagrams already emitted before the fault. A fresh SYN must not
    // be accepted once the worker reports that this room has been removed.
    client.socket.set_nonblocking(true).unwrap();
    let mut buffer = [0; MAX_DATAGRAM];
    loop {
        match client.socket.recv(&mut buffer) {
            Ok(_) => (),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
            Err(error) => panic!("draining old datagrams: {error}"),
        }
    }
    client.socket.set_nonblocking(false).unwrap();
    client
        .socket
        .set_read_timeout(Some(Duration::from_millis(80)))
        .unwrap();
    client.send(SYN, Vec::new());
    let error = client.socket.recv(&mut buffer).unwrap_err();
    assert!(matches!(
        error.kind(),
        io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
    ));
}

#[test]
fn encrypted_worker_satisfies_room_recovery_contract() {
    let contract: Json = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/room-isolation-v1.json"
    )))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["target_build"], 104319);
    assert_eq!(contract["case_id"], "room-isolation.synthetic-policy.v1");
    assert_eq!(contract["evidence"], "synthetic-recovery-policy");
    let input = &contract["input"];
    let expected = &contract["expected"];
    for flag in [
        "retired_seats_absent",
        "retired_link_rejects",
        "survivor_ping_after_fault",
        "new_room_handshake",
    ] {
        assert_eq!(expected[flag], true);
    }
    let failed = room_id(&input["fault_room"]);
    let survivor = room_id(&input["survivor"]);
    let new_room = room_id(&input["new_room"]);
    let cases = input["cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    let mut seen = HashSet::new();
    for case in cases {
        assert!(seen.insert((
            case["stage"].as_str().unwrap(),
            case["kind"].as_str().unwrap()
        )));
        let stage = match case["stage"].as_str().unwrap() {
            "finish_tick" => RoomStage::FinishTick,
            "receive" => RoomStage::Receive,
            "debug" => RoomStage::Debug,
            other => panic!("unknown contract stage {other}"),
        };
        let fault = match case["kind"].as_str().unwrap() {
            "result" => InjectedFault::Error,
            "panic" => InjectedFault::Panic,
            other => panic!("unknown contract fault {other}"),
        };
        let schemas = Arc::new(fixture());
        let mut service = GameService::bind(local(), Arc::clone(&schemas)).unwrap();
        let failed_tickets = service
            .open_match(
                failed,
                GameMap::practice_range(),
                roster(input["failed_room_players"].as_u64().unwrap() as usize),
            )
            .unwrap();
        assert_eq!(
            failed_tickets.len(),
            2,
            "the contract covers an active and a waiting member"
        );
        let mut failed_client = join(&service, &failed_tickets[0], &schemas);
        let survivor_ticket = service
            .open_match(
                survivor,
                GameMap::practice_range(),
                roster(input["survivor_players"].as_u64().unwrap() as usize),
            )
            .unwrap()
            .pop()
            .unwrap();
        let mut survivor_client = join(&service, &survivor_ticket, &schemas);
        ping(&mut survivor_client, &schemas, 101);
        inject(&service, failed, stage, fault);
        match stage {
            RoomStage::Receive => failed_client.send(ACK, Vec::new()),
            RoomStage::Debug => {
                service.debug_snapshot().unwrap();
            }
            _ => (),
        }
        let records = wait_for_fault(&service);
        assert_eq!(
            records.len() as u64,
            expected["fault_records"].as_u64().unwrap()
        );
        assert_eq!(records[0].id, failed);
        assert_eq!(records[0].stage, stage);
        assert_eq!(
            records[0].reason,
            if matches!(fault, InjectedFault::Panic) {
                "panic"
            } else {
                "invariant"
            }
        );
        assert!(records[0].tick >= FIRST_TICK);
        for ticket in &failed_tickets {
            assert!(!service.seat_exists(ticket).unwrap());
            assert!(!service.handoff_pending(ticket.conn).unwrap());
        }
        rejects_stale_syn(&mut failed_client);
        rejects_stale_syn(&mut Client::new(service.local_addr(), &failed_tickets[1]));
        ping(&mut survivor_client, &schemas, 102);
        let ticket = service
            .open_match(
                new_room,
                GameMap::practice_range(),
                roster(input["new_room_players"].as_u64().unwrap() as usize),
            )
            .unwrap()
            .pop()
            .unwrap();
        let mut next = join(&service, &ticket, &schemas);
        ping(&mut next, &schemas, 103);
        ping(&mut survivor_client, &schemas, 104);
        assert_eq!(
            service.debug_snapshot().unwrap().faults.len(),
            records.len()
        );
        service.stop().unwrap();
    }
}

#[test]
fn panic_after_peer_detachment_and_mutation_retires_the_whole_room() {
    use std::cell::Cell;

    let schemas = Arc::new(fixture());
    let mut state = State::new(Arc::clone(&schemas), PatrolScript::load().unwrap());
    let socket = UdpSocket::bind(local()).unwrap();
    let failed = state
        .open_match(
            MATCH,
            GameMap::practice_range(),
            roster(2),
            None,
            Duration::ZERO,
        )
        .unwrap();
    let survivor_id = [5, 9];
    let survivor = state
        .open_match(
            survivor_id,
            GameMap::practice_range(),
            player(),
            None,
            Duration::ZERO,
        )
        .unwrap()
        .pop()
        .unwrap();
    let activate = |state: &mut State, ticket: &Handoff| {
        let client = Client::new(socket.local_addr().unwrap(), ticket);
        let syn = client
            .cipher
            .seal(&Packet {
                conn: ticket.conn,
                kind: KIND_CONTROL,
                flags: SYN | ACK,
                ..Packet::default()
            })
            .unwrap();
        state
            .receive(
                &socket,
                &syn,
                client.socket.local_addr().unwrap(),
                Duration::ZERO,
            )
            .unwrap();
        assert!(
            state.rooms[&ticket.match_id].members[&ticket.conn]
                .peer
                .as_ref()
                .unwrap()
                .open
        );
        assert!(state.links.sender(ticket.conn).is_some());
        client
    };
    let failed_clients: Vec<_> = failed
        .iter()
        .map(|ticket| activate(&mut state, ticket))
        .collect();
    let _survivor_client = activate(&mut state, &survivor);
    let survivor_name = state.rooms[&survivor_id].players[0].info.name.clone();
    let survivor_pending = state.rooms[&survivor_id].members[&survivor.conn]
        .peer
        .as_ref()
        .unwrap()
        .egress
        .oldest_pending_reliable();
    state.next_input_peer = Some(failed[0].conn);

    let detached = Cell::new(false);
    let queued_message_seen = Cell::new(false);
    let reached_injected_panic = Cell::new(false);
    let result: Result<(), RoomError> = guard_room(
        state.rooms.get_mut(&MATCH).unwrap(),
        RoomStage::Input,
        |room| {
            room.with_peer(failed[0].conn, |room, peer, slot| {
                assert!(room.members[&failed[0].conn].peer.is_none());
                detached.set(true);
                room.players[slot].info.name = "partially changed room".into();
                peer.open = false;
                let message = leave_message();
                let message_id = message.id;
                let ordinal = peer.reliable(&schemas, message).unwrap();
                // Inspect the actual encoded pending message. Rejected transport
                // keeps it queued, so unwind must discard real peer delivery work.
                let pending = peer.world.send_frame(
                    FIRST_TICK,
                    Vec::new(),
                    true,
                    &mut peer.egress,
                    |payload| {
                        let packet = Packet {
                            kind: KIND_FRAME,
                            payload,
                            ..Packet::default()
                        };
                        assert!(
                            messages(&schemas, &packet)
                                .iter()
                                .any(|item| item.sequence == ordinal
                                    && item.message.id == message_id)
                        );
                        queued_message_seen.set(true);
                        Err::<u32, ()>(())
                    },
                );
                assert!(matches!(
                    pending,
                    Err(crate::game_egress::SendError::Transport(()))
                ));
                assert!(!peer.egress.delivered_through(ordinal));
                reached_injected_panic.set(true);
                panic!("injected panic after detached peer and room mutations");
            })
        },
    );
    assert!(matches!(&result, Err(RoomError::Panicked)));
    assert!(
        reached_injected_panic.get(),
        "an earlier assertion must not pass as the injected panic"
    );
    assert!(detached.get());
    assert!(
        queued_message_seen.get(),
        "the failure must follow a real reliable queue write"
    );
    assert_eq!(
        state.rooms[&MATCH].players[0].info.name,
        "partially changed room"
    );
    assert!(state.rooms[&MATCH].members[&failed[0].conn].peer.is_none());
    assert!(state.rooms[&MATCH].members[&failed[1].conn].peer.is_some());

    assert!(
        state
            .finish(MATCH, RoomStage::Input, Some(failed[0].conn), result)
            .unwrap()
            .is_none()
    );
    assert!(!state.rooms.contains_key(&MATCH));
    assert_eq!(state.recent_faults.len(), 1);
    assert_eq!(state.recent_faults[0].reason, "panic");
    for (ticket, client) in failed.iter().zip(&failed_clients) {
        assert!(!state.routes.contains_key(&ticket.conn));
        assert!(!state.order.contains_key(&ticket.conn));
        assert!(state.links.sender(ticket.conn).is_none());
        let retry = client
            .cipher
            .seal(&Packet {
                conn: ticket.conn,
                kind: KIND_CONTROL,
                flags: SYN,
                ..Packet::default()
            })
            .unwrap();
        assert_eq!(
            state
                .links
                .receive(&retry, client.socket.local_addr().unwrap(), Duration::ZERO)
                .err(),
            Some(crate::game_connection::Rejected::UnknownConnection)
        );
    }
    assert_eq!(state.routes[&survivor.conn], survivor_id);
    assert_eq!(
        state.order.keys().copied().collect::<Vec<_>>(),
        [survivor.conn]
    );
    assert_eq!(state.next_input_peer, Some(survivor.conn));
    assert!(state.links.sender(survivor.conn).is_some());
    let room = &state.rooms[&survivor_id];
    assert_eq!(room.players[0].info.name, survivor_name);
    let peer = room.members[&survivor.conn].peer.as_ref().unwrap();
    assert!(peer.open);
    assert_eq!(peer.egress.oldest_pending_reliable(), survivor_pending);
    let fresh = state
        .open_match(
            [5, 10],
            GameMap::practice_range(),
            player(),
            None,
            Duration::ZERO,
        )
        .unwrap()
        .pop()
        .unwrap();
    let _fresh_client = activate(&mut state, &fresh);
    assert_eq!(state.rooms.len(), 2);
    assert!(state.routes.contains_key(&survivor.conn));
    assert!(state.order.contains_key(&survivor.conn));
}

#[test]
fn retirement_is_idempotent_and_removes_orphaned_routes_and_members() {
    for missing in ["peer", "route", "room"] {
        let mut state = State::new(Arc::new(fixture()), PatrolScript::load().unwrap());
        let tickets = state
            .open_match(
                MATCH,
                GameMap::practice_range(),
                roster(2),
                None,
                Duration::ZERO,
            )
            .unwrap();
        let survivor = [5, 9];
        let other = state
            .open_match(
                survivor,
                GameMap::practice_range(),
                player(),
                None,
                Duration::ZERO,
            )
            .unwrap();
        for ticket in &tickets {
            state
                .rooms
                .get_mut(&MATCH)
                .unwrap()
                .admit(ticket.conn)
                .unwrap();
            state.order.insert(ticket.conn, ());
        }
        state
            .rooms
            .get_mut(&survivor)
            .unwrap()
            .admit(other[0].conn)
            .unwrap();
        state.order.insert(other[0].conn, ());
        state.next_input_peer = Some(tickets[0].conn);
        match missing {
            "peer" => {
                state
                    .rooms
                    .get_mut(&MATCH)
                    .unwrap()
                    .members
                    .get_mut(&tickets[0].conn)
                    .unwrap()
                    .peer = None
            }
            "route" => {
                state.routes.remove(&tickets[0].conn);
            }
            "room" => {
                state.rooms.remove(&MATCH);
            }
            _ => unreachable!(),
        }
        state.retire_room(MATCH).unwrap();
        state.retire_room(MATCH).unwrap();
        for ticket in tickets {
            assert!(!state.routes.contains_key(&ticket.conn));
            assert!(!state.order.contains_key(&ticket.conn));
            assert!(!state.links.handoff_pending(ticket.conn, Duration::ZERO));
        }
        assert!(state.rooms.contains_key(&survivor));
        assert!(state.links.handoff_pending(other[0].conn, Duration::ZERO));
        assert_eq!(state.next_input_peer, Some(other[0].conn));
    }
}

#[test]
fn admission_rolls_back_new_reservations_without_revoking_the_collision() {
    let mut state = State::new(Arc::new(fixture()), PatrolScript::load().unwrap());
    let collision = Handoff::new(2, [9, 9]).unwrap();
    assert!(state.links.reserve_handoff(&collision, 2, Duration::ZERO));
    let error = state
        .open_match(
            MATCH,
            GameMap::practice_range(),
            roster(2),
            None,
            Duration::ZERO,
        )
        .err()
        .unwrap();
    assert!(error.get_ref().unwrap().is::<WorkerError>());
    assert!(state.rooms.is_empty());
    assert!(state.routes.is_empty());
    assert!(state.order.is_empty());
    assert!(!state.links.handoff_pending(1, Duration::ZERO));
    assert!(state.links.handoff_pending(2, Duration::ZERO));
    assert_eq!(state.next_connection, 1);
}

#[test]
fn conflicting_membership_is_worker_failure_not_silent_room_repair() {
    let mut state = State::new(Arc::new(fixture()), PatrolScript::load().unwrap());
    let ticket = state
        .open_match(
            MATCH,
            GameMap::practice_range(),
            player(),
            None,
            Duration::ZERO,
        )
        .unwrap()
        .pop()
        .unwrap();
    let other = [5, 9];
    state
        .open_match(
            other,
            GameMap::practice_range(),
            player(),
            None,
            Duration::ZERO,
        )
        .unwrap();
    state.routes.insert(ticket.conn, other);
    assert!(matches!(
        state.retire_room(MATCH),
        Err(WorkerError::Routing(_))
    ));
    assert!(state.rooms.contains_key(&MATCH));
    assert!(state.rooms.contains_key(&other));
    assert!(state.links.handoff_pending(ticket.conn, Duration::ZERO));
    // The ordinary FIN/expiry path must not mutate a duplicated owner either.
    state.routes.insert(ticket.conn, MATCH);
    state.rooms.get_mut(&other).unwrap().members.insert(
        ticket.conn,
        RoomMember {
            slot: 0,
            peer: None,
        },
    );
    assert!(matches!(
        state.retire(ticket.conn),
        Err(WorkerError::Routing(_))
    ));
    assert_eq!(state.routes[&ticket.conn], MATCH);
    assert!(state.rooms[&MATCH].members.contains_key(&ticket.conn));
    assert!(state.rooms[&other].members.contains_key(&ticket.conn));
}

#[test]
fn recent_faults_are_bounded_while_new_rooms_keep_opening() {
    let mut service = GameService::bind(local(), Arc::new(fixture())).unwrap();
    for index in 0..70 {
        let id = [99, index];
        service
            .open_match(id, GameMap::practice_range(), player())
            .unwrap();
        inject(&service, id, RoomStage::Debug, InjectedFault::Error);
        service.debug_snapshot().unwrap();
    }
    let recorded = faults(&service);
    assert_eq!(recorded.len(), 64);
    assert_eq!(recorded.first().unwrap().id, [99, 6]);
    assert_eq!(recorded.last().unwrap().id, [99, 69]);
    service.stop().unwrap();
}

#[test]
#[ignore = "requires an authorized base 104319 Practice Range runtime"]
fn prepared_fault_keeps_other_room_input_world_and_patrol_alive() {
    use super::benchmark::{Driver, PeerCounts};
    use crate::{
        game_bots::BOT_ENTITY, game_heroes::SOLDIER, scene_assets::SceneAssets,
        script_player::fixture_hitscan_factory,
    };

    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let schemas = Arc::new(Schemas::load(runtime.join("data/game_schemas_174.json")).unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let factory = Arc::new(fixture_hitscan_factory(0.125));
    let prepare = || {
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        assert_eq!(
            scene.scripted_bots(),
            0,
            "this regression needs the base runtime lane"
        );
        let [x, y, z] = scene.map().spawn_point(0, 0).unwrap().0;
        let (bots, _) = scene.bot_simulation();
        bots.body_mut(BOT_ENTITY + 1).unwrap().position = [x - 4.0, y, z];
        let factory = Arc::clone(&factory);
        scene.owner_body_factory = Some(Arc::new(move |hero, spawn| {
            if hero.guid == SOLDIER {
                Ok(Some(Box::new(factory.start(spawn)?)))
            } else {
                Ok(None)
            }
        }));
        scene
    };
    for (stage, fault, reason) in [
        (RoomStage::Bots, InjectedFault::BrokenBot, "combat"),
        (RoomStage::FinishTick, InjectedFault::BrokenBot, "combat"),
        (RoomStage::Input, InjectedFault::Panic, "panic"),
    ] {
        let mut service = GameService::bind(local(), Arc::clone(&schemas)).unwrap();
        let failed = service
            .open_prepared_practice(
                MATCH,
                prepare(),
                player().pop().unwrap(),
                PlayerCard::default(),
            )
            .unwrap();
        let other_id = [5, 9];
        let survivor = service
            .open_prepared_practice(
                other_id,
                prepare(),
                player().pop().unwrap(),
                PlayerCard::default(),
            )
            .unwrap();
        let mut failed_driver = Driver::join(Client::new(service.local_addr(), &failed), &schemas);
        let mut survivor_driver =
            Driver::join(Client::new(service.local_addr(), &survivor), &schemas);
        // Both rooms reach actual body/weapon replication and command handling.
        for _ in 0..4 {
            failed_driver.step(&schemas, None);
            survivor_driver.step(&schemas, None);
        }
        let before = service.debug_snapshot().unwrap();
        let survivor_hex = format!("{:016X}{:016X}", other_id[0], other_id[1]);
        let positions: Vec<_> = before
            .rooms
            .iter()
            .find(|room| room.id == survivor_hex)
            .unwrap()
            .actors
            .iter()
            .filter(|actor| matches!(actor.kind, game_debug::ActorKind::Bot) && actor.script_active)
            .map(|actor| (actor.entity, actor.position))
            .collect();
        assert!(!positions.is_empty());
        inject(&service, MATCH, stage, fault);
        if stage == RoomStage::Input {
            // Send fresh commands without waiting for another failed-room packet; the next
            // worker input turn, rather than a test helper, reaches the panic.
            failed_driver.submit_input();
        }
        let records = wait_for_fault(&service);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].id, MATCH);
        assert_eq!(records[0].stage, stage);
        assert_eq!(records[0].reason, reason);
        assert!(!service.seat_exists(&failed).unwrap());
        assert!(service.seat_exists(&survivor).unwrap());
        let mut counts = PeerCounts::default();
        let deadline = Instant::now() + Duration::from_secs(4);
        while Instant::now() < deadline {
            survivor_driver.step(&schemas, Some(&mut counts));
        }
        let report = counts.report();
        assert!(report["command_ack_progress"].as_u64().unwrap() > 0);
        assert!(report["world_packets"].as_u64().unwrap() > 1);
        assert!(report["distinct_hit_notices"].as_u64().unwrap() > 0);
        let after = service.debug_snapshot().unwrap();
        assert_eq!(after.rooms.len(), 1);
        assert!(after.tick > before.tick);
        let live = after
            .rooms
            .iter()
            .find(|room| room.id == survivor_hex)
            .unwrap();
        assert!(
            live.actors.iter().any(|actor| positions
                .iter()
                .any(|(entity, position)| *entity == actor.entity && *position != actor.position)),
            "surviving room patrol stopped advancing"
        );
        let fresh = service
            .open_prepared_practice(
                [5, 10],
                prepare(),
                player().pop().unwrap(),
                PlayerCard::default(),
            )
            .unwrap();
        let mut fresh_driver = Driver::join(Client::new(service.local_addr(), &fresh), &schemas);
        fresh_driver.step(&schemas, None);
        survivor_driver.step(&schemas, None);
        assert_eq!(service.debug_snapshot().unwrap().rooms.len(), 2);
        service.stop().unwrap();
    }
}
