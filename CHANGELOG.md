# Changelog

This log records tested changes and their acceptance status. Entries are grouped by date until a version is explicitly released.

## Unreleased

### 2026-11-09

#### Zarya beam/DOT armour policy restoration (partial static acceptance)

- Follow Blizzard's2019-03-19 and2020-08-31 live patch notes: primary beam uses
 30% armour reduction; Graviton field DOT bypasses armour. Keep ordinary hit
 reduction and expose the scalar only during armour-pool consumption, after
 raw barrier interception. Native exhaustion splitting/impact classification stay open.
- Reproduce primary60 raw damage incorrectly losing only30 against Reinhardt's
 authored200 armour. Actual energy0/50/100 beams now remove42/68.6/95.2, with
 matching stats, server HUD and encoded pools. Field DOT removes20 over4s.
 Projection on armoured Reinhardt still consumes raw60 for12 energy.
- Pass282 expanded combat/driver/five-NPC tests in126.159s,6 health-pool encoding
 tests in1.314s and the latest11 armour cases in5.263s; zero skips, Ruff/diff pass.
 Four armour cases explicitly verify declared host pool-boundary policies rather
 than claiming native boundary equivalence. No new mutable storage is introduced.
- Native ultimate charge evidence was not established by fresh source searches;
 the60-second placeholder is unchanged and remains OPEN. Conditional NPC armour,
 complete defense/status interactions and original-client acceptance remain open.
 The complete static goal stays ACTIVE; no parent merge or publication.

#### Zarya Flashbang interception and control acceptance (partial)

- Review/import parent manual NPC control and resource-backed Flashbang commits
  with provenance. Preserve this lane's combat services, projectile attribution,
  static splash placement and all causal snapshot fields during integration.
- Reproduce actual personal/projected barrier damage absorption still allowing
  Flashbang stun. Deliver hit effects within accepted body damage after barrier
  interception so the blocked complete hit cannot stun its protected body.
  The breaking Flashbang is also intercepted; only remaining capacity gains energy.
- Add7 Zarya control methods for both barriers, beam/movement interruption and
  recovery, blocked skill/ultimate input, breaking-hit handling and emitted
  secondary/deployed Graviton persistence. A separate actual-five-NPC method
  checks projected Flashbang protection and caster energy. Update explicit NPC
  fixture targets for the imported manual input service.
- Pass269 expanded combat/driver/NPC tests in157.041s,14 strict server snapshot/
  replay tests in497.421s and the latest8 control/five-NPC tests in7.394s;
  zero skips, Ruff/diff pass. Both initial barrier-control reproductions failed
  before the production change. No native status presentation was exercised.
- This is reconstructed complete-hit interception, not general native immunity,
  cleanse, reflection/absorption or all status hosting. Those cases, ultimate
  generation, remaining NPC attacks/specials and full client acceptance stay open.
  The complete static goal remains ACTIVE; no parent merge or publication.

#### Zarya actual weapon/barrier and energy lifecycle acceptance (partial)

- Execute primary, secondary, melee and initial Graviton damage from an enemy
  Zarya against both personal and projected protection. Verify capacity use,
  absorbed-energy values, frozen source identity, high-energy beam exhaustion,
  post-break body damage and cooldown. No native geometry/control claim is added.
- Verify authored energy delay1s/rate1.8 per second, continued decay during later
  projectile flight, re-absorption delay reset, zero clamping and new-body reset.
  Actual Trooper/Sniper/Heavy weapon paths now exercise projected protection of an
  allied Soldier with energy/source credit retained by the correct actors.
- Pass187 affected tests in138.378s plus one additional three-NPC method in4.329s;
  external NPC data enabled, zero skips, Ruff/diff pass. An initial expanded run
  exposed a fixture accessing an already-retired exhausted pool; correct it to
  require pool retirement and rerun all187 successfully. No failed run is relabeled.
- These11 new methods retain explicit input/timing and observer-transport limits.
  Native pet/status/cleanse/immunity, charge generation, remaining NPC attacks and
  all specials/autonomous AI remain open. Shared Flashbang control is awaiting
  review/import at the next fixed-source boundary. Full goal stays ACTIVE.

#### Zarya secondary splash static-wall occlusion (partial static acceptance)

- Reproduce actual secondary fire dealing47 through a static wall. Require static
  visibility from explosion to target capsule for shared splash; move static impact
  points .01m to the incoming side so near-side victims remain eligible.
  These are reconstructed geometry policies, not native explosion solver equivalence.
- Add10 actual secondary command cases for energy0/50/100 damage47/71/about95,
  emitted damage freezing, release, aimed miss, body/wall-impact occlusion, reduced
  nearby splash, radius and ally exclusion. A separate method shields all five
  actual NPC bodies with a static wall. Contact damage is splash; direct amount is0.
- Pass180 Zarya/combat/projectile/five-NPC/McCree tests in119.125s with zero skips;
  Ruff/diff pass. The reproduction failed before the production change.
  Self-damage, native armor/defense/control rules, ultimate generation, remaining
  NPC attacks/specials and original-client acceptance remain open. Goal stays ACTIVE.

#### Zarya shared-base integration and NPC/melee follow-up (partial static acceptance)

- Review and import parent NPC weapon mounting, native array selection and static
  melee visibility fixes with provenance. Retain this lane's independent source.
- Add actual Zarya-command checks for a player and five actual NPCs behind a wall;
  preserve unobstructed30-damage cases. Sniper's mounted weapon now removes100
  from Zarya shields; a deliberately timed personal barrier blocks it and gains20
  energy. The damage fixture omits NPC observer projectile transport.
- Pass197 affected combat/Zarya/projectile/NPC/array/melee tests in121.294s and32
  body/driver tests in4.841s; zero skips, Ruff/diff pass. An independent offline
  query probe passes192 native routing cases using declared storage adapters;
  the unnamed component's health meaning remains unresolved and is not implemented.
- Enforcer/Assassin, all five specials/autonomous AI, full cross-defense/control,
  ultimate generation and original-client acceptance remain open. Goal stays ACTIVE.

#### Zarya projectile ownership and lifecycle (partial static acceptance)

- Freeze projectile team and source-body credit at emission for direct and splash
  damage, including Zarya secondary fire and Graviton during team/body changes.
- Cancel in-flight projectiles when their owner leaves or the match ends; reject
  orphan Graviton deployment and release existing pull immediately on either event.
  Owner death alone retains the already-fired projectile and field behavior.
- Pass162 affected Zarya/combat/projectile/five-NPC tests in68.487s, including27
  ultimate cases and seven added ownership/cancellation cases. Ruff/diff checks pass.
  Full static acceptance, original-client acceptance and serial integration remain open.

#### Native state-input joins and Deadeye timing

- Implement1113EBD5 distinct-input joins from the104319 input handler rather
  than permissively following output. Track producer/plug arrivals, ignore
  duplicates, request sibling completion checks, clear registrations before
  output and abort active referenced states only after accepted output. Resolve
  states after callbacks; missing states are not allocated as a side effect.
- Include pending arrivals and current traversal context in VM/server causal
  snapshots, restore nested traversal context and clear arrivals on teardown.
  Update the snapshot storage contract test for these new fields.
- Pass945 relevant tests (932 script/VM/NPC/projectile/McCree plus13 server
  snapshot), including10 new join and actual Deadeye command tests. Forty
  offline native-handler arrivals match Python return values, registry order,
  completion checks and aborts; engine lookup/output/event services are adapters.
  Ruff and diff checks pass. No original-client acceptance was run.
- Deadeye now survives warmup, retains the resource's six-second window and
  supports repeated secondary cancel without normal ammunition loss. Test charge
  is explicitly filled. Target acquisition/per-target charging/damage and the
  full ultimate remain open; no replacement damage formula is installed.
- Manifest-verified research resolves the per-target effect053/0056 to01EE in
  an external bundle. Normal runtime data/services and original working changes
  remain unchanged; no complete hero lane merged. Full PvE AI/navigation/specials,
  McCree/cross-hero and original-client combat acceptance remain open.

#### Resource-backed Flashbang control reconstruction

- Preserve authored hit-effect IDs and route Flashbang's053/0079 effect to an
  explicit server control policy. Resource-derived mode flag13408 chooses0.8s
  or1.7s; exclusive deadlines use world ticks. Release held/buffered input, stop
  running abilities through their authored exit paths, suppress player movement
  and attacks, pause NPC routes without catch-up and consume suppressed shots.
- Retire control on expiry, death, body replacement and manual NPC removal.
  Retained corpses remain present. Include deadlines in server snapshots and
  verify that they change the causal digest. Splash now checks static impact-
  to-capsule visibility before applying damage and control through cover.
- Pass138 relevant tests:125 combat/NPC/projectile/route/McCree and13 server
  snapshot checks, including8 new actual-input/control/lifetime methods. Ruff
  and diff checks pass. Fix a test fixture's shared-spawner mutation discovered
  by the combined regression; subsequent fresh-process regressions pass.
- Actual-five-Talon offline probes show25 Flashbang damage and input release
  for all five; Trooper/Sniper/Heavy resume damage after expiry. Console stop
  during control prevents automatic recovery attacks. Enforcer/Assassin incoming
  attacks remain unresolved. Fixtures use adapter transport/permissive VM.
- This policy does not execute the complete native224E status manager. Native
  immunity/cleanse/stacking/barrier interactions, dynamic mode status, status
  replication/HUD/animation/audio and original-client acceptance remain open.
  Deadeye/full hero combat and NPC AI/navigation/specials remain open; no hero
  lane merged. Normal runtime/services and original working changes preserved.

#### McCree melee static-wall occlusion

- Prevent quick melee from damaging targets through static walls: retain the
  authored melee sphere and viewed capsule, then check eye-to-capsule-surface
  visibility. Geometry is an explicit server reconstruction, not native hitbox
  equivalence. Shared quick melee uses the same check for players and NPCs.
- Pass83 McCree/combat/NPC/projectile tests, including3 new real-input methods
  covering visible and wall-blocked player/NPC hits, allies and range refusal.
  An external actual-five-Talon probe passes all10 visible/wall cases. Ruff and
  diff checks pass. Native-client acceptance was not run.
- Record the actual McCree input frontier and recovered Flashbang status resource
  route. The current VM does not execute its complete stun path; Deadeye still
  lacks target/charged damage acceptance. Roll motion, complete skills/cooldowns,
  five-NPC AI/navigation/specials and original-client combat remain open. Normal
  inputs/services and original working changes are preserved; no hero lane merged.

### 2026-11-08

#### Native array selection for PvE difficulty configs

- Implement STU_4BD39CFD with scalar index evaluation and collecting container
  evaluation, array-only bounds checks and successful-null fallback. Preserve
  nested value types and watched index/container updates; unknown child configs
  still fail strict execution. No mission difficulty is invented.
- Execute the original104319 selector/conversion/lookup offline:15 cases match
  the Python runtime. Config providers and value storage/copy are adapters.
  Pass178 relevant tests, including5 new array selection/watcher/failure checks;
  Ruff and diff checks pass. Native-client acceptance was not run.
- Resolve a zero-damage cause in Enforcer's authored weapon. An external
  forced-controller experiment now emits resource-defined damage; its controller
  assumption is unverified and is not production code. The ordinary five-body
  probe still only proves Trooper/Sniper/Heavy damage; Enforcer/Assassin control,
  navigation/specials and full McCree/client combat remain open. Normal inputs,
  services and original working changes remain untouched; no hero lane merged.

#### Authored NPC weapon component mounting

- Share body weapon-root allocation between player and NPC scripts: preserve
  sparse slots, seed slot bindings and allocate manager/weapons before entries.
  This restores the Sniper body's omitted manager and two weapon graphs without
  replacing authored attack damage or timing.
- Pass107 relevant tests (23 NPC script,15 NPC control/lifecycle,27 driver,
  5 body-script and37 projectile), Ruff and diff checks. The new test exercises
  sparse-slot firing and decodes the NPC observer baseline with mounted roots.
- Actual five-body offline data now yields Sniper damage100 against McCree225;
  Trooper20 and Heavy Assault5 still work. All three stop damage for80 further
  frames after stop. Enforcer and Assassin remain unable to damage in200 frames.
  The adapter omits NPC observer projectile transport; native visuals, autonomous
  AI/navigation/specials and complete McCree acceptance remain open. No client
  launched, normal data/deployment unchanged, no completed hero lane merged.


#### Zarya authoritative shield recovery (partial static acceptance)

- Bridge0033's shield pool,3-second delay and30/s healing expression to actual
  Zarya vitals. Read its live default modifier Stacks rather than inventing values.
  Reset delay only after body pool loss, retain recovery behind an absorbing
  barrier, and heal only the shield pool up to its maximum.
- Integrate authoritative world ticks once, handle skipped updates, discard old
  body/death clocks, update HUD health and register all recovery state in strict
  snapshots. Explicit VM modifier fixtures cover changing and ending modifiers;
  no native hero-buff or original-client proof is claimed from those fixtures.
- Pass169 affected combat/ultimate/projectile/NPC/snapshot tests in412.168s,
  including17 recovery behavior cases. Ruff/diff checks pass.
  The complete static goal remains ACTIVE:
  ultimate generation, aura/field/pet hosting, defenses/control/immunity and
  three incoming NPC attack chains/specials remain open.

#### Zarya Graviton damage and pull service (partial static acceptance)

- Follow the target-client00A3 aura reference to the missing0261 effect graph.
  Connect actual ultimate cast/projectile impact to a4-second,6-metre field with
  direct5 plus continuous5/s damage and collision-backed player/NPC pull. Interpret
  scalar3 as host pull speed and explicitly document remaining native force gaps.
- Apply the authored Zarya motion scalar as reconstructed vertical acceleration;
  schedule non-Biotic deployable releases at impact. Preserve original field-source
  identity after body replacement, expire/cancel fields, prevent duplicate damage
  and retain bounded scheduler catch-up. Register all new causal combat storage
  and the previously added command aim in strict server snapshots.
- Pass68 projectile/ultimate/five-NPC tests, including20 ultimate behavior cases.
  An expanded255-test run passed254 and exposed an obsolete projectile-retention
  assertion; update it to observe actual delayed casts and impact retirement.
  The affected68-test rerun passes, as do Ruff/diff checks. Server snapshot,
  journal/replay and repeated-life tests passed in the expanded run.
  Native passive/damage charge, replicated aura/field actors, immunity/escape,
  cross-defenses, three NPC attack chains and all NPC specials remain open.
  The complete static goal stays ACTIVE; no original-client acceptance or merge.

#### Zarya actual five-NPC combat fixtures (partial static acceptance)

- Review and import the parent manual-NPC lifecycle/catalog commits with original
  authorship. Add an opt-in external-data suite that loads all five actual body
  scripts, exercises primary60/secondary47/melee30, ally exclusion, projected
  protection and shortened lethal/retained-corpse behavior. Keep normal runtime
  and external data immutable; do not substitute renamed training actors.
- Exercise actual Trooper/Heavy weapon graphs with explicit logical input: their
  volleys damage Zarya and are intercepted by the personal barrier. This is not
  autonomous AI acceptance. Enforcer/Sniper/Assassin attacks, all five specials,
  conditional/difficulty pools, full ultimate and cross-defense matrix stay open.
- Pass18 NPC/body/lifecycle/catalog tests, including nine new methods with39
  executed sub-scenarios; the expanded combined suite passes197 tests in76.436s.
  Ruff/diff pass. The complete static goal stays ACTIVE;
  native-client acceptance and completion-triggered shared-main merge remain open.

#### Zarya projected barrier target and damage services (partial static acceptance)

- Supply the two authored025C target searches through an optional host callback.
  Write their actual target-list output, run unchanged ally selection/pet/duration
  plugs, intercept damage with the caster's separate pool, and award the caster's
  energy through the authored message. Keep command aim and selected targets in
  strict VM snapshots. Other target searches retain undeclared-service behavior.
- Explicitly reconstruct cone-modifier fallback, target ranking, LOS and lifetime
  policies. Native filter/tag semantics, pet geometry, cleanse/control immunity,
  cross-defense interactions, ultimate combat and five-NPC acceptance remain open.
- Pass179 combat/runtime/codec/snapshot tests, including42 targeted Zarya cases
  and16 new projected cases. Ruff and diff checks pass. The independent
  complete-static goal remains ACTIVE; no original-client or shared-main merge
  is claimed. Broader regression is tracked in the lane verification record.

#### Zarya personal barrier damage and energy services (partial static acceptance)

- Connect the live authored personal-pet State to a per-activation barrier pool
  evaluated from the target resource. Block body damage, retain bounded absorption
  for energy, and schedule the existing duration/StopAbility/cooldown branch on
  exhaustion. Deliver absorbed HP and attacker identity to the authored energy
  message so its existing cap and decay States execute.
- Mark whole-body interception, fully blocked breaking events and next-step
  exhaustion as reconstructed server policies. Native barrier actors/geometry,
  projected barrier, control immunity/cleanse and cross-hero defenses remain open.
- Pass91 Zarya/combat/projectile tests, including actual damage interception,
  cooldown rejection/reuse, held-input rejection, energy cap/decay, primary ammo
  depletion/reload/resume and secondary25 ammo cost. Ruff and diff checks pass.
  The complete static acceptance goal remains ACTIVE; no client acceptance,
  shared-main merge or GitHub synchronization is claimed.

#### Zarya primary beam damage bridge (independent lane)

- Consume active0241/node28 continuous damage using its authored damage expression
  and TrackRay range/radius, with enemy selection, world obstruction and ordinary
  health/death handling. Consume each command interval once and retire body clocks.
  Eye-origin/capsule geometry and integration timing are reconstructed assumptions.
- Pass78 command-driven Zarya, combat and projectile tests, including actual health
  changes, energy expression, allies, walls, duplicate commands, death, incoming
  shield damage, melee, secondary fire and training-bot retained corpse. Ruff and
  diff checks pass. Full hero combat remains OPEN: barriers, energy charge/decay,
  ultimate field/pull, five requested NPC types and cross-hero defenses are unaccepted.
- No original-client session, root merge or publication is claimed. An existing
  client was preserved; the workspace publication helper is absent. Deliver this
  lane as a reviewed patch. See docs/ZARYA_COMBAT.txt for the required matrix.

#### Manual NPC console commands and engagement input

- Implement console command semantics for menu/selection, explicit removal,
  crosshair-selected engagement and stop-all. Spawn requires visible floor;
  selection/aim uses reconstructed sandbox distance and static-visibility policy.
  No native console hook, keyboard frontend or production mounting is installed.
- Drive actual slot0 input, target-player yaw/pitch and input release on target
  loss/occlusion. Stop cancels pending/consumed input and owned projectiles;
  idle manual VMs advance while damaging reports are suppressed. Track queued
  input separately to cancel a press before its first VM update. Preserve new
  target/input/pitch state in snapshots and movement output.
- Pass136 related and13 server-snapshot tests (149 total), including9 new control
  tests, Ruff and diff checks. Actual body data emits primary damage for Trooper
  and Heavy Assault with no later damage in the80-frame stop probe. Enforcer,
  Sniper and Assassin emit no shots and retain unresolved query/behavior gates.
- A separate original104319 console-option probe fails relay loading with Windows
  access denied and times out login. No console window or grave-key overlay is
  observed on that screen; post-login console availability remains unknown.
  Full navigation/specials/McCree skills/ultimate/client combat remain open.
  No normal deployment; mandatory publication helper remains missing.

#### Prepared PvE enemy base-pool catalog

- Add an external XML catalog extractor that resolves actual NPC HealthPool
  type and directly wired schema setters, defaults and body overrides. Preserve
  source fingerprints and list unapplied difficulty/conditional pool nodes.
  The NPC body's variable221 routes to type2 shields; legacy armor labels are
  not used. Validate selection keys, full GUIDs and finite explicit pool values.
- Pass three extractor and six NPC runtime/catalog tests (nine total), Ruff and
  diff checks. Prepare all five local enemy definitions outside Git. Extend the
  validated external script bundle by33 graphs with no new missing references.
- In a fresh in-memory Match, all five actual body scripts load, McCree primary
  input deals70 damage to each, body creates encode and removals complete. Flat
  collision, adapter transport and permissive VMs remain explicit probe inputs;
  this is not enemy AI, complete skills/ultimate or original-client acceptance.
- Console keys/menu, production mounting, navigation/specials and full combat
  remain open. Normal runtime data is unchanged. See PVE_COMBAT_SANDBOX.txt.
  The required publication helper remains absent; no manual push is performed.

#### Manual NPC combat body lifecycle

- Add validated explicit NPC definitions and match-thread spawn/removal APIs.
  Fresh entities join combat with supplied health/armour/shield pools, retained
  corpses, creation boundaries and queued body creates for spawned viewers.
- Explicit removal selects only manual NPCs, ends their scripts/projectiles
  without explosions, clears attacks/respawns/combat state and queues destroys.
  NPC projectile retirement no longer assumes its source has a player client.
- Pass five real-Match lifecycle tests, 37 projectile tests and 28 combat tests
  (70 total), Ruff and diff checks. No fresh original-client run or deployment.
- The five Talon definitions, pool routing, navigation/AI/specials, console keys,
  McCree abilities/ultimate and complete sandbox acceptance remain open. Parallel
  hero goals require complete executed static acceptance before serial integration.
  See docs/PVE_COMBAT_SANDBOX.txt. The required publication helper is still absent.

#### Optional persistent NPC corpses

- Add an opt-in Bot.retain_corpse policy for the requested Rialto combat sandbox.
  Death still sends normal notices and advances the bounded terminal script;
  retained bodies receive no respawn deadline and reject stale replacement calls.
  Keep the policy in passive server state. Existing training bots still respawn.
- Pass 28 combat tests (including retained lifetime, dead-target exclusion and
  terminal callbacks), 22 NPC script/transport tests and 13 server snapshot tests.
  Ruff and diff checks pass. No fresh client or normal deployment is claimed.
- Manual five-enemy spawning, console keys, navigation, specials, McCree combat
  and visual corpse persistence remain open. See docs/PVE_COMBAT_SANDBOX.txt.
  The complete combat sandbox remains unaccepted. Publication uses the required
  helper; its existing absence still prevents synchronization.

#### Native persistent receiving message State

- Mount strict9D6CF8AC through explicit message contexts. Preserve dual receiver
  channels, the native nonzero-key/flags clearing rule, selection generations,
  filter scopes, three parameter modes and separate message/filter/cache
  dependency epochs. Restore deferred begin, forced/non-forced refresh, tick
  routing, context outputs and allowed idle/receive/retrigger subgraph changes.
- Retain per-row output links and duplicate targets, in-place refresh and old
  destinations for baking. Restore live best-source getters without watchers,
  cached array getters and failed-output fallback distinct from successful null.
  Bound variable recursion exposes the lower value and releases guards on faults.
  Context/physical retirement drops rows quietly and refuses replacement-context
  borrowing. Native keyed type12/indexed paths and linked array checkpoints remain strict gaps.
- Encode sender identity/kind and stacked payloads. Preserve dynamic/frame-cache
  binding policy in runtime links, driver snapshots, wire output and diagnostics;
  the previous always-false binding policy could not represent receiver links.
  Extend passive VM storage without invoking getters or claiming shared restore.
- Pass3268 original production-control comparisons (384 invalid selected scene
  owners remain outside SceneWorld),1728 getters,1288 binding operations,360
  variable resolver cases,4864 writer/4864 reader cases and256 binding-policy
  reader suffix cases.16628 total comparisons use declared service/storage
  adapters; actual authored subgraphs and the complete binding reader are not
  run by these probes. No game code is patched. Add25 behavior tests; final1086
  related tests pass in26.003s and33 server tests in249.063s (1119 total), with25
  matching frozen source hashes and Ruff/diff checks.
- All535 extracted receiving nodes complete1605 isolated empty-source/pre-init
  registration/cleanup fixtures under three declared policies. No ordinary
  initialization or authored plugs execute. Full-controller fixtures now need
  explicit message contexts: without one they retain0F44/node7; with declared
  role/frame/generation plus earlier hero/team inputs, all three profiles retain
  0C90/node41's unknown mode component. Failed-helper rejection/cleanup passes.
- Fresh ordinary/placed/real-Match probes retain81A9AB10,50/123 completed first
  frames and rootD38/node106 before controller execution. Native engine roles,
  generation/filter policy, ordinary mode mounting, actor lifetimes/transport,
  original-client receiver acceptance, full story/cinematics/five enemy AI and
  extraction/results remain open. No fresh client run or normal deployment is
  claimed. See docs/MESSAGE_RECEIVER.txt. Publication helper remains missing;
  no GitHub push is performed.

#### Persistent message parameter providers and receiver caches

- Share native parameter providers across one component's instances. Preserve
  reverse ordering, null barriers, GUID0 lookup and destinations written by
  failed providers. Native Entry selection and legacy delivery use scoped VM
  providers; legacy per-instance providers remain the outer compatibility layer.
- Add a sender-backed scope with builtin owner/source/subject identities, first
  full-key matching, missing-row success/int0 and missing-config fallthrough.
  Evaluate in the source instance with the receiver watcher, live-context guards
  and a shared cross-component budget. Keep scalar success separate from value.
- Add the three native receiver cache modes, retaining ordered key/value rows,
  duplicate senders/keys, stacked unmatched caches, iterator versus whole-value
  array handling and different null/failure rules. Map/type13 stays scalar.
  Native keyed type12 storage, live output bindings and lifecycle are still open.
- Pass936 original scope/provider-chain and624 cache comparisons with declared
  engine/storage adapters and unchanged game code. Re-pass1350 Entry binding,
  24 Entry scope (including zero-ID),3024 selection,60 subject,2688 lifetime,
  4626 sender,3456 outgoing-action and1296 hero-iterator comparisons. Add26
  behavior tests. Final1061 related tests pass in25.205s and33 server tests in
  243.748s (1094 total), with20 matching frozen source hashes and Ruff/diff.
- Receiving-State channel/epoch integration, live getters, output application,
  context variables, idle/receive subgraphs and codec remain unimplemented.
  Fresh ordinary/placed/real-Match and explicit controller/Entry probes retain
  their previous frontiers:81A9AB10,50/123 first frames and rootD38/node106 before
  controller execution. Full story/cinematics/enemy-AI/extraction acceptance,
  original-client verification and normal deployment remain open. See
  docs/MESSAGE_PARAMETERS.txt. Publication helper is still missing; no push.

#### Native persistent sending message State

- Connect strict38EE1100 to the persistent manager through explicit per-component
  Contexts and a shared Host. Restore scalar message/priority-resource guards,
  native target policies, channel flags, frame/sequence reuse, watched targets,
  refresh/cleanup and once-only VM timer notification. Parameters remain lazy
  for receiving States. Native frame/role/priority/notification services stay
  explicit; unknown services remain strict. See MESSAGE_SENDER.txt.
- Preserve each component's budget and binding lifetime. Discard detached/failed
  State rows without callbacks or touching other owners; invalidate callbacks
  interrupted by terminal discard. Retain source caches in passive State layouts;
  complete shared-manager export/restore remains unsupported.
- Fix the owner-frame sender reply encoder: preserve low16 sequence bits, emit
  presence plus variable-width unsigned16 for nonzero values. Match768 native
  writer and768 reader cases across width and word boundaries. Also pass4626
  native registration,396 initialization-flag and16 timer cases with declared
  adapters and unchanged game code. The manager's39394 original comparisons
  pass again after terminal discard support.
- Resolve1B72510 to EntityInstance's known null virtual callback; do not repeat
  transition actions after registration. Add22 sender tests and two manager
  cleanup tests. Final1035 related and33 server tests pass (1068 total), with
  matching frozen source/native hashes and Ruff/diff checks. Superseded runs from
  before the null-callback correction remain archived as intermediate evidence.
- Isolated actual-data fixtures cover47 senders in five mode/controller/placed
  graphs under three declared role profiles.44 complete all profiles; three keep
  strict unknown queries. No ordinary initialization or authored plugs run.
  Fresh controller/placed/Match probes retain81A9AB10,50/123 first frames and
  rootD38/node106 respectively; the receiving State and full mission remain open.
- No fresh original-client run, deployment, complete story/cinematic/enemy-AI or
  extraction/results acceptance is claimed. Publication helper remains missing;
  no GitHub push is performed. Normal inputs and unrelated launcher work remain.

#### Native persistent message-State manager

- Restore the standalone persistent sender/receiver manager: exact native channel
  keys, local/global lookup, float32 priority and flag/frame/sequence arbitration.
  Preserve duplicate receivers, metadata-copy upserts, swap removal, frame-based
  sequence allocation and dirty-notification order. Counted selection filters all
  candidates; uncounted selection skips weaker candidates. Collection keeps order
  and duplicates. See MESSAGE_MANAGER.txt.
- Require explicit owner validation, shared budgets and dirty callbacks. Reject
  unknown filters and unsupported callback mutation. Terminal retirement clears
  storage without graph callbacks; a missing receiver in an existing channel is
  refused instead of reproducing an invalid native memory operation.
- Pass17920 original single-selection,17920 collection and3554 mutation comparisons
  with declared storage/descriptor/filter/notification adapters and unchanged game
  code. Add19 behavior tests, including an actual VM operation's budget/fault;
  all1011 related tests pass in24.643s with frozen hashes, plus Ruff/diff checks.
- This backend is not yet mounted by SceneWorld or the sending/receiving States.
  Native identity/channel/frame/filter policies, variable bindings, subgraphs,
  replies and complete State lifetimes remain open. Existing mission frontiers
  are unchanged; no fresh client run, deployment, story/cinematic/enemy-AI or
  extraction/results acceptance is claimed. Publication helper remains missing;
  no GitHub push is performed.

#### Native game-message entry reception

- Preserve full ordered packet parameters, repeated keys and separate caller/
  instigator identities in shared-scene delivery. Select authored entry-array
  nodes in context0 with scalar messages, one-shot indices and explicit filter
  services. Unknown filter engines remain strict; no policy default is installed.
- Queue native parameter/context assignments before Entry events at the receiver
  clock. Match named/positional rows in incoming order; missing inputs keep old
  values. Preserve Dynamic-only context casts, typed invalid identities and the
  source-current-object getter's forward-possession/fallback behavior.
- Add transient parameter scopes with builtin9/A/B and first-full-key lookup.
  Remove scopes after selection/binding, including faults. Check source/receiver
  lifetimes and shared budgets. Passive VM export includes disabled entry indices,
  packets, scopes and queued writes without callbacks. Legacy transport and the
  independent sending/receiving States remain outside native entry acceptance.
- Pass1350 original queue/binding,3024 entry-selection,15 scope and60 subject
  comparisons, plus one native null-namespace fault probe, with declared engine/
  storage adapters. Fix actual named message entries retaining idle transient
  scripts;2688 original lifetime cases also pass with the extracted class name.
  Add25 behavior tests;992 related and33 server tests pass with
  frozen hashes, plus Ruff/diff. See MESSAGE_RECEIVE.txt.
- Actual100B's15 entry nodes pass isolated pre-init queue fixtures, including
  caller bindings10110/10111/10112. Ordinary mode initialization and entry plugs
  are not executed by those fixtures. Three explicit controller profiles still
  retain the unknown06FF receiver; ordinary controller retains81A9AB10; real Match
  retains rootD38/node106. Placed50/123 and failed-receiver cleanup remain unchanged.
- No fresh original-client run, deployment or full story/cinematic/enemy-AI
  acceptance is claimed. Normal inputs and unrelated work remain intact.
  Publication helper missing; no GitHub push is performed.

#### Native outgoing game-message construction

- Restore strict ActionSendGameMessage scalar message selection, generic target
  collection and guard/evaluation order. Preserve duplicate external targets,
  coalesced self delivery and distinct owner/inherited/overridden source inputs.
- Collect every parameter into a typed array, including empty arrays. Retain
  ordered full keys and duplicates in the request. Actual0C90/node41 parameter
  008D is now tuple(0,), correcting the earlier scalar parameter while preserving
  the team query's scalar0. Unknown mode receiver still stops delivery.
- Preserve scalar destination and success separately: empty hero iteration writes
  int0 with failure, so source overrides retain their inherited source. Existing
  scalar-value callers keep their behavior. Bind new services to actual component
  lifetimes and shared budgets. Unsupported instigator/repeated-key delivery stays
  strict; non-strict compatibility and the separate sending State are unchanged.
- Pass3456 original action construction and1296 hero-iterator comparisons with
  declared engine/storage/status adapters. Add18 behavior tests;967 related and33
  server tests pass with frozen hashes, plus Ruff/diff. See GAME_MESSAGE_ACTION.txt.
  PC code constructs external target lists but only dispatches self; it does not
  establish server forwarding or complete native message consumers.
- Ordinary controller retains81A9AB10; real Match retains rootD38/node106 before
  controller execution. Placed50/123, failed-receiver rejection and cleanup remain
  unchanged. No fresh original-client run, normal deployment or complete story,
  cinematic/enemy-AI acceptance is claimed. Normal inputs and unrelated work remain
  intact. Publication helper missing; no GitHub push is performed.

#### Native entity team query and filter dependencies

- Implement65ABFCCC with native no-output versus signed-1 behavior, uint32
  entity conversion, first-pass dependencies and a fresh second lookup. Decode
  five team bits with lowest-bit priority, ignoring unrelated/spectator flags.
  Context, lookup and observer services remain explicit; unknown data stays strict.
- Bind contexts to actual component lifetimes. Preserve callback retirement,
  stale-component isolation and shared lookup budgets. Do not infer a team from
  ownership or install a normal-world service/default for unresolved queries.
- Pass1600 original query comparisons and128 full filter-decoder comparisons
  with declared engine/pointer/storage adapters. Two additional null-namespace
  probes fault natively and remain unsupported. Add21 behavior tests;949 related
  and33 server tests pass with frozen source hashes, plus Ruff/diff checks.
- Three explicitly supplied controller profiles now prepare0C90/node41's06FF
  hero-list/team parameters and retain the unknown mode receiver. Delivery is
  unaccepted. Ordinary controller still retains81A9AB10; real Match still retains
  rootD38/node106 before controller execution. Placed50/123 and failed-receiver
  cleanup remain unchanged. See ENTITY_TEAM_QUERY.txt.
- No fresh original-client run, deployment or complete story/cinematic/enemy-AI
  acceptance is claimed. Normal inputs and unrelated work remain intact.
  Publication helper missing; no GitHub push is performed.

#### Native controller hero-catalog iteration

- Implement1AAB4B4F scalar-first, collected-value and nested-group interfaces.
  Preserve typed integer0 when scalar iteration yields no item, ordered Assets,
  duplicate/zero handling, selector watches and dependency kinds3/37. Unknown
  engine lookup, catalog and availability policies stay strict owner services.
- Bind services to the actual script component lifecycle. Old instances cannot
  borrow a replacement's context or retire it on failure. Detach/fault/unbind and
  callback retirement close the owned iteration path; rejected rows share budgets.
- Pass1296 original iterator/wrapper/traversal comparisons with declared engine
  and storage adapters, including one iterator/vector release per case. Two
  missing-environment probes confirm null-scratch behavior; no empty-success
  result is fabricated. Full catalog construction and availability remain open.
- Six retained graph references include actual0C90/node55 and node147 configs:
  the former receives all supplied assets, the latter reads the first. The
  ForCode State and independent5E805B5A list State remain unaccepted. With explicit
  role/catalog inputs, all three controller profiles now retain65ABFCCC. Ordinary
  execution still retains81A9AB10; real Match still retains rootD38/node106.
- Add21 behavior tests;928 related and33 server checks pass, plus final native/unit
  and Ruff/diff rechecks. Final lint/docstring corrections preserve executable
  ASTs, with earlier sources retained locally. Placed50/123 and failure cleanup
  remain unchanged. No fresh original-client run, deployment or complete story,
  cinematic/enemy-AI acceptance is claimed. See CONTROLLER_HERO_ITERATION.txt.
  Normal inputs and unrelated work remain intact. Publication helper missing;
  no GitHub push is performed.

#### Collected config-array values in variable assignments

- Correct SetVar and SetVars to use the native +28 value interface. Config groups
  now assign all collected values, including typed empty arrays. Preserve direct
  dynamic arrays, nested iterator order, null-member handling and strict faults.
- Match SetVars pointer rules: skip missing targets before evaluating values;
  write integer0 for missing/failed values, while successful null remains null.
  Earlier pair writes remain visible to later values. SetVar retains its different
  rule of skipping either absent pointer. Indexed/keyed store routing is preserved.
- Pass216 original execution cases, including212 value comparisons across both
  actions and four guard/order cases. Replay100 real config inputs with declared
  dynamic values; the635-graph bundle has108 direct array assignments, including
  30 in the static Rialto closure. Adapters and full-engine limits are documented.
- Add13 behavior tests;907 related and33 server tests pass, with Ruff/diff. The
  isolated placed catalog remains50/123 first frames complete,73 strict faults.
  Normal controller still retains81A9AB10; Match retains rootD38/node106 before
  controller execution. Failed receiver/queue cleanup remains verified.
- Original PC loader/cache and BooleanSwitch probes establish81A9AB10's no-output
  client fallback, not its server meaning. No guessed default is installed.
  Three explicit input profiles reach1AAB4B4F; its iterator remains open. No fresh
  original-client session, normal deployment or full story/cinematic/enemy-AI
  acceptance is claimed. See VARIABLE_ASSIGNMENTS.txt. Normal inputs and unrelated
  work remain intact. The publication helper is missing; no GitHub push occurs.

#### Native entity existence and current-possession queries

- Implement E72F6348/0AC15F78 with explicit owner namespace/environment, complete
  lookup and dependency inputs. Preserve no-output failure, Bool false and typed
  Entity(0) as distinct outcomes, nested variable watches and callback ordering.
  Retire contexts on detach/fault/unbind; do not infer forward current possession
  from the reverse primary-possessor catalog or map bindings.
- Pass1680 original-instruction comparisons with declared engine/storage adapters,
  including112 unchanged current-object getter calls. Two earlier read-only
  original-client hero-switch captures agree with component44+D8. No fresh client
  session, full engine timing or complete mission acceptance is claimed.
- Actual000E/0F44 with explicit created controller/body and query inputs now creates
  0C90/1194 and retains81A9AB10/frame0. Failed bound controller messages still fail
  closed. Real Match still prepares148 components/queues and retains rootD38/node106
  before controller execution, then retires bindings. No readiness or deployment.
- Add19 behavior tests;894 related and33 server tests pass, with Ruff/diff checks.
  Full controller/body/actor lifetimes, engine services/phases, cinematics, enemy
  AI and mission/results remain open. See ENTITY_QUERIES.txt. Normal inputs and
  unrelated working changes remain intact. Publication helper remains missing;
  the required synchronization cannot run and no GitHub push is performed.

#### Declarative shared-variable State and original-client acceptance

- Model9D7BF987 with common State lifecycle and strict agreement between Dynamic
  declarations, both compiled node sync groups and graph sync metadata. Preserve
  an empty codec and passive snapshot storage; no variable evaluation or writes.
- Execute24 original allocation/binding cases with the captured parent registry
  record and no replaced callbacks. All278 real nodes/1494 declarations agree;
  the production validator accepts all278 without allocating variables or states.
- Two independently created original-client matches verify the actual0F44/node33
  common State object, three on/off cycles, counter reset, unchanged typed v940,
  phase ACKs and retired native bindings. This is an isolated owner-frame fixture,
  not complete controller execution. Client closed normally; owned server stopped.
- Five former placed9D7 stops now complete frame0:50/123 nonempty components pass
  in the unchanged isolated catalog, with73 strict stops. Actual000E/0F44 now
  retains world query E72F6348/frame0; failed controller messages still fail closed.
  Shared rootD38/node106, global phases, full body/actor lifetimes, cinematics,
  five enemy AI types and mission/results remain open. No normal deployment.
- Add12 behavior tests;875 related and33 server checks pass, with Ruff/diff.
  Passive VM storage covers35 State models. Normal inputs and unrelated working
  changes are preserved. See SHARED_VARIABLE_STATE.txt. Publication helper missing;
  required synchronization cannot run and no GitHub push is performed.

#### Preserved entity script component definitions and controller hosting

- Retain complete entity/hero script components, including empty defaults,
  schemas, policy flags and graph dependencies through shared XML references.
  Distinguish unknown legacy/missing data, known absence and existing components.
  Reject ambiguous ownership, conflicting records and unsupported definitions.
- Add SceneHost.add_entity_scripts for already-created entities in its initial
  catalog, using existing strict startup/override/scheduling and owned cleanup.
  Unknown data cannot publish an empty substitute. Preserve sealing and reject
  overrides/values on known absence. Add22 behavior tests;863 related and33 server
  tests pass, with Ruff/diff checks. No normal input or default Match policy change.
- Actual000E plus explicit mode0025/team0 root0F44 retains9D7BF987/node33 during
  controller startup. Its bound failed receiver still rejects0EC6/node22 after
  parameter41; failure is not treated as absence. A literal16-graph scan does
  not establish all native/dynamic message consumers. Full startup remains open.
- Real Match prepares146 placements, mode100B and one actual controller with148
  queues, retainingD38/node106 before controller execution and retiring bindings.
  The external bundle changes only000E component metadata. Native mode mounting,
  body/controller/actor lifetimes, services/phases/replication, cinematics, enemy
  AI and complete mission results remain unaccepted. No new client run or
  deployment. Preserve unrelated working changes. See ENTITY_SCRIPT_COMPONENTS.txt.
  Required publication helper missing; no GitHub push.

#### Native unkeyed array insertion and typed clear actions

- Restore strict4C3509FB append/insert/unique behavior and1C2CC6D5 fresh typed
  empty-array assignment. Preserve int-zero gaps, float32 index conversion,
  ordered providers, self-reference termination and normal clear notifications.
  Permissive player/body components retain their previous output-only policy.
- Verify880 original insertion comparisons and64 clear comparisons with declared
  storage/iterator/notification adapters.2 additional cases retain the explicit
  native-limit diagnostic gate. Keyed rows and linked insertion/checkpoints
  remain strict. Add25 behavior tests;825 related and33 server tests pass,
  with Ruff/diff checks.
- Actual0F45's isolated voice39->array40/41->voice35->clear42 chain completes
  with typed array1839 empty, no fault, queued events or voice registrations.
  Authored flags/plugs are unchanged. Source42 is uninitialized and backend/
  clock responses remain explicit fixture inputs; full startup/audio is open.
- The separate helper still stops at controller-message component/node22 before
  voice polling. Shared Match retainsD38/node106 and retires147 queues. Complete
  actor/services/replication/phases, cinematics, enemy AI and mission results
  remain unaccepted. No new original-client run or deployment. Normal inputs
  and unrelated working changes are preserved. See ARRAY_ACTIONS.txt.
  Required publication helper missing; no GitHub push.

#### Shared voice stimulus lifecycle and separate polling

- Implement40E05F6B shared begin/poll/timer/failure callbacks behind explicit
  component voice contexts, global clocks and backend services. Preserve delay,
  target deduplication, failure timeout, retries, float32 duration conversion,
  part queries and custom output plugs. Ordinary ticking does not poll voice.
  Unknown consumers remain strict; permissive spawn-bark policy stays passive.
- Verify973 original-instruction cases and both constructors with declared
  adapters. Ten scalar caches enter passive VM storage across34 concrete State
  models. Contexts detach/retire on end, unbind and failure; stale component
  faults preserve replacement services. Add24 behavior tests;800 related and33
  server tests pass, with Ruff/diff checks.
- Actual0F45/node48 now begins voice node39 and executes0EC6 parameter array41
  before the controller-message component/node22 stop. A separate actual voice
  State fixture uses explicit backend responses, keeps authored plugs, finishes
  and retains4C3509FB/node40. Actualnode39 has both client/server-only flags false;
  remote index-1 does not classify it server-only. Full startup remains open.
- Shared Match still retainsD38/node106 and retires147 queues. Complete native
  poll phases, real audio/submission services, replication/readiness, cinematics,
  enemy AI and full mission results remain unaccepted. No new original-client
  run or deployment. Normal inputs and unrelated working changes are preserved.
  See VOICE_STIMULUS_STATE.txt. Publication helper missing; no GitHub push.

#### Component PlayScript queues and transient graph lifetimes

- Route authored requests to actual scene components through explicit resource
  providers and owned queues. Preserve skip/replace/reuse order, immediate and
  reverse flush, resource/startup waits, native1500 boundary and real queued
  schema/initialization/start bindings. Reentrancy, active self replacement and
  lost ready namespaces remain strict boundaries. Normal host policy is unchanged.
- Verify277 original submit/flush cases, including reverse batches, and2688
  automatic-lifetime cases with declared adapters. Request byte0 sets Instance318
  bit11's automatic retirement policy. Events, enabled entries, frame age and
  active States govern retirement; passive VM storage records its three fields.
- Actual0F45/node48 now creates0EC6 through its installed queue and queues array17
  for variable41. The caller's following path retains strict40E05F6B/node39 before
  helper update. Separate isolated service submission assigns array41 and stops
  at controller-message component/node22. No manual loaded-core handoff is used.
  Fixture body/controller/input/resource services do not establish full startup.
- Real in-memory Match configures and retires147 queues, retaining rootD38/node106.
  Failure, leave and stop cancel pending work. Fix stale source failure clearing
  replacement engine services; revalidate identity around request callbacks.
  Add25 behavior tests;770 related and33 server tests pass, with Ruff/diff.
- Full allocator/loader/physical deletion, native phases, actor/body/controller
  lifetimes, live replication/readiness, cinematics, enemy AI and mission results
  remain open. No new client run, deployment or full mission acceptance. Normal
  inputs and unrelated working changes are preserved. See PLAY_SCRIPT_QUEUE.txt.
  Publication helper missing; no GitHub push.

#### Native PlayScript source and entity-instance context

- Derive live SceneWorld entity-instance caller/owner from its actual component
  and inherited instigator from native builtin001B/+234 cache semantics. Verify
  the own entity-instance getter table; detached/stopped sources fail lifecycle
  checks before or after effective-value callbacks.
- New loaded-core allocations accept an explicit instigator, seed typed001B
  before defaults and retain native zero's owner fallback. Start-entry caller
  bindings stay separate. Skip/reuse retain the existing source. Passive VM
  storage includes the uint32 source cache without running its getter.
- Compare 294 original getter/setter cases, seven allocation-source branches
  and seven owner-identity cases with declared storage/owner adapters. Add 13
  behavior tests; 745 related and 33 server tests pass, with Ruff/diff.
- Actual0F45/node48 derives its request context through the bound component.
  An explicit0EC6 loaded-core handoff sets001B and array variable41, then retains
  strict message-target component/node22. Automatic submission still stops before
  success. Body/controller bindings, allocation source and input17 are fixtures;
  full body/controller startup, helper execution and transport are unaccepted.
- Real shared Match remains147 startup preparations and strict100B/D38/node106.
  Native allocator/loader/flags, dynamic actors, full services/replication,
  cinematics, enemy AI and mission results remain open. No new client run or
  deployment. Normal inputs and unrelated working changes are preserved.
  See PLAY_SOURCE_CONTEXT.txt. Publication helper missing; no GitHub push.

#### Initial Match-owned shared scene scripts

- Add an explicit GameServer/Match scene factory for the initial mode/placed
  component catalog. Bind all components before startup and share live foreign
  variable slots, effective links, dependency notifications and queued messages.
  Retirement removes both dependency endpoints. Failed live mode components
  cannot fall back to immutable initial values. Unknown strict message transport
  or receiver components stop execution before the following success plug.
- Match checks map, mode, clock and actual server creates after collision
  readiness. It owns ordinary frames, terminal failure and last-player cleanup;
  server stop also closes owned hosts. Duplicate frames are idempotent and gaps
  require a new lifecycle. Prepared entrance readiness stays blocked pending live
  scene replication. Shared-runtime stored export is explicitly unsupported.
- A real in-memory GameServer probe prepares 146 placed components plus mode
  root 100B in the explicit 371-create research catalog. It retains strict
  D38D0035/node106/frame0, ends the Match and retires all bindings. Later
  components do not run after root failure. Collision is an explicit fixture.
- Add 27 behavior tests; 732 related and 33 server tests pass, with Ruff/diff.
  Preserve a prior server result invalidated by a concurrent source edit;
  authoritative final runs use fixed source. Independent component counts
  remain 45 completed first frames and 78 strict stops, not shared acceptance.
- Initial clock/order is explicit owner policy. Native scene phases, dynamic
  actors, complete services/replication, cinematics, enemy AI, extraction and
  results remain open. No new client run, deployment or full mission acceptance.
  Normal inputs and unrelated working changes are preserved. See SCENE_SCRIPT_HOST.txt.
  Publication helper missing; no GitHub push.

#### Placed data-flow mapping and typed provider callbacks

- Implement native23A661BD ordinary begin/cleanup/dependency and its separate
  typed answer provider with explicit component47 registry/counter/position
  services. Preserve cache-before-register, remove-before-clear, target Handle
  tags, unchanged-GUID target refresh and missing-namespace status4 behavior.
- Own factory/vtables and2070 original State/provider comparisons pass, with
  declared engine adapters. Twelve original A82CD4B2 literal cases verify typed
  unkeyed DataFlow assets including zero; keyed availability remains explicit.
- Two actual0E61 components now pass State allocation and reach the later strict
  action0F647E55/node13 before mapping activation. Separate authored-node fixtures
  verify061D global mapping registration/removal, float answer1 and four38/37-bit
  codec checks. Full component first frames are not accepted by these fixtures.
- Add19 behavior tests;668 related and15 server checks pass, with Ruff/diff.
  Passive VM storage covers33 concrete State classes. Scene contexts are bound
  to existing entities and revoked on retirement/failure.
- The unchanged scene still completes45/123 nonempty first frames with78 strict
  stops:66 control contexts,3 alias components,1 model-look scope,3 action0F647E55
  and5 State9D7BF987. Full100B remainsD38/node106. No unknown-service shortcut.
  Complete data-flow services, production hosting/lifetimes, story/cinematics,
  enemy AI and results remain open. No new client run or deployment. Normal
  inputs and unrelated working changes are preserved. See DATA_FLOW_MAPPING.txt.
  Publication helper missing; no GitHub push.

#### Original-client model-look scope and restoration sample

- Verify actual144E/raw11 in two isolated Story/Normal/Reaper matches, sending
  all authored remote States with no source override. Eight phase frames are
  ACKed after create ACKs and two streams retire. Eight accepted native captures
  verify28 transmitted variable values/tags and three on/off cycles.
- Observe native component47's specialized VM/scope helper registering and
  dispatching the ModelLook interface. State caches/flags, component4's authored
  look override/restoration and the engine look stack agree. Fresh native entity,
  instance, State and target identities reset counters in the second match.
- Correct the inspector's identity association: nativeEntity+AC differs from
  networkEntity+B0. Preserve17 earlier captures whose model/scope fields had
  already settled; no activation frame is resent or pending timing inferred.
- Five successive codec checks and36 focused State/codec/statescript tests
  pass. Prior649 related+15 server checks cover unchanged production code.
  Client closes normally; owned isolated servers stop and release ports.
- Accept this exact native scope/model transition sample with declared server
  services and diagnostic off. Visible appearance, complete engine services,
  phase/clock mapping, production hosting/lifetimes, rootD38, story/cinematics,
  enemy AI and results remain open. Normal inputs and unrelated working changes
  are preserved; no deployment. See MODEL_LOOK_STATE.txt.
  Publication helper missing; no GitHub push.

#### Placed model-look scope and tick callbacks

- Implement native7D71FDF3 scope enter/exit and tick with explicit namespace,
  target policy, global entity lookup, capability and model consumers. Preserve
  cache commits before writes, failed apply, old-target gates, queued abort and
  exit bit6 suppression. Ordinary begin/cleanup/dependency hooks remain passive.
- Verify the class's own factory/vtables and compare4320 original callback cases,
  with explicit engine adapters. Twelve literal cases confirm typed unkeyed
  ModelLook resources. Nonzero-key availability and complete engine services
  remain open. The explicit ForwardHost accepts the verified null+18 interface.
- Actual144E/node26 completes authored frame0 and requests look09800000000080D0
  apply/removal using declared host/model services, without a source override.
  Two187/186-bit on/diagnostic-off codec checks preserve all remote States/values.
  The unchanged strict scene still completes45/123 nonempty first frames with78
  stops; this sample now requires scope registration instead of unknown State code.
- Add19 behavior tests;649 related and15 server checks pass, with Ruff/diff.
  Passive VM storage includes both caches and covers32 concrete State classes.
  Context lifetimes are bound to existing entities and revoked on retirement.
- Root100B remains strictD38D0035/node106. Normal inputs and unrelated working
  changes are preserved. No new original-client acceptance or deployment.
  Rendered model changes, production hosting/lifetimes, full story/cinematics,
  five enemy AI types and results remain open. See MODEL_LOOK_STATE.txt.
  Publication helper missing; no GitHub push.

#### Original-client animation-alias request and selection samples

- Verify all three actual 0D32 alias States in two consecutive original-build
  Story/Normal/Reaper matches. Initial frames omit source001B; native tag10/self
  is observed before each connection's activation. Other active States remain
  outside this sample. Server services and diagnostic aborts are explicit.
- All 24 phase frames are ACKed after create ACKs; six streams retire on leave.
  Eight accepted captures cover 24 native subject samples and 348 transmitted
  variable values/tags. They verify lazy State allocation, handle retention,
  request rows/counters and current-alias selection/restoration. Fresh native
  entities and instances restart counters in the second match.
- Preserve two additional early off captures with cleanup complete but current
  selection still pending; later reads settle. Transport ACK does not establish
  engine completion. No engine phase mapping or latency bound is inferred.
- Fifteen successive codec checks and 34 focused alias/codec/statescript tests
  pass. The preceding 630 related and 15 server checks cover unchanged production
  code; documentation diff checks pass. Client closes normally, owned servers
  stop, and normal runtime inputs and unrelated working changes are preserved.
- Exact sample request/selection is accepted. Visual animation playback, native
  true-mode timing, complete allocator/services, production hosting/lifetimes,
  rootD38, story/cinematics, enemy AI and results remain open. No deployment.
  See ANIMATION_ALIAS_STATE.txt. Publication helper missing; no GitHub push.

#### Native placed animation-alias State

- Implement strictB1FA9C2A begin/cleanup with explicit component55 storage,
  allocation and notification services. Preserve sampled/wrapping handles,
  failed allocation, reverse removal and last/current notification gates.
  True mode notifies then queues ABORT; successful removal retains the handle.
- Verify the class's own factory/vtable and execute966 native State/registry
  comparisons, including original request instructions with explicit event/clock
  adapters. Sixteen native literal comparisons verify typed AnimAlias resources,
  including zero. Keyed availability and complete engine playback remain open.
- Three actual0D32 components complete frame0 with declared animation service
  fixtures; six authored-on/diagnostic-off codec round trips preserve all remote
  States and configured variables. No engine selection/playback is simulated.
  The unchanged strict scene still completes45/123 first frames, with78 stops;
  three now require actual animation components instead of unknown State code.
- Add17 behavior checks;630 related and15 server snapshot/research-injection
  tests pass, with Ruff/diff checks. Read-only VM storage adds the State handle
  and covers31 concrete State classes. Permissive body policy remains passive.
- Normal inputs and unrelated working changes are preserved. No new original-
  client acceptance or deployment for this State. Production host/lifetimes,
  rootD38, full story/cinematics, five enemy AI types and results remain open.
  See ANIMATION_ALIAS_STATE.txt. Publication helper missing; no GitHub push.

#### Original-client active entity-control sample

- Two authorized isolated Story/Normal/Reaper matches verify actual120E raw
  State2 with explicit observed source binding and declared server services.
  Eight phase frames are ACKed after create ACKs; both streams retire on leave.
  Diagnostic abort frames cover three on/off cycles, including a fresh match.
- Read-only native inspection confirms State identity/caches and repeatable
  State/model flag changes with restoration. Runtime component1/model250 exist
  despite no explicit serialized model component. Correct the first inspector's
  active-mask annotation from preserved raw bytes/native bit0 instructions.
- Five codec round trips, eight native comparisons and the transport/lifetime
  aggregate pass. All39 focused State/codec/transport tests pass with external
  runtime configured; the initial invocation omitted that setting and failed.
  The earlier613+15 checks remain applicable to unchanged production code;
  documentation diff checks pass. Client closes normally; owned servers stop.
- Normal runtime and unrelated working changes are preserved; no deployment.
  Exact sample/native model transition is accepted. Complete engine services,
  allocator/host/lifetimes, rootD38, story/cinematics/enemy AI/results remain open.
  See ENTITY_CONTROL_STATE.txt. Publication helper missing; no GitHub push.

#### Native placed entity-control State rules

- Implement strict33A46033 cache/type-list lifecycle, ordered rule aggregation
  and guarded engine dispatch. Retain capability/query inputs across callbacks.
  Require explicit engine services; unknown context/consumers still fault.
  Bind contexts to actual entities and revoke dependent bindings on retirement.
- Original instructions match4514 rule,448 cache and512 dispatch cases with
  declared pointer/config/spatial/engine adapters; eight further native cases
  inspect recursive model-bit updates. See ENTITY_CONTROL_STATE.txt for scope.
- Actual120E/node9 (raw State2, remote1) completes frame0 with declared service
  fixtures, requests entity/model changes and passes a73-bit active-state codec
  round trip. Engine effects and new original-client activation remain unaccepted.
- Add22 behavior checks, including sampled-input regressions and retirement;
  pass613 related and15 server checks, Ruff/diff. VM storage includes seven
  cached fields; wire formats and permissive body policy remain unchanged.
- All146 selected components configure; the strict actual-scene first-frame
  probe still completes45/123 nonempty components. Sixty-six stop on missing entity-control
  context;12 retain other State/action faults. No absent-context shortcut,
  deployment or full mission/cinematic/enemy-AI/results acceptance. RootD38
  stays strict. Publication helper missing; no GitHub push.

#### Typed literal configs for placed script startup

- Evaluate unkeyed VoiceStimulus, Animation, Hero, entityDef and SkinTheme as
  typed resource references; preserve signed32 TeamIndex. Nonzero resource keys
  still delegate availability to context and fault in the strict base world.
- Original factory/vtable links and evaluator/setter instructions match3096
  scalar cases without executed service stubs. Availability services and owning
  destination disposal remain outside this scope. See CONFIG_EVALUATION.txt.
- All146 selected components now configure in the unchanged isolated371-create
  catalog:23 empty,123 nonempty,124 graphs and542 queued writes. Independent
  first frames complete45/123 nonempty components;78 retain strict State/action
  stops. Full root100B still stops at D38D0035/action106.
- Add six behavior checks;591 related and15 server snapshot/research-injection
  checks pass, with Ruff/diff checks. Normal inputs unchanged; no new client
  acceptance or deployment for this code change. The preceding two-sample client
  acceptance covers frozen graph baselines only. Mission hosting, cinematics,
  enemy AI/results remain open. Publication helper missing; no GitHub push.

#### Two actual placed graph baseline samples

- An authorized isolated original-client session completes two consecutive
  Story/Normal Rialto entries as Reaper and settled-menu returns. Actual0F65 and
  120E descriptors and frozen schema values are queued after entity create ACKs;
  both graph streams are acknowledged and retired on leave in each connection.
- Read-only native inspection in both matches confirms the two exact graph/ID1
  instances and all eight authored values/type tags. Native builtin sources also
  match owners, without deriving the server allocator. Two offline codec round
  trips, both native comparisons and the transport/lifetime aggregate pass.
- Client closes normally; isolated server stops and releases ports. Normal
  runtime inputs unchanged; candidate not deployed. Production code remains
  a8234f4 with its585+15 regression results. Documentation diff checks pass.
- Scope is two frozen baselines with remote States off and no authored server
  execution. Match hosting, live State replication, full entity lifetimes,
  rootD38, complete story/cinematics/enemy AI/results remain open. Publication
  helper missing; no GitHub push.

#### Explicit placed component graph startup

- Add an owner-selected starter for fresh components and loaded graphs. Queue
  public/definition values, first-match instance overrides, then additional
  graphs. Prepare all pending graphs at the first update clock before due events.
  Keep manual init=False inert and discard pending work on destroy/failure.
- Match108 original schema/evaluation cases and36 original initialization cases
  with declared allocator/resource/role/event-storage adapters. Full native
  allocation, source bindings, async resources and Match policy remain external.
- Strict actual-scene probing with an explicit isolated371-create catalog:
  137/146 components configure;114 nonempty components create114 graphs and432
  queued writes. Nine unknown configs stop startup. One frame completes43/114
  configured nonempty components;71 stop on four unknown State classes.
- Add19 behavior checks and a read-only pending-snapshot check; pass585 related
  and15 server snapshot/research-injection checks, Ruff and diff checks. VM
  storage includes initialization_pending; wire formats unchanged. Normal runtime
  inputs unchanged. No new client/replication acceptance or deployment; rootD38,
  full story/cinematics/enemy AI/results remain open. Publication helper missing.

#### Rialto placed script creation and initialization boundary

- Identify that the required-loading create list omits every nonempty placed
  script component: 123 components / 124 definition graphs are unbound. The
  twelve bound script components have empty initial graph lists.
- Ninety original executable cases verify definition graph creation, first-match
  instance overrides, additional graphs and the once-only guard, with explicit
  engine/allocation/evaluation adapters. Empty overrides preserve defaults.
- An authorized isolated client loaded a 134-extra-create candidate, entered
  Rialto as Reaper, returned to a settled menu and closed normally. Two sampled
  entities have the expected native component47/owner, but zero graph instances.
  No placed graph descriptors/frames were sent; script execution is unaccepted.
- Document the missing creation/graph-host boundary and its remaining work.
  Normal runtime inputs unchanged; candidate not promoted. Documentation diff
  checks pass; production code remains the 565+15-tested 428fd13 version.
  Full mission, cinematics, AI and rootD38 remain open. Publication helper missing.

#### Ordinary auxiliary scopes before deferred notifications

- Add an explicit ForwardHost frame runner that processes all VM due times,
  drains auxiliary history once, then notifies ForCode channel1 once. Edges
  created by that notification wait for the next ordinary auxiliary phase.
  Preserve the manual/legacy APIs and explicit native clock ownership.
- Original D0D350/1B8C910 place +48/+58 inside ordinary +50, before channel1.
  Thirty native executable cases verify bounded ordering/clock writes; six
  two-frame comparisons execute native history writes from the deferred boundary
  and match the production runner's delayed begin/end scope callbacks.
- Eleven behavior checks cover actual ForCode notifications, authored timers,
  queued/direct edges, lifetime, both failure phases and a shared work budget.
  Actual0F45->0EC6 starts through the runner and reaches frame500 with supplied
  services/clocks, effect creation/retirement and three States still active.
- Passed565 related checks,15 server snapshot/research-injection checks, Ruff
  and diff checks. No new persistent VM fields or wire schema. Full100B remains
  D38D0035/node106; full clock/phase mapping, Match hosting, engine consumers,
  complete story/cinematics/enemy AI remain unaccepted. No deployment/new client;
  required publication helper remains missing.

#### Normal-forward auxiliary scope history

- Add an explicitly selected per-Component ForwardHost. Record native +100/+118
  history before active-bit changes, then dispatch pending parity at the owner's
  explicit frame/phase boundary. Preserve manual Registry and knownNone policies;
  unknown frame, clock rewind and undecoded self-unregister remain strict gates.
- Preserve first-edge snapshots, stored frame/payload, cold registration,
  live pending cleanup/swap removal and callback mutations. Bound repeated scans
  to the VM work budget; terminal teardown/failure discards local history without
  more authored callbacks. No State storage fields or wire schema are added.
- Original writer/wrapper/drain/unregister instructions match the production host
  in 386 cases, including 26 callback-mutation cases, with explicit adapters.
  Correct the earlier +220 inference: A0A2E0 queries; A087E0 marks history dirty.
- Actual 0F45 -> 0EC6 reaches frame500 using the production host, with no injected
  interface callbacks or VM transition wrappers. Its descriptor is created at
  frame0 and retired at frame46; states10/15/16 remain active at frame500.
  Clock/service inputs and the post-update drain boundary are still supplied.
- Passed 554 related checks, 15 server snapshot/research-injection checks,
  Ruff and diff checks. Full100B remains D38D0035/node106. Match hosting, native
  phase placement, rollback, engine consumers, cinematics and enemy AI remain
  unaccepted. No deployment/new client; required publication helper is missing.

#### Auxiliary effect state callbacks and routes

- Register 582E0D5F with explicit scope enter, refresh, matching observer and
  non-orientation exit callbacks. Native owner/context, component-43 targets,
  instance flags and global fallback lists are declared inputs. Unknown services
  remain strict; begin/end/tick do not invent host dispatch or reconstruction.
- Bind descriptors before initial evaluation and discard their local effects on
  fault, object or entity retirement. Preserve unaffected sources in shared lists
  and reject stale instance callbacks. Passive VM storage covers 29 State models.
  Live SceneWorld/effect storage and native orientation cleanup remain unsupported.
- Original routing/callback instructions match 416 cases with 156 allocations
  and matching retirements, using explicit lookup/allocator/constructor adapters.
- Actual 0F45 -> 0EC6 now passes entry and reaches frame 500 with supplied services
  and clock mapping, draining its 750/1500/2250ms timers. No interface dispatch is
  injected into that path; three states remain active. A separate actual node4
  test explicitly enters, refreshes and retires its concrete effect State.
- Passed 533 related runtime/expression/VM/body/bot/entrance/replication/replay
  checks, 15 server snapshot/research-injection checks, Ruff and diff checks.
  Full 100B remains D38D0035/node106. Native dispatch/history, engine consumers,
  complete story/cinematics/enemy AI remain unaccepted. No deployment/new client.
  Required publication helper remains missing.

#### Auxiliary effect list ownership and initialization

- Add explicit effect descriptors and native serial-sorted list membership.
  Retirement clears a live bit while retaining the entry and observer; it
  does not imply deletion, callback dispatch or consumer completion. Preserve
  uint32 wrap and unsigned ordering; unresolved serial ties fail before mutation.
- Add initial authored writes before parameter refresh. F8/FC keep the first
  scalar evaluation while F4 can take a separately evaluated second value.
- Original allocation/reference/sort/retire instructions agree on192 allocation
  and76 retirement operations; initialization/refresh/casts agree on192 cases
  and7488 field comparisons. Allocator/constructor/engine inputs are adapters.
- Establish specialized component262C010 from its constructor, with non-null
  scope registration/removal slots. Seventeen original cold register/removal
  cases confirm append/swap removal. Pending-history dispatch remains open.
- Actual0EC6/node4 builds an isolated descriptor with supplied route inputs,
  then retains it after retirement. No authored VM State or engine object is
  constructed. Helper remains582E0D5F/node4; full100B remainsD38D0035/node106.
- Passed513 related runtime/expression/VM/body/bot/entrance/replication/replay
  checks,15 server snapshot/research-injection checks, Ruff and diff checks.
  Mission/cinematics/enemy AI remain unaccepted. No deployment/new client.
  Required publication helper is still missing.

#### Auxiliary effect parameter updates

- Add 582E0D5F's isolated existing-effect parameter core: native f32 defaults,
  scalar/asset/vector casts, angle conversion, MAXSS edges and ordered entity
  list replacement. Pass the calling State through config iterator reads so
  dynamic dependencies retain watcher and shared VM work-budget behavior.
- Original x64 parameter instructions/casts match 192 cases and 6,336 field
  comparisons with explicit engine/config/iterator adapters. Another 144 native
  callback-gate cases establish pointer/presence/active-bit conditions, using
  stubbed creation/release/refresh bodies. Neither proves host trigger timing.
- Actual 0EC6/node4 parameters independently resolve to 0/0.75/5/0. Creation,
  target routing, consumers, native +88 phase and reconciliation remain open;
  the state remains unsupported. Helper entry still stops at 582E0D5F/node4,
  full 100B at D38D0035/node106. No mission/cinematic/enemy AI acceptance.
- Passed 503 related runtime/expression/VM/body/bot/entrance/replication/replay
  checks, 15 server snapshot/research-injection checks, Ruff and diff checks.
  No deployment or new original-client session. Publication helper is missing.

#### Auxiliary subgraph interfaces

- Model BD02E168's enter/exit interface separately from ordinary state hooks.
  Register after insertion into the instance and release on object retirement,
  including never-begun states. Explicit calls share VM budgets and cancellation.
- SceneWorld requires an explicit per-entity registration service or known no
  service. Missing declarations stay strict errors. Ordinary begin/end do not
  invent interface dispatch. Retirement, ID reuse and failure clear stale calls.
- Isolated original x64 callbacks agree on 256 argument-preparation cases with
  explicit node/follow/exit library stubs. Native authoritative trigger timing
  is not derived. Client/library component registration slots are nullsubs;
  this is not silently made the server-host policy.
- Actual 0F45 -> 0EC6 with declared context now begins states0/2 and produces
  five numeric rows, then stops at 582E0D5F/node4. Three registered interfaces
  remain inactive; no interface dispatch was injected into the helper probe.
- Passed 489 runtime/expression/VM/body/bot/entrance/replication/replay checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks.
  Full 100B remains at D38D0035. Complete helper/host behavior, cinematics and
  enemy AI remain unaccepted. No deployment or new original-client session.
  The required publication helper remains missing.

#### Numeric property modifier records

- Implement the numeric component-31 subarray: native f32 priority ordering,
  in-place updates versus reinsertion, exclusive uint32 frame windows and six
  arithmetic operations. Preserve signed-zero and SSE unordered min/max rules.
- Support 316CFEF2's numeric rows on an explicitly declared SceneWorld backend
  and source clock. Scalar values retain native defaults on failure and watch
  their variables. Per-frame records use distinct identities and timed expiry.
  Typed/filter rows and undeclared components remain strict failures.
- Retire rows at cleanup, remove declarations on entity retirement and discard
  numeric effects when strict execution poisons the owning component. Store the
  state's per-frame flag; SceneWorld storage remains unsupported.
- Isolated original x64 emulation agrees on 3,030 arithmetic and 4,900 insertion
  cases. Direct activation of actual 0EC6 state2 resolves its five rows and
  retires them at the supplied frame. Source clocks are explicit, not derived.
- Actual 0F45 -> 0EC6 entry execution next stops at BD02E168/node33 before
  queued states0/2 begin. Full 100B stays at D38D0035. No full helper, mission,
  cinematics or enemy AI acceptance; no candidate deployment or new client run.
- Passed 476 runtime/expression/VM/body/bot/entrance/replication/replay checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks. Property
  names/bases, native clocks, collection, rollback, gameplay consumers and mission
  hosting remain open. The required publication helper is still missing.

#### Registered state lifecycle and successor links

- Implement 9028E035's authoritative per-type component registry, in begin
  order across graph instances. Clean up after class end hooks and before
  authored end plugs; preserve order on removal and clear failed VM membership.
- Native +1C8 names the successor. Skip server-only successors and retain
  explicit links in owner delta and remote full/delta frames using the linked
  graph's raw state/remote indexes and widths. Reject unknown link metadata.
- Read-only VM storage now includes the registry and concrete state type,
  without invoking payload callbacks. SceneWorld storage stays unsupported.
- Actual 0F45 -> 0EC6 retains parameter (17,), constructs state 0 and queues
  its begin. Next is 316CFEF2/node2, before that begin dispatches. Lifecycle and
  codec tests do not establish helper completion or native gameplay acceptance.
- Preserve standalone World.native queries without an Instance; real strict
  instances still reject unknown services. This fixes two existing mode tests
  found by expanding the regression group.
- Passed 462 runtime/expression/VM/body/bot/entrance/replication/replay checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks.
  Full 100B still stops at D38D0035. Prediction/link reconciliation, mission
  host/routing, cinematics and enemy AI remain open. No candidate deployment or
  new original-client session. Publication helper remains missing.

#### Primary possessor entity queries

- Implement DB742151 using an explicitly known SceneWorld possession catalog:
  direct component 44 first, then reverse primary-possession lookup. Return a
  typed invalid Entity for absent results. Keep unknown catalog availability
  distinct and clean both ends of relations before an entity ID is reused.
- Evaluate the input through native scalar +20, including first-config failure.
  Preserve strict failures for unknown services. Correct the swapped entity/AI
  predicate comments in the legacy driver's trusted-query list.
- An authorized original client entered Rialto Story Normal as Reaper, switched
  to Genji, left and closed normally. Read-only component/registry observations
  confirm the controller and old/new body relationships. This researches native
  semantics on the accepted entrance service, not candidate mission behavior.
- Actual 0F45 -> 0EC6 passes the query and stops next at state 9028E035. Actual
  121D node 5 -> 119F resolves its target controller. Context/catalog/handoff
  remain explicit. Full 100B startup still stops at D38D0035.
- Passed 246 related runtime/expression/VM/bot/entrance/replication checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks. Mission
  host, component observers/routing, cinematics and enemy AI remain unaccepted.
  No candidate deployment is claimed. Publication still requires the missing
  work/sync_orpheus_publication.py helper.

#### Native PlayScript request construction

- Collect valid typed target entities in native order, retaining duplicates.
  Skip context/parameter services when targets are empty. Keep caller, owner
  and inherited instigator explicit; distinguish scalar source failure from
  successful null/nonentity values and retain the native caller fallback.
- Collect every request parameter as an array, including one-item and empty
  arrays. Retain ordered duplicate keys, allocation policy and the wire byte.
  World backends must provide context and submit real requests before output
  follows. Scene query bindings alone do not grant routing or transport.
- An authored 0F45 node 48 probe supplies caller variable 10334 = 17, constructs
  parameter (17,), and explicitly hands it to loaded 0EC6. Its start entry writes
  variable 41 = (17,) before stopping at DB742151. This verifies the request/core
  boundary, not automatic context derivation, target routing or running AI.
- Passed 237 related runtime/expression/VM/bot/entrance/replication checks and
  15 server snapshot/research-injection checks, Ruff and Git diff checks. Full
  100B startup remains at D38D0035. Mission host, component lifecycle, context,
  resource readiness, transport, cinematics and enemy AI remain unaccepted.
  No candidate deployment or new original-client acceptance is claimed.
  Publication still requires the missing work/sync_orpheus_publication.py helper.

#### Map entity identity and UUID queries

- Add an isolated SceneWorld query service using native client_id identities
  for mode-selected client placements and explicit existing server-create IDs.
  Keep definition/instance data separate. Bind and retire UUIDs explicitly;
  missing positions and unavailable foreign component variables stop strict
  callers. The service does not activate components or attach itself to Match.
- Preserve native EntityReference no-output failure without confusing it with
  successful null. Keep empty TargetTags typed as Asset(0). All 23 actual
  PlayScript resource configurations now resolve; queued public initialization
  succeeds for 121D (8 assignments), 1412 (8) and 119F (4).
- Native construction and an authorized original-client read-only probe agree
  on 10 waypoint identities. The client entered Story Normal as Reaper, left
  and closed normally. This verifies identities, not candidate mission behavior.
  The selected mode has 2,303 query bindings (2,066 client and 237 server).
- Passed 226 related runtime/expression/VM/bot/entrance/replication checks and
  15 server snapshot/research-injection checks (256.667 seconds), Ruff and Git
  diff checks. Full 100B startup still stops at D38D0035 node 106. Mission
  scheduling, component execution, cinematics and enemy AI remain unaccepted;
  no candidate deployment is claimed. Publication requires the missing
  work/sync_orpheus_publication.py helper.

#### Played resource selection and schema initialization

- Resolve primary/fallback PlayScript resources and ordered inline overrides
  with native low-key map replacement, bucket enumeration and growth. Keep
  empty ordinary graph resources typed. Distinguish collecting array values
  from the resource fallback's first-config evaluation, including failure.
- Queue callee public defaults, then caller-evaluated overrides before new
  instances' ordinary entries; retain reused-instance event order and separate
  start bindings. Failed defaults are skipped; failed inline overrides use int 0.
  Keep world routing, context, resource readiness and wire policy explicit.
- Passed 208 related runtime/expression/VM/alias/bot/entrance/replication checks,
  15 server snapshot/research-injection checks (253.208 seconds), Ruff and Git
  diff checks. 21 of 23 actual resource configurations resolve in isolation;
  two need EntityReference. Actual 121D node 5 -> 119F binds four supplied caller
  variables. World-dependent target schemas remain strict failures.
- Full 100B startup still stops at D38D0035. World instantiation, full story,
  cinematics and enemy AI remain unaccepted. No candidate deployment or new
  original-client acceptance claimed. Publication still needs the missing
  work/sync_orpheus_publication.py helper.

#### Loaded script invocation core

- Add the loaded-graph PlayScript core: skip/create/reuse/replace policies,
  ordered native parameter and source assignments, BB4AC733 start entries and
  one-shot entry suppression. Preserve pending assignments and disabled entries
  in read-only VM storage. Strict PlayScript actions now require an explicit
  world backend before following their output; legacy bodies remain passive.
- Passed 162 related runtime/expression/bot/entrance/replication/VM checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks. The
  authored 0F45 node 48 -> 0EC6 start-entry mapping receives a supplied value in
  variable 41 before stopping at unsupported STU_DB742151.
- World target/resource resolution, source-context derivation, schema overrides,
  asynchronous requests and native deferred deletion remain open. Full 100B
  initialization still stops at STU_D38D0035. No mission deployment or new
  original-client acceptance is claimed; cinematics, enemy AI and full story
  playthrough remain unaccepted. Publication requires the missing helper.

### 2026-11-07

#### Complete map component research inputs

- Add an external map-world exporter that correlates ordered ENTITY/XML inputs,
  exact transforms, immutable STU V1 component type hashes and full definition
  component maps. Retain nested map keys, component-local references and source
  hashes. Keep defaults and placed overrides separate; reject inconsistent or
  incomplete sources and output paths within the repository.
- Rialto exports 2,464 entities, 2,750 matching component instances and 186 full
  definitions with 454 components. The earlier independent transform audit agrees
  with all placed fields. Correct the older AI extractor's DUTS format comment:
  its +4 field is an instance count, not a version number.
- Passed 42 extraction checks, Ruff and Git diff checks. The export is research
  data, not an executable world. Mission services, full startup, cinematics and
  enemy AI remain unaccepted; no deployment or new client acceptance is claimed.
  Publication still requires the missing work/sync_orpheus_publication.py helper.

#### Empty resource evaluation and assignment

- Preserve the observed empty STU_7BD2CA19 evaluator's no-output failure:
  SetVar assigns int 0, while a ForCode read retains its current destination.
  Skip assignment when the SetVar target or value pointer is absent and retain
  its output-plug behavior. Successful null values still assign null. Nonempty
  resource keys and unknown native configs remain unsupported in strict mode.
- Passed 149 related expression/runtime/bot/entrance/replication/snapshot tests,
  Ruff and Git diff checks. The isolated authored 100B node 322 writes integer
  zero; full-root initialization advances to unsupported STU_D38D0035 node 106.
- A separately authorized original client entered Story Normal as Reaper, exposed
  the cached native evaluator for inspection, returned to the difficulty menu and
  closed normally. This researches config behavior on the accepted entrance
  server: the candidate is not deployed, and full story, cinematics and enemy AI
  remain unaccepted. The required publication helper remains unavailable.

#### Native mission value bindings

- Implement the verified zero-sync-flag ForCodeSynced path for strict mission
  execution: first-match live reads, native swap removal, begin/reentry/end
  ordering and immediate/deferred observer notification with callback identity
  deduplication. Preserve binding registries and pending notifications in read-only
  VM snapshots. Legacy player/bot bodies keep their passive default behavior.
- Passed 126 focused runtime/bot/entrance/replication/snapshot tests, 30 server
  snapshot/runtime checks, Ruff and Git diff checks. An isolated probe of authored
  100B node 158/state 49 reads updates and removes its native binding correctly.
- Full initialization remains closed at the next unsupported resource config;
  nonzero ForCode sync flags, the mission host, cinematics and enemy AI remain
  open. No new original-client mission acceptance or deployment is claimed.
  Publication still requires the absent work/sync_orpheus_publication.py helper.

#### Retribution mission dependency and execution diagnostics

- Include placed map script roots, graph order and component overrides in
  optional external graph extraction. Resolve DragonML IDs per component and
  require generated map inputs outside the repository. A local Rialto candidate
  retains 145 script placements and adds their 16-graph closure without new missing
  graphs; it has not replaced the accepted entrance runtime.
- Add optional strict runtime diagnostics for unmodelled nodes/states, missing
  plug/SubScript targets and unsupported base-world queries. Stop queued work on
  failure and preserve the policy/diagnostic in read-only VM snapshots. Existing
  body prediction keeps its permissive default. The real 100B startup now exposes
  its unimplemented ForCodeSynced state at node 158 instead of treating it as a
  generic state. Registered handlers are not proof of complete mission semantics.
- Passed 158 focused extraction/runtime/expression/bot/snapshot/fault tests and Ruff.
  Full story progression, native code bindings, cinematics and enemy AI remain
  open. No new client acceptance or mission deployment is claimed. Required
  publication synchronization remains unavailable while its helper is absent.

#### PVE entrance acceptance and local deployment

- All 47 Archives/Junkenstein cards passed original build-104319 client menu,
  map load, hero selection, actual-world entry and settled-menu return. This
  covers 24 Archives difficulty cards, nine Archives challenges and 14
  Junkenstein difficulty/challenge cards. The final client completed 39 cards
  consecutively and closed normally without reproducing earlier cleanup crashes.
- Tutorial passed five consecutive fixed-Soldier entry/return cycles, followed
  by Practice Range and normal closure. Both entrances also passed after local
  deployment, alongside Retribution All Heroes Normal in the same client.
- Promoted the two accepted external map/graph inputs with hashes and backups,
  restarted the normal local service, and enabled Archives and Halloween for
  existing profiles and the new-account template. The usual local launcher
  logged in and completed all three deployment smoke checks; the service remains
  available after normal client closure. Game assets and detailed evidence stay local.
- The implementation's 141 focused tests, Ruff and 47 independent offline frame
  checks passed. This acceptance covers entrances only: mission AI, objectives,
  challenge effects, Tutorial guidance and story hero restrictions remain open.
  Long-term stability and the complete causes of earlier crashes are unproven.
  GitHub synchronization remains blocked by the absent required publication helper.

#### Tutorial entry and world delivery on leave

- Route the native Training > Tutorial request to its authored solo red team,
  Tutorial map and fixed Soldier: 76 controller. Wait for controller creation ACK
  before body creation and possession; keep the shared hero picker closed.
- Stop sending queued, in-flight or newly produced world records once a client
  leaves. Preserve reliable message ordinals, retransmissions and packet ACKs,
  including when the leave packet is lost. Clock draining uses the same cleanup.
- Passed 141 focused tests, Ruff and Git diff checks. All 47 PVE card/map loading
  and controller frames still passed independent offline decoding.
- Eight activity cards and Practice Range have original-client entry/return
  evidence. The current Tutorial candidate completed five consecutive world and
  menu cycles in one original-client process, followed by Practice Range and
  normal client closure. Earlier candidates had intermittent cleanup crashes;
  this observation does not establish their complete cause or wider stability.
- The remaining 39 activity cards and normal-service deployment remain open.
  Tutorial guidance, mission AI and objective execution are outside this entry
  acceptance. Publication remains unavailable because the required
  work/sync_orpheus_publication.py helper is absent; assets and evidence stay local.

#### PVE controller and loading states

- Preserve the mode/team script associations in external graph bundles. Replicate
  Archives and Junkenstein controller wrappers with their authored SubScript
  parents, and keep the shared hero-selection assembly countdown.
- Recognize build 104319's native scripted-loading readiness state on the real
  mode graph. Support map-scoped configured mission graphs while retaining the
  primary mode root; unavailable or ambiguous readiness data stays closed.
- Passed 69 focused tests, Ruff and Git diff checks. Separate local inputs now
  contain 619 graphs, the Storm Rising mission binding and authored, ground-checked
  entrance markers on four PVE maps. All 47 card/map choices and their loading
  frames passed offline selection and independent decoding.
- In the separately authorized original-client session, Retribution All Heroes
  Normal and Legendary and Storm Rising All Heroes Normal entered their actual
  starting rooms with Soldier: 76 and returned to the difficulty menu. Junkenstein
  Normal entered the world but crashed after leaving; its return remains open.
  Other cards, Tutorial, Practice Range regression checks and normal-service
  deployment remain pending. Mission AI and objective execution are not covered.
- Publication was not run because the required work/sync_orpheus_publication.py
  helper is absent. Generated assets, captures, logs and runtime inputs stay outside Git.

#### PVE entry preparation

- Allow simultaneous, validated Archives and Halloween event groups so their 47 PVE difficulty and challenge cards can be offered together. Keep event updates atomic and deduplicate repeated groups.
- Include mode, controller and body script roots in the optional 0C5 graph export; retain missing graphs explicitly. Bound plug ownership traversal through non-node reference cycles without dropping alias paths.
- Passed 67 targeted tests (including an external-runtime event fixture), Ruff and Git diff checks. Local research prepared 581 graphs and authored spawn candidates for three previously spawnless PVE maps. These are prerequisites: scripted loading, deployment and original-client entry across all 47 cards remain unverified. No client was started or controlled.
- Publication was not run: this checkout lacks the AGENTS.md-required work/sync_orpheus_publication.py helper. Research assets and candidate runtime inputs remain outside the repository.

### 2026-11-05

#### Project setup

- Named the project ORPHEUS. Added architecture, local startup, and development instructions, plus persistent rules for Conventional Commits, review, and GitHub synchronization.
- Created `leafall903/ORPHEUS` with `main` as its default branch. The original local Git history retains the research material for development.

#### Baseline

- Integrated local login, lobby, match handoff, collision movement, statescript and encrypted UDP replication for build 104319.
- Added controlled input and packet replay, registered server state comparisons, and collision data readiness. Earlier offline real Practice Range evidence covers movement, bot damage, elimination, and respawn.
- Fixed bot creation ordering and latest health delivery after ACK, terminal script recovery on deliberate same-hero reselect, and the repeated self-reference of a Stack modifier.

#### Verification and acceptance

- The baseline at `5ce3414` passed 1086 Python tests. Ruff and Git diff checks passed.
- The manual test of the earlier `aeb2d57` copy still had gaps in animation and HUD, target feedback, names, kill feed, audio, void death, and hero skills. Complete Soldier: 76 and Practice Range client acceptance remains pending.

#### Fixed

- Retired a player's old health, hit history and respawn timer when the body is replaced or the player leaves. A second body now waits for its own death timer, even if an earlier body was replaced before its timer expired.
- Passed 55 targeted Python tests covering the two-death regression, body recovery, repeated switches and reentry, encrypted server replay, and bot notices. Ruff and Git diff checks passed. Client acceptance for the change is pending.

#### Repository checks

- Kept the CI relay compilation check and removed its artifact upload. Verified the workflow diff and checked that the build and regression steps remain in the workflow.

#### Combat fixes

- Swept the final fraction of projectile flight before expiry. Soldier: 76's Helix Rockets now detect impacts in the final part of their configured range and report the time they actually flew.
- Used the volley timestamp for hitscan notice attribution. Shots caught up after missing input report their own frames. Their aim, position, and target rewind still use the available command sample.
- Passed 36 targeted projectile, combat, command epoch, and timing tests. Client acceptance for the changes is pending.

#### Standalone server

- Added an external runtime directory for protocol tables, graph and map data, the lobby signing input, Battle.net descriptors, and prepared collision. The preparation tool copies the required files from a local research checkout and writes a SHA-256 manifest.
- Made server-only modes load collision caches without searching for a client installation. Startup reports missing runtime files before binding, and the map loader reports missing collision.
- Kept upstream notices and references, added the project contributor list, and documented the separate publication history and GitHub synchronization workflow.
- Passed 69 targeted Python tests, including lobby wire vectors and collision readiness, plus Ruff and Git diff checks. An isolated Windows deployment completed a signed lobby handshake and served the Battle.net WebSocket, dashboard, and UDP match. The Practice Range loaded its prepared collision and spawned 15 bots. Manual client acceptance is pending.
- Passed all 1091 Python tests for the standalone server change in the prepared development checkout.

#### GitHub synchronization

- Published the audited standalone server source on GitHub `main`. Verified the public branch head, the complete reachable object audit, and the clean checkout startup with external runtime data.
- Rehearsed the local synchronization command through history export, strict audit, fast-forward verification, and isolated startup. Documented the command for future tested commits.
- Configured the repository to credit new commits to `leafall903` using the GitHub noreply identity. Codex assistance is recorded in the `Assisted-by` trailer.

#### Biotic Field

- Replicated Soldier: 76's field skin theme to the caster and observers, including the Grillmaster theme. Shared the normal entity creation and movement encoder for the parented and world-space field records. The default field records retain their original wire bytes.
- Passed 72 targeted tests covering the theme, the caster's canister binding, observer placement, healing, expiry, and server replay. Ruff and Git diff checks passed. Client rendering and target-build canister placement are pending acceptance.

#### Statescript vectors

- Preserved the XYZ coordinates of vector constants in the build 104319 graph export. Biotic Field's launch and target-centre offsets now evaluate to their configured values in the shared interpreter. Lowercase coordinates remain supported.
- Passed 83 tests for expressions, abilities, body replication and graph extraction. Live behavior and the Biotic Field launch and collision path remain pending.

#### Match handoff

- Rejected UDP handshakes for expired or cancelled match seats immediately, even when the scheduler receives a packet before the tick that releases the seat. A valid handshake can still claim its seat until the deadline.
- Passed 93 tests covering the production scheduling loop, encrypted handoff, cancellation replay, matchmaking, and leaving. Broader scheduler replay and manual client acceptance remain pending.

#### Target list replication

- Encoded ordered entity and age entries supplied to the shared TrackTargets owner payload. Empty lists keep their existing wire representation.
- Passed 66 tests covering the client-derived codec, a Tactical Visor owner delta, body replication and server replay. Target selection, age calculation, authoritative aim and client acceptance remain pending.

#### Scheduler replay

- Ran finite UDP datagrams, timeouts and receive errors through the production game scheduler in memory. Replay checks clock reads, skipped ticks, socket calls, encrypted sends and ordered join and leave callbacks.
- Exercised controlled callback reentry using the existing seat cancellation and player removal methods. Invalid scripts and scheduler or callback failures reject the journal. Stored-state mode compares stopped boundaries around the loop.
- Passed 53 primary tests and the isolated snapshot regressions. A real 0688 Practice Range replay matched 74 receives and 26 encrypted sends, including Soldier: 76 spawn and a callback-driven seat cancellation. Live thread interleavings and manual client acceptance remain pending.

#### Collision fingerprint validation

- Compared derived collision cache entries directly before encoding the geometry fingerprint. The comparison retains exact types, finite numbers and signed zero checks.
- Passed 29 final collision fingerprint and tape tests. Ten A/B samples on a warmed 0688 world produced identical descriptors, with approximately 21% less median CPU time for the diagnostic fingerprint. Client acceptance remains pending.

#### Practice Range HUD initialization

- Enabled the initialized shared-variable state in the Practice Range HUD graph `20E4`, matching its entry in the 104319 graph. The owner frame size and PvP reference packet are unchanged.
- Passed 91 tests covering owner-frame decoding, selection, body replication, abilities and notices. Client HUD color, sprint animation and the full sprint presentation remain pending.

#### Predicted projectile timing

- Assigned predicted projectile leases and shot records to the statescript volley frame. A Biotic Field cast that finishes during a missed command window now confirms the scheduled canister ID when input resumes.
- Passed 38 projectile, combat, hitscan and ability regressions. The catch-up path still uses the available aiming and position sample. Canister placement and client prediction remain pending acceptance.

#### Match mode HUD initialization

- Started `0CB6 st21` in the match mode's initial and countdown frames, matching the shared-variable entry in the 104319 graph. Existing countdown values still decode from those frames.
- Passed 90 primary regressions and decoded the active mode HUD state from an encrypted 0688 Practice Range frame. A separate real-cache test decoded 47 controller full frames across 23 Soldier: 76 body lifecycles, including ten hero round trips and ten rejoins. HUD rendering and notices remain pending client acceptance.

#### Practice Range bot notice delivery

- Kept the old bot entity available to a connection until its combat notices, any earlier reliable messages, and its last health update are acknowledged. This covers a lost kill datagram, a gap before acknowledged notices, and terminal health sent in a separate datagram. The replacement bot can spawn independently.

- Bounded the old entity's lifetime by the existing transport silence budget of 625 server ticks. Passed 90 tests covering encrypted bot delivery, packet budgets, lifecycle changes, journal replay, and state snapshots. Ruff and Git diff checks passed. Client hit markers, overhead health bars, and elimination names remain pending acceptance.

#### Project-wide restoration checklist

- Reorganized the README around build-wide goals for heroes, maps, modes, online features, presentation, audio, and deployment. Listed completed server foundations separately within the open milestones and described the current Practice Range validation group.

- Added a development rule to mark milestones complete once their evidence and required client acceptance are recorded. Checked README links, checklist structure, and the Git diff.

#### Early player death events

- Marked recent client-only pulses after the first instance frame as fresh. The player death graph now sends the first pulse to the owner and observers when death occurs within the first 500 ms. The first-frame Tracer pulse and the expiry window retain their tested behavior.

- Passed 72 targeted tests covering pulse encoding, runtime, codecs, snapshots, and body replication. Ruff and Git diff checks passed. The original server's freshness policy and client kill feed rendering remain unverified.

#### Kill feed attribution

- Applied instance-variable exclusions from each script's own graph in owner and observer frames. This preserves the killer and critical-hit fields for the player death graph `01CF` when unrelated scripts exclude variables with the same IDs. The existing entity-level filtering stays in place.

- Passed 84 focused tests and decoded startup frames for all 32 hero definitions. Decoded the player death event with killer, critical flag, loadout, and an empty assist list. Also checked that a recreated Reaper child script keeps its own exclusions. The real-cache Practice Range replay passed three lifecycle and ammo checks. Assist attribution, the bot death path, and original-client rendering remain pending.

#### Additional entity graph extraction

- Added optional entity roots to the existing statescript extraction tool. Additional definitions and their referenced graphs go into a separate local bundle using the same parser as the hero graphs.

- Passed 15 extraction and graph analysis tests. A build 104319 extraction included eight additional entity definitions and 13 new graph records, with identical hero definitions and shared graphs. An isolated VM probe encoded and decoded the death pulse and attribution for six bot body definitions. Bot runtime activation and the client name lookup remain pending.

#### Training bot spawn and death statescript

- Loaded the optional bot definitions from the prepared graph bundle. The authoritative death stimulus now reaches the bot's own graph, including `0C18`, and shares the existing body frame encoder. A death in the same world tick as creation runs after the script's initial frame so its pulse can be delivered.

- Sent each connection the initial script after its entity create is acknowledged, then a resendable terminal frame after the initial script is acknowledged. Shooter and observers receive their own delivery sequence. Replacement bots can spawn while the old entity waits for acknowledgement, with the existing 625-tick retirement limit.

- Prepared initial bot scripts one per ready loading tick. Added a runtime preparation option for the separate local graph bundle.

- Passed 110 final targeted regressions, Ruff, and Git diff checks. With the original 0688 collision cache and extended 104319 graph data, two encrypted peers decoded identical death attribution, and a separate battle replay matched 101 controlled steps and 18 sends through a kill and respawn. The stored-state probe verified pending death delivery. Original-client name lookup, kill feed, and audio rendering remain pending.

#### Command clock handoff

- Applied each admissible command in order through the last frame whose two projectile lease windows fit in the wire format. At a fresh command beyond `FF7FFFFF`, or a forward serial wrap, the old link now uses the existing reliable leave flow. ACKs and FIN remain available during the leave grace period.

- Used the initialized body frame to filter stale or ambiguous first packets when the input queue has no frame yet. This keeps movement and the body script on the same input history.

- Passed the 99-test command and handoff group and the 117-test simulation group. All three encrypted handoff cases also passed with the original 0688 collision cache and extended 104319 graph bundle. A controlled replay of an unsafe first input before hero selection matched all nine sends. Ruff and Git diff checks passed. Original-client clock re-anchoring and the independent server-wide tick boundary remain unverified.

#### Shared game message recipients

- Routed each valid entity in a resolved target list through the existing local or world message path in list order. Single-target messages and the implicit self target retain their tested behavior.

- Passed 163 focused tests, including the `00BA` and `00BB` graph actions, script runtime, snapshots and replay. With the original 0688 collision cache, hero switches, rejoins and ammo checks passed, and a separate battle replay matched 101 steps through a bot kill and respawn. Production recipient selection and cross-entity delivery remain pending.

#### Global server clock rollover

- Checked the candidate server frame, including skipped ticks, in the common scheduler and replay entry point. Before the global clock reaches the projectile lease boundary, the server freezes old matches and drains their links within a fixed frame budget.

- Kept the original reliable message order, including leave retransmissions, while dropping stale world records from old links. Reserved handoffs can still authenticate and leave through the same protocol. Once those links, reservations and lobby callbacks finish, the next server clock starts from its initial frame with fresh handoffs.

- Kept waiting queue tickets and live Practice Range requests during the admission pause. Reopening retries the waiting rosters and Practice Range requests. A delayed Game Found callback checks that its reservation is still valid.

- Passed 158 targeted tests. The original 0688 collision and extended 104319 graph pack passed the encrypted mixed active and pending rollover, ten hero round trips, ten rejoins, and a 101-step combat replay. Ruff and Git diff checks passed. Original-client clock re-anchoring and the menu experience remain pending acceptance.

#### Timed bot death statescripts

- Advanced due events in the dead bot's original statescript up to its respawn deadline. The immediate `0C18` attribution and the `0C17` pulse from the graph's 50 ms wait each retain their own frame, including when the server skips ticks.
- Sent each delta after the receiver acknowledged its preceding frame. Lost frames can be resent in order, the old body remains available while delivery is pending, and late observers receive only the baseline for that death.
- Passed 82 focused tests, Ruff, and Git diff checks. The original 0688 collision cache and extended 104319 graphs passed the two-peer encrypted death sequence, a stored-state probe with two pending death frames, three Practice Range lifecycle and ammo tests, and the 101-step combat replay.
- The production audience query, victim name lookup, and original-client kill feed and audio remain pending. The audio listener list was verified on the wire with a controlled input.

#### Player death with idle input

- Dispatched the authoritative player death event as soon as combat registers it. Observers now receive the kill feed pulse and its attribution even when the victim stops sending commands. The victim's command frame stays at the last processed input.
- Passed 89 focused tests, Ruff, and Git diff checks. In an encrypted test using the original 0688 collision cache, the observer decoded the death pulse, killer, critical flag, and loadout at the next send while the victim sent ACKs only. The three Practice Range lifecycle and ammo tests and the timed bot death wire probe also passed.
- Original-client presentation, bot name lookup, and native multikill event production and eligibility remain pending.

#### Directed messages between player bodies

- Routed explicitly addressed statescript messages between current player bodies in the same match. Dispatch uses a bounded FIFO before observer snapshots, preserves the recipient's command clock, and drops entries whose body lifetime ended. Messages emitted during dispatch enter the next batch.
- Verified that killing a Soldier: 76 who is using Tactical Visor delivers `064A.025` to the killer's `0C7D` instance, including the numeric and text-reference fields. A retired body or a body in a different match cannot receive the queued message.
- Passed 119 gameplay and transport tests, 55 storage and replay tests, Ruff, and Git diff checks. An encrypted rifle kill replay matched 55 roots and eight sends. A separate 0688 probe with the extended build 104319 graph bundle verified that pending deliveries affect the registered match state.
- Native scoring, Play of the Game, and original-client presentation remain pending. NPC, controller and mode entity recipients still require their own lifecycle and query evidence.

#### Score event data extraction

- Added a local export for the target build's `003F.054` event labels and score scalers. It reuses the DragonML reader, preserves ordered aliases and resource references, and records the source hash. The generated catalog is kept outside the repository.
- Verified that the existing `064A.025` receiver's `2041.01C` event resolves to the localized Ultimate Shutdown label. The original client graph has a separate award presentation entry. Its transport binding, natural On Fire thresholds, decay and credit rules remain pending.
- Passed 97 focused tests, Ruff, and Git diff checks. Two exports of the original build 104319 file produced identical bytes with 23 labels and 20 scalers. The existing scoreboard export also passed a separate CLI fixture.

#### Initial mode entity variables

- Added an optional local graph bundle section for the typed initial variables in build 104319 mode assets. Statescripts can read the explicit values from the mode entity by its GUID, independently of schema defaults. Bundles without the section retain their previous lookup behavior.
- Verified 25 mode records and 21 explicit values. With original Hanamura and Practice Range collision data, the isolated `0BA4` objective branch counted two events in modes `0014` and `0067`. The same branch saw an unset member in mode `0018`. Normal objective script activation, mode entity replication, and client acceptance remain pending.
- Passed the same 157 focused tests with each bundle format, plus Ruff and Git diff checks. The new bundle passed 800 hero and mode VM boot and input checks, three original Practice Range lifecycle and ammo checks, and the encrypted bot death sequence. Synthetic bot fixtures are isolated from optional real NPC data.

#### Scripted bot health and Biotic Field

- Synchronized nonlethal damage and healing to the shared body health input for players and scripted bots. Kept the NPC death script's ordering for the terminal health change and death event.
- Fixed a failure that dropped Biotic Field from server tracking on its first heal of a scripted friendly bot. In the original 0688 map, an encrypted cast healed two injured bots from 205 to 225 health each. The encrypted stream contained both final health values and both destruction records from normal field expiry.
- Passed 101 focused tests with the extended 104319 runtime and repeated the two-peer bot death sequence. Ruff and Git diff checks passed. Original-client field effects and overhead health bars remain pending acceptance.

#### Bot movement after creation

- Wait for each connection to acknowledge a bot's current create before sending its movement. Initial spawns and respawns use the same acknowledgement as bot health and notices. The existing movement cadence applies once the create is acknowledged.
- Passed 149 tests with the default data and 130 with the extended 104319 runtime. On the original 0688 map, a simulated observer lost four respawn creates and received movement after acknowledging the retry. A second observer received movement while the first waited. The three Practice Range lifecycle and ammo checks, the timed bot death wire probe, Ruff and Git diff checks also passed.
- Original-client movement, name lookup, and audio remain pending acceptance.

#### Terminal bot health before respawn

- Queue dirty health before processing respawns so a tick that skips past the respawn deadline still sends the old bot's zero health. When a connection has acknowledged the old bot's create, the server waits for pending health before its destroy, within the existing retirement grace. This also covers bystanders without a kill notice or an NPC statescript. The replacement create is queued on schedule.
- Passed 148 tests with the default data and 129 with the extended 104319 runtime, plus Ruff and Git diff checks. On original 0688 collision, two encrypted peers received zero health after a 194-tick skip. The passive peer lost the first terminal health packet, received the retry and timed death frames, then received the old body's destroy. The Practice Range ammo, lifecycle, Biotic Field and bot movement replay checks also passed.
- Original-client death presentation, overhead health bars and hit audio remain pending acceptance.

#### Map AI placement graph extraction

- Added `--ai-spawners` to include `.01B` graph roots from a local map spawner table in an external graph bundle. The exporter follows each root's references through the existing graph traversal and reports missing assets. Without the option it keeps the existing root set.
- Extracted 19 additional graphs from the original Practice Range and Tutorial placement data, including `0B8E` patrol and `0B92` firing. All 386 earlier graph records stayed equal, both exports produced identical bytes, and a new runtime directory loaded all 19 roots. The existing missing `0F9C` reference remains reported.
- Passed 139 tests with each data pack, Ruff and Git diff checks. Original 0688 ammo, lifecycle and terminal-health replay checks passed, as did the 800 hero and mode VM controls. Production AI graph execution, bot projectile replication and original-client behavior remain pending.

#### Statescript random float ranges

- Evaluate `STU_BE0BD267` from its float32 bounds using the component's existing seeded random stream. Wait states now schedule the sampled duration.
- Verified 126 ranges from 68 build 104319 graphs. With controlled selection of the original `0B92` behavior, six phases alternated within 1.75 to 2.5 seconds. The `0013` reset branch resumed eligibility after 2.75 seconds, and the fire state then followed the selected firing phases. The previous evaluator exhausted the event budget at one timestamp.
- Passed all 1,170 Python tests with the default data and 176 focused tests with the AI-roots runtime. Ruff, Git diff checks, the 800 hero and mode VM controls, and the original 0688 ammo and lifecycle tests also passed. Retail random sequences, production AI selection and projectile replication, and original-client acceptance remain pending.

#### Observer projectile delivery

- Send the observer's projectile creation before its ch2 launch event. Wait for positive acknowledgements of both the creation and the matching volley state in the shooter's current body stream. Retries use the original launch frame and projectile ID.
- Hold an ordinary explosion release until the launch is acknowledged, with up to 64 server ticks of grace after flight ends. A short flight can finish before the first send and still arrive in order. On timeout or shooter body replacement, clean up the projectile and cancel stale launch retries.
- Verified the original 0688 collision cache with two encrypted peers. After independent loss of the first create and the first launch, the observer received the create, launch, and release in order. A replay matched 305 controlled roots and 68 encrypted sends, and a stored state export included the pending delivery. Original-client projectile effects and the NPC firing path remain pending.
- Passed 125 default-data tests including stored snapshots, 123 tests with the AI-roots runtime, and the 15-test projectile group with both packs after adding the body replacement case. The original Practice Range ammo, lifecycle and Biotic Field probes, Ruff and Git diff checks also passed.

#### NPC weapon bodies and projectiles

- Advance the weapon body on the server clock for AI placements with the firing graph. Consume its weapon volleys through the shared collision and damage path. Use the bot's current lifetime entity for damage and death attribution.
- Replicate live NPC weapon state on the existing body stream before the projectile launch. Wait for the matching state and projectile creation to be acknowledged, retain the newest waiting live snapshot, and deliver terminal frames in order. NPC projectiles share the observer encoder and use server entity IDs.
- In the original 0688 map, a controlled logical weapon input fired the configured `1476` projectile through the production combat tick. Two encrypted peers verified 19 direct damage, lethal attribution to the firing bot, and ordered creation, launch and destruction after deliberate loss. Passed 168 tests with default data and 155 with the AI-roots runtime. The original Practice Range ammo, hero switching, rejoin, Helix, timed and skipped bot death, and Biotic Field probes also passed, along with Ruff and Git diff checks.
- The test supplies the weapon input. Native AI selection and input production, animated muzzle position, projectile impact effects, NPC name lookup and original-client acceptance remain pending. Projectile origin currently uses the server body pose.

#### NPC projectile delivery during body setup

- Keep an NPC projectile for a registered observer while the source body's initial state is still in flight. Queue the projectile create after the source baseline is acknowledged, then wait for the projectile create and exact volley acknowledgements before sending its launch.
- Discard a waiting projectile when the source lifetime retires or its delivery deadline expires. A replacement that uses the same entity ID cannot receive the previous body's launch.
- Passed 18 projectile tests with default data and with the build 104319 runtime. Encrypted regressions cover a lost source create, a lost volley delta, same-ID replacement, and expiry. The original 0688 damage and attribution replay also passed. Original-client projectile presentation remains pending.

#### Authored AI weapon input

- Extract logical-button bindings from the associated AI weapon control definition into the external graph bundle. Resolve the slot and button from the authored values and reject ambiguous duplicate slots. The six original bot body records resolve slot 0 to logical button 20. Two independent exports were byte-identical and retained the previous 405 graphs and eight entities.
- Route the self-targeted `0B92` fire action through the existing logical-button queue. Its state starts the hold at the VM event time and releases it on completion or abort. Bundles without the binding keep their previous behavior.
- On original 0688 collision, all three firing placements reached `0BA3` and launched `1476` after a controlled selection and reset. The first firing phase lasted 1,941 ms. Cancelling the next phase released the input, which remained released for 200 frames. Verified stored state export, the original Soldier ammo, switch and rejoin probes, and the encrypted NPC and Helix projectile replays.
- Passed 223 tests with the extended build 104319 runtime, including graph extraction, VM storage, and encrypted server replay. Ruff and Git diff checks passed.
- The control supplies the selection and `0013` event. Normal map activation, the native reset producer, and original-client acceptance remain pending.

#### Automatic Practice Range robot fire

- Start the original `0B92` graph once per lifetime at the authored Practice Range firing placements when the external graph and weapon binding are available. The server selects the single placement behavior on entry and sends one `0013` reset to that instance at creation. Other body graphs keep their existing initialization.
- Advance the original wait and weapon states on the shared clock, seeded per entity. Replicate the weapon state and projectiles while keeping the private placement graph in server storage. Retire the behavior, held inputs, and pending shots before running death callbacks. A respawn initializes a fresh graph under the new entity ID.
- In a headless original 0688 match, all three firing robots used the encrypted projectile path without injected input. A robot killed during a firing phase stopped launching and its replacement resumed after initialization. In a 64-second idle-match run, the three robots each launched at least 103 projectiles. The Soldier: 76 ammo, switch and rejoin probes, bot death and Biotic Field probes, and the Helix replay also passed.
- Passed 184 default-data tests and 216 with the build 104319 runtime. The 11-test bot statescript group, including the added same-frame death regression, passed with both packs. Ruff and Git diff checks passed.
- The spawn reset and single-candidate selection use a server reconstruction policy. The exact original scheduling, reset producer, animated muzzle and aim, displaced return movement, and original-client presentation remain pending.

#### Projectile travel on skipped ticks

- Sweep a projectile through the elapsed server ticks up to its remaining flight bound. A repeated world tick no longer moves it twice. A projectile received from player input starts with one simulation step when the scheduler first advances it.
- In the original 0688 map, an automatically fired robot projectile hit the same stationary target after about 0.129 seconds of flight and dealt 19 damage with either 13 regular ticks or one update after 12 skipped ticks. The same sweep checks static collision and the current target capsules. Historic moving targets and skipped AI input transitions remain pending.
- Passed 91 focused tests with each data pack, plus the original 0688 automatic firing lifecycle and encrypted Helix replay. Ruff and Git diff checks passed. Original-client projectile effects remain pending acceptance.

#### NPC weapon simulation after skipped ticks

- Replay up to the latest 64 combat frames when a live NPC weapon body is active. Sweep admitted projectiles before advancing the NPC weapon graphs in each frame, and consume volley reports before their state advances again. A player projectile accepted just before the scheduler resumes still takes its first step at the current frame.
- Use the simulated collision frame for NPC death. On original 0688, regular and skipped runs produced identical launch frames, volley counters, configured muzzle sequence, and 57 damage to a stationary target. A second run matched an NPC shot at frame 1455, its death at 1465, and cessation of later shots.
- Passed 107 focused tests with each data pack, Ruff and Git diff checks. The replay uses the latest poses and caps long gaps. Delivery of intermediate launches to a backlogged observer and original-client presentation remain pending.

#### NPC launch delivery through an observer backlog

- Retain the weapon activation required by each NPC launch while the observer catches up. Wait for the projectile create and matching activation acknowledgements, then wait for the launch acknowledgement before installing a conflicting activation. Other live state keeps its newest values. Expiry and source retirement release the dependency.
- In an encrypted original 0688 replay, one scheduler jump queued four shots at frames 1436, 1455, 1474, and 1493. The previous observer path received the first and fourth. All four now arrive in order with their matching activations. Verified a server state export while the launches were pending and an encrypted regression with deliberate loss of the first launch.
- Passed 207 focused tests with each data pack, Ruff and Git diff checks, and the original 0688 damage, NPC death, and player Helix replay controls.
- Original-client projectile presentation remains pending acceptance.

#### Soldier Sprint across missing commands

- Advance the body statescript after each reconstructed movement frame in the mover's existing bounded command window. Both use the same continuous input and subsequent neutral frames. Synthetic frames omit the previous command's one-shot action and refresh movement modifiers for the next frame.
- On the original 0688 map, a 40-command gap matched the explicit fallback sequence in position, velocity, and correction frame. The encrypted owner stream cleared Sprint variable 699 in both runs. Retrying the received frame left movement unchanged.
- Passed 199 default-data tests and 198 tests with the build 104319 pack, which skipped one King's Row test because that cache was absent. Ruff and Git diff checks passed. Long-gap policy and original-client animation and HUD appearance remain pending acceptance.

#### Command acknowledgements before the first movement correction

- Keep the owner's replay input while a Soldier body create or its first movement correction is awaiting delivery. When no correction has been sent, cap command ACK at two frames after the newest eligible movement snapshot, or after the packet tick if there is no eligible history. Selected and previously sent corrections retain their existing limit.
- In an encrypted original 0688 match, the first datagram's budget excluded the correction. Its ACK stayed at 1089. The next datagram carried the correction for frame 1090 and ACK 1092, retaining the inputs the client needs to replay from 1091.
- Passed 211 focused tests with each data pack, Ruff and Git diff checks. The original 0688 Sprint, ammo, ten hero switches and ten rejoin controls also passed.
- Original-client correction and replay remain pending acceptance.

#### Combat across reconstructed input frames

- Resolve weapon and melee reports after each reconstructed movement frame, using its pose and the mover's existing fallback input. Repeated controls retain the last sampled ordinary aim and view delay. One-shot actions and click-only aim stay on the received command. Newly admitted player projectiles still advance with the world tick.
- In an encrypted original 0688 replay, losing 22 held-fire frames and turning away at the endpoint now produces the same four 20-damage hits as contiguous input, at frames 1053, 1059, 1066, and 1073. Both paths leave 26 rounds in the owner stream. Regression coverage includes a genuine click aim, retransmissions, and a Biotic Field cast timer expiring in the gap.
- Passed 242 tests with each data pack, Ruff and Git diff checks. The original 0688 ammo, switch, rejoin, Sprint, Helix and Biotic Field controls also passed.
- The comparison covers a stationary target and the mover's bounded reconstruction window. Longer gaps and original-client feedback remain pending acceptance.

#### Scheduled reports for every weapon round

- Use the weapon's rounded shot time to decide when to report a round, matching the timer that ends the volley. The ammunition output retains its existing calculation. A round due at the finish time is reported once.
- Verified Soldier: 76's full 30-round magazine through the original 0688 collision and encrypted owner stream. The controlled target received thirty 20-damage hits at the authored frames, including the last round at frame 1254. Server and owner ammo both reached zero.
- Passed 243 tests with each data pack, Ruff and Git diff checks. The original 0688 command-gap, Sprint, lifecycle, Helix, Biotic Field and automatic NPC firing controls also passed.
- Original-client hit markers, impact effects and audio remain pending acceptance.

#### Combat notices after body creation

- Keep hit and elimination notices behind the recipient's acknowledgement of the target body. A death notice with an attacker waits for that attacker's body. Players and NPCs share the same delivery path.
- Queue the current visible player's body if combat references it before the next match update. Keep the old body available while its already queued notices wait for reliable delivery, up to the existing retirement deadline. A late acknowledgement for an old creation cannot release a new lifetime's queued notices.
- Passed 173 tests with each data pack, including encrypted replay and stored state checks. In the original 0688 collision scene, two encrypted peers lost their first remote creates and received each notice after the corresponding acknowledgement. Regressions also cover a hit immediately after a hero switch and a lost notice that delays destruction of an old body. Ruff and Git diff checks passed.
- The original localization data resolves the Training Bot and Friendly Bot labels in 13 languages. Those bodies share a voice set. The client-side entity name lookup, combat presentation and audio remain pending.
- A recycled body ID can still conflict with a notice that already entered the reliable stream. The generation check covers notices waiting on creation.

#### Owner ammunition when fire restarts

- Send the new volley’s starting ammunition when its activation differs from the last acknowledged volley. The client reads that starting value when it starts firing. Later frames for the same activation carry the remaining ammunition.
- In an encrypted replay on the original Practice Range collision, the owner acknowledged the first burst, lost the stop packet, and received the second burst with 29 starting rounds. The previous delta gave it 28. The regression also checks that the next frame reports 28 without starting another volley.
- Passed 83 tests with each data pack, including body frame decoding, abilities, combat, projectiles and repeated lifecycles. Original-client prediction, animation and the reported trigger behavior remain pending acceptance.

#### Training bot nameplate graph

- Resolve the original self-filter used by the four stock Practice Range robot definitions so their existing body graph mounts the original nameplate child. Keep the authored child arguments. The filter exception applies to this Practice Range call site. The full native filter semantics remain unknown.
- Two encrypted peers on the original 0688 collision received one nameplate child after creation for each of the 15 initial bots, and one for the replacement after respawn. Tests cover the alternate nameplate path, another mode and other filter arguments. Passed 44 tests with each data pack and the repository lint and diff checks.
- Original-client overhead health and name rendering, elimination name lookup, kill feed and audio remain pending acceptance.

#### Entity lifecycle and missing-input test controls

- Give the transport fixture an empty match roster and verify that the rifle stops at the first neutral fallback frame. The test also verifies the restart delay and that late input does not duplicate the intervening hits. Passed 11 tests with each data pack.

#### Predicted projectile confirmation before release

- Wait for the owner to acknowledge each projectile create before sending its explosion release. The server retries lost creates and keeps a 64-tick delivery window after the release becomes due. If simulation finishes after the input-frame deadline, the window starts at the completion update.
- At expiry, remove unconfirmed entities without an explosion. Replacing the shooter's body also retires its pending creates. The existing projectile lease selection remains in place.
- Passed 48 projectile and combat tests. On original 0688 collision, the first owner Helix create was lost at frame 1104, its retry arrived at 1110, and the release followed at 1113. The pending confirmation was stored in a server snapshot, and replay matched 231 input events and 39 sends. Original-client right-click presentation remains pending.

#### Public collaboration

- Document the supported build, known limits and evidence behind the current server behavior. Publish the M0–M6 roadmap and explain how developers, researchers and players can contribute.
- Add GitHub forms for bugs, feature requests and gameplay verification, plus a pull request checklist for testing, redaction and AI-assisted changes. Preserve contributor authorship through the audited publication workflow.
- Add a public source workflow for Windows, Python 3.12 and Node.js 24. The current source-only selection runs Ruff, 51 Python tests and two JavaScript tests without runtime data. Prepared-data regression and original-client acceptance remain separate.
- Resolve the graph export test's temporary repository path to match the real CLI, including on Windows runners that supply an 8.3 short path.

#### Player body ID retirement

- Reuse a player body ID only after each connected peer that saw the old body acknowledges its destruction, the 30-second retirement interval elapses, and old reliable combat notices clear the delivery queue. Measure the interval on the monotonic clock so missed simulation ticks cannot shorten it.
- If all 255 body IDs are occupied or reserved, keep the current body. Automatic respawn retries until an ID becomes available.
- Passed 73 tests with each data pack, including four encrypted regressions for delayed notices, lost destroy acknowledgements, simulation catch-up and ID exhaustion. The original 0688 collision replays also passed 23 Soldier body lifecycles and two-peer combat notice delivery with packet loss. Ruff and Git diff checks passed. Original-client acceptance remains pending.

#### Collision readiness regression fixture

- Forward the movement step's keyword arguments so the test can verify that a single input packet keeps its pinned collision world while the provider changes.
- Passed all 10 collision readiness tests, both dashboard JavaScript tests, Ruff and Git diff checks. This test fixture change does not require client acceptance.

#### Rifle recovery after an input gap

- Release held input before advancing the body through skipped frames for which the mover has no pose. Advance graph timers and movement modifiers, and consume the skipped combat reports before applying the next real command. Short gaps still replay movement and combat together.
- In an encrypted replay on original 0688 collision, a 65-frame command interval previously produced nine old rifle hits aimed with the next neutral command. After the fix, a neutral command left the bot at 200 health and the rifle at 29 rounds. A real press produced one 20-damage hit at the new frame, and the server and decoded owner ammunition then agreed at 28.
- Passed 147 tests with each data pack, 14 encrypted journal tests, the original 0688 short-gap rifle and Sprint controls, Ruff and Git diff checks. The preceding baseline passed all 1,214 Python tests. The observed in-client firing and prediction issues still require investigation and acceptance.

#### Mode UI delivery before elimination and death notices

- Wait for each peer to acknowledge the mode entity before sending its first `0CB6` UI frame. Keep only the latest countdown while the create is pending.
- Elimination and death notices wait for the receiving peer's UI frame acknowledgement and any referenced body's create acknowledgement. Ordinary hit notices still depend only on the target body. Preserve the existing retirement deadline for bodies referenced by deferred notices.
- On original 0688 collision, the first UI packet was lost. The hit still arrived at frame 1053, the UI retry at 1056, and the elimination notice at 1059 after the retry acknowledgement. Four consecutive mode-create losses also recovered with the UI sent after creation.
- Passed 197 tests with each data pack, including eight new encrypted cases for mode startup, packet loss, countdowns, independent peers and expired references. Stored-state replay, original 0688 body-create loss controls, Ruff and Git diff checks passed.
- The kill feed's separate body pulse can still precede the mode UI under loss. Training bot name lookup and original-client presentation remain pending.

#### Kill feed pulses after mode UI acknowledgement

- Gate the authored NPC `0C18` and player `01CF` kill feed pulses for each recipient. Health, death attribution and other body states still reach the client while the mode UI is pending.
- Use the existing 500 ms freshness policy on the death's world clock for the first send. Preserve the same active count and freshness in queued or cumulative frames until acknowledged.
- Encode NPC death snapshots against each viewer's acknowledged body frame so a deferred pulse can arrive after the rest of the death state. Preserve the layout of historical frames when the live graph has since removed an instance.
- On original 0688 collision, losing the initial UI packet left the lethal hit, zero health and attacker attribution at frame 1053. The independent timed death pulse arrived at 1059 and the kill feed pulse at 1062. Losing the UI beyond the freshness window suppressed the unsent feed pulse.
- Passed 212 tests with each data pack, plus stored-state replay and an exact-build NPC snapshot check. Ruff and Git diff checks passed.
- Original-client kill feed rendering, NPC names and actual sound playback remain pending. Owner corrections still use real command frames.

#### Player possession after world creation

- Send an observer's team script after the other player's controller create is acknowledged. Send that player's component 44/75 association after both controller and body creates are acknowledged.
- Cancel outstanding possession updates when a new one supersedes them or the observed body retires. A lost current association still retransmits. The owning client keeps its existing body-create and possession send order.
- In original 0688 encrypted replay, both controller-create and body-create loss recovered before the dependent association. Separate owner and observer controls verified that a lost old association cannot be resent after a body switch.
- Passed 178 tests with each data pack, stored replay and the original 0688 Soldier fire, reload, hero-switch and rejoin controls. Ruff and Git diff checks passed.
- Human combat notices still need to wait for the association itself. NPC identity and original-client presentation remain pending.

#### Remote player identity before combat presentation

- Keep elimination and killed-by notices out of the reliable queue until the receiving peer acknowledges the remote player's body association and mode UI. The hit notice remains independent.
- Apply the same identity check to remote attackers and victims named by the kill feed. Terminal health and other body state still go out while an association is pending.
- Discard unnumbered notices for an unconfirmed retired body or a superseded association. A confirmed old body may still resolve an earlier notice while its association remains current on that peer. Pending feed pulses also stop referring to a retired local body or starting during leave.
- On original 0688 collision with independently lost association packets, ordinary hits arrived at frame 1062. The elimination arrived at 1080 after the attacker's ACK, and the killed-by notice at 1086 after the victim's ACK. Each arrived once.
- Passed 203 tests with each data pack, stored replay, the original 0688 timed NPC death controls, Ruff and Git diff checks.
- Native NPC name lookup and original-client presentation remain pending.

#### Fixed world-target ability movement

- Run a resolved, fixed world target and explicit speed from a player body graph through the shared mover and map collision. Each posed frame, including bounded recovered frames, advances by the configured speed up to the remaining distance. Arrival finishes the state, blocking aborts it, and ordinary movement resumes without carrying the forced velocity.
- In offline controls with the authored Genji `01DB` and Mercy `006E` nodes, both bodies reached a supplied three-metre target on original Practice Range collision and stopped before a full-height wall. Synthetic controls cover vertical targets, descent against walkable ground, explicit aborts, bounded gaps and invalid targets. A controlled encrypted session reproduces active motion, final pose and registered server state.
- Passed 214 targeted tests with each runtime data pack, eight offline movement controls, Ruff and Git diff checks.
- Native target and speed resolution, entity and bone targets, duration-based movement, simultaneous request priority, and sub-grid travel remain open. Original-client ability prediction, animation and presentation still require acceptance.

#### Shared static collision rays

- Use one static ray traversal for combat occlusion and geometry queries that need a hit position, a ray-facing normal, and the triangle's category and surface flags. Retain the combat caller's distance-only interface and existing mover category.
- Add public source checks for category separation, first-hit ordering, two-sided normals, exact segment endpoints, and vertical rays. The query works with the triangles in its supplied world. Authored ability filters, dynamic targets and the original-client ray behavior still require validation.
- On the original Practice Range and Kanezaka collision caches, 5,790 offline queries returned the same distance or miss as the previous traversal. Passed 169 tests with each runtime data pack, an encrypted stored-state replay, Ruff and Git diff checks.

#### Unit body scale during graph initialization

- Resolve the owned player's standard body scale from the same unit value encoded in entity creates. The graph can read it during initialization, including before the first input frame. Foreign, missing and non-unit size inputs remain unresolved.
- Genji's first Shift reaches the authored cast action with a 15 m range and evaluates the movement speed to 50 m/s. In an offline replay of 57 original commands on Kanezaka collision, the position and velocity still match the recorded session while the cast endpoint is unresolved.
- Passed 172 tests with each runtime data pack, including encrypted stored-state replay, and Ruff and Git diff checks. Offline bootstrap and input checks exercised all 32 playable bodies over 320 command poses. Original aim origin, cast filtering, non-unit body scale and client acceptance remain open.

#### Expanded static ray geometry

- Add optional per-map ray caches with all nonzero geometry masks from the original core chunk and supported indexed static props. Retain original prop mesh surface flags and mode-specific placements for ray queries.
- Load these caches explicitly with the map and source chunk identity checked. Missing or mismatched geometry fails the load. The existing character and weapon paths continue to use the original movement caches.
- Include a selected map's optional ray cache in the prepared runtime and its SHA-256 manifest. Public fixture tests cover separate loading, category and mode selection, surface flags, and runtime preparation.
- On original Practice Range and Kanezaka collision, the existing cache hashes stayed identical and 3,000 movement-category rays returned the same distances. Expanded queries recovered previously omitted core and prop hits on both maps. The original 57-command Kanezaka movement replay and two encrypted Soldier rifle controls still match their baselines.
- Passed 173 targeted tests with each reference data pack, Ruff and Git diff checks.
- Broader prop indexing, analytic native shape behavior, dynamic targets, authored filter resolution and original-client acceptance remain open.

#### Authenticated static root props

- List unindexed client-made map root definitions and export their authenticated local definition and model records with pinned OWLib. Check direct records, bundle containers and slices before emitting the local catalog.
- Resolve extra root model references from the original STUv2 definitions. Add supported static shapes to the separate ray cache in placeable order, retaining mode restrictions and the existing rules for client-only breakables.
- Record placement and catalog provenance in the expanded cache. A missing record fails the build before replacement. The runtime loads the prepared cache from its data pack.
- On original 104319 data, resolved 35 omitted definitions and 27 model records. The Practice Range gains 31 static placements and Kanezaka gains one. Found a previously missing hit for each of the four added definitions. Both original movement caches kept their hashes, and 3,000 movement-category queries kept their prior distances.
- Passed 195 targeted tests with each runtime data pack, Ruff and Git diff checks. The final rebuild reproduced the validated cache hashes.
- Attached child geometry, dynamic and server-generated objects, original ability filters and client acceptance remain open.

#### Analytic static rays against round props

- In expanded static ray scenes, intersect uniformly scaled sphere and capsule props against their authored round surfaces. Retain each prop's original mask and mode selection, and choose the closest hit across round props and triangles.
- Resolve the first positive surface in range, including an exit for an origin inside the shape. Keep existing movement and combat queries on the legacy geometry.
- Original Practice Range controls reproduce a sphere hit at 0.1549701543 m and remove a prism-only hit. On Practice Range and Kanezaka, 3,000 movement-category ray distances still match. All 95 original movement cache hashes matched after the local expanded builds.
- Passed 199 targeted tests with each runtime data pack, Ruff and Git diff checks.
- Existing ray caches retain the authored round primitives. Gameplay filter selection and original-client acceptance remain open.

#### Local raycast filter and receiver metadata

- Export build 104319 raycast filters and receivers from pinned OWLib XML. Preserve complete asset IDs, rule order, repeated references, unset results, explicit zero flags and source hashes.
- Validate typed rules and linked filters before replacing the external JSON catalog. Public checks use synthetic XML fixtures.
- The local build 104319 export contains 66 filters and 19 receivers, including 44 override rows and 146 reference slots. Repeated exports match byte for byte.
- Passed 27 extractor tests, Ruff and Git diff checks.
- Native filter execution, the meaning of receiver result flags and original-client acceptance remain open.

#### Owner graph removal before create acknowledgement

- Send graphless removal records when a transient body subgraph has already been sent and disappears before its create is acknowledged. Keep the removal in cumulative frames until it is acknowledged.
- The Soldier body regression covers receivers that applied or lost the create. Both continue applying owner frames and enter the rifle volley after the removal. The previous server threw an exception while building the removal frame.
- Passed 89 targeted tests with each runtime data pack, Ruff and Git diff checks. The original-client effect and the cause of the earlier live weapon symptoms still need separate verification.

#### Match body positions in statescript expressions

- Resolve entity position reads from the match's current body pose, including the owner's initial create and visible player and bot bodies. Retirement detaches the old script from the match.
- With controlled ray inputs, the authored Genji graph keeps the hit point on a hit and uses the current body position to build its miss endpoint. The existing 57-command Kanezaka replay still matches the recorded poses.
- Passed 158 targeted tests with each runtime data pack and the original Practice Range wire-model lifecycle controls, including ten hero round trips and ten reentries. Native aim anchors, filters and original-client acceptance remain open.

#### Resolved raycast results in authored actions

- Allow the shared raycast action to consume a completed query for a world-space type-0 cast without a radius. Forward the authored filter and ordered ignore list to the query provider. Write validated hit and miss outputs before following the authored link.
- An unavailable query preserves the previous values and the fallback path. Live matches still need a verified aim anchor and filter interpreter before providing results.
- On original Kanezaka geometry, controlled queries feed a wall hit and an unobstructed miss to the original Genji action and move the body about 0.8 m on the next frame. The supplied origin and mask are test inputs. Original-client movement remains pending.
- Passed 163 targeted tests with each runtime data pack, 132 script tests, Ruff and Git diff checks. Public source checks use synthetic raycast fixtures.

#### Biotic Field healing across missed world ticks

- Settle an active field's elapsed healing once within the existing 64-tick combat catch-up window. Clip healing to ticks before expiry and use the most recent target poses for retained ticks.
- On original Practice Range collision, the encrypted replay gives each of two stationary friendly robots 2.8 health over five consecutive ticks or one update spanning all five. Both fields expire and send their destroy records.
- Passed 85 targeted tests with each runtime data pack, Ruff and Git diff checks. Regressions also cover duplicate ticks and skipping past expiry. Original placement, moving targets during a gap, and client effects remain open.

#### Unkeyed membership in statescript expressions

- Evaluate unkeyed `45EDF12D` with the same value equality as the existing condition node. Keyed queries retain the existing native fallback.
- A paired original `017B` graph uses both forms with identical operands. Controlled checks with the original `0254` graph distinguish rifle and Helix damage tags. The shared `01C7` hit feedback graph selects its authored gain for the supplied tags.
- Passed 137 script tests with each runtime data pack, including all five membership cases. Public checks cover the source-only cases. The production hit-event producer and original-client audio remain open.

#### Owner snapshots after confirmed packet loss

- Retire snapshots for owner deltas whose datagrams the transport has retired without acknowledgement. Keep cumulative changes by wire key and preserve the latest projected death pulse. An older acknowledgement cannot discard keys carried only by newer lost chunks.
- On original Practice Range collision, 120 lost rifle deltas left zero pending snapshots. The next delivered release cleared the retained keys and agreed on 22 rounds between the server and the decoded owner stream.
- Passed 116 targeted tests with each runtime data pack, including death-pulse delivery after a lost carrier, lifecycle and stored-state coverage. Ruff and Git diff checks passed. Unresolved transport delivery and original-client acceptance remain open.

#### Entity updates after reliable packet trimming

- Fit reliable messages, unreliable messages, and the ordered entity prefix in priority order when the combined frame exceeds the datagram budget. Consider entity updates after reducing the reliable batch.
- An encrypted Soldier fixture on original Practice Range collision sends the oldest reliable card and the current rifle delta in one datagram. The second card stays queued and the acknowledgement advances the exact owner chunk.
- Passed 71 targeted tests with each runtime data pack, including genuine budget deferral, send failure, correction and combat notice cases. Ruff and Git diff checks passed. Owner updates queued across sustained pressure and original-client acceptance remain open.

#### Projectile reports from transmitted owner chunks

- Record a projectile's owner-state report after the containing datagram is successfully sent. Use the transmitted chunk's command frame and carried keys, including when the body has advanced while the record was queued.
- Budget deferral and send failure leave the report unregistered. Positive acknowledgement of the transmitted chunk still confirms the predicted Helix lease. The original Practice Range replay retries a lost create before releasing the projectile and matches 231 recorded inputs and 39 sends.
- Passed 132 targeted tests with each runtime data pack, Ruff and Git diff checks. Repeated confirmation aliases from already transmitted frames and original-client presentation remain open.

#### Exact owner acknowledgements for projectile confirmations

- Use each report's own datagram delivery outcome for predicted projectile confirmation. Cumulative body acknowledgements still advance the snapshot baseline. A lost report cannot supply a projectile ID even if the baseline later advances past it.
- In the original Practice Range Hanzo input replay, a short loss sequence now gives two IDs, for the original shot and the one acknowledged report frame. The old path gave seven IDs and included six dropped reports. Without loss the replay still gives one ID.
- Passed 30 projectile tests with each runtime data pack. Encrypted cases cover mixed delivery, multiple positive acknowledgements and original-frame fallback when every report is lost. The original Practice Range Helix replay retained its create retry and release order. Original-client prediction and aliases from multiple acknowledged frames remain open.

#### Coalescing unsent owner body frames

- Replace an unsent owner delta in its existing queue position with the newest cumulative projection. Drop its unused snapshot once the replacement is encoded. Keep in-flight deltas, reliable full frames and observer streams on their existing paths.
- While the first owner frame waits for space, keep its non-correcting mode and retain an admitted death pulse. The settling interval uses the command frame of the first transmitted projection.
- Original Practice Range checks cover a blocked initialization, sustained fire and release. Recovery sends the latest frame with the rifle stopped and 27 rounds. The repeated hero switch, rejoin and reload controls also pass.
- Passed 152 targeted tests with each runtime data pack, Ruff and Git diff checks. Original-client prediction and presentation remain open.

#### Death update order under packet pressure

- Keep a replacement owner delta after earlier queued updates for its body. With a deferred neutral delta, component 51 now sends zero health before the replacement sends the death pulse.
- Passed 122 targeted tests with each runtime data pack, Ruff and Git diff checks. The encrypted regression exercises both the deferred case and its normal-order control. Original-client death presentation remains pending.

#### Projectile confirmation by volley activation

- Freeze each shot's volley counter and start time at emission. Use only matching volley data carried by the owner datagram, including inactive single-shot states. Two activations of the same state can now confirm their own projectiles independently.
- In the encrypted Hanzo rapid-fire regression on original Practice Range collision, the two arrows keep distinct confirmation IDs. The captured single-arrow replay still confirms one ID in normal delivery and two after simulated loss. The Helix confirmation retry and release replay still matches all 231 inputs and 39 sends.
- Passed 98 targeted tests with each runtime data pack, Ruff and Git diff checks. Alternate IDs shared by multiple shots of one activation and original-client presentation remain open.

#### Shot arguments during command handoff

- Pass volley activation by keyword so the shot time remains in its existing positional argument. The full local run caught the argument change in the encrypted command-handoff regression.
- Passed 76 targeted tests with each runtime data pack, including the last valid shot before a fresh command-clock handoff. Ruff and Git diff checks passed. Original-client acceptance remains pending.

#### Practice Range nameplate initialization

- Wait for each viewer's acknowledgement of the mode UI before replicating the training bot's `0D24` presenter. The initial body frame still carries the bot's other graphs and weapon state. A later death timer keeps the nameplate projection from the original death.
- Replace a pending presenter-only chunk with a cumulative body update when live or terminal state needs to send. The superseding death chunk follows zero health and carries the required presenter data. A late acknowledgement cannot roll back the terminal baseline, and a presenter alone does not hold body retirement.
- On original Practice Range collision, two-viewer runs with three and eight lost mode datagrams preserve all 15 initial bot delivery ticks and the critical hit, terminal health, and death timing. Separate loss and delayed acknowledgement of a presenter-only datagram preserve the next terminal send and its feed pulse. An expired feed and a dead target's withheld presenter stay retired when the mode arrives late.
- Passed the three encrypted nameplate regressions with both runtime data packs. The original shooting robot retained its projectile creation, launch retry, and destruction sequence for lethal and nonlethal hits. Ruff and Git diff checks passed. Original-client HUD, elimination names, kill feed presentation, and audio remain pending.

#### Ownership delivery before world creation

- Wait for acknowledgement of the reliable prefix through `20302` before creating the local controller and the initial world. An acknowledgement of the ownership datagram alone can leave the message parked behind an earlier reliable response.
- The original Practice Range replay keeps the normal controller create at tick 1038. After losing either the ownership datagram or an earlier response, ownership applies at tick 1044 and the controller follows at tick 1047.
- Updated the loopback and replay fixtures to acknowledge actual startup datagrams and seed the rifle model from the delivered owner frames. Passed 93 targeted tests with each runtime data pack. The encrypted Soldier: 76 check selects the hero, fires, and sees 29 rounds in both the server and decoded owner state. Ruff and Git diff checks passed.
- Original-client presentation remains pending. A separate replay still delivers the controller's hero-select graph before its entity when the controller create datagram is lost.

#### Local controller initialization after creation

- Wait for acknowledgement of the current local controller create before sending its hero selection and HUD graph, scoreboard update, or pending possession. Build the first graph from the latest selected hero and screen state. Preserve queued hero switch notices until the controller is ready.
- The original Practice Range replay keeps the healthy first controller frame at tick 1047. With the create dropped, the retry is acknowledged at tick 1053 and the first controller graph follows at tick 1062. The mode UI keeps its own startup path.
- Healthy preselected Mercy retains the original spawn voice activation and wire timing. In the delayed ownership control, the activation time stays the same and its body frame arrives three ticks later. Original-client HUD and audio remain pending.
- Passed 131 targeted tests with each runtime data pack, including positive acknowledgement of unrelated world updates, an early hero pick, and a late acknowledgement from a retired controller generation. Original collision replays pass the 60-second rifle test, ten hero round trips, and ten rejoins. Ruff and Git diff checks passed.

#### Projectile aliases from volley introductions

- Track which transmitted owner states introduced an activation relative to the baseline used to encode the chunk. A positively acknowledged stop or repeat still confirms the original shot ID. An alternate ID requires a positively acknowledged introduction.
- The stock Mercy replay keeps the four original blaster IDs and the first activation alias. It removes the later stop-frame ID previously shared by all four shots. A deferred frame retains the classification from its original encode baseline.
- Passed 33 projectile tests with each runtime data pack, including the encrypted Mercy and deferred-baseline regressions and the Helix acknowledgement controls. The original Practice Range Hanzo replay still produces one ID without loss and two with loss. Ruff and Git diff checks passed. Native behavior when several shots precede the first received activation remains pending.

#### Own body creation before possession

- Preserve create, possession, and old-body removal order when the records fit in one datagram. If packet budgeting splits them, wait for the new body's acknowledgement before sending possession and for the binding before removing the old body. Apply the same body prerequisite to the initial controller create when it carries a preselected body.
- In original Practice Range loss replays, unrelated messages, bot health and movement still send while the body or possession is pending. The no-loss control keeps all three records together. Separate create and possession losses recover in dependency order.
- Passed the 22 lifecycle tests with each runtime data pack and the 121-test focused run with bundled data. Ruff and Git diff checks passed. Original-client switching and presentation remain pending.

#### NPC state encoding cost

- Retain the latest live NPC snapshot without serializing a full body frame on each change. Encode it when a viewer needs a full baseline and preserve it before a terminal graph can remove live instances.
- In a 384-tick replay on original Practice Range collision, the number of live full-frame encodes fell from 771 to one. All 128 decrypted world payloads matched byte for byte. The encrypted regression also verifies two viewers joining at different frames and an older baseline retry after loss.
- Passed the 95 focused bot, notice, projectile, and pulse tests with each runtime data pack, and the 75-test stored-state and bot run with each pack. Ruff and Git diff checks passed. The reported client stalls and rendering performance still require original-client measurement.

#### World delivery after scheduler stalls

- Send the current world state once when a scheduler step crosses a scheduled send boundary. Prepare combat and body states on the same step. Normal simulation and send rates are preserved.
- In an encrypted replay on original Practice Range collision, the old scheduler skipped four packets after receiving fresh commands. The corrected scheduler sent one current snapshot with its command acknowledgement at each crossed boundary, including a step that crossed two boundaries.
- Passed 135 focused tests with each runtime data pack, covering tick and rollover behavior, bot and projectile delivery, lifecycle loss, and packet budgeting. Ruff and Git diff checks passed. Original-client lag and rendering still need measurement.

#### Local movement correction under packet pressure

- Allow a current owner correction to take space from best-effort remote movement when the byte or entity-record budget would otherwise drop it. Preserve the reliable, unreliable, and queued entity records already selected for the packet. Keep the original selection if the correction still cannot fit.
- Encrypted tests cover a 1230-byte payload limit, the 127-record limit, and unchanged healthy payloads. Under pressure the owner receives the new correction and its matching command acknowledgement.
- Passed 144 focused tests with each runtime data pack, plus Ruff and Git diff checks. Added a performance acceptance item to the roadmap. Original-client frame time and lag remain pending.

#### Beta audio and map research

- Recorded the pinned Prometheus voice endpoint and map loading reference. Added penetration, spawn room presentation and environmental death verification to the roadmap.
- Checked the pinned source links and Git diff. The cited behavior is from the beta. Original-client 104319 audio and map behavior remain open.

#### Player health delivery under packet loss

- Use the shared complete health-pool snapshot for player and NPC bodies. Keep the newest snapshot when an older one is lost, and send updates only after the recipient acknowledges the corresponding body create.
- Encrypted regressions drop the owner's 180-health update before delivering 170, and drop an observer's body create after its snapshot is queued. The owner stays at the latest wire value and the observer receives the current pools after the create retry.
- Passed eight focused encrypted health tests with each runtime data pack, one combat pool test, Ruff and Git diff checks. A separate owner script health update still waits for a fresh command frame. Original-client HUD acceptance remains pending.

#### First owner weapon and HUD state delivery

- Retry the original first owner state chunk after loss. Preserve its command frame, payload and projectile report. Keep unsent initialization coalescing and wait for acknowledgement before sending corrections.
- Cancel an already queued retry when the body script stops. In the encrypted regression, losing the first packet twice leaves the weapon and ammo presenter absent until the retry arrives. The decoded client then displays the 29-round state and accepts the next correction.
- Passed 110 focused tests with each runtime data pack, including health, owner frame loss, packet budget, repeated lifecycles, and spawn voice replication. Ruff and Git diff checks passed. Original-client weapon, HUD, Sprint animation, and audio remain pending.

#### Current health in a newly observed player body

- Freeze the current health pools in a player body create, including for a viewer who joins after the last damage update. A fresh body without a recorded pool still uses the hero's initial values.
- The encrypted regression drops a create at 180 health, then inflicts more damage. The retry retains 180, and the 170 update follows the create acknowledgement.
- Passed 138 focused tests with each runtime data pack, Ruff and Git diff checks. Original-client health display remains pending.

#### Replacement NPC delivery after a lost create

- Retire a superseded bot create when its frozen script is the only reason to wait and the viewer has yet to acknowledge that create. Keep the destroy record and preserve terminal delivery for viewers that acknowledged the old body.
- In the original Practice Range replay, one viewer lost the middle bot's create. Repeating it had also delayed the next live bot three times. The replacement now arrives on the respawn tick and its full graph follows on acknowledgement. A second viewer keeps the original terminal and destroy ordering.
- Passed 135 focused tests with each runtime data pack, Ruff and Git diff checks. Original-client respawn visibility remains pending.

#### Graph census checks with extended runtime data

- Compare both MovementMod census commands against the loaded graph definitions. Preserve the bundled minimum of 171 nodes and allow supplemental graphs to add nodes.
- A 1,315-test run on the previous baseline found this one fixed-count assertion. All 15 Statescript query tests now pass with the bundled census of 171 and the extended census of 174.

#### Observer snapshot cost during combat catch-up

- Cache the static remote-state classification for each graph and read unlinked variables directly. Continue evaluating active state payloads and linked values in graph order.
- On original Practice Range collision, the forced-gap replay retained 77 byte-identical decrypted payloads and the same ordered 57 shots, 45 damage events and three respawns. Four 960-snapshot trials took 99–110 ms after the change, compared with 138–166 ms before. Whole-tick timings overlap.
- Passed 90 focused tests with each runtime data pack, Ruff and Git diff checks. The original-client stalls still need measurement.

#### Melee activation across body ID reuse

- Retire the quick-melee activation counters with the player's body. A reused entity ID now starts its own history, while repeat frames for one activation still cause damage once.
- The regression drives Soldier's authored melee graph twice across two body replacements, using the match allocator with its retained-ID constraint. Both activations deal 30 damage.
- Passed 124 focused tests with each runtime data pack, Ruff and Git diff checks. Original-client acceptance remains pending.

#### Lobby seat cancellation in controlled replay

- Let the real matchmaker cancellation handler record its game-side handoff expiration when it runs against a controlled journal session.
- Replayed both cancellation orders. Cancellation before authentication rejects the late SYN and releases the seat on the next tick. Cancellation after the connection opens leaves it connected. The journal preserves the join and leave callback order.
- Passed 17 focused tests with each runtime data pack, Ruff and Git diff checks. Live lobby and game thread capture remains open.

#### Hero-select close after body binding

- Send the controller's closed menu state after the current local possession. Keep the ordinary same-datagram sequence, and wait for acknowledgement when the create or binding needs a separate packet. Reopening or reselecting supersedes an older close. Hero switch notices keep their existing delivery path.
- In original Practice Range replay, the normal Reaper pick still closes at tick 1089. With the split body create dropped, the previous close appeared at tick 1092. The corrected close follows the binding at tick 1098. The encrypted regression also drops a separate binding and checks that a newer open prevents an old close retry.
- Passed 125 focused tests with each runtime data pack, Ruff and Git diff checks. The frozen preceding baseline passed all 1,317 tests with prepared data. Original-client transition and HUD appearance remain pending.

#### Leased projectile damage across body retirement

- Remove a retiring body's leased projectiles from authoritative collision when their replicated copies are already due to retire. The flight of a projectile from the current body proceeds as before.
- In original Practice Range replay, the old server could still deal 40 direct and 80 blast damage after replacing the shooter's body and retiring the projectile copies. The corrected replay keeps the target at 200 health after replacement, and the unchanged-body control still reduces it to 80.
- Passed 125 focused tests with each runtime data pack, Ruff and Git diff checks. Original-client projectile presentation remains pending.

#### Owner weapon and HUD state after body binding

- Keep the body's plain H0 graph tree independent. Send its H1 owner state after the current possession record, either in the same packet or after the binding is acknowledged. Deferred first owner state still coalesces around the latest accepted command.
- In original Practice Range replay, the body tree arrives during two binding packet losses. The first owner state now waits for the binding acknowledged at tick 1104 and arrives at 1107. The wire regression verifies the ammo presenter and the 29-round state after an early shot.
- Passed 158 focused tests with each runtime data pack, Ruff and Git diff checks. The frozen preceding baseline passed all 1,319 tests with prepared data. Original-client weapon and ammo HUD appearance remain pending.

#### Clock rollover admission in controlled replay

- Record the production scheduler's clock-ready callback and permit an explicit controlled match creation from it.
- The registered-state regression drains the old leave, records clock-ready before the new handoff, then authenticates the new connection. Replay verifies the callback order, handoff and encrypted traffic.
- Passed 21 focused tests with each runtime data pack, Ruff and Git diff checks. Live lobby and game thread capture remains open.

#### Shared collision loading state in replay

- Write collision read tapes as v2 with references for repeated loading-state objects. Preserve the difference between a shared state and separate equal results. Continue checking the World on every ready read and accept standalone v1 tapes with their original per-read behavior.
- On original Practice Range collision, two recorded matches now replay with the shared loading state and pass registered-state verification. The clock rollover regression also covers the shared state while admitting a new encrypted connection.
- Passed 55 focused tests with each runtime data pack, Ruff and Git diff checks. Live thread capture and original-client acceptance remain pending.

### 2026-11-06

#### Statescript setup during hero selection

- Cache parsed graph field paths while continuing to look up the current values. Load the shared state codecs before opening the game server socket so the first match update uses the existing cache.
- On the prepared Practice Range runtime, the median body constructor fell from 12.66 ms to 10.91 ms across 288 paired samples for all 32 heroes. The first shared codec lookup took 132–151 ms on the cold baseline, mostly inside garbage collection. It was already cached after startup with the change. Complete pick timings overlapped.
- Compared 64 picks per build in the original Practice Range scene. All 683 decrypted payloads and the observed body binding and initial state timings matched. Passed 161 focused tests with each runtime data pack, Ruff and Git diff checks. Original-client selection stalls remain open.

#### Bot patrol floor queries

- Reuse the ground height from the slope probe when a patrol takes the same step. Query the final position when a slope shortens the stride or the bot reaches an authored waypoint.
- Across four original Practice Range catch-up scenarios, forced-gap floor queries fell by 38.13%. All 216 decrypted payloads and 25,920 per-bot movement snapshots matched. This measures one part of catch-up work. Original-client stalls still need diagnosis.
- Passed 126 focused tests with each runtime data pack, Ruff and Git diff checks.

#### Delayed game handoff in controlled replay

- Record the game server's reservation check when the production lobby timer decides whether to send a pending handoff. Replay the clock read without changing the reservation state during the check.
- Verified the timer one millisecond before expiry and at expiry. The first case sends the handoff and accepts the encrypted connection. The second releases the seat on the next tick after rejecting the late connection. Both cases preserve wire traffic and registered-state callbacks.
- Passed three focused regressions with each runtime data pack, Ruff and Git diff checks. Live lobby and game thread capture remains open.

#### Lag compensation across a body replacement

- Track replacement bodies from their authoritative world tick. Hitscan and quick melee skip a replacement when the historical view predates it. Pose interpolation for existing bodies and projectile flight keep their current paths.
- The player-body regression preserves damage at the replacement tick and prevents damage and a hit notice one millisecond earlier. In the prepared original Practice Range, encrypted rifle input one millisecond before a bot's replacement previously reduced the new bot to 180 health. It now stays at 200 and sends no hit notice. The at-birth control still deals 20 damage with one notice.
- Passed 126 focused tests with each runtime data pack, a nine-root registered-state replay through two replacements, Ruff and Git diff checks. Original-client combat acceptance remains pending.

#### Bilingual project README

- Keep English as the default README and link to a complete Simplified Chinese translation at the top. The translated page links back to English and keeps the same setup commands and evidence status.
- Verified both language links, relative destinations, anchors and command blocks. The README command discovery check passed three tests.

#### Pending owner health updates

- Refresh the latest queued owner frame when its HUD health changed before the first successful send. Keep the original command frame and apply the existing same-body queue order. A transmitted frame keeps its existing acknowledgement or retry behavior.
- In original Practice Range replay, the packet budget delayed a 155-health owner update at command frame 1076. After lethal damage and no new input, the server now sends the zero health pool before the refreshed HUD value and death pulse at 1076. The fresh-input control still sends the terminal state at 1077.
- Passed 168 focused tests with each runtime data pack, plus the final first-frame, in-flight and retry cases in both packs. Ruff and Git diff checks passed. Original-client health HUD rendering remains pending.

#### Initial controller binding for a preselected hero

- Treat the possession embedded in the first controller create as a prerequisite for owner weapon state. The H0 graph tree remains independent. The first H1 follows the controller in the same datagram when it fits, or waits for its acknowledgement.
- In original Practice Range replay with eight controller-create losses and no commands, the old sender emitted H1 at tick 1080 before its controller. The corrected sender delivers the controller first at tick 1089, followed by the same H1 in that datagram. The decoded ammo presenter is active with 30 rounds.
- Passed 170 focused tests with each runtime data pack, Ruff and Git diff checks. The earlier frozen development commit `1868be5` passed 1,330 tests with prepared data. Original-client appearance and acceptance remain pending.

#### Handoff expiry at the controlled receive boundary

- Let a scripted receive expire a handoff while the production scheduler is outside the game lock. Record the action and result at that receive, with the same scheduler deadline and legacy v2 receive rows.
- Reproduced both sides of the authenticated SYN and matched the production lobby timer's output, clock reads and final registered state. Invalid actions fail before the scheduler starts.
- Passed 18 journal tests with each runtime data pack, Ruff and Git diff checks. Live lobby and game thread capture remains open.

#### Biotic Field timing and delivery

- Start and expire Biotic Field on the server's world clock, retaining the existing approximate deployment delay. Recover eligible healing ticks across a scheduler gap. Owner visuals wait for their canister parent to arrive. An expired field stays retired when a late acknowledgement arrives.
- Route only the Biotic Field entity through healing. Torbjörn and Zarya still get projectile confirmation for their deployables without creating a healing field.
- On original Practice Range collision, immediate, delayed and withheld acknowledgements all open the field at tick 1142 and produce identical health at each sampled tick. A second replay verifies that two injured friendly bots receive equal healing and a terminal update across a five-tick gap.
- Passed 193 focused tests with each runtime data pack, a 268-root replay with matching registered state, Ruff and Git diff checks. Original canister flight, deployment timing and client presentation remain pending.
