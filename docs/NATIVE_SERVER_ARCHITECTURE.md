# Native server architecture

ORPHEUS targets Overwatch retail build **1.74.0.0.104319**. This decision defines the Rust server's simulation boundaries and the order of migration. The [restoration roadmap](ROADMAP.md) tracks gameplay acceptance, and the [Rust migration guide](RUST_MIGRATION.md) tracks replacement of the frozen Python server.

**Status.** The Rust worker runs the authored StateScript interpreter, an event-driven Lua patrol pilot and a read-only debug dashboard. Live bots and physical projectile flight reside in the room ECS. Player bodies still need migration. Completing any slice requires separate evidence for automated behavior, network parity and original-client behavior.

## Rust contribution rules

1. **Keep a dedicated server boundary.** Build and validate the server headlessly with a separate runtime pack. Rust owns I/O, networking, protocol codecs, tick scheduling, entity and controller lifetimes, collision and command validation. Keep client setup outside the server and measure tick and delivery costs when moving work onto the match worker.
2. **Use one room authority.** Build on the existing standalone Bevy ECS. Move state into room-owned components in tested slices. Keep per-connection replication and acknowledgements at the transport boundary. Use generation-aware entity handles and shared systems across heroes and maps. Prefer extending established libraries and the current workspace boundaries before adding another engine, scheduler or simulation path.
3. **Put gameplay policy in authored data and event-driven scripts.** Prefer the target build's authored graphs and catalogs for hero, mode and map behavior. Extend the existing StateScript interpreter for those graphs. Use the existing Lua host for custom policies and AI decisions, with typed lifecycle events and scheduled think events driven by the simulation clock. Rust validates commands and applies them through the shared simulation. Keep handlers bounded, ordered and replayable. Put tunable values and rules in data or scripts when their format and behavior are understood.
4. **Diagnose collision with the live query inputs.** Use the optional read-only inspector to compare actual actor positions, geometry source, shape, height, filter and first contact. Capture a repeatable fixture before changing a mesh, route or mask. Share query primitives where semantics agree and check movement, hitscan and projectile behavior against their own authored filters.
5. **Keep diagnostics separate from gameplay authority.** Extend the existing loopback inspector with bounded, tick-stamped views of actors, geometry and events. Keep HTTP and visualization off the simulation tick. The inspector is read-only today. A future write-capable admin backend needs explicit authorization and validated commands applied at the simulation boundary.
6. **Prove each change against a reference.** Record the 104319 evidence and pin the reference revision or fixture. Use the Python reference on `main` where relevant and credit borrowed work in [CREDITS.md](../CREDITS.md). Compare state, event order or decoded wire output using the same inputs and timing. Report headless and original-client results separately. Keep uncertain behavior explicit and add a shared regression when closing a gap.

## Where the current architecture breaks down

| Code today | Consequence |
| --- | --- |
| `RoomWorld` owns a prepared scene and the room's Lua policy instance. Its `RoomSimulation` contains a Bevy world. Each bot entity holds its body, collider, vitals, rewind history and respawn state. Physical projectile entities belong to a connection and body generation. [Match worker](../rust/src/game_service.rs), [room simulation](../rust/src/game_combat.rs) | Bot movement, hits, replacement and projectile flight use the room's current entities. Player bodies still live outside this authority. |
| `WorldDelivery` owns player movement and body state alongside per-viewer baselines, create acknowledgements and projectile prediction leases. [World delivery](../rust/src/game_replication.rs) | Player simulation lifetime and connection delivery remain coupled. Hitscan eligibility checks a viewer's create acknowledgement, while projectile collision uses a different path. Keep that distinction observable until replay establishes the intended historical target eligibility. |
| Player and bot character movement use the contact solver when its cache is present. The combat wall query uses the legacy ground scene with `MOVER_CATEGORY`. Expanded rays have an independent cache identity and validation path. [Scene loading](../rust/src/scene_assets.rs), [collision scenes](../rust/crates/collision/src/collision_scene.rs), [cache validation](../rust/crates/collision/src/collision_cache.rs) | Different shape sets or masks can produce different results at the same location. This is a testable hypothesis for route and hit discrepancies. It does not identify the reported upstairs wall as faulty without a matching trace. |
| The authored StateScript interpreter has a world interface, and the owner body supplies a local adapter. The adapter can return an origin for an unknown foreign entity and includes a Practice Range and Soldier ruleset fallback. [Interpreter](../rust/src/script_init.rs), [body adapter](../rust/src/script_player.rs) | A script can confuse an absent entity with a real position, and the body adapter cannot answer general room queries from its own state. |

## Cargo boundaries

The root `orpheus` package owns the native server. The `orpheus-collision` member owns collision cache decoding, static queries and the contact solver. It builds without the server, ECS, Lua or network stack. The root library re-exports the existing collision module paths for callers. Run the isolated kernel suite with `cargo test --locked -p orpheus-collision`. Use `--workspace` in the full validation lane to include its tests.

The `orpheus-runtime` member owns external pack preparation, the required-input inventory and preflight. It has no dependency on the server, ECS, Lua or networking. Its graph format helpers are shared with the server loader. A server-side integration test checks that the loader accepts a synthetic tag-enriched pack from the independent tool. Run it with `cargo run --locked -p orpheus-runtime --bin prepare_server_runtime -- --help`.

Cross-module public protocol and native CLI tests live in `rust/tests`. The independent runtime CLI has its own integration test under its member. Module-local tests stay next to the implementation, including match-worker tests that exercise private state and injected room fixtures.

| Current server module | Responsibility |
| --- | --- |
| `world` | Retail entity update encoding and packet-budget selection. The room ECS is owned by the simulation layer. |
| `match_scene` | Player, bot and mode baselines and their initial wire records. |
| `scene_assets` | Prepared scene construction from catalogs and immutable collision indexes. Its scene query and owner-runtime interfaces currently cross the combat and replication boundaries. |
| `game_instance` | Roster and entry messages using the map model and match codecs. |
| `match_handoff` | Shared lobby-to-match ticket alongside lobby message construction. Separate the ticket and its wire adapter before moving the whole module. |
| `game_content`, `game_parameters` | Map catalog and spawn data in the former. Retail parameter envelopes and hero selection parsing in the latter. The latter consumes protocol types. |

Keep pure geometry tests and frozen arithmetic fingerprints with the collision crate. Prepared 104319 collision and movement regressions remain in the server validation lane. The kernel fixtures exercise synthetic inputs and reference behavior. Original-client parity needs separate evidence.

See the [structure review](RUST_STRUCTURE_REVIEW.md) for the dependency graph findings and remaining migration decisions.

The [foundation review and task tracker](RUST_FOUNDATION_REVIEW.md) records the 2026-11-12 refactor findings, validation gates and remaining work.

## Room authority and transport

Give each active room one authoritative ECS world. Store body identity, transform and collider, vitals, rewind poses, and lifetime on the same entity. A stable spawner slot identifies the authored spawn across respawns. A body handle includes a generation, so a delayed event cannot affect the replacement body. Keep retail wire IDs in a separate mapping with the existing lifetime and reuse rules. Keep controller identity separate from the body it possesses.

Bot pose and combat state share one room-owned Bevy component per bot. Respawn retires the old handle and inserts a new one at the same authored slot after all replacement records are prepared. The worker transfers accepted projectile launches to the room at the flight stage. Physical flight stays with the room until it ends or its owner generation retires. Egress applies terminal outcomes to its matching prediction leases and keeps packet sequencing, acknowledgements, baselines, retransmission and projected owner and observer state. Use this room boundary for the next migration of player movement and body state. Any game rule that depends on what the shooter could have seen needs an explicit historical predicate with replay evidence.

Keep the current Rust services and game clock. The first ECS slice uses standalone [`bevy_ecs` 0.16.1](https://docs.rs/crate/bevy_ecs/0.16.1) with only its `std` feature. The crate [declares Rust 1.85](https://docs.rs/crate/bevy_ecs/0.16.1/source/Cargo.toml.orig), which matches ORPHEUS's declared baseline. Retain single-worker ordering when adding a manually driven schedule. Verify the locked dependency set on the supported toolchain and measure tick cost as components migrate.

Preserve the current ordering and budgets while relocating state. The worker reconstructs input poses and resolves their direct shots in bounded peer turns between receive batches and tick checks. The scheduled tick steps bots, advances projectile flight, resolves due respawns, records rewind poses, and then prepares delivery. Use explicit stage order and stable iteration where order affects results. Carry both command frame and simulation frame when they differ. Avoid adding an extra tick of delay or sorting a sequence differently without replay evidence. [Worker scheduling](../rust/src/game_service.rs), [game clock](../rust/src/game_clock.rs)

## Script host

Keep the authored StateScript interpreter responsible for existing graph behavior and its startup, timer, and replication semantics. Back its world queries with room state as entity coverage expands. Represent absence explicitly at the host boundary, then apply a retail-compatible default at the graph operation only when supported by evidence. Resolve rules through prepared mode and hero data before generalizing beyond the first acceptance group.

Stock Soldier: 76 body attachment can proceed alongside the room refactor. Keep fixes to authored nodes and their wire representation in the existing interpreter and snapshot modules. The room refactor supplies entity queries and lifecycle events through the host boundary. Keep the prepared Soldier preflight and encrypted owner delivery checks as integration gates for both workstreams.

Introduce a versioned script host ABI once the room boundary has a tested consumer. Host v1 should give handlers a read view of the current simulation stage, stable entity handles, the simulation frame, and seeded randomness. Deliver typed events such as spawn, damage, death, and timer expiry at documented stage boundaries. An AI think event runs when due. Handlers return typed commands such as movement intent or scheduling a timer. Rust validates handles, types, and authority before applying commands through the same simulation mechanisms as native handlers. Commands and events need stable ordering, bounded queues, and a defined policy for events raised during a handler.

The live pilot runs the checked-in [patrol policy](../rust/scripts/patrol.lua) in vendored Lua 5.4 through [`mlua` 0.11.6](https://docs.rs/mlua/0.11.6/mlua/struct.Lua.html). Rust sends a spawn, think or movement-release event at its existing simulation frame. The handler returns one wait duration or route slot, which Rust validates before use. The authoritative clock, authored route, collision, movement and packets stay with the Rust worker. The policy loads from text with an empty environment and bounded instructions and memory. A failed decision stops that patrol until the body is replaced. Each prepared room owns a separate VM for its lifetime. The shipped policy is stateless. Define per-entity state ownership before exposing stateful handlers or writable capabilities.

Expand scripting only after verifying handle lifetimes, event order, deterministic replay and failure isolation through the same host path. Expose only registered capabilities and disable filesystem, sockets, dynamic libraries, unapproved module loading, wall-clock time and unseeded random sources. Bound execution, allocations, scheduled work and command counts. Buffer each handler's commands until it succeeds, and isolate and report failures.

## Collision and live inspection

The first dashboard slice is opt-in and loopback-only. Default builds exclude the HTTP listener and embedded assets. Build with `--features inspector` and run with `--debug-port <port>` to enable it. The HTML, JavaScript and CSS live in [rust/assets/inspector](../rust/assets/inspector). Direct snapshot, collision projection and worker timing tests also run without this feature. HTTP endpoint tests run in the feature-enabled configuration.

It projects read-only, tick-stamped map and actor state from the same worker that runs the match. Show a top-down view of loaded collision geometry, actual actor positions, and available collider information. Label geometry source, query layer, masks, and height so vertically separated surfaces are distinguishable. Report missing inputs and any truncation. Cap the snapshot and wait time, keep HTTP and serialization outside the tick, and expose no credentials, session keys, captures, profiles, or local paths. The visualization consumes a projection of current authority and can switch its source to ECS as entities migrate.

The viewer opens with a 48 × 32 × 48 meter region around the first player spawn. Sample a room actor's current position or specify XYZ and a half extent from 1 to 16 meters. The HTTP worker reads the same immutable indexes and caps each response at 4,096 triangles and 256 contact bounds. The triangle layer shows the ground probe and hitscan surface sample. The contact layer shows character broad-phase bounds. A bound can include empty space and is useful for locating a shape. It cannot establish that the narrow-phase solver reported a hit. The endpoints report source, masks, totals and sampling caps.

Scene preparation reuses the last loaded collision indexes when map, mode and contact requirements match. Each active room holds immutable references to its indexes and owns its own mover state, contact history and ECS bodies. Preparation and cache locking stay outside the simulation worker.

With the inspector enabled, each patrolling bot can report its last successful movement with a fraction-limiting sweep. The row shows the first such sweep in that move, the simulation frame, the movement category and the proxy ID from the loaded contact index. Hover to see the previous, intended and final feet positions. The solver records the result while doing its existing work. The proxy ID matches the contact layer for the same loaded geometry. The absence of a clip leaves manifold-only constraints, unobserved motion and other collision queries open.

Use the dashboard to locate a discrepancy, then turn it into a headless test using the observed spawn or route, tick, position, shape, category, and first contact. Start with the reported Practice Range upstairs patrol. Compare the contact solver's movement geometry and the selected wall-ray geometry at the same location. Check map and placement provenance before evaluating an expanded cache, and record which source each query used. Keep the active source until a test establishes the replacement behavior.

Give players and bots the same character collision policy. Verify live enemies block movement and allies can pass, subject to the authored character-collision setting. Cover player versus player, player versus bot, and bot versus bot in both directions, including free-for-all, crouching, simultaneous movement, and death or body replacement. Keep character relationships separate from static map team barriers. Verify movement, hitscan, projectile sweeps, and penetration separately where their authored filters differ. Reuse geometry and query primitives where the behavior agrees. [World interactions and combat criteria](ROADMAP.md#project-wide-checklist)

## Worker timing

With the loopback inspector enabled, the state endpoint includes a cumulative `timing` sample. Tick duration covers the match worker's simulation and frame delivery call. Schedule lateness measures how far the event loop has passed its scheduled tick deadline when it checks the clock. It can include wake-up delay and work done before the tick. Samples are taken when a peer has started its prepared world and the clock is not draining at the start of the tick. Missed ticks count the scheduler's skipped ticks during those sampled turns.

The upper bound of each microsecond bin is exclusive. One extra bin covers durations at or beyond the last bound. The viewer shows the p95 and p99 bin bounds and input counters since worker startup. Subtract bin counts and sample counts between two snapshots from the same worker to find a percentile interval for that window. The reported maximum covers all samples since worker startup. Input budget counters report charged work, full peer and turn budgets, and turns that leave work queued. Timing runs on the server clock. Client round-trip time, packet arrival gaps and presentation latency need separate observations.

### Two-room headless workload

```powershell
$env:ORPHEUS_TIMING_BENCH_RUNTIME = 'C:\path\to\authorized-base-runtime'
$env:ORPHEUS_TIMING_BENCH_SECONDS = '60'
cargo test --locked --release --lib prepared_worker_timing_two_solo_rooms -- --ignored --nocapture
```

Two encrypted UDP clients load independent solo rooms on one worker. After a two-second warm-up, each client drives a fixed logical firing cadence with a synthetic low-damage hitscan fixture. Both rooms must acknowledge input and report hits. The workload enables timing on the worker directly. HTTP and per-bot contact tracing stay disabled. The test uses local delivery without injected packet loss. Use a one-second duration for a smoke check.

The report includes a runtime manifest digest, worker timing and input budget deltas, and each peer's observed local packet gaps and logical frame gaps. It separates world-bearing frames from all packets. The `prepare_scene_us` pair times the first and second scene preparations before installing the weapon fixture. The first call can include one-time owner preflight. For comparisons, use the same build profile, runtime, duration and host load.

## Migration gates

| Slice | Evidence required to proceed |
| --- | --- |
| Read-only inspection | A loopback test shows active actors and real loaded geometry from the match worker. Empty, unavailable, and bounded responses are explicit. Existing match delivery checks still pass with inspection disabled and enabled. |
| Room ECS | A bot's movement, hit, death, and replacement replay produces the same registered state and decoded delivery sequence, including packet loss. Tests reject stale handles and show that adding or reordering bots keeps each body bound to its own vitals and history. Apply the same comparison as player bodies and projectiles move. |
| Script host and pilot | Run the same fixture twice with the same seed and inputs. Compare event and command order and resulting state. Confirm a failing or over-budget handler cannot partially commit commands or interrupt the next tick. Retain authored StateScript and wire regressions. |
| Gameplay and performance | Replay the same build, data, inputs, and loss schedule before and after each change. Record tick p95 and p99, world packet gaps, owner corrections, and first and repeated hero-selection timing. Include sustained fire and a two-peer workload. Use the roadmap's original-client checks for the 60-second body and weapon test, ten switch-and-return and ten rejoin cycles, and two-client movement, line of sight, damage, and respawn. |
| Rust cutover | Rust exceeds Python's verified coverage, passes the original-client replacement workflows, and boots the audited server-only checkout against separate authorized runtime data. Keep client acceptance distinct from headless tests. [Cutover criteria](RUST_MIGRATION.md) |

The first gameplay acceptance group remains Soldier: 76 and the Practice Range training bots. The shared mechanisms must support the remaining heroes and maps through data and measured behavior. Record completed evidence in the changelog and verification record, and leave restoration milestones open until their full criteria pass.
