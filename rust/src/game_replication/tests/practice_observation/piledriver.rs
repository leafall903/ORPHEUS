use super::*;
use crate::{game_bots::BOT_ENTITY, game_clock::TICK_PERIOD, patrol_script::PatrolScript};
use std::collections::BTreeMap;

#[test]
#[ignore = "requires stock 104319 Practice Range, hero graphs and movers"]
fn prepared_piledriver_uses_capsule_ranges_and_corrects_enemy_owner() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let caster = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let enemy = PlayerAppearance {
        entity: OWNER + 0x100,
        slot: 1,
        team: 1,
        card: &PlayerCard::default(),
    };
    let ball = scene
        .heroes()
        .get(crate::game_heroes::WRECKING_BALL)
        .unwrap()
        .clone();
    let victim = scene
        .heroes()
        .get(crate::game_heroes::TRACER)
        .unwrap()
        .clone();
    let (mut world, id, caster_tick) =
        select_practice_hero(&mut scene, &caster, ball.guid, &mut GameEgress::default());
    let mut egress = GameEgress::default();
    let (mut observer, victim_id, victim_tick) =
        select_practice_hero(&mut scene, &enemy, victim.guid, &mut egress);
    let roster = crate::game_instance::TeamMemberCounts::from_teams(
        scene.map().free_for_all,
        [caster.team, enemy.team],
    );
    scene.simulation_mut().set_team_member_counts(roster);
    let mut tick = caster_tick.max(victim_tick);
    let patrol = PatrolScript::load().unwrap();
    let bot = BOT_ENTITY + 7;
    let [x, y, z] = scene.simulation_mut().body_mut(bot).unwrap().position;
    let crouch = crate::script_runtime::held_button_mask(12).unwrap();
    let mut damage = BTreeMap::new();
    let mut distances = BTreeMap::new();
    let mut victim_at_hit = None;
    let mut center = None;
    let mut sent_correction = false;
    let mut rise = 0.0_f64;
    let mut toward_center = 0.0_f64;
    for step in 0..200 {
        if step == 90 {
            let release = scene
                .player_spawn_mover_at(ball.body, caster.team, [x, y + 5.0, z + 3.0], 0x8000)
                .unwrap();
            // One side of this stock platform drops to the level below. Place the
            // idle player on a walkable patch at the bot's elevation.
            let waiting_position = [[0.0, 4.5], [-4.5, 0.0], [0.0, -4.5], [4.5, 0.0]]
                .into_iter()
                .find_map(|[dx, dz]| {
                    let ground = scene.movement_index().ground(
                        x + dx,
                        z + dz,
                        y + 0.7,
                        y - 0.35,
                        crate::scene_assets::MOVER_CATEGORY,
                    )?;
                    Some([x + dx, ground.y, z + dz])
                })
                .expect("a level patch on the stock platform");
            let waiting = scene
                .player_spawn_mover_at(victim.body, enemy.team, waiting_position, 0)
                .unwrap();
            scene.simulation_mut().player_body_mut(id).unwrap().mover = release;
            scene
                .simulation_mut()
                .player_body_mut(victim_id)
                .unwrap()
                .mover = waiting;
        }
        tick += 1;
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        let frame = scene.simulation().spatial_frame().unwrap();
        let cmd = Command {
            frame: tick,
            ..Command::default()
        };
        observer.queue_commands(tick, &[cmd]);
        observer
            .advance_input(&mut scene, &enemy, RETAIL_QUANTA, 16)
            .unwrap();
        let p = scene.simulation().player_body(victim_id).unwrap();
        let pos = p.mover.pose().position;
        if let (Some((hit_tick, origin)), Some(c)) = (victim_at_hit, center) {
            let origin: [f64; 3] = origin;
            let c: [f64; 3] = c;
            rise = rise.max(pos[1] - origin[1]);
            let before = (origin[0] - c[0]).hypot(origin[2] - c[2]);
            let after = (pos[0] - c[0]).hypot(pos[2] - c[2]);
            toward_center = toward_center.max(before - after);
            if tick == hit_tick + 1 && p.mover.needs_owner_correction_at(tick) {
                let c = observer
                    .body_script
                    .as_ref()
                    .unwrap()
                    .corrections
                    .as_ref()
                    .unwrap()
                    .candidate(tick, true)
                    .expect("victim correction recorded");
                assert_eq!(c.update().movement.unwrap().position, pos);
                let (_, packet) = transmit(
                    &mut observer,
                    Some(scene.simulation_mut()),
                    &mut egress,
                    tick,
                    tick - FIRST_TICK,
                );
                sent_correction = packet.records.contains(&RecordSource::Movement(0));
                transport_outcome(&mut observer, &mut egress, packet.sequence, true);
            }
        }
        let cmd = Command {
            frame: tick,
            yaw: 0x8000,
            buttons: if (95..=98).contains(&step) { crouch } else { 0 },
            ..Default::default()
        };
        world.queue_commands(tick, &[cmd]);
        world
            .advance_input(&mut scene, &caster, RETAIL_QUANTA, 16)
            .unwrap();
        let player = scene.simulation().player_body(id).unwrap();
        let snap = player.runtime.as_ref().unwrap().snapshot().unwrap();
        let state = snap.instances.values().find(|i| i.graph == 0x160a).unwrap();
        if let (Some(ScriptValue::Array(ids)), Some(ScriptValue::Array(ranges))) =
            (state.variables.get(&375), state.variables.get(&9326))
        {
            assert_eq!(ids.len(), ranges.len());
            if distances.is_empty() && !ids.is_empty() {
                let pos = player.mover.pose().position;
                let origin = [pos[0], pos[1] + 0.1, pos[2]];
                center = Some(origin);
                for (id, dist) in ids.iter().zip(ranges) {
                    let (ScriptValue::Entity(body), ScriptValue::Float(actual)) = (id, dist) else {
                        panic!("invalid tracking array")
                    };
                    let feet = frame.actor_pose(*body).unwrap().0;
                    let cap = frame.actor_target_pose(*body, feet);
                    let height = (origin[1] - feet[1])
                        .clamp(cap.radius, cap.radius.max(cap.height - cap.radius));
                    let expected = ((origin[0] - feet[0])
                        .hypot(origin[2] - feet[2])
                        .hypot(origin[1] - feet[1] - height)
                        - cap.radius)
                        .max(0.0);
                    assert!(
                        (actual - expected).abs() < 0.005,
                        "body {body:x} capsule {cap:?} origin={origin:?} reported {actual} expected {expected}"
                    );
                    distances.insert(*body, *actual);
                }
            }
        }
        scene.step_bots(tick, &patrol).unwrap();
        let (room, queries) = scene.bot_simulation();
        room.finish_tick(&queries, tick, TICK_PERIOD * tick)
            .unwrap();
        for cue in room.take_player_cues() {
            if cue.owner != caster.entity {
                continue;
            }
            if let crate::game_combat_messages::CombatNotice::Hit { target, dealt, .. } = cue.notice
            {
                if target == bot || target == victim_id.entity {
                    assert!(
                        damage.insert(target, dealt).is_none(),
                        "duplicate landing damage"
                    );
                    if target == victim_id.entity {
                        victim_at_hit = Some((tick, pos));
                    }
                }
            }
        }
    }
    assert!(
        distances.contains_key(&bot) && distances.contains_key(&victim_id.entity),
        "tracking {distances:?}, damage {damage:?}"
    );
    // The original landing graph interpolates 100 -> 20 from 1.5 -> 8 metres.
    assert!(distances[&bot] < 1.5 && distances[&victim_id.entity] > 1.5);
    for (body, distance) in &distances {
        if let Some(dealt) = damage.get(body) {
            let expected = 100.0 - 80.0 * ((distance - 1.5) / (8.0 - 1.5)).clamp(0.0, 1.0);
            assert!(
                (dealt - expected).abs() < 0.001,
                "target {body:x}: {dealt} vs {expected}"
            );
        }
    }
    assert_eq!(damage.len(), 2, "both targeted bodies must take damage");
    assert!(
        rise > 0.5 && toward_center > 0.05 && sent_correction,
        "rise={rise} toward={toward_center} correction={sent_correction}"
    );
    eprintln!(
        "landing distances={distances:?} damage={damage:?} enemy rise={rise:.3} inward={toward_center:.3} corrected={sent_correction}"
    );
}
