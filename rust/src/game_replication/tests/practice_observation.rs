use super::*;
use crate::script_graph::{Graph, GraphStore};
use crate::script_wire::Value as ScriptValue;
use crate::{
    commands::{FIRE, SECONDARY},
    script_expr::{Scope, Variable, dynamic, truthy},
    script_snapshot_wire::SnapshotError,
};
use serde_json::{Value as Json, json};

mod actor_contact;
mod chain_hook;
mod doomfist;
mod fault_shapes;
mod grapple;
mod lifecycle_voice;
mod piledriver;

fn declared_reload_flag(graph: &Graph) -> bool {
    let reload = Variable {
        scope: Scope::Instance,
        id: 51,
    };
    let Some(switch) = graph
        .node(2)
        .filter(|n| n.class == "STUStatescriptStateBooleanSwitch")
    else {
        return false;
    };
    let Some(write) = graph
        .node(5)
        .filter(|n| n.class == "STUStatescriptActionSetVar")
    else {
        return false;
    };
    let reads = switch
        .fields
        .pointer("/m_condition/m_expression/m_dynamicVars")
        .and_then(serde_json::Value::as_array);
    reads.is_some_and(|vars| vars.iter().any(|v| dynamic(v) == Ok(Some(reload))))
        && write
            .fields
            .get("m_out_Var")
            .is_some_and(|v| dynamic(v) == Ok(Some(reload)))
}

pub(crate) fn rifle_state(body: &PlayerBodyState) -> (i64, Option<bool>) {
    let snapshot = body.runtime.as_ref().unwrap().snapshot().unwrap();
    assert!(snapshot.initialization_complete());
    // Resolve the authored rifle root independently of VM allocation order.
    let mut rifles = snapshot
        .instances
        .iter()
        .filter(|(_, instance)| instance.graph == 0x0254 && instance.parent.is_none());
    let (&id, rifle) = rifles.next().expect("stock Soldier rifle root");
    assert!(rifles.next().is_none(), "ambiguous stock rifle root");
    let value = |key| {
        snapshot
            .resolved_bindings
            .get(&(id, key))
            .or_else(|| rifle.variables.get(&key))
    };
    let Some(ScriptValue::Integer(ammo)) = value(53) else {
        panic!("stock rifle has no integer magazine value");
    };
    // Machine::value falls back to shapes::stored for an unbound local. The
    // native store returns Null before the first write. Establish that the
    // stock eligibility switch and an authored writer actually use this flag
    // before applying that lookup to the otherwise sparse snapshot.
    let graph = body
        .runtime
        .as_ref()
        .unwrap()
        .encoder()
        .graphs
        .graph(rifle.graph)
        .unwrap();
    let reload = if declared_reload_flag(graph)
        && snapshot.bindings.get(&(id, 51)).is_none_or(Vec::is_empty)
    {
        match rifle.variables.get(&51).unwrap_or(&ScriptValue::Null) {
            v @ (ScriptValue::Null | ScriptValue::Bool(_)) => Some(truthy(v)),
            _ => None,
        }
    } else {
        None
    };
    (*ammo, reload)
}

fn deliver_until(
    world: &mut WorldDelivery,
    scene: &mut PreparedScene,
    egress: &mut GameEgress<Arc<Record>>,
    tick: &mut u32,
    ready: impl Fn(&WorldDelivery) -> bool,
) -> bool {
    // Receipt is modelled only after the real egress selected the frame and
    // submitted it to the in-memory transport.
    for _ in 0..32 {
        world.present(scene, *tick).unwrap();
        if ready(world) {
            return true;
        }
        let (_, sent) = transmit(
            world,
            Some(scene.simulation_mut()),
            egress,
            *tick,
            *tick - FIRST_TICK,
        );
        let receipt = egress.acknowledge(sent.sequence + 1, 1);
        world.acknowledge(receipt, Duration::ZERO);
        *tick += 1;
    }
    ready(world)
}

pub(crate) fn processed(world: &WorldDelivery) -> Option<u32> {
    world.input.processed
}

pub(crate) fn controller_acked(world: &WorldDelivery) -> bool {
    world.identity_ready(world.owner()) && world.controller.delivered() != 0
}

pub(crate) fn possession_acked(world: &WorldDelivery) -> bool {
    world.bound.is_some() && world.bound == world.active_body_id()
}

pub(crate) fn body_script_frame(world: &WorldDelivery) -> Option<u32> {
    world.body_script.as_ref().map(|script| script.frame)
}

fn prepare_owner(
    scene: &mut PreparedScene,
    world: &mut WorldDelivery,
    egress: &mut GameEgress<Arc<Record>>,
) -> u32 {
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let mut tick = FIRST_TICK;
    world.start(scene, &viewer, tick).unwrap();
    assert_eq!(world.command_ack(tick), tick);
    assert_eq!(world.input.processed, None);
    world.queue_game_message(tick, Duration::ZERO, pick_hero(OWNER, SOLDIER));
    assert!(!world.has_input_work());

    assert!(deliver_until(world, scene, egress, &mut tick, |world| {
        world.identity_ready(OWNER) && world.controller.latest_data() != 0
    }));
    world.queue_game_message(tick, Duration::ZERO, pick_hero(OWNER, SOLDIER));
    assert_eq!(
        world
            .advance_input(scene, &viewer, RETAIL_QUANTA, 1)
            .unwrap(),
        (1, false)
    );
    let body = scene.simulation().player_body_by_owner(OWNER).unwrap();
    let id = body.id;
    let component_start_frame = world.body_script.as_ref().unwrap().frame;
    assert_eq!(component_start_frame, tick);
    assert_eq!(body.hero, SOLDIER);
    assert_eq!(
        body.runtime.as_ref().unwrap().vitals().unwrap().total(),
        200.0
    );
    assert_eq!(rifle_state(body).0, 30);
    assert!(deliver_until(world, scene, egress, &mut tick, |world| {
        world.identity_ready(id.entity) && world.bound == Some(id)
    }));
    assert_eq!(world.input.processed, None);

    tick
}

fn select_practice_hero(
    scene: &mut PreparedScene,
    viewer: &PlayerAppearance<'_>,
    hero_guid: u64,
    egress: &mut GameEgress<Arc<Record>>,
) -> (WorldDelivery, BodyId, u32) {
    use crate::game_instance::TeamMemberCounts;
    let roster = TeamMemberCounts::from_teams(scene.map().free_for_all, [viewer.team]);
    scene.simulation_mut().set_team_member_counts(roster);
    let mut world = WorldDelivery::new(viewer.entity);
    let mut tick = FIRST_TICK;
    world.start(scene, viewer, tick).unwrap();
    assert!(deliver_until(
        &mut world,
        scene,
        egress,
        &mut tick,
        controller_acked
    ));
    scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
    world.queue_game_message(tick, Duration::ZERO, pick_hero(viewer.entity, hero_guid));
    world
        .advance_input(scene, viewer, RETAIL_QUANTA, 16)
        .unwrap();
    let id = world
        .active_body_id()
        .expect("the selected hero was admitted");
    assert!(deliver_until(
        &mut world,
        scene,
        egress,
        &mut tick,
        possession_acked
    ));
    (world, id, tick)
}

#[test]
#[ignore = "requires the 104319 Practice Range with Wrecking Ball and training bots"]
fn prepared_piledriver_landing_lifts_a_room_bot() {
    use crate::{game_clock::TICK_PERIOD, patrol_script::PatrolScript};

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
    // This stock stationary target has clear overhead space. Face it from
    // three metres behind: the graph moves the owner forward during wind-up.
    let target = crate::game_bots::BOT_ENTITY + 7;
    let [x, y, z] = scene.simulation_mut().body_mut(target).unwrap().position;
    let release = [x, y + 5.0, z + 3.0];
    let yaw = 0x8000;
    let health = scene.simulation().body_healths().unwrap()[&target];
    let mover = scene
        .player_spawn_mover_at(hero.body, 0, release, yaw)
        .unwrap();
    let mut release_mover = Some(mover);
    let crouch = crate::script_runtime::held_button_mask(12).unwrap();
    let patrol = PatrolScript::load().unwrap();
    let mut selected = false;
    let mut hit = None;
    let mut rise = None;
    for step in 0..230 {
        if step == 90 {
            scene.simulation_mut().player_body_mut(id).unwrap().mover =
                release_mover.take().unwrap();
        }
        tick += 1;
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        let command = Command {
            frame: tick,
            yaw,
            buttons: if (95..=98).contains(&step) { crouch } else { 0 },
            ..Command::default()
        };
        world.queue_commands(tick, &[command]);
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let player = scene.simulation().player_body(id).unwrap();
        let snap = player
            .runtime
            .as_ref()
            .expect("owner VM remains active")
            .snapshot()
            .unwrap();
        let landing = snap.instances.values().find(|s| s.graph == 0x160a).unwrap();
        selected |= matches!(landing.variables.get(&375), Some(ScriptValue::Array(targets)) if targets.contains(&ScriptValue::Entity(target)));
        scene.step_bots(tick, &patrol).unwrap();
        let (room, queries) = scene.bot_simulation();
        room.finish_tick(&queries, tick, TICK_PERIOD * tick)
            .unwrap();
        let y = room.body_mut(target).unwrap().position[1];
        if let Some((hit_tick, hit_y)) = hit {
            if tick == hit_tick + 1 {
                rise = Some(y - hit_y);
            }
        }
        for cue in room.take_player_cues() {
            if cue.owner == OWNER
                && matches!(cue.notice,
                crate::game_combat_messages::CombatNotice::Hit { target: body, dealt, .. }
                    if body == target && dealt > 0.0)
            {
                assert!(
                    hit.is_none(),
                    "one landing applied damage twice to {target:#x}"
                );
                assert!(room.body_healths().unwrap()[&target] < health);
                hit = Some((tick, y));
            }
        }
    }
    assert!(
        selected,
        "the original landing tracker did not select the bot"
    );
    assert!(
        hit.is_some(),
        "no damage was credited to the landing player"
    );
    assert!(
        rise.is_some_and(|dy| dy > 0.0),
        "no upward motion on the next tick: {rise:?}"
    );
}

#[test]
#[ignore = "requires the target-tagged 104319 Practice Range runtime"]
fn prepared_final_blow_effect_survives_owner_egress_loss() {
    exercise_final_blow_effect(false);
}

#[test]
#[ignore = "requires the target-tagged 104319 Practice Range runtime"]
fn prepared_projectile_final_blow_effect_survives_owner_egress_loss() {
    exercise_final_blow_effect(true);
}

fn exercise_final_blow_effect(projectile: bool) {
    use crate::{
        game_bots::BOT_ENTITY,
        game_clock::TICK_PERIOD,
        game_combat::{AdmittedFlight, FlightOutcome, ShotContext},
        game_hitscan::{DamageTags, HitDefinition},
        game_projectiles::{OwnerProjectiles, ProjectileLeases},
        projectile_flight::{ProjectileBallistics, ProjectileFlight},
        script_init::ShotEvent,
        script_stream::OWNER_SETTLE_FRAMES,
    };

    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let [x, y, z] = scene.map().spawn_point(0, 0).unwrap().0;
    let target = BOT_ENTITY + 5;
    let simulation = scene.simulation_mut();
    simulation.enable_authored_elimination_feedback();
    simulation.body_mut(target).unwrap().position = [x - 4.0, y, z];

    let mut world = WorldDelivery::new(OWNER);
    let mut egress = GameEgress::default();
    let mut start = prepare_owner(&mut scene, &mut world, &mut egress);
    assert!(deliver_until(
        &mut world,
        &mut scene,
        &mut egress,
        &mut start,
        |world| {
            world
                .body_script
                .as_ref()
                .is_some_and(|script| script.stream.base().is_some())
        }
    ));
    let id = world.active_body_id().unwrap();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let acknowledged = |world: &WorldDelivery| {
        world
            .body_script
            .as_ref()
            .unwrap()
            .stream
            .base()
            .unwrap()
            .1
            .acknowledged_callout()
    };
    let advance = |world: &mut WorldDelivery, scene: &mut PreparedScene, tick| {
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                yaw: -16384,
                ..Command::default()
            }],
        );
        let (_, left) = world
            .advance_input(scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        assert!(!left);
        assert_eq!(world.input.processed, Some(tick));
    };
    let settle = |scene: &mut PreparedScene, tick, amount, killed| {
        let (simulation, queries) = scene.bot_simulation();
        simulation.activate_combat();
        let context = ShotContext {
            team: 0,
            quanta: RETAIL_QUANTA,
            tick,
            now: tick * TICK_PERIOD,
        };
        let hit = HitDefinition {
            amount,
            tags: DamageTags::EMPTY,
            loadout: None,
            critical: 1.0,
            falloff: None,
        };
        let origin = [x, y + 1.0, z];
        let direction = [-1.0, 0.0, 0.0];
        let settled = if projectile && killed {
            let template = 1;
            let leases = ProjectileLeases::new(0, tick, [template]).unwrap();
            let token = OwnerProjectiles::new(id.entity, leases)
                .admit(
                    ShotEvent {
                        entity: id.entity,
                        instance: 1,
                        graph: 1,
                        raw_state: 0,
                        frame: tick,
                        time_ms: 0,
                        activation: 1,
                        activation_ms: 0,
                        ordinal: 0,
                    },
                    template,
                    3,
                )
                .unwrap();
            let flight = ProjectileFlight::new(
                ProjectileBallistics {
                    speed: 300.0,
                    lifetime: 0.03,
                    max_distance: None,
                    radius: 0.0,
                    terrain_radius: 0.0,
                    definition: Some(template),
                    direct: Some(hit),
                    blast: None,
                    terrain_rebound: None,
                },
                origin,
                direction,
                None,
            )
            .unwrap();
            let (outcomes, mut hits) = simulation
                .advance_projectiles(
                    &queries,
                    1,
                    id,
                    vec![AdmittedFlight {
                        token,
                        launch_frame: context.tick,
                        flight,
                        stun: None,
                        hit_effect: None,
                        impact_spawn: None,
                        presentation: None,
                    }],
                    context,
                    |_| true,
                )
                .unwrap();
            assert!(matches!(
                outcomes.as_slice(),
                [FlightOutcome::Ended { token: settled, impact: true, .. }] if *settled == token
            ));
            assert_eq!(hits.len(), 1);
            hits.remove(0)
        } else {
            simulation
                .hitscan(
                    &queries,
                    PosedHitscan {
                        source: id.entity,
                        command: Command {
                            frame: tick,
                            ..Command::default()
                        },
                        shot_frame: tick,
                        origin,
                        direction,
                        reach: 8.0,
                        hit,
                    },
                    context,
                    |entity| entity == target,
                )
                .unwrap()
                .expect("hit the stationary target")
        };
        assert!(matches!(
            settled.notices[0],
            Some(crate::game_combat_messages::CombatNotice::Hit { target: hit_target, killed: observed, .. })
                if hit_target == target && observed == killed
        ));
    };

    let before = acknowledged(&world);
    let lethal_tick = start + OWNER_SETTLE_FRAMES + 4;
    for tick in start + 1..=lethal_tick + 8 {
        advance(&mut world, &mut scene, tick);
        if tick == start + 1 {
            settle(&mut scene, tick, 20.0, false);
        } else if tick == lethal_tick {
            settle(&mut scene, tick, 500.0, true);
        }
        let runtime = scene
            .simulation()
            .player_body(id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap();
        let snapshot = runtime.snapshot().unwrap();
        assert!(snapshot.diagnostics.is_empty());
        let actions = snapshot
            .frame_actions
            .iter()
            .filter(|action| action.callout.serial > before);
        assert_eq!(actions.count(), usize::from(tick > lethal_tick));

        let sequence = tick - FIRST_TICK;
        let (_, frame) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            tick,
            sequence,
        );
        let delivered = tick != lethal_tick + 1;
        let report = egress.acknowledge(frame.sequence + 1, u64::from(delivered));
        if !delivered {
            assert!(report.packets.iter().flat_map(|packet| &packet.records).any(|record| {
                matches!(record.delivery, Delivery::BodyScript { id: sent, delta: true, .. } if sent == id)
            }));
        }
        world.acknowledge(report, Duration::ZERO);
        assert_eq!(
            acknowledged(&world),
            before + u32::from(tick > lethal_tick + 1)
        );
    }
    assert!(world.owner_script_fault.is_none());
}

#[test]
#[ignore = "requires the target-tagged 104319 Practice Range runtime"]
fn stock_practice_owner_observes_processed_commands_and_rifle_state() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    assert!(scene.movement_ready());
    let mut world = WorldDelivery::new(OWNER);
    let mut egress = GameEgress::default();
    let mut tick = prepare_owner(&mut scene, &mut world, &mut egress);
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let id = scene.simulation().player_body_by_owner(OWNER).unwrap().id;
    let start = tick;
    for elapsed in 1..=164 {
        tick = start + elapsed;
        let command = Command {
            frame: tick,
            yaw: 137,
            pitch: 23,
            buttons: match elapsed {
                40 => 1,
                70 => 0x400,
                _ => 0,
            },
            ..Command::default()
        };
        let previous = world.input.processed;
        world.queue_commands(tick, &[command]);
        assert_eq!(world.input.admitted, Some(tick));
        assert_eq!(world.input.processed, previous);
        let (_, leave) = world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        assert!(!leave);
        assert_eq!(world.input.processed, Some(tick));
        let body = scene.simulation().player_body(id).expect("same live body");
        let pose = body.mover.pose();
        assert_eq!(
            (pose.frame, pose.yaw, pose.pitch),
            (Some(tick), command.yaw, command.pitch)
        );
        let (ammo, reload) = rifle_state(body);
        assert_eq!(
            ammo,
            if (40..117).contains(&elapsed) { 29 } else { 30 },
            "tick {elapsed}"
        );
        if elapsed >= 70 {
            assert_eq!(reload, Some(elapsed < 163), "tick {elapsed}");
        } else {
            assert_ne!(reload, Some(true), "tick {elapsed}");
        }
    }
}

#[test]
#[ignore = "requires the target-tagged 104319 Practice Range runtime"]
fn stock_practice_owner_rifle_batched_tap_delivers_terminal_ammo() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let mut world = WorldDelivery::new(OWNER);
    let mut egress = GameEgress::default();
    let mut tick = prepare_owner(&mut scene, &mut world, &mut egress);
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let id = world.active_body_id().unwrap();
    assert!(deliver_until(
        &mut world,
        &mut scene,
        &mut egress,
        &mut tick,
        |world| { world.body_script.as_ref().unwrap().stream.base().is_some() }
    ));

    for _ in 0..32 {
        tick += 1;
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                ..Command::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
        let (_, sent) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            tick,
            tick - FIRST_TICK,
        );
        transport_outcome(&mut world, &mut egress, sent.sequence, true);
    }

    let press = tick + 1;
    let release = press + 1;
    world.queue_commands(
        release,
        &[
            Command {
                frame: press,
                buttons: FIRE,
                ..Command::default()
            },
            Command {
                frame: release,
                ..Command::default()
            },
        ],
    );
    assert_eq!(
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap(),
        (2, false)
    );
    let actor = scene.simulation().player_body(id).unwrap();
    let snapshot = actor.runtime.as_ref().unwrap().snapshot().unwrap();
    let (&rifle_id, rifle) = snapshot
        .instances
        .iter()
        .find(|(_, instance)| instance.graph == 0x0254 && instance.parent.is_none())
        .unwrap();
    assert_eq!(rifle_state(actor).0, 29);
    assert!(!rifle.states[&10].active);
    let shots = world.take_hitscan(scene.simulation_mut());
    assert_eq!(shots.len(), 1);
    assert_eq!(shots[0].0.shot_frame, press);

    let (_, sent) = transmit(
        &mut world,
        Some(scene.simulation_mut()),
        &mut egress,
        release,
        release - FIRST_TICK,
    );
    transport_outcome(&mut world, &mut egress, sent.sequence, true);
    let stream = &world.body_script.as_ref().unwrap().stream;
    assert_eq!(stream.command_frame(), release);
    let (acknowledged, snapshot) = stream.base().unwrap();
    assert_eq!(acknowledged, stream.last());
    assert_eq!(
        snapshot.instance_variables(rifle_id).unwrap()[&53],
        ScriptValue::Integer(29)
    );
    assert_eq!(
        snapshot.volley_identity(rifle_id, 10),
        Some((1, u64::from(press) * FRAME_MS))
    );

    // Send the next press and release separately. The press can seed the
    // client's volley with the count before the shot. Lose that packet and
    // prove the release carries the post-shot count without another shot.
    let next_press = release + 24;
    let next_release = next_press + 1;
    for (frame, buttons, arrived, wire_ammo) in
        [(next_press, FIRE, false, 29), (next_release, 0, true, 28)]
    {
        world.queue_commands(
            frame,
            &[Command {
                frame,
                buttons,
                ..Command::default()
            }],
        );
        assert!(
            !world
                .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 32)
                .unwrap()
                .1
        );
        let shots = world.take_hitscan(scene.simulation_mut());
        assert_eq!(shots.len(), usize::from(buttons == FIRE));
        assert_eq!(
            rifle_state(scene.simulation().player_body(id).unwrap()).0,
            28
        );
        let (_, sent) = transmit(
            &mut world,
            Some(scene.simulation_mut()),
            &mut egress,
            frame,
            frame - FIRST_TICK,
        );
        let stream = &world.body_script.as_ref().unwrap().stream;
        assert_eq!(stream.command_frame(), frame);
        assert_eq!(
            stream
                .latest_snapshot()
                .unwrap()
                .instance_variables(rifle_id)
                .unwrap()[&53],
            ScriptValue::Integer(wire_ammo),
        );
        transport_outcome(&mut world, &mut egress, sent.sequence, arrived);
    }
    let (_, confirmed) = world.body_script.as_ref().unwrap().stream.base().unwrap();
    assert_eq!(
        confirmed.instance_variables(rifle_id).unwrap()[&53],
        ScriptValue::Integer(28)
    );
}

#[test]
#[ignore = "requires the target-tagged 104319 Practice Range runtime"]
fn stock_practice_owner_gap_does_not_charge_for_an_unposed_rifle_round() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let mut world = WorldDelivery::new(OWNER);
    let mut egress = GameEgress::default();
    let tick = prepare_owner(&mut scene, &mut world, &mut egress);
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let id = world.active_body_id().unwrap();
    let ammo = |scene: &PreparedScene| rifle_state(scene.simulation().player_body(id).unwrap()).0;
    let press = tick + 40;
    let last_pose = press + 124;
    let mut posed_shots = 0;
    for frame in tick..=last_pose {
        world.queue_commands(
            frame,
            &[Command {
                frame,
                buttons: if frame >= press { FIRE } else { 0 },
                ..Command::default()
            }],
        );
        let (_, left) = world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 32)
            .unwrap();
        assert!(!left);
        posed_shots += world.take_hitscan(scene.simulation_mut()).len();
    }
    // At 9 rounds per second, round 19 is due on the next frame, which
    // this long gap cannot reconstruct. The neutral boundary releases FIRE
    // without supplying an authoritative shot pose.
    assert_eq!((posed_shots, ammo(&scene)), (18, 12));
    let resume = last_pose + 65;
    world.queue_commands(
        resume,
        &[Command {
            frame: resume,
            ..Command::default()
        }],
    );
    let (_, left) = world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 128)
        .unwrap();
    assert!(!left);
    assert_eq!(world.input.processed, Some(resume));
    assert!(world.take_hitscan(scene.simulation_mut()).is_empty());
    assert_eq!(ammo(&scene), 12);

    let next_shot = resume + 1;
    world.queue_commands(
        next_shot,
        &[Command {
            frame: next_shot,
            buttons: FIRE,
            ..Command::default()
        }],
    );
    let (_, left) = world
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
        .unwrap();
    assert!(!left);
    assert_eq!(world.input.processed, Some(next_shot));
    let shots = world.take_hitscan(scene.simulation_mut());
    assert_eq!(shots.len(), 1);
    assert_eq!(shots[0].0.shot_frame, next_shot);
    assert_eq!(ammo(&scene), 11);
}

#[derive(Default)]
struct InputObservation {
    admitted: bool,
    owner_full_ack: bool,
    owner_delta_ack: bool,
    completed_frames: u32,
    completed_phases: Vec<&'static str>,
    script_fault: Option<OwnerScriptFault>,
    hitscan: usize,
    launches: usize,
}

type InputPhase = (&'static str, u32, i8, u32);

const WEAPON_PHASES: &[InputPhase] = &[
    ("idle", 4, 0, 0),
    ("movement", 8, 127, 0),
    ("primary", 24, 0, FIRE),
    ("primary release", 8, 0, 0),
    ("secondary", 24, 0, SECONDARY),
    ("settle", 32, 0, 0),
];

// These masks are used by the existing prepared input regressions and the
// pinned Python runtime tests. Separate presses with neutral frames.
const ABILITY_PHASES: &[InputPhase] = &[
    ("ability 1", 8, 127, 0x08),
    ("ability 1 release", 24, 0, 0),
    ("ability 2", 8, 0, 0x10),
    ("ability 2 release", 24, 0, 0),
    ("quick melee", 1, 0, 0x800),
    ("melee release", 48, 0, 0),
    ("reload", 1, 0, 0x400),
    ("reload release", 64, 0, 0),
    ("ultimate input", 1, 0, 0x20),
    ("ultimate release", 32, 0, 0),
];

fn observe_prepared_actor_input(
    scene: &mut PreparedScene,
    hero: u64,
    observation: &mut InputObservation,
    phases: impl IntoIterator<Item = &'static InputPhase>,
) -> Result<(), (&'static str, u32, &'static str)> {
    use crate::game_instance::TeamMemberCounts;

    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &PlayerCard::default(),
    };
    let roster = TeamMemberCounts::from_teams(scene.map().free_for_all, [viewer.team]);
    scene.simulation_mut().set_team_member_counts(roster);
    let mut world = WorldDelivery::new(OWNER);
    let mut egress = GameEgress::default();
    let mut tick = FIRST_TICK;
    world.start(scene, &viewer, tick).unwrap();
    if !deliver_until(&mut world, scene, &mut egress, &mut tick, controller_acked) {
        return Err(("controller", tick, "pending acknowledgement"));
    }
    scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
    world.queue_game_message(tick, Duration::ZERO, pick_hero(OWNER, hero));
    let work = world
        .advance_input(scene, &viewer, RETAIL_QUANTA, 16)
        .map_err(|_| ("selection", tick, "input error"))?;
    if work != (1, false) {
        return Err(("selection", tick, "request did not complete"));
    }
    let Some(id) = world.active_body_id() else {
        return Err(("admission", tick, "rejected"));
    };
    let Some(actor) = scene.simulation().player_body(id) else {
        return Err(("admission", tick, "missing committed body"));
    };
    if actor.hero != hero || actor.runtime.is_none() || !world.combat_body_active() {
        return Err(("admission", tick, "missing selected runtime"));
    }
    observation.admitted = true;
    let spawn_pose = actor.mover.pose();

    let owner_baseline = |world: &WorldDelivery| {
        world
            .body_script
            .as_ref()
            .and_then(|script| script.stream.base())
            .map(|(last, _)| last)
    };
    let delivered = deliver_until(&mut world, scene, &mut egress, &mut tick, |world| {
        possession_acked(world) && owner_baseline(world).is_some()
    });
    let owner_full = owner_baseline(&world);
    observation.owner_full_ack = owner_full.is_some();
    let Some(owner_full) = owner_full.filter(|_| delivered) else {
        observation.script_fault = world.owner_script_fault.take();
        return Err((
            "possession",
            tick,
            "missing owner baseline or body acknowledgement",
        ));
    };

    for &(phase, frames, forward, buttons) in phases {
        for _ in 0..frames {
            tick += 1;
            scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
            let command = Command {
                frame: tick,
                forward,
                yaw: spawn_pose.yaw,
                pitch: spawn_pose.pitch,
                buttons,
                ..Command::default()
            };
            world.queue_commands(tick, &[command]);
            let (_, leave) = world
                .advance_input(scene, &viewer, RETAIL_QUANTA, 16)
                .map_err(|_| (phase, tick, "input error"))?;
            if leave || world.input.processed != Some(tick) {
                return Err((phase, tick, "command not processed"));
            }
            let actor = scene
                .simulation()
                .player_body(id)
                .filter(|actor| actor.runtime.is_some() && actor.frame == tick)
                .filter(|actor| actor.mover.pose().frame == Some(tick));
            if actor.is_none() || body_script_frame(&world) != Some(tick) {
                observation.script_fault = world.owner_script_fault.take();
                return Err((phase, tick, "runtime or posed frame lost after input"));
            }
            let simulation = scene.simulation_mut();
            observation.hitscan += world.take_hitscan(simulation).len();
            observation.launches += simulation
                .take_player_projectile_launches(id)
                .map_or(0, |launches| launches.len());
            let (_, sent) = transmit(
                &mut world,
                Some(simulation),
                &mut egress,
                tick,
                tick - FIRST_TICK,
            );
            transport_outcome(&mut world, &mut egress, sent.sequence, true);
            if world.active_body_id() != Some(id)
                || body_script_frame(&world) != Some(tick)
                || scene
                    .simulation()
                    .player_body(id)
                    .is_none_or(|actor| actor.runtime.is_none())
            {
                observation.script_fault = world.owner_script_fault.take();
                return Err((phase, tick, "runtime lost during owner egress"));
            }
            observation.owner_delta_ack |=
                owner_baseline(&world).is_some_and(|last| last > owner_full);
            observation.completed_frames += 1;
        }
        observation.completed_phases.push(phase);
    }
    if !observation.owner_delta_ack {
        return Err(("owner delta", tick, "pending acknowledgement"));
    }
    Ok(())
}

#[test]
#[ignore = "requires a prepared, tagged 104319 Practice Range runtime"]
fn prepared_practice_catalog_observes_input_survival_and_owner_egress() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let mut admitted = 0;
    let mut completed = 0;
    let mut total = 0;
    let mut soldier_passed = false;
    for hero in assets.heroes().heroes() {
        total += 1;
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        let mut observation = InputObservation::default();
        let result =
            observe_prepared_actor_input(&mut scene, hero.guid, &mut observation, WEAPON_PHASES);
        admitted += usize::from(observation.admitted);
        completed += usize::from(result.is_ok());
        if hero.guid == SOLDIER {
            soldier_passed = result.is_ok() && observation.hitscan > 0 && observation.launches > 0;
        }
        let outcome = match result {
            Ok(()) => "completed".to_owned(),
            Err((stage, frame, reason)) => format!("{stage} at {frame} ({reason})"),
        };
        eprintln!(
            "{}: {outcome}, admitted={}, frames={}, owner_full_ack={}, owner_delta_ack={}, hitscan={}, launches={}",
            hero.name,
            observation.admitted,
            observation.completed_frames,
            observation.owner_full_ack,
            observation.owner_delta_ack,
            observation.hitscan,
            observation.launches,
        );
    }
    eprintln!(
        "prepared Practice Range input survival: {admitted}/{total} admitted, {completed}/{admitted} completed"
    );
    assert!(admitted >= 11, "catalog admission regressed");
    assert!(
        soldier_passed,
        "expected both weapon outputs from the reference control"
    );
}

#[test]
#[ignore = "requires the local 104319 stock Practice Range runtime"]
fn prepared_stock_secondary_projectile_launches_from_a_fresh_body() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let hero = assets
        .heroes()
        .heroes()
        .find(|hero| hero.name == "Zarya")
        .unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    let mut observation = InputObservation::default();
    let phases: &[InputPhase] = &[
        ("idle", 12, 0, 0),
        ("secondary", 24, 0, SECONDARY),
        ("settle", 8, 0, 0),
    ];
    let result = observe_prepared_actor_input(&mut scene, hero.guid, &mut observation, phases);
    assert!(
        result.is_ok() && observation.launches > 0,
        "right-click replay {result:?}, launches={}, fault={:?}",
        observation.launches,
        observation
            .script_fault
            .as_ref()
            .map(|fault| fault.error.to_string()),
    );
}

fn script_fault_record(fault: OwnerScriptFault, shapes: Option<&GraphStore>) -> Json {
    let diagnostics = match fault
        .error
        .get_ref()
        .and_then(|error| error.downcast_ref::<SnapshotError>())
    {
        Some(SnapshotError::Incomplete { diagnostics, .. }) => diagnostics
            .iter()
            .map(|diagnostic| fault_shapes::diagnostic_record(diagnostic, shapes))
            .collect(),
        _ => Vec::new(),
    };
    json!({
        "step": fault.step.map_or_else(|| "owner egress".to_owned(), |step| format!("{step:?}")),
        "entity": fault.id.entity,
        "generation": fault.id.generation,
        "frame": fault.frame,
        "kind": format!("{:?}", fault.error.kind()),
        "message": fault.error.to_string(),
        "diagnostics": diagnostics,
    })
}

#[test]
#[ignore = "requires a prepared Practice Range pack and a private report path"]
fn prepared_practice_actor_input_matrix() {
    use std::{env, fs::File, path::PathBuf};

    let Some(path) = env::var_os("ORPHEUS_ACTOR_INPUT_REPORT").map(PathBuf::from) else {
        return;
    };
    assert!(!path.try_exists().unwrap(), "input report must be new");
    let runtime = env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let assets = SceneAssets::load(runtime).unwrap();
    let shapes = env::var("ORPHEUS_ACTOR_INPUT_NODE_SHAPES")
        .is_ok_and(|setting| setting == "1")
        .then(|| assets.graphs());
    let mut rows = Vec::new();
    for hero in assets.heroes().heroes() {
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        let mut observation = InputObservation::default();
        let result = observe_prepared_actor_input(
            &mut scene,
            hero.guid,
            &mut observation,
            WEAPON_PHASES.iter().chain(ABILITY_PHASES),
        );
        let failure = result.err().map(
            |(phase, frame, reason)| json!({"phase": phase, "frame": frame, "reason": reason}),
        );
        rows.push(json!({
            "hero": hero.name,
            "admitted": observation.admitted,
            "owner_full_ack": observation.owner_full_ack,
            "owner_delta_ack": observation.owner_delta_ack,
            "completed_frames": observation.completed_frames,
            "completed_phases": observation.completed_phases,
            "hitscan": observation.hitscan,
            "launches": observation.launches,
            "failure": failure,
            "script_fault": observation.script_fault.map(|fault| script_fault_record(fault, shapes)),
        }));
    }
    let schedule: Vec<_> = WEAPON_PHASES
        .iter()
        .chain(ABILITY_PHASES)
        .map(|(phase, frames, forward, buttons)| {
            json!({
                "phase": phase,
                "frames": frames,
                "forward": forward,
                "buttons": buttons,
            })
        })
        .collect();
    let report = json!({
        "schema_version": 1,
        "source_revision": env::var("ORPHEUS_ACTOR_INPUT_REVISION").ok(),
        "schedule": schedule,
        "actors": rows,
    });
    let file = File::options()
        .create_new(true)
        .write(true)
        .open(path)
        .unwrap();
    serde_json::to_writer_pretty(file, &report).unwrap();
    let actors = report["actors"].as_array().unwrap();
    assert_eq!(actors.len(), 32, "expected the full prepared catalog");
}
