//! Build 104319: 01CA's reset entry binds C6 to local 2227 and can arm the
//! 1.5 s wait (raw state 8). HeroSelect and Respawn stimuli also declare a
//! second criterion, 202F, with a different flag. Its native meaning and
//! the original human speaker route remain open.
use super::*;
use crate::{
    game_clock::TICK_PERIOD, game_combat_messages::CombatNotice, patrol_script::PatrolScript,
};

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
enum Phase {
    FirstPick,
    Respawn,
    HeroSwitch,
    SkinRefresh,
}

#[test]
#[ignore = "requires the private 104319 voice catalog and Practice Range runtime"]
fn prepared_respawn_reaches_the_voice_graph_once_per_natural_respawn() {
    let dir = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&dir).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let catalog =
        crate::actor_voice::ActorVoiceCatalog::load(&dir.join(crate::runtime::ACTOR_VOICE_CATALOG))
            .unwrap()
            .expect("requires actor voice catalog");
    scene.simulation_mut().enable_actor_voice(Arc::new(catalog));
    let default_card = PlayerCard::default();
    let mut skin_card = PlayerCard::default();
    skin_card.skins.insert(
        CASSIDY,
        SkinTheme {
            guid: 0x0350_0000_0000_1ec5,
            ..Default::default()
        },
    );
    let mut viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &default_card,
    };
    let mut egress = GameEgress::default();
    let (mut world, first, mut tick) =
        select_practice_hero(&mut scene, &viewer, SOLDIER, &mut egress);
    let patrol = PatrolScript::load().unwrap();
    let mut previous = None;
    for phase in [
        Phase::FirstPick,
        Phase::Respawn,
        Phase::HeroSwitch,
        Phase::SkinRefresh,
    ] {
        let now = TICK_PERIOD * tick;
        match phase {
            Phase::Respawn => {
                let (_, outcome, _) = scene
                    .simulation_mut()
                    .damage_player_body_by_wire(
                        first,
                        f64::MAX,
                        crate::vitals::Death {
                            time: now.as_secs_f64(),
                            frame: tick,
                            killer: 0,
                        },
                        None,
                    )
                    .unwrap();
                assert!(outcome.killed);
                scene.simulation_mut().schedule_player_respawn(first, now);
                world
                    .advance_player_respawn(&mut scene, &viewer, tick, RETAIL_QUANTA, now)
                    .unwrap();
            }
            Phase::HeroSwitch => {
                world.queue_game_message(tick, now, pick_hero(viewer.entity, CASSIDY));
                world
                    .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                    .unwrap();
            }
            Phase::SkinRefresh => {
                viewer.card = &skin_card;
                world.queue_skin(CASSIDY, tick, now);
                world
                    .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                    .unwrap();
            }
            Phase::FirstPick => (),
        }
        let current = world.active_body_id().unwrap();
        assert_ne!(previous, Some(current));
        previous = Some(current);
        let mut reset_seen = false;
        let mut wait_starts = std::collections::BTreeSet::new();
        let mut reset_diagnostics = std::collections::BTreeSet::new();
        let mut voices = Vec::new();
        for offset in 0..135 {
            tick += 1;
            scene.simulation_mut().prepare_movement_frame(tick).unwrap();
            scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
            // The same pick after possession and loss of a lifecycle packet must not replay it.
            if offset == 16 {
                let hero = if matches!(phase, Phase::HeroSwitch | Phase::SkinRefresh) {
                    CASSIDY
                } else {
                    SOLDIER
                };
                world.queue_game_message(tick, TICK_PERIOD * tick, pick_hero(viewer.entity, hero));
            }
            world.queue_commands(
                tick,
                &[Command {
                    frame: tick,
                    ..Default::default()
                }],
            );
            world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                .unwrap();
            scene.step_bots(tick, &patrol).unwrap();
            let (room, queries) = scene.bot_simulation();
            room.finish_tick(&queries, tick, TICK_PERIOD * tick)
                .unwrap();
            let snapshot = room
                .player_body(current)
                .unwrap()
                .runtime
                .as_ref()
                .unwrap()
                .snapshot()
                .unwrap();
            for diagnostic in &snapshot.diagnostics {
                if diagnostic.site.graph == 0x01ca && diagnostic.site.node == Some(176) {
                    reset_diagnostics.insert(format!("{:?}", diagnostic.issue));
                }
            }
            let graph = snapshot
                .instances
                .values()
                .find(|instance| instance.graph == 0x01ca)
                .expect("the stock body mounted its voice graph");
            reset_seen |= graph.variables.get(&2227) == Some(&ScriptValue::Bool(true));
            if let Some(state) = graph.states.get(&8).filter(|state| state.active) {
                wait_starts.insert(state.start_ms);
            }
            for cue in room.take_player_cues() {
                if let CombatNotice::Voice {
                    speaker,
                    listener,
                    stimulus,
                    ..
                } = cue.notice
                {
                    let kind = stimulus & 0xffff;
                    if matches!(kind, 0x20 | 0x2b | 0x2c) {
                        assert_eq!(
                            (speaker, listener, cue.owner, cue.listener),
                            (current.entity, current.entity, viewer.entity, current)
                        );
                        voices.push(kind);
                    }
                }
            }
            let (_, sent) = transmit(&mut world, Some(room), &mut egress, tick, tick - FIRST_TICK);
            transport_outcome(&mut world, &mut egress, sent.sequence, offset > 1);
            assert_eq!(world.active_body_id(), Some(current));
        }
        eprintln!(
            "{phase:?}: body={current:?}, C6={reset_seen}, waits={wait_starts:?}, switch={reset_diagnostics:?}, received={voices:X?}"
        );
        let respawn = phase == Phase::Respawn;
        assert_eq!(reset_seen, respawn);
        // The Practice Range takes the switch's default branch once. ACK loss,
        // a repeated pick and a skin-only refresh cannot arm its timer again.
        assert_eq!(wait_starts.len(), usize::from(respawn));
        // Human voice playback is pending independent evidence. Flag wrong or duplicate
        // deliveries while allowing a future authenticated event of the correct kind.
        assert!(voices.len() <= 1);
        assert!(voices.iter().all(|&kind| match phase {
            Phase::FirstPick | Phase::HeroSwitch => kind == 0x2b,
            Phase::Respawn => kind == 0x2c,
            Phase::SkinRefresh => false,
        }));
    }
}
