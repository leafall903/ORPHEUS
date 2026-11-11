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

- [ ] Accept Soldier: 76 against enemy players and all five manual PvE NPCs,
  including every skill, ultimate and cross-hero interaction. Static Helix and
  melee occlusion, cooldown recovery, Field healing and reconstructed Visor
  acquisition have pool tests. Normal primary input verifies body and
  critical damage at both falloff boundaries and both sides of 180 m for
  all five PvE bodies. Enemy-player input verifies far critical damage and
  maximum range. Native hurtboxes and prediction remain open.
  Holding Helix during Sprint exits Sprint
  and launches after the release delay against each of the five prepared
  PvE bodies. An early re-press rejects and a fresh press after cooldown
  launches again. Releasing a tap during Sprint exit leaves no delayed
  launch, and a fresh press after the exit delay hits each NPC.
  Actual Heavy Assault fire can kill Soldier during Sprint exit.
  Helix stays unlaunched even when the secondary input remains held.
  One Helix explosion resolves direct, near, far, allied and occluded
  contacts together. All five NPCs rotate through each role and
  per-target notices match the 260-point total.
  Lethal direct and splash controls verify the per-target HP cap,
  one death event per NPC and no extra damage or force to a corpse.
  The original 104319 Helix compiler stores .5 in explosion record +0x40
  and 1.0 at +0x44. Captured client instructions use +0x44 to gate and
  scale a vector. Original +0x40 usage and impact target selection remain open.
  The client length gate, queue order and movement call inputs have native
  execution witnesses. Final native displacement remains open.
  Actual static Helix direct hits push four prepared NPCs to a backstop,
  where they land and settle. Heavy Assault takes the hit and stays in place.
  Quick Melee from Sprint deals 30 to each of the five prepared PvE
  bodies. Soldier is still at sprint speed on the hit frame and returns
  to running speed within the next five forward-input frames.
  Quick Melee now applies its authored contact-force inputs through the
  reconstructed mover path. Four PvE bodies receive the impulse. Heavy
  Assault takes damage but its active resistance suppresses displacement.
  Real swings across the angle and near-surface cover boundaries have
  hit and miss evidence on all five prepared PvE bodies. Follow-up
  swings verify the new range after displacement and recovery after
  Soldier moves closer. With a backstop, four airborne NPCs reach the wall
  and settle. All five reject an early re-press and take the recovered swing.
  Active Zarya barriers use the same
  viewed body pose as Quick Melee, including on all five projected NPCs.
  Rifle and Visor sample the viewed body position for active Zarya and
  Reinhardt barriers, Kinetic Grasp and Defense Matrix. Actual Helix
  still sweeps moving barriers at contact time.
  Layered sphere and mesh tests verify that a Helix impact cannot damage
  a rear barrier through front cover, including a breaking front sphere.
  Actual rifle and Helix check first contact between Grasp and Deflect.
  One held Visor input breaks Projected Barrier, kills each protected NPC,
  and then acquires an off-axis enemy player. The native search refresh
  predicate is verified separately. The host refreshes its owner list every
  three body frames and catches up after a command gap. Native phase-key
  provenance remains open.
  Captured Visor scoring also verifies the weighted projected-bounds and
  distance terms and ascending sort. The scoring camera uses a fixed
  normalized viewport. Native call-site and candidate probes verify the
  cursor anchor, two angle inputs, endpoint construction and single-point
  fallback. Candidate probes confirm separate projection and distance
  positions, stance-scaled mover dimensions for Soldier and the five
  PvE body definitions, and the optional definition override. Live
  object binding, native filter results and host integration remain open.
  The native output path uses an unbounded default for both authored
  Visor branches. Ranked-ID remapping, truncation, ordered comparison and
  entity-array writes have isolated execution evidence. The native
  variable sink and original-client lock replication remain open.
  A supplied-candidate static VM test covers the #2337 array and #3544
  first-target stack through owner-frame encoding and local decoding,
  including order changes, reacquisition and expiry. Native candidate
  generation and original-client lock presentation remain open.
  Actual owner commands now populate both variables from current poses
  for five PvE bodies and one enemy player. The sandbox query is shared
  with rifle targeting, which still uses the historical command view.
  Both paths now apply the projected-bounds and world-distance terms to
  host poses. Actual rifle input distinguishes that ranking from manual
  aim for every PvE body. Native pose provenance, filter results, tie
  ordering and refresh phase remain open. Current-view and 64 ms delayed
  rifle commands hit different targets after each of the five PvE bodies
  leaves the live cone.
  Actual lethal inputs from five PvE NPCs and an enemy Soldier clear
  both owner lock variables in the death handler. Retained NPC corpses
  drop out of the live list, and Visor retargets a surviving enemy.
  An enemy observer's acknowledged stream also carries the live locks
  and both clears on expiry and death, including after reactivation.
  Actual damage to all five NPCs contributes to an earned Visor. That
  activation hits all five and an enemy player before expiring and
  restoring manual aim and damage-based charge.
  Actual Flashbang, Sleep Dart and Assassin pin interrupt an earned
  Visor. Field and rifle hits can earn a second activation from zero.
  The original 053/00A1 resource connects both Visor emitters to a HUD
  graph with separate source and primary-target checks. Isolated VM
  cases cover those checks and the optional one-second wait. Native
  marker application and retirement remain open.
  Holding Biotic Field from Sprint returns Soldier to running speed before
  deployment, then heals a 50-point fixture wound on each allied PvE body.
  Visor input from Sprint consumes seeded full charge and exits Sprint.
  An off-axis shot deals 20 to each PvE body during Visor, and misses
  after expiry. Primary input during startup causes no damage.
  During Visor, actual primary fire spends ammo in all five cases for
  each PvE body. Exposed enemies take 20 damage. Allied targets, full
  static cover, excessive range and off-cone targets take no damage.
  A lethal Visor shot on each wounded PvE body leaves the corpse in the
  match. The next shot at the same aim acquires a living off-axis Trooper.
  With a fixed wall, moving each initial target behind cover in the
  fixture redirects the next shot to an exposed Trooper. Moving the
  initial target back into view restores its priority.
  For all five NPCs behind an actual Orisa barrier, Visor breaks the
  weakened barrier without body damage. The next off-axis shot hits the
  NPC for 20. The same aim misses after Visor expires.
  Sigma's offset Kinetic Grasp absorbs a Visor shot without NPC damage.
  After Grasp expires, a second shot during the same Visor activation
  deals 20 to each of the five PvE targets in turn.
  D.Va's offset Matrix also intercepts the first Visor shot. After Matrix
  ends following release, the same aim hits each NPC during the original
  Visor activation.
  An offset Genji reflects the first Visor round back into Soldier.
  After Deflect expires, the same aim hits each NPC during the original
  Visor activation.
  Actual fire empties the rifle during Visor. Empty input and input
  after refill while the reload state is active both reject. After
  reload recovery, the same Visor activation hits each of the five NPCs.
  Helix interrupts manual reload before refill. On each PvE body,
  Helix hits for 120 and the recovered rifle hits for 20 from the old
  magazine. A new manual reload refills it after the cancelled one.
  Biotic Field also interrupts manual reload before refill. Each allied
  PvE body recovers 25 fixture HP. After the fixture changes the target
  to the enemy team, the recovered rifle hits with the old ammunition.
  Undercharged Visor input leaves manual reload active. Full-charge
  Visor cancels reload, refills the rifle and enables an off-axis hit
  against each PvE body.
  Quick Melee cancels manual reload before refill and deals 30 to each
  NPC. The next rifle shot uses the old magazine and deals 20.
  Sprint cancels manual reload before refill as well. Stopping movement
  lets the rifle resume from the old magazine against each PvE body.
  Quick Melee and Helix can cancel reload after refill. The full
  magazine survives either cancel, and the next hit uses a refilled round.
  A lethal Heavy Assault shot before refill ends manual reload.
  The retired magazine stays at 29 rounds through the old refill deadline.
  The respawned Soldier uses a fresh magazine to hit Heavy Assault.
  The current Assassin pin host suppresses fire while an admitted manual
  reload continues and refills. Fresh fire hits after release. The
  original build's reload interruption rule remains unverified.
  A new manual reload input during an actual Assassin pin is rejected.
  After manual release, a fresh press starts reload and restores 30 rounds.
  Helix fires during an empty-magazine reload without restarting it.
  Each PvE body takes 120, then takes 20 from the rifle after refill.
  Quick Melee cancels the empty-magazine reload and deals 30. Held
  primary later refills the magazine and hits for 20.
  Biotic Field also cancels active empty-magazine reload. Each allied
  NPC recovers 25 fixture HP. After the fixture switches the target to
  the enemy team, held primary refills and hits for 20.
  Sprint cancels active empty-magazine reload while ammo stays at zero.
  After movement stops, held primary refills and hits each PvE body.
  Visor cancels active empty-magazine reload, consumes seeded full charge
  and refills to 30 before the first assisted shot hits each PvE body.
  Actual Ana Sleep Dart blocks new reload and fire input while an
  admitted reload continues. After waking, fresh inputs restore reload
  and rifle hits. Native reload interruption remains open.
  Actual Flashbang also blocks new reload and fire while an admitted
  reload completes. Fresh reload and rifle inputs recover after stun.
  Four manual NPC primaries hit Soldier before and after a fixed cover
  transition. Attack input stays released behind the wall and resumes
  on re-exposure under the same firing order.
  The same four weapons recover after Soldier returns to manual
  engagement range or the fixture restores the opposing team.
  A temporary wall releases an Assassin pin and stops damage. Removing
  the wall permits a new 25-point contact under the original order.
  All five actual NPCs now receive authored
  attacks and retain corpses. Trooper, Enforcer, Sniper and Heavy primary
  fire wounds a sprinting Soldier without slowing him and kills him in a
  1 HP control. Actual Assassin and Heavy capture interrupt Sprint.
  Scheduled respawn or capture release allows fresh Sprint input. Zarya's
  Projected Barrier preserves Sprint on both capture contacts, including
  the hit that breaks the barrier. A later unshielded contact can capture.
  Four NPC primaries also spend barrier health while Soldier sprints.
  Enforcer's remaining pellets can wound Soldier in the volley that
  breaks the shield. The other three wound him on later hits.
  Sniper's alternate projectile also spends shield HP and later wounds
  Soldier without interrupting Sprint. Heavy melee can reverse motion
  while Sprint stays active. A projected barrier absorbs Heavy and
  Trooper melee without changing health or Sprint speed.
  The Enforcer predicate also has original entity table
  walks, the component 43 target-ID write and read, and the component 44
  read on the resolved body. The probe also executes original watcher
  registration, notification and type 9 queue insertion on synthetic
  records. Queue dispatch remains open.
  Actual Field use restores the resulting wounds after each attacker stops
  firing. Across all five NPC recipients, a released canister opens after
  a lethal enemy rifle hit. Body replacement retires the old canister, and
  a fresh cast heals the remaining wound and earns charge. Once admitted,
  Field releases and heals on the world clock without further owner
  input. Actual Sleep Dart and same-side Flashbang cancel the pending
  release. Sleep Dart can interrupt on the same impact tick when catch-up
  starts before its first flight step. An already released Field also
  keeps its opening tick across a jump before first settlement. A late
  packet's newly armed Field wait starts from the catch-up endpoint. A native
  effect callback probe verifies target lookup, binding and transform
  writes, cycle rejection, and dirty queue deduplication. Actual 03DC
  placement remains open.
  Crossing Flashbang hits on Orisa
  and Winston barriers preserve
  the cast through shield break. An already deployed Field heals through a manual
  Assassin pin and restores full health after release. Healing those wounds
  grants Visor charge. Allied Field recovery also grants charge on all five
  prepared NPC bodies after their team changes in the fixture. A Sniper
  wound restored by Field fills the last 50 points and enables Visor to
  hit the off-axis Sniper until expiry. Native enemy
  controller allocation, AI/specials, targeting/protection and original-client
  acceptance remain open. See [Soldier lane](SOLDIER_COMBAT_LANE.txt).
  Visor now has actual partial/full cover evidence against a player and all
  five NPCs, plus Flashbang and Assassin startup cancellation, recharge and
  lethal exit.
  Native target scoring, lock replication and remaining interruption cases
  remain open.
  Combat-backed charge, Field visibility/windup cancellation and actual Sprint
  transitions now have execution evidence. Soldier charge now reads authored
  0B8B amount/multiplier expressions. Practice Range executes the authored
  passive wait via 13C0/01C7 mode permission and current possession.
  Earned charge obeys that permission and authored static damage-tag scaling.
  Practice Range Field healing also exercises 0044 and recipient suppression.
  Field replacement tests cover pending and deployed retirement and late ACKs.
  World-frame catch-up now interleaves an admitted Field's retained healing
  with actual Trooper fire and an enemy player's in-flight Helix. The
  outcome agrees with sequential updates, including same-tick lethal damage.
  Overlapping Fields also preserve each caster's healing and ultimate
  credit across a world-frame gap after an enemy rifle wounds a shared ally.
  The original Field volley supplies separate launch direction and offset.
  Captured readers verify direction normalization, offset retrieval and
  its transformation with a supplied pose.
  The static host uses authored launch vectors, gravity, range and
  static side rebounds. As a host policy, Field waits for flight
  termination and the existing minimum delay. A floor hit supplies
  the placement point. Other terminal causes keep the cast-feet fallback.
  Native pose selection and quaternion conversion are exercised for
  current, historical and fallback poses. The spawn descriptor probe
  reaches the 03DC asset slot. Live pose providers and contact-driven
  placement remain open.
  The captured descriptor dispatch and its contact gate have isolated
  execution evidence. Field leaves that gate's m_DBAC4DDB field empty.
  The original finish handler queues the existing projectile in an
  isolated owner table. The native drain forwards existing entries
  to the entity teardown boundary. The captured terminal callback,
  eight ground-contact trajectories and six side-contact continuations
  do not read the supplied 03DC descriptor. Authoritative 03DC
  deployment remains open.
  Field player values and fade/expiry waits now come from 0258.
  All five PvE bodies have actual Field range and static cover boundary tests.
  Deployed Field tests cover re-admission and free-for-all self-only healing.
  Tag2ED training bots and both D.Va bodies exercise the authored 0012
  damage charge entry.
  Other mode startup, native combat eligibility and clock comparison
  fidelity remain open. Scoped Fortify and Transcendence damage consumers have
  focused execution evidence. Transcendence expires without Zenyatta input.
  Actual Helix contact agrees across sequential and skipped ticks, and
  Soldier rifle and Zenyatta orb damage resume after expiry.
  Fortify reduction and charge now also agree on both sides of expiry for
  in-flight Helix with sequential or skipped world ticks.
  Deflect expires without Genji input. Actual Helix on either side of that
  expiry agrees across the same world tick patterns, including ownership.
  Kinetic Grasp also expires without Sigma input. Actual Helix checks the
  absorption, shield conversion and damage branches across world gaps.
  A 0407 Matrix release during missing input now settles in-flight Helix
  at contact and allows idle resource recovery and a fresh cast.
  Reinhardt's shield also lowers during missing input. In-flight Helix and
  idle shield regeneration agree across sequential and skipped world ticks.
  Soldier Sprint exits through its authored neutral-throttle path during
  missing input. Actual movement, rifle and Sprint recover after the gap.
  Admitted rifle reloads also finish through missing input, including the
  post-refill recovery before a new damaging shot.
  Helix, melee and Field cooldowns finish during missing input. Actual
  early presses are rejected and fresh inputs deal damage or heal after it.
  Actual Wraith input covers rifle and Helix
  pass-through, melee immunity, Visor selection and expiry with continuous or
  absent Reaper commands. In-flight Helix contact before and after Wraith expiry
  agrees across contiguous and skipped ticks at fixed poses. Native transport
  timing and historical moving poses remain open.
  Actual inputs also cover all five PvE recipients behind Wraith, Cryo-Freeze
  and Transcendence, plus four incoming NPC primary weapons through those
  phases. Native phase filters remain open. Cryo-Freeze now has actual rifle,
  Helix, melee and Visor protection evidence,
  plus cancel and no-input expiry/cooldown. Actual rifle wounds verify Cryo
  self-healing, partial cancellation and overheal. Actual hostile Ana
  grenade anti-heal ends on Cryo entry and rifle wounds start healing before
  the old deadline. Native pulses, Mei healing charge and other cleanses
  remain open.
  Zarya's personal Particle Barrier now has actual rifle, Helix, melee and
  Visor absorption, body-miss sphere contact, break, expiry and cooldown
  evidence. Projected Barrier covers live ally selection, Soldier against all
  five NPC recipients, and four actual NPC weapons against protected Soldier.
  Actual Assassin contact and a live pin's next damage tick also resolve
  against projected barrier HP. Native receiver behavior remains open.
  Winston's deployed dome has actual rifle, Helix and Visor evidence across
  the shell, with inside and allied controls, whole-hit break and idle expiry.
  All five PvE recipients and four incoming NPC weapons have shell tests.
  Actual Helix contact before and after expiry agrees across sequential and
  skipped ticks at fixed poses.
  Orisa's actual thrown barrier has rifle, Helix and Visor mesh interception,
  breaking-hit and no-input expiry witnesses. Actual cooldown refusal and
  recovery, landing replacement and persistence through owner death or switch
  have execution evidence. All five PvE recipients and four incoming NPC
  weapons have mesh tests. Native placement and remaining NPC cases are open.
  Ana Sleep Dart has actual Soldier control, Field interruption, wake timing
  and a projected-barrier break witness. It also interrupts Visor startup
  in early and late cases. Full charge cannot activate during sleep.
  After waking, a fresh Visor cast recovers once an actual hit completes
  the charge threshold.
  A live dart passes through Wraith and cancels Soldier's admitted Field
  windup. A fresh Field heals after sleep ends. Ordinary Reaper takes
  the dart. Cryo-Freeze and Transcendence stop it without sleep or damage,
  allowing the Field behind them to open. Native status dispatch remains open.
  The world clock advances admitted Visor without owner commands. A
  retained Field gap across its expiry gives the same health and charge
  as sequential idle ticks. Helix already in flight across expiry also
  preserves its impact tick and charge in a world-frame gap. In Practice
  Range the authored passive wait also advances on idle world ticks
  after the first accepted command and resumes after Visor expires. A
  641-tick idle world gap awards the same 50 points and retains the same
  wait deadline as sequential updates.
  The Practice Range map also preserves its authored 005A variant.
  The shared 13C0 startup enables passive gain for both teams, and an
  actual enemy rifle hit adds combat charge only to the shooter.
  Skirmish's 0D76 startup switch now matches the original presence
  predicate. An isolated route runs 0694 into Soldier's body permission and
  0695 into the controller marker. The latter leaves existing body
  permission intact. Native lookup and execution confirm that node 84
  uses the base Action and follows its output without reading the config.
  The strict manager routes actual 0694 and 0695 states by channel under
  supplied role policies. Withdrawing and restoring 0694 toggles #2426.
  Node 44 now tests authored map mode membership. Strict entry 36 reaches
  node 62 on King's Row. Native execution verifies output clearing, tag
  matching and typed append for a single candidate. The strict action writes
  ordered Entity rows from explicitly supplied candidates and follows its
  output. Native execution verifies node 43's world identity comparison.
  The strict fixture verifies targeted 0702 registration on channel 16.
  Strict 0C8F list selection now executes with a declared random word.
  The child tag States refresh while active. Node 76 resolves to the native
  base State. With declared host services, King's Row entry 36 starts 0694
  and switches between 0694 and 0695 when configuration presence changes.
  A shared permission slot connects the strict receiver to a Practice Range
  combat fixture. Actual rifle damage persists when 0694 is withdrawn,
  while passive and damage charge stop and resume with the broadcast.
  One Biotic Field also heals through withdrawal and restoration, with
  healing charge gated by the same permission.
  Live role binding, the native random stream, registry traversal and full
  Skirmish hosting remain open.
  An actual Sleep Dart suppresses rifle input while the wait still grants
  25 points before wake. Fixed-pose sequential ticks and idle world gaps
  agree. A rifle hit after wake adds 20 combat points.
  All five manual PvE attacks create real wounds without granting Soldier
  combat charge on receipt. The passive wait pays during the live Assassin
  pin. After attacks stop, Field restores the full wound and adds one
  healing point per HP alongside the continuing passive clock.
  Actual rifle hits against Heavy Assault and passive ticks earn Visor
  from zero. The final passive award fills it during the Assassin pin,
  which still blocks activation. A fresh press after release spends
  charge, corrects an off-axis shot onto Assassin and runs to expiry.
  Actual Flashbang before Field launch cancels the pending canister
  without spending cooldown. A fresh launch starts the authored
  cooldown and an early re-press is rejected until it expires.
  Actual Biotic Grenade verifies Field suppression and expiry, and friendly
  direct healing and Field amplification. Fixed-pose replay also preserves
  Field healing order when an admitted grenade hits inside a scheduler gap.
  A hostile grenade passes through Wraith to Soldier behind it, suspends
  Field, and allows healing to resume at anti-heal expiry. Normal Reaper
  intercepts and takes the hit. Cryo-Freeze and Transcendence stop the same
  throw without HP loss or a healing modifier.
  Actual throws verify world occlusion, splash range, and uninterrupted Field
  healing through intact and breaking Projected Barrier contacts.
  Actual Defense Matrix input also intercepts the grenade and preserves
  normal Field healing on a wounded Soldier behind the mech.
  Actual Field casts open and heal during active enemy Matrix and Grasp under
  the server's tag 0145 exclusion. Each volume admits Helix on the same
  server ray. The native filter and canister flight and placement remain open.
  Actual Deflect preserves Field ownership with a reflected Helix control.
  Reinhardt's raised mesh stops server canister flight without damage or a
  change in Field timing or position. The native contact continuation reaches
  a separate client callback. Isolated runs verify its config gate and packet
  forwarding. The downstream receiver suppresses duplicates and forwards a
  projected point to native submission. That submission has missing-config,
  directional-rejection and directional-admission witnesses. A supplied
  descriptor reaches B2D000 with the point and identifiers. Its no-parent
  path runs native preparation and appends an initialized client queue
  record with preallocated storage. Isolated consumer paths group, mark and
  retire the fresh record. Supplied instances verify update argument routing,
  and a supplied handle table verifies matching and stale asset generations.
  The update callback body and native placement remain open.
  Separate original-code probes verify the one-shot projectile finish and
  low-speed deadline paths with supplied inputs. The authored Field leaves
  the optional low-speed policy empty and bypasses both captured callers.
  The authored gravity also reaches original compile and vertical velocity
  arithmetic under supplied evaluation services. An isolated original step
  integrates position, applies the time and range bounds, and reaches the
  collision query boundary with supplied contact inputs. Original result
  arithmetic and caller position commit have isolated witnesses with
  supplied sweep results and query status. Original compile and query-write
  slices also carry the authored 097/011D and hit flags into the query record.
  The sweep's native resolver and filter builder now have coverage for
  GUID fallback, cache reuse and invalidation, and category encoding from
  097/011D. A separate slice checks the Field's two owner-filter gates
  with supplied candidates. The original two-pass handoff preserves the
  full filter for the second pass. A supplied first-pass hit shortens
  the second segment, and original tree traversal rejects disjoint
  leaves and synthetic category mismatches. Isolated original-code
  probes settle sphere-mode contact and the exact hit of a registered
  synthetic sphere. Category, geometry, fraction cap and subshape
  exclusions have separate witnesses. The mode, registry and scene
  geometry are fixture inputs. The same exact hit and two misses now
  feed native projectile result selection and position commit. A
  mode-2 control suppresses the client callback with the hit intact.
  The Field's authored response flags and zero default thresholds
  route the exact side hit to rebound and a supplied upward normal
  to the finish callbacks. Original code clears the finish marker
  after one entity update request. Native rebound now has velocity,
  displacement and count boundary evidence. A tenth rebound finishes
  the projectile with the prior count seeded to nine. The original
  loop reuses the rebound velocity for the remaining step, and a
  supplied opposite-wall result produces the next response on the
  same record. Original hit settlement clips time and distance and
  interpolates impact velocity. A half-step hit can finish early or
  leave 0.0625 seconds for the continuation. Retail scene queries
  and authoritative deployment remain open.
  Overlapping actual throws verify hostile refresh, anti-heal precedence,
  and the remaining friendly boost on expiry. One Field heals Soldier and
  all five allied PvE bodies after anti-heal expires, with and without
  amplification. Both update paths agree through Field expiry,
  cooldown rejection, and a recovered cast that fills all six pools.
  Two concurrent Fields also split seven actual grenade wounds
  consistently across stepped and jumped updates.
  The same holds when the first Field expires before the second during
  a jump longer than the combat replay window.
  Caster body replacement removes its Field from that split. Actual
  death leaves the Field healing living allies and suppresses charge
  for the dead caster.
  Field canisters stop on actual hostile Reinhardt, Zarya, Winston and
  Orisa barriers in the host. The shield pools stay intact and the
  deployed Field still heals an ally across the barrier.
  Actual Projected Barrier on each PvE body also follows team filtering.
  An enemy sphere stops the canister, while a friendly sphere lets it
  pass and the projected NPC heals.
  Field also heals actual rifle wounds during allied Wraith,
  Cryo-Freeze and Transcendence. Its team gate and credit remain
  independent of invulnerability and Mei's self-healing.
  Death and body replacement
  clear the modifier, while caster replacement preserves it.
  Native healing filters remain open.
  Ana Biotic Rifle has actual hip and scoped 70-point damage and healing
  doses on Soldier, including world-clock settlement and anti-heal expiry.
  Both modes have actual intact/breaking Projected Barrier and Genji
  redirection cases. Body replacement tests cover dose retirement and
  original killer identity.
  Both rifle modes deal and heal 70 on all five prepared PvE bodies, and
  actual Soldier Field restores the remaining damage in the same health pool.
  Native dose cadence and remaining cross-hero contacts stay open.
  Actual Flashbang now interrupts Sprint/Field
  windup and suppresses input; deployed Field/Helix continue, while active Visor
  preserves its countdown and resumes firing. Native interruption/immunity
  classification, other control/defenses and the full matrix remain open.
  Flashbang and Sleep Dart each allow a fresh Sprint after control ends.
  Projected Barrier preserves active Sprint on both hits, even on break.
  The complete static milestone stays unchecked.
  All five actual NPCs now have authored Field deployment/healing witnesses,
  including ally/enemy/dead/wall/range, expiry and cooldown recovery. Separate
  raw-slot and manual-controller incoming-weapon tests retain their different
  host policies; neither establishes complete NPC AI.
  Native distinct-input joins are imported and all dedicated Soldier methods
  rechecked; pending arrivals/producer context now affect command replay hashes.
  This does not close the remaining combat matrix or native scheduler fidelity.
  Scoped Helix self splash now has actual-input damage/credit/death/Field-heal
  evidence and all-five-NPC enemy/ally/range checks. Wall impacts across
  both Helix rings verify host damage, charge, force and occlusion on all
  five. Native knockback, resistance and collision/filter equivalence
  remain open.
  A scoped force adapter now executes player/four-NPC displacement and all-five
  raw capsule physics, collision/settlement, route recovery and causal snapshots.
  Heavy's ability receiver remains unsupported; native resistance/classification,
  status overlap order and the complete cross-hero matrix stay open.
  Scoped Genji resource-box interception now returns tagged rifle/Visor/Helix
  with original range/lifetime/payload and new owner credit, blocks frontal melee
  and preserves un-intercepted splash. Returned rifle reaches all five NPC bodies;
  three controller weapons return through Deflect. Rejected ability presses keep
  their original buffer deadline. Native boxes/filter/callback/history/visuals,
  all remaining reflection combinations and complete static acceptance stay open.
  Actual Genji-returned Helix/Visor now reach all five NPC bodies with team/credit
  filtering and original-caster replacement/range/lifetime tests. Scoped Sigma
  Grasp consumes known003 attacks and runs authored damage sum/conversion/cap/
  wait/decay expressions with separate unhealable extra shields. Actual Field
  retains only base-loss healing eligibility. Five NPC protection chains and
  four actual NPC incoming weapons execute. Native profiles/filter/pool/visual
  semantics, remaining defenses and the full matrix remain OPEN.
  Original192-triangle Reinhardt mesh now receives actual rifle/Visor/Helix and
  melee damage with live capacity/regen/break resources and same-impact cover.
  Five actual NPC offset/raised/lowered/allied and breaking-hit controls, three
  incoming NPC weapons, real Flashbang/death/retirement and causal storage have
  scoped evidence. Heavy's old force exclusion is replaced by actual1254/state0
  modifier26=0; Rein's actual0.7 passive and their isolated removals execute.
  Callback cancellation/cooldown-zero dependency repairs permit real broken-
  shield recovery. Native actor/mesh/profile/attachment/clock equivalence and
  every other remaining full-static requirement stay OPEN.

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

- [ ] Complete Soldier versus Assassin/native NPC incoming acceptance.
  Manual1157 context/action36 and scoped contact/continuous damage now have real
  Soldier control/lifecycle/Field/Visor witnesses; native AI, contact, immunity,
  animation and clock rules remain open. Original AI slot0=20 is unchanged.
  See [Soldier manual Assassin evidence](SOLDIER_ASSASSIN.txt). The complete
  static and original-client milestones remain unchecked.

- [ ] Complete cross-hero view/exit ordering acceptance. Soldier03CF original
  standing/crouching offsets are confirmed; McCree02A3 offsets now govern ray
  origin and Target.eye with actual low-cover witnesses. Actual Roll/held-primary
  versus Soldier and all five NPCs reproduces and repairs refill sampling order.
  Native camera/bones, cross-object order, Roll motion and full scope remain open.
  See [cross-hero view and exit evidence](SOLDIER_CROSS_EYE_ROLL.txt).

- [ ] Complete camera/pose geometry acceptance. The configured external33-body
  catalog corrects29 base-body fallback differences with actual32-hero stance/
  rifle/low-cover and active Genji/Sigma/Reinhardt interception witnesses. Native
  camera/bones/attachments, critical zones, posture eligibility, dynamic scale and
  other forms remain open. See [resource view offsets](SOLDIER_VIEW_OFFSETS.txt).
  Explicit 0407 selection loads mech scripts, the 300+300 pools and the 0.7
  mover radius when the supplemental data is mounted. Actual Matrix input
  intercepts Soldier rifle, Helix and Visor fire toward all five prepared NPC
  targets and four NPC weapons fired toward Soldier. Call Mech,
  native possession and remaining NPC incoming and special cases stay open.
