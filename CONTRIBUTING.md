# Contributing to ORPHEUS

You can contribute code, research, documentation or a gameplay report. The public source checks use synthetic fixtures and are a good place to start. Read the [roadmap](docs/ROADMAP.md), check [existing issues](https://github.com/leafall903/ORPHEUS/issues), and pick a task with prerequisites you can meet. For larger work, comment on the issue with the behavior you plan to verify so others can coordinate.

## Development setup

The community-maintained `main` branch accepts Python server and tooling contributions. For native development, see the separate [Rust branch](https://github.com/leafall903/ORPHEUS/tree/rust). Install Git and 64-bit Python 3.12 on Windows. Install Node.js 24 to run the dashboard test.

```powershell
git clone https://github.com/leafall903/ORPHEUS.git
cd ORPHEUS
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The editable install provides `orpheus` and `python -m orpheus`. Runtime dependencies and development extras are declared in `pyproject.toml`.

Submit changes from a topic branch on your fork to `main`. Use your own Git author identity. Keep code, research and evidence scoped to the change.

For Python behavior changes, provide a language-neutral fixture with inputs, expected outputs and reference evidence so Rust can check the same behavior. Record pending client acceptance and any missing fixture prerequisites. Backfill existing behavior as it is touched. See the [shared behavior contract workflow](docs/BEHAVIOR_CONTRACTS.md).

## Public source checks

Run these Python and JavaScript checks from a clean checkout without a local `runtime-data/` pack. Clear `ORPHEUS_RUNTIME_DIR` to match CI. The checks use public source and synthetic fixtures.

```powershell
Remove-Item Env:ORPHEUS_RUNTIME_DIR -ErrorAction SilentlyContinue
$python = '.\.venv\Scripts\python.exe'
& $python -B -m ruff check . --no-cache
& $python -B -m unittest discover
node --test tests/public/test_dashboard_client.mjs tests/public/test_npc_console_client.mjs
git diff --check
```

The [public workflow](.github/workflows/public-checks.yml) defines the current checks for pushes and pull requests. Discovery runs `tests/public`. Keep tests requiring local data or descriptors in `tests/runtime`. Add the closest focused test for the code you changed. Report the exact commands, pass or fail results, and any skips in your pull request.

## Prepared runtime checks

Gameplay regressions can use an in-memory encrypted peer and real map collision without launching Overwatch. These checks need the correct local runtime and any named map or graph fixture. See [runtime data provenance](data/README.md) and [server setup](README.md#separate-deployment-data).

For example, check the inputs and run the projectile suite against a prepared build 104319 pack.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\orpheus-runtime'
.\scripts\START.bat --check-runtime
.\.venv\Scripts\python.exe -B -m unittest discover -s tests/runtime -p test_game_projectiles.py
```

Select the relevant module under `tests/runtime`. Some modules also need separate research fixtures in the checkout's `data` directory. Record any missing prerequisites, the data lane used, and the checks that were actually run.

## Original-client verification

Use your own authorized Overwatch 1 retail **1.74.0.0.104319** installation and a prepared local runtime. Build the Windows relay once with x64 MSVC Build Tools, or reuse a trusted local build at `relay/owwfd_relay.dll`. From the repository root in the x64 Native Tools Command Prompt, run the build script.

```bat
.\relay\build.bat
```

Run the preflight before launching to identify missing server inputs. The launcher accepts the installation folder or the game executable. See the [runtime guide](data/README.md).

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\orpheus-runtime'
.\scripts\START.bat --check-runtime
.\scripts\START_LOCAL.cmd --game-exe 'C:\path\to\Overwatch'
```

For the first acceptance group, enter the Practice Range, pick Soldier: 76, and record one behavior at a time. Include the ORPHEUS commit, client build and locale, hero, map, exact inputs, expected and observed behavior, and how many runs agreed. Distinguish server or wire results from what you actually saw or heard. Use the [gameplay verification form](https://github.com/leafall903/ORPHEUS/issues/new?template=gameplay-verification.yml). Reports with clear steps are useful even when logs or video are unavailable.

## Issues and pull requests

- Use a [bug report](https://github.com/leafall903/ORPHEUS/issues/new?template=bug-report.yml) for a reproducible failure or a [feature request](https://github.com/leafall903/ORPHEUS/issues/new?template=feature-request.yml) for a proposed change. Put distinct behaviors in separate reports and link related issues.
- For protocol or graph research, identify the target build and cite the exact field, node, event or reproducible comparison. Separate observations, inferences and open questions. Describe how another contributor can repeat the check.
- Keep a pull request focused. Link the issue, describe expected behavior and report what changed and how you tested it. Include a short [changelog](CHANGELOG.md) entry for a user-visible or verification change. Use clear English [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).
- Codex, Claude Code and other AI coding tools are welcome. If a tool materially contributed, disclose it in the pull request or an `Assisted-by` commit trailer. Read and understand the diff, run the relevant checks, and be ready to explain the change. Maintainer review applies to every pull request.
- The maintainer reviews pull requests on GitHub and preserves contributor authorship when publishing accepted changes to `main`.

Share only small, redacted log excerpts or a video you are authorized to share. Remove personal account and machine information, credentials, authentication or session data, and private paths. Keep clients, game data dumps, captures, signing input, resource keys and crash dumps local. A description and timestamps are enough to start a discussion if an artifact cannot be shared.
