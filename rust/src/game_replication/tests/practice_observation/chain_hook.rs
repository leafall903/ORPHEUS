use super::*;
use crate::{game_bots::BOT_ENTITY, game_clock::TICK_PERIOD, patrol_script::PatrolScript};

#[test]
#[ignore = "requires build 104319 Roadhog and Practice Range runtime"]
fn prepared_chain_hook_floor_hit_reports_world_point_and_surface_normal() {
    use crate::script_expr::Value;
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut world, player, mut tick) = select_practice_hero(
        &mut scene,
        &viewer,
        crate::pve_encounter::navigation::ROADHOG,
        &mut GameEgress::default(),
    );
    let patrol = PatrolScript::load().unwrap();
    let hook = crate::script_runtime::held_button_mask(35).unwrap();
    const PRESS: u32 = 560;
    let mut observed = None;

    for step in 0..PRESS + 72 {
        if step == PRESS - 10 {
            let [bx, by, bz] = scene
                .simulation_mut()
                .body_mut(BOT_ENTITY + 7)
                .unwrap()
                .position;
            let position = (0..32)
                .find_map(|heading| {
                    let [dx, _, dz] = crate::game_hitscan::direction_for_angles(heading * 2048, 0);
                    let (x, z) = (bx - 5.0 * dx, bz - 5.0 * dz);
                    let floor = scene
                        .movement_index()
                        .ground(x, z, by + 0.3, by - 0.3, 0x08)?;
                    Some([x, floor.y, z])
                })
                .unwrap();
            let hero = scene
                .heroes()
                .get(crate::pve_encounter::navigation::ROADHOG)
                .unwrap();
            let mover = scene
                .player_spawn_mover_at(hero.body, 0, position, 0)
                .unwrap();
            scene
                .simulation_mut()
                .player_body_mut(player)
                .unwrap()
                .mover = mover;
        }
        tick += 1;
        scene.simulation_mut().prepare_movement_frame(tick).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                pitch: 12000,
                buttons: if (PRESS..PRESS + 4).contains(&step) {
                    hook
                } else {
                    0
                },
                ..Default::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        if step >= PRESS - 1 {
            let actor = scene.simulation().player_body(player).unwrap();
            let feet = actor.mover.pose().position;
            let snapshot = actor.runtime.as_ref().unwrap().snapshot().unwrap();
            let (&id, instance) = snapshot
                .instances
                .iter()
                .find(|(_, instance)| instance.graph == 0x1c8)
                .unwrap();
            let binding = |var| {
                snapshot
                    .resolved_bindings
                    .get(&(id, var))
                    .or_else(|| instance.variables.get(&var))
            };
            if step == PRESS - 1 {
                assert_ne!(
                    binding(2413),
                    Some(&Value::Bool(true)),
                    "unexpected hit before the ability was pressed"
                );
            }
            // The graph copies the transient ray result into its persistent hook-tip state on contact.
            if let (
                Some(Value::Bool(true)),
                Some(Value::Vector(point)),
                Some(Value::Vector(normal)),
            ) = (binding(2413), binding(431), binding(434))
            {
                observed = Some((*point, *normal, feet));
                break;
            }
        }
        scene.step_bots(tick, &patrol).unwrap();
        let (room, queries) = scene.bot_simulation();
        room.finish_tick(&queries, tick, TICK_PERIOD * tick)
            .unwrap();
        room.take_player_cues();
    }
    let (point, normal, feet) = observed.expect("the active hook must contact the stock map floor");
    assert!(
        normal[1] > 0.9 && (normal[0].hypot(normal[1]).hypot(normal[2]) - 1.0).abs() < 0.01,
        "expected floor normal, got {normal:?} at {point:?}"
    );
    let ground = scene
        .movement_index()
        .ground(point[0], point[2], point[1] + 0.35, point[1] - 0.35, 0x08)
        .expect("world contact must agree with the 104319 scene geometry");
    assert!(
        (feet[0] - point[0]).hypot(feet[2] - point[2]) < 3.0 && (point[1] - ground.y).abs() < 0.3,
        "point={point:?}, feet={feet:?}, floor={}",
        ground.y
    );
}
