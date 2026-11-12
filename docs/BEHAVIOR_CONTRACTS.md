# Shared behavior contracts

Python and Rust behavior changes should leave a language-neutral fixture containing explicit inputs and independently stored expected bytes, events or state. Keep the fixture on the normal implementation path. Record a pinned source reference and separate synthetic agreement from original-client acceptance. Backfill older behavior as it is investigated or changed.

If a case needs restricted data or an unavailable client observation, record the reproduction, prerequisite and follow-up with the available regression. Do not invent a public fixture or a passing result. Public fixtures use synthetic inputs or material approved for publication; credentials, game data and full captures stay local.

## First shared contract

The [JAM framing fixture](../fixtures/behavior/jam-framing-v1.json) has four synthetic payloads, a public `00` through `1f` byte-sequence key and fixed expected encrypted bytes. It preserves the pre-existing Rust vector derived from the pinned Python implementation. The [Rust test](../rust/crates/wire/src/jam_framing.rs) uses the production sender and reader, checking wire bytes, every two-part stream split and one-byte delivery.

```powershell
cargo test --locked -p orpheus-wire shared_framing_contract
```

The Python contract test lives on the Python branch. Its `ORPHEUS_BEHAVIOR_FIXTURES` setting lets it consume this checkout's exact file. The `shared-behavior-contracts` job in the [public workflow](../.github/workflows/public-checks.yml) checks out a pinned Python test revision in a separate directory and runs both implementations against this fixture. It needs no game installation, runtime pack or authentication input. See the workflow's reference SHA and the fixture's source SHA when reproducing a comparison: the former pins the consumer, the latter records the expected result's provenance.

Both implementations must agree with the stored result; a passing comparison does not prove original-client behavior. The framing case establishes only synthetic JAM stream agreement. Gameplay event/state contracts, loss schedules and original-client acceptance remain open.

## Strict World queries

The [World query fixture](../fixtures/behavior/world-queries-v1.json) records 11 synthetic expression cases against the Python strict host at the pinned source revision. Each input includes the authored-format expression and explicit host tables: `positions`, foreign `members`, owner identity and any typed instance variables. A missing position is unavailable. A known member table with an absent key or explicit null is known unset; an absent member table is unavailable. Expected values retain their type, and expected errors identify the unavailable query and entity.

The [Rust integration test](../rust/tests/world_queries.rs) feeds those inputs to the production expression evaluator. Internal VM tests also cover propagation through the host adapter, diagnostics, local-variable watchers, mode-variable availability and null references.

```powershell
cargo test --locked -p orpheus --test world_queries
```

The Python consumer uses the production strict component and expression evaluator. CI gives both consumers the same fixture file. This establishes query results and diagnostic categories only: Python strict failures stop the component, while Rust records an expression issue and declines the affected operation. Component recovery and fault lifecycle equivalence require separate contracts. Neither this fixture nor its reference revision establishes original-client presentation or gameplay acceptance.

## Format and evolution

| Field | Meaning |
| --- | --- |
| `schema_version` | Version of the JSON contract envelope, initially `1` |
| `case_id` | Stable identifier for the case and its semantic revision |
| `target_build` | Client build being researched, currently `104319` |
| `source` | Pinned source revision and paths establishing the expected result |
| `evidence` | Evidence category, such as `synthetic-reference` |
| `client_acceptance` | Client observation status; `pending` supplies no client evidence |
| `input` | Explicit inputs, using hexadecimal strings for byte arrays |
| `expected` | Independently stored expected bytes, decoded events or state |

For time-dependent gameplay, also specify initial state, logical frames, seed, input order and packet-loss schedule wherever they affect the result. Compare stable decoded fields when identifiers or nonces vary. Update the semantic case version for an intentional incompatible behavior change, explaining the evidence for the new expected result. Consumers must reject unsupported schema or case versions rather than silently using a different contract.

Keep the same fixture bytes in both branch changes, and update the pinned comparison consumer deliberately when importing a new case. A new Python contract can land before its Rust implementation; record the parity gap instead of changing expected output to make Rust pass. The current CI job covers framing and strict World queries, not arbitrary future fixtures. New cases need both a consumer test and an explicit CI command or runner registration.

Follow the [contribution guide](../CONTRIBUTING.md) and update the [parity checklist](RUST_PARITY.md) when the affected migration workflow changes.
