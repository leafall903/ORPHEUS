//! Opt-in inventory and startup probes of the unchanged native owner factory.

use super::*;
use crate::{
    game_instance::TeamMemberCounts,
    script_expr::EvalError,
    script_init::{Diagnostic, InitFault, Issue},
    script_player::OwnerBodyStartupError,
    script_snapshot_wire::{SnapshotError, StateSite},
};
use serde_json::{Value as Json, json};
use sha2::{Digest, Sha256};
use std::{env, io::Write};

const MAX_DIAGNOSTIC_SITES: usize = 128;
const PROBE_TEAM: u8 = 0;
const PROBE_ROSTER: [u8; 1] = [PROBE_TEAM];

const SCENE_INPUTS: &[&str] = &[
    "data/ai_spawners_174.json",
    "data/collision_props_174.json",
    "data/game_graphs_174.json.gz",
    "data/game_heroes_174.json",
    "data/game_movers_174.json",
    "data/game_stats_174.json",
    "data/statescript_codecs_174.json",
];

fn scene_inputs(runtime: &Path) -> BTreeMap<String, Json> {
    let movement_cache = format!(
        "collision/{:04X}.col",
        GameMap::practice_range().map_guid as u32
    );
    SCENE_INPUTS
        .iter()
        .copied()
        .chain(std::iter::once(movement_cache.as_str()))
        .map(|path| {
            let fingerprint = File::open(runtime.join(path)).and_then(|mut file| {
                let mut digest = Sha256::new();
                io::copy(&mut file, &mut digest)?;
                Ok(format!("{:x}", digest.finalize()))
            });
            let row = match fingerprint {
                Ok(sha256) => json!({"sha256": sha256}),
                Err(error) => json!({"error_kind": format!("{:?}", error.kind())}),
            };
            (path.to_owned(), row)
        })
        .collect()
}

fn diagnostic_category(diagnostic: &Diagnostic) -> &'static str {
    if diagnostic.registration_rejected {
        return "registration_rejection";
    }
    match &diagnostic.issue {
        Issue::MissingGraph(_) => "missing_graph",
        Issue::MissingNode(_) => "missing_node",
        Issue::MissingState(_) => "missing_state",
        Issue::UnsupportedAction(_) => "unsupported_action",
        Issue::UnsupportedState(_) => "unsupported_state",
        Issue::UnsupportedMessageRegistration => "unsupported_message_registration",
        Issue::UnsupportedField(_) => "unsupported_field",
        Issue::EntityCreation(_) => "entity_creation",
        Issue::InvalidSubScript => "invalid_subscript",
        Issue::Expression(EvalError::UnsupportedConfig(_)) => "unsupported_config",
        Issue::Expression(EvalError::UnavailableEntityVariable { .. }) => {
            "entity_variable_unavailable"
        }
        Issue::Expression(EvalError::UnavailableBodyHardpoint { .. }) => {
            "body_hardpoint_unavailable"
        }
        Issue::Expression(_) => "expression",
        Issue::ObservationUnavailable => "observation_unavailable",
    }
}

fn node_class(graphs: &GraphStore, graph: u32, node: Option<u16>) -> Option<&str> {
    let class = &graphs.graph(graph)?.node(node?)?.class;
    (class.len() <= 128).then_some(class.as_str())
}

fn diagnostic_operand_class(diagnostic: &Diagnostic) -> Option<&str> {
    let Issue::Expression(EvalError::UnsupportedConfig(class)) = &diagnostic.issue else {
        return None;
    };
    let (first, rest) = class.as_bytes().split_first()?;
    (class.len() <= 128
        && (first.is_ascii_alphabetic() || *first == b'_')
        && rest
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || *byte == b'_'))
    .then_some(class.as_str())
}

fn diagnostic_sites(graphs: &GraphStore, diagnostics: &[Diagnostic]) -> (Vec<Json>, bool) {
    let mut sites = BTreeSet::new();
    let mut truncated = false;
    for diagnostic in diagnostics {
        sites.insert((
            diagnostic.site.graph,
            diagnostic.site.node,
            diagnostic_category(diagnostic),
            diagnostic.server_only,
            diagnostic_operand_class(diagnostic),
        ));
        if sites.len() > MAX_DIAGNOSTIC_SITES {
            sites.pop_last();
            truncated = true;
        }
    }
    let rows = sites
        .into_iter()
        .map(|(graph, node, category, server_only, operand_class)| {
            json!({
                "graph": graph, "node": node, "class": node_class(graphs, graph, node),
                "category": category, "server_only": server_only,
                "operand_class": operand_class,
            })
        })
        .collect();
    (rows, truncated)
}

fn simple_failure(stage: &'static str, category: &'static str) -> Json {
    json!({"stage": stage, "category": category})
}

fn io_failure(stage: &'static str, category: &'static str, error: &io::Error) -> Json {
    json!({"stage": stage, "category": category, "cause_error_kind": format!("{:?}", error.kind())})
}

fn vm_failure(stage: &'static str, fault: &InitFault) -> Json {
    let category = match fault {
        InitFault::InvalidBudget => "invalid_budget",
        InitFault::InvalidRoot(_) => "invalid_root",
        InitFault::InstanceIdExhausted => "instance_id_exhausted",
        InitFault::MissingPlayerGraph(_) => "missing_player_graph",
        InitFault::StatusScript { .. } => "status_script",
        InitFault::RootBinding { .. } => "root_binding",
        InitFault::WorkBudget { .. } => "work_budget",
        InitFault::EventBudget { .. } => "event_budget",
        InitFault::PendingEvents { .. } => "pending_events",
        InitFault::PendingShots => "pending_shots",
        InitFault::FrameOrder { .. } => "frame_order",
        InitFault::Halted => "halted",
        InitFault::PlugDepth => "plug_depth",
    };
    let mut failure = simple_failure(stage, category);
    if let InitFault::MissingPlayerGraph(graph) | InitFault::RootBinding { graph, .. } = fault {
        failure["graph"] = json!(graph);
    }
    failure
}

fn snapshot_failure(graphs: &GraphStore, stage: &'static str, error: &SnapshotError) -> Json {
    let (category, site) = match error {
        SnapshotError::Incomplete { diagnostics, .. } => {
            let (sites, truncated) = diagnostic_sites(graphs, diagnostics);
            return json!({
                "stage": stage, "category": "incomplete", "diagnostics": sites,
                "diagnostics_truncated": truncated,
            });
        }
        SnapshotError::MissingGraph { graph, .. } => {
            return json!({"stage": stage, "category": "missing_graph", "graph": graph});
        }
        SnapshotError::MissingStateIndex { graph, node, .. } => {
            return json!({
                "stage": stage, "category": "missing_state_index", "graph": graph,
                "node": node, "class": node_class(graphs, *graph, Some(*node)),
            });
        }
        SnapshotError::MissingState(site) => ("missing_state", Some(site)),
        SnapshotError::UnsupportedState { site, .. } => ("unsupported_state", Some(site)),
        SnapshotError::UnsupportedTimer { site, .. } => ("unsupported_timer", Some(site)),
        SnapshotError::Payload { site, .. } => ("payload", Some(site)),
        SnapshotError::MissingBoundValue { .. } => ("missing_bound_value", None),
        SnapshotError::UnsupportedMap { .. } => ("unsupported_map", None),
        SnapshotError::InvalidBinding { .. } => ("invalid_binding", None),
        SnapshotError::DifferentStream => ("different_stream", None),
        SnapshotError::ReboundInstance(_) => ("rebound_instance", None),
        SnapshotError::FrameTime => ("frame_time", None),
        SnapshotError::Wire(_) => ("wire", None),
        SnapshotError::Stream(_) => ("stream", None),
    };
    let mut failure = simple_failure(stage, category);
    if let Some(StateSite { graph, state, .. }) = site {
        let node = graphs
            .graph(*graph)
            .and_then(|definition| definition.state(usize::from(*state)))
            .map(|node| node.pos);
        failure["graph"] = json!(graph);
        failure["state"] = json!(state);
        failure["node"] = json!(node);
        failure["class"] = json!(node_class(graphs, *graph, node));
    }
    failure
}

fn startup_failure(error: &OwnerBodyStartupError) -> Json {
    use OwnerBodyStartupError as Start;
    match error {
        Start::Clock => simple_failure("start", "clock"),
        Start::VmInitialize(fault) => vm_failure("vm_initialize", fault),
        Start::FirstFrame(fault) => vm_failure("first_frame", fault),
        Start::MovementModifiers(fault) => vm_failure("movement_modifiers", fault),
        Start::ProjectileLeases(error) => io_failure("start", "projectile_leases", error),
        Start::ChargeAdmission(error) => io_failure("start", "charge_admission", error),
    }
}

fn preparation_failure(graphs: &GraphStore, error: &SceneOwnerPreparationError) -> Json {
    use OwnerBodyPreparationError as Body;
    use SceneOwnerPreparationError as Scene;
    match error {
        Scene::Codecs => simple_failure("scene_preparation", "codecs"),
        Scene::ControllerFlag => simple_failure("scene_preparation", "controller_flag"),
        Scene::SpawnMover(error) => io_failure("scene_preparation", "spawn_mover", error),
        Scene::TeamFilter(error) => io_failure("scene_preparation", "team_filter", error),
        Scene::SpatialFrame(_) => simple_failure("scene_preparation", "spatial_frame"),
        Scene::Body(error) => match error.as_ref() {
            Body::MissingBody => simple_failure("body_preparation", "missing_body"),
            Body::RootLayout(fault) => vm_failure("root_layout", fault),
            Body::ModeRootId => simple_failure("body_preparation", "mode_root_id"),
            Body::MissingModeRoot => simple_failure("body_preparation", "missing_mode_root"),
            Body::AuthoredChargeRoot(_) => {
                simple_failure("body_preparation", "authored_charge_root")
            }
            Body::CompatibilityChargeRoot(_) => {
                simple_failure("body_preparation", "compatibility_charge_root")
            }
            Body::Start(error) => startup_failure(error),
            Body::Projection(error) => snapshot_failure(graphs, "projection", error),
            Body::Encoding(error) => snapshot_failure(graphs, "encoding", error),
        },
    }
}

fn runtime_result(graphs: &GraphStore, runtime: &dyn OwnerBodyRuntime) -> Json {
    let Some(snapshot) = runtime.snapshot() else {
        return json!({"status": "snapshot_unavailable"});
    };
    let (sites, truncated) = diagnostic_sites(graphs, &snapshot.diagnostics);
    let observations = snapshot.node_observations().map(|observed| {
        let sites: Vec<_> = observed.sites().map(|(graph, node, client_scope_skipped)| {
            json!({"graph": graph, "node": node, "client_scope_skipped": client_scope_skipped})
        }).collect();
        json!({"truncated": observed.truncated(), "sites": sites})
    });
    json!({
        "status": "started",
        "initialization_complete": snapshot.initialization_complete(),
        "instances": snapshot.instances.len(),
        "diagnostics": sites,
        "diagnostics_truncated": truncated,
        "observations": observations,
    })
}

fn actor_row(assets: &SceneAssets, scene: &PreparedScene, hero: &Hero) -> Json {
    let body = assets.graphs.bodies().get(&hero.body);
    let mover = scene.player_spawn_mover(hero.body, PROBE_TEAM);
    let body_status = match body {
        None => "missing",
        Some(body) if body.hero.as_deref().and_then(parse_guid) == Some(hero.guid) => "matched",
        Some(_) => "catalog_mismatch",
    };
    let mut roots = BTreeSet::new();
    if let Some(body) = body {
        roots.extend(body.manager);
        roots.extend(body.weapons.iter().copied().flatten());
        roots.extend(
            body.graphs
                .iter()
                .chain(&body.server_graphs)
                .filter_map(|binding| binding.graph),
        );
    }
    let missing_roots: Vec<_> = roots
        .iter()
        .filter(|&&graph| assets.graphs.graph(graph).is_none())
        .collect();
    let prerequisites = json!({
        "body_status": body_status,
        "direct_graph_roots": roots,
        "missing_direct_graph_roots": missing_roots,
        "target_tags_present": body.is_some_and(|body| body.target_tags.is_some()),
        "authored_mover_present": assets.movers.authored_for_body(hero.body).is_some(),
        "codec_table_present": assets.codecs.is_some(),
        "controller_on_fire_present": initial_on_fire(&scene.initial_board).is_some(),
        "spawn_error_kind": mover.as_ref().err().map(|error| format!("{:?}", error.kind())),
    });
    let spawn = mover.ok().map(|mover| OwnerBodySpawn {
        slot: 0,
        controller: OWNER_PROBE_CONTROLLER,
        entity: 0xa000_0101,
        body_filter: team_bit(PROBE_TEAM).unwrap(),
        frame: FIRST_TICK,
        pose: mover.pose(),
        eye_position: script_eye_position(hero.guid, &mover),
        skin: 0,
        quanta: RETAIL_QUANTA,
    });
    let admission = match spawn.map(|spawn| scene.owner_body_runtime(hero, spawn)) {
        None => json!({"status": "not_probed"}),
        Some(Ok(None)) => json!({"status": "not_admitted"}),
        Some(Ok(Some(runtime))) => runtime_result(&assets.graphs, runtime.as_ref()),
        Some(Err(error)) => {
            json!({"status": "factory_error", "error_kind": format!("{:?}", error.kind())})
        }
    };
    // Exercise the same preparation gate without admitting the actor to the scene.
    let setup = scene.owner_body_setup(OWNER_PROBE_CONTROLLER, 0);
    let bootstrap = match assets.prepare_owner_factory(scene, hero, setup.clone()) {
        Err(error) => json!({
            "status": "factory_error", "error_kind": "Unsupported",
            "failure": preparation_failure(&assets.graphs, &error),
        }),
        Ok(factory) => match spawn.map(|spawn| factory.start_with_setup(spawn, setup)) {
            None => json!({"status": "not_probed"}),
            Some(Ok(runtime)) => runtime_result(&assets.graphs, &runtime),
            Some(Err(error)) => json!({
                "status": "factory_error", "error_kind": format!("{:?}", error.kind()),
                "failure": simple_failure("start", "io"),
            }),
        },
    };
    json!({
        "hero": format!("0x{:016X}", hero.guid),
        "body": format!("0x{:016X}", hero.body),
        "prerequisites": prerequisites,
        "admission": admission,
        "bootstrap": bootstrap,
    })
}

fn measure_scene(runtime: &Path) -> Result<(Json, Vec<Json>), (&'static str, io::Error)> {
    let assets = SceneAssets::load(runtime).map_err(|error| ("asset_load", error))?;
    let mut scene = assets
        .prepare(GameMap::practice_range())
        .map_err(|error| ("scene_prepare", error))?;
    let bootstrap_counts = TeamMemberCounts::from_teams(false, PROBE_ROSTER);
    scene
        .simulation_mut()
        .set_team_member_counts(bootstrap_counts);
    scene
        .simulation_mut()
        .prepare_spatial_frame(FIRST_TICK)
        .map_err(|error| ("spatial_frame", io::Error::other(error)))?;
    let mut heroes: Vec<_> = assets.heroes().heroes().collect();
    heroes.sort_unstable_by_key(|hero| hero.guid);
    let rows = heroes
        .into_iter()
        .map(|hero| actor_row(&assets, &scene, hero))
        .collect();
    let owner_startup_spatial = scene
        .owner_body_setup(OWNER_PROBE_CONTROLLER, 0)
        .spatial_frame
        .map(|frame| {
            json!({
                "living_candidates": frame.candidates().len(),
                "entity_facts": frame.entity_facts().is_some(),
            })
        });
    Ok((
        json!({
            "status": "prepared",
            "movement_ready": scene.movement_ready(),
            "range_bots": scene.bots().unwrap().len(),
            "scripted_range_bots": scene.scripted_bots(),
            "owner_factory_present": scene.owner_body_factory.is_some(),
            "owner_startup_spatial": owner_startup_spatial,
        }),
        rows,
    ))
}

#[test]
fn startup_diagnostics_keep_distinct_operand_classes_and_omit_error_text() {
    let graphs =
        GraphStore::from_json(br#"{"version":1,"graphs":[],"bodies":{},"missing":[]}"#).unwrap();
    let site = script_init::Site {
        entity: 417,
        instance: 6,
        graph: 720,
        node: Some(3),
    };
    let row = |error| Diagnostic {
        site,
        issue: Issue::Expression(error),
        registration_rejected: false,
        server_only: false,
    };
    let unsupported = |class: &str| row(EvalError::UnsupportedConfig(class.into()));
    let freeform = "synthetic detail with spaces: internal payload";
    let overlong = "A".repeat(129);
    let diagnostics = vec![
        unsupported("SyntheticOperand"),
        unsupported(freeform),
        unsupported("_SyntheticOperand2"),
        unsupported("SyntheticOperand"),
        unsupported("9BadPrefix"),
        unsupported(&overlong),
        row(EvalError::MalformedConfig("synthetic malformed detail")),
        row(EvalError::UnavailableEntityVariable {
            entity: 81_000_551,
            id: 91_000_557,
        }),
        row(EvalError::UnavailableBodyHardpoint {
            entity: 81_000_559,
            hardpoint: 0x0dc0_0000_00ad_b917,
        }),
    ];
    let report = snapshot_failure(
        &graphs,
        "projection",
        &SnapshotError::Incomplete {
            entity: site.entity,
            diagnostics,
        },
    );
    let entries = report["diagnostics"].as_array().unwrap();
    assert_eq!(entries.len(), 6);
    assert!(
        entries
            .iter()
            .all(|entry| entry["graph"] == 720 && entry["node"] == 3)
    );
    assert_eq!(entries[0]["category"], "body_hardpoint_unavailable");
    assert_eq!(entries[0]["operand_class"], Json::Null);
    assert_eq!(entries[1]["category"], "entity_variable_unavailable");
    assert_eq!(entries[1]["operand_class"], Json::Null);
    assert_eq!(entries[2]["category"], "expression");
    assert_eq!(entries[2]["operand_class"], Json::Null);
    let operand_classes: Vec<_> = entries[3..]
        .iter()
        .map(|entry| entry["operand_class"].as_str())
        .collect();
    assert_eq!(
        operand_classes,
        [None, Some("SyntheticOperand"), Some("_SyntheticOperand2")]
    );
    let serialized = report.to_string();
    assert!(!serialized.contains(freeform));
    assert!(!serialized.contains("synthetic malformed detail"));
    assert!(!serialized.contains("9BadPrefix"));
    assert!(!serialized.contains(overlong.as_str()));
    assert!(!serialized.contains("81000551"));
    assert!(!serialized.contains("91000557"));
    assert!(!serialized.contains("81000559"));
    assert!(!serialized.contains("ADB917"));
}

#[test]
#[ignore = "requires a private prepared runtime and opt-in report destination"]
fn prepared_practice_actor_startup_matrix() {
    let Some(path) = env::var_os("ORPHEUS_ACTOR_STARTUP_REPORT").map(PathBuf::from) else {
        return;
    };
    assert!(!path.try_exists().unwrap(), "startup report must be new");
    let revision = env::var("ORPHEUS_ACTOR_STARTUP_REVISION")
        .expect("startup report requires its source revision");
    assert!(
        revision.len() == 40 && revision.bytes().all(|b| b.is_ascii_hexdigit()),
        "startup report source revision must be a full Git SHA-1"
    );
    let runtime = PathBuf::from(env::var_os("ORPHEUS_RUNTIME_DIR").expect("prepared runtime"));
    let inputs = scene_inputs(&runtime);
    let (scene, actors) = match measure_scene(&runtime) {
        Ok(measured) => measured,
        Err((stage, error)) => (
            json!({"status": "unavailable", "stage": stage, "error_kind": format!("{:?}", error.kind())}),
            Vec::new(),
        ),
    };
    assert_eq!(
        inputs,
        scene_inputs(&runtime),
        "startup report inputs changed"
    );
    let report = json!({
        "schema_version": 2,
        "scenario": "practice-owner-startup-v2",
        "source_revision": revision.to_ascii_lowercase(),
        "inputs": inputs,
        "scene": scene,
        "probe_roster": {
            "kind": "synthetic_admitted_players",
            "free_for_all": false,
            "team_slots": PROBE_ROSTER,
        },
        "actors": actors,
    });
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let mut output = tempfile::NamedTempFile::new_in(parent).unwrap();
    serde_json::to_writer(output.as_file_mut(), &report).unwrap();
    writeln!(output).unwrap();
    output
        .persist_noclobber(path)
        .expect("startup report must be new");
}
