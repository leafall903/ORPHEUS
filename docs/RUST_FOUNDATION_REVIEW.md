# Rust foundation review and task tracker

This record follows the maintainer-supplied review dated 2026-11-12. The starting Rust source is `13acf15`. The supplied review was a source inspection, with no independent build or test results. This tracker records verified source relationships and acceptance work separately from its proposed improvements. Gameplay completion remains in the [restoration roadmap](ROADMAP.md) and [parity checklist](RUST_PARITY.md).

## Validation and baseline

For each implementation step, record the source revision, exact commands and results before committing that step. Keep authorized runtime data, logs and captures in local storage. Public fixtures must use synthetic inputs or material approved for publication.

```text
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --features default -- -D warnings
cargo test --locked --workspace --all-targets --features default
cargo clippy --locked --workspace --all-targets --features inspector -- -D warnings
cargo test --locked --workspace --all-targets --features inspector
git diff --check
```

Run the prepared tests against a named 104319 runtime lane and record each passing, failing, skipped or timed-out test. Some ignored tests require different graph bundles or an explicit benchmark setting. A skipped workload or an unmet prerequisite supplies no passing evidence. Retain every baseline pass after each relevant boundary change and explain any changed prerequisite. Original-client checks have their own evidence record.

Baseline passed with 354 default and 357 inspector public tests, 65 prepared base-pack tests and one enriched-pack test. The [baseline record](RUST_FOUNDATION_BASELINE.md) lists every prepared test and its prerequisites.

## Tasks

The numbers retain the supplied task list. A task is complete only when its stated scope and validation pass. The first implementation slices are the independent wire boundary and CI coverage. Later changes build on those checks.

| Item | Source finding at `13acf15` | Next acceptance step | Status |
| --- | --- | --- | --- |
| 0. Baseline | The source snapshot has existing public and opt-in prepared tests. | Record both public lanes and per-test prepared results, including hangs and missing inputs. | Recorded |
| 1. Domain errors | Dedicated codec, snapshot and VM fault types coexist with broad `io::Error` adapters. | Migrate one boundary whose callers need to distinguish faults and preserve the underlying cause. | Combat, room and worker boundaries implemented with typed causes; other protocol, runtime and VM boundaries remain open |
| 2. Wire crate | Six codec modules form an independent dependency group. Scene and replication coupling is a separate issue. | Extract the six modules with stable server import paths, then verify isolated and workspace tests. | First six modules complete, graph adapters and scene boundaries open |
| 3. Typed StateScript IR | The existing graph compiler builds structural indexes. The VM dispatches on authored class names and JSON field shapes. Expressions can depend on live state. | Lower one verified operation family through the normal loader and compare VM and wire results. | Open |
| 4. World queries | Both the VM host and expression context could substitute origin or Null for unavailable queries. | Distinguish unavailable queries from known origin and known unset variables through the complete expression path. | Complete for this boundary: required Option host queries, typed diagnostics, VM/watcher tests and 11 shared cases pass; all 66 prepared baseline checks retained; all five hosted jobs passed in PR #30 |
| 5. Room fault isolation | One worker owns rooms and shared peer maps. Invariant assumptions include `expect`, indexing and a discarded tick error. | Introduce complete room retirement and prove another room survives an injected fault. | Room-owned recovery verified locally: 375/378 public tests, all 66 prepared baseline checks and two new prepared regressions pass; hosted verification pending |
| 6. Test infrastructure | Synthetic vectors and prepared regressions exist. A shared cross-language fixture runner and differential CI were missing. | Add versioned synthetic fixtures for protocol and query boundaries and run both implementations against them. | JAM framing and strict World queries pass both consumers and hosted differential CI; gameplay event/loss fixtures, properties and fuzz remain open |
| 7. CI | Public Windows CI covers stable Rust with default and inspector features. | Add a locked MSRV build and Linux tests for independent crates, then observe their CI results. | Complete: all five jobs passed in PR #28 and are required by Rust branch protection |
| 8. Benchmarks | Worker timing histograms and a two-room workload exist. Isolated VM and solver measurements are pending. | Record matched release measurements before changing their hot paths. | Open |
| 9. Dedicated server, ECS, scripts and inspection | The native server uses Bevy ECS for bots and projectiles, a bounded Lua patrol pilot and an optional read-only inspector. Player authority and general script hosting remain incomplete. | Migrate one shared mechanism with lifecycle, replay and performance evidence. | Partial |

## Boundary decisions

### Domain errors

Keep existing `CodecError`, `SnapshotError` and `InitFault` semantics. Introduce a domain error where a module caller must choose recovery, rejection or room retirement. Preserve source errors and classify protocol, runtime-data and script failures at their own boundaries. `thiserror` is an implementation option when it simplifies those definitions. A global replacement of `io::Error` has no defined behavioral acceptance criterion.

The first implemented boundary uses `CombatError` for ECS identity/component failures, collision query failures and respawn construction, with source errors retained. `RoomError` separates room invariants, combat, encoding, reliable delivery and panics; `WorkerError` reports shared routing or transport failure. Existing public I/O adapters retain their signatures. Malformed input rejection, ordinary leave grace, bot script failure and flight ray-budget exhaustion keep their previous narrower recovery scope. Further domain migrations remain open.

### Independent wire extraction

The first group contains `bits`, `commands`, `jam_cipher`, `jam_codec`, `jam_framing` and `game_link`. These modules depend on each other and existing libraries without depending on the server, ECS or Lua. Preserve existing `orpheus::...` imports with re-exports. The collection encoder also needs the shared array-count limit, so its visibility must remain sufficient across the new crate boundary.

`jam_handshake` owns stream I/O, deadlines and randomness. `script_wire` currently consumes `Graph` and `ModeValue` and exposes package-local helpers to the snapshot encoder. Separate its graph adapters and define the helper interface before extracting it. `script_stream` also consumes game-clock and entity-update types. Keep those follow-ups explicit.

Immutable scene preparation still depends on combat and owner-runtime interfaces. Split those interfaces before extracting the corresponding simulation modules. Move player body state into room authority before claiming complete shared-room isolation.

### Typed StateScript IR

Preserve authored node and graph indices, remote slots, flags, links and link order because replication uses them. Support named classes as well as hashed class identifiers. Static lowering can decode a known field shape once. Live predicates and dynamic expressions still evaluate at their original event time.

Retain diagnostics for unsupported operations and the existing behavior for reached unsupported paths. An unused graph with unsupported nodes can remain in a loaded pack. A static coverage report complements the dynamic preflight. Any temporary legacy path must preserve execution budgets, timer and event ordering, random-consumption order and diagnostic provenance.

Start with one operation family and compare initial state, input transitions, events and encoded snapshots. A fixed-frame wait representation needs evidence that its operand is static. Measure hot-path cost after expression lowering as well as dispatch changes.

### Explicit unavailable World queries

`World` in `script_init.rs` now requires explicit `Option` implementations for position and foreign-variable queries. `BodyWorld` and `InstanceContext` propagate availability into the expression evaluator. Unavailable entity queries produce typed diagnostics with their entity, variable and executing graph site. Existing nonentity coercions remain separate from entity-query availability.

For entity variables, `Some(Value::Null)` represents a known entity with an unset variable, while `None` represents an unavailable query. Own-entity reads retain their watchers. A mode table that exists but lacks a key returns known unset; an unavailable mode table cannot supply that fact. General room-query coverage remains open. Apply any evidenced retail fallback at the operation that requires it.

Public tests cover known origin, unknown positions in arithmetic and distance, known unset and unavailable variables, null references, own-variable watcher updates and mode lookups. Eleven shared synthetic cases reproduce query outcomes from the pinned Python strict host; the Python consumer is in [PR #29](https://github.com/leafall903/ORPHEUS/pull/29). All 66 prepared baseline checks pass, including Soldier startup, fire, reload and Sprint. Python strict component failure and Rust expression diagnostics have different lifecycles; neither these fixtures nor Python defaults establish original-client semantics.

### Room fault isolation

The review found shared peer state, assumed room associations and a discarded `finish_tick` error. No externally triggerable panic was demonstrated. The implementation now keeps peer and gameplay state under each room, with fallible combat queries and stage results. Shared worker state contains routing, ordered scheduling and transport links.

The idempotent retirement path detaches the damaged room, revokes every associated link and removes scheduled work and routes, including attributable orphaned entries. Cross-room ownership conflicts are worker failures rather than guessed repairs. Admission prepares locally and rolls back partial reservations. A room-local `catch_unwind` fallback includes only that room, immutable inputs and, during transmission, a sender borrowing one connection. It requires `panic=unwind`; allocation failure, aborts and panicking destructors are outside its guarantee.

The public [recovery contract](BEHAVIOR_CONTRACTS.md#rust-room-recovery-policy) covers encrypted requests, stale links, waiting members, surviving-room traffic and subsequent admission. Additional regressions cover partially modified state, orphaned indexes, ambiguous ownership, bounded diagnostics, transactional respawn failures and delayed projectile generations. The prepared two-room test exercises real component failure and input panic, then checks surviving input, world packets, damage, patrol and new-room admission. It uses authorized local data and a synthetic weapon; it does not establish original-client gameplay or same-room multiplayer acceptance.

Room operations and fault types now live in `game_service/room.rs`, routing and scheduling in `game_service/state.rs`, and fault acceptance tests in `game_service/tests/isolation.rs`. The existing inline service tests still remain in `game_service.rs`. The large `script_init.rs` and its JSON dispatch remain unchanged by this slice; splitting and lowering them is task 3, not a completed part of room isolation.

## Reference and test work

- [x] Define a versioned [synthetic JAM framing contract](BEHAVIOR_CONTRACTS.md) with target build, pinned reference, explicit payloads and expected encrypted bytes and decoded payloads. Both implementations consume the same JSON. Python contribution rules and its runner landed in [PR #27](https://github.com/leafall903/ORPHEUS/pull/27).
- [x] Run that fixture through Python and Rust locally and in hosted CI, pinned to the Python consumer commit. [PR #28](https://github.com/leafall903/ORPHEUS/pull/28) passed both Windows public lanes, MSRV, Linux portable tests and the shared contract before merging.
- [ ] Extend shared contracts to gameplay state and event order, including seed, logical frame, inputs and loss schedule. Keep original traces and restricted runtime data local.
- [ ] Inspect the `kjs-picks` research on account kill-feed options, bot datagram composition and observer variable-binding widths. Reproduce each claim against the relevant 104319 trace or fixture before changing Rust. Record its source revision and acceptance result.
- [ ] Add bounded-input fuzz targets for independent parsers and retain any discovered regression as a focused test.
- [ ] Cover bit widths, truncation and alignment with round-trip and invalid-input properties. Reuse existing vector and snapshot formats where adequate.
- [ ] Add measured VM and contact-solver workloads with fixed inputs, build profile, toolchain and host conditions. Compare elapsed cost and allocations where measured separately from client latency.
- [ ] Add a short contributor walkthrough that runs public checks and explains one operation's decode, execution, replication and test path.

Use `kjs/offline` as a separately attributed research source. Evaluate observed behavior through fixtures and the authored-data architecture. Importing its vendored server and hand-written hero tables wholesale is outside this migration plan. Proposed tools such as `cargo-fuzz`, `proptest`, `insta` and `criterion` should be adopted where they address a specific gap in the current tests or measurements.

## Shared implementation direction

Follow the [Rust contribution rules](NATIVE_SERVER_ARCHITECTURE.md#rust-contribution-rules). Rust owns I/O, protocol validation, simulation scheduling, entity lifetimes, controllers and collision. Authored graphs and catalogs carry target-build behavior. Extend the existing event-driven Lua host for policy and AI decisions through typed, bounded commands. Retain deterministic event order and failure isolation as the host expands.

Use the optional inspector's tick-stamped actors and loaded collision sources to investigate mesh or route discrepancies. Record shape, height, filter, position and first contact before changing data. Read-only visualization already exists. Write-capable administrative actions need an explicit command and authorization boundary. Reuse the current standalone Bevy ECS, Lua host and query mechanisms across heroes and maps.

Track completed slices, test results and remaining limitations in the [changelog](../CHANGELOG.md). Leave gameplay milestones open until their required original-client evidence passes.
