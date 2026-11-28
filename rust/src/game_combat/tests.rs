use super::*;
use crate::{
    game_content::GameMap, game_heroes::SOLDIER, game_instance::RETAIL_QUANTA,
    scene_assets::SceneAssets,
};

const SCRIPT_RAY_SITE: crate::script_init::Site = crate::script_init::Site {
    entity: 91,
    instance: 2,
    graph: 93,
    node: Some(4),
};

pub(super) fn fixture_bots() -> Vec<Bot> {
    crate::game_bots::BotCatalog::from_json(
        r#"{
        "heroes":{"0x01":{"name":"Test bot","body":"0x02","health":100}},
        "maps":{"0x03":{"hints":{},"spawners":[
            {"hero":"0x01","team":1,"position":[1,0,0],"rotation":[0,0,0,1],"graphs":[]},
            {"hero":"0x01","team":1,"position":[2,0,0],"rotation":[0,0,0,1],"graphs":[]}
        ]}}
    }"#,
    )
    .unwrap()
    .make(3, |_| crate::game_bots::BotWalk::default())
}

#[test]
fn combat_target_admission_applies_team_and_liveness_rules_to_bots() {
    let mut bot = fixture_bots().remove(0);
    let shooter = bot.entity + 1;
    let allied_team = bot.spawn.team;
    let hostile_team = 0;
    assert_ne!(allied_team, hostile_team);

    assert!(eligible_bot(&bot, shooter, hostile_team, false));
    assert!(!eligible_bot(&bot, shooter, allied_team, false));
    assert!(eligible_bot(&bot, shooter, allied_team, true));
    assert!(!eligible_bot(&bot, bot.entity, allied_team, true));

    bot.dead = true;
    assert!(!eligible_bot(&bot, shooter, hostile_team, false));
    assert!(!eligible_bot(&bot, shooter, hostile_team, true));
}

#[test]
fn autonomous_pet_movement_remains_separate_from_reliable_health() {
    use crate::{
        game_egress::{GameEgress, RecordSource},
        game_heroes::HeroCatalog,
        game_pools::BODY_GRAPH,
        game_replication::WorldDelivery,
        match_scene::{PlayerAppearance, PlayerCard},
        scene_assets::PreparedScene,
        script_graph::GraphStore,
    };
    use serde_json::{Value, json};

    let fixture: Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/room-pet-movement-delivery-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let input = &fixture["input"];
    let definition = input["definition"].as_str().unwrap();
    let definition_id = crate::accounts::parse_guid(definition).unwrap();
    let max_health = input["health"].as_f64().unwrap();
    let damage = input["damage"].as_f64().unwrap();
    let spawn_tick = u32::try_from(input["spawn_tick"].as_u64().unwrap()).unwrap();
    let spawn_position: [f64; 3] = serde_json::from_value(input["position"].clone()).unwrap();
    let graphs = json!({
        "version": 1, "missing": [],
        "graphs": [{
            "index": BODY_GRAPH, "items": 0, "nodes": [], "states": [],
            "entries": [], "remote": [], "sync_vars": [], "containers": {},
            "unknown_nodes": [],
            "header": {
                "m_nodesBitCount": 1, "m_statesBitCount": 1,
                "m_remoteSyncNodesBitCount": 1, "m_syncVarsBitCount": 1
            },
            "fields": {"m_publicSchema": {"m_entries": [{
                "m_0D09D2D9": "0x0D800000000000C7",
                "m_value": {"m_value": max_health}
            }]}}
        }],
        "bodies": {(definition): {
            "name": "Synthetic moving pet", "manager": null,
            "graphs": [{"graph": BODY_GRAPH, "m_75408640": 0}], "weapons": []
        }}
    });
    let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&graphs).unwrap()).unwrap());
    let mut scene = PreparedScene::fixture_for_owner_admission(HeroCatalog::default());
    let source = 0xa000_0201;
    let observer_id = 0xa000_0300;
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: observer_id,
        slot: 0,
        team: 0,
        card: &card,
    };
    let mut observer = WorldDelivery::new(observer_id);
    observer
        .start(&scene, &viewer, spawn_tick - BOT_RECORD_DELAY)
        .unwrap();
    let pet = next_pet_wire_id();
    let mode = scene.map().mode_guid;
    let room = scene.simulation_mut();
    room.enable_spatial_queries(false, Arc::new(fixture_ground()), RoomSpatialNeeds::Queries);
    room.set_script_graphs(graphs, mode);
    room.spawn_room_pet(
        source,
        1,
        pet,
        definition_id,
        None,
        spawn_position,
        0,
        None,
        None,
        spawn_tick,
    );
    let [create]: [EntityUpdate; 1] = room.take_created().try_into().unwrap();
    observer.create_bot(create);

    let pose_tick = spawn_tick + 2;
    room.prepare_spatial_frame(pose_tick).unwrap();
    room.advance_deployables(pose_tick);
    let position = room.room_pets[&pet].position;
    assert!(position[2] > spawn_position[2], "{position:?}");
    assert!(room.take_scripted_health().is_empty());
    assert_eq!(
        room.damage_barrier_or_deployable(
            pet,
            true,
            damage,
            observer_id,
            pose_tick,
            Duration::ZERO
        ),
        damage
    );
    let [health]: [EntityUpdate; 1] = room.take_scripted_health().try_into().unwrap();
    let vitals = room.room_pets[&pet].vitals.as_ref().unwrap();
    assert_eq!(vitals.total(), max_health - damage);
    assert_eq!(health.entity, pet);
    assert_eq!(
        health.operation,
        Some(Operation::Update(vec![vitals.component()]))
    );
    assert!(health.movement.is_none());
    observer.queue_health(health);
    assert!(!observer.identity_ready(pet));
    assert!(observer.movement(&scene, pose_tick).unwrap().is_empty());

    let mut egress = GameEgress::default();
    for sequence in 1..=8 {
        observer
            .send_frame(pose_tick, vec![], false, None, &mut egress, |_| {
                Ok::<_, io::Error>(sequence)
            })
            .unwrap();
        observer.acknowledge(egress.acknowledge(sequence + 1, 1), Duration::ZERO);
        if observer.identity_ready(pet) {
            break;
        }
    }
    assert!(observer.identity_ready(pet));
    assert!(observer.health_pending(pet));
    let [pose]: [EntityUpdate; 1] = observer
        .movement(&scene, pose_tick)
        .unwrap()
        .try_into()
        .unwrap();
    assert_eq!(pose.entity, pet);
    assert!(pose.operation.is_none());
    assert_eq!(pose.movement.unwrap().position, position);
    let sent = observer
        .send_frame(pose_tick, vec![pose], false, None, &mut egress, |_| {
            Ok::<_, io::Error>(9)
        })
        .unwrap();
    assert_eq!(
        sent.records,
        [RecordSource::Queued(0), RecordSource::Movement(0)]
    );
}

#[test]
fn spatial_candidates_capture_living_room_bodies_once_per_frame() {
    check_spatial_candidates_frame(false);
    check_spatial_candidates_frame(true);
}

#[test]
fn frozen_view_query_only_reports_empty_when_no_other_actor_can_match() {
    use crate::{
        contact_world::ContactWorld, mover::Mover, mover_settings::MoverSettings,
        script_init::ViewTargetQuery,
    };

    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.enable_spatial_queries(false, Arc::new(fixture_ground()), RoomSpatialNeeds::Queries);
    let query = ViewTargetQuery {
        source: 9,
        offset: [0.0; 3],
        eye_heading: None,
        range: 10.0,
        aperture: 22.0,
        limit: 4,
        profile: 1,
        masks: [1, 0, 16],
        ranking: &serde_json::Value::Null,
        sight: None,
    };
    let frame = FIXTURE_CONTEXT.tick;
    let snapshot = room.capture_spatial_frame(frame).unwrap().unwrap();
    assert_eq!(snapshot.empty_view_targets(&query), None);
    for (entity, slot) in [(9, 0), (11, 1)] {
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            [4.0, 0.0, 2.0],
            0,
            0x08,
        )
        .unwrap();
        room.spawn_player_body(PlayerBodyState::new(
            BodyId {
                entity,
                generation: 1,
            },
            entity + 100,
            slot,
            0,
            1,
            mover,
            None,
            frame,
            None,
        ))
        .unwrap();
        let snapshot = room.capture_spatial_frame(frame).unwrap().unwrap();
        let expected = (slot == 0).then(Vec::new);
        assert_eq!(snapshot.empty_view_targets(&query), expected);
    }

    // Bots are also valid view targets (e.g. Mercy/Zen/Sombra/Zarya on Practice Range bots),
    // so a solo player in a room with living bots must not short-circuit to an empty result.
    let mut bot_room = RoomSimulation::new(fixture_bots()).unwrap();
    bot_room.enable_spatial_queries(false, Arc::new(fixture_ground()), RoomSpatialNeeds::Queries);
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        [4.0, 0.0, 2.0],
        0,
        0x08,
    )
    .unwrap();
    bot_room
        .spawn_player_body(PlayerBodyState::new(
            BodyId {
                entity: 9,
                generation: 1,
            },
            109,
            0,
            0,
            1,
            mover,
            None,
            frame,
            None,
        ))
        .unwrap();
    let bot_snapshot = bot_room.capture_spatial_frame(frame).unwrap().unwrap();
    assert_eq!(bot_snapshot.empty_view_targets(&query), None);
}

fn check_spatial_candidates_frame(free_for_all: bool) {
    use crate::{contact_world::ContactWorld, mover::Mover, mover_settings::MoverSettings};

    let bots = fixture_bots();
    let moving_bot = bots[0].entity;
    let initial_bot_position = bots[0].position;
    let dying_bot = bots[1].entity;
    let mut room = RoomSimulation::new(bots).unwrap();
    let frame = FIXTURE_CONTEXT.tick;
    let player = BodyId {
        entity: 9,
        generation: 1,
    };
    let player_position = [4.0, 0.0, 2.0];
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        player_position,
        0,
        0x08,
    )
    .unwrap();
    room.spawn_player_body(PlayerBodyState::new(
        player, 8, 0, 0, 1, mover, None, frame, None,
    ))
    .unwrap();

    room.prepare_spatial_frame(frame).unwrap();
    assert!(room.spatial_frame.is_none());

    room.enable_spatial_queries(
        free_for_all,
        Arc::new(fixture_ground()),
        crate::script_graph::RoomSpatialNeeds::EntityTags,
    );
    room.prepare_spatial_frame(frame).unwrap();
    let (captured, initial) = room.spatial_frame.as_ref().unwrap();
    assert_eq!(*captured, frame);
    assert_eq!(initial.candidates().len(), 3);
    assert!(!RoomScriptRequests::default().queue_health(initial, player.entity, -1.0));
    let bot_team = (!free_for_all).then_some(1);
    assert!(
        initial
            .candidates()
            .iter()
            .any(|row| row.entity == dying_bot && row.team == bot_team)
    );
    let initial_bot = SpatialCandidate {
        entity: moving_bot,
        position: initial_bot_position,
        team: bot_team,
    };
    assert!(initial.candidates().contains(&initial_bot));
    assert!(initial.candidates().contains(&SpatialCandidate {
        entity: player.entity,
        position: player_position,
        team: (!free_for_all).then_some(0),
    }));
    let initial_facts = initial.entity_facts().unwrap();
    assert_eq!(initial_facts.len(), 3);
    assert_eq!(initial_facts[&moving_bot].team, bot_team);
    assert!(initial_facts[&dying_bot].alive);
    assert_eq!(
        initial_facts[&player.entity].team,
        (!free_for_all).then_some(0)
    );
    let initial = Arc::clone(initial);

    let new_position = [10.0, 0.0, -2.0];
    room.body_mut(moving_bot).unwrap().position = new_position;
    room.apply_bot_impact(
        7,
        frame,
        FIXTURE_CONTEXT,
        &fixture_impact(dying_bot, 10000.0),
    )
    .unwrap()
    .unwrap();
    assert!(room.retire_player_body(player));
    room.prepare_spatial_frame(frame).unwrap();
    let (_, same_frame) = room.spatial_frame.as_ref().unwrap();
    assert!(Arc::ptr_eq(same_frame, &initial));

    room.prepare_spatial_frame(frame + 1).unwrap();
    let (captured, next) = room.spatial_frame.as_ref().unwrap();
    assert_eq!(*captured, frame + 1);
    assert_eq!(
        next.candidates(),
        &[SpatialCandidate {
            entity: moving_bot,
            position: new_position,
            team: bot_team,
        }]
    );
    assert!(initial.candidates().contains(&initial_bot));
    let next_facts = next.entity_facts().unwrap();
    assert!(!next_facts[&dying_bot].alive);
    assert_eq!(next_facts[&dying_bot].team, bot_team);
    assert!(!next_facts.contains_key(&player.entity));
    assert!(initial.entity_facts().unwrap()[&dying_bot].alive);
}

#[test]
fn spatial_actor_poses_follow_room_frames_and_body_generations() {
    use crate::{contact_world::ContactWorld, mover::Mover, mover_settings::MoverSettings};

    type Pose = (Vec3, [i32; 2]);

    #[derive(serde::Deserialize)]
    struct Input {
        frame: u32,
        bot_poses: [Pose; 2],
        player_poses: [Pose; 2],
        damage: f64,
    }

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-room-actor-pose-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["case_id"], "script-room-actor-pose.synthetic.v1");
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let Input {
        frame,
        bot_poses,
        player_poses,
        damage,
    } = serde_json::from_value(fixture["input"].clone()).unwrap();
    let player_eyes: [Vec3; 2] =
        serde_json::from_value(fixture["expected"]["player_eye_positions"].clone()).unwrap();

    let bot = fixture_bots().remove(0);
    let bot_wire = bot.entity;
    let mut room = RoomSimulation::new(vec![bot]).unwrap();
    let move_bot = |room: &mut RoomSimulation, (position, angles): Pose, frame| {
        let bot = room.body_mut(bot_wire).unwrap();
        bot.position = position;
        bot.apply_encounter_aim(angles, frame);
    };
    let body = BodyId {
        entity: 71,
        generation: 1,
    };
    let contact = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let spawn = |room: &mut RoomSimulation, id, (position, [yaw, _]): Pose| {
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::clone(&contact),
            position,
            yaw,
            0x08,
        )
        .unwrap();
        room.spawn_player_body(PlayerBodyState::new(
            id, 70, 0, 0, 1, mover, None, frame, None,
        ))
        .unwrap();
    };

    move_bot(&mut room, bot_poses[0], frame);
    spawn(&mut room, body, player_poses[0]);
    room.enable_spatial_queries(
        false,
        Arc::new(fixture_ground()),
        RoomSpatialNeeds::EntityFacts,
    );
    room.prepare_spatial_frame(frame).unwrap();
    let initial = Arc::clone(&room.spatial_frame.as_ref().unwrap().1);
    assert_eq!(initial.actor_pose(bot_wire), Some(bot_poses[0]));
    assert_eq!(initial.actor_eye_position(bot_wire), None);
    assert_eq!(initial.actor_pose(body.entity), Some(player_poses[0]));
    assert_eq!(
        initial.actor_eye_position(body.entity),
        Some(player_eyes[0])
    );
    let old_player = initial.script_targets[&body.entity].entity;

    move_bot(&mut room, bot_poses[1], frame);
    let replacement = BodyId {
        generation: 2,
        ..body
    };
    spawn(&mut room, replacement, player_poses[1]);
    room.prepare_spatial_frame(frame).unwrap();
    let same_frame = &room.spatial_frame.as_ref().unwrap().1;
    assert!(Arc::ptr_eq(&initial, same_frame));
    assert_eq!(same_frame.actor_pose(bot_wire), Some(bot_poses[0]));
    assert_eq!(same_frame.actor_pose(body.entity), Some(player_poses[0]));
    assert_eq!(
        same_frame.actor_eye_position(body.entity),
        Some(player_eyes[0])
    );

    room.prepare_spatial_frame(frame + 1).unwrap();
    let next = &room.spatial_frame.as_ref().unwrap().1;
    assert_eq!(next.actor_pose(bot_wire), Some(bot_poses[1]));
    assert_eq!(next.actor_pose(body.entity), Some(player_poses[1]));
    assert_eq!(next.actor_eye_position(body.entity), Some(player_eyes[1]));
    let new_player = next.script_targets[&body.entity].entity;
    assert_ne!(new_player, old_player);
    assert_eq!(new_player, room.players_by_wire[&body.entity]);

    let death = Death {
        time: 0.0,
        frame: frame + 1,
        killer: 0,
    };
    let state = room.state_mut(bot_wire).unwrap().unwrap();
    assert!(state.vitals.damage(damage, death).killed);
    assert!(room.retire_player_body(replacement));
    room.prepare_spatial_frame(frame + 2).unwrap();
    let terminal = &room.spatial_frame.as_ref().unwrap().1;
    assert_eq!(terminal.actor_pose(bot_wire), None);
    assert_eq!(terminal.actor_pose(body.entity), None);
    assert_eq!(terminal.actor_eye_position(body.entity), None);
    assert!(!terminal.entity_facts().unwrap()[&bot_wire].alive);
}

#[test]
fn owner_startup_spatial_frame_excludes_the_frozen_body_on_each_replacement() {
    use crate::{
        contact_world::ContactWorld,
        game_replication::{BodyStep, OwnerBodyRuntime},
        mover::Mover,
        mover_settings::MoverSettings,
        script_init::Snapshot,
        script_snapshot_wire::SnapshotEncoder,
        vitals::Vitals,
    };
    use std::{io, sync::Mutex};

    struct FrameProbe {
        view: Arc<Mutex<Option<Arc<RoomSpatialFrame>>>>,
        vitals: Option<Vitals>,
    }
    impl OwnerBodyRuntime for FrameProbe {
        fn begin_state(
            &mut self,
            _instance: u16,
            _raw: u16,
        ) -> Result<(), crate::script_init::InitFault> {
            // This probe runtime carries no state table; a lifecycle begin is
            // a loud error rather than a silent no-op.
            Err(crate::script_init::InitFault::InvalidRoot(_instance))
        }

        fn receive_applied_damage(&mut self, _: AppliedDamage) -> io::Result<()> {
            Ok(())
        }
        fn receive_room_message(
            &mut self,
            _: u32,
            _: crate::script_init::RoomMessage,
        ) -> Result<usize, crate::script_init::InitFault> {
            Ok(0)
        }
        fn receive_host_event(
            &mut self,
            _: crate::script_init::HostEvent,
            _: crate::script_init::HostHealthContext,
        ) -> Result<usize, crate::script_init::InitFault> {
            Ok(0)
        }
        fn receive_authored_hit_feedback(
            &mut self,
            _: crate::script_init::HostEvent,
            _: &crate::script_init::HostHealthContext,
            _: f32,
        ) -> Result<usize, crate::script_init::InitFault> {
            Ok(0)
        }
        fn receive_authored_elimination_feedback(
            &mut self,
            _: Arc<HealthTargetFacts>,
        ) -> Result<usize, crate::script_init::InitFault> {
            Ok(0)
        }
        fn set_spatial_frame(&mut self, frame: Arc<RoomSpatialFrame>) {
            *self.view.lock().unwrap() = Some(frame);
        }
        fn vitals(&self) -> Option<&Vitals> {
            self.vitals.as_ref()
        }
        fn take_vitals(&mut self) -> Option<Vitals> {
            self.vitals.take()
        }
        fn snapshot(&self) -> Option<&Snapshot> {
            None
        }
        fn encoder(&self) -> SnapshotEncoder<'_> {
            panic!("spatial frame probe has no encoder")
        }
        fn advance(
            &mut self,
            _: crate::commands::Command,
            _: &Mover,
            _: BodyStep,
        ) -> io::Result<()> {
            Ok(())
        }
        fn frame_ms(&self) -> u32 {
            16
        }
    }

    type Pose = (Vec3, [i32; 2]);
    #[derive(serde::Deserialize)]
    struct Owners {
        owner: u32,
        body_wires: [u32; 3],
        other_owner: u32,
        other_body: u32,
        dead_owner: u32,
        dead_body: u32,
    }
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-room-actor-pose-v1.json"
    ))
    .unwrap();
    let input = &fixture["input"];
    let owners: Owners = serde_json::from_value(input["owner_replacement"].clone()).unwrap();
    let poses: [Pose; 2] = serde_json::from_value(input["player_poses"].clone()).unwrap();
    let eyes: [Vec3; 2] =
        serde_json::from_value(fixture["expected"]["player_eye_positions"].clone()).unwrap();
    let frame = u32::try_from(input["frame"].as_u64().unwrap()).unwrap();
    let damage = input["damage"].as_f64().unwrap();
    let contact = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let mover = |(position, [yaw, _]): Pose| {
        Mover::new(
            MoverSettings::default(),
            Arc::clone(&contact),
            position,
            yaw,
            0x08,
        )
        .unwrap()
    };
    let state = |id, owner, pose, runtime| {
        PlayerBodyState::new(id, owner, 0, 0, 1, mover(pose), runtime, frame, None)
    };
    let body = |entity, generation| BodyId { entity, generation };
    let first = body(owners.body_wires[0], 1);
    let other = body(owners.other_body, 1);
    let dead = body(owners.dead_body, 1);

    for needs in [RoomSpatialNeeds::Queries, RoomSpatialNeeds::EntityFacts] {
        let mut room = RoomSimulation::new(Vec::new()).unwrap();
        room.enable_spatial_queries(false, Arc::new(fixture_ground()), needs);
        room.spawn_player_body(state(first, owners.owner, poses[0], None))
            .unwrap();
        room.spawn_player_body(state(other, owners.other_owner, poses[0], None))
            .unwrap();
        let mut vitals = Vitals::new(damage, 0.0, 0.0);
        assert!(
            vitals
                .damage(
                    damage,
                    Death {
                        time: 0.0,
                        frame,
                        killer: 0
                    }
                )
                .killed
        );
        let dead_probe = FrameProbe {
            view: Arc::default(),
            vitals: Some(vitals),
        };
        room.spawn_player_body(state(
            dead,
            owners.dead_owner,
            poses[0],
            Some(Box::new(dead_probe)),
        ))
        .unwrap();

        let detached = room.capture_spatial_frame(frame).unwrap().unwrap();
        assert_eq!(detached.actor_pose(first.entity), Some(poses[0]));
        assert_eq!(detached.actor_eye_position(first.entity), Some(eyes[0]));
        assert_eq!(detached.actor_eye_position(dead.entity), None);
        assert!(room.owner_startup_spatial_frame(owners.owner).is_none());
        room.prepare_spatial_frame(frame).unwrap();
        let frozen = Arc::clone(&room.spatial_frame.as_ref().unwrap().1);
        room.player_body_mut(other).unwrap().mover = mover(poses[1]);
        let current = room.capture_spatial_frame(frame).unwrap().unwrap();
        assert_eq!(current.actor_pose(other.entity), Some(poses[1]));
        assert_eq!(current.actor_eye_position(other.entity), Some(eyes[1]));

        for (generation, &wire) in (2..).zip(&owners.body_wires[1..]) {
            let startup = room.owner_startup_spatial_frame(owners.owner).unwrap();
            let received = Arc::new(Mutex::new(None));
            let probe = FrameProbe {
                view: Arc::clone(&received),
                vitals: None,
            };
            room.spawn_player_body(state(
                body(wire, generation),
                owners.owner,
                poses[1],
                Some(Box::new(probe)),
            ))
            .unwrap();
            let installed = received.lock().unwrap();
            for view in [&startup, installed.as_ref().unwrap()] {
                assert_eq!(view.candidates(), &frozen.candidates()[1..]);
                assert_eq!(view.actor_pose(other.entity), Some(poses[0]));
                assert_eq!(view.actor_eye_position(other.entity), Some(eyes[0]));
                assert!(!view.script_targets.contains_key(&first.entity));
                assert_eq!(view.actor_eye_position(first.entity), None);
                assert_eq!(view.actor_pose(wire), None);
                assert_eq!(view.actor_eye_position(wire), None);
                assert!(Arc::ptr_eq(&view.ground, &frozen.ground));
                if needs == RoomSpatialNeeds::EntityFacts {
                    let facts = view.entity_facts().unwrap();
                    assert!(!facts.contains_key(&first.entity));
                    assert!(!facts[&dead.entity].alive);
                } else {
                    assert!(view.entity_facts().is_none());
                }
            }
        }
        let dead_startup = room.owner_startup_spatial_frame(owners.dead_owner).unwrap();
        assert_eq!(dead_startup.candidates(), frozen.candidates());
        if let Some(facts) = dead_startup.entity_facts() {
            assert!(!facts.contains_key(&dead.entity));
        }
        assert!(Arc::ptr_eq(
            &room.spatial_frame.as_ref().unwrap().1,
            &frozen
        ));
        assert_eq!(frozen.actor_pose(first.entity), Some(poses[0]));
        assert_eq!(frozen.actor_eye_position(first.entity), Some(eyes[0]));
    }
}

#[test]
fn spatial_candidates_exclude_vitals_death_before_bot_reconciliation() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/room-spatial-liveness-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let frame = u32::try_from(fixture["input"]["frame"].as_u64().unwrap()).unwrap();
    let damage = fixture["input"]["damage"].as_f64().unwrap();
    let expected = &fixture["expected"];

    let bot = fixture_bots().remove(0);
    let wire = bot.entity;
    let mut room = RoomSimulation::new(vec![bot]).unwrap();
    room.enable_spatial_queries(
        false,
        Arc::new(fixture_ground()),
        RoomSpatialNeeds::EntityFacts,
    );
    room.prepare_spatial_frame(frame).unwrap();
    let initial = Arc::clone(&room.spatial_frame.as_ref().unwrap().1);
    assert_eq!(initial.candidates()[0].entity, wire);
    assert!(initial.entity_facts().unwrap()[&wire].alive);

    let state = room.state_mut(wire).unwrap().unwrap();
    let death = Death {
        time: 0.0,
        frame,
        killer: 0,
    };
    assert!(state.vitals.damage(damage, death).killed);
    assert!(!state.bot.dead);

    room.prepare_spatial_frame(frame).unwrap();
    assert!(Arc::ptr_eq(
        &initial,
        &room.spatial_frame.as_ref().unwrap().1
    ));

    room.prepare_spatial_frame(frame + 1).unwrap();
    let next = &room.spatial_frame.as_ref().unwrap().1;
    assert_eq!(
        next.entity_facts().unwrap()[&wire].alive,
        expected["next_frame_alive"].as_bool().unwrap()
    );
    assert_eq!(
        RoomScriptRequests::default().queue_health(next, wire, -1.0),
        expected["next_frame_health_target"].as_bool().unwrap()
    );
    assert_eq!(
        next.candidates().len() as u64,
        expected["next_frame_candidates"].as_u64().unwrap()
    );
}

#[test]
fn spatial_visibility_blocks_only_hits_before_the_target() {
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.enable_spatial_queries(
        false,
        Arc::new(fixture_ground()),
        crate::script_graph::RoomSpatialNeeds::Queries,
    );
    room.prepare_spatial_frame(1).unwrap();
    let (_, spatial) = room.spatial_frame.as_ref().unwrap();
    assert!(spatial.entity_facts().is_none());
    let origin = [-2.0, 0.5, 0.25];

    assert_eq!(spatial.line_clear(origin, [0.0, 0.5, 0.25]), Some(false));
    assert_eq!(spatial.line_clear(origin, [-1.0, 0.5, 0.25]), Some(true));
    assert_eq!(spatial.line_clear(origin, [-1.5, 0.5, 0.25]), Some(true));
    assert_eq!(spatial.line_clear(origin, origin), Some(true));
    assert_eq!(spatial.line_clear(origin, [f64::NAN, 0.0, 0.0]), None);
    assert_eq!(
        spatial.line_clear([0.0; 3], [f64::from_bits(1), 0.0, 0.0]),
        Some(true)
    );
}

#[test]
fn spatial_rays_return_the_first_static_hit_and_preserve_query_availability() {
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.enable_spatial_queries(
        false,
        Arc::new(fixture_ground()),
        crate::script_graph::RoomSpatialNeeds::Queries,
    );
    let spatial = room.capture_spatial_frame(1).unwrap().unwrap();
    let origin = [-2.0, 0.5, 0.25];
    let direction = [1.0, 0.0, 0.0];
    let expected = crate::script_expr::SpatialRayHit {
        distance: 1.0,
        point: [-1.0, 0.5, 0.25],
        normal: [-1.0, 0.0, 0.0],
    };
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 1.0),
        Some(Some(expected))
    );
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 0.5),
        Some(None)
    );
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 17, origin, [-1.0, 0.0, 0.0], 2.0),
        Some(None)
    );
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 17, [f64::MAX, 0.0, 0.0], direction, 1.0),
        None
    );
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, f64::MAX),
        None
    );
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 17, [0.0; 3], [0.0, 0.0, 1.0], 1_000_000.0),
        None
    );
}

#[test]
fn actor_rays_sample_capsules_once_and_respect_occlusion_and_replacement() {
    use crate::{
        collision_index::{SceneTriangle, TriangleIndex},
        contact_world::ContactWorld,
        mover::Mover,
        mover_settings::{MoverCatalog, MoverSettings},
        script_expr::SpatialActorRayHit,
    };

    let mut bots = fixture_bots();
    let bot_ids = [bots[0].entity, bots[1].entity];
    for bot in &mut bots {
        bot.position = [-2.0, 0.0, 0.25];
    }
    let mut room = RoomSimulation::new(bots).unwrap();
    room.enable_spatial_queries(false, Arc::new(fixture_ground()), RoomSpatialNeeds::Queries);
    let origin = [-3.0, 0.5, 0.25];
    let direction = [1.0, 0.0, 0.0];
    let targets = [7, bot_ids[1], bot_ids[0]];
    let missing = room.capture_spatial_frame(1).unwrap().unwrap();
    assert!(missing.actor_capsules.is_none());
    assert_eq!(
        missing.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 4.0, &[]),
        None
    );

    let movers = Arc::new(
        MoverCatalog::from_json(r#"{"0x02":{"stand_height":1,"stand_radius":0.25}}"#).unwrap(),
    );
    room.enable_spatial_actor_rays(movers);
    let contacts = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let player = |generation, position| {
        let mover = Mover::new(
            MoverSettings {
                stand_height: 1.0,
                stand_radius: 0.25,
                crouch_height: 0.5,
                crouch_radius: 0.125,
                ..MoverSettings::default()
            },
            Arc::clone(&contacts),
            position,
            0,
            MOVER_CATEGORY,
        )
        .unwrap();
        PlayerBodyState::new(
            BodyId {
                entity: 7,
                generation,
            },
            8,
            0,
            0,
            1,
            mover,
            None,
            1,
            None,
        )
    };
    room.spawn_player_body(player(1, [-2.0, 0.0, 0.25]))
        .unwrap();
    room.prepare_spatial_frame(1).unwrap();
    let first = Arc::clone(&room.spatial_frame.as_ref().unwrap().1);
    let capsules = first.actor_capsules.as_ref().unwrap();
    let original_identity = first.actor_identity(7).unwrap();
    assert_eq!(original_identity, first.script_targets[&7].entity.to_bits());
    assert_eq!(first.actor_identity(99), None);
    assert_eq!(capsules.len(), 3);
    assert_eq!(capsules[&7].radius, 0.25);
    assert_eq!(capsules[&bot_ids[0]].height, 1.0);
    assert_eq!(
        first.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 0.75, &targets),
        Some(Some(SpatialActorRayHit {
            entity: 7,
            distance: 0.75,
            normal: [-1.0, 0.0, 0.0],
        }))
    );
    assert_eq!(
        first.actor_ray_cast(
            SCRIPT_RAY_SITE,
            17,
            origin,
            direction,
            4.0,
            &[bot_ids[1], bot_ids[0]]
        ),
        Some(Some(SpatialActorRayHit {
            entity: bot_ids[0].min(bot_ids[1]),
            distance: 0.75,
            normal: [-1.0, 0.0, 0.0],
        }))
    );
    assert_eq!(
        first.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 0.5, &targets),
        Some(None)
    );
    assert_eq!(
        first.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 4.0, &[]),
        Some(None)
    );
    assert_eq!(
        first.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 4.0, &[99]),
        None
    );
    assert_eq!(
        first.actor_ray_cast(
            SCRIPT_RAY_SITE,
            17,
            [-2.0, 2.0, 0.25],
            [0.0, -1.0, 0.0],
            1.0,
            &targets
        ),
        Some(Some(SpatialActorRayHit {
            entity: 7,
            distance: 1.0,
            normal: [0.0, 1.0, 0.0],
        }))
    );
    let mut incomplete = first.as_ref().clone();
    incomplete
        .actor_capsules
        .as_mut()
        .unwrap()
        .remove(&bot_ids[0]);
    assert_eq!(
        incomplete.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 4.0, &[bot_ids[0]]),
        None
    );

    room.body_mut(bot_ids[0]).unwrap().position = [-0.5, 0.0, 0.25];
    room.body_mut(bot_ids[1]).unwrap().position = [-0.75, 0.0, 0.25];
    let old_entity = first.script_targets[&7].entity;
    for generation in [2, 3] {
        room.spawn_player_body(player(generation, [5.0, 0.0, 0.25]))
            .unwrap();
        let replacement = room.owner_startup_spatial_frame(8).unwrap();
        assert_eq!(first.actor_identity(7), Some(original_identity));
        assert_eq!(replacement.actor_identity(7), None);
        assert_eq!(
            replacement.actor_identity(bot_ids[0]),
            first.actor_identity(bot_ids[0])
        );
        assert!(
            !replacement
                .actor_capsules
                .as_ref()
                .unwrap()
                .contains_key(&7)
        );
        assert_eq!(
            replacement.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 4.0, &[7]),
            None
        );
        assert_eq!(
            replacement.actor_capsules.as_ref().unwrap()[&bot_ids[0]],
            capsules[&bot_ids[0]]
        );
    }
    room.prepare_spatial_frame(1).unwrap();
    assert!(Arc::ptr_eq(&first, &room.spatial_frame.as_ref().unwrap().1));
    assert_eq!(
        first.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 4.0, &[7]),
        Some(Some(SpatialActorRayHit {
            entity: 7,
            distance: 0.75,
            normal: [-1.0, 0.0, 0.0],
        }))
    );
    let standing_ground = TriangleIndex::new([SceneTriangle {
        vertices: [[3.0, 0.0, -2.0], [5.0, 0.0, 3.0], [7.0, 0.0, -2.0]],
        mask: MOVER_CATEGORY,
        surface: 0,
    }])
    .unwrap();
    let crouched = room
        .player_body_mut(BodyId {
            entity: 7,
            generation: 3,
        })
        .unwrap();
    crouched
        .mover
        .step(
            &standing_ground,
            Command {
                frame: 2,
                buttons: crate::commands::CROUCH,
                ..Command::default()
            },
            0.016,
            |_, _| {},
        )
        .unwrap();
    assert!(crouched.mover.crouched());
    room.prepare_spatial_frame(2).unwrap();
    let next = &room.spatial_frame.as_ref().unwrap().1;
    assert_eq!(next.actor_capsules.as_ref().unwrap()[&7].height, 0.5);
    assert_eq!(next.actor_capsules.as_ref().unwrap()[&7].radius, 0.125);
    assert_ne!(next.script_targets[&7].entity, old_entity);
    assert_eq!(
        next.actor_identity(7),
        Some(next.script_targets[&7].entity.to_bits())
    );
    assert_ne!(next.actor_identity(7), Some(original_identity));
    assert_eq!(first.actor_identity(7), Some(original_identity));
    assert_eq!(
        next.actor_ray_cast(
            SCRIPT_RAY_SITE,
            17,
            origin,
            direction,
            4.0,
            &[7, bot_ids[0]]
        ),
        Some(None)
    );
    assert_eq!(
        next.actor_ray_cast(SCRIPT_RAY_SITE, 17, origin, direction, 4.0, &[bot_ids[1]]),
        Some(Some(SpatialActorRayHit {
            entity: bot_ids[1],
            distance: 2.0,
            normal: [-1.0, 0.0, 0.0],
        }))
    );
}

#[test]
fn script_ray_diagnostics_preserve_capture_identity_and_distinguish_query_outcomes() {
    use crate::{game_debug::DebugScriptRay, mover_settings::MoverCatalog};

    let mut bots = fixture_bots();
    let visible = bots[0].entity;
    let hidden = bots[1].entity;
    bots[0].position = [-2.0, 0.0, 0.25];
    bots[1].position = [0.0, 0.0, 0.25];
    let mut room = RoomSimulation::new(bots).unwrap();
    room.enable_spatial_queries(false, Arc::new(fixture_ground()), RoomSpatialNeeds::Queries);
    let origin = [-3.0, 0.5, 0.25];
    let direction = [1.0, 0.0, 0.0];
    let missing = room.capture_spatial_frame(41).unwrap().unwrap();
    assert_eq!(
        missing.actor_ray_cast(SCRIPT_RAY_SITE, 42, origin, direction, 4.0, &[visible]),
        None
    );
    room.enable_spatial_actor_rays(Arc::new(
        MoverCatalog::from_json(r#"{"0x02":{"stand_height":1,"stand_radius":0.25}}"#).unwrap(),
    ));
    let spatial = room.capture_spatial_frame(41).unwrap().unwrap();
    assert_eq!(
        spatial
            .ray_cast(SCRIPT_RAY_SITE, 43, origin, direction, 4.0)
            .unwrap()
            .unwrap()
            .distance,
        2.0
    );
    assert_eq!(
        spatial
            .actor_ray_cast(SCRIPT_RAY_SITE, 44, origin, direction, 4.0, &[visible])
            .unwrap()
            .unwrap()
            .entity,
        visible
    );
    assert_eq!(
        spatial.actor_ray_cast(SCRIPT_RAY_SITE, 45, origin, direction, 4.0, &[hidden]),
        Some(None)
    );
    // A wall alone does not establish that an unchecked actor was occluded.
    assert_eq!(
        spatial.actor_ray_cast(SCRIPT_RAY_SITE, 46, origin, direction, 4.0, &[]),
        Some(None)
    );
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 47, origin, [-1.0, 0.0, 0.0], 0.25),
        Some(None)
    );
    let mut incomplete = spatial.as_ref().clone();
    incomplete.actor_capsules.as_mut().unwrap().remove(&visible);
    assert_eq!(
        incomplete.actor_ray_cast(SCRIPT_RAY_SITE, 48, origin, direction, 4.0, &[visible]),
        None
    );

    room.prepare_spatial_frame(49).unwrap();
    // A retained frame shares the room history, but keeps its own capture tick.
    spatial
        .ray_cast(SCRIPT_RAY_SITE, 50, origin, direction, 4.0)
        .unwrap()
        .unwrap();
    room.spatial_frame
        .as_ref()
        .unwrap()
        .1
        .ray_cast(SCRIPT_RAY_SITE, 51, origin, direction, 4.0)
        .unwrap()
        .unwrap();
    let overlay = room.debug_physics().unwrap().unwrap();
    assert!(!overlay.truncated.rays);
    let expected = [
        ("script_actor", "unavailable", 42, [1.0, 0.5, 0.25], None),
        ("script_static", "world", 43, [-1.0, 0.5, 0.25], None),
        (
            "script_actor",
            "actor",
            44,
            [-2.25, 0.5, 0.25],
            Some(visible),
        ),
        ("script_actor", "world", 45, [-1.0, 0.5, 0.25], None),
        ("script_actor", "none", 46, [1.0, 0.5, 0.25], None),
        ("script_static", "none", 47, [-3.25, 0.5, 0.25], None),
        ("script_actor", "unavailable", 48, [1.0, 0.5, 0.25], None),
        ("script_static", "world", 50, [-1.0, 0.5, 0.25], None),
        ("script_static", "world", 51, [-1.0, 0.5, 0.25], None),
    ];
    assert_eq!(overlay.rays.len(), expected.len());
    for (index, (ray, (kind, hit, frame, end, target))) in
        overlay.rays.iter().zip(expected).enumerate()
    {
        assert_eq!(ray.tick, if index == expected.len() - 1 { 49 } else { 41 });
        assert_eq!(
            (ray.source, ray.kind, ray.hit, ray.end, ray.target),
            (SCRIPT_RAY_SITE.entity, kind, hit, end, target)
        );
        assert_eq!(ray.origin, origin);
        assert!(!ray.critical);
        assert_eq!(
            ray.script,
            Some(DebugScriptRay {
                graph: SCRIPT_RAY_SITE.graph,
                instance: SCRIPT_RAY_SITE.instance,
                node: SCRIPT_RAY_SITE.node,
                vm_frame: frame,
            })
        );
    }
    let encoded = serde_json::to_value(&overlay).unwrap();
    assert_eq!(encoded["rays"][0]["script"]["vm_frame"], 42);
    assert_eq!(encoded["rays"][0]["script"]["graph"], SCRIPT_RAY_SITE.graph);
    assert_eq!(encoded["rays"][8]["tick"], 49);

    // Invalid nonfinite coordinates cannot leak into a serialized debug segment.
    assert_eq!(
        spatial.ray_cast(SCRIPT_RAY_SITE, 52, [f64::NAN, 0.0, 0.0], direction, 4.0),
        None
    );
    assert_eq!(
        room.debug_physics().unwrap().unwrap().rays.len(),
        expected.len()
    );
}

#[test]
fn room_messages_and_host_health_events_reach_npc_bodies_on_their_next_frames() {
    use crate::{
        game_clock::TICK_PERIOD,
        game_pools::HealthPools,
        mover_settings::MoverCatalog,
        npc_catalog::NpcDefinition,
        script_codecs::CodecTable,
        script_enemy::EnemySpawn,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation, RoomMessage},
        script_wire::{Value, Variables},
    };
    use serde_json::json;

    const HERO: u64 = 0x02e0_0000_0050_0101;
    const BODY: u64 = 0x0400_0000_0050_0102;
    const MESSAGE: u64 = 71;
    const SENDER_SLOT: u32 = 7;
    const PAYLOAD_SLOT: u32 = 8;
    const PARAMETER: u32 = 9;
    const HOST_DELTA_SLOT: u32 = 18;
    const ADMITTED_SLOT: u32 = 19;
    const CONTROL_SLOT: u32 = 20;
    const EXCLUDED_TAG: &str = "0x0D60000000500114";

    let entity_var = |id| json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":true});
    let priority = json!({"level":3,"above_equals":false});
    let variable = json!({"scope":"entity","id":CONTROL_SLOT});
    let nodes = [
        json!({
            "pos":0,"uid":0,"rid":0,"remote":-1,"state":null,
            "class":"STUStatescriptEntryGameMessage","server_only":true,"client_only":false,
            "room_message_entry":true,"fields":{
                "m_gameMessage":{"m_gameMessage":format!("0x{MESSAGE:016X}")},
                "m_B60ABDA4":entity_var(SENDER_SLOT),
                "m_params":[{"m_B5051BCE":format!("0x{PARAMETER:016X}"),
                    "m_out_Var":entity_var(PAYLOAD_SLOT)}],
                "m_outPlug":{"$":"Plug","links":[[4,"m_inPlug"]]}
            }
        }),
        json!({
            "pos":1,"uid":1,"rid":1,"remote":-1,"state":null,
            "class":"EntryHostEvent","server_only":true,"client_only":false,
            "host_event_entry":"health_applied","fields":{
                "m_params":[{"m_B5051BCE":"0x0000000000000003",
                    "m_out_Var":entity_var(HOST_DELTA_SLOT)}],
                "m_outPlug":{"$":"Plug","links":[]}
            }
        }),
        json!({
            "pos":2,"uid":2,"rid":2,"remote":-1,"state":0,
            "class":"authored.input_lock","server_only":true,"client_only":false,
            "state_behavior":"button_lock","fields":{"button":1}
        }),
        json!({
            "pos":3,"uid":3,"rid":3,"remote":-1,"state":1,
            "class":"authored.speed_scale","server_only":true,"client_only":false,
            "state_behavior":"movement_scale","fields":{"factor":0.5}
        }),
        json!({
            "pos":4,"uid":4,"rid":4,"remote":-1,"state":null,
            "class":"authored.sender_filter","server_only":true,"client_only":false,
            "action_behavior":"branch","fields":{
                "m_condition":{"$":"EntityMatches","entity":entity_var(SENDER_SLOT),
                    "filter":{"alive":true,"tags":{"none":[EXCLUDED_TAG]}}},
                "m_truePlug":{"$":"Plug","links":[[5,"m_inPlug"]]},
                "m_falsePlug":{"$":"Plug","links":[]}
            }
        }),
        json!({
            "pos":5,"uid":5,"rid":5,"remote":-1,"state":null,
            "class":"authored.priority_gate","server_only":true,"client_only":false,
            "action_behavior":"branch","fields":{
                "m_condition":{"$":"StackAllows","variable":variable,"priority":priority},
                "m_truePlug":{"$":"Plug","links":[[6,"m_beginPlug"],[7,"m_inPlug"]]},
                "m_falsePlug":{"$":"Plug","links":[]}
            }
        }),
        json!({
            "pos":6,"uid":6,"rid":6,"remote":-1,"state":2,
            "class":"authored.control_claim","server_only":true,"client_only":false,
            "state_behavior":"stack_claim","fields":{
                "variable":variable,"priority":priority,"value":true,"duration_frames":2,
                "m_topSubgraphPlug":{"$":"Plug","links":[[2,"m_beginPlug"],[3,"m_beginPlug"]],
                    "m_FF3DAF1E":[0,1]}
            }
        }),
        json!({
            "pos":7,"uid":7,"rid":7,"remote":-1,"state":null,
            "class":"STUStatescriptActionSetVar","server_only":true,"client_only":false,
            "fields":{"m_value":entity_var(PAYLOAD_SLOT),"m_out_Var":entity_var(ADMITTED_SLOT)}
        }),
    ];
    let bundle = json!({
        "version":1,"bodies":{},"missing":[],
        "graphs":[{
            "index":123,"items":8,"states":[2,3,6],"entries":[0,1],"remote":[],"sync_vars":[],
            "fields":{},"containers":{},"unknown_nodes":[],
            "header":{"m_nodesBitCount":4,"m_statesBitCount":4,
                "m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4},
            "nodes":nodes
        }],
        "junkenstein":{
            "pairs":{"0x02E0000000500101":{"body":"0x0400000000500102",
                "controller":"0x0400000000500103"}},
            "entities":{
                "0x0400000000500102":{"hero":"0x02E0000000500101","name":"Body",
                    "manager":null,"weapons":[],"target_tags":[],"graphs":[{"graph":123,"m_75408640":0}]},
                "0x0400000000500103":{"hero":null,"name":"Controller",
                    "manager":null,"weapons":[],"graphs":[{"graph":123,"m_75408640":0}]}
            }
        }
    });
    let load = |bundle: &serde_json::Value| {
        Arc::new(GraphStore::from_json(&serde_json::to_vec(bundle).unwrap()).unwrap())
    };
    let graphs = load(&bundle);
    let mut tagged_bundle = bundle;
    tagged_bundle["junkenstein"]["entities"]["0x0400000000500102"]["target_tags"] =
        json!([EXCLUDED_TAG]);
    let tagged_graphs = load(&tagged_bundle);
    let codecs = Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap());
    let definition = NpcDefinition {
        hero: HERO,
        body: BODY,
        name: "Message test".into(),
        pools: HealthPools {
            health: 100.0,
            armour: 0.0,
            shields: 0.0,
        },
    };
    let request = EntityCreation {
        position: [1.0, 2.0, 3.0],
        kind: CreationKind::Hero {
            hero: HERO,
            team: 1,
            yaw: 0.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: None,
            skin: None,
        },
    };
    let context = FIXTURE_CONTEXT;
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    let mut spawn = |graphs| {
        room.create_enemy(EnemySpawn {
            graphs,
            codecs: Arc::clone(&codecs),
            definition: &definition,
            request: &request,
            mode: 0,
            mode_variables: Arc::new(Variables::new()),
            tick: context.tick,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap()
    };
    let sender = spawn(Arc::clone(&graphs));
    let receiver = spawn(Arc::clone(&graphs));
    let excluded_sender = spawn(tagged_graphs);
    let ground = Arc::new(fixture_ground());
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let needs = graphs.room_spatial_needs();
    assert_eq!(needs, crate::script_graph::RoomSpatialNeeds::EntityTags);
    room.enable_spatial_queries(false, Arc::clone(&ground), needs);
    room.prepare_spatial_frame(context.tick).unwrap();

    let stage = |room: &RoomSimulation, payload| {
        let frame = &room.spatial_frame.as_ref().unwrap().1;
        let mut outbox = RoomScriptRequests::default();
        let message = RoomMessage {
            id: MESSAGE,
            parameters: Variables::from([(PARAMETER, Value::Integer(payload))]),
        };
        assert!(!outbox.queue_message(frame, receiver + 1, message.clone()));
        assert!(outbox.queue_message(frame, receiver, message));
        outbox.take().pop().unwrap()
    };
    let send = |room: &mut RoomSimulation, source, payload, when, now| {
        let envelope = stage(room, payload);
        room.settle_script_request(envelope, source, when, now)
            .unwrap();
    };
    let sender_body = room.sample_health_body(sender).unwrap();
    let excluded_body = room.sample_health_body(excluded_sender).unwrap();
    send(&mut room, sender_body, 17, context.tick, context.now);

    let observed = |room: &RoomSimulation, wire, controller| {
        let enemy = state(room, wire).unwrap().enemy.as_ref().unwrap();
        assert_eq!(enemy.fault, None);
        let component = if controller {
            &enemy.scripts.controller
        } else {
            &enemy.scripts.body
        };
        let variables = &component.snapshot().unwrap().variables;
        (
            variables.get(&SENDER_SLOT).cloned(),
            variables.get(&PAYLOAD_SLOT).cloned(),
            variables.get(&ADMITTED_SLOT).cloned(),
        )
    };
    let lock_active = |room: &RoomSimulation, wire, controller| {
        let enemy = state(room, wire).unwrap().enemy.as_ref().unwrap();
        let component = if controller {
            &enemy.scripts.controller
        } else {
            &enemy.scripts.body
        };
        component.snapshot().unwrap().instances[&1]
            .states
            .get(&0)
            .is_some_and(|state| state.active)
    };
    let speed_scale = |room: &RoomSimulation, wire, frame| {
        state(room, wire)
            .unwrap()
            .enemy
            .as_ref()
            .unwrap()
            .scripts
            .movement_speed_scale(frame)
    };
    assert!(!lock_active(&room, receiver, false));
    assert_eq!(observed(&room, receiver, false), (None, None, None));
    assert_eq!(speed_scale(&room, receiver, context.tick), 1.0);
    room.finish_tick(&queries, context.tick, context.now)
        .unwrap();
    assert!(!lock_active(&room, receiver, false));
    assert_eq!(observed(&room, receiver, false), (None, None, None));
    room.finish_tick(&queries, context.tick + 1, context.now + TICK_PERIOD)
        .unwrap();
    assert_eq!(
        observed(&room, receiver, false),
        (
            Some(Value::Entity(sender)),
            Some(Value::Integer(17)),
            Some(Value::Integer(17))
        )
    );
    assert_eq!(observed(&room, receiver, true), (None, None, None));
    assert_eq!(observed(&room, sender, false), (None, None, None));
    assert!(lock_active(&room, receiver, false));
    assert!(!lock_active(&room, receiver, true));
    assert!(!lock_active(&room, sender, false));
    assert_eq!(speed_scale(&room, receiver, context.tick + 1), 1.0);
    assert_eq!(speed_scale(&room, receiver, context.tick + 2), 0.5);
    assert_eq!(speed_scale(&room, sender, context.tick + 2), 1.0);

    let host_delta = |room: &RoomSimulation, wire| {
        let enemy = state(room, wire).unwrap().enemy.as_ref().unwrap();
        let delta = |component: &crate::script_init::Component| {
            component
                .snapshot()
                .unwrap()
                .variables
                .get(&HOST_DELTA_SLOT)
                .cloned()
        };
        (delta(&enemy.scripts.body), delta(&enemy.scripts.controller))
    };
    room.enable_host_health_events();
    let tick = context.tick + 2;
    let now = context.now + 2 * TICK_PERIOD;
    send(&mut room, sender_body, 18, tick - 1, now - TICK_PERIOD);
    room.settle_health(
        receiver,
        HealthEffect::Damage(25.0),
        crate::vitals::Death {
            time: now.as_secs_f64(),
            frame: tick,
            killer: sender,
        },
        now,
    )
    .unwrap();
    room.finish_tick(&queries, tick, now).unwrap();
    assert_eq!(host_delta(&room, receiver), (None, None));
    assert_eq!(host_delta(&room, sender), (None, None));
    assert_eq!(
        observed(&room, receiver, false),
        (
            Some(Value::Entity(sender)),
            Some(Value::Integer(18)),
            Some(Value::Integer(17))
        )
    );
    assert!(lock_active(&room, receiver, false));
    room.finish_tick(&queries, tick + 1, now + TICK_PERIOD)
        .unwrap();
    assert_eq!(
        host_delta(&room, receiver),
        (Some(Value::Float(-25.0)), None)
    );
    assert_eq!(host_delta(&room, sender), (Some(Value::Float(-25.0)), None));
    assert!(!lock_active(&room, receiver, false));
    assert_eq!(speed_scale(&room, receiver, tick + 1), 0.5);
    assert_eq!(speed_scale(&room, receiver, tick + 2), 1.0);

    send(&mut room, excluded_body, 19, tick + 1, now + TICK_PERIOD);
    room.finish_tick(&queries, tick + 2, now + 2 * TICK_PERIOD)
        .unwrap();
    assert_eq!(
        observed(&room, receiver, false),
        (
            Some(Value::Entity(excluded_sender)),
            Some(Value::Integer(19)),
            Some(Value::Integer(17))
        )
    );
    assert!(!lock_active(&room, receiver, false));
    assert_eq!(speed_scale(&room, receiver, tick + 3), 1.0);

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-room-message-v1.json"
    ))
    .unwrap();
    let generations = &fixture["native_sender_generation"];
    let stale = stage(&room, generations["stale_payload"].as_i64().unwrap());
    let previous = observed(&room, receiver, false);
    let body = room
        .world
        .entity_mut(sender_body.entity)
        .take::<BotState>()
        .unwrap();
    assert!(room.world.despawn(sender_body.entity));
    let replacement = room.world.spawn(body).id();
    assert_ne!(replacement, sender_body.entity);
    assert_eq!(
        room.by_wire.insert(sender, replacement),
        Some(sender_body.entity)
    );
    room.settle_script_request(stale, sender_body, tick + 2, now + 2 * TICK_PERIOD)
        .unwrap();
    room.finish_tick(&queries, tick + 3, now + 3 * TICK_PERIOD)
        .unwrap();
    assert_eq!(observed(&room, receiver, false), previous);

    let current = room.sample_health_body(sender).unwrap();
    let payload = generations["current_payload"].as_i64().unwrap();
    send(&mut room, current, payload, tick + 3, now + 3 * TICK_PERIOD);
    room.finish_tick(&queries, tick + 4, now + 4 * TICK_PERIOD)
        .unwrap();
    let (actual_sender, actual_payload, _) = observed(&room, receiver, false);
    assert_eq!(actual_sender, Some(Value::Entity(sender)));
    assert_eq!(actual_payload, Some(Value::Integer(payload)));
}

#[test]
fn corpse_health_is_available_after_live_target_selection_excludes_it() {
    let mut room = RoomSimulation::new(fixture_bots()).unwrap();
    let victim = room.living_enemies(0).unwrap().next().unwrap().0.entity;
    room.apply_bot_impact(
        7,
        FIXTURE_CONTEXT.tick,
        FIXTURE_CONTEXT,
        &fixture_impact(victim, 10000.0),
    )
    .unwrap()
    .unwrap();
    assert!(
        !room
            .living_enemies(0)
            .unwrap()
            .any(|(bot, _)| bot.entity == victim)
    );
    assert_eq!(room.body_healths().unwrap().get(&victim), Some(&0.0));
    room.respawn_due(
        FIXTURE_CONTEXT.tick + 1,
        FIXTURE_CONTEXT.now + BOT_RESPAWN_TIME,
    )
    .unwrap();
    assert!(!room.body_healths().unwrap().contains_key(&victim));
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_waves_destroy_gate_with_invulnerable_cassidy() {
    junkenstein::verify_natural_ending(junkenstein::NormalEnding::EarlyDefeat);
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_late_waves_destroy_gate_after_all_boss_intros() {
    junkenstein::verify_natural_ending(junkenstein::NormalEnding::LateDefeat);
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_deadeye_clears_natural_bosses_and_reaches_allied_victory() {
    junkenstein::verify_natural_ending(junkenstein::NormalEnding::DeadeyeVictory);
}

mod aim_samples;
mod encounter_ecs;
mod faulted_owner_vitals;
mod junkenstein;
mod settled_bonus;
mod swept_displacement;

#[test]
fn scene_enemy_creation_is_atomic_and_retains_late_join_health_and_skin() {
    use crate::{
        script_enemy::{EnemySpawn, fixture},
        script_wire::Variables,
    };
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    for fail in [true, false, false] {
        let (graphs, codecs, definition, request) = fixture(fail);
        let next = room.next_bot;
        let count = room.bodies().unwrap().len();
        let result = room.create_enemy(EnemySpawn {
            graphs,
            codecs,
            definition: &definition,
            request: &request,
            mode: 0x0230_0000_0000_019f,
            mode_variables: Arc::new(Variables::new()),
            tick: 1000,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        });
        if fail {
            assert!(result.unwrap_err().starts_with("enemy controller startup:"));
            assert_eq!(room.next_bot, next);
            assert_eq!(room.bodies().unwrap().len(), count);
            assert!(room.take_created().is_empty());
            continue;
        }
        let entity = result.unwrap();
        assert_eq!(entity, next);
        assert_eq!(room.next_bot, next + 2);
        assert_eq!(room.bodies().unwrap().len(), count + 1);
        let updates = room.take_created();
        assert_eq!(updates.len(), 1);
        assert_eq!(room.initial_creates().unwrap().last().unwrap(), &updates[0]);
        let enemy = state(&room, entity).unwrap().enemy.as_ref().unwrap();
        assert_eq!(enemy.binding.controller, entity + 1);
        assert_eq!(
            enemy.scripts.controller.snapshot().unwrap().entity,
            entity + 1
        );
        assert_eq!(room.enemy_projections().unwrap().count(), count + 1);
        let hit = room
            .apply_bot_impact(1, 1000, FIXTURE_CONTEXT, &fixture_impact(entity, 500.0))
            .unwrap()
            .unwrap();
        assert!(matches!(
            hit.notices[0],
            Some(CombatNotice::Hit { killed: true, .. })
        ));
        assert!(state(&room, entity).unwrap().respawn_at.is_none());
        let late = room.initial_creates().unwrap().pop().unwrap();
        let Some(Operation::Create(create)) = late.operation else {
            panic!("late join create")
        };
        assert_eq!(create.skin.guid, 0x0b20_0000_0000_0010);
        assert_ne!(room.initial_creates().unwrap().last().unwrap(), &updates[0]);
    }
}

#[test]
fn npc_health_updates_keep_cleared_pool_slots_in_later_snapshots() {
    use crate::{
        game_clock::TICK_PERIOD,
        mover_settings::MoverCatalog,
        script_enemy::{EnemySpawn, fixture as enemy_fixture},
        script_runtime::{HEALTH_POOL, SHIELDS_POOL},
        script_wire::Variables,
        vitals::PoolKind,
        world::{ComponentValue, POOLS, pool},
    };

    #[derive(serde::Deserialize)]
    struct Input {
        spawn_tick: u32,
        shield_identifiers: [String; 2],
        shield_amounts: [f64; 2],
        ordinary_bonus_health: f64,
        drain: f64,
        follow_up_damage: f64,
    }
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/npc-cumulative-health-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let case: Input = serde_json::from_value(fixture["input"].clone()).unwrap();
    let [first, second] = case
        .shield_identifiers
        .map(|identifier| crate::accounts::parse_guid(&identifier).unwrap());
    let [first_amount, second_amount] = case.shield_amounts;
    let first_slot = SHIELDS_POOL * POOLS + 1;
    let second_slot = first_slot + 1;
    let ordinary_slot = HEALTH_POOL * POOLS + 1;
    let clear = Some(ComponentValue::Blob20(pool(
        0,
        SHIELDS_POOL as u32,
        0.0,
        0.0,
    )));

    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    let (graphs, codecs, definition, request) = enemy_fixture(false);
    let npc = room
        .create_enemy(EnemySpawn {
            graphs,
            codecs,
            definition: &definition,
            request: &request,
            mode: 0x0230_0000_0000_019f,
            mode_variables: Arc::new(Variables::new()),
            tick: case.spawn_tick,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    room.activate_combat();
    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let finish = |room: &mut RoomSimulation, offset| {
        room.finish_tick(
            &queries,
            case.spawn_tick + offset,
            FIXTURE_CONTEXT.now + TICK_PERIOD * offset,
        )
        .unwrap();
    };
    let health_update = |room: &mut RoomSimulation| {
        let [update]: [EntityUpdate; 1] = room
            .take_scripted_health()
            .try_into()
            .expect("one NPC health update");
        assert_eq!(update.entity, npc);
        let Some(Operation::Update(components)) = update.operation else {
            panic!("expected NPC health update");
        };
        components.into_iter().next().expect("NPC health component")
    };

    let vitals = &mut room.state_mut(npc).unwrap().unwrap().vitals;
    let first_grant = vitals
        .add_temporary_shield(first, first_amount, false)
        .unwrap();
    vitals
        .add_temporary_shield(second, second_amount, false)
        .unwrap();
    let bonus = vitals
        .add_pool(
            PoolKind::Health,
            case.ordinary_bonus_health,
            case.ordinary_bonus_health,
        )
        .unwrap();
    finish(&mut room, 1);
    let initial = health_update(&mut room);
    assert_eq!(
        initial.fields[first_slot],
        Some(ComponentValue::Blob20(pool(
            first as u32,
            SHIELDS_POOL as u32,
            first_amount as f32,
            first_amount as f32,
        )))
    );
    assert!(initial.fields[ordinary_slot].is_some());

    let vitals = &mut room.state_mut(npc).unwrap().unwrap().vitals;
    assert_eq!(vitals.remove_pool(first_grant), Some(first_amount));
    assert_eq!(vitals.remove_pool(bonus), Some(case.ordinary_bonus_health));
    finish(&mut room, 2);
    let lost = health_update(&mut room);
    assert_eq!(lost.fields[first_slot], clear);
    assert_eq!(
        lost.fields[ordinary_slot],
        Some(ComponentValue::Blob20([0; 20]))
    );

    // The receiver can miss the first clear. Only the second shield changes
    // now, but this snapshot must independently clear both retired slots.
    let vitals = &mut room.state_mut(npc).unwrap().unwrap().vitals;
    assert_eq!(
        vitals.drain_temporary_shield(second, case.drain),
        second_amount - case.drain
    );
    finish(&mut room, 3);
    let replacement = health_update(&mut room);
    assert_eq!(replacement.fields[first_slot], clear);
    assert_eq!(
        replacement.fields[ordinary_slot],
        lost.fields[ordinary_slot]
    );
    assert_eq!(
        replacement.fields[second_slot],
        Some(ComponentValue::Blob20(pool(
            second as u32,
            SHIELDS_POOL as u32,
            second_amount as f32,
            (second_amount - case.drain) as f32,
        )))
    );

    let context = ShotContext {
        tick: case.spawn_tick + 4,
        now: FIXTURE_CONTEXT.now + TICK_PERIOD * 4,
        ..FIXTURE_CONTEXT
    };
    room.apply_bot_impact(
        1,
        context.tick,
        context,
        &fixture_impact(npc, case.follow_up_damage),
    )
    .unwrap()
    .unwrap();
    let from_damage = health_update(&mut room);
    assert_eq!(from_damage.fields[first_slot], clear);
    assert_eq!(
        from_damage.fields[ordinary_slot],
        lost.fields[ordinary_slot]
    );
}

#[test]
#[ignore = "requires the external 104319 Junkenstein NPC catalog and graph bundle"]
fn authored_zomnic_starts_both_components_before_room_publication() {
    use crate::{
        npc_catalog::NpcCatalog,
        script_codecs::CodecTable,
        script_enemy::EnemySpawn,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation},
        script_wire::{Value, Variables},
    };
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs = Arc::new(CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap());
    let catalog = NpcCatalog::load(root.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
    let definition = catalog.get(0x02e0_0000_0000_0175).unwrap();
    let request = EntityCreation {
        position: [74.0, 12.6, -99.0],
        kind: CreationKind::Hero {
            hero: definition.hero,
            team: 1,
            yaw: 180.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: None,
            skin: None,
        },
    };
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    let entity = room
        .create_enemy(EnemySpawn {
            graphs,
            codecs,
            definition,
            request: &request,
            mode: 0x0230_0000_0000_019f,
            mode_variables: Arc::new(Variables::new()),
            tick: 4200,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    assert_eq!(entity, BOT_ENTITY);
    assert_eq!(room.next_bot, BOT_ENTITY + 2);
    assert_eq!(room.bodies().unwrap().len(), 1);
    assert_eq!(room.world.entities().len(), 1);
    assert_eq!(room.take_created().len(), 1);
    assert_eq!(room.enemy_projections().unwrap().count(), 1);
    let state = room.state_mut(entity).unwrap().unwrap();
    let enemy = state.enemy.as_mut().unwrap();
    assert_eq!(enemy.binding.body, entity);
    assert_eq!(enemy.binding.controller, entity + 1);
    assert_eq!(state.vitals.maximum(), 200.0);
    let requests = enemy.scripts.behavior_requests().collect::<Vec<_>>();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].class, "STU_600E3C59");
    assert_eq!(requests[0].priority, 10.0);
    assert_eq!(requests[0].inputs, [2.0, 2.0, 1.0, f64::from(0.2f32), 1.0]);
    assert_eq!(requests[1].class, "STU_B61E6681");
    assert_eq!(requests[1].priority, 0.0);
    assert_eq!(requests[1].inputs, [1.5, 1.0, 0.0, 0.0, 0.0]);
    assert!(
        enemy
            .scripts
            .body
            .snapshot()
            .unwrap()
            .initialization_complete()
    );
    assert!(
        enemy
            .scripts
            .controller
            .snapshot()
            .unwrap()
            .initialization_complete()
    );
}

#[test]
#[ignore = "requires external 104319 encounter body definitions"]
fn boss_controller_mismatch_is_rejected_before_room_allocation() {
    use crate::{
        game_heroes::HeroCatalog,
        npc_catalog::encounter_definition,
        script_codecs::CodecTable,
        script_enemy::EnemySpawn,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation},
        script_wire::{Value, Variables},
    };
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs = Arc::new(CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap());
    let heroes = HeroCatalog::load(root.join("data/game_heroes_174.json")).unwrap();
    for index in [2, 4, 0x16, 0x40, 0x65] {
        let hero = 0x02e0_0000_0000_0000 | index;
        let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
        for controller in [None, Some(0x0400_0000_0000_ffff)] {
            let mut room = RoomSimulation::new(Vec::new()).unwrap();
            let next = room.next_bot;
            let request = EntityCreation {
                position: [74.0, 12.6, -99.0],
                kind: CreationKind::Hero {
                    hero,
                    team: 1,
                    yaw: 180.0,
                    flags: [Value::Bool(false), Value::Bool(false)],
                    controller,
                    skin: None,
                },
            };
            let result = room.create_enemy(EnemySpawn {
                graphs: Arc::clone(&graphs),
                codecs: Arc::clone(&codecs),
                definition: &definition,
                request: &request,
                mode: 0x0230_0000_0000_019f,
                mode_variables: Arc::new(Variables::new()),
                tick: 4200,
                walk: crate::game_bots::BotWalk::default(),
                gate: None,
            });
            assert_eq!(
                result.unwrap_err(),
                "scene enemy creation differs from its validated definition"
            );
            assert_eq!(room.next_bot, next);
            assert_eq!(room.bodies().unwrap().len(), 0);
            assert!(room.take_created().is_empty());
        }
    }
}

pub(super) fn fixture_impact(entity: u32, damage: f64) -> Impact {
    Impact {
        tags: crate::game_hitscan::DamageTags::EMPTY,
        entity,
        distance: 1.0,
        damage,
        critical: false,
        loadout: None,
        impulse: None,
        geometry: None,
    }
}

#[test]
#[ignore = "requires the external 104319 Junkenstein NPC, graph and collision pack"]
fn authored_zomnic_attack_updates_gate_damage_and_retires_the_pair() {
    use crate::{
        game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
        npc_catalog::NpcCatalog,
        script_codecs::CodecTable,
        script_enemy::{EnemyGate, EnemySpawn, SceneMessage},
        script_expr::to_float,
        script_graph::GraphStore,
        script_init::{CreationKind, EntityCreation},
        script_snapshot_wire::View,
        script_stream::{BodyStream, DeltaKind},
        script_wire::{Value, Variables},
    };
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(&root).unwrap();
    let maps = MapCatalog::load(root.join("data/game_maps_174.json")).unwrap();
    let card = JunkensteinCard::from_index(0x3c).unwrap();
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
    assert!(scene.encounter_result_wire().unwrap().is_none());
    assert!(scene.encounter_outcome_wire(1).unwrap().is_none());
    let projection = scene.gate_projection().unwrap();
    let before = projection
        .encoder
        .project(projection.snapshot, View::Observer)
        .unwrap();
    let mut stream = BodyStream::new(projection.snapshot.entity);
    let baseline = projection.encoder.queue_full(&mut stream, &before).unwrap();
    assert!(!stream.ready_to_send(baseline.last));
    stream.create_arrived();
    assert!(stream.ready_to_send(baseline.last));
    stream.sent(baseline.last);
    stream.arrived(baseline.last);
    let initial_gate_damage = scene
        .gate_snapshot()
        .unwrap()
        .variables
        .get(&8569)
        .cloned()
        .unwrap_or(Value::Null);
    let gate = scene.junkenstein_gate().unwrap();
    let gate = EnemyGate {
        entity: gate.entity,
        position: gate.position,
    };
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let codecs = Arc::new(CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap());
    let catalog = NpcCatalog::load(root.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
    let definition = catalog.get(0x02e0_0000_0000_0175).unwrap();
    let variables = Arc::new(scene.encounter_snapshot().unwrap().variables.clone());
    let request = EntityCreation {
        position: [gate.position[0] + 1.0, gate.position[1], gate.position[2]],
        kind: CreationKind::Hero {
            hero: definition.hero,
            team: 1,
            yaw: 0.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: None,
            skin: None,
        },
    };
    let spawn = |scene: &mut crate::scene_assets::PreparedScene, request: &EntityCreation, tick| {
        scene
            .bot_simulation()
            .0
            .create_enemy(EnemySpawn {
                graphs: Arc::clone(&graphs),
                codecs: Arc::clone(&codecs),
                definition,
                request,
                mode: card.mode,
                mode_variables: Arc::clone(&variables),
                tick,
                walk: crate::game_bots::BotWalk::default(),
                gate: Some(gate),
            })
            .unwrap()
    };
    let entity = spawn(&mut scene, &request, 4200);
    let mut saw_dead = false;
    for tick in 4200..=4800 {
        scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
        let (room, queries) = scene.bot_simulation();
        room.set_external_targets(Vec::new());
        room.finish_tick(
            &queries,
            tick,
            Duration::from_millis(u64::from(tick - 4200) * 16),
        )
        .unwrap();
        if let Some(state) = state(room, entity) {
            assert!(
                state.enemy.as_ref().unwrap().fault.is_none(),
                "tick {tick}: {:?}",
                state.enemy.as_ref().unwrap().fault
            );
            if state.bot.dead {
                if !saw_dead {
                    assert_eq!(state.vitals.death().unwrap().frame, tick);
                }
                saw_dead = true;
                assert!(state.respawn_at.is_none());
            }
        }
    }
    let (room, _) = scene.bot_simulation();
    assert!(
        saw_dead,
        "Zomnic did not die: gate={gate:?}, actor={:?}",
        state(room, entity).map(|state| (
            state.bot.position,
            state.vitals.total(),
            state.enemy.as_ref().map(|enemy| &enemy.fault),
        ))
    );
    assert!(state(room, entity).is_none());
    assert_eq!(room.take_retired_enemies(), [entity]);
    assert!(!room.take_scripted_health().is_empty());
    let gate = scene.gate_snapshot().unwrap();
    assert!(gate.diagnostics.is_empty(), "{:?}", gate.diagnostics);
    assert_ne!(
        gate.variables.get(&8569),
        Some(&initial_gate_damage),
        "gate damage counter did not change: {:?}",
        gate.variables
    );
    assert_eq!(gate.instances[&1].states[&19].activation, 1);
    let projection = scene.gate_projection().unwrap();
    let after = projection
        .encoder
        .project_next(projection.snapshot, &before)
        .unwrap();
    assert!(after.instance_states(1).unwrap().contains(&0));
    let hit = projection
        .encoder
        .queue_delta(
            &mut stream,
            &after,
            DeltaKind::Observer { resend: true },
            16,
        )
        .unwrap()
        .unwrap();
    stream.sent(hit.last);
    stream.arrived(hit.last);
    assert_eq!(
        scene.take_encounter_voice_requests(),
        [crate::pve_encounter::voice::VoiceRequest {
            anchor: 0x8000_0032,
            speaker: crate::world::GAME_MODE_ENTITY,
            stimulus: 0x0ee0_0000_0000_06c7,
            line: 0x0760_0000_0000_8c71,
        }]
    );
    scene.step_encounter(5400, None);
    assert!(!scene.gate_snapshot().unwrap().instances[&1].states[&19].active);
    assert!(scene.take_encounter_voice_requests().is_empty());

    let gate = scene.junkenstein_gate().unwrap().entity;
    for (tick, remaining, expected_state) in [
        (5401, 0.625, 6),
        (5402, 0.375, 7),
        (5403, 0.125, 8),
        (5404, 0.0, 8),
    ] {
        let snapshot = scene.gate_snapshot().unwrap();
        let capacity = to_float(&snapshot.resolved_bindings[&(0, 7786)]);
        assert!(capacity > 0.0);
        let consumed = to_float(&snapshot.variables[&8569]);
        scene.bot_simulation().0.queue_scene_message(SceneMessage {
            target: gate,
            message: 0x0240_0000_0000_06b2,
            parameters: Variables::from([
                (0x3ac, Value::Float(capacity * (1.0 - remaining) - consumed)),
                (0x12, Value::Entity(entity)),
            ]),
        });
        scene.step_encounter(tick, None);
        let projection = scene.gate_projection().unwrap();
        assert!(
            projection.snapshot.diagnostics.is_empty(),
            "{:?}",
            projection.snapshot.diagnostics
        );
        assert_eq!(to_float(&projection.snapshot.variables[&8571]), remaining);
        let next = projection
            .encoder
            .project_next(projection.snapshot, stream.base().unwrap().1)
            .unwrap();
        projection.encoder.full(&next).unwrap();
        let states = next.instance_states(1).unwrap();
        assert_eq!(
            states
                .into_iter()
                .filter(|raw| [0, 6, 7, 8].contains(raw))
                .collect::<Vec<_>>(),
            [expected_state],
        );
        if let Some(delta) = projection
            .encoder
            .queue_delta(&mut stream, &next, DeltaKind::Observer { resend: true }, 16)
            .unwrap()
        {
            stream.sent(delta.last);
            stream.arrived(delta.last);
        } else {
            assert_eq!(remaining, 0.0);
        }
    }
    let notices = scene.take_encounter_voice_requests();
    assert_eq!(
        notices
            .iter()
            .map(|notice| notice.stimulus)
            .collect::<Vec<_>>(),
        [0x0ee0_0000_0000_06b8, 0x0ee0_0000_0000_06b9],
    );
    let street = EntityCreation {
        position: [74.0, 12.6, -99.0],
        ..request
    };
    let survivor = spawn(&mut scene, &street, 5405);
    let (room, queries) = scene.bot_simulation();
    let source = crate::world::GAME_MODE_ENTITY;
    let caller = crate::script_init::Site {
        entity: source,
        instance: 2,
        graph: 0x0cba,
        node: Some(9),
    };
    assert!(room.queue_encounter_wave(
        caller,
        survivor + 1,
        crate::script_init::PlayScript {
            policy: 0,
            overrides: Vec::new(),
            graph: 0x0c95,
            source,
            parameters: vec![(
                0x0a40_0000_0000_03a1,
                Value::Array(vec![Value::Vector(street.position)])
            )],
            auto_retire: true,
        }
    ));
    room.finish_tick(&queries, 5405, Duration::from_millis((5405 - 4200) * 16))
        .unwrap();
    assert!(room.encounter_body_alive(survivor));
    assert!(
        state(room, survivor)
            .unwrap()
            .enemy
            .as_ref()
            .unwrap()
            .scripts
            .body
            .snapshot()
            .unwrap()
            .instances
            .values()
            .any(|instance| instance.graph == 0x0cbc)
    );
    for tick in 5405..=5900 {
        scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
    }
    let (room, queries) = scene.bot_simulation();
    room.finish_tick(&queries, 5900, Duration::from_millis((5900 - 4200) * 16))
        .unwrap();
    if let Some(survivor) = state(room, survivor) {
        let enemy = survivor.enemy.as_ref().unwrap();
        let snapshot = enemy.scripts.body.snapshot().unwrap();
        panic!(
            "result did not retire live Zomnic: fault={:?} requested={} roots={:?}",
            enemy.fault,
            enemy.scripts.retirement_requested,
            snapshot
                .instances
                .values()
                .map(|instance| (instance.graph, instance.variables.get(&8617)))
                .collect::<Vec<_>>()
        );
    }
    assert_eq!(room.take_retired_enemies(), [survivor]);
    let mode = scene.encounter_snapshot().unwrap();
    let root = &mode.instances[&2];
    assert_eq!(root.variables[&59], Value::Integer(5));
    assert_eq!(root.states[&24].activation, 1);
    let handoff = root
        .states
        .get(&8)
        .unwrap_or_else(|| panic!("missing authored phase handoff: {:?}", mode.diagnostics));
    assert_eq!(handoff.activation, 1);
    assert_eq!(
        handoff.kind,
        crate::script_init::StateKind::PersistentSend(
            crate::pve_encounter::messages::POST_MATCH_PHASE
        ),
    );
    let phase = scene.encounter_post_match_wire().unwrap().unwrap();
    let native_result = scene.encounter_match_result().unwrap();
    assert_eq!(native_result.index, 114);
    assert_eq!(
        native_result.fields[7],
        Some(crate::world::ComponentValue::I32(1)),
        "native recap must identify the enemy winner after gate destruction"
    );
    assert!(matches!(
        native_result.fields[9],
        Some(crate::world::ComponentValue::F32(seconds)) if seconds > 0.0
    ));
    let mut wire = crate::bits::BitReader::new(phase.as_bytes());
    assert_eq!(wire.bits(2).unwrap(), 0);
    assert_eq!(wire.w_u16().unwrap(), 2);
    assert_eq!(wire.bits(2).unwrap(), 0); // existing mode root
    assert_eq!(wire.w_u16().unwrap(), 0);
    let screens = mode
        .instances
        .values()
        .find(|instance| instance.graph == 0x180a)
        .unwrap();
    assert_eq!(
        screens.states.get(&3).map(|state| state.activation),
        Some(1),
        "{:?}",
        mode.diagnostics
    );

    assert_eq!(screens.states[&21].activation, 1);
    assert!((to_float(&screens.variables[&15076]) - 41.1).abs() < 0.0001);
    let Value::Array(sequence) = &screens.variables[&16638] else {
        panic!("the post-match screen sequence must be an array");
    };
    assert_eq!(sequence.len(), 4);
    let Value::Map(summary) = &sequence[3] else {
        panic!("the fourth screen phase must have a configuration map");
    };
    assert!(summary.iter().any(|(key, value)| {
        *key == Value::Integer(16748)
            && matches!(value, Value::Array(graphs) if graphs.first() == Some(&Value::Asset(0x0580_0000_0000_0283)))
    }));

    let (&result_id, results) = mode
        .instances
        .iter()
        .find(|(_, instance)| instance.graph == 0x0cc7)
        .unwrap();
    assert_eq!(results.states[&2].activation, 1);
    assert_eq!(
        results.states[&2].kind,
        crate::script_init::StateKind::PersistentSend(
            crate::pve_encounter::messages::RESULT_SUMMARY
        ),
    );
    assert_eq!(mode.variables[&8525], Value::Integer(0));
    let payload = scene.encounter_result_wire().unwrap().unwrap();
    let mut wire = crate::bits::BitReader::new(payload.as_bytes());
    assert_eq!(wire.bits(2).unwrap(), 0);
    assert_eq!(wire.w_u16().unwrap(), result_id);
    assert_eq!(wire.bits(2).unwrap(), 2);
    assert_eq!(wire.bits(16).unwrap(), 0x0cc7);
    assert_eq!(wire.bits(2).unwrap(), 0);
    assert!(wire.bit().unwrap());
    assert_eq!((wire.w_var().unwrap(), wire.w_var().unwrap()), (2, 13));
    let outcome = scene.encounter_outcome_wire(41).unwrap().unwrap();
    let mut wire = crate::bits::BitReader::new(outcome.as_bytes());
    assert_eq!(wire.bits(2).unwrap(), 0);
    assert_eq!(wire.w_u16().unwrap(), 0);
    assert!(!wire.bit().unwrap()); // empty entity variables
    assert!(wire.bit().unwrap()); // frame action present
    assert_eq!(wire.w_u32().unwrap(), 41);
    wire.w_var().unwrap();
    assert_eq!(wire.bits(2).unwrap(), 1);
    wire.bits(8).unwrap();
    assert!(wire.bit().unwrap());
    assert_eq!(wire.w_u16().unwrap(), 2);
    assert_eq!(wire.bits(6).unwrap(), 45);
    assert_eq!(wire.bits(3).unwrap(), 0);
    let gate = scene.gate_snapshot().unwrap();
    assert_eq!(gate.instances[&1].variables[&1303], Value::Integer(3));
    let messages: Vec<_> = scene
        .bot_simulation()
        .0
        .take_defender_messages()
        .into_iter()
        .filter(|message| message.message == crate::pve_encounter::messages::ENCOUNTER_RESULT)
        .collect();
    let [result] = messages.as_slice() else {
        panic!("expected one authored match result, received {messages:?}");
    };
    assert_eq!(result.source, crate::world::GAME_MODE_ENTITY);
    assert!(!crate::script_expr::truthy(&result.parameters[&0x3ae]));
    assert_eq!(result.parameters[&0x3b5], Value::Integer(3));
    let hero = scene.heroes().find_hero("Cassidy").unwrap();
    let mut mover = scene.player_spawn_mover(hero.body, 0).unwrap();
    let pose = mover.pose();
    let mut owner = scene
        .owner_body_runtime(
            hero,
            crate::script_player::OwnerBodySpawn {
                slot: 0,
                controller: 0xa000_0100,
                entity: 0xa000_0101,
                body_filter: crate::match_scene::team_bit(0).unwrap(),
                frame: 5900,
                pose,
                eye_position: None,
                skin: 0,
                quanta: RETAIL_QUANTA,
            },
        )
        .unwrap()
        .unwrap();
    assert_eq!(owner.receive_defender_message(result.clone()).unwrap(), 1);
    let snapshot = owner.snapshot().unwrap();
    let defender = snapshot
        .instances
        .values()
        .find(|instance| instance.graph == 0x0ca5)
        .unwrap();
    assert_eq!(defender.states[&32].activation, 1);
    assert_eq!(defender.variables[&8504], Value::Integer(3));
    assert!(
        snapshot.diagnostics.is_empty(),
        "{:?}",
        snapshot.diagnostics
    );
    let encoder = owner.encoder();
    let projected = encoder.project(snapshot, View::Owner).unwrap();
    encoder.full(&projected).unwrap();
    assert_eq!(owner.cinematic_aim(), Some([pose.yaw, pose.pitch]));
    mover.set_cinematic_aim(owner.cinematic_aim());
    mover
        .step(
            scene.movement_index(),
            Command {
                frame: 5901,
                yaw: pose.yaw.wrapping_add(1200),
                pitch: pose.pitch.wrapping_sub(1200),
                ..Command::default()
            },
            0.016,
            |_, _| {},
        )
        .unwrap();
    assert_eq!(
        [mover.pose().yaw, mover.pose().pitch],
        [pose.yaw, pose.pitch]
    );
    let mut phases = Vec::new();
    let mut previous = Vec::new();
    let mut summary_instance = None;
    let mut screen_projection = None;
    assert!(!scene.encounter_screens_complete());

    for tick in 5901..=8500 {
        scene.step_encounter(tick, Some(crate::game_heroes::CASSIDY));
        let mode = scene.encounter_snapshot().unwrap();
        let scopes: Vec<_> = mode
            .instances
            .iter()
            .filter_map(|(&id, instance)| {
                let (scope, 16) = instance.parent? else {
                    return None;
                };
                let (manager, 17) = mode.instances.get(&scope)?.parent? else {
                    return None;
                };
                (mode.instances[&manager].graph == 0x180a).then_some((id, instance.graph))
            })
            .collect();
        if let Some((id, _)) = scopes.iter().find(|(_, graph)| *graph == 0x0283) {
            assert_eq!(summary_instance.get_or_insert(*id), id);
            assert!(!scene.encounter_screens_complete());
        }
        if scopes != previous {
            let (projection, payload) = scene
                .encounter_screen_delta(screen_projection.as_ref())
                .unwrap()
                .unwrap();
            if previous.is_empty() {
                let (&manager, instance) = mode
                    .instances
                    .iter()
                    .find(|(_, instance)| {
                        instance.graph == 0x180a && instance.states.contains_key(&4)
                    })
                    .unwrap();
                assert_eq!(instance.states[&4].activation, 1);
                assert_eq!(
                    instance.states[&4].kind,
                    crate::script_init::StateKind::PersistentSend(
                        crate::pve_encounter::messages::POST_MATCH_SIGNAL
                    )
                );
                assert!(projection.instance_states(manager).unwrap().contains(&4));
                assert_eq!(
                    projection.instance_variables(manager).unwrap().get(&7525),
                    Some(&Value::Integer(1)),
                    "the client screen manager needs the resolved defeat winner"
                );
            }
            if let Some(&(banner, _)) = scopes.iter().find(|(_, graph)| *graph == 0x027f) {
                assert_eq!(
                    projection.instance_variables(banner).unwrap()[&7525],
                    Value::Integer(1)
                );
            }
            if scopes.iter().any(|&(_, graph)| graph == 0x0283) {
                assert_eq!(
                    projection.entity_variables().get(&10274),
                    Some(&Value::Bool(true)),
                    "the recap presenter needs its authored entity input"
                );
            }
            let mut wire = crate::bits::BitReader::new(payload.as_bytes());
            assert_eq!(wire.bits(2).unwrap(), 0);
            let mut seen = std::collections::BTreeMap::new();
            let mut id = 0;
            loop {
                let increment = wire.w_u16().unwrap();
                if increment == 0 {
                    break;
                }
                id += increment;
                if wire.bit().unwrap() {
                    assert!(!mode.instances.contains_key(&id));
                    seen.insert(id, None);
                } else if wire.bit().unwrap() {
                    let graph = wire.bits(16).unwrap();
                    assert_eq!(wire.bits(2).unwrap(), 0);
                    let parent = if wire.bit().unwrap() {
                        Some((
                            wire.w_var().unwrap().try_into().unwrap(),
                            wire.w_var().unwrap().try_into().unwrap(),
                        ))
                    } else {
                        None
                    };
                    assert!(!wire.bit().unwrap());
                    if scopes.iter().any(|&(scope, _)| scope == id) {
                        assert_eq!(parent, None);
                    } else {
                        assert_eq!(mode.instances[&id].parent, parent);
                        assert_ne!(
                            parent.map(|(_, raw)| raw),
                            Some(17),
                            "server list row was sent"
                        );
                    }
                    assert_eq!(u64::from(mode.instances[&id].graph), graph);
                    seen.insert(id, Some(graph));
                }
            }
            for &(id, graph) in &scopes {
                if !previous.iter().any(|&(previous, _)| previous == id) {
                    assert_eq!(seen[&id], Some(u64::from(graph)));
                }
            }
            for &(id, _) in &previous {
                if !scopes.iter().any(|&(current, _)| current == id) {
                    assert_eq!(seen[&id], None);
                }
            }
            let mut stream = crate::script_stream::Stream::new(crate::world::GAME_MODE_ENTITY);
            let initial = stream.full(scene.mode_ui()).unwrap();
            assert!(stream.arrived(initial.last));
            let chunk = stream.reliable_delta(&payload).unwrap();
            assert!(stream.data_in_flight());
            assert!(stream.arrived(chunk.last));
            assert!(!stream.data_in_flight());
            let packet =
                crate::world::frame(u32::MAX, u32::MAX, &[], &[], &[&chunk.update]).unwrap();
            assert!(
                packet.len() <= crate::world::MAX_PAYLOAD,
                "screen delta too large: {}",
                packet.len()
            );
            screen_projection = Some(projection);
            phases.push(scopes.clone());
            previous = scopes;
        }
    }
    let graphs: Vec<Vec<_>> = phases
        .iter()
        .map(|phase| {
            let mut graphs: Vec<_> = phase.iter().map(|(_, graph)| *graph).collect();
            graphs.sort_unstable();
            graphs
        })
        .collect();
    assert_eq!(
        graphs,
        [
            vec![0x027f, 0x0d94, 0x17df, 0x181e, 0x184c],
            vec![0x0281, 0x17df, 0x17e2, 0x181d, 0x184c, 0x1f93],
            vec![0x0282, 0x17df, 0x17e2, 0x1818, 0x184d],
            vec![0x0283, 0x0b46, 0x17e3, 0x17e4, 0x184d],
            vec![],
        ]
    );
    for (graph, earlier, later) in [(0x17df, 0, 1), (0x17e2, 1, 2), (0x184d, 2, 3)] {
        let id = |index: usize| {
            phases[index]
                .iter()
                .find(|(_, candidate)| *candidate == graph)
                .unwrap()
                .0
        };
        assert_eq!(id(earlier), id(later));
    }
    let summary_instance = summary_instance.expect("one authored results graph");
    assert!(scene.encounter_screens_complete());
    assert!(
        !scene
            .encounter_snapshot()
            .unwrap()
            .instances
            .contains_key(&summary_instance)
    );
}

pub(super) const FIXTURE_CONTEXT: ShotContext = ShotContext {
    team: 0,
    quanta: RETAIL_QUANTA,
    tick: 1000,
    now: Duration::from_secs(4),
};

pub(super) fn state(bots: &RoomSimulation, wire_id: u32) -> Option<&BotState> {
    bots.world.get(*bots.by_wire.get(&wire_id)?)
}

pub(super) fn flight_fixture(owner: BodyId, speed: f64) -> AdmittedFlight {
    use crate::game_projectiles::{OwnerProjectiles, ProjectileLeases};

    let frame = FIXTURE_CONTEXT.tick;
    let definition = 0x0400_0000_0000_072c;
    let leases = ProjectileLeases::new(0, frame, [definition]).unwrap();
    let token = OwnerProjectiles::new(owner.entity, leases)
        .admit(
            ShotEvent {
                entity: owner.entity,
                instance: 1,
                graph: 42,
                raw_state: 0,
                frame,
                time_ms: 0,
                activation: 1,
                activation_ms: 0,
                ordinal: 0,
            },
            definition,
            3,
        )
        .unwrap();
    let flight = ProjectileFlight::new(
        ProjectileBallistics {
            speed,
            lifetime: 0.04,
            max_distance: None,
            terrain_rebound: None,
            terrain_radius: 0.0,
            radius: 0.0,
            definition: Some(definition),
            direct: None,
            blast: None,
        },
        [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0],
        None,
    )
    .unwrap();
    AdmittedFlight {
        token,
        launch_frame: 0,
        flight,
        stun: None,
        hit_effect: None,
        impact_spawn: None,
        presentation: None,
    }
}

#[test]
fn owner_splash_uses_the_same_calibrated_shape_as_other_targets() {
    use crate::{
        contact_world::ContactWorld,
        game_hitscan::HurtVolumeCatalog,
        mover_settings::{MoverCatalog, MoverSettings},
        script_player::fixture_body_factory,
        script_wire::Value,
    };
    use serde_json::{Value as Json, json};

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/projectile-self-splash-v1.json"
    ))
    .unwrap();
    let calibrated = &fixture["calibrated_owner"];
    let input = &fixture["input"];
    let (factory, mut spawn) = fixture_body_factory();
    spawn.pose.position = serde_json::from_value(calibrated["feet"].clone()).unwrap();
    let owner = BodyId {
        entity: spawn.entity,
        generation: 1,
    };
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let body_template = 0x0400_0000_0000_ABCD;
    let runtime = Box::new(factory.start(spawn).unwrap());
    let state = PlayerBodyState::new(
        owner,
        spawn.controller,
        spawn.slot,
        0,
        SOLDIER,
        mover,
        Some(runtime),
        spawn.frame,
        None,
    )
    .with_body_template(body_template);
    let before = state.vitals().unwrap().total();
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.spawn_player_body(state).unwrap();
    let mut launch = flight_fixture(owner, input["projectile_speed"].as_f64().unwrap());
    let ballistics = ProjectileBallistics::from_fields(
        &json!({
            "m_projectileSpeed": input["projectile_speed"],
            "m_D2D11CE9": {
                "m_8A3E3883": input["self_damage_scale"],
                "m_modifyHealth": {"m_amount": input["blast_damage"]},
                "m_0D30E40C": [{"m_radius": calibrated["radius"], "m_B399CACB": 1.0}]
            }
        }),
        |value| Ok(value.as_f64().map_or(Value::Null, Value::Float)),
    )
    .unwrap()
    .unwrap();
    launch.flight = ProjectileFlight::new(
        ballistics,
        serde_json::from_value(calibrated["launch_origin"].clone()).unwrap(),
        [-1.0, 0.0, 0.0],
        None,
    )
    .unwrap();
    let catalog = HurtVolumeCatalog::from_json(&json!({
        "version": 1, "bodies": {(format!("0x{body_template:016X}")): calibrated["hurt_profile"]}
    }).to_string()).unwrap();
    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None).with_hurt_volumes(&catalog);
    let (outcomes, hits) = room
        .advance_projectiles(&queries, 7, owner, vec![launch], FIXTURE_CONTEXT, |_| true)
        .unwrap();
    assert_eq!(outcomes.len(), 1);
    assert!(hits.is_empty());
    let expected = fixture["expected"]["owner_damage"].as_f64().unwrap();
    assert_eq!(
        room.player_body(owner).unwrap().vitals().unwrap().total(),
        before - expected
    );
}

#[test]
fn projectile_stun_applies_only_to_surviving_hits_and_expires_exclusively() {
    use crate::{
        collision_cache::{CollisionCache, Mesh, Triangle},
        collision_scene::CollisionScene,
        mover_settings::MoverCatalog,
    };
    let cache = CollisionCache {
        header: serde_json::from_str(
            r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
        )
        .unwrap(),
        meshes: vec![Mesh {
            vertices: vec![[-1.0, 0.0, 0.0], [-1.0, 2.0, 0.0], [-1.0, 0.0, 1.0]],
            triangles: vec![Triangle {
                corners: [0, 1, 2],
                wings: [-1; 3],
                mask: 0x18,
                surface: 0,
            }],
        }],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    };
    let ground = CollisionScene::movement_probe(&cache, &Default::default()).unwrap();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    assert_eq!(Stun::from_seconds(f64::from(0.8f32)).unwrap().ticks, 50);
    assert_eq!(Stun::from_seconds(f64::from(1.7f32)).unwrap().ticks, 107);
    for value in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        assert!(Stun::from_seconds(value).is_none());
    }
    for (amount, visible) in [(25.0, true), (1000.0, true), (25.0, false)] {
        let bots = fixture_bots();
        let victim = bots[0].entity;
        let unhit = bots[1].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        let owner = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        let mut launch = flight_fixture(owner, 100.0);
        launch.stun = Stun::from_seconds(0.8);
        launch.flight = ProjectileFlight::new(
            ProjectileBallistics {
                speed: 100.0,
                lifetime: 1.0,
                max_distance: None,
                terrain_rebound: None,
                terrain_radius: 0.0,
                radius: 0.0,
                definition: None,
                direct: Some(HitDefinition {
                    tags: crate::game_hitscan::DamageTags::EMPTY,
                    amount,
                    loadout: None,
                    critical: 1.0,
                    falloff: None,
                }),
                blast: None,
            },
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            None,
        )
        .unwrap();
        let (_, hits) = room
            .advance_projectiles(&queries, 7, owner, vec![launch], FIXTURE_CONTEXT, |wire| {
                wire != victim || visible
            })
            .unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(
            hits[0].damage.target_facts.target,
            if visible { victim } else { unhit }
        );
        let bot = &state(&room, victim).unwrap().bot;
        assert_eq!(bot.is_stunned(1049), visible && amount == 25.0);
        assert!(!bot.is_stunned(1050));
        assert_eq!(state(&room, unhit).unwrap().bot.is_stunned(1001), !visible);
    }
}

pub(super) fn fixture_ground() -> crate::collision_scene::CollisionScene {
    use crate::{
        collision_cache::{CollisionCache, Mesh, Triangle},
        collision_scene::CollisionScene,
    };

    let cache = CollisionCache {
        header: serde_json::from_str(
            r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
        )
        .unwrap(),
        meshes: vec![Mesh {
            vertices: vec![[-1.0, 0.0, 0.0], [-1.0, 2.0, 0.0], [-1.0, 0.0, 1.0]],
            triangles: vec![Triangle {
                corners: [0, 1, 2],
                wings: [-1; 3],
                mask: 0x18,
                surface: 0,
            }],
        }],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    };
    CollisionScene::movement_probe(&cache, &Default::default()).unwrap()
}

#[test]
fn npc_weapons_share_calibrated_body_contact_and_target_admission() {
    use crate::{
        game_hitscan::{DamageTags, HurtVolumeCatalog},
        mover_settings::MoverCatalog,
        script_enemy::EnemyProjectile,
    };
    #[derive(serde::Deserialize)]
    struct Case {
        height: f64,
        free_for_all: bool,
        expected_damage: Option<f64>,
        expected_hitscan_damage: Option<f64>,
    }
    #[derive(serde::Deserialize)]
    struct Contract {
        volumes: serde_json::Value,
        cases: Vec<Case>,
    }
    let contract: Contract = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/npc-projectile-targeting-v1.json",
    ))
    .unwrap();
    let volumes = HurtVolumeCatalog::from_json(&contract.volumes.to_string()).unwrap();
    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    for case in contract.cases {
        let bots = fixture_bots();
        let source = bots[0].entity;
        let target = bots[1].entity;
        let queries =
            BotQueries::new(case.free_for_all, &movers, &ground, None).with_hurt_volumes(&volumes);
        let mut room = RoomSimulation::new(bots).unwrap();
        let definition = HitDefinition {
            tags: DamageTags::EMPTY,
            amount: 10.0,
            loadout: None,
            critical: 2.0,
            falloff: None,
        };
        let origin = [0.0, case.height, 0.0];
        let direction = [1.0, 0.0, 0.0];
        let hitscan = HitscanShot::with_direction(origin, direction, 8.0, definition);
        let selected = room.npc_weapon_targets(&queries, 0).unwrap();
        let contacts = selected
            .iter()
            .filter_map(|candidate| candidate.hostile_to(source, 1, case.free_for_all));
        let impact = hitscan
            .resolve_combat(contacts, |o, d, r| queries.hitscan_wall(o, d, r))
            .unwrap();
        assert_eq!(
            impact.map(|impact| (impact.entity, impact.damage)),
            case.expected_hitscan_damage.map(|amount| (target, amount))
        );
        let flight = ProjectileFlight::new(
            ProjectileBallistics {
                speed: 200.0,
                lifetime: 1.0,
                max_distance: None,
                radius: 0.0,
                terrain_radius: 0.0,
                terrain_rebound: None,
                definition: None,
                direct: Some(definition),
                blast: None,
            },
            origin,
            direction,
            None,
        )
        .unwrap();
        let handle = room
            .world
            .spawn(EnemyProjectile {
                source,
                team: 1,
                flight,
                damage_tags: Arc::from([]),
                split: None,
            })
            .id();
        room.enemy_projectiles.push(handle);
        let mut damage = Vec::new();
        room.advance_enemy_projectiles(&queries, FIXTURE_CONTEXT.tick, &mut damage)
            .unwrap();
        assert_eq!(
            damage
                .iter()
                .map(|hit| (hit.source, hit.target, hit.amount))
                .collect::<Vec<_>>(),
            case.expected_damage
                .map(|amount| (source, target, amount))
                .into_iter()
                .collect::<Vec<_>>(),
        );
    }
}

#[test]
fn projectile_flights_keep_peer_generation_and_report_terminal_outcomes() {
    use crate::mover_settings::MoverCatalog;

    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    let mut context = FIXTURE_CONTEXT;
    let owner = BodyId {
        entity: 0xa000_0101,
        generation: 1,
    };
    let other = BodyId {
        entity: 0xa100_0101,
        generation: 1,
    };
    let first = flight_fixture(owner, 1.0);
    let second = flight_fixture(other, 1.0);
    let token = first.token;
    assert_eq!(token, second.token);
    for (connection, owner, flight) in [(7, owner, first), (8, other, second)] {
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, connection, owner, vec![flight], context, |_| true)
            .unwrap();
        assert!(outcomes.is_empty() && hits.is_empty());
    }
    let old_handle = simulation.projectiles[&7].order[0];
    let other_handle = simulation.projectiles[&8].order[0];

    context.tick += 3;
    let owner = BodyId {
        generation: 2,
        ..owner
    };
    let (outcomes, hits) = simulation
        .advance_projectiles(&queries, 7, owner, Vec::new(), context, |_| true)
        .unwrap();
    assert!(outcomes.is_empty() && hits.is_empty());
    assert!(simulation.world.get::<AdmittedFlight>(old_handle).is_none());
    assert!(
        simulation
            .world
            .get::<AdmittedFlight>(other_handle)
            .is_some()
    );

    let (outcomes, hits) = simulation
        .advance_projectiles(&queries, 8, other, Vec::new(), context, |_| true)
        .unwrap();
    assert!(hits.is_empty());
    assert!(matches!(
        outcomes.as_slice(),
        [FlightOutcome::Ended { token: observed, travelled, impact: false }]
            if *observed == token && (*travelled - 0.04).abs() < 1e-12
    ));
    assert!(
        simulation
            .world
            .get::<AdmittedFlight>(other_handle)
            .is_none()
    );

    let too_far = flight_fixture(owner, 10_000_000.0);
    let (outcomes, hits) = simulation
        .advance_projectiles(&queries, 7, owner, vec![too_far], context, |_| true)
        .unwrap();
    assert!(hits.is_empty());
    assert!(matches!(outcomes.as_slice(), [FlightOutcome::Failed(observed)] if *observed == token));
    assert!(simulation.projectiles.is_empty());
    assert!(simulation.world.entities().is_empty());
}

#[test]
fn broken_bot_indexes_are_errors_and_do_not_partially_respawn_a_batch() {
    use crate::mover_settings::MoverCatalog;

    let context = FIXTURE_CONTEXT;
    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let mut simulation = RoomSimulation::new(fixture_bots()).unwrap();
    let first = BOT_ENTITY;
    let second = first + 1;
    let first_handle = simulation.by_wire[&first];
    let broken_handle = simulation.by_wire[&second];
    simulation
        .apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
        .unwrap();
    simulation
        .world
        .entity_mut(broken_handle)
        .remove::<BotState>();
    let missing = |error: CombatError| {
        assert!(matches!(error, CombatError::MissingBot { wire, entity }
            if wire == second && entity == broken_handle));
    };
    missing(simulation.bodies().err().unwrap());
    missing(simulation.body_healths().unwrap_err());
    missing(simulation.debug_actors().err().unwrap());
    missing(simulation.for_each_body_mut(|_| {}).unwrap_err());
    missing(simulation.record_poses(&queries, context.tick).unwrap_err());
    missing(
        simulation
            .apply_bot_impact(7, context.tick, context, &fixture_impact(second, 1.0))
            .err()
            .unwrap(),
    );
    let next_bot = simulation.next_bot;
    missing(
        simulation
            .respawn_due(context.tick + 1, context.now + BOT_RESPAWN_TIME)
            .err()
            .unwrap(),
    );
    assert_eq!(simulation.next_bot, next_bot);
    assert_eq!(simulation.by_wire[&first], first_handle);
    assert!(state(&simulation, first).unwrap().bot.dead);

    // A present component with a different wire identity is also invalid,
    // rather than an empty body list or a different target to damage.
    let mut simulation = RoomSimulation::new(fixture_bots()).unwrap();
    simulation.body_mut(first).unwrap().entity = second;
    assert!(
        matches!(simulation.bodies(), Err(CombatError::BotIdentity { wire, actual })
        if wire == first && actual == second)
    );
}

#[test]
fn missing_flight_component_is_explicit_and_retirement_is_idempotent() {
    use crate::mover_settings::MoverCatalog;

    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    let owner = BodyId {
        entity: 0xa000_0101,
        generation: 1,
    };
    simulation
        .advance_projectiles(
            &queries,
            7,
            owner,
            vec![flight_fixture(owner, 1.0)],
            FIXTURE_CONTEXT,
            |_| true,
        )
        .unwrap();
    let entity = simulation.projectiles[&7].order[0];
    simulation
        .world
        .entity_mut(entity)
        .remove::<AdmittedFlight>();
    assert!(matches!(
        simulation.advance_projectiles(&queries, 7, owner, Vec::new(), FIXTURE_CONTEXT, |_| true),
        Err(CombatError::MissingFlight { connection: 7, entity: missing }) if missing == entity
    ));
    simulation.retire_projectiles(7);
    simulation.retire_projectiles(7);
    simulation.clear_projectiles();
    simulation.clear_projectiles();
    assert!(simulation.world.entities().is_empty());
    assert!(simulation.projectiles.is_empty());
}

#[test]
fn stale_generation_does_not_retire_or_advance_the_replacement_flight() {
    use crate::mover_settings::MoverCatalog;

    let ground = fixture_ground();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
    let owner = BodyId {
        entity: 0xa000_0102,
        generation: 2,
    };
    let old = BodyId {
        entity: 0xa000_0101,
        generation: 1,
    };
    simulation
        .advance_projectiles(
            &queries,
            7,
            owner,
            vec![flight_fixture(owner, 1.0)],
            FIXTURE_CONTEXT,
            |_| true,
        )
        .unwrap();
    let handle = simulation.projectiles[&7].order[0];
    let later = ShotContext {
        tick: FIXTURE_CONTEXT.tick + 3,
        ..FIXTURE_CONTEXT
    };
    let (outcomes, hits) = simulation
        .advance_projectiles(
            &queries,
            7,
            old,
            vec![flight_fixture(old, 1.0)],
            later,
            |_| true,
        )
        .unwrap();
    assert!(outcomes.is_empty() && hits.is_empty());
    assert_eq!(simulation.projectiles[&7].order, [handle]);
    assert_eq!(simulation.world.entities().len(), 1);
    assert!(matches!(
        simulation.advance_projectiles(
            &queries,
            7,
            BodyId {
                entity: old.entity,
                ..owner
            },
            Vec::new(),
            later,
            |_| true
        ),
        Err(CombatError::FlightOwnerIdentity { .. })
    ));
    let (outcomes, hits) = simulation
        .advance_projectiles(&queries, 7, owner, Vec::new(), later, |_| true)
        .unwrap();
    assert!(hits.is_empty());
    assert!(
        matches!(outcomes.as_slice(), [FlightOutcome::Ended { travelled, .. }]
        if (*travelled - 0.04).abs() < 1e-12)
    );
    assert!(simulation.world.entities().is_empty());
}

#[test]
fn duplicate_or_exhausted_bot_identities_are_rejected_at_admission() {
    let mut bots = fixture_bots();
    bots[1].entity = bots[0].entity;
    assert!(matches!(
        RoomSimulation::new(bots),
        Err(CombatError::BotIdInUse(BOT_ENTITY))
    ));
    let mut bots = fixture_bots();
    bots[1].entity = u32::MAX;
    assert!(matches!(
        RoomSimulation::new(bots),
        Err(CombatError::BotIdsExhausted)
    ));
}

#[test]
fn bot_damage_history_and_respawn_follow_identity_after_scene_reorder() {
    use crate::game_hitscan::TargetPose;

    let mut bots = RoomSimulation::new(fixture_bots()).unwrap();
    bots.activate_combat();
    let first = BOT_ENTITY;
    let second = BOT_ENTITY + 1;
    let first_handle = bots.by_wire[&first];
    let second_handle = bots.by_wire[&second];
    let context = FIXTURE_CONTEXT;
    let pose = TargetPose {
        feet: state(&bots, first).unwrap().bot.position,
        height: 1.8,
        radius: 0.5,
        eye: 1.6,
    };
    let second_pose = TargetPose {
        feet: state(&bots, second).unwrap().bot.position,
        ..pose
    };
    bots.state_mut(first)
        .unwrap()
        .unwrap()
        .history
        .record(context.tick, pose);
    bots.state_mut(second)
        .unwrap()
        .unwrap()
        .history
        .record(context.tick, second_pose);

    bots.by_wire.swap_indices(0, 1);
    bots.body_mut(first).unwrap().position = [7.0, 0.0, 0.0];
    let result = bots
        .apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
        .unwrap()
        .unwrap();
    let due = context.now + BOT_RESPAWN_TIME;
    let view = ViewFrame::from_command(
        &Command {
            frame: context.tick,
            ..Command::default()
        },
        context.quanta,
    );
    assert_eq!(result.damage.target_facts.target, first);
    assert_eq!(
        result.notices[1],
        Some(CombatNotice::Elimination {
            killer: 7,
            victim: first,
            loadout: 0,
            fire: 100,
            critical: false,
        })
    );
    let actors = bots.debug_actors().unwrap().collect::<Vec<_>>();
    assert_eq!(actors[0].entity, second);
    assert!(actors[0].alive);
    assert_eq!(actors[0].health, Some(100.0));
    assert_eq!(actors[1].entity, first);
    assert_eq!(actors[1].position, [7.0, 0.0, 0.0]);
    assert!(!actors[1].alive);
    assert_eq!(actors[1].health, Some(0.0));
    let target = state(&bots, first).unwrap();
    assert_eq!(target.respawn_at, Some(due));
    assert_eq!(target.history.at(view, second_pose), Some(pose));
    let other = state(&bots, second).unwrap();
    assert_eq!(other.respawn_at, None);
    assert_eq!(other.history.at(view, pose), Some(second_pose));

    let replacements = bots.respawn_due(context.tick + 1, due).unwrap();
    let [replacement] = replacements.as_slice() else {
        panic!("expected one due replacement");
    };
    let fresh = replacement.created.entity;
    assert_eq!(replacement.retired, first);
    assert_eq!(fresh, BOT_ENTITY + 2);
    assert_eq!(
        bots.by_wire.keys().copied().collect::<Vec<_>>(),
        [second, fresh]
    );
    assert!(state(&bots, first).is_none());
    assert!(bots.world.get::<BotState>(first_handle).is_none());
    assert_eq!(bots.by_wire[&second], second_handle);
    let target = state(&bots, fresh).unwrap();
    assert!(!target.bot.dead);
    assert_eq!(target.bot.position, pose.feet);
    assert_eq!(target.vitals.total(), 100.0);
    assert_eq!(target.respawn_at, None);
    assert_eq!(target.history.at(view, pose), None);
    assert_eq!(
        state(&bots, second).unwrap().history.at(view, pose),
        Some(second_pose)
    );
    assert!(
        bots.apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
            .unwrap()
            .is_none()
    );
}

#[test]
fn failed_respawn_batch_preserves_all_due_bodies() {
    let mut bots = RoomSimulation::new(fixture_bots()).unwrap();
    let identities = [BOT_ENTITY, BOT_ENTITY + 1];
    let handles = [bots.by_wire[&identities[0]], bots.by_wire[&identities[1]]];
    let context = FIXTURE_CONTEXT;
    for identity in identities {
        bots.apply_bot_impact(7, context.tick, context, &fixture_impact(identity, 100.0))
            .unwrap();
    }
    let due = context.now + BOT_RESPAWN_TIME;
    let first_free = u32::MAX - 1;
    bots.next_bot = first_free;

    assert!(bots.respawn_due(context.tick + 1, due).is_err());
    assert_eq!(bots.next_bot, first_free);
    assert_eq!(bots.by_wire.keys().copied().collect::<Vec<_>>(), identities);
    for (identity, handle) in identities.into_iter().zip(handles) {
        assert_eq!(bots.by_wire[&identity], handle);
        let target = state(&bots, identity).unwrap();
        assert!(target.bot.dead);
        assert_eq!(target.vitals.total(), 0.0);
        assert_eq!(target.respawn_at, Some(due));
    }
}

#[test]
fn biotic_field_ticks_are_bounded_and_capsule_scoped() {
    let mut field = BioticField::new([0.0; 3]);
    for tick in [100, 111, 112] {
        let (pulse, expired) = field.advance(tick);
        assert!(pulse.is_none() && !expired);
    }
    let (pulse, expired) = field.advance(115);
    assert!(!expired);
    let pulse = pulse.unwrap();
    assert_eq!(pulse.amount, 3.0 * HEAL_PER_TICK);
    let pose = TargetPose {
        feet: [5.5, 0.0, 0.0],
        height: 2.0,
        radius: 0.5,
        eye: 1.55,
    };
    assert!(pulse.reaches(pose));
    assert!(!pulse.reaches(TargetPose {
        feet: [5.501, 0.0, 0.0],
        ..pose
    }));
    assert!(field.advance(115).0.is_none());
    let (tail, expired) = field.advance(900);
    assert!(expired);
    assert!((pulse.amount + tail.unwrap().amount - 312.0 * HEAL_PER_TICK).abs() < 1e-9);
    assert!(field.advance(901).0.is_none());
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range scene"]
fn biotic_field_replicates_healing_only_for_live_allies_in_range() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let mut scene = SceneAssets::load(runtime)
        .unwrap()
        .prepare(GameMap::practice_range())
        .unwrap();
    let (bots, queries) = scene.bot_simulation();
    let [near, far, dead] = {
        let mut allies = bots
            .bodies()
            .unwrap()
            .filter(|bot| bot.spawn.team == 0)
            .map(|bot| bot.entity);
        std::array::from_fn(|_| allies.next().unwrap())
    };
    let enemy = bots
        .bodies()
        .unwrap()
        .find(|bot| bot.spawn.team == 1)
        .unwrap()
        .entity;
    for (id, position) in [
        (near, [1.0, 0.0, 0.0]),
        (far, [7.0, 0.0, 0.0]),
        (dead, [1.0, 0.0, 0.0]),
        (enemy, [1.0, 0.0, 0.0]),
    ] {
        let state = bots.state_mut(id).unwrap().unwrap();
        state.bot.position = position;
        state.vitals.damage(
            if id == dead { 1_000.0 } else { 20.0 },
            Death {
                frame: 100,
                time: 1.6,
                killer: 1,
            },
        );
    }
    bots.body_mut(dead).unwrap().died(100);
    let mut field = BioticField::new([0.0; 3]);
    field.advance(100);
    let pulse = field.advance(117).0.unwrap();
    bots.by_wire.swap_indices(
        bots.by_wire.get_index_of(&near).unwrap(),
        bots.by_wire.get_index_of(&enemy).unwrap(),
    );
    let updates = bots.heal_bots(&queries, 0, &[pulse]).unwrap();
    assert_eq!(updates.len(), 1);
    assert_eq!(updates[0].entity, near);
    let near = state(bots, near).unwrap();
    let far = state(bots, far).unwrap();
    let enemy = state(bots, enemy).unwrap();
    assert_eq!(
        near.vitals.total(),
        near.bot.spawn.health - 20.0 + pulse.amount
    );
    assert_eq!(far.vitals.total(), far.bot.spawn.health - 20.0);
    assert_eq!(enemy.vitals.total(), enemy.bot.spawn.health - 20.0);
    assert_eq!(state(bots, dead).unwrap().vitals.total(), 0.0);
}

#[test]
fn respawn_encoding_failure_preserves_its_source_and_the_entire_batch() {
    let mut bots = fixture_bots();
    std::sync::Arc::get_mut(&mut bots[1].spawn).unwrap().team = u8::MAX;
    let mut simulation = RoomSimulation::new(bots).unwrap();
    let before = simulation.by_wire.clone();
    let next_bot = simulation.next_bot;
    let context = FIXTURE_CONTEXT;
    for identity in [BOT_ENTITY, BOT_ENTITY + 1] {
        simulation
            .apply_bot_impact(7, context.tick, context, &fixture_impact(identity, 100.0))
            .unwrap();
    }
    let error = simulation
        .respawn_due(context.tick + 1, context.now + BOT_RESPAWN_TIME)
        .err()
        .unwrap();
    assert!(matches!(&error, CombatError::RespawnCreate { wire, .. } if *wire == next_bot + 1));
    assert!(std::error::Error::source(&error).unwrap().is::<io::Error>());
    assert_eq!(simulation.by_wire, before);
    assert_eq!(simulation.next_bot, next_bot);
    assert!(simulation.bodies().unwrap().all(|bot| bot.dead));
}

#[test]
#[ignore = "requires a prepared 104319 Practice Range scene"]
fn repeated_rifle_hits_retire_and_respawn_only_the_hit_bot() {
    let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
    let mut scene = SceneAssets::load(runtime)
        .unwrap()
        .prepare(GameMap::practice_range())
        .unwrap();
    let (bots, queries) = scene.bot_simulation();
    let slot = bots
        .bodies()
        .unwrap()
        .position(|bot| bot.spawn.team == 1)
        .unwrap();
    let bot = bots.bodies().unwrap().nth(slot).unwrap();
    let entity = bot.entity;
    let [x, _, z] = bot.position;
    let head_y = queries
        .hurt_pose(bot.spawn.body, queries.target(bot))
        .head_probe_height()
        .expect("training body supports head hits");
    let count = bots.bodies().unwrap().len();
    let mut context = ShotContext {
        team: 0,
        quanta: RETAIL_QUANTA,
        tick: 1000,
        now: Duration::from_secs(4),
    };
    // Before combat activation, moving bodies must not seed rewind history.
    let cold_frame = context.tick - 1;
    let mut fallback = queries.target(&state(bots, entity).unwrap().bot);
    fallback.feet[0] += 1.0;
    let view = ViewFrame::from_command(
        &Command {
            frame: cold_frame,
            ..Command::default()
        },
        context.quanta,
    );
    assert!(
        bots.finish_tick(&queries, cold_frame, context.now)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        state(bots, entity).unwrap().history.at(view, fallback),
        Some(fallback)
    );
    bots.activate_combat();
    bots.finish_tick(&queries, context.tick, context.now)
        .unwrap();
    let mut shot = PosedHitscan {
        source: 0xa000_0101,
        origin: [x - 8.0, head_y, z],
        direction: [1.0, 0.0, 0.0],
        command: Command {
            frame: context.tick,
            yaw: 16384,
            ..Command::default()
        },
        shot_frame: 999,
        reach: 180.0,
        hit: HitDefinition {
            tags: crate::game_hitscan::DamageTags::EMPTY,
            amount: 20.0,
            loadout: None,
            critical: 2.0,
            falloff: None,
        },
    };
    assert!(
        bots.hitscan(&queries, shot, context, |_| false)
            .unwrap()
            .is_none()
    );
    assert!(
        bots.hitscan(&queries, shot, ShotContext { team: 1, ..context }, |_| {
            true
        })
        .unwrap()
        .is_none()
    );
    for index in 0..5 {
        let result = bots
            .hitscan(&queries, shot, context, |_| true)
            .unwrap()
            .unwrap();
        let terminal = index == 4;
        assert_eq!(result.damage.target_facts.target, entity);
        assert_eq!(
            result.notices[0],
            Some(CombatNotice::Hit {
                target: entity,
                dealt: 40.0,
                critical: true,
                killed: terminal,
                shot_frame: shot.shot_frame,
            })
        );
        assert_eq!(
            result.notices[1],
            terminal.then_some(CombatNotice::Elimination {
                killer: shot.source,
                victim: entity,
                loadout: 0,
                fire: 100,
                critical: false,
            })
        );
        assert_eq!(
            state(bots, entity).unwrap().vitals.total(),
            160.0 - f64::from(index) * 40.0
        );
        assert_eq!(bots.bodies().unwrap().nth(slot).unwrap().dead, terminal);
    }
    assert!(
        bots.hitscan(&queries, shot, context, |_| true)
            .unwrap()
            .is_none()
    );
    let due = context.now + BOT_RESPAWN_TIME;
    assert!(
        bots.finish_tick(&queries, 1187, due - Duration::from_millis(1))
            .unwrap()
            .is_empty()
    );
    let [replacement] = bots
        .finish_tick(&queries, 1188, due)
        .unwrap()
        .try_into()
        .ok()
        .unwrap();
    assert_eq!(replacement.retired, entity);
    let next = BOT_ENTITY + count as u32;
    assert_eq!(replacement.created.entity, next);
    assert!(matches!(
        replacement.created.operation,
        Some(Operation::Create(_))
    ));
    assert!(!bots.bodies().unwrap().nth(slot).unwrap().dead);
    assert_eq!(state(bots, next).unwrap().vitals.total(), 200.0);
    context.tick = 1188;
    context.now = due;
    assert!(
        bots.hitscan(&queries, shot, context, |_| true)
            .unwrap()
            .is_none()
    );
    shot.command.frame = context.tick;
    assert!(
        bots.hitscan(&queries, shot, context, |visible| visible == next)
            .unwrap()
            .is_some()
    );
    assert_eq!(state(bots, next).unwrap().vitals.total(), 160.0);
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_common_enemy_death_updates_authored_team_score() {
    junkenstein::normal_common_enemy_death_updates_authored_team_score();
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_encounter_first_reaper_finishes_intro_and_pursues_live_defender() {
    junkenstein::normal_encounter_first_reaper_finishes_intro_and_pursues_live_defender();
}

#[test]
fn debug_physics_records_hitscan_rays_and_enforces_ring_cap() {
    use crate::{
        collision_cache::{CollisionCache, Mesh, Triangle},
        collision_scene::CollisionScene,
        game_debug::MAX_DEBUG_RAYS,
        mover_settings::MoverCatalog,
    };

    let cache = CollisionCache {
        header: serde_json::from_str(
            r#"{"meshes":[[4,2]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
        )
        .unwrap(),
        meshes: vec![Mesh {
            vertices: vec![
                [10.0, -2.0, -5.0],
                [10.0, 5.0, -5.0],
                [10.0, 5.0, 5.0],
                [10.0, -2.0, 5.0],
            ],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                },
                Triangle {
                    corners: [0, 2, 3],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                },
            ],
        }],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    };
    let ground = CollisionScene::movement_probe(&cache, &Default::default()).unwrap();
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);

    let bots = fixture_bots();
    let target = bots[0].entity;
    let mut room = RoomSimulation::new(bots).unwrap();
    assert!(room.debug_physics().unwrap().is_none());

    let make_shot = |tick: u32, origin: [f64; 3], direction: [f64; 3], reach: f64| PosedHitscan {
        source: 0xa000_0101,
        origin,
        direction,
        command: Command {
            frame: tick,
            ..Command::default()
        },
        shot_frame: tick,
        reach,
        hit: HitDefinition {
            tags: crate::game_hitscan::DamageTags::EMPTY,
            amount: 20.0,
            loadout: None,
            critical: 2.0,
            falloff: None,
        },
    };

    // 1) Hit bot target at (1, 0, 0).
    let ctx = ShotContext {
        team: 0,
        quanta: RETAIL_QUANTA,
        tick: 10,
        now: Duration::from_secs(1),
    };
    assert!(
        room.hitscan(
            &queries,
            make_shot(10, [0.0, 1.0, 0.0], [1.0, 0.0, 0.0], 50.0),
            ctx,
            |_| true
        )
        .unwrap()
        .is_some()
    );
    // 2) Miss bots and hit wall at X = 10.
    let ctx_wall = ShotContext { tick: 11, ..ctx };
    assert!(
        room.hitscan(
            &queries,
            make_shot(11, [0.0, 1.0, 3.0], [1.0, 0.0, 0.0], 50.0),
            ctx_wall,
            |_| true,
        )
        .unwrap()
        .is_none()
    );
    // 3) Miss bots and walls cleanly (-X).
    let ctx_miss = ShotContext { tick: 12, ..ctx };
    assert!(
        room.hitscan(
            &queries,
            make_shot(12, [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], 25.0),
            ctx_miss,
            |_| true,
        )
        .unwrap()
        .is_none()
    );

    // 4) Find an actor behind the wall, then report the wall as the endpoint.
    room.body_mut(target).unwrap().position = [11.0, 0.0, 0.0];
    assert!(
        room.hitscan(
            &queries,
            make_shot(13, [0.0, 1.0, 0.0], [1.0, 0.0, 0.0], 50.0),
            ShotContext { tick: 13, ..ctx },
            |entity| entity == target,
        )
        .unwrap()
        .is_none()
    );

    let overlay = room.debug_physics().unwrap().unwrap();
    assert!(!overlay.truncated.rays);
    assert_eq!(overlay.rays.len(), 4);
    assert_eq!(overlay.rays[0].hit, "actor");
    assert_eq!(overlay.rays[0].target, Some(target));
    assert!(overlay.rays[0].surface.is_none());
    assert_eq!(overlay.rays[1].hit, "world");
    assert!((overlay.rays[1].end[0] - 10.0).abs() < 1e-6);
    assert_eq!(overlay.rays[2].hit, "none");
    assert!(overlay.rays[2].surface.is_none());
    assert_eq!(overlay.rays[2].end, [-25.0, 1.0, 0.0]);
    assert_eq!(overlay.rays[3].hit, "world");
    assert!((overlay.rays[3].end[0] - 10.0).abs() < 1e-6);
    assert_eq!(overlay.rays[3].target, None);
    for ray in [&overlay.rays[1], &overlay.rays[3]] {
        let surface = ray.surface.unwrap();
        assert_eq!(surface.geometry, "movement");
        assert_eq!(surface.mask, 0x18);
        assert_eq!(surface.surface_flags, 0);
        assert!(matches!(
            surface.cache_source,
            Some(crate::collision_scene::StaticRaySource::Mesh { .. })
        ));
        assert_eq!(surface.normal.map(f64::abs), [1.0, 0.0, 0.0]);
    }
    assert!(overlay.rays.iter().all(|ray| ray.script.is_none()));

    room.enable_spatial_queries(false, Arc::new(fixture_ground()), RoomSpatialNeeds::Queries);
    let captured = room.capture_spatial_frame(14).unwrap().unwrap();
    captured
        .ray_cast(SCRIPT_RAY_SITE, 15, [-2.0, 0.5, 0.25], [1.0, 0.0, 0.0], 1.0)
        .unwrap()
        .unwrap();
    let shared = room.debug_physics().unwrap().unwrap();
    assert_eq!(shared.rays.len(), 5);
    assert_eq!(shared.rays[4].kind, "script_static");
    assert_eq!(shared.rays[4].tick, 14);
    assert_eq!(shared.rays[4].script.unwrap().vm_frame, 15);
    assert!(
        serde_json::to_value(shared.rays[0])
            .unwrap()
            .get("script")
            .is_none()
    );

    // Overflow ring buffer and assert truncation flag + newest ticks retained.
    for i in 0..(MAX_DEBUG_RAYS + 5) {
        let tick = 100 + i as u32;
        let _ = room
            .hitscan(
                &queries,
                make_shot(tick, [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], 10.0),
                ShotContext { tick, ..ctx },
                |_| true,
            )
            .unwrap();
    }
    let capped = room.debug_physics().unwrap().unwrap();
    assert!(capped.truncated.rays);
    assert_eq!(capped.rays.len(), MAX_DEBUG_RAYS);
    assert_eq!(
        capped.rays.last().unwrap().tick,
        100 + (MAX_DEBUG_RAYS as u32 + 4)
    );
}

#[test]
fn debug_navigation_reports_patrol_routes_and_caps_waypoints() {
    use crate::game_debug::MAX_DEBUG_ROUTE_WAYPOINTS;

    let mut hints = serde_json::Map::new();
    let mut keys = Vec::new();
    for i in 0..40 {
        let name = format!("h{i}");
        hints.insert(
            name.clone(),
            serde_json::json!([f64::from(i) * 2.0, 0.0, 0.0]),
        );
        keys.push(serde_json::Value::String(name));
    }
    let catalog_json = serde_json::json!({
        "heroes": {"0x01": {"name": "Patrol bot", "body": "0x02", "health": 100}},
        "maps": {"0x03": {
            "hints": hints,
            "spawners": [{
                "hero": "0x01",
                "team": 1,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "graphs": ["0x0580000000000B8E"],
                "vars": {"0x0D80000000001B23": keys}
            }]
        }}
    });
    let mut bots = crate::game_bots::BotCatalog::from_json(&catalog_json.to_string())
        .unwrap()
        .make(3, |_| {
            crate::game_bots::BotWalk::from_mover(4.5, 10.0, 10.0)
        });
    let patrol = crate::patrol_script::PatrolScript::load().unwrap();
    for tick in 1..=200 {
        bots[0].step(tick, &patrol, |_, _, _, _| Some(0.0)).unwrap();
    }
    let room = RoomSimulation::new(bots).unwrap();
    let nav = room.debug_navigation().unwrap().unwrap();
    assert!(!nav.truncated.routes);
    assert_eq!(nav.routes.len(), 1);
    let route = &nav.routes[0];
    assert_eq!(route.kind, "patrol");
    assert_eq!(route.waypoints.len(), MAX_DEBUG_ROUTE_WAYPOINTS);
    assert!(route.truncated);
    assert!(route.target.is_some());
}

#[test]
fn debug_scripts_is_read_only_and_enforces_summary_caps() {
    use indexmap::IndexMap;

    use crate::{
        game_debug::{MAX_DEBUG_VM_STATES, MAX_DEBUG_VM_VARS, summarize_script_snapshot},
        script_enemy::{EnemySpawn, fixture},
        script_graph::GraphStore,
        script_init::{Component, EmptyWorld, Root, Snapshot, State, StateKind},
        script_wire::{Value, Variables},
    };

    // Part 1: RoomSimulation::debug_scripts reads live enemy scripts without mutating VM state.
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    let (graphs, codecs, definition, request) = fixture(false);
    let entity = room
        .create_enemy(EnemySpawn {
            graphs,
            codecs,
            definition: &definition,
            request: &request,
            mode: 0x0230_0000_0000_019f,
            mode_variables: Arc::new(Variables::new()),
            tick: 1000,
            walk: crate::game_bots::BotWalk::default(),
            gate: None,
        })
        .unwrap();
    let before_body = state(&room, entity)
        .unwrap()
        .enemy
        .as_ref()
        .unwrap()
        .scripts
        .body
        .snapshot()
        .unwrap()
        .clone();
    let (first, truncated) = room.debug_scripts().unwrap();
    let (second, truncated_again) = room.debug_scripts().unwrap();
    assert!(!truncated);
    assert!(!truncated_again);
    assert!(
        first
            .iter()
            .any(|entry| entry.entity == entity && entry.kind == "enemy_body")
    );
    assert!(
        first
            .iter()
            .any(|entry| entry.entity == entity + 1 && entry.kind == "enemy_controller")
    );
    assert_eq!(
        serde_json::to_value(&first).unwrap(),
        serde_json::to_value(&second).unwrap()
    );
    let after_body = state(&room, entity)
        .unwrap()
        .enemy
        .as_ref()
        .unwrap()
        .scripts
        .body
        .snapshot()
        .unwrap();
    assert_eq!(before_body.now_ms, after_body.now_ms);
    assert_eq!(before_body.variables, after_body.variables);
    assert_eq!(before_body.instances.len(), after_body.instances.len());

    // Part 2: summarize_script_snapshot omits dormant states, preserves flags/resolved bindings,
    // and enforces state/variable/string caps.
    let graph_payload = serde_json::to_vec(&serde_json::json!({
        "version": 1,
        "graphs": [{
            "index": 0x0043,
            "items": 2,
            "nodes": [
                {
                    "pos": 0,
                    "class": "STUStatescriptEntry",
                    "uid": 0,
                    "rid": 0,
                    "remote": -1,
                    "state": null,
                    "client_only": false,
                    "server_only": false,
                    "fields": {
                        "m_2BBEEAB8": false,
                        "m_ADEB6E05": false,
                        "m_outPlug": {
                            "$": "Plug",
                            "links": [[1, "m_beginPlug"]],
                            "m_FF3DAF1E": [0]
                        }
                    }
                },
                {
                    "pos": 1,
                    "class": "STUStatescriptState",
                    "uid": 1,
                    "rid": 1,
                    "remote": -1,
                    "state": 0,
                    "client_only": false,
                    "server_only": false,
                    "fields": {
                        "m_2BBEEAB8": true,
                        "m_ADEB6E05": false
                    }
                }
            ],
            "states": [1],
            "entries": [0],
            "remote": [],
            "sync_vars": [],
            "containers": {},
            "unknown_nodes": [],
            "fields": {},
            "header": {
                "m_nodesBitCount": 4,
                "m_statesBitCount": 4,
                "m_remoteSyncNodesBitCount": 4,
                "m_syncVarsBitCount": 4
            }
        }],
        "bodies": {},
        "missing": []
    }))
    .unwrap();
    let graphs = Arc::new(GraphStore::from_json(&graph_payload).unwrap());
    let mut empty_world = EmptyWorld;
    let component = Component::start(
        graphs,
        0xa000_0001,
        Variables::new(),
        vec![Root::new(1, 0x0043)],
        0,
        &mut empty_world,
        10_000,
    )
    .unwrap();
    let mut synthetic: Snapshot = component.snapshot().unwrap().clone();
    synthetic.now_ms = 480;
    for var_id in 1..=(MAX_DEBUG_VM_VARS as u32 + 5) {
        synthetic
            .variables
            .insert(var_id, Value::Integer(i64::from(var_id)));
    }
    synthetic.variables.insert(2, Value::String("ä".repeat(80)));
    synthetic
        .resolved_bindings
        .insert((0, 1), Value::Integer(999));

    let template_inst = synthetic.instances.get(&1).unwrap().clone();
    let template_state: State = template_inst.states.get(&0).unwrap().clone();
    let mut states = IndexMap::new();
    for raw in 0..u16::try_from(MAX_DEBUG_VM_STATES + 4).unwrap() {
        let mut s = template_state.clone();
        s.active = raw != 1;
        s.activation = raw + 1;
        s.start_ms = u64::from(raw) * 16;
        s.kind = if raw == 0 {
            StateKind::Ability { flags: 7 }
        } else {
            StateKind::Plain
        };
        states.insert(raw, s);
    }
    let mut inst = template_inst;
    inst.parent = Some((0, 2));
    inst.states = states;
    synthetic.instances.insert(1, inst);

    let summary = summarize_script_snapshot(synthetic.entity, "player", &synthetic);
    assert_eq!(summary.now_ms, 480);
    assert!(summary.variables_truncated);
    assert_eq!(summary.variables.len(), MAX_DEBUG_VM_VARS);
    assert!(summary.variables[0].resolved);
    assert_eq!(summary.variables[0].value, "999");
    assert!(summary.variables[1].value.ends_with("..."));
    assert_eq!(summary.variables[1].value.chars().count(), 67);
    let inst_summary = &summary.instances[0];
    assert!(inst_summary.states_truncated);
    assert_eq!(inst_summary.active_states.len(), MAX_DEBUG_VM_STATES);
    assert_eq!(inst_summary.active_states[0].kind, "Ability");
    assert_eq!(inst_summary.active_states[0].flags, Some(7));
    assert!(inst_summary.active_states.iter().all(|s| s.raw != 1));
}

#[test]
fn spatial_frame_honors_weapon_terrain_category() {
    use crate::collision_cache::{CollisionCache, Mesh, RayCacheIdentity, Triangle};
    use serde_json::json;
    use std::collections::BTreeSet;

    let triangles = [
        [[-1.0, 0.0, 2.0], [1.0, 0.0, 2.0], [0.0, 3.0, 2.0]],
        [[-1.0, 0.0, 6.0], [1.0, 0.0, 6.0], [0.0, 3.0, 6.0]],
    ];
    let header = json!({
        "meshes": [[6, 2]],
        "hulls": 0,
        "models": 0,
        "placements": 0,
        "identifiers": [],
        "weapon_terrain_category": 0x0200,
        "geometry": "expanded-static-rays",
        "category_mask": 65535,
        "prop_scope": "indexed-static-models",
        "map": "0x0800000000004242",
        "chunk": "0x0DD0001100004242",
        "ckey": "synthetic-map"
    });
    let cache = CollisionCache {
        header: serde_json::from_value(header).unwrap(),
        meshes: vec![Mesh {
            vertices: triangles.iter().flatten().copied().collect(),
            triangles: vec![
                Triangle {
                    corners: [0, 2, 1],
                    wings: [-1; 3],
                    mask: MOVER_CATEGORY,
                    surface: 0,
                },
                Triangle {
                    corners: [3, 5, 4],
                    wings: [-1; 3],
                    mask: 0x0200,
                    surface: 0,
                },
            ],
        }],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    };
    let identity = RayCacheIdentity {
        map_guid: 0x0800_0000_0000_4242,
        collision_ckey: "synthetic-map",
        placement_ckey: None,
        prop_records_sha256: None,
    };
    let ground =
        Arc::new(CollisionScene::expanded_rays(&cache, &BTreeSet::new(), identity).unwrap());
    let target = 0xc000_0001;
    let frame = RoomSpatialFrame {
        candidates: vec![SpatialCandidate {
            entity: target,
            position: [0.0, 0.0, 4.0],
            team: Some(1),
        }],
        actor_capsules: Some(BTreeMap::from([(
            target,
            TargetPose {
                feet: [0.0, 0.0, 4.0],
                height: 2.0,
                eye: 1.5,
                radius: 0.5,
            },
        )])),
        entity_facts: None,
        script_targets: HashMap::from([(
            target,
            RoomScriptTarget {
                entity: Entity::from_raw(target),
                candidate_index: Some(0),
                position: [0.0, 0.0, 4.0],
                health: true,
                health_snapshot: None,
                angles: [0, 0],
                velocity: [0.0; 3],
                eye_position: Some([0.0, 1.5, 4.0]),
                filter_flags: Some(0),
                script_state: Default::default(),
                messages: false,
                cue_listener: true,
                voice: None,
            },
        )]),
        player_wires: HashMap::new(),
        ground,
        breakables: None,
        actor_voice: None,
        map_guid: None,
        retired: Arc::default(),
        #[cfg(any(test, feature = "inspector"))]
        captured_tick: 1,
        #[cfg(any(test, feature = "inspector"))]
        debug_rays: Arc::default(),
    };
    assert_eq!(
        frame.line_clear([0.0, 1.0, 0.0], [0.0, 1.0, 4.0]),
        Some(true)
    );
    assert_eq!(
        frame.line_clear([0.0, 1.0, 0.0], [0.0, 1.0, 8.0]),
        Some(false)
    );
    let site = crate::script_init::Site {
        entity: target,
        instance: 0,
        graph: 0,
        node: None,
    };
    let world_hit = frame
        .ray_cast(site, 1, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 10.0)
        .unwrap()
        .unwrap();
    assert!((world_hit.distance - 6.0).abs() < 1e-4);
    let actor_hit = frame
        .actor_ray_cast(site, 1, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 10.0, &[target])
        .unwrap()
        .unwrap();
    assert_eq!(actor_hit.entity, target);
}

#[test]
fn hitscan_pierces_and_breaks_breakable_placement_then_clears_projectile_sweeps() {
    use crate::collision_cache::{
        CollisionCache, Mesh, Placement, RAY_GEOMETRY, RAY_PROP_SCOPE, RayCacheIdentity, Shape,
        Triangle, chunk_guid,
    };
    use crate::collision_scene::BreakableState;
    use crate::game_hitscan::{DamageTags, HurtVolumeCatalog};
    use crate::mover_settings::{MoverCatalog, MoverSettings};
    use std::collections::BTreeSet;

    let map = 0x0800_0000_0000_4242;
    let identity = RayCacheIdentity {
        map_guid: map,
        collision_ckey: "synthetic-map",
        placement_ckey: None,
        prop_records_sha256: None,
    };
    let glass = Mesh {
        vertices: vec![[-1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 2.5, 0.0]],
        triangles: vec![Triangle {
            corners: [0, 1, 2],
            wings: [-1; 3],
            mask: MOVER_CATEGORY,
            surface: 0,
        }],
    };
    let cache = CollisionCache {
        header: serde_json::from_value(serde_json::json!({
            "meshes": [],
            "hulls": 0,
            "models": 1,
            "placements": 1,
            "identifiers": [],
            "geometry": RAY_GEOMETRY,
            "category_mask": 0xffff,
            "prop_scope": RAY_PROP_SCOPE,
            "map": format!("0x{map:016X}"),
            "chunk": format!("0x{:016X}", chunk_guid(map)),
            "ckey": "synthetic-map",
            "breakable_model_shapes": { "0": [0] },
        }))
        .unwrap(),
        meshes: vec![],
        hulls: vec![],
        models: vec![vec![Shape::Mesh {
            mesh: glass,
            mask: MOVER_CATEGORY,
        }]],
        placements: vec![Placement {
            model: 0,
            group: 0,
            translation: [0.0, 0.0, 2.0],
            scale: [1.0; 3],
            rotation: [0.0, 0.0, 0.0, 1.0],
        }],
    };
    let rays = Arc::new(CollisionScene::expanded_rays(&cache, &BTreeSet::new(), identity).unwrap());
    let sweeps = Arc::new(
        crate::contact_world::ContactWorld::from_expanded_ray_cache(
            cache,
            &BTreeSet::new(),
            identity,
        )
        .unwrap(),
    );
    let breakables = Arc::new(BreakableState::new());
    let bots = crate::game_bots::BotCatalog::from_json(
        r#"{
            "heroes":{"0x01":{"name":"Test bot","body":"0x02","health":200}},
            "maps":{"0x03":{"hints":{},"spawners":[
                {"hero":"0x01","team":1,"position":[0,0,4],"rotation":[0,0,0,1],"graphs":[]}
            ]}}
        }"#,
    )
    .unwrap()
    .make(3, |_| crate::game_bots::BotWalk::default());
    let bot_entity = bots[0].entity;
    let mut room = RoomSimulation::new(bots).unwrap();
    room.enable_spatial_queries(false, Arc::clone(&rays), RoomSpatialNeeds::Queries);
    room.set_breakables(Arc::clone(&breakables));
    let movers =
        MoverCatalog::from_json(r#"{"0x02":{"stand_height":2.0,"stand_radius":0.5}}"#).unwrap();
    let hurt_volumes = HurtVolumeCatalog::default();
    let queries = BotQueries::new(false, &movers, &rays, None)
        .with_combat_geometry(&rays, Some(&sweeps))
        .with_breakables(&breakables)
        .with_hurt_volumes(&hurt_volumes);

    let owner = BodyId {
        entity: 0xa000_0100,
        generation: 1,
    };
    let mover = crate::mover::Mover::new(
        MoverSettings::default(),
        Arc::clone(&sweeps),
        [0.0, 0.0, 0.0],
        0,
        0x18,
    )
    .unwrap();
    room.spawn_player_body(PlayerBodyState::new(
        owner, 1, 0, 0, SOLDIER, mover, None, 1, None,
    ))
    .unwrap();

    // First projectile hits the unbroken breakable panel and shatters it.
    assert!(
        queries
            .projectile_wall([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 5.0, 0.0)
            .unwrap()
            .is_some()
    );
    assert!(breakables.is_broken(0));
    assert!(
        queries
            .projectile_wall([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 5.0, 0.0)
            .unwrap()
            .is_none()
    );

    // Reset breakable state, then verify hitscan pierces the intact panel, hits the bot, and breaks it.
    breakables.clear();
    assert!(!breakables.is_broken(0));
    let shot = PosedHitscan {
        source: owner.entity,
        origin: [0.0, 1.0, 0.0],
        direction: [0.0, 0.0, 1.0],
        command: crate::commands::Command {
            frame: 1,
            ..Default::default()
        },
        shot_frame: 1,
        reach: 20.0,
        hit: HitDefinition {
            tags: DamageTags::EMPTY,
            amount: 20.0,
            loadout: None,
            critical: 1.0,
            falloff: None,
        },
    };
    let hit = room
        .hitscan(
            &queries,
            shot,
            ShotContext {
                team: 0,
                quanta: 16,
                tick: 1,
                now: Duration::ZERO,
            },
            |_| true,
        )
        .unwrap();
    assert!(hit.is_some());
    assert!(breakables.is_broken(0));
    assert_eq!(room.body_healths().unwrap()[&bot_entity], 180.0);
}

#[test]
fn live_room_interactive_props_authored_blast_breakables_and_pvp_objective() {
    use crate::{
        collision_cache::{
            CollisionCache, Placement, RAY_GEOMETRY, RAY_PROP_SCOPE, RayCacheIdentity, Shape,
            chunk_guid,
        },
        collision_scene::{BreakableState, CollisionScene},
        contact_world::ContactWorld,
        game_hitscan::DamageTags,
        mover::Mover,
        mover_settings::{MoverCatalog, MoverSettings},
        projectile_flight::{Blast, ProjectileBallistics, ProjectileFlight},
        pvp_mode::{AssaultRound, CapturePointSpec, MatchPhase, PvpObjectiveRuntime},
        room_primitives::{INTERACT_BUTTON_BIT, ObbTrigger, ObbZone, PhysicsProp, ResetButtonProp},
    };
    const TEST_MOVER_CATEGORY: u16 = 0x0018;
    use std::collections::BTreeSet;

    let map = 0x0800_0000_0000_4243;
    let identity = RayCacheIdentity {
        map_guid: map,
        collision_ckey: "synthetic-blast-map",
        placement_ckey: None,
        prop_records_sha256: None,
    };
    let cache = CollisionCache {
        header: serde_json::from_value(serde_json::json!({
            "meshes": [],
            "hulls": 0,
            "models": 2,
            "placements": 2,
            "identifiers": [],
            "geometry": RAY_GEOMETRY,
            "category_mask": 0xffff,
            "prop_scope": RAY_PROP_SCOPE,
            "map": format!("0x{map:016X}"),
            "chunk": format!("0x{:016X}", chunk_guid(map)),
            "ckey": "synthetic-blast-map",
            "breakable_model_shapes": { "0": [0] },
        }))
        .unwrap(),
        meshes: Vec::new(),
        hulls: Vec::new(),
        models: vec![
            vec![Shape::Sphere {
                center: [0.0, 0.0, 0.0],
                radius: 0.4,
                mask: TEST_MOVER_CATEGORY,
            }],
            vec![Shape::Sphere {
                center: [0.0, 0.0, 0.0],
                radius: 0.5,
                mask: TEST_MOVER_CATEGORY,
            }],
        ],
        placements: vec![
            // Placement 0: breakable prop 2.4m to the right of projectile wall impact
            Placement {
                model: 0,
                group: 0,
                translation: [2.4, 1.0, 5.0],
                rotation: [0.0, 0.0, 0.0, 1.0],
                scale: [1.0, 1.0, 1.0],
            },
            // Placement 1: solid wall hit by projectile at z=5.0
            Placement {
                model: 1,
                group: 0,
                translation: [0.0, 1.0, 5.0],
                rotation: [0.0, 0.0, 0.0, 1.0],
                scale: [1.0, 1.0, 1.0],
            },
        ],
    };
    let scene =
        Arc::new(CollisionScene::expanded_rays(&cache, &BTreeSet::new(), identity).unwrap());
    let breakables = Arc::new(BreakableState::new());
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &scene, None)
        .with_combat_geometry(&scene, None)
        .with_breakables(&breakables);

    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.enable_spatial_queries(false, Arc::clone(&scene), RoomSpatialNeeds::Queries);
    room.set_breakables(Arc::clone(&breakables));

    // Register a basketball prop, reset button, and Assault capture point.
    room.set_interactive_prop_floor_y(0.0);
    room.interactive_props_mut()
        .props
        .insert(9001, PhysicsProp::new(9001, [0.0, 1.0, 2.0], 0.2));
    room.interactive_props_mut().buttons.insert(
        9002,
        ResetButtonProp::new(9002, [0.0, 0.0, 0.5], vec![9001], 0x0300_0000_0000_BEEF),
    );
    let mut assault = AssaultRound::new(
        vec![CapturePointSpec {
            placeable: 1,
            position: [0.0, 0.0, 0.0],
            zone: ObbZone::new(vec![ObbTrigger::from_raw([
                0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 4.0, 4.0, 4.0,
            ])]),
            round_seconds: 240.0,
            capture_seconds: 10.0,
            segments: 3,
        }],
        1.0,
    );
    assault.phase = MatchPhase::Active;
    assault.deadline = 300.0;
    assault.last_update = 0.0;
    room.set_pvp_objective(PvpObjectiveRuntime::Assault(assault));

    let owner = BodyId {
        entity: 0xa000_0100,
        generation: 1,
    };
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        [0.0, 0.0, 0.0],
        0,
        0x08,
    )
    .unwrap();
    room.spawn_player_body(PlayerBodyState::new(
        owner, 1, 0, 1, SOLDIER, mover, None, 1, None,
    ))
    .unwrap();

    // 1. Hitscan ray shoots the basketball prop -> imparts velocity.
    let shot = PosedHitscan {
        source: owner.entity,
        origin: [0.0, 1.0, 0.0],
        direction: [0.0, 0.0, 1.0],
        command: crate::commands::Command {
            frame: 1,
            ..Default::default()
        },
        shot_frame: 1,
        reach: 20.0,
        hit: HitDefinition {
            tags: DamageTags::EMPTY,
            amount: 20.0,
            loadout: None,
            critical: 1.0,
            falloff: None,
        },
    };
    let _ = room
        .hitscan(
            &queries,
            shot,
            ShotContext {
                team: 1,
                quanta: 16,
                tick: 1,
                now: Duration::from_secs(1),
            },
            |_| true,
        )
        .unwrap();
    assert!(room.interactive_props().props[&9001].velocity[2] > 0.0);

    // 2. Finish tick steps interactive props AND advances Assault capture point progress!
    room.finish_tick(&queries, 2, Duration::from_secs(2))
        .unwrap();
    assert_ne!(
        room.interactive_props().props[&9001].position,
        [0.0, 1.0, 2.0]
    );
    match room.pvp_objective().unwrap() {
        PvpObjectiveRuntime::Assault(round) => {
            assert!(
                round.current().progress > 0.0,
                "live attacker inside Assault OBB must advance capture progress"
            );
        }
        _ => panic!("expected Assault PvP objective"),
    }

    // 3. Player presses Interact button near reset button -> resets basketball cleanly.
    room.player_body_mut(owner).unwrap().pending_pressed_buttons = INTERACT_BUTTON_BIT;
    room.finish_tick(&queries, 3, Duration::from_secs(3))
        .unwrap();
    assert_eq!(
        room.interactive_props().props[&9001].position,
        [0.0, 1.0, 2.0]
    );
    assert!(!room.take_prop_cues().is_empty());

    // 4. Projectile with authored 3.0m blast radius detonates on wall at z=5.0 -> smashes breakable 2.4m away!
    let flight = ProjectileFlight::new(
        ProjectileBallistics {
            speed: 400.0,
            lifetime: 2.0,
            max_distance: None,
            radius: 0.1,
            terrain_radius: 0.0,
            definition: None,
            direct: None,
            blast: Some(Blast {
                hit: HitDefinition {
                    tags: DamageTags::EMPTY,
                    amount: 80.0,
                    loadout: None,
                    critical: 1.0,
                    falloff: None,
                },
                rings: vec![(0.5, 1.0), (3.0, 0.5)],
                self_damage_scale: 0.0,
                self_knockback_scale: 0.0,
                knockback: None,
                detonate_on_expiry: false,
            }),
            terrain_rebound: None,
        },
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        Some(3),
    )
    .unwrap();
    assert!(!breakables.is_broken(0));
    let (outcomes, _) = room
        .advance_projectiles(
            &queries,
            1,
            owner,
            vec![AdmittedFlight {
                token: crate::game_projectiles::ProjectileToken::server_only(1),
                launch_frame: 4,
                flight,
                stun: None,
                hit_effect: None,
                impact_spawn: None,
                presentation: None,
            }],
            ShotContext {
                team: 1,
                quanta: 16,
                tick: 4,
                now: Duration::from_secs(4),
            },
            |_| true,
        )
        .unwrap();
    assert_eq!(outcomes.len(), 1);
    assert!(
        breakables.is_broken(0),
        "projectile splash must smash breakables within its authored 3.0m blast radius"
    );
}

#[cfg(feature = "lua-mods")]
#[test]
fn live_room_lua_mods_apply_rules_hooks_actions_and_hot_reload() {
    use crate::{
        commands::JUMP,
        contact_world::ContactWorld,
        game_hitscan::DamageTags,
        mod_script::ModHost,
        mover::Mover,
        mover_settings::{MoverCatalog, MoverSettings},
        room_primitives::PhysicsProp,
        vitals::Vitals,
    };
    use std::sync::Mutex;

    let low_grav_src = include_str!("../../scripts/mods/low_gravity_arena.lua");
    let headshot_src = include_str!("../../scripts/mods/headshot_only_practice.lua");

    let host = Arc::new(Mutex::new(
        ModHost::from_source("low_gravity_arena.lua", low_grav_src).unwrap(),
    ));

    let bots = fixture_bots();
    let bot_entity = bots[0].entity;
    let mut room = RoomSimulation::new(bots).unwrap();
    let ground = Arc::new(fixture_ground());
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    room.enable_spatial_queries(false, Arc::clone(&ground), RoomSpatialNeeds::Queries);
    room.attach_mod_host(
        Arc::clone(&host),
        0x0800_0000_0000_0688,
        0x0230_0000_0000_0012,
    );

    // Spawn a living player body with vitals so heal actions can be observed.
    let owner = BodyId {
        entity: 0xa000_0100,
        generation: 1,
    };
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        [0.0, 0.0, 0.0],
        0,
        0x08,
    )
    .unwrap();
    let mut player_state = PlayerBodyState::new(owner, 1, 0, 0, SOLDIER, mover, None, 1, None);
    let mut vitals = Vitals::new(200.0, 0.0, 0.0);
    vitals.damage(
        50.0,
        Death {
            time: 0.0,
            frame: 1,
            killer: 0,
        },
    );
    assert_eq!(vitals.total(), 150.0);
    player_state.set_test_halted_vitals(vitals);
    room.spawn_player_body(player_state).unwrap();

    // Verify Low-Gravity Arena rules immediately synced to Mover!
    assert_eq!(room.mod_active_rules().gravity_scale, 0.45);
    assert_eq!(room.mod_active_rules().speed_scale, 1.2);
    assert_eq!(
        room.player_body(owner).unwrap().mover.mod_scales(),
        (0.45, 1.2)
    );
    assert!((room.player_body(owner).unwrap().mover.pose().gravity - 1.75 * 0.45).abs() < 1e-6);

    // Step first tick -> triggers on_room_start + Jump button impulse!
    room.player_body_mut(owner).unwrap().pending_pressed_buttons = JUMP;
    room.finish_tick(&queries, 2, Duration::from_secs(1))
        .unwrap();
    assert!(
        room.mod_broadcast_log()
            .iter()
            .any(|msg| msg.contains("Low-Gravity Arena active")),
        "on_room_start broadcast missing: {:?}",
        room.mod_broadcast_log()
    );

    // Verify Jump button queued vertical impulse onto Mover (+4.5 m/s).
    let mut poses = crate::mover_kinematics::poses(
        None,
        crate::commands::Command {
            frame: 2,
            ..Default::default()
        },
    );
    room.player_body_mut(owner)
        .unwrap()
        .mover
        .step_pending(ground.triangles(), &mut poses, 0.016, 1, || &[], |_, _| {})
        .unwrap();
    assert!(
        room.player_body(owner).unwrap().mover.pose().velocity[1] > 3.5,
        "Jump on_button impulse must launch player upward, got {:?}",
        room.player_body(owner).unwrap().mover.pose().velocity
    );

    // Chat command !help works live.
    room.mod_dispatch_chat("Player", "!help", 3, Duration::from_secs(2));
    assert!(
        room.mod_broadcast_log()
            .iter()
            .any(|msg| msg.contains("!grav")),
        "on_chat !help broadcast missing"
    );

    // Hot-reload shared ModHost to Headshot-Only Practice!
    let status = host
        .lock()
        .unwrap()
        .reload_source("headshot_only_practice.lua", headshot_src)
        .unwrap();
    assert!(status.changed);
    assert_eq!(status.generation, 2);

    // Next finish_tick automatically detects generation 2, resets Mover scales to 1.0, and runs on_room_start.
    room.finish_tick(&queries, 4, Duration::from_secs(3))
        .unwrap();
    assert_eq!(
        room.player_body(owner).unwrap().mover.mod_scales(),
        (1.0, 1.0)
    );
    assert!(
        room.mod_broadcast_log()
            .iter()
            .any(|msg| msg.contains("Headshot-Only Practice enabled"))
    );

    // Bodyshot on bot -> Headshot-Only mod reduces damage to 0.0!
    let bodyshot = Impact {
        entity: bot_entity,
        distance: 1.0,
        damage: 60.0,
        critical: false,
        loadout: None,
        tags: DamageTags::EMPTY,
        geometry: None,
        impulse: None,
    };
    let hit = room
        .apply_bot_impact(
            owner.entity,
            4,
            ShotContext {
                team: 0,
                quanta: 16,
                tick: 4,
                now: Duration::from_secs(3),
            },
            &bodyshot,
        )
        .unwrap();
    assert!(
        hit.is_none(),
        "bodyshot must deal 0 damage under Headshot-Only mod"
    );
    assert_eq!(room.body_healths().unwrap()[&bot_entity], 100.0);

    // Headshot on bot (100 damage -> kill!) -> kills bot, heals attacker +25 HP (150 -> 175), broadcasts kill!
    let headshot = Impact {
        entity: bot_entity,
        distance: 1.0,
        damage: 100.0,
        critical: true,
        loadout: None,
        tags: DamageTags::EMPTY,
        geometry: None,
        impulse: None,
    };
    let hit = room
        .apply_bot_impact(
            owner.entity,
            5,
            ShotContext {
                team: 0,
                quanta: 16,
                tick: 5,
                now: Duration::from_secs(4),
            },
            &headshot,
        )
        .unwrap();
    assert!(hit.is_some(), "headshot must deal full damage and kill");
    assert_eq!(
        room.player_body(owner).unwrap().vitals().unwrap().total(),
        175.0,
        "headshot mod must heal attacker +25 HP (150 -> 175)"
    );
    assert!(
        room.mod_broadcast_log()
            .iter()
            .any(|msg| msg.contains("Crisp headshot! Total headshot eliminations: 1"))
    );

    // Verify ResetInteractiveProp action works cleanly against RoomSimulation interactive props.
    room.interactive_props_mut()
        .props
        .insert(77, PhysicsProp::new(77, [1.0, 2.0, 3.0], 0.2));
    room.interactive_props_mut()
        .props
        .get_mut(&77)
        .unwrap()
        .position = [9.0, 9.0, 9.0];
    host.lock()
        .unwrap()
        .reload_source(
            "prop_reset.lua",
            r#"
            return {
                abi = 1,
                on_room_start = function(ctx)
                    return { { kind = "reset_interactive_prop", prop_id = 77 } }
                end,
            }
            "#,
        )
        .unwrap();
    room.finish_tick(&queries, 6, Duration::from_secs(5))
        .unwrap();
    assert_eq!(
        room.interactive_props().props[&77].position,
        [1.0, 2.0, 3.0]
    );
}

#[cfg(feature = "lua-mods")]
#[test]
fn lua_mod_abi_v2_dance_party_chat_bots_status_and_voice_cues() {
    use crate::{
        contact_world::ContactWorld,
        game_heroes::HeroCatalog,
        mod_script::ModHost,
        mover_settings::{MoverCatalog, MoverSettings},
        vitals::Vitals,
    };
    use std::sync::Mutex;

    let dance_src = include_str!("../../scripts/mods/dance_party_workshop.lua");
    let host = Arc::new(Mutex::new(
        ModHost::from_source("dance_party_workshop.lua", dance_src).unwrap(),
    ));
    let heroes = Arc::new(
        HeroCatalog::from_json(
            r#"{
                "0x02E000000000006E": {"name":"Soldier: 76", "class":"Damage", "body":"0x04000000000003CF"},
                "0x02E0000000000003": {"name":"Tracer", "class":"Damage", "body":"0x040000000000002B"}
            }"#,
        )
        .unwrap(),
    );

    let bots = fixture_bots();
    let bot_entity = bots[0].entity;
    let initial_bot_yaw = bots[0].yaw;
    let mut room = RoomSimulation::new(bots).unwrap();
    let ground = Arc::new(fixture_ground());
    let movers = MoverCatalog::default();
    let queries = BotQueries::new(false, &movers, &ground, None);
    room.enable_spatial_queries(false, Arc::clone(&ground), RoomSpatialNeeds::Queries);
    room.set_mod_world_assets(Arc::clone(&heroes), Arc::clone(&ground));
    room.set_player_names(HashMap::from([(1, String::from("HeroTester"))]));
    room.attach_mod_host(
        Arc::clone(&host),
        0x0800_0000_0000_0688,
        0x0230_0000_0000_0012,
    );

    let owner = BodyId {
        entity: 0xa000_0100,
        generation: 1,
    };
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        [0.0, 0.0, 0.0],
        0,
        0x08,
    )
    .unwrap();
    let mut player_state = PlayerBodyState::new(owner, 1, 0, 0, SOLDIER, mover, None, 1, None);
    player_state.set_test_halted_vitals(Vitals::new(200.0, 0.0, 0.0));
    room.spawn_player_body(player_state).unwrap();
    assert_eq!(room.player_body(owner).unwrap().name, "HeroTester");

    // 1. Live in-room chat `!danceall` makes bots enter dance mode and returns broadcast replies!
    let replies = room.mod_dispatch_chat_from(
        Some(1),
        "HeroTester",
        "!danceall",
        2,
        Duration::from_secs(1),
    );
    assert!(
        replies.iter().any(|r| r.contains("DANCE PARTY")),
        "expected dance party reply, got {replies:?}"
    );
    room.finish_tick(&queries, 3, Duration::from_millis(1016))
        .unwrap();
    let danced_yaw = room.state_mut(bot_entity).unwrap().unwrap().bot.yaw;
    assert_ne!(
        danced_yaw, initial_bot_yaw,
        "dancing bot must visibly rotate/animate"
    );

    // 2. `!hero tracer` resolves Tracer via HeroCatalog and queues a live hero switch for owner 1!
    let replies = room.mod_dispatch_chat_from(
        Some(1),
        "HeroTester",
        "!hero tracer",
        4,
        Duration::from_millis(1032),
    );
    assert!(replies.iter().any(|r| r.contains("Tracer")));
    assert_eq!(room.take_mod_hero_switch(1), Some(0x02E0_0000_0000_0003));

    // 3. `!slowmo 0.5` sets time_scale to 0.5x and scales mover speed & gravity proportionally!
    let _ = room.mod_dispatch_chat_from(
        Some(1),
        "HeroTester",
        "!slowmo 0.5",
        5,
        Duration::from_millis(1048),
    );
    assert!((room.mod_active_rules().time_scale - 0.5).abs() < 1e-6);
    let (g_scale, s_scale) = room.player_body(owner).unwrap().mover.mod_scales();
    assert!((g_scale - 0.25).abs() < 1e-6 && (s_scale - 0.5).abs() < 1e-6);

    // 4. `!bot tracer 1` spawns a brand-new bot dynamically and queues its EntityUpdate create!
    let bot_count_before = room.by_wire.len();
    let _ = room.mod_dispatch_chat_from(
        Some(1),
        "HeroTester",
        "!bot tracer 1",
        6,
        Duration::from_millis(1064),
    );
    assert_eq!(room.by_wire.len(), bot_count_before + 1);
    assert!(!room.take_created().is_empty());

    // 5. Verify OW 1.74 ability1 (0x0008) -> PlayVoiceCue, and reload (0x0400) -> lethal Kill -> PlayerDeath!
    host.lock()
        .unwrap()
        .reload_source(
            "abi2_edges.lua",
            r#"
            return {
                abi = 2,
                on_button = function(ctx, player, button, pressed)
                    if pressed and button == "ability1" then
                        return {
                            { kind = "play_voice_cue", entity = player.entity, stimulus = 12345 },
                        }
                    elseif pressed and button == "reload" then
                        return {
                            { kind = "kill", entity = player.entity },
                        }
                    end
                    return {}
                end,
            }
            "#,
        )
        .unwrap();
    room.player_body_mut(owner).unwrap().pending_pressed_buttons = 0x0008; // OW 1.74 ability1
    room.finish_tick(&queries, 7, Duration::from_millis(1080))
        .unwrap();
    let cues = room.take_player_cues();
    assert!(
        cues.iter().any(|c| matches!(
            c.notice,
            crate::game_combat_messages::CombatNotice::Voice {
                stimulus: 12345,
                ..
            }
        )),
        "PlayVoiceCue must emit CombatNotice::Voice, got {cues:?}"
    );
    room.player_body_mut(owner).unwrap().pending_pressed_buttons = 0x0400; // OW 1.74 reload
    room.finish_tick(&queries, 8, Duration::from_millis(1096))
        .unwrap();
    let deaths = room.take_player_deaths();
    assert_eq!(
        deaths.len(),
        1,
        "lethal ModAction::Kill must emit PlayerDeath"
    );
    assert_eq!(deaths[0].body, owner);
}
