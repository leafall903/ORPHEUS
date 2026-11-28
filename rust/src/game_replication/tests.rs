use super::{body::*, delivery::*, *};
use crate::{
    bits::BitReader,
    collision_index::TriangleIndex,
    contact_world::ContactWorld,
    game_clock::FIRST_TICK,
    game_content::GameMap,
    game_heroes::{CASSIDY, SOLDIER},
    game_instance::RETAIL_QUANTA,
    game_parameters::{CLIENT_GAME_MESSAGE, PICK_HERO},
    jam_codec::{Fields, Value},
    match_scene::PlayerCard,
    mover_settings::MoverSettings,
    scene_assets::SceneAssets,
    script_codecs::CodecTable,
    script_graph::GraphStore,
    script_init::{Component, EmptyWorld, MAX_INIT_WORK, Root},
    script_runtime::FRAME_MS,
    world::{MAX_ENTITY_RECORDS, Movement},
};
use std::sync::{
    Mutex,
    atomic::{AtomicBool, Ordering},
};

mod npc_chunk_delivery;
pub(crate) mod owner_effects;
mod owner_fault_shields;
mod owner_model;
mod owner_reselection;
mod owner_startup_health;
mod owner_swept_motion;
mod owner_synchronous_fault;
mod owner_walk;
mod player_respawn;
pub(crate) mod practice_observation;
mod wheel_input;

fn pick_hero(entity: u32, hero: u64) -> Message {
    Message {
        id: CLIENT_GAME_MESSAGE,
        fields: Fields::from([
            (
                0x78,
                Value::Struct(Fields::from([(0, Value::Unsigned(u64::from(entity)))])),
            ),
            (0x80, Value::Blob(hero.to_le_bytes().to_vec())),
            (0xa8, Value::Unsigned(PICK_HERO)),
            (0xb0, Value::Signed(3)),
        ]),
    }
}

fn forward(frame: u32) -> Command {
    Command {
        frame,
        forward: 127,
        ..Command::default()
    }
}

#[test]
fn bot_health_replaces_stale_packets_and_respawn_keeps_creation_order() {
    use crate::{
        game_bots::BOT_ENTITY,
        game_egress::PacketDelivery,
        world::{Create, health},
    };
    let old = BOT_ENTITY;
    let new = old + 1;
    let create = |entity| EntityUpdate {
        entity,
        operation: Some(Operation::Create(Box::new(Create::new(1, vec![])))),
        ..EntityUpdate::default()
    };
    let health_update = |now| EntityUpdate {
        entity: old,
        operation: Some(Operation::Update(vec![health(now, 200.0, 0.0, 0.0)])),
        ..EntityUpdate::default()
    };
    let send = |world: &mut WorldDelivery, index: usize| {
        let ready = &world.ready()[index];
        let record = ready.tracked();
        let queue_index = ready.index;
        world.sent(&[queue_index]);
        record
    };
    let resolve = |world: &mut WorldDelivery, record, arrived| {
        world.acknowledge(
            DeliveryReport {
                requeued_reliable: 0,
                owner_correction: None,
                packets: vec![PacketDelivery {
                    sequence: 0,
                    arrived,
                    records: vec![record],
                }],
            },
            Duration::ZERO,
        );
    };
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(FIRST_TICK);
    world.bots.insert(old, BotLifetime::Live);
    world.push(Record {
        update: create(old),
        delivery: Delivery::BotCreate { after: None },
    });
    world.queue_health(health_update(160.0));
    assert_eq!(world.ready().len(), 1);
    assert!(!world.identity_ready(old));
    let spawn = send(&mut world, 0);
    assert!(world.ready().is_empty());
    resolve(&mut world, spawn, true);
    assert!(world.identity_ready(old));

    let earlier_health = send(&mut world, 0);
    world.queue_health(health_update(0.0));
    resolve(&mut world, earlier_health, false);
    let ready = world.ready();
    assert_eq!(ready.len(), 1);
    assert_eq!(ready[0].record.update, health_update(0.0));
    let terminal = send(&mut world, 0);
    resolve(&mut world, Arc::clone(&terminal), false);
    assert!(world.health_pending(old));
    let terminal_retry = send(&mut world, 0);
    assert!(Arc::ptr_eq(&terminal, &terminal_retry));
    resolve(&mut world, terminal_retry, true);
    assert!(!world.health_pending(old));

    world.replace_bot(BotReplacement {
        retired: old,
        created: create(new),
        waits_for_presentation: false,
    });
    assert!(!world.identity_ready(old));
    let ready = world.ready();
    assert_eq!(
        ready
            .iter()
            .map(|item| item.record.update.entity)
            .collect::<Vec<_>>(),
        [old, new]
    );
    let destroy = send(&mut world, 0); // the datagram only had room for the destroy
    assert!(world.ready().is_empty());
    resolve(&mut world, destroy, false);
    let retry = send(&mut world, 0);
    resolve(&mut world, retry, true);
    let replacement = send(&mut world, 0);
    assert_eq!(replacement.update.entity, new);
    resolve(&mut world, replacement, true);
    resolve(&mut world, terminal, false);
    world.queue_health(health_update(120.0));
    assert!(world.ready().is_empty());
    assert!(!world.identity_ready(old));
    assert!(world.identity_ready(new));
}

#[test]
fn sparse_vm_work_retains_the_reference_release_and_timer_tail() {
    use BodyStep::{UnposedBoundary, UnposedTimer};
    assert_eq!(ScriptGap::before(100, 101).count(), 0);
    assert_eq!(
        ScriptGap::before(100, 102).collect::<Vec<_>>(),
        [(101, UnposedBoundary)]
    );
    assert_eq!(
        ScriptGap::before(100, 104).collect::<Vec<_>>(),
        [
            (101, UnposedBoundary),
            (102, UnposedTimer),
            (103, UnposedBoundary),
        ]
    );
    let frames = ScriptGap::before(100, 500).collect::<Vec<_>>();
    assert_eq!(frames.len(), 258);
    assert_eq!(frames[0], (101, UnposedBoundary));
    assert!(
        frames[1..257]
            .iter()
            .copied()
            .eq((243..499).map(|frame| (frame, UnposedTimer)))
    );
    assert_eq!(frames[257], (499, UnposedBoundary));
}

#[test]
fn enemy_observer_waits_for_identity_and_retries_the_exact_baseline() {
    use crate::{
        game_combat::RoomSimulation,
        script_enemy::{EnemySpawn, fixture},
        script_snapshot_wire::SceneProjection,
        script_wire::Variables,
    };
    let (graphs, codecs, definition, request) = fixture(false);
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    let entity = room
        .create_enemy(EnemySpawn {
            graphs: Arc::clone(&graphs),
            codecs: Arc::clone(&codecs),
            definition: &definition,
            request: &request,
            mode: 0x0230_0000_0000_019f,
            mode_variables: Arc::new(Variables::new()),
            tick: 1000,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    let mut script = Component::start(
        Arc::clone(&graphs),
        entity,
        Variables::new(),
        vec![Root::new(1, 42)],
        1000,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let mut world = WorldDelivery::new(0xa000_0100);
    world.started = Some(1000);
    world.create_bot(room.take_created().pop().unwrap());
    world
        .present_scene_script(SceneProjection {
            snapshot: script.snapshot().unwrap(),
            encoder: SnapshotEncoder::new(&graphs, &codecs),
        })
        .unwrap();
    assert_eq!(world.ready().len(), 1);
    let create = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    assert!(world.ready().is_empty());
    world.acknowledge(outcome(create, true), Duration::ZERO);
    assert_eq!(world.ready().len(), 1);
    let baseline = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    script
        .set_entity_variable(
            12345,
            crate::script_wire::Value::Integer(5),
            &mut EmptyWorld,
        )
        .unwrap();
    world
        .present_scene_script(SceneProjection {
            snapshot: script.snapshot().unwrap(),
            encoder: SnapshotEncoder::new(&graphs, &codecs),
        })
        .unwrap();
    assert!(world.ready().is_empty());
    world.acknowledge(outcome(Arc::clone(&baseline), false), Duration::ZERO);
    assert!(Arc::ptr_eq(&baseline, &world.ready()[0].tracked()));
    world.sent(&eligible(&world));
    world.acknowledge(outcome(baseline, true), Duration::ZERO);
    world
        .present_scene_script(SceneProjection {
            snapshot: script.snapshot().unwrap(),
            encoder: SnapshotEncoder::new(&graphs, &codecs),
        })
        .unwrap();
    let delta = world.ready()[0].tracked();
    assert!(matches!(delta.delivery, Delivery::SceneScript { last: 2 }));
    world.sent(&eligible(&world));
    world.acknowledge(outcome(delta, true), Duration::ZERO);
    world
        .present_scene_script(SceneProjection {
            snapshot: script.snapshot().unwrap(),
            encoder: SnapshotEncoder::new(&graphs, &codecs),
        })
        .unwrap();
    assert!(world.ready().is_empty());
    assert!(world.scene_views[&entity].base().is_some());
    world.clear();
    assert!(world.scene_views.is_empty());
}

#[test]
fn bot_practice_nameplates_wait_for_mode_and_body_acknowledgments() {
    use crate::{
        script_codecs::CodecTable,
        script_graph::GraphStore,
        script_snapshot_wire::{FIXTURE_CODECS, SceneProjection, View},
        script_wire::Variables,
    };

    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/npc-nameplate-staged-admission-v1.json"
    ))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["case_id"], "npc-nameplate-staged-admission-v1");
    let expected = |key: &str| contract["expected"][key].as_bool().unwrap();
    let mk_graph = |index: u32| {
        serde_json::json!({
            "index": index,
            "items": 2,
            "nodes": [
                {
                    "pos": 0,
                    "class": "STUStatescriptEntry",
                    "uid": 0,
                    "rid": 0,
                    "remote": -1,
                    "state": null,
                    "client_only": false,
                    "server_only": false,
                    "fields": {
                        "m_2BBEEAB8": false,
                        "m_ADEB6E05": false,
                        "m_outPlug": { "$": "Plug", "links": [[1, "m_beginPlug"]], "m_FF3DAF1E": [] }
                    }
                },
                {
                    "pos": 1,
                    "class": "STUStatescriptState",
                    "uid": 1,
                    "rid": 1,
                    "remote": 0,
                    "state": 0,
                    "client_only": false,
                    "server_only": false,
                    "fields": {
                        "m_2BBEEAB8": true,
                        "m_ADEB6E05": false
                    }
                }
            ],
            "states": [1],
            "entries": [0],
            "remote": [1],
            "sync_vars": [],
            "containers": {},
            "unknown_nodes": [],
            "fields": { "m_9CEC6985": ["0x0D800000000001A4"] },
            "header": {
                "m_nodesBitCount": 4,
                "m_statesBitCount": 4,
                "m_remoteSyncNodesBitCount": 4,
                "m_syncVarsBitCount": 4
            }
        })
    };
    let bundle = serde_json::json!({
        "version": 1,
        "missing": [],
        "graphs": [mk_graph(0x0c18), mk_graph(0x0d24)],
        "bodies": {}
    });
    let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    let codecs = CodecTable::from_json(FIXTURE_CODECS).unwrap();
    let bot_create = |entity| crate::world::EntityUpdate {
        entity,
        operation: Some(crate::world::Operation::Create(Box::new(
            crate::world::Create::new(1, vec![]),
        ))),
        ..Default::default()
    };

    let live_entity = 0x8000_0001;
    let dead_entity = 0x8000_0002;
    let later_live_entity = 0x8000_0003;
    let live_script = Component::start(
        Arc::clone(&graphs),
        live_entity,
        Variables::new(),
        vec![Root::new(1, 0x0c18), Root::new(2, 0x0d24)],
        1000,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let dead_script = Component::start(
        Arc::clone(&graphs),
        dead_entity,
        Variables::new(),
        vec![Root::new(1, 0x0c18), Root::new(2, 0x0d24)],
        1000,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    let with_parent = |script: &Component| {
        let mut snapshot = script.snapshot().unwrap().clone();
        snapshot.instances.get_mut(&2).unwrap().parent = Some((1, 0));
        snapshot
    };
    let live = with_parent(&live_script);
    let dead = with_parent(&dead_script);
    let mut later_live = live.clone();
    later_live.entity = later_live_entity;

    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(1000);
    assert!(!world.mode_ready());

    // Live bot before mode_ready(): 0x0D24 is stripped from its initial observer baseline.
    world.create_bot(bot_create(live_entity));
    world.create_bot(bot_create(dead_entity));
    world.create_bot(bot_create(later_live_entity));
    let creates: Vec<_> = world.ready().iter().map(|r| r.tracked()).collect();
    world.sent(&eligible(&world));
    for record in creates {
        world.acknowledge(outcome(record, true), Duration::ZERO);
    }

    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    let full = encoder.project(&live, View::Observer).unwrap();
    assert!(full.has_graph(0x0c18));
    assert!(full.has_graph(0x0d24));

    world
        .present_scene_script_with_dead(
            SceneProjection {
                snapshot: &live,
                encoder: SnapshotEncoder::new(&graphs, &codecs),
            },
            false,
        )
        .unwrap();
    let live_base_record = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    world.acknowledge(outcome(live_base_record, true), Duration::ZERO);
    let (_, live_base) = world.scene_views[&live_entity].base().unwrap();
    assert!(live_base.has_graph(0x0c18));
    assert!(!live_base.has_graph(0x0d24));

    // Once mode_ready() flips true, a live bot can stage the introduction.
    // Death before transmission supersedes that pending projection.
    let mode_chunk = world.mode.full(&crate::bits::BitWriter::new()).unwrap();
    assert!(world.mode.arrived(mode_chunk.last));
    assert!(world.mode_ready());

    world
        .present_scene_script_with_dead(
            SceneProjection {
                snapshot: &live,
                encoder: SnapshotEncoder::new(&graphs, &codecs),
            },
            false,
        )
        .unwrap();
    let live_delta_record = world.ready()[0].tracked();
    world
        .present_scene_script_with_dead(
            SceneProjection {
                snapshot: &live,
                encoder: SnapshotEncoder::new(&graphs, &codecs),
            },
            true,
        )
        .unwrap();
    assert_eq!(
        world
            .ready()
            .iter()
            .any(|record| Arc::ptr_eq(&live_delta_record, &record.tracked())),
        expected("unsent_introduction_remains_queued_after_death")
    );
    let (_, live_after) = world.scene_views[&live_entity].base().unwrap();
    assert!(!live_after.has_graph(0x0d24));

    // Mode was ready before this bot's first script frame. Its parent still
    // needs an ACK before the nameplate can use that baseline.
    let present = |world: &mut WorldDelivery, snapshot: &crate::script_init::Snapshot| {
        world
            .present_scene_script_with_dead(
                SceneProjection {
                    snapshot,
                    encoder: SnapshotEncoder::new(&graphs, &codecs),
                },
                false,
            )
            .unwrap();
    };
    present(&mut world, &later_live);
    let parent_full = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    present(&mut world, &later_live);
    assert!(world.ready().is_empty());
    world.acknowledge(outcome(parent_full, true), Duration::ZERO);
    let (_, later_base) = world.scene_views[&later_live_entity].base().unwrap();
    assert!(later_base.has_graph(0x0c18));
    assert_eq!(
        later_base.has_graph(0x0d24),
        expected("parent_baseline_has_nameplate")
    );

    let unrelated = u32::try_from(
        contract["input"]["concurrent_entity_variable"]
            .as_u64()
            .unwrap(),
    )
    .unwrap();
    later_live
        .variables
        .insert(unrelated, crate::script_wire::Value::Bool(true));
    present(&mut world, &later_live);
    let nameplate = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    if contract["input"]["lost_introduction"].as_bool().unwrap() {
        world.acknowledge(outcome(Arc::clone(&nameplate), false), Duration::ZERO);
        assert!(Arc::ptr_eq(&nameplate, &world.ready()[0].tracked()));
        world.sent(&eligible(&world));
    }
    world.acknowledge(outcome(nameplate, true), Duration::ZERO);
    let (_, staged) = world.scene_views[&later_live_entity].base().unwrap();
    assert_eq!(
        staged.has_graph(0x0d24),
        expected("introduction_has_nameplate")
    );
    assert_eq!(
        staged.entity_variables().contains_key(&unrelated),
        expected("introduction_has_concurrent_entity_variable")
    );

    present(&mut world, &later_live);
    let changes = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    world.acknowledge(outcome(changes, true), Duration::ZERO);
    let (_, caught_up) = world.scene_views[&later_live_entity].base().unwrap();
    assert_eq!(
        caught_up.entity_variables().contains_key(&unrelated),
        expected("following_delta_has_concurrent_entity_variable")
    );

    world
        .present_scene_script_with_dead(
            SceneProjection {
                snapshot: &later_live,
                encoder: SnapshotEncoder::new(&graphs, &codecs),
            },
            true,
        )
        .unwrap();
    let retirement = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    world.acknowledge(outcome(Arc::clone(&retirement), false), Duration::ZERO);
    assert!(Arc::ptr_eq(&retirement, &world.ready()[0].tracked()));
    world.sent(&eligible(&world));
    world.acknowledge(outcome(retirement, true), Duration::ZERO);
    let (_, corpse) = world.scene_views[&later_live_entity].base().unwrap();
    assert!(corpse.has_graph(0x0c18));
    assert_eq!(
        corpse.has_graph(0x0d24),
        expected("acknowledged_nameplate_survives_death_delta")
    );

    // An already-dead bot without a prior 0x0D24 never gets one even after mode_ready().
    world
        .present_scene_script_with_dead(
            SceneProjection {
                snapshot: &dead,
                encoder: SnapshotEncoder::new(&graphs, &codecs),
            },
            true,
        )
        .unwrap();
    let dead_base_record = world.ready()[0].tracked();
    world.sent(&eligible(&world));
    world.acknowledge(outcome(dead_base_record, true), Duration::ZERO);
    let (_, dead_base) = world.scene_views[&dead_entity].base().unwrap();
    assert!(dead_base.has_graph(0x0c18));
    assert_eq!(
        dead_base.has_graph(0x0d24),
        expected("dead_baseline_has_nameplate")
    );
}

#[test]
fn gate_voice_anchor_waits_for_the_placeable_acknowledgment() {
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(1001);
    let gate = 0x8000_0032;
    let notice = crate::game_combat_messages::CombatNotice::Voice {
        anchor: gate,
        speaker: GAME_MODE_ENTITY,
        listener: OWNER | 1,
        stimulus: 0x0ee0_0000_0000_06c7,
        line: 0x0760_0000_0000_8c71,
    };
    let identity = notice.required_identity().unwrap();
    let placeable = Arc::new(Record::baseline(EntityUpdate {
        entity: gate,
        operation: Some(Operation::Placeable(0x32)),
        ..EntityUpdate::default()
    }));
    world.acknowledge(outcome(Arc::clone(&placeable), false), Duration::ZERO);
    assert!(!world.identity_ready(identity));
    world.acknowledge(outcome(placeable, true), Duration::ZERO);
    assert!(world.identity_ready(identity));
}

#[test]
#[ignore = "requires the separately prepared 104319 Junkenstein runtime"]
fn junkenstein_notifications_and_countdown_share_reliable_delivery() {
    use crate::game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog};
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&root).unwrap();
    let maps = MapCatalog::load(root.join("data/game_maps_174.json")).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = maps
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
    let hero = scene.heroes().find_hero("Cassidy").unwrap().guid;
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(FIRST_TICK);
    let bootstrap = world.mode.full(&crate::bits::BitWriter::new()).unwrap();
    assert!(world.mode.arrived(bootstrap.last));
    let mut countdowns = 0;
    let mut notifications = 0;
    let mut phases = 0;
    for tick in 2700..2724 {
        scene.step_encounter(tick, Some(hero));
        let before = world.countdown.clone();
        let phases_before = world.encounter_phases.clone();
        world.queue_encounter_presentation(&scene).unwrap();
        assert_eq!(world.ready().len(), 1);
        if phases_before != world.encounter_phases {
            phases += 1;
        } else if before != world.countdown {
            countdowns += 1;
        } else {
            notifications += 1;
            assert!(world.encounter_notifications.is_some());
        }
        let record = world.ready()[0].tracked();
        world.sent(&eligible(&world));
        world.queue_encounter_presentation(&scene).unwrap();
        assert!(world.ready().is_empty(), "wait for the in-flight chunk");
        if tick == 2700 {
            world.acknowledge(outcome(Arc::clone(&record), false), Duration::ZERO);
            assert!(Arc::ptr_eq(&record, &world.ready()[0].tracked()));
            world.sent(&eligible(&world));
        }
        world.acknowledge(outcome(record, true), Duration::ZERO);
        assert!(!world.mode.data_in_flight());
    }
    assert!(countdowns >= 9, "countdown must not starve: {countdowns}");
    assert!(
        notifications >= 9,
        "notifications must not starve: {notifications}"
    );
    // The first loop only covers hero readiness and its countdown. Advance
    // through the authored opening rather than injecting a HUD message.
    let mut battle_clock = None;
    for tick in 2724..=8000 {
        scene.step_encounter(tick, Some(hero));
        let before = world.encounter_phases.clone();
        world.queue_encounter_presentation(&scene).unwrap();
        if before != world.encounter_phases {
            phases += 1;
            assert!(
                scene
                    .encounter_phase_wire(&world.encounter_phases)
                    .unwrap()
                    .is_none()
            );
        }
        if tick == 7000 {
            battle_clock = world.countdown.as_ref().map(|frame| frame.value.clone());
        }
        assert!(world.ready().len() <= 1);
        if let Some(record) = world.ready().first().map(|record| record.tracked()) {
            world.sent(&eligible(&world));
            if before != world.encounter_phases {
                world.acknowledge(outcome(Arc::clone(&record), false), Duration::ZERO);
                assert!(Arc::ptr_eq(&record, &world.ready()[0].tracked()));
                world.sent(&eligible(&world));
            }
            world.acknowledge(outcome(record, true), Duration::ZERO);
        }
    }
    assert!(phases > 0, "authored HUD startup must reach the client");
    let clock = world.countdown.as_ref().unwrap();
    assert_ne!(
        battle_clock.as_ref(),
        Some(&clock.value),
        "battle clock updates must continue after the authored HUD announcement"
    );
    assert!(
        clock.states[2].is_some(),
        "battle clock chase must reach the client"
    );
    assert!(clock.states[..2].iter().all(Option::is_none));
    assert_eq!(phases, world.encounter_phases.len());
    // The final boss objective uses the second client-only announcement.
    // Keep projecting through that transition, not just the opening HUD.
    for tick in 8001..=43000 {
        scene.step_encounter(tick, Some(hero));
        world.queue_encounter_presentation(&scene).unwrap();
        if let Some(record) = world.ready().first().map(|record| record.tracked()) {
            world.sent(&eligible(&world));
            world.acknowledge(outcome(record, true), Duration::ZERO);
        }
    }
    world.clear();
    assert!(world.encounter_notifications.is_none());
    assert!(world.encounter_notification_turn);
    assert!(world.encounter_phases.is_empty());
}

#[test]
fn shared_first_command_anchor_respects_body_and_existing_input_history() {
    #[derive(serde::Deserialize)]
    struct Contract {
        room_frame: u32,
        cases: Vec<Case>,
    }
    #[derive(serde::Deserialize)]
    struct Case {
        name: String,
        body_frame: Option<u32>,
        prior: Option<u32>,
        offered: Vec<u32>,
        fresh: Vec<u32>,
    }
    let contract: Contract = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/owner-first-command-anchor-v1.json"
    ))
    .unwrap();
    for case in contract.cases {
        let mut world = WorldDelivery::new(OWNER);
        world.started = Some(contract.room_frame);
        if let Some(frame) = case.prior {
            world.queue_commands(contract.room_frame, &[forward(frame)]);
            world.input.work.pop_front().unwrap();
            world.input.processed = Some(frame);
        }
        if let Some(frame) = case.body_frame {
            let id = avatar(1, 1);
            let body = body(id);
            let runtime = synthetic_owner(id.entity, frame, Arc::new(AtomicBool::new(false)));
            world.body = Some(body);
            world.body_script =
                Some(BodyScript::new(body, frame, world.input.processed, &runtime).unwrap());
        }
        for frame in case.offered {
            world.queue_commands(contract.room_frame, &[forward(frame)]);
        }
        let fresh: Vec<_> = world
            .input
            .work
            .iter()
            .map(|work| match work {
                InputWork::Command { command, .. } => command.frame,
                _ => panic!("unexpected input boundary for {}", case.name),
            })
            .collect();
        assert_eq!(fresh, case.fresh, "{}", case.name);
        assert_eq!(world.input.processed, case.prior);
        assert_eq!(world.input.admitted, fresh.last().copied().or(case.prior));
        if fresh.is_empty() && case.prior.is_none() {
            assert_eq!(world.command_ack(contract.room_frame), contract.room_frame);
        }
    }
}

#[test]
fn native_command_window_keeps_duplicates_and_future_input_unacknowledged() {
    let (fresh, boundary) = command_prefix(
        Some(1000),
        1000,
        &[
            forward(1000),
            forward(1001),
            forward(1096),
            forward(1097),
            forward(20_000),
        ],
    );
    assert_eq!(fresh, [forward(1001), forward(1096)]);
    assert_eq!(boundary, None);

    let (fresh, boundary) = command_prefix(None, 1000, &[forward(MAX_LEASE_FRAME + 1)]);
    assert!(fresh.is_empty());
    assert_eq!(boundary, Some(MAX_LEASE_FRAME + 1));
    let (fresh, boundary) = command_prefix(
        None,
        MAX_LEASE_FRAME - 2,
        &[forward(MAX_LEASE_FRAME - 1), forward(MAX_LEASE_FRAME + 1)],
    );
    assert_eq!(fresh, [forward(MAX_LEASE_FRAME - 1)]);
    assert_eq!(boundary, Some(MAX_LEASE_FRAME + 1));
}

#[test]
fn overlapping_bursts_enqueue_each_frame_once_and_do_not_ack_pending_work() {
    let mut world = WorldDelivery::new(0xa000_0100);
    world.started = Some(1000);
    let first: Vec<_> = (980..1012).map(forward).collect();
    let next: Vec<_> = (1000..1032).map(forward).collect();
    world.queue_commands(1000, &first);
    world.queue_commands(1000, &next);
    world.queue_commands(1000, &first);
    let frames: Vec<_> = world
        .input
        .work
        .iter()
        .map(|work| {
            let InputWork::Command {
                command,
                pending: None,
            } = work
            else {
                panic!("fresh command");
            };
            command.frame
        })
        .collect();
    assert_eq!(frames, (980..1032).collect::<Vec<_>>());
    assert_eq!(world.command_ack(1000), 979);
    world.clear();
    assert!(!world.has_input_work());
    assert_eq!(world.command_ack(1001), 979);
}

#[test]
fn pending_queue_closes_after_the_prefix_at_a_boundary_or_capacity() {
    let mut input = InputQueue::default();
    input.commands(
        MAX_LEASE_FRAME - 2,
        &[forward(MAX_LEASE_FRAME - 1), forward(MAX_LEASE_FRAME + 1)],
        None,
    );
    assert!(input.closing);
    assert_eq!(input.admitted, Some(MAX_LEASE_FRAME - 1));
    assert!(matches!(
        input.work.pop_front(),
        Some(InputWork::Command { .. })
    ));
    assert!(matches!(input.work.pop_front(), Some(InputWork::Leave)));

    let mut input = InputQueue::default();
    for frame in 1..=MAX_PENDING_INPUT as u32 + 2 {
        input.commands(frame, &[forward(frame)], None);
    }
    assert_eq!(input.work.len(), MAX_PENDING_INPUT + 1);
    assert_eq!(input.admitted, Some(MAX_PENDING_INPUT as u32));
    assert!(matches!(input.work.back(), Some(InputWork::Leave)));
    assert_eq!(input.processed, None);
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack without bot VM graphs"]
fn queued_player_requests_ignore_invalid_input_but_propagate_replacement_failure() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    // Script support is independently optional in this developer preview.
    scene.owner_body_factory = None;
    let owner = 0xa000_0100;
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: owner,
        slot: 0,
        team: 0,
        card: &card,
    };
    let mut world = WorldDelivery::new(owner);
    world.start(&scene, &viewer, FIRST_TICK).unwrap();
    world.created.insert(owner);
    world.present(&scene, FIRST_TICK).unwrap();
    for message in [
        Message {
            id: 0,
            fields: Fields::new(),
        },
        pick_hero(owner + 1, SOLDIER),
        pick_hero(owner, 0),
    ] {
        world.queue_game_message(FIRST_TICK, Duration::ZERO, message);
    }
    world.queue_skin(SOLDIER, FIRST_TICK, Duration::ZERO);
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (4, false)
    );
    assert!(world.body.is_none());

    world.generation = u64::MAX;
    world.queue_game_message(FIRST_TICK, Duration::ZERO, pick_hero(owner, SOLDIER));
    let error = world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap_err();
    assert_eq!(error.to_string(), "player body lifetime exhausted");
    assert!(world.body.is_none());
    world.generation = 0;
    world.queue_game_message(FIRST_TICK, Duration::ZERO, pick_hero(owner, SOLDIER));
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (1, false)
    );
    let body = world.body.unwrap();
    assert!(world.body_script.is_none());

    let mut card = PlayerCard::default();
    card.skins.insert(
        SOLDIER,
        SkinTheme {
            guid: 1,
            ..SkinTheme::default()
        },
    );
    let viewer = PlayerAppearance {
        card: &card,
        ..viewer
    };
    world.queue_skin(0, FIRST_TICK, Duration::ZERO);
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (1, false)
    );
    world.generation = u64::MAX;
    world.queue_skin(SOLDIER, FIRST_TICK, Duration::ZERO);
    let error = world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap_err();
    assert_eq!(error.to_string(), "player body lifetime exhausted");
    assert_eq!(world.body.unwrap().id, body.id);
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack without bot VM graphs"]
fn native_world_resumes_a_gap_before_switching_the_body_and_acknowledging_input() {
    use crate::game_instance::TeamMemberCounts;

    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let owner = 0xa000_0100;
    let viewer = PlayerAppearance {
        entity: owner,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let roster = TeamMemberCounts::from_teams(scene.map().free_for_all, [viewer.team]);
    scene.simulation_mut().set_team_member_counts(roster);
    scene
        .simulation_mut()
        .prepare_spatial_frame(FIRST_TICK)
        .unwrap();
    let mut world = WorldDelivery::new(owner);
    let first = FIRST_TICK + 1;
    world.queue_commands(FIRST_TICK, &[forward(first)]);
    assert!(!world.has_input_work());
    assert_eq!(world.command_ack(FIRST_TICK), FIRST_TICK);
    world.start(&scene, &viewer, FIRST_TICK).unwrap();
    world.queue_commands(FIRST_TICK, &[forward(first)]);
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (1, false)
    );
    assert_eq!(world.command_ack(FIRST_TICK + 2), first);
    world.created.insert(owner);
    world.present(&scene, FIRST_TICK).unwrap();
    let hero = scene.heroes().get(SOLDIER).unwrap().clone();
    world
        .switch(
            &mut scene,
            &viewer,
            FIRST_TICK,
            RETAIL_QUANTA,
            owner | 1,
            (&hero, SkinTheme::default()),
        )
        .unwrap();
    assert!(world.body_script.is_some());
    let initial = scene
        .simulation()
        .player_body_by_owner(owner)
        .unwrap()
        .mover
        .pose();
    let actual = Command {
        yaw: initial.yaw,
        ..forward(first + 1)
    };
    let gap = Command {
        frame: actual.frame + 64,
        ..actual
    };
    let later = Command {
        frame: gap.frame + 1,
        ..actual
    };
    world.queue_commands(FIRST_TICK + 2, &[actual]);
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (1, false)
    );
    world.queue_commands(FIRST_TICK + 2, &[actual, gap]);

    let next_hero = scene.heroes().find_hero("Zarya").unwrap().clone();
    let switch = pick_hero(owner, next_hero.guid);
    scene
        .simulation_mut()
        .prepare_spatial_frame(FIRST_TICK + 3)
        .unwrap();
    world.queue_game_message(FIRST_TICK + 3, Duration::ZERO, switch);
    world.queue_commands(FIRST_TICK + 3, &[gap, later]);

    for turn in 1..=4 {
        assert_eq!(
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap(),
            (16, false)
        );
        assert_eq!(world.body.unwrap().id.entity, owner | 1);
        assert_eq!(
            scene
                .simulation()
                .player_body_by_owner(owner)
                .unwrap()
                .mover
                .pose()
                .frame,
            Some(actual.frame + turn * 16)
        );
        assert_eq!(
            world.command_ack(FIRST_TICK + 90),
            if turn == 4 { gap.frame } else { actual.frame }
        );
    }
    let mut synchronous = scene.player_spawn_mover(hero.body, viewer.team).unwrap();
    for command in [actual, gap] {
        synchronous
            .step(scene.movement_index(), command, 0.016, |_, _| {})
            .unwrap();
    }
    assert_eq!(
        scene
            .simulation()
            .player_body_by_owner(owner)
            .unwrap()
            .mover
            .pose(),
        synchronous.pose()
    );
    assert_ne!(synchronous.pose().position, initial.position);

    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (2, false)
    );
    let replacement = world.body.unwrap();
    assert_eq!(replacement.id.entity, owner | 2);
    assert_eq!(replacement.hero, next_hero.guid);
    assert_eq!(world.body_script.as_ref().unwrap().id, replacement.id);
    assert!(
        scene
            .simulation()
            .player_body(replacement.id)
            .unwrap()
            .runtime
            .is_some()
    );
    assert_eq!(world.command_ack(FIRST_TICK + 90), later.frame);
    let mut synchronous = scene
        .player_spawn_mover(next_hero.body, viewer.team)
        .unwrap();
    synchronous
        .step(scene.movement_index(), later, 0.016, |_, _| {})
        .unwrap();
    assert_eq!(
        scene
            .simulation()
            .player_body_by_owner(owner)
            .unwrap()
            .mover
            .pose(),
        synchronous.pose()
    );

    world.queue_commands(FIRST_TICK + 3, &[forward(later.frame + 1)]);
    world.clear();
    assert!(!world.has_input_work());
    assert_eq!(world.command_ack(FIRST_TICK + 90), later.frame);
}

#[test]
#[ignore = "requires a prepared, tagged 104319 Practice Range runtime"]
fn prepared_practice_catalog_exercises_transactional_owner_admission() {
    use crate::{game_combat::PlayerBodyEvent, game_instance::TeamMemberCounts};

    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let roster = TeamMemberCounts::from_teams(scene.map().free_for_all, [viewer.team]);
    scene.simulation_mut().set_team_member_counts(roster);
    let mut world = WorldDelivery::new(OWNER);
    world.start(&scene, &viewer, FIRST_TICK).unwrap();
    world.created.insert(OWNER);
    world.present(&scene, FIRST_TICK).unwrap();

    let mut heroes: Vec<_> = scene.heroes().heroes().cloned().collect();
    heroes.sort_unstable_by_key(|hero| (hero.guid != SOLDIER, hero.guid));
    let mut admitted = 0;
    for (index, hero) in heroes.iter().enumerate() {
        let tick = FIRST_TICK + 1 + u32::try_from(index).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        let prior = world.body.map(|body| body.id);
        let generation = world.generation;
        let controller = (
            world.controller.latest_data(),
            world.controller.command_frame(),
        );
        let queued = world.queued.len();
        world.queue_game_message(tick, Duration::ZERO, pick_hero(OWNER, hero.guid));
        assert_eq!(
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap(),
            (1, false)
        );
        let current = world.body.map(|body| body.id);
        assert_eq!(
            scene
                .simulation()
                .player_body_by_owner(OWNER)
                .map(|state| state.id),
            current
        );
        assert_eq!(world.body_script.as_ref().map(|script| script.id), current);
        let events = scene.simulation_mut().take_player_events();
        if current == prior {
            assert_eq!(world.generation, generation);
            assert_eq!(
                (
                    world.controller.latest_data(),
                    world.controller.command_frame()
                ),
                controller
            );
            assert_eq!(world.queued.len(), queued);
            assert!(events.is_empty());
        } else {
            admitted += 1;
            let id = current.unwrap();
            assert_eq!(world.generation, generation + 1);
            assert_eq!(world.controller.latest_data(), controller.0 + 1);
            let actor = scene.simulation().player_body(id).unwrap();
            assert_eq!(actor.hero, hero.guid);
            assert!(
                actor
                    .runtime
                    .as_ref()
                    .unwrap()
                    .snapshot()
                    .unwrap()
                    .initialization_complete()
            );
            assert!(
                matches!(events.last(), Some(PlayerBodyEvent::Created { id: created, .. }) if *created == id)
            );
            if let Some(prior) = prior {
                assert!(
                    matches!(events.first(), Some(PlayerBodyEvent::Retired { id: retired, .. }) if *retired == prior)
                );
                assert!(scene.simulation().player_body(prior).is_none());
            }
        }
    }
    eprintln!(
        "prepared Practice Range room admission: {admitted}/{}",
        heroes.len()
    );
    assert!(
        admitted > 1,
        "expected at least two catalog bodies to attach"
    );
}

use crate::game_egress::PacketDelivery;

const OWNER: u32 = 0xa000_0100;

fn avatar(suffix: u32, generation: u64) -> BodyId {
    BodyId {
        entity: OWNER | suffix,
        generation,
    }
}

fn body(id: BodyId) -> Body {
    Body {
        id,
        hero: 7,
        skin: SkinTheme::default(),
    }
}

fn record(entity: u32, delivery: Delivery) -> Record {
    let operation = match delivery {
        Delivery::CreateBody(_) => {
            Operation::Create(Box::new(crate::world::Create::new(1, Vec::new())))
        }
        Delivery::DestroyBody(_) => Operation::Destroy,
        _ => Operation::Update(Vec::new()),
    };
    Record {
        update: EntityUpdate {
            entity,
            operation: Some(operation),
            ..EntityUpdate::default()
        },
        delivery,
    }
}

fn outcome(record: Arc<Record>, arrived: bool) -> DeliveryReport<Arc<Record>> {
    DeliveryReport {
        requeued_reliable: 0,
        owner_correction: None,
        packets: vec![PacketDelivery {
            sequence: 0,
            arrived,
            records: vec![record],
        }],
    }
}

fn eligible(world: &WorldDelivery) -> Vec<usize> {
    world.ready().iter().map(|ready| ready.index).collect()
}

struct SyntheticOwner {
    component: Component,
    snapshot_override: Option<Snapshot>,
    graphs: Arc<GraphStore>,
    codecs: CodecTable,
    modifiers: Option<BTreeMap<ModifierKey, Modifier>>,
    failed: Arc<AtomicBool>,
    health_update: Arc<Mutex<Option<WorldComponent>>>,
    vitals: Option<crate::vitals::Vitals>,
    authored_health_change: Option<(u32, f64)>,
    authored_room_message: Option<(u32, crate::script_init::RoomMessage)>,
    received_room_messages: Arc<Mutex<Vec<(u32, crate::script_init::RoomMessage)>>>,
    script_requests: crate::game_combat::RoomScriptRequests,
    authored_facing: Arc<Mutex<Option<[i32; 2]>>>,
    move_request: Option<([f64; 3], f64)>,
    ball_roll_speed: Option<f64>,
    queued_hitscan: Option<PosedHitscan>,
    hitscan_fault: Option<(u32, PosedHitscan)>,
}

impl OwnerBodyRuntime for SyntheticOwner {
    fn begin_state(
        &mut self,
        _instance: u16,
        _raw: u16,
    ) -> Result<(), crate::script_init::InitFault> {
        // This probe runtime carries no state table; a lifecycle begin is
        // a loud error rather than a silent no-op.
        Err(crate::script_init::InitFault::InvalidRoot(_instance))
    }

    fn receive_applied_damage(&mut self, _: crate::game_combat::AppliedDamage) -> io::Result<()> {
        Ok(())
    }

    fn receive_host_event(
        &mut self,
        _: crate::script_init::HostEvent,
        _: crate::script_init::HostHealthContext,
    ) -> Result<usize, InitFault> {
        Ok(0)
    }

    fn receive_authored_hit_feedback(
        &mut self,
        _: crate::script_init::HostEvent,
        _: &crate::script_init::HostHealthContext,
        _: f32,
    ) -> Result<usize, InitFault> {
        Ok(0)
    }

    fn receive_authored_elimination_feedback(
        &mut self,
        _: Arc<crate::script_init::HealthTargetFacts>,
    ) -> Result<usize, InitFault> {
        Ok(0)
    }

    fn authored_facing(&self) -> Option<[i32; 2]> {
        *self.authored_facing.lock().unwrap()
    }

    fn move_request(&self) -> Option<([f64; 3], f64)> {
        self.move_request
    }

    fn ball_roll_speed(&self) -> Option<f64> {
        self.ball_roll_speed
    }

    fn set_spatial_frame(&mut self, frame: Arc<crate::game_combat::RoomSpatialFrame>) {
        if let Some((target, delta)) = self.authored_health_change.take() {
            assert!(self.script_requests.queue_health(&frame, target, delta));
        }
        if let Some((target, message)) = self.authored_room_message.take() {
            assert!(self.script_requests.queue_message(&frame, target, message));
        }
    }

    fn receive_room_message(
        &mut self,
        sender: u32,
        message: crate::script_init::RoomMessage,
    ) -> Result<usize, crate::script_init::InitFault> {
        self.received_room_messages
            .lock()
            .unwrap()
            .push((sender, message));
        Ok(1)
    }

    fn queue_wheel_selection(
        &mut self,
        action: ClientWheelAction,
        _command_frame: Option<u32>,
    ) -> Result<usize, InitFault> {
        self.receive_room_message(
            self.component.snapshot().unwrap().entity,
            action.into_local_message(),
        )
    }

    fn wheel_actions_available(&self) -> bool {
        true
    }

    fn take_script_requests(&mut self) -> Vec<crate::game_combat::RoomScriptRequest> {
        self.script_requests.take()
    }

    fn snapshot(&self) -> Option<&Snapshot> {
        (!self.failed.load(Ordering::Relaxed))
            .then(|| {
                self.snapshot_override
                    .as_ref()
                    .or_else(|| self.component.snapshot())
            })
            .flatten()
    }

    fn encoder(&self) -> SnapshotEncoder<'_> {
        SnapshotEncoder::new(&self.graphs, &self.codecs)
    }

    fn advance(&mut self, command: Command, _mover: &Mover, _step: BodyStep) -> io::Result<()> {
        if let Some((_, shot)) = self
            .hitscan_fault
            .filter(|(frame, _)| *frame == command.frame)
        {
            self.queued_hitscan = Some(shot);
            return Err(io::Error::other("fixture owner input fault"));
        }
        self.component
            .advance_command(command, &mut EmptyWorld)
            .map_err(invalid)
    }

    fn frame_ms(&self) -> u32 {
        FRAME_MS as u32
    }

    fn vitals(&self) -> Option<&crate::vitals::Vitals> {
        self.vitals.as_ref()
    }

    fn take_vitals(&mut self) -> Option<crate::vitals::Vitals> {
        self.vitals.take()
    }

    fn heal(&mut self, amount: f64) -> f64 {
        self.vitals
            .as_mut()
            .map_or(0.0, |vitals| vitals.heal(amount))
    }

    fn receive_damage(
        &mut self,
        amount: f64,
        source: crate::vitals::Death,
        _tags: &crate::script_init::HitTags,
    ) -> crate::vitals::Damage {
        self.vitals
            .as_mut()
            .map_or_else(crate::vitals::Damage::default, |vitals| {
                vitals.damage(amount, source)
            })
    }

    fn take_health_update(&mut self) -> Option<WorldComponent> {
        if let Some(update) = self.health_update.lock().unwrap().take() {
            return Some(update);
        }
        let vitals = self.vitals.as_mut()?;
        let component = vitals.changed_component()?;
        vitals.clear_changed();
        Some(component)
    }

    fn movement_modifiers(&self) -> Option<&BTreeMap<ModifierKey, Modifier>> {
        self.modifiers.as_ref()
    }

    fn take_shots(&mut self, command: Option<Command>, _mover: &Mover) -> Vec<PosedShot> {
        let Some(command) = command.filter(|command| command.buttons & crate::commands::FIRE != 0)
        else {
            return Vec::new();
        };
        self.queued_hitscan
            .take()
            .map(|shot| {
                PosedShot::Hitscan(Box::new(PosedHitscan {
                    command,
                    shot_frame: command.frame,
                    ..shot
                }))
            })
            .into_iter()
            .collect()
    }
}

fn synthetic_owner_with_vitals(
    entity: u32,
    frame: u32,
    failed: Arc<AtomicBool>,
    vitals: Option<crate::vitals::Vitals>,
) -> SyntheticOwner {
    let graphs = Arc::new(
        GraphStore::from_json(
            br#"{"version":1,"bodies":{},"missing":[],"graphs":[{
            "index":42,"items":0,"nodes":[],"states":[],"entries":[],"remote":[],
            "sync_vars":[],"containers":{},"unknown_nodes":[],"fields":{},
            "header":{"m_nodesBitCount":4,"m_statesBitCount":4,
                "m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}
        }]}"#,
        )
        .unwrap(),
    );
    let component = Component::start(
        Arc::clone(&graphs),
        entity,
        Default::default(),
        vec![Root::new(1, 42)],
        frame,
        &mut EmptyWorld,
        MAX_INIT_WORK,
    )
    .unwrap();
    SyntheticOwner {
        component,
        snapshot_override: None,
        graphs,
        codecs: CodecTable::from_json(r#"{"classes":{}}"#).unwrap(),
        modifiers: Some(BTreeMap::new()),
        failed,
        health_update: Arc::default(),
        vitals,
        authored_health_change: None,
        authored_room_message: None,
        received_room_messages: Arc::default(),
        script_requests: Default::default(),
        authored_facing: Arc::default(),
        move_request: None,
        ball_roll_speed: None,
        queued_hitscan: None,
        hitscan_fault: None,
    }
}

fn synthetic_owner(entity: u32, frame: u32, failed: Arc<AtomicBool>) -> SyntheticOwner {
    synthetic_owner_with_vitals(entity, frame, failed, None)
}

fn mover_at(position: [f64; 3]) -> Mover {
    Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        position,
        0,
        0x18,
    )
    .unwrap()
}

fn empty_mover() -> Mover {
    mover_at([0.0; 3])
}

#[test]
fn authored_facing_refreshes_after_each_pose_and_releases_after_failure() {
    let id = BodyId {
        entity: 71,
        generation: 1,
    };
    let failed = Arc::new(AtomicBool::new(false));
    let mut runtime = synthetic_owner(id.entity, 100, Arc::clone(&failed));
    let facing = Arc::clone(&runtime.authored_facing);
    *facing.lock().unwrap() = Some([16_384, 0]);
    let mut script = BodyScript::new(body(id), 100, None, &runtime).unwrap();
    let mut mover = empty_mover();
    mover.set_authored_facing(runtime.authored_facing());
    let rays = TriangleIndex::new(Vec::new()).unwrap();
    let mut frame = 100;
    let mut fields = Vec::new();
    let mut launches = Vec::new();
    let mut hitscan = Vec::new();
    let mut run = |frame_number| {
        let mut buffers = StepBuffers {
            frame: &mut frame,
            fields: &mut fields,
            launches: &mut launches,
            hitscan: &mut hitscan,
        };
        let mut result = Ok(());
        mover
            .step(
                &rays,
                Command {
                    frame: frame_number,
                    yaw: 1234,
                    pitch: -5678,
                    ..Command::default()
                },
                0.016,
                |mover, command| {
                    result = script.advance_step(
                        &mut runtime,
                        mover,
                        &mut buffers,
                        command,
                        BodyStep::Posed,
                    );
                },
            )
            .unwrap();
        ([mover.pose().yaw, mover.pose().pitch], result)
    };
    let (pose, result) = run(101);
    result.unwrap();
    assert_eq!(pose, [16_384, 0]);
    *facing.lock().unwrap() = Some([0, -16_384]);
    let (pose, result) = run(102);
    result.unwrap();
    assert_eq!(pose, [16_384, 0]);
    let (pose, result) = run(103);
    result.unwrap();
    assert_eq!(pose, [0, -16_384]);
    *facing.lock().unwrap() = None;
    let (pose, result) = run(104);
    result.unwrap();
    assert_eq!(pose, [0, -16_384]);
    let (pose, result) = run(105);
    result.unwrap();
    assert_eq!(pose, [1234, -5678]);
    *facing.lock().unwrap() = Some([-16_384, 0]);
    let (_, result) = run(106);
    result.unwrap();
    failed.store(true, Ordering::Relaxed);
    let (pose, result) = run(107);
    assert_eq!(pose, [-16_384, 0]);
    assert!(result.is_err());
    let (pose, result) = run(108);
    assert!(result.is_err());
    assert_eq!(pose, [1234, -5678]);
}

fn activate_script(
    world: &mut WorldDelivery,
    simulation: &mut RoomSimulation,
    id: BodyId,
    hero: u64,
    failed: Arc<AtomicBool>,
) {
    let runtime = synthetic_owner(id.entity, world.input.processed.unwrap_or(100), failed);
    activate_synthetic_runtime(world, simulation, id, hero, runtime);
}

fn activate_script_without_movement_projection(
    world: &mut WorldDelivery,
    simulation: &mut RoomSimulation,
    id: BodyId,
    hero: u64,
    failed: Arc<AtomicBool>,
) {
    let mut runtime = synthetic_owner(id.entity, world.input.processed.unwrap_or(100), failed);
    runtime.modifiers = None;
    activate_synthetic_runtime(world, simulation, id, hero, runtime);
}

fn activate_synthetic_runtime(
    world: &mut WorldDelivery,
    simulation: &mut RoomSimulation,
    id: BodyId,
    hero: u64,
    runtime: SyntheticOwner,
) {
    let body = Body { hero, ..body(id) };
    world.body = Some(body);
    world.bound = None;
    world
        .bodies
        .insert(id.entity, (id.generation, Lifetime::Live));
    world.created.remove(&id.entity);
    let frame = world.input.processed.unwrap_or(100);
    world.body_script =
        Some(BodyScript::new(body, frame, world.input.processed, &runtime).unwrap());
    let state = PlayerBodyState::new(
        id,
        world.owner,
        0,
        0,
        hero,
        empty_mover(),
        Some(Box::new(runtime)),
        frame,
        None,
    );
    simulation.spawn_player_body(state).unwrap();
}

#[test]
fn queued_hitscan_settles_before_a_hero_or_skin_replacement() {
    use crate::{
        game_combat::ShotContext,
        game_heroes::HeroCatalog,
        game_hitscan::{DamageTags, HitDefinition},
    };

    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u32,
        input: Input,
        expected: Expected,
    }
    #[derive(serde::Deserialize)]
    struct Input {
        heroes: [u64; 2],
        skin: u64,
        target_position: [f64; 3],
        target_health: f64,
        damage: f64,
        input_budgets: [usize; 2],
    }
    #[derive(serde::Deserialize)]
    struct Expected {
        target_health_after_shot: f64,
        generation_after_switch: u64,
    }

    let Contract {
        schema_version,
        input,
        expected,
    } = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/owner-hitscan-before-switch-v1.json"
    ))
    .unwrap();
    assert_eq!(schema_version, 1);
    let [first, second] = input.heroes;
    let heroes = serde_json::json!({
        format!("0x{first:016X}"): {
            "name": "Fixture shooter", "class": "Damage", "body": "0x0400000000000001",
        },
        format!("0x{second:016X}"): {
            "name": "Fixture replacement", "class": "Damage", "body": "0x0400000000000001",
        },
    })
    .to_string();
    let skin = SkinTheme {
        guid: input.skin,
        ..SkinTheme::default()
    };
    let mut card = PlayerCard::default();
    card.skins.insert(first, skin);
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    let target_owner = OWNER + 0x100;
    let target = BodyId {
        entity: target_owner | 1,
        generation: 1,
    };
    let frame = FIRST_TICK + 1;

    for budget in input.input_budgets {
        for change_skin in [false, true] {
            let mut scene = PreparedScene::fixture_for_owner_admission(
                HeroCatalog::from_json(&heroes).unwrap(),
            );
            let damage = input.damage;
            scene.owner_body_factory = Some(Arc::new(move |_, spawn, _| {
                let mut runtime = synthetic_owner(spawn.entity, spawn.frame, Arc::default());
                runtime.queued_hitscan = Some(PosedHitscan {
                    source: spawn.entity,
                    origin: [0.0, 1.0, 0.0],
                    direction: [1.0, 0.0, 0.0],
                    command: Command::default(),
                    shot_frame: 0,
                    reach: 20.0,
                    hit: HitDefinition {
                        tags: DamageTags::EMPTY,
                        amount: damage,
                        loadout: None,
                        critical: 1.0,
                        falloff: None,
                    },
                });
                Ok(Some(Box::new(runtime)))
            }));
            scene
                .simulation_mut()
                .spawn_player_body(fixture_player_state(
                    target,
                    target_owner,
                    1,
                    1,
                    input.target_position,
                    input.target_health,
                    false,
                ))
                .unwrap();

            let mut world = WorldDelivery::new(OWNER);
            world.started = Some(FIRST_TICK);
            world.created.insert(OWNER);
            world.input.processed = Some(FIRST_TICK);
            world
                .controller
                .full_owner(FIRST_TICK, |_| Ok(crate::bits::BitWriter::new()))
                .unwrap();
            let hero = scene.heroes().get(first).unwrap().clone();
            world
                .switch(
                    &mut scene,
                    &viewer,
                    FIRST_TICK,
                    RETAIL_QUANTA,
                    OWNER | 1,
                    (&hero, SkinTheme::default()),
                )
                .unwrap();
            let shot_body = world.active_body_id().unwrap();

            world.queue_commands(
                frame,
                &[Command {
                    frame,
                    buttons: crate::commands::FIRE,
                    ..Command::default()
                }],
            );
            if change_skin {
                world.queue_skin(first, frame, Duration::ZERO);
            } else {
                world.queue_game_message(frame, Duration::ZERO, pick_hero(OWNER, second));
            }
            assert_eq!(
                world
                    .advance_input(&mut scene, &viewer, RETAIL_QUANTA, budget)
                    .unwrap(),
                (1, false)
            );
            assert_eq!(world.command_ack(frame), frame);
            assert_eq!(world.active_body_id(), Some(shot_body));
            let shots = world.take_hitscan(scene.simulation_mut());
            let [(shot, _)] = shots.as_slice() else {
                panic!("expected exactly one pending shot from the old player body");
            };
            assert_eq!((shot.source, shot.shot_frame), (shot_body.entity, frame));
            let (simulation, queries) = scene.bot_simulation();
            let hit = simulation
                .hitscan(
                    &queries,
                    *shot,
                    ShotContext {
                        team: viewer.team,
                        quanta: RETAIL_QUANTA,
                        tick: frame,
                        now: Duration::ZERO,
                    },
                    |entity| entity == target.entity,
                )
                .unwrap()
                .unwrap();
            assert_eq!(hit.damage.target_facts.target, target.entity);

            assert_eq!(
                world
                    .advance_input(&mut scene, &viewer, RETAIL_QUANTA, budget)
                    .unwrap(),
                (1, false)
            );
            assert!(!world.has_input_work());
            let replacement = world.body.unwrap();
            let (expected_hero, expected_skin) = if change_skin {
                (first, skin)
            } else {
                (second, SkinTheme::default())
            };
            assert_eq!(
                (replacement.hero, replacement.skin),
                (expected_hero, expected_skin)
            );
            assert_eq!(replacement.id.generation, expected.generation_after_switch);
            assert!(scene.simulation().player_body(shot_body).is_none());
            assert!(world.take_hitscan(scene.simulation_mut()).is_empty());
            assert_eq!(
                scene
                    .simulation()
                    .player_body(target)
                    .unwrap()
                    .runtime
                    .as_ref()
                    .unwrap()
                    .vitals()
                    .unwrap()
                    .total(),
                expected.target_health_after_shot
            );
        }
    }
}

#[test]
fn successful_hitscan_survives_owner_vm_fault_before_body_replacement() {
    use crate::{
        game_combat::ShotContext,
        game_heroes::HeroCatalog,
        game_hitscan::{DamageTags, HitDefinition},
    };

    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u32,
        input: Input,
        expected: Expected,
    }
    #[derive(serde::Deserialize)]
    struct Input {
        heroes: [u64; 2],
        target_position: [f64; 3],
        target_health: f64,
        shot_damage: f64,
        input_budgets: [usize; 2],
    }
    #[derive(serde::Deserialize)]
    struct Expected {
        hits: usize,
        target_health_after_shot: f64,
        replacement_generation: u64,
    }

    let Contract {
        schema_version,
        input,
        expected,
    } = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/owner-script-fault-hitscan-v1.json"
    ))
    .unwrap();
    assert_eq!(schema_version, 1);
    let [first, next] = input.heroes;
    let heroes = serde_json::json!({
        format!("0x{first:016X}"): {
            "name": "Fixture shooter", "class": "Damage", "body": "0x0400000000000001",
        },
        format!("0x{next:016X}"): {
            "name": "Fixture replacement", "class": "Damage", "body": "0x0400000000000001",
        },
    })
    .to_string();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let target_owner = OWNER + 0x100;
    let target = BodyId {
        entity: target_owner | 1,
        generation: 1,
    };
    let shot_frame = FIRST_TICK + 1;
    let fault_frame = shot_frame + 1;
    let context = ShotContext {
        team: viewer.team,
        quanta: RETAIL_QUANTA,
        tick: fault_frame,
        now: Duration::ZERO,
    };

    for budget in input.input_budgets {
        let mut scene =
            PreparedScene::fixture_for_owner_admission(HeroCatalog::from_json(&heroes).unwrap());
        let amount = input.shot_damage;
        scene.owner_body_factory = Some(Arc::new(move |hero, spawn, _| {
            let mut runtime = synthetic_owner(spawn.entity, spawn.frame, Arc::default());
            if hero.guid == first {
                let shot = PosedHitscan {
                    source: spawn.entity,
                    origin: [0.0, 1.0, 0.0],
                    direction: [1.0, 0.0, 0.0],
                    command: Command::default(),
                    shot_frame: 0,
                    reach: 20.0,
                    hit: HitDefinition {
                        tags: DamageTags::EMPTY,
                        amount,
                        loadout: None,
                        critical: 1.0,
                        falloff: None,
                    },
                };
                runtime.queued_hitscan = Some(shot);
                runtime.hitscan_fault = Some((fault_frame, shot));
            }
            Ok(Some(Box::new(runtime)))
        }));
        scene
            .simulation_mut()
            .spawn_player_body(fixture_player_state(
                target,
                target_owner,
                1,
                1,
                input.target_position,
                input.target_health,
                false,
            ))
            .unwrap();

        let mut world = WorldDelivery::new(OWNER);
        world.started = Some(FIRST_TICK);
        world.created.insert(OWNER);
        world.input.processed = Some(FIRST_TICK);
        world
            .controller
            .full_owner(FIRST_TICK, |_| Ok(crate::bits::BitWriter::new()))
            .unwrap();
        let hero = scene.heroes().get(first).unwrap().clone();
        world
            .switch(
                &mut scene,
                &viewer,
                FIRST_TICK,
                RETAIL_QUANTA,
                OWNER | 1,
                (&hero, SkinTheme::default()),
            )
            .unwrap();
        let shooter = world.active_body_id().unwrap();

        world.queue_commands(
            fault_frame,
            &[shot_frame, fault_frame].map(|frame| Command {
                frame,
                buttons: crate::commands::FIRE,
                ..Command::default()
            }),
        );
        world.queue_game_message(fault_frame, Duration::ZERO, pick_hero(OWNER, next));

        let mut settled = 0;
        for _ in 0..3 {
            if !world.has_input_work() {
                break;
            }
            let (used, leave) = world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, budget)
                .unwrap();
            assert!((1..=budget).contains(&used));
            assert!(!leave);
            for (shot, _) in world.take_hitscan(scene.simulation_mut()) {
                assert_eq!(world.active_body_id(), Some(shooter));
                assert_eq!((shot.source, shot.shot_frame), (shooter.entity, shot_frame));
                let (simulation, queries) = scene.bot_simulation();
                let hit = simulation
                    .hitscan(&queries, shot, context, |entity| entity == target.entity)
                    .unwrap()
                    .unwrap();
                assert_eq!(hit.damage.target_facts.target, target.entity);
                settled += 1;
            }
        }
        assert_eq!(settled, expected.hits, "budget {budget}");
        assert_eq!(world.command_ack(fault_frame), fault_frame);
        let fault = world.owner_script_fault.as_ref().unwrap();
        assert_eq!((fault.id, fault.frame), (shooter, fault_frame));
        assert_eq!(fault.step, Some(BodyStep::Posed));
        assert!(!world.has_input_work());
        let replacement = world.body.unwrap();
        assert_eq!(replacement.hero, next);
        assert_eq!(replacement.id.generation, expected.replacement_generation);
        assert!(scene.simulation().player_body(shooter).is_none());
        assert!(world.take_hitscan(scene.simulation_mut()).is_empty());
        assert_eq!(
            scene
                .simulation()
                .player_body(target)
                .unwrap()
                .vitals()
                .unwrap()
                .total(),
            expected.target_health_after_shot
        );
    }
}

#[test]
fn rejected_owner_replacements_preserve_body_and_input_progress() {
    #[derive(serde::Deserialize)]
    #[serde(rename_all = "snake_case")]
    enum FactoryOutcome {
        FactoryError,
        WrongEntity,
        InvalidInitialEncoding,
        InvalidObserverProjection,
        Ready,
    }
    #[derive(serde::Deserialize)]
    struct Input {
        active_hero: u64,
        candidate_hero: u64,
        active_wire_suffix: u32,
        active_generation: u64,
        factory_outcomes: Vec<FactoryOutcome>,
        observer_graph: serde_json::Value,
    }
    #[derive(serde::Deserialize)]
    struct Expected {
        candidate_wire_suffix: u32,
        successful_generation: u64,
        successful_controller_increment: u32,
    }
    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u32,
        input: Input,
        expected: Expected,
    }

    use crate::bits::BitWriter;
    use crate::game_combat::PlayerBodyEvent;
    use crate::game_heroes::HeroCatalog;
    use crate::script_codecs::PayloadError;
    use crate::script_init::StateKind;
    use crate::script_snapshot_wire::SnapshotError;
    use crate::script_wire::{Value as ScriptValue, WireError};

    let Contract {
        schema_version,
        input,
        expected,
    } = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/owner-admission-transaction-v1.json"
    ))
    .unwrap();
    assert_eq!(schema_version, 1);
    let heroes = HeroCatalog::from_json(
        &serde_json::json!({
            format!("0x{:016X}", input.active_hero): {
                "name": "Active fixture", "class": "Damage", "body": "0x0000000000007110",
            },
            format!("0x{:016X}", input.candidate_hero): {
                "name": "Candidate fixture", "class": "Damage", "body": "0x0000000000007120",
            },
        })
        .to_string(),
    )
    .unwrap();
    let mut scene = PreparedScene::fixture_for_owner_admission(heroes);
    let candidate_wire = OWNER | expected.candidate_wire_suffix;
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(FIRST_TICK);
    world.created.insert(OWNER);
    world.input.processed = Some(FIRST_TICK);
    world.select_open = true;
    world
        .controller
        .full_owner(FIRST_TICK, |_| Ok(BitWriter::new()))
        .unwrap();
    let active = BodyId {
        entity: OWNER | input.active_wire_suffix,
        generation: input.active_generation,
    };
    activate_script(
        &mut world,
        scene.simulation_mut(),
        active,
        input.active_hero,
        Arc::default(),
    );
    world.bound = Some(active);
    world.created.insert(active.entity);
    assert!(scene.simulation_mut().take_player_events().is_empty());

    let attempt_count = input.factory_outcomes.len();
    assert!(attempt_count > 1);
    let factory = Arc::new(Mutex::new(VecDeque::from(input.factory_outcomes)));
    let factory_input = Arc::clone(&factory);
    let candidate_hero = input.candidate_hero;
    let observer_graph = u32::try_from(input.observer_graph["index"].as_u64().unwrap()).unwrap();
    let observer_graphs = Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&serde_json::json!({
                "version": 1, "bodies": {}, "missing": [], "graphs": [input.observer_graph],
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    scene.owner_body_factory = Some(Arc::new(move |hero, spawn, _| {
        assert_eq!((hero.guid, spawn.entity), (candidate_hero, candidate_wire));
        let outcome = factory_input.lock().unwrap().pop_front().unwrap();
        let entity = match outcome {
            FactoryOutcome::FactoryError => return Err(io::Error::other("fixture start error")),
            FactoryOutcome::WrongEntity => spawn.entity + 1,
            _ => spawn.entity,
        };
        let mut runtime = synthetic_owner(entity, spawn.frame, Arc::default());
        if let FactoryOutcome::InvalidInitialEncoding = outcome {
            runtime.component = Component::start(
                Arc::clone(&runtime.graphs),
                entity,
                [(0, ScriptValue::Bool(true))].into_iter().collect(),
                vec![Root::new(1, 42)],
                spawn.frame,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let encoder = runtime.encoder();
            let projected = encoder
                .project(runtime.snapshot().unwrap(), View::Owner)
                .unwrap();
            assert!(matches!(
                encoder.full(&projected),
                Err(SnapshotError::Wire(WireError::InvalidVariable))
            ));
        }
        if let FactoryOutcome::InvalidObserverProjection = outcome {
            runtime.graphs = Arc::clone(&observer_graphs);
            runtime.codecs = CodecTable::from_json(
                r#"{"classes":{"12340001":{"name":"STUStatescriptState","codec":"link",
                    "layout":[],"stec":false,"status":"VERIFIED"}}}"#,
            )
            .unwrap();
            runtime.component = Component::start(
                Arc::clone(&runtime.graphs),
                entity,
                Default::default(),
                vec![Root::new(1, observer_graph)],
                spawn.frame,
                &mut EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            let mut snapshot = runtime.component.snapshot().unwrap().clone();
            let source = snapshot
                .instances
                .get_mut(&1)
                .unwrap()
                .states
                .get_mut(&0)
                .unwrap();
            assert!(source.active);
            source.kind = StateKind::Registered { next: Some((1, 1)) };
            runtime.snapshot_override = Some(snapshot);

            let snapshot = runtime.snapshot().unwrap();
            let encoder = runtime.encoder();
            let owner = encoder.project(snapshot, View::Owner).unwrap();
            let baseline = encoder
                .queue_full(&mut BodyStream::new(entity), &owner)
                .unwrap();
            let packet = frame(u32::MAX, u32::MAX, &[], &[], &[&baseline.update]).unwrap();
            assert!(packet.len() <= MAX_PAYLOAD);
            assert!(matches!(
                encoder.project(snapshot, View::Observer),
                Err(SnapshotError::Payload { site, source: PayloadError::Field(field) })
                    if site.instance == 1 && site.graph == observer_graph && site.state == 0
                        && field == "link"
            ));
        }
        Ok(Some(Box::new(runtime)))
    }));
    let controller_data = world.controller.latest_data();
    let controller_frame = world.controller.command_frame();

    for remaining in (1..attempt_count).rev() {
        world.queue_game_message(FIRST_TICK, Duration::ZERO, pick_hero(OWNER, candidate_hero));
        assert_eq!(
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap(),
            (1, false)
        );
        assert_eq!(factory.lock().unwrap().len(), remaining);
        assert_eq!(world.body.unwrap().id, active);
        assert_eq!(world.body_script.as_ref().unwrap().id, active);
        assert_eq!(world.bound, Some(active));
        assert!(world.select_open);
        assert!(world.created.contains(&active.entity));
        assert_eq!(world.next_body(Duration::ZERO), Some(candidate_wire));
        assert!(world.queued.is_empty());
        assert_eq!(world.controller.latest_data(), controller_data);
        assert_eq!(world.controller.command_frame(), controller_frame);
        assert!(scene.simulation_mut().take_player_events().is_empty());
        let active_state = scene.simulation().player_body_by_owner(OWNER).unwrap();
        assert_eq!(active_state.id, active);
        assert!(active_state.runtime.is_some());
    }

    let command_frame = FIRST_TICK + 1;
    world.queue_commands(command_frame, &[forward(command_frame)]);
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (1, false)
    );
    assert_eq!(world.input.processed, Some(command_frame));
    assert_eq!(world.body_script.as_ref().unwrap().frame, command_frame);
    assert_eq!(
        scene.simulation().player_body(active).unwrap().frame,
        command_frame
    );

    world.queue_game_message(
        command_frame,
        Duration::ZERO,
        pick_hero(OWNER, candidate_hero),
    );
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (1, false)
    );
    assert!(factory.lock().unwrap().is_empty());
    let replacement = BodyId {
        entity: candidate_wire,
        generation: expected.successful_generation,
    };
    assert_eq!(world.body.unwrap().id, replacement);
    assert_eq!(world.body_script.as_ref().unwrap().id, replacement);
    assert_eq!(
        world.controller.latest_data(),
        controller_data + expected.successful_controller_increment
    );
    assert!(scene.simulation().player_body(active).is_none());
    assert!(
        scene
            .simulation()
            .player_body(replacement)
            .unwrap()
            .runtime
            .is_some()
    );
    assert!(matches!(
        scene.simulation_mut().take_player_events().as_slice(),
        [PlayerBodyEvent::Retired { id: retired, .. }, PlayerBodyEvent::Created { id: created, .. }]
            if *retired == active && *created == replacement
    ));
}

fn step_player(
    world: &mut WorldDelivery,
    simulation: &mut RoomSimulation,
    rays: &TriangleIndex,
    id: BodyId,
    command: Command,
) {
    let state = simulation.player_body_mut(id).unwrap();
    let PlayerBodyState {
        mover,
        runtime,
        frame,
        fields,
        launches,
        hitscan,
        ..
    } = state;
    let script = world.body_script.as_mut().unwrap();
    let mut buffers = StepBuffers {
        frame,
        fields,
        launches,
        hitscan,
    };
    mover
        .step(rays, command, 0.016, |mover, command| {
            script
                .advance_step(
                    runtime.as_deref_mut().unwrap(),
                    mover,
                    &mut buffers,
                    command,
                    BodyStep::Posed,
                )
                .unwrap();
        })
        .unwrap();
}

fn transmit(
    world: &mut WorldDelivery,
    simulation: Option<&mut RoomSimulation>,
    egress: &mut GameEgress<Arc<Record>>,
    tick: u32,
    sequence: u32,
) -> (Vec<u8>, SentFrame) {
    let mut bytes = Vec::new();
    let sent = world
        .send_frame(tick, Vec::new(), false, simulation, egress, |payload| {
            bytes = payload;
            Ok::<_, ()>(sequence)
        })
        .unwrap();
    (bytes, sent)
}

fn transport_outcome(
    world: &mut WorldDelivery,
    egress: &mut GameEgress<Arc<Record>>,
    sequence: u32,
    arrived: bool,
) {
    world.acknowledge(
        egress.acknowledge(sequence + 1, u64::from(arrived)),
        Duration::ZERO,
    );
}

#[test]
fn projectile_retirement_retries_destroys_and_ignores_stale_confirmations() {
    use crate::script_init::ShotEvent;

    const DEFINITION: u64 = 0x0400_0000_0000_072c;
    let id = avatar(1, 1);
    let mut world = WorldDelivery::new(OWNER);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    world.started = Some(100);
    activate_script(&mut world, &mut simulation, id, SOLDIER, Arc::default());
    let leases = ProjectileLeases::new(0, 100, [DEFINITION]).unwrap();
    let alias = leases.entity(DEFINITION, 101, 0).unwrap();
    let mut projectiles = OwnerProjectiles::new(id.entity, leases);
    let token = projectiles
        .admit(
            ShotEvent {
                entity: id.entity,
                instance: 1,
                graph: 42,
                raw_state: 0,
                frame: 101,
                time_ms: 101 * FRAME_MS,
                activation: 1,
                activation_ms: 101 * FRAME_MS,
                ordinal: 0,
            },
            DEFINITION,
            125,
        )
        .unwrap();
    let confirmation = projectiles.advance(133);
    world.body_script.as_mut().unwrap().projectiles = Some(projectiles);
    world.queue_projectiles(id, confirmation).unwrap();
    let old = Arc::clone(&world.queued[0]);
    assert!(
        matches!(old.delivery, Delivery::ProjectileConfirm { id: source, token: key }
            if source == id && key == token)
    );
    assert!(eligible(&world).is_empty());
    world.created.insert(id.entity);
    world.bound = Some(id);
    assert_eq!(eligible(&world), [0]);
    world.sent(&[0]);

    world.cancel_body_script(Some(&mut simulation));
    activate_script(
        &mut world,
        &mut simulation,
        avatar(2, 2),
        SOLDIER,
        Arc::default(),
    );
    world.acknowledge(outcome(old, false), Duration::ZERO);
    let destroy = Arc::clone(&world.queued[0]);
    assert_eq!(world.queued.len(), 1);
    assert_eq!(destroy.update.entity, alias);
    assert!(destroy.update.ch2.is_none());
    assert!(matches!(destroy.update.operation, Some(Operation::Destroy)));
    world.sent(&[0]);
    world.acknowledge(outcome(Arc::clone(&destroy), false), Duration::ZERO);
    assert_eq!(world.queued.len(), 1);
    world.sent(&[0]);
    world.acknowledge(outcome(destroy, true), Duration::ZERO);
    assert!(world.queued.is_empty());
    assert!(world.projectile_destroys.is_empty());
}

#[test]
fn admitted_owner_projectile_preserves_gravity_and_expiry_blast() {
    use crate::{
        game_hitscan::{HitDefinition, TargetPose},
        projectile_flight::{Blast, ProjectileBallistics, lifted_direction},
        script_init::ShotEvent,
    };
    const DEFINITION: u64 = 0x0400_0000_0000_037d;
    let id = avatar(1, 2);
    let mut world = WorldDelivery::new(OWNER);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    activate_script(&mut world, &mut simulation, id, CASSIDY, Arc::default());
    let script = world.body_script.as_mut().unwrap();
    script.projectiles = Some(OwnerProjectiles::new(
        id.entity,
        ProjectileLeases::new(0, 100, [DEFINITION]).unwrap(),
    ));
    let launches = &mut simulation.player_body_mut(id).unwrap().launches;
    script.launch_projectile(
        PosedProjectile {
            stun: crate::game_combat::Stun::from_seconds(0.8),
            shot: ShotEvent {
                entity: id.entity,
                instance: 1,
                graph: 0x01e9,
                raw_state: 1,
                frame: 101,
                time_ms: 101 * FRAME_MS,
                activation: 1,
                activation_ms: 101 * FRAME_MS,
                ordinal: 0,
            },
            ballistics: ProjectileBallistics {
                speed: 30.0,
                lifetime: 7.0 / 30.0,
                max_distance: None,
                terrain_rebound: None,
                terrain_radius: 0.0,
                radius: 0.0,
                definition: Some(DEFINITION),
                direct: None,
                blast: Some(Blast {
                    hit: HitDefinition {
                        tags: crate::game_hitscan::DamageTags::EMPTY,
                        amount: 25.0,
                        loadout: None,
                        critical: 1.0,
                        falloff: None,
                    },
                    rings: vec![(3.0, 1.0)],
                    self_damage_scale: 0.0,
                    self_knockback_scale: 0.0,
                    knockback: None,
                    detonate_on_expiry: true,
                }),
            },
            origin: [0.0, 1.55, 0.0],
            direction: lifted_direction([0.0, 0.0, 1.0], 0.05).unwrap(),
            gravity: Some(20.0),
            predicted: true,
            target_policy: Default::default(),
            piercing: false,
            impact_spawn: None,
            post_bounce_fuse: None,
            sticky_delay: None,
            direct_effect: None,
            blast_effect: None,
            presentation: None,
        },
        launches,
    );
    let (_, mut launches) = world.take_projectile_launches(&mut simulation).unwrap();
    let [launch] = launches.as_mut_slice() else {
        panic!("one admitted flight")
    };
    assert_eq!(launch.stun.unwrap().ticks, 50);
    let targets = [(
        42,
        TargetPose {
            feet: [2.0, 0.0, 7.0],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
    )];
    let mut headings = Vec::new();
    let end = (1..=30)
        .find_map(|tick| {
            launch
                .flight
                .advance(tick, &targets, |_, direction, _| {
                    headings.push(direction[1]);
                    Ok(None)
                })
                .unwrap()
        })
        .expect("range-end blast");
    assert!(end.expired);
    assert!(
        headings.iter().any(|y| *y < -0.05),
        "gravity was lost at admission"
    );
    assert_eq!(
        end.impacts
            .iter()
            .map(|hit| (hit.entity, hit.damage))
            .collect::<Vec<_>>(),
        [(42, 25.0)]
    );
}

#[test]
fn room_flight_outcomes_release_only_the_current_owner_alias() {
    use crate::{projectile_flight::ProjectileBallistics, script_init::ShotEvent};

    const DEFINITION: u64 = 0x0400_0000_0000_072c;
    let id = avatar(1, 2);
    for completed in [false, true] {
        let mut world = WorldDelivery::new(OWNER);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        world.started = Some(100);
        activate_script(&mut world, &mut simulation, id, SOLDIER, Arc::default());
        let leases = ProjectileLeases::new(0, 100, [DEFINITION]).unwrap();
        let alias = leases.entity(DEFINITION, 101, 0).unwrap();
        let script = world.body_script.as_mut().unwrap();
        script.projectiles = Some(OwnerProjectiles::new(id.entity, leases));
        let launches = &mut simulation.player_body_mut(id).unwrap().launches;
        script.launch_projectile(
            PosedProjectile {
                stun: None,
                gravity: None,
                predicted: true,
                shot: ShotEvent {
                    entity: id.entity,
                    instance: 1,
                    graph: 42,
                    raw_state: 0,
                    frame: 101,
                    time_ms: 101 * FRAME_MS,
                    activation: 1,
                    activation_ms: 101 * FRAME_MS,
                    ordinal: 0,
                },
                ballistics: ProjectileBallistics {
                    speed: 1.0,
                    lifetime: 2.0,
                    max_distance: None,
                    terrain_rebound: None,
                    terrain_radius: 0.0,
                    radius: 0.0,
                    definition: Some(DEFINITION),
                    direct: None,
                    blast: None,
                },
                origin: [0.0; 3],
                direction: [1.0, 0.0, 0.0],
                target_policy: Default::default(),
                piercing: false,
                impact_spawn: None,
                post_bounce_fuse: None,
                sticky_delay: None,
                direct_effect: None,
                blast_effect: None,
                presentation: None,
            },
            launches,
        );
        let (owner, launches) = world.take_projectile_launches(&mut simulation).unwrap();
        assert_eq!(owner, id);
        let [launch] = launches.as_slice() else {
            panic!("one accepted flight");
        };
        let token = launch.token;
        assert!(
            world
                .take_projectile_launches(&mut simulation)
                .unwrap()
                .1
                .is_empty()
        );
        world
            .finish_projectiles(avatar(1, 1), 133, vec![FlightOutcome::Failed(token)])
            .unwrap();
        assert!(world.queued.is_empty());

        world.finish_projectiles(id, 133, Vec::new()).unwrap();
        let confirm = Arc::clone(&world.queued[0]);
        assert_eq!(confirm.update.entity, alias);
        assert!(matches!(
            confirm.delivery,
            Delivery::ProjectileConfirm { .. }
        ));
        world.created.insert(id.entity);
        world.bound = Some(id);
        world.sent(&[0]);
        world.acknowledge(outcome(confirm, true), Duration::ZERO);

        let finished = if completed {
            FlightOutcome::Ended {
                token,
                travelled: 34.25 * TICK_PERIOD.as_secs_f64(),
                impact: true,
            }
        } else {
            FlightOutcome::Failed(token)
        };
        world.finish_projectiles(id, 134, vec![finished]).unwrap();
        if completed {
            world.finish_projectiles(id, 137, Vec::new()).unwrap();
            assert!(world.queued.is_empty());
            world.finish_projectiles(id, 138, Vec::new()).unwrap();
        }
        let [release] = world.queued.as_slice() else {
            panic!("one predicted alias retirement");
        };
        assert_eq!(release.update.entity, alias);
        assert!(matches!(release.update.operation, Some(Operation::Destroy)));
        assert_eq!(release.update.ch2.is_some(), completed);
        if let Some(bits) = &release.update.ch2 {
            let mut reader = BitReader::new(bits.as_bytes());
            assert!(!reader.bit().unwrap());
            assert!(reader.bit().unwrap());
            assert_eq!(reader.var_b().unwrap(), u32::MAX);
            assert_eq!(reader.var_b().unwrap(), 138 + 4096 + 1);
        }
    }
}

#[test]
fn owner_health_waits_for_creation_and_discards_superseded_lifetimes() {
    use crate::world::{ARMOUR_POOL, POOLS, health, pool, pool_component};

    let id = avatar(1, 1);
    let mut world = WorldDelivery::new(OWNER);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    world.started = Some(100);
    let body_value = body(id);
    world.body = Some(body_value);
    world
        .bodies
        .insert(id.entity, (id.generation, Lifetime::Live));
    let runtime = synthetic_owner(id.entity, 100, Arc::default());
    let pending = Arc::clone(&runtime.health_update);
    world.body_script = Some(BodyScript::new(body_value, 100, None, &runtime).unwrap());
    simulation
        .spawn_player_body(PlayerBodyState::new(
            id,
            OWNER,
            0,
            0,
            body_value.hero,
            empty_mover(),
            Some(Box::new(runtime)),
            100,
            None,
        ))
        .unwrap();
    world.push(record(id.entity, Delivery::CreateBody(id)));

    let mut egress = GameEgress::default();
    let first = health(160.0, 200.0, 50.0, 0.0);
    *pending.lock().unwrap() = Some(first.clone());
    let sent = world
        .send_frame(
            100,
            Vec::new(),
            false,
            Some(&mut simulation),
            &mut egress,
            |_| Ok::<_, ()>(0),
        )
        .unwrap();
    assert_eq!(sent.records, [RecordSource::Queued(0)]);
    assert!(world.health_pending(id.entity));
    assert!(world.ready().is_empty());
    transport_outcome(&mut world, &mut egress, 0, true);

    let old = world.ready()[0].tracked();
    assert_eq!(old.update.operation, Some(Operation::Update(vec![first])));
    world.sent(&[0]);
    let latest = pool_component([
        (0, pool(1, 0, 200.0, 120.0)),
        (ARMOUR_POOL * POOLS, [0; 20]),
    ]);
    *pending.lock().unwrap() = Some(latest.clone());
    let rejected = world.send_frame(
        101,
        Vec::new(),
        false,
        Some(&mut simulation),
        &mut egress,
        |_| Err::<u32, _>(()),
    );
    assert!(matches!(rejected, Err(SendError::Transport(()))));
    world.acknowledge(outcome(Arc::clone(&old), false), Duration::ZERO);
    world.acknowledge(outcome(old, true), Duration::ZERO);
    assert!(world.health_pending(id.entity));
    let mut updates = world
        .ready()
        .into_iter()
        .filter(|item| matches!(item.record.delivery, Delivery::Health { .. }));
    let ready = updates.next().expect("latest health revision");
    assert!(updates.next().is_none());
    let latest_record = ready.tracked();
    assert_eq!(
        latest_record.update.operation,
        Some(Operation::Update(vec![latest]))
    );

    world.sent(&[ready.index]);
    world.body = Some(body(avatar(2, 2)));
    world.cancel_body_script(Some(&mut simulation));
    world.retire_body(id);
    world.remove_stale();
    world.queue_health(latest_record.update.clone());
    world.acknowledge(outcome(latest_record, false), Duration::ZERO);
    assert!(!world.health_pending(id.entity));
    assert!(!world.identity_ready(id.entity));
    let [destroy] = world.queued.as_slice() else {
        panic!("expected only the retired body's destroy record");
    };
    assert!(matches!(destroy.delivery, Delivery::DestroyBody(retired) if retired == id));
}

#[test]
fn body_script_retries_frozen_initial_frames_and_ignores_old_generation_reports() {
    let mut world = WorldDelivery::new(OWNER);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    let id = avatar(1, 1);
    world.started = Some(100);
    activate_script_without_movement_projection(&mut world, &mut simulation, id, 7, Arc::default());
    world.push(record(id.entity, Delivery::CreateBody(id)));
    let mut egress = GameEgress::default();
    assert_eq!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 100, 0)
            .1
            .records,
        [RecordSource::Queued(0)]
    );
    assert!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 100, 1)
            .1
            .records
            .is_empty()
    );
    transport_outcome(&mut world, &mut egress, 0, true);
    let (h0, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, 101, 2);
    assert_eq!(sent.records, [RecordSource::Queued(0)]);
    transport_outcome(&mut world, &mut egress, 2, false);
    assert_eq!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 101, 3).0,
        h0
    );
    transport_outcome(&mut world, &mut egress, 3, true);
    assert_eq!(
        world.body_script.as_ref().unwrap().stream.base().unwrap().0,
        1
    );

    let blocked = transmit(&mut world, Some(&mut simulation), &mut egress, 101, 4).1;
    assert!(blocked.records.is_empty());
    let rays = TriangleIndex::new([]).unwrap();
    let command = forward(101);
    step_player(&mut world, &mut simulation, &rays, id, command);
    assert!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 101, 5)
            .1
            .records
            .is_empty()
    );
    let stream = &world.body_script.as_ref().unwrap().stream;
    assert_eq!(
        stream.pending().map(|(last, ..)| last).collect::<Vec<_>>(),
        [3]
    );
    assert_eq!(stream.command_frame(), 101);

    world.push(record(OWNER, Delivery::Possession(id)));
    assert_eq!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 101, 6)
            .1
            .records,
        [RecordSource::Queued(0)]
    );
    transport_outcome(&mut world, &mut egress, 6, true);
    let (c0, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, 102, 7);
    assert_eq!(sent.records, [RecordSource::Queued(0)]);
    let command = forward(117);
    step_player(&mut world, &mut simulation, &rays, id, command);
    assert!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 102, 8)
            .1
            .records
            .is_empty()
    );
    transport_outcome(&mut world, &mut egress, 7, false);
    assert_eq!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 102, 9).0,
        c0
    );
    transport_outcome(&mut world, &mut egress, 9, true);
    assert_eq!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 117, 10)
            .1
            .records,
        [RecordSource::Queued(0)]
    );
    transport_outcome(&mut world, &mut egress, 10, false);
    assert!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 118, 11)
            .1
            .records
            .is_empty()
    );

    step_player(&mut world, &mut simulation, &rays, id, forward(118));
    assert_eq!(
        transmit(&mut world, Some(&mut simulation), &mut egress, 118, 12)
            .1
            .records,
        [RecordSource::Queued(0)]
    );
    world.cancel_body_script(Some(&mut simulation));
    let reused = avatar(1, 257);
    activate_script(&mut world, &mut simulation, reused, 7, Arc::default());
    transport_outcome(&mut world, &mut egress, 12, true);
    assert_eq!(world.body_script.as_ref().unwrap().stream.last(), 0);
    assert!(world.queued.is_empty());
}

fn read_ack(payload: &[u8]) -> u32 {
    let mut bits = BitReader::new(payload);
    bits.var_a().unwrap();
    assert!(!bits.bit().unwrap());
    bits.var_a().unwrap()
}

#[test]
fn retirement_waits_for_the_reliable_notice_across_retransmission() {
    let body = avatar(1, 1);
    let other = avatar(2, 1);
    let mut world = WorldDelivery::new(OWNER);
    let mut egress = GameEgress::default();
    world.started = Some(100);
    for id in [body, other] {
        world
            .bodies
            .insert(id.entity, (id.generation, Lifetime::Live));
        world.created.insert(id.entity);
    }
    let notice = egress
        .queue_reliable(crate::game_messages::EncodedMessage::from_bytes(vec![0x42]))
        .unwrap();
    world.track_entity_notice(body.entity, notice);

    world.retire_body(body);
    world.retire_body(other);
    assert!(world.pending_entity_notice(body.entity, &egress));
    let (_, first) = transmit(&mut world, None, &mut egress, 100, 0);
    // The other entity can retire in the same packet that carries the notice.
    assert_eq!(first.records, [RecordSource::Queued(0)]);
    assert_eq!(world.queued.len(), 1);
    assert_eq!(world.queued[0].update.entity, body.entity);

    // Assume the retirement's datagram was lost. Its reliable ordinal is
    // still owed after retransmission, and only the unrelated destroy may retry.
    transport_outcome(&mut world, &mut egress, first.sequence, false);
    let (_, retry) = transmit(&mut world, None, &mut egress, 101, 1);
    assert_eq!(retry.records, [RecordSource::Queued(0)]);
    assert_eq!(world.queued.len(), 1);
    assert_eq!(world.queued[0].update.entity, body.entity);
    assert!(!egress.delivered_through(notice));

    transport_outcome(&mut world, &mut egress, retry.sequence, true);
    assert!(egress.delivered_through(notice));
    let (_, last) = transmit(&mut world, None, &mut egress, 102, 2);
    assert_eq!(last.records, [RecordSource::Queued(0)]);
    assert!(world.queued.is_empty());
}

#[test]
fn command_ack_survives_replacing_a_body_without_a_correction_history() {
    let tick = 100;
    let processed = 125;
    let mut world = WorldDelivery::new(OWNER);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    let mut egress = GameEgress::default();
    world.started = Some(tick);
    activate_script_without_movement_projection(
        &mut world,
        &mut simulation,
        avatar(1, 1),
        7,
        Arc::default(),
    );
    world.input.processed = Some(processed);
    let (previous, _) = transmit(&mut world, Some(&mut simulation), &mut egress, tick, 0);
    assert_eq!(read_ack(&previous), processed);

    let replacement = avatar(2, 2);
    activate_script(
        &mut world,
        &mut simulation,
        replacement,
        SOLDIER,
        Arc::default(),
    );
    let script = world.body_script.as_ref().unwrap();
    assert_eq!(script.frame, processed);
    assert_eq!(script.corrections.as_ref().unwrap().corrected(), None);
    let (next, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, tick + 1, 1);
    assert_eq!(read_ack(&next), processed);
    assert!(sent.records.is_empty());

    let first_new_input = processed + 1;
    let history = world
        .body_script
        .as_mut()
        .unwrap()
        .corrections
        .as_mut()
        .unwrap();
    let pose = |frame| Movement {
        frame: Some(frame),
        ..Movement::default()
    };
    assert!(!history.record(pose(processed - 1)));
    assert!(history.record(pose(first_new_input)));
    world.input.processed = Some(first_new_input);
    world.acknowledge(
        outcome(
            Arc::new(record(
                replacement.entity,
                Delivery::CreateBody(replacement),
            )),
            true,
        ),
        Duration::ZERO,
    );
    let (next, sent) = transmit(
        &mut world,
        Some(&mut simulation),
        &mut egress,
        first_new_input,
        2,
    );
    assert_eq!(read_ack(&next), first_new_input);
    assert!(sent.records.contains(&RecordSource::Movement(0)));
}

#[test]
fn owner_correction_respects_create_budget_and_failed_transport() {
    for hero in [SOLDIER, CASSIDY, 0x02e0_0000_0000_0002] {
        let mut world = WorldDelivery::new(OWNER);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let id = avatar(1, 1);
        world.started = Some(100);
        let failed = Arc::new(AtomicBool::new(false));
        activate_script(&mut world, &mut simulation, id, hero, Arc::clone(&failed));
        world.input.processed = Some(125);
        let history = world
            .body_script
            .as_mut()
            .unwrap()
            .corrections
            .as_mut()
            .unwrap();
        history.record(Movement {
            frame: Some(100),
            ..Movement::default()
        });
        let mut egress = GameEgress::default();
        let (blocked, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, 100, 0);
        assert_eq!(read_ack(&blocked), 102);
        assert!(sent.records.is_empty());
        world.acknowledge(
            outcome(Arc::new(record(id.entity, Delivery::CreateBody(id))), true),
            Duration::ZERO,
        );
        let (first, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, 100, 1);
        assert_eq!(read_ack(&first), 102);
        assert!(sent.records.contains(&RecordSource::Movement(0)));
        transport_outcome(&mut world, &mut egress, 1, true);
        let history = world
            .body_script
            .as_mut()
            .unwrap()
            .corrections
            .as_mut()
            .unwrap();
        assert_eq!(history.corrected(), Some(100));
        history.record(Movement {
            frame: Some(106),
            ..Movement::default()
        });
        history.record(Movement {
            frame: Some(112),
            ..Movement::default()
        });
        for i in 0..MAX_ENTITY_RECORDS {
            world.push(Record::baseline(EntityUpdate {
                entity: 0x8000_0100 + i as u32,
                operation: Some(Operation::Destroy),
                ..EntityUpdate::default()
            }));
        }
        let (crowded, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, 107, 2);
        assert_eq!(read_ack(&crowded), 102);
        assert_eq!(sent.records.len(), MAX_ENTITY_RECORDS);
        assert!(!sent.records.contains(&RecordSource::Movement(0)));
        transport_outcome(&mut world, &mut egress, 2, true);
        let rejected = world.send_frame(
            107,
            Vec::new(),
            false,
            Some(&mut simulation),
            &mut egress,
            |payload| {
                assert_eq!(read_ack(&payload), 108);
                Err(())
            },
        );
        assert!(matches!(rejected, Err(SendError::Transport(()))));
        assert_eq!(
            world
                .body_script
                .as_ref()
                .unwrap()
                .corrections
                .as_ref()
                .unwrap()
                .corrected(),
            Some(100)
        );
        let (selected, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, 107, 3);
        assert_eq!(read_ack(&selected), 108);
        assert_eq!(sent.records, [RecordSource::Movement(0)]);
        assert!(egress.acknowledge(4, 0).packets.is_empty());
        assert!(
            transmit(&mut world, Some(&mut simulation), &mut egress, 107, 4)
                .1
                .records
                .is_empty()
        );

        failed.store(true, Ordering::Relaxed);
        let (payload, sent) = transmit(&mut world, Some(&mut simulation), &mut egress, 112, 5);
        assert_eq!(read_ack(&payload), 125);
        assert!(world.body_script.is_none());
        assert!(sent.records.is_empty());
        assert!(world.queued.is_empty());
    }
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack without bot VM graphs"]
fn prepared_worker_resumes_unposed_vm_frames_before_advancing_physics() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let (factory, _) = crate::script_player::fixture_body_factory();
    scene.owner_body_factory = Some(Arc::new(move |_, spawn, _| {
        Ok(Some(Box::new(factory.start(spawn)?)))
    }));
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(FIRST_TICK);
    let hero = scene.heroes().get(SOLDIER).unwrap().clone();
    world
        .switch(
            &mut scene,
            &viewer,
            FIRST_TICK,
            RETAIL_QUANTA,
            OWNER | 1,
            (&hero, SkinTheme::default()),
        )
        .unwrap();
    let tick = FIRST_TICK + 1;
    world.queue_commands(tick, &[forward(tick)]);
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (1, false)
    );
    assert_eq!(world.body_script.as_ref().unwrap().frame, tick);
    let mut egress = GameEgress::default();
    assert!(
        !transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            tick,
            0
        )
        .1
        .records
        .contains(&RecordSource::Movement(0))
    );
    transport_outcome(&mut world, &mut egress, 0, true);
    assert_eq!(
        transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            tick,
            1
        )
        .1
        .records,
        [RecordSource::Queued(0), RecordSource::Movement(0)]
    );
    transport_outcome(&mut world, &mut egress, 1, true);

    let after_unposed_gap = tick + 400;
    world.queue_commands(after_unposed_gap, &[forward(after_unposed_gap)]);
    for turn in 1..=16 {
        assert_eq!(
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap(),
            (16, false)
        );
        assert_eq!(
            world.body_script.as_ref().unwrap().frame,
            tick + 141 + turn * 16
        );
        assert_eq!(
            scene
                .simulation()
                .player_body_by_owner(OWNER)
                .unwrap()
                .mover
                .pose()
                .frame,
            Some(tick)
        );
        assert_eq!(world.command_ack(after_unposed_gap), tick);
        assert!(
            transmit(
                &mut world,
                Some(scene.simulation_mut()),
                &mut egress,
                after_unposed_gap,
                turn + 1,
            )
            .1
            .records
            .is_empty()
        );
        world.queue_commands(after_unposed_gap, &[forward(after_unposed_gap)]);
    }
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (3, false)
    );
    assert!(!world.has_input_work());
    assert_eq!(world.command_ack(after_unposed_gap), after_unposed_gap);
    assert_eq!(world.body_script.as_ref().unwrap().frame, after_unposed_gap);
    assert_eq!(
        scene
            .simulation()
            .player_body_by_owner(OWNER)
            .unwrap()
            .mover
            .pose()
            .frame,
        Some(after_unposed_gap)
    );
    assert_eq!(
        transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            after_unposed_gap,
            18,
        )
        .1
        .records,
        [RecordSource::Queued(0), RecordSource::Movement(0)]
    );
}

#[test]
fn possession_and_retirement_wait_when_budget_splits_the_prerequisites() {
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(1001);
    let previous = avatar(1, 1);
    let current = avatar(2, 2);
    world.body = Some(body(current));
    world
        .bodies
        .insert(previous.entity, (1, Lifetime::Retiring));
    world.bodies.insert(current.entity, (2, Lifetime::Live));
    world.push(record(current.entity, Delivery::CreateBody(current)));
    world.push(record(OWNER, Delivery::Possession(current)));
    world.push(record(previous.entity, Delivery::DestroyBody(previous)));
    assert_eq!(eligible(&world), [0, 1, 2]);

    let create = world.ready()[0].tracked();
    world.sent(&[0]); // The packet budget only fit the create.
    assert!(eligible(&world).is_empty());
    world.acknowledge(outcome(Arc::clone(&create), false), Duration::ZERO);
    assert_eq!(eligible(&world), [0, 1, 2]);
    world.sent(&[0]);
    world.acknowledge(outcome(create, true), Duration::ZERO);
    assert_eq!(eligible(&world), [0, 1]);

    let possess = world.ready()[0].tracked();
    world.sent(&[0]);
    assert!(eligible(&world).is_empty());
    world.acknowledge(outcome(possess, true), Duration::ZERO);
    assert_eq!(eligible(&world), [0]);
    let destroy = world.ready()[0].tracked();
    world.sent(&[0]);
    let now = Duration::from_secs(5);
    world.acknowledge(outcome(destroy, true), now);
    assert!(matches!(
        world.bodies[&previous.entity].1,
        Lifetime::ReusableAt(at) if at == now + BODY_REUSE_DELAY
    ));
}

#[test]
fn a_late_ack_or_loss_cannot_resurrect_a_retired_or_reused_body() {
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(1001);
    let old = avatar(1, 1);
    let reused = avatar(1, 257);
    world.body = Some(body(reused));
    world
        .bodies
        .insert(reused.entity, (reused.generation, Lifetime::Live));
    let stale = Arc::new(record(old.entity, Delivery::CreateBody(old)));
    world.acknowledge(outcome(Arc::clone(&stale), true), Duration::ZERO);
    world.acknowledge(outcome(stale, false), Duration::ZERO);
    assert!(!world.created.contains(&reused.entity));
    assert!(world.queued.is_empty());
    world.acknowledge(
        outcome(Arc::new(record(OWNER, Delivery::Possession(old))), true),
        Duration::ZERO,
    );
    assert!(world.bound.is_none());
}

#[test]
fn ring_reuses_only_acknowledged_destroy_after_the_retention_window() {
    let mut world = WorldDelivery::new(OWNER);
    world.body = Some(body(avatar(255, 255)));
    for suffix in 1..=255 {
        world
            .bodies
            .insert(OWNER | suffix, (u64::from(suffix), Lifetime::Live));
    }
    let target = avatar(1, 1);
    world.bodies.insert(target.entity, (1, Lifetime::Retiring));
    assert_eq!(world.next_body(Duration::from_secs(1000)), None);
    world
        .bodies
        .insert(target.entity, (1, Lifetime::ReusableAt(BODY_REUSE_DELAY)));
    assert_eq!(
        world.next_body(BODY_REUSE_DELAY - Duration::from_millis(1)),
        None
    );
    assert_eq!(world.next_body(BODY_REUSE_DELAY), Some(target.entity));
}

fn fixture_ground() -> crate::collision_scene::CollisionScene {
    use crate::{
        collision_cache::{CollisionCache, Mesh, Triangle},
        collision_scene::CollisionScene,
    };

    let cache = CollisionCache {
        header: serde_json::from_str(
            r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
        )
        .unwrap(),
        meshes: vec![Mesh {
            vertices: vec![[-1.0, 0.0, 0.0], [-1.0, 2.0, 0.0], [-1.0, 0.0, 1.0]],
            triangles: vec![Triangle {
                corners: [0, 1, 2],
                wings: [-1; 3],
                mask: 0x18,
                surface: 0,
            }],
        }],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    };
    CollisionScene::movement_probe(&cache, &Default::default()).unwrap()
}

fn fixture_player_state(
    id: BodyId,
    owner: u32,
    slot: u8,
    team: u8,
    position: [f64; 3],
    health: f64,
    with_observer_create: bool,
) -> PlayerBodyState {
    use crate::vitals::Vitals;

    let runtime = synthetic_owner_with_vitals(
        id.entity,
        100,
        Arc::default(),
        Some(Vitals::new(health, 0.0, 0.0)),
    );
    let observer_create = with_observer_create.then(|| EntityUpdate {
        entity: id.entity,
        operation: Some(Operation::Create(Box::new(crate::world::Create::new(
            1,
            Vec::new(),
        )))),
        ..EntityUpdate::default()
    });
    PlayerBodyState::new(
        id,
        owner,
        slot,
        team,
        SOLDIER,
        mover_at(position),
        Some(Box::new(runtime)),
        100,
        observer_create,
    )
}

#[test]
fn late_join_delivers_existing_player_at_the_current_authoritative_pose() {
    use crate::{
        game_body::yaw_rotation, game_combat::PlayerBodyEvent, game_heroes::HeroCatalog,
        vitals::Death,
    };

    #[derive(serde::Deserialize)]
    struct Input {
        spawn_frame: u32,
        join_frame: u32,
        update_frame: u32,
        spawn_position: [f64; 3],
        join_position: [f64; 3],
        join_yaw: i32,
        update_position: [f64; 3],
        health: f64,
        damage: f64,
    }
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/late-join-observer-create-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let case: Input = serde_json::from_value(fixture["input"].clone()).unwrap();
    let existing_owner = OWNER + 0x100;
    let existing = BodyId {
        entity: existing_owner | 1,
        generation: 1,
    };
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };

    let mut scene = PreparedScene::fixture_for_owner_admission(HeroCatalog::default());
    let mut state = fixture_player_state(
        existing,
        existing_owner,
        1,
        1,
        case.spawn_position,
        case.health,
        true,
    );
    let initial_health = state.vitals().unwrap().component();
    let Some(Operation::Create(create)) = state
        .observer_create
        .as_mut()
        .and_then(|update| update.operation.as_mut())
    else {
        panic!("missing synthetic body create");
    };
    create.position = case.spawn_position.map(|axis| axis as f32);
    create.components.push(initial_health.clone());
    state.frame = case.spawn_frame;

    scene.simulation_mut().spawn_player_body(state).unwrap();
    let events = scene.simulation_mut().take_player_events();
    let [PlayerBodyEvent::Created { id, update, .. }] = events.as_slice() else {
        panic!("expected one existing body create");
    };
    assert_eq!(*id, existing);

    let state = scene.simulation_mut().player_body_mut(existing).unwrap();
    state.mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        case.join_position,
        case.join_yaw,
        0x18,
    )
    .unwrap();
    let runtime = state.runtime.as_mut().unwrap();
    let damage = runtime.receive_damage(
        case.damage,
        Death {
            time: 0.0,
            frame: case.join_frame - 1,
            killer: 0,
        },
        &crate::script_init::HitTags::EMPTY,
    );
    assert_eq!(damage.dealt, case.damage);
    let join_health = runtime.vitals().unwrap().component();
    assert_ne!(join_health, initial_health);

    let mut observer = WorldDelivery::new(OWNER);
    observer.start(&scene, &viewer, case.join_frame).unwrap();
    assert!(!observer.actor_contact_ready(existing.entity));
    assert!(
        observer
            .movement(&scene, case.update_frame)
            .unwrap()
            .is_empty()
    );

    // An existing peer's spawn event can also be delivered on the join tick.
    observer.create_remote_player(existing, update.as_ref().clone());
    let ready = observer.ready();
    let mut creates = ready.iter().filter_map(|entry| {
        matches!(entry.record.delivery, Delivery::RemotePlayerCreate(id) if id == existing)
            .then_some(&entry.record.update)
    });
    let create_update = creates
        .next()
        .expect("current body create for late observer");
    assert!(creates.next().is_none());
    let Some(Operation::Create(create)) = &create_update.operation else {
        panic!("expected observer create definition");
    };
    assert_eq!(create.position, case.join_position.map(|axis| axis as f32));
    assert_eq!(create.rotation, yaw_rotation(f64::from(case.join_yaw)));
    assert_eq!(create.components, [join_health]);
    assert!(create.movement.is_none());
    assert!(create_update.movement.is_none());

    let mut egress = GameEgress::default();
    for sequence in 0..8 {
        transmit(
            &mut observer,
            Some(scene.simulation_mut()),
            &mut egress,
            case.join_frame,
            sequence,
        );
        transport_outcome(&mut observer, &mut egress, sequence, true);
        if observer.actor_contact_ready(existing.entity) {
            break;
        }
    }
    assert!(observer.actor_contact_ready(existing.entity));

    scene
        .simulation_mut()
        .player_body_mut(existing)
        .unwrap()
        .mover = mover_at(case.update_position);
    let movement = observer.movement(&scene, case.update_frame).unwrap();
    assert_eq!(movement.len(), 1);
    assert_eq!(movement[0].entity, existing.entity);
    assert_eq!(movement[0].movement.unwrap().position, case.update_position);
}

fn fixture_projectile_flight(
    owner: BodyId,
    tick: u32,
    speed: f64,
    damage: Option<f64>,
) -> AdmittedFlight {
    use crate::{
        game_combat::{AdmittedFlight, PosedProjectile},
        game_hitscan::{DamageTags, HitDefinition},
        projectile_flight::{ProjectileBallistics, ProjectileFlight},
        script_init::ShotEvent,
    };

    let definition = 0x0400_0000_0000_072c;
    let leases = ProjectileLeases::new(0, tick, [definition]).unwrap();
    let token = OwnerProjectiles::new(owner.entity, leases)
        .admit(
            ShotEvent {
                entity: owner.entity,
                instance: 1,
                graph: 42,
                raw_state: 0,
                frame: tick,
                time_ms: 0,
                activation: 1,
                activation_ms: 0,
                ordinal: 0,
            },
            definition,
            3,
        )
        .unwrap();
    let posed = PosedProjectile {
        stun: None,
        gravity: None,
        predicted: true,
        shot: ShotEvent {
            entity: owner.entity,
            instance: 1,
            graph: 42,
            raw_state: 0,
            frame: tick,
            time_ms: 0,
            activation: 1,
            activation_ms: 0,
            ordinal: 0,
        },
        ballistics: ProjectileBallistics {
            speed,
            lifetime: 1.0,
            max_distance: None,
            terrain_rebound: None,
            terrain_radius: 0.0,
            radius: 0.0,
            definition: Some(definition),
            direct: damage.map(|amount| HitDefinition {
                tags: DamageTags::EMPTY,
                amount,
                loadout: None,
                critical: 1.0,
                falloff: None,
            }),
            blast: None,
        },
        origin: [0.0, 1.0, 0.0],
        direction: [1.0, 0.0, 0.0],
        target_policy: Default::default(),
        piercing: false,
        impact_spawn: None,
        post_bounce_fuse: None,
        sticky_delay: None,
        direct_effect: None,
        blast_effect: None,
        presentation: None,
    };
    let flight =
        ProjectileFlight::new(posed.ballistics, posed.origin, posed.direction, None).unwrap();
    AdmittedFlight {
        token,
        launch_frame: posed.shot.frame,
        flight,
        stun: None,
        hit_effect: None,
        impact_spawn: None,
        presentation: None,
    }
}

#[test]
fn owner_and_observer_share_one_authoritative_room_body() {
    use crate::{
        game_combat::{PlayerBodyEvent, PosedHitscan, ShotContext},
        game_hitscan::{DamageTags, HitDefinition},
        mover_settings::MoverCatalog,
        scene_assets::BotQueries,
    };

    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let rays = TriangleIndex::new(Vec::new()).unwrap();
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();

    let id = avatar(1, 1);
    let owner = OWNER;
    let observer_owner = OWNER + 0x100;

    let mut owner_world = WorldDelivery::new(owner);
    owner_world.started = Some(100);
    let body_value = body(id);
    owner_world.body = Some(body_value);
    owner_world
        .bodies
        .insert(id.entity, (id.generation, Lifetime::Live));

    let state = fixture_player_state(id, owner, 0, 0, [8.0, 0.0, 0.0], 200.0, true);
    owner_world.body_script = Some(
        BodyScript::new(
            body_value,
            100,
            None,
            state.runtime.as_deref().expect("owner runtime"),
        )
        .unwrap(),
    );
    simulation.spawn_player_body(state).unwrap();
    simulation.record_player_poses(100);

    let mut observer_world = WorldDelivery::new(observer_owner);
    observer_world.started = Some(100);
    for event in simulation.take_player_events() {
        if let PlayerBodyEvent::Created {
            owner: created_owner,
            id: created_id,
            update,
        } = event
        {
            if created_owner != observer_owner {
                observer_world.create_remote_player(created_id, *update);
            }
        }
    }
    let mut observer_egress = GameEgress::default();
    let (_, observer_create_sent) = transmit(
        &mut observer_world,
        Some(&mut simulation),
        &mut observer_egress,
        100,
        0,
    );
    assert_eq!(observer_create_sent.records, [RecordSource::Queued(0)]);
    transport_outcome(&mut observer_world, &mut observer_egress, 0, true);
    assert!(observer_world.identity_ready(id.entity));

    // Step the owner once: only RoomSimulation holds the authoritative Mover/pose.
    step_player(&mut owner_world, &mut simulation, &rays, id, forward(101));
    let moved_pose = simulation.player_body_by_owner(owner).unwrap().mover.pose();
    assert_ne!(moved_pose.position, [8.0, 0.0, 0.0]);

    // Hostile hitscan in RoomSimulation mutates the single authoritative body.
    let shooter = OWNER + 0x200;
    let shooter_id = BodyId {
        entity: shooter | 1,
        generation: 1,
    };
    simulation
        .spawn_player_body(fixture_player_state(
            shooter_id,
            shooter,
            1,
            1,
            [0.0, 0.0, 0.0],
            200.0,
            false,
        ))
        .unwrap();
    let hit = simulation
        .hitscan(
            &queries,
            PosedHitscan {
                source: shooter_id.entity,
                origin: [moved_pose.position[0] - 4.0, 1.0, moved_pose.position[2]],
                direction: [1.0, 0.0, 0.0],
                command: Command {
                    frame: 100,
                    ..Command::default()
                },
                shot_frame: 101,
                reach: 20.0,
                hit: HitDefinition {
                    tags: DamageTags::EMPTY,
                    amount: 75.0,
                    loadout: None,
                    critical: 1.0,
                    falloff: None,
                },
            },
            ShotContext {
                team: 1,
                quanta: RETAIL_QUANTA,
                tick: 101,
                now: Duration::from_secs(2),
            },
            |_| true,
        )
        .unwrap()
        .expect("authoritative player hit");
    assert_eq!(hit.damage.target_facts.target, id.entity);
    assert_eq!(
        simulation
            .player_body_by_owner(owner)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .vitals()
            .unwrap()
            .total(),
        125.0
    );

    // Owner and observer derive their updates from that single room body.
    assert_eq!(simulation.player_projections(observer_owner).count(), 2);
    assert!(
        simulation
            .player_projections(observer_owner)
            .any(|projection| projection.snapshot.entity == id.entity)
    );
    let observer_movements: Vec<_> = simulation
        .observer_player_movements(observer_owner)
        .into_iter()
        .filter(|(entity, _)| *entity == id.entity)
        .map(|(entity, movement)| EntityUpdate {
            entity,
            movement: Some(movement),
            ..EntityUpdate::default()
        })
        .collect();
    assert_eq!(observer_movements.len(), 1);
    assert_eq!(observer_movements[0].movement, Some(moved_pose));

    for update in simulation.take_scripted_health() {
        observer_world.queue_health(update);
    }
    assert!(observer_world.health_pending(id.entity));
    let observer_update_sent = observer_world
        .send_frame(
            101,
            observer_movements,
            false,
            Some(&mut simulation),
            &mut observer_egress,
            |_| Ok::<_, ()>(1),
        )
        .unwrap();
    assert!(
        observer_update_sent
            .records
            .contains(&RecordSource::Queued(0))
    );
    assert!(
        observer_update_sent
            .records
            .contains(&RecordSource::Movement(0))
    );
}

#[test]
fn reconnect_attaches_to_room_authority_and_advances_generation() {
    use crate::game_combat::PlayerBodyEvent;

    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    let owner = OWNER;
    let wire = owner | 1;

    let gen1 = simulation.next_player_generation(owner, 0).unwrap();
    assert_eq!(gen1, 1);
    let id1 = BodyId {
        entity: wire,
        generation: gen1,
    };

    let mut first_peer = WorldDelivery::new(owner);
    first_peer.started = Some(100);
    first_peer.body = Some(body(id1));
    first_peer
        .bodies
        .insert(id1.entity, (id1.generation, Lifetime::Live));
    simulation
        .spawn_player_body(fixture_player_state(
            id1, owner, 0, 0, [0.0; 3], 200.0, true,
        ))
        .unwrap();
    let _ = simulation.take_player_events();
    assert_eq!(simulation.player_body_by_owner(owner).unwrap().id, id1);

    // Disconnect retires the room-owned handle while retaining generation history.
    first_peer.clear_in_room(&mut simulation);
    assert!(first_peer.active_body_id().is_none());
    assert!(simulation.player_body_by_owner(owner).is_none());
    assert!(simulation.player_body(id1).is_none());
    assert!(!simulation.player_body_generation_current(id1));
    let events = simulation.take_player_events();
    assert!(matches!(
        events.as_slice(),
        [PlayerBodyEvent::Retired {
            owner: retired_owner,
            id: retired_id,
        }] if *retired_owner == owner && *retired_id == id1
    ));

    // Reconnecting with a fresh peer (client floor 0) continues monotonic generation.
    let gen2 = simulation.next_player_generation(owner, 0).unwrap();
    assert_eq!(gen2, 2);
    let id2 = BodyId {
        entity: wire,
        generation: gen2,
    };
    let mut reconnected = WorldDelivery::new(owner);
    reconnected.started = Some(120);
    reconnected.body = Some(body(id2));
    reconnected
        .bodies
        .insert(id2.entity, (id2.generation, Lifetime::Live));
    simulation
        .spawn_player_body(fixture_player_state(
            id2,
            owner,
            0,
            0,
            [2.0, 0.0, 0.0],
            200.0,
            true,
        ))
        .unwrap();

    assert_eq!(simulation.player_body_by_owner(owner).unwrap().id, id2);
    assert!(simulation.player_body(id1).is_none());
    assert!(simulation.player_body(id2).is_some());
    assert!(!simulation.player_body_generation_current(id1));
    assert!(simulation.player_body_generation_current(id2));
    assert_eq!(simulation.next_player_generation(owner, 0).unwrap(), 3);
}

#[test]
fn death_and_respawn_preserve_friendly_passthrough_and_hostile_blocking() {
    use crate::{
        game_combat::{PosedHitscan, ShotContext},
        game_hitscan::{DamageTags, HitDefinition},
        mover_settings::MoverCatalog,
        scene_assets::BotQueries,
    };

    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();

    let shooter_owner = OWNER;
    let ally_owner = OWNER + 0x100;
    let front_foe_owner = OWNER + 0x200;
    let back_foe_owner = OWNER + 0x300;

    let shooter_id = BodyId {
        entity: shooter_owner | 1,
        generation: 1,
    };
    let ally_id = BodyId {
        entity: ally_owner | 1,
        generation: 1,
    };
    let front_foe_id = BodyId {
        entity: front_foe_owner | 1,
        generation: 1,
    };
    let back_foe_id = BodyId {
        entity: back_foe_owner | 1,
        generation: 1,
    };

    simulation
        .spawn_player_body(fixture_player_state(
            shooter_id,
            shooter_owner,
            0,
            0,
            [0.0, 0.0, 0.0],
            200.0,
            false,
        ))
        .unwrap();
    simulation
        .spawn_player_body(fixture_player_state(
            ally_id,
            ally_owner,
            1,
            0,
            [4.0, 0.0, 0.0],
            200.0,
            false,
        ))
        .unwrap();
    simulation
        .spawn_player_body(fixture_player_state(
            front_foe_id,
            front_foe_owner,
            2,
            1,
            [8.0, 0.0, 0.0],
            100.0,
            false,
        ))
        .unwrap();
    simulation
        .spawn_player_body(fixture_player_state(
            back_foe_id,
            back_foe_owner,
            3,
            1,
            [12.0, 0.0, 0.0],
            200.0,
            false,
        ))
        .unwrap();
    simulation.record_player_poses(1000);

    let mut context = ShotContext {
        team: 0,
        quanta: RETAIL_QUANTA,
        tick: 1000,
        now: Duration::from_secs(4),
    };
    let fire_hitscan = |sim: &mut RoomSimulation, ctx: ShotContext| {
        sim.hitscan(
            &queries,
            PosedHitscan {
                source: shooter_id.entity,
                origin: [0.0, 1.0, 0.0],
                direction: [1.0, 0.0, 0.0],
                command: Command {
                    frame: ctx.tick,
                    ..Command::default()
                },
                shot_frame: ctx.tick,
                reach: 30.0,
                hit: HitDefinition {
                    tags: DamageTags::EMPTY,
                    amount: 120.0,
                    loadout: None,
                    critical: 1.0,
                    falloff: None,
                },
            },
            ctx,
            |_| true,
        )
        .unwrap()
    };

    // Shot 1: friendly at x=4 passes through; front hostile at x=8 blocks & dies; back at x=12 untouched.
    let first_hit = fire_hitscan(&mut simulation, context).expect("front hostile hit");
    assert_eq!(first_hit.damage.target_facts.target, front_foe_id.entity);
    assert!(!simulation.player_body(front_foe_id).unwrap().alive());
    assert_eq!(
        simulation
            .player_body(ally_id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .vitals()
            .unwrap()
            .total(),
        200.0
    );
    assert_eq!(
        simulation
            .player_body(back_foe_id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .vitals()
            .unwrap()
            .total(),
        200.0
    );

    // Shot 2: dead front hostile passes through, hitting back hostile at x=12.
    let second_hit = fire_hitscan(&mut simulation, context).expect("back hostile hit");
    assert_eq!(second_hit.damage.target_facts.target, back_foe_id.entity);
    assert_eq!(
        simulation
            .player_body(back_foe_id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .vitals()
            .unwrap()
            .total(),
        80.0
    );

    // Projectile along the same ray also passes friendly + dead hostile and kills back hostile.
    let flight = fixture_projectile_flight(shooter_id, context.tick, 1000.0, Some(90.0));
    let (outcomes, hits) = simulation
        .advance_projectiles(&queries, 7, shooter_id, vec![flight], context, |_| true)
        .unwrap();
    assert_eq!(outcomes.len(), 1);
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].damage.target_facts.target, back_foe_id.entity);
    assert!(!simulation.player_body(back_foe_id).unwrap().alive());

    // Respawn front and back hostiles with fresh generations -> front hostile blocks again.
    let front_respawn = BodyId {
        entity: front_foe_owner | 2,
        generation: simulation
            .next_player_generation(front_foe_owner, 0)
            .unwrap(),
    };
    let back_respawn = BodyId {
        entity: back_foe_owner | 2,
        generation: simulation
            .next_player_generation(back_foe_owner, 0)
            .unwrap(),
    };
    simulation
        .spawn_player_body(fixture_player_state(
            front_respawn,
            front_foe_owner,
            2,
            1,
            [8.0, 0.0, 0.0],
            200.0,
            false,
        ))
        .unwrap();
    simulation
        .spawn_player_body(fixture_player_state(
            back_respawn,
            back_foe_owner,
            3,
            1,
            [12.0, 0.0, 0.0],
            200.0,
            false,
        ))
        .unwrap();
    context.tick += 5;
    simulation.record_player_poses(context.tick);
    let respawn_hit = fire_hitscan(&mut simulation, context).expect("respawned hostile blocks");
    assert_eq!(respawn_hit.damage.target_facts.target, front_respawn.entity);
    assert_eq!(
        simulation
            .player_body(front_respawn)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .vitals()
            .unwrap()
            .total(),
        80.0
    );
    assert_eq!(
        simulation
            .player_body(back_respawn)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .vitals()
            .unwrap()
            .total(),
        200.0
    );
}

#[test]
fn authored_player_health_settles_once_for_the_accepted_body() {
    use crate::{
        mover_settings::MoverCatalog,
        scene_assets::BotQueries,
        vitals::{PoolKind, Vitals},
    };

    let ground = Arc::new(fixture_ground());
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let source = BodyId {
        entity: OWNER | 1,
        generation: 1,
    };
    let target_owner = OWNER + 0x100;
    let frame = 100;
    let now = Duration::from_secs(2);

    for (delta, replace_target, expected_pools, killed) in [
        (-60.0, false, [65.0, 0.0], false),
        (500.0, false, [200.0, 25.0], false),
        (-500.0, true, [100.0, 25.0], false),
        (-500.0, false, [0.0, 0.0], true),
    ] {
        let mut target = BodyId {
            entity: target_owner | 1,
            generation: 1,
        };
        let target_state = |id: BodyId| {
            let mut state =
                fixture_player_state(id, target_owner, 1, 1, [3.0, 0.0, 0.0], 200.0, false);
            state.runtime = Some(Box::new(synthetic_owner_with_vitals(
                id.entity,
                frame,
                Arc::default(),
                Some(Vitals::with_current_health(200.0, 0.0, 25.0, 100.0)),
            )));
            state
        };
        let mut source_state = fixture_player_state(source, OWNER, 0, 0, [0.0; 3], 150.0, false);
        let mut source_runtime = synthetic_owner_with_vitals(
            source.entity,
            frame,
            Arc::default(),
            Some(Vitals::new(150.0, 0.0, 0.0)),
        );
        source_runtime.authored_health_change = Some((target.entity, delta));
        source_state.runtime = Some(Box::new(source_runtime));

        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        simulation.spawn_player_body(source_state).unwrap();
        simulation.spawn_player_body(target_state(target)).unwrap();
        simulation.activate_combat();
        simulation.enable_spatial_queries(
            false,
            Arc::clone(&ground),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        simulation.prepare_spatial_frame(frame).unwrap();

        if replace_target {
            target.generation += 1;
            simulation.spawn_player_body(target_state(target)).unwrap();
        }
        simulation.finish_tick(&queries, frame, now).unwrap();

        let vitals = simulation
            .player_body(target)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .vitals()
            .unwrap();
        assert_eq!(
            [
                vitals.amount(Some(PoolKind::Health), false),
                vitals.amount(Some(PoolKind::Shields), false),
            ],
            expected_pools
        );
        assert_eq!(vitals.death().is_some(), killed);
        assert_eq!(
            simulation
                .player_body(source)
                .unwrap()
                .runtime
                .as_ref()
                .unwrap()
                .vitals()
                .unwrap()
                .total(),
            150.0
        );
        let updates = simulation.take_scripted_health();
        assert_eq!(updates.len(), usize::from(!replace_target));
        for update in updates {
            assert_eq!(update.entity, target.entity);
            assert!(
                matches!(update.operation, Some(Operation::Update(components)) if components.len() == 1)
            );
        }
        let deaths = simulation.take_player_deaths();
        assert_eq!(deaths.len(), usize::from(killed));
        for death in deaths {
            assert_eq!((death.owner, death.source), (target_owner, source.entity));
            assert!(death.killed_by);
        }

        simulation.finish_tick(&queries, frame + 1, now).unwrap();
        assert!(simulation.take_scripted_health().is_empty());
        assert!(simulation.take_player_deaths().is_empty());
    }
}

#[test]
fn authored_room_message_targets_the_sampled_player_generation() {
    use crate::{
        mover_settings::MoverCatalog,
        scene_assets::BotQueries,
        script_init::RoomMessage,
        script_wire::{Value, Variables},
        vitals::Vitals,
    };

    const MESSAGE: u64 = 0x1234;
    let ground = Arc::new(fixture_ground());
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let source = BodyId {
        entity: OWNER | 1,
        generation: 1,
    };
    let target_owner = OWNER + 0x100;
    let frame = 100;
    let now = Duration::from_secs(2);

    // Each outbox starts with the same accepted target, before replacement or health settlement.
    for (replace_target, kill_target) in [(false, false), (true, false), (false, true)] {
        let target = BodyId {
            entity: target_owner | 1,
            generation: 1,
        };
        let delivered = Arc::default();
        let target_state = |id: BodyId| {
            let mut state =
                fixture_player_state(id, target_owner, 1, 1, [3.0, 0.0, 0.0], 25.0, false);
            let mut runtime = synthetic_owner_with_vitals(
                id.entity,
                frame,
                Arc::default(),
                Some(Vitals::new(25.0, 0.0, 0.0)),
            );
            runtime.received_room_messages = Arc::clone(&delivered);
            state.runtime = Some(Box::new(runtime));
            state
        };
        let mut source_state = fixture_player_state(source, OWNER, 0, 0, [0.0; 3], 100.0, false);
        let mut runtime = synthetic_owner_with_vitals(
            source.entity,
            frame,
            Arc::default(),
            Some(Vitals::new(100.0, 0.0, 0.0)),
        );
        let parameters = Variables::from([(1, Value::Float(2.5)), (2, Value::Bool(true))]);
        runtime.authored_health_change = kill_target.then_some((target.entity, -50.0));
        runtime.authored_room_message = Some((
            target.entity,
            RoomMessage {
                id: MESSAGE,
                parameters: parameters.clone(),
            },
        ));
        source_state.runtime = Some(Box::new(runtime));

        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        simulation.spawn_player_body(source_state).unwrap();
        simulation.spawn_player_body(target_state(target)).unwrap();
        simulation.enable_spatial_queries(
            false,
            Arc::clone(&ground),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        simulation.prepare_spatial_frame(frame).unwrap();
        assert!(delivered.lock().unwrap().is_empty());

        if replace_target {
            simulation
                .spawn_player_body(target_state(BodyId {
                    generation: 2,
                    ..target
                }))
                .unwrap();
        }
        simulation.finish_tick(&queries, frame, now).unwrap();
        simulation.finish_tick(&queries, frame + 1, now).unwrap();
        let delivered = delivered.lock().unwrap();
        if replace_target || kill_target {
            assert!(delivered.is_empty());
        } else {
            assert_eq!(delivered.len(), 1);
            assert_eq!(delivered[0].0, source.entity);
            assert_eq!(delivered[0].1.id, MESSAGE);
            assert_eq!(delivered[0].1.parameters, parameters);
        }
    }
}

#[test]
fn body_replacement_rejects_stale_generation_mutations() {
    use crate::{
        game_combat::ShotContext, mover_settings::MoverCatalog, scene_assets::BotQueries,
        vitals::Death as VitalsDeath,
    };

    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();

    let owner = OWNER;
    let wire = owner | 1;
    let gen1 = simulation.next_player_generation(owner, 0).unwrap();
    let id1 = BodyId {
        entity: wire,
        generation: gen1,
    };
    simulation
        .spawn_player_body(fixture_player_state(
            id1, owner, 0, 1, [0.0; 3], 200.0, false,
        ))
        .unwrap();

    let gen2 = simulation.next_player_generation(owner, 0).unwrap();
    assert!(gen2 > gen1);
    let id2 = BodyId {
        entity: wire,
        generation: gen2,
    };
    simulation
        .spawn_player_body(fixture_player_state(
            id2,
            owner,
            0,
            1,
            [5.0, 0.0, 0.0],
            200.0,
            false,
        ))
        .unwrap();

    // Stale generation spawn and foreign wire-entity collision are rejected cleanly.
    assert!(
        simulation
            .spawn_player_body(fixture_player_state(
                id1, owner, 0, 1, [0.0; 3], 200.0, false,
            ))
            .is_err()
    );
    assert!(
        simulation
            .spawn_player_body(fixture_player_state(
                BodyId {
                    entity: wire,
                    generation: gen2 + 1,
                },
                owner + 0x100,
                1,
                1,
                [0.0; 3],
                200.0,
                false,
            ))
            .is_err()
    );

    // Stale handle cannot inspect, mutate, strip runtime, retire, damage, or run projectiles.
    assert!(simulation.player_body(id1).is_none());
    assert!(simulation.player_body_mut(id1).is_none());
    assert!(!simulation.player_body_generation_current(id1));
    assert!(simulation.player_body_generation_current(id2));
    assert!(!simulation.retire_player_body(id1));
    assert!(!simulation.clear_player_runtime(id1));
    assert!(
        simulation
            .damage_player_body_by_wire(
                id1,
                90.0,
                VitalsDeath {
                    time: 1.0,
                    frame: 100,
                    killer: 9,
                },
                None,
            )
            .is_none()
    );
    let context = ShotContext {
        team: 1,
        quanta: RETAIL_QUANTA,
        tick: 1000,
        now: Duration::from_secs(4),
    };
    let stale_flight = fixture_projectile_flight(id1, context.tick, 20.0, None);
    let (outcomes, hits) = simulation
        .advance_projectiles(&queries, 9, id1, vec![stale_flight], context, |_| true)
        .unwrap();
    assert!(outcomes.is_empty() && hits.is_empty());

    // Current handle remained untouched at full health.
    let current = simulation.player_body(id2).unwrap();
    assert!(current.alive());
    assert_eq!(
        current.runtime.as_ref().unwrap().vitals().unwrap().total(),
        200.0
    );

    // Current handle can retire cleanly, and repeated retirement is idempotent.
    assert!(simulation.retire_player_body(id2));
    assert!(!simulation.retire_player_body(id2));
    assert!(simulation.player_body(id2).is_none());
}

#[test]
fn present_bot_controllers_sequences_creation_possession_and_respawn() {
    use crate::{
        game_bots::{BOT_CONTROLLER_BASE, BOT_ENTITY, Bot},
        game_combat::BotReplacement,
        match_scene::bot_create,
        scene_assets::PreparedScene,
        world::{ComponentValue, Create, Operation, PLAYER_PATH, WITH_PARTICIPANT},
    };

    let mut scene = PreparedScene::fixture_for_bots(vec![Bot::fixture_walker([0.0, 0.0, 10.0])]);
    scene
        .simulation_mut()
        .seat_bots(&GameMap::practice_range(), 1);
    let seated = scene.simulation().seated_bots();
    assert_eq!(seated.len(), 1);
    let controller = seated[0].controller;
    assert_eq!(controller, BOT_CONTROLLER_BASE | 0x100);
    assert_eq!(seated[0].slot, 1);
    assert_eq!(seated[0].body, BOT_ENTITY);

    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(FIRST_TICK);
    world.create_bot(bot_create(scene.bots().unwrap().next().unwrap(), None).unwrap());
    world.present_bot_controllers(&scene).unwrap();

    // First queue holds BotCreate (with HERO_BIT) and BotControllerCreate (no PLAYER_PATH).
    let initial: Vec<_> = world.ready().iter().map(|ready| ready.tracked()).collect();
    assert_eq!(initial.len(), 2);
    assert_eq!(initial[0].update.entity, BOT_ENTITY);
    assert_eq!(initial[1].update.entity, controller);
    let Some(Operation::Create(ctrl_create)) = &initial[1].update.operation else {
        panic!("expected controller create");
    };
    assert_ne!(ctrl_create.flags & WITH_PARTICIPANT, 0);
    assert_eq!(ctrl_create.flags & PLAYER_PATH, 0);
    let slot_component = ctrl_create
        .components
        .iter()
        .find(|component| component.index == 74)
        .expect("controller component 74");
    assert_eq!(slot_component.fields[2], Some(ComponentValue::I8(1)));
    world.sent(&eligible(&world));

    // If BotControllerCreate is lost while BotCreate arrives, retry creates without possessing.
    world.acknowledge(outcome(Arc::clone(&initial[0]), true), Duration::ZERO);
    world.acknowledge(outcome(Arc::clone(&initial[1]), false), Duration::ZERO);
    world.present_bot_controllers(&scene).unwrap();
    let retry: Vec<_> = world.ready().iter().map(|ready| ready.tracked()).collect();
    assert_eq!(retry.len(), 1);
    assert_eq!(retry[0].update.entity, controller);
    assert!(matches!(
        retry[0].update.operation,
        Some(Operation::Create(_))
    ));
    world.sent(&eligible(&world));
    world.acknowledge(outcome(Arc::clone(&retry[0]), true), Duration::ZERO);

    // Once both controller and body creates are acknowledged, 44/75 possession is queued.
    world.present_bot_controllers(&scene).unwrap();
    let possess: Vec<_> = world.ready().iter().map(|ready| ready.tracked()).collect();
    assert_eq!(possess.len(), 1);
    assert_eq!(possess[0].update.entity, controller);
    let Some(Operation::Update(components)) = &possess[0].update.operation else {
        panic!("expected controller possession update");
    };
    assert_eq!(components.len(), 2);
    assert_eq!(components[0].index, 44);
    assert_eq!(
        components[0].fields,
        vec![Some(ComponentValue::Entity(BOT_ENTITY)); 3]
    );
    assert_eq!(components[1].index, 75);
    assert_eq!(
        components[1].fields[0],
        Some(ComponentValue::Id(seated[0].hero))
    );
    world.sent(&eligible(&world));
    world.acknowledge(outcome(Arc::clone(&possess[0]), true), Duration::ZERO);

    // Respawning replaces the bot body ID while reusing the same seated controller.
    let mut respawned_bot = scene
        .bots()
        .unwrap()
        .next()
        .unwrap()
        .respawned(BOT_ENTITY + 1, FIRST_TICK);
    assert_eq!(respawned_bot.slot, Some(1));
    assert_eq!(respawned_bot.controller(), Some(controller));
    scene
        .simulation_mut()
        .kill_bot_for_test(BOT_ENTITY, FIRST_TICK + 1, Duration::from_secs(1));
    let replacements = {
        let (simulation, queries) = scene.bot_simulation();
        simulation
            .finish_tick(
                &queries,
                FIRST_TICK + 200,
                Duration::from_secs(1) + crate::game_bots::BOT_RESPAWN_TIME,
            )
            .unwrap()
    };
    assert_eq!(replacements.len(), 1);
    let fresh_entity = replacements[0].created.entity;
    assert_eq!(fresh_entity, BOT_ENTITY + 1);
    respawned_bot.entity = fresh_entity;

    world.replace_bot(BotReplacement {
        retired: BOT_ENTITY,
        created: replacements[0].created.clone(),
        waits_for_presentation: false,
    });
    // Possession waits until the respawned bot's create is acknowledged.
    world.present_bot_controllers(&scene).unwrap();
    let respawn_records: Vec<_> = world.ready().iter().map(|ready| ready.tracked()).collect();
    assert_eq!(respawn_records.len(), 2);
    assert!(
        respawn_records
            .iter()
            .all(|r| r.update.entity != controller)
    );
    world.sent(&eligible(&world));
    for record in respawn_records {
        world.acknowledge(outcome(record, true), Duration::ZERO);
    }

    world.present_bot_controllers(&scene).unwrap();
    let repossess: Vec<_> = world.ready().iter().map(|ready| ready.tracked()).collect();
    assert_eq!(repossess.len(), 1);
    assert_eq!(repossess[0].update.entity, controller);
    let Some(Operation::Update(components)) = &repossess[0].update.operation else {
        panic!("expected re-possession update after bot respawn");
    };
    assert_eq!(
        components[0].fields,
        vec![Some(ComponentValue::Entity(fresh_entity)); 3]
    );
    assert!(Create::new(1, vec![]).components.is_empty());
}
