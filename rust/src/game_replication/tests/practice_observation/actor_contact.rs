use super::*;
use crate::game_bots::BOT_ENTITY;
use crate::mover_settings::MoverCatalog;

#[test]
#[ignore = "requires all 32 stock 104319 hero movers and the Practice Range collision cache"]
fn prepared_all_stock_movers_stop_at_their_authored_enemy_radius() {
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let movers = MoverCatalog::load(root.join("data/game_movers_174.json")).unwrap();
    let scene = SceneAssets::load(&root)
        .unwrap()
        .prepare(GameMap::practice_range())
        .unwrap();
    let bot = scene
        .simulation()
        .bodies()
        .unwrap()
        .find(|b| b.entity == BOT_ENTITY + 7)
        .unwrap();
    let other = bot.contact_capsule(movers.authored_for_body(bot.spawn.body).unwrap());
    let [x, y, z] = bot.position;
    let mut count = 0;
    for hero in scene.heroes().heroes() {
        let settings = movers
            .authored_for_body(hero.body)
            .expect("missing stock mover");
        let mut mover = scene
            .player_spawn_mover_at(hero.body, 0, [x, y, z + 3.0], 0x8000)
            .unwrap();
        let mut expected = settings.stand_radius + f64::from(other.radius);
        let mut held = 0;
        for frame in 1..=110 {
            mover
                .step_against(
                    scene.movement_index(),
                    Command {
                        frame,
                        yaw: 0x8000,
                        forward: 127,
                        ..Default::default()
                    },
                    f64::from(RETAIL_QUANTA) / 1_000_000.0,
                    &[other],
                    |_, _| {},
                )
                .unwrap();
            let [px, _, pz] = mover.pose().position;
            expected = crate::contact_body::planar_contact_radius(mover.contact_capsule(), other);
            let gap = (px - x).hypot(pz - z) - expected;
            assert!(gap >= -0.002, "{} overlapped by {} m", hero.name, -gap);
            if frame >= 60 && gap.abs() < 0.08 {
                held += 1;
            }
        }
        assert!(
            held >= 46,
            "{} held {held} frames at a {} m stop distance",
            hero.name,
            expected
        );
        count += 1;
    }
    assert_eq!(count, 32);
}

#[test]
#[ignore = "requires the 104319 Practice Range runtime and the original hero movers"]
fn prepared_owner_holds_against_enemy_and_can_cross_an_ally() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    for (guid, toggle_roll) in [
        (crate::game_heroes::WRECKING_BALL, false),
        (crate::game_heroes::WRECKING_BALL, true),
        (crate::game_heroes::TRACER, false),
    ] {
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        let hero = scene.heroes().get(guid).unwrap().clone();
        let name = &hero.name;
        let card = PlayerCard::default();
        let viewer = PlayerAppearance {
            entity: OWNER,
            slot: 0,
            team: 0,
            card: &card,
        };
        let mut egress = GameEgress::default();
        let (mut world, id, mut tick) =
            select_practice_hero(&mut scene, &viewer, hero.guid, &mut egress);
        assert!(deliver_until(
            &mut world,
            &mut scene,
            &mut egress,
            &mut tick,
            |world| {
                [7, 14]
                    .iter()
                    .all(|n| world.actor_contact_ready(BOT_ENTITY + n))
            }
        ));
        let roll = crate::script_runtime::held_button_mask(35).unwrap();
        for (target, enemy) in [(BOT_ENTITY + 7, true), (BOT_ENTITY + 14, false)] {
            let bot = scene.simulation_mut().body_mut(target).unwrap();
            assert_eq!(bot.spawn.team != viewer.team, enemy);
            let [x, y, z] = bot.position;
            let yaw = 0x8000;
            let mover = scene
                .player_spawn_mover_at(hero.body, viewer.team, [x, y, z + 3.0], yaw)
                .unwrap();
            scene.simulation_mut().player_body_mut(id).unwrap().mover = mover;
            let mut deepest = 0.0_f64;
            let mut near = 0;
            let mut crossed = false;
            let mut nudged = false;
            let mut corrected = false;
            for step in 0..165 {
                tick += 1;
                scene.simulation_mut().prepare_movement_frame(tick).unwrap();
                scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
                let contacts = scene.simulation().movement_contact_frame().unwrap();
                let before = scene
                    .simulation()
                    .player_body(id)
                    .unwrap()
                    .mover
                    .contact_capsule();
                nudged |= contacts.ally_separation_where(
                    id.entity,
                    viewer.team,
                    before,
                    crate::game_clock::TICK_PERIOD.as_secs_f64(),
                    |e| e == target,
                ) != [0.0; 2];
                let blockers = contacts.blockers_where(id.entity, viewer.team, |e| e == target);
                assert_eq!(blockers.len(), usize::from(enemy));
                let buttons = if enemy && toggle_roll && step == 20 {
                    roll
                } else {
                    0
                };
                world.queue_commands(
                    tick,
                    &[Command {
                        frame: tick,
                        yaw,
                        forward: if step > 50 { 127 } else { 0 },
                        buttons,
                        ..Default::default()
                    }],
                );
                world
                    .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
                    .unwrap();
                let state = scene.simulation().player_body(id).unwrap();
                let pose = state.mover.pose();
                let active_override = state.runtime.as_ref().and_then(|r| r.mover_override());
                if step >= 110 && toggle_roll {
                    let active = active_override.expect("the rolled form must still be active");
                    assert_eq!(
                        state.mover.contact_capsule().radius,
                        active.stand_radius as f32
                    );
                }
                crossed |= pose.position[2] < z - 1.0;
                let Some(other) = blockers.first() else {
                    continue;
                };
                let capsule = state.mover.contact_capsule();
                let distance = (pose.position[0] - x).hypot(pose.position[2] - z);
                let expected = crate::contact_body::planar_contact_radius(capsule, *other);
                let gap = distance - expected;
                deepest = deepest.min(gap);
                if step < 110 || gap.abs() >= 0.08 {
                    continue;
                }
                near += 1;
                if !state.mover.needs_owner_correction_at(tick) {
                    continue;
                }
                let Some(correct) = world
                    .body_script
                    .as_ref()
                    .and_then(|s| s.corrections.as_ref())
                    .and_then(|h| h.candidate(tick, true))
                else {
                    continue;
                };
                assert_eq!(correct.update().movement.unwrap().position, pose.position);
                let (bytes, sent) = transmit(
                    &mut world,
                    Some(scene.simulation_mut()),
                    &mut egress,
                    tick,
                    tick - FIRST_TICK,
                );
                if sent.records.contains(&RecordSource::Movement(0)) {
                    assert!(!bytes.is_empty());
                    let history = world
                        .body_script
                        .as_ref()
                        .unwrap()
                        .corrections
                        .as_ref()
                        .unwrap();
                    assert_eq!(history.corrected(), Some(tick));
                    corrected = true;
                }
                transport_outcome(&mut world, &mut egress, sent.sequence, true);
            }
            if enemy {
                assert!(
                    deepest >= -0.002 && near > 45 && !crossed && corrected,
                    "{name} roll={toggle_roll} gap={deepest} near={near} corrected={corrected}"
                );
            } else {
                assert!(
                    nudged && crossed,
                    "{name} roll={toggle_roll} nudged={nudged} crossed={crossed}"
                );
            }
        }
    }
}
