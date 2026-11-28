# Verification & Parity State (`rust`)

Tracks implemented capabilities, Python-vs-Rust parity, verified baselines, and remaining gaps on `rust` targeting **Overwatch 1.74.0.0.104319**. For crate boundaries and contribution rules, see [NATIVE_SERVER_ARCHITECTURE.md](NATIVE_SERVER_ARCHITECTURE.md); for open milestones, see [ROADMAP.md](ROADMAP.md).

The [pinned Python → Rust parity matrix](https://github.com/leafall903/ORPHEUS/blob/232486a430b11cb105585ec5c0bfa6c47ee2a6d7/docs/PARITY.md) records 24 behaviors, their evidence and the next acceptance check for each. Use that revision to reproduce a comparison. For later results record the implementation, commit, runtime and kind of evidence, as Python on `main` and Rust on `rust` can both advance. Earlier Python observations remain available at revision `9f8f3bcb`.

---

## Public CI & Workspace Coverage (No Game Data Required)

| Lane / Suite | Scope Verified in CI (`.github/workflows/public-checks.yml`) |
| --- | --- |
| **Linux workspace** | Default-feature workspace build check, profile persistence on the Linux filesystem, and encrypted UDP room recovery on the Linux worker. |
| **Windows workspace** | Full default workspace tests and Clippy, plus focused Inspector HTTP, owner trace, CLI and node-observation tests. Includes formatting and a separate Rust 1.85.0 check. Named shared contract results are verified from the default workspace test output. |
| **Cross-language contracts** | Runs the pinned Python consumers against the Rust checkout's fixtures. The Windows default workspace job covers the Rust consumers, including JAM framing, strict World queries, and profile settings. The Python job also runs synthetic checks against the pinned wire reader. |
| **C++ gallery preview policy** | `scripts/check_gallery_preview_policy.py` compiles `relay/gallery_preview_policy.h` and checks `18` resource-selection + `6` owned-tree pruning cases against `gallery-preview-policy-v1.json`. |

[#61](https://github.com/leafall903/ORPHEUS/issues/61) is complete. [#79](https://github.com/leafall903/ORPHEUS/issues/79) records the four Rust consumers and [#80](https://github.com/leafall903/ORPHEUS/issues/80) records the Python StateScript consumers.

---

## Practice Range playtest follow-up

The shared static grapple trace reads the firing body's model attachment and
uses the mover's collision category. In a prepared 104319 Practice Range
replay, Wrecking Ball's anchor stayed fixed as the owner turned 90 degrees.
On release the tip returned to the rotated model reference point within
1 mm per axis, with less than 2 cm of horizontal drift from rest. The
same trace passed Widowmaker's prepared hit, miss, ledge and second-cast
VM checks. On a zhCN pack with 613 graphs, the full kit also received
the hook input and completed its graph under the normal server policy.
The local original-client cable animation is still unverified.

The same 613-graph room replay set checked Adaptive Shield during startup
packet loss, enemy contact with 32 stock movers, crossing an allied body,
and delivery of 15 armed Minefield entities to the owner connection. Doomfist
damaged and displaced a training bot with both Rocket Punch and Seismic Slam.
These results cover the simulated room and its outputs. First-person HUD and
deployable visibility still require original-client observation.

The separate full-kit diagnostic-count test expects seven initialization
entries. The enriched 104319 pack produced the same six entries on the
unchanged upstream server and in the integration checkout. This assertion
still fails for that pack; keep it separate from the hook-input checks.

The prepared 104319 regression reproduced a public StateScript diagnostic
several frames after a training bot died. The room now keeps projecting the
corpse through those frames and admits the replacement on schedule. A
synthetic regression also checks that a bot with a blocking diagnostic
cannot interrupt a healthy peer bot's projection.

The original 104319 client completed the 2026-11-21 follow-up session
without returning to the lobby on a training bot kill. The session
lasted about 331 seconds and exited without a room fault or crash dialog.

The room's initial mode frame includes the authored nameplate inputs. The
prepared check decodes those variables from the wire. The follow-up client
session still lacked overhead bars. The native server now stages the
nameplate as a separate delta after acknowledgments for both the mode
frame and the bot's parent script. This follows the delivery sequence
recorded in the [client-tested nameplate research](https://github.com/leafall903/ORPHEUS/blob/5d86bcb2/docs/research/bot-nameplates-and-hit-feedback.md).
The prepared encrypted replay checks this ordering and drops the first
nonlethal hit notice before validating the retry and decreased bot health.
The subsequent original-client session on #176 still showed missing overhead
names and health bars.

That session reproduced the client access violation tracked in
[#121](https://github.com/leafall903/ORPHEUS/issues/121). A local capture
identified a client read from near-null memory after a lethal projectile
impact following a body change. The triggering update remains unconfirmed.
Separately, headless coverage exercises an early-feedback race at body
replacement. Hit and elimination notices now wait for the shooter's
acknowledged possession and expire if their shooter is replaced before
reliable admission.

A second original-client session hit the previously documented
`Overwatch.exe+0xD697D0` access violation. It occurred without an observed
body change, after a hitscan kill. The Rust game channel now preserves the
exact end bit of its final JAM message so the receiver resumes at the
correct world header. Synthetic regressions cover the packet boundary and
the byte alignment of intermediate messages. On 2026-11-22, the maintainer
confirmed the crash was fixed after testing the original client. Issue #121
was closed on that result. The exact session parameters and the cause of the
earlier `0xB41104` fault were not established by the retest report.
[Reference investigation](https://github.com/leafall903/ORPHEUS/blob/5d86bcb2/docs/MCCREE_COMBAT.txt#L1007-L1027)

For profiles without saved gameplay options, the lobby now sends the default
kill feed and tips flags from the [client-tested settings research](https://github.com/leafall903/ORPHEUS/blob/5d86bcb2/docs/research/kill-feed.md).
The Rust wire check covers saved options as well. The prepared 104319
Practice Range regression also projects the stock player death state with a
bot killer's identity and encodes both owner and observer snapshots.
Original-client kill feed rendering remains open.

A local combat calibration replay used six playtest rays against the
prepared scene and distinguished head hits, body hits and a miss. The
calibration is a measured standing proxy. Animated volumes, live visual
alignment and other bodies still require their own evidence.

The 104319 owner replay exposed a due rifle round that spent ammunition
at a synthetic gap boundary without producing a posed shot. The owner
clock now skips such rounds and preserves ordinary ammunition cost when a
posed command resumes. The follow-up [owner volley trace](OWNER_DELIVERY_TEST.md#observe-a-short-weapon-volley)
recorded no unposed boundary and no lost owner reports. It did not
establish what the client rendered for every shot.

Prepared 104319 encrypted loopback now exercises stock Helix against scripted
training bots. It drops the first projectile acknowledgment and verifies
impact, target health, and retirement. A ground shot verifies owner health
on the wire and upward movement in the room simulation. A prepared stock
quick-melee case verifies one damage and knockback settlement. First-person
movement, impact rendering, and Particle Cannon and Junkrat self-launch in
the original client remain open.

A prepared Practice Range replay selects Zarya and fires a single ground shot
before any primary input. Over encrypted loopback, the test verifies impact
retirement for the confirmed projectile, 23.5 points of self-damage across
the owner health pools, and a post-impact owner movement correction. The room
reports an upward launch. The mixed-input matrix still stops on an
unsupported primary `TrackRay` state. Original-client rendering and movement
for Zarya remain open.

A synthetic shot with gravity now expires at its authored flight distance.
The range and lifetime can end independently. Original-client impact timing
at that limit remains open. The local deployable graph probe resolves the
mine definition and its linked actor graph. Standalone startup still requires
room actor admission and health-pool services, along with additional
StateScript state support. A local probe of the authored mode-0 creator
selector returns the supplied creator body and reports unavailable when its
host has no relationship. The actor's startup takes a separate mode-1
relation, whose semantics remain open.

The owner volley now decodes the impact-spawn entity and optional attached
graph from the authored configuration. The existing Biotic Field path is
selected by its canister and deployed entity definitions, including under
a relocated graph on a different synthetic body. Its existing presentation
and timing still use the legacy field host. The room does not yet start an
arbitrary attached actor graph from a projectile.

The follow-up timing capture accumulated 8,819 missed ticks. Misses
began well before the first recorded shot and continued during intervals
with an unchanged ray sample. It also ran with synchronous owner traces
enabled. The trace now uses a bounded background writer, and worker
timing separates work before the tick from timed poll wake lateness.
A controlled original-client performance comparison remains open.

Prepared Sprint coverage now includes the button sequence observed in
that session, with sustained forward input, and controls at the authored
throttle threshold. The server activates Sprint and produces the
expected speed modifier and HUD value in that replay. The session trace
did not record the throttle or matching ability state. The next trace
can distinguish the player's input from server activation and delivery.

## Shared StateScript integration


At `55c6f6fb`, the shared branch validated actor-scoped child retirement, held
button rebinding, and observation capture for queued state starts and committed
input branches. The coverage-enabled server and StateScript libraries passed
666 tests, strict Clippy passed, and both prepared 104319 reports matched
`e2123b35` after excluding their source revision. The earlier `e2123b35`
candidate passed 663 tests across the libraries and message/health integration
tests, including the prepared owner trace and actor input matrix. That
candidate validated hitscan settlement and death notice ordering at body
replacement, plus the settled health target generation guard.


The shared branch validated Phase 2 settled bonus credit and direct target filters
at `29df13cb`. The coverage-enabled server and StateScript library tests passed
(656 total), as did strict Clippy. The 900-frame owner trace and 32-actor input
matrix matched `6027a713` after excluding revision metadata. The matrix remained
at 12 admitted actors and four completed 311-frame schedules. Three charge
probes passed in the same handoff cycle with the annotated prepared graph pack.

The local `work/statescript-integration` checkpoint `79e8a5b9` combines
Phase 2 `90702e42`, Phase 3A `055adca3`, and native Practice Range
admission. The prepared 104319 matrix completed independent startup and
ordinary room attachment for **12 of 32** actors. Moira was the only
addition in each column and no previously passing actor was lost. The
transactional room admission test passed. The locked workspace tests and
Clippy passed with script coverage, including 390 server tests and
242 StateScript tests.

The preceding combined checkpoint `699791e8` matched all 476 #72
observations across 900 frames. Phase 3A also matched that replay on its
standalone `055adca3` checkpoint. Original-client skill, aiming, and
audio acceptance remain open.

### Movement, health and scoped input integration

Shared checkpoint `950100f1` combines Phase 2 `5d3a4727` with the Phase 3A
scoped input checkpoint `b9883400`. The server and StateScript library
suites passed 395 and 251 tests with script coverage. Clippy passed.
The 311-frame input matrix matched `527d3c60` across all actor rows
after normalizing the source revision. It recorded 12 ordinary
attachments and four completed schedules. The #72 reference replay
matched all 476 observations across 900 frames.

### Phase 3A ray diagnostics

Phase 3A checkpoint `05ab525e` includes the synced `131dfdae`
baseline, the held-input changes, and the Inspector ray overlay. Native
tests exercised captured-frame and VM-frame provenance, actor and wall
hits, misses, unavailable queries, source retirement, and the 64-entry
history shared with weapon rays. The Inspector JavaScript syntax,
workspace formatting, and both Clippy configurations passed.

The locked all-target suites passed **768** default tests and **775**
Inspector tests, with 137 opt-in cases skipped per suite. Two separate
104319 actor matrices matched each other byte for byte and matched
`79e8a5b9` apart from the source revision. Both independent startup and
ordinary admission passed for **12 of 32** actors, with **46** blocking
sites. The transactional admission test also passed at 12 of 32. The #72
900-frame capture matched all 476 observation sites. Original-client
aiming and ability acceptance remain open.

### Weapon readiness comparison

Checkpoint `79e8a5b9` ran the same prepared owner-input schedule twice
with the same executable. The second pack supplied the existing neutral
rule `0x2819` through `mode_rulesets` for the known actor profiles.
Both runs admitted 12 of 32 actors. The number with weapon output rose
from one to four. Reaper and Cassidy each emitted one hitscan request
and Sombra emitted eight. Soldier retained four hitscan requests and
one launch request. These observations cover server output.

The authored rule also exposed unsupported states on the first primary
input at frame 1017. Winston reached target tracking and physics
explosion states, and Zarya reached a ray tracking state. Winston
completed the original schedule. Zarya's original run first halted on
secondary input at frame 1064. Completed schedules fell from 11 to 10
of the 12 admitted actors with the rule. The alternate rule pack remains
a local experiment. The state blockers belong to the shared combat
states and Phase 3A ray tracking work.

### Prepared actor input matrix

The [actor input matrix](STATESCRIPT_DEVELOPMENT.md#inspect-actor-input-and-owner-delivery)
measures 311 frames of movement, presses, releases, and owner delivery
in the prepared room. On the local authored-rule experiment above,
12 of 32 actors attached. Hanzo, Cassidy, Soldier: 76, and Mei completed
the whole schedule. Ten admitted actors completed both fire phases,
six completed ability 1, five completed ability 2, and four completed
quick melee, reload, and ultimate input. The report records which
phases completed before each actor's first failure.

The two primary-input faults at frame 1017 now retain the attempted VM
step and the original graph diagnostics. Reaper and Moira both reached
an unsupported `STU_54FEB21C` state on ability 1 at frame 1105. The matrix
observes command processing and owner acknowledgements. Ability effects,
ultimate charge, and original-client acceptance have separate checks.

## Implementation and prepared-runtime observations

Local integration check (2026-11-16): rifle/reload and controller/training-bot startup prepared tests passed with the target-tagged base pack, as did runtime preflight and headless startup. The longer `scene_assets::tests::stock_soldier_owner_session_idles_fires_reloads_and_sprints` test failed its fixed-instance assertion: instance `24` contained graph `0x0F82`, not `0x05AD`. The message table was present elsewhere; diagnostic inspection also found switch instance `19` versus expected `18`. At that revision the fixed instance assertion prevented the later frame checks from running.

Prepared follow-up (2026-11-18, [#72](https://github.com/leafall903/ORPHEUS/issues/72)). On Rust base `a8f5b06b` and the tagged base pack `b11115f3`, the test now resolves each required graph by its authored root or exact parent and attachment slot, then follows that instance for all 900 frames. The table, omitted owner switches, rifle ammo, reload, idle, sprint and both projections pass. Selecting the adjacent attachment slot for the message table fails at startup.

Python `45044d0d` and Rust share 24 startup graph paths on this pack. Two storage entries differ. Both VMs stop root `08B6` during startup. Python retains its empty stopped record and Rust removes it. In manager `004B`, node 21 checks an excluded target tag. The Rust owner lacks that tag and activates child `01CB` on slot 2. The Python host falls back to false for this filter shape and leaves that slot empty. These observations explain the different instance numbering. Whether the original client expects either difference in the replicated startup state remains open.

Follow-up (2026-11-18): on the same prepared runtime, the long test passes using unique graph and parent/attachment identities; a separate test pins the ordered live startup tree and the absent IDs 9 and 26. The additional `01CB` child at `004B` attachment 2 precedes `0BA4` and `05AD`. A test reader decodes descriptor sections of the actual owner/observer baseline and deltas and checks live parent attachments against VM snapshots; synthetic tests exercise removal, lost introductions, cumulative descriptor repetition and rejection of deltas that skip an unacknowledged chunk. The long test does not inspect queue retransmissions. The reader does not decode state links, variables, or the remaining payload, and it does not establish startup creation/retirement events or original-client compatibility. The earlier fixed-ID failure remains a historical observation, not a current test result.

The 2026-11-20 headless actor matrix passed independent startup for **10 of 32** actors at `f506d762`. Two runs on the same 104319 pack produced identical reports. Hanzo was the newly passing actor. The ordinary Practice Range factory attached a player script for one actor. Body creation is a separate operation. The remaining 22 independent probes reported 55 blocking sites, including 16 unsupported config expressions, 14 registration rejections, 11 unsupported states, and eight persistent message registration failures. Client acceptance and ability coverage have separate gates.

The [prepared Python comparison](STATESCRIPT_DEVELOPMENT.md#compare-a-prepared-startup-matrix-with-python) ran Python `c73e5a49` against the same eight input digests. The ordinary Python path decoded the initial tree and first owner frame for **32 of 32** actors with its default compatibility policy. All 32 recorded a config fallback notice. Three recorded an unmodeled action and one recorded an omitted active state. A separate method-entry trace reached the same eight sender sites reported by Rust. All eight used Python's ordinary one-shot sender branch. The Rust count uses its own factory and projection requirements.

The next independent matrix at `934f4fec` used the same pack and removed one movement-sampler diagnostic each for Doomfist and Baptiste with no new sites. The matrix has **53** blocking sites across the same 22 actors. The #72 900-frame result matched the preceding run across all 476 observed sites.

At `d3da7e1a`, two same-pack runs retained 10 independent passes and 53 blocking sites. Two stat timer sites now report unsupported state after the placement checks were removed. Baptiste's remaining site reports an unavailable entity-variable read, which is a controller lookup in the startup expression. The #72 capture kept all 476 observation sites.

The `9485d72e` matrix cleared one frame-wait registration diagnostic for Wrecking Ball. Both runs reported **52 blocking sites** and **10 of 32** independent passes. The #72 capture still matched across all 476 observed sites. A separate local listing audit left Baptiste's controller value unresolved across the statically reachable controller graphs.

The `c2e75f7c` matrix cleared one ordinary health query diagnostic for Moira. Both same-pack runs reported **51 blocking sites**, **10 of 32** independent passes, and **1 of 32** ordinary Practice Range script attachments. Two selected-pool query diagnostics still need investigation. The #72 capture matched across all 476 observed sites.

At `eff202e3`, the loader routed an inherited base state through the class metadata path at arbitrary placements. The same-pack reports retained 51 blockers and 10 of 32 independent passes. The #72 capture matched across all 476 observed sites.

The `d747f926` reports matched byte for byte and cleared both remaining selected-pool health query diagnostics for Zenyatta. The 32 independent probes reported **49 blocking sites**, with **10** passing. The ordinary Practice Range factory still attached one player script. The #72 capture matched at all 476 observed sites. Original-client acceptance has a separate gate.

The Windows paired room checks at `d747f926` used 339 ticks each for idle and single-shot reload. Python `45044d0d` and Rust produced two repeatable, individually valid traces in each case. Strict comparison returned `incompatible` for the host and player VM RNG model and seed. The RNG diagnostic found no first difference in the recorded tick fields. The manifest and schedule digests matched the earlier ARM64 runs. The effective input tree digest differed.

At `b7f953ae` and `60b2b955`, the ordinary health pool and bound body angle changes retained **49 blocking sites**, **10 of 32** independent passes, and **1 of 32** ordinary Practice Range attachments. Two reports per revision matched byte for byte. Each revision matched the preceding startup and #72 captures apart from the recorded source revision. The isolated wave health regression passed for all six placements and four difficulty values.

The Windows idle and single-shot reload checks at `60b2b955` matched the prior Windows input and observation hashes for both implementations. Each schedule covered 339 ticks. The strict comparison retained the same four RNG setup differences.

At `8fbc2fa3`, the authored projected-message contract passed loader,
lifecycle, dispatch exclusion, and owner and observer wire tests. Two
runs on the existing 104319 pack matched each other byte for byte.
All 32 actor results and 49 blocking sites matched the preceding run,
with 10 independent passes and one ordinary Practice Range attachment.
The eight persistent registration diagnostics remain open. The #72
capture matched across all 476 observed sites.
Both 339-tick Windows schedules matched the preceding input and
observation hashes, with the same four RNG differences in strict
comparison.

At `c0fe55ad`, the synthetic fault regression confirmed that a halted
target tracker releases its child's registered host effect exactly once
and discards the due timer. The two same-pack actor reports retained
49 blocking sites, 10 independent passes, and one ordinary Practice
Range attachment. The #72 capture matched all 476 observed sites.

At `ad2d1b42`, synthetic startup tests verified that a one-shot player
query can read the frozen room frame before body attachment, and that
replacing a body twice in one frame keeps the old body out of both startup
views. The existing 104319 Practice Range pack reported no startup spatial
frame. Two same-pack reports matched byte for byte. Apart from the revision
and the added spatial-context field, the reports retained the previous
32 actor results and 49 blocking sites, with 10 independent passes and
one ordinary Practice Range attachment. The #72 capture matched all 476
observed sites.

The Windows #131 idle and single-shot reload schedules each produced 339
ticks. Both implementations repeated their observations and passed
individual trace validation. Every tick matched its preceding Windows
capture. Strict comparison still reports the same four RNG model and
seed differences between the implementations.

At `39932467`, the shared owned-entity count stopped rejecting queries
with an explicitly unbound owner. The old binary reproduced both count
diagnostics on the same prepared pack. Two updated reports matched byte
for byte, with **47 blocking sites** and **11 of 32** independent passes.
Zarya passed independent startup, and Symmetra cleared her count
diagnostic. The #72 capture matched across all 476 sites.

At `39932467`, ordinary Practice Range attachment remained **1 of 32**.
Scene preparation still installed a Soldier-only factory there, while
the independent probe called the per-hero factory builder directly.
Ability coverage and original-client acceptance require separate
verification.

At `9f6114ae` and `763b8e56`, two reports per revision from the same
prepared 104319 runtime matched byte for byte. All 32 actor rows matched
the preceding reports, with 11 independent passes, one ordinary room
attachment, and 47 blocking sites. The Phase 2 changes cover the shared
direction evaluator, scene placement collection, and parent-owned states
without completion operands. The #72 capture matched the earlier
900-frame run at all 476 observation sites on both revisions.

The prepared bot firing-placement regression passed on the earlier
binary and failed at selector evaluation after the first direction
change. The follow-up preserved the host's explicit no-target reply.
The same bot regression then passed. The legacy roll scenario stopped
during map preparation on both tested bundles because its map record
lacked spawn points.

The Windows #131 check at `763b8e56` used the same effective runtime
profile and schedules as the previous baseline. Idle and single-shot
reload each produced 339 ticks. Rust and Python traces both validated and
repeated, and every tick matched the preceding capture for its
implementation. The strict comparison retained the same four RNG model
and seed differences.

At `15a10d4e`, authored owner graphs gained a `BodyActive` query.
Synthetic tests covered initialization with and without room activity,
paused and resumed gains, health awards at dispatch, death, and stale
updates after same-wire replacement. Two same-pack startup reports
matched byte for byte. The 32 rows matched the preceding revision,
with 11 independent passes, one ordinary attachment, and 47 blocking
sites. The #72 trace matched all 476 observations across 900 frames.
On the same Windows #131 inputs, idle and single-shot reload each
produced 339 ticks. Both implementations repeated their observations,
passed individual trace validation, and matched their previous traces
tick for tick. Strict comparison retained the four RNG setup differences.

At `8d1d33a6`, native eye-position operands gained a player anchor from
the simulation mover. Potential foreign eye and direction selectors now
request a frozen room pose. The startup scene captured 15 living
candidates. Both same-pack reports matched byte for byte. All 32 actor
rows remained unchanged, with 11 independent passes, one ordinary
attachment, and 47 blocking sites. The prepared firing-placement
regression passed, and the #72 trace retained all 476 observations.
Simulation stance heights have no new original-client verification.

The Windows #131 idle and single-shot reload schedules each produced
339 ticks. Each implementation validated and repeated both traces.
Every tick matched its own preceding Windows capture. Strict comparison
still reports the four RNG model and seed differences.

At `e540fdc0`, the native local combat accumulator stopped using two
fixed placements to choose its no-tick state. The current owner body
reports the local accumulator absent to the VM. Hosts with available or
unknown context still report an unsupported state. Two same-pack startup
reports matched byte for byte. The 32 actor results remained unchanged,
with 11 independent passes, one ordinary attachment, and 47 blocking
sites. The #72 900-frame trace matched all 476 prior observations. The
Windows #131 idle and single-shot reload traces each matched their
preceding 339-tick captures for both implementations. Strict comparison
retained the same four RNG setup differences.

At `e6cfc337`, player, NPC, gate and mode scripts began reading one
mode-scoped engine settings table. The portable and native synthetic
suites distinguish missing keys, explicit null, empty tables and mode
isolation. The prepared Practice Range pack exercised the compatibility
lookup with its existing settings. The two same-pack startup results
matched byte for byte. The 32 actor rows remained at 11 independent
passes, one ordinary attachment and 47 blocking sites. The #72 trace
retained all 476 observations. This revision also includes the authored
eye observation recovery contract at `9b957ce7`.

At `9caac758`, the auxiliary view binding states began using host
context at any placement. The owned headless body also reports outline
absence through the existing outline branch. The same batch added
elapsed waits for direct numeric variable bindings and moved the
literal-delay cinematic boundary states to field and slot validation.
Synthetic tests covered dormant operands, cleanup, timer changes and
reentry, and the owner and observer boundary payloads.

Two reports from the same prepared Practice Range pack matched byte
for byte. All 32 actor rows and the #72 trace matched the preceding
revision, with 11 independent passes, one ordinary attachment, 47
blocking sites and all 476 observations. Both Windows #131 schedules
repeated and matched their previous 339-tick captures for each
implementation. Strict comparison retained the four RNG setup
differences. The cinematic payload tests used a synthetic graph.

At `0d3716e4`, a player body gained an explicit private charge-root
selection. Synthetic tests cover mount validation after the room adds
its roots, settled health delivery, and owner and observer projection.
The existing bridge takes damage eligibility from the room phase and
retains its startup projection. The local 104319 catalog gives each of
32 player bodies an authored cost binding read by the shared startup
graph. The selected charge route was exercised by the synthetic
fixture.

At `5b269875`, authored cues began rechecking listener liveness at
reliable admission. The synthetic case kills the listener with its
generation unchanged and verifies that the queued cue is discarded.

Two same-pack startup reports at `0d3716e4` matched byte for byte.
All 32 rows remained at 11 independent passes, one ordinary attachment
and 47 blocking sites. The #72 trace retained 476 observations. Both
Windows #131 schedules repeated and matched their preceding 339-tick
captures for each implementation. Strict comparison retained the four
RNG setup differences.

At `3dbfd5e4`, the native voice state keeps its lifecycle when the local
host explicitly has no voice context. Its verified empty state codec
preserves owner and observer projection. Two same-pack 104319 reports
matched byte for byte. Moira raised independent actor startup to
**12 of 32** and the blocking-site count fell to **46**. Ordinary Practice
Range admission still attached one actor. The #72 Soldier owner trace
retained all 476 observations. The two #131 schedules each reproduced
339 ticks per implementation and kept the same four RNG setup differences
in strict comparison.

At `f031b4c1`, the linked state, periodic self shield recharge and
mode script entity query use the shared VM and host paths. The locked
workspace tests and Clippy passed with script coverage. Two same-pack
104319 startup reports matched byte for byte, with all 32 rows
unchanged from `3dbfd5e4`. They retained **12 independent starts**,
one ordinary room attachment, and **46 blocking sites**. The #72 Soldier
owner trace kept all 476 observations. The local pack lacks the
Junkenstein NPC and map inputs, so the encounter shield lifecycle
has synthetic coverage in this batch.

At `6c54b054`, settled player and bot impacts gained optional hit tags
on the shared health event. Native tests check the actual applied
delta, tag availability, and source replacement. The private authored
charge test binds these tags on the next VM frame. The stock synchronous
charge route still has its own target snapshot and damage message.
The locked workspace suite and Clippy passed with script coverage.

Two same-pack 104319 startup reports matched with all 32 rows
unchanged from `f031b4c1`. They retained **12 independent starts**,
one ordinary room attachment, and **46 blocking sites**. The #72 trace
retained 476 observations. Both #131 schedules reproduced their earlier
339 ticks per implementation. Strict comparison retained the same four
RNG setup differences.

At `b9df99a1`, the horizontal cap uses a validated row shape and
applies on the next movement frame. The local 373-graph pack has
two matching selector-1 actions with integer bounds. Synthetic player
and NPC tests exercise queueing and rejection. Settled weapon damage
also carries the target's original flags, tags, and script state
through the host event. Tests verify event scope and generation checks.
The locked workspace suite and Clippy passed with script coverage.

Two same-pack 104319 startup reports matched and retained **12
independent starts**, one ordinary room attachment, and **46 blocking
sites**. All actor rows and the 476 observations in the #72 capture
matched the previous run. Both #131 schedules reproduced 339 ticks
per implementation, with the same four RNG setup differences.

At `001cd3ea`, the room captures the target of a positive heal when the
event has a distinct sampled source. Target facts use the same capture
path as impacts, with the actual healed delta and unavailable hit
attribution. Two resource-free state classes now register by checked
fields and raw state slots. The prepared boss graph probe passed its
successor, owner and observer codec, and retirement checks. The locked
workspace suite and Clippy passed with script coverage.

The unaltered `0B8B` passive subtree and `00EB` bonus receiver passed
the prepared graph-runner probe with a detached host. It reproduced the
one-second passive deadline, admission cancellation and re-arm, and
bonus capping. Admission and attribution for a running room remain
separate from this graph execution.

Two same-pack 104319 startup reports matched with all 32 actor rows
unchanged, including **12 independent starts**, one ordinary room
attachment, and **46 blocking sites**. The #72 trace retained its
476 observations. Both #131 schedules reproduced the previous 339
ticks per implementation and the same four RNG setup differences.

The shared stat action now uses the authored target and stat selectors.
The body host can confirm that it has no stats writer. These actions
follow their continuation without evaluating unused operands. Live stat
updates remain open.

The server-only form of the directed numeric route accepts a terminal
send of one instance value. Its target is either an entity-scope variable
or the owner's possessed body. One prepared `14F2` probe supplies
explicit inputs to check the receiver's absolute charge fraction, cap,
and both authored gates. A second probe connects the unmodified terminal
send to the receiver through the deferred queue. Its trigger and input
values are synthetic. The probes await integration runs. Live trigger
timing remains open.

The opt-in directed numeric route preserves ordinary local delivery
and uses the room queue for a validated foreign target. The prepared
probe passed for the original `00EB` sender actions and receiver with
provided caller inputs. Room tests checked source and target generation
at settlement and delivery on the next receiver VM frame. Natural
target binding and native foreign timing remain unverified.

The shared bonus path can derive its payload from the actual settled
heal and bind the target in an authored graph. The room regression covers
a grant on the recipient's VM frame after routing, the meter cap, and
discarding a request for a replaced body. Positive NPC-local health
events collect committed target facts after the VM's local mutations.
Direct target filters use the event's tags and, in a configured room,
the original target's affiliation and post-settlement liveness. The
room preserves these facts if a lethal target retires before delivery.
The health event carries the original source and target identities
through VM delivery. A direct request to either wire expires if the
room now identifies a different body. The room regressions cover stale
awards to a replaced target and stale replies to a replaced source,
followed by fresh awards. An immediate same-body message can relay each
participant's context through its own typed entity parameter. The
portable regression covers direct and relayed requests after replacement.
A prepared probe uses the original admission claim and local sender
with the stock cap. These additions are pending integration verification.

Player bodies can opt into mode-scoped compatibility charge using an
existing ordinary damage receiver, mounted directly or as a native
child of a declared public root. The configuration can inhibit credit
during an ability state. Bodies without either charge declaration retain
the existing legacy path for its supported profile. Synthetic regressions
await integration runs. Original stock admission and rollout still
require validation.

The full pseudocode and prepared bundle both show seven `0D9A` receivers
and no literal sender. The `0B9E` entry binds different slots from the
three instance operands read by its outgoing grant. The original
dispatcher, the origin of those three values, and the order relative
to health settlement still need original-build evidence.
The Mercy receiver requires two positive numeric inputs before its local
bonus. Baptiste has separate branches for positive and negative values
from the same message slot. These checks establish a signed input format.
What the sender measures and how it relates to the applied health delta
remain open. All 17 literal `0044` references in the pseudocode are
receiver entries. Original attribution to healing still needs evidence.

Player hit notices, immediate damage credit and deferred notifications
now use the same actual pool loss. The prepared `0012` probe supplies
explicit pool records to the original charge receiver and checks how
matching identifiers affect the award. The room records each named
temporary shield's actual loss in damage settlement and supplies the
typed records to the compatibility route. Its synchronous message now
carries its own target snapshot and native filter scope. Other queued
messages on the same frame reuse ordinary queries. The authored `health_applied`
event can also bind those records. Weapon and direct damage use the
same settlement, including NPC-local self damage. Healing and damage
with unavailable pool accounting omit the binding. Regression runs
remain pending.
Owner grants and their named current query use the shared pool lifecycle.
A checked mode-2 effect drains only its own named pool at the authored
rate and ends at zero. Native and VM regressions cover decay separately
from rewarded impact loss. The optional state outputs and original
event timing still need verification. The detached `0044` probe checks
conditional credit using explicit typed inputs.

Static model protection now uses the authored state slots and an
explicit host capability. The prepared boss graph regression passed its
link, encoding, and final restoration checks. The current encounter
host owns the model mask bookkeeping. Runtime rendering and collision
effects have separate verification.

The workspace suite, Clippy, Python bundle and coverage tests, and
the three prepared graph probes above passed before handoff on this
Phase 2 worktree. Shared-branch verification belongs to integration.

Immediate self damage uses validated local state fields for two encounter
forms. The self-instigated form reads a positive native integer amount.
The no-instigator cleanup form retains the reference's 10,000 validation
and skips an already dead VM owner. The existing NPC host still owns health
settlement. Other no-instigator amounts, external targets, and compound
modifiers require their own evidence. Integration verification is pending.

Two of the three remaining native target-tracking startup sites have
empty embedded plugs. One configures a second filter and an entity list
output. The other configures a count output. Both read a dynamic radius.
The remaining site links embedded states. Each configuration still needs
its native query and output semantics verified.

The following Phase 3A results were measured on its standalone revisions.

At `339b05d8`, the Phase 3A authored static ray query passed synthetic
expression, observer lifecycle, and native room collision tests. Two
startup runs with the default build and the same 104319 pack produced
byte-identical reports. The script-coverage report matched the preceding capture
apart from its source revision, with **11 of 32** independent passes,
**one** ordinary Practice Range attachment, and **47** blocking sites.
All 476 #72 observation sites matched. Dynamic actor ray hits and
ability-state integration remain open.

At `7510ba31`, the authored actor ray query passed the shared filter
contract and the native frozen-frame collision test. The native test
checks wall occlusion, equal-distance hits, crouch dimensions, and
exclusion of retired bodies from replacement startup views. The default
and script-coverage 104319 startup reports matched their respective
baselines apart from the source revision. The matrix recorded **11 of 32**
independent passes, **one** ordinary Practice Range attachment, and
**47** blocking sites. All 476 #72 observation sites matched.
The Phase 3A ability states still require integration and acceptance.

At `5a56304c`, the authored facing claim passed its StateScript
lifecycle contract and native player movement regressions. The tests
cover priority ties, refresh and release on the following pose,
vertical directions, runtime removal and failure, and replacement
bodies. The legacy NPC-facing regression passed with two existing
prepared packs containing its boss graph. The prepared 104319 startup
matrix matched the preceding capture at **11 of 32** independent passes,
**one** ordinary Practice Range attachment, and **47** blocking sites.
All 476 #72 observation sites matched. Client zoom presentation and
specific ability integration remain open.

At `e4739efd`, Phase 3A scripted targeting gained an optional callback for
each successful observation sample. The native synthetic room fixture
verified repeated visible hits, wall occlusion, unavailable actor-ray
data, target replacement before effect settlement, and runtime removal.
StateScript tests also verified callback order and cancellation on
retirement or reactivation. Formatting, Clippy, and all public workspace
tests passed in both the default and Inspector configurations. The two
prepared 104319 startup reports were byte-identical. After normalizing
only the source revision, both the startup report and the #72 replay
matched the preceding captures. The startup matrix recorded **11 of 32**
independent passes, **one** ordinary Practice Range attachment, and
**47** blocking sites. The #72 replay matched all 476 observation sites
across 900 frames. Original ability mappings and client zoom acceptance
remain open.

At `3331f0f0`, fresh observation callbacks restored their own sample
when another state or plug reused the output. A higher-priority binding
suppressed the callback while it hid the sample. Portable tests verified
recovery on release, and the native aiming fixture verified that an
intervening write could not change the target of the sampled effect.

At `3482c7b9`, explicit owner-only instance variables projected through
the existing wire codec. Synthetic wire tests covered owner updates,
correction before acknowledgement, release, and child instances.
Observer full bytes stayed identical and no observer delta was queued
when only private instance values changed. Same-ID shared entity values
and explicit public copies retained their visibility.

Formatting, Clippy, and all public workspace tests passed for both
configurations. The default build passed 368 native server tests and
217 StateScript tests. Inspector passed 374 native server tests and
218 StateScript tests. The repeated 104319 startup reports were
byte-identical and matched the preceding report apart from source
revision at **11 of 32** independent passes, **one** ordinary Practice
Range attachment, and **47** blocking sites. The #72 replay matched all
476 observation sites across 900 frames. Original ability mappings
and client zoom acceptance remain open.

At `055adca3`, the original held-aim fixture composed command input,
actor rays, an owner-only target indicator, and a scoped movement
multiplier. The native test verified activation, repeated hold,
occlusion, temporary sampling failure, recovery, release, and
replacement of the source body. Failure cleared the derived indicator
while keeping the last successful raw ray sample. Observer full bytes
were stable through these changes. The movement modifier test
observed activation and release at the next pose boundary.

The portable VM fixture verified that a listener requiring a living
owner refuses late entry and lock release after death. It also verified
cleanup before a timer-only update and preserved the explicit dead-input
opt-in. Observation tests covered unavailable first samples, unchanged
recovery, invalid expressions, and cancellation on retirement.

Formatting, Clippy, and all public workspace tests passed for default
and Inspector builds. Default passed 369 native server tests and 221
StateScript tests. Inspector passed 375 native server tests and 222
StateScript tests. The two prepared startup reports were byte-identical
and matched the preceding 104319 baseline apart from source revision.
The matrix recorded **11 of 32** independent passes, **one** ordinary
Practice Range attachment, and **47** blocking sites. The #72 replay
matched all 476 observation sites across 900 frames. Original ability
mappings and client zoom acceptance remain open.

At standalone Phase 3A revision `3c87fd49`, the held-input contract passed
automatic registration, held and idle ownership, cancellation of sibling
starts queued during release, and rejection of buffered presses from a
previous button binding.
Both listener forms passed the same native held-aim scenario, including
owner-only status, observer bytes, movement cleanup, and body replacement.

Formatting and both Clippy lanes passed. The locked all-target workspace
suites passed **729** default tests and **736** Inspector tests, with
135 opt-in tests skipped per lane. Two separately selected prepared
startup runs produced identical reports. Against the same 104319 inputs,
the matrix matched the preceding baseline apart from the source revision
at **11 of 32** independent starts, **one** ordinary attachment, and **47**
blocking sites. The #72 replay matched all 476 sites across 900 frames.
The original ability mappings and client zoom still require acceptance.

At that standalone checkpoint, seven actor probes had one blocking site
each. Mercy's tag query needed the tagged placement inventory and live
actor tags. Orisa and Sigma still hit persistent message registration.
Baptiste could not read a controller variable. Torbjörn's hardpoint query
and Moira's voice stimulus each lacked their supporting host service or
context. Symmetra still reported an unsupported state.

The shared checkpoint above and the Phase 3A Inspector checkpoint use
separate revisions. Combining the latest Phase 2 follow-up, the actor
input matrix, and the Inspector ray work still requires startup, room
admission, and input measurements.

Two of the three remaining native target-tracking startup sites in the
Phase 2 run have empty embedded plugs. One configures a second filter
and an entity list output. The other configures a count output. Both read
a dynamic radius. The remaining site links embedded states. Each
configuration still needs its native query and output semantics verified.

The Phase 2 run left six actor probes with one blocking site each.
Mercy's paired tag query selects its shared VM path by authored fields.
Its host still needs the tagged placement inventory and live actor
tags. Orisa and Sigma still hit persistent message registration.
Baptiste cannot read a controller variable. Torbjörn's hardpoint query
now reaches the shared host interface and reports unavailable
active-model membership.
Symmetra still reports an unsupported state.

The prepared 104319 bundle supplies voice stimulus references and gallery
unlock metadata. It lacks an authored stimulus-to-line mapping and playback
durations for the player voice service. The room already routes resolved
cues to listeners by body generation. A player voice provider still needs
the catalog and timing inputs before active-context playback can be verified.

At `36818197`, a synthetic input test reproduced the former replacement
after a factory error at `3e778d08`. The fix preserved the current body,
controller counters, and room events when the factory failed, when the
runtime had the wrong entity, and when its owner snapshot could not
be encoded. Input continued and the retry committed one generation.
The prepared input-gap and replacement test passed. Two 104319 reports
matched each other byte for byte and matched the preceding 32 actor
results after removing the revision field. The #72 capture matched
all 476 observations.

At `0d1d6ef4`, the same input regression added a candidate whose owner
baseline fits the wire budget and whose observer projection has an
invalid remote state link. The preceding revision replaced the body.
The fixed revision rejected the candidate, preserved the current body
and its delivery state, and accepted the retry. Both startup views use
the existing full-frame encoder and datagram check. The prepared
input-gap replacement passed. Two 104319 startup reports matched each
other byte for byte and retained the preceding 11 independent passes,
one ordinary Practice Range attachment, and 47 blocking sites. The #72
capture matched all 476 observations.

At `9d5b9644`, ordinary Practice Range admission uses the prepared actor
catalog. Static graph layouts are shared across rooms. The selected
body starts with the current spawn, admitted roster, and spatial
snapshot. The scene validates both full baselines before room insertion.
On the same 104319 inputs, the ordinary startup matrix rose from
**1 of 32** to **11 of 32**. An independent test through queued selection
and room insertion also admitted **11 of 32**. The ten new passes in the
ordinary matrix were Reaper, Hanzo, Pharah, Winston, Roadhog, Cassidy,
Junkrat, Zarya, Mei, and Sombra. The queued test confirmed that rejected
candidates preserve the existing body and delivery state. The input-gap
test mounted Zarya's script while preserving movement and command
acknowledgements. Independent startup still has 47 blocking sites
across the other 21 actors. The two startup reports were byte-identical,
and all 476 #72 observations matched. Ability coverage and client
playability still require separate validation.

The prepared 104319 input probe starts a fresh room for each of the 32
catalog actors. All 11 admitted bodies acknowledged possession, an initial
owner script frame, and a subsequent update. Ten retained their runtime
through 100 frames of idle, forward movement, primary, and secondary input.
Zarya stopped after 59 completed frames at `0242:21` (`STU_CEBDAF17`),
an action whose dispatch is limited to specific graphs and nodes. A second
run with 80 idle frames before movement stopped at the same frame and
site, before either attack input.

Only Soldier: 76 produced combat requests in this schedule, with four
hitscan requests and one projectile launch. A temporary trace at the VM shot
drain saw exactly those five events. The other bodies emitted no weapon
volley events during this sample. Before the host filter, the 176-frame
sample reproduced the same five events. Only Soldier: 76 and Hanzo
reached an active logical-button callback for the observed attack
inputs, and only Soldier: 76 reached volley scheduling. Ability-edge
callbacks remain unmeasured. The probe counts requests
and owner acknowledgements. Hit results, ability coverage, and playability
still need their own acceptance. Run the ignored
`prepared_practice_catalog_observes_input_survival_and_owner_egress` test
with the prepared runtime to reproduce the matrix.

The 11 admitted actors in the startup matrix all declared a weapon
manager and one populated weapon slot. The direct graph references
resolved and the startup snapshot recorded activity in both roots.
The manager had 29 visited sites for each actor. The weapon had 55
for Soldier: 76 and 20–54 for the other actors. These measurements
cover startup. The conditions that enable weapon actions after startup
need separate verification.

A separate 100-frame probe at `140e6a6e` enabled room combat and ran
per-frame settlement. It reproduced the 11 admissions, 10 completed schedules,
and five Soldier combat requests. Only Soldier: 76 and Hanzo showed a
pressed logical state. All 11 admitted weapon roots had a declared
primary-button subscription with a fixed input ID. Among the 11
unvisited primary subscriptions in the other weapons, ten had a
visited BooleanSwitch immediately upstream and one was two links
from a visited BooleanSwitch. At `5a311df7`, all ten directly connected
routes had active switches with a false condition throughout the command
sample. Their listeners are on the true branches and remained inactive.
The condition definitions use local VM variable reads. A second sample
after adding clock dependencies found the same branch results. The ten
routes had no linked condition inputs and at least one declared input
absent from the variable store. Those inputs did not change in the sample.
Soldier's open branch also had an absent input. Eight inactive routes
share the same declared entity and instance inputs as Soldier. Each of
their weapon graphs visited a matching assignment during startup.
The remaining two routes use separate entity inputs. Runtime assignment
results and their effective values are the next comparison.

The tagged 104319 encrypted loopback also covered an attempted Tracer
selection followed by a Reaper selection. The rejected selection retained
the current body and generation. The next selection published the controller
update, replacement body, and old body retirement, and the ensuing reskin
published a third generation.

| Workflow | Python reference | Rust implementation | Open client or parity gap |
| --- | --- | --- | --- |
| **Login, Profiles & Lobby** | Battle.net/JAM auth, profiles, settings (`22200`–`22204`), queues, Windows launcher + TLS relay. | Native BNet/JAM lobby, `profile-settings-v1` (CI verified), encrypted UDP seat handoff. | Standalone Rust launcher & public client connection flow open. |
| **Hero Gallery** | Loadouts, skins, equipped voice & emotes. | Loadout equip/persist, live skin body replacement, 23 NPC previews (C++ policy verified in CI). | Client Hero Gallery NPC framing & animation retest pending. |
| **Practice Range & Soldier: 76** | Full body/weapon VM, rifle, Sprint, Helix, Biotic Field, bots, format-5 collision. | Stock Soldier startup tree + rifle/Sprint/Helix/Biotic Field VM regressions; bots/projectiles in Bevy ECS with default StateScript patrol decisions and an optional Lua debug override; all 43 movers match Python trajectories. | Client 60s stock-weapon mount, HUD/ammo sync, upstairs patrol, bot HUD/SFX (#3, #7–#9, #13), and original-client acceptance of the `08B6` / `004B -> 01CB` startup tree differences vs. Python pending. |
| **Movement & Collision** | GJK/contact solver, static ray queries, `0688` + 95 map caches. | `orpheus-collision` matches 10,148 Python movement records & 8,256 velocity cases; shared map-agnostic static navigation queries (`walkable_ground`, `capsule_clearance`, `walk_edge`, `find_path`, `valid_spawns`, `snap_spawn_points`) verified on synthetic fixtures and prepared `104319` Practice Range (`0688`), King's Row (`00D4`), Temple of Anubis (`005B`), Hanamura (`0165`), and Kanezaka (`0CD3`) collision caches ([#63](https://github.com/leafall903/ORPHEUS/issues/63), manifest SHA-256 `dad26628515e60e45e6128effd0c6408092dee8c8fc674ce00850b3463f8a90b`); player bodies live in `RoomSimulation` ECS ([#65](https://github.com/leafall903/ORPHEUS/issues/65)) and safe-boundary Lua AI hot reload with rollback landed in [#120](https://github.com/leafall903/ORPHEUS/pull/120). | Dynamic player-vs-player / player-vs-bot character collision blocking and live client checks (#7, #13) remain open. |
| **Doomfist & Wrecking Ball** | `leafall` merged into `main`: Slam, Rocket Punch dash/slow, Uppercut, Shield, Grapple, Quad Cannons, Piledriver, Minefield. | Rust has movement, short-action retry, Slam placement and shockwave fixture consumers. Synthetic player MoveTo coverage drives the capsule and runs authored finish/abort plugs; the isolated Widowmaker hook press runs the authored trace→movement chain on real data under the normal policy (the state-group boxes are membership-only in this build — pinned by the runtime state-group tests). | The full-kit body's eight support-graph config/state classes must initialize clean before original-client acceptance. |
| **Fault Isolation & Inspector** | Single-process dashboard & replay journals. | Room-owned errors & `catch_unwind` recovery (`room-isolation-v1`), `--features inspector` 3D viewer with bounded read-only physics, AI route/spawn, and StateScript VM overlays ([#62](https://github.com/leafall903/ORPHEUS/issues/62)), opt-in `--debug-control` local room pause/resume/single-step controls ([#84](https://github.com/leafall903/ORPHEUS/issues/84)), two-room worker timing benchmark. | Multi-player live room retest open. |

---

## Junkenstein's Revenge & Normal Cassidy (`0x3C`) Status

Headless offline regressions against pinned Python `e1128509` and prepared 104319 packs pass across the core encounter loop:

| Component / Mechanics | Rust Headless Coverage (`--ignored` with 104319 Packs) | Remaining Live / Client Gaps |
| --- | --- | --- |
| **All 14 Mode Cards & Gate** | Revenge/Endless/Challenge cards initialize; placed gate runs `0CA7`, health stages (`3` damage poses + destroyed), and team outcome projection (`MECGameMode` fields 7 & 9). Gate health/damage stages, encounter phases, and registration/rollback live in `RoomSimulation` ECS (`GateComponent`, `EncounterComponent`), verified by `9` self-contained unconditional unit tests (`game_combat::tests::encounter_ecs`); local base-pack runs lack Junkenstein graphs. | Gate visual state & post-match recap elapsed-time HUD binding remain open. |
| **Cassidy Primary & Fan (`0B8B`)** | Primary (`70`/`140` crit), Fan (`50` non-crit), ammo & passive/damage ultimate charge (`0.5`/`0.75` target-tag reductions): `scene_assets::tests::normal_cassidy_primary_and_fan_damage_all_boss_bodies`, `scene_assets::tests::normal_cassidy_common_npc_damage_charge_and_retirement`. | Live client attack feedback & HUD charge sync await client acceptance. |
| **Combat Roll (`01F0`)** | Collision-aware ~6.15 m Roll displacement across 5 directions, spawn-wall contact, and 5-to-6 ammo refill (`7b13651b` / `74d6ff78`). | Live directional/actor contacts & full hero matrix open. |
| **Flashbang (`224E`)** | Gravity/range flight blast (`800`-frame E-input replay), press-window gating, priority/excluded-tag checks, Reaper Wraith immunity rejection, velocity cap (`224E/31`), captured aim lock (`224E/34`), and targeted defender notices across all 5 Bosses. | Dynamic Boss spawn category bits & native hit-reaction animations await verification. |
| **Deadeye (`06AB` / `0CA5`)** | LOS target rays, lock progression, shot resolution, and natural two-cast 9-Boss Victory replay (`--junkenstein-validation` overrides tested separately). | Full live-client Deadeye lock visuals, SFX & natural Victory acceptance open. |
| **Bosses, NPCs & Score** | All 5 Boss body/controller pairs, common Zomnic/Zombardier/Shock Tire corpses (`8s` timer), Witch healing, Boss speech (`40E05F6B` / `junkenstein-boss-speech-v1`), `game_combat::tests::normal_encounter_first_reaper_finishes_intro_and_pursues_live_defender`, `game_combat::tests::normal_common_enemy_death_updates_authored_team_score`. | All 5 Boss intro cameras, Witch pistol swap, Zombardier combat, Reinhardt narration & Gallery client rendering await live 104319 client pass. |

*(Reference pack digests used for the original `2026-11-12` `13acf15` prepared-runtime baseline — Base `0688` SHA-256: `b11115f3201ebd0765cc8f0cf2ec0e72de6620b9913f8d17962c71649b528d58`; Enriched `0B92` SHA-256: `db7a7bdbddfb8b40e5e4c9a392d498ac36b13aed5144108311a985ea98e6d8f8`.)*

---

## Open Engineering Issues

| Issue | Scope |
| --- | --- |
| [#70](https://github.com/leafall903/ORPHEUS/issues/70) | Clean up transitional contract scripts, legacy prototypes, and stale branches. |
| [#131](https://github.com/leafall903/ORPHEUS/issues/131) | Complete the private verified Soldier gameplay trace comparison on the ARM64 runner and reconcile remaining RNG setup/parity items (`golden_python_room.py` now aligns `owner_component_start_frame = 1047` and `setup_delivery_ticks = 50`). |
