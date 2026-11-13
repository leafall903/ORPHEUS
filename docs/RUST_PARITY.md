# Python and Rust parity

The default `main` branch is the community-maintained Python implementation. The `rust` branch is the native developer preview. Use the Python implementation and target-build observations as evidence for behavior. Compare supported workflows and their verification level independently of the branches' commit counts.

The initial comparison uses Python `74f36cd` and Rust `be0d41d`. The local combined gameplay reference is pinned to Python `cc620be`. Junkenstein placement and candidate evidence uses that reference and the authorized 104319 map export. Update this checklist alongside new evidence and record the commit or fixture that supports each change.

Cassidy Combat Roll now bridges 01F0's authored velocity edits into the shared
collision-aware mover, with next-frame, once-only delta/cap application and
owner correction delivery. Comparison is pinned to local Python `e1128509`
(`ow174/game/mccree.py`, `tests/test_mccree_roll.py`) and separately prepared
104319 graphs/codecs. The Normal replay covers five directional cases,
refill/re-fire, roughly six metres on flat collision and spawn-wall contact.
The linked parameter states and two native-service actions retain Python
fallback behavior; their native effects and dynamic map rigid-body impulses
are not reconstructed. An isolated original-client Normal replay of 7b13651b
with fixture 74d6ff78 shows the Roll viewpoint/weapon transition, 6.146484375 m
stationary displacement, 5-to-6 refill, cooldown and subsequent 6-to-5 firing
with an active owner VM. Live directional/contact cases and unknown native
effects remain open, along with the full gameplay checklist.

Native Junkenstein result projection now includes the authored winning team
and elapsed encounter seconds in MECGameMode fields 7 and 9, delivered with
the reliable outcome action. The prepared gate-destruction replay checks
enemy team 1 and nonzero duration. Target-build native readers establish the
field mapping; original-client victory/defeat recap acceptance remains open.

An isolated original-client replay on 3e8b2216 now shows Defeat after gate
destruction with Cassidy alive at 225 health. Read-only inspection confirms
native winner 1 and duration 194.847, while the recap still displays time 0.0.
The defeat heading is verified; the recap time binding and boss-clear victory
remain open. The local test fixture is 77a62972 and its invulnerability/gate
overrides are excluded from production.

The reliable HUD replay also covers the final boss transition through 43,000
ticks. It fails at client-targeted announcement 1188:59 before the narrow
presentation-only handling and passes afterward. The original-client run
reaches the final boss objective without the first-Reaper crash, but the
Combat Roll owner path (01F0), boss-clear victory and full presentation matrix
remain unaccepted.

The user's visible gameplay gaps are tracked in the Junkenstein acceptance
table in [ROADMAP.md](ROADMAP.md#junkenstein-original-client-acceptance).
Rialto's existing Python hero mechanisms are comparison references, not
evidence that the Rust Junkenstein owner and AI paths work. In particular,
01F0 identifies Combat Roll; Flashbang remains a separate open item.

| Cutover workflow | Python reference | Rust evidence | Required to close |
| --- | --- | --- | --- |
| Authorized local startup and authentication | Windows launcher and local server use a prepared 104319 runtime. | The native CLI starts on loopback with a prepared base pack. Encrypted login, handoff and map loading pass headless checks. | Establish the supported original-client connection procedure, retain the runtime preflight and verify the tested release checkout with 104319. |
| Stock Soldier: 76 spawn, switch and rejoin | The authored body path is available with prepared data. Client playtests have reported incomplete presentation and skills. | Headless selection, skin replacement and fixture body delivery pass. The tagged 104319 pack attaches the authored Soldier body and weapon. Encrypted loopback checks the stock rifle, Helix and Biotic Field owner paths. | Attach the stock weapon and script in the target client and complete the repeated switch and rejoin checks. |
| Soldier weapon, Sprint, Helix and Biotic Field | Reference implementations and selected automated and playtest evidence. | Authored rifle, reload, Sprint and Helix timelines pass. Encrypted matches cover the stock rifle, Helix and Biotic Field healing and ground-entity delivery, including packet loss. | Verify stock input, HUD, ability presentation, target damage and healing through the original client. |
| Bots, movement and collision | Reference roster, patrol, damage and respawn are available for comparison. Python `cc620be` normalizes stance height before bounding the spring. | Room ECS, Lua patrol and encrypted fixture damage and respawn pass. Prepared movement uses real target geometry; all 43 combined movers match 10,148 Python movement records and 8,256 unconstrained velocity cases after porting the normalized spring bounds. | Record the upstairs route report, shared character collision and original-client movement, damage and respawn. |
| HUD, feedback and ultimate charge | Selected wire fixtures and an ultimate-charge model using community estimates. | Selected combat notification and controller records have fixtures. Native parity for ultimate charge, broader HUD and audio remains open. | Confirm the target rules and verify health, eliminations, kill feed, critical-hit feedback, audio and ultimate progression. |
| Junkenstein solo entrance and first spawn search | Python `cc620be` binds the authored castle gate, Arcade rules and local collision search. | All 14 cards initialize. Encrypted Revenge, Endless and three-player entry checks cover gate delivery, readiness and countdown retry. The Normal scene reaches phase two and returns 172 spawn candidates, with selected positions and a candidate digest checked against the Python reference. The first Zomnic pair now initializes and publishes at the selected candidate. | Complete enemy navigation, attacks, replication acceptance, encounter effects, waves and objectives, and verify the full original-client flow. |
| Non-playable Hero Gallery entries | Local 104319 catalog, authored preview scripts and native cosmetic-builder inspection. | Catalog includes 23 named NPCs; 14 mismatched previews receive exact resource correction and model/local-idle builder filtering. Policy, isolated adapter and original native iterator checks pass. | Verify actual selection, models, framing and animation after remaining static gaps are closed. |
| Multi-peer match and performance | Use prepared scenarios and documented reference behavior. | Two independent solo rooms can run on one worker in an encrypted workload. Prepared fault injection verifies surviving input, world delivery, hits, patrol and new-room admission after one room fails. Timing, packet gaps and bounded fault records are observable. | Verify same-room agreement on movement, damage, ownership and respawn. Compare release builds on the same workload. |

## Combined prepared-data comparison

The pending AI integration snapshot was also rehearsed separately: the schema
option and new code-observer fields coexist, and the Boss rejection test supplies
the optional gate binding. The Soldier fixture expects the implemented `ForCode`
kind for its four `STU_CD46AF93` states and retains its snapshot and wire checks.
The candidate passes 388 default and 391 inspector public checks, all 82 prepared
checks in each configuration, both Clippy configurations and runtime preflight.
The eight-file input patch SHA-256 is
`8d62a3dbceba0512d86b0c120157d36fbadaee9b856a4952198485d737885461`.
This rehearsal
does not import the other worktree's unfinished changes into this branch or
establish their original-client acceptance.

Boss creation can now resolve the five authored hero body/controller pairs
without adding them to the ordinary NPC catalog. Base capacity comes from the
exact encounter body binding, and Boss requests require the matching explicit
controller before room allocation. Prepared evidence checks all five profiles
and ten rejected controller requests. A synthetic fixture distinguishes the
encounter body's pools from a different playable-body cache. This follows
`create` in local Python `junkenstein_ai.py`, SHA-256
`38020736a872c215fcbff86964f38b8b453320221173b42f18d802d726254b10`, and
`authored_pools` in `pools.py`, SHA-256
`a6f6a2ddc6130ade46c539c78550218d33363c1d69fa6d67c033c09bdf98b5cc`.
Enemy body states now register, read and retire their real Vitals pools. Bodies
binding `0033` replace bootstrap pools with authored state-owned pools, while
ordinary `0C89` NPCs retain catalog-owned capacity. A mixed-pool lifecycle fixture
checks damage, remaining-value output, abort, reentry with fresh handles and
healing. Prepared probes check the five Bosses' exact current and maximum pools
without duplicate bootstrap capacity. The active-mode native also accepts the
authored null `resourceKey`, following local `driver.BodyWorld.native`, SHA-256
`7602219c38dbfd9caf09ec1197ef3ebc56745b98de67148888a8c0494e7f9893`.
Enemy body target tags now use the shared state-owned grant and suppression
logic, with reads available to the paired controller. Prepared probes clear
the tag diagnostic. The authored grants and their cleanup match
`EncounterTargetTag` in local Python `nodes.py`, SHA-256
`9e3e7b418a3b4f7cecb13a95f0e45314d2770c67f94925a73bbb620589f8f08f`.
Held-button conditions now read the live component input and respect
state-owned blocks. The fixture covers overlapping blocks, the listener's
ignore flag, abort and instance teardown. Behavior follows
`button_held`, `LogicalButton` and `DisableLogicalButton` in the pinned
`nodes.py` above. The component 92 query now reads the paired entity's metadata imported from
104319 `.003` exports. The prepared pack confirms presence on all eight enemy
controllers and absence on their bodies. The query follows Python
`scene.SceneWorld.native`, SHA-256
`05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`.
The On Fire query preserves the destination's initial value when the live enemy
controller has no stats. This follows the same Python scene adapter and
`expr.py` (SHA-256
`8c5e7190eb3fd260bb0e1fc7a14675b7ddba88c554f917b2ec68556996cbf30f`)
with the missing stats reader in `junkenstein_ai.py` pinned above.

Enemy weapon overrides now validate the authored controller slot and retain
the state's identifier, flag and precedence until exit. This follows
`register_ai_weapon_record` and `_register_ai_record` in the pinned Python
`scene.py` above. Reaper's 0AAA and Roadhog's 0BC6 placements use the base
state lifecycle for the exact nodes in `_BOSS_BASE_STATES` in the pinned
`nodes.py`. The prepared Revenge probes reach controller startup for both
Bosses. The Rust scene now preserves the destination for the two 0C83
fallback evaluators using the pinned `scene.py` reference. The scoped
encounter candidates use the unselected state from Python
`runtime.World.selects_ai_behavior`, SHA-256
`3d823ace524c96fd51353a162008521f395b7cb6c3064fa5508c1b9d09e81f22`.
The localized display name state follows `JunkensteinDisplayNameOverride`
in the pinned `nodes.py` and the scene's component 92 binding registry.
The fixture confirms registration and release, and the prepared room
confirms Reaper's name in the controller registry, successful observer
baseline encoding and 61 subsequent frames. A probe at frame 62 reaches
`01CA:126` on `STU_ED45C5E8`, the same recipient query as Cassidy.
The controller name's client presentation, weapon input, intro and
verification of the reported first-Reaper crash remain open.

The normal owner body now mounts 0CA5 as a passive listener, matching
`_player_graph` and `before_units` in local `junkenstein_scene.py`,
SHA-256 `12d69591c84c2cec53fde1cf766bc9cf05b7f311c1145aab8af224d3128cf99c`.
The body VM carries the sender into local and paired-body game messages
and binds the authored caller and instigator fields using the pinned
`runtime.py` legacy message handler. A prepared 00BD fixture uses the real
defender graph with explicit position, facing, team and tag services. It
verifies binding and facing without invoking the graph's ordinary startup
entry. Normal Cassidy's owner snapshot confirms that only raw state 11
of 0CA5 is active at spawn, matching `mount_player_intro_receiver`.
This arms the authored 06AB receiver. The 0CBA PlayScript request targets
the spawned body's current possessor to start 0C95 on its controller.
0C95 then requests 0CBC on that controller's possessed body. Both
cross-component PlayScript calls and the resulting boss intro are open.
In the 104319 client, Arcade Normal admitted Cassidy and the first
Reaper appeared at 164.6 seconds on the inspector encounter clock.
The client remained in the match at 214.9 seconds. The boss stayed
at its spawn location, and the owner ammo counter remained at 0/0.

The 0CBA living-body roster follows `FilterArray` in the pinned
`nodes.py` and `filter_array_candidate` in `scene.py`, SHA-256
`05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`.
The append follows the unkeyed insertion in `array_actions.py`, SHA-256
`e0ade2935b8afc4560c7514d0ef019dbe69c609496c8ba13b7d891117ba54faa`.
The Rust state watches its source variable and samples the live room
body registry while active. The prepared fixture exercises the authored
array and outgoing counter and timer, independent of the PlayScript caller.

For direct loaded PlayScript assets, the Rust VM implements the keep-existing
policy, ordinary startup, and separately queued start-entry bindings. The
reference is `play.py`, SHA-256
`0f3d7cdd16f9d66d1b09e54f645697387408f3b0941ea62546ca3b6c1b7f92f9`,
`play_queue.py`, SHA-256
`6f3e812db3cc6f2553b08203e9b893e15633b6400de04290705c0dbb82c19f97`,
and the pinned `runtime.Instance.start_entries`. The prepared fixture evaluates
the original 0CBA and 0C95 call sites with explicit body/controller relations.
The room now resolves the spawner's current possessor from the registered
enemy pair. Its 0C95 request enters the controller on the enemy simulation
frame. The controller's 0CBC, 0CCD and 0CE3 calls target only the same pair's
body. The reference is Python `scene.SceneWorld.play_script_context` and
`play_queue.py` at SHA-256
`6f3e812db3cc6f2553b08203e9b893e15633b6400de04290705c0dbb82c19f97`.
The prepared Reaper regression exercises the room queue and both live
components and observes 1000 maximum health on Normal. The prepared scene
also reaches the subsequent authored spawner actions and records the three
spawned Zomnics. The 0C95 controller also resolves the ordered map target
array for tag 0321 and binds the 0CE2 child's position and heading before
entry. The prepared Reaper fixture reads the placement at
`[84.9754638671875, 12.68067741394043, -79.8869400024414]` with heading
`10.78968620300293` degrees. The local Python comparison uses
`scene.py` at SHA-256
`05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`
and the 104319 graph export for the child override rows.

The 0C95 and 185D facing states now register a prioritized direction for
the possessed body. The room applies the winning horizontal direction
while the Boss is alive. The VM refreshes the direction per frame and
removes the record on abort or instance retirement. Prepared tests check
Reaper's spawn yaw and the end and reentry lifecycle. The Python
comparison is `expr.py` at
`8c5e7190eb3fd260bb0e1fc7a14675b7ddba88c554f917b2ec68556996cbf30f`
and `junkenstein_scene.py` at
`12d69591c84c2cec53fde1cf766bc9cf05b7f311c1145aab8af224d3128cf99c`.
The live cinematic exit delivery, contact services and battle behavior
still prevent complete wave execution.

The enemy pair now supplies the exact registered body and controller
definitions to the 0C95 selector. The 0CBC supplemental health records use
the same room difficulty as the mode, with six independent authored tables
checked over all four difficulties. Their state-owned Vitals records preserve
base capacity, absorb damage before base health and retire independently.
The authored maximum-health query reads the combined capacity.
The reference is `AuthoredBodyHealthPool` and `combat.py`, SHA-256
`044e815f5c87c06dad18e1fa405818147c4badc706aec720657cf95478d21cd1`.
The Normal zero-capacity state retains its native no-op behavior. The
prepared Reaper probe reaches 1000 maximum health and starts 0CE0.

The Rust room now forwards 0CBC:30's 06B0 to the mode at its next frame,
retaining the Boss body as the sender and parameter 0008. The mode checks
the active target-tag effects before falling back to the body's authored
tags. The Normal prepared probe starts one 0CE0 and 0C8C sets the Reaper
intro flag 8520. This evidence covers graph startup and server-side
delivery. 06AB client playback and the exit handoff remain open.
The local Python comparison is `game_message.py` at SHA-256
`44096724b33862baa9071b0383851fb45609d7de04ff0e256fbc55d8008e136d`
and `scene.py` at
`05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`.

The wave body's 0CBC:129 records suppression until the state ends or its
instance retires. Rust currently has no encounter-body peer movement
query. Static collision is outside the scope of the suppression. The
prepared lifecycle test checks activation, abort, re-entry and teardown.
The prepared Reaper advances through 0CE0:76 to the one-second wait and
its observer snapshot encodes. The local Python mover
reference is `mover.py` at SHA-256
`8f5eae4017351a1d357fb9d4d7f0ff215addb251aba677fa2574abf3adb246b7`.

Rust now handles the single-part 0CE0 voice cues at nodes 61–70 and 77.
Narration uses the mode entity as speaker. The delayed Boss cue uses the
Boss body. Both use the Boss body as the delivery anchor and wait for its
create ACK. The prepared Reaper produces one 06B5 narration request and
finishes that state. A second fixture covers the delayed 06BD cue and its
cancellation on instance teardown. Both notice layouts match Python byte
vectors. Original-client playback has not been checked for this change.
The local Python comparisons are `junkenstein_ai.py` at SHA-256
`38020736a872c215fcbff86964f38b8b453320221173b42f18d802d726254b10`,
`voice_stimulus.py` at
`1e9d9d58046c227ac053a5cfeee3c6e53ec825351346e65b58f2b6c583d8b7ef`
and `voicelines.py` at
`86e08dbc7f946604ad040d2616677ab950e26167a972152d4d4473e27c73ff24`.

The 0CE0 and 0C8C routed actions now deliver their authored 06DE and 06AC
messages to spawned defender bodies. 06AC also reaches the linked
controller while its 0CDC child is present. The prepared exit fixture
starts the real 0CE0:4 wait and verifies one 06AC, child retirement, the
06B4 update to 0CE2 and removal of the 0C95 facing record. The prepared
Normal Reaper reaches the first 06DE through its ordinary wave startup.
The local Python comparison is `junkenstein_scene.py` at SHA-256
`12d69591c84c2cec53fde1cf766bc9cf05b7f311c1145aab8af224d3128cf99c`.

The prepared Normal Reaper now advances through time scale at 0CE0:3,
the 06AF and 06AB broadcasts at 0CE0:15 and 0CE0:21, and the 06AC exit.
The fixture checks one camera activation, one Boss voice request, the
observer payload, and release of the cinematic aim and facing. The
0CBC:133 pause receiver reads the current local sender, the mode sender
and other Boss body senders from the preceding tick. Player-body pause
receivers and the remaining message target policies still need coverage.
In the standalone Rust 104319 session, Cassidy entered Normal through Arcade and
remained connected through 231 seconds. The Zomnic bodies halted at
0CBC:122 on STU_9B213168, and the first Reaper did not appear.
In the combined build, the first Boss appeared at 193.247 seconds at
the authored Reaper spawn. The client rendered the model and pumpkin
marker and remained connected through 245.071 seconds. The sampled Boss
held 1000 HP and stayed at its spawn position. The Zomnic diagnostic
recurred. These observations establish client survival across Boss
creation. Camera playback and post-intro combat remain open.

Rust now starts the balcony exit when the authored 0CE0:4 wait has ended,
the encounter permits movement and a living defender is present. It uses
the walking heading and throttle window from local Python `BossPursuit`
in `junkenstein_ai.py` at SHA-256
`38020736a872c215fcbff86964f38b8b453320221173b42f18d802d726254b10`.
The prepared 104319 test keeps Reaper at the authored spawn through
pause, observes the mover enter the air, and confirms that Reaper lands
below the balcony and the exit ends once.
The isolated Normal client replay used an invulnerable Cassidy and a
100,000-health gate to reach the original Reaper wave. The same Reaper
entity was first sampled at 182.335 seconds at height 18.49, was airborne
with a downward velocity at 190.879 seconds and was stationary on the
street at height 12.68 by 191.263 seconds. The original client remained
connected and the pumpkin marker moved down with Reaper. The intro
camera and audible dialogue still need client verification.

Rust now reuses the bounded collision search to pursue a living defender
after Reaper leaves the balcony. A route ends at a clear position within
the reference 20-meter shotgun range. The server rechecks visibility
against eye and torso points and stops to aim at a visible defender. Target
selection and replanning follow `BossPursuit` above and `npc_navigation.py`
at SHA-256 `f99eed630ee4f99297c77e23844712730ddedde51ac36b2a7ff8c46a3314dd2b`.
The prepared 104319 fixture covers movement, clear aim, cinematic pause,
defender relocation and target loss. In an isolated server replay with
the high-health gate, the authored wave spawned Reaper and he stopped
to aim about six seconds after landing.

The Rust Reaper body now starts the authored 0015 manager and 0019
shotgun roots. The primary command follows the 08B6:3 slot-zero policy
in local Python `junkenstein_ai.py` at the hash above. Schema 001B supplies
the magazine multiplier for both Reaper and Cassidy, following
`script/rulesets.py` at SHA-256
`a9f7b3c257b5aab857adf4798cf554534eb3d9439023c9bd920b9b138b06b915`.
The prepared Reaper fixture verifies one authored volley, ammo decreasing
from eight to seven, observer encoding and input release when paused or
the defender dies. The prepared Normal Cassidy owner begins at 6/6. The
original 104319 HUD showed 6/6, 5/6 after a single shot and 6/6 after
reload. With an invulnerable Cassidy and a 100,000-health gate, the client
stayed connected through the first Reaper's spawn and approach. Server
positions put Reaper about 20 meters from Cassidy by 205.423 seconds.
A longer server fixture observed two volleys and six rounds remaining.

Rust now resolves the authored Reaper volley through the room's current
capsules and static collision. The 0019:10 amount reads the encounter's
difficulty identifier and the weapon graph supplies range and falloff.
`script/scene.py` at SHA-256
`05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`
supplies the four damage tiers. `weapon_spread.py` at SHA-256
`313bceda73a1225dde16c74058533df3df9681f5a9d43962713285c9c41ad9e9`
supplies the deterministic rotation and concentric rings. The complete
16,384-word table and sampled directions match the reference. The Normal
fixture receives damage from the authored volley on the selected defender.
An isolated 104319 client run used a 100,000-health gate and allowed only
Reaper's damage through to Cassidy. Cassidy was at 225 HP before contact
and 222.67 HP at the first sampled hit. The client remained connected and
displayed 212/225 in a later frame. The server recorded the same Reaper
as the damage source.
Reaper now retains the completed intro handoff when its graph retires,
following local Python `BossPursuit.handoff_complete`. At 0019:38 Rust
follows the reference trace action. The 0019:48 and 0019:50 self messages
have a client-only receiver, so server reload timing follows the authored
animation state. These action handlers follow local Python `nodes.py`
at SHA-256 `9e3e7b418a3b4f7cecb13a95f0e45314d2770c67f94925a73bbb620589f8f08f`.
The prepared fixture now fires after intro retirement,
reaches zero ammo and fires again at seven rounds after reload. The
original-client reload and muzzle and audio playback remain open, along
with Cassidy's Deadeye.

Roadhog's 0BC6 and 0BC8 controller roots now initialize before the room
publishes his body. The empty slot lookup follows Python `af4155f`
`script/entity_queries.py` and the self-possession and position queries
follow `script/scene.py`, at the hashes recorded below. The prepared
104319 observer baseline encodes. Injecting the authored 0C95 wave
reaches the unsupported outgoing combat damage filter at 0CBC:141.
Roadhog's intro and combat still need original-client verification.

The subsequent revision registers Roadhog's outgoing combat damage filter
for 0CBC:141. It evaluates the adjustment, keeps the authored exclusion
tags, and uses the Python `af4155f` `scene.py` rule to adjust outgoing
hits. It projects an empty link payload, following `driver.py` at SHA-256
`7602219c38dbfd9caf09ec1197ef3ebc56745b98de67148888a8c0494e7f9893`
and the 104319 codec. The prepared Normal wave fixture encodes the intro
observer state, checks one activation each for the animation and camera,
records the narrator and Roadhog requests in order, and verifies filter
removal. An accelerated run using 104319 data and a high-health gate
spawns the 3,800-health Roadhog at frame 19450 after direct test damage
kills Reaper. The phase-three wave still reports body startup failures
and never reaches a result in that run. This is prepared-server evidence.

Roadhog now uses the same collision pursuit and shotgun spread services
as Reaper. The 8.749-metre primary range and controller slot-zero target
follow the pinned Python `junkenstein_ai.py`. `scene.py` supplies the
controller 1959 damage tiers. The weapon's 01C6:37 action follows the
pinned Python `nodes.py` continuation. The Normal fixture encodes the
firing snapshot and observes 1.44 damage per pellet after the 0CBC
multiplier. It observes five shots before the empty magazine and another
shot after the authored reload. Secondary fire, hook and original-client
presentation remain open.

Roadhog's primary pellets and secondary scrap ball now follow the linear
sweeps in pinned Python `af4155f` `combat.py`, at the hash below. The
01C6:29 authored transition splits the ball on expiry. The 01C6 ruleset
switch uses the secondary-enable default from Python `rulesets.py` and the
range follows the pinned `junkenstein_ai.py`. The prepared Normal fixture
checks 8 damage from a direct hit at 8.9 metres and 1.44 damage per
split pellet against a defender at 12 metres. The flight regression
distinguishes lifetime expiry from a body or wall impact. NPC projectile
visuals and original-client weapon playback remain open.

The entity-existence test now reads a bound body and an empty target in
Zombardier's 0C87:31 and Junkrat's 0232:45, following the pinned Python
`entity_queries.py`. Junkrat's 0CB5 and 0CBA base-state placements follow
`nodes.py`. The common encounter host leaves the 0CC1, 0B6F and 0CD0
behavior candidates dormant, as in the Python scene host. Both bodies
encode observer baselines from the prepared 104319 pack. In the accelerated
Normal run with a high-health gate and direct test damage to defeat the
Bosses, Zombardier first spawns at frame 7216 and the 2,200-health Junkrat
at 26950. Neither spawn reports a VM fault.

Rust now mounts Junkrat's 022B weapon. The controller's 0CB5:20 state
and ordinary weapon overrides permit the 022B:20 primary input after
the intro handoff. The 022B:31 volley supplies speed, gravity, range and
damage. Aim uses the low arc and collision sweep from Python `af4155f`
`npc_navigation.py`, SHA-256
`f99eed630ee4f99297c77e23844712730dedde51ac36b2a7ff8c46a3314dd2b`.
The motion selector and per-tick float32 gravity follow pinned
`projectiles.py` and `mccree.py`. The prepared Normal room verifies
that a returning Junkrat launches and hits a defender 10 metres away
for 10 direct and 15 splash damage. Its observer body encodes and the
intro remains dormant. The full Normal server replay observes grenades
from the first and final-wave Junkrat bodies and still reaches the
Deadeye victory. Original-client Junkrat presentation remains open.

Zombardier now uses the living room roster and the same grenade sweep,
following `ZombardierControl` in pinned Python `junkenstein_ai.py`.
The 0CA1:16 input uses logical button 29. The 0CA1:0 difficulty
query follows the pinned `script/scene.py` weapon profile. The client-ended
0CA1:58 animation has no authored server timer or completion consumer.
The prepared Normal room advances the original wave schedule and observes
a Zombardier grenade dealing 30 splash damage. The observer snapshot
encodes. Zombardiers launch in the full Normal server replay, which still
reaches the final Deadeye victory. Original-client projectile and animation
presentation remain open.

The autonomous Shock-Tire now initializes 0C93:26 and the shared
0234 body graph. The AI setting lifecycle and live velocity query
follow the pinned Python `nodes.py` and `scene.py`. The tire's
participant query preserves the destination as in `entity_queries.py`
and the scene setup in `junkenstein_ai.py`, at the hashes recorded here.
The prepared fixture encodes the tire observer baseline, verifies the
20/true setting and 5/false cleanup, then separately runs the authored
wave against the gate and observes increased damage and pair retirement.
The accelerated Normal run spawns the first tire at frame 9439 and
observes nine tire deaths near the gate. It remains in phase three at
frame 44000. These observations are from the server and prepared data.

The Witch and Summoner now publish in the prepared Normal scene. The
Witch's target-tag exclusion and local-command query, the Summoner's
empty deployable count and static anchor, and their controller placements
follow pinned Python `scene.py` and `nodes.py`. The body-health query
preserves its initial value when the target is unset. The 0CC0 final-wave
lookup uses the current tagged bodies and returns entity zero when none
exists. The accelerated Normal run first spawns the Summoner at frame
23200 and the Witch at frame 34046. The Summoner advances until the shield
recharge path starts after test damage. The Witch's subsequent 00CA:56
command read uses the same absent local-input value as 0071:39. The
0CC0 join and final-wave message routing are still open.

Rust mounts the Witch's 006B staff, 006D pistol and 0015 weapon manager.
Controller 0CD2 receives 0079 with slots zero and one for the nearest
defender and the ranked wounded ally. Ranking follows
`update_witch_combat` in pinned Python `junkenstein_ai.py`. The prepared
room verifies both slots, their clearing and the weapon changes driven
by 0CD2:44/49 and the existing logical-button dispatcher. With no wounded
ally, the Witch aims at the nearest defender within 20 m using the capsule
samples and wall query from pinned Python `npc_navigation.py`. Controller
override 0CD2:12 and the 006D:7 trigger drive volley 006D:10. The clip
scalar and the four damage values follow pinned `rulesets.py` and
`scene.py`. The prepared Normal room observes 5 damage per hit and
releases the trigger when the defender moves behind a castle wall.

The Witch uses the 0CD2:9 staff override and 006B:118 trigger when her
wounded ally is visible and within 12 m. The staff's 006B:62 effect mounts
0079 on the recipient and sends 000F with the authored healing amount.
0079:0 advances the recipient's actual vitals with the difficulty multiplier.
The recipient resolves the linked healer's display name and health in 01CF.
Target acquisition, application and removal follow `junkenstein_scene.py`
and `script/scene.py` at Python `cc620be`. The prepared Normal room verifies
healing, the observer snapshot and removal when the recipient goes out of
range. If the selected ally or defender cannot be reached, the Witch uses
the shared bounded Boss route search with the capsule visibility test as
the route acceptance condition. The prepared room observes the Witch
walking into staff and pistol range, stopping to heal or shoot, and
releasing the trigger for a defender behind a castle wall. Original-client
acceptance remains open.

The Summoner mounts 157C and uses the 185D target ranges and overrides.
The room delivers both slots through 0079, sweeps the authored orb volley
157C:85 through map collision, and supplies the thin and widened beam rays
for 157C:202/203. The chosen contact drives 157C:170 with the authored
difficulty rate. These follow Python `cc620be` `junkenstein_ai.py`,
`junkenstein_scene.py` and `script/scene.py`. The prepared Normal room
verifies damage from both weapons, beam snapshot encoding, holding fire
behind a wall, and target cleanup. Original-client acceptance remains open.

The final wave now joins four distinct input edges at 0CC0:7. The mode
rechecks 0C8C:33/275 on the resulting completion-variable change.
Both follow Python `af4155f` `script/runtime.py`. The victory continuation
at 0C8C:236 follows the pinned `nodes.py`. The narrator line at 0C8C:221
is selected by the last Boss from pinned `junkenstein_dialogue.py`, using
the authored wait before the client outcome action. The accelerated Normal
server replay preserves the gate and uses direct test damage for Boss deaths.
It reaches phase four and emits the authored defender victory message
at frame 34890. The outcome action and summary both encode by frame 36000.
Rust now handles the 0CBC:69 cleanup using the existing enemy vitals
service. The state follows Python `af4155f` `script/scene.py`, SHA-256
`05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`.
The regression checks one authored self-damage request for a living body
and none for an already dead body. Original-client presentation remains open.

The Normal Cassidy owner charges entity variable 342 up to the authored
cost in 338, following Python `af4155f` `driver.py` at the hash above.
The 0B8B telemetry lifecycle follows the pinned `nodes.py`, and the
022F:8/12 joins use the same distinct-edge rule as the final wave.
The 104319 owner fixture confirms the initial rate and full charge with
a healthy script snapshot. An isolated Q-input probe spends the charge
and reaches the 022F:11 target effects and 022F:26 target search.

The Rust owner now fills the 022F:26 and 022F:86 arrays from the living
enemy room roster. The selection follows Python `af4155f` `mccree.py`
at SHA-256
`29dcee4cf9da996e01b389e9da27f89a8ba21144425794fc68aba5207427ebec`.
It uses the graph's full cone and range, orders by distance and entity ID,
and checks static capsule visibility for 022F:86. The 104319 Normal owner
fixture activates the authored graph with Q and verifies both lists with
the prepared castle collision and positioned test bodies, including an
enemy behind a wall and removal from the room. 022F:11, 01EE damage, and
original-client Deadeye acceptance were open in the search-only revision.

022F:11 now owns the 01EE instances, reapplies their 000F message with
parameter 92, and retires them when their targets leave or their scope ends.
The instance reads current health from the Rust room and 01EE:14 uses
the authored damage and range. The shot uses the target's upper capsule
and rechecks static sight before entering ordinary hitscan resolution.
The Normal fixture uses two positioned 1,000-HP test bodies and a third
behind a castle wall. Both visible bodies die after the second Q and the
occluded body survives. Owner corrections encode lock instance creation
and removal. The 022F:121 action follows the reference fallback, and the
022F:84 selector has no recipients in this reconstruction. Their native
effects, final-wave integration and original-client presentation remain
unverified in the positioned-body fixture.

The subsequent isolated Normal replay advances the actual encounter
clock and final-wave scripts. It ignores incoming damage to Cassidy,
sets the gate capacity to 100,000, defeats earlier Bosses with test damage,
and arranges the four final-wave Bosses' positions and health for the
finishing shot. A second Q fires 01EE volleys at frames 35840, 35848,
35857 and 35865 and kills Roadhog, Junkrat, Reaper and the Witch. Cassidy
stays at 225 HP and the gate survives. The server sends one winning
06BE result and one Reinhardt victory cue. Both the outcome and summary
encode. The replay observes one animation activation for each Boss
type's first introduction. Returning Reaper, Roadhog and Junkrat skip
that cinematic. This replay reported a 0033:5 shield recharge diagnostic
for the Summoner. The replay uses server-side observations.

Rust now advances the 0033:5 shield recharge at the graph's authored rate,
following Python `af4155f` `junkenstein_scene.py` at SHA-256
`12d69591c84c2cec53fde1cf766bc9cf05b7f311c1145aab8af224d3128cf99c`.
The 0CBC:31/33 intro bypass and the 0C95:5 facing handoff can release
Reaper and Roadhog's combat without replaying the cinematic. The latter
also follows the pinned Python `junkenstein_ai.py`. The prepared
Roadhog intro, reload and both firing modes remain operational.
The repeated full Normal replay damaged the Summoner's shields and
observed recovery without a body VM fault. It also verified the combat
handoff and absence of repeat intros on returning Reaper and Roadhog.
The four final Bosses were positioned for the test. Only Roadhog's
health was lowered before the Deadeye release. The release again defeated
all four and produced the victory cue, outcome and summary.
Native audio and visual presentation remain open.

The next Rust revision publishes the authored 0744 sender at 0CBC:122.
Its receiver at 0D24:2 runs on the client. SubScript bindings use the
existing config-group collector for the 0C95:1 identifier array. The
prepared Normal room starts the actual street wave, advances 250 frames
after setup, and verifies that 0C88 receives the identifier array, that
0C88:0 remains dormant and that the observer snapshot encodes. The
combined 104319 session with the first two changes reached 235.583
seconds with a Boss present and reproduced the separate 0C88:0 failure.
The dormant-candidate fix has prepared-room evidence. The next revision
advances the authored 0C89 explosion, supplies the room target roster, and
replicates scripted health and pair retirement. The prepared Normal
street wave runs through death and retirement. A separate fixture with
the Zomnic one metre from the gate verifies the authored gate damage
counter at variable 8569 increases. The gate reports the unsupported
voice state at node 50. Gate approach and gate damage from the street
spawn still need an end-to-end test. These checks use the prepared room.
The additional Python comparisons are `play.py` at SHA-256
`0f3d7cdd16f9d66d1b09e54f645697387408f3b0941ea62546ca3b6c1b7f92f9`
and `runtime.py` at
`3d823ace524c96fd51353a162008521f395b7cb6c3064fa5508c1b9d09e81f22`.
The explosion comparison uses Python `af4155f`, with
`junkenstein_ai.py` at SHA-256
`38020736a872c215fcbff86964f38b8b453320221173b42f18d802d726254b10`
and `script/nodes.py` at
`9e3e7b418a3b4f7cecb13a95f0e45314d2770c67f94925a73bbb620589f8f08f`.

The 104319 client run on Rust `2156c64` entered Normal as Cassidy through
Arcade. Samples through 241.919 seconds included retired Zomnics, a
1000-HP Boss and a connected Cassidy at 225 HP. The client displayed
Zomnic wreckage and a Boss marker. Some Zomnics hit the unsupported
entity team query when the tracked-target filter ran with a nonempty
roster. The Rust evaluator now returns the no-team sentinel for the
unset operand, matching `script/entity_team.py` at SHA-256
`3e2d29b6662607c479975023545506eae239fddc06beadbbc0472f91388c55b0`.
A nearby-defender probe then reaches 0C89:49 contact effects and 0C89:38
damage against another body. The next revision verifies both sites in
the prepared Normal street wave. Two targets receive one authored
damage request each, the Cassidy owner publishes changed health, and
the observer snapshot carries node 49 and variable 375. This follows
Python `script/scene.py` at SHA-256
`05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`
and `combat.py` at
`044e815f5c87c06dad18e1fa405818147c4badc706aec720657cf95478d21cd1`.
Contact playback and player death and respawn still need original-client
verification.

The Rust gate approach reuses the same floor and clearance probes as
spawn sampling and advances an incremental grid search. At the approach
threshold it queues 0C87:20 once. The prepared Normal room confirms the
first street Zomnic moves over ten metres, dies within three metres of
the gate and increases its damage counter. The original 104319 client
run also sampled the street wave at the gate. The movement comparisons
use Python `af4155f`, with `npc_navigation.py` at SHA-256
`f99eed630ee4f99297c77e23844712730ddedde51ac36b2a7ff8c46a3314dd2b`
and `mover.py` at
`8f5eae4017351a1d357fb9d4d7f0ff215adddb251aba677fa2574abf3adb246b7`.

The first original-client gate approach exposed an unsupported entity
filter at 0033:29 when Cassidy took damage. Without owner vitals, the
nearby-target lookup at 0C89:52 also halted. The prepared Normal street
wave now advances the damaged owner through 0033:30 and encodes the
owner snapshot. Rust applies the filter masks from Python `af4155f`,
following `script/scene.py` above and `junkenstein_scene.py` at SHA-256
`12d69591c84c2cec53fde1cf766bc9cf05b7f311c1145aab8af224d3128cf99c`.
Only player bodies with authoritative vitals enter the tracked-target
roster. The original 104319 client retest showed visible explosions and
Cassidy's health declining from 225 to zero with the owner script still
active. The room continued to spawn enemies. This run used normal player
health and did not verify defeat presentation or respawn.

The gate narrator uses the authored 0CA7:50–52 stimuli, the encounter
speaker, and the acknowledged gate placeable as the anchor. This
follows Python `af4155f`, `junkenstein_dialogue.py` at SHA-256
`a1a3f8b247964601b6e0a2425962ecfa4369b6ed7125d4d8c7e6682b817bd248`.
The prepared gate attack produces one 06C7 notice and completes the
voice state after its line duration. Original-client audio remains open.

The castle gate now uses the Rust scene observer stream, following
Python `af4155f` and `observers.py` at SHA-256
`1ca2a8f4d45973c05509d84bb615f45b1f28a9076e071ac94c53b45acde1ea0f`.
The prepared gate test delivers its baseline after the placeable
acknowledgment and advances four authored damage animation states
through the observer stream. The damage-message probe reaches zero
gate health and verifies all three narrator stimuli. The original
client stayed responsive after the first waves at default health.
Gate appearance and end-of-match UI still need original-client evidence.

The 0C8C:84 result broadcast evaluates its four authored parameters
and reaches the matching mode, gate, defender and enemy graphs.
The prepared Normal gate-destruction case reaches phase five and
confirms one defeat result, the 0CC7:39 sender activation and Cassidy's
0CA5:70 aim lock through his owner runtime and mover. The route follows
Python `af4155f` and `scene.py` and `nodes.py` at the hashes recorded
in this section. The next revision publishes the 0CC7 statistics
sender and the separate 0C8C outcome action. The prepared defeat test
decodes 0C8C:268 in remote slot 45 and verifies that the result
retires a live wave pair. The frame action follows Python
`statescript.py` at the hash recorded below.

In the 104319 client, an isolated Normal validation build set the gate
capacity to 20 and made Cassidy immune to damage. The gate reached
zero, Cassidy stayed at 225 HP, and the server reached phase five
without live enemies. The client acknowledged the 06E1 frame action.
Sampled frames showed the first-person HUD after the encounter ended.
The initial mode frame now includes the authored 0CBB HUD and its
03F2 and 180A children. The 14-card fixture decodes their descriptors
and verifies the instance and parent IDs against the server script tree.
The repeated Normal defeat run with those descriptors still showed
the first-person HUD in phase five. In the target graphs, the 03F2
outcome entries trigger voice. The separate 180A screen sequence
starts from a 0695 listener and constructs the 0283 results widget.
The Rust phase handoff now follows the 0C8C:72 base action from
Python `af4155f` in `nodes.py` and activates the authored 0695 sender.
The gate fixture confirms the 180A receiver activates its post-match
stack and checks the sender delta. This is separate from the outcome
voice and summary messages. In the repeated Normal client run the
phase-five HUD stayed in first person. The fixture reports that
180A:55 initially could not evaluate the authored screen-sequence
configuration. The Rust evaluator now follows the authored map, array
and fold behavior from Python `af4155f` in `play.py` at the hash above.
The prepared Normal gate fixture evaluates four stages and their
41.1-second duration. The fourth stage selects the 0283 results graph.
The server now reconciles the 180A:42 script list by occurrence and
starts each 180A:41 child with its own instance scope. The lifecycle
follows the Python `af4155f` handlers in `nodes.py` at the hash below.
The phase uses its authored fade and hold durations as server timers.
The prepared defeat fixture observes the four authored graph sets in
order, verifies retained IDs and confirms the 0283 child retires.
The reliable observer delta now publishes the played screens and
their removals following `observers.py` at the hash above. The server
keeps the phase-list state and row instances local, and the client
receives the played graphs as independent roots. The identifier-map
wire encoding matches `statescript.py` at the hash below.
The corrected isolated Normal defeat run showed the original client
results screen after the gate reached zero with Cassidy at 225 HP.
Its heading was Victory and the client returned to the combat HUD
when the screen sequence ended. The 027F banner
now starts with the winning team from the manager's authored 0695
receiver. The prepared gate fixture checks team one in the banner
projection. The repeated original-client failure case showed the
red outcome effect, and the 0283 recap still displayed Victory.
The authored 180A completion action now records the end of the screen
sequence. Once the final screen removals are acknowledged, the Rust
service uses its existing leave flow. The prepared Normal defeat fixture
checks the completion signal and reliable delivery timing. An isolated
104319 Normal defeat run reached zero gate health with Cassidy at 225 HP,
returned to the difficulty menu, retired the room and opened hero selection
when Normal was selected again. The defeat heading and the live victory
path remain open.

The 180A:4 receiver also begins the networked 08D6 broadcast at 180A:7.
The Rust manager now projects that sender in the same reliable screen
stream. The prepared gate fixture checks its first activation and its
presence in the observer projection at the start of the screen sequence.
The isolated 104319 defeat replay reached phase five at 58.431 seconds
with zero gate health and Cassidy at 225 HP. The final recap showed its
leave countdown and a red background effect. Its heading remained blue
Victory and its match time remained 0.0. The server then retired the room.

The local Python comparisons are `nodes.py` at SHA-256
`9e3e7b418a3b4f7cecb13a95f0e45314d2770c67f94925a73bbb620589f8f08f`,
`message_state.py` at
`5519bd38f3ab01111f67770daa9a98a9cb40dc8eae82bbf13ce07a5fb054c8c3`,
`message_receiver.py` at
`261d1e38b08c077270e2bb46d4579579f39e6fa76325d68874b8ab8c1575656e`
and `statescript.py` at
`ea39b19d6f64bb92537d65f981af4ef2330871e2ff41a4faf91c847ea056c1b4`.

The Rust VM now registers the authored intro entity-control states and
the paired 9028E035 states in activation order. The model-control record
is released after the final suppressing state ends. State links use the
target graph's raw index for owners and remote index for observers.
The local Python comparison is `entity_control.py` at SHA-256
`dff80cc22dd9574bbda16b34dd6a5d6c201cfbab09dcef342f15294ca860fc23`,
`nodes.py` at
`9e3e7b418a3b4f7cecb13a95f0e45314d2770c67f94925a73bbb620589f8f08f`
and `statescript.py` at
`ea39b19d6f64bb92537d65f981af4ef2330871e2ff41a4faf91c847ea056c1b4`.
The two 43994F1B and 93B8758C registrations in 0CE0 and 0D03 now
retain independent chains. The prepared fixture begins the original
body-control states, traverses both sets of child states and encodes
owner and observer baselines. Ending one instance clears the outgoing
links from the other. The last state ending releases model control.
The prepared 0CA5 fixture publishes an owner camera delta with the focus
entity and bone, and a duplicate 06AB during the same activation keeps its
activation count at one. Live 06AB delivery, the same-team collision
service and camera completion remain open.

On the combined Rust inspector candidate, the 104319 client entered Arcade
Normal as Cassidy. The first Reaper at the authored balcony position was
first sampled at 188.335 seconds. At 218.783 seconds the client was responsive
and Reaper still had 250 health and zero velocity. Cassidy's revolver,
health and ability icons rendered while ammunition displayed 0/0. The
gate-damage fixture passed with its existing voice-stimulus diagnostic.
The room now supplies current living defender bodies in seat order and
rechecks the current body before delivery. The verified 01CA self selector
and the reference 00CB and 14B1 forms produce 00BD messages with source
position and the three tags queried by the receivers. The room forwards
these messages to owner runtimes and scopes the foreign actor inputs to the
authored delivery. The mode-existence and required literal filter tag reads
follow Python `entity_queries.py`, SHA-256
`4df6b28f71153038ab27b32faa254545e808e12c61835d214b8b22a75201dd0b`,
and the pinned `scene.SceneWorld._filter_entity`.
The prepared Reaper emits at frame 62, and the prepared Cassidy consumes
the foreign input in both facing directions and runs through its own
62nd frame without a diagnostic. The 06AB intro receiver activation,
dialogue playback, combat and original-client acceptance remain open.

The authorized 2026-11-11 original-client retry did not reach authentication:
enUS and auto-selected zhCN both displayed a black screen. The relay matched
the Gallery instruction signatures but reported `branch protection`, Win32
error 87, on an execute/read mapped view. Copy-on-write also failed; no change
to the patch protection policy was retained. MSVC, the private-memory installer
probe and 12 caller/pruning replays pass. These fixtures establish the stub's
roster scope, not installation on this mapped client or visible NPC rendering.
A later run used the canonical Python launcher for the original client. Its
DevReg launch selected region US with the zhCN interface and audio locales.
The client connected to the native Rust server, showed the Arcade event and
difficulty cards, queued Normal, and entered the castle as Cassidy. The
inspector confirmed the encounter countdown and spawned Zomnics. Cassidy's
owner VM is unavailable in this scene even though the local pack has his body
definition. The Zomnic body VM halted on graph 0C89's unique-static-target
query, STU_6A926B21, ModifyHealth and TrackTargets. The relay still reported
Gallery branch-protection error 87. Win and loss, the gate states, dialogue
and Boss intros remain open.

The Normal Cassidy owner factory now starts the authored body, including the
`STU_A77AC2AC` Tutorial check from the pinned Python `scene.SceneWorld.native`.
The prepared scene verifies the snapshot and 225 health. In a second original
client run the revolver, health and ability icons rendered, with ammo at 0/0.
The owner VM stopped at `01CA:126` on `STU_ED45C5E8`. The original Hero
Gallery showed its 32 playable entries and no NPC entries. Separately, the
pending AI merge candidate passed the prepared authored Zomnic attack check
for updating the gate damage counter and retiring the pair. This fixture
still expects a gate-script diagnostic for `STU_40E05F6B`.

The combined Rust tree also incorporates upstream `cfea41b`, preserving the
contributor history of PR #17. The upstream 900-frame tagged Soldier owner
session is retained alongside the combined encrypted weapon and healing tests.
The merge preserves the combined chase-state payload timing and the gap test's
live owner VM, and classifies the newly ported empty resource literals as
computed values for native-watcher tracking. Original-client observations in
the upstream changelog are not a combined-server client acceptance result.

The comparison helper retained at
`git show a325b9a:tools/rust_reference_vectors.py` runs from external local
storage against the clean Python `cc620be`
checkout and an external authorized runtime. It emits only counts and hashes.
It regenerates the mover settings, unconstrained velocities, graph wire indexes,
and supported map selections; `--movement` also replays the actual Practice Range
collision. The original 38-mover settings and velocity vectors remain unchanged.
Both 38- and 43-mover movement vectors now include the normalized stance fix.

The combined 699-graph bundle and the 700-graph variant with the authored `0B92`
timer fixture have separate Python wire-index hashes. The extra graph was parsed
from retained 104319 XML using OWLib `7afe5f52706e7002ed8c2717299cd60846f3fa3a`;
the XML SHA-256 is `43b7229a4b33df4cae66612987a0f4ce1c8502d1fa8dc7bf24795469ee36619d`.
It is an isolated firing/timer fixture, not evidence that full NPC body scripts
are supported in a live room. Both combined profiles retain the base patrol path.

The map comparison covers 95 map listings, 698 cases and 553 native-supported
selections. Python accepts four additional scripted mission selections that the
native map catalog still excludes. The generator reports those separately;
passing this scoped vector does not close Retribution or Storm Rising parity.

For a single local check command, with Cargo available on `PATH`, run:

```powershell
.\scripts\check_combined_rust.ps1 -Runtime 'C:\path\to\authorized-combined-runtime'
```

This runs locked public and prepared checks for both default and inspector
workspace configurations, runtime preflight and the actual two-room timing
workload. The selected runtime must carry the enriched `0B92`
fixture, Junkenstein placements, the explicit NPC catalog and both map caches; missing fixtures fail the
check. Passing it establishes the covered server-side comparisons, not full
hero, mission or original-client acceptance.

The recent-input selector follows `RecentInputState` in local `ow174/game/script/nodes.py`, SHA-256 `9e3e7b418a3b4f7cecb13a95f0e45314d2770c67f94925a73bbb620589f8f08f`. Isolated fixtures verify history avoidance, oldest eviction, expiry, reentry, shrinking arrays, empty output and nonpositive lifetimes. The prepared authored spawner now runs its target collection, selects a searched position and evaluates its `CreateEntity` request and publishes the initialized Zomnic pair when the explicit NPC catalog is present. The Rust and Python random streams differ; exact selected-slot parity is not established. These checks were static/offline; the user subsequently restored original-client verification authorization.

Entity creation follows the strict action and scalar-config behavior in Python
`cc620be`. Rust validates definition or hero identity, finite transforms, team,
optional controller and skin resources, and unsupported options before calling
the scene. A successful binding is written before outgoing plugs run; a declined
creation preserves the previous output, and an unavailable or failed allocator
does not follow the outgoing plug. The scene is responsible for synchronous
registration, fresh IDs and rollback of partial actors. Native heading uses the
same float32 transform-basis conversion as the reference.

The combined graph store now reads the eight authored enemy pairs and nineteen
encounter definitions separately from player and placed-entity definitions. It
checks resource types, normalized duplicate IDs, required records and any
declared AI association. Prepared evidence confirms the Zomnic's four body roots
and two controller roots. The staged allocator now reserves adjacent IDs, starts
both components and preflights the body observer baseline before publishing any
room state. Public fixtures cover a successful pair, rollback, fresh IDs, current
health and skin on late join, no practice respawn, and identity-gated observer
retry. These fixture checks do not establish authored enemy gameplay.

The explicit NPC catalog is external at `data/junkenstein/junkenstein-npcs.json`;
the runtime packer copies and hashes it for selected Junkenstein packs. With the
700-graph pack, both the real Zomnic body and controller now initialize without
diagnostics. The paired startup test verifies publication, adjacent identities,
observer preflight, 200 health and two active behavior records: `STU_600E3C59`
at priority 10 with inputs `[2,2,1,float32(0.2),1]`, then `STU_B61E6681` at
priority 0 with inputs `[1.5,1,0,0,0]`. The Normal spawner also creates a team-one
Zomnic at its selected candidate. These checks establish authored startup and
publication, not sustained AI gameplay.

The behavior registry follows component-88 priority and lifetime rules and
updates watched inputs in place. Pair-local messages are queued and drained
after component turns; general scene routing and arbitrary synchronous sender/
receiver ordering remain unverified. An extended stationary actor probe reaches
the `0C89` gate-attack branch and stops at the gate static target, `STU_6A926B21`,
ModifyHealth and two TrackTargets states. Navigation, gate damage, full waves
and original-client presentation remain required. Unsupported services remain
diagnostics rather than silently completing those states.
Root-schema initialization and override cleanup follow Python `cc620be`'s
`script/startup.py`, `script/nodes.py`, `script/scene.py` and `junkenstein_ai.py`.
No new original-client result is claimed.

The Halloween source now consolidates the component-88 request format and
bounded pair-message queue from `b663598`. Public-schema startup remains an
explicit VM option, applied before assignments and entries to roots and
descendants. This follows local `script/startup.py`, SHA-256
`1ec41af1824d37fc1bb71aab9bd639febfcc51f387a78018b7a65bec0db42eec`.
Additional fixtures retain native hero-query priority, pair-scoped health and
hero readers, authoritative death flags, optional pool outputs and all ten
common-controller base-state lifecycles. The consolidated source passes 385
default and 388 inspector public checks, both sets of 79 prepared checks, both
Clippy configurations, formatting and native runtime preflight. An earlier
stationary startup probe without paired-message delivery does not establish
sustained AI behavior; the current message-enabled attack boundary described
above remains incomplete. Narrator, hero audio, UI, gate transitions, Boss
introductions and both result screens still require original-client acceptance.

Participant collection follows `query_participants` in local `ow174/game/script/scene.py`, SHA-256 `05aed334dec77667f0b792a1fd165050049a22858c4bf92ae8a2a1fc56364186`, and `enemy_participants` in `ow174/game/junkenstein_ai.py`, SHA-256 `38020736a872c215fcbff86964f38b8b453320221173b42f18d802d726254b10`. The supported `0CBA/56` query reads the room's living team-one bodies in insertion order. Isolated checks cover team and death exclusion, unsupported filters, output ordering, empty collections and unavailable services. This is offline evidence. The first authored Zomnic now allocates as described above; full enemy lifetimes and original-client presentation remain pending.

On 2026-11-11, a client attempt against `c0904b3` used isolated native ports and fresh local profiles, retaining an already running Python server. The Rust listeners started successfully. The original client remained at a black screen before connecting to the test lobby or Battle.net ports, and the session relay reported that its NPC Gallery extension failed to install. Neither the Arcade menu nor Gallery presentation was reached. The cause is unresolved; this is no evidence of encounter, Boss, dialogue or result presentation. Only the client and server created for this attempt were stopped, and session logs remain outside Git.

The [migration guide](RUST_MIGRATION.md) defines the acceptance gate. The [restoration roadmap](ROADMAP.md) covers the broader build. Record each reference at a specific commit, fixture or original-client build. An isolated graph, fixture match and original-client pass are separate evidence.

## Reference and cutover policy

The [shared World query contract](BEHAVIOR_CONTRACTS.md#strict-world-queries) pins Python strict-host semantics at `50ba408` for known positions, known unset variables and unavailable queries. It compares typed query results and diagnostic categories through production expression paths. Python stops a strict component on an unavailable query; Rust records an expression issue and rejects the affected operation. This fixture supplies no evidence of equivalent fault recovery or original-client acceptance.

- Use the Python code on `main`, from a separate checkout, as the behavioral reference. Keep Python `main` and Rust `rust` contributions on their respective branches.
- Evaluate new Python behavior against the target build and the cutover checklist. Record the source and expected result before deciding whether to port it. Community estimates remain provisional.
- Pin the Python comparison commits before starting cutover acceptance. During the cutover window, keep the pinned reference limited to fixes that unblock verification. Any change to community development policy on `main` belongs in a separate maintainer decision.
- Keep game data, private captures, resource keys and signing material local. Public test fixtures must be safe to publish.


### 2026-11-12 original-client stability checkpoint

Clean integration 970c6162 builds with the locked inspector target and enters
Normal Junkenstein through Arcade as Cassidy. Local fixture a0d477db adds
explicit test-only invulnerability and 100x gate health; the original client
shows the first Reaper and continues through 267727 ms (Cassidy 225 health,
gate fraction 0.9761999845504761). This is post-spawn stability evidence, not
complete Boss AI, intro, voice or outcome acceptance. The mode timer/gate HUD
was absent. NPC Gallery and both requested result lines remain open.

Historical local source snapshots are preserved at 38391893, 97a74812 and
77d4c4d7. The clean integration branch is authoritative; those snapshots must
not be merged wholesale, since they include acceptance fixtures and older
copies of changes already integrated here. No Python checkout was edited.


### Junkenstein mission-notification entrance dependency

The 104319 0C8C:68 SubScript starts 1188, whose client-only notification
presenters depend on its server GameMessageEntry states. The Rust entrance
now mounts this sibling with its active 0694 and 06A8 receivers. The prepared
runtime regression exercises all 14 Arcade cards and checks that the existing
03F2 and 180A dependencies remain. This is graph and encoding evidence only;
live rendering still needs acceptance.
It does not close the separate missing timer/gate HUD or outcome-heading issue.

Ongoing 1188 state and variable changes now enter the acknowledged mode stream
as deltas against the already-mounted instance. The prepared Normal regression
drives original 0694 and 06A8 messages through the mode VM, encodes their changes,
suppresses repeated snapshots and checks countdown scheduling opportunities.
Unrelated server graphs and entity globals are excluded. The projection retains
the entity inputs declared by 1188's sync table, including difficulty and gate
bindings, and sends them on first delivery. A prepared delivery regression drives
the clock while checking notification/countdown fairness, ACK gating, exact lost
packet retry and scene reset. This establishes
headless projection behavior, not original-client delivery/rendering
or complete encounter acceptance. Python comparison remains pinned to cc620be.

The original-client a68f5d6c probe enters Normal as Cassidy and reaches active
enemy publication. Its sampled gameplay still lacks the timer/gate HUD; at
126031 ms the inspector records 11 actors and the unprotected player dead.
Notification rendering and outcome headings were not established. This probe
predates the entity-input and delivery-fairness repair, whose client retest is
pending. The owned server and client were stopped after the probe.

### Junkenstein authored HUD phase sends

The 104319 0C8C nodes 17/126 (0694) and 83 (06A8) persist their messages
for the client-only 1188 listeners. Rust previously routed these only inside
the server VM. They now retain their authored send state and each active
activation is delivered once on the reliable mode stream, after notification
inputs. Loss retries the same record; scene reset clears activation tracking.
The Normal/Cassidy regression advances the real opening timeline through frame
4120 and covers phase delivery, duplicate suppression, loss and countdown
fairness. Five prepared notification/entrance/clock checks and 404 public checks pass.
This does not establish HUD rendering or complete encounter acceptance.
The Python comparison remains cc620be; this change uses the local 104319 graphs
and existing persistent-send protocol encoder, without importing game assets.

The authorized 104319 retest of this repair enters Normal as Cassidy. At
86527 ms the HUD visibly has the objective label, 7:55 timer, gate health bar,
and team widgets; a later sample has a shorter gate bar. The timer stays at
7:55 after player death in those samples. No invulnerability or gate override
was active. This confirms the formerly missing HUD is instantiated, not clock
accuracy, gate-state completion, voices, Boss intros or either required outcome.
The external inspector evidence is phase-probe-state.json in the local test
session directory; no capture or runtime data is committed. Both owned processes
were stopped after the probe.

The 2026-11-12 invulnerable Normal/Cassidy baseline records gate zero, living
player, winner 1 and outcome 268 at 187919 ms. The client incorrectly displays
Victory with time 0.0; failure acceptance remains open. The HUD timer freezes
at 7:54 despite a decreasing server clock. Clock deltas now include authored
battle/overtime chases 37/60; five Python cc620be wire vectors and the actual
opening delivery regression cover their serialization and delivery. These
headless checks do not establish the corrected client clock or outcome UI.

The subsequent authorized 104319 probe of clock-chase commit 2159edf1 still
shows a frozen 7:55 HUD timer. External inspector samples have clock 436.181
at 108815 ms and enemy winner 1 / gate zero / outcome 268 at 189759 ms.
Cassidy remains alive at 225 health; the recap still shows blue Victory and
time 0.0. Thus the added battle/overtime state delivery is wire-verified but
does not close timer or outcome UI acceptance. Further UI binding/parameter
work is required. The owned client/server were stopped after this probe.

### Mission HUD announcement projection (2026-11-12)

The authorized 104319 client has active battle chase 37 but a frozen entity
clock (475.001 versus server 455.856). Isolated diagnostics identify unsupported
1188:58 client announcement 0FCE as the projection failure that blocks further
clock delivery. Rust now follows this narrowly matched client-message action
without a server gameplay effect, matching Python cc620be. The extended authored
Normal/Cassidy delivery regression fails with the original diagnostic before
this change and passes through tick 8000 with continuing clock samples afterward.
Public inspector workspace checks, Clippy and formatting pass. The authorized
original-client retest confirms clock progress (473.383 -> 430.654), with server
samples 472.726 -> 429.997; HUD reaches 7:34 and Cassidy remains at 225 health.
No new projection diagnostic appears. The recap capture was missed before the
match returned to the menu, so neither outcome acceptance line passes. Voices,
boss intros, the full encounter and gallery remain open. Owned processes stopped.


The 2026-11-12 Normal/Cassidy failure replay with the announcement repair
reaches gate zero and enemy winner 1 at 154.495 seconds, with Cassidy alive
at 225 health. The 0283 recap still displays Victory / 0.0. Read-only native
inspection shows mode winner 1 but no manager winner. The manager projection
now preserves the authored 180A:4 receiver inputs 7525 and optional 10186.
The prepared gate regression reproduces the missing input before the change
and passes afterward. Locked inspector workspace tests, all-target Clippy
and formatting pass. The second authorized client run reaches the failure
branch at 172.975 seconds, and the native manager contains integer winner 1.
Its recap remains Victory / 0.0, so the UI result is still not accepted.
The next investigation can use the captured 0283 variables; preserving the
manager input alone does not establish the recap's result/time dependency.
Python comparison remains pinned to cc620be. Captures remain local.


The post-match screen projection now selects entity inputs from each included
104319 graph's sync declarations. The prepared gate regression reproduces the
missing 0283 entity input 10274 before the change and passes afterward; the
first-delta baseline is empty for entity inputs. Inspector workspace tests,
all-target Clippy and formatting pass. This establishes the projection repair,
not the result heading or match-time dependency. Original-client acceptance is
still open: the authorized failure replay reaches enemy winner 1 at 162.511
seconds and Cassidy survives at 225 health, but the recap stays Victory / 0.0.
The full boss encounter, voices, intros, win line and gallery stay open.
Python comparison remains pinned to cc620be.

### Full-length original-client encounter checkpoint (2026-11-12)

The isolated Normal/Cassidy high-gate-health fixture passes the first Reaper
spawn at elapsed 199.775 seconds without a client crash. It reaches the final
boss objective and remains in phase three at 919.951 seconds, with clock zero,
no winner and gate ratio 0.9999904. Original-client observations show the final
bosses, objective HUD and Cassidy at 225 health. Deadeye activation is visible,
but its firing window is missed; subsequent absolute mouse injection produces
unexpected view changes. This is stability and encounter-progression evidence,
not successful boss cleanup or a win-line pass. Both owned processes stop;
the server exits zero. Captures remain local. Voices and one-time boss intros
are not accepted from this run.

The local 104319 UX exports identify recap input 10274 as IsPVEMode. The shared
UX dictionaries contain separate MatchTime and MatchResult bindings; their
native data sources are not established. Preserve the authored-input repair
without treating this PvE flag as a winner fix. Python reference: cc620be.

### Common-NPC mover inputs and first authored Reaper replay (2026-11-12)

Prepared runtime enrichment now imports the explicit NPC catalog's character
movers from authorized 104319 XML, using the field/default mapping pinned to
`fd09a36f`. The three records match all 34 fields in the reference extractor:
Zomnic forward 3.0 m/s, Shock-Tire 12.0 m/s and Zombardier 4.25 m/s. All 43
preexisting rows remain unchanged. Missing common-NPC movers reject a
Junkenstein scene instead of silently falling back to Soldier.

Gate approach resolves the body's numeric SPEED modifier. Shared modifier
values publish at state activation, dependency changes or authored polling,
then retire at cleanup; property reads do not resample random expressions.
This follows Python `e1128509` NumericPropertyModifiers and ObjectiveApproach.
A synthetic replay covers random stability, dependency refresh, polling,
retirement and reentry. The Normal street replay checks actual velocity
against the modifier consumed before the command, plus route/contact/death.
Boss pursuit and balcony exit are not newly scaled by this change.

The first-Reaper replay now uses the Normal encounter scheduler, living
external defender and room ticks. It retires common attackers through normal
damage to keep the gate alive, waits for the authored boss spawn, then checks
movement away from the intro position and damage to the defender. This is
headless evidence; native camera playback, once-only visible intros, boss
movement/damage acceptance and the complete success/failure lines remain open.

### Cassidy Flashbang motion bridge (2026-11-12)

The Rust owner bridge previously discarded any volley with projectile motion
and also rejected a zero-damage direct definition even when splash was valid.
It now admits only Cassidy 01E9/7's known single unconditional gravity path,
uses the existing predicted lease and room-flight lifecycle, and preserves
motion through admission. Speed 30, gravity 20, reach 7, launch bias 0.05,
blast radius 3 and damage 25 come from the external authored graph. Float32
launch normalization follows Python e1128509; its Flashbang expiry detonation
policy remains a scoped reconstruction. Other projectiles keep their expiry
behavior. Synthetic coverage includes splash at expiry, radius/occlusion,
once-only completion and motion retained across owner-flight admission.

The prepared Normal E-input replay emits exactly one projectile across 80
commands, resolves off-axis range-end damage and encodes the complete owner
snapshot. This does not cover stun, interruption, recovery, control-effect
replication or original-client presentation. Those are still static gaps;
the next client session must wait for all user-listed static gaps to close.

### Flashbang NPC input control (2026-11-12)

Effect 0079's 0228/7 wrapper supplies the 224E duration override through the
existing expression evaluator, including mode variable 13408. The owner
projectile and room flight retain the validated tick duration alongside the
existing generation-owned token. Surviving impacts apply an exclusive NPC
control deadline; repeated hits extend, and replacements start without it.

The shared script component releases logical buttons before blocking input,
stops active abilities using authored exit/cooldown paths, clears buffered
presses/shots and continues passive timers. Enemy target selection, route
movement and weapon emission honor the deadline. Patrols preserve their route
and resume only unpaused ticks, including skipped updates. Synthetic regressions
cover those invariants and lethal/unaffected projectile targets.

This follows Python e1128509's scoped Flashbang control reconstruction rather
than claiming a complete port of 224E. Full effect-state delivery, immunity
branches, native hit-reaction animation and control flags still require static
implementation/verification before the next original-client session.

The prepared duration-expression check validates both mode branches as 50 and
107 ticks, and Normal E-input still emits the authored control payload.
The first-Reaper encounter replay injects a 50-tick control window after his
first attack, observes stationary/no-damage ticks, then another attack after
expiry. It uses the ordinary encounter spawn and room/script advancement;
the control injection itself is separate from the owner E/projectile-impact
checks. This establishes scoped server recovery, not native effect parity.

### Inline PlayScript effect resources and policies (2026-11-12)

The shared expression evaluator now constructs inline graph-with-overrides
maps through the existing bounded native map insertion path. Native low-32
key replacement and bucket order match Python e1128509's resource resolver.
The action prefers this resource to the direct fallback and follows its
output for an empty resource. Loaded policies 0/1/2/3 skip, create, reuse the
first match or replace all matches. Reuse preserves allocation source and
lifetime flags, re-evaluates defaults against old callee values, then queues
schema/override writes before start inputs. Ordinary init entries run only
on new allocation. Once-only start entries remain disabled on reuse and
are retired with their allocation; a new allocation starts them afresh.

Synthetic checks cover those paths, malformed input, retained identity,
empty output and self-replacement. The selected Python resource/schema/queue
suite passes 31 tests. A broader 65-test Python run has two storage-export
errors from missing local storage inputs; it is not claimed as a full pass.

The prepared raw 0228 effect wrapper allocates 224E and supplies variable
215 (duration), 27606 (identifier), 9292 (loadout), 28383 (effect), and its
allocation source. The follow-up below supersedes the initial unsupported
224E/171 diagnostic. No original-client acceptance is claimed.

### Deferred effect execution and ordered array removal (2026-11-12)

The shared VM now executes STU_9C9A422A after ordinary events and state ticks.
Each instance swaps out its deferred batch, delivers highest priority first
and latest registration first for ties, then repeats the event/tick phase
before delivering newly registered work. State exit removes future queued
callbacks and exits the scoped subgraph; instance destruction removes its
queue. The existing work and event budgets bound requeue cycles. This
matches Python e1128509 `nodes.Defer` and `Instance.run_deferred`; no broader
native activation-counter guarantee is inferred from that reference.

STU_DA54F8F7 removes an evaluated array index while preserving order, writes
through shared variable notifications, and follows its output for absent,
nonarray or out-of-range inputs. The primitive follows native 104319
1B6C00B/D56020 as described by Python e1128509; the Python handler is scoped
to 0C8C/305, while the same authored primitive is needed at 224E/176.

Synthetic regressions cover phase order, stable priority ties, cancellation
before and during delivery, subgraph cleanup, repeated registration budgets
and array bounds/order. An independent Python replay produces the same
three cancellation traces (93214, 9324, 93214) and child-active results
(true, false, false).

The prepared effect test now uses the actual EnemyWorld adapter and Reaper
body roots, rather than a mode-only adapter lacking entity filter facts. Its
0228/224E startup and 100 subsequent frames have no diagnostics; deferred
state 64 completes, its pending array is absent or empty, and remote-schema
state 71 is retained. The body filter follows the current reference facts and does
not enter the filter's bit-0x40 branch. This is schema/queue/filter-path
coverage, not proof of an accepted stun animation, immunity, full effect
lifetime or native control projection. Those remain open static work; no
new client session is started before the complete user checklist is ready.

### Cassidy fire and the encounter death-input gap (2026-11-12)

The Normal secondary trigger reached its held LogicalButton state but never
fired because 01D2/93 read missing ruleset 290C as false. The player adapter
now supplies 290C=true from 104319 Cassidy schema 0026 (Python e1128509).
The primary 01D2/31 hit config leaves its critical multiplier null. Rust now
accepts a caller-provided implicit multiplier, applying the scoped 2x policy
from `mccree.critical_default`; all explicit authored values remain dominant.
The generic resolver retains 1x. This primary fallback is a declared Python
reconstruction and does not establish a global native default.

The new opt-in `normal_cassidy_primary_and_fan_damage_all_boss_bodies` runs
five actual Boss body/pool definitions through three stationary street
scenarios each: primary body, primary head and fan head. Normal owner input
and ammo, authored damage, scene collision, room hit admission, authoritative
pools, hit/elimination notices and component-51 pool blobs are asserted
together. All 15 cases reach the final retirement check: primary damage is
70/140; fan damage is 50, noncritical, six rounds; subsequent ordinary primary
fire kills each body. Unacknowledged target identities and already-dead bodies
produce no hit. The test does not step enemy AI concurrently with owner
commands, and uses the shared approximate head-height rule. It does not
prove pursuit, camera, live target geometry or original-client feedback.

The same probe FAILS its complete exit condition: all 15 corpses remain.
Previously `damage_bot` never delivered the authoritative 003E DIED input.
First combat deaths now queue that message with killer/critical/loadout/
assists, timed to the room frame (or the current script clock if that body's
same-frame update already ran). Further hits are blocked by the room dead
flag, and ordinary training-bot respawns retain their existing path.

Delivering the previously omitted input exposes actual missing VM/adapter
paths rather than accepting a health-zero result: 0075/33 engine setting
CF4A60, 01CA/118 unkeyed unique array insertion, 05AC and 01A3 killer-relative
filters, 01D8/98 killer velocity, and 0CCD's tagged live combat target. Witch
also reads foreign body health and healing targets. The probe supplies a
living external shooter, mode difficulty, wave script and scene anchors;
its roster still lacks some foreign metadata. Audit those facts against the
reference before classifying each diagnostic or supplying a default.

Locked offline workspace/all-target checks and the three prepared Normal
skill checks pass; the Python gun comparison passes 16 subcases with the
external runtime pack. The strict complete probe remains red, is opt-in,
and must pass after the death services are restored. No client is started,
no gameplay/presentation checklist item is closed, and this is not a ready
cutover or deployment candidate.

### Shared unkeyed array insertion (2026-11-12)

The graph/node-specific staff and spawned-body append paths now use the same
bounded action implementation as the death effect at 01CA/118. STU_4C3509FB
merges provider rows in order and recursively flattens arrays; STU_CBE8BDAA
inserts one typed value, retaining nested arrays and a computed null value.
An existing nonarray destination becomes an empty array first. In-range
indices insert without replacement, end/negative indices append, and gaps
contain int zero. The index uses float32 half-away conversion with native
INT_MIN for nonfinite or out-of-range floats, independently of general ToInt.

Uniqueness uses shared native value equality, including rows added earlier
in the operation. Provider groups are evaluated incrementally; later configs
observe earlier rows before one final watcher notification. A nonempty
self iterator terminates the entire merge. Direct dynamic outputs select
the real instance/entity storage. Missing value/output, zero identifiers,
known scalar constant outputs and invalid keys skip safely. Valid keyed
storage and linked writable checkpoints remain explicit boundaries, matching
the pinned Python strict implementation rather than inventing storage.
Traversal, equality scans, shifts and publication copies spend shared work;
value depth is bounded and the native 200-row diagnostic is retained before
or after mutation as appropriate. Partial diagnostics do not produce a
supported initialization snapshot.

The known empty-key STU_7BD2CA19 base evaluator retains the caller's initial
value. Explicit host admission still takes precedence, including the queue's
0C8C/263 difficulty value. Merge iteration omits a failed provider, scalar
index/key evaluation retains -1/0, and single-value insertion uses int zero.
Focused regressions cover those interfaces, both scopes, flattening and
single arrays, typed equality, gaps/order, skipped providers, self iteration,
limits and host priority. Python e1128509's array suite passes 27 tests.

The old all-card HUD test also fails unchanged at 9c0935be: its fixed five-
graph creation order predates the notification graph. The corrected test
decodes every descriptor, checks the actual snapshot/parent bindings while
retaining the distinct client common UI root, and requires 0CB6, 0C8C, 0CBB,
03F2, 180A and 1188. All card difficulties/challenges now pass this check,
alongside prepared enemy observer/healing and Normal skill regressions.
This is static descriptor coverage, not proof of rendered UI.

The complete five-Boss/15-case fire/death probe still FAILS: all cases reach
its final retirement check, with no corpse retirement. The previous
STU_4C3509FB diagnostic at 01CA/118 is absent. Remaining observed classes
are A753DC79 (engine settings), AC829876 (killer-relative filtering),
309B0570 (killer velocity), 9B7648C3 (foreign health), and B3E67A36 (tagged
combat-target queries). Restore those services and re-run the complete
probe; do not weaken its exit assertion or start a new client session.

### Wire-foundation integration and shared array fixture (2026-11-12)

Public Rust 3d82bc20 is merged into the integration branch with protocol
modules re-exported from `orpheus-wire`. The only textual conflict was the
additive changelog, resolved by keeping both histories. Default and inspector
workspace/all-target tests and Clippy pass on local Rust 1.99.1. This does
not establish the new Rust 1.85 CI lane, portable Linux or hosted CI results.
Post-merge prepared all-card HUD, enemy observer/healing and Normal skill
checks also pass; the full 15-case Boss death/retirement check remains red.

The new shared behavior-contract workflow is backfilled for insertion with
`fixtures/behavior/statescript-array-insert-v1.json`: 19 independently stored
synthetic cases run through the normal Rust VM and Python e1128509 action
handler against the same bytes. This includes failed native defaults and
incremental/self providers. `scripts/check_array_insert_contract.py` checks
the supplied Python checkout's exact revision and requires no runtime pack.
The Rust test is registered in Windows CI with an explicit inventory check.
The pinned local comparison source is not a public-main ancestor at
3a7e0424, and a reviewed public Python counterpart/CI registration is still
pending. These are local cross-language fixture results, not hosted CI or
original-client acceptance. Keep every remaining gameplay requirement open.

### Typed engine-setting service (2026-11-12)

A753DC79 dispatches its integer m_74A2248F key as uint32 to the host's
CF4A60 setting getter. The shared evaluator now distinguishes three states:
an unavailable service remains unsupported (unless an explicit legacy host
callback supplies a result); an available table without that hash retains
the caller's destination; a present typed result is returned as-is, including
false or null. Malformed key types and unsupported operand shapes retain
strict diagnostics. A signed hash is not a request for a negative-key table.

The encounter, gate, enemy and owner adapters share the captured headless
Junkenstein table from Python e1128509: 0FE1E03E is bool false, E1C1E402 is
int 128, and 6CE29BAB is float 1.0. Owner/enemy availability is limited to the
encounter modes. Other keys are missing rows, not invented bool defaults.
These values and no-write semantics follow the pinned reconstruction; no
arbitrary engine-preference or original-client-table coverage is claimed.

`statescript-engine-setting-v1.json` stores 14 synthetic input/results.
The Rust production evaluator and Python SceneWorld/Instance evaluator
consume the same bytes. Tests cover value types, missing/unavailable tables,
null results, signed/wide wrapping and invalid bool/float hashes. A separate
VM regression changes host values after startup and checks polled branch
entry/exit, including a missing row after a successful read. Owner-mode and
all-card A753DC79 diagnostic checks cover adapter integration. The Windows
workflow explicitly inventories and runs both StateScript contracts; the
Python consumers remain locally pinned comparisons with the public-ref/CI
follow-up recorded in BEHAVIOR_CONTRACTS.md.

Default and inspector locked workspace/all-target tests and Clippy pass on
local Rust 1.99.1; both shared contracts are explicitly inventoried and run.
No Rust 1.85, hosted-CI or portable-Linux result is inferred from these checks.
All-card preparation/HUD, enemy observer/healing and three Normal owner skill
replays pass. The complete 15-case Boss fire/death probe still FAILS its
retirement condition, while A753DC79 is absent from its diagnostics. The four
remaining observed classes are AC829876 (killer-relative filtering),
309B0570 (killer velocity), 9B7648C3 (foreign health), and B3E67A36 (tagged
combat-target queries). The full static gate and original-client acceptance
remain open. No new client session is started.

### Strict World-query integration checkpoint (2026-11-12)

Public Rust 437a6eb4 is integrated with the local engine-setting service,
authored maps, array insertion, deferred VM behavior and encounter adapters.
Unknown entities now retain unavailable position/member diagnostics instead
of synthetic origin/unset results. The explicit transient defender sighting
position remains available. GateWorld has no position backend and declares
that absence; it does not recover an actual gate model/transform service.

Default/inspector locked offline workspace/all-target tests and Clippy pass
on Rust 1.99.1 (346/350 server-library tests, 23 wire tests, strict World
query contract; 112 server/2 wire checks remain ignored). Both local Python
contract comparisons still pass. This is no hosted CI, MSRV or client claim.

All-card preparation, Flashbang and Roll pass. The post-merge Deadeye probe
is RED at foreign target member 8243; enemy observer/healing is RED at Witch
ally member 6604. The complete 15-case Boss probe still reaches the damage
checks but fails all retirement cases; it now also reports unavailable killer
member 4437, alongside the four previously recorded unsupported classes.
These adapter gaps require actual foreign body state delivery and a shared
contract. Earlier pre-merge passes above must not be used as current
acceptance. Keep the entire static gate open and do not begin live testing.

### Foreign body variable snapshots (2026-11-12)

The encounter roster now carries explicit script-component state. A normal
NPC publishes its real body snapshot; an NPC fault or missing snapshot is
unavailable. A basic room bot with no server script component declares that
absence. Human defenders publish their actual owner snapshot through existing
room delivery. Deadeye's target scene carries the room body's component state.
Enemy/player adapters read typed foreign variables from these facts, while
keeping unknown entities unavailable and preserving the mode-variable path.
No variable-ID allowlist or unconditional null fallback is added.

Foreign member reads register tick polling for persistent expressions, so a
replaced room snapshot can change an active branch. This is a deterministic
frame-boundary projection, not Python's shared mutable variable slot: exact
same-frame foreign watcher notification/order remains a separate parity gap.
Foreign controllers and actors outside the delivered roster remain unavailable.

The ten-case shared contract agrees with pinned Python e1128509 through actual
SceneWorld component bindings. VM polling covers false/true/false/true and
explicit component removal. Prepared Deadeye and enemy observer/Witch-healing
replays pass again; the strict Boss fire/death probe no longer reports foreign
killer member 4437. It still fails all 15 retirement cases at AC829876,
309B0570, 9B7648C3 and B3E67A36. This restores variable reads only, not all
foreign health, velocity, tags or killer-relative filters. Original-client
acceptance and the complete static gate remain open.

Prepared all-card, Flashbang, Roll and Zombardier targeting/projectile checks
also pass after this change. The broader first-Reaper replay remains RED
before its acceptance point: Tire death at tick 7746 reaches 523EE38D at
0C93/11, which the Rust dispatcher does not admit (Python e1128509 does).
No current first-Reaper intro/pursuit acceptance is inferred from that run.
The complete static gate also requires this corpse-retirement entry point.

### Corpse-retirement entry points and first-Reaper replay (2026-11-12)

The 523EE38D dispatcher now admits the pinned reference's 0C93/11 Tire
cleanup and 0CBC/36,38 death entries, in addition to 0C89/43 and 0CBC/57,58.
The VM passes an explicit phase-exit flag to its host; only 0CBC/57,58 set it.
EnemyWorld still requires the calling body and its own body/controller target,
and an actual death outside phase exit. Authored delays and the normal room
retirement queue remain authoritative. Other specialized 523EE38D forms
(temporary weapons, tagged helpers, vengeful ghosts) are not covered here.

The 12-case language-neutral fixture compares real entry/action dispatch in
Rust and Python e1128509 with a declared host response. It checks each admitted
site, body/controller targets, phase-exit flags, host refusal, invalid targets
and an unknown site. This fixture proves routing and failure/continuation;
it does not replace actual paired-actor lifetime verification.

The complete Normal first-Reaper prepared replay now passes (194.42 seconds
wall time locally). Common attackers are killed through normal damage/death
to isolate the boss handoff. Assertions require the first Reaper to leave
its authored intro point, attack a living defender, stop both movement/fire
under control, and resume attack after control. This establishes that replay's
AI/handoff scope only: it proves neither original-client camera/audio/once-only
presentation nor all bosses or full encounter victory/failure.

Default/inspector locked offline workspace/all-target tests and Clippy pass
on Rust 1.99.1 (349/353 server-library and 23 wire tests; 112 server/2 wire
checks remain ignored). Both feature lanes inventory and exactly run the
new action contract. Prepared Zomnic gate damage/paired retirement passes.
The complete 15-case Boss probe still fails every retirement at the four
previously recorded query services, with no foreign-variable diagnostic.
No MSRV, hosted-CI or original-client result is inferred.

### Public room-isolation integration (2026-11-12)

Public Rust f763beb9 is integrated without discarding the encounter helpers
or the public fault-isolation tests. Room-owned member access now carries
NPC creates/retirements, scripted health, defender state/sightings, enemy
player damage, narration/voices and result-screen exit. The new guarded
projectile/finish phases propagate their typed/IO errors. Combat's checked
actor iterator is used by encounter rosters, projections, healing and living
target queries. Deadeye preparation is fallible through the owner interface;
no empty target scene is substituted for a failed registry query.

The encrypted three-card entrance regression now verifies the complete
prepared HUD baseline rather than the obsolete frozen 112-bit prefix. It
identifies countdown deltas by their authored variable, preserving exact
retransmission and value assertions while allowing earlier HUD notification
updates. The all-card test independently decodes descriptor/parent structure.
These are wire checks, not visual/UI acceptance.

Local default/inspector locked workspace/all-target tests pass (364/368
server-library tests, 114 ignored; wire 23 pass/2 ignored). On the existing
base runtime, prepared fault injection keeps the other room's world, input,
hits and patrol alive; stock Soldier rifle, Helix and Biotic Field encrypted
replays pass. Revenge, Endless and three-player gate/mode/countdown delivery
also pass on the encounter runtime. The encounter-only pack lacks Practice
Range collision, so that recovery test was rerun against the existing base
pack instead of changing its prerequisite or assertions. No data pack is
copied into the source checkout or committed.

After integration, all-card preparation, Cassidy Flashbang/Roll/Deadeye,
enemy observer/Witch healing and Zombardier target/projectile replays pass.
The complete first-Reaper handoff/pursuit/attack/control-recovery replay passes
again (172.09 seconds wall time). The strict 15-case Boss fire/death probe is
still RED at AC829876/309B0570/9B7648C3/B3E67A36, with no foreign-variable
error. Final default tests and both Clippy lanes pass, as do formatting/diff
checks. Local Rust is 1.99.1; MSRV, hosted CI, Linux and original-client
acceptance are not established here. Keep the complete static gate open.

### Mover velocity reads and death membership branches (2026-11-12)

309B0570 now has a shared typed velocity query. A declared unbound result
retains the supplied initial value; an unavailable host remains strict (with
an explicit legacy callback available on the supported selector path). A
present velocity must be finite both before and after float32 conversion.
Only the default null 5B94EDB8 mover path is admitted. The native scalar
selector reads a group's first config and preserves a no-output destination,
within the existing expression depth/work bounds.

Room NPC rosters publish their real bot velocity and human defenders publish
the actual mover pose velocity. Owner bodies expose their sampled movement;
Deadeye targets carry the room body's velocity. The old Tire-self and Roll
velocity callbacks are removed in favor of the shared mechanism; Roll's
forward/right/aim callbacks remain scoped. Non-delivered actors/controllers
and a missing synthetic reader remain unavailable, rather than guessed zero.
Only null entity 0 is declared unbound by these partial adapters; this does
not claim complete room actor discovery or retired-entity lookup coverage.
Snapshots retain the previously documented frame-boundary ordering gap.

The 19-case velocity contract agrees with Python e1128509, including typed
no-write results, nonentity/array selectors, float32 conversion and invalid
vectors. A VM switch regression observes changing mover values and explicit
unbound state; depth/work and legacy-boundary checks also pass. After the
velocity query succeeds, the complete Boss probe reaches the previously
unsupported 821116A6 contains condition. Its shared handler now reuses VM
membership equality and normal true/false routing; a separate 16-case
contract agrees with the reference. Both Rust consumers are registered in
Windows CI; these local comparisons establish no hosted-CI/client evidence.

The complete 15-case Boss probe still fails every retirement, but neither
309B0570 nor 821116A6 remains in its diagnostics. Current remaining classes
are AC829876, 9B7648C3 and B3E67A36 (killer-relative filters, foreign health,
and tagged combat-target queries). No gameplay or original-client checklist
item is closed by these query/condition fixtures.

Default/inspector locked offline workspace/all-target tests and Clippy pass
on Rust 1.99.1 (368/372 server-library tests, 114 ignored; wire 23/2 ignored).
Both new contracts are inventoried and exactly run in each feature lane;
formatting/diff checks pass. Prepared all-card, Cassidy Flashbang/Roll/Deadeye,
enemy observer/Witch healing, Zombardier and complete first-Reaper replays
pass. On the existing base pack, Soldier body/weapon-tree plus encrypted
rifle/Helix/Biotic Field regressions also pass. No MSRV, Linux, hosted-CI,
all-boss or original-client acceptance follows from these results.

### Authoritative total-health lookups (2026-11-12)

9B7648C3 now uses an explicit total-health interface with present, unbound
and unavailable results. It preserves the caller's initial value for a
no-write result and keeps missing readers unsupported. Values must satisfy
the pinned reference's non-negative 3.402823466e38 cap before float32
conversion. The existing normal/current/maximum pool query classes retain
their separate paths. Native first-config scalar selection is shared with
velocity reads; existing body-health contexts remain compatible through the
default adapter.

Enemy body/controller components can query their own body's actual vitals
and delivered roster totals. Player bodies use their own vitals and real
room-body health snapshots. Deadeye's health table includes still-bound dead
and friendly bodies, separately from the living enemy list used to select
shots; killing a target does not make its zero health an unavailable reader.
Room invariant errors propagate through fallible snapshot construction.
Undelivered/retired identities still have the documented partial-discovery
limit; health is never guessed from alive/team flags. Facts remain captured
at frame boundaries rather than sharing Python's mutable foreign slots.

Twenty-one shared synthetic cases agree with Python e1128509 through normal
query dispatch. Exact f64 boundary bit patterns preserve the extreme range
checks without relaxing equality for ordinary results. A VM regression polls
health/death/unbinding, and a room regression distinguishes corpse health
from live target selection and subsequent retirement. The corrupted-index
regression also covers total-health snapshot creation.

The complete Boss fire/death probe still fails all 15 retirements; 9B7648C3
is gone from its diagnostics. Only AC829876 (killer-relative filters) and
B3E67A36 (tagged-target selection) remain observed in that probe. Keep the
full static and original-client gates open rather than inferring success
from query availability or health/component damage assertions alone.

Final default/inspector locked offline workspace/all-target tests and Clippy
pass on Rust 1.99.1 (371/375 server-library tests, 114 ignored; wire 23 pass/
2 ignored). Contract inventory/exact runs and formatting/diff checks pass.
Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch
healing, Zombardier and complete first-Reaper replays pass (166.71 seconds
wall time for Reaper). Base-pack Soldier body/weapon-tree and encrypted
rifle/Helix/Biotic Field regressions pass. The final Boss probe still has
15 failed retirements and only AC829876/B3E67A36 diagnostics. These results
supply no MSRV, Linux, hosted-CI or original-client acceptance.

### Delivered target facts and combat selection (2026-11-12)

Room actor facts now include the owner's real filter bits and catalog/effect
target tags, and NPCs export their body filters and immutable catalog/effect
snapshots. Unknown catalog inputs remain unavailable. Grant/suppression
lifetimes are preserved across snapshots; team/name guesses do not supply tags.

The enemy host admits the pinned Python e1128509 scene's killer-relative
filters only in 05AC/01A3/157C with selector 2 and known supported operands.
The shared evaluator uses first-set team bits, literal required/excluded tags
and 01A3's scalar death predicate. The separate ordinary filter layout remains
unchanged. 0CCD's tagged combat-body query uses the delivered roster's order,
actual masks/tags, strict unknowns and bounded work. Its supported subset has
literal primary tags and empty auxiliary tags; Witch ally queries and beam
parameter predicates are not covered.

Seventeen hostile-filter and twelve combat-target synthetic cases run through
production Rust evaluation and the pinned Python scene. Rust CI inventory/exact
commands register both tests; the local Python consumer does not establish
hosted differential CI. The complete Boss probe still fails all 15 retirements:
AC829876 is gone and 0CCD query branches advance to unsupported 40E05F6B
states; Witch 0071 B3E67A36 queries also remain. No client was started and no
UI, audio, intro, Hero Gallery or win/loss acceptance item is closed.

Final default/inspector locked offline workspace/all-target tests and Clippy
pass on local Rust 1.99.1 (374/378 server-library tests, 114 ignored; wire
23 pass/2 ignored). Both new contracts pass inventory/exact runs in both
lanes. Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch
healing, Zombardier and complete first-Reaper replays pass (179.29 seconds
for Reaper). The synthetic Summoner target explicitly declares known filter/
empty-tag facts; missing production facts stay strict. These results do not
supply MSRV, Linux, hosted-CI or original-client acceptance.

Base-pack Soldier body/weapon-tree and encrypted rifle/Helix/Biotic Field
regressions also pass. Formatting/diff checks pass. Public rust f763beb9 is
already integrated; fresh ancestry/merge-tree checks pass without a push.

### Witch friendly filter and combat queries (2026-11-12)

The scene host now admits friendly-other filters only in 006B/0071 with
selector 1 and relation mask 4. The evaluator uses actual filter/tag facts,
first-set team-bit decoding and source exclusion. It follows authored masks;
a dead actor is not implicitly removed when the filter omits the dead bit.
Friendly predicates see the candidate in lexical parameter 8, with the
reference's true scalar initial value. Nested scopes restore the outer
candidate and original caller parameters after success or failure. The
separate hostile scalar predicate retains its null initial value.

0071's B3E67A36 search uses the ordered bound combat roster, supported literal
filter tags and friendly predicates. Host admission distinguishes its null
outer tags from 0CCD's literal primary-tag query. Nested predicate failures
retain their original diagnostic class. Twenty-eight shared synthetic cases
agree locally with pinned Python e1128509 through normal scene dispatch;
Rust CI inventories and executes the contract in both feature lanes.

The final complete Boss probe now reports only unsupported 40E05F6B voice
states: B3E67A36 is gone, but all 15 retirement cases remain RED. Beam Map
parameter predicates and full shared voice services are separate work. Full
Witch AI, gameplay, win/loss and original-client gates remain open; the user's
static-first requirement remains in force.

Default/inspector locked offline workspace/all-target tests and Clippy pass
on local Rust 1.99.1 (375/379 server-library tests, 114 ignored; wire 23 pass/
2 ignored). Explicit inventory/exact runs cover ally, hostile and tagged
combat-target contracts in both lanes. Python comparisons pass all 57 cases.
Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch
healing and Zombardier replays pass. The final full Boss probe remains RED
at 40E05F6B only. No MSRV/Linux/hosted-CI/client acceptance is supplied.

Base-pack Soldier body/weapon-tree and encrypted rifle/Helix/Biotic Field
regressions pass, with formatting/diff checks. Public rust f763beb9 remains
integrated; fresh ancestry/merge-tree checks pass without a push.

### Strict generic voice-state lifecycle (2026-11-12)

40E05F6B now has an explicit generic voice state, typed host services and
independent +80 polling after ordinary actor updates. Begin performs the
manager lookup even when that manager is known absent, then reads external
clock/delay/gap and registers. Poll preserves resource/permission/submission
failures, strict timeout comparison, six duration attempts, float32 timing,
byte-sized part advancement and next/previous-part retry rules. Its immediate
queued timers publish duration and finished output separately from eventual
state completion. End follows only the end plug; explicit failure notification
follows its own plug without automatically finishing. Abort, failed service
and destroy unregister generic voice callbacks and remove stale voice events.

Cached/keyed/null stimulus selection and first-asset coercion use the normal
expression path. The VM shares scalar selection and watcher/diagnostic handling
with other queries. Ordered group/value-array targets retain type, order and
uniqueness; native iterator services remain strict or separate work.

Thirty-eight language-neutral synthetic scenarios agree with pinned Python
e1128509 through actual VM/Context operations. Expected service traces and
typed state/output observations are stored independently, with exact f64
bits. The Rust CI matrix registers inventory/exact runs; local Python
agreement does not establish hosted differential CI or original-client audio.

The enemy World's Boss speech-resource/line-selection backend is still
unavailable. Existing intro/gate/victory specializations retain their earlier
behavior pending migration to shared services. Broader component fault-policy
and callback-mutated external binding equivalence are not established by this
fixture. Full Boss death retirement, successful/failed matches, narration,
ally/enemy sound and the rest of the original-client checklist remain open.

Generic voice caches are allocated only for voice states; inactive ordinary
states keep no voice payload allocation. Room candidate selection is read-only
and preserves checked-registry errors before mutating actors. Empty voice
phases allocate no candidate rows and do not acquire mutable actor references.
The same shared lifecycle traces cover the storage change. Wall time from a
single prepared replay is not throughput or original-client performance
acceptance; those reliability checks remain separate.

Final locked offline workspace/all-target tests and Clippy pass on local
Rust 1.99.1 with default/inspector (376/380 server-library tests, 114 ignored;
wire 23 pass/2 ignored). The new contract passes inventory/exact checks in
both lanes and pinned Python comparison over 38 scenarios/164 observations.
The Python runner rejects tracked modifications to the pinned script package.
Prepared all-card, Cassidy Flashbang/Roll/Deadeye, enemy observer/Witch
healing and Zombardier replays pass. The final full first-Reaper replay
passes in 235.48 seconds; this is not a throughput benchmark. Base-pack
Soldier body/weapon-tree and encrypted rifle/Helix/Biotic Field regressions
pass. Formatting/diff checks pass. No MSRV/Linux/hosted-CI evidence is added.

The final complete Boss probe remains RED for all 15 retirements, each at
an unavailable voice context. No unsupported-state/query class remains
observed in that probe. Providing actual Boss line/resource services remains
required; an absent context or inert bark would not establish audio fidelity.
Current public rust f763beb9 is already integrated; fresh ancestry/merge-tree
checks pass without a push. No original client was launched.


The Boss voice backend now supplies actual resources, factory/mode voice-set
facts, weighted killer-specific defeat selection, raw sample durations and
room notice requests. The 191-case policy contract agrees with pinned Python
at explicit random draws. The complete Cassidy primary/fan probe passes all
15 Boss death retirements and exactly one eligible defeat request per death.
This supersedes the earlier unavailable-context failure record; it is not
full encounter/audio acceptance. General live voice-set overrides, RNG seed
sequence parity and legacy intro/gate/victory migration remain open. Finish
all named static items and both match branches before launching the client.

Final backend validation passes locked offline workspace/all-target tests and
Clippy in both default/inspector lanes (377/381 server-library tests; wire
23 pass/2 ignored). Prepared all-card, Cassidy skills, enemy observer/Witch
healing, Zombardier, voice-wire and complete first-Reaper checks pass. The
latter takes 179.79 seconds, without a throughput claim. Base-pack Soldier
body/weapon and encrypted rifle/Helix/Biotic regressions pass. Pinned Python
checks both voice fixtures; MSRV/Linux/hosted-CI and client remain unverified.
Fresh public-rust ancestry and merge-tree checks remain clean; no push occurs.


The defender body now mounts the complete authored 0CA5 startup instead of
only its intro listener. Its newly exposed 00707E1E query uses explicit
controller body-field facts; 18 cases agree with pinned Python 46b22130.
E9485CAF uses the verified native parent/base State registration, and the
prepared Deadeye probe asserts active defender startup state. This restores
the team-script entry path, not damage-driven charge or score. The 60-second
passive helper and the remaining named static/live requirements stay open.

Final full-root checks pass default/inspector locked offline workspace/all-target
and Clippy (378/382 server-library tests, 114 ignored; wire 23 pass/2 ignored).
The new consumer passes both inventory/exact lanes and normal pinned Python
comparison over 18 queries. Prepared complete Boss15, Cassidy skills,
observer/Witch healing and three-card encrypted castle/gate/mode/selection
checks pass. Base Soldier body/weapon-tree and encrypted rifle/Helix/Biotic
checks pass. Existing voice lifecycle comparison, formatting/diff and fresh
public-rust ancestry/merge-tree checks pass. No client, push, MSRV/Linux or
hosted-CI result is added. Score, proper passive/damage charge and the full
success/failure runs remain required before original-client verification.


The 60-second Cassidy helper is removed. The real 0B8B graph now owns passive
waits/amounts and consumes applied room damage with fresh target facts, ordered
half/three-quarter reductions, live multipliers and cap. Hit tags survive
hitscan/direct/blast paths. Room simulation binds live-defender admission;
death/result revocation drains the same-clock change and keeps owner VM faults
local. This is explicit host policy, not full native game-state broadcast
parity. Forty-one synthetic cases agree with the normal pinned Python VM;
prepared Boss15/Deadeye probes check actual graph integration. Team score,
complete success/failure and the remaining static/client checklist stay open.

The same contract includes the pinned host eight-second common-corpse grace.
A prepared common-NPC probe verifies Zomnic/Zombardier retirement exactly at
500 frames, tire script retirement, net damage and target-reduced charge.
This closes ordinary common-corpse lifetime, not full encounter acceptance.

Final static validation passes locked offline workspace/all-target tests and
Clippy in default/inspector lanes (379/383 server-library tests, 115 ignored;
wire 23 pass/2 ignored), explicit contract inventory/exact checks, all 41
pinned Python cases, prepared common3/Boss15, complete Deadeye, Flashbang,
Roll, three-card encrypted entry, observer/Witch, Zombardier and base Soldier
body/rifle/Helix/Biotic probes. The final first-Reaper intro/pursuit/fire/stun
replay passes in 33.28 seconds; this is not a throughput claim. Existing
body18/voice38/Boss191 comparisons pass. No client, push, MSRV/Linux or
hosted-CI result is added.

The natural Normal loss replay now runs actual common waves, castle collision,
owner VM, gate and native result/post-match encoding without forcing gate damage
or removing attackers. The explicit test-only damage absorber preserves Cassidy
at 225 health; real enemy impacts total 2470.5412788391113 by tick 9666, the gate
fraction reaches zero, and the native winner is enemy team 1. It includes normal
defender message/sighting delivery. Only the three common NPC types spawn before
this early loss; all Boss/late-loss and original-client presentation requirements
remain open. The score investigation has not established a native PVEScore field
or reward formula, so no new score policy is claimed.

The natural-loss replay passes both default/inspector lanes with identical
tick/health/damage observations. Locked offline workspace/all-target tests
and Clippy pass (379/383 server-library tests, 116 ignored; wire 23 pass/2
ignored). Formatting/diff and fresh public-rust merge checks pass; no client,
push, MSRV/Linux or hosted-CI evidence is added.

Retired NPC body/controller identities now remain explicit room facts. Enemy
queries return Null/false for those identities while preserving unavailable
unknown/faulted backends. Eight cases agree with normal Python e1128509
unbind/slot/query services. This addresses the Witch's stale healing target
failure originally reproduced at tick 37117; a subsequent long-wave probe
kept scripts healthy through tick 66000 before being intentionally stopped
while investigating the separate missing late result transition.

The VM now retains foreign slot watchers and accepts coalesced room-frame
change notifications, removing subscriptions at state/instance teardown.
The gate publishes actual changed fields before the mode update. Three shared
SubScript lifecycle flows agree with normal pinned Python callbacks; exact
native callback timestamps and other foreign producers are not claimed.

The complete Normal late-defeat replay now passes: all five natural Boss
introductions finish, each camera's observer payload encodes exactly once,
Reaper/Roadhog move after introduction, Cassidy stays at 225 health despite
20503.89579885375 genuine enemy damage, and a new gate hit yields zero gate
health plus enemy winner/result/post-match payloads at tick 33208. Only the
test fixture changes gate capacity and absorbs player hits. No wave actor is
removed, frozen or force-killed. Actual camera/animation/view return, voices,
gate rendering, score, full Deadeye victory and Hero Gallery acceptance remain
open; no original client was started.

Final checks pass locked offline workspace/all-target tests and Clippy in
default/inspector lanes (381/385 server-library tests, 117 ignored; wire
23 pass/2 ignored), both new inventory/exact consumers in both lanes and
normal pinned Python comparisons. Default prepared common3/Boss15, early/late
defeat, observer/Witch, Zombardier, encrypted three-card entry, Cassidy skills
and base Soldier regressions pass. Existing foreign-component/charge source
comparisons, formatting/diff and public-rust ancestry/merge checks pass. No
client, push, MSRV/Linux or hosted-CI evidence is added.

The extended full-wave victory probe exposes 0CBC/19's attacker-team read
after actual retirement at tick 49050. Known retired identities now produce
signed native team -1 through the VM/host interface; unknown identities retain
unavailable outcomes. The same eight-case synthetic retirement contract agrees
with the actual Rust enemy adapter and normal pinned Python entity-team
context/lookup service. The pinned source's 21 entity-team tests pass. This
does not establish complete native team subscriptions or client acceptance.

The victory-view route restores scalar 0CEC/2 and /3 messages to defender
bodies. Previously their rejected parameters prevented /3 from reaching its
self-stop, leaving 0C8C/73 and main phase 4 alive after the outcome cue.
The prepared actual child/parent replay now advances to phase 5, with both
view messages and no diagnostics. A separate scoped client-only cue preserves
the pinned server forwarding behavior. Three synthetic cases agree with the
normal pinned Python route/parameter service and actual Rust VM; the public
workflow inventories/runs the consumer in both lanes. This does not establish
general array-parameter transport or original-client camera playback.

The prepared gate replay now checks intact/three damage poses, the health-zero
BooleanSwitch branch and full observer encoding at five health samples. The
authored switch keeps pose 0511 at zero; model rendering remains a client check.

The defender's 0CED view states now preserve the networked lifecycle when the
authoritative body host explicitly declares the auxiliary client owner absent.
Unknown/present ownership is unsupported. Six spatial-binding cases agree with
Python 46b22130; the E8D976BE early-return branch has separately inspected native
evidence and no Python class equivalence. The actual prepared Cassidy body now
accepts/encodes start, end and allied result messages without diagnostics.

The scoped Deadeye bridge now checks its emitted auto-aim ray through the real
target capsule entry distance. This repairs the exposed-head/covered-lower-body
balcony reproduction while retaining world occlusion. Six cases agree with
normal pinned Python collision kernels; the nearest-surface policy difference
is explicit. Normal Python Deadeye selection and native selection equivalence
are not claimed. The castle probe checks the actual spawn and moved poses.

View-message delivery now refreshes actual Boss tag/position/facing facts from
the room, including dead bodies that remain present. These use the existing
typed target-tag and transform services; unavailable/faulted bodies are excluded.
The prepared Cassidy receiver probe covers self and actual Roadhog subjects,
release/result processing and full owner encoding. The room's per-peer adapter
refreshes facts immediately before delivery; it does not extend corpse lifetime.

The full natural Normal Deadeye victory now passes at tick 54878. Two casts
using actual passive charge, real movement, FOV and collision terminate all
nine Boss body identities, including the balcony Summoner. All five authored
introductions encode once; Cassidy retains 225 health after absorbing
37002.70704588873 enemy damage in the fixture, and the high-capacity gate
survives through allied winner/result/post-match encoding. Early and late
gate-defeat replays remain green. Test-only invulnerability/gate capacity do
not change production damage or wave actors. Original-client acceptance and
the remaining static checklist, including score, stay open.

Final local checks pass locked offline workspace/all-target tests in default
and inspector lanes (384/388 server-library tests, 122 ignored; wire 23 pass/
2 ignored), both Clippy lanes, four exact/inventoried shared consumers and their
normal pinned Python comparisons. Nine castle prepared probes and four base
Soldier probes pass. No client, push, hosted-CI, Linux or MSRV claim is added.

The existing prepared Flashbang wrapper probe now covers all five Boss body
roots, their actual 0228/224E override/schema values, deferred queue completion,
100 diagnostic-free frames and full observer snapshot encoding. The isolated
host declares Normal difficulty plus empty combat/placement services; it does
not run the paired controller or prove the projectile-to-effect visual bridge.
Immunity branches and original-client reaction remain static/client gaps.

Admitted room Flashbang hits now queue the real 0228 wrapper followed by 000F
through ordinary enemy-pair delivery. Five synthetic Rust-only host-policy
cases cover scoping, normal VM execution and source identity. The separate
restricted physical-hit replay checks one 25-damage hit, actual 224E effect
state and observer encoding through 120 frames on each complete Boss pair.
Its projectile is synthetic; the E-input probe separately verifies authored
launch, gravity/range blast and early-input rejection/later-input recovery.

The longer E probe exposes generic ability press timestamps being refreshed on
every rejected start. Rust now retains the original time and consumes the press
only after gates pass. Four normal VM flows agree with pinned Python e1128509;
both public CI lanes inventory/run the consumer. The source's own PC/PS4
start-gate uncertainty remains explicit. Immunity and original-client/control
presentation remain open; these changes do not establish complete Flashbang
native equivalence or satisfy the user's full static-before-client gate.

Final local locked offline workspace/all-target tests and Clippy pass in both
lanes (386/390 server-library tests, 123 ignored; wire 23 pass/2 ignored). Both
new consumers have separate CI registrations and pass inventory/exact checks
in both builds; the pinned four-case Python comparison passes. Nine prepared
castle probes, including the unchanged full natural victory and late defeat,
and four base Soldier probes pass. Formatting/diff checks pass. No client,
Linux, MSRV, hosted-CI or push evidence is added by these checks.

The Junkenstein Flashbang timed-control adapter now honors actual 224E/122
entity-stack admission before starting a stun. Seven synthetic normal-VM cases
agree with Python e1128509, including higher-priority rejection and recovery
after release. The prepared wrapper probe directly starts Reaper 01C9/45's real
higher-priority scope; both the production adapter predicate and authored query
reject it, while ordinary five-Boss bodies are admitted. Its 01C9/65 base-State
child has inspected registration/codec evidence. This is not full Wraith input
or immunity acceptance.

The current ordinary hit probe retains 224E raw state71 (node191), its schema
carrier. It does not establish active hit-reaction animation/control nodes.
Classification/tag queries and the remaining body-variable guards, native
control presentation and original-client behavior remain open. No client can
start until these and the other user-listed static gaps are resolved.

Final local checks pass locked offline workspace/all-target tests and Clippy
in default/inspector lanes (387/391 server-library tests, 123 ignored; wire
23 pass/2 ignored). The new consumer is registered separately and passes both
inventory/exact lanes; seven normal pinned Python cases pass. Prepared
wrapper/Wraith-state, five-Boss room-hit and E-input probes pass in both builds,
with four base Soldier probes passing as well. Formatting/diff checks pass;
no original client, hosted-CI, Linux or MSRV acceptance is claimed.


### Authored team-score ingress and notification projection

Restricted Normal replays establish the eight NPC/Boss base-point branches:
Reaper 200; Junkenstein, Summoner, Roadhog and Witch 400 each; Zomnic and
Zombardier 10; Shock-Tire 40. Independent 0CBC tag branches supply expected
constants. The existing Cassidy damage/charge/retirement tests cover primary
body/head and fan for each Boss (15 cases), plus three common-enemy cases.
Actual death carriers are buffered during retirement, then returned to normal
scene delivery. 0CC7 accumulates real 06A4/39E and 06C8/3AF carriers with the
Normal 1.2 multiplier. Effective entity2499 bindings and the actual 1188 HUD
projection agree and produce encoded payloads; the null base slot is not the
score. Both default and inspector prepared matrices pass.

All three full natural outcome replays pass the additional score assertions in
the default build. Actual Deadeye victory earns 4121 plus 1799 gate bonus and
produces float32 score 7104.00048828125. Early and late gate defeats earn no
points and no gate bonus. The separate natural Zomnic regression waits for
listeners, uses normal room damage/death and earns 12; it retains exactly one
point event and score12 through real gate destruction, enemy native result and
post-match output. This earned-score defeat passes in both build lanes.

Reproduce with the authorized prepared 104319 castle runtime and ignored tests
`scene_assets::tests::normal_cassidy_common_npc_damage_charge_and_retirement`,
`scene_assets::tests::normal_cassidy_primary_and_fan_damage_all_boss_bodies`,
`game_combat::tests::normal_common_enemy_death_updates_authored_team_score` and
the three `game_combat::tests::normal_*` natural-ending replays. No direct score
assignment or substitute points message is used. These checks establish tested
Normal scoring and notification encoding, not the native PVEScore widget,
other difficulties or account high-score persistence. Mode TrackStat remains
unsupported. Original-client verification is deferred until the user's
remaining listed static requirements are complete.

### Native generic-filter tag-clause correction

Generic AC829876 now uses D0EC70/DB2FD0's three tag groups consistently:
9C7BD8B8 is any, B486603E is all, and 8B2A4DF3 is excluded. This applies with
or without a scoped relationship policy. The previous generic mapping could
require Flashbang's excluded tags and accept only one tag from an all group.
The unrelated C1903771 slot is no longer invented as a fourth tag group;
nonempty operands remain explicitly unsupported.

A 72-case synthetic fixture agrees with restricted original native predicate
execution and production Rust expression dispatch. It includes the eight
membership combinations for 224E/9's actual three exclusion tags. This is
native evidence; no generic pinned-Python parity or original-client claim is
made. Existing scoped ally/hostile fixtures, both-lane prepared damage/charge/
score and hit-dispatch matrices, and all three default full natural outcomes
pass after this correction. The separate default earned-score defeat also
retains score12 through enemy result/post-match output.
This corrects the tag stage only. Boss native category facts and the complete
Flashbang control/reaction/animation lifecycle remain open; client validation
is still deferred under the user's static-first order.

### Natural Reaper control from owner E input

`game_combat::tests::normal_encounter_first_reaper_finishes_intro_and_pursues_live_defender`
now uses Cassidy's actual owner VM instead of directly calling `stun_for`.
After natural intro completion, movement and an attack, normal player collision
movement brings Cassidy into grenade range. One E press emits the authored
projectile, admitted by the real owner lease service. Explicit assembly of its
authored gravity/expiry blast fields then uses normal room flight and hits.
Assertions cover one launch/impact, 25 actual NPC health loss, no movement or
attack for 50 ticks, expiry and resumed combat, one224E instance, and owner/NPC
observer encoding. No Boss damage amount, control duration or category mask is
assigned by the fixture.

This restricted replay keeps the existing common-enemy damage/death isolation
and test-only invulnerable external defender. It does not test transport/create
acknowledgments. The normal room timed-control bridge is exercised; complete
224E classification, native reaction animation and Wraith transitions are not
established by this replay. The separate priority-admission fixture retains its
own narrower scope. Client validation remains deferred under the user's order.
Both default and inspector prepared replays pass at the same ticks: E11616,
impact11627 and resumed attack11677. Workspace/all-target tests and Clippy pass
in both lanes. Production code is unchanged; no new full-outcome, hosted-CI,
Linux, MSRV or original-client result is claimed for this test-only extension.

### Flashbang Boolean entry admission

The room timed-control bridge now evaluates actual224E/4 before the existing
224E/122 priority guard. Entity inputs come from effective Component bindings;
instance inputs use the effect's public-schema default after confirming0228/7
does not override them. This prevents an earlier rejecting condition from being
bypassed while retaining normal damage and wrapper delivery. Unsupported
wrapper changes and unavailable input do not become fabricated Boolean facts.

Eight synthetic cases cover six normal-VM comparisons, including both linked
values shadowing opposite bases, and two Rust adapter-error boundaries. The
prepared authored wrapper probe adds a declared rejecting entity flag and
compares with actual expression evaluation. No category bits are added. Native
actor classification, complete224E reaction/animation and actual Wraith
transitions remain open before client acceptance.
Both default and inspector builds pass the shared fixture, workspace/all-target
checks and Clippy, plus the seven-case authored wrapper probe, five-Boss hit
replay and natural first-Reaper E-input regression. This does not add a new
full-outcome, hosted-CI, Linux, MSRV or original-client acceptance claim.


### Map placement admission is separate from dynamic Boss classification

The authorized native 1B48140 loader reads a map-placeable ENTITY record after
its 24-byte common header. Its translation/scale/quaternion offsets and placed
component count/array agree with the primary TankLib teMapPlaceableEntity
reader and exact castle chunk bytes. Original1B483B6..1B48420 agrees with all 230
actual castle headers and requests component 57 for 31 placements. The probe
captures 18391E0's index/config and returns without constructing components.
Gate placement 16 has zero at header+68/+6A/+6C and does not request57 here.

This replaces the earlier unidentified compiled-header hypothesis. It does not
establish component 118 presence, run 7E37E0, or determine a dynamic Hero/Boss
category mask. Raw003/STUv2 bytes are not this placed-record header. No masks
or gameplay code change. External reproduction is
`probe_flashbang_map_placement_registration_20261113.py`, with its 230-row
`flashbang-map-placement-registration-native-validation-20261113.json` report.
Restricted image/map bytes remain outside Git. Flashbang native reaction and
original-client acceptance remain open under the user's static-first order.


### Flashbang timed-control exclusion tags

The room bridge now checks the loaded 224E/9 excluded-tag clause after the
entry and priority guards. It reuses the production tag evaluator and effective
body tags rather than an immutable base-only inventory. Exclusions reject
control without replacing impact damage or wrapper delivery; unknown facts
remain explicit errors. The same eight existing native-backed exclusion cases
exercise this guard, with live grant/release, base suppression/release and
unknown boundaries. The prepared wrapper probe checks actual loaded inputs.
The room replay also checks five ordinary Bosses and three declared Reaper
exclusions. All eight keep 25 damage and wrapper delivery; only ordinary cases
receive timed control. Declared tag inputs are not inferred native inventories.

This closes a separate omitted tag guard. It does not resolve native actor
category initialization, full filter admission, complete reaction animation or
actual Wraith activation. No mask bits are inserted; original-client checks
remain pending under the user's static-first order.

Both default and inspector lanes pass locked offline workspace/all-target tests
and Clippy, the eight-case room matrix, seven-case wrapper probe and natural
first-Reaper E regression. The latter retains impact 11627/resumed attack 11677,
25 health loss and 50 ticks of control. All 72 original native tag cases pass;
CI inventory has 25 unique shared consumers. No complete no-E outcome replay,
hosted CI, Linux, MSRV or original-client check was repeated for this change.


### Integrated native Flashbang filter result

A 64-case restricted replay now runs the actual CF4340 expression entry and
D0DA20 callback together with original mask/tag/default/result instructions.
Resolved actor/component pointers and literal tag conversion remain explicit
adapters; the actor has a declared absent-world custom service and empty live
tag effects. The same 64 synthetic results check production Rust evaluation.
The current 1|team adapter mask rejects 224E/9; declared 40/41 inputs admit only
when excluded mask bits and all three excluded tags are absent. This rules out
repairing the branch with a Boolean inversion or another tag-clause remapping.

No NPC category bits change. The correct native actor inventory still needs
evidence at spawn provisioning; full reaction/control/animation and client
acceptance remain open. This contract does not turn declared 41 into evidence
that an ordinary Boss owns that mask. Native code/map bytes stay external.

Both lanes pass the exact 64-case consumer, locked offline workspace/all-target
tests and Clippy. Original native replay, CI inventory of 26 unique contracts,
formatting and diff checks pass. No production behavior changes; prepared
encounter/outcome, hosted-CI, Linux, MSRV and client checks were not repeated.


### Nested reaction assignment is a separate unresolved gap

The subsequent isolated declared-admission probe does not alter production
category masks. With an accepting input it reaches 224E/9, but node 172's
array-element/map-field assignments are silently ignored: current `lvalue`
only returns direct variables, and `store` accepts a missing target as a no-op.
Node 175 consequently reads a null queued payload; only carrier state 71 remains.
The 31528 queue is instance scope 0, not entity scope. The existing body entity
variable assertion cannot prove local queue content or complete reaction.

Original 1B81210 parses 12 declared constant-index paths into root 31528 plus an
array index and field 27606. This establishes destination path construction;
actual dynamic 39 lookup, assignment, container growth/COW and watcher behavior
still need verification. External reproduction is
`probe_flashbang_nested_destination_path_20261113.py`; the declared Rust trace
and source hashes are in
`flashbang-nested-assignment-followup-static-evidence-20261113.json`.
Recover and implement nested assignment before claiming the complete reaction.
Spawn category provisioning remains independent; neither a declared accepting
mask nor this path parser establishes a native Boss's category inventory.
No gameplay code or original-client acceptance changes in this addendum.


### Native-shaped nested destination writes

SetVar/multi-assignment now execute explicit root/index/identifier paths instead
of silently discarding 224E/172's pending-record writes. Live index expressions
use normal VM variable reads; the completed root uses normal write notification.
The mutable identifier-map value preserves capacity and empty bucket positions,
including collisions and physical-order growth. Coercion and integer-zero array
holes follow original helper execution; unknown legacy map layouts and other
expression destination forms remain explicit errors. This does not reconstruct
all native variable handles, linked writable COW or foreign address paths.

The shared fixture covers 12 original parsed paths, 21 storage boundary cases
and two native map growth/update sequences. Real VM integration additionally
uses dynamic indexes in both scopes and verifies existing read notifications.
Normal Python e1128509 lacks this complex write; frozen Python ea472943 has a
separate scoped record consumer. No cross-language delivery or full native
notification equivalence is claimed. The isolated declared-admission probe now
loads a real map payload and reaches 224E/150's unsupported 545AB3B8 action.
Production masks stay unchanged; category provisioning and complete reaction/
animation/client acceptance remain open.

The actual Rust-combined reconciliation additionally fixes enemy self-damage
attribution to the room's current frame. `Bot::movement` omits its optional wire
frame; interpreting that omission as frame 0 incorrectly expires late-room
corpses immediately. The synthetic self-damage clock contract and prepared
Zomnic gate/retirement replay cover this host correction. No native timing,
cross-language delivery or original-client acceptance follows from it.

545AB3B8 now follows the evidenced native Action fallback instead of halting
the pending Flashbang payload at224E/150. The shared fixture covers forwarding
and true returns without payload evaluation; frozen/current Python already has
a no-op forwarder for this class. No new Python delivery is implied. An isolated
accepting-category trace reaches external message22, State25, velocity31 and
filter131 gaps; production masks and complete reaction acceptance stay open.

The isolated reaction confirms local393 is unset at224E/131. The native-backed
absent-target contract now returns false before requesting unsupported predicate
services, while retaining host config-shape/type checks. Known absent actors
also reject early; present/unavailable actors still require modeled predicates.
This resolves that diagnostic without adding category or relationship facts.
No new Python delivery or native-client acceptance is established.

The additional224E/25 child uses the already evidenced0DB9E66B PC base State.
The new shared fixture checks actual host lifetime and empty-codec observer
encoding separately from original registry/constructor/reader execution.
Python's base continuation remains scoped to other placements; this is not a
Python delivery or a new health/tag modifier. Full reaction remains open.

The 224E/22 attacker notification uses a targeted defender-message envelope,
with room and owner filtering. Existing intro/result broadcasts are unchanged.
The synthetic shared contract preserves parameter2B and sender identity through
VM dispatch. This is a Rust transport repair; it establishes neither a Python
port nor full native Flashbang reaction, velocity or actor classification.

Rust now admits the authored 224E/31 horizontal zero-lower-bound clamp row and
carries its next-frame edit into NPC objective movement before collision. The
Python reference at3dd55725 supports a related01C9/60 full-speed clamp; this
change does not claim a Python224E port. Original typed-row/enqueue/consumer
execution and synthetic host displacement have separate evidence. Native NPC
motor acceleration/platform parity and actual actor classification remain open.

Body-angle CC53F52B/7BE6D76D queries now use explicit typed host availability.
The Normal encounter NPC and owner adapters supply their current packed pose;
foreign orientation data is not fabricated. Python3dd55725 has scoped scene and
owner-angle handling; its owner path uses a different single-step conversion.
Original native replay1024 cases and a128-value packed input fixture pin the
Rust conversion separately. Full quaternion/platform and client parity remain
unverified.

The additional224E/34 form reads captured locals501/502 at activation through the
shared linked-state registry and applies a zero-limit lock to the room-owned
unparented NPC motor. Python3dd55725's LinkedTarget class supports this resource
form with a richer linked sample/cache model. Rust accepts only its explicit
unparented NPC projection here, not general parent/platform transforms or player
owner-full cache replication. Numeric native cases and synthetic lifecycle checks
remain separate from original-client acceptance and production actor categories.

## PC creation callbacks are not authoritative server spawning

The104319 PC registry has a single exact CreateEntity72C9C62F binding at priority2.
Its8-byte runtime implementation selects an empty slot48 callback. Original
cold/cached dispatcher replays leave the authored creation payload unread.
This differs from Rust and the Python3dd55725 reference, which must actually
create the Hero/body/controller requested by the encounter graph. Do not port
the PC no-op as server behavior or use it to infer runtime NPC category flags.
The partial raw component/requirement view also does not prove the final flags;
additional creation/replication inputs remain a separate evidence gap.
