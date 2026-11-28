use super::*;
use crate::{
    game_heroes::{CASSIDY, HeroCatalog},
    script_player::fixture_body_factory,
    vitals::Death,
};
use std::sync::atomic::AtomicUsize;

const ORIGINAL_SKIN: SkinTheme = SkinTheme {
    guid: 7,
    golden: true,
};

fn respawn_scene(viewer: &PlayerAppearance<'_>) -> (PreparedScene, WorldDelivery, BodyId) {
    let catalog = serde_json::json!({
        format!("0x{SOLDIER:016X}"): {
            "name": "First respawn fixture", "class": "Damage", "body": "0x0400000000000001"
        },
        format!("0x{CASSIDY:016X}"): {
            "name": "Second respawn fixture", "class": "Damage", "body": "0x0400000000000001"
        }
    });
    let mut scene = PreparedScene::fixture_for_owner_admission(
        HeroCatalog::from_json(&catalog.to_string()).unwrap(),
    );
    let (factory, _) = fixture_body_factory();
    scene.owner_body_factory = Some(Arc::new(move |_, spawn, setup| {
        Ok(Some(Box::new(factory.start_with_setup(spawn, setup)?)))
    }));
    let mut world = WorldDelivery::new(viewer.entity);
    world.started = Some(FIRST_TICK);
    world.created.insert(viewer.entity);
    world.input.processed = Some(FIRST_TICK);
    world
        .controller
        .full_owner(FIRST_TICK, |_| Ok(crate::bits::BitWriter::new()))
        .unwrap();
    let hero = scene.heroes().get(SOLDIER).unwrap().clone();
    world
        .switch(
            &mut scene,
            viewer,
            FIRST_TICK,
            RETAIL_QUANTA,
            viewer.entity | 1,
            (&hero, ORIGINAL_SKIN),
        )
        .unwrap();
    let id = world.active_body_id().unwrap();
    let (_, damage, _) = scene
        .simulation_mut()
        .damage_player_body_by_wire(
            id,
            f64::MAX,
            Death {
                time: 0.0,
                frame: FIRST_TICK,
                killer: 0,
            },
            None,
        )
        .unwrap();
    assert!(damage.killed);
    (scene, world, id)
}

#[test]
fn scheduled_player_respawn_keeps_choice_and_fires_once_at_the_original_deadline() {
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    let (mut scene, mut world, dead) = respawn_scene(&viewer);
    let due = Duration::from_secs(3);
    scene.simulation_mut().schedule_player_respawn(dead, due);
    scene
        .simulation_mut()
        .schedule_player_respawn(dead, due + Duration::from_secs(10));
    assert_eq!(scene.simulation().pending_player_respawn(dead), Some(due));
    world
        .advance_player_respawn(
            &mut scene,
            &viewer,
            FIRST_TICK + 1,
            RETAIL_QUANTA,
            due - Duration::from_nanos(1),
        )
        .unwrap();
    assert_eq!(world.active_body_id(), Some(dead));
    assert!(!scene.simulation().player_body(dead).unwrap().alive());
    let command = forward(FIRST_TICK + 1);
    world.queue_commands(command.frame, &[command]);
    world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap();
    assert_eq!(
        scene
            .simulation()
            .player_body(dead)
            .unwrap()
            .mover
            .pose()
            .throttles,
        [0; 2]
    );
    assert_eq!(world.input.processed, Some(command.frame));
    world
        .advance_player_respawn(&mut scene, &viewer, FIRST_TICK + 2, RETAIL_QUANTA, due)
        .unwrap();
    let fresh = world.active_body_id().unwrap();
    assert_ne!(fresh, dead);
    assert!(fresh.generation > dead.generation);
    assert_eq!(
        (world.body.unwrap().hero, world.body.unwrap().skin),
        (SOLDIER, ORIGINAL_SKIN)
    );
    assert!(scene.simulation().player_body(fresh).unwrap().alive());
    assert!(scene.simulation().player_body(dead).is_none());
    assert_eq!(scene.simulation().pending_player_respawn(fresh), None);
    world
        .advance_player_respawn(
            &mut scene,
            &viewer,
            FIRST_TICK + 3,
            RETAIL_QUANTA,
            due + Duration::from_secs(10),
        )
        .unwrap();
    assert_eq!(world.active_body_id(), Some(fresh));
}

#[test]
fn a_dead_player_has_no_implicit_respawn_schedule() {
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut scene, mut world, dead) = respawn_scene(&viewer);
    assert_eq!(scene.simulation().pending_player_respawn(dead), None);
    world
        .advance_player_respawn(
            &mut scene,
            &viewer,
            FIRST_TICK + 1,
            RETAIL_QUANTA,
            Duration::from_secs(3600),
        )
        .unwrap();
    assert_eq!(world.active_body_id(), Some(dead));
    assert!(!scene.simulation().player_body(dead).unwrap().alive());
    assert_eq!(scene.simulation().pending_player_respawn(dead), None);
}

#[test]
fn pending_manual_hero_or_skin_choice_supersedes_the_old_respawn_plan() {
    let due = Duration::from_secs(3);
    let selected_skin = SkinTheme {
        guid: 9,
        ..SkinTheme::default()
    };
    for skin_only in [false, true] {
        let selected_hero = if skin_only { SOLDIER } else { CASSIDY };
        let mut card = PlayerCard::default();
        card.skins.insert(selected_hero, selected_skin);
        let viewer = PlayerAppearance {
            entity: OWNER,
            slot: 0,
            team: 0,
            card: &card,
        };
        let (mut scene, mut world, dead) = respawn_scene(&viewer);
        scene.simulation_mut().schedule_player_respawn(dead, due);
        if skin_only {
            world.queue_skin(selected_hero, FIRST_TICK + 1, due);
        } else {
            world.queue_game_message(FIRST_TICK + 1, due, pick_hero(OWNER, selected_hero));
        }
        assert!(world.has_input_work());
        world
            .advance_player_respawn(&mut scene, &viewer, FIRST_TICK + 1, RETAIL_QUANTA, due)
            .unwrap();
        assert_eq!(world.active_body_id(), Some(dead));
        assert_eq!(scene.simulation().pending_player_respawn(dead), Some(due));
        assert_eq!(
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 1)
                .unwrap(),
            (1, false)
        );
        let selected = world.active_body_id().unwrap();
        assert_ne!(selected, dead);
        assert_eq!(
            (world.body.unwrap().hero, world.body.unwrap().skin),
            (selected_hero, selected_skin)
        );
        assert!(scene.simulation().player_body(selected).unwrap().alive());
        scene.simulation_mut().schedule_player_respawn(dead, due);
        world
            .advance_player_respawn(
                &mut scene,
                &viewer,
                FIRST_TICK + 2,
                RETAIL_QUANTA,
                due + Duration::from_secs(1),
            )
            .unwrap();
        assert_eq!(world.active_body_id(), Some(selected));
        assert_eq!(scene.simulation().pending_player_respawn(selected), None);
    }
}

#[test]
fn player_respawn_waits_for_the_old_partial_command_to_finish() {
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut scene, mut world, dead) = respawn_scene(&viewer);
    let due = Duration::from_secs(3);
    scene.simulation_mut().schedule_player_respawn(dead, due);
    let frame = FIRST_TICK + 4;
    world.queue_commands(frame, &[forward(frame)]);
    let (used, _) = world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 1)
        .unwrap();
    assert_eq!(used, 1);
    assert!(matches!(
        world.input.work.front(),
        Some(InputWork::Command {
            pending: Some(_),
            ..
        })
    ));
    world
        .advance_player_respawn(&mut scene, &viewer, frame, RETAIL_QUANTA, due)
        .unwrap();
    assert_eq!(world.active_body_id(), Some(dead));
    assert_eq!(scene.simulation().pending_player_respawn(dead), Some(due));
    world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap();
    assert!(!world.has_input_work());
    world
        .advance_player_respawn(&mut scene, &viewer, frame + 1, RETAIL_QUANTA, due)
        .unwrap();
    let fresh = world.active_body_id().unwrap();
    assert_ne!(fresh, dead);
    assert_eq!(scene.simulation().pending_player_respawn(fresh), None);
}

#[test]
fn rejected_player_respawn_preserves_the_corpse_without_repeating_startup() {
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut scene, mut world, dead) = respawn_scene(&viewer);
    let attempts = Arc::new(AtomicUsize::new(0));
    let startup_attempts = Arc::clone(&attempts);
    scene.owner_body_factory = Some(Arc::new(move |_, _, _| {
        startup_attempts.fetch_add(1, Ordering::Relaxed);
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "synthetic respawn rejection",
        ))
    }));
    let due = Duration::from_secs(3);
    scene.simulation_mut().schedule_player_respawn(dead, due);
    for tick in FIRST_TICK + 1..=FIRST_TICK + 3 {
        world
            .advance_player_respawn(&mut scene, &viewer, tick, RETAIL_QUANTA, due)
            .unwrap();
        assert_eq!(world.active_body_id(), Some(dead));
        assert!(!scene.simulation().player_body(dead).unwrap().alive());
    }
    assert_eq!(attempts.load(Ordering::Relaxed), 1);
    assert_eq!(scene.simulation().pending_player_respawn(dead), None);
}

#[test]
fn player_respawn_converges_when_observer_drops_packets_across_swap() {
    use crate::game_combat::PlayerBodyEvent;
    use crate::game_egress::{DeliveryReport, PacketDelivery};

    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut scene, mut owner_world, dead) = respawn_scene(&viewer);
    let mut observer = WorldDelivery::new(OWNER + 0x100);
    observer.started = Some(FIRST_TICK);
    observer.created.insert(OWNER + 0x100);

    // Observer receives initial body creation.
    for event in scene.simulation_mut().take_player_events() {
        if let PlayerBodyEvent::Created { id, update, .. } = event {
            observer.create_remote_player(id, *update);
        }
    }
    let sent_init: Vec<_> = observer
        .ready()
        .iter()
        .map(|ready| (ready.index, ready.tracked()))
        .collect();
    let (init_indices, init_records): (Vec<_>, Vec<_>) = sent_init.into_iter().unzip();
    observer.sent(&init_indices);
    observer.acknowledge(
        DeliveryReport {
            requeued_reliable: 0,
            owner_correction: None,
            packets: vec![PacketDelivery {
                sequence: 1,
                arrived: true,
                records: init_records,
            }],
        },
        Duration::ZERO,
    );
    assert!(observer.created.contains(&dead.entity));

    // Player dies and respawns via RoomSimulation ECS schedule.
    let due = Duration::from_secs(3);
    scene.simulation_mut().schedule_player_respawn(dead, due);
    owner_world
        .advance_player_respawn(&mut scene, &viewer, FIRST_TICK + 1, RETAIL_QUANTA, due)
        .unwrap();
    let fresh = owner_world.active_body_id().unwrap();
    assert_ne!(fresh, dead);
    let events = scene.simulation_mut().take_player_events();
    for event in events {
        match event {
            PlayerBodyEvent::Created { id, update, .. } => {
                observer.create_remote_player(id, *update);
            }
            PlayerBodyEvent::Retired { id, .. } => {
                observer.retire_remote_player(id);
            }
            PlayerBodyEvent::ScriptHalted { .. } => {}
        }
    }

    // Observer drops the first packet containing the retirement/creation, then catches up.
    let first_batch: Vec<_> = observer
        .ready()
        .iter()
        .map(|ready| (ready.index, ready.tracked()))
        .collect();
    let (drop_indices, drop_records): (Vec<_>, Vec<_>) = first_batch.into_iter().unzip();
    observer.sent(&drop_indices);
    observer.acknowledge(
        DeliveryReport {
            requeued_reliable: 0,
            owner_correction: None,
            packets: vec![PacketDelivery {
                sequence: 2,
                arrived: false,
                records: drop_records,
            }],
        },
        Duration::from_secs(3),
    );
    for seq in 3..=6 {
        let batch: Vec<_> = observer
            .ready()
            .iter()
            .map(|ready| (ready.index, ready.tracked()))
            .collect();
        if batch.is_empty() {
            break;
        }
        let (indices, records): (Vec<_>, Vec<_>) = batch.into_iter().unzip();
        observer.sent(&indices);
        observer.acknowledge(
            DeliveryReport {
                requeued_reliable: 0,
                owner_correction: None,
                packets: vec![PacketDelivery {
                    sequence: seq,
                    arrived: true,
                    records,
                }],
            },
            Duration::from_secs(3),
        );
    }
    assert!(
        !observer.created.contains(&dead.entity),
        "observer should retire dead body after catching up"
    );
    assert!(
        observer.created.contains(&fresh.entity),
        "observer should converge on the same fresh body {fresh:?}"
    );
}
