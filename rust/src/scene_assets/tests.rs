use super::*;

mod actor_startup;
mod junkenstein;
mod mode_presentation;
#[cfg(feature = "inspector")]
mod script_coverage;

#[test]
fn weapon_queries_preserve_openings_in_the_prepared_combat_geometry() {
    use crate::collision_cache::{CollisionCache, Mesh, Triangle};
    use serde_json::{Value as Json, json};

    let input: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/map-combat-ray-geometry-v1.json"
    ))
    .unwrap();
    let triangles: Vec<[[f32; 3]; 3]> =
        serde_json::from_value(input["combat_triangles"].clone()).unwrap();
    let movement: Vec<[[f32; 3]; 3]> =
        serde_json::from_value(input["movement_triangles"].clone()).unwrap();
    let category = input["layered_weapon_terrain_category"].as_u64().unwrap() as u16;
    let cache = |triangles: Vec<[[f32; 3]; 3]>, weapon_category: Option<u16>| {
        let mut header = json!({
            "meshes":[[triangles.len()*3, triangles.len()]],
            "hulls":0,"models":0,"placements":0,"identifiers":[]
        });
        if let Some(category) = weapon_category {
            header["weapon_terrain_category"] = json!(category);
            header["geometry"] = json!("expanded-static-rays");
            header["category_mask"] = json!(65535);
            header["prop_scope"] = json!("indexed-static-models");
            header["map"] = json!("0x0800000000004242");
            header["chunk"] = json!(format!("0x{:016X}", chunk_guid(0x0800_0000_0000_4242)));
            header["ckey"] = json!("synthetic-map");
        }
        CollisionCache {
            header: serde_json::from_value(header).unwrap(),
            meshes: vec![Mesh {
                vertices: triangles.iter().flatten().copied().collect(),
                triangles: (0..triangles.len() as i32)
                    .map(|i| Triangle {
                        corners: [3 * i, 3 * i + 2, 3 * i + 1],
                        wings: [-1; 3],
                        mask: weapon_category.unwrap_or(MOVER_CATEGORY),
                        surface: 0,
                    })
                    .collect(),
            }],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        }
    };
    let identity = RayCacheIdentity {
        map_guid: 0x0800_0000_0000_4242,
        collision_ckey: "synthetic-map",
        placement_ckey: None,
        prop_records_sha256: None,
    };
    let mut scene = PreparedScene::fixture_for_bots(Vec::new());
    let groups = BTreeSet::new();
    let movement = cache(movement, None);
    scene.ground = Arc::new(CollisionScene::movement_probe(&movement, &groups).unwrap());
    let mut combat = cache(triangles, Some(category));
    combat
        .header
        .meshes
        .extend_from_slice(&movement.header.meshes);
    combat.meshes.extend(movement.meshes.iter().cloned());
    let movement_contacts = Arc::new(ContactWorld::from_movement_cache(movement, &groups).unwrap());
    scene.contacts = Some(Arc::clone(&movement_contacts));
    scene.combat_rays =
        Arc::new(CollisionScene::expanded_rays(&combat, &groups, identity).unwrap());
    scene.combat_sweeps = Some(Arc::new(
        ContactWorld::from_expanded_ray_cache(combat, &groups, identity).unwrap(),
    ));
    assert_eq!(scene.combat_rays.weapon_terrain_category(), Some(category));
    assert_eq!(scene.ground.weapon_terrain_category(), None);
    for row in input["rays"].as_array().unwrap() {
        let origin = serde_json::from_value(row["origin"].clone()).unwrap();
        let direction = [0.0, 0.0, 1.0];
        let legacy = scene
            .movement_index()
            .cast_ray(origin, direction, 4.0, MOVER_CATEGORY)
            .unwrap();
        assert!(legacy.is_some());
        let fallback = BotQueries::new(
            false,
            &scene.movers,
            &scene.ground,
            Some(&movement_contacts),
        );
        assert!(
            fallback
                .projectile_wall(origin, direction, 4.0, 0.2)
                .unwrap()
                .is_some()
        );
        assert_eq!(fallback.combat_ray_geometry(), "movement");
        let (_, queries) = scene.bot_simulation();
        assert_eq!(queries.combat_ray_geometry(), "expanded");
        let expected = row["distance"].as_f64();
        assert_eq!(
            queries.hitscan_wall(origin, direction, 4.0).unwrap(),
            expected
        );
        assert_eq!(
            queries
                .projectile_wall(origin, direction, 4.0, 0.0)
                .unwrap()
                .map(|hit| hit.distance),
            expected
        );
        for (radius, key) in [(0.2, "narrow_sweep_blocked"), (0.6, "wide_sweep_blocked")] {
            assert_eq!(
                queries
                    .projectile_wall(origin, direction, 4.0, radius)
                    .unwrap()
                    .is_some(),
                row[key].as_bool().unwrap(),
                "{} ({radius})",
                row["origin"],
            );
        }
    }

    let gap = &input["small_projectile_gap"];
    let origin = serde_json::from_value(gap["origin"].clone()).unwrap();
    let (_, queries) = scene.bot_simulation();
    for (key, blocked) in [("clear_radius", false), ("blocked_radius", true)] {
        let radius = serde_json::from_value(gap[key].clone()).unwrap();
        assert_eq!(
            queries
                .projectile_wall(origin, [0.0, 0.0, 1.0], 4.0, radius)
                .unwrap()
                .is_some(),
            blocked,
            "{key}"
        );
    }
}

#[test]
#[ignore = "requires prepared 104319 Practice Range expanded ray geometry"]
fn prepared_map_selects_the_validated_combat_ray_side_cache() {
    let dir = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(dir).unwrap();
    let prepared = assets
        .map_collision(&GameMap::practice_range(), true)
        .unwrap();
    assert!(!Arc::ptr_eq(&prepared.combat_rays, &prepared.ground));
    let movement = prepared.contacts.unwrap();
    let sweeps = prepared.combat_sweeps.unwrap();
    assert!(!Arc::ptr_eq(&movement, &sweeps));
    assert!(!sweeps.shapes().is_empty());
}

#[test]
#[ignore = "requires prepared 104319 Practice Range movement geometry"]
fn prepared_map_fallback_preserves_projectile_sweeps_without_mover_contacts() {
    let input: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/map-combat-ray-geometry-v1.json"
    ))
    .unwrap();
    let dir = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let mut assets = SceneAssets::load(&dir).unwrap();
    let map = GameMap::practice_range();
    let movement_name = format!("{:04X}.col", map.map_guid as u32);
    let isolated = tempfile::tempdir().unwrap();
    let collision = isolated.path().join("collision");
    fs::create_dir(&collision).unwrap();
    let data = isolated.path().join("data");
    fs::create_dir(&data).unwrap();
    let keys = "collision_keys_174.json";
    fs::copy(dir.join("data").join(keys), data.join(keys)).unwrap();
    let movement = collision.join(&movement_name);
    fs::copy(assets.collision.join(movement_name), &movement).unwrap();
    assets.collision = collision;

    for case in input["movement_fallback"].as_array().unwrap() {
        if case["expanded_cache"] == "rejected" {
            fs::write(movement.with_extension("rays.col"), b"incomplete cache").unwrap();
        }
        let mover_contacts = case["mover_contacts"].as_bool().unwrap();
        let prepared = assets.map_collision(&map, mover_contacts).unwrap();
        assert!(Arc::ptr_eq(&prepared.combat_rays, &prepared.ground));
        let sweeps = prepared.combat_sweeps.as_ref().unwrap();
        assert!(!sweeps.shapes().is_empty());
        assert_eq!(prepared.contacts.is_some(), mover_contacts);
        if let Some(contacts) = prepared.contacts {
            assert!(Arc::ptr_eq(&contacts, sweeps));
        }
    }
}

#[test]
fn practice_only_omits_owner_runtime_when_all_owner_content_is_absent() {
    use serde_json::json;

    #[derive(Deserialize)]
    struct Case {
        name: String,
        codecs: bool,
        bodies: BTreeMap<String, Option<String>>,
        expected: String,
    }
    #[derive(Deserialize)]
    struct Contract {
        schema_version: u32,
        hero: String,
        catalog_body: String,
        cases: Vec<Case>,
    }

    let contract: Contract = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/practice-owner-content-v1.json"
    ))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    let heroes = Arc::new(
        HeroCatalog::from_json(
            &json!({
                (contract.hero.clone()): {
                    "name": "Fixture owner", "class": "Damage", "body": contract.catalog_body,
                },
            })
            .to_string(),
        )
        .unwrap(),
    );
    let hero = heroes.get(parse_guid(&contract.hero).unwrap()).unwrap();
    let spawn = OwnerBodySpawn {
        slot: 0,
        controller: OWNER_PROBE_CONTROLLER,
        entity: 0xa000_0101,
        body_filter: team_bit(0).unwrap(),
        frame: FIRST_TICK,
        pose: Default::default(),
        eye_position: None,
        skin: 0,
        quanta: RETAIL_QUANTA,
    };
    for case in contract.cases {
        let bodies: serde_json::Map<_, _> = case
            .bodies
            .into_iter()
            .map(|(key, hero)| {
                (
                    key,
                    json!({"hero": hero, "name": "Fixture", "graphs": [], "weapons": []}),
                )
            })
            .collect();
        let bundle = json!({"version": 1, "graphs": [], "bodies": bodies, "missing": []});
        let assets = SceneAssets {
            junkenstein_validation: None,
            environment: EnvironmentCatalog::default(),
            ability_rules: crate::hero_ability_rules::HeroAbilityRules::default_shared(),
            collision: PathBuf::new(),
            junkenstein_map_world: PathBuf::new(),
            last_collision: Mutex::default(),
            trace_contacts: false,
            mode_props: BTreeMap::new(),
            graphs: Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap()),
            codecs: case
                .codecs
                .then(|| Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap())),
            practice_owner_body: OnceLock::new(),
            bots: BotCatalog::default(),
            movers: Arc::new(MoverCatalog::default()),
            hurt_volumes: Arc::new(HurtVolumeCatalog::default()),
            actor_voice: None,
            anim_timing: None,
            stats: Arc::new(StatsCatalog::default()),
            heroes: Arc::clone(&heroes),
            pools: Arc::new(PoolCatalog::default()),
            combat_voice: Arc::new(CombatVoiceCatalog::default()),
        };
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        scene.initial_board.index = crate::game_stats::GMP_STATS;
        scene.owner_body_factory = assets.compile_owner_factories(&scene);
        assert_eq!(
            scene.owner_body_factory.is_none(),
            case.expected == "scriptless",
            "{}",
            case.name
        );
        let runtime = scene.owner_body_runtime(hero, spawn);
        if case.expected == "scriptless" {
            assert!(matches!(runtime, Ok(None)), "{}", case.name);
            continue;
        }
        let Err(error) = runtime else {
            panic!(
                "{} did not reject an incomplete owner configuration",
                case.name
            );
        };
        assert_eq!(error.kind(), io::ErrorKind::Unsupported, "{}", case.name);
        let source = error
            .get_ref()
            .and_then(|source| source.downcast_ref::<Arc<SceneOwnerPreparationError>>())
            .map(Arc::as_ref);
        match (case.expected.as_str(), source) {
            ("missing_codecs", Some(SceneOwnerPreparationError::Codecs)) => {}
            ("missing_body", Some(SceneOwnerPreparationError::Body(error)))
                if matches!(error.as_ref(), OwnerBodyPreparationError::MissingBody) => {}
            _ => panic!("{} produced unexpected rejection: {error}", case.name),
        }
    }
}

#[test]
fn owner_factory_selector_starts_the_validated_actor_definition() {
    use serde_json::{Map, json};

    #[derive(Deserialize)]
    struct Actor {
        hero: String,
        body: String,
        root: u32,
    }
    #[derive(Deserialize)]
    struct Contract {
        schema_version: u32,
        accepted: [Actor; 2],
        rejected: Actor,
        unlisted: Actor,
    }

    let contract: Contract = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/practice-owner-selector-v1.json"
    ))
    .unwrap();
    assert_eq!(contract.schema_version, 1);
    let [first, second] = &contract.accepted;
    let bodies: Map<_, _> = [
        (first, &first.hero),
        (second, &second.hero),
        (&contract.rejected, &first.hero),
        (&contract.unlisted, &contract.unlisted.hero),
    ]
    .into_iter()
    .map(|(actor, declared_hero)| {
        (
            actor.body.clone(),
            json!({
                "name": "Fixture",
                "hero": declared_hero,
                "weapons": [],
                "graphs": [{"graph": actor.root, "m_75408640": 0}],
            }),
        )
    })
    .collect();
    let graphs = contract.accepted.each_ref().map(|actor| {
        json!({
            "index": actor.root, "items": 0, "nodes": [], "states": [], "entries": [],
            "remote": [], "sync_vars": [], "containers": {}, "unknown_nodes": [], "fields": {},
            "header": {"m_nodesBitCount": 4, "m_statesBitCount": 4,
                       "m_remoteSyncNodesBitCount": 4, "m_syncVarsBitCount": 4}
        })
    });
    let bundle = json!({"version": 1, "graphs": graphs, "bodies": bodies, "missing": []});
    let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    let codecs = Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap());
    let make_hero = |actor: &Actor| Hero {
        guid: parse_guid(&actor.hero).unwrap(),
        name: format!("Fixture actor {}", actor.hero),
        role: "Damage".into(),
        body: parse_guid(&actor.body).unwrap(),
    };
    let mut scene = PreparedScene::fixture_for_bots(Vec::new());
    let definitions = [first, second, &contract.rejected].map(|actor| {
        let hero = make_hero(actor);
        let prepared = PlayerBodyFactory::from_body(
            Arc::clone(&graphs),
            Arc::clone(&codecs),
            scene.map().mode_guid,
            &hero,
            None,
            false,
        );
        (
            (hero.guid, hero.body),
            prepared
                .map(Arc::new)
                .map_err(|error| Arc::new(SceneOwnerPreparationError::Body(Box::new(error)))),
        )
    });
    assert!(definitions[0].1.is_ok() && definitions[1].1.is_ok());
    let invalid = definitions[2].1.as_ref().err().map(Arc::as_ref);
    assert!(
        matches!(invalid, Some(SceneOwnerPreparationError::Body(error))
        if matches!(error.as_ref(), OwnerBodyPreparationError::MissingBody))
    );
    scene.owner_body_factory = Some(owner_factory_selector(
        definitions.into_iter().collect(),
        Arc::clone(&graphs),
        Some(Arc::clone(&codecs)),
        scene.map().mode_guid,
        Arc::new(PoolCatalog::default()),
        Some(false),
        None,
    ));
    let spawn = OwnerBodySpawn {
        slot: 0,
        controller: OWNER_PROBE_CONTROLLER,
        entity: 0xa000_0101,
        body_filter: team_bit(0).unwrap(),
        frame: FIRST_TICK,
        pose: Default::default(),
        eye_position: None,
        skin: 0,
        quanta: RETAIL_QUANTA,
    };

    for actor in &contract.accepted {
        let runtime = scene
            .owner_body_runtime(&make_hero(actor), spawn)
            .unwrap()
            .unwrap();
        let snapshot = runtime.snapshot().unwrap();
        assert_eq!(snapshot.entity, spawn.entity);
        assert_eq!(
            snapshot
                .instances
                .values()
                .map(|instance| instance.graph)
                .collect::<Vec<_>>(),
            [actor.root]
        );
    }
    assert!(matches!(
        scene.owner_body_runtime(&make_hero(&contract.rejected), spawn),
        Err(error) if error.kind() == io::ErrorKind::Unsupported
    ));
    assert!(
        scene
            .owner_body_runtime(&make_hero(&contract.unlisted), spawn)
            .unwrap()
            .is_none()
    );
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_cassidy_primary_and_fan_damage_all_boss_bodies() {
    use crate::pve_encounter::navigation::{JUNKRAT, REAPER, ROADHOG, SUMMONER, WITCH};
    junkenstein::verify_normal_cassidy_damage_and_charge(
        &[REAPER, ROADHOG, JUNKRAT, WITCH, SUMMONER],
        &[(false, false), (false, true), (true, true)],
    );
}

#[test]
#[ignore = "requires the prepared 104319 Practice Range runtime"]
fn prepared_practice_firing_lane_stops_at_the_front_bot() {
    use crate::{
        game_hitscan::direction_for_angles,
        static_ray::{along, norm},
    };

    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let bots: Vec<_> = scene.bots().unwrap().collect();
    let (shooter, front, rear_position) = bots
        .iter()
        .filter(|bot| bot.spawn.team == 1 && !bot.spawn.placement_graphs.is_empty())
        .find_map(|shooter| {
            let direction = direction_for_angles(shooter.spawn.yaw, 0);
            bots.iter()
                .filter(|bot| bot.spawn.team == 0 && bot.spawn.placement_graphs.is_empty())
                .find_map(|target| {
                    let delta: [f64; 3] =
                        std::array::from_fn(|axis| target.position[axis] - shooter.position[axis]);
                    let distance: f64 = delta.into_iter().zip(direction).map(|(a, b)| a * b).sum();
                    let miss = std::array::from_fn(|axis| delta[axis] - direction[axis] * distance);
                    (distance > 0.0 && norm(miss) < 0.05).then(|| {
                        (
                            shooter.entity,
                            target.entity,
                            along(target.position, direction, 1.5),
                        )
                    })
                })
        })
        .expect("an authored enemy firing lane through a friendly bot");

    check_practice_firing_lane(scene, shooter, front, rear_position, true);
    check_practice_firing_lane(
        assets.prepare(GameMap::practice_range()).unwrap(),
        shooter,
        front,
        rear_position,
        false,
    );
}

fn check_practice_firing_lane(
    mut scene: PreparedScene,
    shooter: u32,
    front: u32,
    rear_position: [f64; 3],
    live_cover: bool,
) {
    use crate::{
        game_clock::{FIRST_TICK, TICK_PERIOD},
        game_combat::{ObservedProjectileEvent, PlayerBodyState},
        game_combat_messages::CombatNotice,
        game_replication::BodyId,
        mover_settings::MoverSettings,
        script_player::fixture_body_factory,
        static_ray::norm,
    };

    let (room, queries) = scene.bot_simulation();
    let (factory, mut spawn) = fixture_body_factory();
    spawn.frame = FIRST_TICK;
    spawn.pose.position = rear_position;
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        rear_position,
        0,
        MOVER_CATEGORY,
    )
    .unwrap();
    let rear = BodyId {
        entity: spawn.entity,
        generation: 1,
    };
    let state = PlayerBodyState::new(
        rear,
        spawn.controller,
        spawn.slot,
        0,
        crate::game_heroes::SOLDIER,
        mover,
        Some(Box::new(factory.start(spawn).unwrap())),
        FIRST_TICK,
        None,
    );
    let rear_health = state.vitals().unwrap().total();
    room.spawn_player_body(state).unwrap();
    room.enable_received_damage_feedback();
    let front_health = room.body_healths().unwrap()[&front];
    let mut lane_flights = BTreeSet::new();
    let mut cover_down = false;
    for tick in FIRST_TICK + 1..=FIRST_TICK + 800 {
        let elapsed = TICK_PERIOD * (tick - FIRST_TICK);
        room.finish_tick(&queries, tick, elapsed).unwrap();
        let mut completed = false;
        for event in room.take_projectile_reports() {
            match event {
                ObservedProjectileEvent::Launched {
                    id, presentation, ..
                } if presentation.volley.event.entity == shooter => {
                    lane_flights.insert(id);
                    if !live_cover && !cover_down {
                        room.kill_bot_for_test(front, tick, elapsed);
                        cover_down = true;
                    }
                }
                ObservedProjectileEvent::Completed { id, .. } if lane_flights.remove(&id) => {
                    completed = true;
                }
                _ => {}
            }
        }
        let cues: Vec<_> = room
            .take_player_cues()
            .into_iter()
            .filter(|cue| matches!(cue.notice, CombatNotice::DamageReceived { .. }))
            .collect();
        let player_hp = room.player_body(rear).unwrap().vitals().unwrap().total();
        if live_cover {
            assert_eq!(player_hp, rear_health);
            assert!(cues.is_empty());
            let health = room.body_healths().unwrap()[&front];
            if health < front_health {
                assert!(health > 0.0 && completed);
                return;
            }
        } else if player_hp < rear_health {
            assert!(cover_down && room.is_bot_dead(front) && completed);
            let [cue] = cues.as_slice() else {
                panic!("expected one received-damage cue after the lethal cover was removed");
            };
            assert_eq!(cue.listener, rear);
            let CombatNotice::DamageReceived {
                source,
                victim,
                dealt,
                origin: Some(origin),
                normal: Some(normal),
                ..
            } = cue.notice
            else {
                panic!("expected a damage cue with the settled NPC hit geometry");
            };
            assert_eq!(source, shooter);
            assert_eq!(victim, rear.entity);
            assert!((f64::from(dealt) - (rear_health - player_hp)).abs() < 1e-6);
            assert!((norm(normal) - 1.0).abs() < 1e-8);
            let dot: f64 = normal
                .into_iter()
                .zip(origin.into_iter().zip(rear_position))
                .map(|(axis, (source, victim))| axis * (source - victim))
                .sum();
            assert!(dot > 0.0);
            return;
        }
    }
    panic!("the authored lane did not settle the expected impact with live_cover={live_cover}");
}

#[test]
#[ignore = "requires the local 104319 Practice Range runtime with authored mode rules"]
fn prepared_reaper_primary_projects_the_authored_pellet_count() {
    use crate::{
        commands::{Command, FIRE},
        game_clock::FIRST_TICK,
        game_combat::PosedShot,
        game_replication::BodyStep,
    };

    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene.heroes().find_hero("Reaper").unwrap();
    let mover = scene.player_spawn_mover(hero.body, 0).unwrap();
    let mut owner = scene
        .owner_body_runtime(
            hero,
            OwnerBodySpawn {
                slot: 0,
                controller: 0xa000_0100,
                entity: 0xa000_0101,
                body_filter: team_bit(0).unwrap(),
                frame: FIRST_TICK,
                pose: mover.pose(),
                eye_position: script_eye_position(hero.guid, &mover),
                skin: 0,
                quanta: RETAIL_QUANTA,
            },
        )
        .unwrap()
        .unwrap();

    for frame in FIRST_TICK + 1..=FIRST_TICK + 90 {
        let command = Command {
            frame,
            buttons: FIRE,
            ..Command::default()
        };
        owner.advance(command, &mover, BodyStep::Posed).unwrap();
        let shots = owner.take_shots(Some(command), &mover);
        if shots.is_empty() {
            continue;
        }
        assert_eq!(shots.len(), 20);
        let mut directions = BTreeSet::new();
        for shot in shots {
            let PosedShot::Hitscan(shot) = shot else {
                panic!("expected a shotgun ray");
            };
            assert_eq!(shot.shot_frame, frame);
            assert!(shot.hit.amount > 0.0);
            directions.insert(shot.direction.map(f64::to_bits));
        }
        assert_eq!(directions.len(), 20);
        return;
    }
    panic!(
        "the prepared Reaper body did not fire: {:?}",
        owner.snapshot().unwrap().diagnostics
    );
}

#[test]
#[ignore = "requires the local 104319 Practice Range runtime"]
fn prepared_cassidy_fan_uses_authored_cone_and_falloff() {
    use crate::{
        commands::{Command, SECONDARY},
        game_clock::FIRST_TICK,
        game_combat::{PosedShot, ShotContext},
        game_combat_messages::CombatNotice,
        game_replication::BodyStep,
    };

    let assets = SceneAssets::load(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    let hero = scene.heroes().find_hero("Cassidy").unwrap();
    let mover = Mover::new(
        *scene.movers.for_body(Some(hero.body)),
        Arc::clone(scene.contacts.as_ref().unwrap()),
        [0.0; 3],
        0,
        MOVER_CATEGORY,
    )
    .unwrap();
    let mut owner = scene
        .owner_body_runtime(
            hero,
            OwnerBodySpawn {
                slot: 0,
                controller: 0xa000_0100,
                entity: 0xa000_0101,
                body_filter: team_bit(0).unwrap(),
                frame: FIRST_TICK,
                pose: mover.pose(),
                eye_position: script_eye_position(hero.guid, &mover),
                skin: 0,
                quanta: RETAIL_QUANTA,
            },
        )
        .unwrap()
        .unwrap();
    let mut directions = BTreeSet::new();
    let mut volley = Vec::new();
    for frame in FIRST_TICK + 1..=FIRST_TICK + 90 {
        let command = Command {
            frame,
            buttons: SECONDARY,
            ..Command::default()
        };
        owner.advance(command, &mover, BodyStep::Posed).unwrap();
        for shot in owner.take_shots(Some(command), &mover) {
            let PosedShot::Hitscan(shot) = shot else {
                panic!("expected hitscan from the authored alternate fire");
            };
            volley.push(*shot);
            directions.insert(shot.direction.map(f64::to_bits));
            assert!(shot.direction[2] >= 6.0_f64.to_radians().cos() - 1e-6);
            assert_eq!(shot.hit.damage_at(15.0, false), 50.0);
            assert!((shot.hit.damage_at(30.0, false) - 32.5).abs() < 1e-5);
            assert!((shot.hit.damage_at(50.0, false) - 15.0).abs() < 1e-5);
        }
    }
    assert_eq!(volley.len(), 6);
    assert_eq!(directions.len(), volley.len());

    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/cassidy-fan-falloff-v1.json"
    ))
    .unwrap();
    for case in contract["cases"].as_array().unwrap() {
        let range = case["contact_distance"].as_f64().unwrap();
        let per_round = case["raw_damage_per_round"].as_f64().unwrap();
        let bot = Bot::fixture_walker([0.0, 0.0, 1.0]);
        let target = bot.entity;
        let health = bot.spawn.health;
        let mut room = PreparedScene::fixture_for_bots(vec![bot]);
        let (simulation, queries) = room.bot_simulation();
        let target_pose = queries.target(simulation.bodies().unwrap().next().unwrap());
        let [x, y, z] = target_pose.feet;
        let centre = [x, y + target_pose.height * 0.5, z];
        let mut total = 0.0;
        let mut eliminated = false;
        for mut shot in volley.iter().copied() {
            // Keep the authored spread. Translate the ray so that each round
            // goes through the torso and contacts it at the requested range.
            // This measures the full burst even if natural spread would miss.
            let [dx, _, dz] = shot.direction;
            let axis_range = range + target_pose.radius / dx.hypot(dz);
            shot.origin = crate::static_ray::along(centre, shot.direction, -axis_range);
            let context = ShotContext {
                team: 0,
                quanta: RETAIL_QUANTA,
                tick: shot.shot_frame,
                now: std::time::Duration::ZERO,
            };
            let hit = simulation
                .hitscan(&queries, shot, context, |id| id == target)
                .unwrap();
            if eliminated {
                assert!(hit.is_none());
                continue;
            }
            let hit = hit.unwrap_or_else(|| panic!("miss at {range} m"));
            let expected = per_round.min(health - total);
            assert!(
                (hit.damage.amount - expected).abs() < 1e-5,
                "at {range} m: {} vs {expected}",
                hit.damage.amount
            );
            assert!(matches!(
                hit.notices[0],
                Some(CombatNotice::Hit { target: victim, critical: false, shot_frame, .. })
                    if victim == target && shot_frame == shot.shot_frame
            ));
            total += hit.damage.amount;
            eliminated = matches!(hit.notices[1], Some(CombatNotice::Elimination { victim, .. }) if victim == target);
        }
        let expected = case["settled_damage"].as_f64().unwrap();
        assert!(
            (total - expected).abs() < 1e-5,
            "at {range} m: {total} vs {expected}"
        );
        assert_eq!(
            eliminated,
            case["elimination"].as_bool().unwrap(),
            "at {range} m"
        );
        assert_eq!(
            simulation.bodies().unwrap().next().unwrap().dead,
            eliminated
        );
    }
}

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn normal_cassidy_common_npc_damage_charge_and_retirement() {
    use crate::pve_encounter::navigation::{SHOCK_TIRE, ZOMBARDIER};
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
    use crate::mover_modifiers::SPEED_SLOT;
    use crate::script_init::{Snapshot, StateKind};
    use crate::script_snapshot_wire::identity_test_reader::IdentityReader;
    use crate::script_snapshot_wire::{View, uncertain_polled_switch};
    use crate::script_stream::{BodyStream, DeltaKind};
    use crate::script_wire::Value;
    use std::collections::BTreeSet;

    const SESSION_FRAMES: u32 = 900;
    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    #[cfg(feature = "inspector")]
    let coverage = script_coverage::Capture::from_env(&runtime);
    #[cfg(not(feature = "inspector"))]
    assert!(
        std::env::var_os("ORPHEUS_SCRIPT_COVERAGE").is_none(),
        "script coverage capture requires the inspector feature"
    );
    let assets = SceneAssets::load(runtime).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();
    assert!(scene.movement_ready());
    let hero = scene.heroes().get(SOLDIER).unwrap();
    let mover = scene.player_spawn_mover(hero.body, 0).unwrap();
    let spawn = OwnerBodySpawn {
        slot: 0,
        controller: 0xa000_0100,
        entity: 0xa000_0101,
        body_filter: team_bit(0).unwrap(),
        frame: FIRST_TICK,
        pose: mover.pose(),
        eye_position: script_eye_position(hero.guid, &mover),
        skin: 0,
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
        let probe_path = std::env::var_os("ORPHEUS_SPRINT_WIRE_PROBE");
        let mut probe = Vec::new();
        for view in [View::Owner, View::Observer] {
            let projected = encoder.project(snapshot, view).unwrap();
            let mut stream = BodyStream::new(spawn.entity);
            let baseline = encoder.queue_full(&mut stream, &projected).unwrap();
            if probe_path.is_some() && view == View::Owner {
                let bits = baseline.update.chunk.as_ref().unwrap();
                probe.push(
                    serde_json::json!({"t": 0, "bits": bits.bit_len(), "data": bits.as_bytes()}),
                );
            }
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
        for t in 1..=SESSION_FRAMES {
            let frame = FIRST_TICK + t;
            let (forward, buttons) = match t {
                188..=281 | 520..=540 | 600..=640 => (0, 1),
                300 => (0, 0x400),
                // Match the observed two-frame combined mask and the
                // subsequent held Shift before forward-only movement.
                700..=701 => (127, 0x2008),
                702..=709 => (127, 0x8),
                710..=760 => (127, 0),
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
                700..=709 => (5, Some(1)),
                710..=760 => (1, Some(1)),
                761.. => (0, Some(0)),
                _ => (0, None),
            };
            assert_eq!(
                (sprint.states[&1].kind, int(sprint.variables.get(&699))),
                (StateKind::Ability { flags }, hud),
                "{t}"
            );
            let speed_modifier = runtime
                .movement_modifiers()
                .expect("complete owner movement state")
                .iter()
                .find(|(key, value)| key.instance == sprint_id && value.index == SPEED_SLOT)
                .map(|(_, value)| value);
            assert_eq!(
                speed_modifier.is_some_and(|modifier| modifier.apply(1.0) > 1.0),
                (700..=760).contains(&t),
                "Sprint speed modifier at {t}"
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
                    if probe_path.is_some() && next.view() == View::Owner {
                        let bits = chunk.update.chunk.as_ref().unwrap();
                        probe.push(serde_json::json!({"t": t, "bits": bits.bit_len(), "data": bits.as_bytes()}));
                    }
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
        assert_eq!(streams[0].0.command_frame(), FIRST_TICK + SESSION_FRAMES);
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
        if let Some(path) = probe_path {
            std::fs::write(path, serde_json::to_vec(&probe).unwrap()).unwrap();
        }
        #[cfg(feature = "inspector")]
        if let Some(coverage) = coverage {
            coverage.save(hero.guid, SESSION_FRAMES, runtime.snapshot().unwrap());
        }
    } else {
        #[cfg(feature = "inspector")]
        assert!(
            coverage.is_none(),
            "script coverage requires a target-tagged pack"
        );
        assert!(
            std::env::var_os("ORPHEUS_SPRINT_WIRE_PROBE").is_none(),
            "Sprint wire probe requires a target-tagged pack"
        );
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
    let unknown_actor = Hero {
        guid: u64::MAX,
        ..hero.clone()
    };
    assert!(scene.heroes().get(unknown_actor.guid).is_none());
    assert!(
        scene
            .owner_body_runtime(&unknown_actor, spawn)
            .unwrap()
            .is_none()
    );
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
        eye_position: script_eye_position(hero.guid, &mover),
        skin: 0,
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
        eye_position: script_eye_position(hero.guid, &mover),
        skin: 0,
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

#[test]
#[ignore = "requires a prepared 104319 Practice Range pack"]
fn practice_range_stock_bots_drive_overhead_nameplates_through_death_and_respawn() {
    use std::time::Duration;

    use crate::script_snapshot_wire::View;

    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
    assert!(scene.movement_ready());

    let initial: Vec<(u32, usize)> = scene
        .enemy_projections()
        .unwrap()
        .map(|projection| {
            let projected = projection
                .encoder
                .project(projection.snapshot, View::Observer)
                .unwrap();
            assert!(
                projected.has_graph(0x0c18) && projected.has_graph(0x0d24),
                "bot {:08X} missing 0x0C18/0x0D24 nameplate",
                projection.snapshot.entity
            );
            (
                projection.snapshot.entity,
                projection.snapshot.diagnostics.len(),
            )
        })
        .collect();
    assert_eq!(initial.len(), 15);
    assert!(initial.iter().all(|&(_, diagnostics)| diagnostics == 0));

    let patrol = PatrolScript::load().unwrap();
    let mut tick = crate::game_clock::FIRST_TICK + 1;
    let mut now = Duration::from_secs(1);
    scene.step_bots(tick, &patrol).unwrap();
    {
        let (simulation, queries) = scene.bot_simulation();
        assert!(
            simulation
                .finish_tick(&queries, tick, now)
                .unwrap()
                .is_empty()
        );
    }

    // Project the corpse while the delayed death handler is still running.
    let target = initial[0].0;
    tick += 1;
    scene.simulation_mut().kill_bot_for_test(target, tick, now);
    let death_frame = tick;
    let mut saw_kill_feed_pulse = false;
    for _ in 0..10 {
        tick += 1;
        now += crate::game_clock::TICK_PERIOD;
        scene.step_bots(tick, &patrol).unwrap();
        let (simulation, queries) = scene.bot_simulation();
        assert!(
            simulation
                .finish_tick(&queries, tick, now)
                .unwrap()
                .is_empty()
        );
        assert!(simulation.is_bot_dead(target));
        let projection = scene
            .enemy_projections()
            .unwrap()
            .find(|projection| projection.snapshot.entity == target)
            .unwrap_or_else(|| {
                panic!(
                    "missing corpse projection at death + {}",
                    tick - death_frame
                )
            });
        if let Some(parent) = projection
            .snapshot
            .instances
            .values()
            .find(|instance| instance.graph == 0x0c18)
        {
            if parent.states.get(&8).is_some_and(|state| {
                state.active
                    && matches!(
                        state.kind,
                        crate::script_init::StateKind::ClientPulse { count: 1, .. }
                    )
            }) {
                assert_eq!(
                    parent.variables.get(&392).unwrap(),
                    &crate::script_wire::Value::Entity(0xa000_0100)
                );
                saw_kill_feed_pulse = true;
            }
        }
        projection
            .encoder
            .project(projection.snapshot, View::Observer)
            .unwrap_or_else(|error| {
                panic!(
                    "corpse projection at death + {}: {error}",
                    tick - death_frame
                )
            });
    }
    assert!(
        saw_kill_feed_pulse,
        "killed stock bot must activate 0x0C18 state 8 kill-feed pulser and killer #392"
    );

    now += Duration::from_secs(4);
    scene.step_bots(tick + 240, &patrol).unwrap();
    let replacements = {
        let (simulation, queries) = scene.bot_simulation();
        simulation.finish_tick(&queries, tick + 240, now).unwrap()
    };
    assert_eq!(replacements.len(), 1);
    let fresh_entity = replacements[0].created.entity;
    assert_ne!(fresh_entity, target);

    let fresh_projected = scene
        .enemy_projections()
        .unwrap()
        .find(|projection| projection.snapshot.entity == fresh_entity)
        .map(|projection| {
            projection
                .encoder
                .project(projection.snapshot, View::Observer)
                .unwrap()
        })
        .expect("respawned bot script projection");
    assert!(fresh_projected.has_graph(0x0c18));
    assert!(fresh_projected.has_graph(0x0d24));
}

#[test]
#[ignore = "requires prepared external 104319 Practice Range runtime"]
fn prepared_practice_railings_block_player_mover() {
    use crate::{
        collision_cache::read_movement_cache, commands::Command, game_clock::TICK_PERIOD,
        game_heroes::SOLDIER,
    };

    let runtime = PathBuf::from(
        std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("ORPHEUS_RUNTIME_DIR is required"),
    );
    let assets = SceneAssets::load(&runtime).unwrap();
    let scene = assets.prepare(GameMap::practice_range()).unwrap();

    let cache = read_movement_cache(BufReader::new(
        File::open(runtime.join("collision/0688.col")).unwrap(),
    ))
    .unwrap();
    let contacts = scene.contacts.as_ref().expect("Practice Range contacts");
    let breakable_shapes: Vec<_> = contacts
        .shapes()
        .iter()
        .enumerate()
        .filter_map(|(idx, shape)| {
            let placement = shape.breakable_placement()?;
            let p = cache.placements[placement];
            let bounds = shape.fat_bounds()?;
            Some((
                idx,
                placement,
                p.model,
                shape.geometry().mask(),
                p.translation,
                bounds,
            ))
        })
        .collect();
    assert!(
        breakable_shapes.len() >= 148,
        "expected Practice Range breakable railings in ContactWorld"
    );

    let soldier_body = scene.heroes.get(SOLDIER).unwrap().body;
    let settings = *scene.movers.for_body(Some(soldier_body));
    let category = mover_category(scene.map(), 0);
    let dt = TICK_PERIOD.as_secs_f64();

    // Walking straight into intact axis-aligned railing placement 10 must stop at the railing.
    let (_, _, _, _, tr, bounds) = breakable_shapes
        .iter()
        .find(|(_, placement, _, _, _, _)| *placement == 10)
        .copied()
        .expect("placement 10 railing");
    let cx = f64::from(bounds.low[0] + bounds.high[0]) * 0.5;
    let cz = f64::from(bounds.low[2] + bounds.high[2]) * 0.5;
    let floor_y = f64::from(tr[1]);
    let mut mover = Mover::new(
        settings,
        Arc::clone(contacts),
        [cx + 1.5, floor_y, cz],
        -16384,
        category,
    )
    .unwrap();
    mover.set_breakables(Arc::clone(&scene.breakables));
    for frame in 1..=60u32 {
        mover
            .step(
                scene.movement_index(),
                Command {
                    frame,
                    yaw: -16384,
                    pitch: 0,
                    forward: 127,
                    right: 0,
                    buttons: 0,
                    ..Command::default()
                },
                dt,
                |_, _| {},
            )
            .unwrap();
    }
    assert!(
        mover.pose().position[0] > cx + 0.35,
        "player mover walked through intact railing placement 10: pos={:?} cx={cx:.2}",
        mover.pose().position
    );

    // Every model 0 and model 2 railing placement must block a team-0 player capsule sweep.
    let mut swept_hits = 0usize;
    let mut swept_misses = 0usize;
    for &(_, placement, model, _, str_pos, sbounds) in &breakable_shapes {
        if model != 0 && model != 2 {
            continue;
        }
        let p = cache.placements[placement];
        let q = p.rotation;
        let lx = [
            1.0 - 2.0 * (q[1] * q[1] + q[2] * q[2]),
            2.0 * (q[0] * q[1] + q[2] * q[3]),
            2.0 * (q[0] * q[2] - q[1] * q[3]),
        ];
        let lz = [
            2.0 * (q[0] * q[2] + q[1] * q[3]),
            2.0 * (q[1] * q[2] - q[0] * q[3]),
            1.0 - 2.0 * (q[0] * q[0] + q[1] * q[1]),
        ];
        let center = [
            (sbounds.low[0] + sbounds.high[0]) * 0.5,
            str_pos[1],
            (sbounds.low[2] + sbounds.high[2]) * 0.5,
        ];
        let hit_axis = |axis: [f32; 3]| {
            contacts
                .scene_sweep_with_breakables(
                    crate::contact_gjk::sweep::MeshProbe {
                        points: &[[0.0, 0.5, 0.0], [0.0, 1.5, 0.0]],
                        radius: 0.5,
                        start: [
                            center[0] - axis[0] * 1.5,
                            center[1],
                            center[2] - axis[2] * 1.5,
                        ],
                        end: [
                            center[0] + axis[0] * 1.5,
                            center[1],
                            center[2] + axis[2] * 1.5,
                        ],
                        category,
                    },
                    Some(&scene.breakables),
                )
                .unwrap()
                .is_some()
        };
        if hit_axis(lx) || hit_axis(lz) {
            swept_hits += 1;
        } else {
            swept_misses += 1;
        }
    }
    assert_eq!(swept_hits, 148);
    assert_eq!(
        swept_misses, 0,
        "all model 0/2 railings must block capsule sweep"
    );
}
