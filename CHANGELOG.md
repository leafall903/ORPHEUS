# Changelog

This log records tested changes and their acceptance status. Entries are grouped by date until a version is explicitly released.

## Unreleased

### 2026-11-13

#### Keep Halloween restoration compatible with Rust 1.85

- PR #33 exposed unsupported let-chain syntax in the runtime metadata reader,
  encounter combat/replication, voice routing and StateScript services, plus an
  if-let match guard in Boss pursuit. Replace these with ordered nested branches
  and an ordinary tuple match without raising the declared Rust version.
- Existing short-circuit order, fallback evaluation, messages, damage and state
  lifetimes are retained. Behavior fixtures and production actor flags are
  unchanged; no new client acceptance is claimed.
- Verification: Rust 1.85 locked workspace/all-target inspector check passes;
  default 405 / inspector 409 library tests and both full workspace/Clippy lanes
  pass. Eight prepared-runtime regressions pass: encrypted three-card entry,
  first Reaper pursuit, all-Boss Flashbang pairs, observer/Witch behavior,
  Zombardier targeting/flight, early and all-intro late gate losses, and natural
  Deadeye allied victory. These remain headless results, not client acceptance.

#### Separate the PC creation stub from authoritative server spawning

- Native registry inspection resolves CreateEntity72C9C62F to its sole exact
  priority2 record2F63F00, allocation8F5620, placement8F7770 and size getterF79BF0
  (8 bytes). Runtime table262F4E0 executes slot48 through the empty1CE5640 callback.
- Two original1B74220 dispatches (cold resolution and cached reuse) select that
  actual implementation without reading the authored creation payload range.
  This captured PC binding cannot establish authoritative server Hero spawning
  or final actor category bits. Rust retains real authored Hero creation.
- Documentation/research boundary only; server and relay source are unchanged
  fromc1303f05, whose default405/inspector409 checks passed. No new Cargo or
  client test is claimed. Production category provenance and client acceptance
  remain open; Gallery preview code was confirmed already present on Rust.

#### Preserve captured aim through the Flashbang recovery state

- Admit the authored224E/34 zero-limit mode1 form through the existing linked
  state registry. Evaluate its captured501/502 angle values at activation and
  apply them to the room-owned unparented NPC motor; subsequent variable changes
  do not refresh the capture. Abort/reentry removes/rebuilds the effect while
  preserving earlier still-active aim locks and linked-list order.
- Original CAC810 initialization/composition supplies36 numeric reference cases
  for captured degree conversion and the native +/-89 degree pitch clamp.
  The shared numeric contract checks host pose projection separately; a VM/body
  lifecycle fixture checks data-driven capture, layering, cleanup and reentry.
- Validation: locked offline workspace/all-targets passes default405 and
  inspector409 library tests (124 ignored each), wire23 (2 ignored), both
  Clippy lanes with warnings denied, fmt, two shared captured-aim consumers,
  loaded wrapper and all-Boss-pairs room regression in both configurations.
  The isolated admitted reaction passes all seven cases and100 observer frames;
  five Boss captures are active1009..1049 and all end with zero locks, while two
  rejected paths never capture. Diagnostic actor admission stays outside the
  production adapter. General linked/platform transforms, player owner-full
  caches, production actor classification and client acceptance remain open.

#### Read the encounter body's yaw and pitch during Flashbang recovery

- Add typed body-angle queries for CC53F52B and7BE6D76D with separate present,
  unbound and unavailable results. The NPC and player adapters read their live
  authoritative mover pose. Missing foreign pose data remains unavailable;
  a valid zero angle is a computed value. Signed16 host angles use the native
  float32 conversion stages, with nonfinite/out-of-range values rejected.
- Offline original evaluators/getters pass1024 lookup scenarios. A second
  declared packed-angle replay supplies128 reference values, including signed
  endpoints. The shared fixture compares those values separately from owner
  adapter routing and malformed/absent-host behavior.
- Validation: locked offline workspace/all-targets passes default403 and
  inspector407 library tests (124 ignored each), wire23 (2 ignored), both
  Clippy lanes with warnings denied, fmt, the shared numeric contract and live
  owner-adapter check, loaded wrapper and all-Boss-pairs room regression in both
  configurations. Isolated admitted reaction passes both angle reads at1009,
  then requires the224E/34 linked-target state. Rust's packed mover pose is not
  a general native quaternion/platform transform backend. Full reaction,
  production actor classification and original-client acceptance remain open.

#### Apply the Flashbang horizontal cap before NPC collision movement

- Interpret authored 224E/31's zero-lower-bound horizontal clamp row. Read its
  upper bound from graph data and enqueue the validated edit for the next frame.
  Route the edit from the body adapter through the room to objective movement,
  where it changes requested velocity before collision and displacement. It
  expires on its frame, including a controlled stop, without delayed catch-up.
- Original D35310 typed recognition and D2BAC5..D2BB50 row dispatch select
  property33/operation5 through C32860. Twelve original C31870 consumer cases
  distinguish inactive/active frames, both horizontal directions, small/zero
  horizontal speeds and preserved vertical velocity. Shared host fixtures check
  VM dispatch and collision-aware displacement/recovery separately.
- Validation: locked offline workspace/all-targets passes default401 and
  inspector405 library tests (124 ignored each), wire23 (2 ignored), both
  Clippy lanes with warnings denied, fmt, eight shared Flashbang consumers,
  loaded wrapper and all-Boss-pairs room regression in both configurations.
  The isolated admitted reaction now reaches frame1009, where node30 requires
  the still-unimplemented CC53F52B yaw query. NPC requested velocity remains the
  existing host path motor; full native acceleration/platform/contact parity,
  production actor classification and original-client acceptance remain open.

#### Route the Flashbang notification to its intended attacker

- Carry an optional body recipient through the existing defender-message queue.
  Authored 224E/22 notification 0622 preserves parameter 2B and the source body.
  Only registered defenders admit this scoped enemy request. Both room dispatch
  and the player adapter check the recipient; encounter broadcasts keep their
  existing all-defender delivery.
- The synthetic shared sender/receiver contract exercises VM action evaluation,
  enrolled-recipient routing, bounded queue admission, parameter/source identity,
  foreign-recipient exclusion and unchanged intro broadcasts. Its listener
  follows 01D8/54 without inventing a combat effect for listener-free heroes.
- Validation: locked offline workspace/all-targets passes default399 and
  inspector403 library tests (124 ignored each), wire23 (2 ignored), both
  Clippy lanes with warnings denied, fmt, the shared sender/receiver contract
  and admitted all-Boss-pairs room regression. Full reaction velocity, actor
  classification and original-client acceptance remain open.

#### Mount the Flashbang child through the evidenced base State

- Admit the authored224E/25 placement of0DB9E66B through the existing base State
  lifecycle. The captured1735-record PC registry has no exact binding; parent
  record3294268 selects constructor1B726B0. Original owned-object construction
  returns vtable27AC800 and empty reader1CE5640 ignores its payload.
- The shared synthetic lifecycle contract checks begin, nested child, abort,
  end output and reentry, plus observer encoding with the verified empty codec.
  Unevaluable payload configs remain unread, and an unrelated graph still
  diagnoses. Existing authored aliases remain supported. This is not a new
  health/tag multiplier implementation or a claim
  about uncaptured dedicated-server state behavior.
- Full reaction/actor classification and original-client acceptance remain open.
  Validation: locked offline workspace/all-targets passes default398 and
  inspector402 library tests (124 ignored each), wire23 (2 ignored), both
  Clippy lanes with warnings denied, fmt, the shared lifecycle/observer contract,
  loaded wrapper and admitted all-Boss-pairs room regression in both lanes.
  Isolated admitted-reaction diagnostics now stop at attacker message22 and
  velocity action31; no actor mask was changed to admit the production reaction.

#### Resolve absent filter targets before later predicate services

- Original CF4340/D0DA20 returns false for five absent/invalid/unresolved target
  cases before mask/tag/relationship evaluation, including a nonzero relation
  mask and non-null excluded-tag config. Preserve host shape/type validation,
  but defer supported-predicate checks until the target can exist. Explicitly
  known retired targets also reject without asking for flags or tag facts.
- Add a shared native-backed fixture and actual evaluator checks. Malformed
  masks, unknown fields and invalid metadata still diagnose for null targets;
  live or unavailable valid actors still require supported relationship services.
- The declared-category reaction's local393 is actually unset, while28714 is
  the recorded attacker. This repairs the erroneous224E/131 diagnostic without
  inventing a live target, hostility rule or actor category. Other reaction
  services, full animation and original-client acceptance remain open.
- Default/inspector Windows locked/offline workspace checks pass (397/401
  library tests, each124 ignored; wire23 pass/two ignored), as do both Clippy
  lanes with warnings denied. Both lanes pass the new five-case contract, the
  existing64-case admission contract, seven loaded wrappers, actual-E first
  Reaper and eight room cases. Default earned-score defeat passes. The30 shared
  consumers are inventoried. The isolated reaction no longer diagnoses131;
  remaining22/25/31 services keep complete reaction acceptance open. No original
  client, hosted CI, Linux/MSRV or new full late-victory replay is claimed here.

#### Continue the unbound Flashbang loadout action through its native base

- Handle 545AB3B8 through the captured 104319 Action base: follow m_outPlug
  and return true, including no-output calls, without evaluating target/loadout
  configs or applying a loadout. The exact type is absent from the captured
  1735-record registry; original dispatch/resolver/base execution confirms the
  default path, while synthetic exact-registration controls take their own path.
- Add the shared two-case fixture and actual VM checks with unevaluable payload
  configs and repeated calls. The Rust host does not implement an alternate
  registry override or claim equivalent native cache storage.
- The isolated declared-category reaction now advances past 224E/150 to its
  next services. It still reports external 0622 delivery at22, base State at25,
  velocity action at31 and the actor filter at131. Production category masks
  stay unchanged; full reaction/animation and client acceptance remain open.
- Default/inspector Windows locked/offline workspace checks pass (396/400
  library tests, each124 ignored; wire23 pass/two ignored), as do both Clippy
  lanes with warnings denied. Both lanes pass the exact shared consumer, seven
  loaded wrapper cases, actual-E first-Reaper replay and eight room dispatch
  cases. The 29 shared consumers are inventoried. Reported results use a fresh
  main-only Cargo target after discarding mixed diagnostic-probe build output.
  Hosted CI, Linux/MSRV, full reaction and original-client acceptance remain open.

#### Reconcile Halloween with the actual Rust combined server

- Reconcile the nine overlapping files against combined-server 76d37b23,
  preserving both histories. Its shared enemy/gate implementation is already
  present in the newer Halloween implementation: room target/health/retirement
  delivery now lives in game_service/room.rs and state.rs; enemy facts retain
  capsule geometry, tags and signed no-team values. Keep those evolved paths
  rather than duplicate the earlier inline service implementation.
- Preserve the submitted ForCode values/observers, elapsed-state condition,
  contact/health/target services, corpse retirement and Zomnic gate checks.
  This merge does not claim complete Flashbang reaction or client acceptance.
- The Zomnic regression exposed a real host-clock defect: self-damage took a
  missing movement wire frame as frame 0, immediately expiring a corpse in a
  later room frame. Use the explicit room frame and time for NPC self-damage,
  set before actor services. A synthetic three-case contract rejects movement
  markers as clocks and checks the existing eight-second corpse boundary;
  the prepared gate check retains its death/retirement, damage-state, voice
  and result assertions and additionally checks the real death frame.
- Reconciled Windows locked/offline workspace checks pass in default (395
  library tests) and inspector (399) lanes, each with 124 explicitly ignored
  runtime checks; wire passes 23 with two ignored. Both Clippy lanes deny
  warnings and pass. Both lanes pass prepared Zomnic gate/death/retirement,
  actual-E first-Reaper pursuit and eight Flashbang room cases. Default natural
  early/late defeat, full Deadeye victory and earned-score defeat pass; victory
  retains Cassidy at 225 HP, nine Boss bodies, two authored casts, five once-only
  intro/camera routes and score 7104.00048828125. The 28 shared consumers are
  inventoried. Hosted CI, supported MSRV/Linux and original client remain open.

#### Write native nested destinations into the Flashbang pending queue

- Restore chained variable/index/identifier destinations in SetVar and multi-
  assignment. Read live dynamic indexes through the normal VM and publish the
  updated root through its ordinary write/notification path. Unsupported
  expression destinations now report a diagnostic instead of a successful
  no-op. Separate keyed/indexed options retain their existing boundaries.
- Preserve mutable identifier-map capacity, empty buckets and physical-order
  growth. Native array destination storage discards a non-array container,
  fills holes with integer 0 and rejects indexes at 1000 after coercion. Map
  destinations coerce scalar storage and retain field values across collisions,
  growth and updates. A logical map without retained native layout is refused
  for mutation rather than given an invented bucket order.
- Add a native-backed fixture: 12 destination paths, 21 container boundary cases
  and two map insertion/growth/update sequences. Production VM checks also use
  live local indexes in both scopes; existing read watchers observe repeated
  value changes. Mutable maps retain the existing unbound-map projection
  boundary and use the ordinary compact map writer when explicitly encoded.
- The isolated accepting-category trace now carries the real queued payload
  past 224E/21 and reaches the next unsupported action, 545AB3B8 at 150. No
  production category bits are changed. This repairs pending-record storage,
  not complete reaction animation, actor classification or client acceptance.
- Linked roots remain explicitly refused until writable native COW is proven;
  a StateStack integration check preserves the resolved linked value.
- Windows locked/offline workspace checks pass in default (394 library tests)
  and inspector (398) lanes, each with 124 explicitly ignored runtime tests;
  wire checks pass 23 with two ignored. Both Clippy lanes pass with warnings
  denied. Both lanes pass the seven-case loaded wrapper, eight-case room
  dispatch and actual-E first-Reaper checks. Default natural early/late defeat,
  full Deadeye victory and earned-score defeat checks pass. The 27 shared
  consumers remain inventoried. Hosted CI, Linux/MSRV and original-client
  acceptance are not established by these local checks.

### Preserve the outstanding shared reference implementation

- Submit all eight existing modified Rust source files covering combat, game
  replication/service, scene assets, enemy scripts, expression/init handling
  and snapshot wire state. Preserve their complete implementations for the
  ongoing original-language Python integration.
- The locked, offline workspace Cargo check passed. This preservation commit
  does not establish complete feature, original-client or cutover acceptance.
  Existing implementations are submitted despite unfinished acceptance work.

#### Locate a second Flashbang reaction gap after declared admission

- An isolated source probe declares an accepting category input without changing
  production flags. Actual traces reach 224E/9, while upstream 172/175/176
  leave the queued payload null and only remote carrier state 71 remains. The ordinary
  five Boss bodies and two previously declared rejecting cases retain their
  narrower probe outcomes; this is not complete reaction acceptance.
- The concrete source gap is nested assignment: current `lvalue` returns no
  target for the array-element/map-field destination at 172, and `store` treats
  that as a successful no-op. Variable 31528 is local scope 0; the older entity
  variable assertion cannot establish its local queue contents. A separate
  original 1B81210 replay matches 12 constant-index nested path cases. Assignment,
  container growth/COW and watchers still need their own evidence/repair.
- This corrects the earlier implication that spawn classification is the only
  remaining reaction gap. Diagnostics, speculative input and native bytes stay
  in external verification storage. Documentation/diff checks pass; production
  behavior is unchanged and no Cargo/client/full-outcome check is repeated.

#### Pin integrated native Flashbang filter admission

- Execute the original CF4340 expression entry through its actual D0DA20
  callback, mask matcher, tag-list stage/predicate, null condition/post defaults,
  absent-world custom default and Boolean result writer. All 64 synthetic flag/
  tag combinations match. Resolved actor/component pointers, literal tag-config
  conversion, empty live tag effects and variant ownership remain adapters.
- Publish the synthetic 64-case fixture and a production Rust expression
  consumer in both CI inventories. It confirms that 1/team|1 rejects 224E/9;
  declared 40/41 masks only admit without excluded mask bits or tags. This does
  not establish which categories a native Boss owns and changes no gameplay
  flags. The remaining repair belongs at spawn category provisioning, not a
  Boolean inversion or another tag-clause rewrite.
- Locked offline workspace/all-target tests and Clippy pass in default and
  inspector builds (391/395 server-library passes, 124 ignored; wire 23 passes,
  two ignored). Both exact shared consumers, the original 64-case replay, CI
  inventory of 26 unique contracts, formatting and diff checks pass. This is a
  test-only extension: prepared encounter/outcome checks were not repeated.
  No client, hosted CI, Linux or MSRV result is claimed. Native actor inventory
  and complete reaction animation remain open.

#### Respect Flashbang exclusion tags in the room control bridge

- Read the actual 224E/9 exclusion clause before timed room control. Reuse the
  production tag evaluator and effective body-tag facts, including live grants
  and suppressions. An excluded tag rejects control; unavailable facts remain
  explicit errors. Normal impact damage and wrapper delivery keep their path.
- Add an exact CI consumer for the eight existing native-backed Flashbang
  exclusion cases, plus grant/release, base suppression/release and unknown-tag
  boundaries. The prepared wrapper probe also checks the loaded clause against
  actual body-tag services. Extend the room matrix to five ordinary Bosses
  and three declared Reaper exclusions: all retain 25 damage and wrapper
  delivery, while only ordinary cases receive timed control. This repairs an
  omitted independent guard; it does not infer category bits or establish
  complete native reaction animation.
- Locked offline workspace/all-target tests and Clippy pass in default and
  inspector builds (390/394 server-library passes, 124 ignored; wire 23 passes,
  two ignored). Both lanes pass the eight-case room matrix, seven-case wrapper
  probe and natural first-Reaper actual-E regression. The 72-case original
  native tag oracle, CI inventory of 25 unique shared contracts, formatting and
  diff checks pass. No new full-outcome, hosted-CI, Linux, MSRV or client
  acceptance is claimed; original-client verification remains deferred.

#### Identify the Flashbang category probe's map-only input

- Identify native 1B48140's input as a map-placeable ENTITY record. Execute
  original 1B483B6..1B48420 on all 230 exact castle placement headers: all
  admission/config results match, with 31 requests for component 57. Gate
  placement 16 requests none through this block. The native admission call is
  observed without constructing components or running category registration.
- This rules out using raw 003 bytes or this map loader to infer dynamic Boss
  categories. Preserve the distinction in parity/research notes; no gameplay
  masks, authored data or runtime behavior change. Reproduction and restricted
  source inputs stay in external verification storage. The static Flashbang
  reaction gap remains open, and no client was launched.
- Documentation-only change: the 230-case offline native replay and diff checks
  pass. Cargo/full-outcome checks were not repeated; no new original-client,
  hosted-CI, Linux or MSRV result is claimed.

#### Honor the earlier Flashbang Boolean entry guard

- The room control bridge now evaluates loaded 224E/4 before its existing
  224E/122 priority check. Read effective entity bindings and the effect's
  authored local schema default. The admitted 0228 path does not override
  that local; changed overrides and unavailable inputs remain explicit errors.
  Damage and hit-effect delivery retain their existing path when control is
  rejected. No native category bits are guessed or inserted.
- Add eight synthetic cases: unset/false/true flags, forced schema default,
  true/false linked bindings that shadow opposite base values, unavailable
  body and an unsupported wrapper override. Six semantic cases agree with the
  pinned normal Python VM; two adapter error boundaries are Rust-only. Register
  the exact Rust consumer in both CI lanes. Extend the authorized wrapper
  replay with a declared rejecting body flag and compare with the actual
  loaded entry expression as well as the existing Wraith priority check.
- This corrects one omitted control-admission condition. Native actor category
  initialization and the complete224E reaction/animation remain unverified;
  original-client verification is still deferred under the user's order.
- Locked offline workspace/all-target tests and Clippy pass in default and
  inspector builds (389/393 server-library passes, 124 ignored; wire23 passes,
  two ignored). Both lanes pass the seven-case authored wrapper probe,
  five-Boss admitted-hit replay and natural first-Reaper E-input regression.
  Python comparison, formatting and diff checks pass. Hosted CI, Linux and
  MSRV were not run; complete no-E outcome replays were not repeated.

#### Exercise natural Reaper control through Cassidy E input

- Replace direct test-only stun application in the first natural Reaper replay
  with Cassidy's actual owner VM, normal collision movement, one E press,
  owner projectile-lease admission and authored grenade flight. Walk Cassidy
  into range after Reaper has completed his intro, pursued and attacked.
- Check one launch/impact, actual 25-point NPC health loss, 50 ticks with no
  Reaper attack or movement, and subsequent combat recovery. Check the owner
  projection and the paired NPC observer projection; one 224E instance is
  created by the actual hit. No Boss health/control/classification values are
  injected. The fixture still retires common enemies through normal damage
  and declares the external Cassidy target invulnerable to isolate the Boss.
- This strengthens restricted prepared-runtime verification of existing
  production behavior. Lease-to-flight assembly is explicit; network
  transport/create acknowledgments are outside the replay. It exercises the
  existing timed control bridge, not the complete classification-gated native
  224E reaction/animation lifecycle. No client was started.
- The explicit replay passes in default and inspector builds: E press11616,
  impact11627, resumed attack11677, one launch/impact and 25 health loss.
  Locked offline workspace/all-target tests and Clippy pass in both lanes
  (388/392 server-library passes, 124 ignored; wire23 passes, two ignored).
  Formatting/diff checks pass. No production behavior changed; prior full
  natural outcome tests are not repeated for this test-only extension. Hosted
  CI, Linux and MSRV were not run.

#### Correct generic entity-filter tag clauses

- Use the original D0EC70/DB2FD0 any/all/excluded field order for generic
  AC829876 queries independently of the scoped relationship policy. The old
  generic mapping could require Flashbang's excluded tags and accept
  just one tag from an all group. Admit supported any-list operands and reject
  nonempty C1903771 instead of inventing a fourth tag group.
- Add 72 synthetic native-checked cases, including all eight combinations of
  224E/9's three excluded tags. Original predicate/membership code runs with
  explicit tag storage and request vectors. Production Rust expression
  dispatch consumes the same fixture, with an exact CI inventory entry in
  both feature lanes. No game bytes, captures or generated binaries are added.
- Locked offline workspace/all-target tests and Clippy pass in default and
  inspector builds (388/392 server-library passes, 124 ignored; wire 23 passes,
  two ignored). Both lanes pass the prepared five-Boss Flashbang hit-dispatch
  replay and the 15 Boss/three common-enemy damage/charge/score matrices.
  The default build also passes all three full natural outcome replays,
  retaining one-shot Boss intros/cameras, actual Deadeye kills, gate results
  and score assertions. Earned-score defeat also retains 12 through the native
  enemy result/post-match output. Formatting and diff checks pass.
- The tag stage is corrected; actual Boss classification and the complete
  reaction/control/animation lifecycle remain unverified. This intentionally
  supersedes the old generic Rust mapping; generic pinned-Python parity is not
  claimed. No client, hosted CI, Linux or MSRV run.

#### Verify Normal NPC points and both result-score branches

- Extend actual Cassidy damage/charge/retirement replays with independent
  authored base-point assertions for all eight NPC/Boss types: 15 Boss cases
  (primary body/head and fan) plus three common enemies. Return their real
  buffered messages through normal scene delivery and check effective score
  and the encoded 1188 HUD projection; no score values are injected.
- Extend all three complete natural outcome replays with earned-point and
  gate-bonus assertions. Default-build victory earns 4121 points plus 1799 gate
  bonus, then applies the authored Normal 1.2 multiplier (float32 result
  7104.00048828125). Early and late defeats have no point events or gate bonus.
  A separate naturally spawned Zomnic replay earns 12 and retains it through
  actual gate destruction, enemy victory and native result/post-match output.
- This is verification of existing behavior with authorized prepared runtime
  inputs. Default and inspector NPC matrices and earned-score defeat pass;
  all three complete natural outcomes pass in the default build. Locked
  offline workspace/all-target tests and Clippy pass in both builds
  (387/391 server-library passes, 124 ignored; wire 23 passes, two ignored).
  Formatting and diff checks pass. No client, hosted CI, Linux or MSRV run.
- Native widget presentation and account high-score persistence remain
  unverified; mode TrackStat remains unsupported. Full Flashbang reaction
  remains a static gap. Original-client validation stays deferred until the
  user's listed static requirements are complete.

#### Verify authored team-score ingress and HUD projection

- Add a restricted Normal replay that waits for one naturally spawned Zomnic's
  body listeners, then uses the normal room damage/death path with a declared
  Cassidy defender. Its real 0CBC death entry emits one 06A4 message carrying
  10 points. The existing 0CC7 accumulator and Normal 1.2 multiplier produce
  an effective score of 12, with no direct score writes or injected messages.
- Assert that the actual 1188 notification projection includes entity 2499 = 12
  for the game-mode actor and produces a nonempty encoded delta. Observe 200
  frames after death to reject repeated counting. Read the effective stack
  binding: the base variable remains null and does not represent the score.
- This adds verification of existing production behavior. It establishes one
  common-enemy score path, not all NPC/Boss points, complete result bonuses,
  account high-score persistence or original-client PVEScore presentation.
  The mode-context TrackStat action remains unsupported. No client was started.
- Locked offline workspace/all-target tests and Clippy pass in default and
  inspector builds (387/391 server-library passes, 124 ignored; wire 23 passes,
  two ignored). The new prepared score replay passes explicitly in both lanes.
  Formatting and diff checks pass; hosted CI, Linux and MSRV were not run.

#### Correct non-playable hero preview resources

- Add exact Hero/preview/body rules for the 14 NPCs with mismatched preview
  models. Playable, unknown and mismatched definitions retain their original
  resource. Use 93CBF16B (Hero +0x88), which target-build scripts actually use.
- Retain model and local-idle components in the independent cosmetic builder
  and its owned children. Validate the bounded tree before changes; cycles and
  excess nodes fail without partial changes. Shared definitions and normal
  gameplay builders remain unchanged.
- Install signature-checked, aligned preview patches before exposing the NPC
  roster. Preserve native input cleanup, type checking, errors, typed output
  and constructor failure. Check both relative trampoline destinations; retain
  executable storage if the second patch fails.
- MSVC /W4 /WX passes 18 resource and six tree contract cases. A restricted
  adapter probe passes 28 normal/playable evaluations, four failure branches,
  constructor failure, unrelated resources, exact signature installation,
  relocations, RX storage and changed-entry rejection. The original native
  iterator consumes compiled production pruning output and enumerates only
  model4/local-idle42; script47 is absent. The candidate relay DLL compiles.
- No client was started. Rendering/animation, hosted CI and full Halloween
  acceptance remain unproven. Score and full Flashbang reaction remain open;
  client verification still waits for all listed static gaps.

#### Respect the authored Flashbang control priority

- Before applying the scoped Junkenstein timed-control bridge, read the actual
  224E/122 entity-stack predicate and its authored priority. A higher-priority
  active control scope now rejects the stun; effect-message delivery remains
  on the normal VM path. Missing/halted predicate services stay explicit faults.
- Seven shared synthetic VM cases agree with pinned Python e1128509: empty,
  lower/equal/higher scopes, false values at higher priority, mixed scopes and
  release. The generic host read uses the VM's existing arbitration kernel.
- Extend the prepared five-Boss wrapper probe with actual Reaper 01C9/45
  control-stack activation. The production predicate and real query both reject
  that higher-priority scope, while ordinary bodies are admitted. This starts
  the authored state directly; it does not prove the entire Wraith ability.
- Admit the 01C9/65 child through its inspected PC base-State registration and
  payload-free codec. This adds no custom invulnerability or animation behavior.
- Further classification/tag/state-variable immunity branches remain open.
  The current normal hit replay retains 224E's schema carrier (raw71/node191);
  it does not prove active hit-reaction animations or full native control state.
- Locked offline default/inspector workspace/all-target tests pass (387/391
  server-library tests, 123 ignored; wire 23 pass/2 ignored), with both Clippy
  lanes clean. The new consumer passes inventory/exact checks in both lanes;
  the seven-case normal Python comparison passes. Prepared wrapper/Wraith,
  five-Boss room-hit and E-input probes pass in both builds; four base Soldier
  probes pass. Formatting and diff checks pass. No original-client, hosted-CI,
  Linux or MSRV evidence is added.

#### Dispatch Flashbang hits and preserve their input lifetime

- Keep admitted Flashbang source sites with room projectile entities. After a
  surviving NPC hit, queue its real 0228 wrapper before the 000F hit entry through
  ordinary paired-component delivery. Previously damage and timed AI blocking
  ran without producing the original effect-script state for observers.
- Five synthetic Rust host-policy cases check scoped admission, ordered VM
  execution and typed source identity. A restricted prepared room replay uses
  physical projectile hits, actual castle collision and complete body/controller
  adapters for all five Bosses; each takes exactly one 25-damage hit and encodes
  its actual 224E effect state through 120 frames without diagnostics. Its hit
  input is synthetic; actual E launch evidence stays in the separate body probe.
- The longer E probe exposes a generic ability bug: rejecting a buffered start
  consumed/reinserted the press with a new timestamp on every update. A released
  early press could therefore survive the entire cooldown and fire later.
  Retain its original timestamp until all start gates pass, then consume it.
  Four synthetic flows agree with normal pinned Python e1128509. The actual E
  replay rejects the early tap and accepts a fresh later tap over 800 frames.
- Immunity branches, exact native hit reaction/control presentation and original
  client acceptance remain open. The Python ability start model itself labels
  its obfuscated-PC/PS4 start-gate reconstruction unverified; this is reference
  agreement, not recovered native start-gate equivalence.
- Final locked offline workspace/all-target tests pass in default/inspector
  lanes (386/390 server-library tests, 123 ignored; wire 23 pass/2 ignored),
  with both Clippy lanes clean. Both new consumers are individually registered
  and inventory/exact checked in both lanes; the four-case normal Python
  comparison passes. Nine prepared castle probes, including complete natural
  Deadeye victory and all-intros late defeat, plus four base Soldier probes
  pass. Formatting/diff checks pass. No client, Linux, MSRV or hosted-CI claim.

#### Extend Flashbang body-adapter checks to all five Bosses

- Run the existing real 0228/224E wrapper/schema replay against Reaper,
  Roadhog, Summoner, Junkenstein and Witch body roots. Declare Normal
  difficulty and explicit empty combat/placement services for this isolated
  probe; missing services are not replaced with production fallbacks.
- All five drain the deferred queue without diagnostics over 100 frames and
  encode their actual observer snapshots, including the retained effect schema.
  This is body-adapter coverage only. The complete projectile-to-effect visual
  bridge, immunity branches and original-client Flashbang reaction stay open.
- The prepared check passes in default and inspector builds, with formatting,
  diff checks and inspector workspace/all-target Clippy clean. No production
  gameplay policy changes in this follow-up.

#### Align Deadeye visibility with its actual shot ray

- The balcony Summoner's lower capsule point is hidden while its actual
  auto-aim point has a clear world ray. Replace the nearest-surface shortcut
  in the scoped Deadeye bridge with the emitted aim ray and its real capsule
  entry distance. World geometry still blocks shots before target contact.
- Six synthetic clear/partial/full-cover and target-entry cases agree with
  normal pinned Python collision kernels. This intentionally changes the
  pinned Python Deadeye selection policy; native selection equivalence is
  still open. The authorized castle probe checks two real encounter poses.

#### Restore the victory-view child handoff

- Accept the authored scalar payloads on 0CEC/2 and /3 and route their
  start/end view messages to defender bodies. Previously the generic routed
  action rejected these parameters; /3 could not reach its outgoing self-stop
  action, leaving 0C8C/73 and the main mode in phase 4 without a winner/recap.
- Preserve the scoped 0CEC/11 client-only cue's server forwarding behavior.
  This adds no synthetic client outcome or forced phase transition.
- Restore the defender's 0CED view states for explicit absence of the native
  auxiliary client owner and its authored persistent notification. Keep
  unknown/present owners unsupported. Six spatial-binding cases agree with
  pinned Python 46b22130; E8D976BE's owner-absent enter/exit uses separately
  inspected native early returns and has no Python equivalence claim.
- Refresh the view subject's real Boss tag/position/facing facts before peer
  message delivery, including bodies still present after death. The previous
  damage/sighting-only facts were unavailable in the later view callback.
  Missing or faulted bodies keep unavailable results; retirement is unchanged.
- Three synthetic cases agree with normal pinned Python routing/parameter
  collection and the actual Rust VM/defender-message admission. The prepared
  real 0CEC child now sends both messages, stops and advances its real parent
  to phase 5 without diagnostics. Original-client playback remains open.
- A prepared gate replay covers the authored intact and three damage poses,
  the zero-health branch and changed observer frames at all five health
  samples. The gate's final damage pose remains 0511 at zero, as selected by
  the authored switch. Original-client model rendering is still pending.
- The complete natural Normal victory replay passes at tick 54878: actual
  passive charge, collision-backed movement and two actual Deadeye casts
  deliver terminal hits to all nine Boss bodies. All five introductions encode
  once, Cassidy stays at 225 health while absorbing 37002.70704588873 enemy
  damage, the high-capacity gate survives, and allied winner/result/post-match
  payloads encode. Early and all-intros late gate-defeat regressions also pass.
- Locked offline default/inspector workspace/all-target tests pass (384/388
  server-library tests, 122 ignored; wire 23 pass/2 ignored), with Clippy clean
  in both lanes. The four shared contract inventory/exact checks, normal pinned
  Python consumers, nine castle prepared probes and four base Soldier probes
  pass. No original-client, hosted-CI, Linux or MSRV evidence is added. Team
  score, remaining static gaps and NPC Hero Gallery acceptance remain open;
  the user's all-static-items-before-client requirement remains in force.

#### Preserve signed team queries after enemy retirement

- A longer natural Normal victory probe exposes a Zomnic 0CBC/19 failure at
  tick 49050 when its attacker has already retired. Extend actual room
  retirement facts to team lookup: known removal returns signed native -1;
  an unknown target remains unavailable. The VM/host team interface now
  preserves that signed result rather than requiring an unsigned index.
- Extend the existing eight-case shared retirement contract through the normal
  pinned Python entity-team service and actual Rust enemy host/evaluator.
  Both local consumers pass; the pinned source's 21 entity-team tests pass.
  Complete encounter, workspace and client acceptance are tracked separately.


### 2026-11-12

#### Restore Witch target retirement and late gate defeat

- Record actual body/controller identities after successful pair teardown and
  publish immutable room retirement facts to the next enemy update. Reads of
  known retired components return Null and existence returns false, so an old
  Witch staff target can cancel normally. Missing roster entries and unknown
  or faulted backends retain unavailable outcomes; no complete namespace is
  inferred from the combat target list.
- A high-capacity-gate Normal replay exposes the original Witch failure at
  tick 37117: 006B/133 reads old target variable 6604 and 006B/61 queries entity
  existence after that target has retired. Preserve this full-wave regression.
- Add eight language-neutral live/retired/unavailable query cases checked by
  the actual Rust enemy host/evaluator and normal Python e1128509 scene
  unbinding, variable slots and entity queries. Explicitly inventory/run the
  Rust consumer in both public CI lanes. Python differential checks are local.
- Restore foreign component-slot subscriptions in the VM, coalesce room-frame
  change notifications and remove subscriptions at state/instance teardown.
  Publish actual gate variable changes between gate and mode updates. This
  repairs the late failure transition, which previously stayed dormant after
  other countdown/actor changes stopped. Other foreign producers must publish
  their own actual changes; this is not a global engine callback reconstruction.
- Three shared synthetic 0C8C/33 SubScript flows agree with the pinned normal
  Python live-slot callbacks: completion on change, cancellation cleanup and
  immediate completion without rearming. The source loader receives synthetic
  records; handlers and callbacks are unchanged. Per-frame lifecycle is compared,
  not native callback timestamps. Rust also checks duplicate-notice coalescing.
- The complete Normal late-defeat replay passes at tick 33208 with Cassidy at
  225 health and 20503.89579885375 absorbed enemy damage. All five Bosses spawn
  naturally, complete their introductions and encode one camera instance each.
  Reaper/Roadhog move after introduction; ranged casters can keep a valid shot
  from spawn. A high-capacity gate admits every attack until introductions end;
  its remaining margin is then one point, and a new actual hit produces enemy
  winner 1 and result/post-match payloads. No attacker is removed or frozen.
- Test-only gate capacity changes the original 0CA7 local base-health input
  8567 through normal VM dependency notifications. Its 7786 Stack remains
  authored and checked. No production cheat or direct result trigger is added.

- Final validation: locked offline workspace/all-target tests and Clippy pass
  in default/inspector lanes (381/385 server-library tests, 117 ignored; wire
  23 pass/2 ignored). Both new consumers pass explicit inventory/exact checks
  in both lanes and normal pinned Python comparisons (8 cases/3 flows).
  Default prepared early/late defeat, common3/Boss15, observer/Witch,
  Zombardier, three-card encrypted entry, Deadeye/Flashbang/Roll and base
  Soldier body/rifle/Helix/Biotic checks pass. Existing foreign-component10
  and charge41 Python comparisons, formatting/diff and fresh public-rust
  ancestry/merge-tree checks pass. No client, push, MSRV/Linux or hosted-CI
  result is added. Full victory, score, visual/audio/gallery work remains open.

#### Verify natural Normal gate defeat without a client

- Add an external-runtime integration replay using the actual Normal mode,
  Cassidy owner VM, ordinary common-enemy waves, castle collision, projectiles,
  gate script and native result/recap payloads. Do not force gate-zero messages,
  remove attackers or replace their damage. Deliver defender messages/sightings.
- Explicit test-only invulnerability absorbs genuine enemy impacts while the
  owner remains at 225 health. The gate reaches zero normalized health and the
  native winner is team 1, with both result and post-match payloads available.
  The default run ends at tick 9666 after 2470.5412788391113 absorbed damage.
- This run spawns only Zomnic, Shock-Tire and Zombardier before defeat. It does
  not close Boss intro/camera, the late Boss defeat matrix, the Deadeye victory
  line, score, gate rendering or original-client acceptance. Gameplay is unchanged.
- Static score research confirms independent PVEScore UX bindings and authored
  tracking stats, but does not yet identify the native score field or formula.
  No arbitrary kill-point policy or speculative field mapping is introduced.

- Validation: locked offline workspace/all-target tests and Clippy pass in
  default/inspector lanes (379/383 server-library tests, 116 ignored; wire
  23 pass/2 ignored). The prepared natural-loss replay passes both lanes with
  the same tick, health and absorbed damage. Formatting/diff checks and fresh
  public-rust ancestry/merge-tree checks pass. No client or push is added.

#### Restore authored Cassidy charge and common enemy retirement

- Replace the 60-second host fill with the actual 0B8B passive wait/value
  path. Room admission supplies the live defender capability (2426); the
  authored graph owns its one-second/five-point growth, multipliers, damage
  conditions and cost cap. Death and encounter completion revoke admission
  and drain the change at the same clock, cancelling pending passive waits.
  Refresh admission in simulation/input/projectile phases, independently of
  presentation delivery. This scoped room policy does not reconstruct the
  complete native engine game-state broadcast.
- Route only actual applied pool loss through the owner damage entry. Retain
  current source identity, target flags, literal/effect tags and fresh foreign
  component values through synchronous dispatch; clear event facts afterward.
  Preserve owner-local VM fault handling. Target flags remain unavailable if
  their team cannot be encoded, preserving corrupt-registry/respawn checks.
- Preserve ordered modify-health asset tags through hitscan, direct projectile
  and blast impacts. Admit at most 16 tags per payload as a server resource
  bound (the prepared pack's maximum is eight); reject excess without silent
  truncation. Box both posed shot payloads to keep the mixed queue compact
  after adding this metadata. No wire layout or damage formula changes.
- Add A58C1E63 scalar host queries and the evidenced ordinary two-team branch,
  including first-team-bit selection, spectator exclusion and explicit
  unavailable outcomes outside the supported option/team layout. Existing
  explicit native backends retain precedence.
- Add 41 shared synthetic cases with pinned Python e1128509 expectations:
  passive timing and same-clock polls, damage/cap, zero/live multipliers,
  ordered half/three-quarter target reductions, friendly/no-charge targets,
  ultimate and round-end suppression, hostility and ordered asset tags.
  Rust also checks tags on actual hitscan/direct/blast results and rejects
  the 17th tag. The synthetic topology is separate from the prepared actual
  0B8B/room/owner integration probes; it supplies no original-client evidence.
- Prepared Boss15 now checks exact damage credit including real target-tag
  reductions and cap, rejects a foreign source, and retains complete death
  retirement/once-only defeat notice checks. The complete Deadeye replay uses
  the real passive fill, checks the first one-second boundary, spends charge,
  excludes self-charge and stops both credit paths at encounter end.
- Restore the pinned scene-host eight-second corpse grace for killed Zomnics
  and Zombardiers through normal body/controller teardown. Tire and Boss
  retirement paths remain separate. Ten shared boundary/whitelist cases use
  the normal Python host and retirement queue; the prepared common-NPC probe
  checks damage, exact charge, death and the precise retirement boundary.
  Update the Zombardier replay to retain its launch/observer/damage assertions
  when a live-launched grenade lands after normal source retirement; require
  the real retirement record instead of keeping a dead shooter bound.
- Team score, full successful/failed encounters, remaining presentation/audio/
  camera/Hero Gallery items and original-client acceptance remain open.
  No original client was started and no push occurred.

Final static validation passes locked offline workspace/all-target tests and
Clippy in default/inspector lanes (379/383 server-library tests, 115 ignored;
wire 23 pass/2 ignored), explicit contract inventory/exact checks, all 41
pinned Python cases, prepared common3/Boss15, complete Deadeye, Flashbang,
Roll, three-card encrypted entry, observer/Witch, Zombardier and base Soldier
body/rifle/Helix/Biotic probes. The final first-Reaper intro/pursuit/fire/stun
replay passes in 33.28 seconds; this is not a throughput claim. Existing
body18/voice38/Boss191 comparisons pass. No client, push, MSRV/Linux or
hosted-CI result is added.

#### Mount the complete Junkenstein defender body startup

- Replace the isolated 0CA5 intro receiver with the normal authored root and
  its startup entries. The first full-root probe exposed two real gaps;
  restoring them allows the full root to initialize without diagnostics.
- Add 00707E1E's first-config/recursive-first-value component44 body-field
  query with explicit controller-body facts. Preserve absent/unbound zero
  versus unavailable services/fields, and do not substitute current possession
  for this separate field. The authenticated controller exposes its actual
  body; the human body's absent AI controller resolves to zero.
- Resolve E9485CAF through build 104319's verified base State registration
  160 (AA826B0 / 8B6BC800), following the native parent-typeinfo lookup.
  This is a native factory fallback, not an inert implementation of an
  unknown state. Existing base state lifecycle remains authoritative.
- Add 18 language-neutral query cases checked through production Rust and
  normal pinned Python 46b22130 entity-query services. Keep that reference
  in a detached local checkout because the active intake branch advances.
  The fixture also records native fallback evidence; Rust exercises normal
  VM classification, while the Python comparison covers the query cases only.
- Final locked offline workspace/all-target tests and Clippy pass with
  default/inspector on Rust 1.99.1 (378/382 server-library tests, 114 ignored;
  wire 23 pass/2 ignored). The new consumer passes inventory/exact checks in
  both lanes. The 18-case pinned Python query comparison and existing
  38-case voice lifecycle comparison pass. Prepared complete Boss15,
  Flashbang/Roll/Deadeye, observer/Witch healing and three-card encrypted
  castle-gate/mode/selection delivery pass; base Soldier body/weapon-tree
  and encrypted rifle/Helix/Biotic regressions pass. Formatting/diff and
  fresh public-rust ancestry/merge-tree checks pass. No push, MSRV/Linux,
  hosted-CI or original-client result is claimed.
- The prepared Deadeye probe now asserts the real defender startup's active
  body-field stack and typed zero value before charging and firing. Ultimate
  damage credit, the temporary 60-second passive policy, team score and the
  complete success/failure gate remain unfinished. No client was launched.

#### Connect Boss speech services and complete strict death retirement

- Bind the enemy voice lifecycle to actual encounter/body voice-set facts,
  resource rules, weighted killer-specific defeat lines, raw 48 kHz sample
  durations and the existing room voice-notice route. Export actual hero IDs
  from player/NPC rosters; missing killer facts remain an explicit error.
- Read and validate the encounter narrator voice-set definition from authored
  0C8C node 240. Preserve default/themed body sets, target-shape errors and
  Summoner barrier 66's no-human-recipient behavior. General live voice-set
  override stacks and Python RNG seed-sequence equivalence remain open.
- Add 191 shared Boss speech cases: every defeat row, generic-only killers,
  weighted draw boundaries, default/themed/unsupported skins, spawn lines,
  barrier recipients, permissions, unknown cues and rejected targets/sites.
  Pinned Python e1128509's normal backend records the independent results;
  Rust consumes the same fixture through production policy functions.
- Locked offline workspace/all-target tests and Clippy pass with default and
  inspector on local Rust 1.99.1 (377/381 server-library tests, 114 ignored;
  wire 23 pass/2 ignored). Both voice contracts pass pinned Python comparison.
  Prepared all-card, Cassidy Flashbang/Roll/Deadeye, observer/Witch healing,
  Zombardier and voice-wire regressions pass. Complete first-Reaper replay
  passes in 179.79 seconds; this is not a performance benchmark. Base-pack
  Soldier body/weapon-tree and encrypted rifle/Helix/Biotic checks pass.
  No MSRV/Linux/hosted-CI or original-client evidence is added.
- The complete normal-Cassidy primary/fan probe passes all 15 Boss deaths,
  authored retirement and exactly one eligible defeat notice per death.
  This closes the observed missing Boss voice context failure only. Full
  win/loss, legacy intro/gate/victory migration and the remaining requested
  gameplay/presentation checklist still require static work before client
  verification. No client was launched and no push occurred.


#### Restore strict shared voice-state callbacks

- Add the 40E05F6B begin/poll/timer lifecycle with explicit context, manager,
  clock, resource, permission, submission, duration and next-part services.
  Preserve known absence versus unavailable services, float32 duration math,
  strict timeout boundaries, six-attempt handling and byte-sized part state.
- Keep +80 polling separate from ordinary ticks/deferred callbacks; the room
  dispatches it after all ordinary actor updates. Select only registered
  voice components before taking mutable actors, and allocate voice caches
  only for voice states instead of increasing every script state's payload. Timer 1 publishes cached
  duration; timers 1/2 own the voice finished plug. State end follows only
  the end plug. Failure notification, abort, fault and destroy unregister
  callbacks and cancel stale voice events without repeating finished output.
- Supply cached voice-stimulus reads, keyed-resource strictness, recursively
  first-asset selection and native first-config scalar evaluation. Reuse VM
  expression diagnostics and supported ordered group/value-array iteration.
- Add 38 synthetic cases with independently recorded expected service traces,
  phase states, typed output values and exact f64 bits from pinned Python
  e1128509. Production Rust VM and normal pinned Python consumers pass;
  Windows CI explicitly registers Rust inventory/exact runs in both lanes.
- Final locked offline workspace/all-target tests and Clippy pass on local
  Rust 1.99.1 with default/inspector (376/380 server-library tests, 114 ignored;
  wire 23 pass/2 ignored). Contract inventory/exact checks pass in both lanes.
  The 38 scenarios contain 164 independently stored observations; Python
  comparison also verifies a clean pinned script checkout.
- Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch
  healing and Zombardier replays pass. The final complete first-Reaper replay
  passes in 235.48 seconds; this wall time is not a throughput benchmark.
  Base-pack Soldier body/weapon-tree and encrypted rifle/Helix/Biotic Field
  regressions pass. Formatting/diff checks pass. No MSRV/Linux/hosted-CI
  evidence or new original-client acceptance is claimed.
- The final complete Boss probe still fails all 15 retirements, now at
  unavailable voice contexts. No unsupported-state/query class is observed
  there; the missing backend remains a real failure, not completed audio.
  Public rust f763beb9 is already integrated; ancestry/merge-tree checks
  pass. No push occurs.
- The Boss speech-resource/line-selection backend is still unavailable in the
  enemy World. Existing intro/gate/victory specializations retain their prior
  behavior pending migration. Native iterator services and broader component
  fault-policy equivalence remain separate gaps. This is shared machinery,
  not completed Boss retirement or original-client audio acceptance.
- No original client was launched. The full static gate, gameplay/UI/voice/
  camera/Hero Gallery checklist and both match outcome branches remain open.

#### Restore Witch ally-filter and target-query semantics

- Admit the pinned scene's 006B/0071 friendly-other filters with selector 1
  and relation mask 4. Use delivered filter bits and actual literal tag facts;
  exclude the source actor and hostile/unknown teams. Do not silently add an
  alive check where the authored filter has no dead-mask exclusion.
- Scope candidate parameter 8 during friendly predicates, restore it on
  success/error/nesting, and preserve the reference's true scalar initial
  value. Keep ordinary/hostile semantics and fold/outer parameters intact.
- Run 0071's B3E67A36 query over ordered bound combat actors with explicit
  host shape admission. Keep 0CCD's literal primary-tag query separate.
  Preserve nested predicate diagnostic classes rather than relabeling every
  child failure as a missing combat-target query.
- Add 28 synthetic friendly filter/target cases, production Rust and pinned
  Python consumers, and Rust CI inventory/exact registration. Cases cover
  team-bit precedence, self exclusion, tag/mask handling, ordered candidates,
  20-metre distance limits, default predicate values, error cleanup and
  nested candidate scopes. Local Python e1128509 comparison passes.
- Locked offline workspace/all-target tests and Clippy pass on local Rust
  1.99.1 with default/inspector (375/379 server-library tests, 114 ignored;
  wire 23 pass/2 ignored). The ally, hostile and tagged combat-target
  contracts pass explicit inventory/exact checks in both lanes. Both pinned
  Python runners pass all 57 synthetic cases. Prepared all-card, Cassidy
  Flashbang/Roll/Deadeye, enemy observer/Witch healing and Zombardier replays
  pass. No MSRV/Linux/hosted-CI or new original-client evidence is claimed.
- Base-pack Soldier body/weapon-tree and encrypted rifle/Helix/Biotic Field
  regressions pass, as do formatting/diff checks. Public rust f763beb9 remains
  integrated; fresh ancestry/merge-tree checks pass. No push occurs.
- The final complete Boss probe has no B3E67A36 diagnostics; all 15 retirement
  cases still fail at the newly reached 40E05F6B voice states. Full static
  acceptance remains open. No original client was launched.

#### Restore delivered target facts and combat-target queries

- Deliver actual owner/NPC filter bits and target-tag snapshots, including
  current grant/suppression effects. Preserve unknown catalog tags and
  immutable snapshot lifetimes. Remove the Summoner defender-tag guess.
- Support the pinned scene's scoped hostile killer filters in 05AC/01A3/157C:
  first-set team-bit relation, required/excluded literal tags and 01A3 scalar
  death predicates. Ordinary filter semantics remain separately scoped.
- Run 0CCD's B3E67A36 live tagged-body query over the ordered bound combat
  roster with real masks/tags, short-circuiting and work limits. Unknown
  inputs remain strict failures; no forced retirement or synthetic no-target
  result replaces an unavailable scene.
- Add 17 hostile-filter and 12 combat-target synthetic cases, production Rust
  consumers, a pinned Python comparison runner and explicit Rust CI inventory/
  exact registration. Both fixtures agree with Python e1128509 locally.
- Final locked offline workspace/all-target tests and Clippy pass on Rust
  1.99.1 with default/inspector (374/378 server-library tests, 114 ignored;
  wire 23 pass/2 ignored). Both new contracts pass inventory/exact checks in
  both lanes. Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy
  observer/Witch healing, Zombardier and complete first-Reaper replays pass
  (179.29 seconds for Reaper). The synthetic Summoner defender now explicitly
  supplies known team-filter and empty-tag facts instead of relying on the
  removed production guess. No MSRV/Linux/hosted-CI evidence is claimed.
- Base-pack Soldier body/weapon-tree and encrypted rifle/Helix/Biotic Field
  regressions pass. Formatting/diff checks pass. Current public rust f763beb9
  is already integrated; ancestry and merge-tree checks pass. No push occurs.
- The complete 15-case Boss death probe remains RED: AC829876 diagnostics
  are gone and 0CCD tagged-target branches advance, exposing unsupported
  40E05F6B states plus Witch 0071 B3E67A36 queries. Full static acceptance
  and the user's original-client checklist remain open. No client launched.

#### Restore authoritative total-health reads

- Route 9B7648C3 through a typed present/unbound/unavailable health query,
  using actual owner/room vitals. Preserve no-write destinations, native
  first-config scalar selection and strict unavailable readers. Enforce the
  pinned reference's non-negative upper bound before float32 conversion;
  retain separate normal/current/maximum pool query paths.
- Publish real totals in enemy/defender facts and a fallible room health
  snapshot for Deadeye. Keep still-bound corpse health available after the
  corpse leaves live target selection; no health is inferred from alive/team
  flags or script-variable defaults. Registry failures return errors.
- Add 21 shared synthetic cases, normal Rust/Python consumers pinned to
  e1128509 and Rust CI inventory/exact registration in both feature lanes.
  Cases cover zero, missing/unbound data, invalid ranges, float32 conversion,
  typed initial values and selectors. Explicit f64 bits encode the extreme
  boundary, preserving exact results despite JSON decimal-parser drift.
  Local Python comparison passes; hosted differential registration is open.
- Add VM health/death/unbinding polling, corpse-versus-live-selection and
  corrupt-registry regressions. Locked offline workspace/all-target tests and
  Clippy pass on Rust 1.99.1 with default/inspector (371/375 server-library
  tests, 114 ignored; wire 23 pass/2 ignored). Formatting/diff checks and
  explicit new-contract inventory/exact runs pass.
- Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch
  healing, Zombardier and complete first-Reaper replays pass (Reaper wall
  time 166.71 seconds). Existing base-pack Soldier body/weapon-tree and
  encrypted rifle/Helix/Biotic Field regressions pass.
- The final complete Boss probe still fails all 15 retirements, with
  9B7648C3 gone. Remaining observed classes are AC829876 and B3E67A36.
  Partial actor discovery/retired lookup and same-frame ordering also remain
  open. No gameplay/client checklist is closed, original client started,
  deployment performed or public branch pushed. No MSRV/Linux/hosted-CI
  result is inferred from local checks.

#### Restore mover queries and death membership branches

- Implement 309B0570 through a shared unavailable/unbound/present velocity
  interface. Read actual owner, room-bot and delivered defender movement;
  carry velocities in Deadeye targets. Preserve no-write initial values,
  finite float32 conversion and strict unsupported mover variants. Replace
  the scoped Tire/Roll velocity callbacks, keeping Roll's other vectors.
- Implement the native first-config scalar selector with expression depth/
  work bounds, and register foreign/live mover reads for VM tick polling.
  Undelivered actors and controllers stay unavailable; complete actor
  discovery/retired lookup and same-frame notification order remain open.
- Once velocity reads succeed the death graph reaches 821116A6. Restore
  that shared contains condition using the existing VM membership/equality
  primitive and ordinary branch routing, without a death-site allowlist.
- Add 19 velocity and 16 contains language-neutral cases with production
  Rust/Python consumers pinned to e1128509. Both local Python comparisons
  pass without a runtime pack. Add live mover polling, selector budget/depth
  and explicit legacy-host checks. Windows CI inventories/exactly runs both
  new Rust consumers in default/inspector lanes; public Python counterpart/
  differential CI registration remains pending.
- Locked offline workspace/all-target tests and Clippy pass on Rust 1.99.1
  with default/inspector (368/372 server-library tests, 114 ignored; wire
  23 pass/2 ignored). Formatting and explicit new-contract inventory/exact
  runs pass. No MSRV, Linux, hosted-CI or original-client result is inferred.
- Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch
  healing, Zombardier and complete first-Reaper replays pass. Existing base
  runtime Soldier body/weapon-tree and encrypted rifle/Helix/Biotic Field
  regressions pass. The full Boss probe still fails all 15 retirements:
  309B0570 and 821116A6 diagnostics are gone; remaining observed classes are
  AC829876, 9B7648C3 and B3E67A36. Keep the full static/client gates open.
  No original client, deployment or public push is performed.

#### Integrate room fault isolation with encounter delivery

- Merge public Rust f763beb9 / PR #31 while preserving contributor history,
  encounter features and both sides' tests. Resolve battle/replication/scene,
  service-module and documentation conflicts. Move encounter targets,
  scripted health, NPC creation/retirement, damage, voice/sightings and result
  exit into room-owned member access and guarded worker phases.
- Preserve checked combat/actor queries in the restored helpers. Deadeye
  scene preparation now returns errors through its owner interface instead
  of substituting an empty target scene. Propagate healing, projectile and
  history failures through the new room error boundary.
- Replace the stale entrance test's frozen 112-bit frame and fixed sequence
  assumption with exact prepared-baseline delivery plus recognition of the
  authored countdown variable. Keep exact retransmission/value checks; the
  independent all-card descriptor/parent decoder remains required.
- Locked offline workspace/all-target tests pass on local Rust 1.99.1 with
  default/inspector (364/368 server-library tests; 114 ignored, wire 23 pass/
  2 ignored). Both Clippy lanes, formatting and diff checks pass. No MSRV,
  Linux or hosted-CI result is inferred.
- Prepared Revenge/Endless/three-player gate, complete mode frame and
  countdown delivery pass. On the existing base pack, fault injection keeps
  another room's input, world, hits and patrol working; encrypted stock
  Soldier rifle/Helix/Biotic Field regressions pass. The encounter-only pack
  lacks Practice Range collision; its initial prerequisite failure is not
  counted as a passing recovery run or hidden by changing the assertions.
- All-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch healing,
  Zombardier target/projectile and complete first-Reaper static replays pass
  after integration (first-Reaper wall time 172.09 seconds). The strict Boss
  probe still fails all 15 retirements at AC829876/309B0570/9B7648C3/B3E67A36,
  with no foreign-variable diagnostic. Full gameplay/UI/audio/client gates
  remain open. No original client, deployment or public push is performed.

#### Room fault containment

- Moved pending and active peers into their owning room and separated room operations from worker routing and scheduling. Added transactional admission, idempotent whole-room retirement and a sender that borrows only one connection.
- Introduced typed combat, room and worker faults with retained causes. Failed room work now retires its peers, input, delivery queues and projectiles; a room-local unwind fallback handles unexpected panics. Ambiguous ownership and fatal shared transport failures still stop the worker. Normal leave grace, malformed-request rejection and local bot/flight recovery retain their scope.
- Propagated previously discarded combat, respawn and player-request failures. Delayed work from an older body generation can no longer clear newer projectiles. Preserved all-or-nothing respawn preparation.
- Added a synthetic recovery contract, encrypted two-room regressions, cleanup and partially modified-state checks, and bounded room-fault records in the optional read-only inspector. The contract is a Rust recovery policy with no Python or original-server equivalence claim.
- Passed formatting, both Clippy lanes, 375 default / 378 inspector public tests, exact Rust 1.85.0 checking, all 66 prepared baseline checks and two new prepared tests. Those tests cover malformed versus failed player requests and surviving input, world delivery, hits, patrol and new admission after real ECS failures or input panic. Native runtime preflight/startup, JavaScript syntax and documentation links passed. Hosted checks for this change are pending; original-client acceptance and same-room multiplayer remain open.
- Earlier foundation and World-query work passed all five hosted jobs and merged through PRs #28 and #30. Typed StateScript lowering and the large `script_init.rs` split remain open.

#### Restore reference corpse-retirement entry points

- Admit Tire 0C93/11 and Boss death 0CBC/36,38 at 523EE38D, matching pinned
  Python e1128509. Preserve existing 0C89/43 and phase-change 0CBC/57,58.
  Pass phase-exit explicitly from the VM; only the latter two sites set it.
  The enemy host still requires its own body/controller pair and an actual
  death outside phase exit. Preserve authored waits and queued room cleanup.
- Add a 12-case shared synthetic contract and normal Rust/Python entry/action
  consumers for all six sites, body/controller arguments, phase flags, host
  refusal, invalid arguments and unknown sites. Both local consumers agree.
  Register Rust inventory/exact runs in both Windows feature lanes; public
  Python counterpart/differential CI registration remains pending.
- The complete Normal first-Reaper prepared replay now passes: the boss
  leaves its intro point, pursues and attacks a living defender, stops under
  control and resumes combat afterward. Local wall time is 194.42 seconds.
  This isolates the handoff by killing common attackers through ordinary
  damage/death; it does not establish actual client animation/audio/camera,
  once-only presentation, all bosses or complete victory/failure branches.
- Locked offline workspace/all-target tests and Clippy pass on Rust 1.99.1
  with default/inspector (349/353 server-library tests, 23 wire tests; 112
  server and 2 wire opt-in checks remain ignored). Formatting and explicit
  contract inventory/exact runs pass in both feature lanes.
- Prepared Zomnic gate damage/paired retirement passes. The strict complete
  Boss fire/death probe still fails all 15 retirements at AC829876,
  309B0570, 9B7648C3 and B3E67A36. Foreign variable diagnostics remain absent.
  Keep the full static gate open; no original client or GitHub push is used.

#### Deliver foreign script-component variables from authoritative bodies

- Add an explicit unavailable/absent/running component-state projection.
  Room NPCs publish actual body variables; faulted or missing runtimes stay
  unavailable. Basic room bots declare their lack of a server component.
  Human defender delivery and Deadeye target scenes carry real snapshots.
  Known unset keys preserve null, and typed false/zero/array values remain
  intact. No variable-specific fallback or synthetic actor properties are used.
- Foreign member dependencies now register tick polling. A VM regression
  replaces the room snapshot across four frames and checks branch exits and
  entries, including explicit component absence. This frame-boundary transport
  does not establish Python's exact same-frame cross-component notification
  ordering; unprovided actors/controllers remain unsupported.
- Add a ten-case shared synthetic contract, normal Rust/Python evaluator
  consumers and Windows CI inventory/exact-run registration for both feature
  lanes. Pinned Python e1128509 agrees for unavailable and faulted backends,
  absent components, unset keys and typed active values. A public Python
  counterpart/hosted differential CI registration remains follow-up work.
- Locked offline workspace/all-target tests and Clippy pass on Rust 1.99.1
  with default/inspector features (348/352 server-library tests, 23 wire
  tests; 112 server and 2 wire opt-in checks remain ignored). Formatting and
  explicit new-contract inventory/exact runs pass in both feature lanes.
- Prepared all-card/HUD, Flashbang, Roll, Deadeye, enemy observer/Witch-healing
  and Zombardier live-target/projectile replays pass. The first-Reaper
  integrated replay is RED before its acceptance point: a Tire corpse at
  tick 7746 reaches unsupported 523EE38D at graph 0C93/node 11. The pinned
  Python handler already admits this site; the Rust dispatcher does not.
  Keep this independent corpse-action gap open, with no intro/pursuit claim.
  The complete Boss fire/death probe no longer reports unavailable killer
  member 4437; all 15 retirements remain RED at AC829876, 309B0570,
  9B7648C3 and B3E67A36. This does not close the encounter or live-test gate.
  No original client, deployment or GitHub push is performed.

#### Integrate strict World query availability

- Merge public Rust 437a6eb4, preserving contributor history and both local
  StateScript contracts. Resolve evaluator, VM test, player adapter and
  changelog conflicts. Keep authored map lookups and local VM regressions.
- Adapt encounter, gate and enemy hosts to explicit optional position and
  variable queries. Known tables retain unset keys; unknown entities and
  unavailable positions no longer fabricate null or the origin. Preserve
  the explicitly supplied defender sighting position. Propagate missing
  positions through entity creation and optional wave-health reads.
- Local Rust 1.99.1 locked offline workspace/all-target tests and Clippy pass
  with default/inspector: 346/350 server-library tests, 23 wire tests and the
  new strict World query contract. The 112 server and 2 wire opt-in checks
  remain ignored in those totals. Formatting passes. Python e1128509 still
  matches all 14 engine-setting and 19 array cases. No hosted CI/MSRV result
  is inferred.
- Prepared all-card/HUD, Flashbang and Roll replays pass. Stricter queries
  expose two additional RED acceptance probes: Deadeye target variable 8243
  is unavailable in the player adapter, and Witch ally variable 6604 is
  unavailable in the enemy adapter. The 15-case Boss fire/death probe still
  fails all retirements, with foreign killer variable 4437 unavailable in
  addition to AC829876/309B0570/9B7648C3/B3E67A36. Earlier pre-merge prepared
  passes do not establish current acceptance. Restore real foreign-entity
  state delivery; do not return synthetic unset values to silence errors.
- This is a local integration checkpoint. No original client or deployment
  is started, no checklist item is closed, and no protected branch is pushed.

#### Explicit World query availability

- Made World position and foreign-variable methods required `Option` queries. Propagated unavailable results through expression member access, vector arithmetic, length and distance with typed diagnostics instead of invented origin or unset values.
- Preserved known origin, known unset variables, null entity member reads, scalar coercions and own-variable watchers. Restricted the body adapter to its own position and available mode-variable tables; an absent table is distinct from an absent key.
- Added five internal regressions and eleven shared synthetic cases consumed through production Python and Rust expression paths. Pinned the Python consumer from PR #29 in differential CI. The shared lane runs on Windows for the server VM; the independent crate lane remains on Linux.
- Passed formatting, both Clippy lanes, 360 default / 363 inspector public tests, exact Rust 1.85.0 checking, all 66 prepared baseline checks, native headless preflight/startup and 35 local documentation links. Hosted checks for this change are pending. Python component failure and Rust expression diagnostics remain distinct; original-client acceptance is pending.

#### Restore typed engine-setting queries for encounter scripts

- Implement A753DC79/CF4A60 through a typed host interface that distinguishes
  an unavailable service, an available table with no row, and a computed
  value (including false/null). Preserve the caller's initial value for an
  absent row; retain strict unavailable-query diagnostics and explicit legacy
  host overrides. Dispatch integer hashes as uint32 and reject invalid types.
- Share the pinned 104319 headless Junkenstein table across encounter, gate,
  enemy and player adapters. Preserve its bool false, int 128 and float 1.0
  values; non-encounter owner/enemy hosts retain unavailable behavior.
  This is the declared reference host table, not a claim about arbitrary
  original-client engine preferences or uncaptured settings.
- Add a 14-case language-neutral contract and normal-evaluator consumers in
  Rust and Python. The pinned Python e1128509 runner passes with no runtime
  pack. Cases cover typed values, computed null, absent/unavailable tables,
  signed/wide uint32 hashes and invalid key types. Add live-host polling and
  owner-mode boundary regressions, plus a check that all-card preparation has
  no remaining A753DC79 diagnostic. Register both StateScript contracts in
  Windows CI with inventory checks and immediate failure propagation.
- Locked offline workspace/all-target tests and Clippy pass with default and
  inspector features on Rust 1.99.1: 341/345 server-library tests respectively,
  plus 23 wire tests; 112 server and 2 wire opt-in checks remain ignored.
  Formatting and explicit inventory/exact-run checks for both contracts pass.
- Prepared all-card/HUD, enemy observer/healing and the three Normal Cassidy
  skill replays pass. The strict five-Boss/15-case fire/death probe remains
  RED, with 15 failed retirements. Its engine-setting diagnostic is gone;
  remaining observed classes are AC829876, 309B0570, 9B7648C3 and B3E67A36.
  Killer filtering, velocity, foreign health and tagged-target services still
  need restoration. No gameplay/client checklist item is closed and no
  original client is started.

#### Integrate the public wire-foundation update

- Merge public Rust 3d82bc20 into the Halloween integration branch, preserving
  upstream authorship and the independent wire crate. Resolve the additive
  changelog conflict by retaining both histories; protocol re-exports merge
  automatically. No protected branch update or topic push is performed.
- Follow the new behavior-contract workflow with a 19-case public synthetic
  StateScript array fixture, a normal-VM Rust consumer, and a local Python
  comparison runner that checks the exact e1128509 source revision. Register
  the Rust consumer explicitly in Windows CI and check its test inventory.
  Python public-CI registration remains pending verification/review of an
  available public comparison ref; do not claim cross-language CI coverage.
- Merged workspace/all-target tests and Clippy pass with default and inspector
  features on Rust 1.99.1. Default results include 336 server-library and 23
  wire tests; 112 server-library and 2 wire opt-in checks remain ignored.
  The local toolchain contains no Rust 1.85 installation, so its separate
  CI lane is not claimed as locally verified. Portable Linux and hosted CI
  results are also not inferred from the Windows runs.
- Post-merge all-card preparation/HUD, enemy observer/healing and three Normal
  skill replays pass. The complete Boss death probe still reaches 15 failed
  retirements with the same five unsupported query classes; array insertion
  is absent from its diagnostics. All remaining gameplay/static gates and
  original-client acceptance remain open, and no client is started.

#### Shared Python and Rust behavior contract

- Replaced the inline JAM framing vector with a versioned synthetic JSON contract shared with Python PR #27. Rust and the pinned Python consumer use their production sender and receiver against the same fixed bytes and fragmented stream.
- Added a differential CI lane and contribution rules for fixture provenance, gradual backfill, documented evidence gaps and separate original-client acceptance. Documented topic-branch PR publication for protected branches. CI verifies that the registered Rust test exists before running it.
- Passed formatting, both Clippy lanes, 354 default / 357 inspector public tests, exact Rust 1.85.0 checking, all 66 prepared baseline checks, headless runtime startup, 20 local documentation links and the Python consumer against this checkout's fixture. Hosted cross-language and Linux results are pending. The synthetic contract establishes framing agreement only; gameplay and original-client acceptance remain open.

#### Minimum Rust and portable crate CI

- Added a Windows Rust 1.85.0 workspace/all-targets check with inspector enabled and an Ubuntu stable test lane for collision, runtime and wire. Retained both existing public check names.
- The exact Rust 1.85.0 command passed locally. Both public lanes, both Clippy lanes, formatting and all 66 prepared baseline checks passed for the same Rust source. Hosted Linux verification is pending.

#### Independent protocol crate

- Extracted bits, commands, JAM cipher/codecs/framing and the game datagram codec into `orpheus-wire`, preserving existing server import paths and sharing the array-count limit with its collection caller.
- Passed formatting, both Clippy lanes, 354 default / 357 inspector public tests and all 66 prepared baseline checks using their recorded base or enriched pack. The new crate runs 23 public and two prepared tests independently. Original-client acceptance remains open.

#### Rust foundation review baseline

- Recorded the maintainer-supplied review as a scoped task tracker, including typed IR, unavailable World queries, room retirement, reference fixtures and diagnostic boundaries.
- Verified formatting, both Clippy lanes and 354 default / 357 inspector public tests at `13acf15`. Passed 65 base-pack prepared tests and the graph `0B92` test with an enriched pack. Recorded per-test prerequisites and manifest digests. Original-client acceptance remains open.

#### Share bounded native unkeyed array insertion

- Replace the staff-recipient and spawned-body append allowlists with shared
  STU_4C3509FB merge/insertion and STU_CBE8BDAA single-value insertion.
  Preserve ordered recursive flattening, nested single values, native equality
  for uniqueness, first-provider index evaluation, float32 index conversion,
  int-zero gaps and self-iterator termination. Later providers see prior rows;
  watchers are notified once after the writable array is released.
- Keep missing/zero/known constant outputs and invalid keys as no-ops. Valid
  keyed storage and linked-variable checkpoints remain explicit unsupported
  boundaries. Keep the native 200-row diagnostics before/after mutation and
  bound traversal, comparisons, shifts and publication copies by VM budgets.
- Admit the known empty-key 7BD2CA19 no-write result with the caller's initial
  value, preserving explicit host inputs before that fallback. This keeps
  Arcade difficulty admission intact while distinguishing iterator omission,
  scalar -1/0 defaults and the single-value interface's int-zero default.
- Add six focused Rust checks for merge/single-value semantics, order,
  uniqueness, invalid inputs, provider visibility, self references, native
  index/default behavior, host input priority and strict work/storage limits.
  Python e1128509's independent array suite passes 27 tests.
  Locked offline workspace/all-target checks pass: 358 library, 1 protocol,
  7 preflight, 53 collision, 9 runtime and 1 CLI; 114 opt-in library tests
  remain ignored. Formatting and all-target workspace Clippy pass.
- Repair a stale all-card HUD test, reproduced unchanged at 9c0935be in a
  separate source-only worktree. Decode all descriptors and require the six
  actual HUD graphs and their parent bindings; do not assume graph creation
  order or equate the client common UI root with the server common graph.
  All card difficulties/challenges, prepared enemy observer/healing checks,
  10 Boss-script checks and the three Normal Cassidy skill checks pass.
- The strict 15-case complete Boss fire/death probe remains RED. All cases
  reach the retirement assertion and none retire, but the 01CA/118 array
  insertion diagnostic is gone. Engine settings, killer-relative filters,
  velocity, foreign health and tagged combat-target queries remain open.
  This does not close gameplay, presentation, win/loss or client acceptance.

#### Enable Cassidy fan fire and dispatch encounter deaths

- Supply Cassidy's authored 0026 ruleset default 290C=true in Junkenstein.
  The held secondary trigger previously reached 01D2/93 but stayed on its
  false branch, producing no shots. Keep the rule scoped to Cassidy and the
  encounter modes; other heroes and unknown rules retain their old behavior.
- Admit an explicit caller-provided critical fallback in the shared hit
  resolver. Cassidy's 01D2/31 uses the primary-only 2x policy declared by
  Python e1128509; explicit authored multipliers still take precedence.
  This is a declared reconstruction, not a recovered global native default.
- Forward an encounter body's first authoritative combat death into 003E
  with killer, critical flag, loadout and empty assists. Preserve same-frame
  delivery after a body update; ordinary training-bot respawns stay separate.
- Add an opt-in full damage/kill/retirement probe. All 15 stationary street
  cases (five Boss bodies, primary body/head and fan head) reach the final
  retirement assertion: primary damage is 70/140, fan damage is 50 with six
  shots and an empty clip, hit/kill notices match the authoritative pools,
  unacknowledged/dead bodies reject hits, and component 51 contains the same
  remaining health. These use the actual Normal owner graphs, room ray
  query, Boss body/pools, wave setup and map collision. AI is not stepped
  concurrently with the shooter's frames, so pursuit and intro acceptance
  are not covered by this fixture.
- The full probe FAILS: all 15 deaths remain unretired. Delivering DIED now
  exposes unsupported death services, including 0075/33 engine settings,
  01CA/118 unique recipient insertion, 05AC/01A3 killer-relative filters,
  01D8/98 killer velocity and 0CCD tagged combat-target queries. Witch also
  needs foreign health/target facts. Do not deploy or start client validation
  until these paths and the remaining complete static checklist pass.
- Locked offline workspace/all-target tests pass (352 library, 1 protocol,
  7 preflight, 53 collision, 9 runtime, 1 CLI; 114 opt-in library tests ignored),
  as do Clippy and the prepared Flashbang, Roll and Deadeye checks. The pinned
  Python gun comparison passes its one test with 16 subcases using the
  external runtime pack. This does not make the failing full probe green.

#### Run deferred effect callbacks and ordered array removal

- Implement shared STU_9C9A422A event/tick/deferred phases with bounded
  per-instance batches, descending priority and reverse registration order
  for ties. State exit cleans queued callbacks and the scoped subgraph;
  destroyed instances discard their deferred queues.
- Implement STU_DA54F8F7 ordered index removal through shared variable writes.
  Missing, nonarray and out-of-range values preserve the output traversal.
- Synthetic tests cover priority, phase order, cancellation timing, scoped
  cleanup, bounded requeue and array order/bounds. Independent Python
  e1128509 replay matches all three cancellation traces and active states.
- Locked offline workspace/all-target tests pass: 351 library, 1 protocol,
  7 preflight, 53 collision, 9 runtime and 1 CLI; 113 opt-in library checks
  remain ignored. Workspace/all-target Clippy passes. Prepared effect, Normal
  Cassidy skill and 10 Boss-script checks pass. The authored Normal first-Reaper
  replay also passes intro departure, pursuit/damage, control suppression
  and resumed attack (192.67 seconds).
- Replace the mode-only effect fixture with the actual Reaper body and
  EnemyWorld adapter. Prepared 0228/224E initialization and 100 subsequent
  frames have no diagnostics; the deferred queue drains and its remote
  schema state remains active. The current entity filter does not take the
  bit-0x40 branch, so this does not close immunity, stun presentation, native
  control projection or the full effect lifecycle. All client checks remain
  pending behind the user's complete static-checklist requirement.

#### Load inline effect scripts with native instance policies

- Extend shared PlayScript resource evaluation for inline graph overrides.
  Reuse the bounded authored-map implementation for low-32-bit identifier
  collisions and native bucket order; a valid inline resource precedes the
  direct fallback. Keep empty resources flowing through the parent output.
- Preserve skip/create/reuse/replace policies and nonzero wire-byte lifetime
  semantics. Reuse retains allocation source/identity and existing event
  order; replacement retires all matching instances. Defaults are evaluated
  against the pre-assignment callee, then queued before overrides and startup
  inputs. Once-only start entries stay disabled on reuse and reset on a new
  allocation. Replacing the caller stops its outgoing action traversal.
- Synthetic checks cover map order, duplicates, missing/invalid input,
  inline selection, queued schema order, all four policies, retained source
  and lifetime flags, once-only entries, empty output and self-replacement.
  Native-order output also matches the pinned Python resource resolver.
- Locked offline workspace tests pass: 352 library, 1 protocol, 6 preflight,
  53 collision, 9 runtime and 1 CLI; 113 opt-in library checks remain ignored.
  Formatting and all-target workspace Clippy pass. Prepared boss script and
  Normal Cassidy skill checks pass; selected Python resource/schema/queue
  comparisons pass 31 tests.
  The first authored Normal Reaper replay also passes intro departure,
  pursuit, damage, control-window suppression and resumed attack.
- The prepared 0228 wrapper now creates 224E with the authored duration,
  identifier, loadout and effect overrides. It then reports unsupported
  deferred state STU_9C9A422A at 224E/171. The prepared check explicitly
  retains this diagnostic; it is resource/schema coverage, not successful
  full hit-effect execution. Deferred execution, immunity and native
  control/reaction projection remain static gaps. No client is started.


#### Bridge Flashbang hit control into NPC combat

- Read Flashbang effect 0079's duration override from authored 0228/7 rather
  than hardcoding the mode branch. Carry the validated control duration with
  the owner projectile through generation-owned room admission. Apply it to
  surviving damaged targets, extend repeated hits, use an exclusive deadline
  and discard the effect with the body generation or projectile lifetime.
- Add a shared living-body input block: release held logical buttons, stop
  running abilities through their authored exits, clear buffered presses and
  pending shots, and keep passive timers running. NPC selection, movement
  and weapon emission honor control without marking the body dead or changing
  the room's cinematic pause. Recovery reacquires ordinary inputs and targets.
- Patrol control retains the route and bounds resumed movement to unpaused
  ticks; it does not replay the paused interval. Synthetic checks cover held
  button release, channel interruption/recovery, duration validation, lethal
  and unaffected targets, repeated extension, exclusive expiry, and replacement.
- The prepared duration-expression check covers both authored mode branches
  (50 and 107 ticks). Normal E-input, Roll and Deadeye replays pass. The first
  authored Normal Reaper replay now injects control after his first attack,
  verifies no movement/damage during the window, then observes another attack.
  The control injection is separate from the E/projectile-impact checks.
- Locked offline workspace tests pass: 347 library, 1 protocol, 6 preflight,
  53 collision, 9 runtime and 1 CLI; 112 opt-in library tests remain ignored.
  Formatting and all-target workspace Clippy pass.
- This is the scoped control reconstruction from Python e1128509, with the
  duration supplied by the target graph. Full 224E effect lifecycle, immunity
  branches, native hit-reaction animation and control-effect wire projection
  remain open static work. Flashbang and broader boss acceptance stay open;
  no original client is started while the static checklist is incomplete.


#### Admit Cassidy Flashbang flight and range-end blast

- The owner projectile bridge now accepts Cassidy 01E9/7's known single
  unconditional gravity motion instead of silently dropping the volley.
  Resolve its authored upward launch bias, gravity and existing predicted
  lease, and retain these settings through room-flight admission.
- Reuse the shared flight and splash queries for a scoped Flashbang
  range/lifetime-end blast. Other projectiles retain their existing expiry
  policy. A zero-damage direct definition no longer suppresses a valid
  positive-damage blast; target eligibility and wall occlusion stay shared.
- Prepared Normal Cassidy E input emits exactly one 30 m/s, gravity-20
  projectile, with 7/30-second lifetime, upward bias 0.05, radius 3 and damage
  25. The owner VM and full owner wire projection remain valid after 80 ticks.
  Synthetic checks cover admission retaining motion/expiry settings,
  off-axis splash, range exclusion, occlusion and once-only retirement.
- Locked offline workspace checks pass: 344 library, 1 protocol, 6 preflight,
  53 collision, 9 runtime and 1 CLI tests; 111 opt-in library tests remain
  ignored. Prepared Normal Flashbang, Roll and Deadeye replays pass; formatting
  and all-target workspace Clippy pass.
- Expiry detonation follows the scoped Python e1128509 reconstruction;
  native seed/presentation parity is not established. Hit-effect stun,
  interruption, recovery, impact/control replication and broader Flashbang
  acceptance remain open. Per the user's updated instruction, all listed
  static gaps must be resolved before the next original-client session.


#### Restore authored common-NPC movement inputs

- Runtime preparation with `--target-tags-xml` now imports character movers
  only for the explicit Junkenstein NPC catalog. Junkenstein preparation fails
  on missing common-NPC movers instead of silently using Soldier's 5.5 m/s.
  Authorized 104319 XML supplies Zomnic 3.0, Shock-Tire 12.0 and Zombardier
  4.25 m/s; all 34 fields match the pinned Python extractor, all 43 existing
  mover rows are preserved, and all 24 runtime manifest hashes validate.
- Gate navigation consumes body speed modifiers. The shared interpreter
  publishes modifier values on activation, dependency changes and authored
  polling, retires them on exit, and resamples on reentry. Repeated property
  reads no longer reroll the Zomnic's authored random speed multiplier.
- Synthetic regression covers stable random values, dependency updates,
  explicit per-frame polling, retirement and reentry. The prepared Normal
  street-wave replay checks actual velocity against the consumed multiplier,
  route travel, contact damage, gate damage and retirement.
- Locked offline workspace tests pass (342 library, 1 protocol, 6 preflight,
  53 collision, 9 runtime and 1 CLI; 110 opt-in library checks remain ignored).
  Prepared Zomnic startup/attack/street, first authored Reaper, Soldier sprint
  and Cassidy Roll/Deadeye checks pass. Formatting and all-target workspace
  Clippy pass. No new original-client acceptance is claimed by this entry.
- A separate prepared replay now reaches the first Reaper through the ordinary
  Normal encounter rather than a manual boss spawn, then checks departure
  from the intro position, pursuit and damage to a living Cassidy-position
  defender. Common attackers are retired through normal damage in this
  isolated replay to keep the gate alive. This does not establish visible
  intro/camera playback, native speed presentation or full boss acceptance.


#### Verify basic Roll in the original client

- Normal Arcade/Cassidy on production 7b13651b plus isolated fixture 74d6ff78
  shows the Roll presentation and viewpoint transition, then restores the
  weapon. The first stationary Roll moves 6.146484375 metres; the owner VM
  remains active. Mouse primary reduces ammo 6 to 5; another Roll restores 6,
  shows cooldown 6, and subsequent primary returns to 5. Both owned processes
  are stopped; the server exits cleanly.
- Binary SHA-256: 0a97eba49c605756575c8b5f278f867db6b2e487767135fa0dde9f75ee924d9d.
  Invulnerability and gate scale 1000000 are confined to the test fixture.
  Live directional/contact cases and unknown native effects remain open.
  Flashbang only shows its cooldown and an active VM in this run; no enemy
  stun, damage, voice, AI, scoring, gallery or full-result acceptance is claimed.

#### Bridge Cassidy Combat Roll into shared Rust movement

- Read authored 01F0 velocity expressions from the owner's sampled pose and
  enqueue finite next-frame delta/cap edits through the shared collision-aware
  mover, consuming each edit once. Reuse the acknowledged owner correction
  path for Cassidy, including create, generation and transport-budget checks.
- The prepared Normal replay covers five directional cases, next-frame
  15 m/s acceleration, roughly six metres on flat collision, spawn-wall
  contact, magazine refill, re-firing and full owner wire projection.
- Locked offline workspace tests with inspector, both prepared Cassidy Roll
  and Deadeye replays, formatting and all-target workspace Clippy pass.
- Scoped native-service actions and linked movement parameter states retain
  pinned Python fallback behavior. Their native effects and dynamic map
  rigid-body impulses remain unverified. Original-client Roll acceptance and
  the full visible gameplay checklist remain open.

#### Reconcile visible Junkenstein gameplay gaps

- Record the user's twelve open gameplay/presentation areas in ROADMAP.md:
  scoring, Cassidy damage/charge/Flashbang/Roll, boss pursuit/damage,
  gate rendering, intros/cameras, Witch healing/pistol policy, Zomnic speed
  and Zombardier targeting/projectiles. Keep earlier voice, gallery and
  success/failure requirements open; the Defeat heading does not close them.
- Identify existing Rialto Python hero mechanisms and replay material for
  focused Rust migration. Its damage charge helper is scoped to the Rialto
  manual-NPC sandbox and the skill replay presets ultimate charge, so these
  cannot be reused as proof of correct Junkenstein charging.
- Correct the earlier live diagnosis: graph 01F0 belongs to Combat Roll.
  Flashbang is separately reported incomplete and has no accepted Rust
  Junkenstein result. Documentation-only change; no new gameplay acceptance.

#### Original-client native defeat result checkpoint

- The isolated Normal/Cassidy failure replay on 3e8b2216 (local fixture
  77a62972) reaches enemy winner 1, outcome node 268 and gate zero at
  195.023 seconds. Cassidy remains alive at 225 health with an active owner VM.
- Read-only native inspection confirms component 114 winner 1 and float
  duration 194.847 seconds. The actual recap now shows red Defeat, replacing
  the incorrect Victory heading from earlier failure runs. Its displayed
  match time is still 0.0: delivery of the native float does not establish the
  recap's MatchTime binding, and time acceptance remains open.
- Test binary SHA-256:
  b145a6c4680152f1c241abde7b954083c1a3d988758b063f1ce99f5616b54d49.
  Both owned test processes are stopped; the server exits cleanly. Fresh
  origin/rust is 13acf152; merge-tree with 3e8b2216 passes without conflicts.
  This checkpoint is not full success/failure, voice, intro or gallery acceptance.

#### Keep final-boss notifications projectable

- Treat 1188:59, the final-objective variant of the same client-targeted 0FCE
  announcement as 1188:58, as a presentation-only action on the server.
  Retain the graph, node, action, network, message and target shape guards.
- Extend the reliable-notification replay through 43,000 ticks. It reproduces
  the exact live 1188:59 UnsupportedAction before the fix and passes afterward.
  The live high-health run reaches the final bosses, but no victory is claimed.
  A separate live Combat Roll path stops the owner VM at graph 01F0 and remains
  open; narration, one-time intros, recap and gallery acceptance remain open.

#### Publish native encounter result fields

- Publish the authored winning team and encounter duration in MECGameMode
  component 114 fields 7 and 9 alongside the reliable outcome action. Retail
  reads these separately from StateScript screen inputs. The zero winning
  team explains the incorrect Victory heading; the recap's time binding still
  requires original-client verification.
- The prepared gate-destruction replay checks enemy winner 1 and a nonzero
  native duration. Inspector workspace tests (339 library checks plus the
  workspace suites), all-target Clippy and formatting pass. Original-client
  recap acceptance remains pending. The ongoing high-health run reaches all
  final bosses without crashing, but has not established a boss-clear victory.

#### Full-length original-client encounter checkpoint

- The authorized isolated Normal/Cassidy run passes the first Reaper spawn at
  199.775 seconds without crashing and reaches the final boss objective.
  At 919.951 seconds the encounter remains in phase three with clock zero,
  no winner, and gate ratio 0.9999904 under the high-health test fixture.
- The client shows the final bosses, the boss objective, charged Deadeye and
  Cassidy at 225 health. Deadeye activation is observed, but the firing window
  is missed and subsequent mouse injection changes the view unexpectedly.
  No boss-clear or victory acceptance is claimed. Both owned test processes
  are stopped; the server exits cleanly. Voice and one-time intro acceptance
  remain open, as do the defeat recap and Hero Gallery rendering.
- Local 104319 UX exports identify the recap's 10274 input as IsPVEMode,
  rather than its winner. MatchTime and MatchResult bindings require separate
  investigation; the previous entity-input repair does not prove those fields.

#### Project authored post-match entity inputs

- Post-match observer screens now retain entity variables declared by their
  authored sync slots. Previously every entity variable was discarded, leaving
  recap input 10274 null even though the server held true.
- Clear these variables from the first-delta baseline so the first screen sends
  its inputs. Select inputs from included graphs, without exposing unrelated
  encounter globals or changing authored winner policy.
- The prepared gate replay reproduces the missing 0283 input before this fix
  and passes afterward. Locked offline inspector workspace tests, all-target
  Clippy and formatting pass. The authorized client failure replay reaches
  enemy winner 1 at 162.511 seconds with gate zero and Cassidy at 225 health.
  The recap still shows Victory / 0.0; outcome UI acceptance remains open.
  Both owned test processes have stopped (server exit 0).


#### Preserve the post-match manager inputs

- The authorized Normal/Cassidy failure replay reaches zero gate health and
  enemy winner 1 at 154.495 seconds while Cassidy stays at 225 health. The
  client recap still incorrectly displays Victory and 0.0 match time.
- Keep the authored 180A:4 receiver inputs (winner 7525 and optional 10186) in
  the manager observer projection. The prepared gate regression fails before
  this repair because the projected winner is absent, then passes afterward.
- Validation: locked offline inspector workspace tests, prepared gate replay,
  all-target Clippy and formatting pass. A second original-client failure run
  reaches enemy winner 1 at 172.975 seconds. Read-only inspection confirms the
  client manager now holds winner 1; the recap still displays Victory / 0.0.
  This closes the missing manager input only. Outcome UI acceptance, the full
  boss run, voices, intros, win line and gallery remain open. Both owned test
  sessions stopped cleanly on the server; local captures stay outside Git.


#### Keep mission updates running after the HUD announcement

- Read-only inspection of the authorized 104319 client shows the battle chase
  active but entity clock 1303 frozen at 475.001 while the server has 455.856.
  The mode receives enemy winner 1; the screen manager lacks its winner input.
- Isolated server diagnostics identify the first projection failure as
  `1188:58:UnsupportedAction("STU_100EF8C2")`. This persisted diagnostic prevents
  notification projection and consequently blocks later clock deltas.
- Handle only the authored 1188:58 client announcement (0FCE/all-client target)
  with the Python cc620be server behavior: follow its output without a server
  gameplay effect. Other unknown client-message placements remain unsupported.
- Extend the real Normal/Cassidy reliable-delivery regression through tick 8000
  and require changing battle-clock samples after the announcement. It reproduces
  the exact live diagnostic before the repair and passes afterward.
- Validation: prepared delivery regression, locked inspector workspace tests,
  all-target Clippy and formatting pass. Original-client retest confirms the
  timer advances: client clock 473.383 -> 430.654, server 472.726 -> 429.997;
  HUD reaches 7:34 while Cassidy remains at 225 health. No new presentation
  diagnostic appears. The match returned to the difficulty menu before the
  recap capture, so neither outcome line is accepted. Voices, intros, the full
  boss run and gallery remain open. Owned test processes stopped.

#### Replicate battle and overtime clock chases

- Include authored 0C8C states 37 and 60 alongside readiness states 52/53 in
  reliable clock deltas. This preserves the client clock binding during battle
  and overtime rather than only sending its sampled numeric value.
- Validate five wire vectors against Python cc620be with the external 104319
  graph/codecs; the opening delivery regression now requires the battle chase.
- Live baseline: invulnerable Cassidy stays alive while the gate goes from
  intact through yellow damage to zero. At 187919 ms the server has enemy
  winner 1 and outcome action 268; the client recap incorrectly says Victory
  and time 0.0. The baseline HUD timer freezes at 7:54 while the server clock
  decrements. Neither outcome acceptance line passes. Owned processes stopped.
- Validation: both prepared clock/delivery regressions, locked inspector workspace
  tests, formatting and inspector all-target Clippy pass. Corrected clock needs
  another original-client probe; result UI, voices, intros and gallery remain open.
- Follow-up original-client probe of 2159edf1: the timer still freezes at 7:55
  while the server clock is 436.181 at 108815 ms. At 189759 ms the gate is zero,
  winner is enemy 1 and action 268; recap still incorrectly shows Victory / 0.0.
  Cassidy remains alive at 225 health. Neither timer nor outcome UI is accepted.
  The owned test processes were stopped.

#### Deliver authored mission HUD phase messages

- Preserve the 104319 mode graph's 0694 and 06A8 persistent sends and their
  server-side routing. Forward each active activation through the acknowledged
  mode stream after notification inputs are available. Reset delivery tracking
  with the scene and retain the exact packet on loss.
- Extend the prepared Normal/Cassidy delivery regression through the actual
  opening timeline, without injecting phase messages. It checks phase delivery,
  duplicate suppression, retry identity, countdown fairness and reset.
- Validation: locked workspace checks (404), five prepared notification/entrance/clock
  regressions, formatting, inspector Clippy and inspector server build pass.
  An authorized clean-source 104319 Normal/Cassidy probe now shows the top
  "Defend the gate" label, 7:55 timer, gate health bar and left team HUD at
  86527 ms (six inspector actors). A later sample shows reduced gate health.
  The timer remains 7:55 after the unprotected player dies; continuous ticking
  and post-match presentation are still open. No invulnerability or gate-health
  override was used, so this is not either requested outcome acceptance line.
  Narration, Boss intro count and gallery acceptance remain open. The owned
  test client and server were stopped.


#### Preserve notification inputs and countdown delivery

- The authorized original-client probe at a68f5d6c enters Arcade > Junkenstein
  > Normal as Cassidy and publishes enemies. At 126031 ms the inspector records
  11 actors and the unprotected player dead. The sampled gameplay still lacks
  the top timer/gate HUD; notification rendering and the outcome heading were
  not established. The test client and owned server were then stopped.
- Repair the notification projection's missing entity inputs using only the
  1188 graph's authored entity sync slots, including difficulty and gate bindings.
  First delivery now includes those inputs. Unrelated entity variables remain
  excluded. Alternate notification and countdown updates when both are pending,
  since their timer inputs may change every tick; post-match retains priority.
- Prepared regressions cover input scope, initial and changed input encoding,
  repeated clock updates, waiting for ACK, exact retry after loss, and scene reset.
  Both new regressions, the authored message regression, 404 locked workspace
  checks, inspector Clippy and formatting pass. These repairs have not yet been
  retested in the original client and do not close HUD or full encounter acceptance.

#### Synchronize the mounted Junkenstein notification graph

- Project the authored 1188 notification instance into reliable mode deltas,
  retaining its existing bootstrap identity. Send changed variables and states,
  suppress identical snapshots, and reset the baseline on scene replacement.
  Post-match delivery retains priority. Server spawners and unrelated entity
  globals are excluded from this presentation projection.
- Prepared 104319 regression sends the original 0694 and 06A8 messages through
  the mode VM, verifies encodable changes and duplicate suppression, and checks
  that subsequent notification updates leave ticks available for the countdown.
  Locked workspace tests (404 checks), the notification/room-clock/14-card
  prepared regressions, formatting and inspector Clippy pass.
  This is headless encoding evidence; original-client notification rendering,
  timer/gate HUD, audio, boss intros, both outcomes and Gallery remain open.

#### Retain the authored mission notification graph at Junkenstein entry

- Include the 1188 child at 0C8C raw state 68 in the original-client mode
  baseline. Preserve its active GameMessageEntry receivers so mission
  notifications have their authored client-only presentation dependencies.
  The common HUD, victory-voice graph and post-match manager remain mounted.
- Prepared 104319 coverage checks the 0694 and 06A8 message receivers and
  encodes the baseline across all 14 Revenge, Endless and challenge cards.
  Locked workspace tests and inspector Clippy pass. This repairs a missing
  dependency; live notification rendering, ongoing notification delivery,
  top timer/gate HUD and complete outcome acceptance remain open.


#### Original-client checkpoint and local source delivery

- Rebuilt clean integration revision 970c6162 with `cargo build --locked --bin
  orpheus_server --features inspector`. In the authorized 104319 client,
  Arcade > Junkenstein > Normal enters successfully as Cassidy (225 health).
- A separate local fixture at a0d477db enables defender invulnerability and
  a 100x authored gate-health multiplier. The first Reaper appears in the
  original client and remains live through encounter time 267727 ms without
  a client crash; Cassidy remains alive at 225/225 and gate fraction is
  0.9761999845504761. This proves post-spawn stability for this probe only.
- Mode timer/gate HUD is absent in the observed gameplay. Boss intro playback
  count, narration/hero audio, final Deadeye victory, correct defeat heading
  and NPC Hero Gallery remain open original-client acceptance items.
- Preserve historical source deltas as independent local snapshots:
  `38391893` (Deadeye probe), `97a74812` (historical merge candidate),
  `77d4c4d7` (result-screen probe). They are reference snapshots with test
  accommodations, not production integration candidates. Their staged diff
  checks passed; no fresh tests were run for the historical trees.
- No Python ORPHEUS/main or combined-server checkout was edited. No remote
  push was performed. Remote rust remains 13acf152; publication audit and
  complete acceptance remain outstanding.


#### Run the Summoner's Junkenstein weapons

- Mount the authored 157C weapon, publish both targeting slots to 185D,
  and drive the beam or charged orb through the authored override for
  its range. Use the existing Boss pursuit when line of sight is lost.
- The prepared 104319 room verifies a charged orb hit, Normal beam damage
  at 3.2 per second, observer snapshot encoding, holding fire behind a
  wall, and clearing both targets when the defender leaves. Locked
  workspace tests and Clippy pass. Original-client acceptance remains open.
- The isolated Normal replay still observes Summoner shield regeneration
  and completes with one Deadeye clearing all four final bosses.

### 2026-11-11

#### Pursue the Witch's Junkenstein targets

- Use the existing Boss route search and collision mover when the selected
  ally or defender cannot be reached by the staff or pistol. Stop moving
  once the target is in range and visible or when the encounter pauses.
- Prepared 104319 tests cover movement into staff and pistol range, holding
  fire at an occluded defender, and the existing Reaper, Roadhog and Junkrat
  behavior. Locked workspace tests and Clippy pass. Original-client
  acceptance remains open.
- The isolated Normal victory replay still clears the final four bosses
  with one Deadeye and emits the defender outcome.

#### Run the Witch's Junkenstein healing staff

- Drive the staff trigger against the selected wounded ally within 12 m
  and line of sight. The authored staff attaches its healing graph to the
  recipient, binds the healer's name and health, and uses the encounter's
  difficulty modifier. The room removes the effect when the link ends.
- The prepared 104319 room verifies healing and observer snapshot encoding,
  removes the effect out of range, and switches back to the pistol after the
  ally recovers. Locked workspace tests and Clippy pass. Pursuit and
  original-client presentation remain open.
- The isolated Normal victory replay still clears all four final bosses
  with Deadeye and emits the defender outcome. It starts Roadhog at
  1,800 health before charging Deadeye to account for the Witch's healing.

#### Fire the Witch's Junkenstein pistol

- With no wounded ally, aim at the nearest defender within 20 m and use
  the authored pistol volley when a clear shot is available. Load the
  authored clip size and difficulty-scaled damage.
- The prepared 104319 room observes 5 damage per shot on Normal, staff
  priority for a wounded ally, and a released trigger behind a castle
  wall. Staff healing, pursuit and client acceptance remain open.
- Locked workspace tests and Clippy pass. The isolated Normal replay with
  a 100,000-health gate still clears the final four bosses with Deadeye
  and reaches the defender victory message.

#### Bind the Witch's Junkenstein weapon selection

- Mount the Witch's authored staff, pistol and weapon manager. Feed
  controller 0CD2 a hostile defender and a ranked wounded ally, then
  use its switch states to select the weapon.
- Prepared 104319 body and controller checks observe the staff with
  a wounded Junkrat, the pistol after that ally is restored, and both
  target slots clearing when their targets are gone. Healing and pistol
  damage remain open.

#### Run the Junkenstein Zombardier attack

- Bind the Zombardier's authored logical button to a living hostile
  target with a clear grenade arc. Resolve the 0CA1 grenade and its
  difficulty-dependent splash through room collision.
- Keep the client-ended firing animation available for replication
  without an authored server timer. The prepared Normal wave launches
  a grenade, hits a defender for 30 damage and encodes the body snapshot.
  Zombardiers also fire across the full Normal server replay, which
  still reaches defender victory after the final Deadeye release.

#### Run Junkrat's Junkenstein grenade weapon

- Mount the encounter's authored weapon. Use its ordinary AI weapon
  controls and projectile settings to aim a low arc through castle
  collision and sweep the grenade against the room's targets.
- A returning Junkrat fires an authored grenade at a defender 10 metres
  away without replaying its intro. The Normal fixture observes 10
  direct and 15 splash damage and encodes the observer body snapshot.
- In the full server replay, both Junkrat appearances launch grenades
  and Cassidy still reaches defender victory with Deadeye. Client effects
  and weapon audio remain open.

#### Continue Boss combat after Junkenstein intro handling

- Run the authored shield recharge on living encounter Bosses, including
  the Summoner. Recharge updates the shield pool and its health component.
- Recognize the authored intro bypass for returning Reaper and Roadhog,
  and accept the controller facing handoff alongside the completed cinematic.
- The prepared Roadhog intro, both weapon modes, controller handoff and
  gate defeat fixtures pass. A returning Roadhog hits the defender
  without starting a second intro. Original-client presentation remains open.
- In the full Normal server replay, the Summoner recovered from shield
  damage, and returning Reaper and Roadhog reached the combat handoff
  without replaying their intros. Deadeye still killed all four final
  Bosses and triggered defender victory.

#### Apply Junkenstein's authored victory cleanup

- Run the 0CBC:69 self-damage state once for each surviving encounter
  enemy when the authored result reaches its body. Bodies already dead
  keep their existing death record.
- The state regression verifies both cases and checks that subsequent
  frames do not reapply the cleanup hit.
- An isolated 104319 Normal server run reached defender victory after
  one Deadeye release killed all four spawned final Bosses. The fixture
  arranged their finishing-shot positions and health, used a 100,000-HP
  gate, and kept Cassidy at 225 HP. The server emitted one Reinhardt
  victory cue and encoded the outcome and summary.

#### Resolve Cassidy's Deadeye volleys in Junkenstein

- Run an authored target-effect instance for each enemy in Cassidy's
  search. Use current room health for the lock and the selected capsule
  for the authored hitscan direction.
- The Normal 104319 fixture kills two visible 1,000-HP targets with a
  second Q and leaves the enemy behind the castle wall alive. It encodes
  owner corrections that introduce and remove the lock instances.
  The final-wave and original-client runs remain open.

#### Find Deadeye targets in Junkenstein

- Connect Cassidy's two authored target searches to living enemies in the
  Rust room. The second search also checks castle collision to the target's
  capsule.
- The prepared 104319 Normal owner fixture verifies charge consumption,
  team and cone filtering, range, castle-wall occlusion and removal from
  both arrays. Per-target effects, firing and client acceptance remain open.

#### Charge Cassidy's Deadeye in Junkenstein

- Use the authored ultimate cost and the reference passive charge rate in
  the Normal owner runtime. Allow the recovered ultimate-ready telemetry
  and Deadeye's two input joins to advance their graphs.
- The prepared owner reaches full charge without a script fault. A Q
  input spends that charge and reaches the target search and effect
  states. Locking targets and firing Deadeye remain open.

#### Advance Junkenstein Revenge into defender victory

- Join the four authored final-wave branches and recheck the mode subscript
  when its completion variable changes. Continue through the victory narrator
  state and queue the authored client outcome action.
- The join fixture checks repeated edges, the completion watcher and teardown.
  The server-side Normal replay uses a high-health gate and direct test
  damage to the Bosses. Cassidy's Deadeye, the original-client victory
  heading, and the complete post-match UI still need acceptance.

#### Publish the Junkenstein Witch and Summoner

- Start the two encounter bodies and their controllers with the authored
  tag lookups, controller base states and empty startup targets.
- The prepared scene encodes both observer baselines. An accelerated
  Normal run spawns the Summoner and Witch through the encounter scheduler.
  The final-wave lookup resolves the authored Boss tags. The encounter
  still needs the phase-three join, the two Bosses' combat services and
  original-client acceptance.

#### Restore Junkenstein Shock-Tire gate attacks

- Bind the authored AI control setting, read live mover velocity, and
  return no participant for the autonomous tire.
- The prepared fixture verifies the observer baseline, setting cleanup,
  gate damage and retirement. An accelerated Normal run spawns the
  100-health tire from its wave and records nine tire deaths near the gate.
  Client playback and the full encounter result remain open.

#### Publish Junkenstein Zombardier and Junkrat

- Resolve the authored entity-existence test for bound entities and empty
  operands. Run the recovered Junkrat controller base-state placements
  and keep unselected behavior candidates dormant.
- The prepared 104319 fixture encodes both observer baselines. In an
  accelerated Normal wave, Zombardier and Junkrat spawn through the
  encounter scheduler. Their attacks and original-client playback remain open.

#### Simulate Junkenstein Roadhog's scrap projectiles

- Sweep Roadhog's primary pellets through the room instead of applying
  their damage in the firing frame. Apply the authored pellet falloff.
- Use the authored secondary input from 8.75 to 19.75 metres. The scrap
  ball splits into 25 pellets at nine metres only if its flight expires
  without contact.
- The prepared Normal wave observes the single impact before the split
  and pellet hits after it. Client projectile presentation and the hook
  remain open.

#### Drive Junkenstein Roadhog's primary weapon

- Start the authored weapon manager and five-round shotgun. After the intro,
  pursue a living defender, publish the visible target in AI slot zero,
  and feed primary input within the authored 8.749-metre range.
- The prepared Normal wave checks filtered pellet damage, an encoded
  firing snapshot, an empty magazine and the first shot after reload.
  Roadhog's secondary fire, hook and original-client playback remain open.

#### Run the Junkenstein Roadhog intro

- Register the authored outgoing damage filter for the duration of the
  wave. Apply the 20 percent reduction unless the hit carries the
  excluded tag. Encode the filter with its link-only state codec.
- The prepared Normal wave reaches the intro animation and camera once,
  issues the three authored voice requests and releases the filter on
  teardown. An accelerated Normal run reaches Roadhog through the authored
  wave after test damage kills Reaper. Roadhog's weapons and original-client
  playback remain open.

#### Publish the Junkenstein Roadhog body and controller

- Resolve the AI controller's empty linked-target slot as entity zero.
  Keep the position query destination when that slot has no entity.
- The prepared 104319 fixture verifies that the controller completes
  initialization and the body produces an encoded observer baseline.
  The authored wave still stops at 0CBC:141, the outgoing damage
  filter. Roadhog's intro and combat remain unverified in the client.

#### Keep Junkenstein Reaper firing through reload

- Retain the completed intro handoff when the body retires the intro graph.
  Follow the authored reload continuations and keep the two self-directed
  reload messages on the client side of the script.
- The prepared Reaper fixture fires after intro retirement, empties the
  eight-round magazine, then fires with seven rounds remaining after reload.

#### Apply Junkenstein Reaper shotgun damage

- Evaluate the authored 0019:10 volley, generate its twenty pellet directions
  and apply the selected difficulty and distance falloff through the existing
  capsule, wall and room damage paths. Player capsules come from their live
  movers.
- The generated spread table and sampled pellet directions match the pinned
  Python reference. The prepared Normal Reaper fixture verifies damage to
  the living defender after the authored shot.
- The isolated original client stayed connected and displayed the health
  loss caused by Reaper. Formatting, inspector Clippy, workspace tests,
  the gate-destruction fixture and fourteen prepared Arcade cards passed.

#### Start the Junkenstein Reaper weapon and restore Cassidy ammo

- Mount Reaper's authored weapon manager and shotgun graph. Feed primary
  input while Reaper aims at a living defender and the ordinary slot-zero
  override permits it. The prepared encounter checks the first volley,
  eight-to-seven ammo, observer encoding and release on pause or target loss.
- Use the authored magazine multiplier for Reaper and Cassidy. The Normal
  Cassidy fixture starts at 6/6. The isolated 104319 client also showed 6/6
  at spawn, 5/6 after a shot and 6/6 after reloading.
- Locked inspector Clippy, workspace tests, gate destruction and fourteen
  prepared Arcade cards passed. The isolated client stayed connected as
  the first Reaper approached Cassidy. Sustained firing, pellet damage
  and Deadeye remain open.

#### Route Junkenstein Reaper toward living defenders

- After the intro and any balcony exit, pursue living defenders on the
  castle collision. Replan when the target changes or moves and stop to
  aim when a defender is visible within 20 meters.
- The prepared 104319 Reaper fixture checks the approach, clear aim,
  pause, relocation and target loss. An isolated server replay reached
  aiming range from the authored wave spawn. Format, Clippy, workspace,
  gate-destruction and fourteen-card checks passed. Weapon input and
  original-client verification of pursuit remain open.

#### Move Reaper off the Junkenstein balcony after his intro

- Start the balcony exit after the authored 06AC handoff releases the
  intro and a living defender is present. Use the body mover to walk off
  the ledge and publish the fall and landing through bot movement.
- The prepared 104319 fixture checks the defender and pause conditions,
  the fall, a landing below the balcony and a single exit. Locked
  inspector Clippy, workspace tests, the gate-destruction fixture and all
  fourteen prepared Arcade cards passed.
- In an isolated 104319 Normal run with an invulnerable Cassidy and a
  high-health gate, the original client stayed connected. The server
  tracked the first Reaper from the balcony to the street, and the
  client showed his marker lower in the scene after landing. Full intro
  playback and post-landing combat remain open.

#### Replicate the Junkenstein post-match signal

- Send the authored 08D6 notification from the post-match manager when
  0695 opens the results sequence. The prepared Normal gate fixture checks
  one activation and its inclusion in the first reliable screen delta.
- Locked inspector Clippy and workspace tests passed.
- The isolated 104319 run reached zero gate health with Cassidy at 225 HP
  and displayed the final recap and its leave countdown. The heading
  still read Victory and the match time still read 0.0.

#### Return Junkenstein players after the post-match screens

- Route the authored completion message into the existing leave flow
  once the client's reliable stream acknowledges screen cleanup.
- The prepared Normal gate fixture checks completion timing and screen
  delivery. Locked inspector Clippy, workspace tests and the prepared
  14-card check passed.
- In the isolated 104319 Normal defeat run, the gate reached zero while
  Cassidy retained 225 HP. The client returned to the difficulty menu,
  the room closed, and selecting Normal again opened hero selection.
  The final recap's defeat heading still needs correction.

#### Seed the Junkenstein outcome banner with the winning team

- Give the original 027F banner the winning team received by the mode
  screen manager. The banner can read the result from its first frame
  even if its client-only listener starts after the phase handoff.
- The Normal gate-destruction fixture verifies team one on the banner
  instance. Locked inspector Clippy and workspace tests passed.
- The isolated 104319 run showed the red outcome effect with Cassidy
  at 225 HP and the gate at zero. The separate final recap still
  displayed Victory, and the banner text was not verified.

#### Replicate Junkenstein post-match screens

- Publish each played screen as an independent observer root, retaining
  shared instances between phases and removing expired screens. Keep
  the authored list state and its item scopes on the server, matching
  the 104319 observer reference. Use the native keyed-array encoding for
  identifier maps and deliver the screen's initial UI pulse.
- The prepared defeat case decodes the phase descriptors and removals
  and checks the datagram budget. Locked inspector Clippy, workspace
  tests and the prepared gate and 14-card checks passed.
- In the corrected isolated 104319 test, the gate reached zero, Cassidy
  stayed at 225 HP, and the client opened the original results screen
  without the prototype's crash. The defeat run showed a blue Victory
  heading and returned to the combat HUD after the screens retired.
  Correct result text and leaving the ended match remain open.

#### Run the Junkenstein post-match script sequence

- Reconcile the authored post-match graph lists by occurrence. Start each
  new graph in its own instance scope and remove that scope when its phase
  no longer needs it. Advance the authored phase transition on the server.
- The prepared Normal gate-destruction case walks all four phases, retains
  shared script IDs across transitions, starts 0283 once, and removes the
  sequence's children after the last phase. Locked inspector Clippy,
  workspace tests and the prepared gate and 14-card checks passed.
- Replication of the dynamic screen instances and original-client results
  presentation remain open.

#### Evaluate the Junkenstein post-match screen configuration

- Evaluate authored arrays, identifier maps and array folds in the Rust
  script VM. Preserve map bucket order and the fold's scoped parameters.
- The prepared Normal defeat case evaluates the four screen stages,
  checks the 41.1-second total, and confirms the final stage references
  the original 0283 results graph. Launching those scoped scripts and
  original-client results presentation are still open. Locked inspector
  Clippy, workspace tests and the prepared gate and 14-card checks passed.

#### Forward the Junkenstein post-match phase

- Follow the authored 0C8C phase handoff, deliver 0695 to active mode
  listeners, and replicate its sender on the reliable observer stream.
  Include the common HUD and screen manager's active listeners in the
  baseline. The prepared Normal gate case reaches the 180A screen
  manager and checks the phase delta's existing-root descriptor.
- The isolated 104319 test reached zero gate health with Cassidy alive
  at 225 HP. Sampled phase-five frames still showed the combat HUD.
  The Rust fixture reports an unsupported screen configuration at
  180A:55. Locked inspector Clippy, workspace tests and the prepared
  gate and 14-card checks passed.

#### Include Junkenstein HUD children in the initial mode frame

- Publish the authored common HUD, outcome voice listener, and post-match
  screen manager using the live instance IDs and raw parent states. The
  prepared 14-card fixture decodes the descriptor tree.
- In an isolated 104319 Normal defeat run, Cassidy entered through Arcade
  and stayed at 225 HP while the gate reached zero. The client acknowledged
  the defeat action and still showed the first-person HUD in phase five.
  The post-match screen sequence still needs replication and client verification.

#### Replicate Junkenstein result statistics and outcome actions

- Send the authored results child and its active statistics sender on
  the reliable mode stream. Encode the separate victory or defeat
  frame action with the stream revision and its authored remote slot.
- Retire surviving wave body/controller pairs through the authored
  encounter-exit actions. A prepared Normal gate-destruction test
  checks the results descriptor, defeat frame action, and removal of a
  living Zomnic and its linked controller.
- In the 104319 Normal test, a separate build kept Cassidy at 225 HP and
  set the gate capacity to 20. The gate reached zero, the room reached
  its terminal phase, and the client acknowledged the defeat frame
  action. The remaining enemies were removed. The observed client
  frames did not show a results overlay. Locked inspector Clippy,
  workspace tests and the prepared gate, street-wave, countdown and
  Reaper checks passed.

#### Deliver the authored Junkenstein encounter result

- Evaluate the four result parameters at the encounter's authored
  broadcast. Deliver the message to the mode, gate, defenders and
  enemy bodies that host its receiver.
- Register the defender's result states and hold the player's current
  aim while the authored cinematic lock is active.
- The prepared Normal room reaches the terminal phase after gate
  destruction, emits one defeat result and advances the gate, mode and
  Cassidy receivers. Cassidy's snapshot encodes and his mover holds
  the locked aim under a new command. Locked inspector Clippy,
  workspace tests and the prepared gate, street-wave, countdown and
  Reaper checks passed. The native result UI and live acceptance remain open.

#### Replicate the Junkenstein castle gate states

- Publish the gate's authored graph on the acknowledged map placeable.
  Use the same observer baseline and reliable delta path as encounter
  enemies.
- A prepared Zomnic attack verifies the first damage animation. The
  fixture then applies authored damage messages across the remaining
  thresholds, checks the four animation stages and all three narrator
  notices, and advances gate health to zero. Formatting, locked
  inspector Clippy, workspace tests and the prepared gate, street-wave,
  countdown and Reaper checks passed.
- The original 104319 client entered Normal and remained responsive
  after the first waves. Direct confirmation of each visual stage,
  narrator playback and defeat presentation is pending.

#### Route Junkenstein gate narration through combat notices

- Run the gate graph's three authored narrator cues and use the castle
  gate as their spatial anchor. Acknowledge placeable identities before
  releasing combat notices that refer to them.
- The prepared Zomnic attack emits the first damage cue once and
  completes the voice state after its line duration. Locked inspector
  Clippy and workspace tests passed. Original-client playback is pending.

#### Keep Cassidy's body script running after a Zomnic explosion

- Evaluate the authored any, all and excluded entity masks against the
  encounter's player body flags, and run the base state reached by the
  hit reaction. Use authoritative vitals when admitting players to the
  enemy target roster.
- The prepared Normal street wave verifies owner snapshot encoding after
  taking damage, alongside gate damage and enemy retirement. Locked
  inspector Clippy and workspace tests passed.
- The original 104319 client displayed the street Zomnics' explosions
  and Cassidy's falling health. The owner script remained active at
  zero health and the room continued spawning enemies.

#### Route Junkenstein gate attackers across the encounter map

- Use the encounter collision sampler to plan ground-supported routes,
  sweep the NPC capsule along each segment and deliver the authored
  controller arrival action once per route. The controller's wait
  continues while a route is available.
- The prepared Normal room verifies the first street Zomnic moves over
  ten metres, explodes within three metres of the gate and changes its
  damage counter. Locked inspector Clippy, workspace tests, the gate,
  countdown and Reaper fixtures passed.
- The original 104319 client entered Normal as Cassidy and the server
  sampled street Zomnics at the gate. Damage exposed a missing player
  filter evaluation, followed by an invalid tracked-target roster.
  Shock-Tire attack and later waves still need acceptance.

#### Apply authored Zomnic explosion damage to nearby bodies

- Project the contact state and target array to observers. Complete
  each authored damage state so the graph advances through the victim
  list. Room bots and the owning player runtime apply the authored
  damage to their existing health pools.
- The prepared Normal street wave verifies two distinct victims, Cassidy
  health replication, the observer contact payload and enemy retirement.
  Locked inspector Clippy, workspace tests and the gate and Reaper
  fixtures passed.
- Original-client playback of the contact effect and player death and
  respawn behavior still need verification. Gate navigation and match
  results remain open.

#### Handle an unset target in the entity team query

- Return the 104319 no-team value for an unset or invalid entity operand.
  Valid identities still require a team lookup from the world.
- In the 104319 client, Cassidy entered Normal through Arcade and stayed
  connected through the 241.919-second sample. The client rendered
  Zomnic wreckage and a Boss marker. Room samples showed enemy retirement,
  while some explosions stopped on the unset team query.
- A prepared-room probe with a nearby defender advances past that query
  and reaches the unresolved contact effect and damage against other
  bodies. The client showed 0/0 ammo and 0% ultimate charge. Boss intro
  playback, gate states, voices and match results still need acceptance.

#### Run the authored Zomnic explosion and retire the body-controller pair

- Advance the gate script with scene messages and publish scripted enemy
  health and retirement through normal room replication. The Zomnic
  explosion reads the room target roster and follows the authored gate
  damage message and self-damage paths.
- The prepared Normal street wave now runs through death and retirement.
  A second fixture starts a Zomnic near the gate and verifies the authored
  damage counter increases and the pair retires without respawning.
  Locked inspector Clippy, workspace tests and the prepared Reaper fixture
  passed.
- Gate approach movement, the gate damage voice at node 50, and the gate
  and results presentation in the original client still need acceptance.

#### Keep the first Junkenstein street wave running through controller setup

- Replicate the self-targeted 0744 sender at 0CBC:122 for its client
  receiver. Collect config groups before starting a SubScript so the
  Zomnic controller receives the authored identifier array.
- Keep the 0C88:0 behavior candidate dormant in the dedicated encounter
  host, matching the Python scene's selection policy.
- The prepared Normal scene starts from Cassidy selection and advances the
  first street Zomnic for 250 frames after wave setup. It checks the
  child binding, dormant candidate and observer snapshot. The Rust and
  combined inspector checkouts passed formatting, locked Clippy,
  workspace tests and the prepared street-wave and Reaper checks.
- In the combined 104319 session, the first two startup diagnostics cleared
  and the client stayed connected past the first Boss spawn. That build
  reached the 0C88:0 diagnostic before the dormant-candidate fix. Gate
  navigation and the later 0C89 explosion path still need validation.

#### Run the authored Junkenstein Boss intro states

- Replicate the authored time-scale boundaries and persistent 06AF and
  06AB broadcast states. The Boss pause receiver stays active while any
  matching sender remains active. The self-linked aim state holds and
  releases the body's current orientation.
- The prepared Normal Reaper fixture advances through one camera
  activation, one Boss voice request and the authored exit. Python
  payload vectors cover the active and inactive time-scale values and
  the optional link in all three wire modes.
- The Rust and combined inspector checkouts passed formatting, locked
  Clippy and workspace tests. The prepared Reaper, pause, codec and room
  clock tests passed in both checkouts. The prepared Zomnic gate attack
  test also passed in the combined checkout.
- The 104319 client entered Normal from Arcade as Cassidy and stayed
  connected through 231 seconds. The live Zomnic body halted on the
  target-list policy at 0CBC:122, and no Reaper appeared in this run.
  Client camera playback, audio and both match outcomes remain open.
- In the combined inspector build, the first Boss appeared at 193.247
  seconds. The 104319 client rendered its model and pumpkin marker and
  stayed connected through 245.071 seconds. The Boss stayed at its
  spawn position and the same Zomnic fault recurred.

#### Route the authored Junkenstein intro lifecycle messages

- Route 06DE and 06AC to spawned defenders from the exact Boss and mode
  placements. The 0CA5 receiver follows its original highlight-reset
  continuations using the reference base-action behavior.
- Send 06AC to the linked Boss controller only while its 0CDC child is
  present. The prepared exit fixture follows the authored timer and
  controller messages, retires the child and releases the spawn facing.
- Normal Reaper reaches the 06DE preparation route. Its full intro still
  needs the 0CE0:3 state and persistent 06AF/06AB broadcasts. Client
  camera playback and post-intro combat remain open.
- The Rust and combined inspector checkouts passed formatting, locked
  Clippy, workspace tests and all three targeted prepared checks.

#### Deliver Junkenstein Boss intro voice notices

- Submit the 0CE0 narrator and Boss cues with the authored speakers,
  delay and enUS line lengths. Queue voice notice 27500 to spawned
  defenders, anchored to the Boss body and held until its create ACK.
- The prepared Normal Reaper fixture emits one narrator request and
  completes its voice state. The Boss body cue fixture covers delay and
  cancellation and encodes owner and observer snapshots. Both notice
  variants match the Python wire vectors.
- The Rust checkout passed locked Clippy, workspace tests and ten
  prepared checks. The combined inspector checkout passed locked Clippy
  and the three affected prepared checks.
- Original-client audio, the camera broadcast and exit handoff remain
  open. This change covers the Boss introduction cues.

#### Advance the authored Reaper intro past wave setup

- Record the wave body's 0CBC:129 same-team contact suppression until its
  state ends or its instance retires. Encounter bodies currently have no
  movement peer query. The state does not change static map collision.
- Follow the authored 0CE0:72–76 action continuations using the reference
  dedicated VM behavior.
- The prepared Normal Reaper regression passes wave initialization,
  reaches the one-second intro wait and encodes an observer snapshot.
  The next unsupported state is the Boss voice stimulus at 0CE0:61.
  Original-client playback and the exit handoff remain open.
- The Rust checkout passed formatting, locked Clippy, workspace tests
  and seven prepared Boss regressions. The combined inspector checkout
  passed formatting, locked Clippy and both affected prepared regressions.

#### Route the Junkenstein Boss intro start to the mode

- Deliver the authored 06B0 from the Boss body to the mode with the
  original sender and parameter. Resolve the mode's tag tests against
  the live body, including its state-owned tag changes.
- The prepared Normal Reaper regression checks one 0CE0 instance and
  the mode's Reaper intro flag. The same-team contact state still faults
  during wave setup. Client playback and the exit handoff remain open.
- The Rust checkout passed locked Clippy and workspace tests. The combined
  inspector checkout passed locked Clippy and the same Reaper regression.

#### Run the Junkenstein intro's linked body states

- Register the authored 43994F1B and 93B8758C states in their own ordered
  lists for the 0CE0 and 0D03 cinematics. Admit the verified base-state
  continuations under their movement modifiers.
- Start the original nested body states in the prepared regression.
  Check owner and observer encoding, link removal and model-control
  restoration when the intro instances end.
- The Rust branch and the combined inspector checkout passed formatting,
  locked Clippy and workspace tests, and six prepared Boss regressions.
- The broadcast sender, voice stimulus and same-team collision service
  still require separate runtime support. Original-client playback
  remains open.

#### Apply authored Junkenstein spawn facing

- Register the controller's 0C95 and 185D facing records for their
  possessed body. Refresh the direction every frame and remove the
  record on state end or instance retirement.
- Apply the winning direction to the Boss body's horizontal facing
  using the same activation order and priority rules as the reference.
- The prepared tests exercise Reaper's authored spawn direction,
  refreshed heading, state reentry, and retirement.
- Locked workspace checks and all 90 prepared tests passed. The combined
  inspector candidate passed Clippy and the prepared facing, Reaper
  and Zomnic gate-damage regressions.
- The actual cinematic exit message and contact states remain open,
  along with original-client Boss behavior and results.

#### Bind Junkenstein controller targets to map placements

- Share the room's validated tagged placements with spawned enemy VMs.
  Preserve placement order for target arrays and keep unique-tag queries
  strict about duplicate or conflicting data.
- Resolve the authored 0C95 target array and bind the placement position
  and heading to the 0CE2 child before its entry runs. Keep its candidate
  dormant in the headless context that has no selection service.
- The prepared Reaper regression checks the authored target, position
  and heading alongside the Normal 1000 maximum-health path.
- Locked workspace checks and all 89 prepared tests passed. The combined
  inspector candidate passed Clippy and the Reaper, countdown and gate
  damage regressions.
- Facing overrides, team contact, encounter message delivery and the
  complete original-client encounter remain open.

#### Route Junkenstein wave scripts through enemy pairs

- Resolve the spawned body's current possessor from the live room registry.
  Deliver the mode's 0CBA request to that controller and route its 0C95
  requests to the matching body through the existing bounded queue.
- Keep the immediate caller, inherited source and authored parameters
  separate. Run the queued requests on the enemy's simulation frame.
- The prepared Reaper regression now reaches 1000 maximum health on
  Normal through this route. The room fixture checks that successful
  delivery allows the spawner to track its living enemies and continue.
- Locked workspace tests and all 89 prepared tests passed. The combined
  inspector candidate passed its workspace tests, plus the prepared Reaper,
  countdown and Zomnic gate-damage regressions.
- The wave still reaches unsupported controller, contact and message
  branches. Original-client acceptance of the new route remains open.

#### Publish Junkenstein intro control and camera states

- Register the authored entity-control and paired states in activation
  order. Release the boss model-control flags when the last suppressing
  state ends. Encode state links using the target graph's owner or observer
  index and support the authored camera focus bone literal.
- The prepared defender fixture publishes the camera delta and keeps one
  activation when the same 06AB arrives twice during the camera. The
  isolated control fixture checks link cleanup and both baseline views.
- Locked workspace tests, Clippy, and all 89 prepared tests passed. The
  combined inspector candidate also passed the five prepared boss tests
  and the Zomnic gate-damage and retirement regression.
- The 104319 client entered Arcade Normal as Cassidy on the combined
  candidate. The inspector first sampled Reaper near 188 seconds and the
  client remained responsive beyond 218 seconds. Reaper remained at his
  spawn with 250 health, and Cassidy displayed 0/0 ammunition.
- Live boss script delivery, the full intro and the win and loss acceptance
  checks remain open. The Hero Gallery NPC selection remains unverified.

#### Resolve Boss body definitions and wave health

- Resolve the body-definition query from the exact registered enemy pair,
  including the controller's possessed-body selector and definition literals.
- Add the authored 0CBC supplemental health records to Vitals with independent
  cleanup. Damage consumes the bonus before base health and healing restores
  base health before the bonus. Reject overlapping bonus records.
- Validate the wave's difficulty against the room mode and retain the Normal
  zero-health special case. The prepared test checks all six wave health
  tables across the four difficulties and removes each record on abort.
- Support the base lifecycle of the authored wave continuations and the local
  combat accumulator when the dedicated server declares that context absent.
- A prepared Reaper start on the combined candidate reached 1000 maximum
  health and entered 0CE0. The original intro still requires entity control.
  The inspector workspace tests and the combined Zomnic gate regression passed.
- Same-team contact and live cross-component PlayScript delivery remain open.
  No new original-client acceptance was performed.

#### Start loaded PlayScript graphs with authored bindings

- Support direct loaded graphs with the authored keep-existing policy.
  Apply the public schema and queue the named array parameters and caller
  before each enabled start entry. Keep inherited source separate from
  the immediate caller. Honor the transient wire flag and retire only
  after pending work and active states finish.
- A prepared test evaluates the 0CBA and 0C95 calls and verifies the
  controller/body targets and spawn-position parameter. A VM fixture
  verifies duplicate suppression and fresh allocation after retirement.
  Inspector workspace tests, Clippy and all 86 prepared tests passed.
- The isolated Reaper probe reaches 0C95's body-definition selector and
  0CBC's encounter health and team-contact states. Live cross-component
  delivery and these startup operations remain open. No new original-client
  test was performed.

#### Track living bodies in the Junkenstein spawner

- Implement the authored 0CBA append and living-body filter. The scene
  supplies current body lifetime from the room registry and Vitals.
  The filter preserves order, removes corpses and retired bodies, and
  refreshes while active when its source or a body's lifetime changes.
- The prepared 104319 fixture exercises both nodes, including a live
  append, the spawn counter and timer, and subsequent death and removal.
  The prepared Cassidy countdown and first-spawn test, inspector workspace
  tests and Clippy passed. The combined candidate also passed the prepared
  Zomnic gate-damage and retirement test.
- The PlayScript call that precedes the append remains open. No new
  original-client test was performed for this change.

#### Arm the Junkenstein defender intro receiver

- Begin the authored 06AB receiver state on 0CA5 when the defender body
  starts. Leave the ordinary graph entry dormant, matching the Python scene.
- The prepared Normal Cassidy owner test confirms that raw state 11 is
  the only state active on 0CA5 at spawn. The inspector workspace tests
  and Clippy passed.
- The 104319 client entered Normal from Arcade as Cassidy. The first
  Reaper appeared at about 164.6 seconds on the encounter clock and the
  client was still in the match at 214.9 seconds. Reaper stayed at the
  spawn point and Cassidy still displayed 0/0 ammo. Boss intro and
  dialogue, gate damage and both match outcomes remain open.

#### Route Junkenstein sightings through the room

- Route authored 00BD from the supported body graph placements to the room's
  current living defender bodies. Retain sender position, filter flags and
  the authored target tags for the receiver's facing and team checks.
- The prepared Reaper probe now emits a sighting at frame 62. The prepared
  Cassidy owner consumes foreign sightings with its authored facing branch,
  then advances through its own 01CA broadcast without a diagnostic.
- Default and inspector regressions and all 84 prepared checks passed. The
  combined AI candidate passed inspector Clippy and the Reaper, Cassidy
  and Zomnic gate checks. Extending the two body probes to 240 frames also
  passed, with repeated Reaper sightings.
- Boss intro activation, dialogue playback and original-client acceptance
  remain open.

#### Mount the Junkenstein defender message receiver

- Preserve caller identity on body messages and bind the authored caller
  and instigator variables before the entry plug. Existing host messages
  may still omit the sender.
- Mount 0CA5 on the normal owner body as a passive message listener. A
  prepared fixture delivers the authored 00BD entry with controlled source
  poses and confirms that the receiver binds the parameters and takes the
  facing branch only when the source is in front.
- Rust default and inspector checks and the 84 prepared cases passed. The
  pending AI patch applies cleanly and the combined candidate passes
  inspector Clippy and both prepared defender receiver checks.
- Live recipient selection, foreign actor pose and tag queries, and the
  Boss intro activation still need room integration. The initial Cassidy
  and Reaper 01CA recipient diagnostics remain open in live rooms.

#### Publish the authored Reaper body

- Preserve the evaluator destination for the two verified 0C83 queries and
  keep the encounter's unselected fallback candidates dormant. Register
  localized display names on the bound component 92 controller and remove
  the override when the state ends.
- The prepared room creates Reaper, encodes his observer baseline, confirms
  his controller name and advances 61 frames. A probe at frame 62 reaches
  the same 01CA message recipient query as Cassidy. Original-client
  rendering, combat and the first-Reaper crash remain open.
- Passed 390 default and 393 inspector public checks, 83 prepared checks
  in each configuration, Clippy, formatting and runtime preflight.
- The current AI patch applies without textual conflicts. The combined
  candidate passes inspector Clippy, the Reaper publication test and the
  Zomnic gate attack test with the candidate's extra gate fixture field.

#### Register authored Boss weapon overrides

- Validate weapon overrides against the paired controller's authored slots.
  Keep the identifier and flag in state order, respect equal-priority
  insertion, and remove each override when its state exits.
- Execute the verified PC base states at their Reaper and Roadhog controller
  placements. The prepared Revenge probes advance both Bosses through body
  initialization and past those controller diagnostics. Reaper still needs
  behavior selection, the 0C83 query and the localized name state.
- Passed 389 default and 392 inspector public checks, 82 prepared checks
  in each configuration, Clippy, formatting and runtime preflight.
- The pending AI patch applies cleanly to this branch. The combined candidate
  passes inspector Clippy and both the prepared Boss startup probe and the
  Zomnic gate attack test. Boss combat and client acceptance remain open.

#### Attach Cassidy's owner script in Normal Junkenstein

- Enable the authored Cassidy body factory for the Normal Revenge queue.
  Use the active Tutorial mode comparison for `STU_A77AC2AC`. The prepared
  scene test checks the owner snapshot, 225 health and countdown lifecycle.
- Original 104319 rendered Cassidy's revolver, 225/225 health and ability
  icons after selection. Ammo displayed 0/0. The owner VM then stopped at
  graph 01CA node 126 on `STU_ED45C5E8`. The original Hero Gallery showed
  32 playable entries and no NPC entries.
- Passed 389 default and 392 inspector public checks, 82 prepared checks
  in each configuration, Clippy, formatting and runtime preflight.
- The pending AI changes apply cleanly. Their isolated prepared-data test
  verifies that a Zomnic's authored attack advances the gate damage counter
  and retires the pair. Gate destruction, dialogue, Bosses and match results
  remain open.

#### Preserve On Fire defaults for scene enemies

- Resolve the enemy controller On Fire query using the caller's initial value
  when that controller has no stats. Preserve live player flags and the
  unsupported result for unresolved targets. All five prepared Boss probes
  clear the query diagnostic. Reaper still reports AI weapon control.
- Passed 389 default and 392 inspector public checks, 82 prepared checks in
  each configuration, Clippy, formatting and runtime preflight. The isolated
  candidate with the pending AI patch passes Clippy and the prepared Boss probe.
- With the local DevReg launcher, the original zhCN 104319 client connected to
  Rust, queued Normal from Arcade and reached the castle as Cassidy. His owner
  script and weapon were absent. The encounter clock advanced and Zomnics
  spawned, then their scripts halted on unsupported gate and targeting states.
  The Gallery relay still reported mapped-page protection error 87.

#### Read component 92 from authored enemy definitions

- Import the component 92 presence flag and target tags from available DataTool
  `.003` definitions, including the Junkenstein entity table. Preserve unknown
  values for older bundles and missing exports.
- Resolve `STU_D3EB8D87` through the live enemy pair and its imported entity
  metadata. A new local pack confirms the component on all eight controllers
  and its absence on their bodies. All five prepared Boss probes clear this
  query diagnostic. Reaper's on-fire query and weapon control remain open.
- Passed 388 default and 391 inspector public checks, 82 prepared checks in
  each configuration, Clippy, formatting and runtime preflight. The pending
  AI patch applies cleanly and its isolated candidate passes default Clippy
  and the Boss startup probe. Original-client acceptance remains open.

#### Restore authored held-button conditions

- Evaluate `STU_7DEB522D` against the component's live held buttons and
  state-owned blocks. Logical-button listeners resync when a block starts or
  ends and honor their authored ignore-block field. Ability input uses the
  same block checks as the Python reference.
- Cover overlapping blocks, abort, instance destruction and restoring a
  still-held input. Encode the block state through the verified link codec.
  The prepared Reaper probe clears the held-button diagnostic and reports
  two unresolved native queries and AI weapon control.
- Passed 388 default and 391 inspector public checks, all 82 prepared checks
  in both configurations, Clippy, formatting and runtime preflight. The
  current AI patch applies cleanly and its isolated candidate passes default
  Clippy and the focused button and Boss probes. Client acceptance is pending.

#### Apply authored target tags to enemy bodies

- Share the player body's existing grant and suppression accounting with enemy
  bodies. The body owns the effect and its paired controller can read it while
  the state is active. State exit and teardown release the effect.
- The prepared Boss probes clear the target-tag diagnostic on the authored
  startup graphs. Reaper still needs held-button evaluation, two native
  queries and AI weapon control. Original-client acceptance remains pending.
- Passed 387 default and 390 inspector public checks, 82 prepared checks in
  each configuration, Clippy, formatting and runtime preflight. The current
  eight-file AI patch applies cleanly. Its combined candidate also passed
  default Clippy and the focused tag and Boss probes after wiring the two
  pending teardown paths to the same registry.

#### Integrate upstream Rust-only source and architecture rules

- Merge public Rust `13acf15`, preserving PR #19 contributor history and both
  sets of migration and validation records. Resolve only the two documentation
  conflicts; upstream changes no Rust source.
- Keep the Python reference and client launcher in separate local `main`
  checkouts. Retain the combined Python comparison helper in Git history and
  external local storage, removing its remaining tracked Python file from this
  Rust-only tree. Pin the recovery command in the parity record.
- The merged Rust-only tree passes 387 default and 390 inspector public
  checks, all 82 prepared checks in each configuration, both Clippy
  configurations, formatting and native runtime preflight. The current
  eight-file AI patch also applies without textual conflicts.
- The separate pending-AI merge candidate with this turn's pool fix passes
  389 default and 392 inspector public checks, all 83 prepared checks in each
  configuration, both Clippy configurations, formatting and runtime preflight.
  Its new fixtures supply the optional gate, roster and clock bindings. This
  candidate remains an uncommitted local rehearsal; no client acceptance or
  upstream publication is implied.

#### Bind enemy health-pool states to live Vitals

- Give enemy body states owner-scoped pool registration, current-value reads
  and removal using the shared Vitals implementation. Controllers and foreign
  entities cannot read or mutate these state-owned handles.
- When the exact body binds common graph `0033`, retain its initial entity
  variables but replace bootstrap profile pools with the graph-owned pools.
  Keep the existing catalog-owned capacity path for ordinary `0C89` NPCs.
- A lifecycle fixture checks mixed pools without duplicate bootstrap capacity,
  damage, current-value output, remaining health before the end plug, pool
  removal, fresh handles on reentry, healing and aggregate maximum publication.
  Prepared probes check all five Bosses' current and maximum pools independently
  of their still-unsupported startup operations.
- Accept the authored empty `resourceKey` on the active-mode query; preserve
  rejection of nonempty keys and unknown fields. This removes the shared
  `0033` pool-registration and `0043` mode-query blockers without replacing
  unavailable Boss operations with successful placeholders. Full Boss startup,
  introductions, combat and original-client crash acceptance remain open.
- Passed 387 default and 390 inspector public checks, all 82 prepared checks
  in each configuration, both Clippy configurations, formatting and native
  runtime preflight. The five-Boss probe no longer reports pool-registration
  or active-mode-query diagnostics; Reaper still reports five other blockers.

#### Resolve authored Junkenstein Boss creation profiles

- Resolve Reaper, Mercy, Symmetra, Roadhog and Junkrat from their prepared
  encounter body/controller pair when they are absent from the NPC catalog.
  Derive base pools from that exact body's common-graph binding, sharing the
  existing pool parser. Do not substitute cached playable-body pools.
- Route hero creation from the mode's authored scripts through this resolver,
  rather than allowing only the first Zomnic node. Require each Boss's explicit
  controller to match the encounter pair before allocating or publishing IDs.
  Preserve the explicit NPC catalog for ordinary enemies and reject unknown
  heroes, mismatched definitions and invalid Boss pools.
- Prepared checks compare all five Boss profiles and reject ten missing/wrong
  controller requests without allocating a body. A synthetic fixture gives the
  playable and encounter bodies different capacities to verify the selected
  source, and checks malformed or absent definitions.
- Passed 386 default and 389 inspector public checks, all 81 prepared checks
  in each configuration, both Clippy configurations, formatting and native
  runtime preflight against the external 700-graph pack.
- An external startup probe still rejects all five Bosses at unsupported body
  operations. Reaper currently reports health-pool registration, action
  `STU_7DEB522D`, native queries, weapon control and target-tag effects. This
  removes a profile lookup blocker, not proof of Boss startup, introductions,
  combat, replication or a fix for the reported client crash. No new client
  acceptance was performed for this change.

#### Prepare the pending AI integration merge

- Move the internal public-schema option beside the other machine options,
  avoiding a competing insertion at the pending code-observer fields. This
  preserves the schema option and observer registry together without changing
  VM behavior or wire records.
- Rehearse the eight-file pending AI patch in a separate source worktree. The
  combined candidate retains both field groups and adds the new optional gate
  binding to the Boss rejection test. Update the existing Soldier fixture's
  four `STU_CD46AF93` expectations from `Plain` to the implemented `ForCode`
  kind, retaining all subsequent snapshot and wire checks. The candidate passes
  388 default and 391 inspector public checks, all 82 prepared checks in each
  configuration, both Clippy configurations, formatting and runtime preflight.
  This is a local merge rehearsal of an uncommitted snapshot; the other working
  tree is unchanged and its unfinished AI work is not part of this branch.

#### Diagnose original-client Gallery installation failures

- Report the Gallery installer stage and immediate Win32 error instead of an
  undifferentiated failure. Preserve the existing signature gate, patch bytes,
  allocation cleanup and original protection policy. Relay startup now records
  its PID, image size and actual plaintext destination port.
- Passed the MSVC x64 relay build, private-memory installer/protection probe,
  compiled-stub comparison and all 12 native roster replay cases. The two
  Gallery callers add 23 entries (20 after asset pruning); four other callers
  retain only playable heroes.
- Authorized 104319 client attempts with enUS and auto-selected zhCN both stayed
  at a black screen before authentication. Gallery signatures matched, but code
  page protection failed with Win32 error 87 on an execute/read mapped view.
  An experimental copy-on-write retry also failed and was removed. This does
  not establish the cause of the black screen or verify Gallery rendering.
  Both owned client/server sessions were stopped; the existing Python service
  was left running. Arcade, encounter and victory/defeat client acceptance
  remain open. Captures, logs, binaries and profiles remain external to Git.

#### Consolidate Halloween behavior requests and paired messages

- Integrate combined gameplay `b663598`, retaining its component-88 request
  representation, bounded body/controller message queue and first-Zomnic
  publication. Consolidate the overlapping Halloween request implementation
  into this host rather than maintaining competing record formats.
- Retain opt-in public-schema evaluation before assignments and entries for
  enemy roots and descendants, validated schema references and malformed-data
  diagnostics. Keep the existing player startup policy. Optional pool outputs
  continue to publish shared health totals on creation, damage and teardown.
- Keep explicit native hero-query results ahead of the bound-body fallback.
  Limit health/hero readers to the pair's source identities, and publish actual
  death to the body VM and entity-filter flags. Preserve the ten-class lifecycle
  regression covering plugs, child exit, abort, reentry and graph scope.
- Passed 385 default and 388 inspector public workspace checks, all 79 prepared
  checks in each configuration, both Clippy configurations, formatting and
  native runtime preflight with the 700-graph enemy pack. No client was launched
  for this source checkpoint. Navigation, gate attacks, cross-entity scripts,
  Boss introductions/AI, friendly and enemy voice, narrator, UI, gate states and
  victory/defeat acceptance remain open.

#### Initialize the first authored Zomnic body and controller

- Resolve health and hero queries against the linked body from its controller.
  Keep unknown entities unavailable and preserve the float32 total-health read.
  Limit the verified controller base states to `0C87` and the behavior branches
  to `0CF3`, following Python `cc620be`.
- Maintain state-owned component-88 behavior requests with their authored
  defaults, interval and angle conversion, priority order and equal-priority
  insertion rule. Update watched variables and native inputs without changing
  the existing record's order; remove the request on abort or instance teardown.
  Reject unrelated priority resource types.
- Broadcast an entity message to its live roots and route paired body/controller
  messages through a bounded queue drained after each component turn. Preserve
  parameters and reject targets outside the pair. This is not a general scene
  message bus or proof of arbitrary synchronous cross-component ordering.
- The 700-graph Zomnic pair now initializes, passes observer preflight and commits
  one body plus its controller identity. Prepared evidence checks both behavior
  requests and their exact priority/parameters. The real Normal spawner creates
  the team-one Zomnic at its selected collision-search candidate.
- A continued stationary actor probe reaches the `0C89` gate-attack branch and
  still stops at the gate static target, `STU_6A926B21`, ModifyHealth and two
  TrackTargets states. Navigation, attack/damage, waves and original-client
  presentation remain open; behavior registration alone does not drive them.
- Both locked workspace Clippy configurations and formatting passed. Public
  checks passed 382 tests by default and 385 with the inspector. Both variants
  passed all 79 prepared checks, including first-Zomnic publication and the
  actual three-second two-room workload. Native runtime preflight passed.

#### Integrate the latest upstream Soldier owner-session fixes

- Merge upstream Rust `cfea41b` (PR #17) with its original contributor history:
  one-shot effects, message-map projection, owner-session selectors, stat action
  flow, idle-animation lifetime, payload-free Sprint action, native watcher
  classification and owner-script stop diagnostics.
- Retain the combined chase-state payload clock, NPC factory, authored weapon
  paths, Biotic Field and the live owner VM in the movement-gap regression.
  Preserve the upstream 900-frame Soldier session and snapshot checks; classify
  the combined empty texture/resource literals as computed watcher inputs.
- Upstream reports original-client observations for Soldier weapon/HUD, reload
  and Sprint. No original-client check was run for this combined merge, and
  full hero/mission acceptance remains open.
- Both locked workspace Clippy configurations and formatting passed. Default
  public checks passed 376 tests and inspector checks passed 379. Both variants
  passed all 79 prepared checks, including the 900-frame Soldier owner session
  and actual three-second two-room workload. Native runtime preflight passed.

#### Stage authored enemy body/controller creation atomically

- Read the explicit external NPC catalog and carry it in selected Junkenstein
  runtime packs with manifest hashes. Validate hero/body identity and finite
  capacities; keep the catalog separate from training-bot fallbacks.
- Reserve adjacent body/controller IDs, start both authored components and
  preflight the observer baseline before committing room ECS state or a create
  record. A failed controller leaves IDs, room entities and publication unchanged.
  Successful fixture actors share their combat pools with scripts, retain their
  skin and current health for late joins, and do not use practice-bot respawns.
- Seed enemy root public-schema defaults before binding overrides. Port the
  reached NPC health-capacity check, random-float literal, empty texture and
  resource literals, absent outline context and AI control override lifetimes.
  Preserve priority order and remove installed records on abort or destruction.
  A declared controller without the requested weapon identifier declines that
  override; unavailable matching weapon-control services remain unsupported.
- Gate each enemy observer stream on its body-create acknowledgement. Retain
  one outstanding reliable full/delta projection per viewer, retry the exact
  lost record and keep controller scripts server-side, following Python `cc620be`.
- The prepared Zomnic body now starts without diagnostics. Its controller still
  reports nine diagnostics across health/hero queries, controller base states
  and AI behavior requests. The real spawner therefore rolls back creation;
  there is no authored live-enemy or original-client acceptance claim.
- Formatting and locked workspace Clippy passed in both configurations.
  Default public checks passed 365 tests; inspector checks passed 368. Each
  configuration passed all 79 prepared checks, including the real three-second
  two-room workload, and native runtime preflight passed.

#### Evaluate authored entity creation requests

- Port the strict definition/hero creation action from Python `cc620be`.
  Validate finite transforms, identity, team, optional resources and unsupported
  options before invoking the scene allocator. Preserve the old output when no
  entity is returned; failed or invalid bindings do not follow outgoing plugs.
  Scalar config groups evaluate only their first child, matching the reference.
- Add the transform-heading query used by the real first Zomnic spawn request,
  preserving native evaluator priority and float32 angle conversion. The
  prepared Normal timeline now evaluates the creation request and reaches the
  explicit unavailable-allocation diagnostic instead of an unsupported action.
- Read the eight authored enemy pairs and nineteen encounter definitions in
  their own catalog. Validate duplicate resource IDs, required pair records and
  declared AI associations. Accept both existing string and packed-integer tag
  lists. Verify the first Zomnic's body and controller root bindings.
- Formatting and both locked workspace Clippy configurations passed. Public
  checks passed 360 tests by default and 363 with the inspector. Both variants
  passed all 78 prepared checks, including the actual three-second two-room
  workload; native runtime preflight passed. The creation contract regression
  covers 60 combinations of request kind, invalid inputs and allocator result.
- Live allocation, body/controller startup, AI, replication and original-client
  acceptance remain open. This change does not publish partially initialized
  enemies or claim that the first wave can spawn yet.

#### Integrate the upstream workspace and optional inspector

- Merge upstream Rust `bdc8faf`, retaining the combined authored Soldier,
  Junkenstein entrance, countdown, spawn search and recent-input selection.
  Keep the external castle placement packer behavior in `orpheus-runtime`.
- Export the shared analytic helpers needed by projectile cover and Biotic
  Field range checks from the collision member. Gate the encounter inspector
  projection with the same Cargo feature as the upstream debug backend.
- Extend the combined check script to both workspace feature configurations.
  Formatting and both locked Clippy configurations passed. Public workspace
  checks passed 355 tests by default and 358 with the inspector; both runs
  passed all 78 prepared tests with the real three-second two-room workload.
  Native runtime preflight also passed. This checks the merged source, not
  full hero/mission parity or original-client presentation. No push was made.

#### Original-client attempt recorded by the Junkenstein line

- The Junkenstein line records restored user authorization and an original-client
  attempt against `c0904b3`, using isolated native ports and fresh local profiles.
  Its native listeners started, but the client remained at a black screen before
  connecting. The relay reported an NPC Gallery extension installation failure.
- Arcade, Gallery, Boss behavior, dialogue and result presentation remain
  unverified. The line stopped its own client and service and retained private
  logs outside Git. This combined worktree did not launch the original client.


#### Normalize combined mover stances and pass the prepared merge check

- Port the Python `cc620be` minimum stance height into Rust spring bounds.
  The previous raw-height bounds produced different movement bytes for
  three small or wide bodies. The 43-mover replay now matches 10,148
  Python movement records; 8,256 velocity cases also match.
- Add a pinned, clean-reference hash generator and strict combined
  profiles for 43 movers, 699/700 graph wire tables and supported map
  selections. Preserve the original settings and velocity hashes and
  regenerate the 38-mover movement hash for the corrected spring rule.
- Prepare a separate external 700-graph runtime with the retained 104319
  `0B92` XML timer fixture. Keep the original unified runtime untouched.
  The fixture does not enable full live NPC body execution.
- The new `scripts/check_combined_rust.ps1` passed formatting, locked
  Clippy, 349 public library tests, two CLI tests, runtime preflight and
  all 78 prepared checks, including the actual three-second two-room
  workload. Ruff passed for the reference generator. The supported map
  comparison still excludes four Python scripted mission selections.
  Enemy creation, full hero/mission parity and original-client acceptance
  remain open. No remote publication was performed.

#### Combine authored spawn sampling and runtime preflight

- Keep the room-clock spawn search, castle gate, and countdown delivery
  together. The Normal scene returns 172 candidates and compares their
  ordered digest and sample positions with the Python reference.
- Assignments now collect nested config groups, including an empty group,
  while preserving null members inside existing value arrays. Existing
  world-native query results retain their evaluation priority.
- Integrated the upstream runtime preflight and room-local Lua policy.
  Formatting and Clippy passed. Public checks passed 348 library tests
  and two CLI integration tests. The unified runtime passed 71 of 78
  prepared checks, including the actual three-second timing workload.
  The same seven prepared-fixture differences remain open. Enemy
  creation and original-client acceptance remain open.

#### Combine wave startup with countdown delivery

- Keep the authored castle gate and defender roster available to the
  encounter while the room publishes countdown deltas. Scope the shared
  trace action to the verified Junkenstein and Biotic Field graph sites.
- The Normal scene replay reached phase two and checked the first Zomnic
  spawner's hero, placement tag and radius. The combined run passed 346
  public tests and 70 of 77 prepared checks with the seven known fixture
  differences. Spatial search and enemy creation remain open.

#### Replicate the Junkenstein countdown

- Send the authored two-part countdown and its shared value in reliable
  deltas after the mode baseline is acknowledged. Keep the existing UI
  instance and retry a lost delta before sending a newer sample.
- Three packet vectors matched the Python reference byte for byte. The
  encrypted loopback decoded the live countdown value and received a
  byte-identical retry in Revenge, Endless and the three-player challenge.
  Formatting, Clippy and 346 public tests passed. The unified pack passed
  70 of 77 checks with the seven known fixture differences.
  Original-client rendering remains open.

#### Verify Junkenstein readiness through the native worker

- Report the encounter clock and countdown activation in the local room
  inspector. The encrypted worker replay waits for the controller and
  mode frames, picks Cassidy, and confirms that the authored countdown
  starts in Revenge, Endless and the three-player challenge.
- The scene replay confirms that switching to Ana does not restart the
  countdown and that the next authored state is reached. Two Python
  reference checks passed. Formatting, Clippy and 346 public Rust tests
  passed. The unified pack passed 70 of 77 checks, with the opt-in timing
  workload run separately and the same seven fixture differences.
  Encounter effects and original-client acceptance remain open.

#### Integrate room ECS projectiles and shared collision

- Use room ECS flight for physical projectiles while retaining Biotic
  Field healing and its owner delivery order in the same worker tick.
  Keep the authored Junkenstein admission and castle placement on the
  shared collision preparation path.
- Formatting, Clippy and 346 public tests passed. The unified pack
  passed 69 of 76 checks, including the opt-in two-room workload run
  separately for three seconds. The same seven fixture differences
  remain. Original-client acceptance remains open.

#### Bind the prepared Junkenstein room to the selected Arcade card

- Start the encounter using the same exported roots as the mode entrance.
  Bind the castle gate, defender roster, difficulty and challenge flags
  before handing the room to the worker. The local unified pack includes
  the castle placement export alongside both collision caches.
- All 14 cards passed the authored startup check. Encrypted UDP delivered
  the castle gate and the 112-bit mode frame for Revenge, Endless and
  the three-player challenge card. The native binary started and stopped
  using the placement pack. Formatting, Clippy and 344 public tests passed.
  The unified pack passed 67 of 74 checks with seven known fixture
  differences. Encounter progression and original-client acceptance
  remain open.

#### Load the authored Junkenstein mode bindings

- Read the encounter and defender controller graph IDs from the 104319
  mode export for Revenge and Endless. Reject an incomplete Junkenstein
  export before opening its match seat if the shared or defender body
  graph is missing.
- The prepared scene and both entrance frame replays passed, along with
  formatting, Clippy and 343 public tests. The unified pack passed 66 of
  73 prepared checks with the seven known fixture differences. Encounter
  execution and original-client acceptance remain open.

#### Integrate upstream worker timing

- Carry the opt-in worker timing inspector into the combined server.
  Formatting, Clippy and 342 public tests passed. The unified runtime
  still passed 66 of 73 prepared checks with the seven previously
  reproduced fixture failures. Native startup and shutdown passed with
  the inspector enabled and disabled.

#### Combine Junkenstein entry and the Practice Range runtime

- Carry the native Junkenstein Arcade entry and NPC Gallery through the
  same server build as the Soldier and ECS combat changes. The login
  regression checks the 23 NPC entries in both Gallery arrays and compares
  the original playable catalog and login sequence with the Python corpus.
- Prepare one local pack with tagged bodies, both mode graphs and the 0688
  and 06C9 collision caches. The server started and shut down on that pack
  with the inspector enabled and disabled.
- Formatting, Clippy and 339 public tests passed. The unified pack passed
  66 of 73 prepared checks, including all five Junkenstein entry checks
  and the encrypted Soldier Helix and Biotic Field regressions. The seven
  remaining failures also occur on the upstream baseline with the same
  catalog and graph inputs. Encounter progression and original-client
  acceptance remain open.

#### Integrate unified room bot state

- Keep each bot's movement, patrol and vitals on the same ECS entity.
  Biotic Field uses that entity's current capsule and publishes health
  updates under its wire ID. The regression covers bot order changes and
  excludes dead, opposing and out-of-range bots.
- Keep the upstream collision-region inspector and its prepared waypoint
  check. Rust formatting, Clippy and all 339 public tests passed. With
  the combined runtime, 61 of 68 prepared checks passed. Unmodified upstream
  on the same runtime reproduced the same seven catalog, mover, patrol
  and graph fixture failures. Original-client acceptance remains pending.

#### Integrate upstream ECS combat and Lua patrols

- Heal training bots through their wire IDs in the room's ECS combat
  state. The prepared regression reorders the scene and verifies that
  treatment still applies only to the live ally within range.
- Update the input-gap regression to exercise the attached Soldier VM
  before switching bodies. The replay still checks pose parity and
  command acknowledgement across the queued switch.
- The merged source passed 338 public Rust tests and Clippy. The combined
  runtime passed 60 of 67 prepared checks, including encrypted login,
  Soldier rifle, Helix, Biotic Field, bot respawn, and the inspector.
  Seven checks against other prepared catalog and graph profiles remain
  open. Original-client acceptance remains pending.

#### Soldier Biotic Field entity delivery

- Create the ground field for the owning peer after acknowledgement of its
  predicted canister. Deliver the authored active and fade frames after
  the ground entity arrives. Retire the ground entity before its canister.
- The prepared encrypted Practice Range check matched the two Python
  reference state frames and verified delivery order through expiry. Three
  prepared Biotic Field checks and the 331 public Rust tests passed, along
  with formatting and Clippy. Original-client rendering and multiplayer
  delivery remain open.

#### Soldier Biotic Field healing

- Admit the actual Soldier Biotic Field cast and confirm its predicted
  canister. Use the authored skill clock and client pulser wire layout.
- Heal the caster and live friendly training bots within capsule range.
  Apply elapsed world ticks only inside the five-second field lifetime and
  retire the canister without an explosion.
- The prepared-runtime tests cover the authored cast, timed canister
  delivery and retirement, and friendly bot health updates. The public Rust
  suite passed 331 tests with 67 ignored. Four Python reference cases passed.
  Multiplayer recipients and original-client acceptance remain open.

#### Integrate the upstream Rust inspector

- Keep the upstream inspector and its shared codec fixture. On the combined
  tree, Rust formatting and Clippy passed, 330 public Rust tests passed, and
  six prepared checks passed for the Soldier, Helix, selection and inspector.

#### Authored Helix delivery and projectile splash cover

- Stop splash damage at static cover. Test visibility to the nearest capsule
  surface from the approach side of a wall impact, preserving exposed splash.
- Verify the authored Soldier Helix over encrypted loopback, including direct
  and splash damage, lost-create recovery, and release after acknowledgement.
  The six-second cooldown and synthetic worker cases pass alongside it.
- The locked Rust suite passed 324 tests with 63 ignored. The three prepared
  Helix tests and seven Python Pharah reference tests passed separately.
  Original-client presentation and the other heroes' projectile graphs
  remain open.

#### Authored Soldier body and rifle on the Rust worker

- Enrich an existing runtime pack with authored target tags using the normal
  Rust preparation command. Preserve the source pack and hash the destination
  files.
- Follow the Soldier crouch effect's server-side graph path and serialize its
  typed message table using the reference pair-array wire form.
- Verify authored body startup, owner-frame retry, movement correction and a
  training-bot rifle hit with a prepared 104319 runtime over encrypted loopback.
  The locked Rust suite passed 324 tests with 62 ignored. The stock attach
  and rifle loopback cases also passed with the prepared runtime.
  Original-client presentation, stock Helix delivery and the other hero and
  map workflows still require verification.

#### Directional input modifiers

- Apply the authored forward, backward and lateral throttle modifiers in the
  Rust match mover. Keep the original input bytes for replication.
- Add a reference regression for sign, clamping, activation and release.
  The prepared Practice Range movement records match the Python reference.
  Original-client movement acceptance is pending.
- Reuse the synthetic volley codec in the projectile fixture so a public
  source checkout can compile its tests without a prepared runtime.
- The locked Rust suite passed 322 tests with 61 runtime cases ignored.
  Rust formatting and Clippy checks passed.

#### Rust contribution rules

- Documented the Rust dedicated server and Bevy ECS boundaries, authored and event-driven gameplay policy, collision investigation and loopback inspection. Linked the rules from both READMEs, the contribution guide, agent instructions and the pull request checklist.
- Verified six added links and anchors, runtime preflight, native startup and three encrypted loopback checks covering login, Practice Range loading, volley hits, bot replacement and Helix flight. Documentation only. No original-client test was run for this entry.

#### Rust-only branch tree

- Removed the Python server (`ow174/`), its tests and golden fixtures, the dashboard JavaScript test, the Python and PowerShell data tools, the synthetic command replay fixture, the Python launchers, `requirements.txt`, `pyproject.toml`, `WIP.md`, the Python CI job and the Python editor, attribute and packaging ignore entries. Kept ignore patterns for `__pycache__/`, `*.py[cod]` and `.venv/`, which a clone switched from `main` leaves behind. No Rust build, test or tool uses the removed files.
- Kept the Rust workspace, the C++ client relay, the research and format records, the data provenance guide, the license, credits and history. The Python reference, its tests and the data tools live on `main`. The contributor guide lists only the two Rust CI lanes and the prepared-runtime check. Contributor documentation points to `main` for the Python reference, tests and data tools; research records and provenance comments keep their original paths.
- Passed formatting, both Clippy lanes, 354 default and 357 inspector workspace tests and 82 relative documentation link checks. No Rust source changed. Original-client acceptance is unaffected because no runtime behavior changed.

#### Make the native inspector opt-in

- Made the loopback HTTP inspector a default-off Cargo feature. Moved its web assets to `rust/assets/inspector`. The CLI explains how to enable the feature when `--debug-port` is requested.
- Added default and inspector lanes to public CI and documented the module graph findings, retained entry points and remaining crate boundaries in the structure review.
- Passed 343 default and 346 inspector workspace tests, both Clippy configurations, formatting, 67 relative documentation link checks and the Rust 1.85.1 library and binary check with the inspector enabled. The prepared-runtime encrypted login and Practice Range delivery regression passed with the inspector feature enabled.

#### Separate runtime preparation and integration tests

- Moved the runtime packer, input inventory and preflight to `orpheus-runtime`. It builds independently of the game server. The CLI and existing server runtime API remain available, and the packer and graph reader share their catalog format helpers.
- Moved the encrypted protocol fixture to `rust/tests` and verified that the server reads a synthetic graph bundle enriched by the separate packer. Added a CLI contract check to the runtime member.
- Passed 343 public workspace tests, workspace Clippy and the locked Rust 1.85.1 library and binary check. Original-client acceptance remains open.

#### Extract the collision workspace member

- Moved collision cache decoding, scene queries and the contact solver into `orpheus-collision`. The member has no dependency on the server, ECS, Lua or networking. Existing server import paths remain available through re-exports.
- Added the Cargo workspace and shared package metadata. Public CI and contributor commands now run `--workspace` so the extracted kernel tests remain part of the validation lane. Documented current scene, wire and room responsibilities and the remaining coupled interfaces.
- Passed 286 server unit tests, 53 collision unit tests, two runtime CLI integration tests, formatting and workspace Clippy. Three authorized prepared-runtime regressions passed for the upstairs patrol, collision sampling and encrypted native login and Practice Range delivery. Original-client acceptance and build-time measurements remain open.

#### Junkenstein live enemy collection

- On the combined workspace, both locked Clippy configurations passed.
  Public checks passed 357 tests by default and 360 with the inspector.
  Both configurations passed all 78 prepared checks, including the actual
  three-second two-room workload. Runtime preflight also passed. These
  combined results supersede the standalone branch counts below.
- Bind the authored `0CBA/56` target-collection action to the room's ordered living team-one bot bodies. Keep unsupported filter shapes explicit, and share output-before-follow behavior with the existing participant-query state. Empty collections clear the output; an unavailable query leaves the output and outgoing plug untouched.
- The prepared Normal timeline now runs through spatial search, the authored delay, target collection and recent-input selection to the still-unbound `CreateEntity` action. The regression checks the selected point against all 172 searched candidates and retains the retired-search lifetime check. Enemy allocation, body/controller scripts, AI, dialogue and result presentation remain open.
- Passed 345 public unit tests, two runtime-preflight CLI tests, eight Junkenstein offline checks, formatting and Clippy. Original-client acceptance remains blocked at the separately recorded startup black screen; this change has no client evidence.

### 2026-11-10

#### Rust stock Soldier idle fidget, Sprint and owner switch polling

- Left an animation without an authored duration on until a transition or its parent ends it, as the reference does, which knows the asset's length no better. Only the shape seen on the reached `02C5` idle fidget, node 51, is accepted: no client removal flag and no consumer of the animation-finished or finished plug. Any other shape still stops the body with the missing duration. Like the reference, the server does not end the state at the asset's length, so its finished and end follows wait for that transition.
- Passed the payload-free `STU_E9694EFD` action through its output, as the reference's unmodeled fallback does. Only the exact authored build 104319 form is accepted: the node header, no sync variables or state slot, and two unlinked plugs. Every instance in both packs has that form.
- Left switches polled on a native outside the reference's client-agreed set out of owner frames, and kept sending their live subgraphs, as the reference does. The record persists across activations, like the reference's. Observer frames are unchanged. Natives are classified only for watched evaluations.
- The tagged stock Soldier owner VM now runs idle, held fire, reload, a second burst, the idle fidget, fire during the fidget and Sprint over 900 frames without diagnostics in the prepared session test. Its owner frames match the frozen reference's ammo, reload and Sprint HUD transitions. The withheld switches at spawn match the reference's for every instance the two trees share.
- Passed 348 public Rust tests and 62 tagged base-runtime checks. The enriched-runtime firing placement check needs a bot-graph pack that was unavailable. The untagged bundle also passed the stock owner and Sprint regressions. Cargo formatting, Clippy and Git diff checks passed.
- Original-client check on the native server: the body stayed attached through 16 seconds of idle, firing, reload and Sprint, and the worker logged no script stop. Firing lowered the ammo display, reload refilled it to 30, and Sprint showed its active icon while the body ran forward. The view rendered normally after spawn without opening the in-game menu. A scripted held button fired two rounds and then stopped on both the native and the reference server, so continuous fire still needs a check with a physical mouse. Original-client acceptance remains pending.

#### Rust body script stop diagnostics

- Logged the reason when the live worker stops an owner body script, with each initialization diagnostic as graph, node and issue. Previously the worker cancelled the script silently. This exposed the `02C5`, node 51 animation duration stop during held fire in the original client.
- Passed 326 public Rust tests and 60 tagged base-runtime checks. Cargo formatting and Clippy passed.

#### Rust stock Soldier owner session

- Skipped the parameters of a game message that has no recipients. The reference evaluates them first but delivers none, so the only difference is any random draw they would make. A routed message still refuses an unsupported parameter.
- Followed the output plug of stat criteria actions, as the reference's no-op does.
- Answered the `STU_ED45C5E8` entity selector with the reference's unmodeled null default, at the player site in the Practice Range only, and only when every operand is the owner or a zero literal. The resolved current possessor sends the idle body into a periodic branch in graph `01CA` that the reference never takes, because it leaves that query unanswered. That graph handles the message it sends to the selection, so null probably drops a local delivery. The relay form with a set flag and dynamic subjects stay unsupported. The original server's selection remains unverified.
- The tagged stock Soldier owner VM now runs idle, held fire, reload and a second burst without diagnostics in the prepared session test. Its owner frames match the frozen reference's ammo and reload transitions frame by frame. Sprint activation stops at the `STU_E9694EFD` action at `0255`, which the reference only passes through its unmodeled fallback. The untagged bundle still stops at `STU_AC829876` during startup.
- Passed 326 public Rust tests and 60 tagged base-runtime checks. The enriched-runtime firing placement check needs a bot-graph pack that was unavailable. The untagged bundle also passed the stock owner and loopback regressions. Cargo formatting, Clippy and Git diff checks passed.
- Original-client check on the native server: the body no longer stops one second after spawn. Holding fire in the live worker stops the owner VM at an animation duration query in graph `02C5`, node 51, so the ammo display still empties at once and reload does not start. After spawn the view stays black until the in-game menu is opened and closed. Original-client acceptance remains pending.

#### Rust stock Soldier owner attach

- Omitted an unbound script map from owner and observer snapshots, as the reference omits the None it evaluates for an authored `STU_7757D403` message table. The map stays in the VM for message lookups. A bound map is still refused, and the wire form of maps remains unverified.
- The tagged stock Soldier body now attaches. Its preflight encodes the 24-instance tree in both views without the message table in instance 24, variable 29859. About one second after spawn, the idle owner VM stops at the `STU_ED45C5E8` recipient selector in graph `01CA`, which the reference also evaluates to None. The untagged bundle still stops at `STU_AC829876` during startup.
- The encrypted stock loopback now expects owner corrections exactly when the tagged body attaches. The native gap regression runs without an owner VM, because it checks poses from the plain mover.
- Passed 324 public Rust tests and 60 tagged base-runtime checks. The enriched-runtime firing placement check needs a bot-graph pack that was unavailable. The untagged bundle also passed the stock owner and loopback regressions. Cargo formatting and Clippy passed.
- Original-client check on the native server with the tagged pack: Soldier: 76 now spawns in the Practice Range with the rifle, 200 health, 30 rounds, the three ability icons, the ultimate meter and the crosshair, and walks normally. Holding fire empties the ammo display at once and reload does not start, consistent with the owner VM stopping after spawn. Original-client acceptance remains pending.

#### Rust Soldier startup effects and snapshot diagnostics

- Continued the authored one-shot effect action through its output without server state, following the reference server. Like the reference, the server never flags the remote slot of a networked effect. Effects that bind a dynamic `m_37B02425` remain unsupported because that field's role is unverified in build 104319.
- Listed every initialization diagnostic as graph, node and issue when the snapshot preflight refuses a body. A script map variable now names its owner and variable instead of failing later without a location. Network serialization of maps remains unverified.
- The tagged Soldier startup now completes without diagnostics at `01C7`, node `74`. The preflight stops at the authored message table in instance 24, variable 29859. Stock weapon attachment needs the 104319 map wire form, or evidence that the original server omits such variables.
- Passed 322 public Rust tests and 60 tagged base-runtime checks. The enriched-runtime firing placement check needs a bot-graph pack that was unavailable. Cargo formatting and Clippy passed.
- Original-client check on the native server before this change: login, the menus and the Practice Range load, and Soldier: 76 spawns without the rifle or HUD. The preflight still refuses the stock body, so that result is unchanged. Original-client acceptance remains pending.

#### Rust developer preview and runtime preflight

- Replaced the inherited status table with separate Python, Rust and original-client evidence. The bilingual README separates public Rust checks, prepared deployment and client acceptance. Added the cutover parity checklist.
- Added `orpheus_server --check-runtime` and shared the check with native startup. It reports all missing Practice Range pack paths across generation, prepared research and restricted input groups. It checks the local signing input length without printing its contents.
- Passed 339 public Rust unit tests and two CLI integration tests, formatting, Clippy, relative documentation links, and authorized native preflight and encrypted startup. A publicly reproducible original-client authentication setup remains open.

#### Isolate Lua policy state by room

- Gave each prepared room a private Lua patrol VM on the match worker. The server validates the bundled policy at startup and uses that instance for the first prepared room. Later rooms initialize independent instances. Room retirement drops their policy state with the room.
- Passed 339 public Rust tests, formatting and Clippy. Three prepared regressions cover stateful Lua isolation between rooms, the elevated patrol movement sequence and encrypted world delivery. Original-client acceptance is pending.

#### Repeatable two-room timing workload

- Added an opt-in workload with two encrypted UDP clients in independent solo rooms. Both clients must advance input and receive fixture hits. The report separates worker timing and scheduling from packet and world-frame arrivals, and records both scene preparation times.
- Two sequential ten-second debug comparisons used the same runtime and reversed the test order. Second scene preparation fell from 1.01–1.07 s to 78–84 ms. Each run delivered 70 distinct hits and 209 world-bearing frames per client. The worker p95 stayed in the 2–4 ms bucket and p99 moved from 2–4 ms to 4–6 ms in both comparisons. Optimized-build timing and original-client behavior remain open.
- Passed 339 public Rust tests, 63 prepared base-pack regressions, the enriched timer fixture, formatting, Clippy and the Rust 1.85.1 build check.

#### Inspect live patrol collision clips

- Added the last successful clipped patrol move to the opt-in inspector. It reports the first limiting sweep's proxy, category, fraction and simulation frame, plus the before, intended and final feet positions. The trace uses the existing solver and resets on bot replacement.
- Passed 339 public Rust tests, formatting, Clippy and two prepared checks. The synthetic wall test identifies the expected proxy and mask. Two elevated patrol routes produced identical movement records with tracing on and off over 1,900 frames. The original-client wall report remains open.

#### Room ECS projectile flight

- Transferred admitted flights to generational room entities. The room owns physics, collision and damage, and the connection applies terminal outcomes to the matching prediction lease. Body replacement, leave and room drain retire the relevant flights.
- Passed 339 public Rust tests, formatting, Clippy and prepared encrypted volley, Helix and owner VM loopbacks. New checks cover repeated prediction tokens across owners, stale body generations and completed or failed flight release. Player movement remains connection-owned. Original-client acceptance is pending.

#### Reuse prepared map collision across rooms

- Reused the immutable ground and contact indexes across preparations with the same map, mode and contact requirements. Room bodies and movement state stay independent. The cache retains one key and runs outside the simulation worker.
- Passed 337 public Rust tests, formatting, Clippy and five prepared checks. The new 104319 regression confirmed that two rooms share the same collision indexes and can move their bots independently. Prepared geometry, elevated patrol and encrypted startup checks also passed. Original-client acceptance is pending.

#### Worker timing in the Rust inspector

- Added opt-in tick duration and schedule lateness histograms, skipped tick counts and input budget counters. The inspector displays server-side p95 and p99 bucket bounds. Capture starts with an active prepared world. The normal worker adds no timing reads with the inspector disabled.
- Passed 337 public Rust tests, three prepared checks for encrypted startup, active-room timing and the five-peer input budget, plus formatting and Clippy. Headless Chrome verified the idle panel and percentile rendering. Comparative sustained-load performance and original-client acceptance remain pending.

#### One ECS entity per live bot body

- Moved the bot transform, collider and patrol state onto the same Bevy entity as vitals, rewind history and respawn. One ordered wire-ID index preserves spawner order and lets movement, hits, replication and the inspector address the same body.
- Kept the existing combat activation boundary and the retire-before-create delivery order. Prepared replacements retain their original spawn slot and receive a fresh body handle.
- Passed 334 public Rust tests, 62 prepared base-pack tests, the enriched timer fixture, formatting, Clippy and the Rust 1.85.1 build check. Encrypted world tests still cover initial baselines, hit delivery, bot replacement and Lua patrol visibility. Player and projectile authority remain separate. Original-client acceptance is pending.

#### Local collision region inspection

- Added a bounded collision sample centered on an active actor or an entered XYZ point. The server uses its loaded collision indexes and keeps sampling off the match worker. The inspector reports geometry source and truncation.
- Passed 334 public Rust tests, formatting and Clippy. Two prepared 104319 checks verified the loaded collision and an uncapped sample around the highest patrol waypoint. Headless Chrome rendered the real scene and verified layer toggling, coordinate sampling and the spawn reset. The reported original-client wall crossing remains unverified.

#### Event-driven Lua patrol pilot

- Moved the existing patrol waits and next-waypoint decision into an embedded Lua policy. The worker validates the policy at startup and validates each event result before applying it. Collision, movement, timing and packet delivery stay in Rust.
- Limited the Lua environment, instructions and memory. A failed patrol command stops that bot until replacement and preserves its last executed input. The inspector reports which bots have a running patrol policy.
- Passed 333 public Rust tests, 61 prepared base-pack tests and one enriched timer test. The prepared checks cover the existing patrol movement bytes, elevated routes and encrypted match delivery. Formatting, Clippy, the Rust 1.85.1 build check and native startup with the inspector both enabled and disabled passed. Original-client acceptance and tick-tail measurement are pending.

#### Bot combat authority in Bevy ECS

- Moved active bot vitals, rewind poses and respawn deadlines into a room-local Bevy world. Wire IDs resolve to generational handles, and the inspector reads health through the same identity lookup.
- Validate all due replacements before retiring any body. Keep the scene's existing order for hit ties and replacement IDs.
- Passed 328 public Rust tests, formatting, Clippy and four prepared Practice Range regressions covering rifle hits, bot replacement, encrypted volley and Helix delivery, and live actor inspection. The new public regressions cover scene reordering, stale handles and a failed respawn batch. Bot movement and player authority are still separate from the ECS combat component. Original-client acceptance is pending.

#### Native server architecture and scene inspector

- Documented the room authority, replication, event-driven scripting, collision and cutover boundaries. Rust development and public CI now target `rust`. The default branch continues to serve the Python implementation.
- Added an opt-in Rust scene inspector on loopback. It shows tick-stamped actors and separate probe-triangle and character contact-bound layers sampled from the loaded Practice Range collision. The inspector reports limits and leaves the match worker in control of simulation state.
- Removed the public projectile fixture's build-time dependency on a private codec table. The test now shares the checked-in protocol fixture.
- Verified 326 public Rust tests, 61 prepared base-pack tests, one enriched-pack timer test, Rust formatting and Clippy. The encrypted Practice Range check observed the player's active body and 15 bots through the inspector snapshot. The original client was not started. The reported upstairs wall crossing still needs a matching trace.

### 2026-11-11

#### Junkenstein and Hero Gallery

- Route the 14 Junkenstein Arcade cards through native solo matchmaking. The 104319 queue catalog confirms the map, mode and player capacity for each card, including the three-player challenge. Compare the lobby messages with the retail schema and the initial mode and hero-selection frames with the Python reference. The encounter runtime, difficulty and challenge behavior, and original-client acceptance remain open.
- Include the 23 named non-playable characters in the Rust gallery catalog without adding them to the playable roster. The relay filter is scoped to the two native Gallery readers. The retail schema and Windows relay build pass without launching the client.
- Port the base action and scoped array reset reached by the authored encounter roots. A Rust fixture checks that the outgoing plug observes the fresh array and that the other variable scope retains its value. Scene queries and encounter effects still need a room-world binding.
- Validate the gate placement, target tag and graph when preparing either Junkenstein mode. The offline authored roots resolve the gate and can read the admitted team size. The Rust runtime pack includes and hashes the external map-world file when it is supplied with map `06C9`. Missing or invalid scene inputs surface as queue unavailability. Live room events, encounter effects and original-client acceptance remain open.
- Initialize the two authored encounter roots during Arcade handoff with the selected card's difficulty, challenge variables and the admitted team. Verified the 14 difficulty identifiers and six challenge flags against the 104319 card and ruleset exports. The offline scene fixture executes startup for all 14 cards and checks the authored difficulty assignment, gate and challenge variables.
- Advance the encounter on the room clock after world startup. Route the accepted hero through the authored solo ready message at the start of its simulation frame. The 104319 fixture checks both countdown states, phase one and hero changes without a countdown restart. Encounter effects and enemy creation still need their room bindings. Original-client acceptance remains open.
- Run the placed castle gate's `0CA7` script on the room clock and expose its authored initial health and the admitted defender roster to the mission. This keeps the encounter in phase two after the opening and lets the Normal wave graph start its authored first Zomnic spawner with the retail hero, placement tag and search radius. The offline fixture checks the phase transition and spawner inputs. Spatial search, enemy creation and presentation remain open.
- Resolve the first spawn region from its loaded map placement and forward transform. Run the authored `0CBA` search with a 320-quarter-metre-step budget per frame, checking slope, steps, headroom and capsule-width static rays. The Normal fixture produces the same 172 candidates as the Python reference, matching the complete ordered float32 coordinate hash and selected float64 samples. Search completion uses the active state lifetime and the fixture verifies that a retired request ignores late results. Enemy creation and encounter presentation remain open.
- Implement the authored `0CBA` recent-input selector with timed slot history, oldest-slot eviction and an unbiased local random draw. Isolated offline fixtures cover distinct selections, history expiry and reset, shrinking and empty candidates, and nonpositive lifetimes. The prepared Normal graph now admits the selector after search completion. Its later target-collection action remains unbound, so the full spawner has not yet reached enemy creation. The Rust random stream is deterministic; selected-slot parity with Python's random stream is not claimed. Validation is static/offline only at the user's request; original-client presentation remains unverified.
- Passed 343 public Rust unit tests, two runtime-preflight CLI tests, seven prepared offline Junkenstein checks, formatting and Clippy for the recent-input change. No original client or standalone server was launched for this validation.

### 2026-11-06

#### Ultimate charge from combat

- Credited the ultimate's charge for the damage a body dealt, the healing it did, and its eliminations
  (combat.py through the new ult_charge.py). The rates are a data table, `game_ult_charge_174.json`: a
  hero fills the meter with its `damage_to_full` / `heal_to_full` of HP and gets `kill_bonus_percent` of
  a full meter per elimination, converted against the cost #338e its own Entries carry, so the meter is
  right in whatever scale a hero's graphs use.
- OW1 computed these rates in its server code rather than in data: the hero's entity definition has no
  charge component (its components are health, weapon, mover, model, first-person, statescript, target
  tags and voice) and the generic settings carry no charge keys. The shipped table is therefore marked
  `placeholder` and holds community-measured values (380 damage for Soldier: 76's full meter, a 20 %
  elimination bonus); replace them from the original server before trusting the balance.
- Added the generating tool `tools/extract_game_ult_charge.py`; the table joins `MATCH_RUNTIME_DATA`, so
  the runtime pack has to carry it (the loader keeps the fallback and says so if the file is absent).
- Added 12 tests: the rule's arithmetic, its defaults and fallback, the shipped table's shape, and the
  credit at the damage, elimination and healing sites. The full suite (1347) passed, Ruff and Git diff
  checks passed. Client acceptance for the charge climbing under fire is pending.
- Logs report when a body's passive charge rule blocks, and when the meter stops moving although the
  rule runs, with what the client acknowledged (frames, ack, pending). On the target client this told a
  blocked rule from a stalled owner stream: the meter froze on the client while every owner frame was
  acknowledged and the server meter reached its cost; suppressing the combat notices (25001, 25005)
  behind the new `OW174_NO_COMBAT_NOTICES=1` lever makes the same scenario run correctly, which pins
  the stall to the notice records sent while the victim's body is retired.

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

#### Rust transport foundation

- Ported the game bit stream, encrypted UDP datagrams and ACK window, plus the lobby JAM cipher, encrypted TCP framing and address-bound handshake. The handshake takes its signing input from the caller and the tests use synthetic keys.
- Added Windows Cargo checks and documented the migration and its behavior references in both README entry points and the credits.
- Passed 16 Rust tests, including exact Python wire vectors and an encrypted loopback lobby handshake. Cargo formatting and Clippy passed. Passed 19 focused Python regressions with the prepared Practice Range runtime, plus Ruff and Git diff checks.
- The Python entry point continues to serve the lobby and matches while their behavior is ported. Original-client acceptance of the Rust implementation is pending.

#### Rust message and input processing

- Ported the JAM schema codec, game message channel, client command parser and queue, plus lobby announcement, keepalive and BattleTag query handling.
- Ported Battle.net protobuf framing and local account-name session keys. The descriptor set is loaded from the separately prepared runtime.
- Decoded an encrypted datagram generated by Python with both message lanes and a command record. Verified the message boundary, command values and duplicate rejection in Rust.
- Passed 37 public Rust tests and three optional runtime checks for the 104319 schemas and Battle.net descriptors. Cargo formatting and Clippy passed. The frozen Python reference at `795d56d` passed 1,335 tests. The Rust lobby service and match simulation still need implementation and original-client acceptance.

#### Rust accounts, runtime preparation and world frames

- Ported account storage and identity selection, regional catalog lookup, and the local Battle.net TCP and WebSocket listener. Checked Connect, Logon and Referral responses against the Python implementation using the 104319 descriptors. Exercised shutdown with stalled connections on Windows.
- Ported the deployment data preparation command. For the authorized Practice Range runtime, all 23 copied files match the Python tool by SHA-256 and both manifests describe the same files and hashes.
- Ported world-frame encoding and fixed-step scheduling primitives. A mixed 452-byte world payload matches the Python output. The lobby and game entry points still use Python while the Rust modules are integrated.
- Passed 64 public Rust tests, five optional runtime checks, Cargo formatting, Clippy and Git diff checks. Original-client acceptance remains pending.

#### Rust match admission and map cache loading

- Ported the lobby match ticket, queue-pop and delayed seat delivery messages, and the UDP connection lifecycle. A tampered, stale or expired datagram cannot rebind an existing peer. An expired ticket releases its seat on the timeout sweep.
- Used the prepared 104319 schema to encode and decode a 20600 ticket, then used its directional keys to complete an authenticated SYN exchange over loopback UDP.
- Ported retail lobby template loading, personalization and the account's initial settings and endorsement messages. Compared encoded message digests against the Python reference with the prepared schemas.
- Added a bounded XZ and geometry reader for version 5 movement and expanded-ray collision caches. A local release build decoded all 190 prepared caches across 95 maps. Every decoded field of the two Practice Range caches matched the Python uncompressed byte stream.
- Passed 86 public Rust tests, eight optional runtime checks, 39 Python matchmaker tests, Cargo formatting, Clippy and Git diff checks. The normal lobby and match entry points still use Python. Collision queries, full lobby and match integration, and original-client acceptance remain pending.

#### Rust login session, game ingress and combat primitives

- Connected the lobby transport to BattleTag lookup and login preparation. Checked a 43-message initial login subset against the Python wire order. Dynamic collections, queue content and other post-login handlers still need integration.
- Ported reliable message dispatch before input parsing, Pong and the four map-entry milestones. A mixed frame and its Pong match the Python bytes with the 104319 game schema.
- Added static ray primitives and an indexed core triangle scene. Compared 2,720 ray, ground and ordered nearby queries on both prepared Practice Range caches. Hit results, floating-point fields and ordering matched the Python reference in this comparison. Prop placement and character contacts are separate.
- Ported the body health pools and their dirty updates, including shield and armour order and lethal attribution. Full and partial pool updates use the same world component encoder. Original-client validation of health presentation and combat behavior remains pending.
- Passed 107 public Rust tests, ten optional runtime checks, 27 Python combat tests, Cargo formatting, Clippy and Git diff checks. The normal server entry point still runs the Python implementation.

#### Rust match loading worker and lobby collections

- Added a native UDP worker that reserves match seats, authenticates tickets and schedules frames on the fixed game clock. It serves instance and roster messages, ownership and Pong. The worker keeps one owner for connection state and uses readiness polling with bounded receive batches so datagrams cannot delay the tick indefinitely.
- Ported the reliable outgoing queue and ACK handling. A failed send leaves the queue intact, and a reported loss requeues the original reliable ordinal. In an encrypted loopback with the 104319 schemas, deliberately dropping the first instance frame triggers a retry before the ownership and roster messages. Also checked canceled handoffs, FIN and socket shutdown.
- Added collection, loadout and portrait handling to Rust. The initial login subset now includes progression and hero ownership. Its 45 encoded messages match Python for both the default profile and the level-3000 full-unlock fixture.
- Added collision scene assembly for placed static triangles and round shapes. Compared 2,079 queries across three prepared Practice Range views, including authenticated root placements. The selected hits, ground and nearby outputs matched Python bit for bit.
- Passed 124 public Rust tests and 13 optional runtime checks, plus 67 focused Python reference tests covering ownership, shop behavior, frame delivery and UDP match loading. Cargo formatting, Clippy and Git diff checks passed. World creation, character contact and original-client acceptance are pending. The usual launch commands still use Python.

#### Rust map catalog, graph metadata and round contacts

- Added the shared map and mode catalog, ordered placeables and spawn selection. The UDP worker now uses the same map type as catalog lookup. Checked 95 map listings and all 698 candidate map and mode combinations against Python, including the 551 supported results and their placement and spawn order.
- Loaded the authorized state graph bundle and built the owner, remote and variable-slot indexes from its records. All graph layouts in the 373-graph and enriched 405-graph bundles matched the Python reference. The enriched bundle also loaded eight non-player definitions and 25 mode records.
- Ported float32 sphere and capsule contacts with ordered features and cache placement. A local differential run matched Python bit for bit across 12,288 cases and 7,611 contact points. These primitives await the character solver and do not change the active Python server.
- Added own presence to the Rust login subset. The 46-message subset matches Python for both the default and level-3000 full-unlock profiles, and seven additional presence packets match the reference.
- Passed 140 public Rust tests and 16 optional checks with the prepared Practice Range pack, plus the enriched graph bundle check and 21 Python presence reference tests. Cargo formatting, Clippy and Git diff checks passed. Original-client acceptance remains pending.

#### Rust world bootstrap and lobby roster messages

- Ported the initial world records for map placeables, the player controller, the mode entity and training bots. The 21-record Practice Range baseline matches the Python wire payload. The packet budget splits it into ordered datagrams.
- Added shared statescript value, chunk and full-frame writers. Verified Practice Range and PvP controller frames, team entries and the mode UI frame against Python. The controller and mode frames are ready for the worker's entity delivery path.
- Ported training bot route state and shared mover settings. Compared the 15-bot Practice Range roster, 19,560 movement records, and all 34 fields for each of 38 prepared mover bodies. Ported scoreboard and on-fire fields and matched 462 scoreboard wire cases. Combat events still need to drive the on-fire values.
- Ported the float32 GJK distance solver and one-sided capsule contacts against mesh triangles. The Python comparisons matched on 12,000 synthetic distance queries, 2,048 prepared mesh distance queries and 6,144 prepared capsule contact queries. Mesh candidate traversal, sweeps and character integration are pending.
- Added the lobby clock, party payload and ranked rating projection. Verified the 50- and 51-message login subsets when the required ranked and solo party inputs were available.
- Passed 169 public Rust tests, 24 optional runtime checks on each of two prepared packs, Cargo formatting, Clippy and Git diff checks. The native UDP worker still serves the loading phase. World delivery, hero selection and original-client acceptance are pending. The normal launch scripts still use Python.

#### Rust headless Practice Range world delivery

- Connected the decoded lobby Practice Range request to an account-backed world card and the native UDP worker. The headless test covers the encoded handoff, authenticated UDP, withheld ownership ACK, dropped entity creates, and the controller and mode scripts after their own create ACKs. The supported bootstrap uses the base graph bundle. The enriched bundle's authored bot graphs are detected before opening a seat and await the bot VM.
- Loaded the scene outside the game tick and ran the bots against its prepared collision ground. Both runtime packs matched Python for 7,500 serialized movement records per pack. The world sender borrows queued entities and retains their shared records for retry.
- Shared the collision grid between the float64 ray view and the float32 mesh candidate index. Matched 5,000 triangle-box checks and 4,400 ordered mesh queries against Python. All 4,799 earlier ray and scene query comparisons still matched.
- Ported the hero catalog, graph-derived health pools and owner and observer body creates. The 64 body create vectors cover all 32 heroes. Decoded the original hero pick and open, close and toggle messages. Live hero selection, movement sweeps and original-client acceptance remain pending.
- Passed 183 public Rust tests, 31 optional runtime checks on each prepared pack, Cargo formatting, Clippy and Git diff checks. The usual launch scripts still use Python.

#### Rust hero selection and body delivery

- Connected original hero-pick and screen messages to body creation, controller possession, old-body retirement and the owner hero-select frame in the solo Practice Range worker. Creation uses the shared hero and health data and the account's equipped skin and golden weapon at handoff.
- Kept create, possession and retirement in dependency order. A frame can carry adjacent steps when they fit. A split waits for the required ACK. Lost records retry with the same generation and an old body's ID stays reserved for 30 seconds after destroy acknowledgement. Tested the loss of a complete hero selection batch, reopening the screen, and switching heroes on the encrypted worker.
- Ported controller and body statescript stream sequencing and retry state, plus the scoped expression evaluator. Exact checks cover owner full and cumulative delta frames and 11 expression results from target graphs. Live body and bot script execution still need the VM.
- Ported float32 shape casts and mesh sweeps. The local differential comparisons match Python on 9,108 shape casts and 3,530 mesh sweeps, including scaled props and tightened hit limits. Static-world dispatch and character movement are pending.
- Passed 201 public Rust tests, 32 optional runtime checks on each of two prepared packs, seven Python repeated-lifecycle tests, Cargo formatting, Clippy and Git diff checks. Original-client acceptance remains pending. The usual launch scripts still use Python.

#### Rust character movement and statescript foundations

- Ported command gap recovery, frame-scoped movement modifiers, steering, air control and gravity. Compared 7,296 velocity cases across all 38 prepared mover settings at a precision finer than the movement wire grid.
- Added ordered static-world queries, capsule-to-hull contacts, mesh contact clustering and the movement ground probe. All 95 local movement caches built successfully. Two prepared maps matched the Python reference on 2,800 ordered queries, 2,800 sweeps and 11,396 capsule-to-hull queries. The Practice Range probe matched in 1,536 cases, including 37 ground hits recovered by a sweep.
- Ported supported same-time graph initialization with a work budget and diagnostics for unported actions. Checked actual controller child order and bot startup states. Added the 598-class state layout table, 1,824 reference payload comparisons, and owner and observer full frames for supported initialized graphs.
- Passed 223 public Rust tests, 37 optional runtime checks on each of two prepared packs, and 124 Python movement, contact and script reference tests. Cargo formatting, Clippy and Git diff checks passed. The regular launch scripts still use Python. Per-body movement integration, timed and input-driven bot behavior, and original-client acceptance remain pending.

#### Rust player movement and timed statescript

- Connected ordinary player commands to the native solo Practice Range worker with the base graph bundle. The worker prepares static collision before taking a seat, applies body-specific settings, acknowledges fresh commands and preserves the command clock through hero switches. The encrypted loopback checks acknowledgements, the 96-frame input lead bound and the existing ordered body retries.
- Added per-body contact caches, capsule fit checks, bounded contact projection, stance, jump, landing and spring state. The Python reference matched 8,968 serialized movement records across all 38 prepared mover profiles on Practice Range, plus the wall and ceiling fixture and 2,304 per-proxy movement steps on two maps.
- Advanced supported graphs on a bounded timer queue and added scoped game-message entries and owner and observer cumulative frames. The enriched pack verified the two timer boundaries for a training bot's firing eligibility. Actual bot firing, weapon and ability input, and body script delivery in matches still require their runtime behavior.
- Passed 238 public Rust tests, 39 applicable runtime checks on each of two prepared packs, and 154 Python reference tests. Cargo formatting, Clippy, documentation links and Git diff checks passed. Owner corrections, sustained worker load measurements and original-client acceptance are pending. The regular launch scripts still use Python.

#### Rust input scheduling and login content

- Split command catch-up across worker turns with a shared resumable pose iterator, round-robin peers and a bounded queue. The worker allows 16 work units per peer and 64 per turn. Commands advance the acknowledgement after simulation. Queued hero switches and lease boundaries retain order with earlier poses. Tests cover a 64-pose gap, five busy peers and encrypted recovery under duplicate datagrams.
- Added a 64-pose owner correction history with a creation acknowledgement gate and body generation checks. The correction frame advances after its selected datagram is sent. Exact wire tests cover three input history encodings and the replay acknowledgement window. Owner correction delivery still awaits hero movement scripts.
- Added shared command-to-logical-button mapping, press buffering and timed releases in statescript. The prepared Soldier: 76 Sprint graph matched the reference button-entry behavior. Ability start and stop conditions, variable stacks and weapon input remain open.
- Added dated Arcade cards, priority pass pools and career mode rules to the verified Rust login subset. Matched 720 Arcade and pass messages across 180 profile and date combinations, and three mode-rules messages against the Python wire data. Competitive event cards come from the same Arcade state as the menu.
- Passed 252 public Rust tests, 43 applicable runtime checks on each of two prepared packs, and 155 Python reference tests. Cargo formatting, Clippy, documentation links and Git diff checks passed. Sustained worker load and original-client acceptance remain pending. The regular launch scripts still use Python.

#### Experimental Rust lobby and Practice Range server

- Added a loopback server entry point for local Battle.net, encrypted lobby and UDP Practice Range. It reuses existing account storage, tracks the active login and match seat, and stops partial handshakes and active peers on shutdown. An encrypted socket test reads a saved full-unlock profile and joins the map using the lobby's UDP handoff.
- Added main-menu hero resolution, celebration content, and General and Match channel transfer to the native lobby. Celebration payloads match the Python reference across three prepared profiles. Celebration rewards and broader social and matchmaking behavior remain pending.
- Ported variable stack polling and the supported ability activation and cancellation paths. The isolated Soldier: 76 Sprint graph matches the Python reference for the HUD variable and raw movement modifier across a 12-frame input trace.
- Connected the body script stream and movement corrections to the worker's send path for eligible body components. Tests cover exact retries, delivery gates, body replacement and state after a failed send. The base world has no eligible live hero component yet. The enriched world still requires more bot graph support.
- Passed 260 public Rust tests, 48 prepared base runtime checks, 47 enriched runtime checks and 90 focused Python tests. Cargo formatting, Clippy, an isolated CLI startup and shutdown, documentation links and Git diff checks passed. The regular launch scripts still use Python. Original-client acceptance remains pending.

#### Rust player body preparation and hitscan

- Added the prepared player body factory with authored body, weapon manager and equipped weapon roots. The Soldier: 76 startup matches all 19 reference instance IDs, graphs and parent states. The factory checks readiness once per Practice Range asset set and creates fresh runtime state per body.
- Ported supported animation, effect and scoped message states and owner stack bindings. The initial owner tree carries the effective values. The next frame introduces the bindings. Three independent Python wire vectors and an encrypted fixture-body replay verify the baseline, exact retransmission and cumulative recovery.
- Reused the static ray and capsule kernels for direct hits, with click aim, bounded target rewind, critical hits and falloff. A prepared Practice Range test matches the Python geometry and damage through the existing health pools. The four combat notice vectors match the reference bytes.
- Passed 267 public Rust tests, 53 prepared base checks, 50 enriched checks and 156 focused Python tests. Cargo formatting, Clippy, documentation links and Git diff checks passed. The stock body still requires native graph behavior, sparse input catch-up and weapon volley scheduling. Original-client gameplay and presentation remain pending.

#### Rust weapon timing and bot combat delivery

- Added shared volley scheduling, timed variable outputs and variable watches. The authored Soldier: 76 rifle matches eight reference shot times, held-fire ammo, release, the refill and animation-completion frames, and firing after reload.
- Seeded a newly introduced owner volley with its pre-shot ammo. After acknowledgement, the stream sends live ammo. Wire regressions cover a lost stop and lost activation, and preserve the reference observer values.
- Resumed long unposed command gaps within the worker input budget. A 400-frame gap runs the same 258 retained script frames as Python, with input release, bounded timer work and no synthetic hits during the gap.
- Routed posed direct shots through the prepared Practice Range worker. The encrypted fixture verifies a dropped hit's retry, terminal pool delivery, elimination notice and a fresh bot after respawn. A newer full pool update supersedes the old packet, and the old bot retires before the new entity becomes visible.
- Passed 272 public Rust tests, 56 base runtime checks, 52 enriched runtime checks and 172 focused Python tests. Cargo formatting, Clippy, documentation links and Git diff checks passed. The full stock Soldier body remains gated on unsupported graph behavior. Projectiles, bot weapon scripts and original-client acceptance remain pending.

#### Rust Helix cooldown and projectile prediction

- Added the supported ability cooldown, priority push, state query and branch paths. The authored Helix replay matches the reference shot times, six-second cooldown and damage configuration. Guarded human body queries and typed ChaseVar, Watch and Ability payloads match their reference cases.
- Derived Soldier's three predicted projectile definitions from the authored graph. Added scoped lease allocation, confirmation after an individually delivered owner report, and release after confirmed creation. The observer launch and owner confirmation and explosion records match Python bytes.
- Added shared swept projectile flight with wall and capsule collision, direct damage and capsule-distance splash. The projectile modules still need live-worker integration.
- Set Doomfist as the next hero after Soldier: 76 and the Practice Range training bots pass their acceptance checks.
- Passed 284 public Rust tests, 58 base runtime checks, 54 enriched runtime checks and 172 focused Python tests. Cargo formatting, Clippy, documentation links and Git diff checks passed. Stock owner startup still reports unsupported graph queries. Original-client gameplay acceptance remains pending.

#### Rust gallery equip and match appearance

- Processed retail gallery equip requests after login. The native lobby saves the loadout before confirming it and refreshes the local player card when an icon changes.
- Applied new skin and weapon themes to the reserved match. Equipping a different theme for the active hero uses the existing ordered body replacement. Other heroes retain the change for their next selection.
- Passed 84 focused Python reference tests and two prepared-base encrypted Rust loopback regressions covering gallery confirmation, persistence, a subsequent map load and the new skin theme on the replacement entity. The preceding original-client Rust session reached Practice Range, where the user reported missing weapons, unusable abilities and failed skin selection. The gallery repair awaits a client retest. The stock Soldier owner script still needs additional graph behavior.

#### Rust Soldier owner script startup

- Added current and maximum health queries backed by the existing owner variables, with a regression for the authored Soldier full-health branch. Added bounded random expressions and verified base presentation-state lifecycles. The deterministic random sequence remains a local implementation.
- Activated seven authored Soldier client-subgraph controllers on the server and left their client-only branches to the client. These include rifle and weapon manager states. Logged the first preflight failure once per prepared asset set.
- Compared the same 19-instance stock startup state with the Rust and Python encoders. The owner and observer frames matched byte for byte in this offline comparison.
- Validated authored mode tables in the external base bundle. Together with the VM changes they reduced the stock preflight from 35 diagnostics to 22. The first remaining query is the entity relation at graph `0033`, node `51`. The stock Soldier body still stops before attaching its weapon. No new original-client session was run.
- Passed 287 public Rust tests, 58 base-runtime checks and 54 enriched-runtime checks. Cargo formatting, Clippy, documentation links and Git diff checks passed.

#### Practice Range patrol collision

- Reproduced two elevated patrols crossing solid geometry in build 104319. Python and Rust now move patrolling bots through the same standing capsule and static map contact solver used by player movement, with the same map barrier masks.
- Kept the authored spawns, hint order and destinations. Blocked legs can use a clear authored hint or a short lateral detour that passes capsule clearance and floor support checks. This is a local reconstruction of patrol navigation. The original navigation implementation remains unverified.
- Prepared the detours outside the live tick loop and retained the bot's contact state in Python stored-state replay. Recorded allied pass-through and opposing-body blocking in the roadmap. Actor-to-actor movement collision remains open.
- Verified both elevated patrols over the real 104319 map, including the enemy bot's return leg. In an isolated optimized Rust run of 1,900 patrol updates, the patrol step took 47 µs at p95 and 143 µs at maximum. This measurement covers patrol updates only. Client acceptance is pending.
- Passed 288 public Rust tests, 58 base-runtime checks, 54 enriched-runtime checks and 148 focused Python tests. The Python run skipped one King's Row check because the base pack lacks that map's cache. Cargo formatting, Clippy, Ruff, relative documentation links and Git diff checks passed. The optimized Rust server started and stopped on isolated ports.

#### Rust development policy

- Moved new implementation and fixes to Rust. Froze the Python server as the transition entry point and behavioral reference. The cutover criteria now require broader verified Rust coverage, original-client checks for the replacement workflows, and a server-only deployment smoke test.

#### Rust owner relationships and startup states

- Bound the stock human owner VM to the authenticated controller and the fresh body allocated at each switch. Added distinct queries for the owning controller, current possessor and possessed body. Regressions verify typed operands, body replacement, and the authored Soldier startup and HUD lookups.
- Ported the input bridge's explicit activation and abort behavior. It forwards repeated inputs while active and consumes inactive inputs without starting the bridge.
- Enabled the normal graph lifecycle and variable synchronization for the verified networked ForCode state. The Soldier regression covers all six active states, their empty state payloads, and the linked body and weapon-manager states. Native game-code consumers and the server-only form remain open.
- Verified that two config classes share the retail native evaluator that returns a typed false, and added their exact operand-free forms.
- Reduced the stock Soldier preflight from 22 diagnostics to 13 in the same prepared base runtime. The first remaining blocker is the delayed conditional state at graph `0033`, node `66`. Stock weapon attachment and original-client acceptance remain pending.
- Passed 292 public Rust tests, 58 base-runtime checks and 54 enriched-runtime checks. Cargo formatting, Clippy, documentation links and Git diff checks passed.

#### Rust delayed branches and typed health queries

- Added the delayed conditional state's initial override, interruptible per-branch timers, and subgraph teardown to the shared VM. Its branch flags match the build 104319 payload vectors, and only the owner receives pending timers.
- Resolved authored health, armor and shield queries from the body pool catalog and the existing owner health totals. Current values are returned when the pool distribution is determined by those totals. Named and dynamic pools, and damaged mixed pools without per-type values, still require native state.
- Enabled the graph lifecycle and verified empty payload for the authored tips state. The native tips registry and original-client presentation remain open.
- Reduced stock Soldier startup diagnostics from 13 to 11 in the same base runtime. The first blocker is now the dynamic health pool state at graph `0033`, node `1`. Stock weapon attachment and original-client acceptance remain pending.
- Passed 296 public Rust tests, 58 base-runtime checks and 54 enriched-runtime checks. The codec comparison matched 1,833 owner and observer samples. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.

#### Rust body health pool lifecycle

- Added the authored base health pool shape to the shared VM. It registers capacity and initial current health separately, publishes remaining health before the end plug, and retires the pool when its state or instance ends. The initial and live output timing follows an inference from the authored graphs. Named pools, other flag combinations and capacity changes after activation still need validation.
- Made the same `Vitals` data drive health queries, damage, healing and component 51. Player and bot updates share the reliable delivery path, including creation acknowledgement, stale revision rejection and slot clears retained in replacement updates.
- The stock Soldier graph creates 200/200 health. An injected offline hit updates its health output and owner variables to 125/200. The stock startup now has 10 diagnostics, down from 11. The first is the entity filter at graph `004B`, node `21`. Weapon attachment, player damage in a live match and original-client acceptance remain pending.
- Passed 302 public Rust tests, 59 base-runtime checks and 55 enriched-runtime checks. The shared graph reproduces the reference armour and shield components for Reinhardt and Zarya, and the 32-hero initial component corpus remains byte-identical to the reference. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.

#### Rust authored target tags and entity filters

- Added optional import of target tags from local build 104319 entity definitions in the Rust runtime preparation command. The manifest hashes the resulting graph bundle. Older bundles without tag records still load and report unknown tag data.
- Shared the owner's tag facts between direct tag queries and filters. The supported filter form applies any, all and exclusion tag predicates, and can use an exclusion mask when the world provides authoritative flags.
- Verified the authored tag branches at graph `004B`, node `21` and graph `0043`, node `45` with the tagged Practice Range runtime. Stock Soldier startup reports 8 diagnostics, down from 10. The first remaining condition at `004B`, node `20` needs the entity's runtime exclusion flags. Tag mutations, instance overrides, weapon attachment and original-client acceptance remain open.
- Passed 307 public Rust tests, 59 tagged base-runtime checks and 55 tagged enriched-runtime checks. The earlier untagged bundle also passed the owner startup regression. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.

#### Rust owner startup facts and message tables

- Shared the body creation mask with the entity filter query. The same 104319 weapon-manager graph now starts seven additional subgraphs. A frozen reference replay with the corresponding mask matches the order and parents of all 26 instances. Runtime changes to the original server's exclusion flags still need verification.
- Read the initial on-fire flag from the current possessor's component 64 baseline. Resolved the authenticated human body's absent AI relation as an invalid entity ID. The original 104319 graph and reference data establish the operand shapes. Later client disassembly corroborates the typed absence results. Fire score, decay and active AI relations remain open.
- Added ordered VM maps for authored local message tables and their typed key lookup. The 20-entry table in the Soldier startup graph is local to the VM. Map wire encoding still needs verification.
- Used the inherited state lifecycle and empty payload for the verified zero-valued state on the human owner branch. Other values remain unverified.
- Reduced tagged 104319 Soldier startup diagnostics from 8 to 5. The first remaining operation is `STU_22926252` at `08B6`, node `11`. Stock weapon attachment and original-client acceptance remain pending.
- Passed 308 public Rust tests, 59 tagged base-runtime checks and 55 tagged enriched-runtime checks. The untagged bundle also passed the owner startup regression. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.

#### Rust target tag state lifetimes

- Applied the single literal target-tag grant and suppression states from build 104319 to the owner body's shared tag lookup. State end and instance teardown release the effect. Overlapping supported effects preserve the authored base tags, and body replacement starts with fresh effects. A grant during an active suppression and changes to a foreign entity remain unverified.
- Verified the stock Soldier graph grants tag `0422` at `01C7`, node `96` with or without a base tag list. Its state uses the inherited empty payload. The tagged 104319 preflight drops from 5 diagnostics to 4. The first remains `STU_22926252` at `08B6`, node `11`. Stock weapon attachment and original-client acceptance remain pending.
- Clarified that the Python server stays frozen and is retired when Rust exceeds its verified coverage and passes cutover acceptance.
- Passed 311 public Rust tests, 59 tagged base-runtime checks and 55 tagged enriched-runtime checks. The untagged bundle also passed the owner startup regression. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.

#### Rust script instance exit and owner startup transitions

- Queued the operand-free instance exit after earlier same-time events. The normal action output still runs, and the instance teardown cancels later work and releases its descendants. A child exit completes the still-linked parent state when the authored flag permits it. Dynamic targets, named exits and active descendant ordering remain unverified in build 104319.
- Completed the direct-owner AI state through the same-time finish queue when the world confirms no AI control entity. Added the typed zero asset returned by the fieldless resource lookup. The 104319 graph shapes match the local archive references, and later client inspection corroborates the execution paths.
- The tagged Soldier startup retires instances 9 and 25 while retaining the allocated ID 26. Its preflight goes from 4 diagnostics to 1, the movement-record sampler at `01C7`, node `7`. Stock weapon attachment and original-client acceptance remain pending.
- Passed 314 public Rust tests, 59 tagged base-runtime checks and 55 tagged enriched-runtime checks. The untagged bundle also passed the owner startup regression. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.

#### Rust owner movement sampling

- Started the authored movement sampler from the same mover used for the selected body's spawn, then fed it actual posed input and collision results. Unposed script time retains the latest movement record.
- Reused the 104319 owner flags codec and updated only changed authored outputs. The regular mover provides stance, velocity, throttle, ground contact and jump facts. Scripted movement, foreign targets and linked sampler events still need support.
- Used the embedded standing record at fresh owner construction based on the later client's constructor. The exact 104319 startup context still needs original-client evidence. Tagged Soldier startup reaches the crouch effect action at `01C7`, node `74`. Stock weapon attachment and client acceptance remain pending.
- Passed 317 public Rust tests, 59 tagged base-runtime checks and 55 tagged enriched-runtime checks. The untagged bundle also passed the owner startup regression. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.

#### Rust owner projectile flight

- Routed authored predicted straight projectiles from the same posed shot stream as hitscan. Body creation advertises the authored lease blocks, and the worker sweeps each flight against the current opposing bot capsules and map geometry. Direct and splash impacts use the same health, notice and respawn logic as hitscan.
- Used the exact owner report carrying the matching volley state to confirm projectile aliases. Confirmations, including the no-report fallback, wait for the acknowledged body and possession. Explosion release waits for the projectile create acknowledgement. Body replacement stops the old flight and reliably retires its aliases without an explosion.
- The encrypted Practice Range fixture covers Helix's 40 direct and 80 splash damage, an unaffected ally in the flight path, duplicate input and lost confirmation recovery. The stock Soldier body still stops at the authored effect action at `01C7`, node `74`. Observer launch, scripted movement, healing fields and original-client presentation remain open.
- Passed 320 public Rust tests, 60 tagged base-runtime checks and 55 tagged enriched-runtime checks. The untagged bundle also passed the owner startup regression. Cargo formatting, Clippy, documentation links, Git diff checks and isolated release server startup and shutdown passed.
