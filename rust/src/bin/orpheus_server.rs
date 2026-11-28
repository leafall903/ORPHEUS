use std::{env, ffi::OsString, io, path::PathBuf, process::ExitCode};

use orpheus::{
    accounts::import_python_profiles,
    runtime::check_practice_runtime,
    server::{Server, ServerConfig},
};

const USAGE: &str = "Usage: orpheus_server --data-dir <directory> [--runtime <directory>]
       [--account <name>] [--second-account <name>] [--lobby-port <port>] [--game-port <port>]
       [--stall-port <port>] [--rpc-port <port>] [--second-rpc-port <port>]
       [--debug-port <port>] [--debug-control]
       [--junkenstein-validation <local-json>] [--ai-script <lua-file>] [--mod-script <lua-file>]
       orpheus_server --check-runtime [--runtime <directory>]
       orpheus_server --check-runtime-graphs [--runtime <directory>]
       orpheus_server --import-python-profiles <profiles> --data-dir <directory>

The native 1.74 server listens on loopback. Use an authorized base runtime
directory or set ORPHEUS_RUNTIME_DIR. --check-runtime lists missing inputs
without starting listeners. --second-account enables a second relay RPC login
on the same lobby (port 21120 by default). Import profiles with the destination
server stopped. The import keeps existing Rust saves in that directory.
--debug-port enables the read-only inspector
in a binary built with --features inspector (disabled by default).
--debug-control enables opt-in local room pause/resume/step POST commands
(requires --features inspector and --debug-port; disabled by default).
--junkenstein-validation loads explicit local Normal Cassidy acceptance settings.
--ai-script overrides StateScript patrol decisions with local Lua and enables
the 'reload' command (requires --features lua-debug).
--mod-script loads a sandboxed Lua 5.4 Workshop mod and enables the 'reload'
command (requires --features lua-mods).
Press Enter in the terminal to stop.";

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("orpheus_server: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> io::Result<()> {
    let mut args = env::args_os().skip(1);
    let mut check_runtime = false;
    let mut check_graphs = false;
    let mut import_profiles = None;
    let mut config = ServerConfig::new(
        env::var_os("ORPHEUS_RUNTIME_DIR").map_or_else(PathBuf::new, PathBuf::from),
        PathBuf::new(),
    );
    while let Some(arg) = args.next() {
        if arg == "--help" || arg == "-h" {
            println!("{USAGE}");
            return Ok(());
        }
        if arg == "--check-runtime" || arg == "--check-runtime-graphs" {
            check_runtime = true;
            check_graphs |= arg == "--check-runtime-graphs";
            continue;
        }
        if arg == "--debug-control" {
            config.debug_control = true;
            continue;
        }
        let value = args
            .next()
            .ok_or_else(|| usage_error("missing option value"))?;
        match arg.to_str() {
            Some("--runtime") => config.runtime = PathBuf::from(value),
            Some("--data-dir") => config.data = PathBuf::from(value),
            Some("--import-python-profiles") => import_profiles = Some(PathBuf::from(value)),
            Some("--account") => {
                config.account = Some(
                    value
                        .into_string()
                        .map_err(|_| usage_error("invalid account name"))?,
                );
            }
            Some("--second-account") => {
                config.second_account = Some(
                    value
                        .into_string()
                        .map_err(|_| usage_error("invalid second account name"))?,
                );
            }
            Some("--lobby-port") => config.lobby_port = port(value)?,
            Some("--game-port") => config.game_port = port(value)?,
            Some("--stall-port") => config.stall_port = port(value)?,
            Some("--rpc-port") => config.rpc_port = port(value)?,
            Some("--second-rpc-port") => config.second_rpc_port = port(value)?,
            Some("--debug-port") => config.debug_port = Some(port(value)?),
            Some("--junkenstein-validation") => {
                config.junkenstein_validation = Some(PathBuf::from(value));
            }
            Some("--ai-script") => {
                if !cfg!(feature = "lua-debug") {
                    return Err(usage_error(
                        "--ai-script requires a build with --features lua-debug",
                    ));
                }
                config.ai_script = Some(PathBuf::from(value));
            }
            Some("--mod-script") => {
                if !cfg!(feature = "lua-mods") {
                    return Err(usage_error(
                        "--mod-script requires a build with --features lua-mods",
                    ));
                }
                config.mod_script = Some(PathBuf::from(value));
            }
            _ => return Err(usage_error("unknown option")),
        }
    }
    if let Some(source) = import_profiles {
        if check_runtime || config.data.as_os_str().is_empty() {
            return Err(usage_error(
                "profile import requires --data-dir and cannot check a runtime",
            ));
        }
        let (imported, skipped) = import_python_profiles(&source, &config.data)?;
        println!("Imported {imported} profiles. Kept {skipped} existing Rust profiles.");
        return Ok(());
    }
    if !check_runtime && config.debug_port.is_some() && !cfg!(feature = "inspector") {
        return Err(usage_error(
            "--debug-port requires a build with --features inspector",
        ));
    }
    if !check_runtime && config.debug_control && !cfg!(feature = "inspector") {
        return Err(usage_error(
            "--debug-control requires a build with --features inspector",
        ));
    }
    if !check_runtime && config.debug_control && config.debug_port.is_none() {
        return Err(usage_error("--debug-control requires --debug-port"));
    }
    if config.runtime.as_os_str().is_empty() {
        return Err(usage_error("use --runtime or set ORPHEUS_RUNTIME_DIR"));
    }
    if check_runtime {
        check_practice_runtime(&config.runtime)?;
        if check_graphs {
            orpheus::script_graph::GraphStore::load(
                config.runtime.join("data/game_graphs_174.json.gz"),
            )?;
            println!("StateScript graph loader accepted the runtime bundle.");
        }
        println!("Runtime file presence and signing input length checks passed.");
        return Ok(());
    }
    if config.data.as_os_str().is_empty() {
        return Err(usage_error(
            "--data-dir is required when starting the server",
        ));
    }
    let ai_script_configured = config.ai_script.is_some();
    let mod_script_configured = config.mod_script.is_some();
    let debug_control_enabled = config.debug_control;
    let mut server = Server::start(config)?;
    println!(
        "ORPHEUS native lobby {} · game {} · Battle.net {} / {}",
        server.lobby_addr(),
        server.game_addr(),
        server.stall_addr(),
        server.rpc_addr(),
    );
    if let Some(address) = server.second_rpc_addr() {
        println!("Second local account relay RPC {address}");
    }
    if let Some(address) = server.debug_addr() {
        if debug_control_enabled {
            println!("Local room-control inspector http://{address}/");
        } else {
            println!("Read-only inspector http://{address}/");
        }
    }
    if ai_script_configured {
        let status = server.ai_policy_status()?;
        println!(
            "AI policy gen {} (abi v{}, sha256 {}).",
            status.generation, status.abi_version, status.digest
        );
    }
    #[cfg(feature = "lua-mods")]
    if let Some(status) = server.mod_status() {
        println!(
            "Lua Mod '{}' v{} by {} (abi v{}, gen {}, sha256 {}).",
            status.name,
            status.version,
            status.author,
            status.abi_version,
            status.generation,
            status.digest
        );
    }
    if ai_script_configured || mod_script_configured {
        println!("Type 'reload' + Enter to reload Lua scripts, or press Enter to stop.");
        loop {
            let mut line = String::new();
            if io::stdin().read_line(&mut line)? == 0 || line.trim().is_empty() {
                break;
            }
            if line.trim() == "reload" {
                if ai_script_configured {
                    match server.reload_ai_script() {
                        Ok(report) => println!(
                            "AI policy gen {} (abi v{}, sha256 {}, active rooms {}, changed {}).",
                            report.generation,
                            report.abi_version,
                            report.digest,
                            report.active_rooms,
                            report.changed
                        ),
                        Err(error) => {
                            eprintln!("orpheus_server: AI reload failed (rolled back): {error}");
                        }
                    }
                }
                #[cfg(feature = "lua-mods")]
                if mod_script_configured {
                    match server.reload_mod_script() {
                        Ok(Some(status)) => println!(
                            "Lua Mod '{}' gen {} (sha256 {}, changed {}).",
                            status.name, status.generation, status.digest, status.changed
                        ),
                        Ok(None) => {}
                        Err(error) => {
                            eprintln!("orpheus_server: Mod reload failed (rolled back): {error}");
                        }
                    }
                }
            } else {
                println!(
                    "Unknown command. Type 'reload' + Enter to reload Lua scripts, or press Enter to stop."
                );
            }
        }
    } else {
        println!("Press Enter to stop.");
        io::stdin().read_line(&mut String::new())?;
    }
    server.stop()
}

fn port(value: OsString) -> io::Result<u16> {
    value
        .to_str()
        .and_then(|value| value.parse().ok())
        .ok_or_else(|| usage_error("invalid port"))
}

fn usage_error(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, format!("{message}\n{USAGE}"))
}
