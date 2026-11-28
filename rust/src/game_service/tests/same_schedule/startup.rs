//! Drive the normal room ingress and fixed-step scheduler on an in-process test link.
use super::*;
use crate::game_egress::DeliveryReport;
use crate::game_replication::tests::practice_observation::{
    body_script_frame, controller_acked, possession_acked,
};
use std::collections::BTreeMap;

pub(super) struct Startup {
    pub tick: u32,
    pub now: Duration,
    pub component_frame: u32,
    pub acks: BTreeMap<&'static str, u32>,
    pub effects: Vec<Vec<Json>>,
    pub scripts: [u32; 2],
}

pub(super) fn receive_request(state: &mut State, server: &UdpSocket, now: Duration) {
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut buffer = [0; MAX_DATAGRAM];
    loop {
        match server.recv_from(&mut buffer) {
            Ok((len, source)) => {
                state.receive(server, &buffer[..len], source, now).unwrap();
                return;
            }
            Err(err) if err.kind() == io::ErrorKind::WouldBlock && Instant::now() < deadline => {
                thread::yield_now();
            }
            Err(err) => panic!("loopback request was not received: {err}"),
        }
    }
}

pub(super) fn acknowledge(
    state: &mut State,
    server: &UdpSocket,
    client: &mut Client,
    now: Duration,
) -> DeliveryReport<Arc<Record>> {
    client.send(ACK, Vec::new());
    receive_request(state, server, now);
    let peer = state.rooms.get_mut(&MATCH).unwrap().members[&client.connection]
        .peer
        .as_mut()
        .unwrap();
    let reports = peer.observed_receipts.as_mut().unwrap();
    assert_eq!(reports.len(), 1);
    reports.pop().unwrap()
}

pub(super) fn join(
    state: &mut State,
    server: &UdpSocket,
    client: &mut Client,
    schemas: &Schemas,
    bot: u32,
    names: &mut BTreeMap<u32, String>,
) -> Startup {
    join_observed(
        state,
        server,
        client,
        schemas,
        (bot, crate::game_heroes::SOLDIER),
        names,
        |_, _, _| {},
    )
}

pub(super) fn join_observed(
    state: &mut State,
    server: &UdpSocket,
    client: &mut Client,
    schemas: &Schemas,
    (bot, hero): (u32, u64),
    names: &mut BTreeMap<u32, String>,
    mut observe: impl FnMut(&Packet, crate::game_replication::BodyId, &DeliveryReport<Arc<Record>>),
) -> Startup {
    client.send(SYN, Vec::new());
    receive_request(state, server, Duration::ZERO);
    let hello = client.receive();
    assert_eq!((hello.kind, hello.flags), (KIND_CONTROL, SYN | ACK));
    assert!(client.window.accept(hello.seq));
    client.send(ACK, Vec::new());
    receive_request(state, server, Duration::ZERO);
    let conn = client.connection;
    state.rooms.get_mut(&MATCH).unwrap().members[&conn]
        .peer
        .as_mut()
        .unwrap()
        .observed_receipts = Some(Vec::new());
    let mut acks = BTreeMap::new();
    let mut effects = Vec::new();
    let mut scripts = [0; 2];
    let mut picked = false;
    let mut component_frame = None;
    for elapsed in 0..256 {
        let now = Duration::from_micros(elapsed * u64::from(RETAIL_QUANTA));
        let prev = state.clock.frame();
        state.tick(server, now, 0).unwrap();
        let tick = state.clock.frame();
        if tick / SEND_EVERY == prev / SEND_EVERY {
            continue;
        }
        let sent = client.receive();
        assert!(client.window.accept(sent.seq));
        let instance = messages(schemas, &sent)
            .iter()
            .any(|m| m.message.id == 20300);
        let receipt = acknowledge(state, server, client, now);
        let peer = state.rooms[&MATCH].members[&conn].peer.as_ref().unwrap();
        if peer.world.identity_ready(bot) {
            acks.entry("bot_create").or_insert(tick);
        }
        if let Some(id) = peer.world.spawned_body() {
            if let Some(body) = peer.world.active_body_id() {
                observe(&sent, body, &receipt);
            }
            names.insert(id, "player/0/body/1".to_string());
            if peer.world.identity_ready(id) {
                acks.entry("owner_create").or_insert(tick);
            }
            let applied = script_receipts(&receipt, id);
            for (i, name) in ["owner_full_tree", "owner_first_delta"]
                .into_iter()
                .enumerate()
            {
                scripts[i] += applied[i];
                if applied[i] != 0 {
                    acks.entry(name).or_insert(tick);
                }
            }
            if possession_acked(&peer.world) {
                acks.entry("owner_possession").or_insert(tick);
            }
        }
        effects.extend(selected(&receipt, names));
        if ["bot_create", "owner_create", "owner_possession"]
            .iter()
            .all(|key| acks.contains_key(key))
        {
            assert!(processed(&peer.world).is_none());
            return Startup {
                tick,
                now,
                component_frame: component_frame.unwrap(),
                acks,
                effects,
                scripts,
            };
        }
        if instance {
            let mut load = BitWriter::new();
            write_channel(
                &mut load,
                &[(0, encode(schemas, 21602, &Fields::new()).unwrap())],
                &[],
            )
            .unwrap();
            client.send(ACK, load.into_bytes());
        } else if !picked && controller_acked(&peer.world) {
            send_game_message(
                client,
                schemas,
                1,
                crate::game_parameters::PICK_HERO,
                hero.to_le_bytes().to_vec(),
            );
            picked = true;
        } else {
            continue;
        }
        receive_request(state, server, now);
        while state.advance_input(now).unwrap() {}
        let peer = state.rooms.get_mut(&MATCH).unwrap().members[&conn]
            .peer
            .as_mut()
            .unwrap();
        if picked {
            component_frame = body_script_frame(&peer.world);
        }
        // The request repeats the ACK we have already consumed; it cannot deliver records.
        for report in peer.observed_receipts.as_mut().unwrap().drain(..) {
            assert!(report.packets.is_empty());
        }
    }
    panic!("room startup timed out with observed receipts {acks:?}");
}
