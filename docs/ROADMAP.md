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

- [ ] Verify Torbjorn bidirectional player/five-NPC combat and every ability.
  Direct projectile falloff and explicit critical multipliers now reach actual
  health damage in server tests. Authored rivet gravity now uses a documented
  fixed-step ballistic reconstruction. Ten-pellet ring launches now cause actual
  per-pellet damage/ammo use with explicit reconstructed spread; native phase/
  jitter/flight parity remain open. Hammer55 damage now uses an
  authored-duration/threshold reconstruction with hit/miss/LOS/team/cancel tests.
  Overload grants100 temporary armour with expiry/death cleanup and spam-proof
  cooldown evidence. Rejected Ability input now retains its original timestamp
  rather than refreshing indefinitely. Actual Overload early-release expiry,
  near-expiry0.15s buffering,held-until-ready and fresh-after-ready inputs pass;
  the previous method reproduced an automatic recast9.856s after release.
  The private shared runtime also reproduces and fixes canceled notification
  markers persisting through state reentry;825script checks pass. Cleanup precedes
  arbitration-link release under an explicit server reconstruction. Repeated
  hammer background refill/rivet reselect/fire has a dedicated actual-input guard.
  All122selected Torbjorn/VM methods pass after that shared lifecycle fix.
  Actual30percent movement increase,0.42/0.56s fire intervals
  and1.4s reload under Overload restore ordinary values after expiry. Actual18-round
  clip exhaustion/automatic reload,manual refill/fire readiness,holster cancellation
  and2s hammer-equipped background reload pass. A native-backed first-dispatch
  fix permits full secondary volleys from1round against player/all five NPCs;
  broader resource/interrupt/protection semantics remain open.
  A prepared-data
  turret reconstructs persistent build/fire/repair/ownership/destruction with
  player/five-NPC damage and5/10second cooldown evidence. Native firing/observer
  effects and placement/scoring policies remain open.
  Primary030E now runs16FC's0.5s tag states on actual player/five-NPC victims;
  an explicit per-owner priority host redirects real turret damage and falls
  back after expiry. Native tag sharing/acquisition/FX remain open. Hammer
  repair follows the native owner-only679 filter and its separate sphere;
  another allied turret stays unchanged. Overload now selects its authored
  earlier0.30 hammer threshold, with actual damage/repair timing checks.
  Its authored0.2s contact interval now accepts late/moving targets and aim
  changes,with one contact per target per swing,release commitment and scoped
  interruption cleanup.Timing is frozen at swing start across Overload expiry.
  Actual interact dismantle,combat redeploy denial/recovery,respawn binding and
  same-world-tick lethal-input cooldown checks pass under an explicit owned-actor
  control host; strict native namespace/kill services remain unaccepted.
  Molten Core now hosts actual16DD/16DE area/status graphs with reconstructed
  contact and periodic damage, including player/five-NPC hits, armour-only bonus,
  overlap suppression, lifetime and cleanup evidence. Actual ultimate release/
  repress keeps a ten-globule budget,Q edges cannot extend timed end,and death
  denies future launches while a landed pool continues damage. Ultimate charge
  generation now uses actual0B8B's5point/second timer and damage012 event:
  primary/secondary/melee/hammer and turret loss charge the owner;ultimate tags,
  deployable/repair/friendly/missed/incoming damage do not. Cost2142,multipliers,
  caps,permission/disable gates and automatic-respawn retention have evidence.
  Native pre-round/game-state gating,special pool deductions and full charge
  tag taxonomy remain open under the explicit permissive host.
  Surfaces/bounce, cross-owner arbitration, cross-hero protections and
  original-client acceptance remain open. See [Torbjorn combat](TORBJORN_COMBAT.txt).
  Actual five-NPC fixtures cover four outgoing attacks, basic incoming identity/
  pool/death semantics, a manually aimed Heavy Assault volley and corpse retention.
  Reused NPC weapon mounting/array selection now allows actual Trooper/Sniper
  primary input to damage Torbjorn armour15/95, with allied rejection tested.
  Manual aim now uses the target's combat capsule rather than generic eye height.
  Flashbang input has Torbjorn-specific action/movement denial/recovery and
  hammer/ultimate/Overload/deployable checks under explicit reconstructed policy;
  broader controls, immunity/cleansing and original-client effects remain open.
  Complete NPC attack execution/AI, conditional difficulty pools and protection/
  ability interactions remain open; these fixtures use permissive VM and adapters.
  Parent-authored five-NPC movers now supply distinct height/radius/speed for
  revalidation. Assassin eye-height misses are corrected by body-centre aim;
  hammer boundary and floor-backed open/wall controls pass for each body.
  Selected Wraith/Fade/Cryo interactions now use live authored phase stacks:
  ghost pass-through versus solid immunity,damage/status/charge denial,turret/
  burn/mark handling,end recovery and reverse melee checks.Phase engine tags,
  full AI targeting/filter policy and remaining protections are still open.
  Live Genji/Sigma projectile gates now use authored boxes and explicit known
  tags for actual rivets,full secondary,Molten and turret hitscan. Tests cover
  reflected ownership/teams,five-NPC return hits,new-owner pool burns,expiry,
  nearer walls/bodies,full pitch and nonexplosive prediction retirement.
  Reflection preserves remaining flight/gravity/landing metadata and consumes
  the retained interval after the gate. Actual2931turret throws remain excluded
  by145 and keep their owner. DVa Matrix,Genji melee blocking and complete
  protective interactions remain open;this does not close the checklist.
  Follow-up restores Genji reflected-damage charge through actual0B8B,including
  actual-loss/overkill/cap/modifier/gate tests and retained Molten ultimate tags.
  Sigma absorption now feeds21312and hosts the authored self shield request:
  60percent,400cap,3s grace,5/s decay,nonrecoverable extra pool,death/replacement
  cleanup,idempotent requests and cooldown/recast checks. Native special-pool
  charge deductions remain open. Cached tag/host policy and view maps now join
  replay identity. Torbjorn's authored1.0/.7m standing/crouched launch/target
  origins and participating peers' view heights have low-cover/hit evidence.

- [ ] Verify the Rialto McCree PvE combat sandbox: manual five-enemy spawning,
  navigation, acquisition, attacks and specials, bidirectional combat, skills and
  ultimate, persistent corpses and requested console keys. Manual NPC lifetimes,
  an external base-pool catalog and authored weapon components are implemented.
  Three of five enemies damage McCree in an offline fixture; controls and
  original-client acceptance remain open. McCree melee has player/NPC static-wall
  checks; see [McCree combat](MCCREE_COMBAT.txt) for the partial attack frontier
  and open Flashbang/Deadeye requirements. See [PvE combat sandbox](PVE_COMBAT_SANDBOX.txt).
  Resource-backed Flashbang control has offline interruption/recovery/lifetime
  checks; native immunity/presentation and complete hero acceptance remain open.
  Native input joins now preserve Deadeye's six-second timing and secondary
  cancellation; target acquisition/charging/damage and full ultimate acceptance
  remain open. See [state-input joins](STATE_INPUT_JOIN.txt).

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

- Torbjorn private lane follow-up: no-decay239B normalized spread and causal ramp
  snapshot imported;manual Enforcer13EB controller and Assassin1157 pin inputs
  execute. Actual pin/hammer recovery found and fixed equal-null stack topology
  notifications. Targeted19 cases pass,including preserved cadence,buff/ultimate recovery and snapshots. Full
  regression records remain external. Remaining NPC weapon-range/observer combinations,
  remaining controls/protections/modifiers and parent Fan spread review are
  still open;full hero/client acceptance remains unchecked.

- Torbjorn lane verified front-facing Genji melee blocks and real Matrix input
  on an authored possessed-mech fixture. Eight Matrix methods cover eligible
  and excluded projectiles, melee, facing, teams, cover, depletion and recovery.
  Physical barriers, projected bubbles and remaining interaction cases stay open.

- Torbjorn lane verified Mei spray piercing, slow, freeze, shield cover
  and thaw. Blizzard now has actual cast, area freeze and projectile
  defense evidence. Other recipients and native presentation remain open.

- Torbjorn lane verified Orisa Halt's two presses, pull speed and stopping
  point, cooldown, and frontal Defense Matrix and Kinetic Grasp interception.
  Deflect now has a server test for ownership, automatic wall detonation and
  enemy selection. Native target queries and reflected presentation stay open.

- Torbjorn lane verified temporary armor loss from four real Talon ranged
  attacks during Overload, alongside the Assassin pin regression. Sustained
  Trooper and Enforcer fire verifies expiry with temporary armor remaining.
  Sustained Trooper fire also crosses both armor pools into base health.

- Torbjorn lane verified four Talon weapons' 100 m reach independently of
  manual selection, plus Enforcer damage at near, midpoint and floor range.

- Torbjorn lane verified launched Molten Core pools against all five Talon
  bodies, including direct hits, range, team and wall filtering, and burn
  re-entry. Pools from separate casters settle and retire independently.
  Lethal overlap credits the killing caster and clears both burns.
  Direct globule tests distinguish body impact from a vertical wall impact.

- Torbjorn lane verified that thrown turrets reject allied, distant and
  covered Talon bodies and resume fire when an enemy returns to view.
  Actual fire from the four ranged NPCs also verifies destruction and
  redeploy cooldown recovery. An active turret can kill an Assassin and
  release its pinned owner.
  In a full-health exchange the Heavy Assault destroys the turret,
  while the turret defeats Trooper, Enforcer and Sniper in the test setup.
  Hammer repair can restore turret health during Heavy Assault fire.
  Flashbang cancels a pending repair. A fresh swing after recovery repairs
  the same turret.
  Repair contact also has tests on both sides of the capsule boundary
  and for cover clearing during or after the active swing.

- Torbjorn lane verified that marks on all five NPCs preserve the target
  tag through cover, regain turret priority when clear and expire normally.
  Two allied Torbjorns also retain independent turret priority and expiry
  when both mark the same target.
  Personal and projected Zarya barriers consume rivets without creating
  a mark. A fresh body hit after expiry restores turret priority.
  Both barriers absorb turret fire while an existing mark runs to expiry.
  Reflected rivets damage Torbjorn without creating a turret mark.
  Kinetic Grasp also denies the mark while converting the absorbed damage.
  Defense Matrix denies new marks and consumes turret shots while an
  existing mark expires on schedule.
  Owner death preserves turret priority from existing and in-flight rivets
  until their marks expire.
  Switching away from Torbjorn clears turret and mark state immediately.

- Torbjorn lane verified Ana grenade anti-heal against Soldier's Biotic
  Field, including skipped expiry ticks, plus Overload and turret repair.

- Torbjorn lane verified Ana Sleep Dart damage and control, rifle wake,
  shield cover and Molten firing recovery. The wake thresholds use a
  declared server reconstruction.

- Torbjorn lane verified Sombra Hack input restrictions, return-fire
  interruption and fresh-input recovery. The EMP body effect now passes
  multi-target, cover and shared silence lifetime checks. EMP shield drain,
  deployable effects and further controls stay open.

- Torbjorn lane verified landed Molten cover across Winston's shell in
  both directions and across Orisa's grounded mesh. Actual globule break
  exposes a target to the existing pool. Native surface and receiver
  policy and other shield placements remain open.

- Torbjorn lane verified that layered deployed meshes and world spheres
  keep their cover for a single Flashbang event when its front shield breaks.
  Native blast penetration and landed Molten across shields remain open.

- Torbjorn lane verified Sigma Experimental Barrier movement, damage,
  recall, break and delayed repair. Native collision, mixed area effects
  and original-client presentation remain open.

- Torbjorn lane verified Orisa's deployed barrier against rivets, pellets,
  turret fire, melee, Molten globules and Flashbang. The static host runs
  the authored pool and expiry graph. Native flight, filter and presentation
  parity remain open.

- Torbjorn lane verified Winston's Barrier Projector against rivets, pellets,
  turret fire, melee, Molten Core and Flashbang. The authored cast and timer
  drive a fixed server shell. Native collision filters, other physical
  barrier families and client replication remain open.

- Torbjorn lane verified Zarya personal and projected barriers with actual
  input, absorbed damage credited to the caster, and break and expiry.
  The server reconstructs target selection and spherical contact. Native
  target selection, replication and remaining protective combinations stay open.

- Torbjorn lane verified authored Reinhardt shield hits, break and recovery,
  melee contact, allied and wall ordering, and Flashbang splash protection.
  Other physical barriers, projected bubbles, native rebound and remaining
  interaction rows stay active.

- Torbjorn lane verified static Transcendence immunity, ally healing after
  NPC attack settlement, Ana antiheal expiry and Winston shell admission.
  Actual Sleep Dart interrupts all five manual Talon NPC attacks, including
  an active Assassin pin. Natural expiry and rivet damage both recover NPC
  input. Full static coverage and original-client verification remain open.

- Torbjorn lane added static Chain Hook and Steel Trap execution.
  The focused source run passed. Full discovery and remaining static
  interactions are open, with original-client acceptance tracked separately.
