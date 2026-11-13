# Shared behavior contracts

Python and Rust behavior changes should leave a language-neutral fixture containing explicit inputs and independently stored expected bytes, events or state. Keep the fixture on the normal implementation path. Record a pinned source reference and separate synthetic agreement from original-client acceptance. Backfill older behavior as it is investigated or changed.

If a case needs restricted data or an unavailable client observation, record the reproduction, prerequisite and follow-up with the available regression. Do not invent a public fixture or a passing result. Public fixtures use synthetic inputs or material approved for publication; credentials, game data and full captures stay local.

## First shared contract

The separate [NPC preview policy fixture](../fixtures/behavior/gallery-preview-policy-v1.json)
has 18 resource cases and six owned-tree cases. Its C++ consumer compiles the
production relay header, checking exact resource selection, playable/unknown
fallbacks, child pruning, root-sibling isolation, cycles and the 64-node bound.
Run `python -B scripts/check_gallery_preview_policy.py` from a compiler prompt
(`cl` on Windows, `c++` elsewhere). The dedicated public CI job uses this same
fixture and consumer. This is a reconstructed client preview policy, with no
Python equivalence claim. Native adapter/signature/iterator evidence requires
restricted local inputs and is recorded separately from client rendering.

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

## Entity stack admission

The [stack admission fixture](../fixtures/behavior/statescript-stack-admission-v1.json)
contains seven normal VM cases for entity-stack priority, equality, false
values, mixed scopes and message-driven release. Both the actual expression
query and the Rust host read use the same production arbitration kernel.
The pinned Python consumer uses real stack states and `STU_3ACE35FB`; no test
handler substitutes for linking or release. The restricted Reaper Wraith
control-state probe checks authored integration separately from full ability
and original-client acceptance.

```powershell
cargo test --locked --lib script_init::tests::shared_stack_admission_contract -- --exact
python -S -B scripts/check_stack_admission_contract.py --python-reference 'C:\path\to\e1128509-checkout'
```

## Ability press lifetime

The [ability press-window fixture](../fixtures/behavior/statescript-ability-press-window-v1.json)
checks four normal VM flows: rejection beyond the early window, a gate opening
inside it, a fresh press after expiry and a held button. Both consumers sample
the actual Ability start output through normal component updates. Pending
presses keep their original time until start gates pass. This follows pinned
Python e1128509's `Ability.can_start`/`take_press`; its start-gate reconstruction
is explicitly unverified against the obfuscated PC native branch.

```powershell
cargo test --locked --lib script_init::tests::shared_ability_press_window_contract -- --exact
python -S -B scripts/check_ability_press_window_contract.py --python-reference 'C:\path\to\e1128509-checkout'
```

## Rust Flashbang effect dispatch policy

The [Flashbang effect dispatch fixture](../fixtures/behavior/flashbang-effect-dispatch-v1.json)
defines the scoped Rust host integration policy separately from native combat
fidelity. Five synthetic cases cover admitted Normal hits, wrong mode/graph/node
and source identity. Its normal enemy-pair delivery starts a synthetic 0228
receiver before sending the hit message; the real VM writes the marker and
typed source into body variables. No test-only handler replaces VM execution.
This is a Rust-only policy consumer, with no Python-equivalence claim.

```powershell
cargo test --locked --lib script_enemy::tests::shared_flashbang_effect_dispatch_contract -- --exact
```

The ignored `admitted_flashbang_hits_deliver_effects_to_all_boss_pairs` replay
needs restricted, separately prepared 104319 inputs. It uses a synthetic
admitted physical projectile, actual castle collision, complete five-Boss
body/controller adapters and observer encoding through 120 frames. The separate
E-input replay supplies actual authored projectile launch evidence. These
checks do not establish immunity, exact native hit-reaction presentation or
original-client acceptance; these remain required follow-ups. No game graph,
geometry or capture is embedded in the public fixture.

## Rust room recovery policy

The [room isolation fixture](../fixtures/behavior/room-isolation-v1.json) specifies a synthetic server recovery policy: an error or panic retires the affected room, its seats and links, while another room still answers encrypted requests and a new room can be admitted. The [service test](../rust/src/game_service/tests/isolation.rs) consumes it through the real encrypted UDP service with test-only fault injection. It runs in both public Rust test lanes without a runtime pack. This fixture currently has a Rust consumer only and is not included in the Python/Rust differential job.

```powershell
cargo test --locked -p orpheus encrypted_worker_satisfies_room_recovery_contract
```

The public case establishes connection and room lifecycle recovery. The separate ignored `prepared_fault_keeps_other_room_input_world_and_patrol_alive` test needs an authorized base 104319 pack to check continued world delivery, input, damage and patrol in a surviving active room. Neither case is a captured original-server result or proof of gameplay fidelity. Other policy fixtures may use this envelope, but must identify their consumers and evidence limits explicitly.

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

## StateScript unkeyed array contract

[The array fixture](../fixtures/behavior/statescript-array-insert-v1.json)
contains 19 synthetic cases for merge/single-value insertion, order, gaps,
uniqueness, computed null, index conversion, failed native defaults,
provider visibility and self-iterator termination. Expected states are stored
independently from the Rust implementation and pinned to Python e1128509.
The Rust consumer runs the production VM action; the Python comparison
runner imports that pinned implementation and executes its normal action
handler against these exact fixture bytes. Neither needs game assets.

```powershell
cargo test --locked --lib script_init::tests::shared_unkeyed_array_contract -- --exact
python -S -B scripts/check_array_insert_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

The Windows public workflow explicitly registers the Rust consumer and
checks its presence before running it. The Python consumer is currently
locally verified: the pinned research revision is not an ancestor of public
`main` at 3a7e0424. Availability on another public ref has not been verified,
so a Python CI checkout is not registered on that assumption.
A reviewed public Python comparison consumer and counterpart fixture/CI
registration remain follow-up work before claiming public cross-language
CI coverage. The existing portable shared CI job still covers JAM framing.
Original-client acceptance remains pending, as do the full Boss death-query
services required by the strict complete encounter probe.

## Engine-setting read contract

[The engine-setting fixture](../fixtures/behavior/statescript-engine-setting-v1.json)
contains 14 synthetic cases for CF4A60's typed read and no-write semantics:
false/null versus a missing row, unavailable service diagnostics, preservation
of the supplied initial value, uint32 hash conversion and invalid key types.
The Rust evaluator and Python SceneWorld/Instance evaluator use these same
stored inputs and expected results. A table is supplied explicitly by each
case; the fixture does not claim to recover a real client's settings.

```powershell
cargo test --locked --lib script_expr::tests::shared_engine_setting_contract -- --exact
python -S -B scripts/check_engine_setting_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

Windows CI inventories and executes both StateScript contracts. The Python
runner verifies the exact e1128509 checkout and needs no runtime pack or
third-party module. The same public Python counterpart/CI registration gap
described above remains; local agreement supplies no hosted-CI or original-
client evidence. The full encounter death-query gate is still unresolved.

## Foreign component reads

[The foreign-component fixture](../fixtures/behavior/statescript-foreign-component-v1.json)
contains ten synthetic cases for the declared SceneWorld lifecycle: a bound
entity with no published backend, explicit component absence, an active
component with typed variables or an unset key, and a faulted component.
The Rust production evaluator consumes EntityScriptState; Python uses actual
SceneWorld bindings and Component variables through normal member bytecode.
A faulted or unpublished backend remains unavailable; explicit absence and
unset keys return null. Neither consumer invents properties for unknown hosts.

```powershell
cargo test --locked --lib script_entity_state::tests::shared_foreign_component_contract -- --exact
python -S -B scripts/check_foreign_component_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

The Python runner verifies e1128509458882a325c743f67ebed4a5c0c2f1a7 and needs
no runtime pack. Both Windows feature lanes inventory and execute the Rust
contract. The public Python counterpart/hosted differential CI registration
is still pending, as for the local StateScript contracts above. Separate VM
coverage checks branch updates when the room replaces a foreign snapshot.
The contract establishes read semantics, not same-frame cross-component wakeup
order, actor discovery coverage or original-client behavior.

## Corpse-retirement action routing

[The corpse-action fixture](../fixtures/behavior/statescript-corpse-retirement-v1.json)
contains 12 synthetic cases for the six reference-admitted 523EE38D sites,
body/controller arguments, explicit phase-exit flags, host refusal, invalid
arguments and an unknown entry point. Rust runs the normal graph initializer;
Python runs normal entry/action dispatch and the real reference handler.
Both use the fixture's declared host response and check requested operations,
output continuation and failure. This establishes routing and refusal, not
that an arbitrary host can destroy a real entity or bypass corpse guards.

```powershell
cargo test --locked --lib script_init::tests::shared_corpse_retirement_contract -- --exact
python -S -B scripts/check_corpse_retirement_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

The Python consumer pins e1128509458882a325c743f67ebed4a5c0c2f1a7 and needs
no runtime data. Windows CI inventories/runs the Rust contract in both feature
lanes. The public Python counterpart/differential CI gap above still applies.
Prepared encounter replays must separately cover real paired-body retirement
and the original-client requirement remains open.

## Mover velocity and container membership

[The mover-velocity fixture](../fixtures/behavior/statescript-velocity-v1.json)
contains 19 synthetic cases through the production query evaluator. An
unbound lookup preserves the caller's initial value, an unavailable backend
remains unsupported, and a bound mover returns its finite float32 vector.
Cases cover stationary/moving bodies, float32 rounding/overflow, NaN/Infinity,
typed initial values, first-config scalar selectors and unsupported mover
variants. Rust uses a declared host table; Python uses actual SceneWorld
bindings/readers (or World when the backend is unavailable).

[The contains fixture](../fixtures/behavior/statescript-contains-v1.json)
contains 16 entry/condition/branch cases for stored arrays and singleton VM
values. Normal Rust/Python graph dispatch checks branch markers for null,
empty/nested arrays, bool/number equality, entity identity, float tolerance
and unlinked branches. The new VM handler reuses the bytecode evaluator's
existing membership/equality primitive; it does not introduce a separate
approximation for death scripts.

```powershell
cargo test --locked --lib script_expr::tests::shared_velocity_contract -- --exact
cargo test --locked --lib script_init::tests::shared_contains_condition_contract -- --exact
python -S -B scripts/check_velocity_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
python -S -B scripts/check_contains_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

Both Python runners require exact e1128509458882a325c743f67ebed4a5c0c2f1a7
and no runtime pack. Windows CI inventories and exactly runs both Rust tests
in default/inspector lanes. The local Python consumer/public counterpart and
hosted differential CI distinction above still applies. A separate VM check
polls moving/stationary/unbound host changes; work/depth checks keep scalar
selector traversal bounded. Actual actor discovery, snapshot ordering and
client presentation require their own acceptance.

## Total-health reads

[The total-health fixture](../fixtures/behavior/statescript-health-total-v1.json)
contains 21 synthetic 9B7648C3 cases. The production Rust evaluator and Python
SceneWorld/Instance path distinguish a computed zero, unavailable reader and
unbound no-write result. Cases cover float32 conversion, negative/non-finite/
over-range rejection, typed initial destinations and first-config selectors.
The raw input upper bound is the reference's 3.402823466e38; it is checked
before conversion. The extreme boundary cases use explicit `bits64` strings
for exact f64 inputs/results, avoiding differences between JSON decimal parsers.

```powershell
cargo test --locked --lib script_expr::tests::shared_total_health_contract -- --exact
python -S -B scripts/check_health_total_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

The Python consumer pins e1128509458882a325c743f67ebed4a5c0c2f1a7 and requires
no runtime pack. Windows CI inventories/exactly runs the Rust consumer in
both feature lanes. The public Python counterpart and hosted differential CI
gap above still applies. Separate VM coverage polls health/death/unbinding;
room tests keep corpse health available after live-target exclusion and check
that a broken registry produces an error rather than an empty health table.
These facts establish neither complete actor discovery nor exact same-frame
snapshot ordering or original-client presentation.

## Scoped hostile filters and combat targets

[Hostile filters](../fixtures/behavior/statescript-hostile-filter-v1.json)
contains 17 synthetic cases for first-set team bits, required/excluded tags,
dead-mask short circuits, unknown tags and scalar predicates.
[Combat targets](../fixtures/behavior/statescript-combat-target-v1.json)
contains 12 cases for ordered live-tagged-body selection, strict unavailable
inputs and early matches/rejections. The Rust consumers use production
expression dispatch; the Python runner uses the separately supplied pinned
Python e1128509 SceneWorld, bound entities, real team context and normal
native dispatch. Neither fixture includes captured game assets.

```powershell
cargo test --locked --lib script_expr::tests::shared_hostile_filter_contract -- --exact
cargo test --locked --lib script_expr::tests::shared_combat_target_contract -- --exact
python -S -B scripts/check_hostile_filter_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

Windows CI explicitly inventories and runs both Rust tests in both feature
lanes. Python is locally verified; public differential runner registration
remains open under the pinned-research availability limitation above.
Supported scope is killer filters (05AC/01A3/157C) and literal primary-tag
0CCD combat queries. Witch ally/parameter predicates, full death retirement
and original-client gameplay/presentation acceptance remain open.

## Native filter tag clauses

[The native tag-clause fixture](../fixtures/behavior/statescript-filter-tag-clauses-v1.json)
contains 72 synthetic cases. D0EC70 collects STUConfigVarFilter offsets
+58/+60/+68 into the any/all/excluded arguments of DB2FD0. Restricted emulation
of the original resolved-component tail (DB323B) and DB5030/DB4E70 helpers
matches all expected results with explicit immutable base-tag storage, request
vectors and scratch stack. Eight cases use the three actual 224E/9 exclusion
identifiers. No game bytes or captures are included.

```powershell
cargo test --locked --lib script_expr::tests::shared_native_filter_tag_clauses -- --exact
```

The Rust consumer uses production entity-filter expression dispatch and explicit
known membership. Windows CI inventories it in both feature lanes. This changes
the previous generic Rust interpretation; the pinned Python scene only admits
scoped filter subsets, so generic Python parity is not claimed. Nonempty
C1903771 remains unsupported rather than an invented fourth tag clause. Native
actor classification, component construction, tag lifecycle, complete Flashbang
reaction and original-client presentation are outside this fixture's scope.

## Witch ally-filter contract

[The ally-filter fixture](../fixtures/behavior/statescript-ally-filter-v1.json)
contains 28 synthetic cases for friendly-other relationships, required and
excluded tags, ordered combat candidates, dead-mask policy, candidate parameter
8, twenty-metre distance boundaries, scalar initial values and lexical scope
restoration. Nested-query failures preserve the originating diagnostic class.
The Rust consumer uses production expression dispatch and checks the parameter
stack after successes and errors. The Python runner uses the pinned e1128509
SceneWorld and Component parameter scopes through normal query dispatch.

```powershell
cargo test --locked --lib script_expr::tests::shared_ally_filter_contract -- --exact
python -S -B scripts/check_ally_filter_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

Windows CI explicitly inventories and runs the Rust test in both feature
lanes. Python comparison is local; the public differential-registration gap
above remains. This contract covers 006B/0071's friendly filter subset and
0071 combat queries, not full Witch AI, native voice playback or client
acceptance. The complete Boss probe still requires the shared voice state.

## Strict voice lifecycle contract

[The voice fixture](../fixtures/behavior/statescript-voice-lifecycle-v1.json)
contains 38 synthetic scenarios. Its graph, host inputs and explicit phase
steps are shared unchanged by production Rust VM evaluation and pinned Python
e1128509's normal State/Context callbacks. Expected service traces and state/
output observations were recorded independently from Rust. Exact f64 bit
strings distinguish typed float output and float32 timing results from
integer counters and known unset destinations.

Coverage includes separate update/poll/timer phases, external double clock,
first-config scalar/default behavior, cached/keyed/null stimulus reads,
recursive first-asset selection, ordered unique typed targets, permission and
resource failure, six duration attempts, next-part retries, duration writes,
passive end plugs, explicit failure, abort/destroy/fault cleanup and restart.
All error cases preserve the failing service or query class.

```powershell
cargo test --locked --lib script_init::tests::shared_voice_lifecycle_contract -- --exact
python -S -B scripts/check_voice_lifecycle_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

Windows CI inventories and runs the Rust consumer in both feature lanes.
Python comparison is local under the pinned-research availability gap above.
Room code now has the explicit post-update actor polling phase, but the Boss
speech backend is still unprovided. Legacy intro/gate/victory specializations,
native iterator backends, full component fault-policy equivalence and original
client audio remain outside this contract. No complete Boss/match/UI/voice
acceptance item is closed by these synthetic traces.


## Boss speech backend

[The Boss speech fixture](../fixtures/behavior/junkenstein-boss-speech-v1.json)
contains 191 synthetic cases checked by production Rust policy and the pinned
Python e1128509 `bind_boss_voice` backend. It covers every defeat row, weighted
selection with explicit normalized draws, killer eligibility, default/themed
voice sets, spawn/body/barrier cues, permission and target/site rejection.
Expected notices retain anchor/speaker/cue/line and raw duration f64 bits.
The Python consumer calls the normal notice encoder and verifies notice type
27500; the existing Rust notice wire golden covers its byte construction.

```powershell
cargo test --locked --lib junkenstein_voice::tests::shared_boss_speech_contract -- --exact
python -S -B scripts/check_boss_speech_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

Windows CI explicitly inventories and runs the Rust test in both feature lanes.
The Python comparison is local with the same public-reference availability gap
as the voice lifecycle contract. It checks a clean pinned backend/script tree.
The controlled draws do not establish Python RNG seed-sequence equivalence.
Missing live roster hero facts and mode voice-set facts remain unavailable;
these cases supply known facts and do not reinterpret absence as discovery.
General live voice-set override stacks, legacy intro/gate/victory migration,
original-client sound, and full successful/failed matches remain open.


## Controller component44 body field

[The body-field fixture](../fixtures/behavior/statescript-body-field-v1.json)
contains 18 synthetic 00707E1E queries: known body IDs (including zero/invalid
field values), component absence, unbound targets, unknown fields/services,
recursive first-value selection and first-config groups. Rust evaluates the
production expression; Python uses normal `entity_queries.evaluate` through
SceneWorld/Component at pinned 46b22130. Keep a detached reference checkout;
an actively advancing branch is not a pinned reference. Both consumers reject
unsupported schema/build and the Python runner checks a clean script tree.

```powershell
cargo test --locked --lib script_expr::tests::shared_body_field_contract -- --exact
python -S -B scripts/check_body_field_contract.py --python-reference 'C:\path\to\46b22130-checkout'
```

The same fixture records independently extracted E9485CAF base State factory
provenance. Rust additionally exercises its normal VM classification and
activation; the Python query consumer does not validate this native fallback.
The complete prepared Cassidy probe checks actual 0CA5 startup and typed
body-field state before Deadeye use. General entity-query namespace retirement
and subscription equivalence remain outside this subset. Windows CI inventories
and runs the Rust consumer in both lanes. Python comparison is local; public
availability/hosted CI and original-client acceptance remain unverified.


## Applied damage and charge flow

[The charge fixture](../fixtures/behavior/statescript-charge-v1.json) supplies
31 synthetic flow, hostility and damage-tag cases. Both consumers use normal
VM/evaluator paths and independently stored Python e1128509 expectations.
The synthetic graph expresses the evidenced 0B8B wait/value/cap and ordered
half/three-quarter reductions with explicit admission and ultimate flags.
It covers same-clock polling, cancellation/resumption, zero/live multipliers,
friendly/no-charge targets, two-team/spectator relations and ordered typed
asset tags, retaining exact f64 output bits. Advances replay each intervening
frame; the synthetic component starts at clock zero before frame zero ends.
The real prepared owner is admitted at its spawn-frame end, and its separate
probe checks that corresponding first-passive boundary.

```powershell
cargo test --locked --lib script_init::tests::shared_charge_contract -- --exact
python -S -B scripts/check_charge_contract.py --python-reference 'C:\path\to\e1128509-checkout'
```

The Python hostility cases call the pinned scene's supplied ordinary two-team
service for its declared synthetic binding; they do not reconstruct the whole
native namespace/team subsystem. Rust retains unavailable outcomes outside
that branch and gives explicit native callbacks precedence. Rust additionally
checks that actual hitscan and direct/blast simulation retain their own tags,
and that its 16-tag server admission bound rejects a 17th tag without truncating.
The counterpart tag consumer validates normal Python parsing, not that server
resource bound. Windows CI explicitly inventories and runs the Rust consumer
in both lanes; the normal pinned Python comparison remains local.

Prepared actual Normal Cassidy probes separately verify the real 0B8B graph,
exact damage credit for all five Bosses and all 15 primary/fan/head variants,
foreign-source rejection, native passive fill, Deadeye spend/self-suppression
and encounter-end cancellation. The room refreshes live-defender admission in
simulation phases; it does not depend on presentation or network acknowledgments.
Full native game-state broadcasts, original-client HUD acceptance and complete
successful/failed encounters are outside this contract and remain open.

The charge contract also includes ten common-corpse cases. Python calls the
normal pinned `JunkensteinSceneHost.before_units` and validated retirement
queue with explicit synthetic body/controller services. Rust checks the same
eight-second host policy, including the frame before/at/after the boundary,
alive/future-death, tire/other exclusion and a near-u32-limit frame. The
prepared common-NPC probe checks actual damage, exact charge, pair teardown
and the exact 500-frame grace. This is a pinned scene-host lifetime policy,
not a newly inferred native TTL.

The retired-entity contract (`statescript-retired-entity-v1.json`) contains
eight synthetic body/controller removal, running/unset/absent/faulted component
and unavailable-service cases. Python calls normal scene unbinding, entity
variable slots and installed entity-query/team services; Rust calls the actual
enemy host through the expression evaluator. Known retirement is Null/false
and signed team -1; live team facts survive an unavailable script backend.
Unavailable services remain errors. The room regression separately checks
lifetime coupling in a full Normal wave replay. No complete entity namespace
or original-client presentation is claimed.

```powershell
cargo test --locked --lib script_enemy::tests::shared_retired_entity_contract -- --exact
python -S -B scripts/check_retired_entity_contract.py --python-reference 'C:\path\to\e1128509-checkout'
```

The foreign-notification contract (`statescript-foreign-notification-v1.json`)
uses three synthetic 0C8C/33 SubScript flows and an inert synthetic child. The
normal Python graph loader receives only these input records; live foreign Var
slots and normal callback/transition/teardown handlers remain unchanged. Both
consumers compare parent activity, completion, child lifetime and subscription
counts after each frame. Rust additionally submits duplicate notices to check
state coalescing. This compares frame-visible lifecycle, not exact native
callback times. The actual room separately publishes only real gate variable
changes; no universal foreign producer adapter is claimed.

```powershell
cargo test --locked --lib script_init::tests::shared_foreign_notification_contract -- --exact
python -S -B scripts/check_foreign_notification_contract.py --python-reference 'C:\path\to\e1128509-checkout'
```

The victory-view route contract (`statescript-victory-view-route-v1.json`)
contains three synthetic action cases: scalar start/end view messages and the
separate client-only cue. Python uses the normal strict action handler,
parameter collector and SceneWorld's explicit 0CA5 route; only submission is
recorded. Rust uses the actual VM and defender-message admission. Both check
typed payloads and outgoing continuation. This is not general array-parameter
transport or native camera acceptance. The external prepared child/parent
probe separately checks real 0CEC teardown and 0C8C advancement to phase 5.
The public Windows workflow inventories/runs the Rust consumer in both lanes;
the pinned Python comparison remains local.

```powershell
cargo test --locked --lib script_init::tests::shared_victory_view_route_contract -- --exact
python -S -B scripts/check_victory_view_route_contract.py --python-reference 'C:\path\to\e1128509-checkout'
```

The spatial-owner absence contract (`statescript-spatial-owner-absence-v1.json`)
checks six synthetic position/service cases with Python 46b22130's normal
SpatialBindingState and explicit scope registry. An absent owner permits the
state lifecycle without reading binding expressions; unknown/present services
remain unsupported. Rust rejects them at state admission, while Python rejects
them at auxiliary scope dispatch, so failure timing is not equated. Rust also
checks the separately inspected native E8D976BE early-return path; that class
has no Python consumer. Client binding, camera playback and present-owner
services remain outside this contract. Both Rust CI lanes inventory/run it.

```powershell
cargo test --locked --lib script_init::tests::shared_spatial_owner_absence_contract -- --exact
python -S -B scripts/check_spatial_owner_absence_contract.py --python-reference 'C:\path\to\46b22130-checkout'
```

The aim-ray visibility contract (`deadeye-aim-ray-visibility-v1.json`) contains
six synthetic occluder cases with stored expected results. The normal Python
capsule-intersection and static-ray kernels check the explicit Rust admission
policy; its nearest-surface result is recorded separately to expose the policy
difference. Rust checks actual target selection and shot-direction admission.
The aim point can be exposed while the lower capsule is covered, or covered
while a lower point remains exposed. Geometry before actual capsule contact
still rejects the shot. This is not normal Python Deadeye selection parity or
native selection equivalence. Both Rust CI lanes inventory/run the consumer.

```powershell
cargo test --locked --lib script_player::tests::shared_deadeye_aim_ray_visibility_contract -- --exact
python -S -B scripts/check_deadeye_aim_ray_visibility_contract.py --python-reference 'C:\path\to\e1128509-checkout'
```

## Flashbang Boolean entry guard

[The entry-guard fixture](../fixtures/behavior/flashbang-entry-guard-v1.json)
contains the224E/4 Boolean expression and eight synthetic cases. Six compare
unset/false/true entity flags, a local schema force default, and effective
stack links over opposite base values through production Rust and the pinned
normal Python VM. Rust uses real Component stack bindings; the helper reads
resolved values, not the snapshot's unlinked base map. The two extra Rust-only
cases reject unavailable body data and a wrapper override outside this adapter.

```powershell
cargo test --locked --lib script_enemy::tests::shared_flashbang_entry_guard_contract -- --exact
python -S -B scripts/check_flashbang_entry_guard_contract.py --python-reference 'C:\path\to\pinned-python-checkout'
```

The exact Rust consumer is inventoried in both Windows CI lanes. The prepared
wrapper regression separately compares the loaded authored expression, its
false local default and the unchanged priority guard, including a declared
rejecting body flag. The complete classification filter, reaction animation,
actual Wraith transitions and original-client acceptance remain outside this
contract. No generic native category inference is made.


## Flashbang timed-control exclusion guard

The room bridge consumes the same eight `flashbang-exclusions-*` cases in
[the native tag-clause fixture](../fixtures/behavior/statescript-filter-tag-clauses-v1.json).
Its guard reuses the production exclusion evaluator, reads the loaded 224E/9
exclusion list and queries effective body tags. The contract also exercises
live grants, base-tag suppression/release and unknown facts. An excluded tag
blocks timed control; normal damage and wrapper delivery retain their path.

```powershell
cargo test --locked --lib script_enemy::tests::shared_flashbang_exclusion_guard_contract -- --exact
```

Both Windows CI lanes inventory this exact consumer. The prepared wrapper
probe checks the loaded list with actual body-tag services. The room replay
also covers five ordinary Bosses and three explicitly tagged Reaper inputs:
all eight receive 25 damage and the wrapper, while only the ordinary five
receive timed control. These three inputs declare immunity and do not establish
an ordinary Boss's native inventory. The fixture's native provenance applies
to tag semantics only. This does not establish
actor category initialization, the full filter or native reaction animation.


## Integrated Flashbang filter admission

[The 64-case fixture](../fixtures/behavior/flashbang-filter-admission-v1.json)
compares the normal production expression dispatcher with original CF4340
through D0DA20, BCAAE0, D0EC70 and resolved tag predicates, plus native null/
absent-world defaults and Boolean result writing. Eight declared masks cross
all eight excluded-tag inventories. Actor/component pointer resolution,
literal tag-config conversion, empty live tag effects, absent custom world
and variant ownership remain explicit inputs/adapters. CI inventories the
exact consumer in both Windows lanes.

```powershell
cargo test --locked --lib script_expr::tests::shared_flashbang_filter_admission_contract -- --exact
```

This establishes integrated query results for those inputs, including rejection
of 1/team|1. It does not establish a native Boss's category inventory or change
NPC masks. Spawn category provisioning and complete reaction animation remain
open; no original-client acceptance follows from this contract.


## Native nested destination storage

[The nested-destination fixture](../fixtures/behavior/nested-destination-v1.json)
contains 12 original destination-path results, 21 native container boundaries
and two native identifier-map collision/growth/update sequences. The exact
Rust consumer exercises destination parsing, native-shaped storage and wire
encoding, then runs actual multi-assignment with a live local index in both
variable scopes. A separate VM test checks existing read notifications across
0/123/repeated 123/0 writes. These notifications and Rust work/depth limits are
host checks; the native oracles do not provision actor variables/COW/watchers.

```powershell
cargo test --locked --lib script_init::tests::shared_nested_destination_contract -- --exact
cargo test --locked --lib script_init::tests::nested_destination_writes_notify_existing_readers -- --exact
cargo test --locked --lib script_init::tests::nested_destination_keeps_unavailable_storage_explicit -- --exact
```

Native array holes are integer 0; indexes are stored as uint16 and the element
helper refuses 1000 and above after coercing the parent. Identifier maps retain
empty buckets/capacity so growth replays physical bucket order. Mutating a
legacy logical map with missing native layout remains explicitly unsupported.
Linked roots are also refused; the StateStack check retains the resolved linked
value rather than inventing native writable COW behavior.
Supported destination expressions are a direct variable followed by constant/
live-variable index selectors and identifier-member selectors. Other expression
shapes fail explicitly; this is not a full native address evaluator.

Both Windows lanes inventory the shared consumer. The normal pinned Python
reference does not implement these complex destinations, so no normal-VM
parity is claimed. Frozen Python's scoped record consumer is separate.

## Enemy self-damage host clock

`enemy-self-damage-clock-v1.json` covers absent, stale and future movement wire
frame markers. `script_enemy::tests::shared_self_damage_clock_contract` drives
the real enemy damage service, requires the authoritative room frame/time and
self attribution, refuses a controller-owned call and checks the existing
common-corpse policy immediately before/at eight seconds. This is a synthetic
host contract, not additional native timing evidence or Python parity.
The prepared Zomnic gate test checks that a late-frame self-explosion publishes
death before retirement; it must not expire instantly from a missing wire field.

## Unbound loadout action

`unbound-loadout-action-v1.json` records 545AB3B8's captured default-registry
dispatch to the Action base. `script_init::tests::shared_unbound_loadout_action_contract`
checks true returns with/without an output, actual downstream writes and ignored
unevaluable target/loadout configs across repeated VM calls. The native replay
also uses synthetic exact-binding controls; Rust does not claim that override
API or the native cache layout. This is not loadout application, actor-category
or full Flashbang reaction acceptance. The Windows lanes inventory the consumer.

## Absent targets before filter predicates

`absent-target-filter-v1.json` supplies five original CF4340/D0DA20 results.
`script_expr::tests::shared_absent_target_filter_contract` checks the production
evaluator with an unset operand, null, zero/invalid identities and an explicitly
known-absent valid actor. Flags/tag/relation services must not run for those
targets. Extra host checks preserve malformed numeric masks, unknown-field and
metadata diagnostics, and unavailable/live relationship semantics stay explicit.
The original native replay includes a non-null excluded-tag config and asserts
both mask and tag predicates are unreachable. It does not establish a live
actor's category inventory or model all relationship options.

## Flashbang base State lifecycle

`flashbang-base-state-v1.json` pins the captured0DB9E66B parent registration,
base constructor and empty reader. `script_init::tests::shared_flashbang_base_state_contract`
drives the actual VM through begin, abort and reentry, nested-child cleanup,
begin/end outputs and observer full encoding. It checks ignored unevaluable
payloads and retains rejection for an unrelated graph. Native
factory/reader evidence and synthetic host lifecycle evidence remain separate;
no health/tag multiplier, dedicated-server state logic or client acceptance is
inferred. Both Windows lanes inventory the shared consumer.

## Targeted Flashbang notification

`flashbang-targeted-message-v1.json` is a synthetic sender/receiver fixture for
`script_enemy::tests::shared_flashbang_targeted_message_contract`. It follows
104319's 224E/22 target and parameter schema and 01D8/54 listener schema.
The host test checks targeted enqueue and recipient filtering separately from
existing broadcast routing. Unknown defenders, wrong sites and bounded-queue
exhaustion reject. A listener-free hero can accept transport without a gameplay
callback; this does not prove native-client or full Flashbang acceptance.

## Flashbang horizontal velocity edit

`flashbang-horizontal-cap-v1.json` pins native row/consumer evidence separately
from synthetic host graph and locomotion inputs. The shared consumers are
`script_enemy::tests::shared_flashbang_velocity_action_contract` (VM row
validation, next-frame queue and follow output) and
`game_bots::tests::shared_flashbang_horizontal_motion_contract` (collision-aware
requested-velocity application, active-frame expiry, Y preservation and recovery).
The edit changes displacement before collision; a controlled stop expires it.
The existing objective path motor is a host model, so these checks do not prove
native NPC acceleration/platform parity, actor classification or client visuals.

## Body-angle queries

`statescript-body-angle-v1.json` supplies128 numeric reference cases from original
104319 yaw/pitch evaluators and declared packed-angle/quaternion inputs.
`script_expr::tests::shared_body_angle_contract` checks shared evaluation,
float32 numeric conversion, absent/unavailable distinction and malformed input
rejection; the owner-adapter regression checks live pose changes and scope.
Yaw compares within3e-5 degrees; pitch matches its packed native conversion
exactly. Rust poses contain packed angles, so arbitrary native quaternion and
moving-platform transforms remain outside this fixture's acceptance.

## Captured Flashbang aim

`flashbang-captured-aim-lock-v1.json` separates36 original initializer/composition
numeric cases from a synthetic mode1 host graph. Its consumers are
`linked_aim::tests::shared_captured_aim_numeric_contract` and
`script_enemy::tests::shared_captured_aim_lifecycle_contract`. They check the
zero-limit unparented NPC projection, pitch clamp, captured values rather than
current pose, selector1 retention, shared linked-state order and cleanup/reentry.
Yaw pose projection is bounded by half a signed16 angular unit; native component
and arbitrary parent/platform facts are not inferred. Owner-full player cache
replication remains unavailable; observer encoding has separate prepared checks.

### Local Junkenstein validation startup policy

`fixtures/behavior/junkenstein-validation-v1.json` describes the user-requested
Normal Cassidy acceptance overrides. The Rust consumer checks explicit/default
settings, other heroes/cards, missing owners and rejected invalid configuration.
A prepared-runtime regression verifies the gate binding and unchanged harder-mode
HUD. These are host configuration contracts; they do not supply native category
provenance, a Python implementation or original-client acceptance.

The same fixture supplies synthetic post-AI damage for
`game_service::tests::junkenstein_validation::configured_cassidy_immunity_routes_real_room_damage_and_death_notices`.
Real selection messages create Cassidy owner pools; actual `Room.finish_simulation`
checks ordinary 225 -> 155 -> 0 health and one lethal death notice against
protected 225 -> 225 -> 225 health with no death notice. These cases prove host
dispatch and owner health routing, not enemy attack generation or client display.
