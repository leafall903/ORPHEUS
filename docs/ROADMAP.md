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

- [ ] **Tracer hero lane 0003.** Full player/PVE combat and original-client
  acceptance remain open, with a separate active goal for full static combat.
  Uncommitted work now supplies NPC owner-health queries from synchronized
  combat pools, including initial auxiliary pools and death. Five actual classes
  have direct input coverage; foreign queries/native pool policy and remaining
  controller gates are open. Further own commits wait for full static acceptance.
  Thirty-two actual-graph server fixtures cover paired pistol damage, reload,
  melee, incoming Soldier fire, reconstructed Blink displacement/collision and
  charge lifecycle, selected shared health/death and training-NPC lifetime.
  Tracer and Soldier quick melee verify two-way aim, range, static cover
  and team checks, including held or released input during cooldown.
  Tracer's melee verifies the same gates and input recovery against
  all five NPC classes.
  Recall recovers from a second actual attack by each NPC after
  cooldown expires, using the new position and restoring health and ammo.
  Recall also verifies first-impact ordering for all five NPCs,
  including otherwise lethal hits and the Assassin's pin lock.
  Tracer's pistol, melee and Pulse contact damage or kill a
  Soldier stunned by allied Flashbang, with return fire after expiry.
  The same three attacks shorten sleep after the wake grace
  period for all five NPCs hit by allied Ana.
  Pulse splash also verifies damage during grace, a shortened
  deadline after grace and sleep cleanup on death for all five.
  Soldier PvP verifies the same sleep rules for pistol, melee,
  Pulse contact and splash, with rifle input at the wake boundary.
  Actual Ana grenades block or boost Harmony on all five NPCs
  wounded by Tracer, with normal healing at expiry.
  Hanzo's ordinary bow arrows now use the projectile defense path.
  Actual draws verify Winston interception and full-draw Deflect,
  Matrix, Grasp, Blink and Recall interactions with Tracer.
  Full draw also verifies Reinhardt, Sigma and Orisa mesh
  interception, team and cover gates, and Zarya projected-barrier break.
  Bow launch consumes the authored acceleration. Sonic and Storm
  arrows verify direct hits and Winston interception. With an explicit Sonic
  profile, body attachment, wall reveal, range exit and expiry pass.
  Winston interception creates no reveal field. An explicit local server
  option mounts the external profile for each match. Short-draw and combined
  hits verify reveal in server-created matches. Deflect transfers ownership
  and absorption prevents reveal. Death, departure, body replacement and
  overlapping fields pass. Hanzo marks all five Talon classes, and allied
  Tracer exchanges damage with all five marked targets, including Assassin's
  pin. Combined ricochet deploys only at terminal impact. Dragonstrike's
  initial arrow verifies charge, timing, range and lethal interruption.
  Configured moving dragons verify range-end and wall continuation,
  team, Blink, Recall, original source attribution, expiry and departure.
  Winston stops the initial arrow, while moving dragons pass his dome and
  damage Zarya's projected barrier. Batched movement and a skipped arrow
  transition preserve the tested damage samples. Confirmed movement batches
  also preserve both Recall protection boundaries. Moving dragons hit all
  five Talon classes while excluding allied Tracer. All five verify return
  attacks, lethal credit and corpse retention. Deflect changes the initial
  arrow's owner, and Grasp and Matrix absorb it. Moving damage passes all
  three defenses.
  Storm arrows bounce once and retain their projectile, with
  post-bounce hits, cover, team, Blink, Recall and projected barrier checks.
  Combined Sonic and Storm arrows also bounce once. Sonic spends one
  of five Storm charges. Sustained fire, cooldown rejection and a fresh
  five-arrow volley after cooldown pass.
  Blink verifies the first-impact frame boundary against all five NPCs.
  Early movement evades committed projectiles and the first pin, while
  the two hitscan attackers re-aim at the landing position.
  Pulse Pistols verify critical damage and kill credit against the
  standing Tracer bind sphere and the five NPC height classifiers.
  Native multiplier resolution and region fidelity remain open.
  Actual Soldier Helix verifies Tracer hit, Blink, Recall, cooldown,
  team and cover. Winston dome, Deflect, Grasp and Matrix resolve
  the ordinary rocket. Biotic Field verifies allied healing, Blink
  range transitions, expiry and recast. The default Visor search
  now aims actual rifle fire, with Blink, Recall, death, cover,
  range and team checks. Visor Helix keeps manual aim and can hit
  a Winston dome. Actual rifle, Helix and Biotic Field fill the
  Visor meter. Rifle damage can earn charge after Visor ends.
  Sprint verifies moving pistol hits, input release and Pulse
  following through death. Alternate searches, native selection,
  remaining defenses and Sprint controls remain open.
  Reconstructed Recall covers history/health/ammo, invulnerability and cooldown
  cancellation/reuse; five actual-class NPC tests cover outgoing primary/melee/
  bomb, team filtering, death and retention with authored capsules. Nineteen
  Pulse Bomb fixtures cover reconstructed flight/stick/fuse/blast,350/450 mode
  branches, LOS, self damage, lifecycle/cancel, Recall detachment and spending.
  Eleven ultimate fixtures execute authored0B8B passive/damage gain, exclusions,
  multipliers and caps through reconstructed host queries. VM/server snapshots
  retain Recall/Pulse state. Long skipped-frame recovery and full native charge
  filters remain open. Complete Blink controls, Recall cleanse/native timing, critical
  defaults/spread, complete cross-hero effects and five-class NPC acceptance are
  unaccepted. Five selected actual NPC attack graphs now damage Tracer and stop/restart
  around Recall, using an explicit reconstructed manual-controller declaration
  for Enforcer and a distinct typed Assassin controller Entity/foreign1650 target.
  Twelve actual Assassin fixtures cover pin/health/action rejection, pursuit,
  Recall/Pulse, support attacks and cleanup; native AI/controller/clock/animation
  remains open. See [Assassin scope](TRACER_ASSASSIN.txt) and
  [manual context](ENFORCER_MANUAL_CONTEXT.txt). A full-width static partition
  blocks both attack directions for all five manually controlled NPC classes.
  Removing the partition restores damage in both directions. All five NPC
  bodies pass empty-fire rejection, reload recovery and separate Pulse
  attachment, contact damage, fuse and blast checks. Recall restores
  position, health and ammo after a hit from each manually controlled NPC.
  Blink crosses all five NPC capsules without damage and can be reused
  after replenishment. Both attack directions work after landing. A static
  wall clips the cast before each NPC, with full travel after cover removal
  and a new press.
  Five-class melee wall
  exclusion and earlier14 prepared-runtime NPC fixtures are historical evidence.
  Current Soldier input verifies 20 torso damage, 40 standing head damage,
  the corresponding critical flag and static head-shot cover. Crouched and
  animated critical regions remain open. Actual
  Pulse 0034 applies the authored +0.2 vertical launch bias. Eight previously
  failing contact, attachment and falloff methods pass at their original
  distances. Animated hardpoint and aimID transforms remain open.
  Eleven Winston barrier and four eye/low-cover cases pass. Shared baked ramp
  and actual Tracer stored-state replay pass; the two Winston actor replay cases
  are historical pre-ramp-layout evidence. Full server-group acceptance is open.
  Five buffering fixtures verify the shared Ability timestamp correction.
  Shared throttle17-19 consumption now passes7 actual-resource Tracer fixtures,
  including natural00CB expiry and next-pose restoration. Native attack/status
  dispatch and complete controls remain open; see
  [movement scope](TRACER_MOVEMENT_MODS.txt). All47 mover tests pass against existing
  immutable original-runtime map caches. Actual Winston00FA primary interception
  is repaired under reconstructed sphere rules. Actual Pulse sticks from both
  sides, detaches on retirement, and breaks the shield without blast spill.
  Actual Genji return shots use the new team's sphere admission. With D.Va
  Matrix and Winston's bubble active, the nearer defense resolves Pulse first.
  Sigma's active Kinetic Grasp now consumes actual Tracer pistols and Pulse
  and grants a separate temporary shield pool. Experimental Barrier deploys,
  recalls, recharges and redeploys in the Tracer fixture. Actual pistols,
  quick melee and Pulse interact with the verified C015 collision mesh.
  Accretion now hits Tracer for 70, interrupts for 50 ticks and applies
  wall-clipped knockback. Recall protection, wall splash and cooldown reuse
  have actual-input fixtures. Gravitic Flux now verifies static placement,
  caster flight, selected Tracer lift and slam, Blink and Recall interactions,
  and caster interruption by Tracer. Hyperspheres verify paired direct
  and expiry damage, wall bounce, Blink and Recall, and selected live
  defenses. Native control and placement, NPC Flux,
  remaining matchups, pet and wire behavior remain open.
  McCree primary and Fan now verify actual damage, ammo, range and
  static cover, reload, Flashbang, Blink and Recall. Native cone identity,
  critical defaults remain open. Deadeye now verifies actual lock and
  release, cover, Blink, Recall, interruption and retirement. Combat Roll verifies
  movement, evasion, refill, held Fan, cooldown and interruption with
  actual Tracer and McCree inputs.
  Reaper's authored shotgun rings now resolve independent pellet contacts
  against Tracer. Actual hits cover partial static cover, refill, Recall,
  and Reaping from real health loss. Death Blossom verifies its full human
  channel against Tracer, Blink and Recall, a Winston bubble break, actual
  Flashbang and lethal Pulse interruption. Native spread and critical
  defaults and other Blossom defenses remain open. Shadow Step now checks
  actual selection, locked landing, cooldown and Tracer attacks across phase.
  Native destination adjustment and original-client behavior remain open.
  Mercy's actual staff input selects allied Tracer for healing or
  damage boost. Tests cover release, health cap, hostile exclusion,
  static occlusion, anti-heal and range. Boosted Tracer pistol, melee
  and Pulse settle through their usual paths, and hostile Mercy
  blaster and Tracer pistols trade damage. Guardian Angel flies
  toward live or dead allied Tracer, with Blink, cooldown, cover
  and death controls. Resurrect restores the same Tracer body,
  with interruption, temporary input lock and immunity controls.
  Valkyrie verifies charge admission, 3D flight with static
  collision, airborne pistol and blaster damage, Pulse attachment
  during flight and expiry back to gravity. Resource-owned Valkyrie
  chains heal and boost secondary Tracer, with range, cover, release,
  idle expiry and boosted Pulse launch checks. All five manual NPC
  classes can anchor or receive chained healing and hit Tracer with
  a chained boost. Enforcer and Sniper retain their launch boost
  after release, while Recall protects Tracer. Assassin pin damage
  follows the current boost. Native movement, revival, chain
  filters, autonomous NPC coverage and client acceptance remain open.
  Actual Lucio Soundwave damages and displaces Tracer, with cone,
  cover, team, Blink, Recall, projected-barrier and Pulse release
  checks. The primary burst and Tracer return fire settle damage.
  Sound Barrier uses the authored charge gate, grounded slam and
  status graph. Tracer can spend the decaying shield with pistols
  or Pulse and can Recall without refilling an allied shield.
  Lucio's 0B8B charge uses its passive clock, effective damage
  and Crossfade healing. An earned cast spends the meter and
  healing can charge it again after the ability ends.
  Crossfade and Amp now execute the extracted healing status and
  speed controller for Tracer. Tests cover Blink, cover, team,
  anti-heal, stun, idle expiry and cooldown reuse. Static Wall
  Ride verifies release jumping, finite edges, pistol and Pulse
  hits, and Flashbang interruption. All five manually spawned
  Retribution NPCs can receive healing and Sound Barrier. The
  Assassin approach uses ordinary and amplified Crossfade speed.
  Soundwave damages and displaces all five manual NPCs. Tests
  cover Tracer's air hits, the Assassin pin breaking beyond
  contact range, a static wall stop and pursuit after landing.
  Moving surfaces, native timing and resistance, other NPC
  locomotion and autonomous behavior, and client acceptance
  remain open.
  Moira Biotic Grasp verifies damage, self-healing and fuel recovery,
  Recall, barrier break, lethal interruption and actual allied spray
  with a trailing heal. Both Biotic Orbs now verify their selected
  Tracer effects, energy, bounce, shared cooldown, Recall, Deflect
  and absorption. Coalescence now verifies its separate target lists,
  piercing, defenses, Blink, Recall, interruption and recast. Native
  selection, mode branches and remaining pairings stay open.
  Actual Orisa Supercharger deploys and expires through the 104319 graphs.
  Tracer's pistol damage follows range and static visibility. Destroying the
  hostile device removes Orisa's primary damage boost. Actual melee and
  Pulse hit the mesh under tested range, cover and team rules. Lethal pistol
  fire during the windup cancels deployment. The aura also boosts Tracer's
  melee and Pulse. A Trooper's primary damage against Tracer drops from
  30 to 20 after she destroys the device. The device survives its owner's
  death and the new body's #842 lock gates passive refill until expiry.
  Actual input deploys again after refill. Native charge, aura overlap,
  other NPC interactions and client replication remain open.
  Actual Sombra EMP now breaks hostile Reinhardt and Zarya barriers and
  applies the Tracer lockout under static visibility and team rules.
  Native target filters, other EMP barrier types and full replay remain open.
  Pharah Rocket Launcher and Tracer have actual-input checks for direct
  damage, wall-side splash, Blink, Recall, pistol return fire and D.Va
  Matrix interception. Genji can reflect the same primary rocket, and its
  new team controls the next Matrix check. Sigma Grasp consumes the primary
  rocket and grants temporary shields that absorb subsequent damage.
  Winston, Reinhardt and Sigma shields stop the rocket and far-side splash.
  The breaking Winston impact protects Tracer through that hit.
  Zarya's projected barrier protects Tracer from the breaking impact
  and credits its caster for absorbed direct and splash damage.
  Barrage charge, channel lifetime and its first rocket have actual-input
  checks against Tracer, including team, cover and Recall.
  Tracer's lethal Pulse Bomb stops new Barrage rockets while released
  rockets continue flying.
  First-rocket Winston Barrier, Deflect and Grasp checks pass.
  Consecutive Barrage checks cover D.Va Matrix and Zarya's projected
  barrier through break and the next body hit.
  Fixed-aim Barrage verifies Tracer's fourth-rocket death, all 76 launches,
  natural channel end and final projectile retirement.
  Blink leaves the Barrage line before or after contact. Returning by
  Blink or Recall exposes Tracer to damage once protection ends.
  Concussive Blast now verifies zero-damage knockback against Tracer,
  with team, cover, Recall and Winston Barrier gates.
  Its cooldown, Matrix, Deflect and Grasp cases also have actual inputs.
  A Trooper and Tracer exchange fire after the push. Recall restores
  Tracer before Trooper damage resumes.
  Other rocket defenses, impulse and Pharah's remaining abilities remain open.
  [Winston scope and failures](TRACER_WINSTON_BARRIER.txt).
  Shared spread_ramp/layout is integrated:7 generic and10 actual Tracer spread
  methods pass, preserving per-report angles and first-pair zero cone. Native
  seed/animation/muzzle parity remains open. Actual Wraith Form and Fade
  let Tracer shots pass to a rear target. Cryo-Freeze blocks the shots.
  Melee, selected Pulse paths, Wraith cancel and reuse, and live Flux lift
  release are verified. Zarya's personal and projected barriers now spend
  actual Tracer damage and feed the caster's energy. The charged beam
  deals more damage to Tracer in the same fixture. Zarya secondary
  acceleration and Graviton deployment, world-clock pull, expiry and
  selected avoidance are verified against Tracer. Active pull rejects new
  Blink and Recall presses until release. Pulse Bomb can stick and detonate
  during capture, and the field persists after Zarya is killed. Live Deflect transfers
  Graviton ownership, Matrix and Grasp prevent deployment, and Winston
  bubble contact is verified. Native protection and
  the remaining matchups are open. Mei spray verifies piercing contact,
  partial slow, freeze, Pulse interruption, Recall cleanse and thaw
  recovery against Tracer. Blizzard verifies deployment, timed frost and
  damage, escape, death and selected projectile defenses. Ice Wall covers
  cast, expiry, pistols, melee, Blink and Pulse Bomb. Recall returns to a
  clear historical position across a new wall and selects a clear recorded
  point when a pillar occupies the original destination. Tracer can stand,
  jump and walk off pillar tops. Ice Wall and Winston barrier tests verify
  contact and blast in both orders. Native wall and field hosting remain open.
  Icicle verifies body and head hits, avoidance, selected defenses, ammo use,
  and release lifecycle. Native hit geometry and the remaining matrix are open.
  Zenyatta's Orb of Destruction verifies body and head hits,
  charged release and magazine limits, Tracer's Blink and Recall,
  death before release, Winston barriers, Deflect and Grasp.
  Harmony verifies targeted healing and expiry after occlusion.
  Discord verifies actual damage amplification, barrier denial,
  recovery and Recall cleanse. Transcendence uses its authored
  world deadline and verifies actual charge, Tracer healing,
  Pulse and Flashbang immunity and the return of damage after
  expiry. On all five extracted Retribution NPCs, verify Harmony healing
  and charge, Discord amplification for actual pistol and Pulse hits,
  unchanged NPC return damage, and Transcendence team and expiry gates.
  Transcendence healing beyond retained attack frames verifies actual
  Ana anti-heal, Winston dome expiry and Mei Ice Wall expiry.
  Harmony verifies grace, healing credit and actual anti-heal across
  long gaps. Discord expiry verifies actual Pulse damage in retained frames.
  Attached Pulse blasts before the retained window verify Discord scaling,
  Transcendence immunity and the death timestamp after expiry.
  Pulse already in flight at the start of a gap verifies the same
  boundaries, including separate Discord sampling for contact and blast.
  Native effects, movement and remaining pairings stay open.
  Roadhog's Scrap Gun primary verifies authored pellet count, one-round
  spend, lethal torso contact, miss, team and cover, and Tracer return fire.
  Secondary verifies actual slug damage, wall interruption, the authored
  fragment volley after expiry and the body-contact boundary.
  Shared ammo, manual and automatic reload, held-fire recovery,
  Winston dome interception, Genji Deflect, Matrix and Grasp are verified
  for the selected Scrap Gun shots.
  Chain Hook verifies actual player contact, damage, pull, control release,
  Blink and Recall avoidance, Winston dome, and cooldown reuse against Tracer.
  Range edge, Pulse release timing, death, departure, new cover
  and delayed inputs from the pull interval have direct tests.
  Chain Hook on all five Retribution NPCs verifies damage, pull,
  Tracer return fire, attack resumption, team changes, lethal cleanup
  and removal. An actual Pulse follows the pulled Trooper.
  Take a Breather verifies self-heal, the world damage deadline,
  actual pistol and Pulse mitigation, anti-heal, interruption,
  cooldown reuse, lethal cleanup and charge from effective self-healing.
  Whole Hog verifies charge from actual Scrap Gun damage, cast
  spend and end, ring hits, one force per target, player and NPC
  displacement, static cover, team, Winston dome, Defense Matrix,
  Kinetic Grasp, Genji Deflect damage and force, Flashbang interruption
  and lethal pistol interruption. Against Tracer, verify the 100 m
  contact limit, damage falloff and normal pellet damage on head contact.
  Blink evasion and Recall immunity both allow a later hit in the same cast.
  Native spread and the remaining Roadhog combat matrix stay open.
  The shared 01C7 server message path references 0F9C, which is absent from
  the local 104319 content manifests. Its native behavior remains open.
  Bastion Recon verifies bidirectional fire, range and falloff,
  trigger release, sustained fire, automatic and manual reload, and
  empty-magazine rejection. Recon and Sentry retain manual refill timing
  under Flashbang and only fire after both reload recovery and stun end.
  Self-Repair and Reconfigure verify both sides of the refill boundary
  and actual fire after returning to the weapon.
  Reconfigure verifies both transition locks, independent magazines and
  actual Sentry damage with Tracer return fire.
  Sentry verifies range, falloff, Blink evasion and Recall protection.
  Winston's dome and Genji's Deflect intercept both weapons.
  Defense Matrix and Kinetic Grasp also intercept both weapons.
  Reinhardt, Sigma, Orisa and projected Zarya barriers protect Tracer
  from Recon, Sentry and Tank.
  Both weapons hit all five extracted Retribution NPC capsules.
  Self-Repair verifies live healing, release, resource exhaustion,
  recovery, anti-heal and lethal Pulse Bomb interruption.
  Configuration Tank verifies charge from Recon damage and Self-Repair,
  authored passive charge, transition lock, a lethal shell and recovery.
  Tank shells verify 100 m flight, splash falloff, cover, team filtering,
  Winston's dome, Defense Matrix, Kinetic Grasp and Deflect.
  Tank expiry preserves launched shots and credit without charge.
  Body replacement retires leased and unleased shots against Tracer
  and all five NPCs. Nano and Winston dome expiry verify impact timing
  across gaps.
  Tank explosions verify owner damage and credit, outward impulses
  from walls and floors, and knockback against Tracer and surviving NPCs.
  The outer splash ring, breaking projected barrier, Fortify expiry and
  Nano mitigation verify damage and displacement separately.
  Fractional Self-Repair credit verifies adjacent charge values at 2310.
  Blink and both Recall phases evade the shell and splash. Tracer
  pistols and Tank shells both hit the five extracted NPC capsules.
  Flashbang blocks Tank fire and interrupts Self-Repair. A launched
  shell survives the shooter's death. Sentry damage earns a second Tank.
  Ironclad verifies incoming pistol damage through Reconfigure and Tank.
  Pulse Bomb verifies Ironclad separately at attachment and detonation.
  Hack and EMP block skills and interrupt repair, preserving weapon
  fire and all five mode transition clocks. Sleep Dart blocks Tank
  fire and repair through expiry, with reuse from fresh input.
  Late Tank sleep spans the return to Recon, where held or fresh
  fire hits after wake. During Recon and Sentry reload, Sleep Dart
  preserves the refill clock and held fire hits at wake.
  Lethal Tracer fire during either reload distinguishes pending
  refill from recovery. Recon and redeployed Sentry hit after respawn.
  Mei spray permits Self-Repair during partial frost and interrupts
  it at full freeze. Fresh repair input heals again after thaw.
  Sentry's rear core verifies exposed and occluded hits, rotated targets,
  and delayed Tracer shots against retained facing. Recon and Sentry
  headshots against Tracer exercise their respective critical behavior.
  Quick melee verifies Recon hit and rejection gates, return from
  Sentry and Tank, cooldown reuse, Blink and Recall, and Ironclad
  against Tracer's own melee.
  Ana's Sleep Dart verifies Tracer input suppression, Pulse Bomb interruption
  before release, survival after release, recast after expiry and restored
  charge, damage-driven wake and selected defenses. Biotic Grenade
  verifies enemy and ally impact, Soldier field denial and amplification,
  Recall recovery, cover and cooldown reuse. Nano Boost verifies Tracer's
  healing, incoming damage reduction and outgoing damage until expiry.
  Native presentation
  and the remaining combinations are open.
  The default-body roster probe verifies outgoing primary and Pulse damage for
  all 32 heroes. Winston's Tesla, Brigitte's flail, Zarya's beam and Symmetra's
  beam now damage Tracer. D.Va's pilot Light Gun brings the probe to 30 default
  weapon cases plus Mercy's selected pistol. Reinhardt's hammer separately
  deals 85 damage at 3 m after windup. Explicitly selected D.Va 0407
  executes Fusion Cannons and Micro Missiles. Actual Defense Matrix intercepts
  Pulse Pistol shots and Pulse Bomb under reconstructed admission. The 5 m
  hammer case misses with both hold lengths. A 4.5 m control hits for 85.
  Reinhardt Fire Strike verifies release, body and shield penetration,
  selected defenses, cooldown reuse and the caster-death boundary.
  Earthshatter verifies damage and control against Tracer across close
  contact, Recall, team and range filters, shield interception, jump-cast
  landing, representative terrain and Blink entry timing.
  Reinhardt's active shield intercepts Tracer primary and can break between
  pellets. Actual Tracer melee and Pulse use the prepared shield mesh.
  Native hammer reach, shield attachment and pet lifecycle, D.Va form
  transitions and the full player and five-NPC matrix remain open.
  Tracer's standing head sphere separates torso and critical rifle hits.
  Icicle, Orb of Destruction and Pulse Bomb verify crouched contact.
  Icicle and Pulse Bomb use stance at impact after a change in flight.
  Pulse uses complete batched poses for contact, fuse expiry, Recall
  detachment, Genji Deflect at contact and D.Va Matrix on each flight tick.
  Sigma Grasp samples the impact pose and resolves absorption before the
  same activation's timed shield conversion during a complete batch.
  Rear Flashbang preserves Pulse ordering and cancels the pending shield
  request. Preserve the cancelled activation's credit and cooldown, with
  actual early rejection and reuse on its ready frame. Winston's barrier
  honors the cast, deployment and expiry frames. Flashbang rejects a later
  cast and preserves a deployment already started.
  Crouch, animation and native damage-region consumption remain open.
  [Notification cleanup](TRACER_NOTIFICATION_LIFETIME.txt) repairs canceled-event
  coalescing markers and ending-state writes. Ability cleanup precedes arbitration
  unlink as an explicit reconstruction; native inter-object ordering stays open.
  The active-Deflect frontal-pistol failure is now repaired with17 direct
  command fixtures under reconstructed capsule/tag admission. Fourteen further
  command cases now cover a scoped reconstructed Pulse transfer/lifecycle
  service. Native box/filter/transform/redirect policy and reflected visuals
  remain open; see
  [Deflect evidence](TRACER_DEFLECT.txt). Full cross-hero acceptance is open.
  Selected passes do not close these remaining gaps.
  Native spread-growth72-case execution supplies implementation
  evidence. Nine further fixtures cover restored count-based bloom and
  reconstructed per-bullet cone rays, recovery and split wall occlusion. Native
  RNG/muzzle correspondence and critical sentinel resolution remain open.
  Thirteen direct Flashbang/Tracer fixtures cover selected stun rejection,
  interruption, Recall immunity and recovery; five actual NPC classes remain
  damageable while controlled and retire control on death. Early logical-button
  input is cleared to prevent ammunition loss during stun. All five manual NPC
  attacks resume after stun, including a fresh Assassin pin. Launched Enforcer
  and Sniper rounds retain their hits and attribution during stun.
  Genji Deflect returns both authored rounds. Frontal protection,
  rear and team rejection, and returned Sniper kill credit are verified.
  Sigma Grasp grants temporary shield for both rounds. The nearer
  of Grasp and Deflect resolves a Sniper round when both are active.
  Winston, Reinhardt, Sigma and Orisa barriers stop both rounds.
  Winston dome team filtering, expiry and reflected Sniper return
  contact are verified.
  D.Va Matrix verifies enemy absorption and allied pass-through.
  Zarya projected barrier verifies both rounds, shield break and
  energy from actual absorbed damage. A nearer Matrix absorbs an
  Enforcer round without spending the projected barrier.
  Trooper and Heavy Assault rays verify Winston dome, Deflect,
  Matrix and Grasp, including two Heavy rays in one frame.
  Reinhardt, Sigma and Orisa meshes verify both NPC volleys.
  A Heavy ray that breaks the mesh is stopped. Its paired ray
  hits Tracer on the same frame.
  Zarya's projected barrier verifies both NPC volleys and break energy.
  A breaking Winston dome and the projected barrier each take one Heavy ray.
  Lethal attacks from all five NPCs verify single death attribution,
  normal respawn and resumed damage to the replacement Tracer body.
  Pulse Pistols verify zero-, one- and two-hit pairs at 25 m against
  each NPC and one independently occluded ray at full bloom.
  Real pistol damage funds two lethal Pulse Bomb casts per NPC
  class from zero. Near-threshold misses and allied fire reject the cast.
  Actual pistol damage against a normally respawning Soldier also
  funds two lethal casts from zero in the player fixture.
  Pulse flight and splash verify static cover and team exclusion
  for all five NPCs. The splash cases measure capsule-surface falloff.
  Each class can kill Tracer before Pulse release to cancel the throw,
  or after release while the bomb survives and kills with Tracer attribution.
  Soldier's actual rifle and Helix inputs verify both outcomes in PvP.
  The manual Assassin pin verifies projected-barrier absorption,
  break and continued control. The current host's pin crosses
  active Winston, Reinhardt, Sigma and Orisa barriers without
  spending their health. Native pin admission remains open.
  The pin also persists while Genji Deflect, D.Va Matrix and Sigma
  Grasp intercept a separate Trooper's first ray.
  Lethal contact and continuous ticks clear the Assassin pin.
  Actual Mercy Resurrect delays repin until damage immunity ends.
  Tracer can Blink at the earlier rising-lock deadline.
  Respawn and live replacement verify fresh Assassin contact on
  the new body, including reuse of the old ID in the same frame.
  Active pins preserve lethal timing and respawn deadlines across
  skipped world frames, including Sound Barrier decay.
  Nano expiry matches contiguous damage across gaps. Sleep Dart
  releases a live pin, and Tracer's pistols exercise the wake clock.
  Released Pulse survives a new pin. Lethal detonation releases
  Tracer, with nonlethal and self-damage cases verified separately.
  Same-frame pin damage and detonation preserve both death causes
  and the respawn deadline across world update gaps.
  Other native status rules and controls remain open. See
  [Tracer combat matrix](TRACER_COMBAT.md).

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
