# Changelog

This log records tested changes and their acceptance status. Entries are grouped by date until a version is explicitly released.

## Unreleased


### 2026-11-11

#### Tracer lane integration handoff

- Preserve the existing Tracer combat implementation, shared server and
  StateScript changes, cross-hero and NPC interactions, and their tests.
- The integrated Winston regression passed 94 methods. The retained
  Reinhardt Pulse regression reproduces two failures with batched shield
  turns and release. Full static and original-client acceptance remain open.


#### Tracer and Hanzo

- Verify that all five marked Talon NPCs return damage to Tracer and
  that a combined ricochet deploys reveal only on terminal impact.
- Complete the authored Dragonstrike windup and recovery in the server
  simulation. Verify the initial arrow's charge gate, timing, hit, range
  and lethal interruption by Tracer. Mount profile-backed moving dragons
  at normal server startup. Verify range and wall continuation, Blink,
  Recall, source attribution, lifetime and departure.
- Sample complete Tracer movement batches at their original world frames
  and split fractional dragon births at the next frame boundary. Verify
  Winston dome interception and projected barrier absorption by phase.
- Preserve Recall activation and completion boundaries when Dragonstrike
  samples a complete batch of Tracer commands. Verify moving dragon damage
  on all five actual Talon bodies and allied Tracer exclusion.
- Verify Talon return attacks, death events and corpse retention. Admit
  the initial arrow to Deflect and Grasp, retain Matrix absorption and
  transfer reflected continuation ownership. Verify moving damage passes
  through the three active projectile defenses.

#### Tracer hit regions

- Verify Soldier's actual body hit, critical hit, high miss and cover
  against crouched Tracer. Verify that view delay uses the prior standing
  hit region after the target crouches.
- Verify crouched Icicle and Orb of Destruction hits and high misses.
  Verify in-flight stance changes for Icicle and Pulse Bomb attachment.
- Use complete observed input batches for Pulse Bomb contact and attached
  detonation. Verify stance changes, explosion position, damage and hit frame.
- Verify batched Recall detachment at the last unprotected pose and
  explosion immunity. Preserve Genji's contact-time Deflect state and
  redirect when later commands turn or start Deflect.
- Use D.Va Matrix state, eye and aim on each observed Pulse flight tick.
  Verify late activation after a stick and turning away after absorption.
- Use sampled Grasp bounds for Pulse while the same 1D02 activation is
  active. Verify a late press preserves attachment and a later turn
  preserves the 355-point credit and eventual 213-point shield grant.
- Apply a batched Grasp shield request on its original expiry tick, after
  earlier Pulse absorption. Verify cumulative pistol and Pulse credit,
  one grant, and the decay start against separate updates.
- Keep in-flight Flashbang and Pulse in world-frame order during batched
  Grasp. Cancel the shield request when Flashbang interrupts the same
  activation before its scheduled expiry.
- Align the interrupted Grasp cooldown and its wake with the Flashbang
  hit frame. Preserve prior absorption credit and verify early rejection
  and on-time reuse.
- Schedule Winston's pending barrier on its deployment frame and preserve
  earlier Pulse contacts when the barrier expires during a batch.
- Verify Flashbang rejects a fresh Winston cast without consuming cooldown
  and preserves the independent wait for a cast already started.

### 2026-11-10

#### Tracer and Zenyatta

- Resolve actual Orb of Destruction body and critical contacts,
  cap charged release by remaining ammunition, and route both
  orbs through projectile defenses. Verify Tracer's return
  fire, Blink, Recall and lethal charge interruption.
- Resolve Harmony healing and Discord damage on Tracer using
  the source orb graphs' search and timing values. Verify
  expiry, recast, Recall cleanse and Winston dome denial.
- Drive Transcendence from the authored cast and countdown.
  Verify Tracer healing, Pulse and Flashbang immunity, and
  damage after expiry with no caster input. Feed effective
  Orb of Destruction damage and Harmony healing to 0B8B.
- Verify Harmony healing and charge, Discord amplification of
  Tracer's pistols and Pulse Bomb, unmodified NPC return attacks,
  and Transcendence team and expiry gates on all five Retribution
  NPCs. The tests use server fixtures with manual NPC engagement.
  Native effect parity and original-client acceptance remain open.
- Settle Transcendence healing across world gaps beyond retained
  attack frames. Verify actual Ana anti-heal and healing resumption
  after Winston dome and Mei Ice Wall expiry. Use the latest known
  poses in the skipped interval. Original-client acceptance remains open.
- Settle Harmony healing and charge before retained combat frames.
  Verify occlusion and range grace, recovery after actual Ana anti-heal,
  and actual Pulse Bomb damage before and after Discord expires.
- Advance admitted Pulse Bombs with Zenyatta orb effects before
  the retained combat window. Verify Discord amplification,
  Transcendence immunity and death timing at the blast frame.
- Verify Pulse flight and attachment across the same gap. Contact
  and blast can fall on opposite sides of the orb effect's deadline.

#### Tracer and Roadhog

- Use the authored ring configuration to launch all 25 Scrap Gun
  primary pellets per round. Verify death, miss, team and cover
  gates and Tracer return fire. Native spread and original-client
  acceptance remain open.
- Emit the authored Scrap Gun secondary fragments when the slug
  expires. Verify close contact, the split boundary, cover and
  ammunition use. The expiry callback is a server reconstruction.
- Route Scrap Gun pellets and slugs through shared projectile defenses.
  Verify Winston dome, Deflect, Matrix and Grasp. Exercise shared ammo,
  both reload paths and held-fire recovery. Native filter parity stays open.
- Host Chain Hook from the actual launch state and pull graph.
  Verify damage, player pull and release, blocked Tracer inputs,
  evasion, cover, Winston dome and cooldown reuse.
- Preserve the Chain Hook input cutoff after release so late
  commands from the pull interval cannot fire or Blink. Verify
  Pulse interruption, range edge and release on death, departure
  and new cover.
- Host Chain Hook pulls on the five extracted Retribution NPCs.
  Verify attack suppression and recovery, Tracer shots during
  the pull, team and lifecycle cleanup, and Pulse attachment.
- Host Take a Breather self-healing and the timed damage stack.
  Verify Tracer pistol and Pulse mitigation across world gaps,
  actual anti-heal, interruption, lethal cleanup and reuse.
- Resolve Whole Hog's authored ring volley and force through the
  shared combat and movement paths. Verify charge earned by
  Scrap Gun damage, cast lifecycle, protection and displacement
  of Tracer and the five Retribution NPCs.
- Verify Defense Matrix and Kinetic Grasp absorb Whole Hog without
  damage or force. Verify Flashbang and lethal Tracer fire end
  an active volley.
- Verify Genji Deflect returns Whole Hog damage and force to
  Roadhog and credits the hits to Genji.
- Verify Whole Hog falloff, its 100 m contact limit and normal
  pellet damage on Tracer head contact.
- Credit effective Take a Breather self-healing through Roadhog's
  authored ultimate charge graph. Verify undercharge rejection,
  a self-heal-funded cast and zero charge under anti-heal.
- Verify that Scrap Gun damage can complete a buffered Whole Hog
  cast within 150 ms and that a fresh input is required after expiry.
- Verify Recall blocks Whole Hog damage and force through both
  active phases and that later volleys hit after Recall ends.
- Verify Blink evades Whole Hog's aim and that the active cast
  can hit after Roadhog reaims.

#### Tracer and Bastion

- Verify Recon damage, lethal attribution, miss, team, cover,
  Tracer return fire, range, falloff, release and manual reload.
- Verify Reconfigure locks fire until the selected weapon is ready,
  preserves separate magazines and allows Tracer return fire in Sentry.
- Verify Sentry falloff and range, Blink evasion, both Recall phases,
  and Winston dome and Genji Deflect against both weapon modes.
- Host Bastion's authored Self-Repair healing and health queries.
  Verify release, resource exhaustion and reuse, anti-heal and
  lethal Pulse Bomb interruption.
- Route Bastion damage and effective Self-Repair into the authored
  charge graph. Verify Tank admission, a lethal shell, expiry and
  the passive charge pause and restart.
- Route Tank shells through the existing projectile defenses and
  barrier splash checks. Verify flight range, falloff, team and cover.
- Verify Blink and Recall against a live Tank shell and hit all
  five extracted NPC capsules with both Tracer pistols and Tank shells.
- End Self-Repair through its recovery branch on control. Verify Tank
  stun, post-death shell attribution and reuse funded entirely by Sentry.
- Apply Bastion's active Ironclad filter to incoming damage before armor.
  Verify Tracer pistol damage across Bastion's mode transitions.
- Notify Statescript ammo watchers through volley depletion and retain the
  notification sample in VM snapshots. Verify automatic reload for Recon
  and Sentry, manual reload clocks under Flashbang and weapon reuse
  after Self-Repair or Reconfigure during reload.
- Admit the authored Recon and Sentry rounds to Sigma's Kinetic Grasp.
  Verify that Grasp and D.Va's Matrix protect Tracer from both weapons.
- Resolve the deployed Sentry rear core in the retained hitscan pose.
  Reconstruct the omitted Pulse Pistols and Recon critical multipliers
  and verify Tracer headshots before and after Fortify expiry.
- Verify Bastion's actual quick melee hit and configuration gates,
  Blink and Recall evasion, and Ironclad against Tracer's melee.
- Verify Recon and Sentry torso damage and notices on all five
  extracted Retribution NPCs.
- Verify all three Bastion weapons against Reinhardt, Sigma, Orisa
  and projected Zarya barriers. Verify Recon Blink and Recall
  evasion, and Ironclad on both Pulse Bomb impact events.
- Apply actual Sombra Hack to Bastion. End Self-Repair without
  trailing healing and preserve weapon fire and transition timing.
- Apply the same Bastion skill lock to actual EMP. Verify range,
  cover, team, active repair interruption and all mode transitions.
- Verify actual Sleep Dart interruption and fresh Tank fire or
  repair after the sleep deadline.
- Apply Tank's authored self-splash scalar and knockback rings
  to actual shell explosions. Verify wall and floor impacts,
  death credit and movement for players and manual NPCs.
- Verify the outer Tank splash ring, barrier break, Fortify expiry
  and Nano mitigation. Verify fractional Self-Repair credit on both
  sides of the Tank charge threshold.
- Retire leased and unleased Tank shells with the replaced body.
  Verify fire at Tank expiry, posthumous hits and all five NPC targets.
- Advance admitted Tank shells through skipped world frames so
  damage reads timed protections at impact. Verify Nano expiry,
  Winston dome expiry and endpoint-only admission.
- Verify a late Tank Sleep Dart blocks held fire through Recon
  recovery. At wake, held fire hits and a fresh trigger also works.
- Verify Sleep Dart preserves Recon and Sentry reload refill
  clocks and held fire hits at wake. Verify Mei's full freeze
  interrupts Self-Repair and fresh input heals after thaw.
- Verify lethal Tracer pistols cancel pending Bastion refill in
  Recon and Sentry. After refill, recovery keeps its deadline.
  Verify the separate Sentry exit refill and both weapons after respawn.

#### Tracer and Soldier: 76

- Route ordinary Helix Rockets through shared barriers and
  projectile defenses. Verify Tracer's Blink and Recall, return
  fire, and allied protection from Winston, Genji, Sigma and D.Va.
- Verify Biotic Field's allied healing, Blink range transitions,
  expiry and recast.
- Run the default Tactical Visor search and apply its targets to
  rifle fire. Verify charge, target loss, reacquisition, expiry
  and death. Keep manual aim on the Visor Helix variant and
  route it through projectile defenses.
- Feed Soldier's effective damage and Biotic Field healing to
  the authored charge graph. Verify an earned Visor, damage
  falloff during it and charge recovery after expiry.
- Verify Tracer's pistol pair against Sprint and Pulse Bomb
  following the sprinting body through detonation.

#### Tracer and Lucio

- Resolve Soundwave health and player impulse through the authored
  activation. Verify Tracer's Blink, Recall, projected barrier,
  return fire and Pulse release boundaries.
- Bind the Sound Barrier slam and status graph to temporary shields.
  Verify Tracer's pistol and Pulse absorption, shield overflow,
  Recall, cast interruption, expiration and recast.
- Run Crossfade's extracted healing and speed graphs. Verify
  Tracer's actual movement, Amp expiry and reuse, Blink range,
  anti-heal and healing while an active Amp survives a stun.
- Drive Lucio's Wall Ride and release jump through the authored
  graph on static surfaces. Verify Tracer pistol and Pulse hits,
  finite wall exit, and interruption by stun or death.
- Verify Crossfade healing and Sound Barrier on all five manual
  Retribution NPCs. Apply the authored speed modifier to the
  Assassin's manual approach to Tracer.
- Sweep Soundwave's authored impulse on all five manual
  Retribution NPC capsules. Verify midair Tracer hits, a wall
  stop, Assassin pin release and pursuit after landing.
- Feed effective Sonic Amplifier and Soundwave damage and
  Crossfade healing to Lucio's authored charge graph. Verify an
  earned Sound Barrier and charge recovery after the ability.

#### Tracer and Mercy

- Bridge 006B staff selection to authoritative healing and 006C
  outgoing damage boost. Verify allied Tracer's healing, damage
  attribution, beam release and loss, and boosted Pulse launch timing.
- Verify hostile Mercy blaster damage and Tracer pistol return fire.
- Bind 006E Guardian Angel's authored selectors and endpoint to
  friendly Tracer, including Blink, cooldown and flight interruption.
- Route 00CA Resurrect to the same dead player body with the authored
  cast, control and immunity timings. Verify return to combat and
  caster-death interruption, Valkyrie expiry and lethal Pulse.
- Apply Valkyrie's embedded mover profile and kind 16 flight hook.
  Verify pitched and vertical input, collision, airborne weapon
  exchanges, Pulse attachment in flight and the return to gravity.
- Preserve both Gravitic Flux and Combat Roll in the shared velocity
  action dispatcher.
- Mount Mercy's extracted staff root and Valkyrie chain effects. Verify
  secondary Tracer healing, damage boost, Blink range changes, occlusion,
  release, idle expiry and boosted Pulse launch.
- Verify Valkyrie chains with all five actual NPC classes. Remove
  attachments immediately when a manual NPC is removed and keep
  player ability cleanup out of the NPC stun path.

### 2026-11-09

#### Tracer and Pharah Rocket Launcher

- Verify actual rocket direct and splash damage, wall-side splash, range,
  allied rejection, and Tracer's Blink, Recall and pistol return fire.
- Admit Pharah's primary rocket to Defense Matrix from its authored
  volley state and verify interception before damage reaches Tracer.
- Use the same launch identity for Genji Deflect and verify the reflected
  rocket's damage attribution and subsequent Matrix admission.
- Verify Sigma's Grasp absorbs the primary rocket and grants 72 temporary
  shields, which the next unabsorbed rocket spends before base shields.
- Resolve primary rocket contact and splash against hostile Winston and
  Reinhardt barriers, including a breaking rocket and subsequent body hit.
- Verify the deployed Sigma mesh also stops the rocket and its splash.
  Cover friendly passage, barrier recall, cooldown rejection and redeployment.
- Verify Zarya's projected barrier absorbs a direct or splash-only rocket
  and gives Zarya energy for the shield health actually spent.
- Verify Pharah Barrage charge rejection, channel lifetime and first
  rocket damage against Tracer, including team, cover and Recall gates.
- Verify Tracer's lethal Pulse Bomb ends an active Barrage while released
  rockets continue their flight.
- Admit Barrage rockets to barrier collision and verify first-rocket
  interception by Winston Barrier, Genji Deflect and Sigma Grasp.
- Verify consecutive Barrage rockets against D.Va Matrix and Zarya's
  projected barrier, including the first body hit after barrier break.
- Verify a full fixed-aim Barrage against stationary Tracer through death,
  remaining launches, natural channel end and projectile retirement.
- Verify Blink evasion before and after a Barrage impact, plus damage
  resuming after Blink or Recall returns Tracer to the active firing line.
- Apply Concussive Blast's authored impulse to Tracer and Pharah.
  Verify zero damage and team, static cover, Recall and barrier gates.
- Verify Concussive Blast cooldown rejection and reuse, plus Matrix,
  Deflect and Grasp behavior for the zero-damage projectile.
- Verify Pulse Bomb releases from Tracer's displaced position when
  Concussive Blast hits during windup, and continues in flight when hit later.
- Verify Tracer and a Trooper exchange real fire after Concussive Blast
  displaces Tracer, and Recall restores her position before fire resumes.
- Verify Blink dodges already-launched Enforcer and Sniper rounds.
  Later NPC rounds reacquire Tracer, who can return fire after moving.
- Verify Enforcer and Sniper projectiles can hit after Tracer kills the
  shooter, including a posthumous Sniper kill credited to the NPC.
- Verify a lethal Sniper round cancels Pulse Bomb during windup, while
  an already released Pulse can still kill the Sniper and credit Tracer.
- Verify Recall protects Tracer from already-launched Enforcer and
  Sniper projectiles, with later rounds hitting after protection.
- Record manual NPC spawn and attack operations in controlled replay.
  Reproduce Tracer and Trooper damage in both directions from recorded
  operations and encrypted input.
- Verify real Trooper damage funds Tracer's Pulse Bomb and that misses
  or shots at an allied Trooper leave the cast undercharged.
- Verify a lethal volley credits only the Trooper's remaining health
  and further shots at its corpse spend ammo without adding charge.
- Verify a second Pulse Bomb is funded entirely by actual pistol damage
  and manual reloads after the first bomb ends.
- Verify pistol damage builds charge during Pulse flight and fuse, and
  the Pulse explosion itself adds no charge.
- Verify passive charge across gaps within the command catch-up window
  and reproduce the long-gap undercount in a command-only probe. Verify
  the hosted 256-frame gap releases fire without spending ammo and preserves
  charge phase. The 1250-frame undercount also occurs with continuous world ticks.
  An offline ordered replay matches the contiguous 1250- and 2500-frame
  charge totals and phase, with multi-tick synchronous replay cost.

#### Sombra EMP against Tracer

- Host the authored EMP target search and body application, including capped
  shield drain and the Tracer ability deadline.
- Verify cover, range, team and Recall gates, and distinguish unreleased
  Pulse Bomb from a projectile already in flight.
- Apply the authored EMP shield damage to raised hostile Reinhardt barriers.
  Verify static cover, team filtering and the lockout on Tracer behind the shield.
- Apply EMP shield damage to Zarya's personal and projected barriers without
  spilling into body health or granting energy.

#### Sombra Hack against Tracer

- Verify selective ability lockout, Pulse windup interruption and recovery.
- Verify channel rejection by static cover and Recall.

#### Ana Nano Boost with Tracer

- Use the authored team-mode Nano targeting and release for healing and eight
  seconds of damage amplification and reduction.
- Keep Pulse Bomb damage from the release frame through detonation. Verify
  charge and team rejection, expiry, pistol damage and quick melee.

#### Ana Biotic Grenade against Tracer

- Verify Recall's separate cleanse timing with actual Mei frost and Ana
  anti-heal active together. Frost clears on entry and anti-heal clears
  with health restoration at completion. Fresh spray applies frost again.
- Exercise the Recall graph abort in both phases with an actual grenade
  and Biotic Field. Anti-heal persists to expiry, after which the field heals.
- Use the authored gravity and apply four-second healing modifiers on impact.
- Verify enemy damage and healing denial, allied healing and amplification
  with Soldier's actual Biotic Field, and Recall health restoration.
- Verify static cover, Winston interception, Matrix absorption and cooldown reuse.

#### Reinhardt Fire Strike against Tracer

- Complete 00E2's empty windup marker so its authored timer releases
  Fire Strike. Use the direct hit extent and target limit to hit bodies
  once each. Verify Recall protection, cooldown rejection and fresh reuse.
  Killing the wounded caster cancels windup but leaves released shots live.
- Admit Fire Strike to tagged Deflect and Grasp. Verify ownership transfer,
  reflected armour damage, Matrix absorption and Grasp credit.
- Spend shield health once per Fire Strike on Reinhardt, Winston and Sigma
  barriers, continue to bodies behind them, and stop at static cover.
- Verify the order of Winston bubble damage versus Deflect and Grasp
  when either defense is in front. Two successive Deflects transfer ownership
  without refreshing lifetime or repeating damage to a previously hit body.
- Verify Fire Strike passes allied Tracer and stops at static cover.
  Host grounded Earthshatter using 00BE ground sampling, windup and a
  world-clock front. Verify ranged damage and control, lethal close contact,
  Recall protection, ally exclusion, range and static cover. Verify
  protection from Reinhardt, Winston and Sigma barriers. Verify early and
  late Reinhardt shield strafes. Sweep the moving Sigma mesh against the
  wave between combat frames. Verify ranged damage and stun protection
  from intact and breaking Zarya projected barriers. Exclude close-hit
  targets from the ground wave when the close hit breaks a projected
  barrier. Restore 00BE's jump-cast landing branch with its elapsed gate
  and check lethal Tracer fire before and after wave release. Verify Ice
  Wall removal on either side of ground-front contact and Pulse
  interruption before release versus a bomb that has already attached.
  Verify terrain propagation on small steps and a mild ramp, blockage at
  gaps, large ledges and a steep ramp, and Blink entry before or after
  the wave front passes.

#### Tracer against Orisa Protective Barrier

- Reuse the verified 4062.00C mesh and run the 0BD7 throw and 0BD8
  deployment graph. Verify Tracer primary, Orisa return fire, Earthshatter,
  body-switch retirement and authored expiry.
- Verify Tracer melee and Blink against the mesh. Handle Pulse contact,
  detonation, detachment on break and static cover.
- Resolve shield splash visibility before applying damage. Verify both
  Orisa and Winston shield orders and outer break during or before blast.

#### Tracer against Orisa Fortify

- Consume the live 0CF4 filter and control stack. Verify Tracer pistol,
  melee and Pulse damage, Earthshatter stun rejection, damage after
  expiry, cooldown reuse and Soldier critical suppression.
- Keep Sleep Dart and Accretion damage while Fortify blocks sleep, stun
  and projectile displacement. Verify return fire and control after expiry.
  Verify Fortify clears partial frost and freeze can build again after expiry.

#### Tracer against Orisa Halt

- Host the 0CCB orb and detonation message with verified 0CCC and 0CCE
  graphs. Verify Tracer movement, Blink and Recall, team and static cover
  gates, cooldown, body replacement, absorption and reflected ownership.

#### Tracer against Reaper Hellfire Shotguns

- Resolve Reaper's authored 20-pellet ring count and spread through
  independent hitscan rays using the same geometry as Fusion Cannons.
- Verify torso damage, partial and full cover, off-axis hits, range and
  team gates, manual reload, Recall and Blink after pose publication.
- Apply Reaping to actual hostile pool loss and verify both quick melees.
  Overkill and blocked pellets add no healing.

#### Tracer against McCree Peacekeeper

- Resolve Fan with authored cone angles retained per activation and
  deterministic 104319 table sampling under reconstructed seed identities.
- Verify primary and return fire, six-round damage and ammo, duplicate
  input, off-axis hits, range, cover, reload, Flashbang, Blink and Recall.

#### Tracer against McCree Deadeye

- Host the original 022F searches and verified 01EE target effects.
- Verify off-axis release, ammo, lethal damage, distinct lock and shot
  ranges, cover and barrier interception, Blink, Recall, cancel, expiry,
  Flashbang, caster death and body replacement.

#### Tracer against McCree Combat Roll

- Consume authored 01F0 velocity requests before collision-aware movement.
- Verify evasion and re-aim, ammo refill, held Fan, wall clipping, cooldown,
  actual Flashbang and lethal Tracer fire. Retain pending velocity in snapshots.

#### Tracer and Moira Coalescence

- Host the original 1007 ally and enemy searches and apply the separate
  damage, healing and self-heal operations.
- Verify piercing, cover, range, Blink, Recall, Winston Barrier, Deflect,
  Flashbang interruption, lethal pistol fire, natural end and recast.

#### Tracer and Moira Biotic Orb

- Host actual damage and healing releases, bouncing flight and a
  shared energy budget per orb. Verify Recall, static cover, expiry,
  selection cancellation, shared cooldown and release persistence.
- Verify Deflect transfers the damage aura, and actual Sigma Grasp
  and D.Va Matrix remove the orb before it reaches Tracer.

#### Tracer and Moira Biotic Grasp

- Publish the actual F76 damage target, apply its authored damage and
  self-heal, and recover fuel through the original hit expression.
- Host the authored spray packets and trailing heal. Verify ally,
  range, cover, fuel and release gates, Recall, barrier break and
  lethal interruption.

#### Tracer against Reaper Shadow Step

- Route actual selection edges to 00F2, validate and lock the static
  landing, and revalidate at completion.
- Verify cancel, early retry, cooldown reuse, wall and occupancy gates,
  and Tracer's pistol and Pulse interactions across phase. Lethal pistol
  fire and actual Flashbang prevent the pending teleport.

#### Tracer against Reaper Death Blossom

- Host the 0044 channel with 0045's human damage and cadence. Verify
  charge rejection, full duration, input gaps, Blink and Recall,
  static cover and team gates.
- Verify a Winston bubble break protects Tracer for that pulse, and
  actual Flashbang and lethal Pulse Bomb stop the channel.

#### Tracer and Orisa Supercharger

- Deploy the 200 HP device through actual ultimate input and run its
  authored lifetime and buff graphs with the verified 104319 mesh.
- Verify allied pistol amplification, range and static cover, enemy
  device destruction, loss of Orisa's damage boost and authored expiry.
- Resolve Tracer melee and Pulse contact and blast against the device.
  Verify fuse retention after a contact break, blocked splash, ally
  rejection and deployment cancellation by lethal pistol fire.
- Verify the allied aura boosts Tracer's melee, Pulse contact and blast
  and that Pulse retains the launch multiplier after Tracer leaves the aura.
- Verify a manually controlled Trooper hits Tracer for 30 under the aura
  and for 20 after Tracer destroys the device with pistol fire.
- Verify that lethal Tracer fire after launch preserves flight. Retain
  the deployed boost through death and replay the deployment callback on
  respawn. Respect its passive charge lock through expiry and verify recast.

#### Tracer against five NPC classes

- Admit the Enforcer and Sniper projectiles to Genji's tagged
  Deflect path. Verify frontal return, facing and team rejection,
  and Genji attribution when a returned Sniper shot kills.
- Route both tagged NPC rounds through Sigma's Kinetic Grasp.
  Verify shield conversion, rear rejection and nearer-defense
  ordering with active Genji Deflect.
- Route both NPC rounds through Winston, Reinhardt, Sigma and
  Orisa barrier contact. Verify dome team filtering, expiry and
  contact on either side of a Sniper round's reflection.
- Verify D.Va Matrix team filtering and Zarya projected-barrier
  energy and break behavior against both NPC rounds. Verify a
  nearer Matrix intercept leaves the projected barrier untouched.
- Route tagged Trooper and Heavy Assault rays through Sigma's
  Grasp. Verify the first volley against dome, Deflect and Matrix,
  including a two-ray Heavy return with Genji kill credit.
- Forward the Assassin pin's authored damage tag. Verify projected
  Particle Barrier absorption, energy and break during a live pin.
- Reject Assassin pin admission during Mercy Resurrect protection.
  Verify lethal contact and continuous damage, same-body recovery,
  Blink after rising and reengagement at immunity expiry.
- End the Assassin's old pin scope before reusing a Tracer body ID.
  Verify fresh contact after normal respawn and live replacement.
- Settle active Assassin pin damage on its combat frame across
  world update gaps, including Sound Barrier decay and lethal timing.
- Verify Nano expiry during an active Assassin pin and the sleep
  interruption, pistol wake and reengagement sequence.
- Verify a released Pulse Bomb sticks and detonates during an
  Assassin pin, including lethal release, nonlethal continuation
  and close self-damage.
- Verify same-frame pin and Pulse lethality under contiguous,
  24-frame and 100-frame updates.
- Verify Reinhardt, Sigma and Orisa meshes absorb actual Trooper
  and Heavy Assault rays, including a same-frame Heavy hit after break.
- Verify Trooper and Heavy ray absorption and energy on projected
  barrier break, and paired Heavy rays across both active shields.
- Verify all five actual NPC attackers credit one Tracer death and
  resume damage on normal respawn. Cover both lethal Heavy rays.
- Verify actual Pulse Pistol bloom against all five NPC capsules,
  including distant partial hits and full-bloom wall separation.
- Verify two lethal Pulse Bomb casts funded from zero by actual
  pistol damage against each NPC class. Extend miss and allied
  charge rejection to all five classes.
- Verify two Pulse Bomb kills funded from zero by pistol damage
  against a Soldier player who respawns on the normal timer.
- Verify Pulse contact and blast cover and team gates against all
  five NPC classes, plus falloff measured from capsule surfaces.
- Verify manual Assassin contact and continuous pin across active
  Winston, Reinhardt, Sigma and Orisa barriers in the static host.
- Verify the manual Assassin pin persists while Deflect, Matrix
  or Grasp intercepts a separate Trooper ray.
- Verify two-way Tracer and Soldier quick melee against aim, range,
  cover and team gates, including held and released cooldown presses.
- Verify those quick melee gates, cooldown presses and damage credit
  against all five actual NPC classes.
- Verify a second Recall after actual damage from each NPC class,
  including early rejection and a new rewind position.
- Verify Recall one frame before and on first NPC impact, including
  recovery from nonlethal damage, lethal rejection and the pin lock.
- Verify Tracer's three attacks against a Soldier stunned by allied
  Flashbang, including stun cleanup on death and Pulse after expiry.
- Verify pistol, melee and Pulse contact shorten actual NPC sleep
  after the wake grace period, with attack input resuming on expiry.
- Verify Pulse splash preserves or shortens sleep by grace timing
  and clears it on a lethal hit against all five NPC classes.
- Verify Tracer attacks against a player slept by allied Ana,
  including splash timing and the first accepted rifle input on wake.
- Verify enemy and allied Ana grenades block or boost Harmony
  on all five NPCs wounded by Tracer, including expiry and credit.
- Route Hanzo's full and short draw arrows through projectile
  defenses. Verify actual Winston, Deflect, Matrix and Grasp inputs.
- Verify full-draw arrows against three deployed meshes and Zarya's
  projected barrier, including break without spill and the next hit.
- Apply authored bow acceleration and route Sonic and Storm arrows
  through projectile defenses. Verify direct damage and Winston interception.
- Allow one Storm Arrow wall ricochet and verify post-bounce
  damage, cover, team and Tracer defense interactions.
- Preserve the Storm ricochet when a combined volley selects the
  Sonic projectile. Recheck state transitions after watched-variable
  changes so Storm ends at five arrows. Verify the combined charge
  budget, cooldown rejection and a fresh five-arrow volley.
- Run the authored Sonic reveal flag stack with an explicit
  reconstructed profile. Verify body attachment, wall reveal,
  range exit and expiry, and no reveal on Winston dome interception.
- Mount the explicit Sonic profile through local server startup. Verify
  short-draw and combined reveal in server-created matches and reject
  invalid profiles before lobby setup. Verify Deflect ownership and no
  field after Kinetic Grasp or Defense Matrix.
- Verify Sonic reveal at lethal impact, Hanzo death and leave, Tracer body
  replacement and overlapping fields. Verify reveal and allied Tracer
  pistol hits on all five Talon classes.
- Verify Blink one frame before and on first NPC impact, including
  hitscan re-aim, projectile evasion and the Assassin's pin lock.
- Verify Pulse Pistol critical damage and kill credit against the
  standing Tracer bind sphere and all five NPC height fallbacks.
- Verify lethal attacks from all five NPCs cancel Pulse Bomb before
  release and preserve the bomb and Tracer kill credit after release.
- Verify both Pulse release outcomes against actual Soldier rifle and
  Helix inputs, including bombs that attach after Tracer dies.
- Verify actual Flashbang interrupts all five manual NPC attacks,
  breaks an active Assassin pin and permits renewed attacks after stun.
  Already-launched Enforcer and Sniper rounds still hit, including a
  lethal Sniper round with NPC attribution.
- Verify both Blink inputs cross all five NPC bodies without contact damage.
  Check two-way combat from the landing side, recharge and a return Blink.
- Verify static geometry clips Blink before all five NPC bodies and that
  fresh input restores full travel after cover removal. Verify actual Blink
  against the cached Practice Range balcony railing.
- Verify Recall restores position, health and ammo after each actual NPC
  attack, rejects a recast on cooldown, and permits renewed combat.
- Verify empty-fire rejection and resumed two-bullet damage and charge after
  manual reload on all five authored NPCs. Verify Pulse attachment, contact
  damage, armed fuse and explosion separately on each body.
- Verify that a static partition blocks manual NPC attacks and Tracer pistol
  damage and charge against all five authored bodies. Removing the partition
  restores damage in both directions.
- Verify that team changes release active NPC attacks, clear an active
  Assassin pin, and reject allied damage and pistol charge for all five bodies.
- Verify range rejection at 105 metres and resumed damage in both directions
  at close range, including the Assassin's approach before pinning. At 25
  metres both pistol bullets deal 3.6 total and grant 3.6 charge on all five
  NPC bodies, while missed shots grant neither.

#### Ana Sleep Dart against Tracer

- Apply the 026F effect after Sleep Dart damage, interrupt pending abilities
  and record sleep separately from other control deadlines.
- Follow the 224E wake branches. Early damage preserves the deadline and later
  eligible damage caps the remainder at 0.5 seconds.
- Verify actual avoidance, cooldown reuse, selected defenses and death cleanup.
- Verify that an interrupted Pulse Bomb stays canceled until sleep expires
  and a newly charged cast succeeds. An already released bomb still sticks
  to Ana and detonates while Tracer remains asleep.

#### Mei Icicle against Tracer

- Use Tracer's standing head region for Icicle hits and reconstruct the
  omitted 2× Icicle multiplier. Cover and active defenses resolve before damage.
- Verify actual body and head hits, ammunition use, Blink and Recall, shield
  contact, Deflect, Matrix, Grasp, death before and after release, and retirement.

#### Mei Ice Wall against Tracer

- Use the actual Ice Wall preview and confirmation to place five independent
  pillars, with removal, expiry and cooldown.
- Route Tracer's pistol rounds, melee and Blink through pillar collision.
  Ordinary projectile splash and Blizzard now include Ice Wall in visibility.
- Verify actual Pulse Bomb impact, fuse, blast cover and detachment on wall
  removal. Recall reaches a clear recorded location across a newly placed wall.
- When a pillar occupies the Recall destination, use the first clear newer
  position captured at cast time, or retain the current position if all are
  occupied. Include pillar tops in the mover ground probe for jumping.
- Resolve Ice Wall and Winston barrier contact by order. The front surface
  blocks Pulse Bomb blast damage to the rear surface. Ice Wall also blocks
  Tracer melee from reaching a covered barrier.
- Apply Ice Wall cover to manual NPC selection and attack input. Actual
  Enforcer and Tracer fire resumes in both directions after Mei removes it.
- Revalidate Assassin's manual route against live pillars. Assassin can route around
  an active wall before pinning Tracer, and a new wall interrupts an active pin.

#### Mei Blizzard against Tracer

- Verify Blizzard keeps damaging and freezing after Pulse Bomb kills Mei.
  Tracer uses Blink to avoid self-damage while staying in the field.
- Verify Blizzard cancels Pulse Bomb before release without a refund.
  A bomb already attached detonates while Tracer is still frozen.
- Run the actual 064A snowball through ballistic contact and start
  the reconstructed 065E field on landing. Use the extracted damage
  and frost cadence, with independent exposure and freeze deadlines.
- Verify charge, Blink and Recall, Genji reflection, Matrix absorption,
  and death before and after release.

#### Mei primary frost against Tracer

- Accumulate source-scoped frost on actual spray damage, apply the
  movement slow and interrupt active input at the freeze threshold.
- Use the authored spray target count while preserving cover and barrier
  contacts. Verify Recall cleanse, Blink avoidance, Pulse interruption
  and replacement body cleanup.

#### Zarya projectile defenses around Tracer

- Admit the two projectile definitions through the active Genji boxes
  and the existing Matrix and Grasp queries. Reflection transfers
  ownership, team and the remaining flight budget, including the
  subsequent Graviton field.
- Resolve Winston bubble impacts on the approach side. A breaking
  secondary blast respects the original barrier cover.

#### Zarya Particle Cannon and Graviton Surge against Tracer

- Apply the authored secondary and Graviton projectile acceleration.
- Verify Blink crossing the active Graviton edge and next-tick capture
  when Blink lands inside the field. Reject new Blink and Recall presses
  during an active pull. First capture discards prior mobility input, including
  Recall buffered at the end of cooldown. Primary fire and fresh casts after
  expiry still work. Verify Pulse contact and detonation during pull, and
  field expiry after Tracer kills the caster with pistol fire. Two overlapping
  fields deal damage and maintain the mobility lock through the last expiry.
  Actual secondary splash, Blink evasion, Recall and cover are verified.
- Deploy the 0260 field from the actual 025F impact. The host applies
  the extracted 0261 damage and uses its pull scalar for world-clock
  movement, with expiry and source-body retirement.

#### Tracer against Zarya Particle Barrier

- Route actual personal and projected barrier hits to their 200-point
  activation pools. The absorbed loss drives Zarya's authored energy
  receiver. Projected Barrier checks ally, range and static cover.
- Verify Tracer pistols, melee, Pulse contact and fuse, and charged
  return beam damage. Pulse follows the barrier activation and
  detaches when that activation ends.

#### Tracer against Wraith Form, Fade and Cryo-Freeze

- Complete the three stock protection entry gates. The active phase
  stack removes Wraith Form and Fade from hostile contacts.
  Cryo-Freeze remains a solid damage-immune contact.
- Verify actual Tracer pistols, melee and Pulse, protection expiry
  and Wraith cancel and reuse. Fade detaches Pulse, and Wraith Form
  and Fade release a live Flux lift.

#### Sigma Hyperspheres against Tracer

- Run authored 187F sphere gravity, body and wall radii, bounce values
  and expiry blast. Retain the flight budget through bounces and Deflect.
- Resolve hostile shield and absorption contacts. Actual Tracer fixtures
  cover direct and expiry damage, walls, Blink, Recall, teams and
  caster body replacement. Actual quick melee covers both directions,
  static cover and Recall.

#### Sigma Gravitic Flux against Tracer

- Verify both Blink inputs are rejected during lift and a fresh press
  works after landing. Pulse Bomb can hit the airborne caster while
  the lift and slam still resolve if Sigma survives. A lethal Pulse blast
  after pistol damage during the fuse prevents the pending slam.
- Resolve static placement and hostile capsule selection for 1E41.
  Confirming placement starts the authored 1DBC effect on selected
  player bodies. Apply its entry damage, lift and grounded slam.
- Use the caster flight tuning during selection. Actual Tracer commands
  cover Blink escape, Recall admission and lockout, and a pistol kill
  that ends the cast before confirmation.

#### Sigma Accretion against Tracer

- Read the 1BBB launch bias, acceleration and knockback rings. Sweep
  the rock on bounded world steps and pass its tagged direct damage
  through the authored control request.
- Apply the 0.8-second host stun and collision-clipped player impulse.
  Actual Tracer commands cover input interruption, Blink after expiry,
  Recall protection, splash-only impact, cooldown reuse and caster
  body replacement.

#### Sigma Experimental Barrier against Tracer

- Run the authored deploy, recall, cooldown and redeploy inputs with a
  reconstructed actor using the verified 104319 C015 collision mesh.
- Route actual Tracer pistol rounds, quick melee and Pulse contact and
  blast to the projected barrier. Resolve each pistol round separately
  when the barrier breaks between rounds.
- Notify graph conditions as the linked ability cooldown elapses.

#### Sigma Kinetic Grasp against Tracer

- Evaluate the active 1D02 bounds and feed each intercepted round to
  the authored accumulator. Pulse is retired before stick and blast.
- Handle the authored 1D03 request on exit with a separate temporary
  shield pool, delayed decay and removal on body replacement.
  The contact and potential blast sum for Pulse, view-oriented boxes
  and shield storage are server reconstructions.

#### Tracer combined defense fixtures

- Run actual Tracer, Genji and Winston commands in the same encounter.
  Team changes establish whether the sphere intercepts the outbound
  shot or the reflected return. Same-side explosion damage retains
  Tracer attribution.
- Run the real 02CC Matrix and 00FA bubble together. The nearer defense
  wins Pulse contact. After Deflect transfers ownership, the return can
  be absorbed by the original shooter's allied Matrix before the sphere.

#### Tracer Pulse against Winston Barrier Projector

- The authored Pulse sphere checks both sides of the deployed 00FA
  barrier. Contact deals five, starts the fuse and retains the exact
  bubble instance. Retirement clears attachment and preserves the fuse.
- Detonation deals up to 350 to each visible enemy bubble and uses
  the pre-damage shell for body occlusion. Actual inputs cover inner and
  outer contact, break without spill, team admission, and static cover.
  Native collision filters, detachment motion and replication remain open.

#### Tracer melee and Pulse against Reinhardt Barrier Field

- The actual melee sphere checks the nearest visible mesh point once per
  activation. At 4 m it deals 30 to the shield alone. At 3 m it reaches
  both the shield and the body. Walls and allied teams reject both contacts.
- Pulse uses an ordered spherical sweep, a shield-local attachment and
  the authored fuse. Contact deals five to the shield, while the blast
  deals up to 350 and evaluates shield occlusion before applying damage.
  Releasing the shield leaves the bomb at its last position until the fuse
  expires. Native pose, detachment motion and pet replication stay open.

#### Reinhardt Barrier Field against Tracer (uncommitted, static gate open)

- Use lane 0007's prepared 104319 barrier triangles with the active
  0095 raise state. Each Tracer pellet checks the nearest body, wall,
  barrier or Matrix contact before applying damage.
- Actual inputs verify front interception, rear armour damage, break
  between pellets, delayed regeneration and reuse, and a nearer wall.
  Native pose, pet replication, melee and Pulse Bomb remain open.
- The hammer range probe sees the active swing on frame 27, with 85
  damage at 4.5 m and zero at 5 m in the reconstructed fixture.

#### Standing Tracer hitscan regions (uncommitted, static gate open)

- Use Tracer model 02B1's authored motion-1 head sphere for standing
  hitscan classification. Retain its yaw-rotated offset with the viewed pose.
- The same Soldier rifle input produces 20 damage to the torso and 40
  at the head, with the critical flag set only for the latter. A real wall
  blocks the head ray. Animated pose, crouch and native damage consumption
  remain open.

#### D.Va Defense Matrix against Tracer (uncommitted, gate failing)

- Reuse lane 007A's ruleset fallback, body possession query and authored
  Matrix boxes. Load projectile target tags from explicit build104319 data.
- Compare the Matrix contact against earlier body and world contacts, plus
  the existing hitscan Winston barrier. Absorbed Pulse Bomb flights retire
  their projectile record without a stick or explosion. Current aim and
  target-tag admission remain reconstructed.

#### Selected D.Va mech against Tracer (uncommitted, gate failing)

- Reuse lane 007A's additive graph/mover loading and authored 0407 body
  selection. The mech uses its own weapon script, capsule and 300/300 pools.
- Fusion Cannons deal 22 damage across 11 rays at 2 m. Tracer's first pair
  removes six mech armour. Cover, range, team, release/repress and Recall
  checks execute actual input. Micro Missiles deal 126 across a full burst
  and reject reuse during cooldown.
- VM and replay model fingerprints account for the supplied inputs.
  Initial possession, form transitions and native pellet trajectories remain open.

#### D.Va Light Gun against Tracer (uncommitted, gate failing)

- Connect the authored pilot pistol graph to the direct body-owner query
  reviewed in lane 007A. Resolve live player and NPC identities through
  the Match, and include the query context in read-only snapshots.
- The default 5 m roster case fires three 14-damage projectiles. A separate
  single-shot case verifies travel, ammunition and attributed damage.
  D.Va's mech and form transitions remain open.

#### Reinhardt Rocket Hammer against Tracer (uncommitted, gate failing)

- Adapt lane 0007's active 0048 swing queries, using authored capsule endpoints,
  radii, timing and 85 damage. Share each swing's hit record across overlapping
  queries, including when both queries reach the same Winston bubble.
- Map quick melee and primary onto the same hammer input before computing
  button edges. Verify windup, one damage event, real wall and team rejection,
  and actual Recall protection at 3 m. Native sweep and knockback remain open.

#### Symmetra primary beam against Tracer (uncommitted, gate failing)

- Adapt lane 0016's 157C target queries and damage path to the lane's body
  and Winston barrier services. Actual sustained contact advances all
  three authored damage rates. Store the duplicate-frame guard.
- Five focused beam methods pass. Blink clears contact from the next
  retained pose, Recall blocks damage, and a nearer bubble takes the
  damage slice. Native query, barrier, armour and timing parity remain open.

#### Zarya primary beam against Tracer (uncommitted, gate failing)

- Adapt lane 013B's 0241 TrackRay outputs and continuous damage to the
  existing combat services. Read the actual graph's selected entity,
  rate and reach, and register ray state and the consumed-frame guard.
- Verify actual sustained damage, release and reuse, range, team and wall
  rejection, Blink leaving the beam and Recall protection. The ray uses
  server capsules and authored eye height. Native filter, aggregate outputs,
  and barrier interaction parity remain open.

#### Brigitte Rocket Flail against Tracer (uncommitted, gate failing)

- Adapt lane 0195's flail volume to the actual 1248 damage states. Read
  the authored 35 damage and 6 m reach, verify Brigitte's eye offsets,
  and store each swing's hit set so a target takes damage once.
- Select 1248's combo cases by index when the resource disables value
  matching and supplies null case values. Actual release and re-press
  now reach the next swing. Native sweep and index coercion remain open.
- The 5 m roster case now deals 35 damage. Focused tests cover re-press,
  rear and range misses, teammates, real map geometry and Recall.

#### Winston Tesla Cannon against Tracer (uncommitted, gate failing)

- Reuse lane 0009's reconstructed Tesla cone with actual 00FE search and damage
  states, the authored Winston eye offsets, and lane 0003's shield and Recall
  handling. Record the last consumed frame in stored combat state.
- Verify damage and feedback in the 32-hero roster run. Incoming Winston now
  damages Tracer. Focused fixtures cover sustained fire, release and re-press,
  duplicate rejection, range, team, walls, Recall, and enemy barrier contact.
- Native cone, status filters, target payloads and original-client behavior
  remain open, along with the other incoming primary gaps.

#### Tracer Pulse Bomb launch bias (uncommitted, gate failing)

- Evaluate the authored `m_22CE7F30` lift on the actual 0034 release. Apply
  its +0.2 vertical bias before normalization and share the resulting direction
  between authoritative flight and observer projectile creation.
- Match 160 captured native direction calculations. Actual flat 5-metre input
  now attaches and deals 5 contact damage before the fuse. The original
  attachment, Recall, blast falloff, armour and five-NPC checks pass.
- Animated hardpoint and aimID transforms, incoming critical regions, full
  interaction coverage and original-client acceptance remain open.

#### Tracer shared baked spread ramp (uncommitted, gate failing)

- Integrate activation-baked float32 spread_ramp and elapsed+1 exit sampling;
  register causal snapshot state while retaining Tracer per-report shot angles.
- Current source:1265 distinct methods,1256 passing,9 failed methods/15 subcases,
  zero skips.7 generic spread and10 actual Tracer spread methods pass. New actual
  Tracer stored-state replay passes. Earlier Winston actor replay is pre-ramp
  layout evidence; full server-group and original-client checks remain open.
- Full-width character model IDs separate hardpoint3 from bone3. Preserve the
  complete bind chain; animated world muzzle and critical regions remain open.
- Keep all32 heroes/five actual NPCs and the full interaction matrix in scope.
  No own commit/patch is produced until the entire static goal passes.

#### Tracer Winston barrier and first-person offsets (uncommitted, gate failing)

- Reuse only reviewed Bubble/Barriers classes, binding actual00FA deployment to
  server sphere actors. Independently verify modelD02's radius5/motion1 record.
  Intercept individual hitscan rays before bodies/walls; preserve range/credit
  behavior, per-ray break ordering and independent melee volume contact.
- Eleven actual-input cases pass deployment, paired damage, empty rear target,
  break/ally/inside/exit/wall ordering, melee, death, retirement, world expiry,
  cooldown rejection and fresh reuse. Register actor/guard causal state. Two
  new read-only storage and actual deployment-expiry stored replay cases pass
  in1523.964s; this is not a replicated pet or battle-damage replay claim.
- Original XML verifies Tracer standing/crouched1.25/.875 and McCree1.425/.9975.
  Use body-aware offsets for combat origin and Target.eye. Four actual-input
  low-cover/stance cases pass. A selectively exported Tracer02B1 model passes
  the104319 content-manifest check; its node3 is not a resolved world muzzle.
- Current covered selection:1258 distinct methods,1249 pass and9 fail with15
  subcases, zero skips. The170-method Tracer run exposes incoming body/critical
  and Pulse contact/attachment/falloff assumptions after eye correction. Keep
  all failed assertions; do not restore the wrong eye or shorten the range.
  Remaining1084 checks pass. Earlier green results are historical source only.
- Pulse hardpoint3/aimID, native filters/pose/pet/wire and full32-hero/five-NPC
  interactions stay open. Shared spread_ramp/layout change is reviewed and
  queued separately. Full static goal remains ACTIVE; no own commit/patch,
  merge or publication. See docs/TRACER_WINSTON_BARRIER.txt.

#### Shared notification lifetime and Ability exit ordering (uncommitted)

- Reproduce both parent queue-marker regressions in the private runtime. Clear
  CHECK/CHANGED coalescing markers after state cancellation and suppress writes
  notifying ending states. Reuse existing causal fields and snapshot layout.
- Queue Ability scope/stop/resource cleanup before releasing arbitration, using
  the reviewed parent's explicit server reconstruction. Full native inter-object
  ordering remains open; unrelated parent engine services are not copied.
- Preserve actual Tracer empty-magazine Recall behavior across five reload-entry
  offsets and verify held-primary rejection/resumption. The five offsets already
  passed before this fix; two minimal shared regressions prove the repaired
  defect. See docs/TRACER_NOTIFICATION_LIFETIME.txt for source/validation scope.
- Pass1242 distinct current-source tests across the primary run and original-map
  supplement:1233 main passes plus9 missing-cache skips resolved by47 mover passes.
  This includes157 Tracer cases, two notification regressions,771 extra script
  cases and one392.110s two-player lifecycle replay. Frozen source and immutable
  cache hashes, Ruff and diff checks pass; no new causal fields are introduced.
- Keep the preceding484-test freeze and16 full server cases as historical-source
  evidence; full16 were not rerun after the lifecycle patch. Execution probes
  are refreshed on current source; actual Winston barrier interception still
  fails. Full static/client acceptance remains open.

#### Shared movement input modifiers for Tracer (uncommitted)

- Reproduce five initialized actual-resource/input failures: shared023A factor
  leaves Tracer at full speed and00CB/01CB zero-input scopes permit acceleration.
  Apply the reviewed parent mover17/18/19 split/sign/clamp consumer. Raw command
  bytes and existing snapshot layout stay unchanged.
- Seven Tracer fixtures pass four-way scaling, dynamic factors, priority stacking,
  duplicate commands, body replacement and the actual00CB1.5s scope deadline with
  next-pose restoration. Explicit fixture activation does not establish native
  combat status dispatch, instant momentum cancellation or full controls.
- The prepared-runtime mover group skips9 missing-map-cache tests. Reading the
  existing original104319 caches directly makes all47 mover tests pass without
  skips. Cache hashes remain unchanged; no cache build/copy is invoked. Broader
  frozen-source regression results are recorded separately in lane evidence.
- Isolate an actual Winston barrier failure:00FA completes deployment and starts
  cooldown with700 health/9s duration parameters, but actual Tracer primary still
  damages Winston. Keep this failure and the full32-hero/five-NPC matrix open.
  See docs/TRACER_MOVEMENT_MODS.txt. No own commit/patch, merge or publication.

#### Typed manual Assassin pin and Tracer buffering (uncommitted)

- Adapt reviewed parent manual1157 support to a distinct server-only controller
  Entity and typed foreign1650 target. Reserve its identity with the NPC body;
  retain graph health amounts and a reconstructed elapsed damage clock, input
  lock, authored interruption and cleanup. Export all new causal state.
- Reuse reviewed incremental collision-aware pursuit only for manual Assassin.
  The actual1.5m/.5m/14m/s mover approaches from5m and applies25 damage on frame10.
  Twelve actual-body fixtures pass pin damage/clock, action rejection, Recall,
  Pulse cancellation, stop/reengagement, walls, support primary/stun and removal.
  All five selected actual NPC attacks now damage Tracer and pause during Recall.
- Reproduce two actual Tracer early-press failures, then retain Ability press
  timestamps while unready instead of refreshing them every frame. Five cases
  pass released-press expiry, held input and short buffering at real resource
  and cooldown gates. See docs/TRACER_ASSASSIN.txt for provenance and scope.
- Pass430 distinct regressions without skips:414 regular plus16 server snapshot/
  replay, including148 Tracer tests. The734.484s server group completes in the
  same owned run; frozen source hashes, Ruff and diff checks pass. Fourteen
  prepared-runtime NPC repeats pass separately. Audit direct failures are empty;
  its full-static flag remains false. Evidence is external to the repository.
  Native AI/controller/wire, health-clock semantics, release animation, QTEs,
  real-map navigation and the full cross-hero matrix remain OPEN. Parent mover
  properties17-19 appear in referenced shared status resources; actual activation
  and scope-end restoration remain an explicit follow-up. No own commit/patch.

#### Pulse Bomb physical Deflect (uncommitted reconstruction)

- Reproduce two actual Q/E transfer/damage failures with a passing rear-contact
  control. Add scoped Pulse admission under the existing frontal Genji capsule
  policy, ending old prediction/observers and transferring source/kill credit.
  Preserve speed at contact, gravity, age, remaining life and authored hit data;
  begin fuse on a later impact, retaining already-stuck behavior.
- Fourteen direct cases pass owner/lease retirement, self damage, rear contact,
  real rear Soldier kill and Flashbang interruption, body replacement, Recall,
  Blink, lifetime, redirected aim/team/ally, actual walls and the450 PvE branch.
  One storage fixture verifies owner/body causality without claiming simulation.
- Native admission/filter/boxes/redirect/fuse semantics and new-owner replicated
  visual/lease allocation remain OPEN. The eight-transfer per-step bound is an
  explicit reconstruction. Complete Tracer static/client acceptance is open;
  no own commit or new patch is made. See docs/TRACER_DEFLECT.txt.
- Pass394 distinct regressions without skips (379 regular plus15 full-server
  snapshot/replay), including131 Tracer tests. The same frozen production source
  passes the513.313s server run; hashes/Ruff/diff checks pass. Fourteen prepared
  NPC runtime fixtures pass separately with unchanged inputs. Actual Q/E audit
  observes transfer, old prediction retirement and remaining-life preservation.
  Repeated expanded contacts reach the reconstructed eight-transfer retirement
  without damage; that observation does not establish the native policy.

#### Tracer hitscan and melee against Deflect (uncommitted)

- Reproduce three actual-input failures, then reuse the reviewed Genji-lane
  frontal admission/redirect policy in the per-bullet resolver. Preserve evaluated
  damage tags, defender team/credit, accumulated falloff and remaining range;
  block frontal quick melee without return damage. No new causal fields or RNG.
- Seventeen direct fixtures pass paired reflection, rear exposure, redirected
  aim/team/ally/wall, kill credit, armour/shields, Recall/Blink, expiry/cancel/
  cooldown/reuse, body replacement, actual death and both-leg distance handling.
- Eight unchanged native box-ordering slices pass without engine adapters.
  This is local endpoint evidence only. Physical Pulse still sticks to active
  Deflect in an actual Q/E probe; native eligibility, boxes/filters/transforms,
  physical transfer/lifetime and complete reflection remain OPEN. See
  docs/TRACER_DEFLECT.txt. Source remains uncommitted pending the full goal.
- Pass379 distinct regressions without skips (365 regular plus14 full-server
  snapshot/replay);117 Tracer tests include the17 new Deflect cases. The same
  frozen production source passed the393.438s full-server run without restart.
  Source hashes, Ruff and diff checks pass. Fourteen prepared-runtime NPC tests
  repeat successfully with immutable input hashes; these are separately counted.

#### Enforcer manual incoming combat (uncommitted static acceptance work)

- Review/reuse the parent's uncommitted explicit manual-controller predicate,
  restricting its declaration to manual Enforcer bodies in this lane. Eight
  original CC9F61/182C470 tail executions agree with the component-set predicate;
  native controller allocation and the chosen manual component policy stay open.
- The real13EB weapon now damages Tracer:2 on frame67 at5m. Actual-input tests
  cover stop, Recall immunity/recovery, Flashbang interruption/recovery, pistol
  death/corpse retention and static-wall exclusion/recovery. Causal VM and
  full-server snapshots include the declared controller components.
- Direct loads from the parent's separate43-entry NPC runtime match every
  authored mover field for all five bodies before test patching; all14 NPC
  fixtures pass there with unchanged input hashes. Earlier lane fixtures used
  those XML fields in memory. The38-entry base runtime lacks these NPC records;
  neither its defaults nor the unprepared real-map cache establish deployment.
- A32-hero command survey records outgoing pistol loss to all32 and incoming
  held-primary loss from24. This is a diagnostic, not complete weapon/defense
  acceptance. Actual Genji03A3 StateDeflectProjectiles is active but admits12
  frontal Tracer damage without reflection: a direct outstanding failure.
- Pass362 distinct regressions without skips (348 regular plus14 full-server
  snapshot/replay), including100 Tracer and6 controller-context tests. Full-server
  tests took660.813s on the same frozen production source; hashes match and the
  process was not restarted. Separate native8-case and repeated14-NPC runtime
  checks are not added to the distinct test count. Ruff/diff checks pass.
- See docs/ENFORCER_MANUAL_CONTEXT.txt. Assassin's separate typed namespace and
  pin bridge, native weapon fidelity, full static and original-client acceptance
  remain open. No new own commit, exported patch or shared-source write.

#### NPC owner-health input (uncommitted static acceptance work)

- Reuse reviewed parent2ca9178 as private dependency e3416f5. Its distinct-input
  join and strict snapshot changes pass 129 selected regressions before this
  health change; full Tracer acceptance remains open.
- Bridge NPC STU_9B7648C3 to the body's existing synchronized health input.
  Native CEB6C0/D177C0 selects component51 and calls its scalar getter; the
  host uses current aggregate combat health. Foreign entity lookup and the
  native health component's complete pool policy remain unaccepted.
- Install manual NPC combat pools before graph initialization so initial health
  includes shields/armour. A five-class real-input regression failed before the
  bridge and now covers initialization, pistol damage and terminal zero health.
- Pass168 selected regressions and Ruff/diff checks. The97-test Tracer audit
  passes its regressions but still fails both actual Enforcer/Assassin incoming
  attack probes. Prior full-server results remain historical for this change.
- Keep Enforcer/Assassin controller eligibility and primary attacks open. No
  controller predicates are forced. Further own changes stay uncommitted until
  the complete static goal passes; original-client acceptance remains pending.

#### Tracer Flashbang interactions and buffered weapon interruption

- Review/reuse parent64bf71c only in this private clone, preserving Recall pose
  handling, per-bullet cones/tags, Pulse storage and strict snapshot fields when
  resolving conflicts. Its053/0079 ->0228 ->224E duration/resource policy remains
  a reconstructed server service, not complete native status-manager execution.
- Fix a verified early-input leak: a released pistol re-press inside0016's guard
  fired during stun and spent two rounds even though damage was suppressed.
  Clear LogicalButton's early-press timestamp alongside ability buffers at stun
  entry. The same real-input regression fails before the fix and passes after;
  fresh post-expiry input still fires normally.
- Thirteen Tracer input fixtures cover25 damage/50-tick expiry, rejection without
  resource spending, momentum/fire/ammo interruption, Blink evasion/reuse,
  Recall two-phase immunity/rejection/post-expiry healing, Pulse windup spending
  without a delayed bomb, active-bomb survival, death/body replacement and
  wall/team/range filtering. Ten actual NPC tests cover56 class subcases including
  damage to all five controlled classes and controlled death/corpse retention.
- Pass328 regular regressions plus14 full server snapshot/replay tests on the
  fixed source (342 distinct tests). The additional five-class death fixture passes.
  The earlier baseline snapshot run was deliberately stopped for this real
  production fix, not restarted because of an observation timeout.
- Full native immunity/cleanse/stacking/unstoppable, other controls, barriers,
  reflect/absorb, dynamic mode status, native critical/RNG/muzzles and remaining
  two NPC primary attacks/full specials remain open. Original-client acceptance,
  shared-main integration and publication remain absent.

#### Tracer native count-based bloom and reconstructed independent bullet cones

- Execute native-validated count/3 normalization in actual0016 linked output and
  firing cones. Preserve authored recovery delay/Chase and remaining bloom on
  rapid re-press. Capture bounded per-activation cone angles before newer state
  can replace their initial spread; register them in strict read-only snapshots.
- Trace sampled bullets independently for nearest enemy, falloff, wall occlusion
  and per-bullet mitigation. Deterministic polar/radius sampling is a reconstructed
  host RNG independent of graph RNG. Native random-table seed, anisotropy, muzzle
  transforms and hitboxes remain open; other heroes retain their original rays.
- Pass nine actual-input spread fixtures, including first pair, growth/cap,
  recovery, buffered re-press, retained activation, unit rays, real far misses,
  a real wall blocking just one bullet, and snapshot hash sensitivity.271 regular
  regressions and14 server snapshot/replay tests pass (285 distinct tests).
  Python/native normalization matches all72 offline cases, counted separately.
- Recover the native null critical initialization: it stores a -1 sentinel rather
  than a fixed1. Downstream hit-region resolution remains unaccepted; no guessed
  multiplier is substituted. Full static combat remains open, with Enforcer and
  Assassin incoming attacks, full statuses/defenses and cross-hero matrix pending.
  No original-client work, shared-main merge or publication.

#### Tracer actual NPC incoming attacks, Recall and melee wall coverage

- Review/reuse parent manual-controller, NPC weapon-root, array-selection and
  melee-occlusion commits only in this private clone. Preserve lane damage tags
  when resolving the melee conflict. Parent already owns those shared changes.
- Execute actual Trooper, Sniper and Heavy Assault weapon graphs against Tracer:
  first damage20/100/10 at29/128/32 frames. Heavy's authored5 doubles under the
  existing approximate eye/head test. Stop blocks further damage for80 frames.
  Recall blocks all three attacks while active and damage resumes afterward.
  These are manual targeting/transport fixtures, not autonomous AI acceptance.
- Add real static-wall melee exclusion for all five NPC capsules, with no hit
  notice, health loss or charge gain. Eight actual-class tests now cover36 class
  subcases. Enforcer/Assassin still cause zero damage in200 frames and are listed
  as executed missing-attack failures, not hidden behind selected passing tests.
- Pass245 regular regressions after fixing two test-fixture references, plus
  14 server snapshot/replay tests on frozen source (259 distinct tests total).
  No original-client work, shared-main merge, publication or full goal completion.
- Execute captured native spread-growth functionD80A50 for72 combinations without
  engine-service stubs. It adds count/denominator, with Tracer denominator3, not
  a per-second growth rate. Production spread output/ray sampling, native seed/
  muzzle correspondence and critical defaults remain open.

#### Tracer authored ultimate gain and observable Recall/Pulse state

- Route resolved damage and authored tags through graph0B8B's original damage
  entry; run its5-point/second timer instead of the normal60-second debug fill.
  Keep graph gates, multipliers, cap and Pulse exclusions. Scoped actor/hostile/
  owner predicates are reconstructed; full native filters remain open. Training
  tag02ED is a classifier and ordinary training damage gives full gain.
- Preserve earned player charge across death/respawn while explicit hero changes
  reset it. This retention policy is reconstructed. Five actual-class NPC primary
  fixtures now assert12 charge from resolved paired damage.
- Register Recall phases/history/session and Pulse storage in strict VM/server
  snapshots. Exports remain read-only observations, not restorers; unknown fields
  still fail closed. Confirm a rapid primary re-press is buffered by the authored
  0.2-second guard rather than lost.
- Pass216 distinct tests:69 Tracer (32 base,19 Pulse,11 ultimate,2 Recall storage,
  five actual NPC classes),28 combat,37 projectile,27 driver,12 script abilities,
  23 VM snapshot,14 server snapshot and six NPC/catalog. Full server replay passes
  with source frozen. Original-client acceptance and shared integration absent.
- Full static goal remains active. A20-second skipped-command/world-tick probe
  grants25 points under the existing256-frame catch-up cap; full elapsed-time
  recovery is open. Critical/spread, native timing/impact filters, complete
  status/barrier/reflect/absorb, cross-hero and NPC reverse attacks remain open.

#### Native state-input joins and Deadeye timing

- Implement1113EBD5 distinct-input joins from the104319 input handler rather
  than permissively following output. Track producer/plug arrivals, ignore
  duplicates, request sibling completion checks, clear registrations before
  output and abort active referenced states only after accepted output. Resolve
  states after callbacks; missing states are not allocated as a side effect.
- Include pending arrivals and current traversal context in VM/server causal
  snapshots, restore nested traversal context and clear arrivals on teardown.
  Update the snapshot storage contract test for these new fields.
- Pass945 relevant tests (932 script/VM/NPC/projectile/McCree plus13 server
  snapshot), including10 new join and actual Deadeye command tests. Forty
  offline native-handler arrivals match Python return values, registry order,
  completion checks and aborts; engine lookup/output/event services are adapters.
  Ruff and diff checks pass. No original-client acceptance was run.
- Deadeye now survives warmup, retains the resource's six-second window and
  supports repeated secondary cancel without normal ammunition loss. Test charge
  is explicitly filled. Target acquisition/per-target charging/damage and the
  full ultimate remain open; no replacement damage formula is installed.
- Manifest-verified research resolves the per-target effect053/0056 to01EE in
  an external bundle. Normal runtime data/services and original working changes
  remain unchanged; no complete hero lane merged. Full PvE AI/navigation/specials,
  McCree/cross-hero and original-client combat acceptance remain open.

#### Resource-backed Flashbang control reconstruction

- Preserve authored hit-effect IDs and route Flashbang's053/0079 effect to an
  explicit server control policy. Resource-derived mode flag13408 chooses0.8s
  or1.7s; exclusive deadlines use world ticks. Release held/buffered input, stop
  running abilities through their authored exit paths, suppress player movement
  and attacks, pause NPC routes without catch-up and consume suppressed shots.
- Retire control on expiry, death, body replacement and manual NPC removal.
  Retained corpses remain present. Include deadlines in server snapshots and
  verify that they change the causal digest. Splash now checks static impact-
  to-capsule visibility before applying damage and control through cover.
- Pass138 relevant tests:125 combat/NPC/projectile/route/McCree and13 server
  snapshot checks, including8 new actual-input/control/lifetime methods. Ruff
  and diff checks pass. Fix a test fixture's shared-spawner mutation discovered
  by the combined regression; subsequent fresh-process regressions pass.
- Actual-five-Talon offline probes show25 Flashbang damage and input release
  for all five; Trooper/Sniper/Heavy resume damage after expiry. Console stop
  during control prevents automatic recovery attacks. Enforcer/Assassin incoming
  attacks remain unresolved. Fixtures use adapter transport/permissive VM.
- This policy does not execute the complete native224E status manager. Native
  immunity/cleanse/stacking/barrier interactions, dynamic mode status, status
  replication/HUD/animation/audio and original-client acceptance remain open.
  Deadeye/full hero combat and NPC AI/navigation/specials remain open; no hero
  lane merged. Normal runtime/services and original working changes preserved.

#### McCree melee static-wall occlusion

- Prevent quick melee from damaging targets through static walls: retain the
  authored melee sphere and viewed capsule, then check eye-to-capsule-surface
  visibility. Geometry is an explicit server reconstruction, not native hitbox
  equivalence. Shared quick melee uses the same check for players and NPCs.
- Pass83 McCree/combat/NPC/projectile tests, including3 new real-input methods
  covering visible and wall-blocked player/NPC hits, allies and range refusal.
  An external actual-five-Talon probe passes all10 visible/wall cases. Ruff and
  diff checks pass. Native-client acceptance was not run.
- Record the actual McCree input frontier and recovered Flashbang status resource
  route. The current VM does not execute its complete stun path; Deadeye still
  lacks target/charged damage acceptance. Roll motion, complete skills/cooldowns,
  five-NPC AI/navigation/specials and original-client combat remain open. Normal
  inputs/services and original working changes are preserved; no hero lane merged.

### 2026-11-08

#### Manual NPC console commands and engagement input

- Implement console command semantics for menu/selection, explicit removal,
  crosshair-selected engagement and stop-all. Spawn requires visible floor;
  selection/aim uses reconstructed sandbox distance and static-visibility policy.
  No native console hook, keyboard frontend or production mounting is installed.
- Drive actual slot0 input, target-player yaw/pitch and input release on target
  loss/occlusion. Stop cancels pending/consumed input and owned projectiles;
  idle manual VMs advance while damaging reports are suppressed. Track queued
  input separately to cancel a press before its first VM update. Preserve new
  target/input/pitch state in snapshots and movement output.
- Pass136 related and13 server-snapshot tests (149 total), including9 new control
  tests, Ruff and diff checks. Actual body data emits primary damage for Trooper
  and Heavy Assault with no later damage in the80-frame stop probe. Enforcer,
  Sniper and Assassin emit no shots and retain unresolved query/behavior gates.
- A separate original104319 console-option probe fails relay loading with Windows
  access denied and times out login. No console window or grave-key overlay is
  observed on that screen; post-login console availability remains unknown.
  Full navigation/specials/McCree skills/ultimate/client combat remain open.
  No normal deployment; mandatory publication helper remains missing.
#### Native array selection for PvE difficulty configs

- Implement STU_4BD39CFD with scalar index evaluation and collecting container
  evaluation, array-only bounds checks and successful-null fallback. Preserve
  nested value types and watched index/container updates; unknown child configs
  still fail strict execution. No mission difficulty is invented.
- Execute the original104319 selector/conversion/lookup offline:15 cases match
  the Python runtime. Config providers and value storage/copy are adapters.
  Pass178 relevant tests, including5 new array selection/watcher/failure checks;
  Ruff and diff checks pass. Native-client acceptance was not run.
- Resolve a zero-damage cause in Enforcer's authored weapon. An external
  forced-controller experiment now emits resource-defined damage; its controller
  assumption is unverified and is not production code. The ordinary five-body
  probe still only proves Trooper/Sniper/Heavy damage; Enforcer/Assassin control,
  navigation/specials and full McCree/client combat remain open. Normal inputs,
  services and original working changes remain untouched; no hero lane merged.

#### Authored NPC weapon component mounting

- Share body weapon-root allocation between player and NPC scripts: preserve
  sparse slots, seed slot bindings and allocate manager/weapons before entries.
  This restores the Sniper body's omitted manager and two weapon graphs without
  replacing authored attack damage or timing.
- Pass107 relevant tests (23 NPC script,15 NPC control/lifecycle,27 driver,
  5 body-script and37 projectile), Ruff and diff checks. The new test exercises
  sparse-slot firing and decodes the NPC observer baseline with mounted roots.
- Actual five-body offline data now yields Sniper damage100 against McCree225;
  Trooper20 and Heavy Assault5 still work. All three stop damage for80 further
  frames after stop. Enforcer and Assassin remain unable to damage in200 frames.
  The adapter omits NPC observer projectile transport; native visuals, autonomous
  AI/navigation/specials and complete McCree acceptance remain open. No client
  launched, normal data/deployment unchanged, no completed hero lane merged.

#### Tracer Pulse Bomb resource damage and reconstructed impact lifecycle

- Resolve the narrowly scoped015D.130 default350 from a manifest-verified104319
  resource; preserve actual graph0034's450 PvE-mode branch. Add authoritative
  ballistic sphere flight, impact sticking/attachment, delayed explosion, blast
  LOS/falloff, Recall detachment, self damage and lifetime/source cleanup.
  Impact-fuse field interpretation and physics/attachment policies are explicit
  reconstructions; no native-emulation or client-fidelity claim is made.
- Pass19 Pulse Bomb,31 Tracer, five actual-class NPC (25 class subcases), six
  shared NPC,28 combat,37 projectile and27 driver tests (153 total), Ruff and
  diff checks. Cover spending/rejection, release delay, full350/450 damage,
  falloff210 at3m, friendly/miss/wall/self, windup cancel/death, active owner death,
  body retirement, Recall detachment, lifetime, armour/shields, scheduler gaps and reuse.
- Retire the executed charged-bomb explosion failure. Full static goal remains
  active: critical/spread, complete ultimate gain, status/control/deflect/absorb/
  barriers, Recall cleanse and NPC reverse attacks/full cross-hero matrix remain
  open. Original-client acceptance, publication and shared-main merge are absent.

#### Tracer reconstructed Recall and actual-class outgoing NPC fixtures

- Reconstruct001A warmup/playback using resource timing and body-local posed
  history. Restore beneficial health, position and authored ammo capacity; freeze
  translation, reject Blink/fire and exclude incoming damage/target resolution
  during Recall. Existing graph starts12-second cooldown on completion.
  Cancellation spends cooldown without restoration; old timers cannot affect a
  replacement body. These engine semantics are explicit reconstructions.
- Pass31 Tracer, four actual-class NPC (20 class subcases), six shared NPC/catalog,
  28 combat,37 projectile and27 driver tests (133 total), Ruff and diff checks.
  NPC body XML supplies authored mover capsules in fixture memory. Reviewed parent
  NPC commits were cherry-picked into this clone; normal runtime is unchanged.
- Recall position/health/cooldown audit failures are retired. Cleanse, native event
  timing, complete controls/status/cross-hero and NPC reverse attacks remain open.
  Charged Pulse Bomb explosion still fails; full goal remains active. No original
  client acceptance, publication or shared-main merge is claimed.

#### Tracer reconstructed Blink action and full static goal

- Add a narrowly scoped handler for actual graph0024/actionA807207A, using its
  authored7.5-metre distance, existing three-charge/recharge graph and a full
  static-world capsule sweep. Replace the same-frame movement snapshot after
  displacement. Horizontal throttle direction, clipping and momentum retention
  are explicitly reconstructed server policies, not native-action emulation.
- Pass22 Tracer tests,28 combat tests,37 projectile tests and27 driver tests
  (114 total), plus Ruff/diff checks. Blink fixtures cover directional movement,
  walls, exhaustion/reuse, cap, alternate input, death and held/duplicate input.
  Pistol fixtures add miss/range/falloff and empty-magazine damage checks.
- Establish the full static-combat goal as active. The lane-local audit records
  actual failing Recall position/health/cooldown and charged Pulse Bomb explosion
  scenarios. Full lifecycle/status/cross-hero/five-NPC coverage remains open;
  original-client combat acceptance remains separate and unperformed.

#### Tracer paired hitscan damage (hero lane 0003)

- Resolve `m_numProjectilesPerShot` independently for hitscan damage. Actual
  Tracer0016/st8 emits two six-damage bullets per shot, while the previous
  consumer applied only one. Keep armour mitigation and lethal-hit suppression
  per bullet rather than multiplying the damage before applying health pools.
- Pass eleven actual-graph Tracer input/combat fixtures, 28 existing combat
  tests and 37 projectile regressions (76 total); Ruff and diff checks pass.
  Coverage includes magazine damage, explicit reload, melee, incoming
  Soldier fire, armour/shield pools, team filtering, wall occlusion, notices and
  retained training-NPC lifetime. See `docs/TRACER_COMBAT.md` for the matrix.
- Original-client acceptance was not run. Shared-ray spread, critical defaults,
  all three abilities, ultimate gain, statuses/barriers/deflect/absorption and
  all five requested PVE NPC classes remain open. No full hero acceptance or
  publication is claimed; this private lane exports a patch for review.

#### Prepared PvE enemy base-pool catalog

- Add an external XML catalog extractor that resolves actual NPC HealthPool
  type and directly wired schema setters, defaults and body overrides. Preserve
  source fingerprints and list unapplied difficulty/conditional pool nodes.
  The NPC body's variable221 routes to type2 shields; legacy armor labels are
  not used. Validate selection keys, full GUIDs and finite explicit pool values.
- Pass three extractor and six NPC runtime/catalog tests (nine total), Ruff and
  diff checks. Prepare all five local enemy definitions outside Git. Extend the
  validated external script bundle by33 graphs with no new missing references.
- In a fresh in-memory Match, all five actual body scripts load, McCree primary
  input deals70 damage to each, body creates encode and removals complete. Flat
  collision, adapter transport and permissive VMs remain explicit probe inputs;
  this is not enemy AI, complete skills/ultimate or original-client acceptance.
- Console keys/menu, production mounting, navigation/specials and full combat
  remain open. Normal runtime data is unchanged. See PVE_COMBAT_SANDBOX.txt.
  The required publication helper remains absent; no manual push is performed.

#### Manual NPC combat body lifecycle

- Add validated explicit NPC definitions and match-thread spawn/removal APIs.
  Fresh entities join combat with supplied health/armour/shield pools, retained
  corpses, creation boundaries and queued body creates for spawned viewers.
- Explicit removal selects only manual NPCs, ends their scripts/projectiles
  without explosions, clears attacks/respawns/combat state and queues destroys.
  NPC projectile retirement no longer assumes its source has a player client.
- Pass five real-Match lifecycle tests, 37 projectile tests and 28 combat tests
  (70 total), Ruff and diff checks. No fresh original-client run or deployment.
- The five Talon definitions, pool routing, navigation/AI/specials, console keys,
  McCree abilities/ultimate and complete sandbox acceptance remain open. Parallel
  hero goals require complete executed static acceptance before serial integration.
  See docs/PVE_COMBAT_SANDBOX.txt. The required publication helper is still absent.

#### Optional persistent NPC corpses

- Add an opt-in Bot.retain_corpse policy for the requested Rialto combat sandbox.
  Death still sends normal notices and advances the bounded terminal script;
  retained bodies receive no respawn deadline and reject stale replacement calls.
  Keep the policy in passive server state. Existing training bots still respawn.
- Pass 28 combat tests (including retained lifetime, dead-target exclusion and
  terminal callbacks), 22 NPC script/transport tests and 13 server snapshot tests.
  Ruff and diff checks pass. No fresh client or normal deployment is claimed.
- Manual five-enemy spawning, console keys, navigation, specials, McCree combat
  and visual corpse persistence remain open. See docs/PVE_COMBAT_SANDBOX.txt.
  The complete combat sandbox remains unaccepted. Publication uses the required
  helper; its existing absence still prevents synchronization.

#### Native persistent receiving message State

- Mount strict9D6CF8AC through explicit message contexts. Preserve dual receiver
  channels, the native nonzero-key/flags clearing rule, selection generations,
  filter scopes, three parameter modes and separate message/filter/cache
  dependency epochs. Restore deferred begin, forced/non-forced refresh, tick
  routing, context outputs and allowed idle/receive/retrigger subgraph changes.
- Retain per-row output links and duplicate targets, in-place refresh and old
  destinations for baking. Restore live best-source getters without watchers,
  cached array getters and failed-output fallback distinct from successful null.
  Bound variable recursion exposes the lower value and releases guards on faults.
  Context/physical retirement drops rows quietly and refuses replacement-context
  borrowing. Native keyed type12/indexed paths and linked array checkpoints remain strict gaps.
- Encode sender identity/kind and stacked payloads. Preserve dynamic/frame-cache
  binding policy in runtime links, driver snapshots, wire output and diagnostics;
  the previous always-false binding policy could not represent receiver links.
  Extend passive VM storage without invoking getters or claiming shared restore.
- Pass3268 original production-control comparisons (384 invalid selected scene
  owners remain outside SceneWorld),1728 getters,1288 binding operations,360
  variable resolver cases,4864 writer/4864 reader cases and256 binding-policy
  reader suffix cases.16628 total comparisons use declared service/storage
  adapters; actual authored subgraphs and the complete binding reader are not
  run by these probes. No game code is patched. Add25 behavior tests; final1086
  related tests pass in26.003s and33 server tests in249.063s (1119 total), with25
  matching frozen source hashes and Ruff/diff checks.
- All535 extracted receiving nodes complete1605 isolated empty-source/pre-init
  registration/cleanup fixtures under three declared policies. No ordinary
  initialization or authored plugs execute. Full-controller fixtures now need
  explicit message contexts: without one they retain0F44/node7; with declared
  role/frame/generation plus earlier hero/team inputs, all three profiles retain
  0C90/node41's unknown mode component. Failed-helper rejection/cleanup passes.
- Fresh ordinary/placed/real-Match probes retain81A9AB10,50/123 completed first
  frames and rootD38/node106 before controller execution. Native engine roles,
  generation/filter policy, ordinary mode mounting, actor lifetimes/transport,
  original-client receiver acceptance, full story/cinematics/five enemy AI and
  extraction/results remain open. No fresh client run or normal deployment is
  claimed. See docs/MESSAGE_RECEIVER.txt. Publication helper remains missing;
  no GitHub push is performed.

#### Persistent message parameter providers and receiver caches

- Share native parameter providers across one component's instances. Preserve
  reverse ordering, null barriers, GUID0 lookup and destinations written by
  failed providers. Native Entry selection and legacy delivery use scoped VM
  providers; legacy per-instance providers remain the outer compatibility layer.
- Add a sender-backed scope with builtin owner/source/subject identities, first
  full-key matching, missing-row success/int0 and missing-config fallthrough.
  Evaluate in the source instance with the receiver watcher, live-context guards
  and a shared cross-component budget. Keep scalar success separate from value.
- Add the three native receiver cache modes, retaining ordered key/value rows,
  duplicate senders/keys, stacked unmatched caches, iterator versus whole-value
  array handling and different null/failure rules. Map/type13 stays scalar.
  Native keyed type12 storage, live output bindings and lifecycle are still open.
- Pass936 original scope/provider-chain and624 cache comparisons with declared
  engine/storage adapters and unchanged game code. Re-pass1350 Entry binding,
  24 Entry scope (including zero-ID),3024 selection,60 subject,2688 lifetime,
  4626 sender,3456 outgoing-action and1296 hero-iterator comparisons. Add26
  behavior tests. Final1061 related tests pass in25.205s and33 server tests in
  243.748s (1094 total), with20 matching frozen source hashes and Ruff/diff.
- Receiving-State channel/epoch integration, live getters, output application,
  context variables, idle/receive subgraphs and codec remain unimplemented.
  Fresh ordinary/placed/real-Match and explicit controller/Entry probes retain
  their previous frontiers:81A9AB10,50/123 first frames and rootD38/node106 before
  controller execution. Full story/cinematics/enemy-AI/extraction acceptance,
  original-client verification and normal deployment remain open. See
  docs/MESSAGE_PARAMETERS.txt. Publication helper is still missing; no push.

#### Native persistent sending message State

- Connect strict38EE1100 to the persistent manager through explicit per-component
  Contexts and a shared Host. Restore scalar message/priority-resource guards,
  native target policies, channel flags, frame/sequence reuse, watched targets,
  refresh/cleanup and once-only VM timer notification. Parameters remain lazy
  for receiving States. Native frame/role/priority/notification services stay
  explicit; unknown services remain strict. See MESSAGE_SENDER.txt.
- Preserve each component's budget and binding lifetime. Discard detached/failed
  State rows without callbacks or touching other owners; invalidate callbacks
  interrupted by terminal discard. Retain source caches in passive State layouts;
  complete shared-manager export/restore remains unsupported.
- Fix the owner-frame sender reply encoder: preserve low16 sequence bits, emit
  presence plus variable-width unsigned16 for nonzero values. Match768 native
  writer and768 reader cases across width and word boundaries. Also pass4626
  native registration,396 initialization-flag and16 timer cases with declared
  adapters and unchanged game code. The manager's39394 original comparisons
  pass again after terminal discard support.
- Resolve1B72510 to EntityInstance's known null virtual callback; do not repeat
  transition actions after registration. Add22 sender tests and two manager
  cleanup tests. Final1035 related and33 server tests pass (1068 total), with
  matching frozen source/native hashes and Ruff/diff checks. Superseded runs from
  before the null-callback correction remain archived as intermediate evidence.
- Isolated actual-data fixtures cover47 senders in five mode/controller/placed
  graphs under three declared role profiles.44 complete all profiles; three keep
  strict unknown queries. No ordinary initialization or authored plugs run.
  Fresh controller/placed/Match probes retain81A9AB10,50/123 first frames and
  rootD38/node106 respectively; the receiving State and full mission remain open.
- No fresh original-client run, deployment, complete story/cinematic/enemy-AI or
  extraction/results acceptance is claimed. Publication helper remains missing;
  no GitHub push is performed. Normal inputs and unrelated launcher work remain.

#### Native persistent message-State manager

- Restore the standalone persistent sender/receiver manager: exact native channel
  keys, local/global lookup, float32 priority and flag/frame/sequence arbitration.
  Preserve duplicate receivers, metadata-copy upserts, swap removal, frame-based
  sequence allocation and dirty-notification order. Counted selection filters all
  candidates; uncounted selection skips weaker candidates. Collection keeps order
  and duplicates. See MESSAGE_MANAGER.txt.
- Require explicit owner validation, shared budgets and dirty callbacks. Reject
  unknown filters and unsupported callback mutation. Terminal retirement clears
  storage without graph callbacks; a missing receiver in an existing channel is
  refused instead of reproducing an invalid native memory operation.
- Pass17920 original single-selection,17920 collection and3554 mutation comparisons
  with declared storage/descriptor/filter/notification adapters and unchanged game
  code. Add19 behavior tests, including an actual VM operation's budget/fault;
  all1011 related tests pass in24.643s with frozen hashes, plus Ruff/diff checks.
- This backend is not yet mounted by SceneWorld or the sending/receiving States.
  Native identity/channel/frame/filter policies, variable bindings, subgraphs,
  replies and complete State lifetimes remain open. Existing mission frontiers
  are unchanged; no fresh client run, deployment, story/cinematic/enemy-AI or
  extraction/results acceptance is claimed. Publication helper remains missing;
  no GitHub push is performed.

#### Native game-message entry reception

- Preserve full ordered packet parameters, repeated keys and separate caller/
  instigator identities in shared-scene delivery. Select authored entry-array
  nodes in context0 with scalar messages, one-shot indices and explicit filter
  services. Unknown filter engines remain strict; no policy default is installed.
- Queue native parameter/context assignments before Entry events at the receiver
  clock. Match named/positional rows in incoming order; missing inputs keep old
  values. Preserve Dynamic-only context casts, typed invalid identities and the
  source-current-object getter's forward-possession/fallback behavior.
- Add transient parameter scopes with builtin9/A/B and first-full-key lookup.
  Remove scopes after selection/binding, including faults. Check source/receiver
  lifetimes and shared budgets. Passive VM export includes disabled entry indices,
  packets, scopes and queued writes without callbacks. Legacy transport and the
  independent sending/receiving States remain outside native entry acceptance.
- Pass1350 original queue/binding,3024 entry-selection,15 scope and60 subject
  comparisons, plus one native null-namespace fault probe, with declared engine/
  storage adapters. Fix actual named message entries retaining idle transient
  scripts;2688 original lifetime cases also pass with the extracted class name.
  Add25 behavior tests;992 related and33 server tests pass with
  frozen hashes, plus Ruff/diff. See MESSAGE_RECEIVE.txt.
- Actual100B's15 entry nodes pass isolated pre-init queue fixtures, including
  caller bindings10110/10111/10112. Ordinary mode initialization and entry plugs
  are not executed by those fixtures. Three explicit controller profiles still
  retain the unknown06FF receiver; ordinary controller retains81A9AB10; real Match
  retains rootD38/node106. Placed50/123 and failed-receiver cleanup remain unchanged.
- No fresh original-client run, deployment or full story/cinematic/enemy-AI
  acceptance is claimed. Normal inputs and unrelated work remain intact.
  Publication helper missing; no GitHub push is performed.

#### Native outgoing game-message construction

- Restore strict ActionSendGameMessage scalar message selection, generic target
  collection and guard/evaluation order. Preserve duplicate external targets,
  coalesced self delivery and distinct owner/inherited/overridden source inputs.
- Collect every parameter into a typed array, including empty arrays. Retain
  ordered full keys and duplicates in the request. Actual0C90/node41 parameter
  008D is now tuple(0,), correcting the earlier scalar parameter while preserving
  the team query's scalar0. Unknown mode receiver still stops delivery.
- Preserve scalar destination and success separately: empty hero iteration writes
  int0 with failure, so source overrides retain their inherited source. Existing
  scalar-value callers keep their behavior. Bind new services to actual component
  lifetimes and shared budgets. Unsupported instigator/repeated-key delivery stays
  strict; non-strict compatibility and the separate sending State are unchanged.
- Pass3456 original action construction and1296 hero-iterator comparisons with
  declared engine/storage/status adapters. Add18 behavior tests;967 related and33
  server tests pass with frozen hashes, plus Ruff/diff. See GAME_MESSAGE_ACTION.txt.
  PC code constructs external target lists but only dispatches self; it does not
  establish server forwarding or complete native message consumers.
- Ordinary controller retains81A9AB10; real Match retains rootD38/node106 before
  controller execution. Placed50/123, failed-receiver rejection and cleanup remain
  unchanged. No fresh original-client run, normal deployment or complete story,
  cinematic/enemy-AI acceptance is claimed. Normal inputs and unrelated work remain
  intact. Publication helper missing; no GitHub push is performed.

#### Native entity team query and filter dependencies

- Implement65ABFCCC with native no-output versus signed-1 behavior, uint32
  entity conversion, first-pass dependencies and a fresh second lookup. Decode
  five team bits with lowest-bit priority, ignoring unrelated/spectator flags.
  Context, lookup and observer services remain explicit; unknown data stays strict.
- Bind contexts to actual component lifetimes. Preserve callback retirement,
  stale-component isolation and shared lookup budgets. Do not infer a team from
  ownership or install a normal-world service/default for unresolved queries.
- Pass1600 original query comparisons and128 full filter-decoder comparisons
  with declared engine/pointer/storage adapters. Two additional null-namespace
  probes fault natively and remain unsupported. Add21 behavior tests;949 related
  and33 server tests pass with frozen source hashes, plus Ruff/diff checks.
- Three explicitly supplied controller profiles now prepare0C90/node41's06FF
  hero-list/team parameters and retain the unknown mode receiver. Delivery is
  unaccepted. Ordinary controller still retains81A9AB10; real Match still retains
  rootD38/node106 before controller execution. Placed50/123 and failed-receiver
  cleanup remain unchanged. See ENTITY_TEAM_QUERY.txt.
- No fresh original-client run, deployment or complete story/cinematic/enemy-AI
  acceptance is claimed. Normal inputs and unrelated work remain intact.
  Publication helper missing; no GitHub push is performed.

#### Native controller hero-catalog iteration

- Implement1AAB4B4F scalar-first, collected-value and nested-group interfaces.
  Preserve typed integer0 when scalar iteration yields no item, ordered Assets,
  duplicate/zero handling, selector watches and dependency kinds3/37. Unknown
  engine lookup, catalog and availability policies stay strict owner services.
- Bind services to the actual script component lifecycle. Old instances cannot
  borrow a replacement's context or retire it on failure. Detach/fault/unbind and
  callback retirement close the owned iteration path; rejected rows share budgets.
- Pass1296 original iterator/wrapper/traversal comparisons with declared engine
  and storage adapters, including one iterator/vector release per case. Two
  missing-environment probes confirm null-scratch behavior; no empty-success
  result is fabricated. Full catalog construction and availability remain open.
- Six retained graph references include actual0C90/node55 and node147 configs:
  the former receives all supplied assets, the latter reads the first. The
  ForCode State and independent5E805B5A list State remain unaccepted. With explicit
  role/catalog inputs, all three controller profiles now retain65ABFCCC. Ordinary
  execution still retains81A9AB10; real Match still retains rootD38/node106.
- Add21 behavior tests;928 related and33 server checks pass, plus final native/unit
  and Ruff/diff rechecks. Final lint/docstring corrections preserve executable
  ASTs, with earlier sources retained locally. Placed50/123 and failure cleanup
  remain unchanged. No fresh original-client run, deployment or complete story,
  cinematic/enemy-AI acceptance is claimed. See CONTROLLER_HERO_ITERATION.txt.
  Normal inputs and unrelated work remain intact. Publication helper missing;
  no GitHub push is performed.

#### Collected config-array values in variable assignments

- Correct SetVar and SetVars to use the native +28 value interface. Config groups
  now assign all collected values, including typed empty arrays. Preserve direct
  dynamic arrays, nested iterator order, null-member handling and strict faults.
- Match SetVars pointer rules: skip missing targets before evaluating values;
  write integer0 for missing/failed values, while successful null remains null.
  Earlier pair writes remain visible to later values. SetVar retains its different
  rule of skipping either absent pointer. Indexed/keyed store routing is preserved.
- Pass216 original execution cases, including212 value comparisons across both
  actions and four guard/order cases. Replay100 real config inputs with declared
  dynamic values; the635-graph bundle has108 direct array assignments, including
  30 in the static Rialto closure. Adapters and full-engine limits are documented.
- Add13 behavior tests;907 related and33 server tests pass, with Ruff/diff. The
  isolated placed catalog remains50/123 first frames complete,73 strict faults.
  Normal controller still retains81A9AB10; Match retains rootD38/node106 before
  controller execution. Failed receiver/queue cleanup remains verified.
- Original PC loader/cache and BooleanSwitch probes establish81A9AB10's no-output
  client fallback, not its server meaning. No guessed default is installed.
  Three explicit input profiles reach1AAB4B4F; its iterator remains open. No fresh
  original-client session, normal deployment or full story/cinematic/enemy-AI
  acceptance is claimed. See VARIABLE_ASSIGNMENTS.txt. Normal inputs and unrelated
  work remain intact. The publication helper is missing; no GitHub push occurs.

#### Native entity existence and current-possession queries

- Implement E72F6348/0AC15F78 with explicit owner namespace/environment, complete
  lookup and dependency inputs. Preserve no-output failure, Bool false and typed
  Entity(0) as distinct outcomes, nested variable watches and callback ordering.
  Retire contexts on detach/fault/unbind; do not infer forward current possession
  from the reverse primary-possessor catalog or map bindings.
- Pass1680 original-instruction comparisons with declared engine/storage adapters,
  including112 unchanged current-object getter calls. Two earlier read-only
  original-client hero-switch captures agree with component44+D8. No fresh client
  session, full engine timing or complete mission acceptance is claimed.
- Actual000E/0F44 with explicit created controller/body and query inputs now creates
  0C90/1194 and retains81A9AB10/frame0. Failed bound controller messages still fail
  closed. Real Match still prepares148 components/queues and retains rootD38/node106
  before controller execution, then retires bindings. No readiness or deployment.
- Add19 behavior tests;894 related and33 server tests pass, with Ruff/diff checks.
  Full controller/body/actor lifetimes, engine services/phases, cinematics, enemy
  AI and mission/results remain open. See ENTITY_QUERIES.txt. Normal inputs and
  unrelated working changes remain intact. Publication helper remains missing;
  the required synchronization cannot run and no GitHub push is performed.

#### Declarative shared-variable State and original-client acceptance

- Model9D7BF987 with common State lifecycle and strict agreement between Dynamic
  declarations, both compiled node sync groups and graph sync metadata. Preserve
  an empty codec and passive snapshot storage; no variable evaluation or writes.
- Execute24 original allocation/binding cases with the captured parent registry
  record and no replaced callbacks. All278 real nodes/1494 declarations agree;
  the production validator accepts all278 without allocating variables or states.
- Two independently created original-client matches verify the actual0F44/node33
  common State object, three on/off cycles, counter reset, unchanged typed v940,
  phase ACKs and retired native bindings. This is an isolated owner-frame fixture,
  not complete controller execution. Client closed normally; owned server stopped.
- Five former placed9D7 stops now complete frame0:50/123 nonempty components pass
  in the unchanged isolated catalog, with73 strict stops. Actual000E/0F44 now
  retains world query E72F6348/frame0; failed controller messages still fail closed.
  Shared rootD38/node106, global phases, full body/actor lifetimes, cinematics,
  five enemy AI types and mission/results remain open. No normal deployment.
- Add12 behavior tests;875 related and33 server checks pass, with Ruff/diff.
  Passive VM storage covers35 State models. Normal inputs and unrelated working
  changes are preserved. See SHARED_VARIABLE_STATE.txt. Publication helper missing;
  required synchronization cannot run and no GitHub push is performed.

#### Preserved entity script component definitions and controller hosting

- Retain complete entity/hero script components, including empty defaults,
  schemas, policy flags and graph dependencies through shared XML references.
  Distinguish unknown legacy/missing data, known absence and existing components.
  Reject ambiguous ownership, conflicting records and unsupported definitions.
- Add SceneHost.add_entity_scripts for already-created entities in its initial
  catalog, using existing strict startup/override/scheduling and owned cleanup.
  Unknown data cannot publish an empty substitute. Preserve sealing and reject
  overrides/values on known absence. Add22 behavior tests;863 related and33 server
  tests pass, with Ruff/diff checks. No normal input or default Match policy change.
- Actual000E plus explicit mode0025/team0 root0F44 retains9D7BF987/node33 during
  controller startup. Its bound failed receiver still rejects0EC6/node22 after
  parameter41; failure is not treated as absence. A literal16-graph scan does
  not establish all native/dynamic message consumers. Full startup remains open.
- Real Match prepares146 placements, mode100B and one actual controller with148
  queues, retainingD38/node106 before controller execution and retiring bindings.
  The external bundle changes only000E component metadata. Native mode mounting,
  body/controller/actor lifetimes, services/phases/replication, cinematics, enemy
  AI and complete mission results remain unaccepted. No new client run or
  deployment. Preserve unrelated working changes. See ENTITY_SCRIPT_COMPONENTS.txt.
  Required publication helper missing; no GitHub push.

#### Native unkeyed array insertion and typed clear actions

- Restore strict4C3509FB append/insert/unique behavior and1C2CC6D5 fresh typed
  empty-array assignment. Preserve int-zero gaps, float32 index conversion,
  ordered providers, self-reference termination and normal clear notifications.
  Permissive player/body components retain their previous output-only policy.
- Verify880 original insertion comparisons and64 clear comparisons with declared
  storage/iterator/notification adapters.2 additional cases retain the explicit
  native-limit diagnostic gate. Keyed rows and linked insertion/checkpoints
  remain strict. Add25 behavior tests;825 related and33 server tests pass,
  with Ruff/diff checks.
- Actual0F45's isolated voice39->array40/41->voice35->clear42 chain completes
  with typed array1839 empty, no fault, queued events or voice registrations.
  Authored flags/plugs are unchanged. Source42 is uninitialized and backend/
  clock responses remain explicit fixture inputs; full startup/audio is open.
- The separate helper still stops at controller-message component/node22 before
  voice polling. Shared Match retainsD38/node106 and retires147 queues. Complete
  actor/services/replication/phases, cinematics, enemy AI and mission results
  remain unaccepted. No new original-client run or deployment. Normal inputs
  and unrelated working changes are preserved. See ARRAY_ACTIONS.txt.
  Required publication helper missing; no GitHub push.

#### Shared voice stimulus lifecycle and separate polling

- Implement40E05F6B shared begin/poll/timer/failure callbacks behind explicit
  component voice contexts, global clocks and backend services. Preserve delay,
  target deduplication, failure timeout, retries, float32 duration conversion,
  part queries and custom output plugs. Ordinary ticking does not poll voice.
  Unknown consumers remain strict; permissive spawn-bark policy stays passive.
- Verify973 original-instruction cases and both constructors with declared
  adapters. Ten scalar caches enter passive VM storage across34 concrete State
  models. Contexts detach/retire on end, unbind and failure; stale component
  faults preserve replacement services. Add24 behavior tests;800 related and33
  server tests pass, with Ruff/diff checks.
- Actual0F45/node48 now begins voice node39 and executes0EC6 parameter array41
  before the controller-message component/node22 stop. A separate actual voice
  State fixture uses explicit backend responses, keeps authored plugs, finishes
  and retains4C3509FB/node40. Actualnode39 has both client/server-only flags false;
  remote index-1 does not classify it server-only. Full startup remains open.
- Shared Match still retainsD38/node106 and retires147 queues. Complete native
  poll phases, real audio/submission services, replication/readiness, cinematics,
  enemy AI and full mission results remain unaccepted. No new original-client
  run or deployment. Normal inputs and unrelated working changes are preserved.
  See VOICE_STIMULUS_STATE.txt. Publication helper missing; no GitHub push.

#### Component PlayScript queues and transient graph lifetimes

- Route authored requests to actual scene components through explicit resource
  providers and owned queues. Preserve skip/replace/reuse order, immediate and
  reverse flush, resource/startup waits, native1500 boundary and real queued
  schema/initialization/start bindings. Reentrancy, active self replacement and
  lost ready namespaces remain strict boundaries. Normal host policy is unchanged.
- Verify277 original submit/flush cases, including reverse batches, and2688
  automatic-lifetime cases with declared adapters. Request byte0 sets Instance318
  bit11's automatic retirement policy. Events, enabled entries, frame age and
  active States govern retirement; passive VM storage records its three fields.
- Actual0F45/node48 now creates0EC6 through its installed queue and queues array17
  for variable41. The caller's following path retains strict40E05F6B/node39 before
  helper update. Separate isolated service submission assigns array41 and stops
  at controller-message component/node22. No manual loaded-core handoff is used.
  Fixture body/controller/input/resource services do not establish full startup.
- Real in-memory Match configures and retires147 queues, retaining rootD38/node106.
  Failure, leave and stop cancel pending work. Fix stale source failure clearing
  replacement engine services; revalidate identity around request callbacks.
  Add25 behavior tests;770 related and33 server tests pass, with Ruff/diff.
- Full allocator/loader/physical deletion, native phases, actor/body/controller
  lifetimes, live replication/readiness, cinematics, enemy AI and mission results
  remain open. No new client run, deployment or full mission acceptance. Normal
  inputs and unrelated working changes are preserved. See PLAY_SCRIPT_QUEUE.txt.
  Publication helper missing; no GitHub push.

#### Native PlayScript source and entity-instance context

- Derive live SceneWorld entity-instance caller/owner from its actual component
  and inherited instigator from native builtin001B/+234 cache semantics. Verify
  the own entity-instance getter table; detached/stopped sources fail lifecycle
  checks before or after effective-value callbacks.
- New loaded-core allocations accept an explicit instigator, seed typed001B
  before defaults and retain native zero's owner fallback. Start-entry caller
  bindings stay separate. Skip/reuse retain the existing source. Passive VM
  storage includes the uint32 source cache without running its getter.
- Compare 294 original getter/setter cases, seven allocation-source branches
  and seven owner-identity cases with declared storage/owner adapters. Add 13
  behavior tests; 745 related and 33 server tests pass, with Ruff/diff.
- Actual0F45/node48 derives its request context through the bound component.
  An explicit0EC6 loaded-core handoff sets001B and array variable41, then retains
  strict message-target component/node22. Automatic submission still stops before
  success. Body/controller bindings, allocation source and input17 are fixtures;
  full body/controller startup, helper execution and transport are unaccepted.
- Real shared Match remains147 startup preparations and strict100B/D38/node106.
  Native allocator/loader/flags, dynamic actors, full services/replication,
  cinematics, enemy AI and mission results remain open. No new client run or
  deployment. Normal inputs and unrelated working changes are preserved.
  See PLAY_SOURCE_CONTEXT.txt. Publication helper missing; no GitHub push.

#### Initial Match-owned shared scene scripts

- Add an explicit GameServer/Match scene factory for the initial mode/placed
  component catalog. Bind all components before startup and share live foreign
  variable slots, effective links, dependency notifications and queued messages.
  Retirement removes both dependency endpoints. Failed live mode components
  cannot fall back to immutable initial values. Unknown strict message transport
  or receiver components stop execution before the following success plug.
- Match checks map, mode, clock and actual server creates after collision
  readiness. It owns ordinary frames, terminal failure and last-player cleanup;
  server stop also closes owned hosts. Duplicate frames are idempotent and gaps
  require a new lifecycle. Prepared entrance readiness stays blocked pending live
  scene replication. Shared-runtime stored export is explicitly unsupported.
- A real in-memory GameServer probe prepares 146 placed components plus mode
  root 100B in the explicit 371-create research catalog. It retains strict
  D38D0035/node106/frame0, ends the Match and retires all bindings. Later
  components do not run after root failure. Collision is an explicit fixture.
- Add 27 behavior tests; 732 related and 33 server tests pass, with Ruff/diff.
  Preserve a prior server result invalidated by a concurrent source edit;
  authoritative final runs use fixed source. Independent component counts
  remain 45 completed first frames and 78 strict stops, not shared acceptance.
- Initial clock/order is explicit owner policy. Native scene phases, dynamic
  actors, complete services/replication, cinematics, enemy AI, extraction and
  results remain open. No new client run, deployment or full mission acceptance.
  Normal inputs and unrelated working changes are preserved. See SCENE_SCRIPT_HOST.txt.
  Publication helper missing; no GitHub push.

#### Placed data-flow mapping and typed provider callbacks

- Implement native23A661BD ordinary begin/cleanup/dependency and its separate
  typed answer provider with explicit component47 registry/counter/position
  services. Preserve cache-before-register, remove-before-clear, target Handle
  tags, unchanged-GUID target refresh and missing-namespace status4 behavior.
- Own factory/vtables and2070 original State/provider comparisons pass, with
  declared engine adapters. Twelve original A82CD4B2 literal cases verify typed
  unkeyed DataFlow assets including zero; keyed availability remains explicit.
- Two actual0E61 components now pass State allocation and reach the later strict
  action0F647E55/node13 before mapping activation. Separate authored-node fixtures
  verify061D global mapping registration/removal, float answer1 and four38/37-bit
  codec checks. Full component first frames are not accepted by these fixtures.
- Add19 behavior tests;668 related and15 server checks pass, with Ruff/diff.
  Passive VM storage covers33 concrete State classes. Scene contexts are bound
  to existing entities and revoked on retirement/failure.
- The unchanged scene still completes45/123 nonempty first frames with78 strict
  stops:66 control contexts,3 alias components,1 model-look scope,3 action0F647E55
  and5 State9D7BF987. Full100B remainsD38/node106. No unknown-service shortcut.
  Complete data-flow services, production hosting/lifetimes, story/cinematics,
  enemy AI and results remain open. No new client run or deployment. Normal
  inputs and unrelated working changes are preserved. See DATA_FLOW_MAPPING.txt.
  Publication helper missing; no GitHub push.

#### Original-client model-look scope and restoration sample

- Verify actual144E/raw11 in two isolated Story/Normal/Reaper matches, sending
  all authored remote States with no source override. Eight phase frames are
  ACKed after create ACKs and two streams retire. Eight accepted native captures
  verify28 transmitted variable values/tags and three on/off cycles.
- Observe native component47's specialized VM/scope helper registering and
  dispatching the ModelLook interface. State caches/flags, component4's authored
  look override/restoration and the engine look stack agree. Fresh native entity,
  instance, State and target identities reset counters in the second match.
- Correct the inspector's identity association: nativeEntity+AC differs from
  networkEntity+B0. Preserve17 earlier captures whose model/scope fields had
  already settled; no activation frame is resent or pending timing inferred.
- Five successive codec checks and36 focused State/codec/statescript tests
  pass. Prior649 related+15 server checks cover unchanged production code.
  Client closes normally; owned isolated servers stop and release ports.
- Accept this exact native scope/model transition sample with declared server
  services and diagnostic off. Visible appearance, complete engine services,
  phase/clock mapping, production hosting/lifetimes, rootD38, story/cinematics,
  enemy AI and results remain open. Normal inputs and unrelated working changes
  are preserved; no deployment. See MODEL_LOOK_STATE.txt.
  Publication helper missing; no GitHub push.

#### Placed model-look scope and tick callbacks

- Implement native7D71FDF3 scope enter/exit and tick with explicit namespace,
  target policy, global entity lookup, capability and model consumers. Preserve
  cache commits before writes, failed apply, old-target gates, queued abort and
  exit bit6 suppression. Ordinary begin/cleanup/dependency hooks remain passive.
- Verify the class's own factory/vtables and compare4320 original callback cases,
  with explicit engine adapters. Twelve literal cases confirm typed unkeyed
  ModelLook resources. Nonzero-key availability and complete engine services
  remain open. The explicit ForwardHost accepts the verified null+18 interface.
- Actual144E/node26 completes authored frame0 and requests look09800000000080D0
  apply/removal using declared host/model services, without a source override.
  Two187/186-bit on/diagnostic-off codec checks preserve all remote States/values.
  The unchanged strict scene still completes45/123 nonempty first frames with78
  stops; this sample now requires scope registration instead of unknown State code.
- Add19 behavior tests;649 related and15 server checks pass, with Ruff/diff.
  Passive VM storage includes both caches and covers32 concrete State classes.
  Context lifetimes are bound to existing entities and revoked on retirement.
- Root100B remains strictD38D0035/node106. Normal inputs and unrelated working
  changes are preserved. No new original-client acceptance or deployment.
  Rendered model changes, production hosting/lifetimes, full story/cinematics,
  five enemy AI types and results remain open. See MODEL_LOOK_STATE.txt.
  Publication helper missing; no GitHub push.

#### Original-client animation-alias request and selection samples

- Verify all three actual 0D32 alias States in two consecutive original-build
  Story/Normal/Reaper matches. Initial frames omit source001B; native tag10/self
  is observed before each connection's activation. Other active States remain
  outside this sample. Server services and diagnostic aborts are explicit.
- All 24 phase frames are ACKed after create ACKs; six streams retire on leave.
  Eight accepted captures cover 24 native subject samples and 348 transmitted
  variable values/tags. They verify lazy State allocation, handle retention,
  request rows/counters and current-alias selection/restoration. Fresh native
  entities and instances restart counters in the second match.
- Preserve two additional early off captures with cleanup complete but current
  selection still pending; later reads settle. Transport ACK does not establish
  engine completion. No engine phase mapping or latency bound is inferred.
- Fifteen successive codec checks and 34 focused alias/codec/statescript tests
  pass. The preceding 630 related and 15 server checks cover unchanged production
  code; documentation diff checks pass. Client closes normally, owned servers
  stop, and normal runtime inputs and unrelated working changes are preserved.
- Exact sample request/selection is accepted. Visual animation playback, native
  true-mode timing, complete allocator/services, production hosting/lifetimes,
  rootD38, story/cinematics, enemy AI and results remain open. No deployment.
  See ANIMATION_ALIAS_STATE.txt. Publication helper missing; no GitHub push.

#### Native placed animation-alias State

- Implement strictB1FA9C2A begin/cleanup with explicit component55 storage,
  allocation and notification services. Preserve sampled/wrapping handles,
  failed allocation, reverse removal and last/current notification gates.
  True mode notifies then queues ABORT; successful removal retains the handle.
- Verify the class's own factory/vtable and execute966 native State/registry
  comparisons, including original request instructions with explicit event/clock
  adapters. Sixteen native literal comparisons verify typed AnimAlias resources,
  including zero. Keyed availability and complete engine playback remain open.
- Three actual0D32 components complete frame0 with declared animation service
  fixtures; six authored-on/diagnostic-off codec round trips preserve all remote
  States and configured variables. No engine selection/playback is simulated.
  The unchanged strict scene still completes45/123 first frames, with78 stops;
  three now require actual animation components instead of unknown State code.
- Add17 behavior checks;630 related and15 server snapshot/research-injection
  tests pass, with Ruff/diff checks. Read-only VM storage adds the State handle
  and covers31 concrete State classes. Permissive body policy remains passive.
- Normal inputs and unrelated working changes are preserved. No new original-
  client acceptance or deployment for this State. Production host/lifetimes,
  rootD38, full story/cinematics, five enemy AI types and results remain open.
  See ANIMATION_ALIAS_STATE.txt. Publication helper missing; no GitHub push.

#### Original-client active entity-control sample

- Two authorized isolated Story/Normal/Reaper matches verify actual120E raw
  State2 with explicit observed source binding and declared server services.
  Eight phase frames are ACKed after create ACKs; both streams retire on leave.
  Diagnostic abort frames cover three on/off cycles, including a fresh match.
- Read-only native inspection confirms State identity/caches and repeatable
  State/model flag changes with restoration. Runtime component1/model250 exist
  despite no explicit serialized model component. Correct the first inspector's
  active-mask annotation from preserved raw bytes/native bit0 instructions.
- Five codec round trips, eight native comparisons and the transport/lifetime
  aggregate pass. All39 focused State/codec/transport tests pass with external
  runtime configured; the initial invocation omitted that setting and failed.
  The earlier613+15 checks remain applicable to unchanged production code;
  documentation diff checks pass. Client closes normally; owned servers stop.
- Normal runtime and unrelated working changes are preserved; no deployment.
  Exact sample/native model transition is accepted. Complete engine services,
  allocator/host/lifetimes, rootD38, story/cinematics/enemy AI/results remain open.
  See ENTITY_CONTROL_STATE.txt. Publication helper missing; no GitHub push.

#### Native placed entity-control State rules

- Implement strict33A46033 cache/type-list lifecycle, ordered rule aggregation
  and guarded engine dispatch. Retain capability/query inputs across callbacks.
  Require explicit engine services; unknown context/consumers still fault.
  Bind contexts to actual entities and revoke dependent bindings on retirement.
- Original instructions match4514 rule,448 cache and512 dispatch cases with
  declared pointer/config/spatial/engine adapters; eight further native cases
  inspect recursive model-bit updates. See ENTITY_CONTROL_STATE.txt for scope.
- Actual120E/node9 (raw State2, remote1) completes frame0 with declared service
  fixtures, requests entity/model changes and passes a73-bit active-state codec
  round trip. Engine effects and new original-client activation remain unaccepted.
- Add22 behavior checks, including sampled-input regressions and retirement;
  pass613 related and15 server checks, Ruff/diff. VM storage includes seven
  cached fields; wire formats and permissive body policy remain unchanged.
- All146 selected components configure; the strict actual-scene first-frame
  probe still completes45/123 nonempty components. Sixty-six stop on missing entity-control
  context;12 retain other State/action faults. No absent-context shortcut,
  deployment or full mission/cinematic/enemy-AI/results acceptance. RootD38
  stays strict. Publication helper missing; no GitHub push.

#### Typed literal configs for placed script startup

- Evaluate unkeyed VoiceStimulus, Animation, Hero, entityDef and SkinTheme as
  typed resource references; preserve signed32 TeamIndex. Nonzero resource keys
  still delegate availability to context and fault in the strict base world.
- Original factory/vtable links and evaluator/setter instructions match3096
  scalar cases without executed service stubs. Availability services and owning
  destination disposal remain outside this scope. See CONFIG_EVALUATION.txt.
- All146 selected components now configure in the unchanged isolated371-create
  catalog:23 empty,123 nonempty,124 graphs and542 queued writes. Independent
  first frames complete45/123 nonempty components;78 retain strict State/action
  stops. Full root100B still stops at D38D0035/action106.
- Add six behavior checks;591 related and15 server snapshot/research-injection
  checks pass, with Ruff/diff checks. Normal inputs unchanged; no new client
  acceptance or deployment for this code change. The preceding two-sample client
  acceptance covers frozen graph baselines only. Mission hosting, cinematics,
  enemy AI/results remain open. Publication helper missing; no GitHub push.

#### Two actual placed graph baseline samples

- An authorized isolated original-client session completes two consecutive
  Story/Normal Rialto entries as Reaper and settled-menu returns. Actual0F65 and
  120E descriptors and frozen schema values are queued after entity create ACKs;
  both graph streams are acknowledged and retired on leave in each connection.
- Read-only native inspection in both matches confirms the two exact graph/ID1
  instances and all eight authored values/type tags. Native builtin sources also
  match owners, without deriving the server allocator. Two offline codec round
  trips, both native comparisons and the transport/lifetime aggregate pass.
- Client closes normally; isolated server stops and releases ports. Normal
  runtime inputs unchanged; candidate not deployed. Production code remains
  a8234f4 with its585+15 regression results. Documentation diff checks pass.
- Scope is two frozen baselines with remote States off and no authored server
  execution. Match hosting, live State replication, full entity lifetimes,
  rootD38, complete story/cinematics/enemy AI/results remain open. Publication
  helper missing; no GitHub push.

#### Explicit placed component graph startup

- Add an owner-selected starter for fresh components and loaded graphs. Queue
  public/definition values, first-match instance overrides, then additional
  graphs. Prepare all pending graphs at the first update clock before due events.
  Keep manual init=False inert and discard pending work on destroy/failure.
- Match108 original schema/evaluation cases and36 original initialization cases
  with declared allocator/resource/role/event-storage adapters. Full native
  allocation, source bindings, async resources and Match policy remain external.
- Strict actual-scene probing with an explicit isolated371-create catalog:
  137/146 components configure;114 nonempty components create114 graphs and432
  queued writes. Nine unknown configs stop startup. One frame completes43/114
  configured nonempty components;71 stop on four unknown State classes.
- Add19 behavior checks and a read-only pending-snapshot check; pass585 related
  and15 server snapshot/research-injection checks, Ruff and diff checks. VM
  storage includes initialization_pending; wire formats unchanged. Normal runtime
  inputs unchanged. No new client/replication acceptance or deployment; rootD38,
  full story/cinematics/enemy AI/results remain open. Publication helper missing.

#### Rialto placed script creation and initialization boundary

- Identify that the required-loading create list omits every nonempty placed
  script component: 123 components / 124 definition graphs are unbound. The
  twelve bound script components have empty initial graph lists.
- Ninety original executable cases verify definition graph creation, first-match
  instance overrides, additional graphs and the once-only guard, with explicit
  engine/allocation/evaluation adapters. Empty overrides preserve defaults.
- An authorized isolated client loaded a 134-extra-create candidate, entered
  Rialto as Reaper, returned to a settled menu and closed normally. Two sampled
  entities have the expected native component47/owner, but zero graph instances.
  No placed graph descriptors/frames were sent; script execution is unaccepted.
- Document the missing creation/graph-host boundary and its remaining work.
  Normal runtime inputs unchanged; candidate not promoted. Documentation diff
  checks pass; production code remains the 565+15-tested 428fd13 version.
  Full mission, cinematics, AI and rootD38 remain open. Publication helper missing.

#### Ordinary auxiliary scopes before deferred notifications

- Add an explicit ForwardHost frame runner that processes all VM due times,
  drains auxiliary history once, then notifies ForCode channel1 once. Edges
  created by that notification wait for the next ordinary auxiliary phase.
  Preserve the manual/legacy APIs and explicit native clock ownership.
- Original D0D350/1B8C910 place +48/+58 inside ordinary +50, before channel1.
  Thirty native executable cases verify bounded ordering/clock writes; six
  two-frame comparisons execute native history writes from the deferred boundary
  and match the production runner's delayed begin/end scope callbacks.
- Eleven behavior checks cover actual ForCode notifications, authored timers,
  queued/direct edges, lifetime, both failure phases and a shared work budget.
  Actual0F45->0EC6 starts through the runner and reaches frame500 with supplied
  services/clocks, effect creation/retirement and three States still active.
- Passed565 related checks,15 server snapshot/research-injection checks, Ruff
  and diff checks. No new persistent VM fields or wire schema. Full100B remains
  D38D0035/node106; full clock/phase mapping, Match hosting, engine consumers,
  complete story/cinematics/enemy AI remain unaccepted. No deployment/new client;
  required publication helper remains missing.

#### Normal-forward auxiliary scope history

- Add an explicitly selected per-Component ForwardHost. Record native +100/+118
  history before active-bit changes, then dispatch pending parity at the owner's
  explicit frame/phase boundary. Preserve manual Registry and knownNone policies;
  unknown frame, clock rewind and undecoded self-unregister remain strict gates.
- Preserve first-edge snapshots, stored frame/payload, cold registration,
  live pending cleanup/swap removal and callback mutations. Bound repeated scans
  to the VM work budget; terminal teardown/failure discards local history without
  more authored callbacks. No State storage fields or wire schema are added.
- Original writer/wrapper/drain/unregister instructions match the production host
  in 386 cases, including 26 callback-mutation cases, with explicit adapters.
  Correct the earlier +220 inference: A0A2E0 queries; A087E0 marks history dirty.
- Actual 0F45 -> 0EC6 reaches frame500 using the production host, with no injected
  interface callbacks or VM transition wrappers. Its descriptor is created at
  frame0 and retired at frame46; states10/15/16 remain active at frame500.
  Clock/service inputs and the post-update drain boundary are still supplied.
- Passed 554 related checks, 15 server snapshot/research-injection checks,
  Ruff and diff checks. Full100B remains D38D0035/node106. Match hosting, native
  phase placement, rollback, engine consumers, cinematics and enemy AI remain
  unaccepted. No deployment/new client; required publication helper is missing.

#### Auxiliary effect state callbacks and routes

- Register 582E0D5F with explicit scope enter, refresh, matching observer and
  non-orientation exit callbacks. Native owner/context, component-43 targets,
  instance flags and global fallback lists are declared inputs. Unknown services
  remain strict; begin/end/tick do not invent host dispatch or reconstruction.
- Bind descriptors before initial evaluation and discard their local effects on
  fault, object or entity retirement. Preserve unaffected sources in shared lists
  and reject stale instance callbacks. Passive VM storage covers 29 State models.
  Live SceneWorld/effect storage and native orientation cleanup remain unsupported.
- Original routing/callback instructions match 416 cases with 156 allocations
  and matching retirements, using explicit lookup/allocator/constructor adapters.
- Actual 0F45 -> 0EC6 now passes entry and reaches frame 500 with supplied services
  and clock mapping, draining its 750/1500/2250ms timers. No interface dispatch is
  injected into that path; three states remain active. A separate actual node4
  test explicitly enters, refreshes and retires its concrete effect State.
- Passed 533 related runtime/expression/VM/body/bot/entrance/replication/replay
  checks, 15 server snapshot/research-injection checks, Ruff and diff checks.
  Full 100B remains D38D0035/node106. Native dispatch/history, engine consumers,
  complete story/cinematics/enemy AI remain unaccepted. No deployment/new client.
  Required publication helper remains missing.

#### Auxiliary effect list ownership and initialization

- Add explicit effect descriptors and native serial-sorted list membership.
  Retirement clears a live bit while retaining the entry and observer; it
  does not imply deletion, callback dispatch or consumer completion. Preserve
  uint32 wrap and unsigned ordering; unresolved serial ties fail before mutation.
- Add initial authored writes before parameter refresh. F8/FC keep the first
  scalar evaluation while F4 can take a separately evaluated second value.
- Original allocation/reference/sort/retire instructions agree on192 allocation
  and76 retirement operations; initialization/refresh/casts agree on192 cases
  and7488 field comparisons. Allocator/constructor/engine inputs are adapters.
- Establish specialized component262C010 from its constructor, with non-null
  scope registration/removal slots. Seventeen original cold register/removal
  cases confirm append/swap removal. Pending-history dispatch remains open.
- Actual0EC6/node4 builds an isolated descriptor with supplied route inputs,
  then retains it after retirement. No authored VM State or engine object is
  constructed. Helper remains582E0D5F/node4; full100B remainsD38D0035/node106.
- Passed513 related runtime/expression/VM/body/bot/entrance/replication/replay
  checks,15 server snapshot/research-injection checks, Ruff and diff checks.
  Mission/cinematics/enemy AI remain unaccepted. No deployment/new client.
  Required publication helper is still missing.

#### Auxiliary effect parameter updates

- Add 582E0D5F's isolated existing-effect parameter core: native f32 defaults,
  scalar/asset/vector casts, angle conversion, MAXSS edges and ordered entity
  list replacement. Pass the calling State through config iterator reads so
  dynamic dependencies retain watcher and shared VM work-budget behavior.
- Original x64 parameter instructions/casts match 192 cases and 6,336 field
  comparisons with explicit engine/config/iterator adapters. Another 144 native
  callback-gate cases establish pointer/presence/active-bit conditions, using
  stubbed creation/release/refresh bodies. Neither proves host trigger timing.
- Actual 0EC6/node4 parameters independently resolve to 0/0.75/5/0. Creation,
  target routing, consumers, native +88 phase and reconciliation remain open;
  the state remains unsupported. Helper entry still stops at 582E0D5F/node4,
  full 100B at D38D0035/node106. No mission/cinematic/enemy AI acceptance.
- Passed 503 related runtime/expression/VM/body/bot/entrance/replication/replay
  checks, 15 server snapshot/research-injection checks, Ruff and diff checks.
  No deployment or new original-client session. Publication helper is missing.

#### Auxiliary subgraph interfaces

- Model BD02E168's enter/exit interface separately from ordinary state hooks.
  Register after insertion into the instance and release on object retirement,
  including never-begun states. Explicit calls share VM budgets and cancellation.
- SceneWorld requires an explicit per-entity registration service or known no
  service. Missing declarations stay strict errors. Ordinary begin/end do not
  invent interface dispatch. Retirement, ID reuse and failure clear stale calls.
- Isolated original x64 callbacks agree on 256 argument-preparation cases with
  explicit node/follow/exit library stubs. Native authoritative trigger timing
  is not derived. Client/library component registration slots are nullsubs;
  this is not silently made the server-host policy.
- Actual 0F45 -> 0EC6 with declared context now begins states0/2 and produces
  five numeric rows, then stops at 582E0D5F/node4. Three registered interfaces
  remain inactive; no interface dispatch was injected into the helper probe.
- Passed 489 runtime/expression/VM/body/bot/entrance/replication/replay checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks.
  Full 100B remains at D38D0035. Complete helper/host behavior, cinematics and
  enemy AI remain unaccepted. No deployment or new original-client session.
  The required publication helper remains missing.

#### Numeric property modifier records

- Implement the numeric component-31 subarray: native f32 priority ordering,
  in-place updates versus reinsertion, exclusive uint32 frame windows and six
  arithmetic operations. Preserve signed-zero and SSE unordered min/max rules.
- Support 316CFEF2's numeric rows on an explicitly declared SceneWorld backend
  and source clock. Scalar values retain native defaults on failure and watch
  their variables. Per-frame records use distinct identities and timed expiry.
  Typed/filter rows and undeclared components remain strict failures.
- Retire rows at cleanup, remove declarations on entity retirement and discard
  numeric effects when strict execution poisons the owning component. Store the
  state's per-frame flag; SceneWorld storage remains unsupported.
- Isolated original x64 emulation agrees on 3,030 arithmetic and 4,900 insertion
  cases. Direct activation of actual 0EC6 state2 resolves its five rows and
  retires them at the supplied frame. Source clocks are explicit, not derived.
- Actual 0F45 -> 0EC6 entry execution next stops at BD02E168/node33 before
  queued states0/2 begin. Full 100B stays at D38D0035. No full helper, mission,
  cinematics or enemy AI acceptance; no candidate deployment or new client run.
- Passed 476 runtime/expression/VM/body/bot/entrance/replication/replay checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks. Property
  names/bases, native clocks, collection, rollback, gameplay consumers and mission
  hosting remain open. The required publication helper is still missing.

#### Registered state lifecycle and successor links

- Implement 9028E035's authoritative per-type component registry, in begin
  order across graph instances. Clean up after class end hooks and before
  authored end plugs; preserve order on removal and clear failed VM membership.
- Native +1C8 names the successor. Skip server-only successors and retain
  explicit links in owner delta and remote full/delta frames using the linked
  graph's raw state/remote indexes and widths. Reject unknown link metadata.
- Read-only VM storage now includes the registry and concrete state type,
  without invoking payload callbacks. SceneWorld storage stays unsupported.
- Actual 0F45 -> 0EC6 retains parameter (17,), constructs state 0 and queues
  its begin. Next is 316CFEF2/node2, before that begin dispatches. Lifecycle and
  codec tests do not establish helper completion or native gameplay acceptance.
- Preserve standalone World.native queries without an Instance; real strict
  instances still reject unknown services. This fixes two existing mode tests
  found by expanding the regression group.
- Passed 462 runtime/expression/VM/body/bot/entrance/replication/replay checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks.
  Full 100B still stops at D38D0035. Prediction/link reconciliation, mission
  host/routing, cinematics and enemy AI remain open. No candidate deployment or
  new original-client session. Publication helper remains missing.

#### Primary possessor entity queries

- Implement DB742151 using an explicitly known SceneWorld possession catalog:
  direct component 44 first, then reverse primary-possession lookup. Return a
  typed invalid Entity for absent results. Keep unknown catalog availability
  distinct and clean both ends of relations before an entity ID is reused.
- Evaluate the input through native scalar +20, including first-config failure.
  Preserve strict failures for unknown services. Correct the swapped entity/AI
  predicate comments in the legacy driver's trusted-query list.
- An authorized original client entered Rialto Story Normal as Reaper, switched
  to Genji, left and closed normally. Read-only component/registry observations
  confirm the controller and old/new body relationships. This researches native
  semantics on the accepted entrance service, not candidate mission behavior.
- Actual 0F45 -> 0EC6 passes the query and stops next at state 9028E035. Actual
  121D node 5 -> 119F resolves its target controller. Context/catalog/handoff
  remain explicit. Full 100B startup still stops at D38D0035.
- Passed 246 related runtime/expression/VM/bot/entrance/replication checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks. Mission
  host, component observers/routing, cinematics and enemy AI remain unaccepted.
  No candidate deployment is claimed. Publication still requires the missing
  work/sync_orpheus_publication.py helper.

#### Native PlayScript request construction

- Collect valid typed target entities in native order, retaining duplicates.
  Skip context/parameter services when targets are empty. Keep caller, owner
  and inherited instigator explicit; distinguish scalar source failure from
  successful null/nonentity values and retain the native caller fallback.
- Collect every request parameter as an array, including one-item and empty
  arrays. Retain ordered duplicate keys, allocation policy and the wire byte.
  World backends must provide context and submit real requests before output
  follows. Scene query bindings alone do not grant routing or transport.
- An authored 0F45 node 48 probe supplies caller variable 10334 = 17, constructs
  parameter (17,), and explicitly hands it to loaded 0EC6. Its start entry writes
  variable 41 = (17,) before stopping at DB742151. This verifies the request/core
  boundary, not automatic context derivation, target routing or running AI.
- Passed 237 related runtime/expression/VM/bot/entrance/replication checks and
  15 server snapshot/research-injection checks, Ruff and Git diff checks. Full
  100B startup remains at D38D0035. Mission host, component lifecycle, context,
  resource readiness, transport, cinematics and enemy AI remain unaccepted.
  No candidate deployment or new original-client acceptance is claimed.
  Publication still requires the missing work/sync_orpheus_publication.py helper.

#### Map entity identity and UUID queries

- Add an isolated SceneWorld query service using native client_id identities
  for mode-selected client placements and explicit existing server-create IDs.
  Keep definition/instance data separate. Bind and retire UUIDs explicitly;
  missing positions and unavailable foreign component variables stop strict
  callers. The service does not activate components or attach itself to Match.
- Preserve native EntityReference no-output failure without confusing it with
  successful null. Keep empty TargetTags typed as Asset(0). All 23 actual
  PlayScript resource configurations now resolve; queued public initialization
  succeeds for 121D (8 assignments), 1412 (8) and 119F (4).
- Native construction and an authorized original-client read-only probe agree
  on 10 waypoint identities. The client entered Story Normal as Reaper, left
  and closed normally. This verifies identities, not candidate mission behavior.
  The selected mode has 2,303 query bindings (2,066 client and 237 server).
- Passed 226 related runtime/expression/VM/bot/entrance/replication checks and
  15 server snapshot/research-injection checks (256.667 seconds), Ruff and Git
  diff checks. Full 100B startup still stops at D38D0035 node 106. Mission
  scheduling, component execution, cinematics and enemy AI remain unaccepted;
  no candidate deployment is claimed. Publication requires the missing
  work/sync_orpheus_publication.py helper.

#### Played resource selection and schema initialization

- Resolve primary/fallback PlayScript resources and ordered inline overrides
  with native low-key map replacement, bucket enumeration and growth. Keep
  empty ordinary graph resources typed. Distinguish collecting array values
  from the resource fallback's first-config evaluation, including failure.
- Queue callee public defaults, then caller-evaluated overrides before new
  instances' ordinary entries; retain reused-instance event order and separate
  start bindings. Failed defaults are skipped; failed inline overrides use int 0.
  Keep world routing, context, resource readiness and wire policy explicit.
- Passed 208 related runtime/expression/VM/alias/bot/entrance/replication checks,
  15 server snapshot/research-injection checks (253.208 seconds), Ruff and Git
  diff checks. 21 of 23 actual resource configurations resolve in isolation;
  two need EntityReference. Actual 121D node 5 -> 119F binds four supplied caller
  variables. World-dependent target schemas remain strict failures.
- Full 100B startup still stops at D38D0035. World instantiation, full story,
  cinematics and enemy AI remain unaccepted. No candidate deployment or new
  original-client acceptance claimed. Publication still needs the missing
  work/sync_orpheus_publication.py helper.

#### Loaded script invocation core

- Add the loaded-graph PlayScript core: skip/create/reuse/replace policies,
  ordered native parameter and source assignments, BB4AC733 start entries and
  one-shot entry suppression. Preserve pending assignments and disabled entries
  in read-only VM storage. Strict PlayScript actions now require an explicit
  world backend before following their output; legacy bodies remain passive.
- Passed 162 related runtime/expression/bot/entrance/replication/VM checks,
  15 server snapshot/research-injection checks, Ruff and Git diff checks. The
  authored 0F45 node 48 -> 0EC6 start-entry mapping receives a supplied value in
  variable 41 before stopping at unsupported STU_DB742151.
- World target/resource resolution, source-context derivation, schema overrides,
  asynchronous requests and native deferred deletion remain open. Full 100B
  initialization still stops at STU_D38D0035. No mission deployment or new
  original-client acceptance is claimed; cinematics, enemy AI and full story
  playthrough remain unaccepted. Publication requires the missing helper.

### 2026-11-07

#### Complete map component research inputs

- Add an external map-world exporter that correlates ordered ENTITY/XML inputs,
  exact transforms, immutable STU V1 component type hashes and full definition
  component maps. Retain nested map keys, component-local references and source
  hashes. Keep defaults and placed overrides separate; reject inconsistent or
  incomplete sources and output paths within the repository.
- Rialto exports 2,464 entities, 2,750 matching component instances and 186 full
  definitions with 454 components. The earlier independent transform audit agrees
  with all placed fields. Correct the older AI extractor's DUTS format comment:
  its +4 field is an instance count, not a version number.
- Passed 42 extraction checks, Ruff and Git diff checks. The export is research
  data, not an executable world. Mission services, full startup, cinematics and
  enemy AI remain unaccepted; no deployment or new client acceptance is claimed.
  Publication still requires the missing work/sync_orpheus_publication.py helper.

#### Empty resource evaluation and assignment

- Preserve the observed empty STU_7BD2CA19 evaluator's no-output failure:
  SetVar assigns int 0, while a ForCode read retains its current destination.
  Skip assignment when the SetVar target or value pointer is absent and retain
  its output-plug behavior. Successful null values still assign null. Nonempty
  resource keys and unknown native configs remain unsupported in strict mode.
- Passed 149 related expression/runtime/bot/entrance/replication/snapshot tests,
  Ruff and Git diff checks. The isolated authored 100B node 322 writes integer
  zero; full-root initialization advances to unsupported STU_D38D0035 node 106.
- A separately authorized original client entered Story Normal as Reaper, exposed
  the cached native evaluator for inspection, returned to the difficulty menu and
  closed normally. This researches config behavior on the accepted entrance
  server: the candidate is not deployed, and full story, cinematics and enemy AI
  remain unaccepted. The required publication helper remains unavailable.

#### Native mission value bindings

- Implement the verified zero-sync-flag ForCodeSynced path for strict mission
  execution: first-match live reads, native swap removal, begin/reentry/end
  ordering and immediate/deferred observer notification with callback identity
  deduplication. Preserve binding registries and pending notifications in read-only
  VM snapshots. Legacy player/bot bodies keep their passive default behavior.
- Passed 126 focused runtime/bot/entrance/replication/snapshot tests, 30 server
  snapshot/runtime checks, Ruff and Git diff checks. An isolated probe of authored
  100B node 158/state 49 reads updates and removes its native binding correctly.
- Full initialization remains closed at the next unsupported resource config;
  nonzero ForCode sync flags, the mission host, cinematics and enemy AI remain
  open. No new original-client mission acceptance or deployment is claimed.
  Publication still requires the absent work/sync_orpheus_publication.py helper.

#### Retribution mission dependency and execution diagnostics

- Include placed map script roots, graph order and component overrides in
  optional external graph extraction. Resolve DragonML IDs per component and
  require generated map inputs outside the repository. A local Rialto candidate
  retains 145 script placements and adds their 16-graph closure without new missing
  graphs; it has not replaced the accepted entrance runtime.
- Add optional strict runtime diagnostics for unmodelled nodes/states, missing
  plug/SubScript targets and unsupported base-world queries. Stop queued work on
  failure and preserve the policy/diagnostic in read-only VM snapshots. Existing
  body prediction keeps its permissive default. The real 100B startup now exposes
  its unimplemented ForCodeSynced state at node 158 instead of treating it as a
  generic state. Registered handlers are not proof of complete mission semantics.
- Passed 158 focused extraction/runtime/expression/bot/snapshot/fault tests and Ruff.
  Full story progression, native code bindings, cinematics and enemy AI remain
  open. No new client acceptance or mission deployment is claimed. Required
  publication synchronization remains unavailable while its helper is absent.

#### PVE entrance acceptance and local deployment

- All 47 Archives/Junkenstein cards passed original build-104319 client menu,
  map load, hero selection, actual-world entry and settled-menu return. This
  covers 24 Archives difficulty cards, nine Archives challenges and 14
  Junkenstein difficulty/challenge cards. The final client completed 39 cards
  consecutively and closed normally without reproducing earlier cleanup crashes.
- Tutorial passed five consecutive fixed-Soldier entry/return cycles, followed
  by Practice Range and normal closure. Both entrances also passed after local
  deployment, alongside Retribution All Heroes Normal in the same client.
- Promoted the two accepted external map/graph inputs with hashes and backups,
  restarted the normal local service, and enabled Archives and Halloween for
  existing profiles and the new-account template. The usual local launcher
  logged in and completed all three deployment smoke checks; the service remains
  available after normal client closure. Game assets and detailed evidence stay local.
- The implementation's 141 focused tests, Ruff and 47 independent offline frame
  checks passed. This acceptance covers entrances only: mission AI, objectives,
  challenge effects, Tutorial guidance and story hero restrictions remain open.
  Long-term stability and the complete causes of earlier crashes are unproven.
  GitHub synchronization remains blocked by the absent required publication helper.

#### Tutorial entry and world delivery on leave

- Route the native Training > Tutorial request to its authored solo red team,
  Tutorial map and fixed Soldier: 76 controller. Wait for controller creation ACK
  before body creation and possession; keep the shared hero picker closed.
- Stop sending queued, in-flight or newly produced world records once a client
  leaves. Preserve reliable message ordinals, retransmissions and packet ACKs,
  including when the leave packet is lost. Clock draining uses the same cleanup.
- Passed 141 focused tests, Ruff and Git diff checks. All 47 PVE card/map loading
  and controller frames still passed independent offline decoding.
- Eight activity cards and Practice Range have original-client entry/return
  evidence. The current Tutorial candidate completed five consecutive world and
  menu cycles in one original-client process, followed by Practice Range and
  normal client closure. Earlier candidates had intermittent cleanup crashes;
  this observation does not establish their complete cause or wider stability.
- The remaining 39 activity cards and normal-service deployment remain open.
  Tutorial guidance, mission AI and objective execution are outside this entry
  acceptance. Publication remains unavailable because the required
  work/sync_orpheus_publication.py helper is absent; assets and evidence stay local.

#### PVE controller and loading states

- Preserve the mode/team script associations in external graph bundles. Replicate
  Archives and Junkenstein controller wrappers with their authored SubScript
  parents, and keep the shared hero-selection assembly countdown.
- Recognize build 104319's native scripted-loading readiness state on the real
  mode graph. Support map-scoped configured mission graphs while retaining the
  primary mode root; unavailable or ambiguous readiness data stays closed.
- Passed 69 focused tests, Ruff and Git diff checks. Separate local inputs now
  contain 619 graphs, the Storm Rising mission binding and authored, ground-checked
  entrance markers on four PVE maps. All 47 card/map choices and their loading
  frames passed offline selection and independent decoding.
- In the separately authorized original-client session, Retribution All Heroes
  Normal and Legendary and Storm Rising All Heroes Normal entered their actual
  starting rooms with Soldier: 76 and returned to the difficulty menu. Junkenstein
  Normal entered the world but crashed after leaving; its return remains open.
  Other cards, Tutorial, Practice Range regression checks and normal-service
  deployment remain pending. Mission AI and objective execution are not covered.
- Publication was not run because the required work/sync_orpheus_publication.py
  helper is absent. Generated assets, captures, logs and runtime inputs stay outside Git.

#### PVE entry preparation

- Allow simultaneous, validated Archives and Halloween event groups so their 47 PVE difficulty and challenge cards can be offered together. Keep event updates atomic and deduplicate repeated groups.
- Include mode, controller and body script roots in the optional 0C5 graph export; retain missing graphs explicitly. Bound plug ownership traversal through non-node reference cycles without dropping alias paths.
- Passed 67 targeted tests (including an external-runtime event fixture), Ruff and Git diff checks. Local research prepared 581 graphs and authored spawn candidates for three previously spawnless PVE maps. These are prerequisites: scripted loading, deployment and original-client entry across all 47 cards remain unverified. No client was started or controlled.
- Publication was not run: this checkout lacks the AGENTS.md-required work/sync_orpheus_publication.py helper. Research assets and candidate runtime inputs remain outside the repository.

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
