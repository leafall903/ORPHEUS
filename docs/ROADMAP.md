# Restoration roadmap

The target is Overwatch 1 retail **1.74.0.0.104319**. Soldier: 76 and the Practice Range training bots form the first acceptance group. Doomfist is the next hero after this group passes the shared gameplay and original-client checks. Record the source version and the smallest repeatable test for each claim. A milestone stays open until its stated exit criteria have been verified. The [verification record](STATE.md) and [changelog](../CHANGELOG.md) describe completed work and remaining limits. The [issue tracker](https://github.com/leafall903/ORPHEUS/issues) holds individual tasks.

The [Rust migration](RUST_MIGRATION.md) tracks implementation replacement and parity separately from gameplay fidelity.

| Milestone | Exit criteria | Current evidence |
| --- | --- | --- |
| M0 · Research and replay | Reproduce a known baseline with shared entity, time, input, event and state contracts. Track unsupported protocol and graph behavior. | Target build and sources are pinned. Controlled encrypted sessions and stored-state comparisons exist. Full coverage and reproducibility from public inputs remain open. |
| M1 · Body and weapon initialization | Soldier: 76 keeps the expected model, weapon and ammo HUD for 60 seconds, with ten successful switch-and-return cycles and ten exits and rejoins. | Automated body, weapon and lifecycle scenarios pass. Encrypted startup tests cover ownership and controller initialization under loss. Original-client stability and presentation still need acceptance. |
| M2 · Rifle and prediction | Hold and release fire, empty the magazine and reload repeatedly. Client ammo and effects agree with the server, including with lost and repeated input. | Selected shot, ammo, reload and loss scenarios have automated evidence. Original-client prediction and recovery still need verification. |
| M3 · Single-player combat | Repeatedly hit, miss, kill and reset a training target. Its visible health, identity and combat feedback agree with the server. | Headless Practice Range scenarios cover damage, health, death and respawn. Encrypted replay verifies per-viewer nameplate ordering. Original-client HUD, names, kill feed and sound remain pending. |
| M4 · Movement and two-client combat | Two clients agree on movement, line of sight, combat and respawn. Loss, delay and reordering recover without extra damage. | Collision-backed movement and selected two-peer protocol checks exist. Two-original-client agreement is pending. |
| M5 · Match rules and heroes | Complete a full match in one mode on one map and return to the lobby. Verify the declared abilities for each supported hero. | Shared hero mechanisms and partial match flow exist. A fixed world-target movement control covers two authored ability graphs. Native selection, timing and client acceptance remain open. |
| M6 · Fidelity and reliability | Compare timing and behavior against versioned references. Verify long sessions, recovery, performance and repeatable deployment. | Server-only deployment with prepared data and selected replay and loss checks exist. Wider comparisons and end-to-end acceptance remain open. |

## Project-wide checklist

A checked item records evidence for that specific scope. Its parent remains open while other requirements are outstanding.

### Junkenstein original-client acceptance

All items below remain open following the user's original-client observations
on 2026-11-12. An authored-graph test, a server health change, or a result
heading alone does not close any of these gameplay and presentation checks.
Use Normal Cassidy on the Rust server and retain the ordinary Arcade entry.
The user requires all listed items to be resolved and checked statically before
starting the next original-client validation session. Keep client checks pending
until that static gate is met; the previous live authorization remains valid.

| Open item | Required visible behavior | Existing reference and remaining gap |
| --- | --- | --- |
| Team score | Qualifying hits/kills update the expected team score, HUD and recap consistently. | A zero score in a completed result is not scoring acceptance. |
| Cassidy attack damage | Primary and secondary fire cause the expected target health loss, ammo use and feedback, including against bosses. | A stationary Normal prepared fixture checks owner input/ammo, 70/140 primary and 50 noncritical fan damage, room hit admission, pools and health/notice payloads for all five Bosses. Fan ruleset 290C and the declared primary critical fallback are restored. Complete death retirement now passes all 15 cases with once-only eligible defeat notices. Concurrent AI and original-client feedback remain open. |
| Ultimate charge | Idle and damage-driven charge follow the intended mode rules, agree with the HUD, and spend/recover correctly. | Rust now runs the actual 0B8B passive wait/value path and consumes applied damage with native target-tag reductions, live multipliers and cap. Shared synthetic flow and prepared actual Boss15/Deadeye probes cover timing, spend, source identity and end suppression. Room live-defender admission is explicit; complete native phase broadcasts and original-client HUD acceptance remain open. |
| Boss movement and pursuit | Each boss acquires, pursues, reacquires and attacks appropriate living targets; movement continues after its intro. | Spawn and survival evidence does not prove pursuit. Inspect the live controller-to-room movement handoff. |
| Boss damage reception | Player hits visibly reduce each boss's actual pools, with correct death and retirement. | The 15-case actual-body fire fixture reaches zero health, correct hit/kill notices and matching pool payloads for all five Bosses. The full exit check now passes all 15 cases with authored corpse retirement and exactly one eligible defeat notice each. Shared death query/voice services are restored for this probe; concurrent AI and live health presentation remain open. |
| Cassidy Flashbang | Throw, flight, impact/explosion, target effect, cooldown and subsequent input work without stopping the owner VM. | The 800-frame actual E-input replay covers authored flight/gravity/range blast and early-tap rejection/fresh-tap recovery. Physical admitted hits now reach the real effect wrapper on all five complete Boss pairs, with observer snapshots encoded through 120 frames. Shared press-lifetime, deferred-execution and array-order cases cover VM semantics; scoped AI blocking/interruption/recovery checks also exist. Immunity branches and native hit-reaction/control projection remain static gaps. Live acceptance is pending. |
| Cassidy Combat Roll | Roll moves through the shared collision-aware mover, refills ammo, ends and permits subsequent movement/fire. | 7b13651b plus isolated fixture 74d6ff78 verifies visible Roll/weapon recovery, 6.146484375 m stationary displacement, refill, cooldown and re-fire without stopping the owner VM. Prepared replay covers five directions and spawn-wall contact. Live directional/actor-contact cases and unknown native services remain open. |
| Gate entity | The actual gate is present at the correct transform; intact, damaged and destroyed states visibly agree with its health and objective. | The health bar and a gate-zero result do not establish the world entity's rendering. |
| Boss intro and camera | Each required boss intro plays once, switches to its authored camera, returns control/view correctly and does not repeat on later ticks. | Correct timing or a server intro count alone does not establish camera playback. |
| Witch weapon policy | Mercy/Witch normally heals valid allies, then selects and fires the pistol when no healing target is available; target changes recover correctly. | Verify both visible branches and their shared weapon/AI state transitions. |
| Zomnic movement speed | Visible travel speed and arrival timing match the intended ordinary-mode behavior across the authored route. | Prepared input comparison restores Zomnic 3.0 m/s and body speed modifiers; the street replay checks consumed velocity. Measure visible displacement/time and native movement presentation before closing this item. |
| Zombardier combat | The bomber acquires valid targets and visibly creates, launches and resolves its authored projectiles. | Target queries, projectile lifetime/damage and observer delivery must all work together. |

Keep the earlier requirements open as well: Reinhardt narration, friendly and
enemy hero voices, correct recap time, the invulnerable-Cassidy/high-health-gate
Deadeye victory line, the invulnerable-Cassidy/destructible-gate defeat line,
and actual NPC Hero Gallery selection/rendering. The latest failure run proves
the Defeat heading with gate zero and Cassidy alive; it does not close the
full failure-line matrix or any item in the table.

The 2026-11-13 static replay now completes the full natural Deadeye victory:
two actual casts terminate all nine Boss bodies, all five intros encode once,
the high-capacity gate survives, invulnerable Cassidy remains at 225 health,
and allied winner/result/post-match payloads encode. Early and all-intros late
gate-defeat replays also pass. These close the corresponding server replay
gaps only. Score, other declared static gaps and all original-client visual,
audio and Gallery checks remain open; no client session has started.

The isolated Flashbang body-adapter probe also covers all five Bosses and full
observer encoding for 100 frames. This extends the earlier Reaper-only check;
it does not close the projectile-to-effect bridge or immunity branches above.

A subsequent room adapter fix connects admitted physical Flashbang impacts to
the actual 0228/000F/224E effect chain. The restricted five-Boss paired replay
checks exactly one 25-damage hit and real observer effect-state encoding through
120 frames each. The 800-frame actual E-input probe also covers early-tap
rejection and fresh later-tap recovery after fixing retained press timestamps.
Four shared flows agree with pinned normal Python ability semantics; native
start-gate equivalence, immunity and native reaction/control projection remain
open. These are static integration results, with no new original-client check.

The timed-control adapter additionally checks the real Flashbang control-stack
predicate. The prepared actual Reaper Wraith control state rejects admission;
ordinary five-Boss bodies are admitted, and seven shared priority/release
cases agree with pinned Python. Full Wraith activation and the remaining
classification/tag/body-variable immunity branches stay open. The retained
effect schema carrier alone does not prove active hit-reaction animation.

For reuse, the inspected local Python reference is `e1128509`, particularly
`ow174/game/mccree.py` and the McCree gun, Roll and Flashbang tests. The local
Rialto skill/primary-reaction replay reports provide comparison inputs and
results. Some reports explicitly leave native-client acceptance false, so
their automated evidence must remain distinct from the user's live Rialto
observations and from new Rust/Junkenstein acceptance. Port shared mechanisms
in focused changes rather than importing another worktree wholesale.

- [ ] **Research and replay.** Rebuild required data from documented sources, resolve protocol and graph semantics, and reproduce complete sessions from recorded inputs and events.
  - [x] Pin the target build and document upstream sources and data provenance.
  - [x] Replay controlled encrypted server sessions and compare wire output and registered server state.
- [ ] **Login, profiles and social features.** Complete authentication, account and profile state, progression, cosmetics, parties and other social features supported by the target build.
- [ ] **Lobby and match lifecycle.** Verify queues, team assignment, game setup, hero selection, match handoff, joining, leaving, reconnecting and returning to the lobby.
- [ ] **Network synchronization.** Verify authoritative movement, prediction and correction, input timing, and state consistency for players and observers under delay, loss, reordering and repeated connections.
- [ ] **Heroes and abilities.** Cover weapons, reloads, aiming, movement abilities, passives, cooldowns, resources, ultimates, transformations and deployables with shared mechanics.
- [ ] **Combat and health.** Reproduce hit detection, projectiles, explosions, damage, healing, armor, shields, barriers, status effects, eliminations, assists and respawns.
  - [ ] Verify projectile and hitscan penetration by weapon and impact surface, including stacked targets, barriers and map geometry.
- [ ] **Maps and world interactions.** Verify collision, movement surfaces, spawn rooms, doors, health packs, destructible objects and environmental deaths for applicable maps and modes.
  - [x] Extract and integrity-check local collision caches for all 95 catalog maps and handle missing caches explicitly.
  - [ ] Use the shared character collision system for player and bot bodies. Allies pass through each other and live opponents block movement. Verify player versus player, player versus bot and bot versus bot in both directions, including free-for-all, crouching, simultaneous movement, death and body replacement. Check the authored character-collision setting and its movement modifier separately from map barrier masks.
  - [ ] Verify placement, team permissions, transitions and visible effects in authored spawn rooms. Reproduce out-of-bounds death and respawn on each applicable map.
- [ ] **Modes and match rules.** Verify objectives, scoring, timers, overtime, round transitions, victory conditions and results across supported modes.
- [ ] **Bots and scripted scenarios.** Verify training and tutorial behavior, including target identity, routes, combat, spawning, death, respawn and resets.
- [ ] **Models, skins and animation.** Verify first- and third-person models, skins, weapon handling, aiming, ability animations, and spawn, death and respawn sequences.
- [ ] **HUD, menus and visual feedback.** Verify health, ammo, overhead bars, reticles, hit markers, material-specific effects, elimination names, kill feed, on-fire meter, scoreboards and match screens.
- [ ] **Audio and dialogue.** Verify weapon, hit, critical-hit and death sounds, multikill and match announcements, ambient audio, music, contextual and randomized dialogue, and Easter eggs. Check listeners, timing and supported languages.
- [ ] **Stability and delivery.** Verify tick and packet budgets, long-running matches, recovery, reproducible deployment, and final acceptance with the original client.
  - [x] Verify server-only startup of the audited public source checkout using separate runtime data.
  - [ ] Measure server tick tail latency, world packet gaps, delivered owner corrections, and original-client frame time during sustained fire. Repeat with two clients before widening the playtest scope.
  - [ ] Compare first and repeated hero selections on the same map. Separate pick processing, body creation and binding, initial script delivery, and the original client's first playable frame.

#### Current Halloween static gate (2026-11-12)

Strict World-query integration additionally exposes missing foreign body
members: Deadeye target 8243, Witch ally 6604 and Boss killer 4437. All-card,
Flashbang and Roll prepared replays pass; Deadeye, enemy observer/healing and
all 15 Boss retirement cases are RED. Restore real foreign state services
before original-client testing or closing any encounter checklist item.

The subsequent foreign-component snapshot change restores reads of 8243,
6604 and 4437 from actual component state. Deadeye and Witch observer/healing
prepared probes pass again; all 15 Boss retirements remain RED at the four
query services above. Same-frame cross-component watcher ordering is not yet
proven by the frame-snapshot/polling transport. Full gameplay and client
checklist items remain open.

The current first-Reaper integrated replay additionally stops on a Tire's
corpse action 523EE38D at 0C93/11, before Reaper acceptance. Restore the
reference-supported retirement action entry point and re-run the complete
probe; neither its intro nor pursuit is currently accepted by this test.

The subsequent corpse-action change admits the reference's Tire 0C93/11 and
Boss death 0CBC/36,38 requests while preserving phase-exit and dead-pair guards.
The complete Normal first-Reaper prepared handoff/pursuit/attack/control-
recovery probe now passes. This supersedes that probe's Tire blocker above;
client intro/camera/audio, all bosses and complete win/loss remain unaccepted.

Public room-isolation f763beb9 is integrated with the encounter delivery
pipeline. Recovery and stock Soldier encrypted regressions pass on the
existing base pack; three-card entrance, all-card prep, Cassidy skills,
Witch observer/healing, Zombardier and the complete first-Reaper static replay
pass on the encounter pack. All 15 complete Boss retirement cases remain
RED at the four query services. This does not close gameplay, actual UI,
audio, camera/once-only intro, Hero Gallery client or win/loss requirements.

The mover-velocity/contains change removes 309B0570 and the subsequently
reached 821116A6 from the complete Boss probe. All 15 retirements remain RED
at AC829876 (killer filters), 9B7648C3 (foreign health) and B3E67A36 (tagged
targets). Real velocity is supplied for delivered actors; undiscovered or
retired actor lookup and exact same-frame cross-component wakeup remain
separate gaps. Keep the full static and original-client gates open.

The authoritative total-health change removes 9B7648C3 from the complete
Boss probe. Deadeye can read still-bound corpse totals independently of its
live aim list. All 15 Boss retirements remain RED at AC829876 and B3E67A36;
actor discovery/retired-identity and exact same-frame ordering gaps remain
separate. No gameplay/client requirement is checked off by this change.

Delivered target filter/tag facts remove AC829876 from the complete Boss probe,
and 0CCD's actual tagged-body queries now advance. All 15 retirements remain
RED at newly reached 40E05F6B states and Witch 0071 B3E67A36 queries. The 29
shared synthetic filter/target cases establish the supported query subset,
not encounter completion. Finish every listed static item before restarting
original-client verification, per the user's latest instruction.

Witch friendly-other filters and 0071's ordered combat-target queries now
use real team/tag facts and lexical candidate scopes; 28 shared cases cover
their supported subset. The early complete Boss probe loses B3E67A36 but
still fails all 15 retirements at 40E05F6B. Implement and verify the shared
voice lifecycle without faking a finished bark or deleting the death graph.
This progress closes no full AI, win/loss or original-client checklist item.

The generic 40E05F6B lifecycle now has explicit host services and a separate
room polling phase, with 38 shared synthetic scenarios for callbacks, timing,
outputs and cleanup. The actual Boss speech backend remains unavailable;
legacy intro/gate/victory specializations still need migration and original
client audio still needs acceptance. Do not treat the generic state replacing
an unsupported-state diagnostic as successful Boss death retirement. Keep
all requested static items and original-client gates open until verified.

The final complete Boss probe now fails all 15 retirements at unavailable
voice contexts, while the shared lifecycle contract and first-Reaper replay
pass. Continue with pinned `junkenstein_ai.bind_boss_voice`: explicit mode/body
voice-set facts, real killer-hero identity, conditional/weighted line eligibility,
actual duration rows and the existing combat voice notification path. Keep
legacy intro/gate/victory migration and both match outcome branches open.


The Boss voice backend now supplies actual resources, factory/mode voice-set
facts, weighted killer-specific defeat selection, raw sample durations and
room notice requests. The 191-case policy contract agrees with pinned Python
at explicit random draws. The complete Cassidy primary/fan probe passes all
15 Boss death retirements and exactly one eligible defeat request per death.
This supersedes the earlier unavailable-context failure record; it is not
full encounter/audio acceptance. General live voice-set overrides, RNG seed
sequence parity and legacy intro/gate/victory migration remain open. Finish
all named static items and both match branches before launching the client.


The defender body now mounts the complete authored 0CA5 startup instead of
only its intro listener. Its newly exposed 00707E1E query uses explicit
controller body-field facts; 18 cases agree with pinned Python 46b22130.
E9485CAF uses the verified native parent/base State registration, and the
prepared Deadeye probe asserts active defender startup state. This restores
the team-script entry path, not damage-driven charge or score. The 60-second
passive helper and the remaining named static/live requirements stay open.


The 60-second Cassidy helper is removed. The real 0B8B graph now owns passive
waits/amounts and consumes applied room damage with fresh target facts, ordered
half/three-quarter reductions, live multipliers and cap. Hit tags survive
hitscan/direct/blast paths. Room simulation binds live-defender admission;
death/result revocation drains the same-clock change and keeps owner VM faults
local. This is explicit host policy, not full native game-state broadcast
parity. Thirty-one synthetic cases agree with the normal pinned Python VM;
prepared Boss15/Deadeye probes check actual graph integration. Team score,
complete success/failure and the remaining static/client checklist stay open.

The 2026-11-12 static charge work also restores the pinned eight-second
Zomnic/Zombardier corpse policy. Shared boundaries and a prepared three-NPC
damage/charge/retirement replay pass; full encounter and client checks remain.

The natural early-defeat headless replay now proves common-wave gate destruction,
Cassidy's explicit test-only invulnerability and enemy native result/recap output.
It finishes before Boss spawns, so it does not close the full defeat-line matrix
or original-client acceptance. The score field/formula remains unestablished.

The static Normal late-defeat replay now covers all five natural Boss
introductions with one encoded camera instance each, completed handoffs,
post-intro Reaper/Roadhog movement and enemy result/post-match output after an
actual new gate hit. It also restores explicit retired-target query facts for
Witch healing and live foreign gate-slot change delivery to the mode VM.
This does not close original-client camera/view/audio/rendering acceptance or
the full Deadeye victory, score and Hero Gallery requirements.

An extended static victory replay also exposed a Zomnic's attacker-team lookup
after retirement at tick 49050. The shared retirement contract now checks
signed no-team (-1), live filter-team facts and unknown-service rejection.
This query repair does not by itself complete the victory or client checklist.

Static victory-view coverage now checks normal routed start/end parameters,
the real 0CEC self-stop and parent advancement to phase 5. The prepared gate
probe checks intact/three damage poses plus the zero-health branch and observer
encoding. Complete victory, score, native view/model/audio and Hero Gallery
acceptance remain open.

NPC preview resource correction now covers the 14 mismatched model roots with
exact Hero/preview/body guards and isolated cosmetic-builder pruning. The
source-only policy, restricted adapter installation and original native
iterator checks pass. This does not check off Gallery selection/rendering,
framing or animation, and no client was started. Score and full Flashbang
reaction remain static gaps before original-client verification resumes.

Normal score coverage now includes all eight authored base-point branches,
15 actual Cassidy Boss damage variants and three common-enemy cases, plus the
1188 HUD projection. All three full natural outcomes pass score assertions in
the default build: victory includes the gate bonus; early/late defeat has none.
A separate earned-score defeat retains score12 through native enemy result and
post-match output. Prepared NPC matrices and earned-score defeat pass in both
build lanes. The effective entity2499 stack binding is authoritative.
Native widget presentation and account high-score recording remain unverified;
mode TrackStat is unsupported. Full Flashbang reaction remains a listed static
gap before client verification resumes. Earlier score-gap notes above are
historical and superseded for this tested Normal scope.

The Flashbang investigation corrected generic filter tag-clause semantics using
72 native-checked synthetic cases, including all combinations of its three
excluded tags. This removes a second independent reaction-branch error. Boss
classification and complete reaction/control/animation remain static gaps;
this does not check off Flashbang or any original-client requirement.

The natural first-Reaper regression now reaches the timed control bridge from
an actual Cassidy E press and authored grenade flight rather than direct stun
injection. It checks actual NPC health loss, the50-tick attack/movement pause,
combat recovery and owner/NPC observer encoding. This strengthens the existing
functional control evidence; native classification and complete reaction
animation remain open before original-client verification resumes.

The Flashbang room bridge now also honors the authored Boolean entry condition
before priority admission, including effective linked body values and the
loaded local default. Six shared semantic cases match normal Python; two Rust
adapter boundaries stay explicit. This fixes an omitted predicate, without
closing native category initialization or complete reaction animation.


The category investigation now identifies 1B48140 as the map-placeable ENTITY
loader. All 230 exact castle headers match the original admission block;
31 request component 57, while the gate does not through this block. This
rules out applying its header interpretation to raw 003 Hero resources.
Dynamic Boss classification and complete Flashbang reaction remain open;
no gameplay flags change and no original-client checkbox is completed.


The Flashbang room bridge now honors the actual excluded-tag clause using
effective body facts. Eight native-backed cases plus grant/suppression release
boundaries cover the independent guard; the prepared wrapper checks loaded
data. Native category initialization, complete reaction animation and client
acceptance remain open. No original-client checkbox is completed.


The integrated original expression/filter replay now matches 64 declared mask/
tag combinations and production Rust. It confirms that the current 1|team mask
rejects the Flashbang reaction branch; the original result is not inverted.
The next repair must establish spawn category facts. No categories are guessed
from an accepting test mask, and no client/animation item is checked off.


A separate isolated accepting-input trace exposes another concrete reaction
gap: nested array/map destinations at 224E/172 are silently skipped, leaving a
null queued payload. Original destination parsing matches 12 constant-index
paths, but writes/growth/COW still need repair and validation. The 31528 queue
is instance scope 0. Classification is not the only remaining reaction issue;
no production category bits change and client validation stays deferred.


Nested pending-record writes are now implemented through the ordinary VM,
with native-shaped array coercion and identifier-map bucket storage. The
native 12-path/21-storage/two-growth-sequence fixture and dynamic-index/read-
notification checks cover this narrower repair. The isolated accepting-input
trace advances from a null payload to the next unsupported 224E/150 action.
No actor masks change. Complete reaction animation, spawn category facts,
actual combined-target conflict resolution and client acceptance remain open.
The Rust combined branch has nine conflicting files at the current committed
heads; a clean trial against origin/rust does not close that delivery gate.

The nine actual Rust-combined conflicts are reconciled in an isolated candidate,
preserving its submitted shared enemy/gate services in their newer implementations.
Candidate verification also exposed and fixed the missing-wire-frame/self-damage
clock error; the Zomnic gate replay now observes death and paired retirement.
Canonical integration and full client acceptance are recorded separately.

The unbound545 action now continues the queued Flashbang reaction using its
captured native base behavior. An isolated accepting-input trace exposes the
next message/State/velocity/filter services at224E/22,25,31,131. Actor category
facts remain separate and no production mask is changed to force admission.

The reaction's131 target is actually unset. Five original absent-target replays
and a shared evaluator contract now cover rejection before predicate services,
with malformed host configs still explicit. The22/25/31 services and native
dynamic actor category provisioning remain independent follow-ups.

The224E/25 base-State placement is now implemented with a shared begin/abort/
reentry, child cleanup and observer-encoding check, backed by original PC
registry/constructor/reader evidence. Complete reaction still requires the
remaining message/velocity services and actual dynamic category facts.

The 224E/22 attacker notification has a targeted defender transport and synthetic
sender/receiver regression. Velocity31, native dynamic actor classification and
original-client Flashbang acceptance remain independent requirements.

224E/31 horizontal cap is implemented through the VM, bounded body request and
room-owned NPC collision movement with frame expiry and shared fixtures. Native
row/enqueue/consumer evidence is separate from the existing host path motor;
production category provisioning and client acceptance remain open.

The224E/30 recovery gap is addressed by typed own-body yaw/pitch queries with
original numeric reference cases and live mover adapters. General foreign pose,
quaternion/platform behavior, remaining reaction diagnostics, native dynamic
category provisioning and original-client acceptance remain open.

224E/34 captured aim now has scoped unparented-NPC motor support with native
numeric references and shared capture/retention/layering/cleanup/reentry checks.
General linked transforms and player owner-full cache replication remain outside
this slice; complete reaction and production category evidence are still tracked.

The PC CreateEntity binding is now explicitly distinguished from server spawning:
its exact priority2 runtime callback is empty in two original dispatcher cases.
This closes that particular research path, not the production category gap.
Rust's actual Hero creation remains intact. Static component requirement lists
and existing cross-ASLR crash dumps do not establish final Boss category flags;
creation/replication inputs and original-client verification remain open.

PR #33 now separates the public source-only candidate from the retained local
research history. Its first hosted run exposed Rust 1.85 syntax incompatibility;
the repair preserves the declared minimum compiler and existing behavior
contracts. Hosted confirmation of the repaired candidate remains required,
alongside the open production category and original-client acceptance work.
