# Shared behavior contracts

Python and Rust behavior changes should leave a language-neutral fixture containing explicit inputs and independently stored expected bytes, events or state. Keep the fixture on the normal implementation path. Record a pinned source reference and separate synthetic agreement from original-client acceptance. Backfill older behavior as it is investigated or changed.

If a case needs restricted data or an unavailable client observation, record the reproduction, prerequisite and follow-up with the available regression. Do not invent a public fixture or a passing result. Public fixtures use synthetic inputs or material approved for publication; credentials, game data and full captures stay local.

## First shared contract

The [JAM framing fixture](../fixtures/behavior/jam-framing-v1.json) has four synthetic payloads, a public `00` through `1f` byte-sequence key and fixed expected encrypted bytes. It preserves the pre-existing Rust vector derived from the pinned Python implementation. The [Rust test](../rust/crates/wire/src/jam_framing.rs) uses the production sender and reader, checking wire bytes, every two-part stream split and one-byte delivery.

```powershell
cargo test --locked -p orpheus-wire shared_framing_contract
```

The Python contract test lives on the Python branch. Its `ORPHEUS_BEHAVIOR_FIXTURES` setting lets it consume this checkout's exact file. The `shared-behavior-contracts` job in the [public workflow](../.github/workflows/public-checks.yml) checks out a pinned Python test revision in a separate directory and runs both implementations against this fixture. It needs no game installation, runtime pack or authentication input. See the workflow's reference SHA and the fixture's source SHA when reproducing a comparison: the former pins the consumer, the latter records the expected result's provenance.

Both implementations must agree with the stored result; a passing comparison does not prove original-client behavior. This first case establishes only synthetic JAM stream framing agreement. Gameplay event/state contracts, loss schedules and original-client acceptance remain open.

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

Keep the same fixture bytes in both branch changes, and update the pinned comparison consumer deliberately when importing a new case. A new Python contract can land before its Rust implementation; record the parity gap instead of changing expected output to make Rust pass. The current CI job covers this first registered case, not arbitrary future fixtures. New cases need both a consumer test and an explicit CI command or runner registration.

Follow the [contribution guide](../CONTRIBUTING.md) and update the [parity checklist](RUST_PARITY.md) when the affected migration workflow changes.
