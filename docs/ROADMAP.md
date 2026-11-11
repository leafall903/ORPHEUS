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

- [ ] Verify Ashe bidirectional player/five-NPC combat and all abilities in the
  original client. Coach Gun fixed-ring pellets have partial offline damage,
  armour, shield-pool and death coverage; spread geometry is reconstructed.
  Viper 2x critical damage is reconstructed; dynamite, B.O.B., control, full
  critical eligibility and client presentation remain open.
  Per-round reload, damage charge, cooldown rejection/expiry and ultimate
  resource gates have partial offline evidence; no complete static acceptance.
  Dynamite has reconstructed throw/fuse/blast/burn and eleven lifecycle checks;
  five actual NPC definitions pass outgoing weapon/skill and ally checks, with
  controlled Trooper/Heavy incoming fire plus actual Sniper100 damage and stop.
  Enforcer now has actual AI-root-driven25-projectile/50 close damage plus
  wall/stop/falloff checks. Assassin manual selection and authored target writer
  execute, but its unsupported entity filter clears the target without damage.
  The native filter bit/relation stage matches4572 original-helper executions;
  five original guard probes and25 source checks pass. Full filter stages and
  native category/status inputs are still open; this does not admit a target.
  Candidate-condition scoping/conversion additionally matches132 native cases;
  the actual condition observes the target's actual14695 Stack and retirement.
  Effect acquisition/full lifecycle and Assassin damage remain unaccepted.
  Native bit projection matches272 prefix executions; actual record categories
  now select the controller for the manual override. Full controller-to-body
  routing, dynamic statuses and filter admission remain open.
  Native member selection now proves independent target component47 namespaces;
  216 opcode/member comparisons verify the uint16 key fix. Player-controller000E
  has no initial graphs. Optional normal-base metadata enables an independent strict controller
  VM with reconstructed lifetime/clock and causal snapshot capture. Controller
  member reads now bind to that live controller VM with scoped foreign watches
  and reciprocal snapshot boundaries. Stop/NPC retirement removes only its own
  watches; host retirement removes all bindings. Controller wire/effect/health
  integration and full filter remain open.
  An unmounted native-order coordinator matches2620 return/service-order cases,
  and status10 precedence/fallback matches81 cases. Live engine-service inputs,
  nonempty script filters, callback registry and extension collections remain
  explicit adapters; these checks do not admit an Assassin target.
  Actual13C2 has no inline rules or key expression:192 native cases prove its
  enum2 callback result even with populated registries. The coordinator uses
  that path; generic keyed/nonempty rules stay unsupported. Component57's child
  predicate matches1060 native storage-input cases. Live attachment/status
  inputs, effect/health delivery and full Assassin admission remain open.
  With an explicit admitted controller, the manual Assassin pin now executes
  authored1157 contact25 and delayed100/s damage against the bound body.
  Release, wall, death and body replacement stop damage. Rate clock and body
  binding are stored causally. Manifest-verified clip508A supplies the
  reconstructed manual Anim31 release clock. A fresh request after release
  delivers a second contact even during the authored two-second wait. Native dynamic
  playback, pin control and1177 effect delivery remain open.
  NativeF525E0 maps151 component occurrences/30 unique types in14 lane definitions;
  Health51/Model4 and controller76/29/73/47 are identified. Definition absence
  does not establish native automatic/dynamic absence. Health predicate225-case
  epsilon/NaN evidence exists; live getter/pool mapping remains open.
  Parent43-row NPC mover reference matches all34 fields for five lane bodies;
  25 executed Viper upper/side capsule hit/miss/wall cases verify exact loaded
  rows and ammo spending.105 Ashe checks pass on the canonical lane data.
  Native presence1408-case evidence derives status10=false for a retained
  controller sample; future dynamic attachment and full filter remain open.
  The same105 checks also pass directly on the parent's43-row runtime with a
  matching670-graph/catalog base and regenerated additive metadata extension.
  Optional controller inputs now drive actual13C2/node10, with atomic/lifetime
  guards, live14695 watches and passive snapshots; default service remains
  unavailable.3718 native comparisons include1086 source-independent values.
  Repeated stop/start exposed stale CHECK/CHANGED flags after event cancellation;
  both end modes now clear them, matching520 native cases;855 script tests pass.
  Current boundary also passes108 Ashe,89 related and90 gameplay checks; final
  component47 consistency rejection passes26 focused checks after that full run.
  All13 Assassin checks now include actual1157 target536 resolution and strict
  health/effect failure boundaries.24 native factory cases/96 hooks verify the
  captured ModifyHealth class inherits generic client State behavior; server
  health ownership and timing/completion remain open.
  Bound NPC VMs now supply caller/owner/inherited source with lifecycle checks;
  actual1177 request construction reaches the unsupported submission boundary.
  Source core passes294+7+7 native comparisons. Ability buffering retains the
  original press timestamp; actual Coach/Dynamite null windows retain their
  released-input rejection, held-input and fresh-recast behavior. Native start
  fidelity, target queues, wire IDs, health ownership and delivery remain open.
  Current source passes112 Ashe,857 script,90 gameplay and28 focused checks.
  Input properties17/18/19 now affect acceleration, with120 native-prefix
  comparisons and next-frame/raw-byte restoration evidence. Five actual ADS
  direction cases verify01C7/node152's -0.25 speed modifier and release recovery;
  native movement/control scope remains open. Controller wire IDs/full-frame
  ownership still need coordination before queued1177 effect delivery.
  Latest113 Ashe/105 gameplay checks pass;56 mover/physics cases pass on the
  parent data and9 skipped map cases pass on canonical caches, with4 final
  ADS/modifier checks. No broader native or original-client acceptance implied.
  Controller reservations now come from the actual owner-tree builder: PvP1..4,
  Practice1..5, monotonic VM allocation afterwards, collision/rewind rejection and
  passive causal storage.32 pre-refactor fixture frames stay byte-identical.
  Full-frame composition,9935FBC8 linked-target payload and actual1177 delivery
  remain open; reserved IDs alone are not complete wire integration.
  Native9935FBC8 owner/full suffix passes1030 cases with actual bit/entity/float
  readers and declared linked-base/notification adapters.115 Ashe,26 focused and
  14 final guard cases pass;48-case storage/journal attempt passes46, with both
  errors corrected and verified separately. Runtime linked-target services and
  final frame composition remain open, as does ending-cleanup notification review.
  Ending cleanup now excludes the ending watcher and preserves live peers/reentry;
  exact Viper shell/shot accounting passes through partial/empty reload input.
  Current116 Ashe/858 script and the frozen two-player journal pass. Complete
  native watcher/stop/cadence behavior remains open. Immutable FirstPerson heights
  now feed shooter/target/history eyes: Ashe251C1.445/1.0115 and McCree02A3
  1.425/0.9975. Four pose cases and eight low-cover shot cases pass, with ammo and
  fallback checks. Animated camera/muzzle/skeletal hit regions remain open.
  Reviewed no-decay WeaponVolley spread now bakes its ramp and stores it causally.
  Actual Viper hip ramp6/ADS null ramp pass twelve command shots;252 native ramp
  and20 angle cases pass, including native integer conversion. Current120 Ashe,
  865 script,42 snapshot cases and the frozen two-player journal pass. Actual
  damage-ray dispersion/RNG caller identities remain open. Linked-target absent
  post-lookup suffix passes128 native cases; full runtime/writer remains open.
  Viper now applies its actual hip cone/ADS zero cone to real rays using native
  generated tables/math with declared server seed/mode reconstruction.125 Ashe,
  105 gameplay and the frozen journal pass;33 cases/198 rays include10 off-axis
  spread hits across all five NPCs. Queued shots now carry a fixed cone and seed
  through State reuse, verified with delayed real-shot consumption. An Ashe
  stored journal matches the ray, damage and ammo. Native State indices 7 and
  122 are verified at the constructor boundary and used in the queued seed.
  Native counter and instance-key probes support the existing mappings at their
  tested boundaries. Both authored Viper nodes select the native transform
  provider, with aspect 1.0 and mode zero on the tested dispatch path. The
  native seed bake uses the packed key-49 word and stores State +2A4.
  The live manager identifies factory 64E600 and a verified player body has
  word zero. Both factory branches clear the word, and the real publisher and
  reader compose in 16 fixture cases. Static Viper uses the zero initializer.
  The firing caller's native aim quantization now feeds the hip and ADS rays.
  A 662-case native probe also verifies the local-to-world origin transform
  and shot-record store with declared pose inputs. A separate read-only player
  snapshot selects the native component-28 origin path. Forty variations
  verify stance height, adjustments and scale, and two cases verify origin
  fallbacks. Another 272 cases exercise attached fallback transforms, socket
  selection and scale inheritance with declared records. Live animated pose,
  later mutations, allocator parity and full native Viper fidelity stay open.
  Native observers, automatic profile acquisition, effects/health and full AI
  remain open. Per-frame reevaluation is explicitly reconstructed.
  Native selection/filter services and Assassin incoming attack,
  full status/physics semantics and the complete B.O.B. scope remain open.
  Actual B.O.B. body/gun metadata now mount through a small external extension;
  12 gun/lifecycle tests and four actual-NPC interaction tests pass after actual
  charge-wall stops. Physical charge now uses actual charge/idle mover profiles;
  13 charge tests cover120 uppercut damage, five actual NPC launches, walls, misses,
  expiry and death/replacement. Native phase/contact/launch/control fidelity and
  broad cross-hero statuses remain open; this does not complete the static matrix.
  Coach Gun has eleven physical recoil/knockback checks including walls, landing,
  cooldown spam and five actual NPCs using their external native body movers.
  Force magnitude/aggregation and native resistance/control semantics remain
  reconstructed; native B.O.B. phase-graph mounting remains open.
  Separate actual AI components now expose paired body/root lookups and live
  variable watches; native registry semantics and full AI remain open. Actual
  Flashbang input pauses/resumes Ashe and B.O.B. gun/charge; the broad status
  and resistance matrix remains open.
  Ashe melee visible/wall checks pass for all five actual NPCs.
  See [Ashe combat lane](ASHE_COMBAT.txt).

- [ ] Verify the Rialto McCree PvE combat sandbox: manual five-enemy spawning,
  navigation, acquisition, attacks and specials, bidirectional combat, skills and
  ultimate, persistent corpses and requested console keys. Manual NPC lifetimes,
  an external base-pool catalog and companion console/API are implemented.
  Authored NPC weapon components are mounted; three of five enemies damage
  McCree in an offline fixture. Full enemy combat and browser/original-client
  acceptance remain open.
  McCree melee has player/NPC static-wall checks; see [McCree combat](MCCREE_COMBAT.txt)
  for the partial attack frontier and open Flashbang/Deadeye requirements.
  Resource-backed Flashbang control has offline interruption/recovery/lifetime
  checks; native immunity/presentation and complete hero acceptance remain open.
  Native input joins now preserve Deadeye's six-second timing and secondary
  cancellation; target acquisition/charging/damage and full ultimate acceptance
  remain open. See [state-input joins](STATE_INPUT_JOIN.txt).
  See [PvE combat sandbox](PVE_COMBAT_SANDBOX.txt).

A checked item records evidence for that specific scope. Its parent remains open while other requirements are outstanding.

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
