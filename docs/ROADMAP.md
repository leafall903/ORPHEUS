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

Mesh-dependent static acceptance requires the pinned collision meshes.
The Orisa snapshot case declares this prerequisite and verifies both
flight and landed state with the pinned 104319 mesh.
An actual Reaper shell verifies the tick before Protective Barrier
lands and the landing tick, with matching results across skipped updates.
Actual Helix Rockets also preserve flight order across landing.
A rocket already beyond the future barrier can hit Reaper,
while a later arrival hits the new barrier.
The same flight scheduling preserves an earlier interception
when a world update crosses the barrier's expiry.
An in-flight Helix Rocket also preserves its impact frame and
Experimental Barrier's position when the moving shield parks.
A parked Experimental Barrier also resolves competing projectiles
in contact order. Sleep Dart can reach it before an earlier-fired
Helix Rocket and spend shield health before that rocket breaks it.
With Experimental Barrier and Kinetic Grasp active together, an
actual Helix Rocket spends only the first defense it reaches.
Stepped and skipped updates also preserve the handoff to
Experimental Barrier when Kinetic Grasp expires in flight.
An actual Helix Rocket reflected by Genji can also be absorbed by
an allied Sigma before its splash reaches Reaper. Stepped and
40-tick skipped updates agree on the interception tick and shield grant.
Helix Rockets also preserve Deflect's expiry boundary. The earlier
rocket reflects and splashes Reaper. The next hits Genji and leaves
Reaper at full health. Both paths preserve attribution across gaps.
Eligible Helix Rockets also preserve Defense Matrix release timing.
The last active tick absorbs the rocket. A later flight reaches
Reaper and splashes the mech.
An active Death Blossom also transfers damage to the barrier on landing.
Stepped and 200-tick skipped updates match the hit frames and Reaping.
After a break, the next scheduled Blossom pulse reaches Orisa.
Actual shells resolve overlapping Protective and Projected Barriers
in contact order. A forward barrier blocks the whole break shot,
while intersecting surfaces split the shell.
Death Blossom damages the two barriers independently. After both
break, the next scheduled pulse reaches Orisa in stepped and
200-tick skipped updates.
Staggered personal and projected Zarya barriers let Death Blossom
reach each body on its own expiry tick. A lethal first body pulse
on Zarya leaves the projection active until its original deadline.
Helix Rockets also preserve Projected Barrier expiry across skipped
updates. A rocket that reaches the surface at expiry travels one more
tick to Reaper's body.
Winston's deployed dome likewise preserves its last interception.
At expiry the original flight continues from the dome surface to Reaper.
Ice Wall expiry preserves the last interception for Helix Rockets and
Sleep Dart. After the wall expires, the dart applies sleep from the
actual body hit frame.
Reinhardt's raised barrier now releases on the idle world clock.
A Sleep Dart crossing the release boundary retains its shield hit
or reaches Reaper and starts sleep from the body hit frame.
Brigitte's barrier also releases on the idle world clock.
Helix Rockets preserve the shield interception and subsequent
body contact across skipped updates.
Photon Barrier confirmation now reaches its host from the shared
placement state. Actual deployment, Reaper damage and passive
snapshots pass with the pinned meshes.
An actual Helix Rocket also preserves Photon Barrier's last
interception before expiry and can hit Reaper after expiry.

- [ ] Complete Reaper's static combat acceptance for all weapons, melee, passive,
  Wraith Form, Shadow Step and Death Blossom, cooldown/resource rules and
  bidirectional enemy-player/five-NPC/cross-hero interactions. Shotgun, Reaping
  and bounded Wraith reconstruction have direct partial execution evidence;
  actual Wraith movement/ammo and manual reload/near-melee tests also pass.
  Native button-edge correction prevents Wraith-blocked Shadow Step VM loops;
 0B8B damage/passive charge arithmetic and own-skill interlocks have executed
  subset evidence; native charging eligibility/target filters remain open.
  Bound passive charge now advances without input, does not double count future
  commands, and excludes Blossom time through expiry/interruption and skipped
  world ticks. Tick/remainder observations and standalone fallback are tested;
  original native/server-clock equivalence remains open.
  Damage charge now applies the authored Roadhog and PvE tag factors,
  including tag priority and lethal overkill against the five Talon
  bodies. Actual Reaper shells exercise target exclusions, including
  the 1503 stack and controlled damage-event tags. Practice Range
  faction and D.Va post-armour credit have direct evidence. Outer
  target filters and broader native mode admission remain open.
  Actual Echo Q now copies an eligible Reaper in the server model.
  Copied primary, Reaping, Wraith, accelerated damage charge and
  Death Blossom have direct evidence. Lethal counterfire breaks the
  copy without an elimination, and retained expiry stops its
  channel. Against Reaper, copied Soldier: 76 has direct evidence
  for rifle and Helix damage and charge, Wraith rejection, idle
  cooldown recovery and retirement of in-flight Helix at copy
  expiry. Copied Tactical Visor redirects the rifle and expires
  during idle updates, while Helix keeps manual aim. Deflected
  Helix changes ownership and can hit restored Echo after expiry.
  Kinetic Grasp and Defense Matrix protect Reaper without feeding
  copy charge. A fresh launch hits Reaper after Matrix release
  and cooldown. Reaper's lethal shell breaks the Soldier copy
  and retires its outbound Helix. Visor target selection, defense
  geometry and body-scoped projectile retirement use reconstructed
  server rules. Native transitions and further copy profiles remain open.
  No-input Wraith/Zen/Mei scopes now advance authored ends/recovery/cooldowns via
  completed missing windows, with stale-input rejection, owner quanta/epoch and
  replacement checks. Blossom acquisition after enemy Wraith expiry matches
  contiguous/skipped windows; native transport/rollback/history fidelity is open.
  Reaper interactions with bounded Zen/Mei protection and resource-driven healing
  now have direct checks for actual gun damage recovery, friendly range/LOS/team,
  cap/stats/health sync, cancel/recast/death, no-input/jump and slot-order rules.
  Native healing integration/filter/mode/activation rules and antiheal/boost/
  cleanse beyond the scoped Ana grenade consumer, wider defense fidelity and
  cross-hero interactions remain open. Normal Ana modifiers now have direct
  pellet/Blossom/Zen/Mei, Wraith-cleanse and Shadow-phase checks. Actual five-NPC
  mover rows replace the previous fallback for capsule range/LOS verification;
  map collision caches and full NPC AI/specials/native hitboxes remain open.
  The Enforcer's 25-projectile volley now has direct contact evidence
  against Wraith, both sides of Shadow Step immunity and a live or
  lethal Death Blossom channel. Retaining the current volley through
  a 270-tick gap agrees with contiguous Wraith playback. A
  300-tick gap also preserves later launch and damage frames
  through Wraith, manual engagement, autonomous acquisition and
  pursuit. The 512-tick retained combat window is a server policy.
  Longer gaps, native targeting and pursuit remain open.
  A single actual Enforcer volley breaks a weakened Projected
  Barrier during Death Blossom. Later pellets from that volley
  reach Reaper. Stepped and 28-tick skipped updates agree on the
  barrier contacts and lethal channel interruption.
  The Assassin's 1157 pin uses the same retained window. Its
  sustained damage and lethal frame match through a 270-tick
  gap. Tests cover pursuit after Wraith, a new pin after target
  death, and fresh contact after Reaper body replacement.
  An autonomous pin also persists through a Projected Barrier break.
  The break tick spends the shield, and body damage starts one tick later.
  The input bridge and native target arbitration remain open.
  The Sniper uses the same retained clock. Tests match shot,
  damage and death frames after a 300-tick lethal retarget, a
  235-tick reposition and a 350-tick gap across Wraith expiry.
  With autonomous targeting, the Sniper can break a projection, kill
  Reaper with the following round, then acquire and hit Zarya.
  Destination selection uses a reconstructed server policy.
  The Sniper's allied Sigma can absorb a reflected round for 60 shields.
  With Grasp idle the Sniper takes 100 damage. Both outcomes agree
  across a 180-tick update gap.
  Native aim and real Rialto trajectories remain open.
  The autonomous Enforcer keeps its 120-tick volley cadence through a
  projection break. Both launches and the per-pellet contacts agree
  across stepped and skipped updates.
  The Trooper's primary and melee contacts and Heavy Assault's
  charge, Wraith rejection, lethal Blossom interruption and
  mech wall impact also match long skipped updates. All five
  Talon profiles now use the bounded combat window for active
  sandbox encounters. Native AI and wider mixed cases remain open.
  The autonomous Trooper switches from rifle to melee to break Reaper's
  projection, then resumes rifle fire at range. Reaper can return fire
  and reap after engagement stops.
  Heavy Assault's primary fire also follows confirmed Shadow Step.
  Its autonomous charge can kill a channeling Reaper or spend 50 points
  on Projected Barrier. A protected Reaper keeps dealing Blossom damage.
  It hits during windup, releases input during protection, and
  reacquires Reaper at his destination. A 140-tick skipped update
  preserves the targeting and damage frames and Reaper can return fire.
  Mercy Valkyrie now gives a chained damage boost to Reaper from
  a valid primary beam. Shell damage follows range, cover and
  release. Death Blossom follows Valkyrie expiry, effective loss
  and Reaping. Actual yellow beam heals Reaper through Wraith.
  Valkyrie secondary healing and Reaping are both blocked by
  enemy Ana grenade and resume after expiry. Valkyrie expiry
  leaves normal primary healing. Effective staff healing now
  reaches the authored 0B8B charge branch. Antiheal, full health,
  Valkyrie and explicit eligibility gates reject awards.
  Reaper's boosted shotgun, melee and Blossom enter the staff
  006B damage event. Its amplification action and 00EB output
  run in the VM. Reaper charge uses base loss, while damage
  credit and Reaping use full effective loss. Valkyrie and the
  victim's #6442 gate deny Mercy charge while amplification
  remains recorded. Hit message production and ordinary victim
  filters are reconstructed. A selected Mercy who contributes
  effective damage to a lethal Reaper attack is recorded in
  the 03E7 death assist array and 01CF observer frame.
  Assist lifetime across attacks and staff feedback remain
  open, along with other victim filters and independent effect
  scripts. A Reaper shell reflected by Genji resolves the
  reflector's current blue beam and credits the resulting
  base loss and amplification to Genji and his Mercy. Other
  reflected attacks and native modifier transfer remain open.
  Sigma Grasp absorbs that boosted return before body
  damage or charge. A fresh shell hits Sigma after Grasp
  ends while Deflect and the beam are still active.
  Grasp grant and decay records retire on death or body
  replacement. Actual Reaper melee and shells exercise
  both death phases and a fresh grant after replacement.
  A Helix Rocket also samples the final Grasp shield fraction
  at its original contact tick across skipped updates. That
  fraction preserves a low-health Sigma for one more tick.
  Death Blossom samples Nano Boost and Fortify separately
  when their protection expires during one channel. Orisa's
  idle body clock preserves pulse damage and Reaping through
  a 200-tick skipped update.
  With outgoing Nano Boost and Mercy's blue beam, the first three
  Blossom pulses deal 61.2 each. Beam release drops the next three to
  51, and Nano expiry restores 34 for the final nine.
  Against Fortify, three blue-beam pulses deal 26.52 each and fund
  Mercy with 18.36 charge. The next three deal 20.4 after beam release,
  and the final nine return to 34 after Fortify expires.
  Mercy credit during Blossom also follows the Immortality Field floor.
  A capped pulse earns zero or six depending on actual marginal loss.
  The next pulse can kill when protection expires before device removal.
  Incoming Helix Rockets also preserve Nano Boost's expiry.
  The last active tick halves direct and splash damage.
  Helix Rockets preserve Discord's cover grace. The last
  amplified tick can be lethal when the expiry tick is survivable.
  Sleep Dart preserves Fortify expiry across skipped updates.
  Orisa rejects sleep on the last active tick. At expiry the
  dart starts sleep and a later Reaper shell begins waking her.
  Sleep Dart also resolves Transcendence and Cryo-Freeze
  expiry on contact. Their last protected ticks reject damage
  and sleep. The expiry ticks start sleep on the actual hit.
  Immortality Field ends its health floor before device
  teardown. Death Blossom on the expiry tick kills the
  formerly protected target in stepped and skipped updates.
  Helix Rockets also preserve that boundary. The last protected
  tick leaves Reaper at 25 health. The expiry tick is lethal
  while the device is still present.
  Helix Rockets preserve the last two points of a decaying
  Sound Barrier. A 119-health Reaper survives on that tick
  and dies if the rocket lands on the depletion tick.
  Landing starts protection when the authored target tracker
  is active. Blossom hits lethally before landing and when
  the target branch is disabled with a controlled input.
  An eligible overlapping field preserves the floor at the
  first expiry. The fallback range check and lethal tick
  agree across stepped and skipped updates.
  Actual Blizzard flights and landed areas preserve Shadow
  Step immunity, hit frames and frost across a 235-tick
  skipped update.
  A reflected Sleep Dart in flight also resolves against the
  confirmed Shadow Step phase at contact. A 120-tick update
  preserves the windup interruption and the protected miss.
  Chain Hook also preserves its flight and pull across a
  150-tick update. It interrupts Shadow Step before immunity
  and passes through the protected pose.
  An already-running Reinhardt Charge also keeps its pose and
  contact through 150 idle ticks. A vulnerable Reaper is pinned
  and takes 300 at the wall. During Shadow Step immunity the run
  crosses the departure point and the teleport lands. Both
  results agree across roster order and update cadence.
  An already-launched Rocket Punch also keeps its swept contact
  and wall damage through 150 idle ticks. Vulnerable windup takes
  the direct and wall hits, while the protected cast lands.
  Contact, motion and cooldown agree across roster order and
  update cadence.
  Ground and aerial Seismic Slam also land after owner input
  stops. Their wave ticks, damage and recovery match across
  contiguous and 230-tick skipped updates. Wraith Form remains
  protected through the wave even if it expires inside the gap.
  With autonomous specials enabled, Trooper and Heavy Assault
  re-evaluate the attack range for a visible retained Reaper.
  The authored melee and charge connect after the range changes,
  and Reaper can answer with a shell and Reaping. The selection
  policy and the controlled position change are fixtures for
  static acceptance.
  Heavy Assault's carried-mech collision now observes Ice
  Wall expiry before the combat pass removes the pieces.
  The last live tick produces the authored impact. At expiry
  the mech passes through and is released when the charge
  times out. Flanking Reaper takes only the side hit.
  Confirmed Shadow Step now checks the locked destination
  on its completion tick across a 165-tick update. A pillar
  top supports the last active tick. At expiry the cast
  consumes its cooldown and Reaper stays at the origin.
  A Reinhardt Charge pin on the confirmation frame also
  closes the Shadow Step cast record. No teleport follows.
  The damage and cooldown agree across update cadences,
  and a later gap does not replay the cancelled cast.
  A live allied Discord Orb also amplifies Death Blossom up
  to its cover expiry. Aligning the second pulse on either
  side of the boundary moves the kill from the second to the
  third pulse. Reaping, death attribution and charge agree
  under contiguous and skipped updates.
  A second Death Blossom pulse against an authored Sound
  Barrier also matches across skipped updates. The last two
  shield points let the target survive at one health. With
  the shield already depleted, that pulse is lethal.
  Actual Sound Barrier and Grasp can coexist on Sigma.
  With equal pools at the first Death Blossom pulse, the
  later-created pool is spent first. Spending Sound Barrier
  first leaves fewer points to decay and increases Reaping.
  Both creation orders agree across contiguous and skipped ticks.
  The passive server snapshot distinguishes temporary shield
  priority when both pool values and IDs match. The next
  actual Blossom pulse spends the pool in the later slot.
  Two pellets hit Genji directly while eighteen are reflected.
  Frontal Grasp absorbs the return. A rear return hits Sigma.
  A reflected shell can break a parked Experimental Barrier.
  Later pellets resolve against Grasp or Sigma's body.
  A reflected shell now has direct Death Blossom interruption
  evidence. Lethal return ends the channel with Genji credit.
  The surviving Reaper continues to pulse and can kill Genji.
  A reflected Sleep Dart now has direct Projected Barrier
  expiry evidence during confirmed Shadow Step. The last
  shield tick protects the landing. At expiry the dart
  interrupts the cast and starts sleep at body contact.
  Two reflected Sleep Darts also resolve across a projection
  break during Death Blossom. The shield spends the first.
  A later dart applies sleep and ends the still-live channel.
  A reflected shuriken fan resolves each of its three 29-point projectiles
  against the current shield pool. The shield-breaking shuriken has no
  spill. A later lethal body hit ends Blossom.
  A reflected fan also resolves across Kinetic Grasp expiry. One shuriken
  can feed Grasp while two hit bodies. At expiry the formerly absorbed
  shuriken reaches Reaper instead.
  The last Grasp tick absorbs a reflected dart and Blossom continues.
  After expiry the dart reaches Reaper and ends the channel.
  Projected Barrier takes 50 from a reflected Biotic Grenade and blocks
  the impact effect on Reaper even when the same contact breaks it.
  Defense Matrix absorbs the reflected grenade on its next flight
  tick. Without Matrix, Reaper takes 60 and receives four seconds of
  antiheal. Stepped and skipped post-reflection updates agree.
  Kinetic Grasp also intercepts a reflected Biotic Grenade before its
  impact effect. The 50-point projectile grants about 30 temporary
  shields. With Grasp inactive, Reaper takes 60 from the normal effect
  and receives four seconds of antiheal with Genji as source.
  Reflected Sleep Dart now resolves Matrix release with Grasp behind it.
  When the return starts inside both volumes, the server sweep chooses
  the first defender it visits. Both orderings protect Reaper and agree
  across stepped and skipped updates.
  The same tie rule applies when the dart starts outside both volumes
  and reaches their aligned front faces at a positive distance.
  At a tie between Projected Barrier and Grasp, the barrier spends five
  health. Without projection, the same dart feeds Grasp instead.
  The barrier also wins when the two front contacts tie at 0.5 m.
  Moving the Grasp face closer makes Grasp absorb instead.
  Matrix absorbs on its last active tick. Earlier release lets Grasp
  absorb the dart and grant Sigma three shields. Both protect Reaper.
  Overlapping Biotic Grenades give Blossom three Reaping phases.
  Antiheal blocks it, then the remaining friendly boost gives 17.85 per
  pulse. After the boost expires, each pulse heals 11.9.
  Biotic Grenade impact now resolves on either side of Wraith
  expiry across a skipped update. Only the first vulnerable
  tick admits damage and the four-second antiheal.
  Genji's world clock preserves Deflect expiry on incoming
  Blizzard contact. Stepped and skipped updates agree on
  area ownership, placement and Reaper's resulting damage.
  Sigma's world clock also preserves Grasp expiry on
  incoming Blizzard contact. The last active tick absorbs
  the throw, and the expiry tick lets the area form.
  D.Va's mech releases Matrix on the idle world clock.
  Explicit release and missing input agree on an actual
  Blizzard flight and its effects on Reaper.
  A rear Reaper shell also retires Matrix interception
  before an in-flight Blizzard arrives. The corpse and
  explicit pilot replacement paths agree across skips.
  All five Talon bodies now have executed blue beam charge,
  Reaping and same-attack assist evidence through their
  decoded 0C18 death frame. Baptiste Matrix placement also
  reaches its existing 1902 confirmation route and the five
  Talon exchanges run again.
  Authored Reinhardt Charge now has static evidence for interrupting Blossom,
  carrying Reaper into a 300-damage wall impact, crossing Wraith without
  contact, and releasing the pin on either body replacement or a lethal
  shell. Tests cover Shadow Step before immunity, during immunity and after
  landing, plus a lethal shell during Charge windup.
  Winston Primal Rage has actual evidence for 1540 charge, 1000
  total health, idle expiry, 40-damage fist contacts, Wraith immunity,
  Death Blossom continuation, Projected Barrier and lethal Reaper
  counterfire during windup. Tests cover three Shadow Step phases,
  Flashbang during windup, missing poses, pending fist cleanup at
  expiry, charge resumption and
  simultaneous contacts against Reaper and active Genji or Sigma.
  The five Talon bodies settle to matching poses after an 85-tick
  gap and take Reaper follow-up damage. Fist contact and force use
  reconstructed server policies. After Primal, Tesla Cannon resumes
  damage, ammo use and 0B8B damage charge. Tests cover Wraith, Shadow
  Step, Blossom, cone/range/faction, shields, Ice Wall, Deflect,
  Grasp, reload, death credit and all five Talon bodies. Native beam
  selection and timing remain open. Jump Pack now has actual launch,
  landing, normal cooldown, Primal air recast, Wraith and Shadow
  protection, Blossom continuation, Projected Barrier, Flashbang
  interruption and lethal Reaper counterfire. Reaper and all five
  Talon bodies settle through a 235-tick gap. Closed-frame landings
  now sample the same-frame protection state across roster orders,
  including Shadow Step and Wraith edges. Zarya's overlapping live
  barriers expire separately without input. Native Jump Pack physics
  and wider mixed cases remain open.
  Roadhog Whole Hog has actual evidence for charge, the authored
  16-pellet volley, collision-swept displacement, protection windows,
  and lethal Reaper counterfire. It moves Reaper during Death Blossom.
  Projected Barrier, Defense Matrix, Kinetic Grasp and Deflect resolve
  before body force.
  All five Talon bodies settle consistently across an 85-tick gap and
  Reaper can hit their resulting poses. Pellet spread, omitted critical
  scaling and force integration use reconstructed host policies.
  The Immortality Field floor preserves health and allows body force
  on Reaper and all five Talon NPCs. Projected Barrier spends its pool
  before body force begins. Destruction of the device removes the
  floor during the same cast.
  Player knockback now settles to the floor without owner input.
  Reaper's contiguous and skipped updates agree through landing and
  fresh input. Wraith removes residual force, Flashbang retains its
  original stun deadline, and a reflected Roadhog volley exercises a
  temporary world clock for a body without an existing anchor.
  Sigma Gravitic Flux has actual Reaper evidence for selection,
  faction, range, static cover, Wraith before and during lift,
  Blossom continuation, and lethal Reaper counterfire before and
  after descent commits. The graph deals 50 on entry and 125 on
  Reaper's ground contact. The 310-tick owner-input gap agrees with
  contiguous updates. Flux interrupts the confirmed Shadow Step
  windup, and the immune phase rejects selection. A late Projected
  Barrier takes the slam, and either body replacement retires the
  effect. All five Talon bodies receive the authored effect. Four use
  their collision movers for lift and slam, while Heavy Assault takes
  the entry hit through the 6601 branch and stays grounded. Skipped
  updates, Projected Barrier, lethal Flux and Reaper follow-up are
  covered. Graviton moves Reaper and Trooper laterally while Flux
  controls the lift and slam. A live Halt MoveTo also pulls Reaper
  and the four liftable Talon bodies laterally. Tests distinguish
  an active MoveTo from a pending Halt whose movement never starts.
  A Whole Hog volley during the lift changes Reaper's lateral
  landing position. It preserves Flux's 125-point slam and agrees
  with stepped playback across a 270-tick gap.
  A volley after descent commits also shifts the landing without
  changing the first grounded tick or the slam frame. Two
  consecutive volleys during descent each apply a fresh impulse
  and preserve the slam frame. Two Roadhogs firing from different
  directions one tick apart also preserve the slam when the
  second volley combines with residual force from the first.
  The single-volley control lands with less lateral displacement.
  A volley on the last airborne tick delays the slam by one tick.
  A volley on or after the baseline slam tick preserves its timing.
  All three cases retain Sigma's entry and slam damage credit.
  Native placement, targeting and physics
  semantics and broader mixed-control cases remain open.
  Actual Zarya Graviton Surge now has Reaper evidence for cast cost,
  projectile flight, field damage and expiry, continuous Death Blossom,
  and Wraith Form escape from damage and pull with reacquisition on exit.
  Confirmed Shadow Step still lands at its chosen destination. Matrix
  and Grasp prevent the field, Deflect transfers ownership, and a
  projected barrier takes damage while Reaper continues to move.
  Actual Graviton and Halt can both control the confirmed windup.
  Halt clears at immunity, and the ongoing field hits again after
  Reaper reaches the chosen landing. Stepped and skipped updates
  agree on position, health and hit frames through expiry.
  All five Talon bodies use their existing collision movers. Flight,
  pull and expiry agree across a 310-tick gap, including the next Reaper
  hit. Trooper can fire while pulled and records a lethal Reaper return.
  Manual and autonomous Trooper fire also agree across a 72-tick gap.
  Heavy Assault's Charge windup ends and enters its authored cooldown.
  An otherwise available Charge request starts when the field expires.
  An active capture releases a carried D.Va mech without impact damage.
  The server interprets the 0261 scalar as a 3 m/s pull and uses capsule
  visibility and collision movement. Native aura hosting, force and
  visibility semantics, and broader status interactions remain open.
  Actual Orisa Halt now covers second-press and wall detonation, 0CCE
  movement without damage or stun, Wraith rejection and cleanup,
  Blossom continuation, cover, faction, range and cooldown. A lethal
  shell can stop Orisa before release. The host flight and visibility
  query are reconstructed. Confirmed Shadow Step keeps its destination
  through a pre-immunity pull, rejects acquisition during immunity and
  accepts the pull after landing. The post-pull slow has expiry, Wraith
  cleanse and body replacement evidence. Frontal Matrix and Grasp
  consume the flight. Deflect changes ownership and subsequent pull
  eligibility, and the original caster loses manual detonation.
  Four Talon bodies move and pursue at reduced speed after Halt.
  Heavy Assault rejects the pull. Trooper fire and lethal Reaper
  counterfire cover the surviving and terminal slow states.
  Halt can acquire a projected Reaper during enemy Blossom without
  spending the barrier. Cryo-Freeze rejects and clears Halt, while
  Transcendence keeps damage immunity during the pull.
  Missing owner frames settle active pulls through the collision mover.
  Contiguous and skipped world updates agree through pull, wall contact,
  post-slow expiry and input resumption. A retained flight window also
  covers world and range detonation, Deflect, and Wraith at impact.
  Talon navigation and weapon updates keep their order through the
  retained wall impact. Reaper fire uses the resulting NPC positions.
  The post-Deflect remainder covers same-step Matrix absorption and a
  second Deflect with travelled-range accounting.
  Actual Orisa Supercharger now covers ally shotgun and Blossom boosts,
  effective Reaping and charge, cover/range reacquisition, expiry during
  Blossom and enemy shell, melee and Blossom damage. A lethal Reaper shell
  can stop the charged cast before release. Flashbang cancels the same
  windup unless Fortify is active, and preserves flight or the landed
  device after release. Placement, recipient selection and nearest-surface
  area queries are reconstructed. Actual Helix now covers direct and
  splash device damage, cover, team, Grasp, reflected allegiance and a
  Reaper shell finishing the device. Reflected shells and Helix use
  Genji's Supercharger bonus at contact, with the launch bonus tracked
  as causal state.
  Actual Baptiste Immortality Field now covers the 10 percent health floor,
  ally/range/cover gates, effective shotgun damage and charge, direct
  device damage, Blossom destruction and lethal follow-up after expiry.
  Actual Sound Barrier now has self/ally delivery, shield-only shotgun
  damage, Blossom spill, Reaping and scoped charge filtering, depletion
  and expiry, body retirement and lethal pre-grant interruption.
  Actual Lucio Soundwave now covers damage and displacement without
  interrupting Blossom, Wraith, cover and range, ally projection,
  cooldown reuse and lethal Reaper counterfire. Actual Shadow phase tests
  distinguish push, immunity and landing. A push during Flashbang retains
  the original stun clock, and a breaking projection absorbs the impact.
  Actual Meteor Strike now verifies charge refusal and spending, selection,
  confirmation and timeout, inner and outer damage, Wraith, projection and
  Reaper's return fire after the protected descent. Confirmed and timed-out
  Meteor impacts now settle without owner input and across retained
  world ticks, including a same-window Wraith expiry.
  Outer-ring Meteor impact ends Death Blossom on a lethal body hit.
  A survivor keeps pulsing after impact. Projected Barrier can absorb
  all 107.5 damage and preserve the channel at 100 health.
  Actual Seismic Slam has ground and aerial landing evidence, Wraith
  immunity, cover and faction checks, projection after release, and lethal
  Reaper interruption before landing. Its wave advances across a tick gap.
  The ground wave hits Reaper one tick before Shadow immunity and
  passes without damage on the first immune tick. Both cases preserve
  the confirmed teleport across stepped and skipped world updates.
  The aerial cast lands at the selected point before releasing its wave.
  Its Shadow immunity boundary, landing and teleport agree across stepped
  and skipped updates.
  Ground and aerial waves also resolve against an active Death Blossom.
  Nonlethal displacement preserves later pulses, as does a hit absorbed
  by Projected Barrier. A lethal hit ends the channel without a refund.
  Actual Rising Uppercut now lifts both actors, deals 50 without stun and
  preserves running Blossom. Wraith, cover, faction, range, direction and
  projected-barrier cases exercise the authored search and hit path.
  Uppercut damages and lifts Reaper before Shadow immunity and after
  landing. Immunity prevents both. The windup hit preserves the locked
  destination, and impulse cleanup agrees across a 180-tick update gap.
  Actual Doomfist Rocket Punch now covers tap and full charge, wall bonus,
  Wraith rejection, Blossom interruption, return fire into the temporary
  shield, and lethal Reaper interruption during windup. Three Shadow
  phases, projection on break, cover, range, team and cooldown reuse
  also have actual command evidence.
  Actual Sigma Accretion now damages and interrupts Blossom, respects Wraith
  and static cover, and matches contiguous flight across a retained tick gap.
  A rock still within Reaper's capsule when Wraith expires can hit
  immediately. A rock that already cleared the capsule misses.
  Direct contact reports 30 direct damage and 40 splash damage on the same
  tick and stuns the target. A nearby Reaper takes 40 splash damage and
  knockback without stun. Wraith rejects both splash damage and knockback.
  In the reconstructed blast LOS rule, side Projected Barrier blocks both
  effects without losing health, even with 20 health remaining. A direct
  rock collision instead spends up to 70 shield health.
  In-flight projection and lethal Reaper fire on either side of launch
  also have actual evidence. The host uses the authored ballistic operands
  and tagged control request. Further tests distinguish all three Shadow
  phases, projected-barrier break, front Deflect and Grasp, and Matrix
  exclusion with a nearby splash-only recipient.
  Actual Flashbang verifies Reaper immunity windows, Blossom/Shadow interruption,
  cooldown/charge preservation, recovery, no-input expiry and lifecycle cleanup;
  native status-manager/cleanse/other control and defense cases remain open.
  the complete matrix and original-client acceptance remain open.
  See [Reaper combat](REAPER_COMBAT.txt).

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

- Reaper Sleep Dart subset: actual0BDD/224E/676B duration/wake reconstruction
  executes; native filter/message/mode and full control/NPC matrix stay open.

- Manual Enforcer13EB25 projectile subset and actual Shadow17/18/19 input
  lock now execute. Native controller/phase/muzzle/difficulty and Assassin/full
  autonomous NPC matrix remain open; see REAPER_COMBAT.txt.

- Manual Assassin1157 pin has resource-based damage/control/lifecycle evidence
  against Reaper. Reaper crouch/McCree opponent eye heights corrected from XML.
  Full static matrix and original-client differences remain explicitly open.

- [ ] Reaper full static barrier matrix: normal Reinhardt0095 actual hitscan,
  health/break/recovery/reuse plus passive snapshot/geometry identity verified;
  projectile/melee/Blossom consumers, other families and wider lifecycle remain open.
  Active shield pose/Pet health-presence/time dependency notification are declared
  server reconstructions. Original-client acceptance separate and unperformed.
