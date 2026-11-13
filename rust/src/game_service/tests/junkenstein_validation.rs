//! Prepared owner pools through the actual room's post-AI damage dispatcher.

use super::*;
use crate::{
    game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
    game_egress::{DeliveryReport, PacketDelivery, RecordSource},
    game_parameters::{CLIENT_GAME_MESSAGE, PICK_HERO},
    junkenstein_validation::JunkensteinValidation,
    scene_assets::SceneAssets,
    script_enemy::EnemyDamage,
};

fn acknowledge_ready(world: &mut WorldDelivery, scene: &PreparedScene) {
    for _ in 0..4 {
        let ready = world.ready();
        let sent: Vec<_> = (0..ready.len()).map(RecordSource::Queued).collect();
        let indexes = WorldDelivery::sent_indices(&ready, &sent);
        let records = ready.iter().map(|record| record.tracked()).collect();
        world.sent(&indexes);
        world.acknowledge(
            DeliveryReport {
                requeued_reliable: 0,
                packets: vec![PacketDelivery {
                    sequence: 0,
                    arrived: true,
                    records,
                }],
            },
            Duration::ZERO,
        );
        world.present(scene, FIRST_TICK).unwrap();
    }
}

#[test]
#[ignore = "requires the prepared 104319 Junkenstein runtime"]
fn configured_cassidy_immunity_routes_real_room_damage_and_death_notices() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/junkenstein-validation-v1.json"
    ))
    .unwrap();
    let input = &fixture["room_damage"];
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let maps = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
    for case in input["cases"].as_array().unwrap() {
        let mut assets = SceneAssets::load(&runtime).unwrap();
        if !case["settings"].is_null() {
            let file = tempfile::NamedTempFile::new().unwrap();
            std::fs::write(file.path(), serde_json::to_vec(&case["settings"]).unwrap()).unwrap();
            assets.set_junkenstein_validation(JunkensteinValidation::load(file.path()).unwrap());
        }
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
        let scene = assets.prepare_junkenstein(map, card, 0).unwrap();
        let hero = scene.heroes().find_hero("Cassidy").unwrap().guid;
        let owner = player_entity(0);
        let appearance_card = PlayerCard::default();
        let viewer = PlayerAppearance {
            entity: owner,
            slot: 0,
            team: 0,
            card: &appearance_card,
        };
        let mut peer = Peer::new(owner);
        peer.world.start(&scene, &viewer, FIRST_TICK).unwrap();
        acknowledge_ready(&mut peer.world, &scene);
        peer.world.queue_game_message(
            FIRST_TICK,
            Duration::ZERO,
            Message {
                id: CLIENT_GAME_MESSAGE,
                fields: Fields::from([
                    (
                        0x78,
                        Value::Struct(Fields::from([(0, Value::Unsigned(u64::from(owner)))])),
                    ),
                    (0x80, Value::Blob(hero.to_le_bytes().to_vec())),
                    (0xa8, Value::Unsigned(PICK_HERO)),
                    (0xb0, Value::Signed(3)),
                ]),
            },
        );
        assert_eq!(
            peer.world
                .advance_input(&scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap(),
            (1, false)
        );
        assert_eq!(
            peer.world.debug_actor(0).unwrap().health,
            input["initial_health"].as_f64()
        );
        let body = peer.world.spawned_body().unwrap();
        let mut room = Room {
            map: scene.map().clone(),
            players: vec![Player {
                info: player().pop().unwrap(),
                entity: owner,
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
                card: PlayerCard::default(),
                patrol: PatrolScript::load().unwrap(),
            }),
            injection: None,
        };
        for (step, amount) in input["amounts"].as_array().unwrap().iter().enumerate() {
            let tick = FIRST_TICK + step as u32 + 1;
            room.world
                .as_mut()
                .unwrap()
                .scene
                .bot_simulation()
                .0
                .queue_test_external_enemy_damage(EnemyDamage {
                    source: input["source"].as_u64().unwrap() as u32,
                    target: body,
                    amount: amount.as_f64().unwrap(),
                });
            room.finish_simulation(tick, Duration::from_millis(u64::from(tick) * 16))
                .unwrap();
            let peer = room.members[&1].peer.as_ref().unwrap();
            let actor = peer.world.debug_actor(0).unwrap();
            assert_eq!(
                actor.health,
                case["health"][step].as_f64(),
                "case {case}, step {step}"
            );
            assert_eq!(actor.alive, case["alive"][step].as_bool().unwrap());
            let deaths = peer
                .combat_ui
                .notices
                .iter()
                .filter(|notice| {
                    matches!(
                        notice,
                        CombatNotice::Death {
                            killed_by: true,
                            ..
                        }
                    )
                })
                .count();
            assert_eq!(
                deaths,
                case["death_notices"][step].as_u64().unwrap() as usize
            );
            assert!(
                room.world
                    .as_mut()
                    .unwrap()
                    .scene
                    .bot_simulation()
                    .0
                    .take_external_enemy_damage()
                    .is_empty()
            );
        }
    }
}
