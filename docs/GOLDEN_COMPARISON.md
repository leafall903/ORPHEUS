# Compare two gameplay traces

`compare_golden` reads a Python reference and a Rust candidate from local files. A successful comparison means that the recorded inputs, selected state and ordered output groups agree through the final tick. The public example uses invented values. Recording a complete Soldier session from either server and validating its gameplay in the original client are separate milestones. [Recorder and replay work](https://github.com/leafall903/ORPHEUS/issues/131)

```sh
cargo run --locked -p orpheus-golden-trace --bin compare_golden -- reference.jsonl candidate.jsonl
cargo run --locked -p orpheus-golden-trace --bin compare_golden -- reference.jsonl candidate.jsonl --report private-report.json
cargo test --locked -p orpheus-golden-trace
cargo run --locked -p orpheus-golden-trace --bin compare_golden -- --check-reference reference.jsonl
cargo run --locked -p orpheus-golden-trace --bin compare_golden -- --check-candidate candidate.jsonl
```

| Exit | Result | Meaning |
| --- | --- | --- |
| 0 | `match` | Both complete traces cover the same schedule and agree. |
| 1 | `mismatch` | Both traces are valid. The local report identifies the first tick, phase, logical actor and field that differ. |
| 2 | `incompatible` | Both traces passed individual validation. The private report lists the inputs or setup fields that prevent a gameplay comparison. |
| 2 | `incomplete` or a command error | A record is incomplete or corrupt, or a file operation failed. A bad footer invalidates an earlier apparent mismatch. |

Detailed values go in the optional report. Choose a new file in a private directory. A report distinguishes a missing field from a field whose value is JSON `null`. Keep recordings and reports on the runner or maintainer machine. The repository ignores `local-data/` at the project root.

## Record the same session twice on each server

Use a prepared 104319 runtime and a private canonical input schedule. The Python reference checkout must match the revision pinned by the adapter. Select a Python interpreter with the reference project's dependencies installed. Both adapter checkouts must contain the replay observers.

```sh
python scripts/capture_room_pair.py --python-source /path/to/python-reference --python-adapter /path/to/rust-checkout --rust-source /path/to/rust-checkout --runtime /private/runtime-data --schedule /private/schedule.jsonl --bot-slot 2 --out /private/new-session --python /path/to/reference-venv/python
```

Each process receives the same input schedule and selected spawner. The collector checks the entire runtime tree, manifest and schedule before the first process and after each run. Its receipt records source revisions, observation hashes and whether the two recordings of each implementation agree. A repeatable diagnostic can still contain missing fields or disagree with the other server.

The collector builds the native room test and the validator once, then runs the same private copy of the test executable in a fresh process for each attempt. The receipt records both executable digests. Each repeatable, complete role is encoded and checked through its footer before being saved as `reference.jsonl` or `candidate.jsonl`. The collector exits successfully only on `match`.

Captured `orpheus.golden.v2` headers bind the selected spawner, roster size, initial and first scheduled room frames, setup delivery ticks and owner acknowledgement phase. Host and player VM random models and initial seeds have separate fields. An OS-entropy transport records a null host seed. When setups differ, both validated recordings stay on disk and the receipt identifies the incompatible fields. Synthetic v1 records and historical v1 headers with the smaller setup remain readable.

The collector attempts both recordings for each implementation. A local recorder or native build failure is listed under `recording_failures` in the receipt. A valid pair from the other implementation can still produce a checked single trace. Changed runtime inputs or source revisions invalidate the run.

## What has been recorded

The current recorders wait for the room's map offer, load the map, select Soldier after the initial controller StateScript frame has been acknowledged (`player.script.delivered` / `controller_acked`, aligning `owner_component_start_frame = 1047` and `setup_delivery_ticks = 50`), and advance real room ticks throughout. Recording starts on the next tick after acknowledgements for the selected bot, player body and possession. Each trace retains the actual startup duration, receipt frames and player VM construction frame. The schedule clock starts at zero at this boundary. Submitted command frames remain exactly as supplied. Earlier traces remain readable with their original startup metadata.

In the earlier abbreviated-start experiment on Windows, the pinned Python reference and the Rust room each processed all 339
inputs in three headless scenarios: sustained fire at a training bot, a single
shot followed by a reload, and an idle-weapon control. Each implementation ran
each scenario twice in a fresh process. Both copies of each role's recording
agreed and passed the single-trace validator. The pack supplied stock bot
placements and health, but lacked their optional body scripts. Native solo
admission currently rejects a Practice Range that includes scripted bot bodies.
The collector reports that profile as unsupported for Rust and can preserve
the checked Python recording for the same inputs.

The paired comparison returned `incompatible` in all three scenarios. The
first scheduled room tick matched, but the initial owner acknowledgements,
number of setup delivery steps, and host and player-script random streams
differed. Confirm equivalent startup conditions before using these recordings
as a gameplay parity gate. Original-client acceptance is recorded separately.

## Writing the first format

The [synthetic reference](../rust/crates/golden-trace/tests/fixtures/reference.jsonl) is a complete four-tick example with a command, two effects in one delivery group and a bot retirement followed by respawn. The test suite reads those Python-generated bytes and builds a candidate independently in Rust.

Every line contains one compact UTF-8 JSON object with sorted ASCII keys and a final LF. The format rejects extra fields and duplicate keys. Use the following records in order.

| Record | Required content |
| --- | --- |
| Header | Use schema `orpheus.golden.v1`, profile `soldier-range-v1`, scenario `soldier-range` and build `104319`. Pin `reference_rev`, `source_rev` and `adapter_rev` to 40 lowercase hex digits. Specify the role, platform, toolchain, seed, tick duration and the three SHA-256 fingerprints shown in the example. The reference records its source revision in both `source_rev` and `reference_rev`. |
| Tick | Record contiguous ticks starting at zero. `elapsed_us` equals the tick index times `quanta_us`. This revision uses zero missed ticks and at most one offered command per tick. Store the input, post-tick state, ordered owner effects and projection digest. |
| End | Store the tick count, final tick index and SHA-256 of every preceding canonical line including its LF. The end row closes the file. |

Both producers must use the same platform, clock configuration and runtime inputs. `runtime_sha256` identifies the runtime manifest. `profile_sha256` identifies the **effective input tree**, including supplemental files. Use a common fingerprint producer when recording both runs. `schedule_sha256` covers each tick's canonical object containing `elapsed_us`, `inputs`, `missed` and `tick`, each followed by LF. Each tick's `digest` covers its canonical object containing `effects` and `state`, without LF. Hashes use lowercase hexadecimal.

**Input and acceptance.** Record the offered decoded command before per-player transformations. Its body epoch, frame, axes, angles, 24-bit buttons, action and view delay are required. The subframe flag (`0x10000`) carries a three-element click sample with subframe, yaw and pitch. An ordinary command has `click: null`. Consecutive inputs for the same body advance the unsigned frame counter by one, including wraparound. The player's `accepted_frame` is a separate observation. A producer must distinguish its last processed command from a fallback server tick.

**State.** Each row is `[logical identity, field, value]`, sorted by identity and field. The two permanent slots are `bot/0` and `player/0`. Each has a `body` field holding its current logical body or an explicit `null`. Body generations start at 1 and advance on replacement. A retired body cannot reappear. Each present body supplies `alive`, `health_bits` and `position_bits`. The player also supplies `accepted_frame`, `aim`, `ammo` and `reloading`. Health and each position component use 16 lowercase hex digits for the finite IEEE-754 binary64 bits in big-endian order. The comparison preserves signed zero. The reference must observe both bodies and at least one offered command during the run.

**Outputs.** Record owner delivery groups in observed order, with each group's effects in observed order. Each effect contains recipient, kind, logical body and value. This revision supports `create`, `retire`, `health` and `ammo`. An empty list means the observer saw no relevant output. A producer that cannot observe the group boundary or account for a relevant effect must report incomplete evidence and withhold a complete trace.

The comparator checks acceptance before other state and then compares grouped effects. The design describes the remaining recorder work. [Gameplay trace design](GOLDEN_TRACE.md)
## Replay a private schedule in the native room

The prepared-runtime test takes a local decoded schedule with the command fields listed above. Select an ordinary hostile Practice Range spawner by its zero-based position in the filtered catalog. Provide a fresh output filename in a private directory. The test leaves existing files intact.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\private\runtime'
$env:ORPHEUS_ROOM_SCHEDULE = 'C:\private\schedule.jsonl'
$env:ORPHEUS_ROOM_OBSERVATION = 'C:\private\native-observation.json'
$env:ORPHEUS_ROOM_BOT_SLOT = '2'
cargo test --locked -p orpheus --lib same_schedule_practice_room -- --ignored
```

The fixture selects stock Soldier, acknowledges the initial owner and body, and passes each offered command through the production room. It samples the state after the worker tick. The output separates setup deliveries from scheduled deliveries and records the effective field set. The latter uses the records selected by successful sends in their original order. Compare independent runs under the same runtime and record source revisions, clock, RNG, and startup conditions separately. The resulting observation is a local input to the comparison runner.
