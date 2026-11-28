use super::*;
use crate::{game_bots::BOT_ENTITY, game_clock::TICK_PERIOD, patrol_script::PatrolScript};

fn quad_cannons_ammo(body: &PlayerBodyState) -> i64 {
    let snapshot = body.runtime.as_ref().unwrap().snapshot().unwrap();
    let (&id, weapon) = snapshot
        .instances
        .iter()
        .find(|(_, inst)| inst.graph == 0x1514 && inst.parent.is_none())
        .expect("stock Quad Cannons root");
    let value = snapshot
        .resolved_bindings
        .get(&(id, 53))
        .or_else(|| weapon.variables.get(&53));
    match value {
        Some(ScriptValue::Integer(ammo)) => *ammo,
        other => panic!("unexpected Quad Cannons ammo value: {other:?}"),
    }
}

fn roll_fireball_tier(body: &PlayerBodyState) -> (i64, bool, bool) {
    let snapshot = body.runtime.as_ref().unwrap().snapshot().unwrap();
    let roll = snapshot
        .instances
        .values()
        .find(|inst| inst.graph == 0x1203)
        .expect("stock Roll graph 0x1203");
    let tier = match snapshot
        .resolved_bindings
        .get(&(0, 15252))
        .or_else(|| snapshot.variables.get(&15252))
    {
        Some(ScriptValue::Integer(v)) => *v,
        _ => 0,
    };
    let boosted = matches!(
        snapshot
            .resolved_bindings
            .get(&(0, 15250))
            .or_else(|| snapshot.variables.get(&15250)),
        Some(ScriptValue::Bool(true))
    );
    let fireball_active = roll.states.get(&37).is_some_and(|s| s.active);
    (tier, fireball_active, boosted)
}

#[test]
#[ignore = "requires stock 104319 Practice Range, Wrecking Ball graphs and movers"]
fn prepared_wrecking_ball_quad_cannons_and_roll_auto_reload() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene
        .heroes()
        .get(crate::game_heroes::WRECKING_BALL)
        .unwrap()
        .clone();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut world, id, mut tick) =
        select_practice_hero(&mut scene, &viewer, hero.guid, &mut GameEgress::default());
    let patrol = PatrolScript::load().unwrap();
    let bot = BOT_ENTITY + 7;
    let [bx, by, bz] = scene.simulation_mut().body_mut(bot).unwrap().position;
    let roll_button = crate::script_runtime::held_button_mask(35).unwrap();
    let spawn_mover = scene
        .player_spawn_mover_at(hero.body, viewer.team, [bx, by, bz + 4.5], 0x8000)
        .unwrap();
    scene.simulation_mut().player_body_mut(id).unwrap().mover = spawn_mover;

    let initial_ammo = quad_cannons_ammo(scene.simulation().player_body(id).unwrap());
    assert_eq!(initial_ammo, 80, "Quad Cannons should start at 80 ammo");

    let mut bot_hits = 0usize;
    let mut ammo_after_fire = initial_ammo;
    let mut rolled_active = false;
    let mut ammo_after_roll_reload = 0;
    let mut unrolled_on_fire = false;
    let mut ammo_after_unroll_fire = 80;

    for step in 0..360 {
        tick += 1;
        scene.simulation_mut().prepare_movement_frame(tick).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        let buttons = if (10..35).contains(&step) {
            FIRE
        } else if step == 45 {
            roll_button
        } else if (230..255).contains(&step) {
            FIRE
        } else {
            0
        };
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                yaw: 0x8000,
                pitch: 0,
                buttons,
                ..Default::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let shots = world.take_hitscan(scene.simulation_mut());
        {
            let (room, queries) = scene.bot_simulation();
            for (shot, policy) in shots {
                let context = crate::game_combat::ShotContext {
                    team: viewer.team,
                    tick,
                    quanta: RETAIL_QUANTA,
                    now: TICK_PERIOD * tick,
                };
                if let Some(hit) = room
                    .hitscan_with_policy(&queries, shot, policy, context, |_| true)
                    .unwrap()
                {
                    if hit.damage.target_facts.target == bot {
                        bot_hits += 1;
                    }
                }
            }
        }
        scene.step_bots(tick, &patrol).unwrap();
        let (room, queries) = scene.bot_simulation();
        room.finish_tick(&queries, tick, TICK_PERIOD * tick)
            .unwrap();
        let _ = room.take_player_cues();
        let body = scene.simulation().player_body(id).unwrap();
        let ammo = quad_cannons_ammo(body);
        let in_roll = body
            .runtime
            .as_ref()
            .and_then(|r| r.mover_override())
            .is_some();
        if step == 40 {
            ammo_after_fire = ammo;
        }
        if step == 80 {
            rolled_active = in_roll;
        }
        if step == 225 {
            ammo_after_roll_reload = ammo;
        }
        if step >= 235 && !in_roll {
            unrolled_on_fire = true;
        }
        if step == 260 {
            ammo_after_unroll_fire = ammo;
        }
    }
    eprintln!(
        "WB quad cannons & roll auto-reload: hits={bot_hits} after_fire={ammo_after_fire} rolled={rolled_active} after_roll_reload={ammo_after_roll_reload} unrolled_on_fire={unrolled_on_fire} after_unroll_fire={ammo_after_unroll_fire}"
    );
    assert!(bot_hits > 0, "Quad Cannons hitscan should hit enemy bot");
    assert!(
        ammo_after_fire < initial_ammo,
        "Quad Cannons should spend ammo when firing: {ammo_after_fire}"
    );
    assert!(rolled_active, "Roll should activate mover override");
    assert_eq!(
        ammo_after_roll_reload, 80,
        "staying in Roll form should auto-reload Quad Cannons back to 80"
    );
    assert!(
        unrolled_on_fire,
        "pressing primary fire in Roll form should auto-transform back to Crab form"
    );
    assert!(
        ammo_after_unroll_fire < 80,
        "Quad Cannons should fire after auto-unrolling: {ammo_after_unroll_fire}"
    );
}

#[test]
#[ignore = "requires stock 104319 Practice Range, Wrecking Ball and Doomfist graphs"]
fn prepared_wrecking_ball_adaptive_shield_blocks_ability_ult_charge() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let ball_hero = scene
        .heroes()
        .get(crate::game_heroes::WRECKING_BALL)
        .unwrap()
        .clone();
    let doom_hero = scene.heroes().get(0x02e0_0000_0000_012f).unwrap().clone();
    let wb = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let df = PlayerAppearance {
        entity: OWNER + 0x100,
        slot: 1,
        team: 1,
        card: &PlayerCard::default(),
    };
    let (mut wb_world, wb_id, wb_tick) =
        select_practice_hero(&mut scene, &wb, ball_hero.guid, &mut GameEgress::default());
    let (mut df_world, df_id, df_tick) =
        select_practice_hero(&mut scene, &df, doom_hero.guid, &mut GameEgress::default());
    let roster = crate::game_instance::TeamMemberCounts::from_teams(
        scene.map().free_for_all,
        [wb.team, df.team],
    );
    scene.simulation_mut().set_team_member_counts(roster);
    let mut tick = wb_tick.max(df_tick);
    let patrol = PatrolScript::load().unwrap();
    let bot = BOT_ENTITY + 7;
    let [bx, by, bz] = scene.simulation_mut().body_mut(bot).unwrap().position;
    let shield_button = crate::script_runtime::held_button_mask(36).unwrap();
    let uppercut_button = crate::script_runtime::held_button_mask(35).unwrap();

    let mut base_hp_before_shield = 0.0_f64;
    let mut total_peak = 0.0_f64;
    let mut total_before_hit = 0.0_f64;
    let mut total_after_hit = 0.0_f64;
    let mut df_ult_before_hit = 0.0_f64;
    let mut df_ult_after_hit = 0.0_f64;
    let mut uppercut_dealt = Vec::new();
    let mut wb_rise = 0.0_f64;
    let mut wb_y_at_hit = None;

    for step in 0..170 {
        if step == 40 {
            let wb_mover = scene
                .player_spawn_mover_at(ball_hero.body, wb.team, [bx, by, bz + 2.5], 0)
                .unwrap();
            let df_mover = scene
                .player_spawn_mover_at(doom_hero.body, df.team, [bx, by, bz + 4.5], 0x8000)
                .unwrap();
            scene.simulation_mut().player_body_mut(wb_id).unwrap().mover = wb_mover;
            scene.simulation_mut().player_body_mut(df_id).unwrap().mover = df_mover;
        }
        tick += 1;
        scene.simulation_mut().prepare_movement_frame(tick).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();

        wb_world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                yaw: 0,
                buttons: if (50..=53).contains(&step) {
                    shield_button
                } else {
                    0
                },
                ..Default::default()
            }],
        );
        wb_world
            .advance_input(&mut scene, &wb, RETAIL_QUANTA, 16)
            .unwrap();

        df_world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                yaw: 0x8000,
                buttons: if (70..=73).contains(&step) {
                    uppercut_button
                } else {
                    0
                },
                ..Default::default()
            }],
        );
        df_world
            .advance_input(&mut scene, &df, RETAIL_QUANTA, 16)
            .unwrap();

        scene.step_bots(tick, &patrol).unwrap();
        let (room, queries) = scene.bot_simulation();
        room.finish_tick(&queries, tick, TICK_PERIOD * tick)
            .unwrap();
        for cue in room.take_player_cues() {
            if cue.owner == df.entity {
                if let crate::game_combat_messages::CombatNotice::Hit { target, dealt, .. } =
                    cue.notice
                {
                    if target == wb_id.entity {
                        uppercut_dealt.push(dealt);
                        let y = scene
                            .simulation()
                            .player_body(wb_id)
                            .unwrap()
                            .mover
                            .pose()
                            .position[1];
                        wb_y_at_hit.get_or_insert(y);
                    }
                }
            }
        }
        let wb_body = scene.simulation().player_body(wb_id).unwrap();
        let wb_vitals = wb_body.vitals().unwrap();
        let df_ult = scene
            .simulation()
            .player_body(df_id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .ult_charge_percent();
        total_peak = total_peak.max(wb_vitals.total());
        if step == 45 {
            base_hp_before_shield = wb_vitals.total();
        }
        if step == 68 {
            total_before_hit = wb_vitals.total();
            df_ult_before_hit = df_ult;
        }
        if step == 90 {
            total_after_hit = wb_vitals.total();
            df_ult_after_hit = df_ult;
        }
        if let Some(y0) = wb_y_at_hit {
            wb_rise = wb_rise.max(wb_body.mover.pose().position[1] - y0);
        }
    }
    let shield_peak = total_peak - base_hp_before_shield;
    eprintln!(
        "WB Adaptive Shield vs Doomfist Uppercut: base={base_hp_before_shield} shield_peak={shield_peak} dealt={uppercut_dealt:?} total={total_before_hit}->{total_after_hit} df_ult={df_ult_before_hit:.4}%->{df_ult_after_hit:.4}% wb_rise={wb_rise:.3}m"
    );
    assert!(
        shield_peak >= 175.0,
        "Adaptive Shield near 1 enemy must grant at least 175 shield, got {shield_peak}"
    );
    assert!(
        matches!(uppercut_dealt.as_slice(), [dealt] if (*dealt - 50.0).abs() < 0.01),
        "expected one 50-damage Uppercut hit, got {uppercut_dealt:?}"
    );
    assert!(
        total_after_hit > base_hp_before_shield
            && (total_before_hit - total_after_hit - 50.0).abs() < 1.0,
        "Adaptive Shield should absorb all 50 damage: base={base_hp_before_shield}, before={total_before_hit}, after={total_after_hit}"
    );
    // Passive ult trickle over 22 ticks is ~0.18%; 50 raw ult damage would add ~3.0%+.
    let ult_gain = df_ult_after_hit - df_ult_before_hit;
    assert!(
        ult_gain < 0.5,
        "ability damage absorbed by Adaptive Shield must not grant ult charge (delta={ult_gain:.4}%)"
    );
    assert!(
        wb_rise > 0.5,
        "Doomfist Uppercut should launch player victim cleanly even with 0x0E7D reset (rise={wb_rise:.3}m)"
    );
}

#[test]
#[ignore = "requires stock 104319 Practice Range, Wrecking Ball graphs and collision"]
fn prepared_wrecking_ball_fireball_swings_into_enemy_for_damage_and_knockback() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene
        .heroes()
        .get(crate::game_heroes::WRECKING_BALL)
        .unwrap()
        .clone();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut world, id, mut tick) =
        select_practice_hero(&mut scene, &viewer, hero.guid, &mut GameEgress::default());
    let patrol = PatrolScript::load().unwrap();
    let roll_button = crate::script_runtime::held_button_mask(35).unwrap();
    let bot = BOT_ENTITY + 7;

    let mut max_speed = 0.0_f64;
    let mut max_tier = 0i64;
    let mut saw_boost = false;
    let mut entered_fireball = false;
    let mut fireball_hits = Vec::new();
    let mut bot_moved = 0.0_f64;
    let mut placed_bot_at = None;
    let mut bot_origin = None;

    const PRESS: u32 = 490;
    const MOVE: u32 = PRESS + 30;
    const RELEASE: u32 = MOVE + 240;

    // Move Wrecking Ball to the open training plaza around BOT_ENTITY + 7 and aim
    // slightly upward (`pitch < 0`) at a nearby wall/pillar so the grapple pendulum
    // has a wide, unobstructed swing arc.
    let [bx, by, bz] = scene.simulation_mut().body_mut(bot).unwrap().position;
    let start_pos = [bx, by, bz + 3.0];
    let eye = [start_pos[0], start_pos[1] + 1.4, start_pos[2]];
    let grapple_pitch = -0x0400_i32;
    let mut grapple_yaw = 0i32;
    let mut best_dist = 0.0_f64;
    for k in 0..32 {
        let yaw = k * 0x0800;
        let dir = crate::game_hitscan::direction_for_angles(yaw, grapple_pitch);
        if let Ok(Some(hit)) =
            scene
                .test_combat_rays()
                .cast_ray(eye, dir, 18.0, crate::scene_assets::MOVER_CATEGORY)
        {
            if (8.0..=17.0).contains(&hit.distance) && hit.distance > best_dist {
                best_dist = hit.distance;
                grapple_yaw = yaw;
            }
        }
    }
    assert!(best_dist >= 8.0, "expected a grappleable wall in the plaza");
    let plaza_mover = scene
        .player_spawn_mover_at(hero.body, viewer.team, start_pos, grapple_yaw)
        .unwrap();
    scene.simulation_mut().player_body_mut(id).unwrap().mover = plaza_mover;
    // Move the training bot out of the way until Fireball activates.
    scene.simulation_mut().body_mut(bot).unwrap().position = [bx, by, bz - 10.0];

    for step in 0..RELEASE + 40 {
        tick += 1;
        scene.simulation_mut().prepare_movement_frame(tick).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                yaw: grapple_yaw,
                pitch: grapple_pitch,
                right: if step >= MOVE { 127 } else { 0 },
                forward: if step >= MOVE { 127 } else { 0 },
                buttons: if step == 70 {
                    roll_button
                } else if (PRESS..RELEASE).contains(&step) {
                    SECONDARY
                } else {
                    0
                },
                ..Default::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let body = scene.simulation().player_body(id).unwrap();
        let pose = body.mover.pose();
        let speed = pose.velocity[0].hypot(pose.velocity[2]);
        max_speed = max_speed.max(speed);
        let (tier, fireball, boosted) = roll_fireball_tier(body);
        max_tier = max_tier.max(tier);
        saw_boost |= boosted;
        if fireball {
            entered_fireball = true;
            if placed_bot_at.is_none() && speed > 10.0 {
                // Place the enemy training bot directly ahead of the rolling fireball.
                let dir_x = pose.velocity[0] / speed;
                let dir_z = pose.velocity[2] / speed;
                let target_pos = [
                    pose.position[0] + dir_x * 2.2,
                    pose.position[1],
                    pose.position[2] + dir_z * 2.2,
                ];
                let bot_body = scene.simulation_mut().body_mut(bot).unwrap();
                bot_body.position = target_pos;
                placed_bot_at = Some(step);
                bot_origin = Some(target_pos);
            }
        }
        scene.step_bots(tick, &patrol).unwrap();
        let (room, queries) = scene.bot_simulation();
        room.finish_tick(&queries, tick, TICK_PERIOD * tick)
            .unwrap();
        for cue in room.take_player_cues() {
            if cue.owner == viewer.entity {
                if let crate::game_combat_messages::CombatNotice::Hit { target, dealt, .. } =
                    cue.notice
                {
                    if target == bot {
                        fireball_hits.push((step, dealt));
                    }
                }
            }
        }
        if let Some(origin) = bot_origin {
            let pos = scene.simulation_mut().body_mut(bot).unwrap().position;
            let disp = (pos[0] - origin[0]).hypot(pos[2] - origin[2]);
            bot_moved = bot_moved.max(disp);
        }
    }
    eprintln!(
        "WB Fireball probe: max_speed={max_speed:.2} max_tier={max_tier} saw_boost={saw_boost} entered_fireball={entered_fireball} placed_bot_at={placed_bot_at:?} hits={fireball_hits:?} bot_moved={bot_moved:.3}m"
    );
    assert!(
        entered_fireball,
        "Grapple swing must reach Fireball state (max_speed={max_speed:.2}, max_tier={max_tier})"
    );
    assert!(
        !fireball_hits.is_empty(),
        "Fireball must hit enemy bot in its path (placed_bot_at={placed_bot_at:?})"
    );
    assert!(
        fireball_hits
            .iter()
            .all(|(_, dealt)| (*dealt - 50.0).abs() < 0.01),
        "Fireball hits must deal 50 damage: {fireball_hits:?}"
    );
    assert!(
        bot_moved > 0.5,
        "Fireball impact must knock the enemy bot back (moved={bot_moved:.3}m)"
    );
}
