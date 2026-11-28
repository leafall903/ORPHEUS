use super::*;

#[derive(Clone, Copy)]
pub(super) enum NormalEnding {
    EarlyDefeat,
    LateDefeat,
    DeadeyeVictory,
}

pub(super) fn verify_natural_ending(ending: NormalEnding) {
    use crate::{
        game_clock::FIRST_TICK,
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        game_heroes::CASSIDY,
        game_instance::RETAIL_QUANTA,
        game_replication::BodyStep,
        script_enemy::EnemyTarget,
        script_player::OwnerBodySpawn,
        world::ComponentValue,
    };
    let late = !matches!(ending, NormalEnding::EarlyDefeat);
    let victory = matches!(ending, NormalEnding::DeadeyeVictory);
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let mut assets = SceneAssets::load(&runtime).unwrap();
    assets.set_junkenstein_validation(crate::pve_encounter::validation::JunkensteinValidation {
        invulnerable_cassidy: true,
        gate_capacity: late.then_some(1_000_000_000.0),
    });
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
    let hero = scene.heroes().find_hero("Cassidy").unwrap();
    assert!(scene.protects_cassidy(Some(hero.guid)));
    let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
    let body = 0xa000_0101;
    let flags = crate::match_scene::team_bit(0).unwrap() | 1;
    let mut owner = scene
        .owner_body_runtime(
            hero,
            OwnerBodySpawn {
                slot: 0,
                controller: body - 1,
                entity: body,
                body_filter: flags,
                frame: FIRST_TICK,
                pose: mover.pose(),
                eye_position: crate::game_combat::script_eye_position(hero.guid, &mover),
                skin: 0,
                quanta: RETAIL_QUANTA,
            },
        )
        .unwrap()
        .unwrap();
    let health = owner.vitals().unwrap().total();
    let mut absorbed = 0.0;
    let mut winner_at = None;
    let mut spawned_heroes = std::collections::BTreeSet::new();
    let mut earned_points = 0.0_f32;
    let mut point_events = 0_usize;
    let mut gate_released = false;
    let mut intro_cameras: std::collections::BTreeMap<u64, std::collections::BTreeSet<(u32, u16)>> =
        Default::default();
    let mut intro_finished = std::collections::BTreeSet::new();
    let mut combat_positions = HashMap::new();
    let mut pursued = std::collections::BTreeSet::new();
    let mut gate_release_damage = None;
    let mut gate_zero_at = None;
    let mut deadeye_started = None;
    let mut victory_approach = None;
    let mut deadeye_casts = 0;
    let mut outcome_at = None;
    let mut deadeye_kills = std::collections::BTreeSet::new();
    let mut deadeye_hits = Vec::new();
    let mut boss_bodies = std::collections::BTreeSet::new();
    let mut deadeye_bodies = std::collections::BTreeSet::new();
    scene.step_encounter(FIRST_TICK, Some(CASSIDY));
    for tick in FIRST_TICK + 1..=FIRST_TICK + 110_000 {
        if late
            && !victory
            && !gate_released
            && [REAPER, ROADHOG, JUNKRAT, SUMMONER, WITCH]
                .iter()
                .all(|hero| intro_finished.contains(hero))
        {
            // Preserve the full natural introduction sequence, then allow
            // one further point of actual attack damage to end the match.
            let damage =
                crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8569]);
            scene.set_test_gate_capacity(damage + 1.0);
            gate_release_damage = Some(damage);
            assert!(
                crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8571])
                    > 0.0
            );
            gate_released = true;
            eprintln!(
                "late gate released at {tick}: heroes={spawned_heroes:X?}, cameras={intro_cameras:X?}, pursued={pursued:X?}"
            );
        }
        scene.step_encounter(tick, Some(CASSIDY));
        assert!(scene.encounter_snapshot().is_some(), "mode fault at {tick}");
        if !scene.encounter_active() && outcome_at.is_none() {
            outcome_at = Some(tick);
            let snapshot = scene.encounter_snapshot().unwrap();
            let mode = &snapshot.instances[&2];
            eprintln!(
                "outcome at {tick}: winner={:?}, success={:?}, challenge={:?}, active={:?}",
                mode.variables.get(&7525),
                mode.variables.get(&8617),
                mode.variables.get(&11776),
                mode.states
                    .iter()
                    .filter(|(_, state)| state.active)
                    .map(|(&raw, state)| (raw, &state.kind))
                    .collect::<Vec<_>>()
            );
            eprintln!("final hits: {deadeye_hits:X?}");
        }
        if let Some(outcome) = outcome_at {
            if tick == outcome + 4000 {
                let snapshot = scene.encounter_snapshot().unwrap();
                let mode = &snapshot.instances[&2];
                assert!(
                    scene.encounter_match_result().is_some()
                        && scene.encounter_post_match_wire().unwrap().is_some(),
                    "outcome did not settle: winner={:?}, success={:?}, challenge={:?}, active={:?}",
                    mode.variables.get(&7525),
                    mode.variables.get(&8617),
                    mode.variables.get(&11776),
                    mode.states
                        .iter()
                        .filter(|(_, state)| state.active)
                        .map(|(&raw, state)| (raw, &state.kind))
                        .collect::<Vec<_>>()
                );
            }
        }
        owner
            .set_encounter_active(scene.encounter_active())
            .unwrap();
        if victory
            && victory_approach.is_none()
            && [REAPER, ROADHOG, JUNKRAT, SUMMONER, WITCH]
                .iter()
                .all(|hero| intro_finished.contains(hero))
        {
            victory_approach = Some(tick);
        }
        if victory_approach.is_some()
            && mover.pose().position[2] <= -77.0
            && (deadeye_casts == 0 || mover.pose().position[0] <= 80.0)
            && deadeye_started.is_none_or(|start| tick > start + 600)
            && scene
                .bots()
                .unwrap()
                .any(|bot| !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero))
            && owner.snapshot().unwrap().variables[&342]
                == owner.snapshot().unwrap().variables[&338]
        {
            let snapshot = owner.snapshot().unwrap();
            assert_eq!(
                snapshot.variables[&342], snapshot.variables[&338],
                "real passive charge must be full before the final cast"
            );
            deadeye_started = Some(tick);
            deadeye_casts += 1;
            assert!(
                deadeye_casts <= 4,
                "normal charge/casts did not clear the Bosses"
            );
            let roster = scene
                .bots()
                .unwrap()
                .filter(|bot| !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero))
                .map(|bot| (bot.entity, bot.spawn.hero, bot.position))
                .collect::<Vec<_>>();
            eprintln!(
                "final-phase Deadeye {deadeye_casts} begins at {tick}: position={:?}, bosses={roster:X?}",
                mover.pose().position
            );
        }
        let mut command = Command {
            frame: tick,
            yaw: mover.pose().yaw,
            pitch: mover.pose().pitch,
            ..Default::default()
        };
        if victory_approach.is_some() {
            let position = mover.pose().position;
            let mut yaw_range = [f64::INFINITY, f64::NEG_INFINITY];
            let mut pitch_range = yaw_range;
            for bot in scene
                .bots()
                .unwrap()
                .filter(|bot| !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero))
            {
                let delta = [bot.position[0] - position[0], bot.position[2] - position[2]];
                let distance = delta[0].hypot(delta[1]);
                if distance > 0.001 {
                    // Every Boss is ahead in this courtyard segment. Aim
                    // between the angular extremes so a cluster of nearer
                    // bodies does not turn the view away from the balcony.
                    let yaw = delta[0].atan2(delta[1]).rem_euclid(std::f64::consts::TAU);
                    let pitch = (bot.position[1] + eye_height(bot.spawn.hero, false)
                        - position[1]
                        - eye_height(CASSIDY, false))
                    .atan2(distance);
                    yaw_range = [yaw_range[0].min(yaw), yaw_range[1].max(yaw)];
                    pitch_range = [pitch_range[0].min(pitch), pitch_range[1].max(pitch)];
                }
            }
            if yaw_range[0].is_finite() {
                command.yaw = ((yaw_range[0] + yaw_range[1]) * 0.5 * 65536.0
                    / std::f64::consts::TAU)
                    .round_ties_even() as i32;
                command.pitch = (-(pitch_range[0] + pitch_range[1]) * 0.5 * 65536.0
                    / std::f64::consts::TAU)
                    .round_ties_even() as i32;
            }
            if mover.pose().position[2] > -77.0 {
                // Walk from the actual spawn into the courtyard. No pose
                // teleport or visibility/collision override is used.
                command.yaw = 32768;
                command.pitch = 0;
                command.forward = i8::MAX;
            } else if deadeye_casts > 0
                && position[0] > 80.0
                && deadeye_started.is_some_and(|start| tick > start + 600)
            {
                command.yaw = -16384;
                command.pitch = 0;
                command.forward = i8::MAX;
            }
            if let Some(start) = deadeye_started {
                if tick == start || tick == start + 350 {
                    command.buttons = 0x20;
                }
            }
            mover
                .step(scene.movement_index(), command, 0.016, |_, _| {})
                .unwrap();
            owner.set_deadeye_scene(&scene, 0).unwrap();
        }
        owner
            .advance(
                command,
                &mover,
                if victory_approach.is_some() {
                    BodyStep::Posed
                } else {
                    BodyStep::UnposedTimer
                },
            )
            .unwrap();
        for shot in owner.take_shots(Some(command), &mover) {
            let PosedShot::Hitscan(shot) = shot else {
                panic!("final Deadeye must use authored hitscan");
            };
            let (room, queries) = scene.bot_simulation();
            if let Some(hit) = room
                .hitscan(
                    &queries,
                    *shot,
                    ShotContext {
                        team: 0,
                        quanta: RETAIL_QUANTA,
                        tick,
                        now: Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
                    },
                    |_| true,
                )
                .unwrap()
            {
                let hero = state(room, hit.damage.target_facts.target)
                    .unwrap()
                    .bot
                    .spawn
                    .hero;
                let killed = matches!(hit.notices[0], Some(CombatNotice::Hit { killed: true, .. }));
                deadeye_hits.push((
                    hit.damage.target_facts.target,
                    hero,
                    hit.damage.amount,
                    killed,
                ));
                if killed && crate::npc_catalog::is_junkenstein_boss(hero) {
                    deadeye_kills.insert(hero);
                    deadeye_bodies.insert(hit.damage.target_facts.target);
                }
                owner.receive_applied_damage(hit.damage).unwrap();
            }
        }
        let defender = EnemyTarget {
            hero: Some(CASSIDY),
            filter_flags: Some(flags),
            tags: owner.target_tags(),
            health: Some(health),
            velocity: Some([0.0; 3]),
            script_state: crate::script_entity_state::EntityScriptState::Running(Arc::new(
                owner.snapshot().unwrap().variables.clone(),
            )),
            entity: body,
            team: 0,
            alive: true,
            pose: TargetPose {
                feet: mover.pose().position,
                height: 1.8,
                radius: 0.35,
                eye: 1.55,
            },
        };
        let (room, queries) = scene.bot_simulation();
        room.set_external_targets(vec![defender]);
        room.finish_tick(
            &queries,
            tick,
            Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
        )
        .unwrap();
        for message in &room.scene_messages {
            let parameter = match message.message {
                0x0240_0000_0000_06a4 => Some(0x39e),
                0x0240_0000_0000_06c8 => Some(0x3af),
                _ => None,
            };
            if let Some(parameter) = parameter {
                earned_points +=
                    crate::script_expr::to_float(&message.parameters[&parameter]) as f32;
                point_events += 1;
            }
        }
        for actor in room.states().map(Result::unwrap) {
            if let Some(enemy) = &actor.enemy {
                if crate::npc_catalog::is_junkenstein_boss(enemy.binding.hero) {
                    boss_bodies.insert(actor.bot.entity);
                }
                if spawned_heroes.insert(enemy.binding.hero) {
                    eprintln!(
                        "natural wave hero {:X} first seen at {tick}",
                        enemy.binding.hero
                    );
                }
                assert!(
                    enemy.fault.is_none(),
                    "enemy {:x} fault at {tick}: {:?}",
                    enemy.binding.hero,
                    enemy.fault
                );
                if late && crate::npc_catalog::is_junkenstein_boss(enemy.binding.hero) {
                    for (&instance_id, intro) in &enemy.scripts.body.snapshot().unwrap().instances {
                        if intro.graph != 0x0ce0 {
                            continue;
                        }
                        if let Some(camera) = intro.states.get(&14).filter(|camera| camera.active) {
                            assert_eq!(camera.activation, 1, "Boss camera restarted");
                            if intro_cameras
                                .entry(enemy.binding.hero)
                                .or_default()
                                .insert((actor.bot.entity, instance_id))
                            {
                                let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(
                                    &enemy.graphs,
                                    &enemy.codecs,
                                );
                                encoder
                                    .full(
                                        &encoder
                                            .project(
                                                enemy.scripts.body.snapshot().unwrap(),
                                                crate::script_snapshot_wire::View::Observer,
                                            )
                                            .unwrap(),
                                    )
                                    .unwrap();
                            }
                        }
                        if intro
                            .states
                            .get(&3)
                            .is_some_and(|wait| wait.activation != 0 && !wait.active)
                        {
                            intro_finished.insert(enemy.binding.hero);
                        }
                    }
                    if intro_finished.contains(&enemy.binding.hero) {
                        let start = combat_positions
                            .entry(actor.bot.entity)
                            .or_insert(actor.bot.position);
                        if norm(std::array::from_fn(|axis| {
                            actor.bot.position[axis] - start[axis]
                        })) > 0.5
                        {
                            pursued.insert(enemy.binding.hero);
                        }
                    }
                }
            }
        }
        // Explicit test-only invulnerability: count real enemy impacts while
        // the defender's pools stay intact. Gate damage is never intercepted.
        absorbed += room
            .take_external_enemy_damage()
            .into_iter()
            .filter(|hit| hit.target == body)
            .map(|hit| hit.amount)
            .sum::<f64>();
        let messages = room.take_defender_messages();
        let sightings = room.take_defender_sightings();
        if !messages.is_empty() {
            owner.set_deadeye_scene(&scene, 0).unwrap();
        }
        for message in messages {
            owner.receive_defender_message(message).unwrap();
        }
        for sighting in sightings {
            owner.receive_sighting(sighting).unwrap();
        }
        if late && (tick - FIRST_TICK) % 5000 == 0 {
            eprintln!(
                "late replay tick={tick}, gate={}, finished={intro_finished:X?}, casts={deadeye_casts}, charge={:?}",
                crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8571]),
                owner.snapshot().unwrap().variables.get(&342),
            );
        }
        assert_eq!(owner.vitals().unwrap().total(), health);
        assert!(
            owner.snapshot().unwrap().initialization_complete(),
            "owner at {tick}: {:?}",
            owner.snapshot().unwrap().diagnostics
        );
        if let Some(start) = deadeye_started {
            if deadeye_casts == 4 && tick == start + 1500 {
                let remaining = scene
                    .bots()
                    .unwrap()
                    .filter(|bot| {
                        !bot.dead && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero)
                    })
                    .map(|bot| (bot.entity, bot.spawn.hero, bot.position))
                    .collect::<Vec<_>>();
                assert!(
                    remaining.is_empty(),
                    "Deadeye did not clear all Bosses: remaining={remaining:X?}, hits={deadeye_hits:X?}, targets={:?}, visible={:?}",
                    owner.snapshot().unwrap().variables.get(&2551),
                    owner.snapshot().unwrap().variables.get(&5874)
                );
            }
        }
        if late
            && crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8571]) == 0.0
        {
            let zero = *gate_zero_at.get_or_insert(tick);
            assert!(
                tick - zero < 4000 || scene.encounter_match_result().is_some(),
                "late gate zero did not settle within 64 seconds"
            );
        }
        if let Some(result) = scene.encounter_match_result() {
            assert!(
                !late || victory || gate_released,
                "high-capacity gate ended the encounter early"
            );
            assert_eq!(
                result.fields[7],
                Some(ComponentValue::I32(if victory { 0 } else { 1 }))
            );
            winner_at.get_or_insert(tick);
            if scene.encounter_post_match_wire().unwrap().is_some() {
                if victory {
                    assert_eq!(
                        boss_bodies, deadeye_bodies,
                        "every spawned Boss body must die to actual Deadeye damage: {deadeye_hits:X?}"
                    );
                    assert!(
                        crate::script_expr::to_float(
                            &scene.gate_snapshot().unwrap().variables[&8571]
                        ) > 0.0
                    );
                    assert!(
                        !scene.bots().unwrap().any(|bot| !bot.dead
                            && crate::npc_catalog::is_junkenstein_boss(bot.spawn.hero))
                    );
                }
                if late {
                    assert!(
                        victory
                            || crate::script_expr::to_float(
                                &scene.gate_snapshot().unwrap().variables[&8569]
                            ) > gate_release_damage.unwrap(),
                        "late loss requires a new actual gate hit"
                    );
                    for hero in [REAPER, ROADHOG, JUNKRAT, SUMMONER, WITCH] {
                        if victory {
                            assert!(
                                deadeye_kills.contains(&hero),
                                "Boss {hero:X} must die to actual Deadeye damage: {deadeye_hits:X?}"
                            );
                        }
                        assert!(
                            intro_finished.contains(&hero),
                            "Boss {hero:X} never completed introduction"
                        );
                        assert_eq!(
                            intro_cameras.get(&hero).map(|roots| roots.len()),
                            Some(1),
                            "Boss {hero:X} camera must play once"
                        );
                    }
                    // These two must close their spawn distance. The casters
                    // may already have a valid ranged solution from spawn.
                    for hero in [REAPER, ROADHOG] {
                        assert!(
                            pursued.contains(&hero),
                            "Boss {hero:X} did not move after introduction"
                        );
                    }
                }
                assert!(
                    absorbed > 0.0,
                    "no actual enemy hit exercised invulnerability"
                );
                assert!(
                    crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8569])
                        > 0.0
                );
                let gate_health =
                    crate::script_expr::to_float(&scene.gate_snapshot().unwrap().variables[&8571]);
                assert!(
                    if victory {
                        gate_health > 0.0
                    } else {
                        gate_health == 0.0
                    },
                    "gate state disagrees with outcome"
                );
                assert!(scene.encounter_result_wire().unwrap().is_some());
                let snapshot = scene.encounter_snapshot().unwrap();
                let gate_bonus = snapshot
                    .variables
                    .get(&8525)
                    .map(crate::script_expr::to_float)
                    .unwrap_or(0.0);
                assert_eq!(
                    gate_bonus > 0.0,
                    victory,
                    "gate bonus must follow the actual result"
                );
                let expected_score = f64::from((earned_points + gate_bonus as f32) * 1.2_f32);
                assert_eq!(
                    snapshot.resolved_bindings.get(&(0, 2499)),
                    Some(&crate::script_wire::Value::Float(expected_score)),
                    "natural result score differs from actual point carriers and gate bonus"
                );
                if victory {
                    assert!(point_events >= deadeye_bodies.len());
                }
                let (projected, _) = scene.encounter_notification_delta(None).unwrap().unwrap();
                assert_eq!(
                    projected.entity_variables().get(&2499),
                    Some(&crate::script_wire::Value::Float(expected_score))
                );
                eprintln!(
                    "natural score: events={point_events}, earned={earned_points}, gate_bonus={gate_bonus}, final={expected_score}"
                );

                eprintln!(
                    "natural ending: tick={tick}, health={health}, absorbed={absorbed}, casts={deadeye_casts}, heroes={spawned_heroes:X?}"
                );
                return;
            }
        }
    }
    panic!("natural waves did not reach recap: winner_at={winner_at:?}, absorbed={absorbed}");
}

pub(super) fn normal_common_enemy_death_updates_authored_team_score() {
    use crate::{
        game_clock::FIRST_TICK,
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        game_heroes::CASSIDY,
        script_enemy::EnemyTarget,
        world::GAME_MODE_ENTITY,
    };
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&runtime).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
    let defender = EnemyTarget {
        hero: Some(CASSIDY),
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: 0xa000_0101,
        team: 0,
        pose: TargetPose {
            feet: [87.013671875, 12.65625, -64.34765625],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    };
    scene.set_junkenstein_defenders(Arc::from([defender.entity]));
    let (initial_projection, _) = scene.encounter_notification_delta(None).unwrap().unwrap();
    let mut score_messages = Vec::new();
    let mut live_score_verified = false;
    let mut killed_at = None;
    let mut first_seen = None;
    let mut before = 0.0;
    for tick in FIRST_TICK..FIRST_TICK + 16000 {
        scene.step_encounter(tick, Some(CASSIDY));
        let snapshot = scene.encounter_snapshot().unwrap();
        let score = snapshot
            .resolved_bindings
            .get(&(0, 2499))
            .or_else(|| snapshot.variables.get(&2499))
            .map(crate::script_expr::to_float)
            .unwrap_or(0.0);
        if let Some(killed_at) = killed_at {
            if tick > killed_at + 200 {
                assert_eq!(
                    score_messages,
                    [crate::script_wire::Variables::from([(
                        0x39e,
                        crate::script_wire::Value::Float(10.0)
                    )])]
                );
                assert_eq!(before, 0.0);
                assert_eq!(
                    score, 12.0,
                    "authored 10-point Zomnic and Normal 1.2 multiplier"
                );
                if !live_score_verified {
                    let (projected, payload) = scene
                        .encounter_notification_delta(Some(&initial_projection))
                        .unwrap()
                        .unwrap();
                    assert_eq!(
                        projected.entity_variables().get(&2499),
                        Some(&crate::script_wire::Value::Float(12.0))
                    );
                    assert_eq!(projected.entity(), GAME_MODE_ENTITY);
                    assert!(payload.bit_len() > 0);
                    live_score_verified = true;
                }
                if let Some(result) = scene.encounter_match_result() {
                    if scene.encounter_post_match_wire().unwrap().is_some() {
                        assert_eq!(result.fields[7], Some(crate::world::ComponentValue::I32(1)));
                        assert_eq!(
                            snapshot
                                .variables
                                .get(&8525)
                                .map(crate::script_expr::to_float),
                            Some(0.0)
                        );
                        assert!(scene.encounter_result_wire().unwrap().is_some());
                        let (projected, _) =
                            scene.encounter_notification_delta(None).unwrap().unwrap();
                        assert_eq!(
                            projected.entity_variables().get(&2499),
                            Some(&crate::script_wire::Value::Float(12.0))
                        );
                        return;
                    }
                }
            }
        } else {
            before = score;
        }
        let (room, queries) = scene.bot_simulation();
        room.activate_combat();
        room.set_external_targets(vec![defender.clone()]);
        if killed_at.is_none() {
            let common = room
                .states()
                .map(Result::unwrap)
                .find(|state| !state.bot.dead && state.bot.spawn.hero == 0x02e0_0000_0000_0175)
                .map(|state| state.bot.entity);
            if let Some(entity) = common {
                if tick > *first_seen.get_or_insert(tick) + 20 {
                    let now = Duration::from_millis(u64::from(tick - FIRST_TICK) * 16);
                    RoomSimulation::damage_bot(
                        room.state_mut(entity).unwrap().unwrap(),
                        10000.0,
                        Death {
                            time: now.as_secs_f64(),
                            frame: tick,
                            killer: defender.entity,
                        },
                        now,
                    );
                    killed_at = Some(tick);
                }
            }
        }
        room.finish_tick(
            &queries,
            tick,
            Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
        )
        .unwrap();
        // Keep the declared defender invulnerable while real enemy attacks destroy the gate.
        room.take_external_enemy_damage();
        for message in &room.scene_messages {
            if message.message == 0x0240_0000_0000_06a4 {
                assert_eq!(message.target, GAME_MODE_ENTITY);
                score_messages.push(message.parameters.clone());
            }
        }
        for state in room.states().map(Result::unwrap) {
            if let Some(enemy) = &state.enemy {
                assert!(
                    enemy.fault.is_none(),
                    "enemy score input fault: {:?}",
                    enemy.fault
                );
            }
        }
    }
    panic!("score fixture did not retain its earned score through enemy victory");
}

pub(super) fn normal_encounter_first_reaper_finishes_intro_and_pursues_live_defender() {
    use crate::{
        game_clock::FIRST_TICK,
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        game_heroes::CASSIDY,
        game_projectiles::{OwnerProjectiles, ProjectileLeases},
        game_replication::BodyStep,
        script_enemy::EnemyTarget,
        script_player::OwnerBodySpawn,
    };
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&runtime).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = assets.prepare_junkenstein(map, card, 0).unwrap();
    let cassidy = scene.heroes().find_hero("Cassidy").unwrap().clone();
    let mut mover = scene.player_spawn_mover(cassidy.body, 0).unwrap();
    let mut owner = scene
        .owner_body_runtime(
            &cassidy,
            OwnerBodySpawn {
                slot: 0,
                controller: 0xa000_0100,
                entity: 0xa000_0101,
                body_filter: crate::match_scene::team_bit(0).unwrap(),
                frame: FIRST_TICK,
                pose: mover.pose(),
                eye_position: crate::game_combat::script_eye_position(cassidy.guid, &mover),
                skin: 0,
                quanta: RETAIL_QUANTA,
            },
        )
        .unwrap()
        .unwrap();
    let mut projectile_owner = OwnerProjectiles::new(
        0xa000_0101,
        ProjectileLeases::new(0, FIRST_TICK, [0x0400_0000_0000_037d]).unwrap(),
    );
    let owner_id = BodyId {
        entity: 0xa000_0101,
        generation: 1,
    };
    let mut defender = EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: 0xa000_0101,
        team: 0,
        pose: TargetPose {
            feet: mover.pose().position,
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    };
    scene.set_junkenstein_defenders(Arc::from([defender.entity]));
    let mut reaper = None;
    let mut first_pose = None;
    let mut moved = false;
    let mut inflicted = 0.0;
    let mut controlled: Option<(u32, [f64; 3])> = None;
    let mut press_at = None;
    let mut last_reaper_pose = None;
    let mut impacts = 0;
    let mut launches = 0;
    let mut e_target_health = None;

    for tick in FIRST_TICK..FIRST_TICK + 12500 {
        let delta = last_reaper_pose
            .map(|position: [f64; 3]| {
                [
                    position[0] - defender.pose.feet[0],
                    position[1] + 1.0 - defender.pose.feet[1] - 1.55,
                    position[2] - defender.pose.feet[2],
                ]
            })
            .unwrap_or([0.0, 0.0, 1.0]);
        let command = crate::commands::Command {
            frame: tick,
            yaw: (delta[0].atan2(delta[2]) / std::f64::consts::TAU * 65536.0).round() as i32,
            pitch: ((-delta[1]).atan2(delta[0].hypot(delta[2])) / std::f64::consts::TAU * 65536.0)
                .round() as i32,
            buttons: if press_at == Some(tick) { 16 } else { 0 },
            forward: if inflicted > 0.0 && press_at.is_none() {
                127
            } else {
                0
            },
            ..Default::default()
        };
        if tick > FIRST_TICK {
            mover
                .step(scene.movement_index(), command, 0.016, |mover, command| {
                    owner.advance(command, mover, BodyStep::Posed).unwrap();
                    for (frame, change) in owner.take_velocity_changes() {
                        mover.queue_velocity_change(frame, change);
                    }
                    mover.track_modifiers(command.frame, owner.movement_modifiers().unwrap());
                })
                .unwrap();
            defender.pose.feet = mover.pose().position;
        }
        // Use actual owner lease admission and the authored flight fields.
        // Transport/create acknowledgments are outside this restricted replay.
        let mut admitted = Vec::new();
        for shot in owner.take_shots(Some(command), &mover) {
            let PosedShot::Projectile(projectile) = shot else {
                panic!("E must launch a projectile")
            };
            assert_eq!(projectile.shot.graph, 0x01e9);
            assert_eq!(projectile.stun, Stun::from_seconds(0.8));
            let bound = (projectile.ballistics.lifetime
                / crate::game_clock::TICK_PERIOD.as_secs_f64())
            .ceil() as u32;
            let token = projectile_owner
                .admit(
                    projectile.shot,
                    projectile.ballistics.definition.unwrap(),
                    bound,
                )
                .unwrap();
            let flight = ProjectileFlight::new(
                projectile.ballistics,
                projectile.origin,
                projectile.direction,
                None,
            )
            .unwrap();
            let flight = if let Some(gravity) = projectile.gravity {
                flight.with_gravity(gravity)
            } else {
                flight
            };
            admitted.push(AdmittedFlight {
                token,
                launch_frame: projectile.shot.frame,
                flight,
                stun: projectile.stun,
                hit_effect: Some(crate::script_init::Site {
                    entity: projectile.shot.entity,
                    instance: projectile.shot.instance,
                    graph: projectile.shot.graph,
                    node: Some(7),
                }),
                impact_spawn: None,
                presentation: None,
            });
            launches += 1;
        }
        scene.step_encounter(tick, Some(CASSIDY));
        let (room, queries) = scene.bot_simulation();
        room.activate_combat();
        // Isolate the boss handoff: retire common attackers through normal
        // damage/death, so the unprotected gate cannot end this fixture early.
        let common: Vec<_> = room
            .states()
            .map(Result::unwrap)
            .filter(|s| !s.bot.dead && !crate::npc_catalog::is_junkenstein_boss(s.bot.spawn.hero))
            .map(|s| s.bot.entity)
            .collect();
        for entity in common {
            let now = Duration::from_millis(u64::from(tick - FIRST_TICK) * 16);
            RoomSimulation::damage_bot(
                room.state_mut(entity).unwrap().unwrap(),
                10000.0,
                Death {
                    time: now.as_secs_f64(),
                    frame: tick,
                    killer: defender.entity,
                },
                now,
            );
        }
        room.set_external_targets(vec![defender.clone()]);
        let (_, hits) = room
            .advance_projectiles(
                &queries,
                7,
                owner_id,
                admitted,
                ShotContext {
                    team: 0,
                    quanta: RETAIL_QUANTA,
                    tick,
                    now: Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
                },
                |_| true,
            )
            .unwrap();
        for hit in hits {
            assert_eq!(
                Some(hit.damage.target_facts.target),
                reaper,
                "E hit another enemy"
            );
            assert_eq!(hit.damage.amount, 25.0);
            assert_eq!(
                state(room, reaper.unwrap()).unwrap().vitals.total(),
                e_target_health.unwrap() - 25.0,
                "actual NPC health after E"
            );
            owner.receive_applied_damage(hit.damage).unwrap();
            impacts += 1;
            assert!(controlled.is_none(), "one E must not stun repeatedly");
            let bot = &state(room, reaper.unwrap()).unwrap().bot;
            assert!(bot.is_stunned(tick));
            controlled = Some((tick, bot.position));
            eprintln!("actual E impact tick={tick}, position={:?}", bot.position);
        }
        room.finish_tick(
            &queries,
            tick,
            Duration::from_millis(u64::from(tick - FIRST_TICK) * 16),
        )
        .unwrap();
        for state in room.states().map(Result::unwrap) {
            let Some(enemy) = state.enemy.as_ref() else {
                continue;
            };
            assert!(
                enemy.fault.is_none(),
                "tick {tick} hero {:x}: {:?}",
                enemy.binding.hero,
                enemy.fault
            );
            if state.bot.spawn.hero == REAPER {
                last_reaper_pose = Some(state.bot.position);
                if reaper.is_none() {
                    reaper = Some(state.bot.entity);
                    first_pose = Some(state.bot.position);
                    eprintln!(
                        "first Reaper spawned tick {tick} at {:?}",
                        state.bot.position
                    );
                }
                if Some(state.bot.entity) == reaper {
                    moved |= norm(std::array::from_fn(|i| {
                        state.bot.position[i] - first_pose.unwrap()[i]
                    })) > 3.0;
                    if tick % 1000 == 0 {
                        eprintln!(
                            "Reaper tick {tick}: {:?}, handoff {}, exit {}, pause {}, aim {:?}, facing {:?}",
                            state.bot.position,
                            enemy.boss_handoff_complete,
                            enemy.reaper_exit.is_some(),
                            enemy.scripts.boss_pause_active(),
                            enemy.scripts.cinematic_aim(),
                            enemy.scripts.facing_direction()
                        );
                    }
                }
            }
        }
        let damage = room
            .take_external_enemy_damage()
            .into_iter()
            .filter(|hit| Some(hit.source) == reaper && hit.target == defender.entity)
            .map(|hit| hit.amount)
            .sum::<f64>();
        inflicted += damage;
        if let Some((start, position)) = controlled {
            let state = state(room, reaper.unwrap()).unwrap();
            let enemy = state.enemy.as_ref().unwrap();
            let snapshot = enemy.scripts.body.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            if tick == start {
                assert_eq!(
                    snapshot
                        .instances
                        .values()
                        .filter(|i| i.graph == 0x224e)
                        .count(),
                    1
                );
            }
            let encoder =
                crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
            encoder
                .full(
                    &encoder
                        .project(snapshot, crate::script_snapshot_wire::View::Observer)
                        .unwrap(),
                )
                .unwrap();
            if tick < start + 50 {
                assert_eq!(
                    state.bot.position, position,
                    "controlled Reaper moved at {tick}"
                );
                assert_eq!(damage, 0.0, "controlled Reaper fired at {tick}");
            } else if damage > 0.0 {
                assert_eq!((launches, impacts), (1, 1));
                assert!(!state.bot.is_stunned(tick));
                let snapshot = owner.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                let encoder = owner.encoder();
                encoder
                    .full(
                        &encoder
                            .project(snapshot, crate::script_snapshot_wire::View::Owner)
                            .unwrap(),
                    )
                    .unwrap();
                eprintln!("Reaper resumed combat at {tick} after actual E impact at {start}");
                return;
            }
        } else if moved && inflicted > 0.0 && press_at.is_none() {
            let position = last_reaper_pose.unwrap();
            if (position[0] - defender.pose.feet[0]).hypot(position[2] - defender.pose.feet[2])
                < 6.0
            {
                press_at = Some(tick + 1);
                e_target_health = Some(state(room, reaper.unwrap()).unwrap().vitals.total());
                eprintln!(
                    "actual E press scheduled: tick={}, Cassidy={:?}, Reaper={position:?}",
                    tick + 1,
                    defender.pose.feet
                );
            }
        }
        assert!(
            scene.encounter_snapshot().is_some(),
            "encounter stopped at tick {tick}"
        );
    }
    assert!(
        reaper.is_some(),
        "normal authored encounter did not spawn Reaper"
    );
    assert!(
        moved,
        "Reaper never left his authored introduction position"
    );
    assert!(inflicted > 0.0, "Reaper never attacked the living defender");
    panic!("Reaper did not resume combat after its control window");
}

#[test]
#[ignore = "requires the external 104319 Junkenstein scene and NPC graphs"]
fn normal_street_wave_keeps_the_zomnic_body_running_after_setup() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        game_instance::RETAIL_QUANTA,
        game_replication::BodyStep,
        match_scene::team_bit,
        scene_assets::SceneAssets,
        script_init::StateKind,
        script_player::OwnerBodySpawn,
        script_snapshot_wire::{SnapshotEncoder, View},
    };

    const DEFENDERS: [u32; 2] = [0xa000_0101, 0xa000_0102];
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = SceneAssets::load(&runtime)
        .unwrap()
        .prepare_junkenstein(map, card, 0)
        .unwrap();
    let cassidy = scene.heroes().find_hero("Cassidy").unwrap();
    let mover = scene.player_spawn_mover(cassidy.body, 0).unwrap();
    let mut owner = scene
        .owner_body_runtime(
            cassidy,
            OwnerBodySpawn {
                slot: 0,
                controller: 0xa000_0100,
                entity: DEFENDERS[0],
                body_filter: team_bit(0).unwrap(),
                frame: 2700,
                pose: mover.pose(),
                eye_position: crate::game_combat::script_eye_position(cassidy.guid, &mover),
                skin: 0,
                quanta: RETAIL_QUANTA,
            },
        )
        .unwrap()
        .unwrap();
    let cassidy = cassidy.guid;
    let full_health = owner.vitals().unwrap().total();
    owner.take_health_update();
    let gate_position = scene.junkenstein_gate().unwrap().position;
    let gate_damage_before = scene.gate_snapshot().unwrap().variables.get(&8569).cloned();
    scene.bot_simulation().0.activate_combat();
    let mut first_active = None;
    let mut first_body = None;
    let mut checked_setup = false;
    let mut checked_contacts = false;
    let mut saw_dead = false;
    let mut moved_from_spawn = false;
    let mut spawn_position = None;
    let mut victims = Vec::new();
    let mut checked_hurt_state = false;
    let mut saw_authored_speed = false;
    for tick in 2700..=5800 {
        scene.step_encounter(tick, Some(cassidy));
        let (room, queries) = scene.bot_simulation();
        let position = first_body.and_then(|entity| state(room, entity).map(|s| s.bot.position));
        room.set_external_targets(position.map_or_else(Vec::new, |position| {
            DEFENDERS
                .into_iter()
                .map(|entity| crate::script_enemy::EnemyTarget {
                    hero: None,
                    filter_flags: None,
                    tags: Default::default(),
                    health: None,
                    velocity: None,
                    script_state: Default::default(),
                    entity,
                    team: 0,
                    pose: TargetPose {
                        feet: position,
                        height: 1.8,
                        radius: 0.35,
                        eye: 1.55,
                    },
                    alive: true,
                })
                .collect()
        }));
        // Movement consumes the pre-command modifier; script advancement below
        // refreshes the following frame's value (including authored randomness).
        let applied_scale = first_body
            .and_then(|entity| state(room, entity))
            .and_then(|state| state.enemy.as_ref())
            .map(|enemy| enemy.scripts.movement_speed_scale(tick));
        room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
            .unwrap();
        for hit in room.take_external_enemy_damage() {
            if Some(hit.source) == first_body {
                assert!(hit.amount > 0.0);
                if hit.target == DEFENDERS[0] {
                    assert!(
                        !owner
                            .receive_damage(
                                hit.amount,
                                Death {
                                    time: f64::from(tick) * 0.016,
                                    frame: tick,
                                    killer: hit.source,
                                },
                                &crate::script_init::HitTags::EMPTY
                            )
                            .killed
                    );
                }
                victims.push(hit.target);
            }
        }
        if victims.contains(&DEFENDERS[0]) {
            owner
                .advance(
                    Command {
                        frame: tick,
                        ..Default::default()
                    },
                    &mover,
                    BodyStep::UnposedTimer,
                )
                .unwrap_or_else(|err| panic!("Cassidy after taking damage at frame {tick}: {err}"));
            if !checked_hurt_state {
                let snapshot = owner.snapshot().unwrap();
                let encoder = owner.encoder();
                let projected = encoder.project(snapshot, View::Owner).unwrap();
                encoder.full(&projected).unwrap();
                checked_hurt_state = snapshot.instances.values().any(|instance| {
                    instance.graph == 0x0033
                        && instance.states.get(&22).is_some_and(|state| state.active)
                });
            }
        }
        let retired = room.take_retired_enemies();
        if first_body.is_some_and(|entity| retired.contains(&entity)) {
            assert!(checked_setup);
            assert!(checked_contacts);
            assert!(saw_dead);
            assert!(moved_from_spawn);
            assert!(saw_authored_speed);
            assert!(checked_hurt_state);
            assert_eq!(victims, DEFENDERS);
            assert!(owner.vitals().unwrap().total() < full_health);
            assert!(owner.take_health_update().is_some());
            assert_ne!(
                scene.gate_snapshot().unwrap().variables.get(&8569),
                gate_damage_before.as_ref(),
            );
            return;
        }
        let first = first_body.or_else(|| {
            room.states()
                .map(Result::unwrap)
                .find_map(|state| state.enemy.as_ref().map(|enemy| enemy.binding.body))
        });
        let Some(entity) = first else {
            continue;
        };
        first_body = Some(entity);
        let state = state(room, entity).expect("first wave body retires through the room");
        let initial_position = *spawn_position.get_or_insert(state.bot.position);
        moved_from_spawn |= norm(std::array::from_fn(|i| {
            state.bot.position[i] - initial_position[i]
        })) > 10.0;
        let enemy = state.enemy.as_ref().unwrap();
        assert!(enemy.fault.is_none(), "frame {tick}: {:?}", enemy.fault);
        let horizontal_speed = state.bot.velocity[0].hypot(state.bot.velocity[2]);
        if let Some(scale) = applied_scale {
            assert!(
                horizontal_speed <= 3.0 * scale + 0.02,
                "Zomnic speed {horizontal_speed}, applied scale {scale}"
            );
            saw_authored_speed |= horizontal_speed > 2.0;
        }

        if state.bot.dead {
            assert!(
                norm(std::array::from_fn(
                    |i| state.bot.position[i] - gate_position[i]
                )) < 3.0
            );
            saw_dead = true;
            assert!(state.respawn_at.is_none());
        }
        let body = enemy.scripts.body.snapshot().unwrap();
        if !checked_contacts {
            if let Some((&id, explosion)) = body.instances.iter().find(|(_, instance)| {
                instance.graph == 0x0c89
                    && instance.states.get(&17).is_some_and(|state| state.active)
            }) {
                let crate::script_wire::Value::Array(contacts) = &explosion.variables[&375] else {
                    panic!("expected the authored contact array")
                };
                assert!(contacts.starts_with(&DEFENDERS.map(crate::script_wire::Value::Entity)));
                let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                let projected = encoder.project(body, View::Observer).unwrap();
                assert!(projected.instance_states(id).unwrap().contains(&17));
                assert_eq!(
                    projected.instance_variables(id).unwrap().get(&375),
                    explosion.variables.get(&375)
                );
                encoder.full(&projected).unwrap();
                checked_contacts = true;
            }
        }
        if checked_setup {
            continue;
        }
        let Some(sender) = body.instances.values().find_map(|instance| {
            (instance.graph == 0x0cbc)
                .then(|| instance.states.get(&49))
                .flatten()
        }) else {
            continue;
        };
        assert!(sender.active);
        assert_eq!(sender.activation, 1);
        assert_eq!(
            sender.kind,
            StateKind::PersistentSend(0x0240_0000_0000_0744)
        );
        let initial_frame = *first_active.get_or_insert(tick);
        if tick - initial_frame == 250 {
            let controller = enemy.scripts.controller.snapshot().unwrap();
            let patrol = controller
                .instances
                .values()
                .find(|instance| instance.graph == 0x0c88)
                .expect("authored Zomnic behavior graph");
            assert_eq!(
                patrol.variables.get(&6947),
                Some(&crate::script_wire::Value::Array(vec![
                    crate::script_wire::Value::Asset(0x0d80_0000_0000_20b5)
                ]))
            );
            let candidate = &patrol.states[&0];
            assert!(candidate.active);
            assert_eq!(candidate.kind, StateKind::AiCandidate { selected: false });
            let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
            encoder
                .full(&encoder.project(body, View::Observer).unwrap())
                .unwrap();
            checked_setup = true;
        }
    }
    panic!("the first street wave body did not complete its lifecycle");
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn admitted_flashbang_hits_deliver_effects_to_all_boss_pairs() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        game_heroes::HeroCatalog,
        npc_catalog::{NpcCatalog, encounter_definition},
        scene_assets::SceneAssets,
        script_codecs::CodecTable,
        script_enemy::{EnemyGate, EnemySpawn},
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation, Site},
        script_snapshot_wire::{SnapshotEncoder, View},
        script_wire::{Value, Variables},
    };
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&runtime).unwrap();
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs =
        Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
    let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
    let npcs = NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
    let maps = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    for (hero, exclusion) in [
        (REAPER, None),
        (ROADHOG, None),
        (SUMMONER, None),
        (JUNKRAT, None),
        (WITCH, None),
        (REAPER, Some(0x0d60_0000_0000_02cbu64)),
        (REAPER, Some(0x0d60_0000_0000_003au64)),
        (REAPER, Some(0x0d60_0000_0000_093du64)),
    ] {
        let graphs = if let Some(tag) = exclusion {
            // Declared immunity input, not a claim about an ordinary Boss's
            // native category/tag inventory. Keep all other loaded data.
            let mut declared: serde_json::Value =
                serde_json::from_reader(flate2::read::GzDecoder::new(
                    std::fs::File::open(runtime.join("data/game_graphs_174.json.gz")).unwrap(),
                ))
                .unwrap();
            let body = format!("0x{:016X}", graphs.enemy_pair(hero).unwrap().body);
            declared["junkenstein"]["entities"][&body]["target_tags"]
                .as_array_mut()
                .unwrap()
                .push(serde_json::json!(format!("0x{tag:016X}")));
            Arc::new(GraphStore::from_json(&serde_json::to_vec(&declared).unwrap()).unwrap())
        } else {
            Arc::clone(&graphs)
        };
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
        let gate = scene.junkenstein_gate().map(|gate| EnemyGate {
            entity: gate.entity,
            position: gate.position,
        });
        let definition = encounter_definition(Some(&npcs), &graphs, &heroes, hero).unwrap();
        let request = EntityCreation {
            position: [74.0, 12.6, -99.0],
            kind: CreationKind::Hero {
                hero,
                team: 1,
                yaw: 180.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: Some(graphs.enemy_pair(hero).unwrap().controller),
                skin: None,
            },
        };
        let (room, queries) = scene.bot_simulation();
        let target = room
            .create_enemy(EnemySpawn {
                graphs: Arc::clone(&graphs),
                codecs: Arc::clone(&codecs),
                definition: &definition,
                request: &request,
                mode: card.mode,
                mode_variables: Arc::new(Variables::from([(6751, Value::Asset(card.difficulty))])),
                tick: 1000,
                walk: Default::default(),
                gate,
            })
            .unwrap_or_else(|error| panic!("Boss {hero:X}: {error}"));
        let before = state(room, target).unwrap().vitals.total();
        let owner = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        let caller = Site {
            entity: owner.entity,
            instance: 1,
            graph: 0x01e9,
            node: Some(7),
        };
        // Synthetic admitted physical hit, real collision and complete paired
        // NPC adapters. Separate E-input replay verifies the authored launch.
        let mut launch = flight_fixture(owner, 30.0);
        launch.stun = Stun::from_seconds(0.8);
        launch.hit_effect = Some(caller);
        launch.flight = ProjectileFlight::new(
            ProjectileBallistics {
                speed: 30.0,
                lifetime: 1.0,
                max_distance: None,
                terrain_rebound: None,
                terrain_radius: 0.0,
                radius: 0.0,
                definition: Some(0x0400_0000_0000_037d),
                direct: Some(HitDefinition {
                    amount: 25.0,
                    tags: crate::game_hitscan::DamageTags::EMPTY,
                    loadout: None,
                    critical: 1.0,
                    falloff: None,
                }),
                blast: None,
            },
            [74.0, 13.6, -100.0],
            [0.0, 0.0, 1.0],
            None,
        )
        .unwrap();
        let mut impacts = 0;
        for tick in 1000..=1120 {
            let (_, hits) = room
                .advance_projectiles(
                    &queries,
                    7,
                    owner,
                    if tick == 1000 {
                        vec![std::mem::replace(&mut launch, flight_fixture(owner, 30.0))]
                    } else {
                        vec![]
                    },
                    ShotContext {
                        team: 0,
                        quanta: 0,
                        tick,
                        now: Duration::from_millis(u64::from(tick) * 16),
                    },
                    |_| true,
                )
                .unwrap();
            if !hits.is_empty() {
                assert_eq!(
                    state(room, target).unwrap().bot.is_stunned(tick),
                    exclusion.is_none(),
                    "Boss {hero:X}, exclusion {exclusion:?}: damage must not bypass control immunity"
                );
            }
            impacts += hits.len();
            room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
                .unwrap();
            let actor = state(room, target).unwrap();
            let enemy = actor.enemy.as_ref().unwrap();
            assert!(
                enemy.fault.is_none(),
                "Boss {hero:X} at {tick}: {:?}",
                enemy.fault
            );
            if impacts != 0 {
                let snapshot = enemy.scripts.body.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                assert!(
                    snapshot
                        .instances
                        .values()
                        .any(|instance| instance.graph == 0x224e),
                    "Boss {hero:X} missing hit effect at {tick}"
                );
                let encoder = SnapshotEncoder::new(&graphs, &codecs);
                encoder
                    .full(&encoder.project(snapshot, View::Observer).unwrap())
                    .unwrap();
            }
        }
        assert_eq!(
            impacts, 1,
            "Boss {hero:X}: admitted hit must resolve exactly once"
        );
        assert_eq!(state(room, target).unwrap().vitals.total(), before - 25.0);
    }
}

#[test]
#[ignore = "requires the external 104319 Junkenstein enemy graphs and codecs"]
fn authored_encounter_enemies_publish_their_observer_baselines() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
        game_heroes::HeroCatalog,
        npc_catalog::{NpcCatalog, encounter_definition},
        pve_encounter::navigation::SHOCK_TIRE,
        script_codecs::CodecTable,
        script_enemy::EnemySpawn,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation, StateKind},
        script_snapshot_wire::{SnapshotEncoder, View},
        script_wire::{Value, Variables},
    };

    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs =
        Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
    let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
    let npcs = NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = SceneAssets::load(&runtime)
        .unwrap()
        .prepare_junkenstein(map, card, 0)
        .unwrap();
    let (room, _) = scene.bot_simulation();
    let mut witch_targets = [0; 2];
    let mut summoner = None;
    for hero in [
        0x02e0_0000_0000_0177,
        0x02e0_0000_0000_0065,
        SHOCK_TIRE,
        0x02e0_0000_0000_0004,
        0x02e0_0000_0000_0016,
    ] {
        let definition = encounter_definition(Some(&npcs), &graphs, &heroes, hero).unwrap();
        let request = EntityCreation {
            position: [74.0, 12.6, -99.0],
            kind: CreationKind::Hero {
                hero,
                team: 1,
                yaw: 180.0,
                flags: [Value::Bool(false), Value::Bool(false)],
                controller: Some(graphs.enemy_pair(hero).unwrap().controller),
                skin: None,
            },
        };
        let entity = room
            .create_enemy(EnemySpawn {
                graphs: graphs.clone(),
                codecs: codecs.clone(),
                definition: &definition,
                request: &request,
                mode: JUNKENSTEIN_REVENGE,
                mode_variables: Arc::new(Variables::from([(6751, Value::Asset(card.difficulty))])),
                tick: 4200,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            })
            .unwrap_or_else(|error| panic!("hero {hero:#x}: {error}"));
        match hero {
            WITCH => witch_targets[0] = entity,
            JUNKRAT => witch_targets[1] = entity,
            _ => (),
        }
        assert_eq!(room.take_created().len(), 1);
        assert_eq!(room.encounter_possessor(entity), Some(entity + 1));
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_mut().unwrap();
        let body = enemy.scripts.body.snapshot().unwrap();
        let controller = enemy.scripts.controller.snapshot().unwrap();
        let (snapshot, graph, raw, expected) = match hero {
            0x02e0_0000_0000_0177 => (controller, 0x0c87, 19, true),
            0x02e0_0000_0000_0065 => (body, 0x0232, 22, false),
            SHOCK_TIRE => {
                assert_eq!(enemy.scripts.ai_control_setting(), Some((20.0, true)));
                (body, 0x0234, 35, false)
            }
            0x02e0_0000_0000_0004 => (body, 0x0071, 10, false),
            0x02e0_0000_0000_0016 => (body, 0x1571, 47, false),
            _ => unreachable!(),
        };
        let instance = snapshot
            .instances
            .values()
            .find(|i| i.graph == graph)
            .unwrap();
        assert_eq!(
            instance.states[&raw].kind,
            StateKind::BooleanSwitch(expected)
        );

        let encoder = SnapshotEncoder::new(&graphs, &codecs);
        encoder
            .full(&encoder.project(body, View::Observer).unwrap())
            .unwrap();
        if matches!(hero, 0x02e0_0000_0000_0004 | 0x02e0_0000_0000_0016) {
            for tick in 4201..=4320 {
                enemy
                    .scripts
                    .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, tick)
                    .unwrap_or_else(|error| panic!("hero {hero:#x} at tick {tick}: {error}"));
            }
        }
        if hero == SHOCK_TIRE {
            enemy
                .scripts
                .teardown(&enemy.graphs, &enemy.binding, &mut state.vitals)
                .unwrap();
            assert_eq!(enemy.scripts.ai_control_setting(), Some((5.0, false)));
        }
        if !matches!(hero, WITCH | JUNKRAT) {
            let handle = room.by_wire.shift_remove(&entity).unwrap();
            if hero == SUMMONER {
                summoner = Some((entity, handle));
            } else {
                assert!(room.world.despawn(handle));
            }
        }
    }
    let [witch, wounded] = witch_targets;
    let (room, queries) = scene.bot_simulation();
    let defender = 0xa000_0101;
    let hostile = crate::script_enemy::EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: defender,
        team: 0,
        pose: TargetPose {
            feet: [74.0, 12.6, -103.0],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    };
    room.set_external_targets(vec![hostile.clone()]);
    room.state_mut(wounded).unwrap().unwrap().bot.position = [74.0, 12.6, -97.0];
    RoomSimulation::damage_bot(
        room.state_mut(wounded).unwrap().unwrap(),
        120.0,
        Death {
            time: 69.12,
            frame: 4320,
            killer: defender,
        },
        Duration::from_millis(69_120),
    )
    .unwrap();
    let damaged = state(room, wounded).unwrap().vitals.total();
    assert!(damaged > 0.0, "wounded fixture died from test damage");
    for frame in 4321..=4370 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        let recipient = state(room, wounded).unwrap();
        assert!(
            recipient.enemy.as_ref().unwrap().fault.is_none(),
            "recipient fault at {frame}, health {} from {damaged}, links {:?}: {:?}",
            recipient.vitals.total(),
            room.witch_staff_links,
            recipient.enemy.as_ref().unwrap().fault
        );
    }
    let check_targets = |room: &RoomSimulation, enemy, ally, slot| {
        let witch = state(room, witch).unwrap().enemy.as_ref().unwrap();
        assert!(witch.fault.is_none(), "{:?}", witch.fault);
        let controller = witch.scripts.controller.snapshot().unwrap();
        let control = controller
            .instances
            .values()
            .find(|instance| instance.graph == 0x0cd2)
            .unwrap();
        assert_eq!(control.variables.get(&2299), Some(&Value::Entity(enemy)));
        assert_eq!(control.variables.get(&7126), Some(&Value::Entity(ally)));
        if let Some(slot) = slot {
            let body = witch.scripts.body.snapshot().unwrap();
            assert_eq!(body.variables.get(&30), Some(&Value::Integer(slot)));
        }
    };
    let recipient = state(room, wounded).unwrap();
    let recipient_scripts = &recipient.enemy.as_ref().unwrap().scripts;
    assert!(
        recipient.enemy.as_ref().unwrap().fault.is_none(),
        "{:?}",
        recipient.enemy.as_ref().unwrap().fault
    );
    check_targets(room, defender, wounded, Some(1));
    let healed = recipient.vitals.total();
    assert!(healed > damaged, "staff healed {healed} from {damaged}");
    let &(target, effect) = room.witch_staff_links.get(&witch).expect("staff link");
    assert_eq!(target, wounded);
    let snapshot = recipient_scripts.body.snapshot().unwrap();
    assert_eq!(snapshot.instances[&effect].graph, 0x0079);
    assert_eq!(
        snapshot.instances[&effect].variables.get(&27),
        Some(&Value::Entity(witch))
    );
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    encoder
        .full(&encoder.project(snapshot, View::Observer).unwrap())
        .unwrap();
    assert!(
        room.take_scripted_health()
            .iter()
            .any(|update| update.entity == wounded)
    );
    room.state_mut(wounded).unwrap().unwrap().bot.position = [58.0, 12.6, -91.0];
    for frame in 4371..=4400 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
    }
    let recipient = state(room, wounded).unwrap();
    assert!(
        recipient.enemy.as_ref().unwrap().fault.is_none(),
        "{:?}",
        recipient.enemy.as_ref().unwrap().fault
    );
    assert!(
        recipient.vitals.total() - healed <= 1.0,
        "staff kept healing outside range"
    );
    assert!(
        !recipient
            .enemy
            .as_ref()
            .unwrap()
            .scripts
            .body
            .snapshot()
            .unwrap()
            .instances
            .contains_key(&effect)
    );
    assert!(
        !room
            .take_external_enemy_damage()
            .iter()
            .any(|hit| hit.source == witch)
    );
    room.set_external_targets(Vec::new());
    let staff_start = state(room, witch).unwrap().bot.position;
    let ally_position = [74.0, 12.6, -80.0];
    assert!(
        norm(std::array::from_fn(
            |axis| ally_position[axis] - staff_start[axis]
        )) > 12.0
    );
    let ally = room.state_mut(wounded).unwrap().unwrap();
    ally.bot.position = ally_position;
    let before_pursuit_heal = ally.vitals.total();
    let staff_tick = (4401..=4700)
        .find(|&frame| {
            room.finish_tick(
                &queries,
                frame,
                Duration::from_millis(u64::from(frame) * 16),
            )
            .unwrap();
            state(room, wounded).unwrap().vitals.total() > before_pursuit_heal
        })
        .unwrap_or_else(|| {
            panic!(
                "Witch did not reach staff range from {staff_start:?}, stopped at {:?}",
                state(room, witch).unwrap().bot.position
            )
        });
    let caster = state(room, witch).unwrap();
    assert!(
        norm(std::array::from_fn(
            |axis| caster.bot.position[axis] - staff_start[axis]
        )) > 2.0
    );
    assert_eq!(caster.bot.movement(staff_tick).throttles, [0; 2]);
    room.state_mut(wounded).unwrap().unwrap().vitals.heal(120.0);
    room.state_mut(witch).unwrap().unwrap().bot.position = [74.0, 12.6, -99.0];
    room.set_external_targets(vec![hostile.clone()]);
    let pistol_tick = staff_tick + 80;
    for frame in staff_tick + 1..=pistol_tick {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
    }
    check_targets(room, defender, 0, Some(2));
    let pistol_damage = room
        .take_external_enemy_damage()
        .into_iter()
        .filter(|hit| hit.source == witch && hit.target == defender)
        .map(|hit| hit.amount)
        .collect::<Vec<_>>();
    assert!(!pistol_damage.is_empty(), "Witch did not hit the defender");
    assert!(
        pistol_damage.iter().all(|&amount| amount == 5.0),
        "{pistol_damage:?}"
    );
    let handle = room.by_wire.shift_remove(&wounded).unwrap();
    assert!(room.world.despawn(handle));
    let mut covered = hostile.clone();
    covered.pose.feet = [58.0, 12.6, -91.0];
    let [x, y, z] = state(room, witch).unwrap().bot.position;
    let origin = [x, y + eye_height(WITCH, false), z];
    assert!(
        crate::static_ray::norm(std::array::from_fn(|i| covered.pose.feet[i] - origin[i])) < 20.0
    );
    assert!(queries.visible_aim(origin, covered.pose).is_none());
    room.set_external_targets(vec![covered]);
    let cover_tick = pistol_tick + 60;
    for frame in pistol_tick + 1..=cover_tick {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
    }
    check_targets(room, defender, 0, Some(2));
    let caster = state(room, witch).unwrap().enemy.as_ref().unwrap();
    assert_eq!(
        caster.scripts.body.command_buttons() & crate::commands::FIRE,
        0
    );
    assert!(
        !room
            .take_external_enemy_damage()
            .iter()
            .any(|hit| hit.source == witch)
    );
    let pursuit_start = state(room, witch).unwrap().bot.position;
    let mut distant = hostile;
    distant.pose.feet = [74.0, 12.6, -74.0];
    assert!(
        norm(std::array::from_fn(
            |axis| distant.pose.feet[axis] - pursuit_start[axis]
        )) > 20.0
    );
    room.set_external_targets(vec![distant]);
    let mut hit_at = None;
    for frame in cover_tick + 1..=cover_tick + 360 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        if room
            .take_external_enemy_damage()
            .iter()
            .any(|hit| hit.source == witch && hit.target == defender)
        {
            hit_at = Some(frame);
            break;
        }
    }
    let caster = state(room, witch).unwrap();
    let hit_at = hit_at.unwrap_or_else(|| {
        panic!(
            "Witch did not reach pistol range from {pursuit_start:?}, stopped at {:?}: {:?}",
            caster.bot.position,
            caster.enemy.as_ref().unwrap().fault
        )
    });
    assert!(
        norm(std::array::from_fn(
            |axis| caster.bot.position[axis] - pursuit_start[axis]
        )) > 2.0
    );
    assert_eq!(caster.bot.movement(hit_at).throttles, [0; 2]);
    room.set_external_targets(Vec::new());
    room.finish_tick(
        &queries,
        hit_at + 1,
        Duration::from_millis(u64::from(hit_at + 1) * 16),
    )
    .unwrap();
    check_targets(room, 0, 0, None);

    let handle = room.by_wire.shift_remove(&witch).unwrap();
    assert!(room.world.despawn(handle));
    let (summoner, handle) = summoner.unwrap();
    room.by_wire.insert(summoner, handle);
    // This synthetic defender explicitly has a team filter and no catalog tags.
    let mut target = crate::script_enemy::EnemyTarget {
        hero: None,
        filter_flags: Some(0x0080_0001),
        tags: crate::script_init::TargetTagEffects::default()
            .snapshot(Some(&std::collections::BTreeSet::new())),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: defender,
        team: 0,
        pose: TargetPose {
            feet: [74.0, 12.6, -89.0],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    };
    room.set_external_targets(vec![target.clone()]);
    let mut orb_hit = None;
    for frame in hit_at + 2..=hit_at + 180 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        let enemy = state(room, summoner).unwrap().enemy.as_ref().unwrap();
        assert!(
            enemy.fault.is_none(),
            "Summoner orb at {frame}: {:?}",
            enemy.fault
        );
        if room
            .take_external_enemy_damage()
            .iter()
            .any(|hit| hit.source == summoner && hit.target == defender && hit.amount > 0.0)
        {
            orb_hit = Some(frame);
            break;
        }
    }
    let orb_hit = orb_hit.expect("Summoner orb hit");
    let check_summoner_targets =
        |room: &RoomSimulation, primary: Option<Value>, secondary: Option<Value>| {
            let scripts = &state(room, summoner)
                .unwrap()
                .enemy
                .as_ref()
                .unwrap()
                .scripts;
            let controller = scripts.controller.snapshot().unwrap();
            let control = controller
                .instances
                .values()
                .find(|instance| instance.graph == 0x185d)
                .unwrap();
            assert_eq!(control.variables.get(&8066), primary.as_ref());
            assert_eq!(control.variables.get(&11652), secondary.as_ref());
        };
    check_summoner_targets(room, None, Some(Value::Entity(defender)));
    target.pose.feet = [74.0, 12.6, -96.0];
    room.set_external_targets(vec![target.clone()]);
    let mut beam_ticks = 0;
    for frame in orb_hit + 1..=orb_hit + 80 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        let enemy = state(room, summoner).unwrap().enemy.as_ref().unwrap();
        assert!(
            enemy.fault.is_none(),
            "Summoner beam at {frame}: {:?}",
            enemy.fault
        );
        for hit in room
            .take_external_enemy_damage()
            .into_iter()
            .filter(|hit| hit.source == summoner && hit.target == defender)
        {
            beam_ticks += usize::from((hit.amount - 0.0512).abs() < 1e-6);
        }
    }
    assert!(
        beam_ticks > 20,
        "only {beam_ticks} normal beam damage ticks"
    );
    check_summoner_targets(
        room,
        Some(Value::Entity(defender)),
        Some(Value::Entity(defender)),
    );
    let scripts = &state(room, summoner)
        .unwrap()
        .enemy
        .as_ref()
        .unwrap()
        .scripts;
    let encoder = SnapshotEncoder::new(&graphs, &codecs);
    encoder
        .full(
            &encoder
                .project(scripts.body.snapshot().unwrap(), View::Observer)
                .unwrap(),
        )
        .unwrap();

    target.pose.feet = [58.0, 12.6, -91.0];
    room.set_external_targets(vec![target]);
    for frame in orb_hit + 81..=orb_hit + 100 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        let enemy = state(room, summoner).unwrap().enemy.as_ref().unwrap();
        assert!(
            enemy.fault.is_none(),
            "Summoner behind cover at {frame}: {:?}",
            enemy.fault
        );
        assert_eq!(
            enemy.scripts.body.command_buttons()
                & (crate::commands::FIRE | crate::commands::SECONDARY),
            0
        );
        assert!(
            room.take_external_enemy_damage()
                .iter()
                .all(|hit| hit.source != summoner)
        );
    }
    room.set_external_targets(Vec::new());
    room.finish_tick(
        &queries,
        orb_hit + 101,
        Duration::from_millis(u64::from(orb_hit + 101) * 16),
    )
    .unwrap();
    check_summoner_targets(room, Some(Value::Entity(0)), Some(Value::Entity(0)));
}

#[test]
#[ignore = "requires the external 104319 Junkenstein NPC, graph and collision pack"]
fn authored_shock_tire_damages_the_gate_and_retires() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        npc_catalog::NpcCatalog,
        pve_encounter::navigation::SHOCK_TIRE,
        script_codecs::CodecTable,
        script_enemy::{EnemyGate, EnemySpawn},
        script_expr::to_float,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation, PlayScript, Site},
        script_wire::Value,
        world::GAME_MODE_ENTITY,
    };
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = SceneAssets::load(&runtime)
        .unwrap()
        .prepare_junkenstein(map, card, 0)
        .unwrap();
    scene.step_encounter(4200, None);
    let gate = scene.junkenstein_gate().unwrap();
    let gate = EnemyGate {
        entity: gate.entity,
        position: gate.position,
    };
    let mut position = gate.position;
    position[0] += 1.0;
    let previous = to_float(&scene.gate_snapshot().unwrap().variables[&8569]);
    let mode_variables = Arc::new(scene.encounter_snapshot().unwrap().variables.clone());
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs =
        Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
    let catalog = NpcCatalog::load(runtime.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
    let request = EntityCreation {
        position,
        kind: CreationKind::Hero {
            hero: SHOCK_TIRE,
            team: 1,
            yaw: 0.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: Some(graphs.enemy_pair(SHOCK_TIRE).unwrap().controller),
            skin: None,
        },
    };
    let (room, _) = scene.bot_simulation();
    let body = room
        .create_enemy(EnemySpawn {
            graphs,
            codecs,
            definition: catalog.get(SHOCK_TIRE).unwrap(),
            request: &request,
            mode: card.mode,
            mode_variables,
            tick: 4200,
            walk: crate::game_bots::BotWalk::default(),
            gate: Some(gate),
        })
        .unwrap();
    assert!(room.queue_encounter_wave(
        Site {
            entity: GAME_MODE_ENTITY,
            instance: 1,
            graph: 0x0cba,
            node: Some(9)
        },
        body + 1,
        PlayScript {
            policy: 0,
            overrides: Vec::new(),
            graph: 0x0c95,
            source: GAME_MODE_ENTITY,
            parameters: vec![(
                0x0a40_0000_0000_03a1,
                Value::Array(vec![Value::Vector(position)])
            )],
            auto_retire: true,
        },
    ));
    let state = room.state_mut(body).unwrap().unwrap();
    let enemy = state.enemy.as_mut().unwrap();
    enemy
        .scripts
        .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, 4201)
        .unwrap();
    let mut retired_at = None;
    for tick in 4202..=4600 {
        scene.step_encounter(tick, None);
        let (room, queries) = scene.bot_simulation();
        room.set_external_targets(Vec::new());
        room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
            .unwrap();
        if let Some(state) = room.state_mut(body).unwrap() {
            assert!(
                state.enemy.as_ref().unwrap().fault.is_none(),
                "tick {tick}: {:?}",
                state.enemy.as_ref().unwrap().fault
            );
        } else {
            retired_at = Some(tick);
            break;
        }
    }
    // The queued gate damage reaches its script on the next scene tick.
    scene.step_encounter(retired_at.expect("the tire retires after impact") + 1, None);
    assert!(
        scene
            .bot_simulation()
            .0
            .take_retired_enemies()
            .contains(&body)
    );
    assert!(to_float(&scene.gate_snapshot().unwrap().variables[&8569]) > previous);
}

#[test]
#[ignore = "requires the external 104319 Junkenstein scene and NPC graphs"]
fn normal_zombardier_wave_fires_at_a_live_defender() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        scene_assets::SceneAssets,
        script_enemy::EnemyTarget,
        script_snapshot_wire::{SnapshotEncoder, View},
    };

    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = SceneAssets::load(runtime)
        .unwrap()
        .prepare_junkenstein(map, card, 0)
        .unwrap();
    let defender = 0xa000_0101;
    let mut spawned = None;
    let mut in_flight = false;
    let mut saw_weapon_input = false;
    let mut checked_projection = false;
    let mut source_retired = false;
    scene.bot_simulation().0.set_external_targets(Vec::new());
    for tick in 2700..=7900 {
        scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
        let (room, queries) = scene.bot_simulation();
        if spawned.is_none() {
            let bot = room
                .bodies()
                .unwrap()
                .find(|bot| bot.spawn.hero == ZOMBARDIER)
                .map(|bot| (bot.entity, bot.position));
            if let Some((body, position)) = bot {
                let [x, y, z] = position;
                spawned = Some((body, position));
                room.set_external_targets(vec![EnemyTarget {
                    hero: None,
                    filter_flags: None,
                    tags: Default::default(),
                    health: None,
                    velocity: None,
                    script_state: Default::default(),
                    entity: defender,
                    team: 0,
                    pose: TargetPose {
                        feet: [x, y, z + 10.0],
                        height: 1.8,
                        radius: 0.35,
                        eye: 1.55,
                    },
                    alive: true,
                }]);
            }
        }
        room.finish_tick(
            &queries,
            tick,
            Duration::from_millis(u64::from(tick - 2700) * 16),
        )
        .unwrap();
        let damage = room.take_external_enemy_damage();
        let Some((body, _)) = spawned else { continue };
        in_flight |= room.enemy_projectiles.iter().any(|&handle| {
            room.world
                .get::<crate::script_enemy::EnemyProjectile>(handle)
                .is_some_and(|projectile| projectile.source == body)
        });
        source_retired |= room.take_retired_enemies().contains(&body);
        let enemy = state(room, body).and_then(|state| state.enemy.as_ref());
        if let Some(enemy) = enemy {
            assert!(enemy.fault.is_none(), "tick {tick}: {:?}", enemy.fault);
            saw_weapon_input |=
                enemy.scripts.body.command_buttons() & crate::commands::SECONDARY != 0;
            if in_flight && !checked_projection {
                let snapshot = enemy.scripts.body.snapshot().unwrap();
                let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                encoder
                    .full(&encoder.project(snapshot, View::Observer).unwrap())
                    .unwrap();
                checked_projection = true;
            }
        } else {
            assert!(
                source_retired,
                "source vanished without normal pair retirement"
            );
        }
        if let Some(hit) = damage
            .iter()
            .find(|hit| hit.source == body && hit.target == defender)
        {
            assert!(in_flight);
            assert!(saw_weapon_input);
            assert!(checked_projection);
            assert_eq!(hit.amount, 30.0);
            room.set_external_targets(Vec::new());
            room.finish_tick(
                &queries,
                tick + 1,
                Duration::from_millis(u64::from(tick + 1 - 2700) * 16),
            )
            .unwrap();
            // A launched grenade can outlive the source's eight-second
            // corpse grace. Input cleanup is observable only while bound.
            if let Some(enemy) = state(room, body).and_then(|state| state.enemy.as_ref()) {
                assert!(enemy.fault.is_none(), "{:?}", enemy.fault);
                assert_eq!(
                    enemy.scripts.body.command_buttons() & crate::commands::SECONDARY,
                    0
                );
            } else {
                assert!(source_retired);
            }
            return;
        }
    }
    panic!(
        "Zombardier did not hit the defender: spawned={spawned:?}, projectile launched={in_flight}"
    );
}

#[test]
#[ignore = "requires the external 104319 Junkenstein Boss graphs and codecs"]
fn authored_junkrat_fires_a_grenade_at_the_defender() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
        game_heroes::HeroCatalog,
        npc_catalog::encounter_definition,
        script_codecs::CodecTable,
        script_enemy::{EnemySpawn, EnemyTarget},
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation, PlayScript, Site},
        script_snapshot_wire::{SnapshotEncoder, View},
        script_wire::{Value, Variables},
    };
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs =
        Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
    let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = crate::scene_assets::SceneAssets::load(runtime)
        .unwrap()
        .prepare_junkenstein(map, card, 0)
        .unwrap();
    let definition = encounter_definition(None, &graphs, &heroes, JUNKRAT).unwrap();
    let request = EntityCreation {
        position: [74.0, 12.6, -99.0],
        kind: CreationKind::Hero {
            hero: JUNKRAT,
            team: 1,
            yaw: 180.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: Some(graphs.enemy_pair(JUNKRAT).unwrap().controller),
            skin: None,
        },
    };
    let (room, queries) = scene.bot_simulation();
    let body = room
        .create_enemy(EnemySpawn {
            graphs: Arc::clone(&graphs),
            codecs: Arc::clone(&codecs),
            definition: &definition,
            request: &request,
            mode: JUNKENSTEIN_REVENGE,
            mode_variables: Arc::new(Variables::from([
                (6751, Value::Asset(card.difficulty)),
                (8522, Value::Bool(true)),
                (8566, Value::Bool(true)),
                (9948, Value::Integer(0)),
            ])),
            tick: 4200,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    let caller = Site {
        entity: crate::world::GAME_MODE_ENTITY,
        instance: 1,
        graph: 0x0cba,
        node: Some(9),
    };
    assert!(room.queue_encounter_wave(
        caller,
        body + 1,
        PlayScript {
            policy: 0,
            overrides: Vec::new(),
            graph: 0x0c95,
            source: caller.entity,
            parameters: vec![(
                0x0a40_0000_0000_03a1,
                Value::Array(vec![Value::Vector(request.position)])
            )],
            auto_retire: true,
        }
    ));
    let defender = 0xa000_0101;
    room.set_external_targets(vec![EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: defender,
        team: 0,
        pose: TargetPose {
            feet: [74.0, 12.6, -89.0],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    }]);
    let mut in_flight = false;
    for frame in 4201..=4650 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        assert!(room.take_boss_intro_starts().is_empty());
        in_flight |= !room.enemy_projectiles.is_empty();
        let enemy = state(room, body).unwrap().enemy.as_ref().unwrap();
        assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
        let hits = room.take_external_enemy_damage();
        if hits
            .iter()
            .any(|hit| hit.source == body && hit.target == defender)
        {
            assert!(in_flight);
            assert_eq!(
                hits.iter().map(|hit| hit.amount).collect::<Vec<_>>(),
                [10.0, 15.0]
            );
            let enemy = state(room, body).unwrap().enemy.as_ref().unwrap();
            let snapshot = enemy.scripts.body.snapshot().unwrap();
            let encoder = SnapshotEncoder::new(&graphs, &codecs);
            encoder
                .full(&encoder.project(snapshot, View::Observer).unwrap())
                .unwrap();
            return;
        }
    }
    panic!("Junkrat never hit the defender, projectile launched={in_flight}");
}

#[test]
#[ignore = "requires the external 104319 Junkenstein Boss graphs and codecs"]
fn authored_roadhog_wave_runs_its_intro_and_both_weapon_modes() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
        game_heroes::HeroCatalog,
        npc_catalog::encounter_definition,
        script_codecs::CodecTable,
        script_enemy::EnemySpawn,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation, PlayScript, Site, StateKind},
        script_snapshot_wire::{SnapshotEncoder, View},
        script_wire::{Value, Variables},
    };

    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs =
        Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
    let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
    let hero = 0x02e0_0000_0000_0040;
    let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
    let request = EntityCreation {
        position: [74.0, 12.6, -99.0],
        kind: CreationKind::Hero {
            hero,
            team: 1,
            yaw: 180.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: Some(graphs.enemy_pair(hero).unwrap().controller),
            skin: None,
        },
    };
    let card = JunkensteinCard::from_index(0x3c).unwrap();
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            card.mode,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = crate::scene_assets::SceneAssets::load(&runtime)
        .unwrap()
        .prepare_junkenstein(map, card, 0)
        .unwrap();
    scene.step_encounter(4200, None);
    let mode_variables = Arc::new(Variables::from([
        (9948, Value::Integer(0)),
        (6751, Value::Asset(card.difficulty)),
    ]));
    let (room, _) = scene.bot_simulation();
    let entity = room
        .create_enemy(EnemySpawn {
            graphs: Arc::clone(&graphs),
            codecs: Arc::clone(&codecs),
            definition: &definition,
            request: &request,
            mode: JUNKENSTEIN_REVENGE,
            mode_variables: mode_variables.clone(),
            tick: 4200,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    assert_eq!(room.take_created().len(), 1);
    assert_eq!(room.encounter_possessor(entity), Some(entity + 1));
    let enemy = room
        .state_mut(entity)
        .unwrap()
        .unwrap()
        .enemy
        .as_ref()
        .unwrap();
    let controller = enemy.scripts.controller.snapshot().unwrap();
    for graph in [0x0bc6, 0x0bc8] {
        assert!(
            controller
                .instances
                .values()
                .any(|instance| instance.graph == graph)
        );
    }
    assert!(
        controller.initialization_complete(),
        "{:?}",
        controller.diagnostics
    );
    let body = enemy.scripts.body.snapshot().unwrap();
    let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
    encoder
        .full(&encoder.project(body, View::Observer).unwrap())
        .unwrap();
    assert_eq!(enemy.scripts.outgoing_damage(10.0, &[]), 10.0);

    room.set_mode_variables(mode_variables);
    let caller = Site {
        entity: crate::world::GAME_MODE_ENTITY,
        instance: 1,
        graph: 0x0cba,
        node: Some(9),
    };
    let queue_wave = |room: &mut RoomSimulation, entity: u32| {
        assert!(room.queue_encounter_wave(
            caller,
            entity + 1,
            PlayScript {
                policy: 0,
                overrides: Vec::new(),
                graph: 0x0c95,
                source: caller.entity,
                parameters: vec![(
                    0x0a40_0000_0000_03a1,
                    Value::Array(vec![Value::Vector(request.position)]),
                )],
                auto_retire: true,
            }
        ));
    };
    queue_wave(room, entity);

    let mut intro_id = None;
    let mut cameras = 0;
    let mut anims = 0;
    let mut ended = false;
    let mut voices = Vec::new();
    for frame in 4201..=5200 {
        scene.step_encounter(frame, None);
        let (room, queries) = scene.bot_simulation();
        room.set_external_targets(Vec::new());
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        let enemy = room
            .state_mut(entity)
            .unwrap()
            .unwrap()
            .enemy
            .as_ref()
            .unwrap();
        assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
        assert_eq!(enemy.scripts.outgoing_damage(10.0, &[]), 8.0);
        assert_eq!(
            enemy
                .scripts
                .outgoing_damage(10.0, &[0x0d80_0000_0000_3fce]),
            10.0,
        );
        let body = enemy.scripts.body.snapshot().unwrap();
        let wave = body.instances.values().find(|i| i.graph == 0x0cbc).unwrap();
        assert_eq!(
            wave.states[&60].kind,
            StateKind::OutgoingDamageFilter { registered: true }
        );
        for (&id, intro) in body.instances.iter().filter(|(_, i)| i.graph == 0x0ce0) {
            assert_eq!(*intro_id.get_or_insert(id), id);
            if let Some(animation) = intro.states.get(&7) {
                anims = anims.max(animation.activation);
            }
            if let Some(camera) = intro.states.get(&14) {
                if camera.active && cameras == 0 {
                    let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                    encoder
                        .full(&encoder.project(body, View::Observer).unwrap())
                        .unwrap();
                }
                cameras = cameras.max(camera.activation);
            }
            ended |= intro
                .states
                .get(&3)
                .is_some_and(|wait| wait.activation == 1 && !wait.active);
        }
        voices.extend(room.take_encounter_voice_requests());
    }
    assert_eq!((anims, cameras, ended), (1, 1, true));
    let cues: Vec<_> = voices
        .iter()
        .filter(|voice| voice.anchor == entity)
        .map(|voice| (voice.speaker, voice.stimulus))
        .collect();
    assert_eq!(
        cues,
        [
            (crate::world::GAME_MODE_ENTITY, 0x0ee0_0000_0000_06b6),
            (entity, 0x0ee0_0000_0000_06bd),
            (crate::world::GAME_MODE_ENTITY, 0x0ee0_0000_0000_06da),
        ]
    );

    let defender = crate::script_enemy::EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: 0xa000_0101,
        team: 0,
        pose: TargetPose {
            feet: [74.0, 12.6, -97.0],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    };
    let mut empty_magazine = false;
    let mut fired_after_reload = false;
    let mut hit = None;
    let mut last_frame = 5200;
    for frame in 5201..=6300 {
        last_frame = frame;
        scene.step_encounter(frame, None);
        let (room, queries) = scene.bot_simulation();
        room.set_external_targets(vec![defender.clone()]);
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        let damage = room
            .take_external_enemy_damage()
            .into_iter()
            .find(|damage| damage.source == entity && damage.target == defender.entity);
        let enemy = room
            .state_mut(entity)
            .unwrap()
            .unwrap()
            .enemy
            .as_ref()
            .unwrap();
        assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
        let body = enemy.scripts.body.snapshot().unwrap();
        let weapon = body.instances.values().find(|i| i.graph == 0x01c6).unwrap();
        let shots = weapon.states.get(&9).map_or(0, |volley| volley.activation);
        empty_magazine |= weapon.variables[&53] == Value::Integer(0);
        if let Some(damage) = damage {
            if hit.is_none() {
                assert_eq!(shots, 1);
                assert_eq!(weapon.variables[&53], Value::Integer(4));
                let encoder = SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
                encoder
                    .full(&encoder.project(body, View::Observer).unwrap())
                    .unwrap();
            }
            hit = Some(damage.amount);
            if shots > 5 {
                fired_after_reload = empty_magazine && weapon.variables[&53] == Value::Integer(4);
                break;
            }
        }
    }
    let damage = hit.expect("Roadhog hits the nearby defender with the authored shotgun");
    assert!((damage - 1.44).abs() < 1e-6, "damage {damage}");
    assert!(fired_after_reload);

    let mut run_secondary = |distance: f64, direct: bool| {
        for _ in 0..85 {
            last_frame += 1;
            scene.step_encounter(last_frame, None);
            let (room, queries) = scene.bot_simulation();
            room.set_external_targets(Vec::new());
            room.finish_tick(
                &queries,
                last_frame,
                Duration::from_millis(u64::from(last_frame) * 16),
            )
            .unwrap();
            room.take_external_enemy_damage();
        }
        let mut defender = defender.clone();
        defender.pose.feet[2] = request.position[2] + distance;
        for _ in 0..300 {
            last_frame += 1;
            scene.step_encounter(last_frame, None);
            let (room, queries) = scene.bot_simulation();
            room.set_external_targets(vec![defender.clone()]);
            room.finish_tick(
                &queries,
                last_frame,
                Duration::from_millis(u64::from(last_frame) * 16),
            )
            .unwrap();
            let damage = room.take_external_enemy_damage();
            let split_in_flight = room.enemy_projectiles.len() > 1;
            let enemy = room
                .state_mut(entity)
                .unwrap()
                .unwrap()
                .enemy
                .as_ref()
                .unwrap();
            assert!(
                enemy.fault.is_none(),
                "frame {last_frame}: {:?}",
                enemy.fault
            );
            if let Some(hit) = damage
                .into_iter()
                .find(|hit| hit.source == entity && hit.target == defender.entity)
            {
                assert_eq!(split_in_flight, !direct);
                return hit.amount;
            }
        }
        panic!("Roadhog did not hit the defender at {distance} metres");
    };
    assert!((run_secondary(8.9, true) - 8.0).abs() < 1e-6);
    assert!((run_secondary(12.0, false) - 1.44).abs() < 1e-6);

    let (room, _) = scene.bot_simulation();
    let first_body = room.state_mut(entity).unwrap().unwrap();
    let enemy = first_body.enemy.as_mut().unwrap();
    enemy
        .scripts
        .teardown(&enemy.graphs, &enemy.binding, &mut first_body.vitals)
        .unwrap();
    assert_eq!(enemy.scripts.outgoing_damage(10.0, &[]), 10.0);

    let (original, queries) = scene.bot_simulation();
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.set_spawn_anchors(Arc::clone(original.spawn_anchors.as_ref().unwrap()));
    let repeated = room
        .create_enemy(EnemySpawn {
            graphs,
            codecs,
            definition: &definition,
            request: &request,
            mode: JUNKENSTEIN_REVENGE,
            mode_variables: Arc::new(Variables::from([
                (9948, Value::Integer(0)),
                (6751, Value::Asset(card.difficulty)),
                (8521, Value::Bool(true)),
                (8566, Value::Bool(true)),
            ])),
            tick: 7000,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    queue_wave(&mut room, repeated);
    room.set_external_targets(vec![defender.clone()]);
    let mut hit = false;
    for frame in 7001..=7200 {
        room.finish_tick(
            &queries,
            frame,
            Duration::from_millis(u64::from(frame) * 16),
        )
        .unwrap();
        assert!(room.take_boss_intro_starts().is_empty());
        let enemy = state(&room, repeated).unwrap().enemy.as_ref().unwrap();
        assert!(enemy.fault.is_none(), "frame {frame}: {:?}", enemy.fault);
        assert!(
            !enemy
                .scripts
                .body
                .snapshot()
                .unwrap()
                .instances
                .values()
                .any(|instance| instance.graph == 0x0ce0)
        );
        if room.take_external_enemy_damage().iter().any(|impact| {
            impact.source == repeated && impact.target == defender.entity && impact.amount > 0.0
        }) {
            hit = true;
            break;
        }
    }
    assert!(hit, "returning Roadhog did not shoot the defender");
}

#[test]
#[ignore = "requires the external 104319 Junkenstein Boss graphs and codecs"]
fn authored_reaper_publishes_an_observer_baseline_and_receives_wave_setup() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JUNKENSTEIN_REVENGE, JunkensteinCard, MapCatalog},
        game_heroes::HeroCatalog,
        npc_catalog::encounter_definition,
        script_codecs::CodecTable,
        script_enemy::EnemySpawn,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation},
        script_wire::{Value, Variables},
    };
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs =
        Arc::new(CodecTable::load(runtime.join("data/statescript_codecs_174.json")).unwrap());
    let heroes = HeroCatalog::load(runtime.join("data/game_heroes_174.json")).unwrap();
    let hero = 0x02e0_0000_0000_0002;
    let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
    let request = EntityCreation {
        position: crate::pve_encounter::navigation::REAPER_BALCONY,
        kind: CreationKind::Hero {
            hero,
            team: 1,
            yaw: 180.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: Some(graphs.enemy_pair(hero).unwrap().controller),
            skin: None,
        },
    };
    let map = MapCatalog::load(runtime.join("data/game_maps_174.json"))
        .unwrap()
        .game_map(
            JUNKENSTEIN_MAP,
            JUNKENSTEIN_REVENGE,
            "Junkenstein's Revenge",
            false,
            &[4, 0],
        )
        .unwrap();
    let mut scene = crate::scene_assets::SceneAssets::load(&runtime)
        .unwrap()
        .prepare_junkenstein(map, JunkensteinCard::from_index(0x3c).unwrap(), 0)
        .unwrap();
    scene.step_encounter(4200, None);
    let (room, _) = scene.bot_simulation();
    let entity = room
        .create_enemy(EnemySpawn {
            graphs,
            codecs,
            definition: &definition,
            request: &request,
            mode: JUNKENSTEIN_REVENGE,
            mode_variables: Arc::new(Variables::new()),
            tick: 4200,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    assert_eq!(entity, BOT_ENTITY);
    assert_eq!(room.take_created().len(), 1);
    assert_eq!(room.enemy_projections().unwrap().count(), 1);
    let state = room.state_mut(entity).unwrap().unwrap();
    let enemy = state.enemy.as_mut().unwrap();
    assert_eq!(enemy.binding.controller, entity + 1);
    assert!(
        enemy
            .scripts
            .controller
            .snapshot()
            .unwrap()
            .initialization_complete()
    );
    assert_eq!(
        enemy.scripts.controller_display_name(),
        Some(0x0de0_0000_0000_7106)
    );
    let defender = 0xa000_0101;
    enemy.scripts.set_defenders(Arc::from([defender]));
    for frame in 4201..=4262 {
        enemy
            .scripts
            .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
            .unwrap_or_else(|error| panic!("Reaper stopped at frame {frame}: {error}"));
    }
    let sightings = enemy.scripts.take_sightings();
    assert_eq!(sightings.len(), 1);
    let sighting = &sightings[0];
    assert_eq!(
        (sighting.source.entity, sighting.target),
        (entity, defender)
    );
    assert_eq!(sighting.parameters[&0x8d], Value::Integer(1));
    assert_eq!(sighting.parameters[&0x5c], Value::Entity(entity));
    assert!(enemy.scripts.take_sightings().is_empty());
    let controller = enemy.binding.controller;
    room.set_mode_variables(Arc::new(Variables::from([
        (9948, Value::Integer(0)),
        (
            6751,
            Value::Asset(JunkensteinCard::from_index(0x3c).unwrap().difficulty),
        ),
    ])));
    assert_eq!(room.encounter_possessor(entity), Some(controller));
    let caller = crate::script_init::Site {
        entity: crate::world::GAME_MODE_ENTITY,
        instance: 1,
        graph: 0x0cba,
        node: Some(9),
    };
    let request = || crate::script_init::PlayScript {
        policy: 0,
        overrides: Vec::new(),
        graph: 0x0c95,
        source: caller.entity,
        parameters: vec![(
            0x0a40_0000_0000_03a1,
            Value::Array(vec![Value::Vector([74.0, 12.6, -99.0])]),
        )],
        auto_retire: true,
    };
    assert!(!room.queue_encounter_wave(caller, entity, request()));
    assert!(room.queue_encounter_wave(caller, controller, request()));
    let state = room.state_mut(entity).unwrap().unwrap();
    let enemy = state.enemy.as_mut().unwrap();
    enemy
        .scripts
        .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, 4263)
        .unwrap();
    assert_eq!(state.vitals.maximum(), 1000.0);
    let direction = enemy
        .scripts
        .facing_direction()
        .expect("authored facing record");
    assert!(direction[0].abs() < 1e-6);
    assert_eq!(direction[2], -1.0);
    state.bot.yaw = 0.0;
    state.bot.apply_encounter_facing(direction, 4263);
    assert_eq!(state.bot.movement(4263).yaw, -32768);
    let body = enemy.scripts.body.snapshot().unwrap();
    let wave_body = body
        .instances
        .values()
        .find(|instance| instance.graph == 0x0cbc)
        .unwrap();
    assert!(wave_body.states[&52].active);
    assert_eq!(
        wave_body.states[&52].kind,
        crate::script_init::StateKind::SameTeamContact { registered: true }
    );
    assert_eq!(
        body.instances
            .values()
            .filter(|instance| instance.graph == 0x0ce0)
            .count(),
        1
    );
    assert!(
        body.instances
            .values()
            .any(|instance| instance.graph == 0x0cbc)
    );
    let controller = enemy.scripts.controller.snapshot().unwrap();
    let anchors = enemy.binding.spawn_anchors.as_ref().unwrap();
    let &[target] = anchors.targets(0x0d60_0000_0000_0321).unwrap() else {
        panic!("expected one authored controller target");
    };
    let wave = controller
        .instances
        .values()
        .find(|instance| instance.graph == 0x0c95)
        .unwrap();
    assert_eq!(
        wave.variables[&536],
        Value::Array(vec![Value::Entity(target)])
    );
    let approach = controller
        .instances
        .values()
        .find(|instance| instance.graph == 0x0ce2)
        .unwrap();
    assert_eq!(
        approach.variables[&431],
        Value::Vector([84.9754638671875, 12.68067741394043, -79.8869400024414])
    );
    assert_eq!(approach.variables[&501], Value::Float(10.78968620300293));
    for snapshot in [body, controller] {
        assert!(
            !snapshot.diagnostics.iter().any(|diagnostic| {
                matches!(
                    diagnostic.issue,
                    crate::script_init::Issue::UnsupportedField("PlayScript target component")
                )
            }),
            "{:?}",
            snapshot.diagnostics
        );
    }
    let mode = scene.encounter_snapshot().unwrap();
    assert_ne!(mode.variables.get(&8520), Some(&Value::Bool(true)));
    let diagnostics = mode.diagnostics.len();
    scene.step_encounter(4264, None);
    let mode = scene.encounter_snapshot().unwrap();
    assert_eq!(
        mode.diagnostics.len(),
        diagnostics,
        "{:?}",
        mode.diagnostics
    );
    assert_eq!(mode.variables[&8520], Value::Bool(true));
    assert!(scene.bot_simulation().0.take_boss_intro_starts().is_empty());
    let (room, _) = scene.bot_simulation();
    let state = room.state_mut(entity).unwrap().unwrap();
    let enemy = state.enemy.as_mut().unwrap();
    for frame in 4264..=4356 {
        enemy
            .scripts
            .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
            .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
    }
    let body = enemy.scripts.body.snapshot().unwrap();
    let intro = body
        .instances
        .values()
        .find(|instance| instance.graph == 0x0ce0)
        .unwrap();
    // The half-second wait must follow 0CE0:76 to the one-second wait.
    assert!(!intro.states[&23].active);
    assert!(intro.states[&15].active);
    assert_eq!(intro.states[&15].activation, 1);
    let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
    let observer = encoder
        .project(body, crate::script_snapshot_wire::View::Observer)
        .unwrap();
    encoder.full(&observer).unwrap();
    assert!(enemy.scripts.take_voice_requests().is_empty());
    for frame in 4357..=4570 {
        enemy
            .scripts
            .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
            .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
    }
    let body = enemy.scripts.body.snapshot().unwrap();
    let intro = body
        .instances
        .values()
        .find(|instance| instance.graph == 0x0ce0)
        .unwrap();
    assert_eq!(intro.states[&39].activation, 1);
    assert!(!intro.states[&39].active);
    assert_eq!(
        room.take_encounter_voice_requests(),
        [crate::pve_encounter::voice::VoiceRequest {
            anchor: entity,
            speaker: crate::world::GAME_MODE_ENTITY,
            stimulus: 0x0ee0_0000_0000_06b5,
            line: 0x0760_0000_0000_8cb1,
        }]
    );
    assert!(room.take_encounter_voice_requests().is_empty());
    let state = room.state_mut(entity).unwrap().unwrap();
    let enemy = state.enemy.as_mut().unwrap();
    for frame in 4571..=4763 {
        enemy
            .scripts
            .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
            .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
    }
    let snapshot = enemy.scripts.body.snapshot().unwrap();
    let (&intro_id, intro) = snapshot
        .instances
        .iter()
        .find(|(_, instance)| instance.graph == 0x0ce0)
        .unwrap();
    for (raw, message) in [
        (8, crate::pve_encounter::messages::BOSS_PAUSE),
        (14, crate::pve_encounter::messages::BOSS_CAMERA),
    ] {
        let state = &intro.states[&raw];
        assert!(state.active);
        assert_eq!(state.activation, 1);
        assert_eq!(
            state.kind,
            crate::script_init::StateKind::PersistentSend(message)
        );
    }
    assert_eq!(intro.variables[&8695], Value::Asset(0x0880_0000_0000_0010));
    assert_eq!(
        intro.states[&2].kind,
        crate::script_init::StateKind::CinematicTimeScale {
            boundary_frame: 4764
        }
    );
    let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
    encoder
        .full(
            &encoder
                .project(snapshot, crate::script_snapshot_wire::View::Observer)
                .unwrap(),
        )
        .unwrap();
    for frame in 4764..=4820 {
        enemy
            .scripts
            .advance(&enemy.graphs, &enemy.binding, &mut state.vitals, frame)
            .unwrap_or_else(|error| panic!("Reaper at {frame}: {error}"));
    }
    let snapshot = enemy.scripts.body.snapshot().unwrap();
    let intro = &snapshot.instances[&intro_id];
    assert_eq!(intro.states[&14].activation, 1);
    assert!(!intro.states[&14].active);
    assert!(!intro.states[&2].active);
    assert!(matches!(
        intro.states[&2].kind,
        crate::script_init::StateKind::CinematicTimeScale { boundary_frame }
            if boundary_frame > 4764 && boundary_frame <= 4821
    ));
    assert_eq!(enemy.scripts.cinematic_aim(), None);
    assert_eq!(enemy.scripts.facing_direction(), None);
    assert_eq!(
        room.take_encounter_voice_requests(),
        [crate::pve_encounter::voice::VoiceRequest {
            anchor: entity,
            speaker: entity,
            stimulus: 0x0ee0_0000_0000_06bd,
            line: 0x0760_0000_0000_8a2b,
        }]
    );
    assert_eq!(
        room.take_defender_messages(),
        [
            crate::pve_encounter::messages::RESET_HIGHLIGHTS,
            crate::pve_encounter::messages::BOSS_INTRO_ENDED,
            crate::pve_encounter::messages::RESET_HIGHLIGHTS,
        ]
        .map(|message| DefenderMessage {
            target: None,
            source: entity,
            message,
            parameters: crate::script_wire::Variables::new(),
        })
    );

    let (room, queries) = scene.bot_simulation();
    let step = |room: &mut RoomSimulation, tick| {
        room.finish_tick(&queries, tick, Duration::from_millis(u64::from(tick) * 16))
            .unwrap();
    };
    room.set_external_targets(Vec::new());
    step(room, 4821);
    room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: defender,
        team: 0,
        pose: TargetPose {
            feet: [87.0, 12.6, -64.0],
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    }]);
    room.set_mode_boss_pause(true);
    step(room, 4822);
    let state = room.state_mut(entity).unwrap().unwrap();
    assert_eq!(
        state.bot.position,
        crate::pve_encounter::navigation::REAPER_BALCONY
    );
    assert!(matches!(
        state.enemy.as_ref().unwrap().reaper_exit,
        Some(ReaperExit::Waiting)
    ));
    room.set_mode_boss_pause(false);
    let mut airborne = false;
    let mut landing = None;
    for tick in 4823..=5023 {
        step(room, tick);
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_ref().unwrap();
        assert!(enemy.fault.is_none(), "{tick}: {:?}", enemy.fault);
        airborne |= state.bot.movement(tick).flags & crate::mover_posture::IN_AIR != 0;
        if enemy.reaper_exit.is_none() {
            landing = Some((tick, state.bot.position));
            break;
        }
    }
    let (landed, position) = landing.expect("Reaper lands on the street");
    assert!(airborne);
    assert!(
        position[1] < crate::pve_encounter::navigation::REAPER_BALCONY[1] - 3.0,
        "{position:?}"
    );
    let defender_position = [87.0, 12.6, -64.0];
    let mut in_range = None;
    for tick in landed + 1..=landed + 900 {
        step(room, tick);
        let state = room.state_mut(entity).unwrap().unwrap();
        let range = norm(std::array::from_fn(|axis| {
            state.bot.position[axis] - defender_position[axis]
        }));
        if range <= 20.0 && state.bot.velocity == [0.0; 3] {
            in_range = Some((tick, state.bot.position));
            break;
        }
    }
    let (arrived, in_range) = in_range.expect("Reaper pursues to a visible firing position");
    assert_ne!(in_range, position);
    let fire_state = |room: &mut RoomSimulation| {
        let state = room.state_mut(entity).unwrap().unwrap();
        let enemy = state.enemy.as_ref().unwrap();
        assert!(enemy.fault.is_none(), "{:?}", enemy.fault);
        let snapshot = enemy.scripts.body.snapshot().unwrap();
        let weapon = snapshot
            .instances
            .values()
            .find(|i| i.graph == 0x0019)
            .unwrap();
        (
            weapon.states.get(&6).map_or(0, |volley| volley.activation),
            enemy.scripts.body.command_buttons(),
        )
    };
    assert_eq!(fire_state(room), (1, 1));
    let impacts = room.take_external_enemy_damage();
    assert!(
        !impacts.is_empty(),
        "the authored volley must hit the visible defender"
    );
    assert!(impacts.iter().all(|hit| hit.source == entity
        && hit.target == defender
        && (0.524..=1.75).contains(&hit.amount)));
    {
        let enemy = room
            .state_mut(entity)
            .unwrap()
            .unwrap()
            .enemy
            .as_ref()
            .unwrap();
        let body = enemy.scripts.body.snapshot().unwrap();
        let (&weapon_id, weapon) = body
            .instances
            .iter()
            .find(|(_, i)| i.graph == 0x0019)
            .unwrap();
        assert_eq!(weapon.variables[&198], Value::Integer(8));
        assert_eq!(weapon.variables[&53], Value::Integer(7));
        let encoder =
            crate::script_snapshot_wire::SnapshotEncoder::new(&enemy.graphs, &enemy.codecs);
        let observer = encoder
            .project(body, crate::script_snapshot_wire::View::Observer)
            .unwrap();
        assert_eq!(
            observer
                .volley_identity(weapon_id, 6)
                .map(|(counter, _)| counter),
            Some(1)
        );
        encoder.full(&observer).unwrap();
    }
    let pose = room
        .state_mut(entity)
        .unwrap()
        .unwrap()
        .bot
        .movement(arrived);
    let direction = crate::game_hitscan::aim_direction(&Command {
        yaw: pose.yaw,
        pitch: pose.pitch,
        ..Command::default()
    });
    let delta: [f64; 3] = std::array::from_fn(|axis| defender_position[axis] - in_range[axis]);
    let range = norm(delta);
    assert!(
        delta
            .into_iter()
            .zip(direction)
            .map(|(offset, axis)| offset * axis)
            .sum::<f64>()
            > range * 0.99
    );
    let mut eye = in_range;
    eye[1] += 1.55;
    assert_eq!(queries.hitscan_wall(eye, direction, range).unwrap(), None);
    for tick in arrived + 1..=arrived + 5 {
        step(room, tick);
        let state = room.state_mut(entity).unwrap().unwrap();
        assert_eq!(state.bot.position, in_range);
    }
    let relocated = [89.0, 12.6, -64.0];
    room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: defender,
        team: 0,
        pose: TargetPose {
            feet: relocated,
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    }]);
    room.set_mode_boss_pause(true);
    step(room, arrived + 6);
    assert_eq!(
        room.state_mut(entity).unwrap().unwrap().bot.position,
        in_range
    );
    assert_eq!(fire_state(room), (1, 0));
    room.set_mode_boss_pause(false);
    let resumed = (arrived + 7..=arrived + 300)
        .find(|&tick| {
            step(room, tick);
            let bot = &room.state_mut(entity).unwrap().unwrap().bot;
            bot.position != in_range || bot.movement(tick).yaw != pose.yaw
        })
        .expect("Reaper updates its pursuit after the defender moves");
    room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: defender,
        team: 0,
        pose: TargetPose {
            feet: relocated,
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: false,
    }]);
    step(room, resumed + 1);
    assert_eq!(
        room.state_mut(entity).unwrap().unwrap().bot.velocity,
        [0.0; 3]
    );
    assert_eq!(fire_state(room).1, 0);

    room.set_external_targets(vec![crate::script_enemy::EnemyTarget {
        hero: None,
        filter_flags: None,
        tags: Default::default(),
        health: None,
        velocity: None,
        script_state: Default::default(),
        entity: defender,
        team: 0,
        pose: TargetPose {
            feet: defender_position,
            height: 1.8,
            radius: 0.35,
            eye: 1.55,
        },
        alive: true,
    }]);
    let mut fired_after_intro_retired = false;
    let mut last_volley = fire_state(room).0;
    let mut empty_magazine = false;
    let mut reloaded_after_empty = false;
    for tick in resumed + 2..=resumed + 750 {
        step(room, tick);
        let (volley, _) = fire_state(room);
        let enemy = room
            .state_mut(entity)
            .unwrap()
            .unwrap()
            .enemy
            .as_ref()
            .unwrap();
        if !enemy.scripts.boss_intro_finished() && volley > last_volley {
            fired_after_intro_retired = true;
        }
        let weapon = enemy
            .scripts
            .body
            .snapshot()
            .unwrap()
            .instances
            .values()
            .find(|instance| instance.graph == 0x0019)
            .unwrap();
        empty_magazine |= weapon.variables[&53] == Value::Integer(0);
        last_volley = volley;
        if volley > 8 {
            reloaded_after_empty = empty_magazine && weapon.variables[&53] == Value::Integer(7);
            break;
        }
    }
    assert!(
        fired_after_intro_retired,
        "Reaper keeps firing after the intro graph is retired"
    );
    assert!(
        reloaded_after_empty,
        "Reaper fires again after exhausting the initial eight rounds"
    );
}
