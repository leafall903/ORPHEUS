# ORPHEUS

[简体中文](README.zh-CN.md)

[Discord](https://discord.gg/tqukqjAk7)

ORPHEUS is an open-source server reimplementation for **Overwatch 1 retail 1.74.0.0.104319**. It is an independent, unofficial research and game preservation project. The long-term goal is to reproduce the server behavior and client-facing events for this build across its heroes, maps and modes. The server is still in development, with Soldier: 76 in the Practice Range as the first end-to-end target.

[Test skills in the local Practice Range](#local-practice-range) · [Join an existing server](#join-an-existing-server)

[Contribute](CONTRIBUTING.md) · [Roadmap](docs/ROADMAP.md) · [Rust branch](https://github.com/leafall903/ORPHEUS/tree/rust) · [Verification record](docs/STATE.md) · [Changelog](CHANGELOG.md) · [Open issues](https://github.com/leafall903/ORPHEUS/issues)

## Run locally

The `main` branch contains the community-maintained Python server and Windows launcher. Native server development continues on the separate `rust` branch.

### Local Practice Range

Each tester runs ORPHEUS and the original client on the same Windows PC. Install 64-bit Python 3.12 and use your authorized **1.74.0.0.104319** installation and prepared Practice Range runtime. [Download the Python source ZIP](https://github.com/leafall903/ORPHEUS/archive/refs/heads/main.zip)

#### Place and check the runtime

Extract ORPHEUS and put the prepared pack under `runtime-data/` in the extracted checkout. Put `data/`, `bnet/` and `collision/` directly inside `runtime-data/`.

```text
ORPHEUS/
  runtime-data/
    manifest.json
    data/
    bnet/descriptors.pb
    collision/0688.col
```

ORPHEUS detects this directory when it contains `manifest.json` or `data/`. An explicit `ORPHEUS_RUNTIME_DIR` takes precedence, and the legacy checkout layout remains the fallback. From the ORPHEUS directory, run the startup preflight. It lists missing startup inputs and checks the signing input length. Map collision is checked when a scene loads.

```powershell
.\scripts\START.bat --check-runtime
```

#### Build the Windows relay once

If you need a local `relay/owwfd_relay.dll` build, install the x64 MSVC Build Tools. Open the x64 Native Tools Command Prompt and run this from the ORPHEUS directory.

```bat
.\relay\build.bat
```

#### Launch the game and test a skill

Pass the game installation directory or its `_retail_\Overwatch.exe` to the launcher. With no path argument, the launcher uses its saved client path or opens a file picker.

```powershell
.\scripts\START_LOCAL.cmd --game-exe 'C:\path\to\Overwatch'
```

The launcher creates a Python environment if needed and remembers the selected installation for future launches and collision preparation. Enter the Practice Range, pick the hero you are testing, and record the exact input and outcome for one skill at a time. [Original-client verification](CONTRIBUTING.md#original-client-verification)

### Separate deployment data

A complete authorized runtime includes protocol data, signing input, gameplay graphs and map collision. The runtime guide lists the source and preparation route for each required file. Preparing the entire runtime from a game folder alone remains open. [Runtime guide](data/README.md)

- `tools/prepare_client_catalogs.py` generates four catalog JSON files locally from the target game installation with pinned DataTool. [Generate client catalogs](data/README.md#generate-client-catalogs)
- `tools/prepare_server_runtime.py` assembles the runtime and writes a manifest. Optional game and DataTool arguments generate missing catalogs, gameplay data and map caches from local inputs. The remaining authorized files must be ready in the source checkout. [Generate missing data while packing](data/README.md#generate-missing-client-data-while-packing)

For a new Practice Range pack in the default location, run this from the ORPHEUS directory with an authorized prepared source checkout.

```powershell
py -3.12 -B tools/prepare_server_runtime.py --source 'C:\path\to\prepared-checkout' --destination .\runtime-data --map 0688
```

### Server-only checks

With the runtime in place, run `scripts/START_SERVER_LOCAL.cmd` to start the loopback services and dashboard at `http://127.0.0.1:3725`. This entry needs the Python environment and prepared runtime.

### Join an existing server

On Windows, use the **1.74.0.0.104319** client and 64-bit Python 3.12. Ask the host for the address of their running, compatible ORPHEUS server. The host runs the service with its authorized runtime. The player launcher needs the game folder and host address and runs without a local server runtime or Windows relay.

Open `scripts/JOIN_SERVER.cmd` from the same checkout. Enter the address as `host:port`, then select the client if prompted. You can also pass the address and game folder directly in PowerShell.

```powershell
.\scripts\JOIN_SERVER.cmd --server 'HOST:3724' --game-exe 'C:\path\to\Overwatch'
```

The launcher requests the client's simpler tournament menu. Original-client verification is pending.

### Source-only checks

Use Windows with 64-bit Python 3.12. The source tests below use synthetic fixtures. Node.js 24 is used for the separate dashboard test.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -B -m unittest discover
```

Python discovery runs the public suite. See [contributor checks](CONTRIBUTING.md#public-source-checks) for the dashboard suite and the separate local runtime tests.

## Current status

| Area | Implemented and tested on the server | Evidence from the original client and remaining work |
| --- | --- | --- |
| Local login and lobby | Local authentication emulation, lobby, hero selection and match handoff. Automated checks cover handshake and server startup with prepared data. | Login, Practice Range loading and hero selection or switching have been observed. Broader lobby flows and match rules need verification. |
| Soldier: 76 | Body and weapon state, movement and Sprint, rifle fire and reload, Helix Rockets and Biotic Field. Automated tests cover selected scenarios including repeated switching and rejoining, lost packets, and ammunition state. | The tester has fired on and killed training bots. Stable weapon presentation, ammunition HUD, input response and ability behavior still need a complete client pass. |
| Practice Range combat | Hit and damage resolution, bot health, death and respawn, and replication of the original overhead health display graph for stock bots. Automated tests use controlled fixtures and prepared collision from the target map. | The tester has reported missing overhead health bars, unnamed eliminations, missing kill feed and missing headshot audio. The display graph change has wire evidence. Its rendering still needs a documented client result. |
| Networking and research | Encrypted UDP, player input, entity lifecycles, server snapshots, replay and selected loss/reorder checks. | Client prediction, combat feedback, two-client agreement, longer sessions and broader map behavior remain open. |
| Heroes, modes and audio | Shared systems and partial coverage exist. | Full hero abilities, objectives, match flow, hit and multikill feedback, and contextual voice playback are still in development. |

An automated result covers the fixture or replay named in its evidence. A manual observation covers the build, commit and steps that were actually tried. No complete original-client acceptance pass has been recorded for the first target scenario. See the [verification record](docs/STATE.md) and [changelog](CHANGELOG.md).

## Get involved

- **Developers and AI-assisted contributors** can start with the public source tests. The guide covers Codex, Claude Code and the same review requirements for all submissions. [Contribution guide](CONTRIBUTING.md)
- **Reverse engineers** can help pin a single field, state transition, event or timing rule to build 104319. Start with the relevant evidence and report uncertainty explicitly. [Research evidence](docs/RESEARCH_EVIDENCE.txt)
- **Players** can run the local Practice Range and report a short, repeatable gameplay result. Reports of working behavior are welcome alongside bugs. [Gameplay verification](https://github.com/leafall903/ORPHEUS/issues/new?template=gameplay-verification.yml)
- **New contributors** can look for tasks that use the public checkout and synthetic data. [Good first issues](https://github.com/leafall903/ORPHEUS/labels/good%20first%20issue)

## Project layout

| Path | Role |
| --- | --- |
| `src/ow174/bnet`, `src/ow174/lobby` | Local login, accounts, social and lobby services, matchmaking and handoff |
| `src/ow174/game`, `src/ow174/game/script` | Encrypted game transport, inputs, simulation, combat and statescript replication |
| `src/ow174/dashboard`, `relay` | Local administration and the Windows client relay source |
| `scripts`, `src/orpheus` | Windows launchers and the installed ORPHEUS entry point |
| `tests/public`, `tests/runtime` | Public checks and prepared-runtime regressions |
| `tools`, `docs` | Extraction and replay tools, research records and credits |

The [roadmap](docs/ROADMAP.md) tracks the full build scope. [CREDITS.md](docs/CREDITS.md) records the original 1.74 lobby project, research sources and upstream authors. The public source retains the original [MIT license notice](LICENSE). Overwatch and its assets belong to their respective rights holders. Keep game clients, commercial assets, keys, private logs, raw captures and dumps out of public submissions.
