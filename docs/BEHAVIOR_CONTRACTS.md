# Shared behavior contracts

Python behavior fixes should leave an executable contract that Rust can consume. Include a small fixture describing the changed input and expected result in behavior-changing pull requests. Use a synthetic case or material approved for publication. Pin the reference revision and state what the evidence establishes, including any original-client acceptance still pending.

Backfill older behavior as it is investigated or changed. When a behavior needs restricted data or an unavailable client observation, record the reproduction, missing prerequisite and follow-up instead of inventing a public fixture or a passing result. A focused fix can proceed with its available regression and an explicit evidence gap.

## First contract

The [JAM framing fixture](../fixtures/behavior/jam-framing-v1.json) contains four synthetic payloads and their expected encrypted stream. Its key is the public byte sequence `00` through `1f`. The fixture comes from the pinned Python framing implementation and matches the pre-existing Rust framing vector. It establishes synthetic protocol agreement. Its original-client acceptance remains pending.

The [Python contract test](../tests/public/test_behavior_contracts.py) calls the real `send_frame` and `FrameReader` APIs. It checks the expected ciphertext, every two-part fragmentation of the stream, and one-byte delivery. Run it from an installed development checkout.

```powershell
python -B -m unittest tests.public.test_behavior_contracts
```

Normal public test discovery also runs this contract. For cross-language checks, set `ORPHEUS_BEHAVIOR_FIXTURES` to the other checkout's `fixtures/behavior` directory so both consumers read the same file. Without that setting, the test uses this repository's fixtures. Record the exact Python and Rust revisions used for a comparison.

## Fixture fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Version of this JSON contract format, initially `1` |
| `case_id` | Stable identifier for the behavior and fixture revision |
| `target_build` | Client build the project targets, currently `104319` |
| `source` | Pinned source revision and the paths used to establish the expected result |
| `evidence` | Evidence category, such as `synthetic-reference` for this case |
| `client_acceptance` | Client observation status, including `pending` when no matching client result exists |
| `input` | Explicit operation inputs, with byte arrays encoded as hexadecimal strings |
| `expected` | Independently stored expected bytes, decoded events or registered state |

Keep each fixture tied to a concrete behavior. For time-dependent gameplay, also specify the initial state, simulation frames, seed, input order and packet-loss schedule that affect the result. Compare stable decoded fields when generated identifiers or cryptographic nonces vary. Keep tests on the normal implementation path and retain the fixed expected output when refactoring.

## Contribution checklist

- State the behavior being changed and link the closest shared fixture or documented reproduction.
- Record its reference revision, evidence category and expected result.
- Run the affected implementation against that result and report any skips or failures.
- Record client presentation, animation, audio and feel as separate observations when applicable.
- Keep credentials, runtime game data, full captures and private paths in local storage. Share synthetic or approved fixtures with enough provenance to repeat the check.

Use the [public source checks](../CONTRIBUTING.md#public-source-checks) for the Python lane and the [Rust parity checklist](https://github.com/leafall903/ORPHEUS/blob/rust/docs/RUST_PARITY.md) for migration acceptance.
