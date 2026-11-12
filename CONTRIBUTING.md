# Contributing to ORPHEUS

You can contribute code, research, documentation or a gameplay report. The public source checks use synthetic fixtures and are a good place to start. Read the [roadmap](docs/ROADMAP.md), check [existing issues](https://github.com/leafall903/ORPHEUS/issues), and pick a task with prerequisites you can meet. For larger work, comment on the issue with the behavior you plan to verify so others can coordinate.

## Development setup

Use Rust for new implementation work. Install Git, stable Rust, `rustfmt` and `clippy`. On Windows, install the x64 MSVC C++ Build Tools and Windows SDK. Cargo uses the C toolchain to build the vendored Lua runtime.

```powershell
git clone --branch rust https://github.com/leafall903/ORPHEUS.git
cd ORPHEUS
```

Use a fork and a short topic branch for a pull request. Test with your own Git author identity. Keep code, research and evidence scoped to the change.

## Rust design and review

Read the [Rust contribution rules](docs/NATIVE_SERVER_ARCHITECTURE.md#rust-contribution-rules) before changing the server. In your pull request, identify the simulation, transport, script or data boundary you changed. For behavior changes, include the reference or reproduction and the closest headless check. For collision changes, include the loaded geometry source, filter and contact evidence. Record original-client results separately when available.

Use [shared behavior contracts](docs/BEHAVIOR_CONTRACTS.md) to compare Python and Rust against the same explicit input and expected output. Supply a synthetic or approved fixture for changed behavior, or document the reproduction, missing prerequisite and follow-up. Backfill older behavior as it is touched and keep client acceptance separate from fixture agreement.

## Public source checks

Run the native source checks from the repository root with stable Rust, `rustfmt` and `clippy`. They use public synthetic fixtures and match the two lanes of the [public workflow](.github/workflows/public-checks.yml).

```powershell
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --features default -- -D warnings
cargo test --locked --workspace --all-targets --features default
cargo clippy --locked --workspace --all-targets --features inspector -- -D warnings
cargo test --locked --workspace --all-targets --features inspector
git diff --check
```

The inspector's HTTP listener and web assets are opt-in. The default test build covers snapshots, collision projection and worker timing. HTTP endpoint tests run with `inspector` enabled. Public CI runs both lanes for pushes and pull requests to `rust`. Add the closest focused test for the code you changed. Report the exact commands, pass or fail results, and any skips in your pull request.

The Python reference implementation, its tests and the data extraction tools live on [`main`](https://github.com/leafall903/ORPHEUS/tree/main). Use a separate `main` checkout when a change needs a behavioral comparison.

## Prepared runtime checks

Native gameplay regressions can use encrypted loopback and real map collision without launching Overwatch. They need the correct local runtime and the named graph fixture. See [runtime requirements](data/README.md) and [server setup](README.md#separate-deployment-data).

Run the focused native Practice Range checks with an authorized base pack.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\orpheus-runtime'
$env:ORPHEUS_BNET_DESCRIPTORS = Join-Path $env:ORPHEUS_RUNTIME_DIR 'bnet/descriptors.pb'
cargo run --locked --bin orpheus_server -- --check-runtime
cargo test --locked -p orpheus --lib prepared_worker_delivers -- --ignored
```

Choose ignored tests by name and read their prerequisites. The base and enriched graph lanes differ. The [two-room benchmark](docs/NATIVE_SERVER_ARCHITECTURE.md#two-room-headless-workload) has separate opt-in settings. Report the exact test and runtime lane you used.

## Original-client verification

Use your own authorized Overwatch 1 retail **1.74.0.0.104319** installation and an already configured local connection. The Rust developer preview provides a server-only entry. Original-client authentication requires separately supplied local inputs. The Python launcher is documented on [`main`](https://github.com/leafall903/ORPHEUS/tree/main#local-practice-range).

For the first acceptance group, enter the Practice Range, pick Soldier: 76, and record one behavior at a time. Include the ORPHEUS commit, client build and locale, hero, map, exact inputs, expected and observed behavior, and how many runs agreed. Distinguish server or wire results from what you actually saw or heard. Use the [gameplay verification form](https://github.com/leafall903/ORPHEUS/issues/new?template=gameplay-verification.yml). Reports with clear steps are useful even when logs or video are unavailable.

## Issues and pull requests

- Use a [bug report](https://github.com/leafall903/ORPHEUS/issues/new?template=bug-report.yml) for a reproducible failure or a [feature request](https://github.com/leafall903/ORPHEUS/issues/new?template=feature-request.yml) for a proposed change. Put distinct behaviors in separate reports and link related issues.
- For protocol or graph research, identify the target build and cite the exact field, node, event or reproducible comparison. Separate observations, inferences and open questions. Describe how another contributor can repeat the check.
- Keep a pull request focused. Link the issue, describe expected behavior and report what changed and how you tested it. Include a short [changelog](CHANGELOG.md) entry for a user-visible or verification change. Use clear English [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).
- Codex, Claude Code and other AI coding tools are welcome. If a tool materially contributed, disclose it in the pull request or an `Assisted-by` commit trailer. Read and understand the diff, run the relevant checks, and be ready to explain the change. Maintainer review applies to every pull request.
- Open Rust pull requests against `rust` and Python pull requests against `main`. The maintainer preserves contributor authorship and checks the audited publication commit.

Share only small, redacted log excerpts or a video you are authorized to share. Remove personal account and machine information, credentials, authentication or session data, and private paths. Keep clients, game data dumps, captures, signing input, resource keys and crash dumps local. A description and timestamps are enough to start a discussion if an artifact cannot be shared.
