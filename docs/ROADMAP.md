# Restoration roadmap

Mei now resumes held secondary after Cryo exit priority clears. Actual input
tests cover natural expiry, cancel and released press expiry. Frontal Genji
Deflect blocks admitted quick melee contacts under the reconstructed capsule
and authored angle model. The full static matrix remains open.

Mei world-clock checkpoint: existing body graph timers now advance through
older no-input windows using a captured world/command anchor. Future/late
windows cannot fast-forward cooldowns or replay attacks/movement; already
admitted Blizzard release and defense/projectile chronology are tested. The
phase-stuck counterexample is repaired under this explicit hosting policy.
Thirteen clock cases pass;833 scripts/147 shared cases pass. Frozen-source226
Mei tests PASS865.993s and13 snapshots PASS683.390s. Immediate held secondary
still retains a rejected
HELD latch under Cryo exit priority; the full static/client gates remain open.

Mei phase/view checkpoint: resource-bound Wraith/Fade input, protection stacks,
damage/status/projectile/Blizzard and cooldown cases have executed evidence.
Mei1.3/.91 and McCree1.425/.9975 FirstPerson heights drive actual stance/cover
shots and target history. A no-command phase probe remains stuck after250
world ticks, so complete world/input-clock lifecycle is not accepted. Full
213 Mei tests PASS650.286s;13 snapshots PASS567.883s. The full-scope matrix
and real goal remain open despite these current-source passes.

Mei current reconstruction now includes Enforcer25-pellet resource rings and
actual teammate-spray Assassin pin interruption. Ice Wall rejects off-centre
static obstruction, unsupported footprints and oriented column overlaps with
explicit terrain/partial-wall policy. Reviewed notification lifetime/cleanup
ordering fixes preserve re-entry and fresh weapon recovery after Cryo. Focused
tests pass; frozen-source200 Mei PASS449.989s and13 snapshots PASS432.868s.
Complete phase/
defense/input/native-fidelity and original-client gates remain open.

Mei manual incoming checkpoint: Enforcer13EB/Assassin1157 inputs reach combat;
stale pin cleanup and Cryo/source-interruption cases pass. Controller/contact/
clock/recovery remain reconstructed. Enforcer25-pellet count/spread is unhosted.
Directional mover17/18/19 preserves raw input bytes; related tests pass.
Full static and original-client gates stay open; no milestone is checked off.

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
  lifetime policy is implemented; actual NPC creation, controls and original-client
  acceptance remain open. See [PvE combat sandbox](PVE_COMBAT_SANDBOX.txt).

A checked item records evidence for that specific scope. Its parent remains open while other requirements are outstanding.

- [ ] **Research and replay.** Rebuild required data from documented sources, resolve protocol and graph semantics, and reproduce complete sessions from recorded inputs and events.
  - [x] Pin the target build and document upstream sources and data provenance.
  - [x] Replay controlled encrypted server sessions and compare wire output and registered server state.
- [ ] **Login, profiles and social features.** Complete authentication, account and profile state, progression, cosmetics, parties and other social features supported by the target build.
- [ ] **Lobby and match lifecycle.** Verify queues, team assignment, game setup, hero selection, match handoff, joining, leaving, reconnecting and returning to the lobby.
- [ ] **Network synchronization.** Verify authoritative movement, prediction and correction, input timing, and state consistency for players and observers under delay, loss, reordering and repeated connections.
- [ ] **Heroes and abilities.** Cover weapons, reloads, aiming, movement abilities, passives, cooldowns, resources, ultimates, transformations and deployables with shared mechanics.
  - [ ] Restore Mei's complete player/five-NPC combat and original-client presentation. Partial weapon/reload, frost/control, Cryo, reconstructed Ice Wall/Blizzard/Zarya shields and actual NPC target/route/three-weapon checks are recorded in [Mei combat lane](MEI_COMBAT.txt). Complete status/defense/ultimate-charge policies, wall/Blizzard/shield geometry, remaining NPC attacks/specials and original-client acceptance remain open.
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

Mei/Genji checkpoint: scoped authored-window icicle reflection has seven
real-input tests, including spray exclusion, front/rear/team/static ordering
and native expiration/cooldown restart. Blizzard/other defense paths remain
open; full Mei static acceptance is not achieved. See MEI_COMBAT.txt.

Mei Blizzard reflection checkpoint:13 reflection and70 related checks pass.
Re-evaluate NPC geometry using independent resource-backed Talon mover data;
prior default-mover fixtures do not prove complete native NPC geometry.
D.Va matrix graph was located in missing mech data; absorption remains open.

Mei/D.Va Matrix: explicit possessed0407 fixture now runs native02CC input/fuel
with scoped reconstructed foreign ruleset reads and projectile absorption.
Full mech possession and defense geometry/controller acceptance remain open.
Updated tests and preparation command are recorded in MEI_COMBAT.txt.

Mei charge checkpoint: effective combat/Cryo accounting and death/respawn
retention have executed evidence; passive5/s and credit1:1 remain explicitly
reconstructed. Resource-backed generation/spawn and freeze settings are now
identified but still require complete hosting and acceptance. Full goal OPEN.

Mei settings checkpoint: versioned generation/spawn-ready inputs and the
ultimate-disable input gate have executed evidence. The freeze minimum menu
value.3 must not replace0B77's.2 fallback without resolving external #392 and
effect-instance/source lifetime. Complete static matrix remains OPEN.

Mei per-source frost now has independent progress/expiry and explicit stacking
reconstruction, including declared proxy overrides and stored-state coverage.
Frozen Deflect no longer resumes after thaw and keeps its native cooldown.
Native effect merging/proxy binding and the remaining complete static matrix
are still open; see MEI_COMBAT.txt for versioned execution boundaries.

Mei/Sigma checkpoint: actual Barrier and Grasp inputs now drive scoped
static combat reconstruction. Eight private-worktree checks cover the
verified C015 mesh, interception, breaking hit, recall, temporary shield
decay, airborne Blizzard absorption and passive state capture. Incoming
Accretion control and full cross-hero acceptance remain open.

Mei/Sigma incoming checkpoint: private-worktree tests exercise
Accretion damage, stun and impulse, Hypersphere ricochet and expiry,
Cryo and Ice Wall protection, and Barrier redeployment after cooldown.
Flux selection and recipient delivery have separate candidate evidence.
Flux damage and movement phases remain open.

Mei/Sigma Flux checkpoint: actual charge, confirmation, recipient
selection, lift, landing and cleanup have private-worktree execution.
Cryo, caster interruption, terrain, Ice Wall and projected barrier have
direct counterplay evidence. The full-current guarded runs are pending.

Mei/Symmetra Photon Barrier checkpoint
Actual placement, lifetime, interception and passive capture passed
four worktree checks. The preceding guarded Mei discovery passed 412
checks. Its sole skip, Practice Range Ice Wall placement, passed
separately with the full 104319 collision cache.

Mei/non-unit body size checkpoint
Focused combat checks compare unit and doubled body sizes for Deflect,
Matrix and Mei quick melee. The Matrix check uses an explicit mech
size fixture. Native controller-to-mech scale propagation remains open.

Mei/Symmetra Photon Projector checkpoint
Actual orb and beam tests now cover damage, sustained charge, release,
Cryo cover, Ice Wall, projected-barrier ammo refill and interruption
by Mei freeze. The Photon interruption hook now accepts calls from the
shared NPC frost and stun paths without assuming a player script.

Mei/Symmetra Sentry checkpoint
The integrated static host passes the actual Cryo, Ice Wall, icicle,
recharge and passive capture checks. The Photon Projector binding is
now scoped to Symmetra's capture fields.

Mei/Symmetra orb defense checkpoint
Actual orb tests cover direct and splash cover across five deployed
barriers, nested ice and projected shields, and Genji, D.Va and Sigma
projectile defenses in the static reconstruction.

Mei/Roadhog Chain Hook checkpoint
The static host passes actual Hook, Cryo, Ice Wall, cooldown, caster
freeze, five NPC body and passive capture checks. Contact and pull
motion are reconstructed.

Mei/Roadhog Take a Breather checkpoint
Actual E heals its owner and expires without commands. Actual icicle
critical damage is reduced by half during the channel. Spray freeze
stops healing and damage reduction, and held E cannot bypass cooldown.
Landed Blizzard also interrupts the active heal and restores its full
damage rate on subsequent ticks.

Mei/Roadhog weapon volleys checkpoint
Static Scrap Gun primary and Whole Hog resolve authored pellet
counts. Secondary dispatches the authored burst at natural expiry,
with actual body, Cryo and Ice Wall contacts tested before expiry.
Whole Hog applies authored force with a reconstructed capped impulse.
Actual Cryo, Ice Wall, spray freeze, landed Blizzard and commandless
movement have focused tests.

Roadhog volleys against deployed barriers
Actual primary, secondary slug and Whole Hog resolve enemy Photon,
Orisa, Winston, Reinhardt and Sigma barriers before Mei. An earlier
barrier receives the pellet and prevents damage and impulse behind it.
Friendly Photon passes all three. Contact priority is reconstructed.

Scrap Gun against active projectile defenses
The 104319 primary and slug carry 02F9 and omit 0145. Actual Genji
Deflect returns both to Roadhog and receives the hit notices. Actual
Defense Matrix and Kinetic Grasp intercept both before Mei. Grasp
records 165 for the checked full primary volley and 50 for the close
slug. The reflected child cone is reoriented by reconstructed aim
geometry. Whole Hog uses a separate hitscan path.

Whole Hog against active defenses
The 104319 0405 ray carries 0535 and omits 0145. Active Genji
Deflect returns it with Genji hit attribution and return-direction
force. Defense Matrix and Kinetic Grasp stop it before the body.
Earlier static, ice, projected or deployed cover keeps priority.
Distance, contact priority and the return-ray policy are reconstructed.
