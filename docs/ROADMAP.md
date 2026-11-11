# Restoration roadmap

The target is Overwatch 1 retail **1.74.0.0.104319**. Soldier: 76 and the Practice Range are the first acceptance group. Record the source version and the smallest repeatable test for each claim. A milestone stays open until its stated exit criteria have been verified. The [verification record](STATE.md) and [changelog](../CHANGELOG.md) describe completed work and remaining limits. The [issue tracker](https://github.com/leafall903/ORPHEUS/issues) holds individual tasks.

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

- [ ] Verify the Rialto McCree PvE combat sandbox: manual five-enemy spawning,
  navigation, acquisition, attacks and specials, bidirectional combat, skills and
  ultimate, persistent corpses and requested console keys. The opt-in corpse
  lifetime policy is implemented. NPC scripts now mount authored weapon managers
  and sparse slots. Actual NPC creation, controls and original-client
  acceptance remain open. See [PvE combat sandbox](PVE_COMBAT_SANDBOX.txt).
  Shared resource-backed Flashbang control has offline interruption/recovery
  checks; native immunity/presentation and full hero acceptance remain open.
  Native input joins now preserve Deadeye's six-second timing and secondary
  cancellation; target acquisition/charging/damage and full ultimate acceptance
  remain open. See [state-input joins](STATE_INPUT_JOIN.txt).

A checked item records evidence for that specific scope. Its parent remains open while other requirements are outstanding.

Symmetra static placement preview (private WIP): actual ultimate/teleporter
commands now resolve a point from static collision using authored ranges and
native-verified math. Fixtures cover floor, wall occlusion, absent ground,
non-ground materials, yaw updates and the teleporter4m minimum. Server policy
uses line casts, no clip volumes and a constructed selection camera; native
sweeps, footprint/filter/dynamic geometry and real camera parity remain open.
Explicitly admitted commit retains the static point in the authored Photon
Barrier position expression. Actual input confirmation, actor deployment,
portal use and barrier effects remain OPEN; the full hero milestone is open.

- [ ] **Research and replay.** Rebuild required data from documented sources, resolve protocol and graph semantics, and reproduce complete sessions from recorded inputs and events.
  - [x] Pin the target build and document upstream sources and data provenance.
  - [x] Replay controlled encrypted server sessions and compare wire output and registered server state.
- [ ] **Login, profiles and social features.** Complete authentication, account and profile state, progression, cosmetics, parties and other social features supported by the target build.
- [ ] **Lobby and match lifecycle.** Verify queues, team assignment, game setup, hero selection, match handoff, joining, leaving, reconnecting and returning to the lobby.
- [ ] **Network synchronization.** Verify authoritative movement, prediction and correction, input timing, and state consistency for players and observers under delay, loss, reordering and repeated connections.
- [ ] **Heroes and abilities.** Cover weapons, reloads, aiming, movement abilities, passives, cooldowns, resources, ultimates, transformations and deployables with shared mechanics.
  - [ ] Complete Symmetra static combat acceptance. Primary base-DPS target
    selection and charged-orb release/flight/damage have command-to-pool tests;
    authored shield recovery and melee static LOS now affect actual pools.
    Actual attacks from all five Talon NPCs reset the shield recovery delay
    on body damage. Projected Barrier absorption preserves the original delay.
    A breaking hit preserves that delay if the body takes no damage. Enforcer
    pellets that land after the break restart it. A retained Sniper hit uses
    the shields already recovered through the preceding tick.
    The authored Zarya barrier clock runs during retained attacks. Sniper
    shots, charged orbs and sentries resolve on their respective sides of
    personal or projected expiry in the tested fixtures. An airborne sentry
    can attach and boot inside the retained interval.
    A limited world target/filter adapter now lets unchanged 157C charge/decay
    produce actual 60/120/180 DPS against players and all five NPC bodies.
    External five-NPC body/mover fixtures cover outgoing weapon damage, filters
    and retained death; incoming attack AI and specials remain unverified.
    An allied Genji redirects the four actual Talon ranged attacks aimed at
    Symmetra. The separate Assassin contact route still applies its pin.
    Allied Defense Matrix and Kinetic Grasp absorb live Enforcer and Sniper
    projectiles. The nearer absorber wins when both are active. Trooper and
    Heavy Assault rays still hit the defender. Missing command frames
    preserve Matrix's minimum hold and release timing for retained flight.
    Grasp expiry now converts before later retained impacts. Bonus shield
    decay and the next charged orb agree between retained and stepped ticks.
    Actual Talon attacks cover Teleporter transit, pin rescue and either
    endpoint's destruction. A replacement Symmetra can use the surviving
    pair and still enters cooldown when dismissing it.
    Heavy Assault capture blocks Teleporter use. After rescue, the
    interaction still requires range and a new press if the first failed.
    Transit admission checks the current movement capsule against map
    collision and live Ice Wall at the authored destination.
    Ultimate charge counts actual 0407 Mech damage. Mixed orb impacts
    credit character pools and exclude Teleporter device damage.
    Photon Barrier expiry and break restore charge on the correct
    retained tick for actual orbs and sentry beams.
    Sentry input now executes launch, attachment, boot, beam damage, stacked
    aura slow, charges/recovery and destruction in static world fixtures,
    including five NPCs. Client Pet reconciliation and surface hit geometry,
    armor cadence/barrier refill, other deployables, ultimate and cross-hero/five-NPC
    matrices remain open. Original-client acceptance is separate and pending.
    Teleporter's 15F5-to-1647 shared action now relocates actual mover/physics
    state with declared admitted inputs; placement/pairing/use gates and endpoint
    lifecycle remain open, so this does not complete teleporter acceptance.
    Its54FEB21C server finish callback now matches1,295 original instruction
    cases; client scheduling/transport and the following550D0C5C service remain open.
    Actual primary input from Trooper/Sniper/Heavy Assault damages Symmetra
    shields, with miss/wall/ally protection. Enforcer/Assassin input and complete
    NPC control, AI, specials and bidirectional matrices remain open.
    Catalogs now preserve all five authored linked AI-control definitions and
    their initial graph evidence; a separate reproducible shard adds18 missing
    control graphs. Actual controller mounting/possession and execution stay open.
    See [Symmetra combat](SYMMETRA_COMBAT.txt).
- [ ] **Combat and health.** Reproduce hit detection, projectiles, explosions, damage, healing, armor, shields, barriers, status effects, eliminations, assists and respawns.
  - [ ] Verify projectile and hitscan penetration by weapon and impact surface, including stacked targets, barriers and map geometry.
- [ ] **Maps and world interactions.** Verify collision, movement surfaces, spawn rooms, doors, health packs, destructible objects and environmental deaths for applicable maps and modes.
  - [x] Extract and integrity-check local collision caches for all 95 catalog maps and handle missing caches explicitly.
  - [ ] Verify placement, team permissions, transitions and visible effects in authored spawn rooms. Reproduce out-of-bounds death and respawn on each applicable map.
- [ ] **Modes and match rules.** Verify objectives, scoring, timers, overtime, round transitions, victory conditions and results across supported modes.
  - [ ] Run Rialto Retribution story from start to extraction/results with authored cinematics and correct enemy AI. Placed mission graph roots/overrides enter isolated extraction. Native-backed ForCode, UUID/primary-possessor queries, PlayScript requests, type-list state lifecycle, numeric modifiers, auxiliary interfaces/effect routes and queued schema/start bindings have probes; original-client identities and possession updates agree. An explicit forward host records native scope history and runs the ordinary scope phase before deferred channel1. Actual0F45->0EC6 reaches frame500 with supplied services/clocks, including effect creation/retirement without injected callbacks. Full phase/clock mapping, rollback, orientation cleanup and engine consumers remain open. An explicit GameServer/Match factory now prepares 146 placed components plus mode100B in one shared research world, with live variables/messages and owned cleanup. It still stops strictly at D38D0035/node106 before later component execution. Native scene phases, full hosting/replication, cinematics and enemy AI remain unaccepted. See [Retribution restoration](RETRIBUTION_RESTORATION.txt).
  - [ ] Create and host Rialto's selected gameplay script placements with definition defaults, matching instance overrides and replicated graph lifetimes. The normal catalog still omits all 123 nonempty placed script components. An explicit offline starter preserves native schema ordering and prepares graphs at the first update clock, backed by 108 schema and 36 initialization comparisons. Six native-backed literal config classes remove the selected schema stops: all 146 components configure in the isolated 371-create catalog, with 124 graphs and 542 queued writes. Of 123 nonempty components, 50 complete one frame and 73 retain strict faults: 66 require actual entity-control services, three require animation-alias components, one requires model-look scope registration, and three retain one action. DataFlowMapping matches 2070 original State/provider cases; both actual 0E61 components now allocate it and retain the later strict 0F647E55/node13 action before activation. Independent authored-node fixtures register/remove mapping061D, answer float1 and pass four codec checks. These fixtures do not establish completed component first frames or mission replication. Complete data-flow services remain open. The animation-alias State matches 966 native cases; all three actual 0D32 components complete frame0 with declared services and pass six on/diagnostic-off codec round trips. Two subsequent original-client matches verify native request rows, handles and current-alias selection/restoration for these three samples: 24 phase ACKs, six stream retirements, 24 native subject samples and 348 variable value/tag checks. Early off captures preserve pending engine selection until later reads settle. Visual animation playback, native true-mode timing and complete animation services remain open. The model-look State matches 4320 original scope/tick cases; actual 144E/node26 completes frame0 with explicit host/model services and passes 187/186-bit on/diagnostic-off codec checks, without a source override. Two subsequent original-client matches verify native scope registration/dispatch, model look override/restoration and look-stack rows for this exact sample: eight phase ACKs, two stream retirements, eight accepted captures, 28 variable checks and three on/off cycles. Fresh native identities reset counters. The inspector distinguishes nativeEntity+AC from networkEntity+B0; 17 earlier captures preserve the corrected inspector error. Visible appearance, complete engine services, phase/clock mapping and production hosting remain open. Two earlier client matches verify actual 0F65/120E frozen descriptors and eight initial values. Two later matches verify the actual 120E raw State2 sample: authored frame0 with declared server services and explicit observed source binding, diagnostic abort, eight phase ACKs after create ACKs and stream retirement. Read-only native State/model flags toggle and restore across three on/off cycles, including fresh entity/instance identities. Complete engine services and all-placement behavior remain unaccepted. An explicit Match factory now owns initial mode/placed startup, ordinary scheduling, live foreign-variable dependencies, queued messages and failure/leave/server-stop cleanup. The actual 371-create catalog prepares 147 components and retains rootD38/node106. Prepared entrance readiness stays blocked; shared-runtime storage export is unsupported. Entity-instance PlayScript caller/owner and builtin001B source cache now derive from live component bindings, backed by294 original getter/setter, seven allocation-source and seven owner-identity cases. New loaded-core instigators precede defaults and remain distinct from start-entry caller bindings. Explicit component queues now submit requests and preserve resource waits, policies, reverse flush and transient lifetimes, backed by277 original queue and2688 lifetime cases. Actual0F45/node48 creates0EC6 without a manual core handoff. Shared VoiceStimulus callbacks now begin caller40E05F6B/node39, allowing helper parameter41 before the controller-message component/node22 stop. A separate actual voice-state fixture now completes39->40/41->35->42 through unchanged plugs, with typed-array cleanup and no fault. Native973 voice and944 array comparisons pass with declared services/storage adapters, plus two explicit array-limit gates. Keyed storage, linked insertion/checkpoints, real voice services and the complete global poll driver remain open. Passive VM storage covers35 State models. Full startup, allocator/loader/deletion/phases and live transport remain open. Full entity script component definitions now survive extraction, including shared-schema graph references and empty000E defaults. An initial host API distinguishes unknown data from actual component absence. Actual000E/0F44 startup passes the declarative9D7 State. SetVar/SetVars now use the native value interface, collecting config arrays and preserving their different missing-pointer rules. The retained bundle has108 direct array assignments, including30 in the static Rialto closure;100 real configurations replay with explicit dynamic inputs. 216 native assignment cases and13 behavior tests cover value/guard/order distinctions. The ordinary controller keeps81A9AB10 strict: native PC loader/fallback and false-branch evidence do not establish its server meaning. Earlier explicit no-output/false/true fixtures reached common0C90 and retained1AAB4B4F before its iterator was implemented. Controller hero iteration now preserves scalar-first, collected-value and nested-group interfaces, order, dependencies and component ownership, backed by1296 original cases and21 behavior tests. Full native catalog/availability policies remain explicit unimplemented owner services. With declared inputs, three role profiles pass0C90/node55. The65ABFCCC team query now preserves two lookup passes, dependencies and signed team indices, backed by1600 native query and128 filter-decoder comparisons plus21 behavior tests. All three explicit profiles prepare0C90/node41 message06FF hero-list/team parameters and retain the unknown mode receiver; delivery is unaccepted. Strict outgoing message construction now preserves scalar success separately, generic targets, inherited/overridden source and ordered array parameters, backed by3456 original action comparisons and18 behavior tests. Parameter008D is corrected to tuple(0,). A subsequent native Entry backend preserves different instigators, repeated full-key parameters and transient builtin scopes, queues assignments before entries, and resolves source current possession through declared descriptors. It matches1350 original binding,3024 selection,15 scope and60 subject cases, with25 behavior tests. Actual named message entries now receive the native idle-transient retirement exemption, backed by2688 original lifetime comparisons. Actual100B's15 entries pass isolated pre-init queue fixtures without running ordinary mode initialization or entry plugs. A standalone persistent message-State manager now matches17920 original selection,17920 collection and3554 mutation comparisons, with19 behavior tests. The strict sending State now mounts the manager through explicit SceneWorld Contexts, with4626 native registration,396 initialization and16 timer comparisons;768 native writer and768 reader cases verify reply encoding.22 sender tests and two additional manager cleanup tests cover the integration.44 of47 actual sending nodes complete isolated pre-init registration across three declared role profiles; three nodes retain unknown queries. No authored plugs or ordinary initialization run in these fixtures. Native engine roles/frame/filter policy remain explicit. A sender-backed parameter scope and three-mode receiver cache backend now match936 and624 original cases, with26 behavior tests. The VM provider stack is shared across instances, preserves failed destinations and null barriers, and uses Source-instance/Receiver-watcher evaluation. Ordinary Entry scope regressions include24 cases with GUID0. The receiving State now mounts explicit contexts with native dual channels, epoch/tick selection, lazy getters, row-ID bindings, context outputs, subgraph transitions and codec. It matches3268 native control,1728 getter,1288 binding and360 variable resolver cases;4864 writer/4864 reader and256 binding-policy suffix cases pass, with24 behavior tests. All535 extracted receivers complete1605 isolated empty-source/pre-init role fixtures without authored plugs. Keyed type12/indexed paths, real engine roles/generation/filter policies, production mounting and original-client acceptance remain open. Full engine filters and native server forwarding remain open. The ordinary controller still keeps81A9AB10 strict. Config evaluation from node147 is verified without claiming its State lifecycle. Entity existence/current-possession queries now match1680 original instruction cases; with explicit query contexts and two declared controller/body objects it creates0C90 and1194 and retains81A9AB10/frame0. Known absent objects, missing components and no-output failure stay distinct; forward possession is not inferred from the reverse catalog. Two earlier read-only hero-switch captures agree with the current-object getter; no fresh client session is claimed for this query change. The failed bound receiver still rejects helper-message/node22. Five former placed9D7 stops complete their first frame. Two independent original-client matches accept the actual0F44/node33 common State allocation, three on/off cycles, unchanged typed variable940, fresh counters and retired bindings through an explicit owner-frame fixture. Complete controller execution is still open. A real Match prepares148 components/queues with one actual controller, retaining rootD38/node106 before controller execution. Native mode mounting and complete actor lifetimes remain open. Native scene phases, dynamic actor/controller/body lifetimes, full services/replication and mission execution remain open. See [Placed script initialization](PLACED_SCRIPT_INITIALIZATION.txt), [Entity-control State](ENTITY_CONTROL_STATE.txt), [Animation-alias State](ANIMATION_ALIAS_STATE.txt), [Model-look State](MODEL_LOOK_STATE.txt), [Data-flow mapping](DATA_FLOW_MAPPING.txt), [Shared scene host](SCENE_SCRIPT_HOST.txt), [PlayScript source context](PLAY_SOURCE_CONTEXT.txt), [PlayScript queue/lifetime](PLAY_SCRIPT_QUEUE.txt), [Shared voice stimulus](VOICE_STIMULUS_STATE.txt), [Native array actions](ARRAY_ACTIONS.txt), [Entity script component definitions](ENTITY_SCRIPT_COMPONENTS.txt), [Shared-variable State](SHARED_VARIABLE_STATE.txt), [Entity queries](ENTITY_QUERIES.txt), [Variable assignments](VARIABLE_ASSIGNMENTS.txt), [Controller hero iteration](CONTROLLER_HERO_ITERATION.txt), [Entity team query](ENTITY_TEAM_QUERY.txt), [Game-message action](GAME_MESSAGE_ACTION.txt), [Message reception](MESSAGE_RECEIVE.txt), [Persistent message manager](MESSAGE_MANAGER.txt) and [Sending message State](MESSAGE_SENDER.txt) and [Message parameter scopes/caches](MESSAGE_PARAMETERS.txt) and [Receiving message State](MESSAGE_RECEIVER.txt).
  - [x] Restore and verify entry through all 47 Archives/Junkenstein difficulty and challenge cards. All cards passed original build-104319 client menu selection, load, hero selection, actual-world entry and settled-menu return. The final client completed 39 consecutive cards and closed normally. Accepted external inputs were promoted to the normal service with backups; native deployment smoke checks passed. See [PVE entrance acceptance](PVE_ENTRANCES.md). Mission AI, objectives, challenge effects, story hero restrictions and long-term stability remain outside this completed entrance scope.
- [ ] **Bots and scripted scenarios.** Verify training and tutorial behavior, including target identity, routes, combat, spawning, death, respawn and resets.
  - [x] Restore Tutorial entry with its authored solo red team and fixed Soldier: 76 controller, and verify actual-world entry and menu return. Five consecutive cycles and a subsequent Practice Range cycle passed in one original client; Tutorial and Practice Range also passed normal-service deployment smoke checks. Tutorial guidance and scripted progression remain open.
- [ ] **Models, skins and animation.** Verify first- and third-person models, skins, weapon handling, aiming, ability animations, and spawn, death and respawn sequences.
- [ ] **HUD, menus and visual feedback.** Verify health, ammo, overhead bars, reticles, hit markers, material-specific effects, elimination names, kill feed, on-fire meter, scoreboards and match screens.
- [ ] **Audio and dialogue.** Verify weapon, hit, critical-hit and death sounds, multikill and match announcements, ambient audio, music, contextual and randomized dialogue, and Easter eggs. Check listeners, timing and supported languages.
- [ ] **Stability and delivery.** Verify tick and packet budgets, long-running matches, recovery, reproducible deployment, and final acceptance with the original client.
  - [x] Verify server-only startup of the audited public source checkout using separate runtime data.
  - [ ] Measure server tick tail latency, world packet gaps, delivered owner corrections, and original-client frame time during sustained fire. Repeat with two clients before widening the playtest scope.
  - [ ] Compare first and repeated hero selections on the same map. Separate pick processing, body creation and binding, initial script delivery, and the original client's first playable frame.

Symmetra shared control frontier (private WIP): actual McCree Flashbang input
now has four direct Symmetra tests: beam interruption/recovery, cancelled orb
charge with fresh120-damage release, body replacement and wall/ally/range
protection. This is reconstructed control-policy evidence only. Full native
status/defense, sentry/portal/barrier interruption and hero acceptance remain
open; no new owned lane commit or parent integration until complete acceptance.

Symmetra armour frontier (private WIP): explicit primary/sentry beam classification
now applies the historical OW1 30% armour reduction rather than halving every
small frame sample. Four real-input/pool tests cover two authored armoured
heroes, all three primary levels, sentry damage and unchanged orb/melee policy.
Native armour exhaustion boundaries, all damage modifiers and complete combat
acceptance remain open. Historical source links are in SYMMETRA_COMBAT.txt.

Symmetra ultimate pre-deployment evidence (private WIP): three actual-input tests
verify the1680 charge gate, authored0.35-second right-click cancel with charge
retained/reentry, and Flashbang placement interruption/recovery. Native charge
generation and positive barrier creation/geometry/blocking/lifetime remain open.

Symmetra shared dependency review: parent2ca9178 input joins imported privately
as795708c; current57 join/VM/hero,553 game and63 Symmetra-suite checks pass.
Teleporter1571 joins require placement success arrivals; the actual placement
service is still missing. Original-client and complete hero acceptance stay open.

An additional actual Flashbang test proves teleporter placement-ability
interruption, held-input suppression and reopening after expiry. The five-test
Symmetra Flashbang module passes; positive portal deployment/cancel stays open.

Symmetra placement directions (private WIP): native evaluator inspection separates
body X/Z axes from view-forward with stored pitch. Six real-body/query methods
and504 original-instruction comparisons validate the narrow upright owner
adapter and actual ultimate axis Stack. Native engine lookups use adapters;
confirmed orientation assignment, placement casts and positive actor deployment
remain open. Full hero goal stays active; no owned commit/merge before acceptance.

Symmetra placement role correction: three BD02 child roots in156B/1571 are
client-only. The server confirmation chain instead runs57 success ->89 -> Wait0
->36 charge spend ->82 orientation assignment. Three role/declared-callback tests
pass; their success/failure callbacks are fixture inputs. Native admission,
geometry, actor creation and full acceptance remain open. No new server host
or snapshot fields were retained from the discarded trial binding.

Symmetra placement clock/frontend (private WIP): existing State.tick is native
+70, correcting the earlier extra-host diagnosis.4C96470A now keeps last/latest
uint32 frames and dispatches replay or preview under typed World clock services.
3459 original-instruction comparisons pass; clock/resource/replay/geometry
adapters are explicit.37 State models export passively.31 placement/VM,73 hero,
553 game and837 script methods pass, plus one new read-only server hash case.
Forward-server clock policy is reconstructed; native prediction and geometry
remain open. Photon Barrier receiver002A/098 and21 filters are manifest-verified
external metadata; relation flags, shape/blocking and positive Pet creation are
unaccepted. Full hero/cross-hero/five-NPC acceptance and original-client checks
remain open; no owned commit or merge before the full static gate.

Symmetra placement history (private WIP): typed records now reproduce native
CAB450/C9FCE0 unsigned frame-60 retention, reverse swap-removal and same-frame
first-record retention.8192 native instruction cases and38 placement/VM methods
pass with declared role/preallocated capacity/debug checks disabled. Helpers
are not mounted as admitted client history; prediction ownership, allocator
failure, replay entity validation and confirmation transport remain open.
CB6BA0 preview/result/commit andC98E90 replay paths are traced externally;
geometry/result publication and positive Pet deployment remain unaccepted.

Symmetra native result/confirmation investigation:384 CB6BA0 and144 generic+
variant input cases pass with declared services. Priority2's special confirm
input latches340 and dirties property2C8; priority1 requests begin. Original
property bitset instructions execute, while scope/type/begin services are adapters.
Priority values alone do not prove authoritative factory roles; that selection,
property forwarding/commit, iterative placement geometry and actor creation stay
open. Production continues to implement only its declared priority1 callbacks.

The independent Confirmation primitive now matches864 native input/property-
scope cases with real nested entry/exit/reset instructions and explicit owner
services; it is not automatically mounted as authoritative transport. Four
behavior methods and848 full script methods pass. The previous scope adapters
missed outer-entry value clearing, now captured without inventing server policy.
All five NPC catalog mover records match the parent's independent43-row pack
and original XML hashes. Under that immutable runtime, existing10 NPC methods
and2 new capsule-radius/top/established-hit wall methods pass. These are static
fixtures; the pack lacks real-map collision caches. Full acceptance remains open.

Symmetra static placement result publication now uses a typed declared outcome,
native write/frame/history/output order and explicit history/clock services.
384 original-instruction cases compare production.8 focused methods include
actual156B preview/charge routing with fixture geometry/commit;854 script,77
hero,553 game and23 VM snapshot methods pass. No new State fields (37 models).
Geometry stays unsupported by default; dynamic transforms, role/property commit
transport and positive Pet deployment remain open. Full static acceptance is incomplete.

Symmetra projection math: original5C7970 polynomial matches6109 return words;
48 rotations and216 late-native retry profiles match with zero observed component
error under declared services. Six witness methods and860 script methods pass.
This independent primitive preserves the native retry count/residual/rotation,
including repeated first directions, and is not an authoritative geometry provider.
Declared contact refinement additionally matches1,680 original late-block profiles
with explicit clearance replies (maximum component error9.313225746154785e-10,
tolerance2e-5). Eleven direct and865 script methods pass. Candidates remain
unvalidated; no geometry provider is mounted.
Full pose, clipping/casts/contact admission, CA0E50 validation, commit transport and
positive actor deployment stay open. No owned commit before full static acceptance.

Shared Ability start buffering now preserves the original press timestamp while
cooldown/condition/stack gates are closed. Actual Symmetra sentry input reproduces
the previous indefinite refresh after charges reach zero; regression coverage
includes expired early taps, near-recovery taps, held input and new ready input.
79 hero,865 script,553 game and23 VM snapshot methods pass; Ruff36 files passes.
This does not close native start-check parity or the full static combat goal.

Placement sample checks now reproduce C9E800/C9C760 with explicit cast/clipping/
surface/final-geometry services:432 query and576 status/order/output profiles
match original instructions, with zero observed component error.72 CA5550
static/no-body-sentinel cases match the strict normal threshold/flag policy.
No actual query engine, dynamic surface or authoritative geometry binding added.
Shared mover throttle attributes17/18/19 now act before acceleration; a declared
actual23A utility state on Symmetra verifies both input signs, scope restoration,
next-frame timing and unchanged raw bytes. Natural caller routes remain open;
67-graph resource audit still lacksF9C. Full hero acceptance/merge stay false.
18 direct placement,80 hero,872 script,553 game and23 VM snapshot methods pass;
Ruff39 changed Python files and diff checks pass. No complete server-snapshot
suite rerun claimed.

Candidate collection/selection:96 original count profiles,1,296 loop profiles and
72 selection profiles match exactly. Counts use trunc(f32(distance/step+.5));
fallback spacing is distance/count when count>0. Success can stop the first phase
and shorten the second phase; selection minimizes transformed height difference,
retaining the first tie and original point/entity/id/sample index.24 direct and
878 script methods pass; Ruff40 files passes. These primitives remain unmounted;
full CA0E50 pose/basis/cast/evaluator/transform/allocation contracts and complete
hero acceptance remain OPEN.

Resolved camera projection now reproduces1A77F80:1,728 point/return profiles are
bit exact and216 original projection+selection profiles agree. Selection uses
projected screen-y, including perspective/viewport math, rather than world-y.
Native camera/basis/FOV/key/viewport services and below-guard caller-buffer state
remain open. C90212 minimum-distance pair correction matches224 original cases.
30 direct/884 script methods and Ruff41 files pass; these primitives remain
unmounted. Full placement geometry/commit/actors and complete combat acceptance
are still incomplete.

Default-profile perspective construction matches192 original matrix/half-angle
word profiles; view/basis construction, real key/viewport and nondefault camera
profiles remain OPEN. Canceled notification markers now retire with their events;
ending states do not receive CHANGED events. Cleanup is queued before ability
arbitration release as explicit server reconstruction (native order unproven).
Actual Symmetra inputs pass three ultimate enter/cancel/beam-resume cycles with
charge preserved.37 focused,81 hero,890 script,553 game and23 VM snapshot methods
pass; full static hero acceptance remains incomplete.
The complete15-method server snapshot suite also passes(610.046s), including
stored two-player damage/death/respawn/hero-change replay. Ruff43 files passes;
no persistent VM fields added. Full acceptance and merge readiness remain false.

Resolved view construction now matches1,620 emulated native matrix word profiles;
60 constructed-camera selections agree. Position/basis inputs are explicitly
admitted; no implicit normalization or authoritative owner/geometry binding.
Symmetra26F0 authored FirstPerson1.3/.91 and opposing McCree1.425/.9975 now feed
both rays and player target descriptors. Three actual stance/low-cover methods
pass after8 baseline failures.37 geometry,84 hero,894 script,553 game and23 VM
methods pass; Ruff44 files passes. Full deployment, reload/ultimate lifecycle,
resource acquisition and complete five-NPC/cross-hero matrix remain open.
The full goal stays active, with permitted server reconstruction clearly separated
from original-client/native parity gaps; partial changes remain uncommitted.

Photon Barrier now has actual primary confirmation through a scoped server owner,
.25s resource cast and4000HP actor with resource12s ordinary duration. Fixtures
cover rotation, enemy hitscan/beam/orb/sentry blocking, own/explicit team ally
pass-through, nearer static cover, breaking-shot absorption, expiry, independent
survival after death, hero switch and leave cleanup. Manager/plane/lifetime/seen
state is included in read-only server hashes. A differing world/VM clock origin
reproduced premature expiry; the server host now times lifetime from creation.
Map-spanning plane/current poses/world-creation timing/whole-projectile absorption
are explicit reconstructions. Same-side splash, complete defenses/status/NPC
matrix, actual ultimate charge generation and teleporter deployment remain open.
The full hero goal stays active and changes remain uncommitted.

Actual Symmetra reload now has three additional acceptance methods without runtime
changes:0x400/logical14 starts partial reload,1.095s refill and1.5s end boundaries,
full magazine rejection, melee pre-refill cancellation/fresh reentry and subsequent
actual beam damage. Actual Flashbang now verifies input lock across refill
and completion, while lethal rifle hits verify cancellation before and
after refill and a fresh magazine on natural respawn. Actual Mei freeze
and the Talon Assassin pin also verify started reload timing, input lock
and beam recovery. Thaw admits a fresh reload press after rejecting
attempts during freeze. Other status and weapon combinations, and
original-client behavior, remain open.

Symmetra teleporter has actual static confirm and bidirectional use with 272D
and 26F2 endpoints. Tests exercise startup, owner/ally access, range, static LOS,
damage across shield and health pools, dismissal, cooldown and cleanup.
Complete dynamic placement and child-graph behavior, status/attack combinations
and original-client presentation remain open.

Symmetra uses the authored 0B8B natural and damage charge paths in live static
fixtures. Actual weapon and sentry damage, cap, active barrier suppression,
respawn retention and an earned 1680-point cast are covered. World updates
without commands now exercise passive gain, late weapon input, death and
respawn, and recovery across actual Photon Barrier expiry. Dynamic tag and
modifier context, full attack and defense matrices and original client stay open.

Pharah's actual Rocket Launcher now has hostile, allied, break and retained
expiry evidence against Photon Barrier. Symmetra's two-way orb exchange,
primary Deflect ordering, fixed-aim Barrage and lethal beam interruption
are covered. Matrix and Kinetic Grasp arbitrate authored Barrage rockets
against the wall, including Matrix release during the burst. Rocket
Launcher now has scoped static Matrix and Kinetic Grasp interception
through its implicit volley, including reflected return. Barrage now
uses the authored launch cone and two curved motion rows, with actual
terrain, retained Photon Barrier expiry and absorption contacts. Native
projectile identity, animated launch pose and other Pharah defense
combinations remain open.

Curved Barrage also has actual personal and projected particle barrier
coverage, including projected protection on Symmetra. The tests cover
direct and splash break, overlap and retained expiry.

Actual Pharah Concussive Blast now pushes player targets through its
resource-backed impulse. Photon and Projected Barrier protect Symmetra
until expiry. Allied passage, self push, Matrix and Grasp are covered.
The five prepared manual NPC bodies also have actual response evidence,
including Heavy Assault's zero response, retained Trooper flight, and
Symmetra's beam damage and NPC return fire after landing. Actual sentry
acquisition and release across impulse-driven range changes match in
stepped and retained updates for the five manual bodies. A two-sentry
fixture also verifies independent line of sight, slow cleanup and
reacquisition during the same impulse. Two Symmetra players now have
separate per-tick damage and charge evidence, including an actual Helix
kill of one owner inside the retained window. A surviving sentry now
credits the replacement Symmetra body after a retained respawn at the
same tick as stepped updates. When respawn precedes the retained
window, the new passive clock still uses the scheduled deadline and
the sentry credits all 64 retained ticks. Two simulated body allocation
failures also preserve retry timing and post-respawn credit when the
successful attempt lands before or inside the retained window. Separate
failure updates preserve the same sentry and Photon Barrier. Actual wall
expiry restores sentry and passive charge on the replacement body.
Native force
conversion, autonomous movement and remaining combinations stay open.

Symmetra now has actual incoming manual attacks for all five Talon bodies.
Enforcer launches resource ring pellets with travel and falloff. Assassin uses
resource contact and delayed damage states with a server-owned pin scope.
Static cover, range, team, barrier, stop, source death and Flashbang cases are
covered in the tested subsets. Native acquisition and the complete interaction
matrix remain open.

Symmetra has live player-input evidence for frontal Genji orb reflection,
0407 Mech Matrix orb absorption, beam bypass of both, and Transcendence
damage immunity against the primary, orb, melee and sentry. The Matrix
fixture selects the prepared Mech form explicitly. Natural D.Va form
transitions, other defense/status cases and client
acceptance remain open.

Sigma's live Grasp has actual orb absorption, delayed conversion, extra
pool consumption and expiry evidence, plus beam, rear-hit, cover and
Flashbang controls. Five and seven simultaneous orbs verify conversion
below and above the 400-point shield cap. A recast after cooldown adds a
new orb to decaying shields, with one case below the cap and one at it.
A charged orb and Flashbang convert together in three arrival orders,
including the same tick. A rear Flashbang also interrupts a recast and
spends the old bonus pool without converting the newly absorbed orb. A
lethal rear orb volley cancels conversion, and the respawned body can
convert a new orb from a clean pool. Full mitigation, ability lifecycle and
NPC interactions remain open.

Reinhardt's 0095 barrier has actual beam, orb, melee and sentry-occlusion
evidence using the build 104319 mesh. The tested cases cover rear exposure,
world cover, break without spill and lowered regeneration. The 157C tagged
refill branch has actual rate and cap evidence against Reinhardt's barrier
and Photon Barrier. Native mesh pose and acquisition timing, remaining
splash and other shield actors remain open.

Zarya's personal and projected particle barriers have actual beam, orb,
melee, energy, breaking-hit and ammo-refill evidence. The projected search
rejects enemy and statically covered targets. An active personal barrier
intercepts sentry damage and suppresses slow until expiry. Native bubble
surface, further control, energy decay and combined lifecycle remain open.

Actual Flashbang inputs now cover projected protection of a charging
Symmetra, personal bubble break, allied Grasp and Transcendence. The
remaining control and mitigation interactions still need direct evidence.

Projected Zarya protection now has actual Assassin contact, breaking-hit
and sustained-phase rescue evidence on Symmetra. Projection during the
initial wait releases control before the next damage tick. Native control
admission and other cross-NPC effects remain open.

Orisa Fortify has actual Symmetra weapon and sentry mitigation evidence,
with Flashbang, Assassin pin, sentry slow and expiry controls. Further
control, critical and combined mitigation interactions remain open.

Photon Barrier and Winston's sphere can both stand in the attack path.
Either can take the near hit and protect the farther layer on orb break.
The next beam reaches the farther layer after the break.
Two same-tick orbs can also break both layers in order. The impact
tick and per-orb pool changes match when a world update skips 90 ticks
after both flights start.
Fresh orbs instead take their first world step at the jump endpoint.
A mixed volley preserves the established orb's earlier impact and
the fresh orb later sees the resulting barrier state.
With Kinetic Grasp behind both layers, the third orb is absorbed
only after both barriers break. The resulting 72 shields and impact
order match across stepped updates and a 90-tick gap.
Five nine-orb volleys also break both barriers from full health before
expiry. Grasp absorbs the five remaining orbs and grants 360 shields.
One world update per established volley reproduces every impact tick
and shield pool transition for both barrier orders. The replay preserves
the original charge and release schedule.
Extending the final flight update to 90 ticks still reproduces all nine
contacts before the retained window and leaves 335 converted shields
after the subsequent rear Flashbang at cast plus 125.
In a mixed volley, Flashbang first leaves an orb for Grasp, and
Flashbang last is itself absorbed when both shield layers are weak.
A stronger near layer keeps all three impacts out of Grasp.
With both barriers at full health, a mixed three-projectile volley
spends 265 from the near barrier and leaves the far barrier full.
In the final volley of a full-health break, an early Flashbang spends
25 from the remaining barrier. A throw one tick after the orbs reaches
Grasp and raises its conversion from 360 to 375. Both release orders
preserve the full impact trace across a 90-tick final-flight update.
After both layers break and Grasp absorbs the last orb, a rear
Flashbang interrupts before conversion or spends the converted
shield pool just after expiry.
At cast plus 125, the Flashbang spends 25 bonus shields in either
update mode. Contiguous input converts after tick 124 impacts, and
catch-up converts before tick 125 impacts.
The same boundary is verified after both full-health layers break in
either order. A rear hit at cast plus 124 cancels conversion after five
absorptions. At cast plus 125 it leaves 335 of the 360 converted shields
with contiguous input or a 20-tick gap during Flashbang flight.

Winston's deployed sphere has actual attack, resource, surface crossing,
team, expiry and cooldown evidence. Its combat pose is reconstructed.
Native lifecycle, additional blast and shield combinations remain open.

Orb splash has actual approach-side and breaking-hit evidence, plus
body impacts that damage nearby Photon Barrier, Winston's sphere and
Reinhardt's mesh through splash. Overlapping surfaces and the remaining
native collision combinations remain open.

Reaper Wraith and Moira Fade have actual Symmetra weapon, target
pass-through, sentry, Flashbang and phase expiry evidence. Wraith cancel
and early recast are also verified. Both abilities now have actual
world-idle expiry, stale input and fresh recast evidence. Native
transport, broader phase and cleansing interactions remain open.

Mei Cryo-Freeze has actual solid-cover evidence for beam, orb, melee,
sentry, Flashbang and allied occlusion. Cancel and world-idle expiry
restore damage. Authored 200-point self-healing is verified against
actual beam damage, including idle expiry, cancel and overheal. Native
ice shape, heal cadence and further interactions remain open. A live
sentry slow clears on Cryo entry and is reapplied on reacquisition after
cancel. Other status cleansing remains open. Reverse Mei weapon cases
cover spray damage and filters, icicle pool routing, critical hits,
ammo, range, death and live Photon Barrier interception. Actual spray
now verifies player frost, slowed movement, idle expiry, freeze
interruption of the teleporter preview, thaw and barrier protection.
Single-shot multi-target spray now verifies distinct damage and frost
on two bodies, with static cover and particle barrier termination.
Two actual Mei spray sources now verify independent progress, expiry,
explicit stacking and a fixed freeze deadline. Blizzard now executes
actual throw, area damage and freeze against Symmetra, including LOS,
team, range, expiry and source separation from spray. Actual Matrix and
Genji Deflect cover airborne absorption and reflected ownership. Sigma
Grasp also stops the throw and preserves the conversion from an actual
Symmetra orb. Actual lethal orb damage cancels windup while released
flight and active area persist. Blizzard also verifies sentry destruction,
slow cleanup, teleporter shield damage and pair retirement. Actual
Photon Barrier cases verify carrier traversal and the current host's
landed-area behavior. Actual Flashbang interrupts windup without
killing Mei, and Photon Barrier can preserve the allied cast.
Actual portal transit verifies area exit, re-entry and thaw
admission. Reflected Blizzard now verifies freeze, route pause and
actual post-thaw attacks for all five Talon NPCs. Native landed-area
filtering and other NPC special-ability interruptions remain open.
Actual Mei spray freeze interrupts the Assassin's sustained pin.
Manual Heavy Assault charge now verifies 1408 windup and travel,
incoming beam damage, wall stop and windup freeze interruption.
Side graze damage, displacement and repeat-contact suppression now
execute. Close impact damage and control now execute. Capture and wall crush remain open.

Heavy Assault frontal capture, transport and release now execute.
Static wall crush now deals 300 and releases the victim.

Active Heavy Assault charge catches up within retained combat frames.
Charge gaps beyond the retention window remain open.

Actual Cryo cover now verifies Heavy Assault wall crush and release.
Mei Ice Wall and other dynamic wall types remain open.

Actual Mei Ice Wall deployment, direct beam and orb damage, allied
occlusion, cancellation and expiry now execute. Movement and other
ability interactions remain open.

Actual Mei Ice Wall stops Heavy Assault and triggers wall crush.
Cancel clears the collision and prevents the crush.

Actual Symmetra melee now damages enemy ice and respects allied ice
and nearer static cover.

Actual orb impact now verifies adjacent ice splash and rear protection
when the centre pillar breaks.

An Ice Wall deployment and cancel now exercise a sentry with an
existing lock and slow, including removal and reacquisition.

Player movement into and through an Ice Wall is covered across
cancel, expiry, and beam destruction of the centre pillar.

Real Trooper, Enforcer, Sniper, Assassin and Heavy Assault route
movement now covers Ice Wall obstruction and resume on cancel.

A wire replay of Ice Wall deploy and cancel now compares stored-state
checkpoints while Symmetra touches the ice and after movement resumes.

Each of the five NPC basic attacks now has a verified lethal path from
full Symmetra pools, with death attribution and automatic input release.

Actual Symmetra primary and reload input now cover full-pool lethal
sequences against all five NPCs, with attribution and corpse retention.
Quick melee has an exposed hit and facing, range, static cover and team
controls for every NPC. Only the exposed hit grants damage and charge.
Near the capsule boundary, melee follows current or retained position
according to view delay as each NPC enters or leaves reach. Tangent
capsules are included, and fractional views immediately before and after
a crossing are tested for all five NPCs. A live allied Ice Wall can block
melee after the viewed frame, and canceling it restores the attack against
an in-range current or retained pose. Against an enemy wall, two beams
wear down its full center pillar before melee breaks it. The breaking
swing cannot hit the NPC behind it, while the recovered swing can hit an
in-range pose through the opening.
Quick melee also trades with the first hit of each ranged NPC. An
Assassin pin blocks the input on contact, while a swing one tick earlier
lands. A new press after release hits normally.
Heavy Assault capture also blocks melee on contact. Manual stop and
Projected Barrier release the attachment. A new melee press can hit on
the same tick that the projected rescue releases it.
A queued melee is cleared before a lethal wall crush. Projected Barrier
one tick before crush saves Symmetra, while a same-tick cast arrives
after lethal damage. Only a fresh melee hits after rescue or respawn.
After a nonlethal crush, the buffer stays clear on the original body.
The crush depletes shields and restarts their recovery delay.
The surviving body can land a new melee on the crush tick after
damage resolves. Capture and lethal damage both block the press
at their respective command boundaries.

Actual full-charge orb sequences now cover full-pool lethal hits against
all five NPCs, including projectile travel, ammo and reload.
Each of those NPCs can also kill Symmetra while a full charge is held.
Death discards the pending orb and natural respawn permits a fresh hit
on the same NPC.
Actual death after launch also has one nonlethal hit and one lethal
trade against each NPC. The orb hits after the owner dies, keeps the
original kill attribution and grants no ultimate charge while dead.
An actual long flight also crosses the ten-second PvP respawn.
Pre-respawn attribution and zero charge are verified. The current
post-respawn path credits the replacement body and grants damage
charge. Its build parity remains open.
Actual Deflect, Defense Matrix and Kinetic Grasp also intercept or
return the long-flight orb after the launcher's natural respawn.
The defensive paths have direct pool, damage credit and resource
evidence.
Genji's reflected damage also reaches his authored charge graph. The
return earns 120 up to the cost cap, and an actual Dragonblade cast
suppresses the charge without changing the damage.
Actual ranged Talon returns also feed Genji charge from resolved
pool loss. Projected Barrier on the NPC transfers the reflected
damage to Zarya energy and awards no charge to Genji.
The breaking reflected hit also preserves the body for all four
ranged NPCs. Remaining Enforcer pellets on that tick and later
Trooper and Heavy Assault returns earn charge from actual pool loss.
Later Sniper and Enforcer attacks also verify recovery, the
eight-second Deflect cooldown, and the next allowed Deflect against
the exposed NPC.
Deflect now expires and runs its cooldown without owner commands.
Actual Symmetra orb contact on the last covered tick reflects, and
contact on the next tick reaches the body in both stepped and
retained updates.
An actual long jump with both contacts before the retained window
also preserves the Deflect decision.
The direct and reflected body impacts now retain their simulated
ticks even when the retained window starts later. A lethal direct
hit schedules respawn from the impact tick.
If the same update passes that deadline, it also creates the body
and starts passive charge at the scheduled tick. An actual second
orb verifies this while the historical projectile sweep remains live.
Separate catch-up updates now preserve that deadline and a failed
body ID allocation's scheduled retry. The new body earns passive
charge from the successful attempt.
Reflectable and nonreflectable projectiles now share the same early
sweep. Actual Accretion and an orb verify both contact orders and
the reflected return after Genji's death.
Natural Ice Wall expiry now precedes orb collision in the early
sweep. Actual contact immediately before expiry reaches the pillar.
Contact on or after expiry reaches Deflect.
Actual Helix and orb also verify pillar destruction before Deflect.
Both launch orders on a shared contact tick agree with stepped
updates.
Actual Mei commands between catch-up updates also verify cancellation
on both sides of contact and new wall placement against both flight
directions.
The production receive loop now catches up the world before routing
late input from open peers. Encrypted command replay covers Ice Wall
cancel around orb contact and confirmation after the orb hits Mei.
Three-peer replay also covers cancellation before or after the
omitted wall contact and confirmation after the omitted reflected
return. The reflected damage tick matches stepped simulation and
precedes the retained window. Actual Reinhardt turns while holding
Barrier Field also preserve earlier omitted orb contacts. Turning
the mesh in or out before contact changes whether the shield or
Deflect intercepts the orb. The same four cases now cover actual
lateral movement at a fixed shield angle. Stepped and skipped
execution agree on the final position, pool changes and Deflect
return tick. Independently moving targets and mixed cover remain open.
Two charged orbs now also verify lethal damage after Deflect expires
in the omitted interval and replacement at the historical respawn
deadline. Encrypted replay drops the observer's old-body destroy
datagram and starts its 30-second reservation from the retry ACK.
Encrypted replay now exhausts all 255 IDs through actual hero
switches. The historical lethal-orb respawn holds the corpse
until both peers release the first retired ID and reuses it at
the next scheduled retry. The allocator evaluates each retry
against its own historical monotonic time.
Two consecutive charged orbs from the same Symmetra also verify
fresh flight admission through encrypted input. The older orb
returns through omitted Deflect. A fresh release already decoded
steps once at the catch-up endpoint. A release received on the
skipped scheduler iteration or immediately after it first steps
on the following world tick. An admitted charged orb now keeps
its authored 25-second flight history beyond the defensive clock
budget. At 450 metres a lethal hit preserves its respawn deadline.
At 624 metres the orb hits and at 626 metres it expires. Stepped,
skipped and split updates agree after Deflect ends. Longer flights
from other projectile definitions remain open.
An encrypted scheduler replay also covers the 624- and 626-metre
paths across a 30.08-second stall. The new release begins on the
next world tick. A silent defender is dropped after the earlier
hit is resolved. A keepalive dispatched before catch-up preserves
the defender connection.
An omitted charged-orb sweep now advances actual Concussive Blast
movement before testing the orb against a manual Talon NPC. The
Trooper, Enforcer, Sniper and Assassin move clear when the blast
arrives first. Heavy Assault holds position. All five take the orb
when it arrives before the blast.
Five route-controlled Talon NPCs also use the pose at orb contact
during skipped updates. Across twenty cases, entering or leaving
before contact changes the hit result. Entering or leaving after
contact preserves it. Impact frame, damage, charge and final route
pose agree with stepped simulation.
An independent Soldier: 76 movement packet also verifies all four
player crossing orders at authoritative checkpoints. Late motion
changes the final pose and preserves historical body damage or miss.
The five Talon routes also resume at natural Ice Wall expiry
within a skipped update, including after the orb has landed. If
expiry opens the route first, the NPC crosses into a 120-damage
hit. If the orb passes first, the NPC resumes on expiry with full
health. Stepped and skipped updates agree in both orders.
Mei's actual spray freeze also overlaps a live sentry against all
five Talon NPCs. The same aura and 20 percent slow remain at thaw,
while the route resumes on the thaw tick. Damage, credit and pose
match stepped simulation for split gaps within 64 ticks. For one
locked sentry on the sole enemy NPC, damage and charge also match
across the 74-tick frozen gap and the 89-tick gap through thaw.
The 89-tick gap also preserves lethal damage and its attribution
on all five NPCs four ticks before thaw with 48 starting health
and three ticks after thaw with 52 starting health.
Longer gaps, other lethal boundaries and combined cover remain open.
With two sentries from separate allied owners locked on the same
sole NPC, the 89-tick gap preserves 56.96 damage and corresponding
charge per owner. Both slow auras remain, and all five NPC routes
resume at 60 percent of authored run speed on the thaw tick.
Static cover then independently stops one beam on the same
historical ticks in either schedule, leaving the other beam and
its slow intact. Lethal cases also agree on the owning body,
both damage and charge totals, and aura cleanup when the first
tower kills before thaw or the second kills after it.
When the locked sentry is attached to a single allied Ice Wall,
natural expiry retires the tower and clears its slow. All five
Talon NPCs move on the later of expiry and thaw. Skipped and
stepped updates match damage ticks, charge and final pose in
both orders.
Actual Mei cancellation at a world checkpoint keeps the beam
damage already resolved on that tick and immediately removes
the device and aura. All five NPC routes resume at the later
of thaw and the first tick after cancellation.
An enemy Soldier entering at the checkpoint can also spend a
rifle round to destroy just the weakened support. This preserves
the checkpoint's beam damage, removes the device and aura, and
lets each of the five NPCs use the opening after thaw.
The same ordering holds with a Soldier already on the field
outside the attached beam's reach. His first 19 rifle rounds
leave the support at 20 health. The checkpoint shot breaks it,
and both schedules preserve the prior beam damage and charge.
An already-flying Helix can also hit the supporting pillar
before thaw. Impact then precedes beam damage, and the last
beam hit falls one tick earlier. All five routes resume on
thaw with the same damage and charge under either schedule.
When the wall holds the route through impact, the same replay
also matches damage and charge for impacts on thaw or three
ticks later. All five NPCs stay in place through impact and
move on the next tick.
An allied Helix hitting hostile ice can also splash the
sentry's frozen target. All five NPCs match stepped pool loss
and credit. Survivors move on thaw, and lethal hits preserve
the corpse and terminal body script on the impact tick.
The same impact can splash an exposed stationary enemy player
and the NPC. Their actual pool losses and the NPC's survival or
death agree across schedules. With lethal player splash, death
and kill notices identify the same bodies, and the player gets
a new full-health body at the deadline ten seconds after impact.
The same impact also reaches a stationary second Talon NPC.
All five bodies rotate through both roles. Their pool changes
and terminal body scripts agree with stepped simulation.
For all five Talon definitions, a second NPC can enter or leave
Helix splash during the replay. The impact uses its route pose
on that tick, preserving pool loss or a miss in both schedules.
All five Talon definitions also intercept Helix while moving.
At the farther intercept, the ice stays intact and the sentry
keeps its lock and damage until natural expiry. At the nearer
intercept, the same blast breaks the weakened support and
removes the sentry and slow on impact. Both schedules agree
on death or resumed movement at thaw for the frozen target.
A lethal direct hit also leaves the intercepting NPC's corpse
at contact. The explosion excludes the corpse from its damage
and kill count. The farther blast preserves all pillars, and
the nearer blast breaks the support.
All five Talon NPCs also rotate through an earlier sentry lock change.
The moving NPC acquires the slow before impact, and the same ice
impact retires the sentry and its new aura in both schedules.
Both schedules agree when that blast kills either target or both.
They also agree when the sentry kills its new target first,
then reacquires the frozen NPC before Helix strikes.
When the new target instead crosses the rocket's path, the
direct hit precedes the blast that breaks the support. Both
schedules agree on the survival and death of either NPC.
At a farther interception the ice stays intact. The sentry
continues to fire at a survivor until ice expiry, or stops
dealing damage on impact if both NPCs die.
A third moving Talon can intercept Helix while the sentry
keeps its new lock on the second NPC until expiry.
If splash kills that target, the sentry selects the original
NPC if alive, then the interceptor if it is the last survivor.
Near the support, the third NPC takes the direct hit and the
blast removes the pillar, sentry and active slow on impact.
Pharah's Rocket Launcher primary also agrees across both
schedules on the wall hit and the separate body interception.
Both paths now cover lethal combinations across all five
Talon definitions, with one Pharah elimination per death.
Far interceptions also preserve the ice while the sentry
keeps its lock, selects a survivor, or stops firing.
An allied Genji can redirect Pharah's rocket into the ice
support after the sentry changes targets. Both schedules
agree on splash, deaths and Genji's credit.
The returned rocket can also hit a third Talon before its
blast breaks the pillar and removes the sentry.
Farther reflected contacts preserve the ice and cover
continued locks, retargeting and loss of all targets.
An active Projected Barrier also absorbs Helix wall-hit
splash for a Talon moving into range before impact. Both
schedules preserve its health at full capacity and at breakage.
A moving Talon can also intercept Helix while protected. The
direct hit offers 40 damage and the same rocket's splash offers
80. Both schedules preserve the recipient's body pools when the
direct hit breaks the barrier, when splash breaks it, and when
the barrier survives. Near contact breaks the support on impact.
Far contact leaves it intact and the sentry expires naturally.
The isolated replay also covers a lock change to a second
Talon while a projected third Talon intercepts. Both schedules
agree on the second Talon's beam and splash damage and the
third Talon's shield breakage or remaining capacity.
The newly locked Talon can itself carry Projected Barrier and
intercept. The beam spends barrier capacity without slowing it.
On a far break the sentry starts slowing it on the impact tick.
A near hit removes the sentry before it can apply a slow.
Both schedules agree on Zarya's energy credit after impact.
When Helix reaches the ice instead, the projected target
absorbs wall splash after the beam has spent part of its
barrier. The original frozen target can survive and thaw or
die on impact. Both schedules agree on either outcome.
Projected Barrier can also protect the original frozen target.
It removes the sentry slow while the freeze remains. The
original target survives otherwise lethal wall splash and
moves at thaw, whether the sentry keeps its lock or switches.
A separate Talon can intercept Helix near the ice. The
interceptor takes direct damage and either dies before splash
or survives to take splash as well. The original frozen
target still absorbs splash and moves at thaw.
Far interception leaves the ice intact. When direct damage
kills the interceptor, the sentry locks back onto the original
target on the impact tick. It spends remaining barrier
capacity or resumes the slow and body damage after a break.
A third Talon can move into the wall splash behind Projected
Barrier while the sentry locks onto the second Talon. Each
unprotected Talon can independently survive or die on impact.
The third keeps its body pools and damage time at both shield
capacities.
The third Talon also absorbs splash from a near body hit.
The unshielded interceptor takes the direct hit and may die
before splash is selected. The protected Talon still absorbs
its own splash whether or not the sentry switched locks or
the original frozen target survived the blast.
Far interception after a lock change leaves the support
intact. If both unprotected Talon die, the third takes the
beam on impact. It takes slow and body damage after a barrier
break or continues absorbing the beam until sentry expiry.
With a stable lock, far splash can reach a separate projected
Talon while missing the frozen beam target. If the frozen
target carries the barrier instead, the beam alone drains it.
Slow and body damage resume on the first beam tick after a break.
A projected player with a recorded pose can also absorb
Helix wall splash before the frozen target thaws. The player
keeps their health, native shields and last damage time as
the ice support and sentry disappear on impact.
A recorded player can also intercept the rocket near the
support. The projection protects the player across direct
and splash damage while the blast removes the ice and sentry.
Far interception by a projected player outside sentry range
preserves the ice and the original target's beam history.
An interceptor inside range can also coexist with the
original lock while the frozen target stays closer and
visible through thaw.
When the Talon moves farther away, the sentry can lock onto
the interceptor. A surviving projection absorbs the beam.
After a break the new target takes slow and body damage.
If the beam kills the frozen Talon after interception, the
retained corpse leaves the lock and the sentry selects the
player on its next tick.
When the fatal beam tick follows thaw, the Talon moves
before that tick's damage and the corpse stays at that pose.
If the Talon walks behind cover while still nearer than
the player, the first blocked beam ray transfers the lock.
A Talon can also intercept near the ice while a separate
player's projection absorbs the resulting splash.
At a far interception, an out-of-range player can absorb
splash while the support and original lock last until expiry.
An in-range player can also absorb that splash when neither
the player nor the interceptor can take the original lock.
If the interceptor dies on the direct hit and the beam later
kills the frozen Talon, the splash recipient can take the lock
on the tick after that second death.
When the interceptor survives, the sentry selects the nearer
visible capsule between the interceptor and the separate player
on that next tick. Both candidates stay in range in the checks.
The far splash checks also cover the original Talon thawing
before it dies. It moves on the fatal tick and its corpse
retains that pose. The next lock covers both distance orders
when the interceptor survives. The player takes the lock when
the interceptor dies.
The far splash case also covers fixed cover hiding the
original Talon as it moves after thaw. Even though it remains
alive and closer, the sentry switches to the nearest visible
candidate on the first blocked tick.
A far body interception can also splash and kill the frozen
Talon before thaw while the separate player absorbs splash.
The sentry clears the old slow and selects the closest
visible survivor on the impact tick.
Nonlethal splash leaves the original Talon's freeze, slow
and lock in place. A surviving original target can thaw and
keep the lock through ice expiry. A later fatal beam tick
credits Symmetra and transfers the lock on the next tick.
The far interception also covers nonlethal splash on the
thaw tick and the next tick. The moving original target
keeps the sentry lock through ice expiry.
If splash kills the original Talon at either boundary, its
corpse stays at the position it moved to before the blast.
The sentry transfers the lock on the impact tick.
A projected player can also take the direct hit on either
boundary tick. The blast still reaches the moving original
Talon, and a barrier broken by the direct hit protects the
player from splash from that same rocket.
Helix can also hit the ice support on either boundary tick.
The original Talon's moved pose determines splash damage
while the player's barrier absorbs its own exposure.
The support, sentry and old slow end on the impact tick.
A stationary Zarya can cast from inside sentry reach when
the original Talon stays closer through the pre-impact beam
ticks. The same blast and removal sequence applies.
Fixed cover can transfer the lock before a later wall hit.
The player absorbs the beam until impact. The original
Talon moves without the old slow and can still take splash.
When both targets remain visible, movement can make the
player nearer before the wall hit and transfer the lock.
The sentry also reacquires the original Talon when it clears
narrow cover and remains the nearer target.
Swift Strike cancellation also admits an in-flight orb that the
matched active Deflect returns.

Actual quick melee now covers full-pool lethal sequences against all
five NPCs, including recovery rejection, ammo and corpse retention.

Actual sentry-only fights now cover full-pool lethal sequences against
all five NPCs, with slow cleanup and kill attribution.

All five live NPC attacks now overlap against one Symmetra and verify
same-tick damage resolution and single death attribution.

Actual primary beam now trades damage within a single world tick of
the five-NPC encounter. Assassin pin interrupts held primary.

Projected Barrier absorbs concurrent NPC attacks while Symmetra
returns fire. Coverage includes pool depletion and energy credit.

Photon Barrier now covers simultaneous Talon fire from both sides,
including the owner returning fire while the wall takes damage.

Concurrent ranged NPC fire now covers Photon Barrier break and expiry
and subsequent admission of body damage.

Ten concurrent Heavy Assault weapons now break a full-health Photon
Barrier before expiry and then damage Symmetra.

Quick melee now verifies adjacent recovery frames and buffered input
on the 16 ms host command grid.

Quick melee now verifies the first accepted buffered tap and the aim
used when the queued swing executes.

Actual Flashbang control now verifies suppression of a buffered quick
melee strike and recovery on fresh input.

Actual enemy revolver damage now verifies buffered melee suppression
through death and new input after respawn.

Buffered quick melee now verifies both sides of the command gap
reconstruction limit.

Flashbang suppresses buffered melee across a recoverable 64-frame command
gap, even when input resumes after stun expiry.

An actual Assassin pin suppresses buffered melee across a recoverable
64-frame gap even when released before input resumes.

Projected Barrier releases an Assassin pin during a recoverable command
gap. No old buffered hit returns and a fresh melee press hits.

Projected Barrier rescue before the buffered melee deadline also leaves
the interrupted swing suppressed across a recoverable command gap.

Projected Barrier deployed before Assassin contact prevents the pin
and preserves buffered melee across the recoverable command gap.
Actual orb release now has a separate Assassin timing check. The pin
cancels a held orb and leaves a launched orb in flight. Barrier
prevention preserves the held orb; rescue only admits fresh input.

Own and allied Teleporter traversal now preserves live orb flight
and damage attribution. The deployer can charge after the cast.

Held primary retains 120 DPS and switches to the exit target on the
actual Teleporter transit frame. The entrance target stops taking damage.

Flashbang blocks Teleporter during stun. A released tap does not queue.
Continuously held interaction transits on the first unstunned frame.

Exit-side static cover blocks a held primary beam immediately after Teleporter transit.

A deployed Ice Wall takes held primary on the Teleporter transit frame. Cancel opens the beam path.

A full-health central Ice Wall pillar at the Teleporter exit absorbs the breaking beam tick.

Cryo-Freeze blocks held primary at the Teleporter exit and protects a rear enemy.

The Winston Barrier Projector takes held primary on transit, returns ammo, and absorbs the breaking tick.

Personal Barrier at the Teleporter exit absorbs held primary and grants energy.

Projected Barrier at the Teleporter exit takes held primary and credits its caster.

A full charged orb releases from the Teleporter exit on or after the transit command.

Partial orb charge preserves damage and ammo cost across Teleporter transit.

Flashbang interrupts a charging Teleporter user and leaves no queued orb.

Projected Barrier preserves orb charge against Flashbang before Teleporter use.

Projected Barrier targets the ally overlapping the Teleporter entrance.

An allied sentry can receive Projected Barrier on the static host.

All five Talon NPCs can receive Projected Barrier on the static host. The shield absorbs a full orb and the breaking primary beam tick.

Hostile Helix Rockets splash exposed targets on the impact side of Photon Barrier, including on the breaking hit. Allied rockets and their splash pass through the wall.

Personal and Projected Barrier protect their recipient through both parts of the Helix explosion that depletes the barrier.

Overlapping personal and projected particle barriers resolve each hit against one layer. Both cast orders and separate energy credit are covered in the static host.

Two simultaneous projectors keep separate barrier and energy pools. The recipient stays protected across the first barrier's break, expiry, caster switch or caster death.

A sentry launch crosses active Deflect, Defense Matrix and Kinetic Grasp in the static host, spends one charge and attaches with its original owner.

Transcendence expires on world ticks without owner commands. Sentries retain their slow aura during damage immunity and resume damage at expiry.

Sentry damage across idle Transcendence expiry matches contiguous updates for tested jumps of 16, 64 and 80 world frames.

Sentry damage and slow reacquisition across idle Wraith, Fade and Cryo-Freeze expiry match the contiguous combat result.

An expiring Cryo-Freeze capsule protects a rear enemy from sentry catch-up until the ray clears. A capsule outside that ray leaves the normal damage window intact.

Sentries respect the natural expiry tick of a hostile Photon Barrier across retained world gaps. Allied Photon Barrier does not restrict catch-up.

Sentry catch-up across an actual orb break of a hostile Photon Barrier starts on the breaking tick and matches contiguous damage.

When an in-flight orb breaks Photon Barrier before the end of a retained gap, the break tick and subsequent sentry damage match contiguous updates.

Sentry rays crossing Winston's barrier sphere respect natural expiry and orb break during catch-up. Rays entirely inside keep the normal damage window.

Winston's hero switch or leave removes sphere cover immediately. A lethal primary hit leaves the deployed sphere active until expiry in the tested case.

Same-hero body replacement and a three-second Practice Range respawn retire the old Winston sphere. The new body can deploy a fresh sphere.

When the scheduled respawn falls inside a retained world gap, the old sphere protects through the actual respawn tick. A blocked replacement keeps the sphere until the retry succeeds or the sphere expires.

Static sentry flight crosses hostile Photon Barrier, Winston's sphere and Reinhardt's raised mesh. Each barrier can block the beam from the deployed sentry.

An actual sentry launch attaches to the nearest allied or hostile Ice Wall pillar. Removing that pillar retires its sentry and clears the slow.

An airborne sentry attaches or flies through at the same Ice Wall expiry boundary in contiguous and retained world updates.

An ice-supported sentry can boot and deal damage before its pillar expires in the same retained update. The hit ticks and slow cleanup match contiguous updates.

A charged orb can break the support in the middle of a retained update. A newly booted or already active sentry deals damage through the preceding tick and loses its slow on the break.

Helix direct impact and splash from an adjacent pillar produce the same sentry damage, slow cleanup and break tick in contiguous and retained updates.

A single Helix can break two supporting pillars on the same tick. The third sentry retains its aura and damage when its pillar survives.
