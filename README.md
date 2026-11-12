# ORPHEUS

[简体中文](README.zh-CN.md) · [Discord](https://discord.gg/tqukqjAk7)

ORPHEUS is an independent, open-source server reimplementation for **Overwatch 1 retail 1.74.0.0.104319**. It aims to preserve the server behavior and client-facing events for this build. The first end-to-end target is Soldier: 76 in the Practice Range.

> **Rust developer preview.** You can build and run synthetic tests from the public source. Native startup needs an authorized, prepared 104319 runtime. A publicly reproducible original-client authentication setup and a Rust one-click client launcher are still open.

This is the Rust branch. It contains only the native server and the sources its contributors need. The [default branch](https://github.com/leafall903/ORPHEUS/tree/main) contains the community-maintained Python server, which is also the behavioral reference, with its Windows launcher, tests and data tools.

[Contribute](CONTRIBUTING.md) · [Python/Rust parity](docs/RUST_PARITY.md) · [Architecture](docs/NATIVE_SERVER_ARCHITECTURE.md) · [Rust contribution rules](docs/NATIVE_SERVER_ARCHITECTURE.md#rust-contribution-rules) · [Roadmap](docs/ROADMAP.md) · [Changelog](CHANGELOG.md) · [Open issues](https://github.com/leafall903/ORPHEUS/issues)

## Current status

| Area | Python on `main` | Rust on `rust` | Original-client evidence |
| --- | --- | --- | --- |
| Login and solo Practice Range | Windows launcher and local login, lobby and match handoff with prepared data. | Native loopback login, lobby, world creation and encrypted UDP pass headless regressions with prepared data. | A previous Rust playtest entered Practice Range. The latest Rust build awaits a client retest. |
| Soldier: 76 body and weapon | The reference runs stock-body paths with partial weapon, Sprint and ability behavior. | Authored rifle, reload, Sprint and Helix timing have isolated regressions. The native stock body still stops at an unsupported effect action. Match tests use fixture weapons. | The Rust playtest reported a missing weapon and unusable abilities. |
| Bot combat and projectiles | Practice Range hits, damage and respawn have reference checks. | Encrypted fixture matches verify bot hits, lost-packet recovery, death, respawn and Helix damage. Bots and physical flight live in the room's Bevy ECS. Lua handles patrol decisions. | Stock-client damage presentation and the reported upstairs patrol discrepancy still need verification. |
| HUD, audio and ultimate charge | Partial HUD wire evidence and community-estimated ultimate charge. | Combat messages and prediction records have focused tests. Full HUD, audio and ultimate-charge parity remain open. | Historical tests reported missing overhead health, named eliminations, kill feed and headshot audio. |
| Diagnostics and scale | Local dashboard and Python reference replays. | The optional inspector shows actors, collision samples, patrol clips, tick timing and bounded room-fault records. Public and prepared tests verify that another room survives an isolated room fault. | These checks use independent solo rooms and synthetic fault injection. Multiplayer in a shared original-client room remains open. |

Fixture, wire and original-client results cover different scopes. See the [parity checklist](docs/RUST_PARITY.md), [verification record](docs/STATE.md) and [migration guide](docs/RUST_MIGRATION.md) for the evidence and cutover gates.

## Start with the public Rust checks

Use Git and stable Rust. The Windows build also needs the x64 MSVC C++ Build Tools and Windows SDK for the vendored Lua runtime. Public Rust tests use synthetic fixtures and run without game files or a runtime pack.

```powershell
git clone --branch rust https://github.com/leafall903/ORPHEUS.git
cd ORPHEUS
cargo test --locked --workspace --all-targets
```

The contribution guide lists the default and inspector checks that public CI runs. [Contribution guide](CONTRIBUTING.md#public-source-checks)

## Run the native server

### Separate deployment data

Use a prepared, authorized **base** 104319 Practice Range runtime. The public source and client installation alone leave required research data and restricted local inputs outstanding. The Rust packer copies prepared inputs and writes a SHA-256 manifest. See the [three-part input checklist](data/README.md).

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\authorized-base-runtime'
cargo run --locked --bin orpheus_server -- --check-runtime
cargo run --locked --bin orpheus_server -- --data-dir 'C:\path\to\local-state'
```

The preflight reports missing inputs by category and checks the signing input length. The native server parses the runtime before opening its loopback listeners. It can start independently of a game installation or relay DLL. The local state directory holds profiles and server state. Use a separate directory for each running server.

The read-only inspector is disabled in default builds. Enable its Cargo feature and choose a loopback port when running it.

```powershell
cargo run --locked --features inspector --bin orpheus_server -- --data-dir 'C:\path\to\local-state' --debug-port 3726
```

Open [127.0.0.1:3726](http://127.0.0.1:3726/) after startup. The [architecture guide](docs/NATIVE_SERVER_ARCHITECTURE.md#collision-and-live-inspection) describes its sampling limits. Original-client verification uses a separately configured local connection and the exact 104319 client. [Verification procedure](CONTRIBUTING.md#original-client-verification)

## Get involved

- **Developers and AI-assisted contributors** can start with the public Rust tests. The contribution guide covers validation and review for Codex, Claude Code and manual contributions. [Contribution guide](CONTRIBUTING.md)
- **Reverse engineers** can document a field, event or timing rule against build 104319. [Research evidence](docs/RESEARCH_EVIDENCE.txt)
- **Players with a working local setup** can report repeatable original-client observations. Include the branch, commit, client build and exact steps. [Gameplay verification form](https://github.com/leafall903/ORPHEUS/issues/new?template=gameplay-verification.yml)
- **New contributors** can pick tasks that use only public source and synthetic fixtures. [Good first issues](https://github.com/leafall903/ORPHEUS/labels/good%20first%20issue)

## Project layout

| Path | Role |
| --- | --- |
| `rust/src`, `rust/scripts` | Native server, unit tests and the Lua patrol policy |
| `rust/crates/collision` | Collision cache decoding, static queries and contact solver |
| `rust/crates/runtime` | External runtime inventory, preflight and independent preparation tool |
| `rust/tests` | Native CLI integration tests |
| `relay` | C++ relay source for connecting the original client |
| `docs` | Architecture, acceptance criteria and research records |

ORPHEUS is an unofficial preservation project. The public source retains the upstream [MIT notice](LICENSE) and [credits](CREDITS.md). Keep game clients, commercial assets, signing material, content keys, private logs, captures and dumps out of the repository and public discussion.
