use super::*;
use crate::{
    game_combat::PlayerDeath,
    game_heroes::HeroCatalog,
    game_parameters::{CLIENT_GAME_MESSAGE, PICK_HERO},
    game_replication::BodyId,
    script_player::fixture_body_factory,
    vitals::Death,
};
use serde::Deserialize;

pub(super) fn acknowledge_world_frame(peer: &mut Peer, sequence: &mut u32) {
    *sequence += 1;
    peer.world
        .send_frame(
            FIRST_TICK,
            Vec::new(),
            false,
            None,
            &mut peer.egress,
            |_| Ok::<_, io::Error>(*sequence),
        )
        .unwrap();
    // Bit zero acknowledges the sequence immediately before the window's next value.
    let report = peer.egress.acknowledge(*sequence + 1, 1);
    peer.world.acknowledge(report, Duration::ZERO);
}

pub(super) fn pick_body(
    peer: &mut Peer,
    scene: &mut PreparedScene,
    viewer: &PlayerAppearance<'_>,
    hero: u64,
) -> BodyId {
    peer.world.queue_game_message(
        FIRST_TICK,
        Duration::ZERO,
        Message {
            id: CLIENT_GAME_MESSAGE,
            fields: Fields::from([
                (
                    0x78,
                    Value::Struct(Fields::from([(
                        0,
                        Value::Unsigned(u64::from(viewer.entity)),
                    )])),
                ),
                (0x80, Value::Blob(hero.to_le_bytes().to_vec())),
                (0xa8, Value::Unsigned(PICK_HERO)),
                (0xb0, Value::Signed(3)),
            ]),
        },
    );
    assert_eq!(
        peer.world
            .advance_input(scene, viewer, RETAIL_QUANTA, 1)
            .unwrap(),
        (1, false)
    );
    peer.world.active_body_id().unwrap()
}

fn kill_body(scene: &mut PreparedScene, body: BodyId, damage: f64) -> PlayerDeath {
    let simulation = scene.simulation_mut();
    let (owner, outcome, _) = simulation
        .damage_player_body_by_wire(
            body,
            damage,
            Death {
                time: 0.0,
                frame: FIRST_TICK,
                killer: body.entity,
            },
            None,
        )
        .unwrap();
    assert!(outcome.killed);
    assert!(!simulation.player_body(body).unwrap().alive());
    PlayerDeath {
        body,
        owner,
        source: body.entity,
        killed_by: false,
    }
}

#[test]
fn bot_hit_feedback_waits_for_possession_and_keeps_its_shooter_generation() {
    use crate::{
        game_combat::AppliedDamage,
        script_entity_state::EntityScriptState,
        script_init::{HealthTargetFacts, HitTags},
        world::{Create, Operation, health},
    };

    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/combat-feedback-owner-body-v1.json"
    ))
    .unwrap();
    let input = &contract["input"];
    let expected = &contract["expected"];
    let first_hero = input["heroes"][0].as_u64().unwrap();
    let next_hero = input["heroes"][1].as_u64().unwrap();
    let target = u32::try_from(input["target"].as_u64().unwrap()).unwrap();
    let amount = input["lethal_damage"].as_f64().unwrap();
    let catalog = HeroCatalog::from_json(
        &serde_json::json!({
            format!("{first_hero:#018x}"): {
                "name": "First fixture hero", "class": "Damage", "body": "0x0400000000000001",
            },
            format!("{next_hero:#018x}"): {
                "name": "Second fixture hero", "class": "Damage", "body": "0x0400000000000001",
            },
        })
        .to_string(),
    )
    .unwrap();
    let mut scene = PreparedScene::fixture_for_owner_admission(catalog);
    let owner = player_entity(0);
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: owner,
        slot: 0,
        team: 0,
        card: &card,
    };
    let mut peer = Peer::new(owner);
    peer.world.start(&scene, &viewer, FIRST_TICK).unwrap();
    peer.world.create_bot(EntityUpdate {
        entity: target,
        operation: Some(Operation::Create(Box::new(Create::new(1, Vec::new())))),
        ..EntityUpdate::default()
    });
    let mut sequence = 0;
    while !peer.world.identity_ready(target) || !peer.world.mode_ready() {
        assert!(sequence < 8, "fixture baseline did not arrive");
        peer.world.present(&scene, FIRST_TICK).unwrap();
        acknowledge_world_frame(&mut peer, &mut sequence);
    }

    let hit = |shooter: BodyId| BotHit {
        damage: AppliedDamage {
            source: shooter.entity,
            amount,
            named_pool_losses: Vec::new(),
            tags: HitTags::EMPTY,
            target_facts: HealthTargetFacts {
                target,
                filter_flags: None,
                room_status: None,
                tags: Default::default(),
                script_state: EntityScriptState::Absent,
            },
        },
        notices: [
            Some(CombatNotice::Hit {
                target,
                dealt: amount,
                critical: false,
                killed: true,
                shot_frame: FIRST_TICK,
            }),
            Some(CombatNotice::Elimination {
                killer: shooter.entity,
                victim: target,
                loadout: 0,
                fire: 100,
                critical: false,
            }),
        ],
    };
    // Only the envelope is needed to exercise reliable admission.
    let schemas = Schemas::from_json(r#"{"8F89E0DF":{"25001":[],"25005":[]}}"#).unwrap();
    let stale = pick_body(&mut peer, &mut scene, &viewer, first_hero);
    peer.bot_hit(hit(stale));
    assert!(!peer.world.health_pending(target));
    peer.world.queue_health(EntityUpdate {
        entity: target,
        operation: Some(Operation::Update(vec![health(
            0.0,
            amount as f32,
            0.0,
            0.0,
        )])),
        ..EntityUpdate::default()
    });
    peer.flush_combat(&schemas, Duration::ZERO, None).unwrap();
    assert_eq!(
        peer.pending_combat_notices().count() as u64,
        expected["pending_before_possession"].as_u64().unwrap(),
    );
    assert!(!peer.egress.has_queued_reliable());

    let live = pick_body(&mut peer, &mut scene, &viewer, next_hero);
    peer.flush_combat(&schemas, Duration::ZERO, None).unwrap();
    assert_eq!(
        peer.pending_combat_notices().count() as u64,
        expected["pending_after_replacement"].as_u64().unwrap(),
    );
    assert!(!peer.egress.has_queued_reliable());
    assert!(peer.world.health_pending(target));

    peer.bot_hit(hit(live));
    while !peer.world.owner_body_ready(live) {
        assert!(sequence < 16, "replacement possession did not arrive");
        acknowledge_world_frame(&mut peer, &mut sequence);
    }
    peer.flush_combat(&schemas, Duration::ZERO, None).unwrap();
    assert_eq!(peer.pending_combat_notices().count(), 0);
    acknowledge_world_frame(&mut peer, &mut sequence);
    let acknowledged = u32::try_from(expected["reliable_notices"].as_u64().unwrap()).unwrap();
    assert!(peer.egress.delivered_through(acknowledged - 1));
    assert!(!peer.egress.delivered_through(acknowledged));
}

#[test]
fn death_notice_is_scoped_to_the_active_body_generation_until_reliable_admission() {
    #[derive(Deserialize)]
    struct Contract {
        schema_version: u32,
        input: Input,
        expected: Expected,
    }
    #[derive(Deserialize)]
    struct Input {
        heroes: [u64; 2],
        lethal_damage: f64,
    }
    #[derive(Deserialize)]
    struct Expected {
        held_before_mode_ack: usize,
        pending_after_replacement: usize,
        admitted_after_mode_ack: u32,
    }

    let Contract {
        schema_version,
        input,
        expected,
    } = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/owner-death-notice-v1.json"
    ))
    .unwrap();
    assert_eq!(schema_version, 1);

    let [first_hero, next_hero] = input.heroes;
    let heroes = HeroCatalog::from_json(
        &serde_json::json!({
            format!("0x{first_hero:016X}"): {
                "name": "First fixture hero", "class": "Damage", "body": "0x0400000000000001",
            },
            format!("0x{next_hero:016X}"): {
                "name": "Second fixture hero", "class": "Damage", "body": "0x0400000000000001",
            },
        })
        .to_string(),
    )
    .unwrap();
    let mut scene = PreparedScene::fixture_for_owner_admission(heroes);
    let (factory, _) = fixture_body_factory();
    scene.owner_body_factory = Some(Arc::new(move |_, spawn, setup| {
        Ok(Some(Box::new(factory.start_with_setup(spawn, setup)?)))
    }));

    let card = PlayerCard::default();
    let owner = player_entity(0);
    let viewer = PlayerAppearance {
        entity: owner,
        slot: 0,
        team: 0,
        card: &card,
    };
    let mut peer = Peer::new(owner);
    peer.world.start(&scene, &viewer, FIRST_TICK).unwrap();
    let mut sequence = 0;
    while !peer.world.identity_ready(owner) || !peer.world.identity_ready(GAME_MODE_ENTITY) {
        assert!(sequence < 8, "world identities were not acknowledged");
        acknowledge_world_frame(&mut peer, &mut sequence);
    }
    peer.world.present(&scene, FIRST_TICK).unwrap();
    assert!(!peer.world.mode_ready());

    // The schema only provides an envelope for this reliable delivery test.
    let schemas = Schemas::from_json(r#"{"8F89E0DF":{"25004":[]}}"#).unwrap();
    let first = pick_body(&mut peer, &mut scene, &viewer, first_hero);
    let death = kill_body(&mut scene, first, input.lethal_damage);
    peer.queue_player_death(death);
    peer.flush_combat(&schemas, Duration::ZERO, Some(scene.simulation()))
        .unwrap();
    assert_eq!(
        peer.pending_combat_notices().count(),
        expected.held_before_mode_ack
    );

    let replacement = pick_body(&mut peer, &mut scene, &viewer, next_hero);
    assert!(replacement.generation > first.generation);
    assert!(scene.simulation().player_body(replacement).unwrap().alive());
    peer.queue_player_death(death);
    peer.queue_player_death(PlayerDeath {
        body: BodyId {
            generation: first.generation,
            ..replacement
        },
        ..death
    });
    assert_eq!(
        peer.pending_combat_notices().count(),
        expected.held_before_mode_ack,
        "a death dispatched late must be rejected, even after recycling a wire ID"
    );
    peer.flush_combat(&schemas, Duration::ZERO, Some(scene.simulation()))
        .unwrap();
    assert_eq!(
        peer.pending_combat_notices().count(),
        expected.pending_after_replacement
    );

    // An actual death of the active replacement still reaches reliable delivery.
    let death = kill_body(&mut scene, replacement, input.lethal_damage);
    peer.queue_player_death(death);
    assert_eq!(
        peer.pending_combat_notices().copied().collect::<Vec<_>>(),
        [CombatNotice::Death {
            source: replacement.entity,
            killed_by: false,
        }]
    );
    while !peer.world.mode_ready() {
        assert!(sequence < 16, "mode UI was not acknowledged");
        acknowledge_world_frame(&mut peer, &mut sequence);
    }
    assert!(!peer.egress.delivered_through(0));
    peer.flush_combat(&schemas, Duration::ZERO, Some(scene.simulation()))
        .unwrap();
    assert_eq!(peer.pending_combat_notices().count(), 0);
    acknowledge_world_frame(&mut peer, &mut sequence);
    assert!(
        peer.egress
            .delivered_through(expected.admitted_after_mode_ack - 1)
    );
    assert!(
        !peer
            .egress
            .delivered_through(expected.admitted_after_mode_ack)
    );
}

#[test]
fn room_respawn_waits_for_death_notice_admission_then_replaces_the_body_once() {
    use crate::{
        contact_world::ContactWorld, game_combat::PlayerBodyEvent, game_heroes::SOLDIER,
        map_environment::EnvironmentCatalog, mover::Mover, mover_settings::MoverSettings,
    };

    let heroes = HeroCatalog::from_json(
        &serde_json::json!({
            format!("0x{SOLDIER:016X}"): {
                "name":"Room respawn fixture", "class":"Damage",
                "body":"0x0400000000000001"
            }
        })
        .to_string(),
    )
    .unwrap();
    let mut scene = PreparedScene::fixture_for_owner_admission(heroes);
    let (factory, _) = fixture_body_factory();
    scene.owner_body_factory = Some(Arc::new(move |_, spawn, setup| {
        Ok(Some(Box::new(factory.start_with_setup(spawn, setup)?)))
    }));

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/owner-death-notice-v1.json"
    ))
    .unwrap();
    let death_position: [f64; 3] =
        serde_json::from_value(fixture["respawn"]["death_position"].clone()).unwrap();
    let delay = Duration::from_millis(fixture["respawn"]["practice_delay_ms"].as_u64().unwrap());
    let map_guid = scene.map().map_guid;
    let mode_guid = scene.map().mode_guid;
    let override_file = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(
        override_file.path(),
        serde_json::json!({"version":1,"maps":{
            format!("0x{map_guid:016X}"):{
                "lethal_volumes":[{
                    "min":death_position.map(|axis| axis - 1.0),
                    "max":death_position.map(|axis| axis + 1.0)
                }]
            }
        }})
        .to_string(),
    )
    .unwrap();
    let environment = EnvironmentCatalog::load(override_file.path())
        .unwrap()
        .for_map(map_guid);
    assert_eq!(environment.player_respawn_delay(mode_guid), Some(delay));
    scene.simulation_mut().set_environment(environment);

    let card = PlayerCard::default();
    let owner = player_entity(0);
    let mut peer = Peer::new(owner);
    let mut sequence = 0;
    let old_body = {
        let viewer = PlayerAppearance {
            entity: owner,
            slot: 0,
            team: 0,
            card: &card,
        };
        peer.world.start(&scene, &viewer, FIRST_TICK).unwrap();
        while !peer.world.identity_ready(owner) || !peer.world.identity_ready(GAME_MODE_ENTITY) {
            assert!(sequence < 8, "world identities were not acknowledged");
            acknowledge_world_frame(&mut peer, &mut sequence);
        }
        peer.world.present(&scene, FIRST_TICK).unwrap();
        assert!(!peer.world.mode_ready());
        pick_body(&mut peer, &mut scene, &viewer, SOLDIER)
    };
    let initial_pose = scene
        .simulation()
        .player_body(old_body)
        .unwrap()
        .mover
        .pose();
    drop(scene.simulation_mut().take_player_events());
    scene
        .simulation_mut()
        .player_body_mut(old_body)
        .unwrap()
        .mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        death_position,
        0,
        0x18,
    )
    .unwrap();

    let mut room = Room {
        map: scene.map().clone(),
        players: vec![Player {
            info: player().pop().unwrap(),
            entity: owner,
            room_slot: None,
            gone: false,
        }],
        members: IndexMap::from([(
            1,
            RoomMember {
                slot: 0,
                peer: Some(peer),
            },
        )]),
        world: Some(RoomWorld {
            scene: Box::new(scene),
            cards: vec![card],
            patrol: PatrolScript::load().unwrap(),
            patrol_generation: 0,
        }),
        debug_control: room::RoomDebugControl::new(FIRST_TICK, Duration::ZERO),
        injection: None,
    };
    let updates = room
        .finish_simulation(FIRST_TICK + 1, Duration::ZERO)
        .unwrap();
    assert!(updates.player_events.is_empty());
    let peer = room.members[&1].peer.as_ref().unwrap();
    assert_eq!(peer.world.active_body_id(), Some(old_body));
    assert_eq!(
        room.world
            .as_ref()
            .unwrap()
            .scene
            .simulation()
            .pending_player_respawn(old_body),
        Some(delay)
    );
    assert_eq!(peer.pending_combat_notices().count(), 1);
    assert!(
        !room
            .world
            .as_ref()
            .unwrap()
            .scene
            .simulation()
            .player_body(old_body)
            .unwrap()
            .alive()
    );

    let schemas = Schemas::from_json(r#"{"8F89E0DF":{"25004":[]}}"#).unwrap();
    {
        let world = room.world.as_ref().unwrap();
        let peer = room.members.get_mut(&1).unwrap().peer.as_mut().unwrap();
        peer.flush_combat(&schemas, Duration::ZERO, Some(world.scene.simulation()))
            .unwrap();
        assert_eq!(peer.pending_combat_notices().count(), 1);
    }
    let updates = room.finish_simulation(FIRST_TICK + 2, delay).unwrap();
    assert!(updates.player_events.is_empty());
    let peer = room.members[&1].peer.as_ref().unwrap();
    assert_eq!(peer.world.active_body_id(), Some(old_body));
    assert_eq!(
        room.world
            .as_ref()
            .unwrap()
            .scene
            .simulation()
            .pending_player_respawn(old_body),
        Some(delay)
    );

    {
        let world = room.world.as_ref().unwrap();
        let peer = room.members.get_mut(&1).unwrap().peer.as_mut().unwrap();
        while !peer.world.mode_ready() {
            assert!(sequence < 24, "mode UI was not acknowledged");
            acknowledge_world_frame(peer, &mut sequence);
        }
        assert_eq!(peer.pending_combat_notices().count(), 1);
        assert!(!peer.egress.delivered_through(0));
        peer.flush_combat(&schemas, delay, Some(world.scene.simulation()))
            .unwrap();
        assert_eq!(peer.pending_combat_notices().count(), 0);
        assert!(!peer.egress.delivered_through(0));
        // The death is now admitted to reliable delivery. No packet containing
        // it is acknowledged before the replacement is requested below.
    }

    let replacement_time = delay + Duration::from_millis(16);
    let updates = room
        .finish_simulation(FIRST_TICK + 3, replacement_time)
        .unwrap();
    let peer = room.members[&1].peer.as_ref().unwrap();
    let fresh = peer.world.active_body_id().unwrap();
    assert_ne!(fresh, old_body);
    assert!(fresh.generation > old_body.generation);
    assert_eq!(
        room.world
            .as_ref()
            .unwrap()
            .scene
            .simulation()
            .pending_player_respawn(old_body),
        None
    );
    assert!(!peer.egress.delivered_through(0));
    assert!(matches!(
        updates.player_events.as_slice(),
        [
            PlayerBodyEvent::Retired { owner: retired_owner, id: retired },
            PlayerBodyEvent::Created { owner: created_owner, id: created, .. }
        ] if *retired_owner == owner && *created_owner == owner
            && *retired == old_body && *created == fresh
    ));
    let simulation = room.world.as_ref().unwrap().scene.simulation();
    assert!(simulation.player_body(old_body).is_none());
    let replacement = simulation.player_body(fresh).unwrap();
    assert!(replacement.alive());
    let health = replacement.vitals().unwrap();
    assert_eq!(health.total(), health.maximum());
    assert_eq!(replacement.mover.pose().position, initial_pose.position);

    let updates = room
        .finish_simulation(FIRST_TICK + 4, replacement_time + delay)
        .unwrap();
    assert!(updates.player_events.is_empty());
    let peer = room.members[&1].peer.as_ref().unwrap();
    assert_eq!(peer.world.active_body_id(), Some(fresh));
    assert_eq!(
        room.world
            .as_ref()
            .unwrap()
            .scene
            .simulation()
            .pending_player_respawn(fresh),
        None
    );
}
