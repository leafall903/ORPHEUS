use super::*;

#[derive(Clone, Copy)]
pub enum PreparedFixture {
    Stock,
    StockOwner,
    StockNameplate { hit_effect: bool },
    StockProjectile,
    StockSelfProjectile(GroundShot),
    StockBiotic,
    Owner,
    Combat,
    Projectile,
}

#[derive(Clone, Copy)]
pub struct GroundShot {
    pub hero: u64,
    pub definition: u32,
    pub expected_damage: f32,
}

pub fn exercise_prepared_world(fixture: PreparedFixture) {
    use std::collections::HashMap;

    use crate::{
        accounts::Accounts,
        catalog::ItemDb,
        collection::Collection,
        game_bots::BOT_ENTITY,
        jam_codec::id16,
        lobby_login::Identity,
        lobby_practice::{CREATE_GAME, GAME_REQUEST, SoloPracticeHandoff, dispatch_scene},
        lobby_session::PreparedLogin,
        match_handoff::{HANDOFF, HANDOFF_MESSAGE},
        retail_templates::RetailTemplates,
        scene_assets::SceneAssets,
        script_player::{
            fixture_body_factory, fixture_hitscan_factory, fixture_projectile_factory,
        },
    };

    const CONTROLLER: u32 = 0xa000_0100;
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let data = runtime.join("data");
    let schemas = Arc::new(Schemas::load(data.join("game_schemas_174.json")).unwrap());
    let lobby = Schemas::load(data.join("schemas_174.json")).unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scripted_bots = false;
    let mode_variables: crate::script_wire::Variables = assets
        .graphs()
        .mode_variables(PRACTICE_RANGE_MODE)
        .into_iter()
        .flatten()
        .map(|(&id, &value)| (id, value.into()))
        .collect();
    let expected_mode = crate::match_scene::mode_ui(assets.graphs(), &mode_variables).unwrap();
    let hit_effect = matches!(
        fixture,
        PreparedFixture::StockNameplate { hit_effect: true }
    );
    let target = BOT_ENTITY + if hit_effect { 5 } else { 1 };
    let mut nameplate_target_health = None;
    let mut allied_nameplates = HashSet::new();
    let collection = Collection::new(
        &RetailTemplates::load(data.join("retail_templates.json")).unwrap(),
        Arc::new(ItemDb::load(&data).unwrap()),
    )
    .unwrap();
    let temporary = tempfile::tempdir().unwrap();
    let accounts = Accounts::new(
        temporary.path().join("profiles"),
        temporary.path().join("seed"),
    )
    .unwrap();
    let account = accounts.get("world fixture").unwrap();
    let login = PreparedLogin {
        identity: Identity::create(account.account_lo, 1).unwrap(),
        account,
        tournament: false,
    };
    let fields = lobby
        .decode(GAME_REQUEST, CREATE_GAME, b"\x02\x00\x04\x00\x00\x00", true)
        .unwrap();
    let request = (GAME_REQUEST, CREATE_GAME, fields);
    let mut server = GameService::bind_with_timing(
        local(),
        Arc::clone(&schemas),
        matches!(fixture, PreparedFixture::Stock),
    )
    .unwrap();
    let channel = |id: MatchId, kind| {
        Fields::from([
            (0, Value::Struct(id16(id[0], id[1]))),
            (0x10, Value::Unsigned(kind)),
        ])
    };
    let mut outgoing = Vec::new();
    let result = dispatch_scene(
        &request,
        &login,
        &server,
        SoloPracticeHandoff {
            match_id: MATCH,
            host: "127.0.0.1",
            general: channel([7, 8], 7),
            match_channel: channel(MATCH, 6),
        },
        (&assets, &collection),
        |map| {
            let mut scene = assets.prepare(map)?;
            if hit_effect {
                scene.bot_simulation().0.enable_authored_hit_feedback(1.0);
            }
            scripted_bots = scene.scripted_bots() != 0;
            if matches!(
                fixture,
                PreparedFixture::StockOwner | PreparedFixture::StockNameplate { .. }
            ) {
                if matches!(fixture, PreparedFixture::StockNameplate { .. }) {
                    assert!(scripted_bots);
                    assert_eq!(
                        mode_variables.get(&9539),
                        Some(&crate::script_wire::Value::Bool(true))
                    );
                } else {
                    assert!(!scripted_bots);
                }
                let [x, y, z] = scene.map().spawn_point(0, 0).unwrap().0;
                let (bots, _) = scene.bot_simulation();
                if matches!(fixture, PreparedFixture::StockNameplate { .. }) {
                    allied_nameplates.extend(
                        bots.bodies()
                            .unwrap()
                            .filter(|bot| bot.spawn.team == 0)
                            .map(|bot| bot.entity),
                    );
                    assert!(!allied_nameplates.is_empty());
                }
                let bot = bots.body_mut(target).unwrap();
                bot.position = [x - 4.0, y, z];
                nameplate_target_health = Some(bot.spawn.health);
            }
            if matches!(
                fixture,
                PreparedFixture::Projectile | PreparedFixture::StockProjectile
            ) {
                let [x, y, z] = scene.map().spawn_point(0, 0).unwrap().0;
                let (bots, _) = scene.bot_simulation();
                assert_eq!(
                    bots.bodies()
                        .unwrap()
                        .take(3)
                        .map(|bot| bot.spawn.team)
                        .collect::<Vec<_>>(),
                    [0, 1, 1]
                );
                assert!(
                    bots.bodies()
                        .unwrap()
                        .take(3)
                        .all(|bot| bot.spawn.route.is_empty())
                );
                bots.body_mut(BOT_ENTITY).unwrap().position = [x - 3.5, y, z];
                bots.body_mut(BOT_ENTITY + 1).unwrap().position = [x - 5.0, y, z];
                bots.body_mut(BOT_ENTITY + 2).unwrap().position = [x - 5.0, y, z + 0.8];
            }
            if !matches!(
                fixture,
                PreparedFixture::Stock
                    | PreparedFixture::StockOwner
                    | PreparedFixture::StockNameplate { .. }
                    | PreparedFixture::StockProjectile
                    | PreparedFixture::StockSelfProjectile(_)
                    | PreparedFixture::StockBiotic
            ) {
                assert!(!scripted_bots);
                let factory = match fixture {
                    PreparedFixture::Combat => {
                        let [x, y, z] = scene.map().spawn_point(0, 0).unwrap().0;
                        let (bots, _) = scene.bot_simulation();
                        bots.body_mut(BOT_ENTITY + 1).unwrap().position = [x - 4.0, y, z];
                        fixture_hitscan_factory(100.0)
                    }
                    PreparedFixture::Projectile => {
                        let [x, y, z] = scene.map().spawn_point(0, 0).unwrap().0;
                        let (bots, _) = scene.bot_simulation();
                        assert_eq!(
                            bots.bodies()
                                .unwrap()
                                .take(3)
                                .map(|bot| bot.spawn.team)
                                .collect::<Vec<_>>(),
                            [0, 1, 1]
                        );
                        assert!(
                            bots.bodies()
                                .unwrap()
                                .take(3)
                                .all(|bot| bot.spawn.route.is_empty())
                        );
                        bots.body_mut(BOT_ENTITY).unwrap().position = [x - 3.5, y, z];
                        bots.body_mut(BOT_ENTITY + 1).unwrap().position = [x - 5.0, y, z];
                        bots.body_mut(BOT_ENTITY + 2).unwrap().position = [x - 5.0, y, z + 0.8];
                        fixture_projectile_factory(40.0)
                    }
                    _ => fixture_body_factory().0,
                };
                scene.owner_body_factory = Some(Arc::new(move |hero, spawn, _| {
                    if hero.guid != crate::game_heroes::SOLDIER {
                        return Ok(None);
                    }
                    Ok(Some(Box::new(factory.start(spawn)?)))
                }));
            }
            Ok(scene)
        },
        |(crc, id, fields)| {
            let bytes = lobby.encode(*crc, *id, fields).unwrap();
            outgoing.push((*crc, *id, lobby.decode(*crc, *id, &bytes, true).unwrap()));
            Ok(true)
        },
    );
    if scripted_bots
        && !matches!(
            fixture,
            PreparedFixture::Stock
                | PreparedFixture::StockNameplate { .. }
                | PreparedFixture::StockProjectile
                | PreparedFixture::StockSelfProjectile(_)
                | PreparedFixture::StockBiotic
        )
    {
        assert_eq!(result.err().unwrap().kind(), io::ErrorKind::Unsupported);
        assert!(outgoing.is_empty());
        return;
    }
    let ticket = result.unwrap().unwrap().handoff;
    assert_eq!(
        outgoing.iter().map(|(_, id, _)| *id).collect::<Vec<_>>(),
        [20404, 20402, HANDOFF_MESSAGE]
    );
    assert_eq!(outgoing[2].0, HANDOFF);
    let Value::Struct(wire) = &outgoing[2].2[&0x80] else {
        panic!("the decoded handoff has no seat body");
    };
    let key = |offset| {
        let Value::Array(bytes) = &wire[&offset] else {
            panic!("the decoded handoff has no direction key");
        };
        std::array::from_fn(|index| match bytes[index] {
            Value::Unsigned(byte) => u8::try_from(byte).unwrap(),
            _ => panic!("the handoff key contains an invalid byte"),
        })
    };
    assert_eq!(wire[&0x28], Value::Unsigned(u64::from(ticket.conn)));
    assert_eq!(
        wire[&0x2c],
        Value::Unsigned(u64::from(server.local_addr().port()))
    );
    let client_ticket = Handoff {
        key_in: key(0xae),
        key_out: key(0xce),
        ..ticket
    };
    let mut client = Client::new(server.local_addr(), &client_ticket);
    client.handshake();
    let load = client.receive();
    assert_eq!(messages(&schemas, &load)[0].message.id, 20300);
    assert!(client.window.accept(load.seq));
    let mut payload = BitWriter::new();
    let loaded = encode(&schemas, 21602, &Fields::new()).unwrap();
    write_channel(&mut payload, &[(0, loaded)], &[]).unwrap();
    let started = Instant::now();
    client.send(ACK, payload.into_bytes());

    let mut ownership = false;
    while started.elapsed() < Duration::from_millis(650) {
        let frame = client.receive();
        assert!(client.window.accept(frame.seq));
        assert!(world_records(&schemas, &frame).is_empty());
        ownership |= messages(&schemas, &frame)
            .iter()
            .any(|message| message.message.id == 20302);
    }
    assert!(ownership);
    client.send(ACK, Vec::new());

    let deadline = Instant::now() + Duration::from_secs(3);
    let lost = loop {
        let frame = client.receive();
        let records = world_records(&schemas, &frame);
        let creates: Vec<_> = records
            .iter()
            .filter(|record| record.is_create())
            .map(|record| record.entity)
            .collect();
        if !creates.is_empty() {
            assert!(creates.contains(&CONTROLLER) && creates.contains(&GAME_MODE_ENTITY));
            break creates; // Deliberately leave the first baseline out of the ACK window.
        }
        assert!(client.window.accept(frame.seq));
        client.send(ACK, Vec::new());
        assert!(Instant::now() < deadline, "waiting for first world creates");
    };
    let expected_bot_controllers: Vec<u32> = (1..=15)
        .map(|slot| crate::game_bots::BOT_CONTROLLER_BASE | (slot << 8))
        .collect();
    let mut creates = HashSet::new();
    let mut acknowledged_creates = HashSet::<u32>::new();
    let mut bot_possessions = HashMap::new();
    let mut retried_all_creates = false;
    let mut mode = false;
    let mut mode_ack_sent = false;
    let mut controller = false;
    let mut moving_bot = false;
    let mut npc_full_parents = HashMap::new();
    let mut acknowledged_npc_baselines = HashSet::new();
    let mut target_nameplate = !matches!(fixture, PreparedFixture::StockNameplate { .. });
    loop {
        let frame = client.receive();
        assert!(client.window.accept(frame.seq));
        for record in world_records(&schemas, &frame) {
            if record.is_create() {
                assert!(!retried_all_creates);
                assert!(creates.insert(record.entity));
            }
            if let Some(body) = record.possessed_body {
                if record.entity != CONTROLLER {
                    assert!(
                        acknowledged_creates.contains(&record.entity),
                        "possession arrived before bot controller create ACK for {:08x}",
                        record.entity
                    );
                    assert!(
                        acknowledged_creates.contains(&body),
                        "possession arrived before bot body create ACK for {body:08x}",
                    );
                    bot_possessions.insert(record.entity, body);
                }
            }
            if record.movement {
                assert!((BOT_ENTITY..BOT_ENTITY + 15).contains(&record.entity));
                assert!(acknowledged_creates.contains(&record.entity));
                moving_bot = true;
            }
            if let Some(script) = record.script {
                assert!(
                    acknowledged_creates.contains(&record.entity),
                    "script arrived before the create ACK for {:08x}",
                    record.entity
                );
                if record.entity == GAME_MODE_ENTITY {
                    assert_eq!((script.first, script.span), (0, 1));
                    assert_eq!(script.width, expected_mode.bit_len());
                    assert_eq!(script.data, expected_mode.as_bytes());
                    if mode_variables.is_empty() {
                        assert_eq!(script.data, [0x08, 0x5b, 0x06, 0x00, 0x0c]);
                    }
                    mode = true;
                } else if record.entity == CONTROLLER {
                    assert_eq!((script.first, script.span), (0, 1));
                    let mut bits = script.bits();
                    assert_eq!(bits.bits(3).unwrap(), 2);
                    assert!(bits.w_var().unwrap() >= crate::game_clock::FIRST_TICK);
                    controller = true;
                } else {
                    assert!(
                        scripted_bots && (BOT_ENTITY..BOT_ENTITY + 15).contains(&record.entity)
                    );
                    let descriptions = observer_descriptors(&script);
                    if script.first == 0 {
                        assert!(
                            !descriptions.iter().any(|entry| entry.graph == 0x0d24),
                            "nameplate was included in the parent baseline"
                        );
                        npc_full_parents.insert(
                            record.entity,
                            descriptions
                                .into_iter()
                                .map(|entry| (entry.id, entry.graph))
                                .collect::<HashMap<_, _>>(),
                        );
                    } else if let Some(nameplate) =
                        descriptions.iter().find(|entry| entry.graph == 0x0d24)
                    {
                        assert!(mode_ack_sent, "nameplate arrived before mode frame ACK");
                        assert!(
                            acknowledged_npc_baselines.contains(&record.entity),
                            "nameplate arrived before the body script full-frame ACK"
                        );
                        let (parent, state) = nameplate.parent.expect("body-owned nameplate");
                        assert_eq!(npc_full_parents[&record.entity].get(&parent), Some(&0x0c18));
                        assert_eq!(state, 3);
                        target_nameplate |= record.entity == target;
                        allied_nameplates.remove(&record.entity);
                    }
                }
            }
        }
        if !retried_all_creates && creates.len() == 36 && lost.iter().all(|id| creates.contains(id))
        {
            for &id in &expected_bot_controllers {
                assert!(creates.contains(&id), "missing bot controller {id:08x}");
            }
            retried_all_creates = true;
            // Receive one more datagram before acknowledging these creates.
            // It may carry scripts only for entities acknowledged earlier.
            continue;
        }
        client.send(ACK, Vec::new());
        acknowledged_creates.extend(&creates);
        acknowledged_npc_baselines.extend(npc_full_parents.keys().copied());
        mode_ack_sent = mode;
        if retried_all_creates
            && mode
            && controller
            && moving_bot
            && target_nameplate
            && allied_nameplates.is_empty()
            && bot_possessions.len() == 15
        {
            let possessed_bodies: HashSet<_> = bot_possessions.values().copied().collect();
            let expected_bodies: HashSet<_> = (BOT_ENTITY..BOT_ENTITY + 15).collect();
            assert_eq!(possessed_bodies, expected_bodies);
            break;
        }
        assert!(
            Instant::now() < deadline,
            "waiting for world and script baselines; allied nameplates pending: {allied_nameplates:?}"
        );
    }
    match fixture {
        PreparedFixture::Owner => {
            exercise_owner_vm(&mut client, &schemas, true);
        }
        PreparedFixture::StockOwner => {
            let frame = exercise_owner_vm(&mut client, &schemas, false);
            exercise_stock_rifle(&mut client, &schemas, frame + 1, BOT_ENTITY + 1);
        }
        PreparedFixture::StockNameplate { .. } => {
            let frame = pick_soldier_for_weapon(&mut client, &schemas);
            exercise_stock_nameplate_hit(
                &mut client,
                &schemas,
                frame,
                target,
                nameplate_target_health.unwrap(),
                hit_effect.then_some(&server),
            );
        }
        PreparedFixture::Combat => {
            exercise_combat(&mut client, &schemas, BOT_ENTITY + 1, BOT_ENTITY + 15)
        }
        PreparedFixture::Projectile => exercise_projectile(&mut client, &schemas, None),
        PreparedFixture::StockProjectile => {
            let frame = exercise_owner_vm(&mut client, &schemas, false);
            let launch = wait_for_stock_weapon(&mut client, &schemas, frame + 1);
            exercise_projectile(&mut client, &schemas, Some(launch));
        }
        PreparedFixture::StockSelfProjectile(shot) => {
            let frame = pick_stock_actor(&mut client, &schemas, shot.hero);
            let launch = wait_for_stock_weapon(&mut client, &schemas, frame);
            exercise_stock_self_splash(&mut client, &schemas, &server, launch, shot);
        }
        PreparedFixture::StockBiotic => {
            let frame = exercise_owner_vm(&mut client, &schemas, false);
            let launch = wait_for_stock_weapon(&mut client, &schemas, frame + 1);
            exercise_stock_biotic(&mut client, &schemas, launch);
        }
        PreparedFixture::Stock => {
            exercise_hero_selection(&mut client, &schemas, &assets, &server, &ticket);
            let snapshot = server.debug_snapshot().unwrap();
            let timing = snapshot.timing.as_ref().unwrap();
            assert!(timing.tick_duration_us.samples > 0);
            assert_eq!(
                timing.schedule_lateness_us.samples,
                timing.tick_duration_us.samples
            );
            assert_eq!(
                timing.tick_duration_us.counts.iter().sum::<u64>(),
                timing.tick_duration_us.samples
            );
            let [room] = snapshot.rooms.as_slice() else {
                panic!("expected one prepared room");
            };
            assert!(!snapshot.truncated);
            assert_eq!(
                room.map,
                format!("{:016X}", GameMap::practice_range().map_guid)
            );
            assert_eq!(room.actors.len(), 16);
            let actor = room
                .actors
                .iter()
                .find(|actor| actor.entity == CONTROLLER | 3)
                .unwrap();
            assert!(matches!(actor.kind, game_debug::ActorKind::Player));
            assert_eq!(actor.generation, Some(3));
            assert!(actor.position.into_iter().all(f64::is_finite));
            let bot = room
                .actors
                .iter()
                .find(|actor| actor.entity == BOT_ENTITY)
                .unwrap();
            assert!(matches!(bot.kind, game_debug::ActorKind::Bot));
            assert!(bot.alive && bot.health.is_some_and(|health| health > 0.0));
            assert!(!bot.script_active);
            assert!(room.actors.iter().any(|actor| {
                matches!(actor.kind, game_debug::ActorKind::Bot) && actor.script_active
            }));
        }
    }
    server.stop().unwrap();
}

struct ObserverDescriptor {
    id: u32,
    graph: u32,
    parent: Option<(u32, u32)>,
}

fn observer_descriptors(script: &ReceivedChunk) -> Vec<ObserverDescriptor> {
    let mut reader = script.bits();
    assert_eq!(reader.bits(2).unwrap(), 0, "observer frame header");
    let mut descriptions = Vec::new();
    let mut id = 0;
    loop {
        let offset = reader.w_u16().unwrap();
        if offset == 0 {
            break;
        }
        id += u32::from(offset);
        if script.first != 0 && (reader.bit().unwrap() || !reader.bit().unwrap()) {
            continue;
        }
        let graph = reader.bits(16).unwrap() as u32;
        assert_eq!(reader.bits(2).unwrap(), 0);
        let parent = reader
            .bit()
            .unwrap()
            .then(|| (reader.w_var().unwrap(), reader.w_var().unwrap()));
        assert!(!reader.bit().unwrap(), "unexpected descriptor references");
        descriptions.push(ObserverDescriptor { id, graph, parent });
    }
    descriptions
}

pub fn send_game_message(
    client: &mut Client,
    schemas: &Schemas,
    ordinal: u32,
    guid: u64,
    parameters: Vec<u8>,
) {
    let fields = Fields::from([
        (
            0x78,
            Value::Struct(Fields::from([(
                0,
                Value::Unsigned(u64::from(player_entity(0))),
            )])),
        ),
        (0x80, Value::Blob(parameters)),
        (0xa8, Value::Unsigned(guid)),
        (0xb0, Value::Signed(3)),
    ]);
    let message = encode(
        schemas,
        crate::game_parameters::CLIENT_GAME_MESSAGE,
        &fields,
    )
    .unwrap();
    let mut payload = BitWriter::new();
    write_channel(&mut payload, &[(ordinal, message)], &[]).unwrap();
    client.send(ACK, payload.into_bytes());
}

pub fn pick_soldier_for_weapon(client: &mut Client, schemas: &Schemas) -> u32 {
    pick_stock_actor(client, schemas, crate::game_heroes::SOLDIER)
}

fn pick_stock_actor(client: &mut Client, schemas: &Schemas, hero: u64) -> u32 {
    use crate::game_parameters::PICK_HERO;
    let body = player_entity(0) | 1;
    send_game_message(client, schemas, 1, PICK_HERO, hero.to_le_bytes().to_vec());
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        client.send(ACK, Vec::new());
        if world_records(schemas, &packet)
            .iter()
            .any(|record| record.entity == body && record.is_create())
        {
            return BitReader::new(&packet.payload).var_a().unwrap() + 1;
        }
        assert!(Instant::now() < deadline, "waiting for weapon fixture body");
    }
}

pub fn exercise_combat(client: &mut Client, schemas: &Schemas, target: u32, replacement: u32) {
    let frame = pick_soldier_for_weapon(client, schemas);
    let fire = crate::commands::FIRE;
    let commands = command_batch(frame, 1, -16384, fire, false);
    client.send(ACK, commands.clone());
    client.send(ACK, commands);

    let deadline = Instant::now() + Duration::from_secs(7);
    let mut lost = None;
    let mut delivered_hit = false;
    let mut delivered_kill = false;
    let mut delivered_health = false;
    let mut destroyed = false;
    let mut created = false;
    while !created {
        let packet = client.receive();
        let reliable = messages(schemas, &packet);
        let hit = reliable.iter().find(|item| item.message.id == 25001);
        if lost.is_none() {
            if let Some(hit) = hit {
                lost = Some((hit.sequence, hit.message.fields.clone()));
                continue; // let a following ACK reveal loss of the original hit
            }
        }
        for item in reliable {
            match item.message.id {
                25001 => {
                    let (ordinal, fields) = lost.as_ref().unwrap();
                    assert_eq!((&item.sequence, &item.message.fields), (ordinal, fields));
                    assert_eq!(fields[&0x84], Value::Unsigned(u64::from(frame)));
                    let Value::Struct(impact) = &fields[&0x78] else {
                        panic!("hit body")
                    };
                    assert_eq!(impact[&4], Value::Unsigned(200));
                    assert_eq!(impact[&8], Value::Unsigned(3));
                    delivered_hit = true;
                }
                25005 => {
                    let Value::Struct(victim) = &item.message.fields[&0x84] else {
                        panic!("elimination body")
                    };
                    assert_eq!(victim[&0], Value::Unsigned(u64::from(target)));
                    delivered_kill = true;
                }
                _ => {}
            }
        }
        for record in world_records(schemas, &packet) {
            if record.entity == target {
                if record.operation == Some(2) {
                    delivered_health = true;
                }
                if record.operation == Some(3) {
                    assert!(delivered_hit && delivered_kill && delivered_health);
                    destroyed = true;
                }
            } else if record.entity == replacement && record.is_create() {
                assert!(destroyed);
                created = true;
            }
        }
        assert!(client.window.accept(packet.seq));
        client.send(ACK, Vec::new());
        assert!(
            Instant::now() < deadline,
            "waiting for combat recovery and bot respawn"
        );
    }
}

pub fn exercise_projectile(client: &mut Client, schemas: &Schemas, stock_frame: Option<u32>) {
    use crate::{
        commands::SECONDARY, game_bots::BOT_ENTITY, game_projectiles::ProjectileLeases,
        script_player::FIXTURE_PROJECTILE_DEFINITION,
    };

    let frame = stock_frame.unwrap_or_else(|| pick_soldier_for_weapon(client, schemas));
    let definition = FIXTURE_PROJECTILE_DEFINITION;
    let mut original = stock_frame.is_none().then(|| {
        ProjectileLeases::new(0, frame, [definition])
            .unwrap()
            .entity(definition, frame, 0)
            .unwrap()
    });
    let commands = command_batch(frame, 1, -16384, SECONDARY, false);
    client.send(ACK, commands.clone());
    client.send(ACK, commands);

    let deadline = Instant::now() + Duration::from_secs(3);
    let mut noticed = HashSet::new();
    let mut impacts = Vec::new();
    let mut health = HashMap::new();
    let mut creates = HashSet::new();
    let mut releases = HashSet::new();
    let mut lost_original = false;
    while !original.is_some_and(|id| releases.contains(&id))
        || health.len() < 2
        || impacts.len() < 3
    {
        let packet = client.receive();
        let tick = BitReader::new(&packet.payload).var_a().unwrap();
        let records = world_records(schemas, &packet);
        let first_create = records.iter().find(|record| {
            original.is_none_or(|id| record.entity == id)
                && record.definition == Some(definition as u32)
        });
        if let (false, Some(create)) = (lost_original, first_create) {
            assert!(records.iter().all(|record| {
                record.entity != create.entity || record.projectile_release.is_none()
            }));
            original = Some(create.entity);
            lost_original = true;
            continue; // a later ACK will expose the lost confirmation datagram
        }
        for item in messages(schemas, &packet) {
            assert_ne!(
                item.message.id, 25005,
                "one Helix flight cannot eliminate this target"
            );
            if item.message.id != 25001 || !noticed.insert(item.sequence) {
                continue;
            }
            let fields = &item.message.fields;
            let Value::Unsigned(impact_frame) = fields[&0x84] else {
                panic!("impact frame")
            };
            assert!(impact_frame > u64::from(frame));
            let Value::Struct(impact) = &fields[&0x78] else {
                panic!("hit body")
            };
            let Value::Struct(target) = &impact[&0] else {
                panic!("hit target")
            };
            assert_eq!(impact[&8], Value::Unsigned(0));
            impacts.push((target[&0].clone(), impact[&4].clone()));
        }
        for record in records {
            if let Some(current) = record.base_health {
                health.insert(record.entity, current);
            }
            if record.definition == Some(definition as u32) {
                creates.insert(record.entity);
            }
            if let Some(release_frame) = record.projectile_release {
                assert!(creates.contains(&record.entity));
                assert_eq!(record.operation, Some(3));
                assert!(release_frame > tick);
                releases.insert(record.entity);
            }
        }
        assert!(client.window.accept(packet.seq));
        client.send(ACK, Vec::new());
        assert!(
            Instant::now() < deadline,
            "waiting for Helix: shot {frame}, entity {original:?}, impacts {impacts:?}, health {health:?}, creates {creates:?}, releases {releases:?}"
        );
    }
    assert!(lost_original);
    let impact = |entity, damage| (Value::Unsigned(u64::from(entity)), Value::Unsigned(damage));
    assert_eq!(
        impacts,
        [
            impact(BOT_ENTITY + 1, 40),
            impact(BOT_ENTITY + 1, 80),
            impact(BOT_ENTITY + 2, 80)
        ]
    );
    assert_eq!(
        health,
        HashMap::from([(BOT_ENTITY + 1, 80.0), (BOT_ENTITY + 2, 120.0)])
    );
}

pub fn exercise_stock_biotic(client: &mut Client, schemas: &Schemas, launch: u32) {
    client.send(ACK, command_batch(launch, 32, 0, 0x10, false));
    client.send(ACK, command_batch(launch + 32, 32, 0, 0, false));
    let deadline = Instant::now() + Duration::from_secs(9);
    let mut canisters = HashMap::new();
    let mut fields = HashMap::new();
    loop {
        let packet = client.receive();
        let tick = BitReader::new(&packet.payload).var_a().unwrap();
        assert!(client.window.accept(packet.seq));
        let mut retired = false;
        let records = world_records(schemas, &packet);
        for record in &records {
            if record.is_create() && record.definition == Some(0x03dc) {
                assert!(
                    !canisters.is_empty(),
                    "field preceded canister confirmation"
                );
                fields.entry(record.entity).or_insert((0, None));
            }
            if let Some((phase, destroyed)) = fields.get_mut(&record.entity) {
                if let Some(script) = &record.script {
                    assert!(!record.is_create(), "field script preceded create ACK");
                    match (script.width, script.data.as_slice()) {
                        (48, [0x08, 0x2c, 0x01, 0, 0x78, 0x0f]) => {
                            assert!(*phase <= 1);
                            *phase = 1;
                        }
                        (47, [0x08, 0x2c, 0x01, 0, 0x78, 0x03]) => {
                            assert!(*phase >= 1, "fade preceded active field");
                            *phase = 2;
                        }
                        _ => panic!("unexpected field script {script:?}"),
                    }
                }
                if record.operation == Some(3) {
                    assert_eq!(*phase, 2, "field retired before fade");
                    destroyed.get_or_insert(tick);
                }
            }
            if let (Some(3), Some(&created)) = (record.operation, canisters.get(&record.entity)) {
                assert!(record.projectile_release.is_none());
                // The create can arrive after prediction acknowledgement.
                assert!(
                    tick - created >= 250,
                    "canister retired before field expiry"
                );
                assert!(!fields.is_empty(), "no field entity was replicated");
                assert!(
                    fields
                        .values()
                        .all(|(_, destroyed)| destroyed.is_some_and(|t| t < tick)),
                    "canister retired before field destroy ACK"
                );
                retired = true;
            }
        }
        for record in records {
            if record.is_create()
                && record.definition == Some(crate::game_combat::BIOTIC_CANISTER as u32)
            {
                canisters.entry(record.entity).or_insert(tick);
            }
        }
        client.send(ACK, Vec::new());
        if retired {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "waiting for canister and field lifecycle: {canisters:?}, {fields:?}"
        );
    }
}

fn exercise_stock_self_splash(
    client: &mut Client,
    schemas: &Schemas,
    server: &GameService,
    launch: u32,
    shot: GroundShot,
) {
    use crate::{commands::SECONDARY, game_debug::ActorKind};
    let snapshot_owner = || {
        server
            .debug_snapshot()
            .unwrap()
            .rooms
            .into_iter()
            .flat_map(|room| room.actors)
            .find(|actor| matches!(actor.kind, ActorKind::Player))
            .expect("stock owner must have a body")
    };
    let before = snapshot_owner();
    let initial_health = before.health.unwrap() as f32;
    let pitch = 16_384;
    client.send(
        ACK,
        command_batch_with_pitch(launch, 1, -16_384, pitch, SECONDARY, false),
    );
    let mut predicted = HashSet::new();
    let mut released = false;
    let mut owner_health = None;
    let deadline = Instant::now() + Duration::from_secs(3);
    while !released || owner_health.is_none() {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        for record in world_records(schemas, &packet) {
            if record.definition == Some(shot.definition) {
                predicted.insert(record.entity);
            }
            if record.projectile_release.is_some() && predicted.contains(&record.entity) {
                assert_eq!(record.operation, Some(3));
                released = true;
            }
            if record.entity == before.entity {
                if let Some(health) = record
                    .total_health
                    .filter(|health| *health < initial_health)
                {
                    owner_health = Some(health);
                }
            }
        }
        assert!(
            messages(schemas, &packet)
                .iter()
                .all(|message| { !matches!(message.message.id, 25001 | 25005) })
        );
        client.send(ACK, Vec::new());
        assert!(
            Instant::now() < deadline,
            "waiting for self splash and release, health={owner_health:?} release={released} predicted={predicted:?} server={:?}",
            snapshot_owner().health,
        );
    }
    let owner_health = owner_health.unwrap();
    assert!(owner_health > 0.0);
    assert_eq!(initial_health - owner_health, shot.expected_damage);
    assert_eq!(
        snapshot_owner().health.map(|value| value as f32),
        Some(owner_health)
    );
    // The room registers the impulse at the first real pose after impact.
    client.send(
        ACK,
        command_batch_with_pitch(launch + 1, 8, -16_384, pitch, 0, false),
    );
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut corrected = false;
    loop {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        corrected |= world_records(schemas, &packet).iter().any(|record| {
            record.entity == before.entity
                && record.operation.is_none()
                && record
                    .movement_frame
                    .is_some_and(|frame| (launch + 1..=launch + 8).contains(&frame))
        });
        client.send(ACK, Vec::new());
        if corrected && input_ack(schemas, &packet) >= launch + 8 {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "waiting for posed movement and owner correction after self splash"
        );
    }
    let after = snapshot_owner();
    assert_eq!(after.health.map(|value| value as f32), Some(owner_health));
    assert!(
        after.position[1] > before.position[1],
        "self splash did not raise owner from {:?} to {:?}",
        before.position,
        after.position
    );
    assert!(after.velocity[1] > 0.0, "{:?}", after.velocity);
}

pub fn wait_for_stock_weapon(client: &mut Client, schemas: &Schemas, frame: u32) -> u32 {
    // The authored weapon startup must finish before combat input.
    let warmup = command_batch(frame, 24, -16384, 0, false);
    client.send(ACK, warmup);
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        client.send(ACK, Vec::new());
        if input_ack(schemas, &packet) >= frame + 23 {
            break;
        }
        assert!(Instant::now() < deadline, "waiting for the rifle warmup");
    }
    frame + 24
}

pub fn exercise_stock_rifle(client: &mut Client, schemas: &Schemas, frame: u32, target: u32) {
    let frame = wait_for_stock_weapon(client, schemas, frame);
    client.send(
        ACK,
        command_batch(frame, 8, -16384, crate::commands::FIRE, false),
    );
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut damaged = false;
    let mut reported = false;
    while !damaged || !reported {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        for message in messages(schemas, &packet) {
            if message.message.id == 25001 {
                let Value::Struct(impact) = &message.message.fields[&0x78] else {
                    panic!("missing hit body");
                };
                assert_eq!(
                    impact[&0],
                    Value::Struct(Fields::from([(0, Value::Unsigned(u64::from(target)))]))
                );
                assert!(matches!(impact[&4], Value::Unsigned(amount) if amount > 0));
                reported = true;
            }
        }
        damaged |= world_records(schemas, &packet).iter().any(|record| {
            record.entity == target
                && record.operation == Some(2)
                && record
                    .base_health
                    .is_some_and(|health| (0.0..200.0).contains(&health))
        });
        client.send(ACK, Vec::new());
        assert!(
            Instant::now() < deadline,
            "waiting for an authored rifle hit"
        );
    }
}

fn exercise_stock_nameplate_hit(
    client: &mut Client,
    schemas: &Schemas,
    first: u32,
    target: u32,
    initial_health: f64,
    verify_active: Option<&GameService>,
) {
    let frame = wait_for_stock_weapon(client, schemas, first);
    let body = player_entity(0) | 1;
    let fire = crate::commands::FIRE;
    client.send(ACK, command_batch(frame, 1, -16384, fire, false));
    client.send(ACK, command_batch(frame + 1, 1, -16384, 0, false));

    let deadline = Instant::now() + Duration::from_secs(3);
    let (mut lost, mut seen_damage, mut seen_health) = (None, None, None);
    while seen_damage.is_none() || seen_health.is_none() {
        let packet = client.receive();
        let reliable = messages(schemas, &packet);
        if lost.is_none() {
            if let Some(hit) = reliable.iter().find(|item| item.message.id == 25001) {
                lost = Some((hit.sequence, hit.message.fields.clone()));
                if verify_active.is_some() {
                    client.send(ACK, command_batch(frame + 2, 8, -16384, 0, false));
                }
                continue;
            }
        }
        let tick = BitReader::new(&packet.payload).var_a().unwrap();
        for item in reliable {
            assert_ne!(
                item.message.id, 25005,
                "one rifle shot should leave this bot alive"
            );
            if item.message.id != 25001 {
                continue;
            }
            let (ordinal, fields) = lost.as_ref().unwrap();
            assert_eq!((&item.sequence, &item.message.fields), (ordinal, fields));
            assert_eq!(fields[&0x84], Value::Unsigned(u64::from(frame)));
            assert!(
                crate::game_clock::TICK_PERIOD * tick.abs_diff(frame) < Duration::from_secs(5),
                "hit notice frame {frame} is outside the recency window at tick {tick}"
            );
            let Value::Struct(impact) = &fields[&0x78] else {
                panic!("missing hit body");
            };
            assert_eq!(
                impact[&0],
                Value::Struct(Fields::from([(0, Value::Unsigned(u64::from(target)))]))
            );
            let Value::Unsigned(amount) = impact[&4] else {
                panic!("missing hit damage");
            };
            assert!(amount > 0);
            let Value::Unsigned(flags) = impact[&8] else {
                panic!("missing hit flags");
            };
            assert_eq!(flags & 2, 0);
            seen_damage = Some(amount);
        }
        for record in world_records(schemas, &packet) {
            if record.entity == target && record.operation == Some(2) {
                if let Some(health) = record.base_health {
                    let health = f64::from(health);
                    assert!((0.0..initial_health).contains(&health) && health != 0.0);
                    seen_health = Some(health);
                }
            }
        }
        assert!(client.window.accept(packet.seq));
        client.send(ACK, Vec::new());
        assert!(
            Instant::now() < deadline,
            "missing combat delivery: damage={seen_damage:?}, health={seen_health:?}"
        );
    }
    if let Some(server) = verify_active {
        let snapshot = server.debug_snapshot().unwrap();
        let script = snapshot
            .rooms
            .iter()
            .flat_map(|room| &room.scripts)
            .find(|script| script.entity == body)
            .expect("the owner VM must remain mounted after hitting a stationary bot");
        assert_eq!(script.diagnostics, 0);
    }
    let actual_damage = initial_health - seen_health.unwrap();
    assert_eq!(actual_damage.round_ties_even() as u64, seen_damage.unwrap());
}

pub fn exercise_owner_vm(client: &mut Client, schemas: &Schemas, forward: bool) -> u32 {
    use crate::{game_heroes::SOLDIER, game_parameters::PICK_HERO};

    let owner = player_entity(0);
    let body = owner | 1;
    send_game_message(
        client,
        schemas,
        1,
        PICK_HERO,
        SOLDIER.to_le_bytes().to_vec(),
    );
    let deadline = Instant::now() + Duration::from_secs(2);
    let start = loop {
        let packet = client.receive();
        let records = world_records(schemas, &packet);
        assert!(
            records
                .iter()
                .all(|record| record.entity != body || record.script.is_none())
        );
        assert!(client.window.accept(packet.seq));
        client.send(ACK, Vec::new());
        if records
            .iter()
            .any(|record| record.entity == body && record.is_create())
        {
            let controller = records
                .iter()
                .find(|record| record.entity == owner && record.script.is_some())
                .unwrap();
            let mut bits = controller.script.as_ref().unwrap().bits();
            assert_eq!(bits.bits(3).unwrap(), 2);
            break bits.w_var().unwrap();
        }
        assert!(Instant::now() < deadline, "waiting for fixture body create");
    };
    let wait_chunk = |client: &mut Client| {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let packet = client.receive();
            if let Some(script) = world_records(schemas, &packet)
                .into_iter()
                .find(|record| record.entity == body && record.script.is_some())
                .and_then(|record| record.script)
            {
                return (packet.seq, script);
            }
            assert!(client.window.accept(packet.seq));
            client.send(ACK, Vec::new());
            assert!(Instant::now() < deadline, "waiting for fixture body script");
        }
    };
    let skip_chunk = |client: &mut Client, payload| {
        let packet = client.receive();
        assert!(
            world_records(schemas, &packet)
                .iter()
                .all(|record| record.entity != body || record.script.is_none())
        );
        assert!(client.window.accept(packet.seq));
        client.send(ACK, payload);
    };
    let accept_chunk = |client: &mut Client, sequence| {
        assert!(client.window.accept(sequence));
        client.send(ACK, Vec::new());
    };
    let check_delta = |chunk: &ReceivedChunk, corrected, command_frame| {
        let mut bits = chunk.bits();
        assert_eq!(bits.bits(3).unwrap(), if corrected { 6 } else { 2 });
        assert_eq!(bits.w_var().unwrap(), command_frame);
    };

    let (_, h0) = wait_chunk(client);
    assert_eq!((h0.first, h0.span), (0, 1));
    assert_eq!(h0.bits().bits(2).unwrap(), 0);
    skip_chunk(client, Vec::new());
    let (sequence, retry) = wait_chunk(client);
    assert_eq!(retry, h0);
    accept_chunk(client, sequence);

    let (_, c0) = wait_chunk(client);
    assert_eq!((c0.first, c0.span), (2, 0));
    check_delta(&c0, false, start);
    skip_chunk(client, command_batch(start + 1, 16, 0, 0, forward));
    let (sequence, retry) = wait_chunk(client);
    assert_eq!(retry, c0);
    accept_chunk(client, sequence);

    let (_, c1) = wait_chunk(client);
    assert_eq!((c1.first, c1.span), (3, 0));
    check_delta(&c1, true, start + 16);
    skip_chunk(client, command_batch(start + 17, 1, 0, 0, forward));
    let (sequence, cumulative) = wait_chunk(client);
    assert_eq!((cumulative.first, cumulative.span), (3, 1));
    check_delta(&cumulative, true, start + 17);
    accept_chunk(client, sequence);
    start + 17
}

pub fn exercise_hero_selection(
    client: &mut Client,
    schemas: &Schemas,
    assets: &crate::scene_assets::SceneAssets,
    server: &GameService,
    ticket: &Handoff,
) {
    use crate::{
        game_heroes::SOLDIER,
        game_parameters::{OPEN_OR_CLOSE, PICK_HERO},
    };
    const OWNER: u32 = 0xa000_0100;
    const FIRST: u32 = OWNER | 1;
    const SECOND: u32 = OWNER | 2;

    let send = |client: &mut Client, ordinal, guid, parameters| {
        send_game_message(client, schemas, ordinal, guid, parameters);
    };
    let next_body_frame = |client: &mut Client, body| {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let packet = client.receive();
            let records = world_records(schemas, &packet);
            if records
                .iter()
                .any(|record| record.entity == body && record.is_create())
            {
                break (packet, records);
            }
            assert!(client.window.accept(packet.seq));
            client.send(ACK, Vec::new());
            assert!(
                Instant::now() < deadline,
                "waiting for the selected body {body:08x}"
            );
        }
    };
    let describe = |records: &[WorldRecord]| {
        records
            .iter()
            .filter(|record| matches!(record.entity, OWNER | FIRST | SECOND))
            .filter(|record| {
                record.entity != OWNER
                    || record.operation != Some(2)
                    || record.possessed_body.is_some()
            })
            .map(|record| {
                (
                    record.entity,
                    record.operation,
                    record.script.as_ref().map(|script| script.span),
                )
            })
            .collect::<Vec<_>>()
    };
    let controller =
        crate::game_controller::Controller::load(assets.graphs(), PRACTICE_RANGE_MODE).unwrap();
    let verify_controller = |records: &[WorldRecord], hero, open| {
        let script = records
            .iter()
            .find(|record| record.entity == OWNER && record.script.is_some())
            .and_then(|record| record.script.as_ref())
            .expect("hero selection controller frame");
        assert!(
            script.first > 0,
            "selection restarted the controller graphs"
        );
        let mut reader = script.bits();
        assert_eq!(reader.bits(3).unwrap(), 2);
        let cmfd = reader.w_var().unwrap();
        let expected = controller
            .selection_delta(
                cmfd,
                ControllerFrame {
                    select_open: open,
                    skins: true,
                    hide_assemble: true,
                    hero: Some(hero),
                    ..ControllerFrame::default()
                },
                false,
            )
            .unwrap();
        assert_eq!(script.width, expected.bit_len());
        assert_eq!(script.data, expected.as_bytes());
        cmfd
    };

    send(client, 1, PICK_HERO, SOLDIER.to_le_bytes().to_vec());
    let (_lost, records) = next_body_frame(client, FIRST);
    let expected = [
        (FIRST, Some(1), None),
        (OWNER, Some(2), None),
        (OWNER, None, Some(0)),
    ];
    assert_eq!(describe(&records), expected);
    assert!(
        records
            .iter()
            .any(|record| record.entity == FIRST && record.movement)
    );
    let first_cmfd = verify_controller(&records, SOLDIER, false);

    // A later datagram ACK identifies the first body batch as lost.
    let following = client.receive();
    assert!(client.window.accept(following.seq));
    assert!(describe(&world_records(schemas, &following)).is_empty());
    client.send(ACK, Vec::new());
    let (retry, records) = next_body_frame(client, FIRST);
    assert_eq!(describe(&records), expected);
    assert_eq!(verify_controller(&records, SOLDIER, false), first_cmfd);
    assert!(client.window.accept(retry.seq));

    let tick = BitReader::new(&retry.payload).var_a().unwrap();
    let first_input = tick + 32;
    let mut newest = first_input + 4;
    let mut controller_end = 2; // The first selection delta just acknowledged.
    let observe_controller_ack = |records: &[WorldRecord], end: &mut u32| {
        for script in records
            .iter()
            .filter(|record| record.entity == OWNER)
            .filter_map(|record| record.script.as_ref())
            .filter(|script| script.first != 0)
        {
            let mut bits = script.bits();
            assert_eq!(bits.bits(3).unwrap(), 2);
            let cmfd = bits.w_var().unwrap();
            if bits.w_var().unwrap() != 7 {
                continue;
            }
            let expected = crate::script_wire::owner_ack(cmfd).unwrap();
            assert_eq!(script.width, expected.bit_len());
            assert_eq!(script.data, expected.as_bytes());
            assert!(script.first <= *end + 1);
            *end = (*end).max(script.first + script.span);
        }
    };
    // The acknowledged stock body has an owner VM, so its own movement
    // records are owner corrections, as from Python's correction.own_record.
    let mut corrected = false;
    let mut await_input = |client: &mut Client, expected, controller_end: &mut u32| {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let packet = client.receive();
            assert!(client.window.accept(packet.seq));
            let records = world_records(schemas, &packet);
            observe_controller_ack(&records, controller_end);
            corrected |= records
                .iter()
                .any(|record| record.entity == FIRST && record.movement);
            if input_ack(schemas, &packet) == expected {
                return;
            }
            client.send(ACK, Vec::new());
            assert!(
                Instant::now() < deadline,
                "waiting for command acknowledgement"
            );
        }
    };
    client.send(ACK, input_batch(first_input, 5, -31010));
    await_input(client, newest, &mut controller_end);
    client.send(ACK, input_batch(newest + 2_000, 1, -31010));
    for _ in 0..2 {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        assert_eq!(input_ack(schemas, &packet), newest);
        observe_controller_ack(&world_records(schemas, &packet), &mut controller_end);
        client.send(ACK, Vec::new());
    }
    newest += 48;
    let recoverable = input_batch(newest, 1, -31010);
    for _ in 0..=RECEIVE_BATCH {
        client.send(ACK, recoverable.clone());
    }
    await_input(client, newest, &mut controller_end);
    // Only the tagged stock pack completes owner VM startup.
    let soldier = assets.heroes().get(SOLDIER).unwrap();
    let attached = assets.graphs().bodies()[&soldier.body]
        .target_tags
        .is_some();
    assert_eq!(corrected, attached);

    let mut open = vec![1, 1];
    open.extend(OWNER.to_le_bytes());
    open.extend(OWNER.to_le_bytes());
    send(client, 2, OPEN_OR_CLOSE, open);
    let deadline = Instant::now() + Duration::from_secs(2);
    let reopen_cmfd = loop {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        let records = world_records(schemas, &packet);
        observe_controller_ack(&records, &mut controller_end);
        let changes: Vec<_> = describe(&records)
            .into_iter()
            .filter(|(entity, operation, _)| *entity != FIRST || operation.is_some())
            .collect();
        if !changes.is_empty() {
            let [(entity, operation, _)] = changes.as_slice() else {
                panic!("unexpected records while reopening hero selection: {changes:?}");
            };
            assert_eq!((*entity, *operation), (OWNER, None));
            let script = records
                .iter()
                .find(|record| record.entity == OWNER)
                .and_then(|record| record.script.as_ref())
                .expect("controller chunk while reopening hero selection");
            let mut bits = script.bits();
            assert_eq!(bits.bits(3).unwrap(), 2);
            bits.w_var().unwrap();
            if bits.w_var().unwrap() != 7 {
                assert!(script.first > 0 && script.first <= controller_end + 1);
                assert!(script.first + script.span > controller_end);
                controller_end = script.first + script.span;
                break verify_controller(&records, SOLDIER, true);
            }
        }
        client.send(ACK, Vec::new());
        assert!(
            Instant::now() < deadline,
            "waiting for hero selection to reopen"
        );
    };
    assert!(reopen_cmfd > first_cmfd);

    let next_hero = if attached {
        assets.heroes().find_hero("Reaper").unwrap().guid
    } else {
        assets.heroes().find_hero("Tracer").unwrap().guid
    };
    send(client, 3, PICK_HERO, next_hero.to_le_bytes().to_vec());
    let (swap, records) = next_body_frame(client, SECOND);
    let swap_script = records
        .iter()
        .find(|record| record.entity == OWNER && record.script.is_some())
        .and_then(|record| record.script.as_ref())
        .expect("hero replacement controller frame");
    assert!(swap_script.first > 0 && swap_script.first <= controller_end + 1);
    assert!(swap_script.first + swap_script.span > controller_end);
    assert_eq!(
        describe(&records),
        [
            (SECOND, Some(1), None),
            (OWNER, Some(2), None),
            (FIRST, Some(3), None),
            (OWNER, None, Some(swap_script.span))
        ],
    );
    assert!(verify_controller(&records, next_hero, false) > reopen_cmfd);
    assert!(client.window.accept(swap.seq));
    client.send(ACK, Vec::new());

    let skin = SkinTheme {
        guid: 0x0a50_0000_0000_1234,
        golden: true,
    };
    server.update_skin(ticket, next_hero, skin).unwrap();
    let third = OWNER | 3;
    let (reskin, records) = next_body_frame(client, third);
    assert_eq!(
        records
            .iter()
            .find(|record| record.entity == third)
            .unwrap()
            .skin,
        Some((0x1234, true))
    );
    assert!(
        records
            .iter()
            .any(|record| record.entity == SECOND && record.operation == Some(3))
    );
    assert!(client.window.accept(reskin.seq));
    client.send(ACK, Vec::new());
}
