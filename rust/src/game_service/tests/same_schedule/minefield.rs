//! Exercise the stock ability through the owner's connected Practice Range peer.
use super::*;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

fn late_viewer_receives_existing_mines(
    scene: &PreparedScene,
    schemas: &Schemas,
    mines: &BTreeSet<u32>,
    tick: u32,
) {
    use crate::game_replication::WorldDelivery;
    use crate::match_scene::PlayerAppearance;
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: 0xa000_0102,
        slot: 1,
        team: 1,
        card: &card,
    };
    let mut world = WorldDelivery::new(viewer.entity);
    world.start(scene, &viewer, tick).unwrap();
    let mut egress = GameEgress::default();
    let mut received_creates = BTreeSet::new();
    let mut received_scripts = BTreeSet::new();
    let mut dropped_create = false;
    for seq in 1..=100 {
        let frame = tick + seq * SEND_EVERY;
        world.present_scene_scripts(scene, frame).unwrap();
        let mut packet = Packet::default();
        world
            .send_frame(frame, Vec::new(), false, None, &mut egress, |payload| {
                packet.payload = payload;
                Ok::<_, ()>(seq)
            })
            .unwrap();
        let records = world_records(schemas, &packet);
        let drop = !dropped_create
            && records
                .iter()
                .any(|r| mines.contains(&r.entity) && r.is_create());
        dropped_create |= drop;
        if !drop {
            for record in records.into_iter().filter(|r| mines.contains(&r.entity)) {
                if record.script.is_some() {
                    assert!(received_creates.contains(&record.entity));
                    assert!(world.identity_ready(record.entity));
                    received_scripts.insert(record.entity);
                }
                if record.is_create() {
                    assert_eq!(record.definition, Some(0x2578));
                    assert!(received_creates.insert(record.entity));
                }
            }
        }
        world.acknowledge(
            egress.acknowledge(seq + 1, u64::from(!drop)),
            Duration::ZERO,
        );
        if received_scripts == *mines {
            break;
        }
    }
    assert!(dropped_create);
    assert_eq!(&received_creates, mines);
    assert_eq!(&received_scripts, mines);
}

#[test]
#[ignore = "requires the local 104319 Practice Range and the Wrecking Ball stock graphs"]
fn prepared_owner_q_delivers_armed_minefield_to_the_casting_peer() {
    let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&root).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let bot = scene.simulation().bodies().unwrap().next().unwrap().entity;
    let hero = scene
        .heroes()
        .get(crate::game_heroes::WRECKING_BALL)
        .unwrap()
        .guid;
    let schemas = Arc::new(Schemas::load(root.join("data/game_schemas_174.json")).unwrap());
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
    let setup = startup::join_observed(
        &mut state,
        &server,
        &mut client,
        &schemas,
        (bot, hero),
        &mut Default::default(),
        |_, _, _| {},
    );
    let owner = state.rooms[&MATCH].members[&ticket.conn]
        .peer
        .as_ref()
        .unwrap()
        .world
        .active_body_id()
        .unwrap();
    let mut created = BTreeSet::new();
    let mut armed = BTreeSet::new();
    let mut positions = BTreeSet::new();
    let mut airborne = BTreeSet::new();
    let mut launched = BTreeSet::new();
    let mut retired = BTreeSet::new();
    let mut simulated = 0;
    let mut charge_after = None;
    let mut first_all_armed = None;
    let mut first_landed = None;
    let mut first_armed = None;
    let mut survived_arming = false;
    for elapsed in 1..=520 {
        let now = setup.now + Duration::from_micros(u64::from(elapsed) * u64::from(RETAIL_QUANTA));
        let frame = setup.tick + elapsed;
        if elapsed == 95 {
            let room = state
                .rooms
                .get_mut(&MATCH)
                .unwrap()
                .world
                .as_mut()
                .unwrap()
                .scene
                .simulation_mut();
            let rt = room
                .player_body_mut(owner)
                .unwrap()
                .runtime
                .as_mut()
                .unwrap();
            rt.set_ult_charge_percent(100.0).unwrap();
            assert_eq!(rt.ult_charge_percent(), 100.0);
        }
        let q = if (100..104).contains(&elapsed) {
            0x20
        } else {
            0
        };
        client.send(ACK, command_batch(frame, 1, 0, q, false));
        startup::receive_request(&mut state, &server, now);
        while state.advance_input(now).unwrap() {}
        let peer = state.rooms.get_mut(&MATCH).unwrap().members[&ticket.conn]
            .peer
            .as_mut()
            .unwrap();
        assert!(
            peer.observed_receipts
                .as_mut()
                .unwrap()
                .drain(..)
                .all(|r| r.packets.is_empty())
        );
        let previous = state.clock.frame();
        state.tick(&server, now, 0).unwrap();
        let sim = state.rooms[&MATCH]
            .world
            .as_ref()
            .unwrap()
            .scene
            .simulation();
        let live = sim.active_room_deployables();
        if live > 0 {
            first_landed.get_or_insert(elapsed);
        }
        simulated = simulated.max(live);
        if elapsed == 170 {
            charge_after = sim
                .player_body(owner)
                .unwrap()
                .runtime
                .as_ref()
                .map(|rt| rt.ult_charge_percent());
        }
        if state.clock.frame() / SEND_EVERY == previous / SEND_EVERY {
            continue;
        }
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        for record in world_records(&schemas, &packet) {
            if record.entity == owner.entity {
                launched.extend(record.launched_projectiles);
            }
            if record.operation == Some(3) && airborne.contains(&record.entity) {
                retired.insert(record.entity);
            }
            if created.contains(&record.entity) {
                assert_ne!(
                    record.operation,
                    Some(3),
                    "mine retired at the caster's spawn"
                );
            }
            if record.definition == Some(0x2578) {
                let position = record
                    .create_position
                    .expect("stock mine creation has a transform");
                assert!(position.into_iter().all(f32::is_finite));
                if position == [0.0, -1000.0, 0.0] {
                    airborne.insert(record.entity);
                } else {
                    assert_eq!(
                        record.create_filter,
                        Some(crate::match_scene::team_bit(0).unwrap()),
                        "the deployed mine must carry its owner's team mask"
                    );
                    created.insert(record.entity);
                    positions.insert(position.map(f32::to_bits));
                }
            }
            if let Some(chunk) = record.script.filter(|_| created.contains(&record.entity)) {
                first_armed.get_or_insert(elapsed);
                // Pinned Python cedec602 mine_armed_frame() on the same 104319 graphs.
                assert_eq!((chunk.first, chunk.span, chunk.width), (0, 1, 89));
                assert_eq!(
                    format!("{:x}", Sha256::digest(&chunk.data)),
                    "c63d067dee386c756a985a87cde8fd829426a495bb3be9a06deb2257b58d13ce"
                );
                armed.insert(record.entity);
            }
        }
        startup::acknowledge(&mut state, &server, &mut client, now);
        if armed.len() == 15 {
            assert_eq!(live, 15, "the room must retain every armed mine");
            if elapsed - *first_all_armed.get_or_insert(elapsed) >= 16 {
                survived_arming = true;
                break;
            }
        }
    }
    assert_eq!(
        created.len(),
        15,
        "simulated={simulated}, visible armed={}, post-Q charge={charge_after:?}",
        armed.len()
    );
    assert_eq!(created, armed);
    assert!(survived_arming);
    let observed_arm_delay = first_armed.unwrap() - first_landed.unwrap();
    assert!(
        observed_arm_delay >= 94,
        "effects preceded the authored 1.5 s wait"
    );
    assert_eq!(airborne.len(), 15);
    eprintln!(
        "airborne: {} created, {} referenced by the caster's volley, {} retired",
        airborne.len(),
        airborne.intersection(&launched).count(),
        retired.len()
    );
    assert_eq!(
        airborne, launched,
        "an airborne placeholder never appeared in a launch on the caster's peer"
    );
    assert_eq!(
        airborne, retired,
        "an airborne placeholder survived its flight"
    );
    assert_eq!(
        positions.len(),
        15,
        "mine launches converged on the same position"
    );
    assert_eq!(charge_after, Some(0.0));
    let profiles = state.rooms[&MATCH]
        .world
        .as_ref()
        .unwrap()
        .scene
        .simulation()
        .proximity_readout();
    assert_eq!(
        profiles.len(),
        15,
        "landed creates must also have their authored combat data"
    );
    for p in &profiles {
        assert_eq!(p["arm"], 94);
        assert_eq!(p["health"], 50.0);
        assert_eq!(p["trigger"], 1.5);
        assert_eq!(p["damage_radius"], 1.5);
        assert_eq!(p["damage"], 130.0);
        assert_eq!(p["unit_x_impulse"], serde_json::json!([5.0, 5.0, 0.0]));
        assert_eq!(p["loadout"], 0x0b90_0000_0000_01edu64);
        assert!((1125..=1250).contains(&p["lifetime"].as_u64().unwrap()));
    }
    eprintln!("landed profile: {}", profiles[0]);
    late_viewer_receives_existing_mines(
        &state.rooms[&MATCH].world.as_ref().unwrap().scene,
        &schemas,
        &created,
        state.clock.frame(),
    );
    eprintln!(
        "Q created {} airborne and {} settled mines with {} distinct transforms and {} armed chunks on the caster's connection; first armed delivery {observed_arm_delay} ticks after first landing",
        airborne.len(),
        created.len(),
        positions.len(),
        armed.len()
    );
}
