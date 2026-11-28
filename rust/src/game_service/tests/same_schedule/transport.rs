//! Observe actual owner-script datagrams and authenticated delivery receipts.
use super::*;
use crate::game_replication::tests::owner_effects::body_script_receipts;

fn check_receipts(
    reports: &[(u32, bool, u32)],
    lost: (u32, u32),
    received: (u32, u32),
) -> Result<(), &'static str> {
    if !reports.contains(&(lost.0, false, lost.1)) {
        return Err("missing lost body-script receipt");
    }
    if !reports.contains(&(received.0, true, received.1)) {
        return Err("missing arrived body-script receipt");
    }
    Ok(())
}

fn body_chunk(schemas: &Schemas, packet: &Packet, body: u32) -> Option<ReceivedChunk> {
    let mut chunks = world_records(schemas, packet)
        .into_iter()
        .filter(|record| record.entity == body)
        .filter_map(|record| record.script);
    let chunk = chunks.next();
    assert!(chunks.next().is_none());
    if let Some(chunk) = &chunk {
        let mut bits = BitReader::with_range(&chunk.wire, 0, chunk.wire_width).unwrap();
        assert_eq!(bits.w_u32().unwrap(), chunk.first);
        assert_eq!(bits.w_var().unwrap(), chunk.span);
        assert!(!bits.bit().unwrap());
        assert_eq!(bits.w_var().unwrap() as usize, chunk.width);
        assert_eq!(bits.left(), chunk.width);
        assert!(
            bits.raw_bits(chunk.width).unwrap() == chunk.data,
            "chunk payload mismatch: first={}, span={}, width={}, wire_width={}",
            chunk.first,
            chunk.span,
            chunk.width,
            chunk.wire_width
        );
    }
    chunk
}

#[test]
#[ignore = "requires a target-tagged 104319 Practice Range pack without bot VM graphs"]
fn prepared_owner_post_startup_loss_receipts() {
    let probe = std::env::var_os("ORPHEUS_RECEIVER_PROBE");
    let mut rows = Vec::new();
    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("prepared runtime"));
    let assets = SceneAssets::load(&runtime).unwrap();
    assert!(
        assets.graphs().entity_body(0x0400_0000_0000_143e).is_none(),
        "bot VM graphs are unsupported"
    );
    let hero = assets.heroes().get(crate::game_heroes::SOLDIER).unwrap();
    assert!(
        assets.graphs().bodies()[&hero.body].target_tags.is_some(),
        "target-tagged Soldier pack required"
    );
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    assert!(scene.movement_ready());
    let bot = scene.simulation().bodies().unwrap().next().unwrap().entity;
    let schemas = Arc::new(Schemas::load(runtime.join("data/game_schemas_174.json")).unwrap());
    let mut state = State::new(Arc::clone(&schemas), PatrolScript::load().unwrap());
    let ticket = state
        .open_match(
            MATCH,
            GameMap::practice_range(),
            player(),
            Some(WorldStart {
                scene: Box::new(scene),
                card: PlayerCard::default(),
            }),
            Duration::ZERO,
        )
        .unwrap()
        .remove(0);
    let server = UdpSocket::bind(local()).unwrap();
    let mut client = Client::new(server.local_addr().unwrap(), &ticket);
    let mut captured = Vec::new();
    let mut baseline_acked = false;
    let setup = startup::join_observed(
        &mut state,
        &server,
        &mut client,
        &schemas,
        (bot, crate::game_heroes::SOLDIER),
        &mut Default::default(),
        |packet, body, receipt| {
            if let Some(chunk) = body_chunk(&schemas, packet, body.entity) {
                if probe.is_some() {
                    rows.push(serde_json::json!({"seq": packet.seq, "elapsed": 0, "kind": "accepted", "bits": chunk.wire_width, "data": chunk.wire}));
                }
                if chunk.first == 0 && chunk.span == 1 {
                    baseline_acked =
                        body_script_receipts(receipt, body).contains(&(packet.seq, true, 1));
                }
                captured.push((packet.seq, chunk));
            }
        },
    );
    let body = state.rooms[&MATCH].members[&ticket.conn]
        .peer
        .as_ref()
        .unwrap()
        .world
        .active_body_id()
        .unwrap();
    let mut lost = None;
    let mut post_startup_acked = None;
    let mut no_loss_window = None;
    let mut finished = false;
    let mut last_send = None;
    for elapsed in 1..=if probe.is_some() { 180 } else { 120 } {
        let now = setup.now + Duration::from_micros(u64::from(elapsed) * u64::from(RETAIL_QUANTA));
        let frame = setup.tick + elapsed;
        let command = Command {
            frame,
            forward: if if probe.is_some() {
                (76..110).contains(&elapsed)
            } else {
                elapsed >= 64
            } {
                127
            } else {
                0
            },
            buttons: if elapsed == if probe.is_some() { 76 } else { 64 } {
                8
            } else {
                0
            },
            ..Command::default()
        };
        let peer = state.rooms.get_mut(&MATCH).unwrap().members[&ticket.conn]
            .peer
            .as_mut()
            .unwrap();
        peer.world.queue_commands(state.clock.frame(), &[command]);
        while state.advance_input(now).unwrap() {}
        assert_eq!(
            processed(
                &state.rooms[&MATCH].members[&ticket.conn]
                    .peer
                    .as_ref()
                    .unwrap()
                    .world
            ),
            Some(frame)
        );
        let previous = state.clock.frame();
        state.tick(&server, now, 0).unwrap();
        if state.clock.frame() / SEND_EVERY == previous / SEND_EVERY {
            continue;
        }
        let packet = client.receive();
        let mut chunk = body_chunk(&schemas, &packet, body.entity);
        if probe.is_some() && chunk.is_some() {
            last_send = Some((elapsed, packet.seq));
        }
        if elapsed >= if probe.is_some() { 76 } else { 75 } && lost.is_none() {
            if let Some((prior_sequence, prior_end)) = post_startup_acked {
                if let Some(missing) = chunk.take() {
                    if probe.is_some() {
                        rows.push(serde_json::json!({"seq": packet.seq, "elapsed": elapsed, "kind": "lost", "bits": missing.wire_width, "data": missing.wire}));
                    }
                    assert!(prior_sequence < packet.seq);
                    assert!(
                        prior_end < missing.first,
                        "lost chunk must follow an ACKed post-startup delta"
                    );
                    let mut control = client.window;
                    assert!(control.accept(packet.seq));
                    no_loss_window = Some(control);
                    lost = Some((packet.seq, missing));
                    continue;
                }
            }
        }
        assert!(client.window.accept(packet.seq));
        let receipt = startup::acknowledge(&mut state, &server, &mut client, now);
        if probe.is_some() {
            if let Some(chunk) = &chunk {
                rows.push(serde_json::json!({"seq": packet.seq, "elapsed": elapsed, "kind": "accepted", "bits": chunk.wire_width, "data": chunk.wire}));
            }
        }
        if let Some((sequence, missing)) = lost.as_ref().filter(|_| !finished) {
            let sequence = *sequence;
            let mut control = no_loss_window.unwrap();
            assert!(control.accept(packet.seq));
            assert_eq!(
                crate::game_link::delivery(sequence, client.window.expected, client.window.bits),
                Some(false)
            );
            assert_eq!(
                crate::game_link::delivery(sequence, control.expected, control.bits),
                Some(true)
            );
            let received = chunk.expect("the following packet must carry an owner script");
            let reports = body_script_receipts(&receipt, body);
            let lost_record = (sequence, missing.first + missing.span);
            let received_record = (packet.seq, received.first + received.span);
            assert_eq!(
                check_receipts(&reports, lost_record, received_record),
                Ok(())
            );
            let mut control = reports.clone();
            control.retain(|entry| *entry != (sequence, false, lost_record.1));
            assert_eq!(
                check_receipts(&control, lost_record, received_record),
                Err("missing lost body-script receipt")
            );
            let mut control = reports.clone();
            control.retain(|entry| *entry != (packet.seq, true, received_record.1));
            assert_eq!(
                check_receipts(&control, lost_record, received_record),
                Err("missing arrived body-script receipt")
            );
            assert_eq!(packet.seq, sequence + 1);
            assert!(
                received.first <= missing.first
                    && received.first + received.span > missing.first + missing.span,
                "following packet does not cover lost chunk"
            );
            assert!(!reports.contains(&(sequence, true, missing.first + missing.span)));
            captured.push((packet.seq, received));
            finished = true;
            if probe.is_none() {
                break;
            }
            continue;
        }
        if let Some(chunk) = chunk {
            if chunk.first == 0 && chunk.span == 1 {
                baseline_acked =
                    body_script_receipts(&receipt, body).contains(&(packet.seq, true, 1));
            }
            if chunk.first > 1 {
                let last = chunk.first + chunk.span;
                if body_script_receipts(&receipt, body).contains(&(packet.seq, true, last)) {
                    post_startup_acked = Some((packet.seq, last));
                }
            }
            captured.push((packet.seq, chunk));
        }
    }
    assert!(
        captured
            .iter()
            .any(|(_, chunk)| chunk.first == 0 && chunk.span == 1),
        "owner H0 was not captured"
    );
    assert!(baseline_acked, "owner H0 was not acknowledged");
    assert!(
        post_startup_acked.is_some(),
        "no post-startup owner delta was acknowledged"
    );
    assert!(
        finished,
        "post-startup loss and ACK scenario was not completed"
    );
    if let Some(path) = probe {
        let (last_elapsed, last_seq) = last_send.expect("probe sent no packets");
        assert!(last_elapsed > 110);
        let peer = state.rooms[&MATCH].members[&ticket.conn]
            .peer
            .as_ref()
            .unwrap();
        let processed_frame = processed(&peer.world).expect("probe processed no commands");
        assert_eq!(processed_frame, setup.tick + 180);
        let capture = serde_json::json!({"rows": rows, "end": {"elapsed": last_elapsed, "seq": last_seq, "processed_frame": processed_frame, "processed_elapsed": 180, "release_elapsed": 110}});
        std::fs::write(path, serde_json::to_vec(&capture).unwrap()).unwrap();
    }
    println!(
        "completed: acknowledged H0 and post-startup delta, post-startup loss, cumulative following packet and authenticated receipts"
    );
}
