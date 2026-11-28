use super::*;
use crate::{
    game_clock::FIRST_TICK,
    game_combat::{PlayerBodyState, RoomSimulation},
    game_content::GameMap,
    game_replication::BodyId,
    scene_assets::SceneAssets,
    script_graph::RoomSpatialNeeds,
    script_init::{HUD_HEALTH, HUD_MAX_HEALTH},
    vitals::Death,
};

fn owner_hud(body: &PlayerBodyRuntime) -> (f64, f64) {
    let snapshot = body.snapshot().unwrap();
    let owner = body.encoder().project(snapshot, View::Owner).unwrap();
    let read = |key| match owner.entity_variables().get(&key) {
        Some(Value::Float(value)) => *value,
        other => panic!("missing numeric owner HUD variable {key}: {other:?}"),
    };
    (read(HUD_HEALTH), read(HUD_MAX_HEALTH))
}

#[test]
fn named_shield_and_owner_hud_follow_the_same_vitals_revision() {
    let (factory, spawn) = fixture_body_factory();
    let mut body = factory.start(spawn).unwrap();
    let base = owner_hud(&body);
    let pool = body
        .world
        .vitals
        .add_temporary_shield(0x0d80_0000_0000_4567, 38.0, false)
        .unwrap();
    assert!(body.take_health_update().is_some());
    assert_eq!(owner_hud(&body), (base.0 + 38.0, base.1 + 38.0));
    assert!(body.take_health_update().is_none());
    let hit = Death {
        time: 0.0,
        frame: spawn.frame,
        killer: spawn.entity + 1,
    };
    assert_eq!(
        body.receive_damage(12.0, hit, &crate::script_init::HitTags::EMPTY)
            .dealt,
        12.0
    );
    assert!(body.take_health_update().is_some());
    assert_eq!(owner_hud(&body), (base.0 + 26.0, base.1 + 38.0));
    assert_eq!(body.world.vitals.remove_pool(pool), Some(26.0));
    assert!(body.take_health_update().is_some());
    assert_eq!(owner_hud(&body), base);
    assert!(body.take_health_update().is_none());
}

#[test]
fn owner_shield_wire_repeats_current_values_across_loss_and_expiry() {
    use crate::bits::BitReader;
    use crate::script_stream::{BodyStream, DeltaKind, OWNER_SETTLE_FRAMES, ScriptChunk};

    // Read the entity variable list in a correction with no changes to instance
    // descriptors. Only the two HUD floats change on this synthetic body.
    fn hud_writes(chunk: &ScriptChunk) -> BTreeMap<u32, f64> {
        let wire = chunk.update.chunk.as_ref().unwrap();
        let mut bits = BitReader::with_range(wire.as_bytes(), 0, wire.bit_len()).unwrap();
        let first = bits.w_u32().unwrap();
        assert_eq!(first + bits.w_var().unwrap(), chunk.last);
        assert!(!bits.bit().unwrap());
        let size = bits.w_var().unwrap() as usize;
        assert_eq!(size, bits.left());
        assert_eq!(bits.bits(3).unwrap(), 6); // H=0, owner, correction
        assert_ne!(bits.w_var().unwrap(), 0);
        assert!(bits.w_var().unwrap() as usize <= bits.left());
        assert_eq!(bits.w_u16().unwrap(), 0);
        let mut values = BTreeMap::new();
        if bits.bit().unwrap() {
            loop {
                let id = bits.w_var().unwrap();
                if id == 0 {
                    break;
                }
                let value = match bits.bits(4).unwrap() {
                    5 => bits.bits(4).unwrap() as f64,
                    6 => bits.signed(8).unwrap() as f64,
                    8 => f64::from(bits.f32().unwrap()),
                    other => panic!("unexpected HUD value type {other}"),
                };
                assert!(!bits.bit().unwrap()); // no stack binding
                assert!(values.insert(id, value).is_none());
            }
        }
        values
    }

    fn health_wire(body: &mut PlayerBodyRuntime) -> (f64, f64) {
        let component = body.take_health_update().unwrap();
        let wire = crate::world::update(0, &[component]).unwrap();
        let bytes = wire.as_bytes();
        assert_eq!(&bytes[..2], &[1, 51]); // one health-pool component
        let mask_end = 3 + usize::from(bytes[2]);
        let mask = &bytes[3..mask_end];
        let pools = &bytes[mask_end..];
        assert_eq!(
            pools.len(),
            20 * mask.iter().map(|b| b.count_ones() as usize).sum::<usize>()
        );
        let mut totals = (0.0, 0.0);
        for pool in pools.chunks_exact(20) {
            totals.0 += f64::from(f32::from_le_bytes(pool[8..12].try_into().unwrap()));
            totals.1 += f64::from(f32::from_le_bytes(pool[4..8].try_into().unwrap()));
        }
        totals
    }

    let (factory, spawn) = fixture_body_factory();
    let mut body = factory.start(spawn).unwrap();
    let base = owner_hud(&body);
    let mut stream = BodyStream::new(spawn.entity);
    stream.create_arrived();
    let encoder = body.encoder();
    let projected = encoder
        .project(body.snapshot().unwrap(), View::Owner)
        .unwrap();
    let full = encoder.queue_full(&mut stream, &projected).unwrap();
    stream.sent(full.last);
    assert!(stream.arrived(full.last).advanced);
    let initial = encoder
        .queue_delta(
            &mut stream,
            &projected,
            DeltaKind::InitialOwner(spawn.frame),
            16,
        )
        .unwrap()
        .unwrap();
    stream.sent(initial.last);
    assert!(stream.arrived(initial.last).advanced);

    let mut frame = spawn.frame + OWNER_SETTLE_FRAMES;
    let mut next = |body: &PlayerBodyRuntime, stream: &mut BodyStream<_>| {
        let encoder = body.encoder();
        let view = encoder
            .project_next(body.snapshot().unwrap(), stream.base().unwrap().1)
            .unwrap();
        let chunk = encoder
            .queue_delta(stream, &view, DeltaKind::Correction(frame), 16)
            .unwrap()
            .unwrap();
        frame += 1;
        stream.sent(chunk.last).unwrap();
        chunk
    };
    let expected =
        |(current, maximum)| BTreeMap::from([(HUD_HEALTH, current), (HUD_MAX_HEALTH, maximum)]);
    let grant = body
        .world
        .vitals
        .add_temporary_shield(0x0d80_0000_0000_4567, 38.0, false)
        .unwrap();
    let granted = health_wire(&mut body);
    assert_eq!(granted, (base.0 + 38.0, base.1 + 38.0));
    let cast = next(&body, &mut stream);
    assert_eq!(hud_writes(&cast), expected(granted));
    body.receive_damage(
        12.0,
        Death {
            time: 0.0,
            frame: spawn.frame,
            killer: spawn.entity + 1,
        },
        &crate::script_init::HitTags::EMPTY,
    );
    let hit = health_wire(&mut body);
    assert_eq!(hit, (granted.0 - 12.0, granted.1));
    let damaged = next(&body, &mut stream);
    assert_eq!(hud_writes(&damaged), expected(hit));
    assert!(!stream.lost(damaged.last).retry);
    let retry = next(&body, &mut stream);
    assert_eq!(hud_writes(&retry), expected(hit));

    // A -> B -> A before any receipt: an omitted value would leave a
    // client that received B showing a shield after its removal.
    body.world.vitals.remove_pool(grant).unwrap();
    assert_eq!(health_wire(&mut body), base);
    let expiry = next(&body, &mut stream);
    assert_eq!(hud_writes(&expiry), expected(base));
    assert!(stream.arrived(expiry.last).advanced);
    assert!(!stream.arrived(cast.last).advanced);
    assert!(!stream.arrived(retry.last).advanced);
    let settled = next(&body, &mut stream);
    assert!(hud_writes(&settled).is_empty());

    let id = spawn.entity + 1;
    let replacement = factory
        .start(OwnerBodySpawn {
            entity: id,
            ..spawn
        })
        .unwrap();
    stream.retire();
    assert!(!stream.arrived(settled.last).advanced);
    let new_encoder = replacement.encoder();
    let new_view = new_encoder
        .project(replacement.snapshot().unwrap(), View::Owner)
        .unwrap();
    let first = new_encoder
        .queue_full(&mut BodyStream::new(id), &new_view)
        .unwrap();
    assert_eq!((first.update.entity, first.last), (id, 1));
    assert_eq!(owner_hud(&replacement), base);
}

#[test]
#[ignore = "requires separately prepared 104319 runtime"]
fn prepared_adaptive_shield_latches_the_count_used_by_its_health_pool() {
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&root).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = assets
        .heroes()
        .get(crate::game_heroes::WRECKING_BALL)
        .unwrap();
    let factory = assets.compile_owner_factory(&scene, hero).unwrap();
    let mover = scene.player_spawn_mover(hero.body, 0).unwrap();
    let pose = mover.pose();
    let spawn = OwnerBodySpawn {
        controller: 0xa000_1001,
        entity: 0xa000_2001,
        body_filter: crate::match_scene::team_bit(0).unwrap(),
        slot: 0,
        frame: FIRST_TICK,
        pose,
        eye_position: Some([
            pose.position[0],
            pose.position[1] + eye_height(hero.guid, false),
            pose.position[2],
        ]),
        quanta: RETAIL_QUANTA,
        skin: 0,
    };
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.enable_spatial_queries(false, scene.test_combat_rays(), RoomSpatialNeeds::Queries);
    let empty = room.capture_spatial_frame(FIRST_TICK).unwrap().unwrap();
    let enemy = BodyId {
        entity: spawn.entity + 1,
        generation: 1,
    };
    let (enemy_factory, enemy_spawn) = fixture_body_factory();
    let enemy_runtime = enemy_factory
        .start(OwnerBodySpawn {
            entity: enemy.entity,
            ..enemy_spawn
        })
        .unwrap();
    room.spawn_player_body(PlayerBodyState::new(
        enemy,
        spawn.controller + 1,
        1,
        1,
        enemy_factory.hero,
        scene.player_spawn_mover(hero.body, 0).unwrap(),
        Some(Box::new(enemy_runtime)),
        FIRST_TICK + 1,
        None,
    ))
    .unwrap();
    let occupied = room.capture_spatial_frame(FIRST_TICK + 1).unwrap().unwrap();
    let mut body = factory
        .start_with_setup(
            spawn,
            OwnerBodySetup {
                mover_settings: *crate::mover_settings::MoverCatalog::load(
                    root.join("data/game_movers_174.json"),
                )
                .unwrap()
                .authored_for_body(hero.body)
                .unwrap(),
                spatial_frame: Some(Arc::clone(&empty)),
                team_member_counts: Some(TeamMemberCounts::from_teams(false, [0, 1])),
                room_activity: Some(scene.owner_room_activity()),
            },
        )
        .unwrap();
    let observe = |body: &PlayerBodyRuntime, variable: u32| {
        let snapshot = body.snapshot().unwrap();
        let (&instance, graph) = snapshot
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == 0x1566)
            .unwrap();
        let value = graph.variables.get(&variable);
        let encoder = body.encoder();
        let owner = encoder.project(snapshot, View::Owner).unwrap();
        assert_eq!(
            owner.instance_variables(instance).unwrap().get(&variable),
            value
        );
        encoder.full(&owner).unwrap();
        value.map(to_float)
    };
    let mut frame = FIRST_TICK;
    let mut advance = |body: &mut PlayerBodyRuntime, buttons| {
        frame += 1;
        body.advance(
            Command {
                frame,
                buttons,
                ..Command::default()
            },
            &mover,
            BodyStep::Posed,
        )
        .unwrap();
        body.take_health_update();
        let vitals = &body.world.vitals;
        let snapshot = body.snapshot().unwrap();
        for (key, actual) in [
            (HUD_HEALTH, vitals.total()),
            (HUD_MAX_HEALTH, vitals.maximum()),
        ] {
            assert_eq!(
                snapshot.variables.get(&key),
                Some(&Value::Float(actual)),
                "frame {frame}, entity variable {key}"
            );
        }
    };
    for _ in 0..25 {
        advance(&mut body, 0);
    }
    assert_eq!(observe(&body, 11), Some(0.0), "no enemy in range");
    body.set_spatial_frame(occupied);
    for _ in 0..5 {
        advance(&mut body, 0);
    }
    assert_eq!(
        observe(&body, 11),
        Some(1.0),
        "enemy in range before activation"
    );
    let base = owner_hud(&body);
    advance(&mut body, 0x10);
    assert_eq!(
        observe(&body, 11398),
        Some(1.0),
        "count sampled on ability activation"
    );
    body.set_spatial_frame(empty);
    for _ in 0..60 {
        advance(&mut body, 0);
    }
    // The authored switch disables tracking while the shield's active flag is held.
    // The saved cast count and granted amount therefore survive the room change.
    assert_eq!(observe(&body, 11398), Some(1.0), "cast count stays frozen");
    let granted = 100.0 + 75.0;
    assert_eq!(
        body.world
            .vitals
            .temporary_shield_current(0x0d80_0000_0000_3ebb),
        granted
    );
    assert_eq!(owner_hud(&body), (base.0 + granted, base.1 + granted));
    for _ in 0..600 {
        advance(&mut body, 0);
    }
    assert_eq!(
        observe(&body, 11),
        Some(0.0),
        "tracking resumes after expiry"
    );
    assert_eq!(owner_hud(&body), base);
}
