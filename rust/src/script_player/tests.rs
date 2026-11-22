use std::collections::{BTreeMap, BTreeSet};

use super::{
    world::{BodyWorld, target_delta},
    *,
};
use crate::{
    collision_scene::CollisionScene,
    commands::Command,
    game_combat::{PosedShot, Stun, eye_height},
    game_content::TUTORIAL_MODE,
    game_heroes::CASSIDY,
    game_hitscan::{TargetPose, aim_direction},
    game_replication::BodyStep,
    mover::{Mover, sample_movement_flags},
    scene_assets::MOVER_CATEGORY,
    script_control::LEGACY_PROJECTILE_CONTROL,
    script_expr::to_float,
    script_init::{Site, TargetTagEffect},
    script_wire::Value,
    static_ray::{Vec3, capsule_entry_distance, norm},
    world::GAME_MODE_ENTITY,
};

mod charge_receiver;

#[test]
#[ignore = "requires separately prepared 104319 Junkenstein runtime"]
fn castle_summoner_deadeye_sightlines_follow_real_collision() {
    use crate::game_bots::{BotCatalog, BotWalk};
    use crate::game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog};
    let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let assets = crate::scene_assets::SceneAssets::load(&runtime).unwrap();
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
    let hero = scene.heroes().find_hero("Symmetra").unwrap();
    let catalog=BotCatalog::from_json(&serde_json::json!({
        "heroes":{"0x02E0000000000016":{"name":"Sightline target","body":format!("0x{:016X}",hero.body),"health":1000}},
        "maps":{"0x03":{"hints":{},"spawners":[{"hero":"0x02E0000000000016","team":1,
            "position":[61.984676361083984,18.126224517822266,-103.21149444580078],
            "rotation":[0,0,0,1],"graphs":[]}]}}
    }).to_string()).unwrap();
    let bots = catalog.make(3, |_| BotWalk::default());
    let pose = scene.bot_simulation().1.target(&bots[0]);
    let mut selection = scene.deadeye_scene(0).unwrap();
    selection.targets.push(DeadeyeTarget {
        velocity: Some([0.0; 3]),
        script_state: Default::default(),
        entity: bots[0].entity,
        pose,
        health: 1000.0,
    });
    for [x, y, z] in [
        [87.013671875, 12.6474609375, -64.34765625],
        [79.775390625, 13.0693359375, -78.779296875],
    ] {
        let origin = [x, y + eye_height(CASSIDY, false), z];
        let direction = selection
            .shot_direction(origin, bots[0].entity)
            .expect("the actual auto-aim point is exposed above the balcony");
        let [a, b] = pose.segment();
        let reach = capsule_entry_distance(
            origin,
            direction,
            norm(target_delta(origin, pose)),
            a,
            b,
            pose.radius,
        )
        .unwrap();
        assert!(
            selection
                .collision
                .as_ref()
                .unwrap()
                .cast_ray(origin, direction, reach, MOVER_CATEGORY)
                .unwrap()
                .is_none()
        );
    }
}

#[test]
fn shared_deadeye_aim_ray_visibility_contract() {
    use crate::collision_cache::{CollisionCache, Mesh, Triangle};
    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/deadeye-aim-ray-visibility-v1.json"
    ))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["case_id"], "deadeye.aim-ray-visibility.v1");
    assert_eq!(contract["target_build"], 104319);
    let origin: Vec3 = serde_json::from_value(contract["input"]["origin"].clone()).unwrap();
    let raw = &contract["input"]["target"];
    let pose = TargetPose {
        feet: serde_json::from_value(raw["feet"].clone()).unwrap(),
        height: raw["height"].as_f64().unwrap(),
        radius: raw["radius"].as_f64().unwrap(),
        eye: raw["eye"].as_f64().unwrap(),
    };
    for case in contract["input"]["cases"].as_array().unwrap() {
        let triangles: Vec<[[f32; 3]; 3]> =
            serde_json::from_value(case["triangles"].clone()).unwrap();
        let cache=CollisionCache {
            header:serde_json::from_value(serde_json::json!({"meshes":[[triangles.len()*3,triangles.len()]],"hulls":0,"models":0,"placements":0,"identifiers":[]})).unwrap(),
            meshes:vec![Mesh {vertices:triangles.iter().flatten().copied().collect(),
                triangles:(0..triangles.len()).map(|index|Triangle {corners:[index as i32*3,index as i32*3+1,index as i32*3+2],wings:[-1;3],mask:MOVER_CATEGORY,surface:0}).collect()}],
            hulls:vec![],models:vec![],placements:vec![],
        };
        let selection = DeadeyeScene {
            health: None,
            collision: Some(Arc::new(
                CollisionScene::movement_probe(&cache, &BTreeSet::new()).unwrap(),
            )),
            targets: vec![DeadeyeTarget {
                entity: 0xc000_1000,
                pose,
                health: 1000.0,
                velocity: None,
                script_state: Default::default(),
            }],
            view_subjects: BTreeMap::new(),
        };
        let expected = case["expected"]["visible"].as_bool().unwrap();
        assert_eq!(
            selection.aim_point_visible(origin, pose),
            expected,
            "{}",
            case["name"]
        );
        assert_eq!(
            selection.shot_direction(origin, 0xc000_1000).is_some(),
            expected,
            "{}",
            case["name"]
        );
        assert_eq!(
            selection.select(origin, [0.0, 0.0, 1.0], 200.0, 180.0, true),
            if expected { vec![0xc000_1000] } else { vec![] },
            "{}",
            case["name"]
        );
    }
}
use crate::{
    collision_index::TriangleIndex, contact_world::ContactWorld, game_instance::TOURNAMENT_QUANTA,
    mover_settings::MoverSettings,
};

#[test]
fn legacy_engine_table_is_available_only_in_encounter_modes() {
    use crate::script_expr::EngineSetting;
    let (factory, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: Some(0),
    };
    assert_eq!(
        runtime.world.engine_setting(site, 0xe1c1_e402),
        EngineSetting::Unavailable
    );
    for mode in [
        crate::game_content::JUNKENSTEIN_REVENGE,
        crate::game_content::JUNKENSTEIN_ENDLESS,
    ] {
        runtime.world.mode = mode;
        assert_eq!(
            runtime.world.engine_setting(site, 0xe1c1_e402),
            EngineSetting::Present(Value::Integer(128))
        );
        assert_eq!(
            runtime.world.engine_setting(site, 0),
            EngineSetting::Missing
        );
    }
    runtime.world.mode = TUTORIAL_MODE;
    assert_eq!(
        runtime.world.engine_setting(site, 0xe1c1_e402),
        EngineSetting::Unavailable
    );
}

#[test]
fn owner_engine_settings_follow_the_authored_mode_table() {
    use crate::script_expr::EngineSetting;

    let (factory, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-mode-rulesets-v1.json"
    ))
    .unwrap();
    runtime.world.graphs = Arc::new(
        GraphStore::from_json(&serde_json::to_vec(&contract["input"]["bundle"]).unwrap()).unwrap(),
    );
    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: Some(0),
    };

    runtime.world.mode = 0xa1;
    for hero in [SOLDIER, CASSIDY] {
        runtime.world.hero = hero;
        assert_eq!(
            runtime.world.engine_setting(site, 101),
            EngineSetting::Present(Value::Bool(false))
        );
        assert_eq!(
            runtime.world.engine_setting(site, 104),
            EngineSetting::Present(Value::Null)
        );
    }
    assert_eq!(
        runtime.world.engine_setting(site, 105),
        EngineSetting::Missing
    );

    for (mode, expected) in [
        (0xa2, EngineSetting::Unavailable),
        (0xa3, EngineSetting::Missing),
        (0xa4, EngineSetting::Present(Value::Bool(true))),
    ] {
        runtime.world.mode = mode;
        assert_eq!(runtime.world.engine_setting(site, 101), expected);
    }
}

#[test]
fn owner_reports_absent_host_contexts_only_for_valid_own_body() {
    let (factory, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let world = &mut runtime.world;
    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: Some(7),
    };
    for mode in [
        PRACTICE_RANGE_MODE,
        crate::game_content::JUNKENSTEIN_REVENGE,
    ] {
        world.mode = mode;
        for (entity, expected) in [
            (spawn.entity, Some(false)),
            (spawn.controller, None),
            (spawn.entity + 1, None),
            (0, None),
        ] {
            let site = Site { entity, ..site };
            assert_eq!(world.local_combat_context_available(site), expected);
            assert_eq!(world.spatial_binding_owner_available(site), expected);
            assert_eq!(world.outline_context_available(site), expected);
            assert_eq!(world.voice_context(site), expected);
        }
    }

    world.entity = 0;
    let invalid = Site { entity: 0, ..site };
    assert_eq!(world.local_combat_context_available(invalid), None);
    assert_eq!(world.spatial_binding_owner_available(invalid), None);
    assert_eq!(world.outline_context_available(invalid), None);
    assert_eq!(world.voice_context(invalid), None);
}

#[test]
fn cassidy_junkenstein_rules_enable_fan_without_changing_other_hero_defaults() {
    let (factory, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let world = &mut runtime.world;
    world.hero = CASSIDY;
    world.mode = crate::game_content::JUNKENSTEIN_REVENGE;
    assert_eq!(world.ruleset(0x2819), Some(Value::Float(1.0)));
    assert_eq!(world.ruleset(0x290c), Some(Value::Bool(true)));
    assert_eq!(world.ruleset(0x290b), None);
    world.hero = SOLDIER;
    assert_eq!(world.ruleset(0x290c), None);
    world.hero = CASSIDY;
    world.mode = PRACTICE_RANGE_MODE;
    assert_eq!(world.ruleset(0x290c), None);
}

#[test]
fn declared_compatibility_charge_uses_its_mode_root_and_room_activity() {
    use crate::{
        game_combat::AppliedDamage, game_hitscan::DamageTags,
        script_entity_state::EntityScriptState, script_init::HealthTargetFacts, vitals::Death,
    };

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-charge-compatibility-v1.json"
    ))
    .unwrap();
    let input = &contract["input"];
    let codecs = Arc::new(
        CodecTable::from_json(
            r#"{"classes":{"ABCDEF01":{"name":"STUStatescriptStateSubScript",
                "codec":"subscript","stec":true,"status":"VERIFIED","layout":[
                    {"op":"if","c":"H","then":[{"op":"w_u16","f":"child"}],"else":[]}
                ]}}}"#,
        )
        .unwrap(),
    );
    let hero = Hero {
        guid: 0x02e0_0000_0000_af12,
        name: "Fixture".into(),
        role: "Damage".into(),
        body: 0x0400_0000_0000_af12,
    };
    let (_, spawn) = fixture_body_factory();
    let start = |bundle: &Json, mode, room_activity| {
        let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(bundle).unwrap()).unwrap());
        let setup = OwnerBodySetup {
            room_activity: Some(room_activity),
            ..OwnerBodySetup::default()
        };
        PlayerBodyFactory::prepare(
            Arc::clone(&graphs),
            Arc::clone(&codecs),
            mode,
            &hero,
            None,
            false,
            (spawn, setup.clone()),
        )
        .unwrap()
        .start_runtime(spawn, setup)
        .unwrap()
    };
    let current = |runtime: &PlayerBodyRuntime, variable| {
        runtime
            .snapshot()
            .unwrap()
            .variables
            .get(&variable)
            .cloned()
    };
    let marker = |runtime: &PlayerBodyRuntime| current(runtime, 714);
    let admission = |runtime: &PlayerBodyRuntime| current(runtime, 713);
    let phase = |runtime: &mut PlayerBodyRuntime, activity: OwnerRoomActivity| {
        runtime.set_room_activity(activity);
        runtime
            .set_encounter_active(activity.encounter_active())
            .unwrap();
    };
    let damage = AppliedDamage {
        source: spawn.entity,
        amount: input["damage"].as_f64().unwrap(),
        named_pool_losses: Vec::new(),
        tags: DamageTags::EMPTY,
        target_facts: HealthTargetFacts {
            target: spawn.entity + 1,
            room_status: None,
            filter_flags: None,
            tags: Default::default(),
            script_state: EntityScriptState::Unavailable,
        },
    };
    let opened = contract["expected"]["open_marker"]
        .as_f64()
        .map(Value::Float);
    let closed = contract["expected"]["closed_marker"]
        .as_f64()
        .map(Value::Float);
    let inactive = OwnerRoomActivity::Encounter { active: false };
    let configured = 0x0230_0000_0000_af10;
    let mut runtime = start(&input["bundle"], configured, inactive);
    assert_eq!(admission(&runtime), Some(Value::Bool(false)));
    runtime.receive_applied_damage(damage.clone()).unwrap();
    assert_eq!(marker(&runtime), closed);

    phase(&mut runtime, OwnerRoomActivity::Regular);
    assert_eq!(admission(&runtime), Some(Value::Bool(true)));
    runtime.receive_applied_damage(damage.clone()).unwrap();
    assert_eq!(marker(&runtime), opened);

    phase(&mut runtime, inactive);
    assert_eq!(admission(&runtime), Some(Value::Bool(false)));
    let mut larger = damage.clone();
    larger.amount = 5.5;
    runtime.receive_applied_damage(larger.clone()).unwrap();
    assert_eq!(marker(&runtime), opened);

    phase(&mut runtime, OwnerRoomActivity::Encounter { active: true });
    runtime.receive_applied_damage(larger).unwrap();
    assert_eq!(marker(&runtime), Some(Value::Float(5.5)));
    assert!(runtime.snapshot().unwrap().diagnostics.is_empty());
    assert!(
        runtime
            .receive_damage(
                10_000.0,
                Death {
                    time: 0.0,
                    frame: spawn.frame,
                    killer: damage.target_facts.target,
                },
            )
            .killed
    );
    assert_eq!(admission(&runtime), Some(Value::Bool(false)));

    let mut other_mode = start(
        &input["bundle"],
        0x0230_0000_0000_af12,
        OwnerRoomActivity::Regular,
    );
    assert!(admission(&other_mode).is_none());
    other_mode.receive_applied_damage(damage.clone()).unwrap();
    assert_eq!(marker(&other_mode), closed);

    let mount = &input["nested_mount"];
    let mut nested = input["bundle"].clone();
    nested["graphs"]
        .as_array_mut()
        .unwrap()
        .push(mount["graph"].clone());
    let body = &mut nested["bodies"]["0x040000000000AF12"];
    body["graphs"][0]["graph"] = mount["graph"]["index"].clone();
    body["charge_compatibility"]["parent"] = mount["parent"].clone();
    let mut runtime = start(&nested, configured, OwnerRoomActivity::Regular);
    let child_binding = |runtime: &PlayerBodyRuntime| {
        runtime
            .snapshot()
            .unwrap()
            .instances
            .iter()
            .find_map(|(&id, instance)| (instance.parent == Some((1, 0))).then_some(id))
    };
    let original_child = child_binding(&runtime).unwrap();
    runtime.receive_applied_damage(damage.clone()).unwrap();
    assert_eq!(marker(&runtime), opened);
    runtime
        .component
        .debug_end_state(1, 0, &mut runtime.world)
        .unwrap();
    assert!(child_binding(&runtime).is_none());
    let mut after_remount = damage;
    after_remount.amount = 7.5;
    runtime
        .receive_applied_damage(after_remount.clone())
        .unwrap();
    assert_eq!(marker(&runtime), opened);

    runtime
        .component
        .begin_state(1, 0, &mut runtime.world)
        .unwrap();
    assert_ne!(child_binding(&runtime).unwrap(), original_child);
    runtime.receive_applied_damage(after_remount).unwrap();
    assert_eq!(marker(&runtime), Some(Value::Float(7.5)));
    assert!(runtime.snapshot().unwrap().diagnostics.is_empty());
}

#[test]
fn charge_admission_preserves_profile_scope_and_requires_a_live_instance_for_credit() {
    use crate::{
        game_combat::AppliedDamage,
        game_content::{JUNKENSTEIN_ENDLESS, JUNKENSTEIN_REVENGE},
        game_hitscan::DamageTags,
        script_entity_state::EntityScriptState,
        vitals::Death,
    };

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-owner-room-activity-v1.json"
    ))
    .unwrap();
    let projected = |key: &str| {
        Value::Bool(
            contract["expected"]["legacy_charge_admission"][key]
                .as_bool()
                .unwrap(),
        )
    };
    let (mut factory, spawn) = fixture_body_factory();
    let damage = AppliedDamage {
        source: spawn.entity,
        amount: 1.0,
        named_pool_losses: Vec::new(),
        tags: DamageTags::EMPTY,
        target_facts: crate::script_init::HealthTargetFacts {
            target: spawn.entity + 1,
            room_status: None,
            filter_flags: None,
            tags: Default::default(),
            script_state: EntityScriptState::Unavailable,
        },
    };
    let admission =
        |runtime: &PlayerBodyRuntime| runtime.snapshot().unwrap().variables.get(&2426).cloned();
    let require_live_graph = |runtime: &mut PlayerBodyRuntime| {
        assert!(
            runtime
                .receive_applied_damage(damage.clone())
                .unwrap_err()
                .to_string()
                .contains("authored common charge graph")
        );
    };
    for (mode, hero, admitted) in [
        (JUNKENSTEIN_REVENGE, CASSIDY, true),
        (JUNKENSTEIN_ENDLESS, CASSIDY, true),
        (JUNKENSTEIN_REVENGE, SOLDIER, false),
        (PRACTICE_RANGE_MODE, CASSIDY, false),
    ] {
        factory.mode = mode;
        factory.hero = hero;
        let mut runtime = factory.start(spawn).unwrap();
        assert_eq!(runtime.world.room_activity, None);
        assert_eq!(
            admission(&runtime),
            admitted.then(|| projected("bootstrap"))
        );
        if admitted {
            require_live_graph(&mut runtime);
            let mut inactive = factory
                .start_with_setup(
                    spawn,
                    OwnerBodySetup {
                        room_activity: Some(OwnerRoomActivity::Encounter { active: false }),
                        ..OwnerBodySetup::default()
                    },
                )
                .unwrap();
            assert_eq!(admission(&inactive), Some(projected("bootstrap")));
            inactive.receive_applied_damage(damage.clone()).unwrap();
        } else {
            runtime.receive_applied_damage(damage.clone()).unwrap();
        }

        runtime.set_encounter_active(true).unwrap();
        assert_eq!(
            runtime.world.room_activity,
            admitted.then_some(OwnerRoomActivity::Encounter { active: true })
        );
        runtime.set_room_activity(OwnerRoomActivity::Encounter { active: false });
        assert_eq!(
            admission(&runtime),
            admitted.then(|| projected("bootstrap"))
        );
        runtime.receive_applied_damage(damage.clone()).unwrap();
        runtime.set_encounter_active(false).unwrap();
        assert_eq!(
            admission(&runtime),
            admitted.then(|| projected("inactive_room"))
        );

        runtime.set_room_activity(OwnerRoomActivity::Regular);
        runtime.set_encounter_active(false).unwrap();
        assert_eq!(
            runtime.world.room_activity,
            Some(OwnerRoomActivity::Regular)
        );
        runtime.receive_applied_damage(damage.clone()).unwrap();

        if admitted {
            runtime.set_encounter_active(true).unwrap();
            require_live_graph(&mut runtime);
            let outcome = runtime.receive_damage(
                contract["input"]["lethal_damage"].as_f64().unwrap(),
                Death {
                    time: 0.0,
                    frame: spawn.frame,
                    killer: damage.target_facts.target,
                },
            );
            assert!(outcome.killed);
            assert_eq!(admission(&runtime), Some(projected("after_death")));
            assert_eq!(
                runtime.world.room_activity,
                Some(OwnerRoomActivity::Encounter { active: true })
            );
            runtime.receive_applied_damage(damage.clone()).unwrap();
        }
    }
}

#[test]
fn declared_charge_root_uses_settled_health_events_without_legacy_credit() {
    use crate::{
        game_combat::AppliedDamage,
        game_content::JUNKENSTEIN_REVENGE,
        game_hitscan::DamageTags,
        script_entity_state::EntityScriptState,
        script_init::{HealthTargetFacts, HostEvent, HostHealthContext},
    };
    use serde_json::json;

    const BODY: &str = "0x040000000000B013";
    const METER: u32 = 612;
    const DELTA_SLOT: u32 = 607;
    const HIT_TAG_SLOT: u32 = 608;
    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-authored-charge-route-v1.json"
    ))
    .unwrap();
    let mut bundle = contract["input"]["bundle"].clone();
    let hits: Vec<u64> = serde_json::from_value(contract["input"]["hit_tags"].clone()).unwrap();
    let mut hit_tags = DamageTags::EMPTY;
    for &tag in &hits {
        assert!(hit_tags.push(tag));
    }
    let probe = &mut bundle["graphs"][0];
    probe["nodes"][0]["fields"]["m_params"] = json!([
        {
            "m_B5051BCE": "0x0000000000000003",
            "m_out_Var": {"$":"STUConfigVarDynamic","m_identifier":DELTA_SLOT,"m_60DB8F99":false}
        },
        {
            "m_B5051BCE": "0x0000000000000005",
            "m_out_Var": {"$":"STUConfigVarDynamic","m_identifier":HIT_TAG_SLOT,"m_60DB8F99":false}
        }
    ]);
    bundle["graphs"].as_array_mut().unwrap().push(json!({
        "index": 0x0ca5, "items": 0, "nodes": [], "states": [], "entries": [], "remote": [],
        "sync_vars": [], "containers": {}, "unknown_nodes": [], "fields": {},
        "header": {
            "m_nodesBitCount":4, "m_statesBitCount":4, "m_remoteSyncNodesBitCount":4, "m_syncVarsBitCount":4
        }
    }));
    bundle["bodies"][BODY]["hero"] = json!(format!("0x{CASSIDY:016X}"));
    let hero = Hero {
        guid: CASSIDY,
        name: "Configured charge fixture".into(),
        role: "Damage".into(),
        body: parse_guid(BODY).unwrap(),
    };
    let (_, spawn) = fixture_body_factory();
    let setup = OwnerBodySetup {
        room_activity: Some(OwnerRoomActivity::Encounter { active: true }),
        ..OwnerBodySetup::default()
    };
    let build = |bundle: &Json| {
        let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(bundle).unwrap()).unwrap());
        let codecs = Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap());
        PlayerBodyFactory::prepare(
            graphs,
            codecs,
            JUNKENSTEIN_REVENGE,
            &hero,
            None,
            false,
            (spawn, setup.clone()),
        )
        .unwrap()
        .start_runtime(spawn, setup.clone())
        .unwrap()
    };
    let mut authored = build(&bundle);

    let mut conflicting = bundle.clone();
    conflicting["graphs"].as_array_mut().unwrap().pop();
    conflicting["graphs"][0]["index"] = json!(0x0ca5);
    conflicting["bodies"][BODY]["server_graphs"][0]["graph"] = json!(0x0ca5);
    conflicting["bodies"][BODY]["authored_charge_graph"] = json!("0x0580000000000CA5");
    let graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&conflicting).unwrap()).unwrap());
    let codecs = Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap());
    assert!(matches!(
        PlayerBodyFactory::prepare(
            graphs,
            codecs,
            JUNKENSTEIN_REVENGE,
            &hero,
            None,
            false,
            (spawn, setup.clone()),
        ),
        Err(OwnerBodyPreparationError::AuthoredChargeRoot(0x0ca5))
    ));

    bundle["bodies"][BODY]
        .as_object_mut()
        .unwrap()
        .remove("authored_charge_graph");
    let mut compatibility = build(&bundle);
    let damage = AppliedDamage {
        source: spawn.entity,
        amount: 3.0,
        named_pool_losses: Vec::new(),
        tags: DamageTags::EMPTY,
        target_facts: crate::script_init::HealthTargetFacts {
            target: spawn.entity + 1,
            room_status: None,
            filter_flags: None,
            tags: Default::default(),
            script_state: EntityScriptState::Unavailable,
        },
    };
    assert!(!authored.snapshot().unwrap().variables.contains_key(&2426));
    assert_eq!(
        compatibility.snapshot().unwrap().variables[&2426],
        Value::Bool(true)
    );
    authored.receive_applied_damage(damage.clone()).unwrap();
    assert!(
        compatibility
            .receive_applied_damage(damage)
            .unwrap_err()
            .to_string()
            .contains("authored common charge graph")
    );
    assert_eq!(
        authored
            .receive_host_event(
                HostEvent::HealthApplied {
                    source: Some(spawn.entity),
                    target: spawn.entity + 1,
                    delta: -3.0,
                    killed: false,
                    hit_tags: Some(hit_tags),
                },
                HostHealthContext {
                    named_pool_losses: None,
                    critical_hit: None,
                    damage_frame: None,
                    source_body: None,
                    target_body: None,
                    target_facts: Some(Arc::new(HealthTargetFacts {
                        target: spawn.entity + 1,
                        room_status: None,
                        filter_flags: None,
                        tags: Default::default(),
                        script_state: EntityScriptState::Unavailable,
                    })),
                },
            )
            .unwrap(),
        1
    );
    authored
        .component
        .advance_frame(spawn.frame + 1, &mut authored.world)
        .unwrap();
    let snapshot = authored.snapshot().unwrap();
    assert!(
        snapshot.diagnostics.is_empty(),
        "{:?}",
        snapshot.diagnostics
    );
    let meter = snapshot
        .instances
        .values()
        .find(|instance| instance.graph == METER)
        .unwrap();
    assert!(meter.is_server_only());
    assert_eq!(meter.variables[&DELTA_SLOT], Value::Float(-3.0));
    assert_eq!(
        meter.variables[&HIT_TAG_SLOT],
        Value::Array(hits.into_iter().map(Value::Asset).collect())
    );
    let encoder = authored.encoder();
    for view in [View::Owner, View::Observer] {
        let projected = encoder.project(snapshot, view).unwrap();
        assert_eq!(projected.instance_count(), 1);
        encoder.full(&projected).unwrap();
    }
}

#[test]
fn player_bound_body_angles_follow_the_live_pose_in_each_mode() {
    use crate::script_expr::{AngleQuery, BodyAngle};
    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-bound-body-angle-v1.json"
    ))
    .unwrap();
    let poses = contract["input"]["poses"].as_array().unwrap();
    let expected = contract["expected"].as_array().unwrap();
    assert_eq!(poses.len(), expected.len());
    let (factory, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let world = &mut runtime.world;
    for mode in [
        crate::game_content::JUNKENSTEIN_REVENGE,
        PRACTICE_RANGE_MODE,
        TUTORIAL_MODE,
    ] {
        world.mode = mode;
        assert_eq!(world.body_angle(0, BodyAngle::Yaw), AngleQuery::Unbound);
        for (pose, angles) in poses.iter().zip(expected) {
            world.movement.yaw = i32::try_from(pose["yaw"].as_i64().unwrap()).unwrap();
            world.movement.pitch = i32::try_from(pose["pitch"].as_i64().unwrap()).unwrap();
            for (axis, key) in [(BodyAngle::Yaw, "yaw"), (BodyAngle::Pitch, "pitch")] {
                assert_eq!(
                    world.body_angle(spawn.entity, axis),
                    angles[key]
                        .as_f64()
                        .map_or(AngleQuery::Unavailable, AngleQuery::Present)
                );
                assert_eq!(
                    world.body_angle(spawn.controller, axis),
                    AngleQuery::Unavailable
                );
                assert_eq!(
                    world.body_angle(spawn.entity + 1, axis),
                    AngleQuery::Unavailable
                );
            }
        }
    }
}

#[test]
fn local_velocity_bridge_preserves_sampled_delta_and_rejects_foreign_owners() {
    use crate::mover::VelocityChange;
    let (factory, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    runtime.world.movement.velocity = [3.0, 2.0, 4.0];
    let site = Site {
        entity: spawn.entity,
        instance: 6,
        graph: 991,
        node: Some(1),
    };
    assert!(
        runtime
            .world
            .edit_velocity(site, 10, Value::Vector([0.0, 2.0, 15.0]), false)
    );
    assert_eq!(
        runtime.take_velocity_changes(),
        vec![(11, VelocityChange::Delta([-3.0, 0.0, 11.0]))]
    );
    assert!(runtime.take_velocity_changes().is_empty());
    assert!(!runtime.world.edit_velocity(
        Site {
            entity: spawn.entity + 1,
            ..site
        },
        10,
        Value::Vector([0.0; 3]),
        false
    ));
    assert!(
        !runtime
            .world
            .edit_velocity(site, 10, Value::Vector([f64::INFINITY; 3]), false)
    );
    assert!(
        !runtime
            .world
            .edit_velocity(site, 10, Value::Vector([5.5; 3]), true)
    );
    let cap = Site {
        node: Some(4),
        ..site
    };
    assert!(
        runtime
            .world
            .edit_velocity(cap, 11, Value::Float(5.5), true)
    );
    assert_eq!(
        runtime.take_velocity_changes(),
        vec![(12, VelocityChange::HorizontalCap(5.5))]
    );
}

#[test]
fn shared_horizontal_cap_queues_on_a_live_player_without_a_roll_profile() {
    use crate::mover::VelocityChange;

    let spawn = fixture_body_factory().1;
    let mut body = fixture_hitscan_factory(20.0).start(spawn).unwrap();
    let site = Site {
        entity: spawn.entity,
        instance: 2,
        graph: 61061,
        node: Some(8),
    };
    let foreign_site = Site {
        entity: spawn.entity + 1,
        ..site
    };
    assert!(!body.world.cap_horizontal_velocity(foreign_site, 12, 4.0));
    assert!(!body.world.cap_horizontal_velocity(site, 12, f64::INFINITY));
    assert!(!body.world.cap_horizontal_velocity(site, 12, -1.0));
    assert!(body.world.cap_horizontal_velocity(site, 12, 4.0));
    assert_eq!(
        body.take_velocity_changes(),
        [(13, VelocityChange::HorizontalCap(4.0))]
    );
    assert!(body.take_velocity_changes().is_empty());
    body.world.vitals.damage(
        10_000.0,
        crate::vitals::Death {
            time: 1.0,
            frame: 13,
            killer: foreign_site.entity,
        },
    );
    assert!(!body.world.cap_horizontal_velocity(site, 13, 4.0));
}

#[test]
fn authored_impulse_queues_only_for_its_live_body() {
    use crate::mover::VelocityChange;

    let spawn = fixture_body_factory().1;
    let mut body = fixture_hitscan_factory(20.0).start(spawn).unwrap();
    let site = Site {
        entity: spawn.entity,
        instance: 2,
        graph: 61061,
        node: Some(9),
    };
    let foreign = Site {
        entity: spawn.entity + 1,
        ..site
    };
    let delta = [2.0, 7.0, -0.5];
    assert!(!body.world.apply_velocity_impulse(foreign, 12, 1.0, delta));
    assert!(!body.world.apply_velocity_impulse(site, 12, f64::MAX, delta));
    assert!(body.world.apply_velocity_impulse(site, 12, 1.0, delta));
    assert_eq!(
        body.take_velocity_changes(),
        [(
            13,
            VelocityChange::VerticalImpulse {
                vertical: 1.0,
                delta,
            },
        )]
    );
    assert!(body.take_velocity_changes().is_empty());
}

#[test]
fn swept_displacement_requests_belong_to_the_live_player_body() {
    use crate::script_init::{Relocation, ScriptedMotion, SweptDisplacement};

    let (factory, spawn) = fixture_body_factory();
    let mut body = factory.start(spawn).unwrap();
    let displacement = SweptDisplacement::new([2.25, 0.0, 0.0]).unwrap();
    let alternate = ScriptedMotion::Relocate(Relocation::new([0.0, 0.0, 1.0]).unwrap());
    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 1_783,
        node: Some(4),
    };
    let foreign = Site {
        entity: spawn.controller,
        ..site
    };
    assert_eq!(
        body.world
            .queue_swept_displacement(foreign, 31, displacement),
        None
    );
    assert_eq!(
        body.world.queue_swept_displacement(site, 31, displacement),
        Some(true)
    );
    assert_eq!(
        body.world.queue_scripted_motion(site, 32, alternate),
        Some(false)
    );
    assert_eq!(
        body.take_scripted_motion(),
        Some((31, ScriptedMotion::Sweep(displacement)))
    );
    assert_eq!(body.take_scripted_motion(), None);
    assert_eq!(
        body.world.queue_scripted_motion(site, 32, alternate),
        Some(true)
    );
    assert!(
        body.receive_damage(
            10_000.0,
            crate::vitals::Death {
                time: 0.5,
                frame: 31,
                killer: spawn.controller,
            }
        )
        .killed
    );
    assert_eq!(body.take_scripted_motion(), None);
    assert_eq!(
        body.world.queue_scripted_motion(site, 33, alternate),
        Some(false)
    );
}

#[test]
fn player_natives_follow_the_verified_owner_and_override_shapes() {
    use crate::script_expr::EvalError;
    use serde_json::json;

    let spawn = fixture_body_factory().1;
    let mut runtime = fixture_hitscan_factory(20.0).start(spawn).unwrap();
    let evaluate = |body: &mut PlayerBodyRuntime, config: &Json| {
        body.component
            .evaluate_state_config(1, 0, config, &mut body.world)
    };
    let owner = json!({"$":"STU_B5A0CAF0","m_EE729DCB":0});
    let size = json!({"$":"STU_FDBB0B67","m_entity":owner});
    assert_eq!(evaluate(&mut runtime, &size), Ok(Value::Float(1.0)));
    for (value, supported) in [
        (Value::Integer(1), true),
        (Value::Float(1.0), true),
        (Value::Bool(true), false),
        (Value::Float(1.25), false),
        (Value::Null, true),
    ] {
        runtime
            .component
            .set_entity_variable(31_210, value, &mut runtime.world)
            .unwrap();
        assert_eq!(
            evaluate(&mut runtime, &size),
            if supported {
                Ok(Value::Float(1.0))
            } else {
                Err(EvalError::UnsupportedConfig("STU_FDBB0B67".into()))
            }
        );
    }
    let dynamic = json!({"$":"STUConfigVarDynamic","m_identifier":400,"m_60DB8F99":true});
    let dynamic_size = json!({"$":"STU_FDBB0B67","m_entity":dynamic});
    for (entity, supported) in [(spawn.entity, true), (spawn.entity + 1, false), (0, false)] {
        runtime
            .component
            .set_entity_variable(400, Value::Entity(entity), &mut runtime.world)
            .unwrap();
        assert_eq!(
            evaluate(&mut runtime, &dynamic_size).ok(),
            supported.then_some(Value::Float(1.0))
        );
    }

    let ai = json!({"$":"STU_DB742151","m_entity":owner,"m_EE729DCB":0});
    assert_eq!(evaluate(&mut runtime, &ai), Ok(Value::Entity(0)));
    let path = json!({"$":"STU_D3EB8D87","m_00903303":ai,"m_EE729DCB":0});
    assert_eq!(evaluate(&mut runtime, &path), Ok(Value::Bool(false)));
    let other_path =
        json!({"$":"STU_D3EB8D87","m_00903303":{"$":"STU_DB742151","m_entity":dynamic}});
    assert_eq!(
        evaluate(&mut runtime, &other_path),
        Err(EvalError::UnsupportedConfig("STU_DB742151".into()))
    );
    let branch = json!({"$":"STU_A77AC2AC","m_EE729DCB":0});
    assert_eq!(evaluate(&mut runtime, &branch), Ok(Value::Bool(false)));
    runtime.world.mode = TUTORIAL_MODE;
    assert_eq!(evaluate(&mut runtime, &branch), Ok(Value::Bool(true)));
    runtime.world.mode = PRACTICE_RANGE_MODE;
    let flag = |value| json!({"$":"STUConfigVarBool","m_EE729DCB":0,"m_value":value});
    let selector = json!({
        "$":"STU_ED45C5E8","m_EE729DCB":0,"m_range":{"$":"STUConfigVarFloat","m_value":0},
        "m_first":flag(0),"m_second":flag(0),"m_subject":owner
    });
    assert_eq!(evaluate(&mut runtime, &selector), Ok(Value::Null));
    let unsupported_selector = Err(EvalError::UnsupportedConfig("STU_ED45C5E8".into()));
    for (field, value) in [
        ("m_second", flag(1)),
        ("m_range", json!({"$":"STUConfigVarFloat","m_value":2.5})),
        ("m_subject", dynamic.clone()),
        ("m_other", owner.clone()),
        ("m_filter", Json::Null),
    ] {
        let mut variant = selector.clone();
        variant[field] = value;
        assert_eq!(
            evaluate(&mut runtime, &variant),
            unsupported_selector,
            "{field}"
        );
    }
    runtime.world.mode = 0;
    assert_eq!(
        evaluate(&mut runtime, &branch),
        Err(EvalError::UnsupportedConfig("STU_A77AC2AC".into()))
    );
    assert_eq!(evaluate(&mut runtime, &selector), unsupported_selector);
}

#[test]
fn target_tag_effects_restore_authored_tags_and_remaining_grants() {
    let (mut factory, spawn) = fixture_body_factory();
    let authored = 0x0d60_0000_0000_0042;
    let tag = 0x0d60_0000_0000_0422;
    factory.target_tags = Some(BTreeSet::from([authored]));
    let world = &mut factory.start(spawn).unwrap().world;
    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: Some(1),
    };
    let read = |world: &BodyWorld, tag| world.has_target_tag(site, spawn.entity, tag);
    let grant = TargetTagEffect {
        entity: spawn.entity,
        tag,
        present: true,
    };
    let suppress = TargetTagEffect {
        present: false,
        ..grant
    };
    let mask_authored = TargetTagEffect {
        tag: authored,
        ..suppress
    };

    assert_eq!(read(world, authored), Some(true));
    assert!(world.set_target_tag_effect(site, mask_authored, true));
    assert_eq!(read(world, authored), Some(false));
    assert!(world.set_target_tag_effect(site, mask_authored, false));
    assert_eq!(read(world, authored), Some(true));

    assert_eq!(read(world, tag), Some(false));
    for _ in 0..2 {
        assert!(world.set_target_tag_effect(site, grant, true));
    }
    assert_eq!(read(world, tag), Some(true));
    for _ in 0..2 {
        assert!(world.set_target_tag_effect(site, suppress, true));
    }
    assert_eq!(read(world, tag), Some(false));
    assert!(!world.set_target_tag_effect(site, grant, true));
    assert!(world.set_target_tag_effect(site, grant, false));
    assert!(world.set_target_tag_effect(site, suppress, false));
    assert_eq!(read(world, tag), Some(false));
    assert!(world.set_target_tag_effect(site, suppress, false));
    assert_eq!(read(world, tag), Some(true));
    assert!(world.set_target_tag_effect(site, grant, false));
    assert_eq!(read(world, tag), Some(false));
    assert!(!world.set_target_tag_effect(site, grant, false));
}

#[test]
fn foreign_target_tags_read_the_frozen_body_generation() {
    use crate::{
        collision_cache::CollisionCache,
        contact_world::ContactWorld,
        game_combat::{PlayerBodyState, RoomSimulation},
        game_replication::BodyId,
        mover_settings::MoverSettings,
        script_graph::RoomSpatialNeeds,
    };

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/actor-direct-target-tags-v1.json"
    ))
    .unwrap();
    let tags = contract["input"]["tags"].as_object().unwrap();
    let tag = |name: &str| parse_guid(tags[name].as_str().unwrap()).unwrap();

    let (factory, spawn) = fixture_body_factory();
    let other = OwnerBodySpawn {
        controller: spawn.controller + 0x100,
        entity: spawn.entity + 0x100,
        slot: spawn.slot + 1,
        ..spawn
    };
    let observer_site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: Some(1),
    };
    let target_site = Site {
        entity: other.entity,
        ..observer_site
    };
    let effect = |tag, present| TargetTagEffect {
        entity: other.entity,
        tag,
        present,
    };

    let mut observer = factory.start(spawn).unwrap();
    let mut target = factory.start(other).unwrap();
    target.world.target_tags = Some(BTreeSet::from([tag("base"), tag("suppressed")]));
    assert!(
        target
            .world
            .set_target_tag_effect(target_site, effect(tag("grant"), true), true)
    );
    let suppression = effect(tag("suppressed"), false);
    assert!(
        target
            .world
            .set_target_tag_effect(target_site, suppression, true)
    );

    let cache = CollisionCache {
        header: serde_json::from_value(serde_json::json!({
            "meshes": [], "hulls": 0, "models": 0, "placements": 0, "identifiers": []
        }))
        .unwrap(),
        meshes: vec![],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    };
    let ground = Arc::new(CollisionScene::movement_probe(&cache, &BTreeSet::new()).unwrap());
    let contacts = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.enable_spatial_queries(false, ground, RoomSpatialNeeds::EntityTags);
    let body = |generation, runtime: PlayerBodyRuntime| {
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::clone(&contacts),
            other.pose.position,
            0,
            MOVER_CATEGORY,
        )
        .unwrap();
        PlayerBodyState::new(
            BodyId {
                entity: other.entity,
                generation,
            },
            other.controller,
            other.slot,
            1,
            factory.hero,
            mover,
            Some(Box::new(runtime)),
            other.frame,
            None,
        )
    };
    let assert_tags = |observer: &PlayerBodyRuntime, generation: &str| {
        for name in tags.keys() {
            assert_eq!(
                observer
                    .world
                    .has_target_tag(observer_site, other.entity, tag(name)),
                contract["expected"][generation][name.as_str()].as_bool(),
                "{generation}: {name}"
            );
        }
    };

    room.spawn_player_body(body(1, target)).unwrap();
    let first = room.capture_spatial_frame(other.frame).unwrap().unwrap();
    observer.set_spatial_frame(Arc::clone(&first));
    assert_tags(&observer, "first_generation");
    assert_eq!(
        observer
            .world
            .has_target_tag(observer_site, other.entity + 1, tag("base")),
        None
    );

    let mut replacement = factory.start(other).unwrap();
    replacement.world.target_tags = None;
    assert!(
        replacement
            .world
            .set_target_tag_effect(target_site, suppression, true)
    );
    room.spawn_player_body(body(2, replacement)).unwrap();
    let next = room
        .capture_spatial_frame(other.frame + 1)
        .unwrap()
        .unwrap();
    assert_ne!(
        first.actor_identity(other.entity),
        next.actor_identity(other.entity)
    );
    assert_tags(&observer, "first_generation");
    observer.set_spatial_frame(next);
    assert_tags(&observer, "next_generation");
}

#[test]
fn target_tag_effects_keep_unknown_tags_and_replacement_independent() {
    let (factory, spawn) = fixture_body_factory();
    let tag = 0x0d60_0000_0000_0422;
    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: Some(1),
    };
    let read = |world: &BodyWorld| world.has_target_tag(site, spawn.entity, tag);
    let effect = TargetTagEffect {
        entity: spawn.entity,
        tag,
        present: true,
    };
    let mut runtime = factory.start(spawn).unwrap();
    assert_eq!(read(&runtime.world), None);
    let foreign = TargetTagEffect {
        entity: spawn.controller,
        ..effect
    };
    assert!(!runtime.world.set_target_tag_effect(site, foreign, true));
    assert!(runtime.world.set_target_tag_effect(site, effect, true));
    assert_eq!(read(&runtime.world), Some(true));
    assert_eq!(
        runtime.world.has_target_tag(site, spawn.entity, tag + 1),
        None
    );

    let replacement = factory
        .start(OwnerBodySpawn {
            frame: spawn.frame + 1,
            ..spawn
        })
        .unwrap();
    assert_eq!(read(&replacement.world), None);
    assert!(runtime.world.set_target_tag_effect(site, effect, false));
    assert_eq!(read(&runtime.world), None);
}

#[test]
fn health_queries_read_the_authoritative_vitals() {
    use crate::script_expr::EvalError;
    use serde_json::json;

    let spawn = fixture_body_factory().1;
    let mut runtime = fixture_hitscan_factory(20.0).start(spawn).unwrap();
    let evaluate = |body: &mut PlayerBodyRuntime, config: &Json| {
        body.component
            .evaluate_state_config(1, 0, config, &mut body.world)
    };
    let owner = json!({"$": "STU_B5A0CAF0"});
    let current = json!({"$": "STU_32E6E4D8", "m_entity": owner});
    let maximum = json!({"$": "STU_7D7D2470", "m_entity": owner});
    assert_eq!(evaluate(&mut runtime, &current), Ok(Value::Float(200.0)));
    assert_eq!(evaluate(&mut runtime, &maximum), Ok(Value::Float(200.0)));
    assert_eq!(
        runtime.receive_damage(
            75.0,
            crate::vitals::Death {
                time: 0.0,
                frame: spawn.frame,
                killer: spawn.entity + 1,
            },
        ),
        crate::vitals::Damage {
            dealt: 75.0,
            killed: false,
            ..Default::default()
        }
    );
    assert_eq!(evaluate(&mut runtime, &current), Ok(Value::Float(125.0)));
    assert_eq!(evaluate(&mut runtime, &maximum), Ok(Value::Float(200.0)));

    let target = json!({"$": "STUConfigVarDynamic", "m_identifier": 400, "m_60DB8F99": true});
    let current = json!({"$": "STU_32E6E4D8", "m_entity": target});
    for (entity, expected) in [
        (
            spawn.entity + 1,
            Err(EvalError::UnsupportedConfig("STU_32E6E4D8".into())),
        ),
        (0, Ok(Value::Float(0.0))),
    ] {
        runtime
            .component
            .set_entity_variable(400, Value::Entity(entity), &mut runtime.world)
            .unwrap();
        assert_eq!(evaluate(&mut runtime, &current), expected);
    }
}

#[test]
fn typed_health_queries_read_the_live_distribution_after_damage() {
    use crate::script_expr::EvalError;
    use serde_json::json;

    let (_, spawn) = fixture_body_factory();
    let mut factory = fixture_hitscan_factory(20.0);
    let mixed = HealthPools {
        health: 200.0,
        armour: 50.0,
        shields: 25.0,
    };
    let evaluate = |body: &mut PlayerBodyRuntime, config: &Json| {
        body.component
            .evaluate_state_config(1, 0, config, &mut body.world)
    };
    let query = |class, kind| {
        json!({
            "$":class, "m_entity":{"$":"STU_B5A0CAF0"}, "m_7BE02F89":null,
            "m_7E8B666C":{"$":"STU_297E1A30","m_7E8B666C":kind,"m_EE729DCB":0}
        })
    };
    let current = "STU_783B4CF0";
    let maximum = "STU_68883EAC";
    let unsupported = |class: &str| Err(EvalError::UnsupportedConfig(class.into()));

    let mut unknown = factory.start(spawn).unwrap();
    assert_eq!(
        evaluate(&mut unknown, &query(current, 0)),
        unsupported(current)
    );
    factory.pools = Some(FALLBACK_POOLS);
    let mut single = factory.start(spawn).unwrap();
    let hit = crate::vitals::Death {
        time: 0.0,
        frame: spawn.frame,
        killer: spawn.entity + 1,
    };
    single.world.vitals.damage(75.0, hit);
    for (kind, amount) in [(0, 125.0), (1, 0.0), (2, 0.0)] {
        assert_eq!(
            evaluate(&mut single, &query(current, kind)),
            Ok(Value::Float(amount))
        );
    }

    factory.pools = Some(mixed);
    for id in [253, 1732, 1737] {
        factory.variables.insert(id, Value::Float(mixed.total()));
    }
    let mut body = factory.start(spawn).unwrap();
    for (kind, amount) in [(0, 200.0), (1, 50.0), (2, 25.0)] {
        for class in [current, maximum] {
            assert_eq!(
                evaluate(&mut body, &query(class, kind)),
                Ok(Value::Float(amount))
            );
        }
    }
    assert!(body.take_health_update().is_none());
    assert_eq!(body.world.vitals.damage(30.0, hit).dealt, 27.5);
    for (kind, amount) in [(0, 200.0), (1, 47.5), (2, 0.0)] {
        assert_eq!(
            evaluate(&mut body, &query(current, kind)),
            Ok(Value::Float(amount))
        );
    }
    assert_eq!(
        evaluate(&mut body, &query(maximum, 2)),
        Ok(Value::Float(25.0))
    );
    assert_eq!(
        body.take_health_update(),
        Some(body.world.vitals.component())
    );
    assert!(body.take_health_update().is_none());
    assert!(body.world.vitals.damage(1000.0, hit).killed);
    for kind in [0, 1, 2] {
        assert_eq!(
            evaluate(&mut body, &query(current, kind)),
            Ok(Value::Float(0.0))
        );
    }
    let mut named = query(current, 0);
    named["m_7BE02F89"] = json!({"$":"STUConfigVarIdentifier","m_identifier":"0x01"});
    let mut foreign = query(current, 0);
    foreign["m_entity"] = json!({"$":"STU_F76C486C","m_D63CBEC2":{"$":"STU_B5A0CAF0"}});
    for config in [named, query(current, 3), foreign] {
        assert_eq!(evaluate(&mut single, &config), unsupported(current));
    }
}

#[test]
fn entity_relations_resolve_only_the_active_player_pair() {
    use crate::script_expr::EvalError;
    use serde_json::json;

    let (_, mut spawn) = fixture_body_factory();
    let factory = fixture_hitscan_factory(20.0);
    spawn.controller = 0xa012_3400;
    let replacement = OwnerBodySpawn {
        entity: 0xa012_7856,
        ..spawn
    };
    let mut first = factory.start(spawn).unwrap();
    let mut second = factory.start(replacement).unwrap();
    let target = json!({"$":"STUConfigVarDynamic","m_identifier":400,"m_60DB8F99":true});
    let owner = json!({"$":"STU_F76C486C","m_D63CBEC2":target,"m_EE729DCB":0});
    let possessor = json!({"$":"STU_F7262B36","m_801B298F":target});
    let body = json!({"$":"STU_0AC15F78","m_entity":target});
    let invalid_owner = json!({"$":"STU_F76C486C","m_D63CBEC2":target,"m_EE729DCB":1});
    let evaluate = |runtime: &mut PlayerBodyRuntime, config: &Json, target: Value| {
        runtime
            .component
            .set_entity_variable(400, target, &mut runtime.world)
            .unwrap();
        runtime
            .component
            .evaluate_state_config(1, 0, config, &mut runtime.world)
    };
    let unsupported_owner = || Err(EvalError::UnsupportedConfig("STU_F76C486C".into()));
    let unsupported_body = || Err(EvalError::UnsupportedConfig("STU_0AC15F78".into()));
    assert_eq!(
        evaluate(&mut first, &owner, Value::Entity(spawn.entity)),
        Ok(Value::Entity(spawn.controller))
    );
    assert_eq!(
        evaluate(&mut first, &possessor, Value::Entity(spawn.entity)),
        Ok(Value::Entity(spawn.controller))
    );
    assert_eq!(
        evaluate(&mut first, &body, Value::Entity(spawn.controller)),
        Ok(Value::Entity(spawn.entity))
    );
    assert_eq!(
        evaluate(&mut second, &body, Value::Entity(spawn.controller)),
        Ok(Value::Entity(replacement.entity))
    );
    assert_eq!(
        evaluate(&mut second, &owner, Value::Entity(replacement.entity)),
        Ok(Value::Entity(spawn.controller))
    );
    assert_eq!(
        evaluate(&mut second, &possessor, Value::Entity(replacement.entity)),
        Ok(Value::Entity(spawn.controller))
    );
    for entity in [spawn.entity, spawn.controller, 0] {
        assert_eq!(
            evaluate(&mut second, &owner, Value::Entity(entity)),
            unsupported_owner()
        );
    }
    assert_eq!(
        evaluate(&mut second, &body, Value::Entity(replacement.entity)),
        unsupported_body()
    );
    assert_eq!(
        evaluate(&mut second, &possessor, Value::Entity(spawn.entity)),
        Err(EvalError::UnsupportedConfig("STU_F7262B36".into()))
    );
    assert_eq!(
        evaluate(
            &mut second,
            &body,
            Value::Integer(i64::from(spawn.controller))
        ),
        unsupported_body()
    );
    assert_eq!(
        evaluate(
            &mut second,
            &invalid_owner,
            Value::Entity(replacement.entity)
        ),
        unsupported_owner()
    );
}

#[test]
fn body_factory_preparation_retains_distinct_startup_failures() {
    use crate::script_init::{Diagnostic, Issue};
    use crate::script_snapshot_wire::{SnapshotError, StateSite};
    use serde_json::json;

    const GRAPH: u32 = 91;
    const UNKNOWN_ACTION: &str = "SyntheticUnknownStartupAction";
    const PLAIN_STATE: &str = "STUStatescriptState";

    let (fixture, spawn) = fixture_body_factory();
    let hero = Hero {
        guid: 1,
        name: "Synthetic".into(),
        role: "Fixture".into(),
        body: 2,
    };
    let prepare = |class: &str, state, probe| {
        let (slot, states, plug) = if state {
            (Some(0), json!([1]), "m_beginPlug")
        } else {
            (None, json!([]), "m_inPlug")
        };
        let bundle = json!({
            "version":1, "missing":[],
            "graphs":[{
                "index":GRAPH, "items":2, "entries":[0], "states":states,
                "remote":[], "sync_vars":[], "containers":{}, "unknown_nodes":[], "fields":{},
                "header":{"m_nodesBitCount":2,"m_statesBitCount":2,
                    "m_remoteSyncNodesBitCount":2,"m_syncVarsBitCount":2},
                "nodes":[
                    {"pos":0,"rid":0,"uid":0,"remote":-1,"class":"STUStatescriptEntry",
                     "state":null,"server_only":false,"client_only":false,
                     "fields":{"m_outPlug":{"$":"Plug","links":[[1,plug]]}}},
                    {"pos":1,"rid":1,"uid":1,"remote":-1,"class":class,
                     "state":slot,"server_only":false,"client_only":false,
                     "fields":{"m_2BBEEAB8":state,"m_ADEB6E05":!state}}
                ]
            }],
            "bodies":{
                format!("0x{:016X}", hero.body):{
                    "hero":format!("0x{:016X}",hero.guid),"name":"Synthetic",
                    "graphs":[{"graph":GRAPH,"m_75408640":0}],"weapons":[]
                }
            }
        });
        let graphs = GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap();
        PlayerBodyFactory::prepare(
            Arc::new(graphs),
            Arc::clone(&fixture.codecs),
            3,
            &hero,
            None,
            false,
            (probe, OwnerBodySetup::default()),
        )
    };

    let Err(OwnerBodyPreparationError::Projection(SnapshotError::Incomplete {
        entity,
        diagnostics,
    })) = prepare(UNKNOWN_ACTION, false, spawn)
    else {
        panic!("unknown action must retain the initialization diagnostics");
    };
    assert_eq!(entity, spawn.entity);
    assert!(matches!(
        diagnostics.as_slice(),
        [Diagnostic {
            site: Site { graph: GRAPH, node: Some(1), .. },
            issue: Issue::UnsupportedAction(class),
            registration_rejected: false,
            server_only: false,
        }] if class == UNKNOWN_ACTION
    ));

    assert!(matches!(
        prepare(PLAIN_STATE, true, spawn),
        Err(OwnerBodyPreparationError::Projection(SnapshotError::UnsupportedState {
            site: StateSite { graph: GRAPH, state: 0, .. },
            class,
            ..
        })) if class == PLAIN_STATE
    ));

    assert!(matches!(
        prepare(PLAIN_STATE, true, OwnerBodySpawn { quanta: 0, ..spawn }),
        Err(OwnerBodyPreparationError::Start(
            OwnerBodyStartupError::Clock
        ))
    ));
}

#[test]
fn player_host_team_counts_are_available_before_body_start_and_refresh_afterward() {
    use serde_json::json;

    #[derive(serde::Deserialize)]
    struct Input {
        query_team: u8,
        invalid_team: u8,
        initial_teams: Vec<u8>,
        updated_teams: Vec<u8>,
        free_for_all_teams: Vec<u8>,
    }

    #[derive(serde::Deserialize)]
    struct Expected {
        initial: u32,
        updated: Option<u32>,
        empty: Option<u32>,
        without_snapshot: Option<u32>,
        invalid_team: Option<u32>,
        free_for_all_query_team: Option<u32>,
        free_for_all_pool: Option<u32>,
    }

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-player-host-facts-v1.json"
    ))
    .unwrap();
    let input: Input =
        serde_json::from_value(fixture["input"]["team_member_counts"].clone()).unwrap();
    let expected: Expected =
        serde_json::from_value(fixture["expected"]["team_member_counts"].clone()).unwrap();

    const GRAPH: u32 = 731;
    const COUNT_VAR: u32 = 36;
    let bundle = json!({
        "version": 1, "missing": [], "bodies": {},
        "graphs": [{
            "index": GRAPH, "items": 2, "entries": [0], "states": [], "remote": [],
            "sync_vars": [], "containers": {}, "unknown_nodes": [], "fields": {},
            "header": {"m_nodesBitCount": 2, "m_statesBitCount": 2,
                "m_remoteSyncNodesBitCount": 2, "m_syncVarsBitCount": 2},
            "nodes": [
                {"pos": 0, "rid": 0, "uid": 0, "remote": -1, "class": "STUStatescriptEntry",
                    "state": null, "server_only": false, "client_only": false,
                    "fields": {"m_outPlug": {"$": "Plug", "links": [[1, "m_inPlug"]]}}},
                {"pos": 1, "rid": 1, "uid": 1, "remote": -1, "class": "STUStatescriptActionSetVar",
                    "state": null, "server_only": false, "client_only": false,
                    "fields": {"m_ADEB6E05": true,
                        "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": COUNT_VAR,
                            "m_60DB8F99": true},
                        "m_value": {"$": "STU_D289FA32", "m_team": input.query_team}}}
            ]
        }]
    });
    let (mut factory, spawn) = fixture_body_factory();
    factory.graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    factory.roots = vec![Root::new(1, GRAPH)];

    let mut runtime = factory
        .start_with_setup(
            spawn,
            OwnerBodySetup {
                team_member_counts: Some(TeamMemberCounts::from_teams(false, input.initial_teams)),
                ..Default::default()
            },
        )
        .unwrap();
    let snapshot = runtime.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert_eq!(
        snapshot.variables.get(&COUNT_VAR),
        Some(&Value::Integer(i64::from(expected.initial)))
    );
    assert_eq!(
        runtime.world.team_members(input.invalid_team),
        expected.invalid_team
    );

    runtime.set_team_member_counts(TeamMemberCounts::from_teams(false, input.updated_teams));
    assert_eq!(
        runtime.world.team_members(input.query_team),
        expected.updated
    );
    runtime.set_team_member_counts(TeamMemberCounts::from_teams(false, []));
    assert_eq!(runtime.world.team_members(input.query_team), expected.empty);
    runtime.set_team_member_counts(TeamMemberCounts::from_teams(true, input.free_for_all_teams));
    assert_eq!(
        runtime.world.team_members(input.query_team),
        expected.free_for_all_query_team
    );
    assert_eq!(runtime.world.team_members(4), expected.free_for_all_pool);

    let without_snapshot = fixture_body_factory().0.start(spawn).unwrap();
    assert_eq!(
        without_snapshot.world.team_members(input.query_team),
        expected.without_snapshot
    );
}

#[test]
fn owner_startup_evaluates_owned_entity_count_without_an_ai_controller() {
    use serde_json::json;

    const DESTINATION: u32 = 812;
    let (mut factory, spawn) = fixture_body_factory();
    let bundle = json!({
        "version": 1, "missing": [], "bodies": {}, "graphs": [{
            "index": 87, "items": 2, "entries": [0], "states": [], "remote": [],
            "sync_vars": [], "containers": {}, "unknown_nodes": [], "fields": {},
            "header": {"m_nodesBitCount": 2, "m_statesBitCount": 2,
                "m_remoteSyncNodesBitCount": 2, "m_syncVarsBitCount": 2},
            "nodes": [
                {"pos": 0, "rid": 0, "uid": 0, "remote": -1, "class": "STUStatescriptEntry",
                    "state": null, "server_only": false, "client_only": false,
                    "fields": {"m_outPlug": {"$": "Plug", "links": [[1, "m_inPlug"]]}}},
                {"pos": 1, "rid": 1, "uid": 1, "remote": -1, "class": "STUStatescriptActionSetVar",
                    "state": null, "server_only": false, "client_only": false,
                    "fields": {"m_ADEB6E05": true,
                        "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": DESTINATION,
                            "m_60DB8F99": true},
                        "m_value": {"$": "STU_967463AB",
                            "m_92D12BE6": {"$": "STU_DB742151",
                                "m_entity": {"$": "STU_B5A0CAF0"}},
                            "m_C8655030": {"$": "STU_8556841E",
                                "m_entityDef": "0x040000000000FA32"}}}}
            ]
        }]
    });
    factory.graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    factory.roots = vec![Root::new(1, 87)];

    let runtime = factory.start(spawn).unwrap();
    let snapshot = runtime.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    // SetVar supplies integer zero as its initial accumulator. The unbound
    // count leaves that value intact without looking up an ownership registry.
    assert_eq!(snapshot.variables[&DESTINATION], Value::Integer(0));
}

#[test]
fn authored_body_activity_reads_room_bootstrap_and_current_generation() {
    use crate::{
        game_combat::{PlayerBodyState, RoomSimulation},
        game_replication::{BodyId, OwnerRoomActivity},
        scene_assets::PreparedScene,
        script_expr::EvalError,
        script_init::{Diagnostic, Issue},
        vitals::Death,
    };
    use serde_json::json;

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-owner-room-activity-v1.json"
    ))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["evidence"], "synthetic-rust");
    let graph = contract["input"]["graph"].as_u64().unwrap() as u32;
    let variable = contract["input"]["variable"].as_u64().unwrap() as u32;
    let expected = &contract["expected"];
    let bundle = json!({
        "version": 1, "missing": [], "bodies": {},
        "authored_classes": [{"class": "authored.activity_probe", "behavior": "interval_state"}],
        "graphs": [{
            "index": graph, "items": 3, "entries": [0], "states": [2], "remote": [],
            "sync_vars": [], "containers": {}, "unknown_nodes": [], "fields": {},
            "header": {"m_nodesBitCount": 2, "m_statesBitCount": 2,
                "m_remoteSyncNodesBitCount": 2, "m_syncVarsBitCount": 2},
            "nodes": [
                {"pos": 0, "uid": 0, "rid": 0, "remote": -1, "class": "STUStatescriptEntry",
                    "state": null, "server_only": true, "client_only": false,
                    "fields": {"m_outPlug": {"$": "Plug", "links": [[1, "m_inPlug"], [2, "m_inPlug"]]}}},
                {"pos": 1, "uid": 1, "rid": 1, "remote": -1, "class": "STUStatescriptActionSetVar",
                    "state": null, "server_only": true, "client_only": false,
                    "fields": {"m_ADEB6E05": true,
                        "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": variable,
                            "m_60DB8F99": true},
                        "m_value": {"$": "BodyActive"}}},
                {"pos": 2, "uid": 2, "rid": 2, "remote": -1, "class": "authored.activity_probe",
                    "state": 0, "server_only": true, "client_only": false,
                    "fields": {"interval_frames": 1,
                        "on_interval": {"$": "Plug", "links": [[1, "m_inPlug"]]}}}
            ]
        }]
    });
    let (mut factory, spawn) = fixture_body_factory();
    factory.graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    factory.roots = vec![Root::new(1, graph)];
    let assert_activity = |runtime: &dyn OwnerBodyRuntime, expected: &Json| {
        let snapshot = runtime.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(
            snapshot.variables[&variable],
            Value::Bool(expected.as_bool().unwrap())
        );
    };

    let detached = factory.start(spawn).unwrap();
    let snapshot = detached.snapshot().unwrap();
    assert!(matches!(
        snapshot.diagnostics.as_slice(),
        [Diagnostic {
            issue: Issue::Expression(EvalError::UnavailableBodyActivity),
            ..
        }]
    ));
    assert!(!snapshot.variables.contains_key(&variable));

    let inactive = factory
        .start_with_setup(
            spawn,
            OwnerBodySetup {
                room_activity: Some(OwnerRoomActivity::Encounter { active: false }),
                ..OwnerBodySetup::default()
            },
        )
        .unwrap();
    assert_activity(&inactive, &expected["inactive_at_startup"]);

    let mut scene = PreparedScene::fixture_for_bots(vec![]);
    let hero = Hero {
        guid: factory.hero,
        name: "Activity probe".into(),
        role: "Damage".into(),
        body: 0x0400_0000_0000_F823,
    };
    scene.owner_body_factory = Some(Arc::new(move |_, spawn, setup| {
        Ok(Some(Box::new(factory.start_with_setup(spawn, setup)?)))
    }));
    let contacts = Arc::new(ContactWorld::new(vec![]).unwrap());
    let state = |id, runtime: Box<dyn OwnerBodyRuntime>| {
        let mover = Mover::new(
            MoverSettings::default(),
            Arc::clone(&contacts),
            spawn.pose.position,
            0,
            MOVER_CATEGORY,
        )
        .unwrap();
        PlayerBodyState::new(
            id,
            spawn.controller,
            spawn.slot,
            0,
            hero.guid,
            mover,
            Some(runtime),
            spawn.frame,
            None,
        )
    };
    let sample_next_frame = |room: &mut RoomSimulation, id, frame, expected: &Json| {
        let state = room.player_body_mut(id).unwrap();
        let runtime = state.runtime.as_mut().unwrap();
        runtime
            .advance(
                Command {
                    frame,
                    ..Command::default()
                },
                &state.mover,
                BodyStep::UnposedTimer,
            )
            .unwrap();
        assert_activity(runtime.as_ref(), expected);
    };

    let first = BodyId {
        entity: spawn.entity,
        generation: 1,
    };
    let runtime = scene.owner_body_runtime(&hero, spawn).unwrap().unwrap();
    assert_activity(runtime.as_ref(), &expected["regular_at_startup"]);
    let room = scene.simulation_mut();
    room.spawn_player_body(state(first, runtime)).unwrap();
    let mut frame = spawn.frame;
    for (active, sample) in [false, true]
        .into_iter()
        .zip(expected["during_encounter"].as_array().unwrap())
    {
        room.set_player_activity(first, OwnerRoomActivity::Encounter { active });
        frame += 1;
        sample_next_frame(room, first, frame, sample);
    }
    let (_, outcome, _) = room
        .damage_player_body_by_wire(
            first,
            contract["input"]["lethal_damage"].as_f64().unwrap(),
            Death {
                time: 2.0,
                frame,
                killer: 42,
            },
            None,
        )
        .unwrap();
    assert!(outcome.killed);
    sample_next_frame(room, first, frame + 1, &expected["after_death"]);

    let replacement = BodyId {
        generation: 2,
        ..first
    };
    let fresh = scene.owner_body_runtime(&hero, spawn).unwrap().unwrap();
    assert_activity(fresh.as_ref(), &expected["after_replacement"]);
    let room = scene.simulation_mut();
    room.spawn_player_body(state(replacement, fresh)).unwrap();
    room.set_player_activity(first, OwnerRoomActivity::Encounter { active: false });
    assert!(room.player_body(first).is_none());
    sample_next_frame(
        room,
        replacement,
        spawn.frame + 1,
        &expected["after_replacement"],
    );
}

#[test]
fn owner_startup_reads_the_frozen_room_frame_before_body_attachment() {
    use crate::{
        collision_cache::CollisionCache,
        contact_world::ContactWorld,
        game_combat::PlayerBodyState,
        game_replication::BodyId,
        mover_settings::MoverSettings,
        scene_assets::PreparedScene,
        script_expr::EvalError,
        script_init::{Diagnostic, Issue},
    };
    use serde_json::json;

    #[derive(serde::Deserialize)]
    struct Input {
        graph: u32,
        variable: u32,
        owner_frame: u32,
        room_frame: u32,
        owner_position: Vec3,
        other_entity: u32,
        other_position: Vec3,
        moved_position: Vec3,
        radius: f64,
    }

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-owner-startup-spatial-v1.json"
    ))
    .unwrap();
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["evidence"], "synthetic-rust");
    let input: Input = serde_json::from_value(contract["input"].clone()).unwrap();
    let bundle = json!({
        "version": 1, "missing": [], "bodies": {},
        "graphs": [{
            "index": input.graph, "items": 2, "entries": [0], "states": [], "remote": [],
            "sync_vars": [], "containers": {}, "unknown_nodes": [], "fields": {},
            "header": {"m_nodesBitCount": 2, "m_statesBitCount": 2,
                "m_remoteSyncNodesBitCount": 2, "m_syncVarsBitCount": 2},
            "nodes": [
                {"pos": 0, "rid": 0, "uid": 0, "remote": -1, "class": "STUStatescriptEntry",
                    "state": null, "server_only": false, "client_only": false,
                    "fields": {"m_outPlug": {"$": "Plug", "links": [[1, "m_inPlug"]]}}},
                {"pos": 1, "rid": 1, "uid": 1, "remote": -1, "class": "STUStatescriptActionSetVar",
                    "state": null, "server_only": false, "client_only": false,
                    "fields": {"m_ADEB6E05": true,
                        "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": input.variable,
                            "m_60DB8F99": true},
                        "m_value": {"$": "EntitiesInRadius", "radius": input.radius,
                            "center": {"$": "EntityPosition", "entity": {"$": "STU_B5A0CAF0"}}}}}
            ]
        }]
    });
    let (mut factory, mut spawn) = fixture_body_factory();
    factory.graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    factory.roots = vec![Root::new(1, input.graph)];
    spawn.frame = input.owner_frame;
    spawn.pose.position = input.owner_position;

    let detached = factory.start(spawn).unwrap();
    let snapshot = detached.snapshot().unwrap();
    assert!(matches!(
        snapshot.diagnostics.as_slice(),
        [Diagnostic {
            issue: Issue::Expression(EvalError::UnavailableSpatialCandidates),
            ..
        }]
    ));
    assert!(!snapshot.variables.contains_key(&input.variable));

    let scene_cache = CollisionCache {
        header: serde_json::from_value(json!({
            "meshes": [], "hulls": 0, "models": 0, "placements": 0, "identifiers": []
        }))
        .unwrap(),
        meshes: vec![],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    };
    let ground = Arc::new(CollisionScene::movement_probe(&scene_cache, &BTreeSet::new()).unwrap());
    let hero = Hero {
        guid: factory.hero,
        name: "Startup fixture".into(),
        role: "Damage".into(),
        body: 0x0400_0000_0000_F872,
    };
    let mut scene = PreparedScene::fixture_for_bots(vec![]);
    scene.simulation_mut().enable_spatial_queries(
        false,
        ground,
        factory.graphs.room_spatial_needs(),
    );
    scene.owner_body_factory = Some(Arc::new(move |_, spawn, setup| {
        let runtime: Box<dyn OwnerBodyRuntime> = Box::new(factory.start_with_setup(spawn, setup)?);
        Ok(Some(runtime))
    }));

    scene
        .simulation_mut()
        .prepare_spatial_frame(input.room_frame)
        .unwrap();
    let empty = scene.owner_body_runtime(&hero, spawn).unwrap().unwrap();
    let snapshot = empty.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    assert_eq!(
        snapshot.variables[&input.variable],
        Value::Array(Vec::new())
    );

    let contacts = Arc::new(ContactWorld::new(Vec::new()).unwrap());
    let mover = |position| {
        Mover::new(
            MoverSettings::default(),
            Arc::clone(&contacts),
            position,
            0,
            MOVER_CATEGORY,
        )
        .unwrap()
    };
    let other = BodyId {
        entity: input.other_entity,
        generation: 1,
    };
    let room = scene.simulation_mut();
    room.spawn_player_body(PlayerBodyState::new(
        other,
        input.other_entity - 1,
        1,
        1,
        hero.guid,
        mover(input.other_position),
        None,
        input.room_frame + 1,
        None,
    ))
    .unwrap();
    room.prepare_spatial_frame(input.room_frame + 1).unwrap();
    room.player_body_mut(other).unwrap().mover = mover(input.moved_position);

    let ready = scene.owner_body_runtime(&hero, spawn).unwrap().unwrap();
    let snapshot = ready.snapshot().unwrap();
    assert!(
        snapshot.initialization_complete(),
        "{:?}",
        snapshot.diagnostics
    );
    let expected: Vec<u32> =
        serde_json::from_value(contract["expected"]["selected"].clone()).unwrap();
    assert_eq!(
        snapshot.variables[&input.variable],
        Value::Array(expected.into_iter().map(Value::Entity).collect())
    );
    assert!(
        scene
            .simulation()
            .player_handle_for_wire(spawn.entity)
            .is_none()
    );
}

#[test]
fn player_host_facts_use_the_bound_body_and_canonical_team() {
    use crate::{match_scene::team_bit, script_expr::EntityRelation};

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-player-host-facts-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["case_id"], "script-player-host-facts.synthetic.v1");
    assert_eq!(fixture["evidence"], "synthetic-rust");
    let foreign = u32::try_from(fixture["input"]["foreign_entity"].as_u64().unwrap()).unwrap();
    let teams: Vec<(u8, i8)> = serde_json::from_value(fixture["input"]["teams"].clone()).unwrap();
    let invalid_filters: Vec<u64> =
        serde_json::from_value(fixture["input"]["invalid_filters"].clone()).unwrap();
    let presence = &fixture["expected"]["entity_exists"];

    let (factory, spawn) = fixture_body_factory();
    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: None,
    };
    let wrong_site = Site {
        entity: foreign,
        ..site
    };
    let mut world = factory.start(spawn).unwrap().world;
    assert_eq!(
        world.related_entity(site, EntityRelation::CurrentPossessor, spawn.entity),
        Some(spawn.controller)
    );
    for (entity, role) in [
        (spawn.entity, "body"),
        (spawn.controller, "controller"),
        (0, "zero"),
        (foreign, "foreign"),
    ] {
        assert_eq!(
            world.entity_exists(entity),
            presence[role].as_bool(),
            "{role}"
        );
    }

    let mut composite = 0;
    for (team, expected) in teams {
        let body_filter = team_bit(team).unwrap();
        composite |= body_filter;
        let world = factory
            .start(OwnerBodySpawn {
                body_filter,
                ..spawn
            })
            .unwrap()
            .world;
        assert_eq!(world.entity_team(site, spawn.entity), Some(expected));
        assert_eq!(world.entity_team(site, spawn.controller), None);
        assert_eq!(world.entity_team(site, foreign), None);
        assert_eq!(world.entity_team(wrong_site, spawn.entity), None);
    }
    for body_filter in invalid_filters.into_iter().chain([composite]) {
        let world = factory
            .start(OwnerBodySpawn {
                body_filter,
                ..spawn
            })
            .unwrap()
            .world;
        assert_eq!(world.entity_team(site, spawn.entity), None);
    }
}

#[test]
fn verified_body_starts_fresh_instances_and_advances_only_its_own_retail_clock() {
    let (factory, spawn) = fixture_body_factory();
    assert!(
        factory
            .start(OwnerBodySpawn {
                quanta: TOURNAMENT_QUANTA,
                ..spawn
            })
            .is_err()
    );
    let mut first = factory.start(spawn).unwrap();
    let second = factory
        .start(OwnerBodySpawn {
            entity: spawn.entity + 1,
            ..spawn
        })
        .unwrap();
    let snapshot = first.snapshot().unwrap();
    assert_eq!(
        snapshot.instances.keys().copied().collect::<Vec<_>>(),
        [1, 2, 3]
    );
    assert_eq!(snapshot.instances[&3].variables[&28], Value::Integer(1));
    assert_eq!(snapshot.variables[&1732], Value::Float(200.0));
    assert_eq!(first.movement_modifiers().map(BTreeMap::len), Some(0));
    assert_eq!(
        first.world.position(spawn.entity),
        Some(spawn.pose.position)
    );
    assert_eq!(first.world.movement, spawn.pose);
    assert_eq!(second.snapshot().unwrap().entity, spawn.entity + 1);

    let site = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 42,
        node: None,
    };
    assert_eq!(
        first.world.movement_sample(site),
        Some(crate::script_init::MovementSample {
            flags: 2,
            air_ticks: spawn.pose.air_ticks,
            pose_frame: spawn.pose.frame,
        })
    );
    assert_eq!(second.world.movement_sample(site), None);
    let mut mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let command = Command {
        frame: 101,
        right: -63,
        forward: 64,
        ..Command::default()
    };
    mover
        .step(
            &TriangleIndex::new([]).unwrap(),
            command,
            0.016,
            |mover, command| first.advance(command, mover, BodyStep::Posed).unwrap(),
        )
        .unwrap();
    assert_eq!(
        first.world.position(spawn.entity),
        Some(mover.pose().position)
    );
    let posed = mover.pose();
    let sample = sample_movement_flags(&posed).expect("a regular posed movement record");
    assert_eq!(first.world.movement, posed);
    assert_eq!(
        first.world.movement_sample(site),
        Some(crate::script_init::MovementSample {
            flags: sample,
            air_ticks: posed.air_ticks,
            pose_frame: posed.frame,
        })
    );
    assert_eq!(first.component.now_ms(), 102 * FRAME_MS - 1);
    assert_eq!(second.component.now_ms(), 101 * FRAME_MS - 1);
    let gap = Command {
        frame: 104,
        ..command
    };
    assert!(first.advance(gap, &mover, BodyStep::Posed).is_err());
    assert_eq!(first.component.now_ms(), 102 * FRAME_MS - 1);
    let unposed_mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        [4.0, 5.0, 6.0],
        0,
        0x18,
    )
    .unwrap();
    first
        .advance(
            Command {
                frame: 102,
                ..Command::default()
            },
            &unposed_mover,
            BodyStep::UnposedBoundary,
        )
        .unwrap();
    assert_eq!(first.world.movement, posed);
    first
        .advance(
            Command {
                frame: 200,
                ..Command::default()
            },
            &unposed_mover,
            BodyStep::UnposedTimer,
        )
        .unwrap();
    assert_eq!(first.world.movement, posed);
    first
        .advance(
            Command {
                frame: 201,
                ..Command::default()
            },
            &mover,
            BodyStep::UnposedBoundary,
        )
        .unwrap();
    assert_eq!(first.world.throttles, [0, 0]);
    assert_eq!(first.component.now_ms(), 202 * FRAME_MS - 1);
}

#[test]
fn owner_eye_query_reads_spawn_anchor_then_updates_with_live_crouch_and_spring() {
    use crate::{
        collision_index::SceneTriangle, commands::CROUCH, game_combat::script_eye_position,
        vitals::Death,
    };

    let (factory, spawn) = fixture_body_factory();
    let [x, y, z] = spawn.pose.position;
    let rays = TriangleIndex::new([SceneTriangle {
        vertices: [[0.0, y, 0.0], [0.0, y, 8.0], [8.0, y, 8.0]],
        mask: MOVER_CATEGORY,
        surface: 0,
    }])
    .unwrap();
    let mut mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        MOVER_CATEGORY,
    )
    .unwrap();
    let standing = [x, y + eye_height(factory.hero, false), z];
    let eye_position = script_eye_position(factory.hero, &mover);
    assert_eq!(eye_position, Some(standing));
    let mut runtime = factory
        .start(OwnerBodySpawn {
            eye_position,
            ..spawn
        })
        .unwrap();
    assert_eq!(
        runtime.world.body_eye_position(spawn.entity),
        Some(standing)
    );

    let command = Command {
        frame: spawn.frame + 1,
        buttons: CROUCH,
        ..Command::default()
    };
    mover
        .step(&rays, command, 0.016, |mover, command| {
            runtime.advance(command, mover, BodyStep::Posed).unwrap();
        })
        .unwrap();
    assert!(mover.crouched());
    let spring = f64::from(mover.spring_offset());
    assert_ne!(spring, 0.0);
    let [x, y, z] = mover.pose().position;
    let crouched = [x, y + (eye_height(factory.hero, true) + spring), z];
    assert_ne!(crouched, mover.pose().position);
    assert_ne!(crouched, standing);
    assert_eq!(
        runtime.world.body_eye_position(spawn.entity),
        Some(crouched)
    );

    assert!(
        runtime
            .receive_damage(
                1_000.0,
                Death {
                    time: 0.0,
                    frame: command.frame,
                    killer: 0,
                },
            )
            .killed
    );
    assert_eq!(runtime.world.body_eye_position(spawn.entity), None);
}

#[test]
fn authored_player_facing_claims_seed_the_mover_and_release_with_runtime() {
    use crate::{
        contact_world::ContactWorld,
        game_combat::{PlayerBodyState, RoomSimulation},
        game_replication::BodyId,
        mover_settings::MoverSettings,
        script_expr::StackPriority,
        script_init::AiFacing,
    };
    use serde_json::json;

    let (mut factory, spawn) = fixture_body_factory();
    let bundle = json!({
        "version": 1, "missing": [], "bodies": {},
        "graphs": [{
            "index": 87, "items": 2, "entries": [0], "states": [1], "remote": [],
            "sync_vars": [], "containers": {}, "unknown_nodes": [], "fields": {},
            "header": {"m_nodesBitCount": 2, "m_statesBitCount": 2,
                "m_remoteSyncNodesBitCount": 2, "m_syncVarsBitCount": 2},
            "nodes": [
                {"pos": 0, "rid": 0, "uid": 0, "remote": -1, "class": "STUStatescriptEntry",
                    "state": null, "server_only": false, "client_only": false,
                    "fields": {"m_outPlug": {"$": "Plug", "links": [[1, "m_inPlug"]]}}},
                {"pos": 1, "rid": 1, "uid": 1, "remote": -1, "class": "authored.native_facing",
                    "state": 0, "server_only": true, "client_only": false,
                    "state_behavior": "facing_claim", "fields": {
                        "direction": {"$": "STUConfigVarDynamic", "m_identifier": 42,
                            "m_60DB8F99": true},
                        "priority": {"level": 17, "above_equals": false}}}
            ]
        }]
    });
    factory.graphs =
        Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
    factory.roots = vec![Root::new(1, 87)];
    factory.variables.insert(42, Value::Vector([1.0, 0.0, 0.0]));
    let mut runtime = factory.start(spawn).unwrap();
    assert!(runtime.snapshot().unwrap().initialization_complete());
    assert_eq!(runtime.authored_facing(), Some([16_384, 0]));
    let first = Site {
        entity: spawn.entity,
        instance: 1,
        graph: 87,
        node: Some(1),
    };
    let second = Site {
        node: Some(2),
        ..first
    };
    let record = AiFacing {
        target: spawn.entity,
        direction: [-1.0, 0.0, 0.0],
        priority: StackPriority {
            level: 17,
            above_equals: false,
        },
    };
    assert_eq!(
        runtime.world.set_facing_claim(second, record, true),
        Some(true)
    );
    assert_eq!(runtime.authored_facing(), Some([16_384, 0]));
    assert_eq!(
        runtime.world.set_facing_claim(first, record, true),
        Some(true)
    );
    assert_eq!(runtime.authored_facing(), Some([-16_384, 0]));
    let later = AiFacing {
        direction: [0.0, 1.0, 0.0],
        priority: StackPriority {
            above_equals: true,
            ..record.priority
        },
        ..record
    };
    assert_eq!(
        runtime.world.set_facing_claim(second, later, true),
        Some(true)
    );
    assert_eq!(runtime.authored_facing(), Some([0, -16_384]));
    assert_eq!(
        runtime.world.set_facing_claim(second, later, false),
        Some(true)
    );
    assert_eq!(runtime.authored_facing(), Some([-16_384, 0]));
    assert_eq!(
        runtime.world.set_facing_claim(
            Site {
                entity: spawn.controller,
                ..first
            },
            record,
            true
        ),
        None
    );
    assert_eq!(
        runtime.world.set_facing_claim(
            second,
            AiFacing {
                target: spawn.controller,
                ..record
            },
            true
        ),
        Some(false)
    );
    assert_eq!(
        runtime.world.set_facing_claim(
            second,
            AiFacing {
                direction: [0.0; 3],
                ..record
            },
            true
        ),
        Some(false)
    );
    let east = AiFacing {
        direction: [1.0, 0.0, 0.0],
        ..record
    };
    assert_eq!(
        runtime.world.set_facing_claim(first, east, true),
        Some(true)
    );

    let ground = crate::collision_index::TriangleIndex::new(Vec::new()).unwrap();
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        MOVER_CATEGORY,
    )
    .unwrap();
    let id = BodyId {
        entity: spawn.entity,
        generation: 1,
    };
    let mut room = RoomSimulation::new(Vec::new()).unwrap();
    room.spawn_player_body(PlayerBodyState::new(
        id,
        spawn.controller,
        0,
        0,
        factory.hero,
        mover,
        Some(Box::new(runtime)),
        spawn.frame,
        None,
    ))
    .unwrap();
    let body = room.player_body_mut(id).unwrap();
    body.mover
        .step(
            &ground,
            Command {
                frame: spawn.frame + 1,
                yaw: 1234,
                pitch: 5678,
                ..Command::default()
            },
            0.016,
            |_, _| {},
        )
        .unwrap();
    assert_eq!(
        [body.mover.pose().yaw, body.mover.pose().pitch],
        [16_384, 0]
    );
    body.mover.set_cinematic_aim(Some([-1234, -5678]));
    body.mover
        .step(
            &ground,
            Command {
                frame: spawn.frame + 2,
                ..Command::default()
            },
            0.016,
            |_, _| {},
        )
        .unwrap();
    assert_eq!(
        [body.mover.pose().yaw, body.mover.pose().pitch],
        [-1234, -5678]
    );
    factory
        .variables
        .insert(42, Value::Vector([-1.0, 0.0, 0.0]));
    let replacement = BodyId {
        generation: 2,
        ..id
    };
    let replacement_runtime = factory.start(spawn).unwrap();
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        MOVER_CATEGORY,
    )
    .unwrap();
    room.spawn_player_body(PlayerBodyState::new(
        replacement,
        spawn.controller,
        0,
        0,
        factory.hero,
        mover,
        Some(Box::new(replacement_runtime)),
        spawn.frame,
        None,
    ))
    .unwrap();
    assert!(room.player_body(id).is_none());
    let body = room.player_body_mut(replacement).unwrap();
    body.mover
        .step(
            &ground,
            Command {
                frame: spawn.frame + 3,
                yaw: 4321,
                pitch: -1234,
                ..Command::default()
            },
            0.016,
            |_, _| {},
        )
        .unwrap();
    assert_eq!(
        [body.mover.pose().yaw, body.mover.pose().pitch],
        [-16_384, 0]
    );
    assert!(room.clear_player_runtime(replacement));
    let body = room.player_body_mut(replacement).unwrap();
    body.mover
        .step(
            &ground,
            Command {
                frame: spawn.frame + 4,
                yaw: 4321,
                pitch: -1234,
                ..Command::default()
            },
            0.016,
            |_, _| {},
        )
        .unwrap();
    assert_eq!(
        [body.mover.pose().yaw, body.mover.pose().pitch],
        [4321, -1234]
    );

    let mut dead = factory.start(spawn).unwrap();
    dead.receive_damage(
        10_000.0,
        crate::vitals::Death {
            frame: spawn.frame,
            time: 0.0,
            killer: 0,
        },
    );
    assert_eq!(dead.authored_facing(), None);
    assert_eq!(dead.world.set_facing_claim(second, east, true), Some(false));
    assert_eq!(dead.world.set_facing_claim(first, east, false), Some(true));
    assert!(dead.world.facing_claims.is_empty());
}

#[test]
fn authored_pose_queries_read_live_player_position_and_aim_outside_encounter_mode() {
    let (factory, spawn) = fixture_body_factory();
    let mut world = factory.start(spawn).unwrap().world;
    assert!(!is_junkenstein_mode(world.mode));
    let current = [0.0, 1.5, -6.25];
    world.position = current;
    world.movement.yaw = 16_384;
    world.movement.pitch = -8_192;
    assert_eq!(world.known_position(spawn.entity), Some(current));
    assert_eq!(world.position(spawn.entity), Some(current));
    assert_eq!(world.authored_position(spawn.entity), Some(current));
    let heading = world.forward(spawn.entity).unwrap();
    for (actual, expected) in heading.into_iter().zip([1.0, 0.0, 0.0]) {
        assert!((actual - expected).abs() < 1e-12);
    }
    let axis = std::f64::consts::FRAC_1_SQRT_2;
    let direction = world.authored_forward(spawn.entity).unwrap();
    for (actual, expected) in direction.into_iter().zip([axis, axis, 0.0]) {
        assert!((actual - expected).abs() < 1e-12);
    }

    let foreign = spawn.entity + 1;
    let static_position = [12.0, 4.0, -2.0];
    let static_forward = [0.0, 0.0, 1.0];
    world.deadeye_scene.view_subjects.insert(
        foreign,
        super::world::ViewSubject {
            position: static_position,
            forward: static_forward,
            tags: Default::default(),
        },
    );
    assert_eq!(world.known_position(foreign), None);
    assert_eq!(world.authored_position(foreign), Some(static_position));
    assert_eq!(world.authored_forward(foreign), Some(static_forward));
    assert_eq!(world.authored_position(spawn.controller), None);
    assert_eq!(world.authored_forward(spawn.controller), None);
}

#[test]
fn body_world_queries_only_expose_its_body_and_available_mode_variable_table() {
    use serde_json::json;

    let (factory, spawn) = fixture_body_factory();
    let mut world = factory.start(spawn).unwrap().world;
    assert_eq!(world.position(spawn.entity), Some(spawn.pose.position));
    world.position = [0.0; 3];
    assert_eq!(world.position(spawn.entity), Some([0.0; 3]));
    for foreign in [0, spawn.controller, spawn.entity + 1, GAME_MODE_ENTITY] {
        assert_eq!(world.position(foreign), None);
    }
    // The initial fixture has no mode table; it cannot claim an unset
    // value for a mode it knows nothing about.
    assert_eq!(world.entity_variable(GAME_MODE_ENTITY, 1), None);
    world.graphs = Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version":1,"graphs":[],"bodies":{},"missing":[],
                "mode_variables":{format!("0x{:016X}", world.mode):{"1":42}}
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    assert_eq!(
        world.entity_variable(GAME_MODE_ENTITY, 1),
        Some(Value::Integer(42))
    );
    assert_eq!(
        world.entity_variable(GAME_MODE_ENTITY, 2),
        Some(Value::Null)
    );
    for foreign in [0, spawn.controller, spawn.entity, spawn.entity + 1] {
        assert_eq!(world.entity_variable(foreign, 1), None);
    }
    world.mode += 1;
    assert_eq!(world.entity_variable(GAME_MODE_ENTITY, 1), None);
}

#[test]
fn owner_mode_entity_query_reads_the_active_mode_at_any_script_site() {
    use crate::{game_content::JUNKENSTEIN_REVENGE, script_expr::EvalError};
    use serde_json::json;

    let contract: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/script-mode-entity-query-v1.json"
    ))
    .unwrap();
    let input = &contract["input"];
    let expected = &contract["expected"];
    let present = Ok(Value::Bool(expected["present"].as_bool().unwrap()));
    let known_absent = Ok(Value::Bool(expected["known_absent"].as_bool().unwrap()));
    assert_eq!(expected["unknown"], "unsupported");
    let variable = u32::try_from(input["entity_variable"].as_u64().unwrap()).unwrap();

    let spawn = fixture_body_factory().1;
    let mut runtime = fixture_hitscan_factory(20.0).start(spawn).unwrap();
    runtime.world.graphs = Arc::new(
        GraphStore::from_json(
            &serde_json::to_vec(&json!({
                "version":1,"graphs":[],"bodies":{},"missing":[],
                "mode_variables":input["mode_variables"]
            }))
            .unwrap(),
        )
        .unwrap(),
    );
    let evaluate = |body: &mut PlayerBodyRuntime, config: &Json| {
        body.component
            .evaluate_state_config(1, 0, config, &mut body.world)
    };
    let mode = json!({"$":"STU_A3DEF368","m_EE729DCB":0});
    let exists = json!({"$":"STU_E72F6348","m_entity":mode.clone()});
    let unsupported = Err(EvalError::UnsupportedConfig("STU_A3DEF368".into()));
    let foreign_site = Site {
        entity: spawn.controller,
        instance: 1,
        graph: 42,
        node: Some(0),
    };

    runtime.world.mode = input["present_mode"].as_u64().unwrap();
    assert_eq!(
        evaluate(&mut runtime, &mode),
        Ok(Value::Entity(GAME_MODE_ENTITY))
    );
    assert_eq!(evaluate(&mut runtime, &exists), present);
    assert_eq!(runtime.world.native(foreign_site, &mode), None);

    let slot = json!({"$":"STUConfigVarDynamic","m_identifier":variable,"m_60DB8F99":true});
    let stored = json!({"$":"STU_E72F6348","m_entity":slot});
    runtime
        .component
        .set_entity_variable(
            variable,
            Value::Entity(GAME_MODE_ENTITY),
            &mut runtime.world,
        )
        .unwrap();
    assert_eq!(evaluate(&mut runtime, &stored), present);

    let mut malformed = exists.clone();
    malformed["m_entity"]["m_extra"] = Json::Null;
    assert_eq!(evaluate(&mut runtime, &malformed), unsupported);

    runtime.world.mode = JUNKENSTEIN_REVENGE;
    assert_eq!(evaluate(&mut runtime, &exists), known_absent);
    assert_eq!(evaluate(&mut runtime, &stored), known_absent);
    assert_eq!(runtime.world.native(foreign_site, &exists), None);
    assert_eq!(evaluate(&mut runtime, &mode), unsupported);
    assert_eq!(evaluate(&mut runtime, &malformed), unsupported);

    runtime.world.mode = input["unknown_mode"].as_u64().unwrap();
    assert_eq!(evaluate(&mut runtime, &exists), unsupported);
    assert_eq!(
        evaluate(&mut runtime, &stored),
        Err(EvalError::UnsupportedConfig("STU_E72F6348".into()))
    );
}

#[test]
fn posed_volley_keeps_the_authored_shot_frame_and_drains_unposed_reports() {
    let factory = fixture_hitscan_factory(20.0);
    let (_, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let fire = Command {
        frame: 101,
        buttons: crate::commands::FIRE,
        ..Command::default()
    };
    runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
    let command = Command {
        frame: 102,
        yaw: 12,
        pitch: 30,
        ..Command::default()
    };
    let shots = runtime.take_shots(Some(command), &mover);
    let [PosedShot::Hitscan(shot)] = shots.as_slice() else {
        panic!("expected the opening shot")
    };
    assert_eq!(shot.source, spawn.entity);
    assert_eq!(shot.origin, [1.0, 3.55, 3.0]);
    assert_eq!(shot.shot_frame, 101);
    assert_eq!(shot.command, command);
    assert_eq!(
        (shot.reach, shot.hit.amount, shot.hit.critical),
        (180.0, 20.0, 2.0)
    );
    assert!(runtime.take_shots(Some(command), &mover).is_empty());

    runtime
        .advance(command, &mover, BodyStep::UnposedBoundary)
        .unwrap();
    assert_eq!(
        runtime.snapshot().unwrap().instances[&1].states[&0].kind,
        crate::script_init::StateKind::LogicalButton { down: false }
    );
    let timer = Command {
        frame: 107,
        ..Command::default()
    };
    runtime
        .advance(timer, &mover, BodyStep::UnposedTimer)
        .unwrap();
    assert!(runtime.take_shots(None, &mover).is_empty());
    assert!(runtime.take_shots(Some(timer), &mover).is_empty());
}

#[test]
fn a_volley_skips_unposed_rounds_and_bills_the_next_posed_round() {
    use crate::{
        script_expr::{Scope, Variable},
        script_player::fixture_volley_factory,
    };

    #[derive(serde::Deserialize)]
    struct Contract {
        schema_version: u32,
        magazine: i64,
        maximum_rounds: u32,
        shots_per_second: f64,
        unposed_offset: u32,
        resumed_offset: u32,
        expected_ammunition: [i64; 3],
        expected_shots: [usize; 3],
    }
    let contract: Contract = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/owner-unposed-volley-v1.json"
    ))
    .unwrap();
    assert_eq!(contract.schema_version, 1);

    const AMMO: u32 = 147;
    let mut factory = fixture_volley_factory(
        20,
        false,
        serde_json::json!({
            "m_A17BE89B":contract.maximum_rounds,
            "m_numShotsPerSecond":contract.shots_per_second,
            "m_FEC435C6":100,
            "m_24E351A4":{"$":"STUConfigVarDynamic","m_identifier":AMMO},
            "m_6396149F":{"m_modifyHealth":{"m_amount":10.0}},
        }),
    );
    factory.roots[0]
        .variables
        .insert(AMMO, Value::Integer(contract.magazine));
    let (_, spawn) = fixture_body_factory();
    let mut runtime = factory.start(spawn).unwrap();
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let ammo = |runtime: &PlayerBodyRuntime| {
        runtime.component.resolved_variable(
            1,
            Variable {
                scope: Scope::Instance,
                id: AMMO,
            },
        )
    };
    let fire = Command {
        frame: spawn.frame + 1,
        buttons: crate::commands::FIRE,
        ..Command::default()
    };
    runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
    assert_eq!(
        runtime.take_shots(Some(fire), &mover).len(),
        contract.expected_shots[0]
    );
    assert_eq!(
        ammo(&runtime),
        Some(Value::Integer(contract.expected_ammunition[0]))
    );

    // Advance the authored clock across the second round, which has no
    // usable pose. The volley survives to its scheduled third round.
    let timer = Command {
        frame: fire.frame + contract.unposed_offset,
        ..Command::default()
    };
    runtime
        .advance(timer, &mover, BodyStep::UnposedTimer)
        .unwrap();
    assert_eq!(
        runtime.take_shots(None, &mover).len(),
        contract.expected_shots[1]
    );
    assert_eq!(
        ammo(&runtime),
        Some(Value::Integer(contract.expected_ammunition[1]))
    );

    let resumed = Command {
        frame: fire.frame + contract.resumed_offset,
        ..fire
    };
    runtime.advance(resumed, &mover, BodyStep::Posed).unwrap();
    let shots = runtime.take_shots(Some(resumed), &mover);
    assert_eq!(shots.len(), contract.expected_shots[2]);
    let [PosedShot::Hitscan(shot)] = shots.as_slice() else {
        panic!("expected the last scheduled round with a valid pose");
    };
    assert_eq!(shot.shot_frame, resumed.frame);
    assert_eq!(
        ammo(&runtime),
        Some(Value::Integer(contract.expected_ammunition[2]))
    );
}

#[test]
fn secondary_volley_uses_authored_projectile_and_owner_lease() {
    let factory = fixture_projectile_factory(40.0);
    let (_, mut spawn) = fixture_body_factory();
    spawn.slot = 2;
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let fire = Command {
        frame: spawn.frame + 1,
        buttons: crate::commands::SECONDARY,
        yaw: 16384,
        ..Command::default()
    };
    let mut runtime = factory.start(spawn).unwrap();
    assert_eq!(
        runtime
            .projectile_leases()
            .unwrap()
            .entity(FIXTURE_PROJECTILE_DEFINITION, fire.frame, 0),
        Some(0xa210_0065)
    );
    runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
    let shots = runtime.take_shots(Some(fire), &mover);
    let [PosedShot::Projectile(rocket)] = shots.as_slice() else {
        panic!("expected a predicted rocket");
    };
    assert_eq!(
        (rocket.shot.entity, rocket.shot.frame),
        (spawn.entity, fire.frame)
    );
    assert_eq!((rocket.shot.instance, rocket.shot.raw_state), (1, 1));
    assert_eq!(rocket.origin, [1.0, 3.55, 3.0]);
    assert_eq!(rocket.direction, aim_direction(&fire));
    assert_eq!(
        rocket.ballistics.definition,
        Some(FIXTURE_PROJECTILE_DEFINITION)
    );
    assert_eq!(
        (rocket.ballistics.speed, rocket.ballistics.lifetime),
        (50.0, 2.0)
    );
    assert_eq!(rocket.ballistics.direct.unwrap().amount, 40.0);
    assert_eq!(rocket.ballistics.blast.as_ref().unwrap().hit.amount, 80.0);
    assert!(runtime.take_shots(Some(fire), &mover).is_empty());

    let encoder = runtime.encoder();
    let owner = encoder
        .project(runtime.snapshot().unwrap(), View::Owner)
        .unwrap();
    assert_eq!(
        owner.volley_identity(1, 1),
        Some((rocket.shot.activation, rocket.shot.activation_ms))
    );
}

#[test]
#[ignore = "requires separately prepared 104319 Flashbang effect graphs"]
fn authored_flashbang_duration_follows_both_mode_branches() {
    use serde_json::json;
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap();
    let config = LEGACY_PROJECTILE_CONTROL.duration(&graphs).unwrap();
    for (flag, ticks) in [(false, 50), (true, 107)] {
        let (_, spawn) = fixture_body_factory();
        let mut runtime = fixture_volley_factory(29, true, fixture_projectile_fields(40.0))
            .start(spawn)
            .unwrap();
        let mode = format!("0x{:016X}", runtime.world.mode);
        runtime.world.graphs = Arc::new(
            GraphStore::from_json(
                &serde_json::to_vec(&json!({
                    "version":1,"graphs":[],"bodies":{},"missing":[],
                    "mode_variables":{mode:{"13408":flag}}
                }))
                .unwrap(),
            )
            .unwrap(),
        );
        let duration = runtime
            .component
            .evaluate_state_config(1, 1, config, &mut runtime.world)
            .unwrap();
        assert_eq!(
            Stun::from_seconds(to_float(&duration)).unwrap().ticks,
            ticks
        );
    }
}

#[test]
fn posed_single_projectiles_use_the_supported_aim_configurations() {
    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/projectile-posed-aim-v1.json"
    ))
    .unwrap();
    let (_, spawn) = fixture_body_factory();
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let fire = Command {
        frame: spawn.frame + 1,
        buttons: crate::commands::SECONDARY,
        yaw: 16_384,
        pitch: 4_096,
        ..Command::default()
    };
    for case in fixture["cases"].as_array().unwrap() {
        let mut fields = fixture_projectile_fields(40.0);
        fields["m_aimID"] = case["aim"].clone();
        fields["m_5EC1EB38"] = case["launch"].clone();
        let mut runtime = fixture_volley_factory(29, true, fields)
            .start(spawn)
            .unwrap();
        runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
        let shots = runtime.take_shots(Some(fire), &mover);
        if case["uses_pose"] == true {
            let [PosedShot::Projectile(projectile)] = shots.as_slice() else {
                panic!("expected the posed projectile for {}", case["name"]);
            };
            assert_eq!(projectile.direction, aim_direction(&fire));
        } else {
            assert!(shots.is_empty(), "{}", case["name"]);
        }
    }
}

#[test]
fn impact_spawn_uses_declared_resources_on_a_relocated_volley() {
    use crate::{
        game_combat::{BIOTIC_CANISTER, BIOTIC_FIELD_ENTITY},
        game_projectiles::ImpactSpawn,
    };
    use serde_json::json;

    let fixture: Json = serde_json::from_str(include_str!(
        "../../../fixtures/behavior/projectile-impact-spawn-v1.json"
    ))
    .unwrap();
    let (_, spawn) = fixture_body_factory();
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let fire = Command {
        frame: spawn.frame + 1,
        buttons: crate::commands::SECONDARY,
        ..Command::default()
    };
    for case in fixture["cases"].as_array().unwrap() {
        let mut fields = fixture_projectile_fields(40.0);
        fields["m_projectileEntity"]["m_entityDef"]["m_entityDef"] =
            json!(format!("0x{BIOTIC_CANISTER:016X}"));
        fields["m_49CBA3BC"] = json!({
            "$": case["class"],
            "m_entityDefinition": {"$":"STU_8556841E", "m_entityDef":format!("0x{BIOTIC_FIELD_ENTITY:016X}")},
        });
        if let Some(graph) = case["on_spawn_graph"].as_str() {
            fields["m_49CBA3BC"]["m_410BF8BD"] = json!({"$":"STU_105E1BCC","m_graph":graph});
        }
        let mut factory = fixture_volley_factory(29, true, fields);
        factory.hero = CASSIDY;
        let node = factory.graphs.graph(42).unwrap().state(1).unwrap();
        let parsed = ImpactSpawn::from_volley(node, |value| {
            crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
        });
        match case["on_spawn_graph"].as_str() {
            Some(graph) => assert_eq!(
                parsed.unwrap().unwrap().on_spawn_graph,
                crate::accounts::parse_guid(graph)
            ),
            None if case["field"] == true => assert_eq!(
                parsed.unwrap(),
                Some(ImpactSpawn {
                    definition: BIOTIC_FIELD_ENTITY,
                    on_spawn_graph: None
                })
            ),
            None => assert!(parsed.is_err()),
        }
        let mut runtime = factory.start(spawn).unwrap();
        runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
        let shots = runtime.take_shots(Some(fire), &mover);
        assert_eq!(
            matches!(shots.as_slice(), [PosedShot::BioticField(_)]),
            case["field"] == true,
            "{}",
            case["name"],
        );
    }
}

#[test]
fn unsupported_projectile_modes_do_not_become_hitscan_or_straight_damage() {
    use serde_json::json;

    let (_, spawn) = fixture_body_factory();
    let mover = Mover::new(
        MoverSettings::default(),
        Arc::new(ContactWorld::new(Vec::new()).unwrap()),
        spawn.pose.position,
        0,
        0x18,
    )
    .unwrap();
    let fire = Command {
        frame: spawn.frame + 1,
        buttons: crate::commands::SECONDARY,
        ..Command::default()
    };
    for (field, value) in [
        ("m_45271E8C", json!(false)),
        ("m_projectileMotions", json!([{"$":"STU_E1A5B579"}])),
        ("m_numProjectilesPerShot", json!(2)),
        ("m_6396149F", json!({"m_modifyHealth":{"m_amount":-1.0}})),
    ] {
        let mut fields = fixture_projectile_fields(40.0);
        fields[field] = value;
        let mut runtime = fixture_volley_factory(29, true, fields)
            .start(spawn)
            .unwrap();
        runtime.advance(fire, &mover, BodyStep::Posed).unwrap();
        assert!(runtime.take_shots(Some(fire), &mover).is_empty(), "{field}");
    }
}

fn prepared_soldier_component(graphs: Arc<GraphStore>, tagged: bool) -> (Component, BodyWorld) {
    use crate::{game_clock::FIRST_TICK, game_content::GameMap};
    let pools = crate::game_pools::PoolCatalog::from_graphs(&graphs)
        .unwrap()
        .hero_pools(SOLDIER)
        .unwrap();
    let body = &graphs.bodies()[&0x0400_0000_0000_03cf];
    assert!(body.target_tags.is_some());
    let roots = crate::script_init::player_roots(body, &graphs).unwrap();
    let health = pools.total();
    let variables = crate::script_init::player_entity_variables(body, health, health);
    let mut world = BodyWorld {
        spatial_frame: None,
        team_member_counts: None,
        room_activity: None,
        script_requests: Default::default(),
        graphs: Arc::clone(&graphs),
        controller: 0xa000_0100,
        entity: 0xa000_0101,
        body_filter: crate::match_scene::team_bit(0).unwrap(),
        position: GameMap::practice_range().spawn_point(0, 0).unwrap().0,
        eye_position: None,
        movement: Movement::default(),
        throttles: [0; 2],
        mode: PRACTICE_RANGE_MODE,
        hero: SOLDIER,
        vitals: Vitals::empty(),
        pool_types_known: true,
        target_tags: tagged.then(|| body.target_tags.clone().unwrap()),
        model_hardpoints: None,
        target_tag_effects: TargetTagEffects::default(),
        controller_on_fire: false,
        defenders: Arc::default(),
        sightings: Vec::new(),
        sighting_source: None,
        cinematic_aim: None,
        facing_claims: Default::default(),
        deadeye_scene: DeadeyeScene::default(),
        velocity_changes: Vec::new(),
        scripted_motion: None,
    };
    let mut component = Component::start(
        graphs,
        world.entity,
        variables,
        roots,
        FIRST_TICK,
        &mut world,
        MAX_INIT_WORK,
    )
    .unwrap();
    component.advance_frame(FIRST_TICK, &mut world).unwrap();
    (component, world)
}

#[test]
#[ignore = "requires a tagged separately prepared 104319 statescript catalog"]
fn prepared_soldier_without_target_tags_reports_unavailable_filter() {
    use crate::{script_expr::EvalError, script_init::Issue};
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let (component, world) = prepared_soldier_component(graphs, false);
    assert!(world.target_tags.is_none());
    let snapshot = component.snapshot().unwrap();
    assert!(!snapshot.initialization_complete());
    assert!(snapshot.diagnostics.iter().any(|diagnostic| {
        diagnostic.site.graph == 0x004b
            && diagnostic.site.node == Some(21)
            && matches!(&diagnostic.issue, Issue::Expression(EvalError::UnsupportedConfig(class)) if class == "STU_AC829876")
    }), "{:?}", snapshot.diagnostics);
}

#[test]
#[ignore = "requires a tagged separately prepared 104319 statescript catalog"]
fn prepared_wheel_packet_bindings_reach_both_authored_handlers() {
    use crate::{
        collision_cache::CollisionCache,
        contact_world::ContactWorld,
        game_clock::FIRST_TICK,
        game_combat::{PlayerBodyState, RoomSimulation},
        game_messages::Message,
        game_parameters::{CLIENT_WHEEL_ACTION, ClientWheelAction},
        game_replication::BodyId,
        jam_codec::{Fields, Value as WireValue},
        mover_settings::MoverSettings,
        script_graph::RoomSpatialNeeds,
    };

    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let action = 0x0a90_0000_0000_0171;
    let request = ClientWheelAction::read(&Message {
        id: CLIENT_WHEEL_ACTION,
        fields: Fields::from([
            (0x78, WireValue::Unsigned(action)),
            (0x80, WireValue::Bool(true)),
        ]),
    })
    .unwrap()
    .into_local_message();
    for with_room in [false, true] {
        let (mut component, mut world) = prepared_soldier_component(Arc::clone(&graphs), true);
        if with_room {
            let ground = CollisionScene::movement_probe(
                &CollisionCache {
                    header: serde_json::from_str(
                        r#"{"meshes":[],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
                    )
                    .unwrap(),
                    meshes: Vec::new(),
                    hulls: Vec::new(),
                    models: Vec::new(),
                    placements: Vec::new(),
                },
                &Default::default(),
            )
            .unwrap();
            let mut room = RoomSimulation::new(Vec::new()).unwrap();
            room.enable_spatial_queries(false, Arc::new(ground), RoomSpatialNeeds::Queries);
            let mover = Mover::new(
                MoverSettings::default(),
                Arc::new(ContactWorld::new(Vec::new()).unwrap()),
                world.position,
                0,
                MOVER_CATEGORY,
            )
            .unwrap();
            room.spawn_player_body(PlayerBodyState::new(
                BodyId {
                    entity: world.entity,
                    generation: 1,
                },
                world.controller,
                0,
                0,
                world.hero,
                mover,
                None,
                FIRST_TICK,
                None,
            ))
            .unwrap();
            world.spatial_frame = room.capture_spatial_frame(FIRST_TICK + 1).unwrap();
            assert!(world.spatial_frame.is_some());
        }
        assert_eq!(
            component
                .queue_entity_message_from(world.entity, request.id, request.parameters.clone())
                .unwrap(),
            2
        );
        component.advance_frame(FIRST_TICK + 1, &mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        if graphs.has_asset_category_data() {
            assert!(!snapshot.diagnostics.iter().any(|diagnostic| {
                matches!(&diagnostic.issue, crate::script_init::Issue::Expression(crate::script_expr::EvalError::UnsupportedConfig(class))
                    if class == "STU_4460419A")
            }));
            assert_eq!(
                snapshot.diagnostics.iter().any(|diagnostic| {
                    diagnostic.issue
                        == crate::script_init::Issue::UnsupportedField(
                            "view target query unavailable",
                        )
                }),
                !with_room,
                "{:?}",
                snapshot.diagnostics
            );
            if with_room {
                let callout = snapshot
                    .instances
                    .values()
                    .find(|instance| instance.graph == 0x0686)
                    .unwrap();
                let local_callout = callout.variables.get(&29861);
                assert!(
                    matches!(local_callout, Some(Value::Map(rows)) if rows.iter().any(
                        |(key, value)| *key == Value::Asset(action) && matches!(value, Value::Map(parameters) if parameters.len() == 2)
                    )),
                    "{local_callout:?}"
                );
                assert!(
                    snapshot.frame_actions.iter().any(|action| {
                        snapshot
                            .instances
                            .get(&action.callout.instance)
                            .is_some_and(|instance| instance.graph == 0x0686)
                            && action.values.len() == 2
                    }),
                    "{:?}",
                    snapshot.frame_actions
                );
                if let Some(texture) = graphs.asset_texture(action) {
                    assert_eq!(callout.variables.get(&3793), Some(&Value::Asset(texture)));
                }
            }
        }
        for diagnostic in snapshot
            .diagnostics
            .iter()
            .filter(|diagnostic| !diagnostic.server_only)
        {
            eprintln!(
                "wheel room={with_room} {:04X}:{:?} {:?}",
                diagnostic.site.graph, diagnostic.site.node, diagnostic.issue
            );
        }
        for graph in [0x05ad, 0x02cf] {
            let instance = snapshot
                .instances
                .values()
                .find(|instance| instance.graph == graph)
                .unwrap();
            assert_eq!(
                instance.variables[&29699],
                Value::Asset(action),
                "{graph:04X}"
            );
            if graph == 0x05ad {
                assert_eq!(instance.variables[&9253], Value::Bool(true));
            }
        }
    }
}

#[test]
#[ignore = "requires a tagged separately prepared 104319 statescript catalog"]
fn prepared_soldier_starts_the_tagged_body_and_weapon_tree() {
    use crate::{game_clock::FIRST_TICK, script_codecs::PayloadMode, script_init::StateKind};
    use std::path::PathBuf;

    let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let pools = crate::game_pools::PoolCatalog::from_graphs(&graphs)
        .unwrap()
        .hero_pools(SOLDIER)
        .unwrap();
    let (mut component, mut world) = prepared_soldier_component(graphs, true);
    let snapshot = component.snapshot().unwrap();
    for diagnostic in &snapshot.diagnostics {
        eprintln!(
            "{:04X}:{:?} {:?}",
            diagnostic.site.graph, diagnostic.site.node, diagnostic.issue
        );
    }
    // The tagged tree includes 01CB, shifting subsequent IDs from the
    // frozen BodyScript replay. IDs 9 and 26 are absent after startup.
    let expected = [
        (1, 0x0033, None),
        (2, 0x004b, None),
        (3, 0x0043, None),
        (4, 0x0251, None),
        (5, 0x0255, None),
        (6, 0x0257, None),
        (7, 0x0259, None),
        (8, 0x091b, None),
        (10, 0x0015, None),
        (11, 0x0254, None),
        (12, 0x01c7, Some((2, 0))),
        (13, 0x01cf, Some((2, 3))),
        (14, 0x02c5, Some((2, 5))),
        (15, 0x0b8b, Some((2, 10))),
        (16, 0x0c7d, Some((2, 18))),
        (17, 0x01ca, Some((2, 1))),
        (18, 0x01cb, Some((2, 2))),
        (19, 0x0ba4, Some((2, 11))),
        (20, 0x05ac, Some((2, 8))),
        (21, 0x02cf, Some((2, 6))),
        (22, 0x0b3b, Some((2, 9))),
        (23, 0x01d8, Some((2, 4))),
        (24, 0x0f82, Some((2, 21))),
        (25, 0x05ad, Some((2, 7))),
        (27, 0x0e8f, Some((13, 32))),
    ];
    let instances: Vec<_> = snapshot
        .instances
        .iter()
        .map(|(&id, instance)| (id, instance.graph, instance.parent))
        .collect();
    assert_eq!(instances, expected);
    let movement_graph = &snapshot.instances[&12];
    let movement = &movement_graph.states[&4];
    assert!(movement.active);
    assert!(matches!(
        movement.kind,
        StateKind::MovementSample { flags: 2, .. }
    ));
    assert_eq!(snapshot.variables[&545], Value::Bool(true));
    assert!(
        !snapshot
            .diagnostics
            .iter()
            .any(|item| item.site.graph == 0x01c7 && item.site.node == Some(7))
    );
    let body_pools = &snapshot.instances[&1];
    assert!(body_pools.states[&0].active);
    assert_eq!(
        body_pools.states[&0].kind,
        StateKind::HealthPool { pool: Some(1) }
    );
    assert_eq!(body_pools.variables[&520], Value::Float(200.0));
    assert_eq!(world.vitals.component(), pools.vitals().component());
    let target_tag = &snapshot.instances[&12].states[&31];
    let effect = TargetTagEffect {
        entity: world.entity,
        tag: 0x0d60_0000_0000_0422,
        present: true,
    };
    assert!(target_tag.active);
    assert_eq!(
        target_tag.kind,
        StateKind::TargetTag {
            effect: Some(effect)
        }
    );
    let site = Site {
        entity: world.entity,
        instance: 12,
        graph: 0x01c7,
        node: Some(96),
    };
    assert_eq!(
        world.has_target_tag(site, world.entity, effect.tag),
        Some(true)
    );
    assert!(
        !snapshot
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.site == site)
    );
    let Value::Map(messages) = &snapshot.instances[&25].variables[&29859] else {
        panic!("the authored message table must be a map");
    };
    assert_eq!(messages.len(), 20);
    assert_eq!(
        messages[0],
        (
            Value::Asset(0x0a90_0000_0000_0171),
            Value::Asset(0x0240_0000_0000_05ad)
        )
    );
    assert_eq!(
        snapshot.instances[&1].variables[&9737],
        Value::Entity(world.entity)
    );
    assert!(
        !snapshot
            .diagnostics
            .iter()
            .any(|item| item.site.graph == 0x0033 && item.site.node == Some(51))
    );
    let watch = &snapshot.instances[&13].states[&21];
    assert!(watch.active);
    assert_eq!(watch.kind, crate::script_init::StateKind::Watch);
    assert!(
        !snapshot
            .diagnostics
            .iter()
            .any(|item| item.site.graph == 0x01cf && item.site.node == Some(41))
    );
    assert_eq!(snapshot.instances[&11].variables[&28], Value::Integer(1));
    for (instance, state) in [(10, 42), (10, 43), (11, 83), (13, 138), (13, 139), (14, 30)] {
        let controller = &snapshot.instances[&instance].states[&state];
        assert!(controller.active);
        assert_eq!(controller.kind, crate::script_init::StateKind::Plain);
    }
    let tips = &snapshot.instances[&4].states[&0];
    assert!(tips.active);
    assert_eq!(tips.kind, StateKind::Plain);
    let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
    for mode in [
        PayloadMode::Remote { full: true },
        PayloadMode::Owner {
            full: true,
            frame_time: i64::from(FIRST_TICK) * FRAME_MS as i64,
            frame_ms: FRAME_MS as u32,
        },
    ] {
        for class in [
            "STU_CD46AF93",
            "STU_F5A99B2A",
            "STU_D9843704",
            "STU_E56BA8D8",
        ] {
            let codec = codecs.codec(class).unwrap();
            assert_eq!(codec.payload(&BTreeMap::new(), mode).unwrap().bit_len(), 0);
        }
    }
    for (instance, raw, position) in [(1, 13, 39), (2, 22, 32), (2, 23, 33), (10, 39, 73)] {
        let instance = &snapshot.instances[&instance];
        let node = world
            .graphs
            .graph(instance.graph)
            .unwrap()
            .state(usize::from(raw))
            .unwrap();
        assert_eq!((node.pos, node.class.as_str()), (position, "STU_CD46AF93"));
        let state = &instance.states[&raw];
        assert!(state.active);
        assert_eq!(state.kind, StateKind::ForCode);
    }
    for (instance, raw) in [(1, 4), (10, 41)] {
        let child = &snapshot.instances[&instance].states[&raw];
        assert!(child.active);
        assert_eq!(child.kind, StateKind::BooleanSwitch(false));
    }
    let selection = &snapshot.instances[&19].states[&0];
    assert!(selection.active);
    assert_eq!(selection.kind, StateKind::BooleanSwitch(false));
    assert!(!snapshot.instances[&19].states.contains_key(&1));
    let on_fire = &snapshot.instances[&13].states[&17];
    assert!(on_fire.active);
    assert_eq!(on_fire.kind, StateKind::BooleanSwitch(false));
    assert!(
        !snapshot.diagnostics.iter().any(|diagnostic| {
            diagnostic.site.graph == 0x01cf && diagnostic.site.node == Some(31)
        })
    );
    for (graph, node) in [
        (0x0033, 1),
        (0x0033, 66),
        (0x004b, 19),
        (0x004b, 20),
        (0x08b6, 3),
        (0x08b6, 11),
        (0x0ba4, 10),
    ] {
        assert!(!snapshot.diagnostics.iter().any(|diagnostic| {
            diagnostic.site.graph == graph && diagnostic.site.node == Some(node)
        }));
    }
    let filter_diagnostic = snapshot
        .diagnostics
        .iter()
        .any(|diagnostic| diagnostic.site.graph == 0x004b && diagnostic.site.node == Some(21));
    assert_eq!(filter_diagnostic, world.target_tags.is_none());
    // With tags, startup continues past the effect action at 01C7 node 74.
    assert_eq!(
        snapshot.initialization_complete(),
        world.target_tags.is_some()
    );
    for raw in [12, 13] {
        let filter = &snapshot.instances[&2].states[&raw];
        assert!(filter.active);
        assert_eq!(filter.kind, StateKind::BooleanSwitch(true));
    }
    if world.target_tags.is_some() {
        let filter = &snapshot.instances[&2].states[&14];
        assert!(filter.active);
        assert_eq!(filter.kind, StateKind::BooleanSwitch(true));
    }

    let authored_graphs = Arc::clone(&world.graphs);
    let authored_field = |graph, position, field: &str| {
        authored_graphs
            .graph(graph)
            .unwrap()
            .node(position)
            .unwrap()
            .field(field)
            .unwrap()
            .clone()
    };
    let sprint_condition = authored_field(0x0255, 6, "m_condition");
    let watch_possessor = authored_field(0x01cf, 41, "m_B7A3CF78");
    let shield_refill = authored_field(0x0033, 66, "m_BD83C8E6");
    let health_present = authored_field(0x08b6, 2, "m_condition");
    let filter = authored_field(0x004b, 20, "m_condition");
    let mask = world.body_filter;
    for (flags, passes) in [(mask, true), (mask | 2, false)] {
        world.body_filter = flags;
        assert_eq!(
            component.evaluate_state_config(2, 13, &filter, &mut world),
            Ok(Value::Bool(passes))
        );
    }
    world.body_filter = mask;
    let on_fire = authored_field(0x01cf, 31, "m_condition");
    for value in [false, true] {
        world.controller_on_fire = value;
        assert_eq!(
            component.evaluate_state_config(13, 17, &on_fire, &mut world),
            Ok(Value::Bool(value))
        );
    }
    world.controller_on_fire = false;
    let entries = authored_field(0x05ad, 240, "m_value");
    let mut message = authored_field(0x05ad, 183, "m_gameMessage");
    // Supply one authored key as a literal rather than simulating its UI input.
    message["m_configVars"] = serde_json::json!([entries["m_configVars"][0]["m_key"].clone()]);
    message["m_expression"]["m_opcodes"] = serde_json::json!([8, 0, 14, 0, 17, 0]);
    assert_eq!(
        component.evaluate_state_config(25, 0, &message, &mut world),
        Ok(Value::Asset(0x0240_0000_0000_05ad))
    );
    if let Some(original_tags) = world.target_tags.clone() {
        let filter = authored_field(0x004b, 21, "m_condition");
        let tag_query = authored_field(0x0043, 45, "m_condition");
        let required = crate::script_graph::target_tag_guid(&tag_query["m_88B0050A"]).unwrap();
        assert!(!original_tags.contains(&required));
        for present in [false, true] {
            if present {
                world.target_tags.as_mut().unwrap().insert(required);
            }
            assert_eq!(
                component.evaluate_state_config(2, 14, &filter, &mut world),
                Ok(Value::Bool(!present))
            );
            assert_eq!(
                component.evaluate_state_config(3, 0, &tag_query, &mut world),
                Ok(Value::Bool(present))
            );
        }
        world.target_tags = Some(original_tags);
    }
    let rifle_conditions = [81, 83].map(|position| authored_field(0x0254, position, "m_condition"));
    // 01C7 st48 reads the same current and maximum health as the
    // health-ratio branches in 01CF st7/st9.
    let full_health = authored_field(0x01c7, 145, "m_condition");
    assert_eq!(
        component.evaluate_state_config(13, 21, &watch_possessor, &mut world),
        Ok(Value::Entity(world.controller))
    );
    assert_eq!(
        component.evaluate_state_config(1, 27, &shield_refill, &mut world),
        Ok(Value::Bool(false))
    );
    assert_eq!(
        component.evaluate_state_config(12, 48, &health_present, &mut world),
        Ok(Value::Bool(true))
    );
    assert_eq!(
        component.evaluate_state_config(12, 48, &full_health, &mut world),
        Ok(Value::Bool(true))
    );
    // 0255 st2 follows the throttle comparison for a human player.
    for (forward, enabled) in [(63, false), (64, true)] {
        world.throttles = [0, forward];
        assert_eq!(
            component.evaluate_state_config(5, 2, &sprint_condition, &mut world),
            Ok(Value::Bool(enabled))
        );
    }
    // Both 0254 rifle branches have the same parameterless human condition.
    for condition in rifle_conditions {
        assert_eq!(
            component.evaluate_state_config(11, 10, &condition, &mut world),
            Ok(Value::Bool(false))
        );
    }
    world.vitals.damage(
        75.0,
        crate::vitals::Death {
            time: 0.0,
            frame: FIRST_TICK + 1,
            killer: world.entity + 1,
        },
    );
    component.advance_frame(FIRST_TICK + 1, &mut world).unwrap();
    let damaged = component.snapshot().unwrap();
    assert_eq!(damaged.variables[&253], Value::Float(125.0));
    assert_eq!(damaged.variables[&1732], Value::Float(125.0));
    assert_eq!(damaged.variables[&1737], Value::Float(200.0));
    assert_eq!(damaged.instances[&1].variables[&520], Value::Float(125.0));
    assert_eq!(
        component.evaluate_state_config(12, 48, &full_health, &mut world),
        Ok(Value::Bool(false))
    );
}

#[test]
#[ignore = "requires the separately prepared 104319 hero and graph catalogs"]
fn authored_armour_and_shield_roots_reproduce_the_reference_body_pools() {
    use crate::game_clock::FIRST_TICK;
    use std::path::PathBuf;

    let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let heroes =
        crate::game_heroes::HeroCatalog::load(root.join("data/game_heroes_174.json")).unwrap();
    let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
    let catalog = crate::game_pools::PoolCatalog::from_graphs(&graphs).unwrap();
    for (hero, secondary_state) in [(0x02e0_0000_0000_0007, 3), (0x02e0_0000_0000_0068, 1)] {
        let body = &graphs.bodies()[&heroes.get(hero).unwrap().body];
        let pools = catalog.hero_pools(hero).unwrap();
        let roots = crate::script_init::player_roots(body, &graphs)
            .unwrap()
            .into_iter()
            .filter(|root| root.graph == BODY_GRAPH)
            .collect();
        let health = pools.total();
        let mut world = BodyWorld {
            spatial_frame: None,
            team_member_counts: None,
            room_activity: None,
            script_requests: Default::default(),
            graphs: Arc::clone(&graphs),
            controller: 0xa000_0100,
            entity: 0xa000_0101,
            body_filter: crate::match_scene::team_bit(0).unwrap(),
            position: [0.0; 3],
            eye_position: None,
            movement: Movement::default(),
            throttles: [0; 2],
            mode: PRACTICE_RANGE_MODE,
            hero,
            vitals: Vitals::empty(),
            pool_types_known: true,
            target_tags: body.target_tags.clone(),
            model_hardpoints: None,
            target_tag_effects: TargetTagEffects::default(),
            controller_on_fire: false,
            defenders: Arc::default(),
            sightings: Vec::new(),
            sighting_source: None,
            cinematic_aim: None,
            facing_claims: Default::default(),
            deadeye_scene: DeadeyeScene::default(),
            velocity_changes: Vec::new(),
            scripted_motion: None,
        };
        let component = Component::start(
            Arc::clone(&graphs),
            world.entity,
            crate::script_init::player_entity_variables(body, health, health),
            roots,
            FIRST_TICK,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(!snapshot.diagnostics.iter().any(|item| {
            matches!(item.issue, crate::script_init::Issue::UnsupportedField(field) if field.starts_with("health pool"))
        }));
        let states = &snapshot.instances[&1].states;
        assert_eq!(
            states[&secondary_state].kind,
            crate::script_init::StateKind::HealthPool { pool: Some(1) }
        );
        assert_eq!(
            states[&0].kind,
            crate::script_init::StateKind::HealthPool { pool: Some(2) }
        );
        assert_eq!(world.vitals.component(), pools.vitals().component());
    }
}
