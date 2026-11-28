use super::*;
use crate::{
    bits::BitWriter,
    game_ingress::{GameIngress, MessageKind},
    game_parameters::CLIENT_WHEEL_ACTION,
    script_wire::Value as ScriptValue,
    vitals::Vitals,
};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    input: Input,
    expected: Expected,
}

#[derive(Deserialize)]
struct Input {
    action: String,
    foreign_asset: String,
    unvalidated_marker: u32,
    confirmed: Vec<bool>,
}

#[derive(Deserialize)]
struct Expected {
    topic: String,
    action_parameter: u32,
    confirmed_parameter: u32,
    delivered_with_current_body: usize,
    delivered_to_replacement: usize,
}

#[test]
fn delayed_spray_reports_require_a_recent_owner_transmission() {
    use crate::script_snapshot_wire::{ProjectedInstance, ProjectedState};
    use serde_json::Value as Json;
    use std::collections::BTreeSet;

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/wheel-spray-use-report-v1.json"
    ))
    .unwrap();
    let history = &fixture["sent_history"];
    let input = &fixture["input"];
    let guid = |value: &Json| {
        u64::from_str_radix(value.as_str().unwrap().trim_start_matches("0x"), 16).unwrap()
    };
    let report = ClientWheelUse {
        entity: guid(&input["entity"]) as u32,
        instance: input["instance"].as_u64().unwrap() as u16,
        unlock: guid(&input["unlock"]),
    };
    let raw = history["state"].as_u64().unwrap() as u16;
    let start_ms = history["instance_start_ms"].as_u64().unwrap();
    let sent_frame = history["sent_frame"].as_u64().unwrap() as u32;
    let id = BodyId {
        entity: report.entity,
        generation: 1,
    };
    let runtime = synthetic_owner_with_vitals(id.entity, sent_frame, Arc::default(), None);
    let mut script = BodyScript::new(body(id), sent_frame, None, &runtime).unwrap();
    let empty = ProjectedSnapshot {
        entity: id.entity,
        view: View::Owner,
        entity_exclusions: BTreeSet::new(),
        variables: Default::default(),
        instances: BTreeMap::new(),
        callout: 0,
        callouts: Vec::new(),
        emit_events: true,
    };
    let bits = BitWriter::new();
    script.stream.create_arrived();
    let baseline = script.stream.full(&bits, empty.clone()).unwrap();
    script.sent(baseline.last);
    assert!(script.stream.arrived(baseline.last).advanced);

    let mut active = empty;
    active.instances.insert(
        report.instance,
        ProjectedInstance {
            graph: history["graph"].as_u64().unwrap() as u32,
            start_ms,
            parent: None,
            variables: Default::default(),
            states: BTreeMap::from([(
                raw,
                ProjectedState {
                    active: true,
                    payload: BTreeMap::from([("unlock".into(), ScriptValue::Asset(report.unlock))]),
                    link: None,
                    volley: None,
                },
            )]),
            events: Vec::new(),
        },
    );
    let chunk = script
        .stream
        .delta(
            &bits,
            active,
            BTreeSet::from([ChangeKey::State(report.instance, raw)]),
            DeltaKind::InitialOwner(sent_frame),
        )
        .unwrap();
    assert_eq!(script.sent_spray_origin(report), None);
    script.sent(chunk.last);
    assert!(script.stream.arrived(chunk.last).advanced);
    assert_eq!(script.sent_spray_origin(report), Some(start_ms));
    for wrong in [
        ClientWheelUse {
            unlock: report.unlock + 1,
            ..report
        },
        ClientWheelUse {
            entity: report.entity + 1,
            ..report
        },
        ClientWheelUse {
            instance: report.instance + 1,
            ..report
        },
    ] {
        assert_eq!(script.sent_spray_origin(wrong), None);
    }

    script.frame = history["last_eligible_frame"].as_u64().unwrap() as u32;
    assert_eq!(script.sent_spray_origin(report), Some(start_ms));
    script.frame = history["first_expired_frame"].as_u64().unwrap() as u32;
    assert_eq!(script.sent_spray_origin(report), None);
}

#[test]
fn wheel_input_follows_reliable_order_and_current_body_generation() {
    let fixture: Fixture = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/wheel-selection-ingress-v1.json"
    ))
    .unwrap();
    let guid = |hex: &str| u64::from_str_radix(hex.trim_start_matches("0x"), 16).unwrap();
    let action = guid(&fixture.input.action);
    let topic = guid(&fixture.expected.topic);
    let schemas = crate::jam_codec::Schemas::from_json(
        r#"{"A7FBBD0C":{"21608":[
            {"type":8,"off":120,"size":8,"count":0,"array":false},
            {"type":0,"off":128,"size":1,"count":0,"array":false},
            {"type":14,"off":132,"size":4,"count":0,"array":false,
             "fields":[{"type":6,"off":0,"size":4,"count":0,"array":false}]}
        ]}}"#,
    )
    .unwrap();
    let packet = |ordinal, asset, confirmed, marker| {
        let fields = Fields::from([
            (0x78, Value::Unsigned(asset)),
            (0x80, Value::Bool(confirmed)),
            (
                0x84,
                Value::Struct(Fields::from([(0, Value::Unsigned(u64::from(marker)))])),
            ),
        ]);
        let encoded = crate::game_messages::encode(&schemas, CLIENT_WHEEL_ACTION, &fields).unwrap();
        let mut packet = BitWriter::new();
        crate::game_messages::write_channel(&mut packet, &[(ordinal, encoded)], &[]).unwrap();
        packet.bit(false);
        packet
    };
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    let mut scene = PreparedScene::fixture_for_owner_admission(Default::default());
    let mut world = WorldDelivery::new(OWNER);
    let original = BodyId {
        entity: OWNER | 1,
        generation: 1,
    };
    let spawn = |scene: &mut PreparedScene, id| {
        let mut state = fixture_player_state(id, OWNER, 0, 0, [0.0; 3], 200.0, false);
        let runtime = synthetic_owner_with_vitals(
            state.id.entity,
            100,
            Arc::default(),
            Some(Vitals::new(200.0, 0.0, 0.0)),
        );
        let received = Arc::clone(&runtime.received_room_messages);
        state.runtime = Some(Box::new(runtime));
        scene.simulation_mut().spawn_player_body(state).unwrap();
        received
    };
    let recorded = spawn(&mut scene, original);
    world.started = Some(100);
    world.body = Some(body(original));
    world.created.insert(original.entity);

    let mut ingress = GameIngress::new();
    let mut receive = |world: &mut WorldDelivery, scene: &PreparedScene, packet: BitWriter| {
        ingress
            .receive(&schemas, packet.as_bytes(), |message, command_frame| {
                assert_eq!(MessageKind::from(message.id), MessageKind::WheelAction);
                world.queue_wheel_action(scene.simulation(), &message, command_frame);
            })
            .unwrap();
    };
    receive(&mut world, &scene, packet(0, action, true, 0)); // possession not delivered
    world.bound = Some(original);
    receive(
        &mut world,
        &scene,
        packet(1, guid(&fixture.input.foreign_asset), true, 0),
    );
    receive(
        &mut world,
        &scene,
        packet(2, action, true, fixture.input.unvalidated_marker),
    );
    assert!(!world.has_input_work());

    for (index, &confirmed) in fixture.input.confirmed.iter().enumerate().rev() {
        receive(
            &mut world,
            &scene,
            packet(3 + index as u32, action, confirmed, 0),
        );
    }
    receive(&mut world, &scene, packet(3, action, false, 0)); // reliable retry
    let used = world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 10)
        .unwrap();
    assert_eq!(used, (fixture.expected.delivered_with_current_body, false));
    let expected = |confirmed| RoomMessage {
        id: topic,
        parameters: crate::script_wire::Variables::from([
            (
                fixture.expected.action_parameter,
                ScriptValue::Asset(action),
            ),
            (
                fixture.expected.confirmed_parameter,
                ScriptValue::Bool(confirmed),
            ),
        ]),
    };
    assert_eq!(
        *recorded.lock().unwrap(),
        fixture
            .input
            .confirmed
            .iter()
            .map(|&confirmed| (original.entity, expected(confirmed)))
            .collect::<Vec<_>>()
    );

    receive(&mut world, &scene, packet(5, action, true, 0));
    let replacement = BodyId {
        generation: 2,
        ..original
    };
    let new_recorded = spawn(&mut scene, replacement);
    world.body = Some(body(replacement));
    world.bound = Some(replacement);
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 10)
            .unwrap(),
        (1, false)
    );
    assert_eq!(
        new_recorded.lock().unwrap().len(),
        fixture.expected.delivered_to_replacement
    );

    receive(&mut world, &scene, packet(6, action, true, 0));
    scene
        .simulation_mut()
        .damage_player_body_by_wire(
            replacement,
            f64::MAX,
            Death {
                time: 0.0,
                frame: 100,
                killer: 0,
            },
            None,
        )
        .unwrap();
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 10)
            .unwrap(),
        (1, false)
    );
    assert!(new_recorded.lock().unwrap().is_empty());
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack"]
fn prepared_world_delivery_emote_completes_and_wasd_cancels() {
    use crate::{
        accounts::Profile, catalog::ItemDb, collection::Collection,
        game_instance::TeamMemberCounts, match_scene::WheelCosmetics,
        retail_templates::RetailTemplates,
    };
    use std::path::PathBuf;

    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let data = runtime.join("data");
    let assets = SceneAssets::load(&runtime).unwrap();
    let templates = RetailTemplates::load(data.join("retail_templates.json")).unwrap();
    let items = Arc::new(ItemDb::load(&data).unwrap());
    let collection = Collection::new(&templates, Arc::clone(&items)).unwrap();
    let profile = Profile::default();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let mut card = PlayerCard::default();
    for hero in scene.heroes().heroes() {
        let cosmetics = collection
            .equipped_wheel_cosmetics(&profile, hero.guid)
            .unwrap();
        card.wheel_cosmetics.insert(hero.guid, cosmetics);
    }
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    scene
        .simulation_mut()
        .set_team_member_counts(TeamMemberCounts::from_teams(false, [viewer.team]));

    let mut world = WorldDelivery::new(OWNER);
    world.start(&scene, &viewer, FIRST_TICK).unwrap();
    world.created.insert(OWNER);
    world.present(&scene, FIRST_TICK).unwrap();

    let flush_all = |world: &mut WorldDelivery| {
        let ready: Vec<_> = world
            .ready()
            .iter()
            .map(|r| (r.index, r.tracked()))
            .collect();
        if ready.is_empty() {
            return;
        }
        let indices: Vec<_> = ready.iter().map(|(i, _)| *i).collect();
        let records: Vec<_> = ready.into_iter().map(|(_, r)| r).collect();
        world.sent(&indices);
        world.acknowledge(
            DeliveryReport {
                requeued_reliable: 0,
                owner_correction: None,
                packets: vec![PacketDelivery {
                    sequence: 0,
                    arrived: true,
                    records,
                }],
            },
            Duration::ZERO,
        );
    };

    // Spawn Soldier: 76
    let mut frame = FIRST_TICK + 1;
    scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
    world.queue_game_message(frame, Duration::ZERO, pick_hero(OWNER, SOLDIER));
    world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap();
    world.present(&scene, frame).unwrap();
    flush_all(&mut world);
    world.present(&scene, frame).unwrap();
    flush_all(&mut world);

    // Step 10 neutral frames
    for _ in 0..10 {
        frame += 1;
        scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
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
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);
    }
    if let Some(fault) = &world.owner_script_fault {
        panic!(
            "fault before emote at frame {}: {}",
            fault.frame, fault.error
        );
    }

    // Trigger default emote slot 0 (0x0A90_0000_0000_0172)
    let make_emote_msg = || Message {
        id: CLIENT_WHEEL_ACTION,
        fields: Fields::from([
            (0x78, Value::Unsigned(0x0a90_0000_0000_0172)),
            (0x80, Value::Bool(true)),
        ]),
    };
    world.queue_wheel_action(scene.simulation(), &make_emote_msg(), Some(frame));
    world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap();
    world.present(&scene, frame).unwrap();
    flush_all(&mut world);

    let emote_start_pos = scene
        .simulation()
        .player_body(world.body.unwrap().id)
        .unwrap()
        .mover
        .pose()
        .position;
    let mut saw_023a = false;
    let mut exited_023a = false;
    let mut max_emote_drift = 0.0_f64;
    for _ in 0..400 {
        frame += 1;
        scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
        world.queue_commands(
            frame,
            &[Command {
                frame,
                forward: if saw_023a { 127 } else { 0 },
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);
        if let Some(fault) = &world.owner_script_fault {
            panic!(
                "owner script fault during default emote at frame {}: {}",
                fault.frame, fault.error
            );
        }
        let id = world.body.unwrap().id;
        let actor = scene.simulation().player_body(id).unwrap();
        let snap = actor.runtime.as_ref().unwrap().snapshot().unwrap();
        let has_023a = snap.instances.values().any(|i| i.graph == 0x023a);
        if has_023a {
            saw_023a = true;
            let p = actor.mover.pose().position;
            let drift =
                ((p[0] - emote_start_pos[0]).powi(2) + (p[2] - emote_start_pos[2]).powi(2)).sqrt();
            max_emote_drift = max_emote_drift.max(drift);
        } else if saw_023a {
            exited_023a = true;
            break;
        }
    }
    assert!(saw_023a, "default emote must enter 0x023A");
    assert!(exited_023a, "default emote must exit 0x023A cleanly");
    assert!(
        max_emote_drift < 0.05,
        "holding W during a non-looping emote must lock movement (drifted {max_emote_drift:.3}m)"
    );
    let id = world.body.unwrap().id;
    let pos_before = scene
        .simulation()
        .player_body(id)
        .unwrap()
        .mover
        .pose()
        .position;
    for _ in 0..20 {
        frame += 1;
        scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
        world.queue_commands(
            frame,
            &[Command {
                frame,
                forward: 127,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);
    }
    let pos_after = scene
        .simulation()
        .player_body(id)
        .unwrap()
        .mover
        .pose()
        .position;
    let dist =
        ((pos_after[0] - pos_before[0]).powi(2) + (pos_after[2] - pos_before[2]).powi(2)).sqrt();
    assert!(
        dist > 0.5,
        "player must be able to walk after default emote finishes (moved {dist:.3}m)"
    );

    // Now equip a looping Sit emote (alias 0x0C80000000000476) and verify WASD cancels it!
    // Pick deterministically by lowest unlock GUID so HashMap order cannot pick an 8.4s intro.
    let sit_bt = items
        .emote_aliases
        .iter()
        .filter_map(|(&unlock, &alias)| {
            (alias == 0x0c80_0000_0000_0476)
                .then(|| items.emote_blend_trees.get(&unlock).map(|&bt| (unlock, bt)))
                .flatten()
        })
        .min_by_key(|&(unlock, _)| unlock)
        .map(|(_, bt)| bt)
        .expect("sit emote blend tree in catalog");
    scene
        .simulation_mut()
        .player_body_mut(id)
        .unwrap()
        .runtime
        .as_mut()
        .unwrap()
        .set_wheel_cosmetics(WheelCosmetics {
            emote_aliases: [0x0c80_0000_0000_0476, 0, 0, 0],
            emote_blend_trees: [sit_bt, 0, 0, 0],
            ..Default::default()
        });
    for _ in 0..5 {
        frame += 1;
        scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
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
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);
    }
    world.queue_wheel_action(scene.simulation(), &make_emote_msg(), Some(frame));
    world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap();
    world.present(&scene, frame).unwrap();
    flush_all(&mut world);
    for _ in 0..220 {
        frame += 1;
        scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
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
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);
        if let Some(fault) = &world.owner_script_fault {
            panic!(
                "owner script fault during sit emote at frame {}: {}",
                fault.frame, fault.error
            );
        }
    }
    assert!(
        scene
            .simulation()
            .player_body(id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .snapshot()
            .unwrap()
            .instances
            .values()
            .any(|i| i.graph == 0x023a),
        "looping sit emote should stay active until cancelled"
    );
    let mut cancelled = false;
    for _ in 0..160 {
        frame += 1;
        scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
        world.queue_commands(
            frame,
            &[Command {
                frame,
                forward: 127,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);
        if let Some(fault) = &world.owner_script_fault {
            panic!(
                "owner script fault cancelling sit emote at frame {}: {}",
                fault.frame, fault.error
            );
        }
        let active = scene
            .simulation()
            .player_body(id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .snapshot()
            .unwrap()
            .instances
            .values()
            .any(|i| i.graph == 0x023a);
        if !active {
            cancelled = true;
            break;
        }
    }
    if !cancelled {
        let snap = scene
            .simulation()
            .player_body(id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .snapshot()
            .unwrap();
        let detail: Vec<String> = snap
            .instances
            .iter()
            .filter(|(_, i)| i.graph == 0x023a)
            .map(|(inst_id, i)| {
                let active: Vec<u16> = i
                    .states
                    .iter()
                    .filter(|(_, s)| s.active)
                    .map(|(&raw, _)| raw)
                    .collect();
                format!(
                    "inst={inst_id} active={active:?} v488={:?} v1322={:?} v2101={:?} v681={:?}",
                    i.variables.get(&488),
                    i.variables.get(&1322),
                    i.variables.get(&2101),
                    i.variables.get(&681)
                )
            })
            .collect();
        panic!(
            "pressing W must cancel looping sit emote cleanly: {}",
            detail.join("; ")
        );
    }
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack"]
fn prepared_world_delivery_all_32_heroes_full_ability_probe() {
    use crate::{
        accounts::Profile, catalog::ItemDb, collection::Collection,
        game_instance::TeamMemberCounts, retail_templates::RetailTemplates,
    };
    use std::path::PathBuf;

    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let data = runtime.join("data");
    let assets = SceneAssets::load(&runtime).unwrap();
    let templates = RetailTemplates::load(data.join("retail_templates.json")).unwrap();
    let items = Arc::new(ItemDb::load(&data).unwrap());
    let collection = Collection::new(&templates, Arc::clone(&items)).unwrap();
    let profile = Profile::default();
    let heroes: Vec<_> = assets.heroes().heroes().cloned().collect();
    assert_eq!(heroes.len(), 32);

    let mut card = PlayerCard::default();
    for hero in &heroes {
        let cosmetics = collection
            .equipped_wheel_cosmetics(&profile, hero.guid)
            .unwrap();
        card.wheel_cosmetics.insert(hero.guid, cosmetics);
    }
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };

    let flush_all = |world: &mut WorldDelivery| {
        let ready: Vec<_> = world
            .ready()
            .iter()
            .map(|r| (r.index, r.tracked()))
            .collect();
        if ready.is_empty() {
            return;
        }
        let indices: Vec<_> = ready.iter().map(|(i, _)| *i).collect();
        let records: Vec<_> = ready.into_iter().map(|(_, r)| r).collect();
        world.sent(&indices);
        world.acknowledge(
            DeliveryReport {
                requeued_reliable: 0,
                owner_correction: None,
                packets: vec![PacketDelivery {
                    sequence: 0,
                    arrived: true,
                    records,
                }],
            },
            Duration::ZERO,
        );
    };

    let mut faults = Vec::new();
    for hero in &heroes {
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        scene
            .simulation_mut()
            .set_team_member_counts(TeamMemberCounts::from_teams(false, [viewer.team]));
        let mut world = WorldDelivery::new(OWNER);
        world.start(&scene, &viewer, FIRST_TICK).unwrap();
        world.created.insert(OWNER);
        world.present(&scene, FIRST_TICK).unwrap();
        flush_all(&mut world);

        let mut frame = FIRST_TICK + 1;
        scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
        world.queue_game_message(frame, Duration::ZERO, pick_hero(OWNER, hero.guid));
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);
        world.present(&scene, frame).unwrap();
        flush_all(&mut world);

        let Some(body_id) = world.body.map(|b| b.id) else {
            faults.push(format!("{}: no body spawned", hero.name));
            continue;
        };
        let has_script_start = world.body_script.is_some();

        // Real Overwatch 1.74 button bits from statescript::script_runtime::INPUTS:
        // 0x001=Primary, 0x002=Secondary, 0x008=Ability1, 0x010=Ability2, 0x020=Ult,
        // 0x040=Jump, 0x080=Crouch, 0x400=Reload, 0x800=QuickMelee
        let phases: &[(&str, u32, u32, i8)] = &[
            ("idle", 0x000, 6, 0),
            ("primary", 0x001, 10, 0),
            ("rel", 0x000, 6, 0),
            ("secondary", 0x002, 10, 0),
            ("rel", 0x000, 6, 0),
            ("ability1", 0x008, 10, 127),
            ("rel", 0x000, 10, 0),
            ("ability2", 0x010, 10, 127),
            ("rel", 0x000, 10, 0),
            ("ult", 0x020, 8, 0),
            ("rel", 0x000, 8, 0),
            ("jump", 0x040, 8, 127),
            ("rel", 0x000, 8, 0),
            ("crouch", 0x080, 4, 0),
            ("rel", 0x000, 4, 0),
            ("reload", 0x400, 6, 0),
            ("rel", 0x000, 6, 0),
            ("melee", 0x800, 6, 0),
            ("rel", 0x000, 8, 0),
        ];

        let mut fault_msg: Option<String> = None;
        let mut hitscan_n = 0usize;
        let mut proj_n = 0usize;
        let mut start_pos = scene
            .simulation()
            .player_body(body_id)
            .map(|b| b.mover.pose().position)
            .unwrap_or([0.0; 3]);
        let mut max_disp = 0.0f64;

        'phases: for &(label, buttons, ticks, forward) in phases {
            for _ in 0..ticks {
                frame += 1;
                scene.simulation_mut().prepare_spatial_frame(frame).unwrap();
                world.queue_commands(
                    frame,
                    &[Command {
                        frame,
                        buttons,
                        forward,
                        ..Command::default()
                    }],
                );
                if let Err(err) = world.advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16) {
                    fault_msg = Some(format!("advance_input({label}): {err}"));
                    break 'phases;
                }
                if let Err(err) = world.present(&scene, frame) {
                    fault_msg = Some(format!("present({label}): {err}"));
                    break 'phases;
                }
                flush_all(&mut world);
                if let Some(fault) = &world.owner_script_fault {
                    fault_msg = Some(format!(
                        "owner_script_fault({label}@f{}): {}",
                        fault.frame, fault.error
                    ));
                    break 'phases;
                }
                if let Some(state) = scene.simulation().player_body(body_id) {
                    hitscan_n += state.hitscan.len();
                    proj_n += state.launches.len();
                    let p = state.mover.pose().position;
                    let d = ((p[0] - start_pos[0]).powi(2)
                        + (p[1] - start_pos[1]).powi(2)
                        + (p[2] - start_pos[2]).powi(2))
                    .sqrt();
                    if d > max_disp {
                        max_disp = d;
                    }
                    let _ = &mut start_pos;
                }
            }
        }

        // Check D.Va blackout state & active graph summary
        let mut extra = String::new();
        if let Some(snap) = scene
            .simulation()
            .player_body(body_id)
            .and_then(|b| b.runtime.as_ref())
            .and_then(|r| r.snapshot())
        {
            if let Some(inst) = snap.instances.values().find(|i| i.graph == 0x026a) {
                let black = inst.states.get(&95).is_some_and(|s| s.active);
                let v6828 = inst.variables.get(&6828);
                extra = format!(" [026A black_ux95={black} v6828={v6828:?}]");
                if black || v6828 == Some(&crate::script_wire::Value::Bool(true)) {
                    faults.push(format!(
                        "{}: D.Va blackout curtain active (ux95={black}, v6828={v6828:?})",
                        hero.name
                    ));
                }
            }
        }
        let script_alive = world.body_script.is_some();
        eprintln!(
            "  {:<16} start_script={:<5} end_script={:<5} hitscan={:<3} proj={:<3} disp={:<5.2} fault={:?}{}",
            hero.name,
            has_script_start,
            script_alive,
            hitscan_n,
            proj_n,
            max_disp,
            fault_msg,
            extra
        );
        if let Some(msg) = fault_msg {
            faults.push(format!("{}: {msg}", hero.name));
        } else if !script_alive {
            faults.push(format!("{}: body_script dropped without fault", hero.name));
        }
    }

    assert!(
        faults.is_empty(),
        "32-hero WorldDelivery faults ({}/32):\n{}",
        faults.len(),
        faults.join("\n")
    );
}
