use super::*;

mod benchmark;
mod harness;
mod isolation;
mod junkenstein_validation;
mod same_schedule;
mod world_harness;

pub use harness::*;
pub use world_harness::*;

pub(super) use crate::{
    bits::{BitReader, BitWriter},
    game_content::PRACTICE_RANGE_MAP,
    game_link::{FIN, KIND_CONTROL, LinkCipher, MAX_DATAGRAM, ReceiveWindow, SYN},
    game_messages::{read_channel, write_channel},
};
pub(super) use std::{collections::HashSet, net::UdpSocket as ClientSocket};

#[test]
fn worker_serves_a_match_seat_and_retries_before_map_entry() {
    exercise_loading(fixture());
}

#[test]
fn debug_snapshot_excludes_unprepared_rooms_and_stops_with_the_worker() {
    let mut server = GameService::bind(local(), Arc::new(fixture())).unwrap();
    let idle = server.debug_snapshot().unwrap();
    assert!(idle.rooms.is_empty());
    assert!(!idle.truncated);
    assert!(idle.timing.is_none());
    server
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap();
    assert!(server.debug_snapshot().unwrap().rooms.is_empty());
    server.stop().unwrap();
    assert_eq!(
        server.debug_snapshot().err().unwrap().kind(),
        io::ErrorKind::BrokenPipe
    );
}

#[test]
fn timing_opt_in_reports_empty_histograms_while_no_world_is_running() {
    let mut server = GameService::bind_with_timing(local(), Arc::new(fixture()), true).unwrap();
    let idle = server.debug_snapshot().unwrap();
    let timing = idle.timing.unwrap();
    assert_eq!(timing.tick_duration_us.samples, 0);
    assert_eq!(timing.schedule_lateness_us.samples, 0);
    server
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap();
    let ready = server.debug_snapshot().unwrap();
    assert!(ready.rooms.is_empty());
    let timing = ready.timing.unwrap();
    assert_eq!(timing.tick_duration_us.samples, 0);
    assert_eq!(timing.schedule_lateness_us.samples, 0);
    assert_eq!(timing.input_budget.turns, 0);
    server.stop().unwrap();
}

#[test]
fn lua_fault_in_one_room_leaves_sibling_room_and_patrol_alive() {
    use crate::{
        game_bots::{BOT_ENTITY, Bot},
        game_service::room::InjectedFault,
        scene_assets::PreparedScene,
    };

    for (label, faulty_source) in [
        (
            "think_loop",
            "return { abi = 2, think = function() while true do end end }",
        ),
        (
            "think_oom",
            r#"return { abi = 2, think = function() local s = "0123456789abcdef"; for i = 1, 16 do s = s .. s end; return {} end }"#,
        ),
        (
            "think_runtime_error",
            "return { abi = 2, think = function() local x = nil; return x.missing end }",
        ),
    ] {
        let schemas = Arc::new(fixture());
        let mut state = State::new(schemas, PatrolScript::load().unwrap());
        let socket = UdpSocket::bind(local()).unwrap();
        let failed_id = MATCH;
        let survivor_id = [5, 9];

        state
            .open_match(
                failed_id,
                GameMap::practice_range(),
                player(),
                Some(WorldStart {
                    scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                        [0.0, 0.0, 10.0],
                    )])),
                    card: PlayerCard::default(),
                }),
                Duration::ZERO,
            )
            .unwrap();
        state
            .rooms
            .get_mut(&failed_id)
            .unwrap()
            .world
            .as_mut()
            .unwrap()
            .patrol = PatrolScript::from_source(faulty_source).unwrap();

        state
            .open_match(
                survivor_id,
                GameMap::practice_range(),
                player(),
                Some(WorldStart {
                    scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                        [0.0, 0.0, 10.0],
                    )])),
                    card: PlayerCard::default(),
                }),
                Duration::ZERO,
            )
            .unwrap();

        for _ in 0..220 {
            state.tick(&socket, Duration::ZERO, 0).unwrap();
        }

        // Lua faults are contained per-bot inside PreparedScene::step_bots:
        // neither room is retired, failed room's bot stops cleanly, and survivor advances.
        assert!(state.rooms.contains_key(&failed_id), "{label}");
        assert!(state.rooms.contains_key(&survivor_id), "{label}");
        assert!(state.recent_faults.is_empty(), "{label}");
        let failed_bot = state.rooms[&failed_id]
            .world
            .as_ref()
            .unwrap()
            .scene
            .bots()
            .unwrap()
            .find(|bot| bot.entity == BOT_ENTITY)
            .unwrap();
        assert!(!failed_bot.patrol_active(), "{label}");
        assert_eq!(failed_bot.position, [0.0; 3], "{label}");

        let survivor_before = state.rooms[&survivor_id]
            .world
            .as_ref()
            .unwrap()
            .scene
            .bots()
            .unwrap()
            .find(|bot| bot.entity == BOT_ENTITY)
            .unwrap()
            .position;
        assert_ne!(survivor_before, [0.0; 3], "{label}");

        // A room-level Bots-stage fault retires only the faulted room; survivor keeps patrolling.
        state.rooms.get_mut(&failed_id).unwrap().injection =
            Some((RoomStage::Bots, InjectedFault::BrokenBot));
        state.tick(&socket, Duration::ZERO, 0).unwrap();
        assert!(!state.rooms.contains_key(&failed_id), "{label}");
        assert!(state.rooms.contains_key(&survivor_id), "{label}");
        assert_eq!(state.recent_faults.len(), 1, "{label}");
        let survivor_after = state.rooms[&survivor_id]
            .world
            .as_ref()
            .unwrap()
            .scene
            .bots()
            .unwrap()
            .find(|bot| bot.entity == BOT_ENTITY)
            .unwrap()
            .position;
        assert_ne!(survivor_after, survivor_before, "{label}");
    }
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack"]
fn prepared_rooms_keep_patrol_script_state_local() {
    use crate::{ai_script::AiActorSnapshot, patrol_script::WaitEvent, scene_assets::SceneAssets};

    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let schemas = Arc::new(Schemas::load(runtime.join("data/game_schemas_174.json")).unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let stateful = PatrolScript::from_source(
        r#"
        local count = 10
        return {
            abi = 2,
            think = function()
                count = count + 1
                return { { kind = "wait", seconds = count } }
            end,
        }
        "#,
    )
    .unwrap();
    let mut state = State::new(schemas, stateful);
    let second = [MATCH[0], MATCH[1] + 1];
    for id in [MATCH, second] {
        let map = GameMap::practice_range();
        let scene = assets.prepare(map.clone()).unwrap();
        state
            .open_match(
                id,
                map,
                player(),
                Some(WorldStart {
                    scene: Box::new(scene),
                    card: PlayerCard::default(),
                }),
                Duration::ZERO,
            )
            .unwrap();
    }
    let wait = |id, frame| {
        state.rooms[&id]
            .world
            .as_ref()
            .unwrap()
            .patrol
            .wait(
                AiActorSnapshot::patrol_fixture(0, 1),
                WaitEvent::Spawn,
                frame,
            )
            .unwrap()
    };
    assert_eq!(wait(MATCH, FIRST_TICK), 11.0);
    assert_eq!(wait(second, FIRST_TICK), 3.0);
    assert_eq!(wait(MATCH, FIRST_TICK + 1), 12.0);
    assert_eq!(wait(second, FIRST_TICK + 1), 3.0);
}

#[test]
#[ignore = "requires the separately prepared 104319 game schemas"]
fn worker_serves_loading_using_the_retail_schemas() {
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let schemas = Schemas::load(root.join("data/game_schemas_174.json")).unwrap();
    exercise_loading(schemas);
}

#[test]
fn canceled_seat_is_rejected_before_tick_and_released() {
    let mut server = GameService::bind(local(), Arc::new(fixture())).unwrap();
    let ticket = server
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap()
        .pop()
        .unwrap();
    assert!(server.handoff_pending(ticket.conn).unwrap());
    assert!(server.cancel_handoff(ticket.conn).unwrap());
    assert!(!server.handoff_pending(ticket.conn).unwrap());
    let mut client = Client::new(server.local_addr(), &ticket);
    client
        .socket
        .set_read_timeout(Some(Duration::from_millis(100)))
        .unwrap();
    client.send(SYN, Vec::new());
    let mut buffer = [0; MAX_DATAGRAM];
    let error = client.socket.recv(&mut buffer).unwrap_err();
    assert!(matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    ));
    let retry = server
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap()
        .pop()
        .unwrap();
    assert_ne!(ticket.conn, retry.conn);
    server.stop().unwrap();
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range scene"]
fn busy_peers_resume_in_round_robin_order_after_the_global_input_budget() {
    use crate::{commands::Command, scene_assets::SceneAssets};

    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let mut state = State::new(Arc::new(fixture()), PatrolScript::load().unwrap());
    state.timing = Some(WorkerTiming::new());
    // Five synthetic peers share a prepared fixture solely to test the
    // worker scheduler. The actual admission route remains solo.
    let players: Vec<_> = (0..5)
        .map(|slot| Player {
            info: player().pop().unwrap(),
            entity: player_entity(slot),
            gone: false,
        })
        .collect();
    state.rooms.insert(
        MATCH,
        Room {
            map: scene.map().clone(),
            members: IndexMap::new(),
            players,
            debug_control: room::RoomDebugControl::new(FIRST_TICK, Duration::ZERO),
            injection: None,
            world: Some(RoomWorld {
                scene: Box::new(scene),
                card: PlayerCard::default(),
                patrol: PatrolScript::load().unwrap(),
                patrol_generation: 0,
            }),
        },
    );
    let first = FIRST_TICK + 1;
    let commands: Vec<_> = (first..first + 32)
        .map(|frame| Command {
            frame,
            ..Command::default()
        })
        .collect();
    for slot in 0..state.rooms[&MATCH].players.len() {
        let room = &state.rooms[&MATCH];
        let connection = slot as u32 + 1;
        let viewer = room.appearance(slot).unwrap();
        let mut peer = Peer::new(viewer.entity);
        peer.world
            .start(&room.world.as_ref().unwrap().scene, &viewer, FIRST_TICK)
            .unwrap();
        peer.world.queue_commands(FIRST_TICK, &commands);
        state.routes.insert(connection, MATCH);
        state.order.insert(connection, ());
        state.rooms.get_mut(&MATCH).unwrap().members.insert(
            connection,
            RoomMember {
                slot,
                peer: Some(peer),
            },
        );
    }
    let acks = |state: &State| {
        state
            .order
            .keys()
            .map(|connection| {
                state.rooms[&MATCH].members[connection]
                    .peer
                    .as_ref()
                    .unwrap()
                    .world
                    .command_ack(FIRST_TICK)
            })
            .collect::<Vec<_>>()
    };
    assert!(state.advance_input(Duration::ZERO).unwrap());
    assert_eq!(
        acks(&state),
        [first + 15, first + 15, first + 15, first + 15, FIRST_TICK]
    );
    assert!(state.advance_input(Duration::ZERO).unwrap());
    assert_eq!(
        acks(&state),
        [first + 31, first + 31, first + 31, first + 15, first + 15]
    );
    assert!(!state.advance_input(Duration::ZERO).unwrap());
    assert_eq!(acks(&state), [first + 31; 5]);
    let timing = state.debug_snapshot().unwrap().timing.unwrap();
    assert_eq!(timing.input_budget.turns, 3);
    assert_eq!(timing.input_budget.used, 160);
    assert_eq!(timing.input_budget.peer_limit_hits, 10);
    assert_eq!(timing.input_budget.turn_limit_hits, 2);
    assert_eq!(timing.input_budget.turns_with_backlog, 2);
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range scene"]
fn prepared_world_waits_for_ownership_and_retries_baselines_before_scripts() {
    exercise_prepared_world(PreparedFixture::Stock);
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range scene without bot VM graphs"]
fn prepared_worker_retries_a_concrete_owner_vm_on_encrypted_loopback() {
    exercise_prepared_world(PreparedFixture::Owner);
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range scene without bot VM graphs"]
fn prepared_worker_delivers_a_volley_hit_and_replaces_the_bot_over_encrypted_udp() {
    exercise_prepared_world(PreparedFixture::Combat);
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range scene without bot VM graphs"]
fn prepared_worker_delivers_helix_flight_damage_and_predicted_release_over_encrypted_udp() {
    exercise_prepared_world(PreparedFixture::Projectile);
}

#[test]
#[ignore = "requires a tagged 104319 Practice Range scene without bot VM graphs"]
fn prepared_worker_retries_the_stock_soldier_vm_on_encrypted_loopback() {
    exercise_prepared_world(PreparedFixture::StockOwner);
}

#[test]
#[ignore = "requires a tagged 104319 Practice Range scene without bot VM graphs"]
fn prepared_worker_delivers_stock_helix_over_encrypted_udp() {
    exercise_prepared_world(PreparedFixture::StockProjectile);
}

#[test]
#[ignore = "requires a tagged 104319 Practice Range scene without bot VM graphs"]
fn prepared_worker_delivers_stock_biotic_canister_and_field() {
    exercise_prepared_world(PreparedFixture::StockBiotic);
}

#[test]
#[ignore = "requires the prepared 104319 Junkenstein map, collision and graph data"]
fn junkenstein_worker_delivers_castle_gate_and_mode_frame() {
    use crate::game_content::{JUNKENSTEIN_GATE, JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog};
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let data = runtime.join("data");
    let schemas = Arc::new(Schemas::load(data.join("game_schemas_174.json")).unwrap());
    let maps = MapCatalog::load(data.join("game_maps_174.json")).unwrap();
    let assets = crate::scene_assets::SceneAssets::load(runtime).unwrap();
    let mut server = GameService::bind(local(), Arc::clone(&schemas)).unwrap();
    for index in [0x3c, 0x43, 0x16e] {
        let card = JunkensteinCard::from_index(index).unwrap();
        let mode = card.mode;
        let map = maps
            .game_map(
                JUNKENSTEIN_MAP,
                mode,
                "Junkenstein's Revenge",
                false,
                &[card.players, 0],
            )
            .unwrap();
        let scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        // Verify that encrypted delivery preserves the complete prepared HUD.
        // Authored descriptor/parent structure is covered by the all-card test.
        let expected_mode = (
            scene.mode_ui().bit_len(),
            scene.mode_ui().as_bytes().to_vec(),
        );
        let id = [MATCH[0], u64::from(index)];
        let debug_id = format!("{:016X}{:016X}", id[0], id[1]);
        let encounter = || {
            server
                .debug_snapshot()
                .unwrap()
                .rooms
                .into_iter()
                .find(|room| room.id == debug_id)
                .unwrap()
                .encounter
        };
        let ticket = server
            .open_prepared_solo(id, scene, player().pop().unwrap(), PlayerCard::default())
            .unwrap();
        let mut client = Client::new(server.local_addr(), &ticket);
        client.handshake();
        let load = client.receive();
        assert!(client.window.accept(load.seq));
        let messages = messages(&schemas, &load);
        assert_eq!(messages[0].message.id, 20300);
        assert_eq!(
            messages[0].message.fields[&0x4a8],
            Value::Unsigned(JUNKENSTEIN_MAP)
        );
        assert!(encounter().is_none());
        let mut payload = BitWriter::new();
        let loaded = encode(&schemas, 21602, &Fields::new()).unwrap();
        write_channel(&mut payload, &[(0, loaded)], &[]).unwrap();
        client.send(ACK, payload.into_bytes());

        let deadline = Instant::now() + Duration::from_secs(4);
        let mut gate_created = false;
        let mut presented = false;
        let mut controller_ready = false;
        loop {
            let packet = client.receive();
            assert!(client.window.accept(packet.seq));
            for record in world_records(&schemas, &packet) {
                if record.entity == (0x8000_0000 | JUNKENSTEIN_GATE) && record.is_create() {
                    gate_created = true;
                }
                controller_ready |= record.entity == player_entity(0) && record.script.is_some();
                if let Some(frame) = record
                    .script
                    .filter(|frame| record.entity == GAME_MODE_ENTITY && frame.first == 0)
                {
                    assert_eq!(frame.width, expected_mode.0);
                    assert_eq!(frame.data, expected_mode.1);
                    presented = true;
                }
            }
            client.send(ACK, Vec::new());
            if gate_created && presented && controller_ready {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "waiting for castle gate and mode {mode:x}"
            );
        }
        let started = encounter().expect("encounter clock after world entry");
        assert!(
            started.elapsed_ms < 2_000,
            "clock must be relative to world entry"
        );
        assert!(!started.countdown_started);
        let hero = assets.heroes().find_hero("Cassidy").unwrap().guid;
        send_game_message(
            &mut client,
            &schemas,
            1,
            crate::game_parameters::PICK_HERO,
            hero.to_le_bytes().to_vec(),
        );
        let deadline = Instant::now() + Duration::from_secs(3);
        let mut lost = None;
        loop {
            let packet = client.receive();
            let delta = world_records(&schemas, &packet)
                .into_iter()
                .find(|record| record.entity == GAME_MODE_ENTITY)
                .and_then(|record| record.script)
                .filter(|script| {
                    if script.first == 0 {
                        return false;
                    }
                    let mut bits = script.bits();
                    bits.bits(2).ok() == Some(0)
                        && bits.w_u16().ok() == Some(2)
                        && bits.bits(2).ok() == Some(0)
                        && bits.w_u16().ok() == Some(0)
                        && bits.bit().ok() == Some(true)
                        && bits.w_var().ok() == Some(1303)
                });
            if let Some(delta) = delta {
                assert_eq!(delta.span, 0);
                let mut bits = delta.bits();
                assert_eq!(bits.bits(2).unwrap(), 0);
                assert_eq!(bits.w_u16().unwrap(), 2);
                assert_eq!(bits.bits(2).unwrap(), 0);
                assert_eq!(bits.w_u16().unwrap(), 0);
                assert!(bits.bit().unwrap());
                assert_eq!(bits.w_var().unwrap(), 1303);
                let value = match bits.bits(4).unwrap() {
                    5 => bits.bits(4).unwrap() as f32,
                    8 => bits.f32().unwrap(),
                    tag => panic!("unexpected countdown value type {tag}"),
                };
                assert!((7.5..=8.0).contains(&value));
                if let Some(first) = lost {
                    assert_eq!(delta, first);
                    assert!(client.window.accept(packet.seq));
                    client.send(ACK, Vec::new());
                    let live = encounter().unwrap();
                    assert!(live.countdown_started && live.elapsed_ms > started.elapsed_ms);
                    break;
                }
                lost = Some(delta);
            } else {
                assert!(client.window.accept(packet.seq));
                client.send(ACK, Vec::new());
            }
            assert!(
                Instant::now() < deadline,
                "waiting for countdown retransmit in mode {mode:x}"
            );
        }
    }
    server.stop().unwrap();
}

const POLICY_STAY: &str = r#"
return {
    abi = 2,
    think = function(snapshot, events)
        local event = events[1]
        if event.kind == "think" then
            return { { kind = "patrol_slot", slot = 0 } }
        end
        return { { kind = "wait", seconds = 3.0 } }
    end,
}
"#;

const POLICY_MOVE: &str = r#"
return {
    abi = 2,
    think = function(snapshot, events)
        local event = events[1]
        if event.kind == "think" then
            return { { kind = "patrol_slot", slot = 1 } }
        end
        return { { kind = "wait", seconds = 0.016 } }
    end,
}
"#;

#[test]
fn hot_reload_updates_active_rooms_future_rooms_and_digest() {
    use crate::{
        game_bots::{BOT_ENTITY, Bot},
        scene_assets::PreparedScene,
    };
    use sha2::{Digest, Sha256};

    let schemas = Arc::new(fixture());
    let mut state = State::new(schemas, PatrolScript::from_source(POLICY_STAY).unwrap());
    let socket = UdpSocket::bind(local()).unwrap();
    let room_a = [5, 8];
    let room_b = [5, 9];

    state
        .open_match(
            room_a,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                    [0.0, 0.0, 10.0],
                )])),
                card: PlayerCard::default(),
            }),
            Duration::ZERO,
        )
        .unwrap();

    for _ in 0..187 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }
    let pos_before = state.rooms[&room_a]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap()
        .position;
    assert_eq!(pos_before, [0.0; 3]);

    let report = state.reload_patrol(POLICY_MOVE).unwrap();
    let expected_digest = format!("{:x}", Sha256::digest(POLICY_MOVE.as_bytes()));
    assert_eq!(report.generation, 1);
    assert_eq!(report.abi_version, crate::ai_script::AI_ABI_VERSION);
    assert_eq!(report.digest, expected_digest);
    assert_eq!(report.active_rooms, 1);
    assert!(report.changed);
    assert_eq!(state.active_patrol_source.as_ref(), POLICY_MOVE);
    assert_eq!(
        state.rooms[&room_a]
            .world
            .as_ref()
            .unwrap()
            .patrol_generation,
        1
    );

    // Identical source is idempotent and does not increment generation.
    let same = state.reload_patrol(POLICY_MOVE).unwrap();
    assert_eq!(same.generation, 1);
    assert!(!same.changed);

    for _ in 0..5 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }
    let pos_after = state.rooms[&room_a]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap()
        .position;
    assert!(pos_after[2] > 0.0, "active room should use reloaded policy");

    // Room B opens after preloaded_patrol was consumed by Room A; it must use active_patrol_source.
    state
        .open_match(
            room_b,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                    [0.0, 0.0, 10.0],
                )])),
                card: PlayerCard::default(),
            }),
            Duration::ZERO,
        )
        .unwrap();
    for _ in 0..5 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }
    let pos_b = state.rooms[&room_b]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap()
        .position;
    assert!(
        pos_b[2] > 0.0,
        "newly opened room should use active_patrol_source (0.016s wait)"
    );
    assert_eq!(
        state.rooms[&room_b]
            .world
            .as_ref()
            .unwrap()
            .patrol_generation,
        1
    );

    // End-to-end GameService channel check.
    let mut service = GameService::bind(local(), Arc::new(fixture())).unwrap();
    service
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap();
    let svc_report = service.reload_ai_script(POLICY_MOVE).unwrap();
    assert_eq!(svc_report.generation, 1);
    assert_eq!(svc_report.digest, expected_digest);
    assert_eq!(svc_report.active_rooms, 0);
    assert!(svc_report.changed);
    let status = service.ai_policy_status().unwrap();
    assert_eq!(status.generation, 1);
    assert_eq!(status.digest, expected_digest);
    service.stop().unwrap();
}

#[test]
fn hot_reload_rejects_invalid_scripts_and_rolls_back_atomically() {
    use crate::{
        ai_script::{AI_MAX_SOURCE_BYTES, AiExecutionPhase, AiScriptError},
        game_bots::{BOT_ENTITY, Bot},
        patrol_script::DEFAULT_SOURCE,
        scene_assets::PreparedScene,
    };

    let schemas = Arc::new(fixture());
    let default_script = PatrolScript::load().unwrap();
    let digest_0 = default_script.digest();
    let digest_0_hex = default_script.digest_hex();
    let mut state = State::new(schemas, default_script);
    let socket = UdpSocket::bind(local()).unwrap();
    let room_a = [5, 8];

    state
        .open_match(
            room_a,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                    [0.0, 0.0, 10.0],
                )])),
                card: PlayerCard::default(),
            }),
            Duration::ZERO,
        )
        .unwrap();
    for _ in 0..10 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }

    type InvalidCase<'a> = (&'a str, &'a str, fn(&AiScriptError) -> bool);
    let oversized = format!("-- {}", "a".repeat(AI_MAX_SOURCE_BYTES));
    let invalid_cases: Vec<InvalidCase<'_>> = vec![
        ("syntax", "return {", |err| {
            matches!(err, AiScriptError::Syntax(_))
        }),
        (
            "abi_mismatch",
            "return { abi = 1, think = function() return {} end }",
            |err| matches!(err, AiScriptError::UnsupportedAbi { found: 1, .. }),
        ),
        ("missing_think", "return { abi = 2 }", |err| {
            matches!(err, AiScriptError::InvalidAbi(_))
        }),
        (
            "init_oom",
            r#"local s = "0123456789abcdef"; for i = 1, 16 do s = s .. s end; return { abi = 2, think = function() return {} end }"#,
            |err| matches!(err, AiScriptError::MemoryLimitExceeded),
        ),
        ("init_budget", "while true do end", |err| {
            matches!(
                err,
                AiScriptError::InstructionBudgetExceeded {
                    phase: AiExecutionPhase::Init
                }
            )
        }),
        (
            "think_loop",
            "return { abi = 2, think = function() while true do end end }",
            |err| {
                matches!(
                    err,
                    AiScriptError::InstructionBudgetExceeded {
                        phase: AiExecutionPhase::Think
                    }
                )
            },
        ),
        (
            "think_oom",
            r#"return { abi = 2, think = function() local s = "0123456789abcdef"; for i = 1, 16 do s = s .. s end; return {} end }"#,
            |err| matches!(err, AiScriptError::MemoryLimitExceeded),
        ),
        ("empty", "", |err| {
            matches!(err, AiScriptError::InvalidAbi(_))
        }),
        ("whitespace", "   \n\t  ", |err| {
            matches!(err, AiScriptError::InvalidAbi(_))
        }),
        ("oversized", oversized.as_str(), |err| {
            matches!(err, AiScriptError::SourceTooLarge { .. })
        }),
    ];

    for (label, candidate, check) in invalid_cases {
        let err = state.reload_patrol(candidate).unwrap_err();
        let script_err = err
            .get_ref()
            .and_then(|source| source.downcast_ref::<AiScriptError>())
            .unwrap_or_else(|| panic!("{label}: expected AiScriptError source, got {err}"));
        assert!(
            check(script_err),
            "{label}: unexpected error {script_err:?}"
        );
        assert_eq!(state.patrol_reload_generation, 0, "{label}");
        assert_eq!(state.patrol_policy_digest, digest_0, "{label}");
        assert_eq!(
            state.active_patrol_source.as_ref(),
            DEFAULT_SOURCE,
            "{label}"
        );
        assert!(state.recent_faults.is_empty(), "{label}");
        assert!(state.rooms.contains_key(&room_a), "{label}");
        assert_eq!(
            state.rooms[&room_a]
                .world
                .as_ref()
                .unwrap()
                .patrol_generation,
            0,
            "{label}"
        );
    }

    for _ in 0..210 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }
    let bot = state.rooms[&room_a]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap();
    assert!(bot.patrol_active());
    assert!(
        bot.position[2] > 0.0,
        "room should continue patrolling with rolled-back original script"
    );

    // Worker stays alive and handles subsequent operations after rejecting invalid reloads.
    let mut service = GameService::bind(local(), Arc::new(fixture())).unwrap();
    assert!(service.reload_ai_script("return {").is_err());
    assert!(service.reload_ai_script("").is_err());
    assert!(service.reload_ai_script(oversized).is_err());
    let status = service.ai_policy_status().unwrap();
    assert_eq!(status.generation, 0);
    assert_eq!(status.digest, digest_0_hex);
    service
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap();
    assert_eq!(service.reload_ai_script(POLICY_MOVE).unwrap().generation, 1);
    service.stop().unwrap();
}

#[test]
fn hot_reload_partial_staging_failure_leaves_all_rooms_and_digest_unchanged() {
    use crate::{
        ai_script::AiScriptError,
        game_bots::{BOT_ENTITY, Bot},
        scene_assets::PreparedScene,
    };

    let schemas = Arc::new(fixture());
    let mut state = State::new(schemas, PatrolScript::load().unwrap());
    let initial = state.reload_patrol(POLICY_STAY).unwrap();
    assert_eq!(initial.generation, 1);
    let stay_digest = state.patrol_policy_digest;
    let socket = UdpSocket::bind(local()).unwrap();
    let room_a = [5, 8];
    let room_b = [5, 9];
    let room_c = [5, 10];

    for id in [room_a, room_b] {
        state
            .open_match(
                id,
                GameMap::practice_range(),
                player(),
                Some(WorldStart {
                    scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                        [0.0, 0.0, 10.0],
                    )])),
                    card: PlayerCard::default(),
                }),
                Duration::ZERO,
            )
            .unwrap();
    }
    for _ in 0..187 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }

    // Fail on stage index 2 (Room B) after index 0 (next_preloaded) and index 1 (Room A) succeeded.
    let err = state
        .reload_patrol_with(POLICY_MOVE, |stage_idx, candidate| {
            if stage_idx == 2 {
                Err(AiScriptError::MemoryLimitExceeded)
            } else {
                PatrolScript::from_source_checked(candidate)
            }
        })
        .unwrap_err();
    let script_err = err
        .get_ref()
        .and_then(|source| source.downcast_ref::<AiScriptError>())
        .unwrap();
    assert_eq!(*script_err, AiScriptError::MemoryLimitExceeded);
    assert_eq!(state.patrol_reload_generation, 1);
    assert_eq!(state.patrol_policy_digest, stay_digest);
    assert_eq!(state.active_patrol_source.as_ref(), POLICY_STAY);

    // Tick both rooms past the 3s spawn wait: neither should adopt POLICY_MOVE (slot 1).
    for _ in 0..33 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }
    for id in [room_a, room_b] {
        let world = state.rooms[&id].world.as_ref().unwrap();
        assert_eq!(world.patrol_generation, 1);
        let bot = world
            .scene
            .bots()
            .unwrap()
            .find(|bot| bot.entity == BOT_ENTITY)
            .unwrap();
        assert!(bot.patrol_active());
        assert_eq!(bot.position, [0.0; 3]);
    }

    // Newly opened Room C after aborted reload must also use POLICY_STAY, not POLICY_MOVE.
    state
        .open_match(
            room_c,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                    [0.0, 0.0, 10.0],
                )])),
                card: PlayerCard::default(),
            }),
            Duration::ZERO,
        )
        .unwrap();
    for _ in 0..5 {
        state.tick(&socket, Duration::ZERO, 0).unwrap();
    }
    let bot_c = state.rooms[&room_c]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap();
    assert_eq!(bot_c.position, [0.0; 3]);
    assert_eq!(
        state.rooms[&room_c]
            .world
            .as_ref()
            .unwrap()
            .patrol_generation,
        1
    );
}

#[test]
fn hot_reload_never_resurrects_stale_body_id_handles() {
    use crate::{
        ai_script::{
            AiActorSnapshot, AiEvent, AiScript, AiScriptError, AiSnapshot, AiTargetSnapshot,
            validate_commands_against_simulation,
        },
        contact_world::ContactWorld,
        game_bots::Bot,
        game_combat::PlayerBodyState,
        game_heroes::SOLDIER,
        game_replication::BodyId,
        mover::Mover,
        mover_settings::MoverSettings,
        scene_assets::PreparedScene,
    };

    const TARGET_SELECTOR_LUA: &str = r#"
    return {
        abi = 2,
        think = function(snapshot, events)
            if #snapshot.targets > 0 then
                local target = snapshot.targets[1]
                return {
                    {
                        kind = "select_target",
                        role = "enemy",
                        entity = target.entity,
                        generation = target.generation,
                    },
                }
            end
            local event = events[1]
            if event.kind == "think" then
                return {
                    {
                        kind = "patrol_slot",
                        slot = (snapshot.actor.patrol.route_index + 1) % snapshot.actor.patrol.route_count,
                    },
                }
            end
            return { { kind = "wait", seconds = 3.0 } }
        end,
    }
    "#;

    let schemas = Arc::new(fixture());
    let mut state = State::new(schemas, PatrolScript::load().unwrap());
    let room_id = [5, 8];
    state
        .open_match(
            room_id,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                    [0.0, 0.0, 10.0],
                )])),
                card: PlayerCard::default(),
            }),
            Duration::ZERO,
        )
        .unwrap();

    let contact_world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let owner = 0xa000_0100;
    let wire = owner | 1;
    let make_state = |id: BodyId, pos: [f64; 3]| {
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::clone(&contact_world),
            pos,
            0,
            0x08,
        )
        .unwrap();
        PlayerBodyState::new(id, owner, 0, 1, SOLDIER, mover, None, 100, None)
    };

    let (id1, id2) = {
        let sim = state
            .rooms
            .get_mut(&room_id)
            .unwrap()
            .world
            .as_mut()
            .unwrap()
            .scene
            .simulation_mut();
        let gen1 = sim.next_player_generation(owner, 0).unwrap();
        let id1 = BodyId {
            entity: wire,
            generation: gen1,
        };
        sim.spawn_player_body(make_state(id1, [0.0; 3])).unwrap();

        let gen2 = sim.next_player_generation(owner, 0).unwrap();
        let id2 = BodyId {
            entity: wire,
            generation: gen2,
        };
        sim.spawn_player_body(make_state(id2, [5.0, 0.0, 0.0]))
            .unwrap();
        (id1, id2)
    };

    let report = state.reload_patrol(TARGET_SELECTOR_LUA).unwrap();
    assert_eq!(report.generation, 1);

    let sim = state
        .rooms
        .get_mut(&room_id)
        .unwrap()
        .world
        .as_mut()
        .unwrap()
        .scene
        .simulation_mut();
    assert!(!sim.player_body_generation_current(id1));
    assert!(sim.player_body(id1).is_none());
    assert!(sim.player_body_generation_current(id2));
    assert!(sim.player_body(id2).is_some());

    let script = AiScript::from_source("=target_selector.lua", TARGET_SELECTOR_LUA).unwrap();
    let run_select = |body: BodyId| {
        let snapshot = AiSnapshot {
            tick: FIRST_TICK + 10,
            dt_secs: crate::game_clock::TICK_PERIOD.as_secs_f64(),
            actor: AiActorSnapshot::patrol_fixture(0, 4),
            targets: vec![AiTargetSnapshot {
                body: Some(body),
                entity: body.entity,
                team: 1,
                alive: true,
                health: 200.0,
                max_health: 200.0,
                distance: 5.0,
                favored: false,
                visible: true,
            }],
        };
        let commands = script
            .think(
                &snapshot,
                &[AiEvent::Think {
                    tick: FIRST_TICK + 10,
                }],
            )
            .unwrap();
        validate_commands_against_simulation(&commands, sim)
    };

    assert_eq!(
        run_select(id1).unwrap_err(),
        AiScriptError::StaleBodyHandle(id1)
    );
    let zero_gen = BodyId {
        entity: wire,
        generation: 0,
    };
    assert_eq!(
        run_select(zero_gen).unwrap_err(),
        AiScriptError::StaleBodyHandle(zero_gen)
    );
    let unknown = BodyId {
        entity: 0xDEAD_BEEF,
        generation: 1,
    };
    assert_eq!(
        run_select(unknown).unwrap_err(),
        AiScriptError::UnknownTargetHandle(unknown)
    );
    assert!(run_select(id2).is_ok());

    // Re-spawning with retired id1 must still be rejected after reload.
    assert!(sim.spawn_player_body(make_state(id1, [0.0; 3])).is_err());
}

#[test]
fn hot_reload_file_rejects_missing_or_invalid_file_and_server_config_defaults_opt_out() {
    use crate::{ai_script::AI_MAX_SOURCE_BYTES, server::ServerConfig};
    use sha2::{Digest, Sha256};

    let config = ServerConfig::new(std::path::PathBuf::new(), std::path::PathBuf::new());
    assert!(config.ai_script.is_none());

    let dir = tempfile::tempdir().unwrap();
    let mut service = GameService::bind(local(), Arc::new(fixture())).unwrap();
    service
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap();

    let missing = dir.path().join("missing.lua");
    let err = service.reload_ai_script_file(&missing).unwrap_err();
    assert_eq!(err.kind(), io::ErrorKind::NotFound);
    assert_eq!(service.ai_policy_status().unwrap().generation, 0);

    let script_path = dir.path().join("patrol.lua");
    std::fs::write(&script_path, POLICY_MOVE).unwrap();
    let report = service.reload_ai_script_file(&script_path).unwrap();
    assert_eq!(report.generation, 1);
    assert_eq!(
        report.digest,
        format!("{:x}", Sha256::digest(POLICY_MOVE.as_bytes()))
    );

    // Invalid file content (ABI mismatch) and oversized file (> 64 KiB) roll back cleanly.
    std::fs::write(
        &script_path,
        "return { abi = 1, think = function() return {} end }",
    )
    .unwrap();
    assert!(service.reload_ai_script_file(&script_path).is_err());
    assert_eq!(service.ai_policy_status().unwrap().generation, 1);

    let oversized_path = dir.path().join("oversized.lua");
    std::fs::write(&oversized_path, "a".repeat(AI_MAX_SOURCE_BYTES + 1)).unwrap();
    assert!(service.reload_ai_script_file(&oversized_path).is_err());
    assert_eq!(service.ai_policy_status().unwrap().generation, 1);

    let reset = service
        .reload_ai_script(crate::patrol_script::DEFAULT_SOURCE)
        .unwrap();
    assert_eq!(reset.generation, 2);
    assert!(reset.changed);
    service.stop().unwrap();
}

#[test]
fn debug_room_control_is_disabled_by_default() {
    let mut service = GameService::bind_with_timing(local(), Arc::new(fixture()), true).unwrap();
    let snapshot = service.debug_snapshot().unwrap();
    assert!(!snapshot.control_enabled);
    let err = service
        .debug_room_control(MATCH, game_debug::RoomControlCommand::Pause)
        .unwrap_err();
    assert_eq!(err.kind(), io::ErrorKind::PermissionDenied);
    service.stop().unwrap();

    let mut enabled =
        GameService::bind_with_options(local(), Arc::new(fixture()), true, true).unwrap();
    let enabled_snapshot = enabled.debug_snapshot().unwrap();
    assert!(enabled_snapshot.control_enabled);
    let missing_err = enabled
        .debug_room_control(MATCH, game_debug::RoomControlCommand::Pause)
        .unwrap_err();
    assert_eq!(missing_err.kind(), io::ErrorKind::NotFound);
    enabled.stop().unwrap();
}

#[test]
fn pausing_one_room_freezes_its_bots_and_timers_while_sibling_room_runs_unimpeded() {
    use crate::{
        game_bots::{BOT_ENTITY, BOT_RESPAWN_TIME, Bot},
        game_clock::TICK_PERIOD,
        game_debug::{MAX_DEBUG_STEP_BUDGET, RoomControlCommand},
        scene_assets::PreparedScene,
    };

    let schemas = Arc::new(fixture());
    let mut state = State::new(schemas, PatrolScript::load().unwrap());
    state.debug_control_enabled = true;
    let socket = UdpSocket::bind(local()).unwrap();
    let paused_id = MATCH;
    let sibling_id = [5, 9];
    let mut now = Duration::ZERO;

    for id in [paused_id, sibling_id] {
        state
            .open_match(
                id,
                GameMap::practice_range(),
                player(),
                Some(WorldStart {
                    scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                        [0.0, 0.0, 10.0],
                    )])),
                    card: PlayerCard::default(),
                }),
                now,
            )
            .unwrap();
    }

    // Advance both rooms for 20 ticks so both bots are walking and active.
    for _ in 0..20 {
        now += TICK_PERIOD;
        state.tick(&socket, now, 0).unwrap();
    }

    // Stepping an unpaused room must be rejected with InvalidInput.
    assert_eq!(
        state
            .handle_debug_control(paused_id, RoomControlCommand::Step(1))
            .unwrap_err()
            .kind(),
        io::ErrorKind::InvalidInput
    );

    // Also schedule a bot respawn in the paused room right before pausing to prove
    // respawn deadlines do not expire prematurely while paused.
    let paused_sim_tick_before = state.rooms[&paused_id].debug_control.sim_tick();
    let paused_sim_now_before = state.rooms[&paused_id].debug_control.sim_now();
    {
        let world = state
            .rooms
            .get_mut(&paused_id)
            .unwrap()
            .world
            .as_mut()
            .unwrap();
        let (bots, _) = world.scene.bot_simulation();
        bots.kill_bot_for_test(BOT_ENTITY, paused_sim_tick_before, paused_sim_now_before);
    }

    let paused_status = state
        .handle_debug_control(paused_id, RoomControlCommand::Pause)
        .unwrap();
    assert!(paused_status.paused);
    assert_eq!(paused_status.pending_steps, 0);
    assert_eq!(paused_status.sim_tick, paused_sim_tick_before);

    let sibling_pos_at_pause = state.rooms[&sibling_id]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap()
        .position;

    // Advance global worker clock by 500 ticks (8 seconds > 5s BOT_RESPAWN_TIME).
    for _ in 0..500 {
        now += TICK_PERIOD;
        state.tick(&socket, now, 0).unwrap();
    }

    // Paused room must remain completely frozen (same sim_tick, same sim_now, dead bot not respawned).
    assert_eq!(
        state.rooms[&paused_id].debug_control.sim_tick(),
        paused_sim_tick_before
    );
    assert_eq!(
        state.rooms[&paused_id].debug_control.sim_now(),
        paused_sim_now_before
    );
    let paused_bot = state.rooms[&paused_id]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap();
    assert!(
        paused_bot.dead,
        "paused bot respawn timer must not expire during pause"
    );

    // Sibling room must have advanced 500 ticks unimpeded.
    assert_eq!(
        state.rooms[&sibling_id].debug_control.sim_tick(),
        paused_sim_tick_before + 500
    );
    let sibling_pos_after = state.rooms[&sibling_id]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap()
        .position;
    assert_ne!(sibling_pos_after, sibling_pos_at_pause);

    // Bounded step admission: 0 steps or > MAX_DEBUG_STEP_BUDGET must fail without mutating pending_steps.
    for invalid_steps in [0, MAX_DEBUG_STEP_BUDGET + 1] {
        assert_eq!(
            state
                .handle_debug_control(paused_id, RoomControlCommand::Step(invalid_steps))
                .unwrap_err()
                .kind(),
            io::ErrorKind::InvalidInput
        );
    }

    // Single-step by 1 tick: advances exactly 1 tick and 1 TICK_PERIOD, then freezes again.
    let step1 = state
        .handle_debug_control(paused_id, RoomControlCommand::Step(1))
        .unwrap();
    assert_eq!(step1.pending_steps, 1);
    now += TICK_PERIOD;
    state.tick(&socket, now, 0).unwrap();
    assert_eq!(
        state.rooms[&paused_id].debug_control.sim_tick(),
        paused_sim_tick_before + 1
    );
    assert_eq!(
        state.rooms[&paused_id].debug_control.sim_now(),
        paused_sim_now_before + TICK_PERIOD
    );
    assert_eq!(
        state.rooms[&paused_id].debug_control.status().pending_steps,
        0
    );
    assert_eq!(
        state.rooms[&paused_id].debug_control.status().stepped_ticks,
        1
    );

    // Next tick without pending steps keeps the paused room frozen.
    now += TICK_PERIOD;
    state.tick(&socket, now, 0).unwrap();
    assert_eq!(
        state.rooms[&paused_id].debug_control.sim_tick(),
        paused_sim_tick_before + 1
    );

    // Multi-step by 3 ticks: advances 1 tick per worker tick for 3 ticks, then freezes on tick 4.
    let step3 = state
        .handle_debug_control(paused_id, RoomControlCommand::Step(3))
        .unwrap();
    assert_eq!(step3.pending_steps, 3);
    // Overflowing cumulative budget while 3 steps are pending must be refused cleanly.
    assert_eq!(
        state
            .handle_debug_control(
                paused_id,
                RoomControlCommand::Step(MAX_DEBUG_STEP_BUDGET - 2)
            )
            .unwrap_err()
            .kind(),
        io::ErrorKind::InvalidInput
    );
    for expected_offset in 2..=4 {
        now += TICK_PERIOD;
        state.tick(&socket, now, 0).unwrap();
        assert_eq!(
            state.rooms[&paused_id].debug_control.sim_tick(),
            paused_sim_tick_before + expected_offset
        );
    }
    now += TICK_PERIOD;
    state.tick(&socket, now, 0).unwrap();
    assert_eq!(
        state.rooms[&paused_id].debug_control.sim_tick(),
        paused_sim_tick_before + 4
    );
    assert_eq!(
        state.rooms[&paused_id].debug_control.status().stepped_ticks,
        4
    );

    // Resume: must continue smoothly from paused_sim_tick_before + 4 without a catch-up jump,
    // and the dead bot must still wait its remaining respawn duration before respawning.
    let resumed = state
        .handle_debug_control(paused_id, RoomControlCommand::Resume)
        .unwrap();
    assert!(!resumed.paused);
    assert_eq!(resumed.pending_steps, 0);
    now += TICK_PERIOD;
    state.tick(&socket, now, 0).unwrap();
    assert_eq!(
        state.rooms[&paused_id].debug_control.sim_tick(),
        paused_sim_tick_before + 5
    );
    let still_dead = state.rooms[&paused_id]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY)
        .unwrap()
        .dead;
    assert!(still_dead, "resuming must not cause a timer catch-up spike");

    let respawn_ticks = (BOT_RESPAWN_TIME.as_millis() / TICK_PERIOD.as_millis()) as usize + 2;
    for _ in 0..respawn_ticks {
        now += TICK_PERIOD;
        state.tick(&socket, now, 0).unwrap();
    }
    let respawned_live = state.rooms[&paused_id]
        .world
        .as_ref()
        .unwrap()
        .scene
        .bots()
        .unwrap()
        .any(|bot| !bot.dead);
    assert!(
        respawned_live,
        "bot must respawn once the full simulation respawn duration elapses after resume"
    );
}

#[test]
fn paused_room_keeps_keepalive_disconnect_and_packet_budget_sane_without_busy_spin() {
    use crate::{
        game_bots::Bot,
        game_clock::{FIRST_TICK, SEND_EVERY, TICK_PERIOD},
        game_debug::RoomControlCommand,
        scene_assets::PreparedScene,
    };

    let schemas = Arc::new(fixture());
    let mut state = State::new(Arc::clone(&schemas), PatrolScript::load().unwrap());
    state.debug_control_enabled = true;
    let socket = UdpSocket::bind(local()).unwrap();
    let addr = socket.local_addr().unwrap();
    let mut now = Duration::ZERO;

    let mut tickets = state
        .open_match(
            MATCH,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                    [0.0, 0.0, 10.0],
                )])),
                card: PlayerCard::default(),
            }),
            now,
        )
        .unwrap();
    let ticket = tickets.pop().unwrap();
    let mut client = Client::new(addr, &ticket);
    client
        .socket
        .set_read_timeout(Some(Duration::from_millis(50)))
        .unwrap();

    // Handshake with State directly.
    let pump_inbound = |state: &mut State, socket: &UdpSocket, now: Duration| {
        let mut buf = [0_u8; MAX_DATAGRAM];
        while let Ok((count, peer_addr)) = socket.recv_from(&mut buf) {
            state
                .receive(socket, &buf[..count], peer_addr, now)
                .unwrap();
        }
    };
    client.send(SYN, Vec::new());
    pump_inbound(&mut state, &socket, now);
    let syn_ack = client.receive();
    assert_eq!((syn_ack.kind, syn_ack.flags), (KIND_CONTROL, SYN | ACK));
    assert!(client.window.accept(syn_ack.seq));
    client.send(ACK, Vec::new());
    pump_inbound(&mut state, &socket, now);

    // Tick once to reach a SEND_EVERY boundary and drain the initial reliable frame.
    while state.clock.frame() % SEND_EVERY != 0 {
        now += TICK_PERIOD;
        state.tick(&socket, now, 0).unwrap();
    }
    let first_frame = client.receive();
    assert!(client.window.accept(first_frame.seq));
    client.send(ACK, Vec::new());
    pump_inbound(&mut state, &socket, now);

    // Pause the room and verify advance_input reports backlog == false even when input is queued.
    let paused_status = state
        .handle_debug_control(MATCH, RoomControlCommand::Pause)
        .unwrap();
    assert!(paused_status.paused);
    let frozen_tick = paused_status.sim_tick;
    assert!(frozen_tick > FIRST_TICK);

    // Send a Ping while the room is paused.
    let ping = encode(
        &schemas,
        21610,
        &Fields::from([
            (0x78, Value::Unsigned(99)),
            (0x80, Value::Array(vec![Value::Unsigned(123)])),
        ]),
    )
    .unwrap();
    let mut payload = BitWriter::new();
    write_channel(&mut payload, &[], &[ping]).unwrap();
    client.send(ACK, payload.into_bytes());
    pump_inbound(&mut state, &socket, now);

    // advance_input must not report a backlog for a paused room.
    assert!(!state.advance_input(now).unwrap());

    // Advance 6 global ticks (two SEND_EVERY intervals): exactly 2 frames should be sent,
    // carrying the Pong reliable reply and preserving the frozen sim_tick on the wire.
    let mut frames_received = 0;
    let mut pong_seen = false;
    for _ in 0..6 {
        now += TICK_PERIOD;
        state.tick(&socket, now, 0).unwrap();
        while let Ok(packet) = {
            let mut bytes = [0; MAX_DATAGRAM];
            client
                .socket
                .recv(&mut bytes)
                .map(|count| client.cipher.open(&bytes[..count]).unwrap())
        } {
            frames_received += 1;
            assert!(client.window.accept(packet.seq));
            let mut reader = BitReader::new(&packet.payload);
            let wire_tick = reader.var_a().unwrap();
            assert_eq!(
                wire_tick, frozen_tick,
                "paused frame must report frozen sim_tick"
            );
            for msg in messages(&schemas, &packet) {
                if msg.message.id == 20306 {
                    assert_eq!(msg.message.fields[&0x78], Value::Unsigned(99));
                    pong_seen = true;
                }
            }
        }
    }
    assert_eq!(
        frames_received, 2,
        "paused room must honor SEND_EVERY packet budget (2 frames across 6 ticks)"
    );
    assert!(pong_seen, "Ping must receive Pong while room is paused");

    // Step(1) on a non-SEND_EVERY tick must flush the stepped tick on the next SEND_EVERY tick.
    state
        .handle_debug_control(MATCH, RoomControlCommand::Step(1))
        .unwrap();
    let mut stepped_wire_tick = None;
    for _ in 0..SEND_EVERY {
        now += TICK_PERIOD;
        state.tick(&socket, now, 0).unwrap();
        let mut bytes = [0; MAX_DATAGRAM];
        if let Ok(count) = client.socket.recv(&mut bytes) {
            let packet = client.cipher.open(&bytes[..count]).unwrap();
            let mut reader = BitReader::new(&packet.payload);
            stepped_wire_tick = Some(reader.var_a().unwrap());
        }
    }
    assert_eq!(stepped_wire_tick, Some(frozen_tick + 1));

    // Client FIN disconnect while paused must retire the member and room cleanly,
    // allowing a fresh seat to be opened and connected afterward.
    client.send(FIN | ACK, Vec::new());
    pump_inbound(&mut state, &socket, now);
    assert!(!state.rooms.contains_key(&MATCH));

    let mut reconnect_tickets = state
        .open_match(
            MATCH,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(PreparedScene::fixture_for_bots(vec![Bot::fixture_walker(
                    [0.0, 0.0, 10.0],
                )])),
                card: PlayerCard::default(),
            }),
            now,
        )
        .unwrap();
    let reconnect_ticket = reconnect_tickets.pop().unwrap();
    let mut reconnect_client = Client::new(addr, &reconnect_ticket);
    reconnect_client.send(SYN, Vec::new());
    pump_inbound(&mut state, &socket, now);
    let reconnect_syn_ack = reconnect_client.receive();
    assert_eq!(
        (reconnect_syn_ack.kind, reconnect_syn_ack.flags),
        (KIND_CONTROL, SYN | ACK)
    );
}

#[test]
#[ignore = "requires an authorized base 104319 Practice Range runtime"]
fn prepared_practice_rooms_hot_reload_and_roll_back_without_interrupting_patrol() {
    use crate::{
        game_bots::BOT_ENTITY, game_heroes::SOLDIER, scene_assets::SceneAssets,
        script_player::fixture_hitscan_factory,
    };
    use benchmark::{Driver, PeerCounts};

    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let schemas = Arc::new(Schemas::load(runtime.join("data/game_schemas_174.json")).unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let factory = Arc::new(fixture_hitscan_factory(0.125));
    let prepare = || {
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
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

    let mut service = GameService::bind(local(), Arc::clone(&schemas)).unwrap();
    let first_id = [5, 8];
    let first_ticket = service
        .open_prepared_practice(
            first_id,
            prepare(),
            player().pop().unwrap(),
            PlayerCard::default(),
        )
        .unwrap();
    let mut first_driver = Driver::join(Client::new(service.local_addr(), &first_ticket), &schemas);
    for _ in 0..4 {
        first_driver.step(&schemas, None);
    }

    let fast_patrol = r#"
    return {
        abi = 2,
        think = function(snapshot, events)
            local patrol = snapshot.actor.patrol
            local event = events[1]
            if event.kind == "spawn" or event.kind == "release" then
                return { { kind = "wait", seconds = 0.25 } }
            elseif event.kind == "think" then
                return { { kind = "patrol_slot", slot = (patrol.route_index + 1) % patrol.route_count } }
            end
            return {}
        end,
    }
    "#;
    let reloaded = service.reload_ai_script(fast_patrol).unwrap();
    assert_eq!(reloaded.generation, 1);
    assert_eq!(reloaded.active_rooms, 1);
    assert!(reloaded.changed);

    // Invalid reload (think loop) must roll back without touching Room 1.
    assert!(
        service
            .reload_ai_script("return { abi = 2, think = function() while true do end end }")
            .is_err()
    );
    assert_eq!(service.ai_policy_status().unwrap().generation, 1);

    let second_id = [5, 9];
    let second_ticket = service
        .open_prepared_practice(
            second_id,
            prepare(),
            player().pop().unwrap(),
            PlayerCard::default(),
        )
        .unwrap();
    let mut second_driver =
        Driver::join(Client::new(service.local_addr(), &second_ticket), &schemas);

    let mut first_counts = PeerCounts::default();
    let mut second_counts = PeerCounts::default();
    for _ in 0..20 {
        first_driver.step(&schemas, Some(&mut first_counts));
        second_driver.step(&schemas, Some(&mut second_counts));
    }
    assert!(first_counts.report()["world_packets"].as_u64().unwrap() > 0);
    assert!(second_counts.report()["world_packets"].as_u64().unwrap() > 0);

    let snap = service.debug_snapshot().unwrap();
    assert_eq!(snap.rooms.len(), 2);
    assert_eq!(snap.ai_policy.as_ref().unwrap().generation, 1);
    assert!(snap.rooms.iter().all(|room| room.ai_generation == Some(1)));
    service.stop().unwrap();
}
