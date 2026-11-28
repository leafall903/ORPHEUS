use super::*;

#[test]
#[ignore = "requires build 104319 Practice Range and model attachment positions"]
fn prepared_grapple_latches_without_launching_an_idle_owner_and_releases() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene
        .heroes()
        .get(crate::game_heroes::WRECKING_BALL)
        .unwrap()
        .clone();
    let model = assets.graphs().bodies()[&hero.body]
        .model_geometry(0)
        .unwrap();
    let [bx, by, bz] = model
        .bone_reference_point(0x0880_0000_0000_13db, [0.0, 0.0, 0.7])
        .expect("the selected 104319 model must include the authored retraction bone");
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let (mut world, id, mut tick) =
        select_practice_hero(&mut scene, &viewer, hero.guid, &mut GameEgress::default());
    let roll = crate::script_runtime::held_button_mask(35).unwrap();
    let mut origin = None;
    let mut anchor = None;
    let mut final_tip = None;
    let mut final_feet = [0.0; 3];
    let mut latched = false;
    let mut released = false;
    let mut max_drift = 0.0_f64;
    // Leave time for the stock spawn cooldown. Hold the real secondary input,
    // turn while attached, then release it without providing any movement input.
    const PRESS: u32 = 490;
    const RELEASE: u32 = PRESS + 86;
    for step in 0..RELEASE + 44 {
        tick += 1;
        scene.simulation_mut().prepare_movement_frame(tick).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        let command = Command {
            frame: tick,
            yaw: if step >= PRESS + 40 { 0x4000 } else { 0 },
            pitch: 0x0800,
            buttons: match step {
                70 => roll,
                step if (PRESS..RELEASE).contains(&step) => SECONDARY,
                _ => 0,
            },
            ..Default::default()
        };
        world.queue_commands(tick, &[command]);
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let body = scene.simulation().player_body(id).unwrap();
        let pose = body.mover.pose();
        let [x, _, z] = pose.position;
        if step + 1 == PRESS {
            origin = Some([x, z]);
        }
        if let Some([ox, oz]) = origin {
            max_drift = max_drift.max((x - ox).hypot(z - oz));
        }
        let runtime = body.runtime.as_ref().expect("owner VM remains attached");
        let snapshot = runtime.snapshot().unwrap();
        let (&instance, hook) = snapshot
            .instances
            .iter()
            .find(|(_, inst)| inst.graph == 0x14e4)
            .unwrap();
        let active = |raw| hook.states.get(&raw).is_some_and(|s| s.active);
        let value = |n| {
            snapshot
                .resolved_bindings
                .get(&(instance, n))
                .or_else(|| hook.variables.get(&n))
        };
        if (PRESS..RELEASE).contains(&step)
            && active(21)
            && value(435) == Some(&ScriptValue::Bool(true))
        {
            let Some(ScriptValue::Vector(point)) = value(432) else {
                panic!("missing latched cable tip")
            };
            assert_eq!(
                anchor.get_or_insert(*point),
                point,
                "turning changed the fixed map anchor"
            );
            latched = true;
        }
        if step >= RELEASE {
            if let Some(ScriptValue::Vector(tip)) = value(432) {
                final_tip = Some(*tip);
                final_feet = pose.position;
            }
        }
        if step >= RELEASE && latched && !active(0) && !active(21) {
            released = true;
        }
    }
    assert!(
        latched,
        "hook did not enter the tether state after a map hit"
    );
    assert!(
        released,
        "tether remained active after secondary was released"
    );
    let tip = final_tip.expect("missing release presentation");
    // The owner turned a quarter turn while the world anchor stayed fixed.
    let expected = [final_feet[0] + bz, final_feet[1] + by, final_feet[2] - bx];
    for (axis, wanted) in tip.into_iter().zip(expected) {
        assert!(
            (axis - wanted).abs() < 0.001,
            "released tip {tip:?}, expected {expected:?}"
        );
    }
    assert!(
        max_drift < 0.02,
        "idle owner drifted horizontally {max_drift} m"
    );
}

#[test]
#[ignore = "requires build 104319 Practice Range, collision and stock model data"]
fn prepared_taut_cable_limits_owner_input_and_releases_to_the_mover() {
    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let mut egress = GameEgress::default();
    let (mut world, id, mut tick) = select_practice_hero(
        &mut scene,
        &viewer,
        crate::game_heroes::WRECKING_BALL,
        &mut egress,
    );
    let roll = crate::script_runtime::held_button_mask(35).unwrap();
    let mut anchor = None;
    let mut first = None;
    let mut last = None;
    let mut max_stretch = 0.0_f64;
    let mut along_wall = 0.0_f64;
    let mut outside_after_release = 0.0_f64;
    let mut corrected = false;
    let mut scope = None;
    let mut near_limit = false;
    const PRESS: u32 = 490;
    const MOVE: u32 = PRESS + 50;
    const RELEASE: u32 = MOVE + 200;
    for step in 0..RELEASE + 45 {
        tick += 1;
        scene.simulation_mut().prepare_movement_frame(tick).unwrap();
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                yaw: 0,
                pitch: 0x0800,
                right: if step >= MOVE { 90 } else { 0 },
                forward: if step >= MOVE { -90 } else { 0 },
                buttons: if step == 70 {
                    roll
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
        let snap = body.runtime.as_ref().unwrap().snapshot().unwrap();
        let (&instance, hook) = snap
            .instances
            .iter()
            .find(|(_, inst)| inst.graph == 0x14e4)
            .unwrap();
        let active = hook.states.get(&21).is_some_and(|state| state.active);
        let tip = snap
            .resolved_bindings
            .get(&(instance, 432))
            .or_else(|| hook.variables.get(&432));
        if active {
            let Some(ScriptValue::Vector(point)) = tip else {
                panic!("missing anchor")
            };
            assert_eq!(anchor.get_or_insert(*point), point);
        }
        if step < MOVE {
            continue;
        }
        let cable = if active {
            let cable = hook.states[&21].tether().expect("active graph cable");
            scope = Some(cable);
            cable
        } else {
            scope.expect("earlier cable before release")
        };
        let x = pose.position[0];
        let length = cable.attachment_distance(pose.position);
        let (start_x, _) = *first.get_or_insert((x, cable.length()));
        if step < RELEASE {
            assert!(active, "cable detached before release at {step}");
            max_stretch = max_stretch.max(length - cable.length());
            near_limit |= (length - cable.length()).abs() < 0.02;
            along_wall = along_wall.max((x - start_x).abs());
            last = Some((cable.length(), pose.velocity));
            if near_limit && body.mover.needs_owner_correction_at(tick) && !corrected {
                let candidate = world
                    .body_script
                    .as_ref()
                    .and_then(|script| script.corrections.as_ref())
                    .and_then(|history| history.candidate(tick, true));
                if let Some(candidate) = candidate {
                    let wire_pose = candidate.update().movement.unwrap();
                    assert_eq!(wire_pose.position, pose.position);
                    let (_, sent) = transmit(
                        &mut world,
                        Some(scene.simulation_mut()),
                        &mut egress,
                        tick,
                        tick - FIRST_TICK,
                    );
                    corrected = sent.records.contains(&RecordSource::Movement(0));
                    transport_outcome(&mut world, &mut egress, sent.sequence, true);
                }
            }
        } else if !active {
            outside_after_release = outside_after_release.max(length - last.unwrap().0);
        }
    }
    eprintln!(
        "grapple input: anchor={anchor:?} first={first:?} last={last:?} lateral={along_wall} stretch={max_stretch} release={outside_after_release} corrected={corrected}"
    );
    assert!(
        along_wall > 0.5,
        "owner should travel tangentially, got {along_wall} m"
    );
    assert!(near_limit, "input did not reach the active cable limit");
    assert!(
        max_stretch < 0.02,
        "held cable stretched {max_stretch} m under directional input"
    );
    assert!(
        outside_after_release > 0.2,
        "moving owner did not travel beyond the released line"
    );
    assert!(
        corrected,
        "no owner correction sent for the constrained pose"
    );
}
