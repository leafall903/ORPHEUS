# Shared behavior contracts

Echo Flight force contract (`echo-flight-force-r2-v1.json`)
records the original1BB6 node26 vertical SET0 followed by direction*28 ADD.
The runtime consumer exercises actual Flight input and actual enemy0043 melee:
one30 damage, one force, world1051 -> recipient9052, owner HIT5051 unchanged.
The kind16 dispatch evidence establishes that this hook does not suppress the
velocity queue; finite host steering, native transform equality and real client
adoption remain bounded separately. This is not a native acceptance fixture.

Python behavior fixes should leave an executable contract that Rust can consume. Include a small fixture describing the changed input and expected result in behavior-changing pull requests. Use a synthetic case or material approved for publication. Pin the reference revision and state what the evidence establishes, including any original-client acceptance still pending.

Backfill older behavior as it is investigated or changed. When a behavior needs restricted data or an unavailable client observation, record the reproduction, missing prerequisite and follow-up instead of inventing a public fixture or a passing result. A focused fix can proceed with its available regression and an explicit evidence gap.

## NPC Gallery preview policy

`fixtures/behavior/gallery-preview-policy-v1.json` and
`scripts/check_gallery_preview_policy.py` share the Rust Halloween `19abbb8c`
C++ production policy. Run the script with a C++17 compiler (x64 MSVC on Windows,
or `c++` on Linux). It compiles the real header against 18 resource-selection
and six owned-builder-tree cases, including exact 14-NPC substitutions, playable/
unmatched preservation and failed tree validation without mutation.
The relay DLL must also compile; this synthetic policy test does not execute
native hooking or prove original-client menus, animations or model rendering.

The Python Halloween pursuit repair retains the existing shared navigation
policy and passes the missing Match through its three consumers. Its current
reproduction is the prepared `test_game_scene_host` authored Reaper fixture,
which requires real physical displacement toward a distant target. Restricted
authored graph/collision inputs are not published. A source-only cross-language
pursuit fixture and original-client observation remain pending; this scoped
replay must not be reported as full NPC AI or Normal outcome parity.

## First contract

The [JAM framing fixture](../fixtures/behavior/jam-framing-v1.json) contains four synthetic payloads and their expected encrypted stream. Its key is the public byte sequence `00` through `1f`. The fixture comes from the pinned Python framing implementation and matches the pre-existing Rust framing vector. It establishes synthetic protocol agreement. Its original-client acceptance remains pending.

The [Python contract test](../tests/public/test_behavior_contracts.py) calls the real `send_frame` and `FrameReader` APIs. It checks the expected ciphertext, every two-part fragmentation of the stream, and one-byte delivery. Run it from an installed development checkout.

```powershell
python -B -m unittest tests.public.test_behavior_contracts
```

Normal public test discovery also runs this contract. For cross-language checks, set `ORPHEUS_BEHAVIOR_FIXTURES` to the other checkout's `fixtures/behavior` directory so both consumers read the same file. Without that setting, the test uses this repository's fixtures. Record the exact Python and Rust revisions used for a comparison.

## Strict World queries

The [World query fixture](../fixtures/behavior/world-queries-v1.json) records eleven synthetic cases: known origin and position, known unset, missing and populated foreign variables, unavailable position and foreign-variable services, and a null entity member read. Its pinned Python source establishes the strict host's query policy. It is not original-client evidence.

The [Python consumer](../tests/public/test_world_query_contracts.py) creates a fresh production `Component(strict=True)` and graph instance for every case, then calls `instance.evaluate`. Its World adapter supplies only the fixture's positions and variable values. Missing positions return `None`; an unavailable entity-variable service returns `NotImplemented`, while a known variable table with a missing key returns `None`. The consumer checks fixed result types and values or the exact unsupported-query diagnostic, including its entity and variable identifier.

```powershell
$env:PYTHONPATH = 'src'
python -S -B -m unittest tests.public.test_world_query_contracts
```

This source-only check needs neither installed dependencies nor prepared runtime data. It uses the same `ORPHEUS_BEHAVIOR_FIXTURES` override as the framing consumer. The contract covers query results and error categories only: Python's strict host faults and stops the component, while Rust records an expression issue and declines that evaluation. It does not establish equivalent component recovery, event order, live foreign-variable watchers or client behavior. Original-client acceptance remains pending.

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

Mei's [scriptless frost fixture](../fixtures/behavior/mei-scriptless-frost-v1.json)
pins the damage-register input and expected status for an enemy with no body
script. `tests/runtime/test_mei_scriptless_frost.py` consumes it and separately
exercises actual spray commands and owner HIT feedback. The ice presentation
regression checks single creates, existing-VM stream identity, live snapshots
for both viewers and retirement. Original-client presentation remains pending.
The [ice wire fixture](../fixtures/behavior/mei-ice-wire-v1.json) also checks
decoded component51 initial/damaged pools, team/filter bits, the original
25B4 startup graph, and typed builtin001B owner source in H=0 chunks.
The [projectile clock fixture](../fixtures/behavior/mei-projectile-clock-v1.json)
submits the same production commands in 1/8/32-frame batches, compares the
same world query and lifecycle deadlines, and separately checks a real body
replacement with unequal world/command epochs. Floating trajectory comparisons
allow 1e-9 rounding error; release, landing and retirement ticks must agree.
The [encrypted delivery fixture](../fixtures/behavior/mei-projectile-delivery-v1.json)
separately checks physical flight, Shot and owner/observer proxy retirement
with unequal epochs, delayed ACKs and body replacement. Ch2 release decoding
uses the original source-owner labels; protocol success is not native rendering
acceptance.

- State the behavior being changed and link the closest shared fixture or documented reproduction.
- Record its reference revision, evidence category and expected result.
- Run the affected implementation against that result and report any skips or failures.
- Record client presentation, animation, audio and feel as separate observations when applicable.
- Keep credentials, runtime game data, full captures and private paths in local storage. Share synthetic or approved fixtures with enough provenance to repeat the check.

Use the [public source checks](../CONTRIBUTING.md#public-source-checks) for the Python lane and the [Rust parity checklist](https://github.com/leafall903/ORPHEUS/blob/rust/docs/RUST_PARITY.md) for migration acceptance.


<!-- Integrated owner delta: mei-rigid; original patch fd9ad2587c7c1148b0aebea23a5ea6d1e2a765f9af6b35fe41b2befd8e4c2838 -->


## Mei force producers on 1.74

`fixtures/behavior/mei-force-producers-174-v1.json` records the authored force
inventory. `tests/runtime/test_mei_force_producers_174.py` drives actual ability
commands and checks damage/healing separately from force delivery. The new
0636/node64 bridge forwards each live pillar's explicit entry request through
the existing rigid-body host. It does not add a character impulse or duplicate
the shared0043 melee producer. Pillar VM start times are already world time.
The verified host covers its existing spherical rigid-body class only; native
registration, repetition, complete prop association and live-client acceptance
remain open. The shared r2 API and transport are owned by0042.

<!-- Integrated Pet-only v2; rejected v1 is not applied. -->


## Mei ice Pet owner

`mei-ice-wire-v1.json` additionally requires component72 to contain the explicit
creation provider m_D749D7D8 value. The original05FE/node21 and all five0622
creation nodes resolve this to the player controller entity. This is distinct
from their combat/PlayScript body source. Capture it once at allocation and do
not infer it from a later owner body. The decoded owner/observer create and
destroy regressions establish protocol inputs only; native model visibility
and the original frozen-effect chain remain unaccepted.

## Torbjorn retired throw aliases

`fixtures/behavior/torbjorn-retired-throw-aliases-v1.json` specifies delayed owner
report outcomes following an actual host throw and landing. The runtime consumer
`tests/runtime/test_torbjorn_retired_aliases.py` verifies one non-explosive destroy
per late alias, no subsequent create or actor spawn, a fixed report/grace deadline,
and rejection after source-VM replacement. It also exports the stored terminal
record with its original VM identity and verifies a lost destroy is retried through
the production encrypted delivery queue. Prepared runtime data stays external.
This contract does not establish native duplicate-model, upgrade, weapon or lava
acceptance, or the unresolved original Pet source and VM initialization order.

## Explicit descriptor source

`fixtures/behavior/statescript-descriptor-source-v1.json` pins synthetic descriptor bytes for omitted, zero, launcher and maximum round-trippable uint32 sources. `tests/public/test_descriptor_source_contracts.py` exercises both writers, decoding, explicit runtime opt-in, owner/observer snapshots, initial and delta transport, delayed snapshots and unacknowledged source reversion. Descriptor source is the native factory input, not the attachment entity or Pet owner. Omitted and explicit zero differ on wire; native factory zero fallback resolves the current entity before initialization. External target-build parser/factory/cache replay establishes that bounded initialization order; native rendering, weapon visibility, turret upgrades and Pet ownership remain pending.

## Torbjorn pool team metadata

`fixtures/behavior/torbjorn-pool-team-metadata-v1.json` records the existing native component26/team-bit wire contract for team0, team1 and FFA. `tests/runtime/test_torbjorn_team_metadata.py` consumes it through original ultimate/fire input and encrypted self, ally, enemy and delayed joins, while preserving the independent launcher descriptor. It also checks the already present body/turret team metadata and frozen pool birth bits. Original graph inventory and the isolated native null-teamColor consumer remain external research: neither component26 nor descriptor source alone proves original-client colors. No fixed color, material replacement or speculative Pet binding is supplied. Torbjorn has no barrier creation path in the inspected original graph bundle; gun/hammer/body FX, rivets, turret carrier/actor/shots, Overload, Molten globules/pools and victim burn FX are applicable and their final rendered relation remains pending.

Echo copied native healing charge uses
`fixtures/behavior/echo-copied-native-heal-charge-v1.json`. Original1544/node5
measures a positive342 meter delta and node4 adds delta times5.5, capped at338.
An already credited copied Soldier heal must receive only that bonus; zero
effective healing and a retired copied-body source must add no charge. The
actual command-driven copied-field regression retains its6.5 total expectation.
This contract does not accept native effect colours or copy animation timing.
