use super::*;

mod junkenstein;

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_cassidy_primary_and_fan_damage_all_boss_bodies() {
    use crate::junkenstein_navigation::{JUNKRAT, REAPER, ROADHOG, SUMMONER, WITCH};
    junkenstein::verify_normal_cassidy_damage_and_charge(
        &[REAPER, ROADHOG, JUNKRAT, WITCH, SUMMONER],
        &[(false, false), (false, true), (true, true)],
    );
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_cassidy_common_npc_damage_charge_and_retirement() {
    use crate::junkenstein_navigation::{SHOCK_TIRE, ZOMBARDIER};
    const ZOMNIC: u64 = 0x02e0_0000_0000_0175;
    junkenstein::verify_normal_cassidy_damage_and_charge(
        &[ZOMNIC, ZOMBARDIER, SHOCK_TIRE],
        &[(false, false)],
    );
}

use crate::game_bots::BOT_ENTITY;

fn unique_script_instance(
    snapshot: &crate::script_init::Snapshot,
    graph: u32,
    parent: Option<(u16, u16)>,
) -> u16 {
    let matches: Vec<_> = snapshot
        .instances
        .iter()
        .filter_map(|(&id, instance)| {
            (instance.graph == graph && instance.parent == parent).then_some(id)
        })
        .collect();
    assert_eq!(
        matches.len(),
        1,
        "graph {graph:04x}, parent {parent:?}: {matches:?}"
    );
    matches[0]
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack"]
fn repeated_rooms_share_collision_indexes_and_keep_independent_bodies() {
    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let first = assets.prepare(GameMap::practice_range()).unwrap();
    let mut second = assets.prepare(GameMap::practice_range()).unwrap();
    assert!(Arc::ptr_eq(&first.ground, &second.ground));
    assert!(Arc::ptr_eq(
        first.contacts.as_ref().unwrap(),
        second.contacts.as_ref().unwrap()
    ));
    let before = first.bots().unwrap().next().unwrap().position;
    let after = [before[0] + 1.0, before[1], before[2]];
    second
        .bot_simulation()
        .0
        .body_mut(BOT_ENTITY)
        .unwrap()
        .position = after;
    assert_eq!(first.bots().unwrap().next().unwrap().position, before);
    assert_eq!(second.bots().unwrap().next().unwrap().position, after);
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack"]
fn prepared_practice_debug_view_contains_real_collision_layers() {
    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let view = serde_json::to_value(scene.debug_geometry().view()).unwrap();

    assert_eq!(view["map"], "0800000000000688");
    assert_eq!(view["sources"]["movement_cache"], "0688.col");
    assert_eq!(
        view["origin"],
        serde_json::to_value(scene.map().spawn_point(0, 0).unwrap().0).unwrap()
    );
    assert!(view["counts"]["triangles"].as_u64().unwrap() > 100);
    assert!(view["counts"]["proxies"].as_u64().unwrap() > 10);
    assert_eq!(
        view["totals"]["triangles"],
        scene.movement_index().triangle_count()
    );
    assert_eq!(
        view["totals"]["proxies"],
        scene.contacts.as_ref().unwrap().shapes().len()
    );
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack without bot VM graphs"]
fn stock_soldier_owner_session_idles_fires_reloads_and_sprints() {
    use crate::commands::Command;
    use crate::game_replication::BodyStep;
    use crate::script_init::{Snapshot, StateKind};
    use crate::script_snapshot_wire::identity_test_reader::IdentityReader;
    use crate::script_snapshot_wire::{View, uncertain_polled_switch};
    use crate::script_stream::{BodyStream, DeltaKind};
    use crate::script_wire::Value;
    use std::collections::BTreeSet;

    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    assert!(scene.movement_ready());
    let hero = scene.heroes().get(SOLDIER).unwrap();
    let spawn = OwnerBodySpawn {
        slot: 0,
        controller: 0xa000_0100,
        entity: 0xa000_0101,
        body_filter: team_bit(0).unwrap(),
        frame: FIRST_TICK,
        pose: scene.player_spawn_mover(hero.body, 0).unwrap().pose(),
        quanta: RETAIL_QUANTA,
    };
    if assets.graphs.bodies()[&hero.body].target_tags.is_some() {
        let mut runtime = scene.owner_body_runtime(hero, spawn).unwrap().unwrap();
        let snapshot = runtime.snapshot().unwrap();
        let encoder = runtime.encoder();
        let root = |graph| unique_script_instance(snapshot, graph, None);
        let manager = root(0x004b);
        let child = |graph, raw| unique_script_instance(snapshot, graph, Some((manager, raw)));
        let table_id = child(0x05ad, 7);
        let idle_id = child(0x02c5, 5);
        let rifle_id = root(0x0254);
        let sprint_id = root(0x0255);
        // The authored message table stays in the VM for lookups.
        let table = &snapshot.instances[&table_id];
        assert_eq!(table.graph, 0x05ad);
        assert!(matches!(&table.variables[&29859], Value::Map(entries) if entries.len() == 20));
        let mut streams = Vec::new();
        for view in [View::Owner, View::Observer] {
            let projected = encoder.project(snapshot, view).unwrap();
            let mut stream = BodyStream::new(spawn.entity);
            let baseline = encoder.queue_full(&mut stream, &projected).unwrap();
            stream.create_arrived();
            let mut wire = IdentityReader::default();
            wire.apply(
                baseline.update.chunk.as_ref().unwrap(),
                true,
                view == View::Owner,
            )
            .unwrap();
            wire.verify(snapshot, &assets.graphs);
            stream.arrived(baseline.last);
            // Only the unbound table is omitted, as Python omits its None.
            let sent = projected.instance_variables(table_id).unwrap();
            assert!(!sent.contains_key(&29859));
            for (&variable, value) in &table.variables {
                let value = snapshot
                    .resolved_bindings
                    .get(&(table_id, variable))
                    .unwrap_or(value);
                if variable != 29859 && !matches!(value, Value::Null) {
                    assert_eq!(sent.get(&variable), Some(value), "{variable}");
                }
            }
            streams.push((stream, projected, wire));
        }
        // A live session: idle for about 3 s, hold fire for 1.5 s, reload,
        // fire briefly, stand until the idle fidget plays, fire through it,
        // then hold forward and Sprint. Both streams project and encode
        // every frame, as the worker sends them.
        let int = |value: Option<&Value>| match value {
            None => None,
            Some(Value::Integer(value)) => Some(*value),
            Some(value) => panic!("unexpected integer {value:?}"),
        };
        let flag = |value: Option<&Value>| match value {
            None => None,
            Some(Value::Bool(value)) => Some(*value),
            Some(value) => panic!("unexpected flag {value:?}"),
        };
        // A switch polled on a native the owner client may answer
        // differently stays off the owner stream.
        let uncertain = |snapshot: &Snapshot| {
            let mut found = BTreeSet::new();
            for (&id, instance) in &snapshot.instances {
                let graph = assets.graphs.graph(instance.graph).unwrap();
                for (&raw, state) in &instance.states {
                    let node = graph.state(usize::from(raw)).unwrap();
                    if state.active && uncertain_polled_switch(node, state) {
                        found.insert((id, raw));
                    }
                }
            }
            found
        };
        // The reference withholds these switches too, and two more in
        // instance 9, which is absent from this startup snapshot. The 0ba4 child is
        // one of the tagged weapon-manager subgraphs the reference lacks.
        let root_0033 = root(0x0033);
        let child_01c7 = child(0x01c7, 0);
        let child_01cf = child(0x01cf, 3);
        let child_0ba4 = child(0x0ba4, 11);
        let withheld = BTreeSet::from([
            (root_0033, 10),
            (root_0033, 15),
            (root_0033, 20),
            (root_0033, 21),
            (root_0033, 22),
            (root_0033, 23),
            (manager, 12),
            (manager, 13),
            (manager, 14),
            (child_01c7, 25),
            (child_01c7, 28),
            (child_01c7, 30),
            (child_01c7, 48),
            (child_01cf, 17),
            (child_0ba4, 0),
        ]);
        assert_eq!(uncertain(snapshot), withheld);
        for (_, projected, _) in &streams {
            for &(id, raw) in &withheld {
                assert!(!projected.instance_states(id).unwrap().contains(&raw));
            }
        }
        let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
        let mut owner = Vec::new();
        let mut owner_hud = Vec::new();
        for t in 1..=900 {
            let frame = FIRST_TICK + t;
            let (forward, buttons) = match t {
                188..=281 | 520..=540 | 600..=640 => (0, 1),
                300 => (0, 0x400),
                700 => (127, 8),
                701..=760 => (127, 0),
                _ => (0, 0),
            };
            let command = Command {
                frame,
                forward,
                buttons,
                ..Command::default()
            };
            mover
                .step(scene.movement_index(), command, 0.016, |_, _| {})
                .unwrap();
            runtime.advance(command, &mover, BodyStep::Posed).unwrap();
            if let Some(modifiers) = runtime.movement_modifiers() {
                mover.track_modifiers(frame, modifiers);
            }
            runtime.take_shots(Some(command), &mover);
            let snapshot = runtime.snapshot().unwrap();
            // The worker cancels the body script at the first diagnostic.
            assert!(
                snapshot.diagnostics.is_empty(),
                "{t}: {:?}",
                snapshot.diagnostics
            );
            // The idle fidget animation has no authored duration. Like
            // the reference, it stays on without a timer until held fire
            // ends it.
            let idle = &snapshot.instances[&idle_id];
            assert_eq!(idle.graph, 0x02c5);
            let playing = idle.states.get(&19).is_some_and(|state| state.active);
            if playing {
                assert_eq!(idle.states[&19].kind, StateKind::Anim);
                assert!(
                    snapshot
                        .pending_timers
                        .get(&idle_id)
                        .into_iter()
                        .flatten()
                        .all(|timer| timer.raw_state != 19)
                );
            }
            assert_eq!(playing, (586..600).contains(&t), "{t}");
            // Sprint starts on the press with forward held, runs while
            // forward stays held and drives its HUD flag, as in the
            // reference.
            let sprint = &snapshot.instances[&sprint_id];
            assert_eq!(sprint.graph, 0x0255);
            let (flags, hud) = match t {
                700 => (5, Some(1)),
                701..=760 => (1, Some(1)),
                761.. => (0, Some(0)),
                _ => (0, None),
            };
            assert_eq!(
                (sprint.states[&1].kind, int(sprint.variables.get(&699))),
                (StateKind::Ability { flags }, hud),
                "{t}"
            );
            let excluded = uncertain(snapshot);
            let encoder = runtime.encoder();
            for (stream, initial, wire) in &mut streams {
                let next = encoder.project_next(snapshot, initial).unwrap();
                let kind = match initial.view() {
                    View::Owner => stream.owner_kind(frame, false, false).unwrap(),
                    View::Observer => Some(DeltaKind::Observer { resend: false }),
                };
                let Some(kind) = kind else { continue };
                let delta = encoder.queue_delta(stream, &next, kind, runtime.frame_ms());
                if let Some(chunk) = delta.unwrap() {
                    stream.sent(chunk.last);
                    wire.apply(
                        chunk.update.chunk.as_ref().unwrap(),
                        false,
                        next.view() == View::Owner,
                    )
                    .unwrap();
                    wire.verify(snapshot, &assets.graphs);
                    stream.arrived(chunk.last);
                }
                if next.view() != View::Owner {
                    continue;
                }
                for &(id, raw) in &excluded {
                    assert!(!next.instance_states(id).unwrap().contains(&raw), "{t}");
                }
                let rifle = next.instance_variables(rifle_id).unwrap();
                let fact = (int(rifle.get(&53)), flag(rifle.get(&51)));
                if owner.last().is_none_or(|&(_, last)| last != fact) {
                    owner.push((t, fact));
                }
                let hud = int(next.instance_variables(sprint_id).unwrap().get(&699));
                if owner_hud.last().is_none_or(|&(_, last)| last != hud) {
                    owner_hud.push((t, hud));
                }
            }
        }
        assert_eq!(streams[0].0.command_frame(), FIRST_TICK + 900);
        // The owner frames carry the Sprint HUD flag the client shows.
        assert_eq!(owner_hud, [(1, None), (700, Some(1)), (761, Some(0))]);
        // The frozen reference produces the same rifle ammo and reload
        // transitions from these inputs: 14 rounds in 1.5 s, the magazine
        // refilled 47 frames after the reload press, the reload ended 46
        // frames later, and six rounds through the idle fidget.
        let mut expected = vec![(1, (Some(30), None))];
        for (t, ammo) in [
            188, 195, 201, 208, 215, 222, 229, 236, 243, 250, 257, 264, 271, 278,
        ]
        .into_iter()
        .zip((16..30).rev())
        {
            expected.push((t, (Some(ammo), None)));
        }
        expected.extend([
            (300, (Some(16), Some(true))),
            (347, (Some(30), Some(true))),
            (393, (Some(30), Some(false))),
            (520, (Some(29), Some(false))),
            (527, (Some(28), Some(false))),
            (533, (Some(27), Some(false))),
            (540, (Some(26), Some(false))),
            (600, (Some(25), Some(false))),
            (607, (Some(24), Some(false))),
            (613, (Some(23), Some(false))),
            (620, (Some(22), Some(false))),
            (627, (Some(21), Some(false))),
            (634, (Some(20), Some(false))),
        ]);
        assert_eq!(owner, expected);
    } else {
        let Err(blocked) = scene.owner_body_runtime(hero, spawn) else {
            panic!("the untagged stock owner body was expected to stop before attach");
        };
        assert_eq!(blocked.kind(), io::ErrorKind::Unsupported);
        assert!(
            blocked
                .to_string()
                .contains("UnsupportedConfig(\"STU_AC829876\")"),
            "{blocked}"
        );
    }
    let tracer = scene.heroes().find_hero("Tracer").unwrap();
    assert!(scene.owner_body_runtime(tracer, spawn).unwrap().is_none());
}

#[test]
#[ignore = "requires a tagged 104319 Practice Range pack"]
fn stock_soldier_casts_biotic_field() {
    use crate::{commands::Command, game_replication::BodyStep};

    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene.heroes().get(SOLDIER).unwrap();
    let mover = scene.player_spawn_mover(hero.body, 0).unwrap();
    let spawn = OwnerBodySpawn {
        slot: 0,
        controller: 0xa000_0100,
        entity: 0xa000_0101,
        body_filter: team_bit(0).unwrap(),
        frame: FIRST_TICK,
        pose: mover.pose(),
        quanta: RETAIL_QUANTA,
    };
    let mut owner = scene.owner_body_runtime(hero, spawn).unwrap().unwrap();
    let mut shots = Vec::new();
    for frame in FIRST_TICK + 1..=FIRST_TICK + 120 {
        let command = Command {
            frame,
            buttons: if frame == FIRST_TICK + 40 { 0x10 } else { 0 },
            ..Command::default()
        };
        owner.advance(command, &mover, BodyStep::Posed).unwrap();
        let snapshot = owner.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "frame {frame}: {:?}",
            snapshot.diagnostics
        );
        shots.extend(owner.take_shots(Some(command), &mover));
    }
    let [crate::game_combat::PosedShot::BioticField(field)] = shots.as_slice() else {
        panic!("the actual ability must emit exactly one Biotic Field");
    };
    assert_eq!(field.shot.graph, 0x0257);
    assert_eq!(field.feet, mover.pose().position);
}

#[test]
#[ignore = "requires a tagged 104319 Practice Range pack"]
fn stock_soldier_quick_melee_runs_and_projects_on_owner_and_observer_streams() {
    use crate::{
        commands::Command,
        game_replication::BodyStep,
        script_init::StateKind,
        script_snapshot_wire::{View, identity_test_reader::IdentityReader},
        script_stream::{BodyStream, DeltaKind},
        script_wire::Value,
    };
    use std::collections::BTreeSet;

    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene.heroes().get(SOLDIER).unwrap();
    let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
    let spawn = OwnerBodySpawn {
        slot: 0,
        controller: 0xa000_0100,
        entity: 0xa000_0101,
        body_filter: team_bit(0).unwrap(),
        frame: FIRST_TICK,
        pose: mover.pose(),
        quanta: RETAIL_QUANTA,
    };
    let mut owner = scene.owner_body_runtime(hero, spawn).unwrap().unwrap();
    let snapshot = owner.snapshot().unwrap();
    let melee_id = unique_script_instance(snapshot, 0x0043, None);
    let encoder = owner.encoder();
    let mut streams = Vec::new();
    for view in [View::Owner, View::Observer] {
        let projected = encoder.project(snapshot, view).unwrap();
        let mut stream = BodyStream::new(spawn.entity);
        let baseline = encoder.queue_full(&mut stream, &projected).unwrap();
        stream.create_arrived();
        let mut wire = IdentityReader::default();
        wire.apply(
            baseline.update.chunk.as_ref().unwrap(),
            true,
            view == View::Owner,
        )
        .unwrap();
        wire.verify(snapshot, &assets.graphs);
        stream.arrived(baseline.last);
        streams.push((stream, projected, wire));
    }

    let mut melee_owner_sent = 0usize;
    let mut active_on_press = BTreeSet::new();
    let mut saw_hit_window_exit = false;
    let mut saw_ability_stop = false;
    let mut saw_cooldown_zero = false;
    let mut owner_cooldown_ticks = Vec::new();
    for t in 1..=120 {
        let frame = FIRST_TICK + t;
        let command = Command {
            frame,
            buttons: if t == 20 { 0x800 } else { 0 },
            ..Command::default()
        };
        mover
            .step(scene.movement_index(), command, 0.016, |_, _| {})
            .unwrap();
        owner.advance(command, &mover, BodyStep::Posed).unwrap();
        if let Some(modifiers) = owner.movement_modifiers() {
            mover.track_modifiers(frame, modifiers);
        }
        owner.take_shots(Some(command), &mover);
        let snapshot = owner.snapshot().unwrap();
        assert!(
            snapshot.diagnostics.is_empty(),
            "t={t} frame={frame}: {:?}",
            snapshot.diagnostics
        );
        let melee = &snapshot.instances[&melee_id];
        assert_eq!(melee.graph, 0x0043);
        let cooldown = snapshot
            .resolved_bindings
            .get(&(melee_id, 61))
            .or_else(|| melee.variables.get(&61));
        let active: BTreeSet<u16> = melee
            .states
            .iter()
            .filter_map(|(&raw, state)| state.active.then_some(raw))
            .collect();
        if t == 20 {
            assert_eq!(melee.states[&6].kind, StateKind::Ability { flags: 7 });
            assert_eq!(
                snapshot.resolved_bindings.get(&(0, 32)),
                Some(&Value::Bool(true))
            );
            assert!(
                matches!(cooldown, Some(Value::Float(v)) if *v > 0.9 && *v <= 1.0),
                "{cooldown:?}"
            );
            assert!(
                active.contains(&5),
                "TrackTargets state 5 missing: {active:?}"
            );
            assert!(
                active.contains(&24),
                "STU_77E13026 state 24 missing: {active:?}"
            );
            active_on_press = active.clone();
        } else if t == 21 {
            assert_eq!(melee.states[&6].kind, StateKind::Ability { flags: 3 });
        } else if t > 20 && !saw_hit_window_exit && !active.contains(&5) && active.contains(&9) {
            assert!(!active.contains(&8));
            assert!(!active.contains(&24));
            assert_eq!(t, 32, "0.2s wait should finish at t=32");
            saw_hit_window_exit = true;
        } else if t > 32
            && !saw_ability_stop
            && melee.states[&6].kind == (StateKind::Ability { flags: 0 })
        {
            assert_eq!(t, 53, "0.53s ability should stop at t=53");
            saw_ability_stop = true;
        } else if t > 20 && !saw_cooldown_zero && cooldown == Some(&Value::Float(0.0)) {
            assert_eq!(melee.states[&6].kind, StateKind::Ability { flags: 0 });
            saw_cooldown_zero = true;
        }

        let encoder = owner.encoder();
        for (stream, initial, wire) in &mut streams {
            let next = encoder.project_next(snapshot, initial).unwrap();
            let kind = match initial.view() {
                View::Owner => stream.owner_kind(frame, false, false).unwrap(),
                View::Observer => Some(DeltaKind::Observer { resend: false }),
            };
            let Some(kind) = kind else { continue };
            if next.view() == View::Owner {
                if t >= 20 {
                    melee_owner_sent += 1;
                }
                if let Some(Value::Float(value)) =
                    next.instance_variables(melee_id).unwrap().get(&61)
                {
                    if owner_cooldown_ticks
                        .last()
                        .is_none_or(|&(_, last): &(u32, f64)| last != *value)
                    {
                        owner_cooldown_ticks.push((t, *value));
                    }
                }
            }
            let delta = encoder
                .queue_delta(stream, &next, kind, owner.frame_ms())
                .unwrap();
            if let Some(chunk) = delta {
                stream.sent(chunk.last);
                wire.apply(
                    chunk.update.chunk.as_ref().unwrap(),
                    false,
                    next.view() == View::Owner,
                )
                .unwrap();
                wire.verify(snapshot, &assets.graphs);
                stream.arrived(chunk.last);
            }
        }
    }
    assert_eq!(melee_owner_sent, 101);
    assert_eq!(
        active_on_press,
        BTreeSet::from([
            2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 24, 26
        ])
    );
    assert!(
        saw_hit_window_exit,
        "did not observe 0.2s TrackTargets window exit"
    );
    assert!(saw_ability_stop, "did not observe 0.53s Ability stop");
    assert!(
        saw_cooldown_zero,
        "did not observe cooldown returning to 0.0"
    );
    assert!(
        owner_cooldown_ticks
            .iter()
            .any(|&(tick, v)| tick == 20 && v > 0.9 && v <= 1.0),
        "owner_cooldown_ticks={owner_cooldown_ticks:?}"
    );
    assert!(
        owner_cooldown_ticks.iter().any(|&(_, v)| v == 0.0),
        "owner_cooldown_ticks={owner_cooldown_ticks:?}"
    );
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack"]
fn elevated_practice_patrols_reach_their_hints_without_crossing_walls() {
    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let mut assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    assets.enable_contact_trace();
    let mut traced = assets.prepare(GameMap::practice_range()).unwrap();
    assert_eq!(scene.bots().unwrap().len(), 15);
    let scripted = match assets.graphs.graph_indexes().count() {
        373 | 699 | 700 => 0,
        405 => 15,
        count => panic!("no patrol reference profile for the {count}-graph bundle"),
    };
    assert_eq!(scene.scripted_bots(), scripted);
    let bots = [BOT_ENTITY + 4, BOT_ENTITY + 9];
    let mut previous = bots.map(|entity| {
        scene
            .bots()
            .unwrap()
            .find(|bot| bot.entity == entity)
            .unwrap()
            .position
    });
    let goals = [[3, 4], [0, 1]];
    let mut visited = [0; 2];
    let patrol = PatrolScript::load().unwrap();
    for tick in 1001..=2900 {
        scene.step_bots(tick, &patrol).unwrap();
        traced.step_bots(tick, &patrol).unwrap();
        for (slot, entity) in bots.into_iter().enumerate() {
            let bot = scene
                .bots()
                .unwrap()
                .find(|bot| bot.entity == entity)
                .unwrap();
            let traced_bot = traced
                .bots()
                .unwrap()
                .find(|bot| bot.entity == entity)
                .unwrap();
            assert_eq!(
                traced_bot.movement(tick),
                bot.movement(tick),
                "contact trace changed bot {entity:x} at {tick}"
            );
            let before = previous[slot];
            previous[slot] = bot.position;
            let delta = std::array::from_fn::<_, 3, _>(|axis| bot.position[axis] - before[axis]);
            let length = delta[0].hypot(delta[1]).hypot(delta[2]);
            if length > 0.001 {
                let origin = [before[0], before[1] + 1.05, before[2]];
                let category = mover_category(scene.map(), bot.spawn.team);
                let hit = scene
                    .ground
                    .cast_ray(origin, delta.map(|axis| axis / length), length, category)
                    .unwrap();
                assert!(
                    hit.is_none_or(|hit| hit.normal[1].abs() >= 0.1),
                    "bot {entity:x} crossed a wall at {tick}: {before:?} -> {:?}, {hit:?}",
                    bot.position
                );
            }
            if let Some(&index) = goals[slot].get(visited[slot]) {
                let hint = bot.spawn.route[index];
                if (bot.position[0] - hint[0]).hypot(bot.position[2] - hint[2]) < 0.06
                    && (bot.position[1] - hint[1]).abs() < 0.15
                {
                    visited[slot] += 1;
                }
            }
        }
    }
    assert_eq!(visited, [2; 2], "last positions {previous:?}");
}

#[test]
#[ignore = "requires a prepared 104319 runtime pack with 0688.col and 00D4.col"]
fn prepared_practice_range_and_kings_row_navigation_queries_route_around_walls_and_validate_spawns()
{
    use crate::{
        game_content::MapCatalog,
        navigation::{
            ClearanceQuery, DEFAULT_NAV_CATEGORY, EdgeQuery, GroundQuery, NavAgent, PathOptions,
            PathQuery, SpawnQuery, SpawnSearchRequest, capsule_clearance, find_path,
            snap_spawn_points, valid_spawns, walk_edge, walkable_ground,
        },
    };

    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&runtime).unwrap();
    let practice = assets.prepare(GameMap::practice_range()).unwrap();
    let soldier_mover = assets.movers.for_body(None);

    // 1. Practice Range (0688.col): player spawn + all 15 AI spawners + upstairs patrol wall detour.
    let player_spawn = practice.map().spawn_point(0, 0).unwrap().0;
    let player_agent = soldier_mover.nav_agent(mover_category(practice.map(), 0));
    assert!(matches!(
        walkable_ground(
            &practice.ground,
            &player_agent,
            player_spawn[0],
            player_spawn[2],
            player_spawn[1] + 1.0,
            player_spawn[1] - 2.0,
        ),
        GroundQuery::Walkable(_)
    ));

    let bot_spawns: Vec<[f64; 3]> = practice
        .bots()
        .unwrap()
        .map(|bot| bot.spawn.position)
        .collect();
    let bot_agent = soldier_mover.nav_agent(DEFAULT_NAV_CATEGORY);
    match snap_spawn_points(
        &practice.ground,
        practice.contacts.as_deref(),
        &bot_agent,
        &bot_spawns,
        15,
    ) {
        SpawnQuery::Found(snapped) => assert_eq!(snapped.len(), 15),
        other => panic!("expected all 15 Practice Range bot spawns to be valid, got {other:?}"),
    }

    // Upstairs patrolling bot (BOT_ENTITY + 4, y ≈ 11.0): verify wall-blocked spawn->route[1]
    // leg and all cyclic patrol legs via find_path detour.
    let upstairs_bot = practice
        .bots()
        .unwrap()
        .find(|bot| bot.entity == BOT_ENTITY + 4)
        .expect("upstairs patrol bot");
    let upstairs_agent = assets
        .movers
        .for_body(Some(upstairs_bot.spawn.body))
        .nav_agent(mover_category(practice.map(), upstairs_bot.spawn.team));
    let route = &upstairs_bot.spawn.route;
    let options = PathOptions::default();

    // Direct line from upstairs spawn.position to first patrol target route[1] crosses an upstairs wall.
    assert_eq!(
        capsule_clearance(
            &practice.ground,
            practice.contacts.as_deref(),
            &upstairs_agent,
            upstairs_bot.spawn.position,
            route[1],
        ),
        ClearanceQuery::Blocked
    );
    assert_eq!(
        walk_edge(
            &practice.ground,
            practice.contacts.as_deref(),
            &upstairs_agent,
            upstairs_bot.spawn.position,
            route[1][0],
            route[1][2],
        ),
        EdgeQuery::Blocked
    );
    let PathQuery::Found(upstairs_detour) = find_path(
        &practice.ground,
        practice.contacts.as_deref(),
        &upstairs_agent,
        upstairs_bot.spawn.position,
        route[1],
        &options,
    ) else {
        panic!("expected find_path to route around upstairs wall from spawn to route[1]");
    };
    assert!(
        upstairs_detour.len() >= 3,
        "wall-blocked upstairs spawn->route[1] must detour around wall: {upstairs_detour:?}"
    );
    for segment in upstairs_detour.windows(2) {
        assert_eq!(
            walk_edge(
                &practice.ground,
                practice.contacts.as_deref(),
                &upstairs_agent,
                segment[0],
                segment[1][0],
                segment[1][2],
            ),
            EdgeQuery::Walkable(segment[1]),
            "upstairs detour segment not walkable: {segment:?}"
        );
    }

    for index in 0..route.len() {
        let from = route[(index + route.len() - 1) % route.len()];
        let to = route[index];
        let PathQuery::Found(path) = find_path(
            &practice.ground,
            practice.contacts.as_deref(),
            &upstairs_agent,
            from,
            to,
            &options,
        ) else {
            panic!("upstairs patrol leg {index} ({from:?} -> {to:?}) failed pathfinding");
        };
        for segment in path.windows(2) {
            assert_eq!(
                walk_edge(
                    &practice.ground,
                    practice.contacts.as_deref(),
                    &upstairs_agent,
                    segment[0],
                    segment[1][0],
                    segment[1][2],
                ),
                EdgeQuery::Walkable(segment[1]),
                "upstairs leg {index} segment not walkable: {segment:?}"
            );
        }
    }
    assert_eq!(
        find_path(
            &practice.ground,
            practice.contacts.as_deref(),
            &upstairs_agent,
            route[0],
            [route[0][0], route[0][1] + 25.0, route[0][2]],
            &options,
        ),
        PathQuery::NoPath
    );
    assert_eq!(
        find_path(
            &practice.ground,
            practice.contacts.as_deref(),
            &upstairs_agent,
            route[0],
            [65_000.0, 0.0, 65_000.0],
            &options,
        ),
        PathQuery::Unknown
    );

    // 2. King's Row (00D4.col) + optional additional maps in the prepared pack (005B, 0165, 0CD3).
    let map_catalog = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
    let kings_row_guid: u64 = 0x0800_0000_0000_00d4;
    let kings_row_mode: u64 = 0x0230_0000_0000_0010; // Skirmish
    let kings_row = map_catalog
        .game_map(kings_row_guid, kings_row_mode, "Skirmish", false, &[6, 6])
        .expect("King's Row map entry");
    let kr_collision = assets.map_collision(&kings_row, true).unwrap();
    let kr_contacts = kr_collision.contacts.as_deref();
    let kr_agent = NavAgent::default();

    let kr_spawns: Vec<[f64; 3]> = kings_row.spawns.iter().map(|s| s.position).collect();
    let SpawnQuery::Found(kr_valid_spawns) =
        snap_spawn_points(&kr_collision.ground, kr_contacts, &kr_agent, &kr_spawns, 4)
    else {
        panic!("expected walkable King's Row spawns");
    };
    let anchor = kr_valid_spawns[0];
    let radial = valid_spawns(
        &kr_collision.ground,
        kr_contacts,
        &kr_agent,
        &SpawnSearchRequest {
            origin: anchor,
            direction: [1.0, 0.0, 0.0],
            radius: 4.0,
            half_angle_rad: std::f64::consts::PI,
            minimum: 2,
            maximum: 12,
        },
    );
    let SpawnQuery::Found(radial_points) = radial else {
        panic!("expected valid radial spawn candidates around King's Row spawn {anchor:?}");
    };
    let goal = *radial_points
        .iter()
        .max_by(|a, b| {
            let da = (a[0] - anchor[0]).hypot(a[2] - anchor[2]);
            let db = (b[0] - anchor[0]).hypot(b[2] - anchor[2]);
            da.total_cmp(&db)
        })
        .unwrap();
    let PathQuery::Found(kr_path) = find_path(
        &kr_collision.ground,
        kr_contacts,
        &kr_agent,
        anchor,
        goal,
        &options,
    ) else {
        panic!("expected path on King's Row between {anchor:?} and {goal:?}");
    };
    assert!(kr_path.len() >= 2);
    for segment in kr_path.windows(2) {
        assert_eq!(
            walk_edge(
                &kr_collision.ground,
                kr_contacts,
                &kr_agent,
                segment[0],
                segment[1][0],
                segment[1][2],
            ),
            EdgeQuery::Walkable(segment[1])
        );
    }
    assert_eq!(
        find_path(
            &kr_collision.ground,
            kr_contacts,
            &kr_agent,
            anchor,
            [anchor[0], anchor[1] + 40.0, anchor[2]],
            &options,
        ),
        PathQuery::NoPath
    );

    // 3. Also verify any additional prepared maps present in the multi-map pack (Temple of Anubis 005B, Hanamura 0165, Kanezaka 0CD3).
    for (hex, guid, mode) in [
        (
            "005B.col",
            0x0800_0000_0000_005b_u64,
            0x0230_0000_0000_0010_u64,
        ),
        (
            "0165.col",
            0x0800_0000_0000_0165_u64,
            0x0230_0000_0000_0010_u64,
        ),
        (
            "0CD3.col",
            0x0800_0000_0000_0cd3_u64,
            0x0230_0000_0000_001e_u64,
        ),
    ] {
        if !runtime.join("collision").join(hex).is_file() {
            continue;
        }
        let ffa = mode == 0x0230_0000_0000_001e;
        let map = map_catalog
            .game_map(guid, mode, "Navigation Probe", ffa, &[6, 6])
            .expect(hex);
        let col = assets.map_collision(&map, true).unwrap();
        let raw_spawns: Vec<[f64; 3]> = map.spawns.iter().map(|s| s.position).collect();
        let SpawnQuery::Found(valid) = snap_spawn_points(
            &col.ground,
            col.contacts.as_deref(),
            &kr_agent,
            &raw_spawns,
            2,
        ) else {
            panic!("expected walkable spawns on {hex}");
        };
        let SpawnQuery::Found(cone) = valid_spawns(
            &col.ground,
            col.contacts.as_deref(),
            &kr_agent,
            &SpawnSearchRequest {
                origin: valid[0],
                direction: [1.0, 0.0, 0.0],
                radius: 3.5,
                half_angle_rad: std::f64::consts::PI,
                minimum: 1,
                maximum: 8,
            },
        ) else {
            panic!(
                "expected valid cone spawn around {hex} spawn {:?}",
                valid[0]
            );
        };
        assert!(matches!(
            find_path(
                &col.ground,
                col.contacts.as_deref(),
                &kr_agent,
                valid[0],
                cone[0],
                &options,
            ),
            PathQuery::Found(_)
        ));
    }
}
