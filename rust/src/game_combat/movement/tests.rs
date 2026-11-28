use std::{collections::BTreeMap, sync::Arc};

use super::super::{PlayerBodyState, RoomSimulation, tests::fixture_bots};
use super::MovementContactFrame;
use crate::{
    collision_index::TriangleIndex,
    commands::Command,
    contact_body::CharacterBody,
    contact_cache::CapsulePose,
    contact_triangle::{SKIN, TOUCH},
    contact_world::ContactWorld,
    game_replication::BodyId,
    mover::Mover,
    mover_modifiers::{Modifier, ModifierKey, Operation},
    mover_settings::{MoverCatalog, MoverSettings},
};

#[test]
fn npc_sweep_blocks_against_the_pose_already_resolved_by_player_input() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/npc-contact-after-input-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let tick = u32::try_from(input["tick"].as_u64().unwrap()).unwrap();
    let vector = |key: &str| serde_json::from_value::<[f64; 3]>(input[key].clone()).unwrap();
    let player = BodyId {
        entity: 7,
        generation: 1,
    };
    let settings = MoverSettings {
        gravity_scale: 0.0,
        ..MoverSettings::default()
    };
    let geometry = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let mut mover = Mover::new(settings, Arc::clone(&geometry), [0.0; 3], 0, 0x18).unwrap();
    let displacement =
        crate::script_init::SweptDisplacement::new(vector("player_displacement")).unwrap();
    assert!(mover.queue_swept_displacement(tick, displacement));
    let mut bots = fixture_bots();
    bots.truncate(1);
    bots[0].position = vector("npc_position");
    let npc = bots[0].entity;
    let capsule = bots[0].contact_capsule(&settings);
    let mut room = RoomSimulation::new(bots).unwrap();
    room.enable_movement_contacts(false, Arc::new(MoverCatalog::default()));
    room.spawn_player_body(PlayerBodyState::new(
        player,
        6,
        0,
        0,
        1,
        mover,
        None,
        tick - 1,
        None,
    ))
    .unwrap();
    room.prepare_movement_frame(tick).unwrap();
    let before = room.movement_contact_frame().unwrap();
    let state = room.player_body_mut(player).unwrap();
    state
        .mover
        .step_against(
            &TriangleIndex::new([]).unwrap(),
            Command {
                frame: tick,
                ..Command::default()
            },
            0.016,
            &before.blockers(player.entity, 0),
            |_, _| {},
        )
        .unwrap();
    let player_capsule = state.mover.contact_capsule();

    room.prepare_npc_movement_frame(tick).unwrap();
    let after = room.movement_contact_frame().unwrap();
    assert_eq!(after.blockers(npc, 1), [player_capsule]);
    assert_ne!(before.blockers(npc, 1), after.blockers(npc, 1));
    let separation = |frame: &super::MovementContactFrame| {
        let mut bot = CharacterBody::new(Arc::clone(&geometry), capsule, 0x18);
        let motion = bot
            .move_capsule_against(
                vector("npc_displacement").map(|axis| axis as f32),
                0.016,
                &frame.blockers(npc, 1),
            )
            .unwrap();
        (
            motion.position[0] - player_capsule.position[0],
            motion.actor_contact,
        )
    };
    let touching = capsule.radius + player_capsule.radius;
    let (stale_gap, _) = separation(&before);
    let (resolved_gap, clipped) = separation(&after);
    assert!(stale_gap < touching - 0.1, "{stale_gap}");
    assert!(clipped);
    assert!(
        resolved_gap >= touching - 2.0 * SKIN - TOUCH,
        "{resolved_gap}"
    );
}

#[test]
fn opposing_peers_sweep_against_committed_poses_in_the_same_tick() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/peer-contact-order-v1.json"
    ))
    .unwrap();
    let tick = u32::try_from(fixture["tick"].as_u64().unwrap()).unwrap();
    let origins = [
        fixture["left_x"].as_f64().unwrap(),
        fixture["right_x"].as_f64().unwrap(),
    ];
    let step = fixture["step"].as_f64().unwrap();
    let rays = TriangleIndex::new([]).unwrap();
    let geometry = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let settings = MoverSettings {
        gravity_scale: 0.0,
        ..MoverSettings::default()
    };
    let ids = [
        BodyId {
            entity: 71,
            generation: 1,
        },
        BodyId {
            entity: 72,
            generation: 1,
        },
    ];
    // Either player can get the first turn under the room's round-robin scheduler.
    for order in [[0, 1], [1, 0]] {
        let mut room = RoomSimulation::new(vec![]).unwrap();
        room.enable_movement_contacts(false, Arc::new(MoverCatalog::default()));
        for i in 0..2 {
            let id = ids[i];
            let mover = Mover::new(
                settings,
                Arc::clone(&geometry),
                [origins[i], 0.0, 0.0],
                0,
                0x18,
            )
            .unwrap();
            let team = u8::try_from(i).unwrap();
            room.spawn_player_body(PlayerBodyState::new(
                id,
                id.entity,
                team,
                team,
                1,
                mover,
                None,
                tick - 1,
                None,
            ))
            .unwrap();
        }
        for i in order {
            room.prepare_peer_movement_frame(tick).unwrap();
            let id = ids[i];
            let blockers = room
                .movement_contact_frame()
                .unwrap()
                .blockers(id.entity, u8::try_from(i).unwrap());
            assert_eq!(blockers.len(), 1);
            let mover = &mut room.player_body_mut(id).unwrap().mover;
            let sign = if i == 0 { 1.0 } else { -1.0 };
            let motion =
                crate::script_init::SweptDisplacement::new([sign * step, 0.0, 0.0]).unwrap();
            assert!(mover.queue_swept_displacement(tick, motion));
            mover
                .step_against(
                    &rays,
                    Command {
                        frame: tick,
                        ..Command::default()
                    },
                    0.016,
                    &blockers,
                    |_, _| {},
                )
                .unwrap();
        }
        let first = order[0];
        let actual = room.player_body(ids[first]).unwrap().mover.pose().position[0];
        assert!(((actual - origins[first]).abs() - step).abs() < 0.02);
        let [left, right] = ids.map(|id| room.player_body(id).unwrap().mover.contact_capsule());
        let separation = right.position[0] - left.position[0];
        let touching = left.radius + right.radius;
        assert!(
            separation >= touching - 2.0 * SKIN - TOUCH,
            "{order:?}: {separation}"
        );

        assert!(room.retire_player_body(ids[first]));
        let remaining = order[1];
        room.prepare_peer_movement_frame(tick).unwrap();
        assert!(
            room.movement_contact_frame()
                .unwrap()
                .blockers(ids[remaining].entity, u8::try_from(remaining).unwrap())
                .is_empty()
        );
    }
}

#[test]
fn movement_snapshot_honors_base_and_next_pose_contact_participation() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/movement-contact-participation-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let input = &fixture["input"];
    let number = |key: &str| u32::try_from(input[key].as_u64().unwrap()).unwrap();
    let birth_frame = number("birth_frame");
    let disabled_frame = number("disabled_sample_frame");
    let restored_frame = number("restored_sample_frame");
    let source = BodyId {
        entity: number("source_wire"),
        generation: 1,
    };
    let target = BodyId {
        entity: number("target_wire"),
        generation: 1,
    };
    let catalog = Arc::new(
        MoverCatalog::from_json(&serde_json::to_string(&input["mover_catalog"]).unwrap()).unwrap(),
    );
    let mut bots = fixture_bots();
    Arc::get_mut(&mut bots[0].spawn).unwrap().body = input["disabled_npc_body"].as_u64().unwrap();
    Arc::get_mut(&mut bots[1].spawn).unwrap().body = input["enabled_npc_body"].as_u64().unwrap();
    let enabled_npc = bots[1].contact_capsule(catalog.for_body(Some(bots[1].spawn.body)));
    let disabled_npc = bots[0].contact_capsule(catalog.for_body(Some(bots[0].spawn.body)));
    let [disabled_npc_wire, enabled_npc_wire] = [bots[0].entity, bots[1].entity];
    let mut room = RoomSimulation::new(bots).unwrap();
    for (slot, (id, team, position)) in [
        (source, 0, &input["source_position"]),
        (target, 1, &input["target_position"]),
    ]
    .into_iter()
    .enumerate()
    {
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            serde_json::from_value(position.clone()).unwrap(),
            0,
            0x18,
        )
        .unwrap();
        room.spawn_player_body(PlayerBodyState::new(
            id,
            id.entity + 100,
            u8::try_from(slot).unwrap(),
            team,
            7,
            mover,
            None,
            birth_frame,
            None,
        ))
        .unwrap();
    }
    let target_capsule = room.player_body(target).unwrap().mover.contact_capsule();
    let source_capsule = room.player_body(source).unwrap().mover.contact_capsule();
    room.player_body_mut(target).unwrap().mover.track_modifiers(
        birth_frame,
        &BTreeMap::from([(
            ModifierKey {
                instance: 1,
                state: 1,
                slot: 0,
            },
            Modifier {
                index: u8::try_from(number("modifier_slot")).unwrap(),
                operation: Operation::Set,
                value: 0.0,
                priority: 0.0,
            },
        )]),
    );
    room.enable_movement_contacts(false, catalog);
    room.prepare_movement_frame(disabled_frame).unwrap();
    let initial = room.movement_contact_frame().unwrap();
    let initial_blockers = initial.blockers(source.entity, 0);
    assert_eq!(
        initial_blockers.len(),
        fixture["expected"]["disabled_blockers"].as_u64().unwrap() as usize
    );
    assert_eq!(initial_blockers, [enabled_npc]);
    assert!(!initial_blockers.contains(&disabled_npc));
    assert!(!initial_blockers.contains(&target_capsule));
    for (wire, expected) in [
        (disabled_npc_wire, &[][..]),
        (enabled_npc_wire, std::slice::from_ref(&source_capsule)),
    ] {
        let bot = room.body_mut(wire).unwrap();
        initial.stage_bot(bot, disabled_frame);
        assert_eq!(bot.movement_blockers(disabled_frame), expected);
    }

    // Publish the modifier's retirement after frame 101. It applies to the
    // next pose; the room's already frozen frame remains immutable.
    let target_state = room.player_body_mut(target).unwrap();
    target_state
        .mover
        .step(
            &TriangleIndex::new([]).unwrap(),
            Command {
                frame: disabled_frame,
                ..Command::default()
            },
            0.016,
            |_, _| {},
        )
        .unwrap();
    target_state
        .mover
        .track_modifiers(disabled_frame, &BTreeMap::new());
    let restored_target = target_state.mover.contact_capsule();
    room.prepare_movement_frame(disabled_frame).unwrap();
    assert!(Arc::ptr_eq(
        &initial,
        &room.movement_contact_frame().unwrap()
    ));
    assert_eq!(initial.blockers(source.entity, 0), initial_blockers);

    room.prepare_movement_frame(restored_frame).unwrap();
    let restored = room.movement_contact_frame().unwrap();
    let blockers = restored.blockers(source.entity, 0);
    assert_eq!(
        blockers.len(),
        fixture["expected"]["restored_blockers"].as_u64().unwrap() as usize
    );
    assert!(blockers.contains(&enabled_npc));
    assert!(blockers.contains(&restored_target));
    assert!(!blockers.contains(&disabled_npc));
    // A same-team observer sees only the opposing source body.
    assert_eq!(restored.blockers(999, 1), [source_capsule]);
}

#[test]
fn ally_separation_pushes_same_floor_allies_and_ignores_different_heights() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/movement-contact-participation-v1.json"
    ))
    .unwrap();
    let shape = &fixture["input"]["ally_capsule"];
    let position = |key: &str| serde_json::from_value(shape[key].clone()).unwrap();
    let mut actors = BTreeMap::new();
    let base = CapsulePose {
        position: position("position"),
        radius: serde_json::from_value(shape["radius"].clone()).unwrap(),
        top: serde_json::from_value(shape["top"].clone()).unwrap(),
    };
    let overlapping_ally = CapsulePose {
        position: position("overlapping_position"),
        ..base
    };
    let upper_floor_ally = CapsulePose {
        position: position("separated_position"),
        ..base
    };
    actors.insert(1, (Some(0), base));
    actors.insert(2, (Some(0), overlapping_ally));
    actors.insert(3, (Some(0), upper_floor_ally));
    let frame = MovementContactFrame { actors };
    let push = frame.ally_separation(1, 0, base);
    assert!(push[0] < 0.0, "{push:?}");
    assert_eq!(push[1], 0.0);

    let isolated = frame.ally_separation(3, 0, upper_floor_ally);
    assert_eq!(isolated, [0.0, 0.0]);
    assert_eq!(frame.ally_separation(4, 0, base), [0.0, 0.0]);
}

#[test]
fn ally_separation_uses_the_horizontal_slice_through_rounded_ends() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/movement-contact-participation-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let capsule = &input["ally_capsule"];
    let base = CapsulePose {
        position: [0.0; 3],
        radius: capsule["radius"].as_f64().unwrap() as f32,
        top: capsule["top"].as_f64().unwrap() as f32,
    };
    let offsets: Vec<[f32; 3]> = serde_json::from_value(input["ally_offsets"].clone()).unwrap();
    let fractions: Vec<f64> =
        serde_json::from_value(fixture["expected"]["ally_step_fractions"].clone()).unwrap();
    assert_eq!(offsets.len(), fractions.len());
    let max_step = 2.8 * crate::game_clock::TICK_PERIOD.as_secs_f64();
    for (position, fraction) in offsets.into_iter().zip(fractions) {
        let other = CapsulePose { position, ..base };
        let frame = MovementContactFrame {
            actors: BTreeMap::from([(1, (Some(0), base)), (2, (Some(0), other))]),
        };
        let push = frame.ally_separation(1, 0, base);
        assert!(
            (push[0] + max_step * fraction).abs() < 1e-9,
            "{position:?} {push:?}"
        );
        assert_eq!(push[1], 0.0);
        assert_eq!(frame.ally_separation(2, 0, other), [-push[0], -push[1]]);
    }
}
