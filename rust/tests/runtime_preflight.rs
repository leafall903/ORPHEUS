use std::{fs, path::Path, process::Command};

use orpheus::{
    runtime::{BNET_DESCRIPTORS, LOBBY_RUNTIME_DATA, MATCH_RUNTIME_DATA},
    server::{Server, ServerConfig},
};

fn check(root: &Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_orpheus_server"))
        .arg("--check-runtime")
        .arg("--runtime")
        .arg(root)
        .output()
        .unwrap()
}

fn seed_data_files(root: &Path) {
    let data = root.join("data");
    fs::create_dir_all(&data).unwrap();
    for path in LOBBY_RUNTIME_DATA.iter().chain(MATCH_RUNTIME_DATA) {
        fs::write(data.join(path), []).unwrap();
    }
}

#[test]
fn check_runtime_groups_missing_inputs_without_starting_the_server() {
    let dir = tempfile::tempdir().unwrap();
    let result = check(dir.path());
    assert!(!result.status.success());
    assert!(result.stdout.is_empty());
    let error = String::from_utf8(result.stderr).unwrap();
    for text in [
        "Tool outputs from authorized local inputs",
        "Bring prepared research data",
        "Blocked without restricted local inputs",
        "collision/0688.col",
        BNET_DESCRIPTORS,
    ] {
        assert!(error.contains(text), "missing diagnostic for {text}");
    }
}

#[test]
fn check_runtime_checks_signing_length_without_reading_runtime_payloads() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    for path in ["bnet", "collision"] {
        fs::create_dir(root.join(path)).unwrap();
    }
    seed_data_files(root);
    for path in [BNET_DESCRIPTORS, "collision/0688.col"] {
        fs::write(root.join(path), []).unwrap();
    }

    let malformed = check(root);
    assert!(!malformed.status.success());
    assert!(
        String::from_utf8(malformed.stderr)
            .unwrap()
            .contains("Expected exactly 64 bytes.")
    );

    fs::write(root.join("data/lobby_state_blob.key"), [0; 64]).unwrap();
    let valid = check(root);
    assert!(valid.status.success());
    assert!(
        String::from_utf8(valid.stdout)
            .unwrap()
            .contains("Runtime file presence and signing input length checks passed.")
    );
}

#[test]
fn server_loads_the_graph_bundle_enriched_by_the_runtime_packer() {
    use std::collections::BTreeSet;

    use flate2::{Compression, write::GzEncoder};
    use orpheus::{runtime::prepare_with_target_tags, script_graph::GraphStore};
    use serde_json::json;

    let dir = tempfile::tempdir().unwrap();
    let source = dir.path().join("source");
    seed_data_files(&source);
    for path in ["cache/collision", "ow174/bnet", "003"] {
        fs::create_dir_all(source.join(path)).unwrap();
    }
    fs::write(source.join("cache/collision/0688.col"), []).unwrap();
    fs::write(source.join("ow174").join(BNET_DESCRIPTORS), []).unwrap();
    let graph_bundle = "data/game_graphs_174.json.gz";
    let mut encoder = GzEncoder::new(
        fs::File::create(source.join(graph_bundle)).unwrap(),
        Compression::default(),
    );
    serde_json::to_writer(
        &mut encoder,
        &json!({
            "version":1,"graphs":[],"missing":[],
            "bodies":{"0x0400000000000001":{"name":"Fixture","hero":null,"graphs":[],
                "manager":null,"weapons":[]}}
        }),
    )
    .unwrap();
    encoder.finish().unwrap();
    fs::write(
        source.join("003/000000000001.003.xml"),
        br#"<STUEntityDefinition xmlns:d="https://yretenai.com/dragonml/v1">
            <d:map d:name="m_componentMap">
                <STUTargetTagComponent>
                    <STUTargetTagComponent d:name="dragon:value">
                        <d:array d:name="m_targetTags">
                            <d:ref GUID="000000000001.06C" Target="STUTargetTag"/>
                        </d:array>
                    </STUTargetTagComponent>
                </STUTargetTagComponent>
            </d:map>
        </STUEntityDefinition>"#,
    )
    .unwrap();

    let pack = prepare_with_target_tags(
        &source,
        &dir.path().join("pack"),
        &["0688".into()],
        None,
        Some(&source.join("003")),
    )
    .unwrap();
    let graphs = GraphStore::load(pack.join(graph_bundle)).unwrap();
    assert_eq!(
        graphs.bodies()[&0x0400_0000_0000_0001].target_tags,
        Some(BTreeSet::from([0x0d60_0000_0000_0001]))
    );
}

#[test]
fn help_describes_the_inspector_feature_without_runtime_inputs() {
    let output = Command::new(env!("CARGO_BIN_EXE_orpheus_server"))
        .env_remove("ORPHEUS_RUNTIME_DIR")
        .arg("--help")
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let help = String::from_utf8(output.stdout).unwrap();
    assert!(help.contains("--features inspector"));
    assert!(help.contains("--debug-control"));
    assert!(help.contains("disabled by default"));
}

#[test]
fn normal_server_options_reach_runtime_validation_before_writing_state() {
    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("local-state");
    let mut command = Command::new(env!("CARGO_BIN_EXE_orpheus_server"));
    command
        .arg("--runtime")
        .arg(dir.path())
        .arg("--data-dir")
        .arg(&state)
        .args([
            "--account",
            "rust",
            "--lobby-port",
            "0",
            "--game-port",
            "0",
            "--stall-port",
            "0",
            "--rpc-port",
            "0",
        ]);
    if cfg!(feature = "inspector") {
        command.args(["--debug-port", "0"]);
    }
    let output = command.output().unwrap();
    assert!(!output.status.success());
    let error = String::from_utf8(output.stderr).unwrap();
    assert!(
        error.contains("Tool outputs from authorized local inputs"),
        "{error}"
    );
    assert!(!state.exists());
}

#[test]
fn runtime_preflight_uses_the_environment_without_enabling_the_inspector() {
    let dir = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_orpheus_server"))
        .env("ORPHEUS_RUNTIME_DIR", dir.path())
        .args(["--check-runtime", "--debug-port", "0"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let error = String::from_utf8(output.stderr).unwrap();
    assert!(error.contains("collision/0688.col"), "{error}");
    assert!(!error.contains("requires a build"), "{error}");
}

#[test]
#[cfg(not(feature = "inspector"))]
fn debug_port_requires_the_inspector_feature_before_startup() {
    let output = Command::new(env!("CARGO_BIN_EXE_orpheus_server"))
        .env_remove("ORPHEUS_RUNTIME_DIR")
        .args(["--debug-port", "0"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .contains("--debug-port requires a build with --features inspector")
    );

    let control_output = Command::new(env!("CARGO_BIN_EXE_orpheus_server"))
        .env_remove("ORPHEUS_RUNTIME_DIR")
        .args(["--debug-control"])
        .output()
        .unwrap();
    assert!(!control_output.status.success());
    assert!(control_output.stdout.is_empty());
    assert!(
        String::from_utf8(control_output.stderr)
            .unwrap()
            .contains("--debug-control requires a build with --features inspector")
    );

    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("local-state");
    let mut config = ServerConfig::new(dir.path().to_owned(), state.clone());
    config.debug_port = Some(0);
    let error = Server::start(config).err().expect("feature disabled");
    assert_eq!(error.kind(), std::io::ErrorKind::Unsupported);
    assert!(error.to_string().contains("--features inspector"));
    assert!(!state.exists());

    let mut control_config = ServerConfig::new(dir.path().to_owned(), state.clone());
    control_config.debug_control = true;
    let control_error = Server::start(control_config)
        .err()
        .expect("control feature disabled");
    assert_eq!(control_error.kind(), std::io::ErrorKind::Unsupported);
    assert!(control_error.to_string().contains("--features inspector"));
    assert!(!state.exists());
}

#[test]
#[cfg(feature = "inspector")]
fn debug_control_requires_debug_port_before_startup() {
    let output = Command::new(env!("CARGO_BIN_EXE_orpheus_server"))
        .env_remove("ORPHEUS_RUNTIME_DIR")
        .args(["--debug-control"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .contains("--debug-control requires --debug-port")
    );

    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("local-state");
    let mut config = ServerConfig::new(dir.path().to_owned(), state.clone());
    config.debug_control = true;
    let error = Server::start(config).err().expect("missing debug port");
    assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
    assert!(
        error
            .to_string()
            .contains("--debug-control requires --debug-port")
    );
    assert!(!state.exists());
}
