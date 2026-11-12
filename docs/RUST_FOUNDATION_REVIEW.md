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
| 1. Domain errors | Dedicated codec, snapshot and VM fault types coexist with broad `io::Error` adapters. | Migrate one boundary whose callers need to distinguish faults and preserve the underlying cause. | Open |
| 2. Wire crate | Six codec modules form an independent dependency group. Scene and replication coupling is a separate issue. | Extract the six modules with stable server import paths, then verify isolated and workspace tests. | First six modules complete, graph adapters and scene boundaries open |
| 3. Typed StateScript IR | The existing graph compiler builds structural indexes. The VM dispatches on authored class names and JSON field shapes. Expressions can depend on live state. | Lower one verified operation family through the normal loader and compare VM and wire results. | Open |
| 4. World queries | Both the VM host and expression context can substitute origin or Null for unavailable queries. | Distinguish unavailable queries from known origin and known unset variables through the complete expression path. | Open |
| 5. Room fault isolation | One worker owns rooms and shared peer maps. Invariant assumptions include `expect`, indexing and a discarded tick error. | Introduce complete room retirement and prove another room survives an injected fault. | Open |
| 6. Test infrastructure | Synthetic vectors and prepared regressions exist. A shared cross-language fixture runner and differential CI were missing. | Add a versioned synthetic fixture for one protocol or gameplay boundary and run both implementations against it. | First JAM framing contract passes both implementations locally; differential CI added, hosted result pending; gameplay fixtures, properties and fuzz remain open |
| 7. CI | Public Windows CI covers stable Rust with default and inspector features. | Add a locked MSRV build and Linux tests for independent crates, then observe their CI results. | Implemented, Rust 1.85.0 passed locally, hosted Linux result pending |
| 8. Benchmarks | Worker timing histograms and a two-room workload exist. Isolated VM and solver measurements are pending. | Record matched release measurements before changing their hot paths. | Open |
| 9. Dedicated server, ECS, scripts and inspection | The native server uses Bevy ECS for bots and projectiles, a bounded Lua patrol pilot and an optional read-only inspector. Player authority and general script hosting remain incomplete. | Migrate one shared mechanism with lifecycle, replay and performance evidence. | Partial |

## Boundary decisions

### Domain errors

Keep existing `CodecError`, `SnapshotError` and `InitFault` semantics. Introduce a domain error where a module caller must choose recovery, rejection or room retirement. Preserve source errors and classify protocol, runtime-data and script failures at their own boundaries. `thiserror` is an implementation option when it simplifies those definitions. A global replacement of `io::Error` has no defined behavioral acceptance criterion.

### Independent wire extraction

The first group contains `bits`, `commands`, `jam_cipher`, `jam_codec`, `jam_framing` and `game_link`. These modules depend on each other and existing libraries without depending on the server, ECS or Lua. Preserve existing `orpheus::...` imports with re-exports. The collection encoder also needs the shared array-count limit, so its visibility must remain sufficient across the new crate boundary.

`jam_handshake` owns stream I/O, deadlines and randomness. `script_wire` currently consumes `Graph` and `ModeValue` and exposes package-local helpers to the snapshot encoder. Separate its graph adapters and define the helper interface before extracting it. `script_stream` also consumes game-clock and entity-update types. Keep those follow-ups explicit.

Immutable scene preparation still depends on combat and owner-runtime interfaces. Split those interfaces before extracting the corresponding simulation modules. Move player body state into room authority before claiming complete shared-room isolation.

### Typed StateScript IR

Preserve authored node and graph indices, remote slots, flags, links and link order because replication uses them. Support named classes as well as hashed class identifiers. Static lowering can decode a known field shape once. Live predicates and dynamic expressions still evaluate at their original event time.

Retain diagnostics for unsupported operations and the existing behavior for reached unsupported paths. An unused graph with unsupported nodes can remain in a loaded pack. A static coverage report complements the dynamic preflight. Any temporary legacy path must preserve execution budgets, timer and event ordering, random-consumption order and diagnostic provenance.

Start with one operation family and compare initial state, input transitions, events and encoded snapshots. A fixed-frame wait representation needs evidence that its operand is static. Measure hot-path cost after expression lowering as well as dispatch changes.

### Explicit unavailable World queries

`World` in `script_init.rs`, `BodyWorld` in `script_player.rs` and `Context` in `script_expr.rs` all participate in the current fallback behavior. `InstanceContext` forwards those queries into expressions. Length and distance expressions also supply fallback values, while vector arithmetic assumes successful resolution.

Use an explicit unavailable result throughout those paths. For entity variables, `Some(Value::Null)` represents a known entity with an unset variable, while `None` represents an unavailable query. Keep own-entity reads and their watchers intact. Apply any evidenced retail fallback at the operation that requires it.

Cover known origin, unknown foreign position in arithmetic and distance, known unset variable, unavailable foreign-variable service, own-variable watchers, mode lookups and prepared Soldier startup, fire, reload and Sprint. Python defaults alone do not establish original-client semantics.

### Room fault isolation

`game_service.rs` assumes consistency across rooms, seats and peers through both `expect` and indexed access. Its tick path currently turns a `finish_tick` error into an empty result. `game_combat.rs` assumes the wire index and ECS body components remain consistent. No externally triggerable panic has been demonstrated by this review.

Implement an idempotent room-retirement path that removes every associated peer, seat, link, projectile and pending operation even if one association is absent. Propagate recoverable stage errors with room identity. Any unwind fallback must retire the failed room and its peers before continuing and account for the build's panic strategy.

Inject a failure into one of two active rooms, verify the other continues receiving inputs and ticks, and verify a new room can be admitted. Include failure during respawn or body replacement. A whole-worker unwind catch alone does not establish safe recovery of shared state.

## Reference and test work

- [x] Define a versioned [synthetic JAM framing contract](BEHAVIOR_CONTRACTS.md) with target build, pinned reference, explicit payloads and expected encrypted bytes and decoded payloads. Both implementations consume the same JSON. Python contribution rules and its runner are proposed in [PR #27](https://github.com/leafall903/ORPHEUS/pull/27).
- [x] Run that fixture through Python and Rust locally. Add differential CI pinned to the Python consumer commit; hosted verification is pending.
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
