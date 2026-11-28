use super::*;
use crate::{game_clock::TICK_PERIOD, patrol_script::PatrolScript};
struct Trial {
    button: u8,
    graph: u32,
    height: f64,
    distance: f64,
    pitch: i32,
    hold_frames: u32,
}

#[derive(Default)]
struct Impact {
    notices: Vec<(u32, f64)>,
    health_before: f64,
    health_after: f64,
    max_rise: f64,
    max_forward_displacement: f64,
    slam_damage: Option<f64>,
    first_velocity: Option<[f64; 2]>,
    target_scale: Option<f64>,
}

#[test]
#[ignore = "requires the private 104319 Practice Range and stock Doomfist graphs"]
fn prepared_doomfist_rocket_punch_hits_a_room_bot() {
    let impact = exercise(Trial {
        button: 29,
        graph: 0x0d06,
        height: 0.0,
        distance: 4.0,
        pitch: 0,
        hold_frames: 50,
    });
    assert!(!impact.notices.is_empty());
    assert!(impact.health_after < impact.health_before);
    assert!(
        impact.max_forward_displacement > 0.2,
        "target moved {} m along punch",
        impact.max_forward_displacement
    );
}

#[test]
#[ignore = "requires the private 104319 Practice Range and stock Doomfist graphs"]
fn prepared_doomfist_seismic_slam_hits_a_room_bot() {
    let impact = exercise(Trial {
        button: 36,
        graph: 0x0b27,
        height: 1.0,
        distance: 6.0,
        pitch: 0x2000,
        hold_frames: 4,
    });
    let [(_, dealt)] = impact.notices.as_slice() else {
        panic!("expected one player hit, got {:?}", impact.notices)
    };
    let authored = impact
        .slam_damage
        .expect("the landing graph set its damage");
    assert!((dealt - authored).abs() < 0.001);
    assert!((impact.health_before - impact.health_after - authored).abs() < 0.001);
    assert!(impact.health_after > 0.0);
    assert!(impact.max_rise > 0.2, "target rose {} m", impact.max_rise);
    let [away, up] = impact
        .first_velocity
        .expect("first post-hit target movement");
    assert!(
        away < -1.0 && (up - 6.0).abs() < 1.0,
        "Slam target radial/up velocities {away}/{up}"
    );
}

#[test]
#[ignore = "requires the private 104319 Practice Range and stock Doomfist graphs"]
fn prepared_doomfist_rising_uppercut_applies_authored_target_velocity() {
    let impact = exercise(Trial {
        button: 35,
        graph: 0x0dd8,
        height: 0.0,
        distance: 2.0,
        pitch: 0,
        hold_frames: 3,
    });
    assert!(
        matches!(impact.notices.as_slice(), [(_, dealt)] if (*dealt-50.0).abs()<0.001),
        "uppercut contacts {:?}",
        impact.notices
    );
    let [away, up] = impact
        .first_velocity
        .expect("first post-hit target movement");
    let scale = impact
        .target_scale
        .expect("stock uppercut target velocity scale");
    assert!(
        scale > 0.0 && (away - 7.0 * scale).abs() < 1.0 && (up - 14.0 * scale).abs() < 1.0,
        "uppercut radial/up velocities {away}/{up}, scale {scale}"
    );
}

fn exercise(trial: Trial) -> Impact {
    let Trial {
        button,
        graph,
        height,
        distance: approach,
        pitch,
        hold_frames: held,
    } = trial;
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene.heroes().get(0x02e0_0000_0000_012f).unwrap().clone();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut world, id, mut tick) =
        select_practice_hero(&mut scene, &viewer, hero.guid, &mut GameEgress::default());
    let target = crate::game_bots::BOT_ENTITY + 7;
    let source = scene.simulation_mut().body_mut(target).unwrap().position;
    let [x, y, z] = source;
    let yaw = 0x8000;
    let aim = crate::game_hitscan::direction_for_angles(yaw, 0);
    let press = 165;
    let buttons = crate::script_runtime::held_button_mask(button).unwrap();
    let patrol = PatrolScript::load().unwrap();
    let mut impact = Impact::default();
    let mut radial = None;
    for step in 0..press + held + 100 {
        if step + 1 == press {
            impact.health_before = scene.simulation().body_healths().unwrap()[&target];
            let mover = scene
                .player_spawn_mover_at(
                    hero.body,
                    viewer.team,
                    [x - aim[0] * approach, y + height, z - aim[2] * approach],
                    yaw,
                )
                .unwrap();
            scene.simulation_mut().player_body_mut(id).unwrap().mover = mover;
        }
        tick += 1;
        scene.simulation_mut().prepare_movement_frame(tick).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                yaw,
                pitch,
                buttons: if (press..press + held).contains(&step) {
                    buttons
                } else {
                    0
                },
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let player = scene.simulation().player_body(id).unwrap();
        let runtime = player.runtime.as_ref().unwrap_or_else(|| {
            panic!(
                "frame {step}: {:?}",
                world.owner_script_fault.as_ref().map(|fault| &fault.error)
            )
        });
        if graph == 0x0b27 && step >= press && impact.slam_damage.is_none() {
            let snapshot = runtime.snapshot().unwrap();
            let instance = snapshot
                .instances
                .values()
                .find(|instance| instance.graph == graph)
                .unwrap();
            if let Some(ScriptValue::Float(amount)) = instance.variables.get(&476) {
                if *amount > 0.0 {
                    impact.slam_damage = Some(*amount);
                }
            }
        }
        let caster_pos = player.mover.pose().position;
        if graph == 0x0dd8 && impact.target_scale.is_none() {
            if let Some(scale) = runtime.encoder().graphs.mode_ruleset_value(
                crate::game_content::PRACTICE_RANGE_MODE,
                hero.guid,
                10958,
            ) {
                impact.target_scale = match scale {
                    ScriptValue::Float(n) => Some(n),
                    ScriptValue::Integer(n) => Some(n as f64),
                    _ => None,
                };
            }
        }
        scene.step_bots(tick, &patrol).unwrap();
        let (room, q) = scene.bot_simulation();
        room.finish_tick(&q, tick, TICK_PERIOD * tick).unwrap();
        let pos = room.body_mut(target).unwrap().position;
        if let Some((hit_step, from, away)) = radial {
            let from: [f64; 3] = from;
            let away: [f64; 2] = away;
            if step == hit_step + 1 {
                let dt = f64::from(RETAIL_QUANTA) / 1_000_000.0;
                impact.first_velocity = Some([
                    ((pos[0] - from[0]) * away[0] + (pos[2] - from[2]) * away[1]) / dt,
                    (pos[1] - from[1]) / dt,
                ]);
            }
        }
        if !impact.notices.is_empty() {
            impact.max_rise = impact.max_rise.max(pos[1] - y);
            impact.max_forward_displacement = impact
                .max_forward_displacement
                .max((pos[0] - x) * aim[0] + (pos[2] - z) * aim[2]);
        }
        for cue in room.take_player_cues() {
            if cue.owner == OWNER {
                if let crate::game_combat_messages::CombatNotice::Hit {
                    target: bot, dealt, ..
                } = cue.notice
                {
                    if bot == target && dealt > 0.0 {
                        assert!(step >= press, "target hit before the ability was pressed");
                        impact.notices.push((step, dealt));
                        let dx = pos[0] - caster_pos[0];
                        let dz = pos[2] - caster_pos[2];
                        let length = dx.hypot(dz);
                        if length > 0.0001 && radial.is_none() {
                            radial = Some((step, pos, [dx / length, dz / length]));
                        }
                    }
                }
            }
        }
    }
    impact.health_after = scene.simulation().body_healths().unwrap()[&target];
    eprintln!(
        "radial/up velocity {:?}, scale {:?}",
        impact.first_velocity, impact.target_scale
    );
    eprintln!(
        "graph {graph:04X} hits {:?}, hp {} -> {}, rise {:.3} m, forward {:.3} m",
        impact.notices,
        impact.health_before,
        impact.health_after,
        impact.max_rise,
        impact.max_forward_displacement
    );
    impact
}
