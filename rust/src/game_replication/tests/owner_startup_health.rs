use super::*;

fn records(receipt: &DeliveryReport<Arc<Record>>) -> impl Iterator<Item = &Arc<Record>> {
    receipt.packets.iter().flat_map(|packet| &packet.records)
}

fn send(
    world: &mut WorldDelivery,
    scene: &mut PreparedScene,
    egress: &mut GameEgress<Arc<Record>>,
    tick: u32,
    sequence: &mut u32,
) {
    *sequence += 1;
    transmit(world, Some(scene.simulation_mut()), egress, tick, *sequence);
}

#[test]
fn a_lost_initial_owner_delta_holds_only_newer_pools_for_its_body() {
    use crate::world::health;
    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/owner-startup-health-v1.json"
    ))
    .unwrap();
    let (factory, spawn) = crate::script_player::fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let _ = runtime.take_health_update();
    let base = runtime.vitals().unwrap().total();
    let id = BodyId {
        entity: spawn.entity,
        generation: 1,
    };
    let active = body(id);
    let mut room = RoomSimulation::new(vec![]).unwrap();
    let mut world = WorldDelivery::new(spawn.controller);
    world.started = Some(spawn.frame);
    world.body = Some(active);
    world.bound = Some(id);
    world
        .bodies
        .insert(id.entity, (id.generation, Lifetime::Live));
    world.created.insert(id.entity);
    let mut script = BodyScript::new(active, spawn.frame, None, &runtime).unwrap();
    script.stream.create_arrived();
    world.body_script = Some(script);
    room.spawn_player_body(PlayerBodyState::new(
        id,
        spawn.controller,
        0,
        0,
        active.hero,
        empty_mover(),
        Some(Box::new(runtime)),
        spawn.frame,
        None,
    ))
    .unwrap();
    let mut egress = GameEgress::default();
    transmit(&mut world, Some(&mut room), &mut egress, spawn.frame, 1);
    transport_outcome(&mut world, &mut egress, 1, true);
    transmit(&mut world, Some(&mut room), &mut egress, spawn.frame + 1, 2);
    let lost = egress.acknowledge(3, 0);
    assert!(
        lost.packets[0]
            .records
            .iter()
            .any(|r| matches!(r.delivery, Delivery::BodyScript { delta: true, .. }))
    );
    world.acknowledge(lost, Duration::ZERO);

    let other = id.entity + 1;
    world.bots.insert(other, BotLifetime::Live);
    world.created.insert(other);
    let hp = contract["input"]["other_body_health"].as_f64().unwrap() as f32;
    world.queue_health(EntityUpdate {
        entity: other,
        operation: Some(Operation::Update(vec![health(hp, hp, 0.0, 0.0)])),
        ..Default::default()
    });
    for amount in contract["input"]["damage"].as_array().unwrap() {
        let rt = room.player_body_mut(id).unwrap().runtime.as_mut().unwrap();
        let amount = amount.as_f64().unwrap();
        assert_eq!(
            rt.receive_damage(
                amount,
                crate::vitals::Death {
                    time: 0.0,
                    frame: spawn.frame,
                    killer: other
                },
                &crate::script_init::HitTags::EMPTY
            )
            .dealt,
            amount
        );
        world.queue_health(EntityUpdate {
            entity: id.entity,
            operation: Some(Operation::Update(vec![rt.take_health_update().unwrap()])),
            ..Default::default()
        });
    }
    let pools_ready = |world: &WorldDelivery, entity: u32| {
        world.ready().iter().any(|it| {
            it.record.update.entity == entity
                && matches!(it.record.delivery, Delivery::Health { .. })
        })
    };
    assert!(!pools_ready(&world, id.entity));
    assert_eq!(
        !pools_ready(&world, other),
        contract["expected"]["other_body_blocked"]
            .as_bool()
            .unwrap()
    );
    transmit(&mut world, Some(&mut room), &mut egress, spawn.frame + 2, 3);
    transport_outcome(&mut world, &mut egress, 3, true);
    assert!(pools_ready(&world, id.entity));
    let current = room.player_body(id).unwrap().vitals().unwrap().total();
    assert_eq!(
        base - current,
        contract["expected"]["damage_after_startup"]
            .as_f64()
            .unwrap()
    );
}

#[test]
#[ignore = "requires the prepared 104319 Practice Range"]
fn prepared_shield_cast_waits_for_the_frozen_owner_baseline_before_sending_new_pools() {
    use crate::game_instance::TeamMemberCounts;
    use crate::script_init::{HUD_HEALTH, HUD_MAX_HEALTH};
    use crate::script_wire::Value as ScriptValue;

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
    let mut tick = FIRST_TICK;
    scene
        .simulation_mut()
        .set_team_member_counts(TeamMemberCounts::from_teams(false, [0]));
    scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(tick);
    world
        .switch(
            &mut scene,
            &viewer,
            tick,
            RETAIL_QUANTA,
            OWNER | 1,
            (&hero, SkinTheme::default()),
        )
        .unwrap();
    let id = world.active_body_id().unwrap();
    let mut egress = GameEgress::default();
    let mut seq = 0;
    let (lost, frozen) = loop {
        assert!(seq < 24, "baseline was not sent");
        send(&mut world, &mut scene, &mut egress, tick, &mut seq);
        // Hold the first script packet; acknowledge earlier body creation normally.
        let is_baseline = world.body_script.as_ref().unwrap().stream.last() == 1;
        let receipt = egress.acknowledge(seq + 1, u64::from(!is_baseline));
        let frozen = records(&receipt).find(|r| matches!(
            r.delivery, Delivery::BodyScript { id: target, delta: false, .. } if target == id
        )).cloned();
        if let Some(record) = frozen {
            break (receipt, record);
        }
        world.acknowledge(receipt, Duration::ZERO);
        tick += 1;
    };
    let script = world.body_script.as_ref().unwrap();
    assert!(script.stream.base().is_none());
    let full = script.stream.pending().next().unwrap().1;
    let vars = full.entity_variables();
    let (ScriptValue::Float(base), ScriptValue::Float(base_max)) =
        (&vars[&HUD_HEALTH], &vars[&HUD_MAX_HEALTH])
    else {
        panic!("missing baseline HUD")
    };
    let before = (*base, *base_max);
    for step in 0..34 {
        tick += 1;
        scene.simulation_mut().prepare_spatial_frame(tick).unwrap();
        world.queue_commands(
            tick,
            &[Command {
                frame: tick,
                buttons: if step == 30 { 0x10 } else { 0 },
                ..Default::default()
            }],
        );
        world
            .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 16)
            .unwrap();
    }
    let body = scene.simulation().player_body(id).unwrap();
    let vitals = body.vitals().unwrap();
    let after = (vitals.total(), vitals.maximum());
    let current_pools = Some(Operation::Update(vec![vitals.cumulative_component()]));
    assert!(
        after.0 > before.0 && after.1 > before.1,
        "authored E must grant shield before H0 ACK"
    );
    // The original datagram is still outstanding at this point. A new
    // component in this send can precede the frozen baseline's retry.
    send(&mut world, &mut scene, &mut egress, tick, &mut seq);
    let between = egress.acknowledge(seq + 1, 1);
    assert!(
        !records(&between).any(|r| {
            r.update.entity == id.entity && matches!(r.delivery, Delivery::Health { .. })
        }),
        "unacknowledged baseline HUD {before:?} let newer pools {after:?} through"
    );
    world.acknowledge(between, Duration::ZERO);
    world.acknowledge(lost, Duration::ZERO);
    let mut retried = false;
    for _ in 0..8 {
        send(&mut world, &mut scene, &mut egress, tick, &mut seq);
        let receipt = egress.acknowledge(seq + 1, 1);
        for record in records(&receipt) {
            assert!(
                !(record.update.entity == id.entity
                    && matches!(record.delivery, Delivery::Health { .. })),
                "new pool revision {after:?} overtook frozen HUD {before:?}"
            );
            retried |= Arc::ptr_eq(record, &frozen);
        }
        world.acknowledge(receipt, Duration::ZERO);
        if retried {
            break;
        }
    }
    assert!(retried, "missing frozen H0 retry");
    assert!(world.health_pending(id.entity));
    let mut sent_health = false;
    for _ in 0..12 {
        send(&mut world, &mut scene, &mut egress, tick, &mut seq);
        let receipt = egress.acknowledge(seq + 1, 1);
        for record in records(&receipt).filter(|r| {
            r.update.entity == id.entity && matches!(r.delivery, Delivery::Health { .. })
        }) {
            assert_eq!(record.update.operation, current_pools);
            sent_health = true;
        }
        world.acknowledge(receipt, Duration::ZERO);
        if sent_health
            && world
                .body_script
                .as_ref()
                .unwrap()
                .stream
                .base()
                .is_some_and(|(_, s)| {
                    s.entity_variables().get(&HUD_HEALTH) == Some(&ScriptValue::Float(after.0))
                })
        {
            break;
        }
        tick += 1;
    }
    assert!(sent_health);
    let acknowledged = world
        .body_script
        .as_ref()
        .unwrap()
        .stream
        .base()
        .unwrap()
        .1
        .entity_variables();
    assert_eq!(acknowledged[&HUD_HEALTH], ScriptValue::Float(after.0));
    assert_eq!(acknowledged[&HUD_MAX_HEALTH], ScriptValue::Float(after.1));
}
