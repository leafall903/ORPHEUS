# Restoration roadmap

The target is Overwatch 1 retail **1.74.0.0.104319**. Soldier: 76 and the Practice Range are the first acceptance group. Record the source version and the smallest repeatable test for each claim. A milestone stays open until its stated exit criteria have been verified. The [verification record](STATE.md) and [changelog](../CHANGELOG.md) describe completed work and remaining limits. The [issue tracker](https://github.com/leafall903/ORPHEUS/issues) holds individual tasks.

- [ ] Wrecking Ball complete static combat acceptance (hero 02E00000000001CA).
  The five PvE opening attacks and both NPC melees still spend
  Adaptive Shield during allied Transcendence. The aura restores
  staged armor damage, and Assassin contact still pins Ball.
  Heavy Assault's actual charge also crosses the shield-to-armor
  boundary. Its armor damage receives healing before the aura's
  world deadline and none when the charge lands at that deadline.
  The five extracted AI controllers read an explicit scene-owned
  difficulty binding. In modes 0F and 1A, a matching difficulty
  reaches the shared distance constraint at 142A node 3. A
  mismatch or no-output selector still reaches node 26. An
  explicit component 88 registry runs isolated nodes 2 and 3
  through begin, abort and teardown. Native registry lookup
  resolves nodes 7 and 12 to the plain State. With an explicit
  persistent-message fixture, startup activates both constraints
  and reaches the entity-existence check on the controller's
  current possession at node 25. A separate fixture binds
  controller possession to each actual Match NPC body and
  exercises both presence branches. A present body reaches the
  independently registered native node 24. A removed body
  bypasses it and reaches the next species-specific query or
  state. Live controller mounting and autonomous combat remain open.
  Node 24's isolated native tails verify the component 70
  request record, priority ordering and removal. The strict
  scene runtime now routes the default request to an explicit
  body registry with host-supplied owner and clock. Controller
  startup reaches the species graphs. Live component discovery
  and the request consumer still require evidence.
  Four species graphs now pass five native plain-State aliases.
  All five graphs reach the component 51 health query. The scene
  host resolves the unkeyed total from an explicit Match vitals
  binding, including after an actual Ball cannon hit. Four
  species then reach 00707E1E, which selects component 44 and
  returns a typed invalid entity on the verified missing-owner
  path. Enforcer passes the inherited no-output queries and
  reaches 13E9 node 0 after allocating node 19. Isolated
  node 19 registers and retires the authored kind-1 request.
  Live controller mounting, native pool subscriptions and the
  query fallback context remain open. The resolved component 44
  path now reads the explicit D0 body identity. Four species
  reach the body's variable 888, and Enforcer reaches 13E9
  node 1 after native-backed node 0 allocation.
  An explicit read-only bridge now serves 888 and subsequent
  body variable queries. An isolated authored stack verifies
  the controller reads the effective value and abort clears it.
  Automatic activation and live cross-VM subscriptions remain open.
  Sniper now reaches the same D379F515 engine predicate as
  Assassin. Heavy Assault passes 632B74B9 and reaches the
  same A37A910D entity query as Trooper. Native probes verify
  both query result types, engine callback slots and configured
  argument order. Explicit host callbacks now exercise those
  graph paths. Trooper passes registered State 274FF373 and
  reaches 78F71BED. Sniper and Assassin reach 47077281,
  and Heavy Assault reaches 25ECB55F without declared component 76 data.
  Isolated authored component 70 requests run on all five bodies. Trooper
  reads actual Ball variables 13393 and 13395 with an explicit target context
  and read-only VM binding. It passes plain State D2E53215 and reaches world query
  71A87E92 in graph 102F. With bound Match position snapshots, the controller
  reads its controlled body pose and completes 51 strict frames in each mode.
  The authored 07A5 message path reads both poses when its local gate is opened
  by the test. With a bound forward axis, the facing case reaches the node 28
  message send, while the away case completes 51 frames without it. An explicit
  cross-world route delivers 07A5 to the actual Trooper body VM in both modes.
  No loaded body graph handles it. Trooper, Sniper, Assassin, and Heavy Assault
  execute 07D0 with positive, negative, and omitted inputs. Enforcer exercises
  registration and rejection with explicit selector tables. With an explicit
  empty target, Enforcer completes 375 strict frames in each mode and passes
  the same injected 07D0 cases. A test bridge from its authored target gate to
  the actual weapon verifies rejection, cover, and re-engagement against Ball.
  The opt-in Match path now selects a visible enemy, sends 0079, and gates the
  weapon through 13E9. Six-metre Ball tests cover team rejection, occlusion,
  reacquisition, stop, and removal in both modes. Trooper, Sniper, and Heavy
  Assault now pass the same scheduling checks at their recorded distances.
  Heavy Assault also executes a moving charge at ten metres. Assassin now
  reaches the authored 13C2 handoff and pins through normal Match ticks. The
  test covers team, reach, interrupted windup, pin release and reacquisition.
  Both modes now exercise lethal attacks and acquisition of respawned Ball for
  each NPC. Ball can kill each NPC with weapon fire after staging it at 1 HP.
  The same controllers spend temporary shield first and hit permanent pools
  after shield expiry on the world clock.
  Both modes also cover automatic counterfire after Piledriver and an armed
  mine surviving Ball's death without becoming the acquisition target.
  Natural message production remains
  open. Sniper and Assassin
  complete 375 strict frames with an explicit empty target. Heavy Assault
  completes 51 strict frames with synthetic component 76 key lists. These
  starts produce no weapon reports. Live target acquisition, list population,
  other native decisions, and autonomous combat remain open.
  Partial Adaptive Shield reconstruction and player/actual-five-NPC cannon/melee
  and Piledriver damage/knockup tests exist in its isolated lane.
  Piledriver input modifiers now reach ordinary acceleration; actual landing
  recovery restricts all directions, then restores movement, including checked
  Flashbang, body-replacement and timed-respawn lifetimes. Native trajectory,
  full control/filter policies and original-client acceptance remain open.
  Roll input/cooldown and view-time critical immunity tests pass. The finite
  press buffer now prevents stale Roll/Q input from activating later;
  real-input early/near-ready/held/fresh press checks exist. Output-link
  invalidation restores automatic empty-magazine reload and repeated held-fire
  cycles without synthetic reload inputs. Three Roll/manual-reload return paths
  refill and resume real fire. Actual Hack and EMP preserve both reload
  phases in manual and automatic reload. Primary resumes at reload exit
  while Minefield remains locked. Grounded Piledriver rejection preserves
  the current reload. An airborne Piledriver cancels the wait before refill
  and preserves the exit after refill in both modes. Held fire waits through
  Piledriver recovery and starts a fresh automatic reload if still empty.
  Quick melee likewise cancels pre-fill and preserves post-fill. Pressing
  both attacks together lands the melee alone. Held primary then waits
  through the swing, recovery and any continuing or restarted reload.
  Boundary tests compare the frame before refill, the scheduled refill
  frame and the following frame in both modes. Quick melee wins on
  the refill frame. The following-frame press preserves the refill.
  Adaptive Shield across the same frames grants the nearby-enemy
  shield without interrupting refill or letting primary fire early.
  Grapple launch, in-flight retraction and attached release are
  covered before and after refill in both reload modes. The first
  cannon hit follows the return to gun form and any required reload.
  Hostile Piledriver preserves both reload phases through armor damage
  and knockup. In the post-fill cases, cannon fire resumes while the
  movement restriction is still active.
  An actual Heavy Assault charge preserves both reload phases.
  After its 170 armor-adjusted damage, the first cannon shot
  follows the original reload exit and hits the Heavy Assault.
  Four actual ranged PvE openers and both NPC melee attacks
  have the same reload-preservation evidence in both modes.
  The Assassin pin's interruption and release are tested separately.
  Lethal opening attacks from all five PvE enemies now exercise
  both reload phases in both modes. The old weapon retires and
  the new body fires from a fresh magazine after a fresh press.
  Lethal Trooper and Heavy Assault melee cover the same four phases.
  Lethal Heavy Assault charge also covers all four phases and
  retains NPC kill attribution.
  Authored Ball1.83/1.5 and enemy McCree1.425/.9975
  eye offsets now feed shot origins and target descriptors; low-cover checks
  cover reachable stances (Ball ground crouch is rejected by its current graph).
  Ball's20-shot normalized spread ramp and0..2degree damage rays now execute
  with generated native tables; actual ammo/repeated-input/recovery/far-miss
  checks exist. Uniform sampling, seed identities and eye/muzzle mapping remain
  explicit reconstruction; native cone math has bounded instruction evidence.
  Full cannon spread/critical/filter
  and reload-interruption matrices, and native PC start-check fidelity remain
  open. A bounded
  RMB grapple bridge has real collision/swing/release/cancellation tests;
  bounded fireball damage/physical control executes against player/all five NPCs.
  Actual Flashbang interrupts tether/contact and timed shields still expire.
  Minefield now creates15 owned entities with bounded physical/graph-timed
  preparation,130 damage,50HP and lifetime; actual-five-NPC hit/ally and three
  controlled incoming-weapon checks exist. Native prediction/collision/pet
  persistence/filter/chain rules remain open.
  Declared manual contexts now run real Enforcer13EB and Assassin1157 incoming
  damage, completing selected all-five NPC hit/stop and wall/recovery rows.
  Assassin's data-only target,3m contact, health clock and pin-control policies
  are reconstructed; actual graph tests cover Ball grapple interruption,
  source rifle death, target replacement/removal and control release. Native
  allocation/AI/specials and the complete cross-hero matrix remain open.
  Enforcer13EB now launches25 physical projectiles from its resource ring data.
  Ball shield damage, distance attenuation, wall/stop interception and mine
  destruction execute in static tests. Native ring phase and transport remain open.
  Ball's charge uses general and category resource factors. Static death and
  replacement checks pass. The original passive base rate remains open.
  Actual Genji Deflect tests cover Ball's cannon and quick melee,
  including the two-second expiry with Genji's command clock frozen. Other
  applicable reflection, barriers, absorption and invulnerability stay open.
  Sigma Grasp now intercepts tagged Ball cannon rounds and consumes the
  authored shield request, with temporary pool decay and charge exclusion
  exercised by actual commands. Interception and shield conversion
  respect the authored deadline during Sigma command gaps.
  Temporary shields also decay after the three-second wait during
  Sigma command gaps. Other absorption rows remain open.
  Defense Matrix interception also respects the depletion deadline
  of its remaining resource while the D.Va command clock is frozen.
  The body graph retains control of release, recharge and reuse.
  Reinhardt's barrier now intercepts Ball cannon fire using the extracted 011A
  mesh. Break lockout and subsequent re-raise with regenerated shield health,
  saved shield health, and rear, wall and melee controls have actual input
  coverage. Native pose and transport and other barrier interactions remain open.
  Actual Zarya personal barrier takes Ball cannon and quick melee damage in
  its own health pool and awards energy to Zarya. Projected barrier routes
  damage to the protected ally's barrier and energy to the caster.
  Personal barrier takes Ball Piledriver, Fireball and Minefield damage
  without body impulse. Projected barrier has actual Piledriver, Fireball
  and Minefield coverage.
  Native overlap and remaining ability interactions are open.
  Minefield crosses a raised Reinhardt shield mesh without damaging it.
  A projected Zarya barrier on the target absorbs the same blast and
  prevents displacement.
  Personal and projected Zarya barriers keep their two-second expiry
  on the world clock. Minefield reaches the body at the deadline
  in stepped and skipped updates.
  Actual Ball cannon tests cover shots from outside and inside
  Winston's dome. Its 00FC graph expires after nine seconds even
  during a caster command gap. Delayed deployments preserve that
  deadline and cannot block a shot after it.
  Actual Sleep Dart also hits the dome before a protected Ball.
  Adaptive Shield counts enemies in Wraith Form, Fade, Cryo-Freeze and
  Transcendence in the same cast. The active pool keeps its sampled
  maximum and a ready cast samples again.
  Its temporary pool and HUD maximum expire at the authored deadline
  when the body command clock is frozen. The body graph catches up
  when commands resume.
  Minefield proximity resolves at the end of a scheduler gap, after
  earlier rifle pulses have spent their share of Adaptive Shield.
  Reaper Wraith Form and Moira Fade have actual Ball phase-admission checks
  for selected cannon, melee, Piledriver and Fireball cases. An armed
  mine skips both while phased, even when a third enemy triggers it.
  Fade and Wraith Form restore Minefield and cannon targeting on
  their authored world expiry ticks, even during command gaps. Other
  immunity hosts, attack combinations and native timing remain open.
  Transcendence retains cannon collision and Piledriver lift while
  rejecting damage until its six-second world deadline. The last
  healing interval settles before a mine detonating at expiry.
  Cryo-Freeze settles its self-heal over 3.9 seconds of world time.
  Its Pet collider and immunity end at four seconds, with actual
  cannon, Minefield and late-command coverage.
  Piledriver uses its authored distance curve for inward impulse.
  Piledriver also suppresses air steering for half a second, with actual
  weapon input, deadline and body replacement coverage.
  Actual Sleep Dart cancels Piledriver windup and descent before impact.
  Actual rifle kills cancel both phases without landing damage or charge.
  A fresh press on the replacement body lands once for 100 damage.
  Minefield interruptions have actual Sleep Dart coverage across 0, 1
  and 3 deployment reports, including an armed hit while Ball is asleep.
  Already deployed mines also arm and hit before respawn after an actual
  lethal rifle hit on Ball.
  Actual rifle death between Minefield reports stops later deployment.
  An actual enemy Fireball crossing now detonates an armed mine.
  Cryo-Freeze rejects Piledriver damage and lift. Actual Ana Biotic
  Grenade and allied Transcendence exercise healing denial, expiry and
  amplification on Ball. Actual Zarya projection clears the denial and
  lets the active Transcendence resume with amplification intact.
  Native healing dispatch, other cleanse interactions and Cryo-Freeze
  area and control interactions remain open.
  Actual Ana rifle fire covers both friendly healing modes, blocked
  treatment across a grenade deadline, static cover and body replacement.
  Both hostile rifle modes cover timed damage, armor, Adaptive Shield,
  Nano expiry and projected barrier interception or cleanse.
  Both rifle modes also cover Matrix and Grasp interception, allied
  healing passage, and hostile Deflect into armor or Adaptive Shield.
  Rifle doses crossing the combat catch-up boundary cover Nano expiry,
  lethal tick attribution and duplicate-update rejection.
  A bound hostile rifle dose and active Transcendence retain their
  damage-before-healing order during 80- and 130-tick scheduler gaps.
  An allied Biotic Field retains that order during 45-, 80- and
  130-tick gaps, including when a previously admitted canister opens
  during the gap. Healing starts one tick after its scheduled opening.
  Outside the retained window, the field replays only ticks that
  overlap the bound dose. Duplicate updates add no healing.
  Ana Sleep Dart interrupts Ball's tether and suppresses input until expiry
  or damage-triggered wake. Native control filters and overlap remain open.
  Reinhardt and Zarya projected barriers stop the dart before Ball takes
  damage or receives sleep. Other projectile and barrier combinations stay open.
  With existing sleep, the projected barrier absorbs a Soldier rifle
  hit that would otherwise shorten the sleep deadline. The static
  server admits Minefield at wake. Original status removal remains open.
  An allied Ana can release a live Assassin pin with Sleep Dart.
  Scripted NPC route movement observes the same sleep deadline as weapon input.
  Manual pin binds Ball's body with a deployed mine nearby.
  The Assassin's 1157 impact rejects a deployed mine supplied directly
  to the graph. Native autonomous selection remains open. Heavy Assault
  crosses an EMP-disabled mine during charge, and the mine triggers on
  renewed contact after rearming.
  Changing the manual target to a disabled mine cancels both NPC
  melee windups and moving Heavy Assault charge without an impact.
  Melee can hit Ball again after its cooldown.
  A controlled windup reaches the target query for a visible
  disabled mine inside the melee sphere. Trooper skips impact,
  while Heavy Assault reaches impact with an empty target list.
  A moving charge also queries a visible disabled mine without
  selecting it. Restoring Ball before contact lets the same
  charge impact complete.
  Controlled ranged NPC weapons can destroy an EMP-disabled mine, whose
  effect and health retire before the original disable deadline.
  Value-first ruleset fallback has37 captured native wrapper parity cases;
  actual mine creator lifetime/infinite/knockback settings execute. Exact
  native creator/component resolution remains open;20 native MEMBER source
  branches and12 EntityID setter cases now have bounded execution evidence.
  Missing/replaced mine creator scripts reject stale source settings. Native
  ungated gravity and unit-scale position arithmetic have executable parity
  evidence; timing/scale production and mine collision response remain open.
  Flight lifetime/reach clipping and world-birth state have native arithmetic
  and fixture evidence. The qualified Windows x64 host RSQRTSS profile matches
  native distance/clipping with strict replay identity; other control/platform
  profiles, original-client environment and native terminal effects remain open.
  NPC self-health queries now use the authoritative health input; Enforcer's
  health gate opens but its entity/state predicate still prevents primary fire.
  Manual engagements now select the Trooper and Heavy Assault melee
  graphs in reach. Static tests cover Ball's armor and Adaptive Shield,
  windup cancellation, cooldown and reuse. Heavy charge now has static
  tests for damage, armor, Adaptive Shield, capsule collision, sidestep,
  teammate blockage, cooldown, interruption and attacker death.
  Allied Fireball contact now has coverage against the moving charge,
  including damage, charge gain and cancellation through landing.
  Incoming charge tests cover a surviving tether, lethal tether cleanup,
  cooldown rejection and a fresh attachment after respawn.
  Projected Barrier coverage includes both NPC melee graphs and Heavy
  charge, with actual barrier damage and energy relay.
  Heavy melee tests distinguish the displacement of tethered Ball under
  Adaptive Shield from the displacement blocked by Projected Barrier.
  Allied Piledriver has actual windup interruption and melee recovery
  coverage for both Trooper and Heavy Assault.
  All five PvE targets have Fireball coverage for wall occlusion, lateral
  misses and a second hit after exiting the outer contact range.
  Defense Matrix now has actual Enforcer projectile coverage for frontal
  protection, rear exposure and restored damage after release.
  Kinetic Grasp covers the same volley, temporary shield conversion and
  nearer static-wall priority.
  Actual Genji Deflect now returns the volley with damage, charge and
  kill attribution. Returned projectile presentation remains open.
  Returned pellets respect cover and team boundaries, and retire with
  the reflector's body on replacement.
  Opposing Grasp and Matrix have actual interception coverage on the
  returned flight, including shield conversion and charge exclusion.
  Ball's cannon has nearest-contact coverage with Matrix and Grasp
  active together, both directly and after Genji's Deflect changes
  the owner and reverses the route.
  Matrix depletion hands the cannon path to Grasp at the world
  deadline, even when D.Va's held input arrives late.
  Grasp expiry hands the same path back to Matrix and grants
  Sigma his absorbed-damage shields once.
  A shared end frame gives Sigma the same shield grant and admits
  cannon damage to the mech's armor, including after late inputs.
  Actual cannon tests cover both contact orders for Grasp with
  Reinhardt's shield mesh or Zarya's protected recipient.
  Winston's dome has both outside contact orders and an inside
  control where Grasp takes the shot before any shell crossing.
  The same three contact cases are exercised on Genji's return
  path after friendly defenses pass Ball's outgoing cannon.
  The projected recipient and Grasp also have both return-path
  orders, including the handoff after the nearer defender moves.
  When Genji is the projected recipient, cannon tests distinguish
  frontal reflection, rear barrier damage and post-Deflect damage.
  Stepped and skipped updates reach the same handoff on Deflect's
  deadline with Genji's script clock frozen.
  Both deadlines can coincide. The exact frame and the next
  shot after late inputs deal body damage and award Ball charge.
  A rear Sleep Dart cancels Matrix with D.Va's command clock
  frozen. Late input during sleep leaves the cannon path open.
  Opposing Deflect tests cover a second owner change, retained flight
  lifetime, last-reflector damage credit and the server reflection limit.
  Sleep Dart has actual Matrix, Grasp and Deflect coverage, including Ball's
  Minefield input after interception and Ana's sleep after a return.
  Enemy Deflect also has coverage for a returned dart against Ball's
  armor, Adaptive Shield, attached Grapple and Minefield input.
  Flashbang has actual Matrix and Grasp absorption and Deflect explosion
  coverage, including stun and damage credit.
  Biotic Grenade has actual Matrix and Grasp absorption and Deflect
  coverage. A reflected explosion applies healing or healing denial
  according to the reflector's team.
  Its resource-driven gravity now has stepped and skipped flight tests
  over low cover and a tall-wall interception control.
  Long-drop tests distinguish terrain impact inside the 300 m travel
  limit from retirement before terrain contact beyond it.
  Actual Nano Boost covers the paid release, grenade healing modifiers,
  Ball cannon and rifle damage into Adaptive Shield, expiry and invalid
  target rejection. Caster death before release prevents the effect.
  After release, caster death preserves Nano on the original recipient.
  A second cast extends its deadline. Recipient replacement retires
  the old effect and rejects a pending release addressed to that body.
  Two actual Nano releases also cover cannon and Piledriver against
  projected barriers, breaking-hit isolation, pool damage and lift.
  Actual Deflect verifies cannon and grenade returns with Nano on
  either side, armor and Adaptive Shield accounting, and expiry during
  an active Deflect. Native modifier attribution remains open.
  Actual Minefield and Nano cover the deployer's boost before and after
  deployment, recipient reduction and expiry, projected-barrier
  interception and rejection of an armed mine as a Nano target.
  Actual Nano reduces all five NPCs' incoming damage before Adaptive
  Shield and armor accounting. Assassin pin and Heavy Assault melee
  displacement remain active through the damage reduction.
  All five NPC opening attacks, Trooper and Heavy Assault melee, and
  Heavy Assault charge have Adaptive Shield expiry and replay coverage
  while Ball's command clock is frozen.
  All five engagements also cover damaged shield retirement, armor on
  a replacement Ball, and damage into its separately cast shield.
  An already launched Enforcer volley has replacement pose, armor and
  startup shield timing coverage independent of later volleys.
  Enforcer source death, explicit removal and lethal attribution have
  static coverage. A retained posthumous hit keeps its original tick
  across Adaptive Shield expiry.
  Twice-reflected Enforcer pellets retain their launch timing after
  source removal and credit the final reflector.
  A reflected volley spanning Nano expiry has stepped and skipped
  coverage for armor, Adaptive Shield and reflector charge.
  The same reflected flight has projected barrier expiry coverage,
  including Zarya energy and final-reflector credit.
  The Adaptive Shield expiry cases compare stepped and skipped
  impact frames. They cover shield-to-armor damage and a shared
  expiry with projected barrier, with Zarya energy only from the
  early barrier hits and final-reflector charge only from armor.
  A paid Nano on Ball during the shared expiry leaves the early
  barrier damage and Zarya energy unchanged, then reduces the
  later armor damage and final-reflector charge by half.
  Ball's Nano-boosted cannon, melee, Piledriver and Fireball hit all
  five actual NPCs with effective-damage charge. A 120 HP Piledriver
  fixture verifies lethal credit and corpse handling before expiry
  and nonlethal lift after expiry.
  Sniper retreat has static arrival, shot recovery, wall, stop, death
  and cooldown coverage. Actual Sleep Dart and Piledriver cover interruption
  during motion and rifle recovery. A mine in the retreat path interrupts
  motion before arrival. Loss of sight, range, or hostile team eligibility
  also cancels the move. The existing engagement resumes rifle fire when
  eligible. Native destination selection remains open.
  Moving Heavy Assault charge has target-loss and late teammate-block
  coverage, plus Ball death and respawn before the charge impact.
  The interrupted charge preserves cooldown through primary recovery.
  Manual Assassin pins expose the 1177 victim intro, hold and one-second
  release in observer snapshots. Native persistent-message timing remains open.
  Delayed Minefield input respects the first pin and final release frames.
  Live and released pins preserve zero, one or two pre-pin reports when
  a paid Minefield is reconstructed from delayed input.
  An attached Grapple breaks on the Assassin pin frame during input gaps.
  Delayed launches settle cooldown on that frame and require a new press.
  Delayed Piledriver windup and descent cancel at pin contact. Actual
  recovery rejects the early retry and verifies damage and charge on reuse.
  Overlapping Assassin pin and Sleep Dart preserve both command windows
  in both start and end orders, including wake caused by pin damage.
  Actual 1177 reload coverage distinguishes interrupted pre-fill waits
  from preserved post-fill waits and verifies automatic refill on recovery.
  Actual EMP drains Ball's Adaptive Shield through the scoped 0B32 health
  service at pulse time, including command gaps and delayed pre-pulse
  shield input. Projected Barrier blocks ordinary Hack and breaks under EMP.
  Actual ordinary Hack releases an attached Grapple after channel
  completion. An aborted channel preserves it. Holding Grapple through
  expiry does not reconnect it, and a fresh press attaches again.
  Ordinary Hack and EMP release an existing tether on impact without
  Ball commands. The saved impact frame settles cooldown during
  command replay, including when the first command arrives after expiry.
  Delayed launches from before the impact keep the graph activation
  but cannot recreate the interrupted hook.
  Static input tests cover both overlapping and separated EMP locks, including
  delayed Minefield presses and a usable Roll input between separated casts.
  Sleep Dart and Flashbang reject late Minefield and movement input stamped
  inside the control interval, including the shortened sleep window after damage.
  Delayed Minefield casts stamped before impact preserve reports due before
  the interrupt in both posed replay and timer catch-up.
  Delayed Grapple keeps its launch graph but cannot create a world hook
  after Sleep Dart or Flashbang has landed. Its cooldown settles at the
  impact frame. Delayed Piledriver has Sleep Dart cancellation and
  recovery coverage in windup and descent.
  An attached Grapple cut by overlapping Sleep Dart and EMP keeps
  the first impact's cooldown in both cast orders, even during a
  recipient command gap.
  A lethal Soldier rifle hit before command replay clears that pending
  interruption. A late command cannot relaunch on the corpse, and
  the respawned body can attach once ready.
  Native damage attribution, targeting and timing remain open.
  Two simultaneous pins retain control and one victim animation through
  either source's early exit, including the death of the animation's source.
  Remaining specials and native AI behavior remain open.
  Native retract/steering/lifetime, target/rehit/immunity policies remain open. Full skill,
  ultimate, cooldown and cross-hero matrices remain open; see
  `WRECKING_BALL_COMBAT.txt`. Original-client acceptance
  is a separate, still-open gate.

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
