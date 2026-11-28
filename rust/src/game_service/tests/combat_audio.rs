use super::{
    death_notice::{acknowledge_world_frame, pick_body},
    *,
};
use crate::{
    commands::Command,
    contact_world::ContactWorld,
    game_clock::TICK_PERIOD,
    game_combat::{PosedHitscan, ShotContext},
    game_heroes::{HeroCatalog, SOLDIER},
    game_hitscan::{DamageTags, HitDefinition, HurtVolumeCatalog, TargetHistory},
    game_replication::{BodyId, BodyStep},
    mover::Mover,
    mover_settings::MoverSettings,
    script_player::fixture_body_factory_with_nodes,
    vitals::Death,
};
use serde::Deserialize;
use serde_json::{Value as Json, json};

#[derive(Deserialize)]
struct Contract {
    input: Input,
    expected: Expected,
}

#[derive(Deserialize)]
struct Input {
    damage: f64,
    stimulus: u64,
    line: u64,
}

#[derive(Deserialize)]
struct Expected {
    shooter_notices: usize,
    victim_notices: usize,
    after_replacement: usize,
    reliable_sequence: u32,
}

const ALTERNATE_HERO: u64 = 0x02e0_0000_0052_0044;

enum CueTrigger {
    DamageReceived,
    CriticalDealt,
    EliminationDealt,
}

fn fixture_node(pos: usize, class: &str, fields: Json) -> Json {
    json!({
        "pos":pos,"uid":pos,"rid":pos,"remote":-1,"state":null,
        "class":class,"client_only":false,"server_only":true,"fields":fields,
    })
}

fn feedback_graph(stimulus: u64, line: u64, trigger: CueTrigger) -> Vec<Json> {
    let variable = |id| json!({"$": "STUConfigVarDynamic", "m_identifier": id});
    let plug = |index| json!({"$": "Plug", "links": [[index, "m_inPlug"]]});
    let empty = json!({"$": "Plug", "links": []});
    let (recipient_slot, condition_slot, condition) = match trigger {
        CueTrigger::DamageReceived => (
            2,
            3,
            json!({
                "$":"STUConfigVarExpression",
                "m_expression":{"m_opcodes":[8,0,6,0,34,0],"m_dynamicVars":[variable(2)],"m_D99EF254":[0]},
            }),
        ),
        CueTrigger::CriticalDealt => (1, 7, variable(2)),
        CueTrigger::EliminationDealt => (1, 4, variable(2)),
    };
    let binding = |slot, id| {
        json!({
            "m_B5051BCE":format!("0x{slot:016X}"),"m_out_Var":variable(id),
        })
    };
    let mut entry = fixture_node(
        0,
        "EntryHostEvent",
        json!({
            "m_params": [
                binding(recipient_slot, 1),
                binding(condition_slot, 2),
            ],
            "m_outPlug":plug(1),
        }),
    );
    entry["host_event_entry"] = json!("health_applied");
    let branch = |pos, condition| {
        let mut node = fixture_node(
            pos,
            "FixtureBranchAction",
            json!({
                "m_ADEB6E05":true,"m_condition":condition,
                "m_truePlug":plug(pos + 1),"m_falsePlug":empty,
            }),
        );
        node["action_behavior"] = json!("branch");
        node
    };
    let recipient = branch(
        1,
        json!({
            "$":"STUConfigVarExpression","m_configVars":[{"$":"STU_B5A0CAF0"}],
            "m_expression":{"m_opcodes":[8,0,14,0,37,0],"m_dynamicVars":[variable(1)]},
        }),
    );
    let damage = branch(2, condition);
    let mut cue = fixture_node(
        3,
        "FixtureCueAction",
        json!({
            "m_ADEB6E05":true,"m_outPlug":empty,
            "stimulus":format!("0x{stimulus:016X}"),
            "line":format!("0x{line:016X}"),
            "audience":{"$":"STUConfigVarExpression","m_expression":{
                "m_opcodes":[8,0,20,1,0],"m_dynamicVars":[variable(1)],
            }},
        }),
    );
    cue["action_behavior"] = json!("room_cue");
    vec![entry, recipient, damage, cue]
}

fn apply_and_advance(room: &mut Room, bodies: [BodyId; 2], tick: u32, damage: f64) {
    let [shooter, victim] = bodies;
    let simulation = room.world.as_mut().unwrap().scene.simulation_mut();
    let (_, outcome, _) = simulation
        .damage_player_body_by_wire(
            victim,
            damage,
            Death {
                time: (tick * TICK_PERIOD).as_secs_f64(),
                frame: tick,
                killer: shooter.entity,
            },
            None,
        )
        .unwrap();
    assert_eq!(outcome.dealt, damage);
    advance_feedback(room, bodies, tick);
}

fn advance_feedback(room: &mut Room, bodies: impl IntoIterator<Item = BodyId>, tick: u32) {
    room.finish_simulation(tick, tick * TICK_PERIOD).unwrap();
    let next_tick = tick + 1;
    let simulation = room.world.as_mut().unwrap().scene.simulation_mut();
    simulation.prepare_spatial_frame(next_tick).unwrap();
    for id in bodies {
        let actor = simulation.player_body_mut(id).unwrap();
        let runtime = actor.runtime.as_mut().unwrap();
        runtime
            .advance(
                Command {
                    frame: next_tick,
                    ..Command::default()
                },
                &actor.mover,
                BodyStep::UnposedTimer,
            )
            .unwrap();
        assert!(runtime.snapshot().unwrap().diagnostics.is_empty());
        actor.frame = next_tick;
    }
    room.finish_simulation(next_tick, next_tick * TICK_PERIOD)
        .unwrap();
}

fn feedback_room<const N: usize>(
    graph: Vec<Json>,
    teams: [u8; N],
) -> (Room, [BodyId; N], [u32; N]) {
    feedback_room_with_bots(graph, teams, Vec::new())
}

fn feedback_room_with_bots<const N: usize>(
    graph: Vec<Json>,
    teams: [u8; N],
    bots: Vec<crate::game_bots::Bot>,
) -> (Room, [BodyId; N], [u32; N]) {
    let hero_entry =
        json!({"name":"Synthetic cue actor","class":"Damage","body":"0x0400000000000001"});
    let heroes = HeroCatalog::from_json(
        &json!({
            format!("0x{SOLDIER:016X}"):hero_entry,
            format!("0x{ALTERNATE_HERO:016X}"):hero_entry,
        })
        .to_string(),
    )
    .unwrap();
    let (factory, _) = fixture_body_factory_with_nodes(graph);
    let mut scene = PreparedScene::fixture_for_owner_admission_with_bots(heroes, bots);
    scene.fixture_enable_room_health_scripts();
    scene.owner_body_factory = Some(Arc::new(move |_, spawn, setup| {
        Ok(Some(Box::new(factory.start_with_setup(spawn, setup)?)))
    }));
    let card = PlayerCard::default();
    let mut sequence = [0; N];
    let peers: [_; N] = std::array::from_fn(|slot| {
        let owner = player_entity(slot);
        let viewer = PlayerAppearance {
            entity: owner,
            slot: i8::try_from(slot).unwrap(),
            team: teams[slot],
            card: &card,
        };
        let mut peer = Peer::new(owner);
        peer.world.start(&scene, &viewer, FIRST_TICK).unwrap();
        while !peer.world.identity_ready(owner) || !peer.world.identity_ready(GAME_MODE_ENTITY) {
            assert!(sequence[slot] < 8);
            acknowledge_world_frame(&mut peer, &mut sequence[slot]);
        }
        peer.world.present(&scene, FIRST_TICK).unwrap();
        let id = pick_body(&mut peer, &mut scene, &viewer, SOLDIER);
        (id, peer)
    });
    let bodies = peers.each_ref().map(|(body, _)| *body);
    let room = Room {
        map: scene.map().clone(),
        players: teams
            .into_iter()
            .enumerate()
            .map(|(slot, team)| {
                let mut info = player().pop().unwrap();
                info.team = team;
                Player {
                    info,
                    entity: player_entity(slot),
                    room_slot: None,
                    gone: false,
                }
            })
            .collect(),
        members: peers
            .into_iter()
            .enumerate()
            .map(|(slot, (_, peer))| {
                (
                    u32::try_from(slot + 1).unwrap(),
                    RoomMember {
                        slot,
                        peer: Some(peer),
                    },
                )
            })
            .collect(),
        world: Some(RoomWorld {
            scene: Box::new(scene),
            cards: (0..teams.len()).map(|_| card.clone()).collect(),
            patrol: PatrolScript::load().unwrap(),
            patrol_generation: 0,
        }),
        debug_control: room::RoomDebugControl::new(FIRST_TICK, Duration::ZERO),
        injection: None,
    };
    (room, bodies, sequence)
}

fn voice_schema() -> Schemas {
    Schemas::from_json(
        r#"{"82A2F991":{"27500":[
            {"type":14,"off":120,"count":0,"array":false,"fields":[
                {"type":6,"off":0,"count":0,"array":false}
            ]},
            {"type":14,"off":124,"count":0,"array":false,"fields":[
                {"type":6,"off":0,"count":0,"array":false}
            ]},
            {"type":8,"off":144,"count":0,"array":false}
        ]}}"#,
    )
    .unwrap()
}

#[test]
fn received_damage_audio_follows_only_the_current_victim_connection() {
    let contract: Contract = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/received-damage-audio-v1.json",
    ))
    .unwrap();
    let graph = feedback_graph(
        contract.input.stimulus,
        contract.input.line,
        CueTrigger::DamageReceived,
    );
    let (mut room, [shooter, victim], mut sequence) = feedback_room(graph, [0, 0]);
    let schemas = voice_schema();
    let now = FIRST_TICK * TICK_PERIOD;
    apply_and_advance(
        &mut room,
        [shooter, victim],
        FIRST_TICK + 1,
        contract.input.damage,
    );
    assert_eq!(
        room.members[&1]
            .peer
            .as_ref()
            .unwrap()
            .pending_combat_notices()
            .count(),
        contract.expected.shooter_notices
    );

    let world = room.world.as_mut().unwrap();
    let peer = room.members.get_mut(&2).unwrap().peer.as_mut().unwrap();
    assert_eq!(
        peer.pending_combat_notices().count(),
        contract.expected.victim_notices
    );
    peer.flush_combat(&schemas, now, Some(world.scene.simulation()))
        .unwrap();
    assert!(
        !peer.egress.has_queued_reliable(),
        "old body identity has not been acknowledged"
    );
    let viewer = PlayerAppearance {
        entity: player_entity(1),
        slot: 1,
        team: 0,
        card: &world.cards[0],
    };
    let replacement = pick_body(peer, &mut world.scene, &viewer, ALTERNATE_HERO);
    assert_ne!(victim, replacement);
    peer.flush_combat(&schemas, now, Some(world.scene.simulation()))
        .unwrap();
    assert_eq!(
        peer.pending_combat_notices().count(),
        contract.expected.after_replacement
    );
    assert!(!peer.egress.has_queued_reliable());

    apply_and_advance(
        &mut room,
        [shooter, replacement],
        FIRST_TICK + 4,
        contract.input.damage,
    );
    let peer = room.members[&2].peer.as_ref().unwrap();
    assert_eq!(
        peer.pending_combat_notices().copied().collect::<Vec<_>>(),
        [CombatNotice::Voice {
            anchor: replacement.entity,
            speaker: replacement.entity,
            listener: replacement.entity,
            stimulus: contract.input.stimulus,
            line: contract.input.line,
        },]
    );
    assert_reliable_cue(
        &mut room,
        2,
        replacement,
        &mut sequence[1],
        FIRST_TICK + 5,
        contract.input.line,
        contract.expected.reliable_sequence,
    );
}

#[test]
fn native_received_damage_is_delivered_only_to_the_body_that_lost_health() {
    let contract: Json = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/received-damage-feedback-v1.json"
    ))
    .unwrap();
    let damage = contract["input"]["damage"].as_f64().unwrap();
    let schemas = Schemas::from_json(&contract["jam_schema"].to_string()).unwrap();
    let ray_distance = contract["input"]["ray_distance"].as_f64().unwrap();
    let normal =
        serde_json::from_value(contract["expected"]["direct_hit_outward_normal"].clone()).unwrap();
    let source_position =
        serde_json::from_value(contract["input"]["script_source_position"].clone()).unwrap();
    assert_native_received_damage(
        &schemas,
        damage,
        Some((ray_distance, normal)),
        source_position,
    );
    assert_native_received_damage(&schemas, damage, None, source_position);
}

#[test]
#[ignore = "requires the separately prepared 104319 game schemas"]
fn prepared_native_received_damage_uses_the_104319_schema() {
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let schemas = Schemas::load(runtime.join("data/game_schemas_174.json")).unwrap();
    let source_position = [-6.25, 0.5, 8.75];
    assert_native_received_damage(
        &schemas,
        9.0,
        Some((4.0, [-1.0, 0.0, 0.0])),
        source_position,
    );
    assert_native_received_damage(&schemas, 9.0, None, source_position);
}

fn assert_native_received_damage(
    schemas: &Schemas,
    damage: f64,
    ray: Option<(f64, [f64; 3])>,
    source_position: [f64; 3],
) {
    let graph = feedback_graph(109, 207, CueTrigger::EliminationDealt);
    let (mut room, [source, victim], mut sequence) = feedback_room(graph, [0, 1]);
    let source_create = room
        .world
        .as_mut()
        .unwrap()
        .scene
        .simulation_mut()
        .take_player_events()
        .into_iter()
        .find_map(|event| match event {
            crate::game_combat::PlayerBodyEvent::Created { id, update, .. } if id == source => {
                Some(*update)
            }
            _ => None,
        })
        .expect("source observer create");
    room.world
        .as_mut()
        .unwrap()
        .scene
        .simulation_mut()
        .player_body_mut(source)
        .unwrap()
        .mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        source_position,
        0,
        0x18,
    )
    .unwrap();
    let tick = FIRST_TICK + 1;
    apply_and_advance(&mut room, [source, victim], tick, damage);
    for member in room.members.values() {
        assert_eq!(
            member
                .peer
                .as_ref()
                .unwrap()
                .pending_combat_notices()
                .count(),
            0
        );
    }

    room.world
        .as_mut()
        .unwrap()
        .scene
        .simulation_mut()
        .enable_received_damage_feedback();
    apply_and_advance(&mut room, [source, victim], tick + 2, 0.0);
    assert_eq!(
        room.members[&2]
            .peer
            .as_ref()
            .unwrap()
            .pending_combat_notices()
            .count(),
        0
    );
    let hit_tick = tick + 4;
    let origin = if let Some((distance, _)) = ray {
        let scene = &mut room.world.as_mut().unwrap().scene;
        let [x, y, z] = scene
            .simulation()
            .player_body(victim)
            .unwrap()
            .mover
            .pose()
            .position;
        let origin = [x - distance, y + 1.0, z];
        let (simulation, queries) = scene.bot_simulation();
        simulation.activate_combat();
        simulation
            .hitscan(
                &queries,
                PosedHitscan {
                    source: source.entity,
                    origin,
                    direction: [1.0, 0.0, 0.0],
                    command: Command {
                        frame: hit_tick,
                        ..Command::default()
                    },
                    shot_frame: hit_tick,
                    reach: 12.0,
                    hit: HitDefinition {
                        tags: DamageTags::EMPTY,
                        amount: damage,
                        loadout: None,
                        critical: 1.0,
                        falloff: None,
                    },
                },
                ShotContext {
                    team: 0,
                    quanta: RETAIL_QUANTA,
                    tick: hit_tick,
                    now: hit_tick * TICK_PERIOD,
                },
                |entity| entity == victim.entity,
            )
            .unwrap()
            .expect("player hit");
        advance_feedback(&mut room, [source, victim], hit_tick);
        Some(origin)
    } else {
        apply_and_advance(&mut room, [source, victim], hit_tick, damage);
        Some(source_position)
    };
    assert!(
        room.members[&1]
            .peer
            .as_ref()
            .unwrap()
            .pending_combat_notices()
            .all(|notice| !matches!(notice, CombatNotice::DamageReceived { .. }))
    );
    let normal = ray.map(|(_, normal)| normal);
    let simulation = room.world.as_ref().unwrap().scene.simulation();
    let peer = room.members.get_mut(&2).unwrap().peer.as_mut().unwrap();
    let notices: Vec<_> = peer.pending_combat_notices().copied().collect();
    let [
        CombatNotice::DamageReceived {
            source: actual_source,
            victim: actual_victim,
            dealt: actual_dealt,
            origin: actual_origin,
            impact_point,
            normal: actual_normal,
            contact_set: actual_contact_set,
            critical: false,
            armor_or_shield: false,
            special_vfx: false,
        },
    ] = notices.as_slice()
    else {
        panic!("unexpected pending damage notices: {notices:?}");
    };
    assert_eq!(*actual_source, source.entity);
    assert_eq!(*actual_victim, victim.entity);
    assert_eq!(*actual_dealt, damage as f32);
    assert_eq!(*actual_origin, origin);
    let expected_impact = impact_point.expect("victim impact point");
    assert_eq!(*actual_normal, normal);
    assert_eq!(*actual_contact_set, 0);
    peer.flush_combat(schemas, (tick + 4) * TICK_PERIOD, Some(simulation))
        .unwrap();
    assert!(!peer.egress.has_queued_reliable());
    peer.world.create_remote_player(source, source_create);
    while !peer.world.identity_ready(source.entity) || !peer.world.identity_ready(victim.entity) {
        assert!(sequence[1] < 16);
        acknowledge_world_frame(peer, &mut sequence[1]);
    }
    peer.flush_combat(schemas, (tick + 5) * TICK_PERIOD, Some(simulation))
        .unwrap();
    peer.world
        .send_frame(
            tick + 5,
            Vec::new(),
            false,
            None,
            &mut peer.egress,
            |payload| {
                let mut reader = BitReader::new(&payload);
                reader.var_a().unwrap();
                let channel = read_channel(&mut reader, schemas).unwrap();
                let [event] = channel.reliable.as_slice() else {
                    panic!("expected one victim feedback event");
                };
                assert_eq!(event.message.id, 25000);
                assert_eq!(event.message.fields[&0x78], Value::Signed(1));
                for (offset, xyz) in [
                    (0x84, normal.unwrap_or_default()),
                    (0x90, expected_impact),
                    (0x9c, origin.unwrap_or_default()),
                ] {
                    assert_eq!(
                        event.message.fields[&offset],
                        Value::Struct(Fields::from_iter(
                            [0, 4, 8]
                                .into_iter()
                                .zip(xyz.map(|v| Value::Float32(v as f32)))
                        ))
                    );
                }
                assert_eq!(
                    event.message.fields[&0xa8],
                    Value::Array(vec![Value::Unsigned(0); 3])
                );
                assert_eq!(event.message.fields[&0xc0], Value::Float32(damage as f32));
                assert_eq!(
                    event.message.fields[&0xd8],
                    Value::Array(vec![Value::Float32(damage as f32), Value::Float32(1.0)])
                );
                for (offset, body) in [(0x7c, source), (0x80, victim)] {
                    assert_eq!(
                        event.message.fields[&offset],
                        Value::Struct(Fields::from(
                            [(0, Value::Unsigned(u64::from(body.entity))),]
                        ))
                    );
                }
                Ok::<_, io::Error>(sequence[1] + 1)
            },
        )
        .unwrap();
}

fn assert_reliable_cue(
    room: &mut Room,
    connection: u32,
    listener: BodyId,
    sequence: &mut u32,
    tick: u32,
    line: u64,
    reliable_sequence: u32,
) {
    assert_reliable_voice_cue(
        room,
        connection,
        listener.entity,
        listener,
        sequence,
        tick,
        line,
        reliable_sequence,
    );
}

#[allow(clippy::too_many_arguments)]
fn assert_reliable_voice_cue(
    room: &mut Room,
    connection: u32,
    speaker: u32,
    listener: BodyId,
    sequence: &mut u32,
    tick: u32,
    line: u64,
    reliable_sequence: u32,
) {
    let schemas = voice_schema();
    let simulation = room.world.as_ref().unwrap().scene.simulation();
    let peer = room
        .members
        .get_mut(&connection)
        .unwrap()
        .peer
        .as_mut()
        .unwrap();
    while !peer.world.identity_ready(listener.entity)
        || !peer.world.identity_ready(GAME_MODE_ENTITY)
    {
        assert!(*sequence < 16);
        acknowledge_world_frame(peer, sequence);
    }
    peer.flush_combat(&schemas, tick * TICK_PERIOD, Some(simulation))
        .unwrap();
    assert_eq!(peer.pending_combat_notices().count(), 0);
    *sequence += 1;
    send_voice_frame(
        peer,
        &schemas,
        tick,
        *sequence,
        VoiceFrame {
            speaker,
            listener: listener.entity,
            line,
            ordinal: reliable_sequence,
        },
    );
    let report = peer.acknowledge(*sequence + 1, 1);
    peer.world.acknowledge(report, tick * TICK_PERIOD);
}

struct VoiceFrame {
    speaker: u32,
    listener: u32,
    line: u64,
    ordinal: u32,
}

fn send_voice_frame(
    peer: &mut Peer,
    schemas: &Schemas,
    tick: u32,
    packet_sequence: u32,
    expected: VoiceFrame,
) {
    peer.world
        .send_frame(tick, Vec::new(), false, None, &mut peer.egress, |payload| {
            let mut reader = BitReader::new(&payload);
            reader.var_a().unwrap();
            let channel = read_channel(&mut reader, schemas).unwrap();
            let [voice] = channel.reliable.as_slice() else {
                panic!("expected one cue, got {:?}", channel.reliable);
            };
            assert_eq!(voice.message.id, 27500);
            assert_eq!(voice.sequence, expected.ordinal);
            assert_eq!(voice.message.fields[&0x90], Value::Unsigned(expected.line));
            for (field, wire) in [(0x78, expected.speaker), (0x7c, expected.listener)] {
                assert_eq!(
                    voice.message.fields[&field],
                    Value::Struct(Fields::from([(0, Value::Unsigned(u64::from(wire)))]))
                );
            }
            Ok::<_, io::Error>(packet_sequence)
        })
        .unwrap();
}

fn elimination_cue_graph(stimulus: u64, cues: [EliminationCue; 2]) -> Vec<Json> {
    let mut graph = feedback_graph(stimulus, cues[0].line, CueTrigger::EliminationDealt);
    let template = graph.pop().unwrap();
    let count = json!({"$":"STUConfigVarDynamic","m_identifier":3});
    let mut cases = Vec::new();
    for (offset, cue) in cues.into_iter().enumerate() {
        let position = 5 + offset;
        let mut node = template.clone();
        for key in ["pos", "uid", "rid"] {
            node[key] = json!(position);
        }
        node["fields"]["speaker"] = json!("mode");
        node["fields"]["line"] = json!(format!("0x{:016X}", cue.line));
        graph.push(node);
        cases.push(json!({"$":"Plug","links":[[position,"m_inPlug"]],"m_value":cue.count}));
    }
    graph.insert(
        3,
        fixture_node(
            3,
            "STUStatescriptActionSetVar",
            json!({
                "m_out_Var": count,
                "m_value": {"$":"STUConfigVarExpression","m_expression":{
                    "m_opcodes":[8,0,6,0,23,0],"m_dynamicVars":[count],"m_D99EF254":[1]
                }},
                "m_outPlug": {"$":"Plug","links":[[4,"m_inPlug"]]}
            }),
        ),
    );
    graph.insert(
        4,
        fixture_node(
            4,
            "STUStatescriptActionSwitch",
            json!({"m_262AE982": count, "m_FDD34C25": cases}),
        ),
    );
    graph
}

#[derive(Deserialize)]
struct EliminationCue {
    count: u32,
    line: u64,
}

#[test]
fn settled_elimination_cues_keep_their_order_through_reliable_retransmission() {
    #[derive(Deserialize)]
    struct RetryContract {
        stimulus: u64,
        cues: [EliminationCue; 2],
        hits: Vec<Settlement>,
        sends: Vec<RetrySend>,
    }
    #[derive(Deserialize)]
    struct Settlement {
        target: usize,
        frame: u32,
        damage: f64,
        killed: bool,
    }
    #[derive(Deserialize)]
    struct RetrySend {
        line: u64,
        ordinal: u32,
        arrived: bool,
        waiting: usize,
    }
    let contract: RetryContract = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/mode-cue-retry-v1.json",
    ))
    .unwrap();
    let lines = contract.cues.each_ref().map(|cue| cue.line);
    let graph = elimination_cue_graph(contract.stimulus, contract.cues);
    let (mut room, bodies, mut sequences) = feedback_room(graph, [0, 1, 1, 1]);
    let shooter = bodies[0];
    let last_frame = contract.hits.last().unwrap().frame;
    for group in contract
        .hits
        .chunk_by(|left, right| left.frame == right.frame)
    {
        let frame = group[0].frame;
        let tick = FIRST_TICK + frame;
        let simulation = room.world.as_mut().unwrap().scene.simulation_mut();
        for hit in group {
            let (_, outcome, _) = simulation
                .damage_player_body_by_wire(
                    bodies[hit.target],
                    hit.damage,
                    Death {
                        frame: tick,
                        time: (tick * TICK_PERIOD).as_secs_f64(),
                        killer: shooter.entity,
                    },
                    None,
                )
                .unwrap();
            assert_eq!(outcome.killed, hit.killed);
        }
        advance_feedback(&mut room, [shooter], tick);
        if frame < last_frame {
            assert_eq!(
                room.members[&1]
                    .peer
                    .as_ref()
                    .unwrap()
                    .pending_combat_notices()
                    .count(),
                0
            );
        }
    }
    for connection in 2..=4 {
        assert_eq!(
            room.members[&connection]
                .peer
                .as_ref()
                .unwrap()
                .pending_combat_notices()
                .count(),
            0
        );
    }
    let simulation = room.world.as_ref().unwrap().scene.simulation();
    let peer = room.members.get_mut(&1).unwrap().peer.as_mut().unwrap();
    assert_eq!(
        peer.pending_combat_notices()
            .filter_map(|notice| match notice {
                CombatNotice::Voice { line, .. } => Some(*line),
                _ => None,
            })
            .collect::<Vec<_>>(),
        lines,
    );
    let sequence = &mut sequences[0];
    while !peer.world.identity_ready(shooter.entity) {
        assert!(*sequence < 16);
        acknowledge_world_frame(peer, sequence);
    }
    let schemas = voice_schema();
    let mut tick = FIRST_TICK + last_frame + 1;
    for step in contract.sends {
        let now = tick * TICK_PERIOD;
        peer.flush_combat(&schemas, now, Some(simulation)).unwrap();
        assert_eq!(peer.pending_combat_notices().count(), step.waiting);
        *sequence += 1;
        send_voice_frame(
            peer,
            &schemas,
            tick,
            *sequence,
            VoiceFrame {
                speaker: GAME_MODE_ENTITY,
                listener: shooter.entity,
                line: step.line,
                ordinal: step.ordinal,
            },
        );
        let ack_bits = u64::from(step.arrived);
        let report = peer.acknowledge(*sequence + 1, ack_bits);
        assert_eq!(report.requeued_reliable, usize::from(!step.arrived));
        peer.world.acknowledge(report, now);
        let duplicate = peer.acknowledge(*sequence + 1, ack_bits);
        assert_eq!(duplicate.requeued_reliable, 0);
        assert_eq!(peer.egress.delivered_through(step.ordinal), step.arrived);
        tick += 1;
    }
    peer.flush_combat(&schemas, tick * TICK_PERIOD, Some(simulation))
        .unwrap();
    assert_eq!(peer.pending_combat_notices().count(), 0);
    assert!(!peer.egress.has_queued_reliable());
}

#[test]
fn confirmed_headshot_cue_reaches_only_the_shooter_connection() {
    #[derive(Deserialize)]
    struct Headshots {
        stimulus: u64,
        line: u64,
        volumes: Json,
        rays: Vec<Ray>,
    }
    #[derive(Deserialize)]
    struct Ray {
        name: String,
        height: f64,
        multiplier: f64,
        confirmed: Option<bool>,
    }
    let contract: Headshots = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/confirmed-headshot-audio-v1.json",
    ))
    .unwrap();
    let graph = feedback_graph(contract.stimulus, contract.line, CueTrigger::CriticalDealt);
    let (mut room, [shooter, victim], mut sequence) = feedback_room(graph, [0, 1]);
    let scene = &mut room.world.as_mut().unwrap().scene;
    scene.fixture_set_hurt_volumes(
        HurtVolumeCatalog::from_json(&contract.volumes.to_string()).unwrap(),
    );
    let victim_state = scene.simulation_mut().player_body_mut(victim).unwrap();
    victim_state.mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        [6.0, 0.0, 2.0],
        0,
        8,
    )
    .unwrap();
    victim_state.history = TargetHistory::new(None);

    let mut tick = FIRST_TICK + 1;
    for ray in contract.rays {
        let scene = &mut room.world.as_mut().unwrap().scene;
        let (simulation, queries) = scene.bot_simulation();
        let result = simulation
            .hitscan(
                &queries,
                PosedHitscan {
                    source: shooter.entity,
                    origin: [0.0, ray.height, 2.0],
                    direction: [1.0, 0.0, 0.0],
                    command: Command {
                        frame: tick,
                        ..Command::default()
                    },
                    shot_frame: tick,
                    reach: 12.0,
                    hit: HitDefinition {
                        tags: DamageTags::EMPTY,
                        amount: 5.0,
                        loadout: None,
                        critical: ray.multiplier,
                        falloff: None,
                    },
                },
                ShotContext {
                    team: 0,
                    quanta: RETAIL_QUANTA,
                    tick,
                    now: tick * TICK_PERIOD,
                },
                |entity| entity == victim.entity,
            )
            .unwrap();
        let confirmed = result.as_ref().map(|hit| match hit.notices[0] {
            Some(CombatNotice::Hit { critical, .. }) => critical,
            _ => panic!("missing hit confirmation"),
        });
        assert_eq!(confirmed, ray.confirmed, "{}", ray.name);
        advance_feedback(&mut room, [shooter, victim], tick);
        let shooter_peer = room.members[&1].peer.as_ref().unwrap();
        assert_eq!(
            shooter_peer.pending_combat_notices().count(),
            usize::from(confirmed == Some(true)),
            "{}",
            ray.name
        );
        assert_eq!(
            room.members[&2]
                .peer
                .as_ref()
                .unwrap()
                .pending_combat_notices()
                .count(),
            0,
            "{}",
            ray.name
        );
        if confirmed == Some(true) {
            assert_reliable_cue(
                &mut room,
                1,
                shooter,
                &mut sequence[0],
                tick + 1,
                contract.line,
                0,
            );
        }
        tick += 2;
    }
}

#[test]
fn multikill_announcer_cues_emit_from_mode_entity_across_bot_eliminations() {
    use crate::combat_voice::CombatVoiceCatalog;

    let voice = CombatVoiceCatalog::from_json(
        r#"{
            "multikill_window_ticks": 3,
            "multikills": {
                "2": { "stimulus": "0x0EE0000000000102", "lines": ["0x0760000000000202"] },
                "3": { "stimulus": "0x0EE0000000000103", "lines": ["0x0760000000000203"] }
            }
        }"#,
    )
    .unwrap();
    assert_multikill_announcer_cues(voice);
}

#[test]
#[ignore = "requires a prepared combat voice catalog using the authored two-second interval"]
fn prepared_multikill_announcer_restarts_the_two_second_interval() {
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let voice =
        crate::combat_voice::CombatVoiceCatalog::load(runtime.join("data/combat_voice_174.json"))
            .unwrap();
    assert_eq!(
        voice.multikill_window_ticks() * TICK_PERIOD,
        Duration::from_secs(2)
    );
    assert_multikill_announcer_cues(voice);
}

fn assert_multikill_announcer_cues(voice: crate::combat_voice::CombatVoiceCatalog) {
    use crate::game_bots::{BotCatalog, BotWalk};

    let window = voice.multikill_window_ticks();
    assert!(window > 2);
    let (_, double) = voice.multikill_cue(2, 2).unwrap();
    let (_, triple) = voice.multikill_cue(3, 3).unwrap();
    let bots = BotCatalog::from_json(
        r#"{
            "heroes": {"0x01": {"name": "Target", "body": "0x02", "health": 50}},
            "maps": {"0x03": {"hints": {}, "spawners": [
                {"hero": "0x01", "team": 1, "position": [6, 0, 0], "rotation": [0, 0, 0, 1], "graphs": []},
                {"hero": "0x01", "team": 1, "position": [6, 0, 4], "rotation": [0, 0, 0, 1], "graphs": []},
                {"hero": "0x01", "team": 1, "position": [6, 0, 8], "rotation": [0, 0, 0, 1], "graphs": []},
                {"hero": "0x01", "team": 1, "position": [6, 0, 12], "rotation": [0, 0, 0, 1], "graphs": []},
                {"hero": "0x01", "team": 1, "position": [6, 0, 16], "rotation": [0, 0, 0, 1], "graphs": []},
                {"hero": "0x01", "team": 0, "position": [6, 0, 20], "rotation": [0, 0, 0, 1], "graphs": []}
            ]}}
        }"#,
    )
    .unwrap()
    .make(3, |_| BotWalk::default());
    let targets: Vec<_> = bots
        .iter()
        .map(|bot| (bot.entity, bot.spawn.position))
        .collect();
    let (mut room, [shooter], mut sequence) = feedback_room_with_bots(Vec::new(), [0], bots);
    room.world
        .as_mut()
        .unwrap()
        .scene
        .fixture_set_combat_voice(voice);

    let kill_bot = |room: &mut Room, target: u32, pos: [f64; 3], tick: u32| {
        let scene = &mut room.world.as_mut().unwrap().scene;
        let (simulation, queries) = scene.bot_simulation();
        simulation.activate_combat();
        let hit = simulation
            .hitscan(
                &queries,
                PosedHitscan {
                    source: shooter.entity,
                    origin: [0.0, pos[1] + 1.0, pos[2]],
                    direction: [1.0, 0.0, 0.0],
                    command: Command {
                        frame: tick,
                        ..Command::default()
                    },
                    shot_frame: tick,
                    reach: 12.0,
                    hit: HitDefinition {
                        tags: DamageTags::EMPTY,
                        amount: 100.0,
                        loadout: None,
                        critical: 1.0,
                        falloff: None,
                    },
                },
                ShotContext {
                    team: 0,
                    quanta: RETAIL_QUANTA,
                    tick,
                    now: tick * TICK_PERIOD,
                },
                |entity| entity == target,
            )
            .unwrap()
            .expect("bot lethal hit");
        assert!(matches!(
            hit.notices[0],
            Some(CombatNotice::Hit { killed: true, .. })
        ));
        advance_feedback(room, [shooter], tick);
    };

    let first = FIRST_TICK + 1;
    let second = first + window - 1;
    let third = second + window - 1;
    let expired = third + window + 1;
    let next = expired + window - 1;
    kill_bot(&mut room, targets[0].0, targets[0].1, first);
    assert_eq!(
        room.members[&1]
            .peer
            .as_ref()
            .unwrap()
            .pending_combat_notices()
            .count(),
        0
    );

    kill_bot(&mut room, targets[1].0, targets[1].1, second);
    assert_reliable_voice_cue(
        &mut room,
        1,
        GAME_MODE_ENTITY,
        shooter,
        &mut sequence[0],
        second + 1,
        double,
        0,
    );

    kill_bot(&mut room, targets[2].0, targets[2].1, third);
    assert_reliable_voice_cue(
        &mut room,
        1,
        GAME_MODE_ENTITY,
        shooter,
        &mut sequence[0],
        third + 1,
        triple,
        1,
    );

    // Same-team scripted elimination does not extend the multikill streak.
    room.world
        .as_mut()
        .unwrap()
        .scene
        .simulation_mut()
        .kill_bot_for_test(targets[5].0, third + 1, (third + 1) * TICK_PERIOD);
    assert_eq!(
        room.members[&1]
            .peer
            .as_ref()
            .unwrap()
            .pending_combat_notices()
            .count(),
        0
    );

    // Expiring the multikill window resets the streak back to 1 (no announcement).
    kill_bot(&mut room, targets[3].0, targets[3].1, expired);
    assert_eq!(
        room.members[&1]
            .peer
            .as_ref()
            .unwrap()
            .pending_combat_notices()
            .count(),
        0
    );

    kill_bot(&mut room, targets[4].0, targets[4].1, next);
    assert_reliable_voice_cue(
        &mut room,
        1,
        GAME_MODE_ENTITY,
        shooter,
        &mut sequence[0],
        next + 1,
        double,
        2,
    );
}

#[test]
fn bot_hurt_and_death_voice_cues_honor_cooldown_and_bypass_on_elimination() {
    use crate::{
        combat_voice::CombatVoiceCatalog,
        game_bots::{BotCatalog, BotWalk},
    };

    let bots = BotCatalog::from_json(
        r#"{
            "heroes": {"0x01": {"name": "Target", "body": "0x0400000000000002", "health": 120}},
            "maps": {"0x03": {"hints": {}, "spawners": [
                {"hero": "0x01", "team": 1, "position": [6, 0, 0], "rotation": [0, 0, 0, 1], "graphs": []}
            ]}}
        }"#,
    )
    .unwrap()
    .make(3, |_| BotWalk::default());
    let target = bots[0].entity;
    let target_pos = bots[0].spawn.position;
    let (mut room, [shooter], mut sequence) = feedback_room_with_bots(Vec::new(), [0], bots);
    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/combat-hurt-voice-v1.json",
    ))
    .unwrap();
    let voice = CombatVoiceCatalog::from_json(&contract["input"]["catalog"].to_string()).unwrap();
    assert_eq!(
        voice.hurt_cue(Some(0x0400_0000_0000_0003), true, false, false, 0),
        None
    );
    let shot_frame = |event: &str| {
        FIRST_TICK
            + u32::try_from(contract["input"]["frame_offsets"][event].as_u64().unwrap()).unwrap()
    };
    let scene = room.world.as_mut().unwrap().scene.as_mut();
    scene.fixture_set_combat_voice(voice);
    scene.fixture_set_hurt_volumes(
        HurtVolumeCatalog::from_json(
            r#"{"version": 1, "bodies": {"0x0400000000000002": {"body": {"lower": 0.4, "upper": 1.1, "radius": 0.24}, "head": {"lower": 1.7, "upper": 1.7, "radius": 0.2}}}}"#,
        )
        .unwrap(),
    );

    let shoot_bot = |room: &mut Room, height: f64, amount: f64, critical: f64, tick: u32| {
        let scene = &mut room.world.as_mut().unwrap().scene;
        let (simulation, queries) = scene.bot_simulation();
        simulation.activate_combat();
        simulation
            .hitscan(
                &queries,
                PosedHitscan {
                    source: shooter.entity,
                    origin: [0.0, target_pos[1] + height, target_pos[2]],
                    direction: [1.0, 0.0, 0.0],
                    command: Command {
                        frame: tick,
                        ..Command::default()
                    },
                    shot_frame: tick,
                    reach: 12.0,
                    hit: HitDefinition {
                        tags: DamageTags::EMPTY,
                        amount,
                        loadout: None,
                        critical,
                        falloff: None,
                    },
                },
                ShotContext {
                    team: 0,
                    quanta: RETAIL_QUANTA,
                    tick,
                    now: tick * TICK_PERIOD,
                },
                |entity| entity == target,
            )
            .unwrap()
            .expect("bot hit");
    };

    // Non-lethal body hit emits minor hurt cue.
    shoot_bot(&mut room, 0.8, 20.0, 1.0, shot_frame("first"));
    advance_feedback(&mut room, [shooter], shot_frame("first"));
    assert_reliable_voice_cue(
        &mut room,
        1,
        target,
        shooter,
        &mut sequence[0],
        FIRST_TICK + 2,
        contract["expected"]["first_line"].as_u64().unwrap(),
        0,
    );

    // An older damage frame must not bypass or rewind the cooldown.
    shoot_bot(&mut room, 0.8, 20.0, 1.0, shot_frame("late"));

    // Follow-up hit within cooldown (4 ticks) is suppressed.
    shoot_bot(&mut room, 0.8, 20.0, 1.0, shot_frame("follow_up"));
    advance_feedback(&mut room, [shooter], shot_frame("follow_up"));
    assert_eq!(
        room.members[&1]
            .peer
            .as_ref()
            .unwrap()
            .pending_combat_notices()
            .count(),
        contract["expected"]["late_and_follow_up_cues"]
            .as_u64()
            .unwrap() as usize
    );

    // Critical head hit once cooldown expires emits severe hurt cue.
    shoot_bot(&mut room, 1.7, 20.0, 2.0, shot_frame("critical"));
    advance_feedback(&mut room, [shooter], shot_frame("critical"));
    assert_reliable_voice_cue(
        &mut room,
        1,
        target,
        shooter,
        &mut sequence[0],
        FIRST_TICK + 6,
        contract["expected"]["critical_line"].as_u64().unwrap(),
        1,
    );

    // Lethal hit inside cooldown bypasses cooldown and emits bot_death cue.
    shoot_bot(&mut room, 0.8, 80.0, 1.0, shot_frame("lethal"));
    advance_feedback(&mut room, [shooter], shot_frame("lethal"));
    assert_reliable_voice_cue(
        &mut room,
        1,
        target,
        shooter,
        &mut sequence[0],
        FIRST_TICK + 7,
        contract["expected"]["lethal_line"].as_u64().unwrap(),
        2,
    );
}

#[test]
fn player_hurt_voice_cue_emits_from_victim_on_hit() {
    use crate::combat_voice::CombatVoiceCatalog;

    let (mut room, [shooter, victim], mut sequence) = feedback_room(Vec::new(), [0, 1]);
    let voice = CombatVoiceCatalog::from_json(
        r#"{
            "hurt_cooldown_ticks": 4,
            "hurt": {
                "minor": { "stimulus": "0x0EE0000000000003", "lines": ["0x0760000000000006"] },
                "severe": { "stimulus": "0x0EE0000000000004", "lines": ["0x076000000000000B"] },
                "death": { "stimulus": "0x0EE0000000000001", "lines": ["0x076000000000001C"] }
            }
        }"#,
    )
    .unwrap();
    room.world
        .as_mut()
        .unwrap()
        .scene
        .fixture_set_combat_voice(voice);

    let tick = FIRST_TICK + 1;
    let scene = &mut room.world.as_mut().unwrap().scene;
    let victim_pos = scene
        .simulation()
        .player_body(victim)
        .unwrap()
        .mover
        .pose()
        .position;
    let (simulation, queries) = scene.bot_simulation();
    simulation.activate_combat();
    simulation
        .hitscan(
            &queries,
            PosedHitscan {
                source: shooter.entity,
                origin: [victim_pos[0] - 4.0, victim_pos[1] + 1.0, victim_pos[2]],
                direction: [1.0, 0.0, 0.0],
                command: Command {
                    frame: tick,
                    ..Command::default()
                },
                shot_frame: tick,
                reach: 12.0,
                hit: HitDefinition {
                    tags: DamageTags::EMPTY,
                    amount: 25.0,
                    loadout: None,
                    critical: 1.0,
                    falloff: None,
                },
            },
            ShotContext {
                team: 0,
                quanta: RETAIL_QUANTA,
                tick,
                now: tick * TICK_PERIOD,
            },
            |entity| entity == victim.entity,
        )
        .unwrap()
        .expect("player hit");
    advance_feedback(&mut room, [shooter, victim], tick);

    assert_reliable_voice_cue(
        &mut room,
        2,
        victim.entity,
        victim,
        &mut sequence[1],
        tick + 1,
        0x0760_0000_0000_0006,
        0,
    );
}
