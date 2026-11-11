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

- [ ] Finish D.Va static acceptance after the source handoff. The current
  server snapshot has host regressions for both forms and all five Talon
  NPCs. Native ejection trajectory and protection, hit geometry, NPC
  controllers and specials, and remaining cross-hero combinations still
  need verification. Original-client acceptance remains separate.

- [ ] Compare native delayed input during D.Va control. Static combat
  records Flashbang, sleep, wake and freeze admission by body and keeps
  late input neutral through the original effect window. Projected
  Barrier ends the freeze window early. Command-clock admission and
  history retention are reconstructed.
  Chain Hook and Assassin pin now record their actual release frames.
  Multiple Assassin sources keep the input lock until the last release.
  Graviton records movement restrictions separately. Delayed commands
  from the pull interval retain aim, weapons and Matrix.

- [ ] Compare native D.Va command-gap recovery. The controlled mech and
  parked pilot use the same bounded neutral command history for the
  reconstructed passive fill. Unposed frames advance the mech's weapon
  graph, and a launched missile remains on the world flight clock.

- [ ] Compare native Sniper flight and defenses against both D.Va forms.
  Static combat covers pre-launch and in-flight cover, allied Genji
  reflection, late Matrix admission and Flashbang while a round is airborne.
  The authored 1122 weapon's 200 m/s and 100 m reach drive flight timing.
  A shot crossing Self-Destruct handoff samples the exposed pilot and
  uses its launch team for bodies and defenses. Deflect assigns a new team.

- [ ] Compare native Graviton counters to Talon specials. An allied field
  can separate Assassin from either D.Va form and release the live pin.
  Heavy Assault charge now yields to the field's movement through its
  authored cooldown path. Contact and movement arbitration are reconstructed.

- [ ] Verify D.Va's NPC death and elimination presentation in the original
  client. Static combat credits controlled Fusion kills on all five NPCs
  to the pilot and keeps the mech as the attack source. A detached bomb
  kills all five NPC types and one player while Assassin pins the pilot,
  then releases the pin and credits the six eliminations once each.

- [ ] Compare native Graviton Surge against both D.Va forms. Actual Matrix
  intercepts the flight before 03E2 lands. The deployed field still pulls
  and damages a mech with active Matrix. Both forms fire under world-driven
  pull and recover at expiry. The host filters Boosters from commands in
  the pull interval and stops active Boosters through its authored cooldown.
  Self-Destruct and lethal ejection retire the old pull, and the next
  world tick samples the exposed pilot. Call Mech after the fuse refill
  completes with an independent replacement mover. Native field
  admission, force, and mobility arbitration remain open. Genji's Deflect
  transfers field ownership and team. Matrix or Kinetic Grasp can intercept
  the returned flight, and Grasp uses the authored shield conversion.
  The Call Mech immunity scope releases the pilot's pull, and a fresh
  mech can be captured by the same field. Projected Barrier spends its
  own pool while preserving the existing sleep deadline until the next
  admitted body packet.

- [ ] Verify native Mei spray, frost buildup and freeze against D.Va.
  Actual 08BE packets bypass Matrix and pierce an aligned body once.
  A static wall stops the packet after the mech. Both forms now have
  host evidence for slow, freeze, aim locking, thaw and Projected Barrier
  cleanse. Lethal rifle damage and Call Mech retire the frozen body's
  status. Explicit frost setting slots change the initial value, rate,
  duration and stacking in actual spray tests. Freeze blocks both
  ultimate starts and leaves the detached fuse and refill on their own
  clock. Boosters stops and starts its authored cooldown. Micro Missiles
  retains launched rounds and follows its pre-launch or active-volley
  cooldown path. Flashbang expiry leaves freeze intact, and Projected
  Barrier clears frost while leaving Flashbang on its own deadline.
  Native status hosting, client ruleset routing and
  remaining lifecycle cases are open.

- [ ] Compare native Zarya primary beam against Defense Matrix. Actual
  0241 beam damage bypasses an active Matrix, respects range, cover and
  teams, and spends Projected Barrier health. A held beam switches to
  the ejected pilot at the pilot exposure tick in the command's view.
  Sleep Dart and Flashbang stop the beam without damage replay at expiry.
  The host eye origin and command-time integration still need native comparison.

- [ ] Compare native Projected Barrier control handling. Host tests on
  both D.Va forms exercise Sleep Dart and Flashbang against full and
  breaking barriers, then verify that late projection preserves the
  existing control deadline.

- [ ] Compare native quick melee at forced-control and input-gap boundaries.
  Actual Sleep Dart and Flashbang suppress either form before melee starts.
  Control after a hit preserves its one-second cooldown. The static host
  drops cached input across control expiry and accepts a fresh press.

- [ ] Compare native Talon volley geometry and projectile presentation.
  Enforcer's authored 25 pellets now resolve armour, falloff and Matrix
  independently. Heavy Assault's authored two rays each apply 5 raw damage
  from the static eye origin. An actual Projected Barrier on either form
  consumes the breaking packet and exposes the body to later packets in
  the same volley. At range, the host checks Enforcer damage at barrier
  contact, Kinetic Grasp conversion and Genji's reflected hits for both
  forms. The host applies falloff to total travel through reflection.
  Separate muzzle transforms, native spread phase, native reflection
  falloff and individual Enforcer projectile wire records remain open.

- [ ] Compare native Enforcer impact order through a delayed world update.
  Retained host ticks reproduce the lethal contact, pilot exposure and
  trailing hits when a launched volley remains after firing stops.
  The host replay uses current poses and a 64-tick retention window.

- [ ] Compare native Boosters interruption. Actual Flashbang and Sleep Dart
  stop propulsion on the possessed mech. The host follows the authored
  300 ms wait before starting the 4 s cooldown. A held Shift restarts
  exactly once after the later of cooldown and forced-control expiry.

- [ ] Compare native D.Va attacks against Fortify. Host input tests cover
  both primary weapons during Fortify and after expiry, both quick melee
  attacks and Micro Missiles during Fortify, and damage without radial
  knockback from Boosters, Self-Destruct and Call Mech. See
  `../dva-fortify-attack-matrix.txt` from the lane source directory.

- [ ] Compare native projectile impact at protection expiry. The host
  tests start Micro Missiles during Wraith or Cryo-Freeze and verify
  that in-flight missiles can hit once protection ends.

- [ ] Compare native Boosters barrier arbitration. The host checks
  unprotected contact and surviving or breaking Zarya barriers.
  Recontact after break keeps the same per-target contact record.
  A fresh activation after cooldown can hit the exposed target.
  In the server reconstruction, Cryo-Freeze and Transcendence consume
  that activation's contact while immune. Wraith Form skips contact
  tracking, so the same activation can hit after Wraith Form expires.
  Active Deflect and Grasp allow contact damage and impulse, without
  a reflected hit or temporary shield grant in the host scenarios.
  Wraith, Cryo-Freeze and Transcendence prevent contact damage, impulse,
  and combat charge in the host tests.

- [ ] Compare native Micro Missiles damage at Zarya barrier break. The
  host absorbs the breaking missile as a whole and credits later hits.
  The Reinhardt mesh case also blocks the breaking missile. Later
  missiles deal and credit 3.5 damage apiece against armor.

- [x] Execute the native DeflectProjectiles box registration with the four
  authored Matrix boxes. The probe confirms per-axis corner normalization
  and writes into an owner collision record. The native end callback
  clears that record while preserving another with a different second key.
  The host applies the same normalization.
  See `../dva-matrix-native-box-registration.txt`.

- [ ] Compare native Defense Matrix .097 collision categories and owner frame.
  The original sweep reads the registered boxes and uses the authored incidence
  angle and projectile radius. Actual Light Gun commands cover expanded
  Matrix and Deflect edges. The original world query receives 0002.097.
  Probes cover profile selection, both filter records, the preliminary
  candidate predicate and the entity-tag predicate. Model-holder masks
  use the native packing order, including when reading legacy caches.
  Live main-query collision behavior, tag sources and pose sampling remain open. See `../dva-matrix-native-tag-policy.txt`.

- [ ] Compare native Matrix team filtering for D.Va ordnance. The
  host tests fire a complete Micro Missiles burst through allied and
  enemy controlled Matrix volumes with a live target behind each.
  A partial Matrix charge also tests depletion mid-burst and credits
  effective armor damage from the remaining missiles.

- [ ] Compare native Self-Destruct target eligibility. One host blast
  excludes allied players and NPCs in team mode. In free-for-all the
  same labels can be hit, while the source pilot remains excluded.

- [ ] Compare native Call Mech team filters. A single host landing
  excludes allied players and NPCs in team mode and hits the same
  candidates in free-for-all.

- [ ] Compare native self-target filtering at Call Mech possession.
  A previously fired Light Gun round passes through the owned replacement
  in free-for-all and can still hit an enemy without moving charge across forms.

- [ ] Compare native Micro Missiles windup interruption. Actual Sleep Dart
  and Flashbang cancel 1128 before the first report and leave cooldown zero.
  The next eligible press starts the full burst at the authored deadline.

- [ ] Compare native Micro Missiles interruption in mid-burst. The host
  stops new launches on Sleep Dart, preserves existing projectiles, and
  accepts a new full burst after wake and cooldown.
  A lethal rifle hit stops new launches and retains old projectiles
  without granting their damage as Call Mech charge.
  A hero switch retires in-flight Micro Missiles and Light Gun rounds
  with their source body. The replacement receives no hit or charge.
  A later Call Mech retires still-flying missiles with the destroyed
  predecessor before the pilot takes control of the replacement.
  The test also checks the 026A start gate after ejection. A full-charge
  press outside the authored 150 ms window expires before the gate opens.
  A press released within the window starts once at unlock.

- [ ] Compare native Micro Missiles command recovery at cooldown expiry.
  On the host, a received release before the gap prevents a new burst.
  Retained held input and a fresh eligible press each start one burst.

- [ ] Compare native Self-Destruct timing through delayed updates. The
  detached host bomb retains the authored fuse hit frame and sends one
  pilot refill across the tested scheduler gap.
  When the due tick is outside the catch-up window, detonation runs
  on the first retained tick. The refill is delivered before cleanup.
  Hero switch and leave retire the host bomb before detonation. An
  already queued refill cannot charge a replacement pilot body.

- [ ] Compare native Self-Destruct interruption boundaries. The host
  test covers sleep before activation and sleep on the pilot after
  detachment, with an enemy target verifying the blast.
  The authored refill reaches the sleeping pilot without starting Call
  Mech. A fresh command after waking spends it and controls a new body.

- [ ] Compare native D.Va charge on lethal hits and Talon targets.
  Host tests verify the remaining health cap on both forms and measure
  charge from all four controlled mech attacks against all five NPCs.
  The same matrix covers pilot Light Gun and quick melee and verifies
  that the inactive form never receives combat charge.

- [ ] Compare native reflected damage and charge attribution. Host
  scenarios isolate D.Va combat charge and verify hit credit when
  Matrix intercepts reflected Micro Missiles.

- [ ] Compare native D.Va charge from blocked and absorbed shots. The
  host tests verify zero charge against Zarya and Sigma, then exact
  effective body-damage credit after the Zarya barrier expires.

- [ ] Compare native projectile hitbox geometry and the alternate radius paths.
  Original positive-radius selection, ranking and eligibility pass fixture
  probes. Light Gun grazes, near cover and Mei piercing pass through the
  host capsule path. Sixteen normal D.Va viewer creates reach native
  health attachment with declared services and pass 128 eligibility checks.
  Twelve original Light Gun and Micro Missiles cases verify the ordinary
  hit priority. Host input reproduces the priority and the five-NPC
  splash boundaries. Three authored volleys select the .097/0002 model
  route in native fixture probes. A 264-case full-entry sweep verifies
  root transform and nearest bone with the authored group-1 cache and
  supplied bind pose. Native MSKL load and pose publication pass 528
  further sweeps across all 11 models. Three authored D.Va clips drive
  6,900 more native queries with an explicit offline sampling model.
  The authored first-person sets resolve six further graph positions,
  including both Fusion Cannon references through tree 0519. Two
  positions use 022A, a native group command with no table entry.
  72 component selection, 48 group command and 96 alias lookup
  cases verify native routing with fixture components and providers.
  A 28-case Fusion Cannon replay allocates native group records
  and verifies 0082 category publication. The 01F9 reference is
  registered as a native default. Its value provider remains open.
  128 native Anim entries verify explicit duration setup and
  independent timing flags through the playback boundary with fixture
  services. The host honors the 050A/34 timing bypass and reads
  m_2DEAE6A9 for automatic finish suppression. A 32-case native
  queue replay verifies remaining time and event order with fixture
  services. The host reproduces both orders and finish cancellation.
  Live selection, layering and world candidate generation remain open.
  See ../dva-authored-clip-model-sweeps.json and
  ../dva-animation-alias-archive.json.

- [ ] Compare native Fusion Cannon critical zones and pellet geometry.
  Controlled inputs now use the pilot command's view delay and click
  subframe. Executed regressions cover a crossing target, replacement
  birth boundaries, and the authored falloff and reach.
  D.Va's two primaries use reconstructed 2x critical damage and
  Micro Missiles use 1x. Twelve native replays carry the m_A872C70E
  field from the queue to a fixture direction provider.
  See ../dva-native-direct-config-direction-transfer.json.
  159 isolated and 954 full-cache native sweeps now join authored
  model regions to target health profiles. Both Heavy Assault region
  0015.05C bones win contacts with the 4x profile. Native writer
  selection and ordinary queue region transfer pass for the same
  contacts. Final weapon damage and live hit geometry remain open. See ../dva-native-authored-region-health.json.

- [ ] Compare native quick melee target volumes and input timing.
  Both D.Va forms now use the command view time. Actual 0043 first damage
  is on the press frame in the host. Flashbang stops its running scope,
  and fresh input recovers after stun and cooldown.

- [ ] Compare native Defense Matrix frame ordering and attachment.
  Normal-selection commands verify the authored half-second minimum,
  cooldown re-entry, resource threshold and rear Flashbang interruption.
  Early forced closure uses the authored one-second cooldown, including
  a reconstructed impact timestamp during a command gap. The post-tap
  authored release path uses the same impact timestamp under forced control.
  A bounded gap with Matrix held uses the authored drain through impact
  and starts the authored recharge wait there.
  Combat contact checks the same drain during missing held frames.
  Rifle and Helix can hit once the resource is exhausted.
  Contact also respects a released tap's deadline and the first
  neutral frame after the reconstructed 32-frame hold window.
  A later Flashbang preserves the natural cooldown and recharge
  from those closures or resource exhaustion within pose recovery.
  Longer gaps inside retained command history carry only the live
  Matrix button across the projected hold in unposed VM frames.
  Gaps beyond retained history settle the projected close and run
  the authored Matrix recharge event at its deadline.

- [ ] Compare native Fusion movement transitions and prediction.
  Actual controlled commands verify the authored 40% walk penalty,
  Matrix weapon suppression, Boosters override and release. Pilot Light
  Gun keeps its base walking speed while firing.

- [ ] Compare native Fusion ring geometry and launch identity.
  Fusion Cannons use 050A/6's authored ring pattern and the verified
  D80310 direction math. The seed uses its authored state index 3
  and the reported activation counter. Both D.Va primaries use the
  verified launch angle codec. The host supplies the input aim,
  muzzle origin and reconstructed entity and instance words.
  Light Gun uses the normalized 239B571E output with zero cone angles.
  Both muzzle selectors and static model attachment chains have
  executed evidence. Animated muzzle sampling remains open.

- [ ] Recover native ejection launch and protection. Lethal and
  Self-Destruct return now start a new targetable interval for the retained
  pilot. Actual delayed rifle input respects it. Low ceiling tests verify
  the reconstructed parked-pose fallback for both return paths.
  Retaining that mover now closes its parked modifier rows before
  the next movement frame. Native rows 11 and 23 pass 32 dispatch,
  field and timing cases. Another 56 cases verify the mover flag
  through BBF500 and two callers. The separate single-contact filter
  takes an evaluated boolean on the stack in 96 original cases.
  A third caller on FBFF5DE7 can use a boolean override in 16 original
  cases. None of the eight scanned D.Va graphs uses that state.
  Twenty host form and Talon regressions pass. Scene queries and host
  integration remain open.
  D07EBF01 definition and runtime allocation now replay in the
  original image. The runtime type falls back to its parent factory
  without reading either configuration slot. Twelve begin/finish/abort
  cases reach the original component callbacks with fixture owner and
  journals. Damage admission and ejection motion remain open.
  See ../dva-native-protection-definition.json.
  Seven ejection-adjacent factory comparisons and four host tests
  verify movement-mod scope retirement on the return tick.
  See ../dva-native-ejection-route.json.

- [ ] Verify native mech combat charge. Actual Fusion Cannon hits earn
  and spend the host mech meter independently of the retained pilot.
  Damage conversion and mode modifiers are reconstructed.

- [ ] Verify native D.Va combat charge. The server awards one point
  per effective pilot damage up to the authored Call Mech cost. The
  sleep interruption scenario earns the retry with Light Gun damage.
  Native conversion, mode modifiers and mech combat charge remain open.

- [ ] Compare native Call Mech entry interruption and retry. Actual
  Sleep Dart aborts entry after landing on the host. Waking does not
  resume the old activation. A fresh charged input replaces the mech.

- [ ] Compare native Call Mech interruption at landing. The server
  retires the replacement VM and body when the pilot dies before
  possession. A death before landing cancels its effects. A death
  after landing preserves the one completed damage and impulse event.

- [ ] Compare native Call Mech landing edges. Server tests use the
  authored 2.5 metre target search and the separate 3 metre knockback
  ring. Capsule surface distance is reconstructed.

- [ ] Compare native Call Mech landing against projectile defenses.
  Server scenarios exercise landing while Kinetic Grasp, Deflect, or
  Defense Matrix is active.
  The 026A impulse probe records the evaluated origin, live target
  position, and replacement mech position. The Matrix and Deflect
  scenarios verify forward displacement away from that origin.

- [ ] Verify native Call Mech barrier arbitration. The host spends both
  Zarya barrier variants without passing damage or impulse through a
  break. The unprotected control takes 250 damage and is knocked away.

- [ ] Verify native Talon Assassin contact geometry. Server acquisition
  and pin damage use the same capsule visibility rule for both D.Va
  forms. Full cover prevents or ends the pin without a late damage tick.
  Assassin selection names the player controller while contact damage
  remains bound to the active body selected at engagement.

- [ ] Verify native Talon Assassin target selection and navigation.
  The host skips either D.Va form under an active projected barrier,
  switches to an eligible enemy, and can reacquire D.Va on expiry.
  Native target selection and navigation remain open.

- [ ] Verify native Boosters and Talon Assassin control priority.
  The host releases the pin on impact for both live D.Va forms and
  preserves the authored contact damage and knockback. The release
  trigger and recovery lockout use a server reconstruction.

- [ ] Verify native Talon Assassin melee rescue and victim escape.
  Both D.Va forms can rescue a pinned teammate through quick melee on
  the host, including with two normally controlled D.Va mechs. The pin
  ends immediately and damage stays stopped during
  the two-second recovery window. Native hit routing and victim escape
  input remain open.

- [ ] Verify native Assassin control priority and escape. On the host,
  an actual Zarya projection applied before or after a pin protects either
  D.Va form and allows weapon input. The same Assassin can pin the target
  again after expiry. The native filters, priority, and escape sequence
  remain open.

- [ ] Verify native Heavy Assault control priority. Actual Boosters
  stop charge and move the NPC away without a later impact. A lethal
  charge on the mech returns an undamaged pilot. Host interruption
  and movement ownership are reconstructed.

- [ ] Complete the Heavy Assault charge interaction matrix. Explicit
  logical35 drives the authored 1408 windup, 15 m/s movement and 175
  damage. Actual pilot and controlled mech hits, Matrix bypass, wall
  collision, windup and motion cancellation, and lethal Fusion
  interruption have evidence. The server reconstructs pose queries,
  the target array, capsule sweep and 3-second motion limit. Native
  control priority, acquisition and the remaining NPC specials are open.
  Projected Barrier absorbs the 175 damage on either D.Va form without overflow.
  After Boosters interrupts charge, an early explicit retry fails and a fresh
  input at cooldown expiry hits the controlled mech once.

- [ ] Complete Talon specials. Trooper 1422 and Heavy Assault 140E
  melee use the authored input, sphere, windup and damage. The host
  reconstructs the target array and capsule contact. Both D.Va forms,
  Matrix, cover after windup and stop have evidence.

- [ ] Complete native Talon acquisition and navigation. The opt-in
  stationary host policy selects the closest visible eligible controller
  and retains it while visible. All five NPCs have actual graph-input
  evidence for initial mech acquisition, pilot return and acquisition
  of the replacement mech. Navigation, native AI decisions, timing
  parity and specials remain open.

- [ ] Complete the five-NPC D.Va matrix. All five authored Talon
  bodies now have actual-input evidence for Fusion, Micro Missiles,
  quick melee and Boosters from the normally controlled mech. Manual
  NPC attacks defeat the initial mech, reacquire the restored pilot
  and allow a pilot counterattack after release. A separate Call Mech
  scenario retains the NPC target controller through cover and
  resolves the new mech when cover clears. Native autonomous
  acquisition, navigation, NPC specials and the full counter matrix
  remain open.
  In the autonomous acquisition reconstruction, Matrix absorbs the four
  ranged attacks. Assassin pin bypasses Matrix and cancels it.
  All five can also destroy the one-health Call Mech replacement
  after first damaging it at full health. Both acquisition modes
  reacquire the returned pilot, who deals 14 with Light Gun after
  NPC stop. See ../dva-npc-replacement-demech-regression.txt.
  The normal Self-Destruct refill also feeds a Call Mech protection
  regression against all five. The pilot remains immune at handoff
  while the replacement admits fresh enemy attacks. See
  ../dva-call-mech-npc-protection-regression.txt.

- [ ] Complete D.Va's defense trace fidelity.
  Rifle fire reaches the returned pilot after mech death or Self-Destruct.
  Call Mech starts the replacement Matrix at 100 after the previous mech depleted it.
  Pilot reload completes while the replacement is controlled. The
  reconstructed return stops Call Mech arbitration so the Light Gun
  can fire after the replacement self-destructs.
  A lethal enemy rifle hit on the replacement also releases arbitration.
  The returned pilot can charge and deploy another mech through Light Gun damage.
  Fusion Cannons then credit damage charge to the new mech's own meter.
  The server applies pilot-only immunity from 026A/45 within 026A/22.
  An in-flight Micro Missile from a destroyed mech can hit without charging either form.
  Lethal damage during windup prevents the first launch.
  Matrix, Deflect and Grasp
  now use a reconstructed static trace from the defender's eye to the
  candidate box entry. Actual rifle and Helix hit a teammate on the
  shooter's side of a wall and are absorbed with the wall removed.
  Genji-reflected Micro Missiles remain interceptable. Native masks
  and attachment queries remain open.

- [ ] Complete D.Va's Sigma defense interactions. Actual 1D02 Grasp
  absorbs the three ranged weapons through its authored boxes. The
  server reconstructs the eye attachment, facing gate, projectile
  eligibility and missile absorption amount. The authored 1D03 request
  supplies 60% conversion and a 400 cap, with a 3-second grace and
  5-per-second decay from 1D02. A separate temporary pool is spent
  before the base pools. Quick melee and detached Self-Destruct use
  their ordinary damage routes. Native filters, shield engine and
  client presentation remain open.

- [ ] Complete D.Va's forced-control interactions. Actual Roadhog
  01C8/01C9 supplies hook range, speed, damage and pull parameters.
  Static combat reconstructs the swept contact, relative destination,
  collision movement, input ownership and release timing. The chain
  has a separate target sweep from projectile interception. Both D.Va
  forms exercise damage, blocked input and recovery. Matrix bypass,
  range, miss, static cover and team ordering have focused tests.
  Native hook filters, control priority and original-client rope
  presentation remain open.

- [ ] Complete D.Va's status control coverage. Actual 0BDD Sleep Dart
  carries effect 026F through 0BDE/51 to normal 224E profile 676B.
  Its authored five-second sleep, half-second initial damage grace
  and half-second wake are enforced by reconstructed body-local
  world deadlines. Actual combat and movement inputs are inhibited
  during sleep. Damage wake, body retirement, Matrix contact and
  expiry have focused evidence. Native effect dispatch, alternate
  filters, control stacking, cleanse and original-client wake
  presentation remain open.

- [ ] Complete D.Va's Zarya barrier and cleanse interactions. Actual
  0244 and 025C states acquire a finite barrier using 0256's authored
  pool, a 10D7 sphere and the live recipient body. Target ranking,
  sphere attachment, private identity, barrier damage routing and the
  direct anti-heal removal at attachment are reconstructed. The
  authored 0256 and 0C13 references to #478 connect the barrier
  resources to the 0C72 cleanse predicate. The native writer remains
  unverified. Actual D.Va ranged attacks and detached Self-Destruct,
  projected cleanse on both forms, shield expiry and resumed damage
  have static evidence. Other cleanse statuses, native filters and
  client presentation remain open.

- [ ] Complete D.Va's status interaction matrix. The normal Ana
  0B97/0267 effect dispatch uses 0C71, 0C72 and 0B99 for 60 hostile
  damage, 100 friendly healing, a four-second duration and 1.5x delayed
  friendly healing. The server reconstructs the relationship and
  line-of-sight dispatch, uses world ticks for modifier intervals and
  evaluates Soldier's Biotic Field over retained ticks. Actual Ana and
  Soldier inputs exercise both D.Va forms, Matrix and both form changes.
  Native predicate filters, cleanse, trajectory and client status
  presentation remain open.

- [ ] Complete D.Va protection and status interactions. The static
  server uses Reaper 0020/3, Mei 05FE/17 and Zenyatta 017B/8 to scope
  Wraith Form, Cryo-Freeze and Transcendence. Server entry completion
  for each authored zero-delay callback and each protection policy
  are reconstructed. Actual Fusion and Light Gun inputs are blocked
  during those scopes and work again after expiry. Actual missiles,
  quick melee, Boosters, detached Self-Destruct and Call Mech provide
  additional coverage. Native targeting masks, cleanse, defensive
  healing and interruption details remain open.

- [ ] Complete dynamic barrier interactions for both D.Va forms. Actual
  Reinhardt secondary input raises 0095/17, which references 011A.
  The static server uses the build 104319 A3C collision mesh and the
  authored health variable. It reconstructs the current shield pose from
  the bind height, forward offset and aim. The runtime reads the separately
  prepared mesh from `ORPHEUS_REINHARDT_BARRIER_GEOMETRY`. D.Va's three
  ranged weapons now exercise the raised shield, with shield break,
  release, wall ordering and ally checks. Missiles use
  reconstructed direct plus blast damage at contact. Detached Self-Destruct
  uses the authored 0B2F/node24 curve at the nearest visible shield surface.
  Body selection captures shield cover before damage, including the blast
  that breaks a shield. Native Pet creation, animation, damage filters,
  projectile splash surface acquisition, Call Mech and other barrier
  cases remain open.

- [ ] Complete D.Va cross-hero defense coverage. Genji's active 03A3/10
  Deflect returns Fusion Cannon hits, Light Gun shots and Micro Missiles.
  Matrix can intercept the returned missiles. The server reconstructs the
  eye anchor, the facing calculation, and the aim-directed return using
  authored geometry and a conservative projectile-tag gate. Hitscan uses
  the remaining range and allows one redirection. Missiles keep speed and
  lifetime and transfer damage ownership. Active Deflect blocks D.Va quick
  melee after the ordinary hit and cover checks. The melee direction uses
  the attacker's horizontal bearing and the authored facing angle.
  Blizzard's Overwatch Classic description documents the block behavior.
  Native angle and callback behavior, reflected projectile prediction and
  broader defense interactions remain open.
  [Blizzard's Overwatch Classic ability description](https://overwatch.blizzard.com/en-us/news/24146047/)

- [ ] Complete D.Va Self-Destruct and Call Mech. The explicit 0407 Match path
  now runs Self-Destruct's charge, fuse and radial damage graph against enemy
  players and the Talon NPCs. After explicit return to the pilot, Call Mech
  creates a replacement through its authored path, applies landing damage and knockback
  and runs the pilot's swept entry poses. Successful entry routes server-side
  movement and Fusion Cannon input to that mech. Normal selection also binds
  the initial mech, and lethal mech damage restores the retained pilot.
  Controlled Matrix, melee and Boosters contact use the existing static
  combat services. Recovered mech command poses now advance the same VM and
  consume their combat reports. Cross-tick lag compensation remains open.
  The detached shell now runs its authored fuse and radial
  damage and impulse rings after actual controlled input. Native ejection and detached flight,
  owner streams, full defense interactions and recovery remain open.
  See DVA_FORM_LIFECYCLE.txt.

- [ ] Complete D.Va control handoff and ejection. Initial 026A/20 resolves the
  created 0407. Queued 016C dispatch validates that body's script and ownership
  during the initial control state. Controlled input, mover and presentation
  handoff and physical ejection remain open. See DVA_FORM_LIFECYCLE.txt.

- [ ] Complete D.Va all-five NPC incoming skill/controller matrix. Manual actual
  first-hit damage now works in both forms for all five; Assassin pin clock,
  control/stop/retirement/rebind and actual Flashbang recovery have directed
  evidence. Native AI allocation, acquisition/navigation, full gun/pin/QTE/
  immunity/special/difficulty and complete bidirectional acceptance remain open.
  Strict 1177/9 now runs with declared mover inputs and preserves its State
  in VM snapshots. Explicit startup of the 1177/16 branch with numeric and
  scope services reaches the controller health query at 1177/21.
  Live 1157/45 mounts 1177 on a strict 000E controller with declared
  presence. Source-scoped retirement clears the controller flag on stop,
  body replacement and entity retirement. Passive snapshots capture the
  active controller VM. Owner full frames carry 1177 and its bound flag,
  then omit both on retirement. Rebind allocates a fresh instance ID.
  A declared positive-health fixture delivers 08FE from 1157/18 to
  1177/17 and executes the authored queued self-stop. Live native message
  policy, escape input and original-client playback remain open.
  See DVA_MANUAL_NPCS.txt; full-static commit gate stays closed.

- [ ] Complete shared ability/resource exit acceptance. The D.Va lane reproduced
  canceled notification marker failures and adapted the parent lifetime and
  scope-before-arbitration fixes; three focused regressions pass. Full combat,
  native inter-object order and original-client acceptance remain open.

- [ ] Complete D.Va Boosters native movement role16. Its context helper matches
  864 original-instruction cases and post velocity6048 cases with declared
  engine result adapters. Live legacy scopes/3D motion/capsule sweeps and15
  directed movement tests pass. The static host now consumes authored contact
  damage with reconstructed capsule overlap and LOS. Native role arbitration,
  quaternion/view, native impulse arbitration, immunity and full ability/hero
  acceptance remain open. Player and NPC impulse motion uses the static mover.
- [ ] Compare native D.Va pilot stance and combat geometry. Host tests use
  actual crouch and Light Gun input after lethal or Self-Destruct returns
  from the initial and replacement mechs. The crouched 0.8 eye fires into
  low cover and the standing 1.32 eye clears it. Native camera, muzzle and
  ejection motion remain open. See DVA_EYE.txt.

- [ ] Complete Sombra status interactions with D.Va. Actual Hack and EMP commands
  reach both forms through the authored target graphs and clear their skill gates
  at expiry. Tests cover range, walls, allies, and a wall entering Hack's active
  ray. EMP tests cover uncharged casts, primary fire, active interruption and a
  detached bomb. The host reconstructs capsule LOS and receiver classification.
  Actual Reinhardt and Zarya barriers block Hack. EMP removes the tested
  enemy barriers without body damage, and walls preserve both barriers.
  Both status orders have Match evidence. Remaining deployables, native
  reverse-order attribution and original-client acceptance are open.
- [ ] Verify D.Va combat in both forms against enemy players and all five manual
  PvE NPC types. A shared projectile splash repair now respects static cover in
  player/NPC health tests; a reconstructed Fusion Cannon consumer executes
  independent pellets against player/NPC health and armour. Mech creation/possession,
  native pellet geometry, ability/input timing,
  transformations and original-client acceptance remain open. See
  [D.Va combat lane](DVA_COMBAT.txt).
  Direct component43 ownership now permits authored pilot command fire and reload
  in static player/NPC-stand-in tests, backed by1056 original query comparisons;
  this does not accept the initial mech or both-form lifecycle.
  Explicit alternate-body inputs now run real0407 primary/Micro Missile command
  tests, including per-pellet armour, simultaneous fire and cooldown refusal/reuse.
  Initial creation, placement and transformations remain open.
  Matrix resource activation/drain/release/cooldown/regeneration now execute through
  the authored graph after native-backed ruleset-reference fallback and live
  possession resolution. A reconstructed consumer now checks authored boxes,
  external projectile tags, enemy rifle/Helix, release/exhaustion and collision
  ordering. Native filter/attachment, complete Matrix acceptance
  remain open;1296 scalar comparisons do not accept the combat consumer.
  Authored Boosters typed mover/throttle rows now produce horizontal thrust,
  yaw steering and static capsule blocking, with cancellation and cooldown reuse
  checks.1600 native throttle comparisons cover split/sign/clamp with adapters;
  vertical flight, contact damage/knockback and full ability acceptance stay open.
  Both forms'0043 melee now respects the reviewed shared static-wall policy;
  command tests cover enemy players and the five actual NPC target definitions
  with authored capsules/base pools. NPC AI/incoming skills, status/native
  geometry and full acceptance remain open; see DVA_MELEE.txt. Both primary
  weapons and the full Micro Missile burst also pass positive target cases
  against all five base-pool definitions; incoming AI/skills remain unaccepted.
  Shared NPC backend/weapon/sparse-slot/array selection now runs actual Trooper,
  Sniper and Heavy Assault primary inputs against both forms, including armour,
  miss/wall/team and Matrix interception/release. All five scripts/base pools
  initialize; Enforcer/Assassin attacks and full NPC AI/specials remain OPEN.
  See DVA_NPC_INCOMING.txt for the scoped evidence.
  Flashbang resource policy now interrupts D.Va active channels, suppresses
  movement/fire and respects Matrix absorption, with controlled recovery checks
  for both forms and three NPC primary sources. Distinct-input joins and passive
  state fields are restored. Native status, immunity and forced-close
  timing remain unaccepted. See DVA_CONTROL.txt. Full hero acceptance stays open.
  Five distinct spatial scalar interfaces used by pilot creation/Call Mech
  inputs now pass1,920 original scalar comparisons with engine-result adapters
  and actual026A input tests. Live poses now feed a reconstructed unparented
  model mapping;440 look and180 mover-origin original arithmetic cases match
  byte-for-byte. Native hierarchy/state selection and placement/creation/
  possession remain open; see DVA_FORM_LIFECYCLE.txt. This does not
  accept the form lifecycle or close the full-static-before-commit gate.
  A reconstructed static placement consumer now executes canonical026A/node38
  ground/slope/clearance/body blocking and authored success/failure routing,
  including normal charged input. Creation/possession/landing damage and native
  placement filters/preview remain OPEN; this is not complete Call Mech.
  Normal hero selection now creates an owned0407 actor with authored pools and
  graphs, health targeting and observer records.303B cleanup runs before
  replacement or its authored delayed retirement; pilot retirement removes its
  exact owned bodies. Native factory/VM/control services, possession, movement,
  ejection and full lifecycle remain OPEN; the hero checklist stays unchecked.

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

D.Va uncommitted controller/clock prerequisite: child ownership resolves during
initialization, and live primary-controller queries preserve unpossessed child
results. Created VMs retain source command-clock offsets through cleanup/death.
369 regressions,53 storage and8 placement cases pass (overlapping groups).
All five lane NPC movers exactly match the independent parent extraction.
CA861E44 possession/input and stream transfer, two retained body VMs, demech,
Call Mech/Self-Destruct and complete static/client acceptance remain open.

D.Va child Match queries and deferred message routing now execute actual026A007A
and0276016C paths bidirectionally with existing VM clocks and lifetime checks.
The actual recipient config group uses the native-backed collecting interface.
Possession/state-end flags, retained-body input/stream transfer, cross-body
variables, demech and complete ultimates/static/client acceptance remain open.

Latest D.Va evidence:384 combined combat/query/placement/VM/NPC/message cases
PASS291.062s,53 storage PASS187.077s and1 child receiver-failure isolation
PASS1.809s. Full objective and commit/integration gate remain open.

D.Va foreign member/subscription prerequisite: actual026A charge/cost expressions
read child storage, and its real Stack watches child842. Slot identity, receiver
clock scheduling, retirement disconnection and passive cross-VM bridges are
implemented. Full charge return still needs controller selector F76C486C and
actual possession/retained-body control transfer; neither ultimate is accepted.

Foreign-slot verification:431 combined cases PASS363.363s; later live/death/timer
failure cleanup51 cases PASS12.633s. Existing45 VM/alias and8 server snapshots
pass in recorded groups. Corrected uninitialized clock fixture is included in
final431; earlier failures are retained. No full-static/client acceptance claim.

D.Va F76 pet selector:1872 original scalar/helper comparisons pass with declared
storage/engine adapters; native hash mapping identifies component72 as STUPet.
Separate reconstructed pet-owner storage and pre-publication Component registry
now execute actual026A save ->0276 charge-return consumers and initialization
rollback. Original server factory/wire policy, actual possession/stream transfer,
full charge lifecycle, demech and both complete ultimates remain open.

Pet/registry final evidence:443 combined tests PASS423.246s;53 storage tests
PASS168.930s;1872 native scalar/helper comparisons pass. All current tests include
rollback/charge-save-return cases. Control-return016C routing still needs native
primary/current/controlled-body and message-forwarding proof; the initial/static
closure lacks a child receiver but includes one missing graph. No full acceptance.

D.Va shared input-buffer repair: released early Boosters no longer auto-recasts
at expiry; actual150ms Boosters/CallMech charge-gate and zero-window Micro input
cases pass. Final55 focused tests PASS38.648s. Native transient control still
requires distinct component44 relations; D8 getter label alone does not prove
D0/D4 meanings. Full/static/client/commit/integration gates remain open.

Input follow-up: old melee reuse pressed345ms before readiness and depended on
the expired-input bug; real readiness plus both-form expired/near tests now pass
(11 recovery cases). Parent throttle review agrees in245float32 cases;1600native
cases pass again and actualBoosters reverse/strafe/next-frame restoration passes.
Production mover already handled17/18/19; no replacement was imported. Full goal
and native timing/flight/control/ults/client boundaries remain open.

Final shared-input review regression:452 combined cases PASS481.490s, Ruff/diff
pass. Both-form melee now waits for actual readiness and separately verifies
expired/near-window presses. Full goal remains active; no commit/integration.
