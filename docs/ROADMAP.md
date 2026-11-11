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

- [ ] Verify Roadhog (02E0000000000040) against players and all five manual PvE
  enemy types: primary/secondary/melee, hook, Breather, Whole Hog, incoming
  damage, defenses/status interactions and original-client presentation.
  The server integration checkpoint includes the current reconstructed
  combat paths and tests. The latest Steel Trap change passed focused
  regressions after the previous checkpoint's full Roadhog suite.
  Actual inputs now cover projectile, melee, Hook and Whole Hog contact limits.
  Both phases of Scrap Gun secondary fire pass through a teammate in the
  reconstructed team filter. Aimed misses spend one round with no damage
  or charge.
  Repeated Rocket Punch hits fill Doomfist's temporary shield to its
  150-point cap. A close Scrap Gun secondary hit then spends only that
  pool, with no damage charge.
  Seismic Slam uses its live damage chase at short aerial contact and
  caps at 125 on long falls. Breather cuts the capped hit to 62.5.
  Hand Cannon uses the teams at impact and preserves the firing body's
  kill attribution across a simultaneous team swap and body replacement.
  Ice Wall placement over an armed mine blocks its view without
  triggering it. Placement around a flying mine preserves its flight.
  A mine blast with covered and exposed targets gives the same results
  in both tested orders, including when the shared Winston barrier breaks.
  Prepared 104319 Practice Range and King's Row collision caches also
  pass all six attack forms, rifle damage followed by Breather, and
  map-cover rejection for an incoming slug and Hook.
  Deflect, Kinetic Grasp and Defense Matrix are checked at projectile
  contact. Actual casts after secondary launch or fragmentation verify
  this ordering. Matrix release can expose D.Va to the same fragments.
  Whole Hog resolves overlapping Experimental Barrier and Kinetic Grasp per
  pellet. The same channel can spend Grasp's granted shields and apply force
  after both defenses stop protecting the target.
  Whole Hog remains active through Ana antiheal, a projected-barrier
  cleanse, and Sleep Dart contact with the barrier. Starting Whole Hog
  cancels an antihealed Breather. Its cooldown survives the cleanse and
  a later held press starts Breather at expiry.
  Actual Orisa Halt presses now trigger the authored Roadhog pull graph.
  The reconstructed host rejects allies, range and static cover. Roadhog can
  use Hook, Take a Breather and Whole Hog while being pulled. Defense Matrix
  and Kinetic Grasp remove the orb and trigger cooldown. Deflect changes the
  orb's team. Four NPC types can be pulled. Heavy Assault triggers the
  authored victim refusal. To run the targeted tests,
  set `ORPHEUS_ORISA_HALT` to the Orisa lane's prepared `orisa_halt_174.json`.
  Steel Trap, Graviton and incoming Hook take precedence over Halt on the
  tested player paths. A live Halt resumes after release. The NPC path
  also yields to Graviton. An Ice Wall stops and detonates Halt before it
  reaches covered Roadhog. Encrypted tests verify the victim graph, caster
  and victim disconnects, retransmitted body removal and a new cast after
  rejoin. A missed detonation input can be retried with a fresh press.
  Halt flies through energy barriers. At detonation the pull checks enemy
  barrier cover without dealing damage. Raised Reinhardt shields and Winston
  domes verify the reconstructed visibility policy, as do Brigitte's shield
  and the deployed Orisa, Sigma and Symmetra barriers. Reflected Halt uses
  the deflector's team when checking cover.
  Real Scrap Gun kills cover pre-launch cancellation, post-death contact,
  victim cleanup and natural respawn. Reflected flight and pulls retire
  with the deflector's body under the reconstructed server policy.
  Roadhog's Halt receiver uses the reconstructed world clock. Its horizontal
  path and release are independent of victim command cadence, and returning
  input skips movement already simulated by the world at both encrypted
  command rates. Reflected ownership survives original caster disconnect
  and is cleaned up on deflector disconnect even with a dropped destroy
  packet. Projected barriers prevent acquisition and end active pulls.
  Whole Hog and Halt share a single displacement owner per tick in the
  tested Roadhog and Trooper cases. Whole Hog keeps dealing damage
  and can push again once Halt stops moving the target.
  Earthshatter locks Roadhog's actions while existing or new Whole Hog
  force still moves him. Halt's graph keeps its timers during knockdown.
  A live pull resumes on the recovery tick without victim commands.
  A pull that has already expired stays retired.
  The Assassin pin overrides Halt in both hit orders. A live pull resumes
  on the first world tick after an allied Roadhog melee rescue. Halt also
  yields to Heavy Assault Charge and takes over after a manual stop.
  A projected Zarya barrier prevents a new Heavy Assault pin. Applied
  during an established carry, it absorbs the wall impact while the
  reconstructed carry runs to the wall.
  An allied Sleep Dart or Flashbang on the charging NPC releases Roadhog
  before the wall impact. Fresh Breather works immediately after release.
  Reinhardt Charge takes movement priority in both contact orders.
  Halt resumes on the first world tick after a 300-damage wall impact
  if the pull is still live. Open Charge can outlast the pull.
  Other control and defense combinations remain open.
  Hook moves player targets on the combat clock and releases them at the
  same tick whether they send continuous, intermittent or no commands.
  Delayed packets suppress Hook-period movement and actions while retaining
  fresh input after release at both command rates, including a target's
  first packet. The initial body frame supplies the first clock anchor.
  Steel Trap and Graviton can overlap in either order. The reconstructed
  trap root takes movement priority until expiry. Hook, Take a Breather
  and Whole Hog remain usable, and the live field resumes its pull at release.
  A trap clears external motion on capture. Whole Hog and Soundwave still
  deal damage during the root. Whole Hog applies force again on the first
  damaging volley after the trap or Graviton releases the victim.
  Reinhardt and Heavy Assault Charge and the Assassin pin retain
  displacement control during Whole Hog damage. Assassin contact clears
  existing force, and Whole Hog applies force again after release.
  Baptiste field and Enforcer spread use documented server reconstructions.
  Whole Hog applies force on the hit that reaches the field's health floor.
  Further hits at that floor add no force. Breaking the device during the
  same channel restores lethal damage.
  Hook cancels a manual Enforcer's pending volley before ammo is spent.
  The existing reload timer completes even when the NPC is being pulled.
  Sleep, cover and manual stop also cancel the pending volley. A pellet
  released before Flashbang can still hit during the stun.
  A manual Trooper's three-round burst spends only the rounds already
  released when Hook, stop or a switch to melee interrupts it.
  A canceled last round defers reload until the magazine is actually
  empty. Hook or manual stop after the last shot preserves the refill.
  Sniper reposition suppresses pending rifle fire and preserves an
  already launched projectile. Fresh fire uses the same post-move delay
  in the windup, release-frame, and in-flight cases.
  The field restores health before Breather on the antiheal expiry tick.
  Only the remaining eligible healing funds Breather's charge. Two fields
  share a floor, and destroying one leaves the other protecting Roadhog.
  Roadhog's body graph reduces enemy damage charge to 75 percent of eligible
  pool loss. Damage and his healing deficit retain their resolved values.
  Quad Cannons and Wrecking Ball quick melee confirm the same victim
  multiplier while Roadhog is pulling each of the five NPC types.
  A primary volley that breaks Projected Barrier verifies this charge
  reduction on the remaining pellets after Breather mitigation.
  A real rifle hit through a nearly depleted Sound Barrier credits Breather
  only for the damage to base health. The reconstructed shield decays and
  absorbs damage without increasing the healing deficit.
  Wrecking Ball Minefield now deploys, arms and retires 2578/151A entities
  from the real ultimate. Roadhog can damage the mines, reduce blast damage
  with Breather and continue Whole Hog or a clear Hook pull after a hit.
  Actual Wrecking Ball Grappling Claw reaches a static anchor and triggers
  Fireball during a reconstructed swing. Roadhog can break the tether with
  Hook or a lethal shot. Allied Sleep Dart also ends the swing before contact.
  Breather mitigation and the five NPC cases pass. Encrypted flight and
  attachment disconnects retire the old body, reject stale commands and
  let the rejoined Ball land a fresh Fireball.
  Actual Quad Cannons uses its twenty-shot ramp, automatic reload and
  effective damage charge. Tests cover Roadhog's mitigation, Hook and
  incoming damage, Roll's critical eligibility and five NPC interactions.
  Mine contact, cover and force use the reconstructed server model.
  Encrypted tests cover dropped landing updates, mine owner disconnect,
  observer rejoin and fresh casts. Practice Range and King's Row caches
  verify static terrain contact and one hit to Roadhog. Static side
  walls deflect the mines' flight along the surface until they land.
  In the server model, deployed Ice Wall pillars also deflect side hits
  and support mines that land on top. Wall cancel, expiry and destruction
  drop the same mine to the floor and preserve its original arming timer.
  Real mine flight crosses deployed Winston, Orisa, Sigma and Symmetra
  surfaces and raised Reinhardt and Brigitte shields without changing
  their pools. The crossed mines still arm. After arming, direct contact
  with a hostile energy shield can trigger the authored blast independently
  of nearby bodies. The contact and visibility checks are reconstructed.
  Server tests cover standalone contact for the four deployed barriers,
  both raised shields and Zarya's projected bubble. An armed mine touching
  overlapping Orisa and Photon barriers spends each shield's pool once.
  A shared Winston
  dome takes one blast for two covered targets. Deflected Scrap Gun hits
  use the deflector's team when selecting mines. Wraith Form defers mine
  acquisition until protection expires. Actual airborne mines cross live
  Matrix, Deflect and Grasp boxes, retain ownership, and can still arm
  and hit Roadhog. The selected tag filter and flight policy are recorded
  as a server reconstruction. Enemy EMP suspends a deployed mine for
  10 seconds through its authored hack stack, including across landing.
  The same mine can trigger
  again after expiry. A deployed Ice Wall blocks EMP selection and mine
  acquisition across its surface. Canceling the wall reopens acquisition.
  Lethal interactions with all five manual NPCs are verified in both directions.
  Team checks cover all six outgoing attacks through each allied NPC and
  the reverse case of an active NPC order as team eligibility changes.
  Static cover blocks all six attacks against each NPC. It also pauses
  active orders from all five NPCs and stops in-flight Sniper and Enforcer
  projectiles.
  The actual five NPC capsules pass centimetre-level contact tests for
  primary pellets, secondary fragments, melee, Hook and Whole Hog.
  Manual NPC orders recheck the reconstructed engagement limit separately
  from the range of the attack they admit.
  Four manual NPC guns pass through Wraith Form and stop at Cryo-Freeze
  when the defender is between the NPC and Roadhog. The projectile cases
  cast after release and verify the reconstructed state at impact.
  Actual Deflect returns each of the four NPC guns to its source and
  credits Genji on lethal hits, with Roadhog protected behind him.
  A retained manual attack stays idle while Roadhog is dead and resumes on
  his replacement body when the original sight line is restored.
  The five NPCs' difficulty-indexed pool operands and base-pool damage
  are verified. An explicit sandbox difficulty exercises reconstructed
  additive pools, depletion, component 51 identity and teardown. Live
  mission difficulty binding and native pool lifetime remain open.
  Actual Sombra Hack and EMP can reach Roadhog's authored receiver timer.
  Reconstructed ability lock, range and LOS cases are verified.
  Hack and EMP preserve an already buffered melee until its normal recovery.
  Sleep Dart clears the same buffer and requires a fresh press after waking.
  EMP checks energy barrier surfaces and leaves Ice Wall intact. Actual
  casts protect Roadhog and his projected barrier behind the ice while
  an exposed Mei receives the lock.
  An allied EMP locks an eligible enemy player and leaves the five active
  manual NPC attack paths unchanged under the current receiver selector.
  The pulse also leaves Heavy Assault's charge and wall impact, and
  Sniper's reposition and resumed fire, unchanged in paired live trials.
  Native EMP receiver selection for NPCs remains open.
  Brigitte has actual NPC bidirectional barrier and Flashbang coverage.
  Actual Shield Bash stops Whole Hog without a refund and cancels flying
  Chain Hook or releases an established pull on a player or any of the
  five NPC bodies. The reconstructed interrupt preserves Hook cooldown.
  Two allied Roadhogs casting simultaneously at one enemy player or any of
  the five NPC bodies resolve to one damaging contact and one pull. Both
  cooldowns keep counting down, and reversed processing order changes the
  winner under the reconstructed rule.
  Live Sleep Dart and Shield Bash verify the sleep minimum, late wake
  replacement, and a fresh sleep during Bash stun. Hook and Breather
  presses during control expire without replay.
  Shield Bash uses a reconstructed forward ray to stop at hostile
  barriers. The projected Zarya test preserves Breather and bubble
  durability, then verifies independent cooldowns and fresh casts.
  Lowering Reinhardt's shield before Bash contact permits a hit. Lowering
  it on or after contact leaves the intercepted dash finished.
  Encrypted disconnect and same-account rejoin exercise live Hook release,
  Breather cancellation, and the end of Whole Hog damage. The server gives
  the returning account a fresh body and rejects commands from its old link.
  The observer recovers a lost body-removal packet for both active channels.
  Fresh Breather heals normally, earns charge only from the new body's
  eligible damage and ignores duplicate input. Whole Hog needs fresh charge.
  In-flight Roadhog scrap and buffered melee retire with the old body, while reflected
  scrap follows its current owner. This is the reconstructed server rejoin
  policy. Original-client rejoin remains open.
  Sleep and Flashbang now arbitrate a single deadline with a reconstructed
  minimum sleep period and damage wake recovery. The target-build hero
  loadout inventory and passive charge checks are recorded separately.
  Actual Symmetra preview, cancel, rotation and placement now reach a
  156C pet with a finite 6880 wall. Map-width selection is reconstructed.
  Brigitte's actual raised-state graph now controls the attached 5DA0 mesh.
  Finite pool, body placement and break recovery are reconstructed.
  New Zarya barriers clear existing Ana healing denial and block later
  Ana projectile effects. A projected barrier leaves existing sleep in place.
  A Sleep Dart that breaks a projected barrier leaves buffered melee
  intact. An unshielded dart clears it.
  Actual Ana hostile casts now interrupt Roadhog through a reconstructed
  sleep deadline or deny healing during a separate grenade deadline.
  Sleep Dart cancels flying Hook or releases its already-hooked target.
  Waking leaves the original Hook cooldown in force, and a fresh cast
  can hit again after it expires.
  Primary pellets, secondary burst pellets and Whole Hog now have a
  scoped reconstructed critical default. Actual inputs distinguish the
  unburst slug and active Fortify from eligible head contacts.
  Sigma's 1B55 pet and 1A2A graph now drive a moving and recallable C015 shield.
  Its server pose, world clearance and damage routing are reconstructed.
  The authored break condition disables the deployed mesh at one health.
  A real Whole Hog volley broke the near Sigma mesh, then the far Orisa
  mesh, before later pellets damaged and pushed the player behind them.
  With Projected Barrier on Sigma behind his deployed mesh, the first
  pellet broke the mesh and the next broke the bubble. Later pellets
  damaged Sigma and applied one impulse under the reconstructed rules.
  The Orisa path now uses a real 0BD7 throw and 0BD8 lifetime graph with the
  4062 collision mesh. The launch pose, world contacts and interception
  policy are reconstructed on the server.
  Whole Hog now has a selected resource-backed external motion model, with
  per-volley aggregation, finite velocity caps and swept player/five-NPC movers.
  Its semantics are reconstructed; docs/ROADHOG_KNOCKBACK.txt separates actual
  movement checks from native parity and the remaining full-scope audit.
  Selected Winston00FA/00FC creation, finite fixed sphere and original graph
  lifetime are hosted with explicit reconstructed policies; see
  ROADHOG_WINSTON_BARRIER.txt and external frozen-source verification. Remaining
  barrier/control/knockback/full-static and original-client gates stay open.
  Lane 0040 repairs shared direct-projectile falloff/critical application with
  pool-damage regressions and reconstructs the authored timed self-heal consumer.
  The lane incorporates native distinct-input joins and includes pending arrivals
  plus traversal origin in command-trace hashes as well as VM/server storage.
  Hero/NPC/regression evidence is rerun after this shared foundation update.
  A charged ultimate can kill a player in an offline real-input audit. Pellet
  count25 and a reconstructed primary ring pattern now have real damage,
  miss/team/wall tests; Breather cooldown rejection/expiry also has evidence.
  Real-input magazine checks now cover held primary/secondary/both, mixed-mode
  sharing of five rounds, cadence, empty refusal, automatic/manual reload and
  resumed fire. Hook/Breather cancel reload without delayed refill; fresh reload
  still succeeds. Duplicate/old commands and pre-respawn dead inputs cannot add
  hits/ammo use or finish reload. Native timing agreement and wider loss remain open.
  Whole Hog's16-pellet path now executes charge consumption, repeated damage,
  end, uncharged rejection and death cancellation with miss/team/wall regressions.
  Starting Whole Hog cancels manual reload, preserves the existing Hook
  cooldown and stops Hook before latch. An established pull finishes.
  Whole Hog refills a partial magazine on its own timeline, and presses
  for reload or Hook during the channel create no delayed action.
  Actual Flashbang before or on the refill frame preserves the partial
  magazine. Flashbang after the refill preserves the five rounds.
  Command recovery across the channel end completes the refill once
  without replaying damage.
  Roadhog now bridges actual damage/eligible Breather healing into shared0B8B
  charge expressions and uses its default passive interval/amount/multipliers.
  Full-cost combat gain, Q spend, active/dead suppression and post-end resumption
  have real-input checks. Host event eligibility/clock remain reconstruction;
  victim-specific passives and native charge exclusions remain open.
  See [charge evidence](ROADHOG_CHARGE.txt).
  Explicit external five-Talon inputs now cover weapon/melee/ultimate damage and
  retained-death bodies for all five. Manual Trooper/Heavy/Sniper input now causes
  reverse damage into Roadhog and respects stop; NPC weapon-manager startup restores
  Sniper's100-damage projectile. Heavy stops while hooked and resumes without a
  catch-up burst; Breather reduces its real input damage. Autonomous AI, remaining
  Enforcer full pellets/Assassin pin fidelity, specials and complete two-way interaction remain open;
  base pools are not complete authored difficulty totals.
  Shared declared manual Enforcer context now admits actual reverse2-damage
  shots; Breather halves a live hit and Hook control suppresses/release resumes
  fire. Five-NPC tests now require actual43-table mover inputs and recheck
  capsules/range/static walls; older generic-fallback geometry is historical.
  Full Enforcer pellets/controller lifecycle, Assassin pin fidelity and native AI remain
  open; see [Enforcer context](ENFORCER_MANUAL_CONTEXT.txt).
  Roadhog028F standing/crouched view heights now use original1.6/1.12;
  enemy McCree02A3 uses1.425/.9975. Shot origins, target metadata/history and actual
  narrow-cover/crouch inputs share that data. Native bones/camera/muzzle and other
  bodies' unlisted viewpoint fields remain open; generic defaults are not proof.
  Finite Zarya0244/025C spheres now receive Roadhog primary/secondary/fragments/Q/
  Hook/melee and projected ally/NPC selectors, with actual cooldown/last-hit/energy,
  Hook rescue, Breather/Flashbang, phase-delay pin release and read-only storage.
  Five actual NPC bodies and all five manual reverse admissions are covered in
  explicit scoped fixtures; native Pet/filter/credit/clock/cleanse/AI remain open.
  Actual Reinhardt mesh now intercepts primary/secondary/fragments/Whole Hog and
  Hook; melee mesh/body policy, walls/allies/lowering, dynamic raise/tether,
  Deflect/Grasp order, break/regeneration/re-raise/death/replacement and causal
  snapshots have scoped reconstruction checks. Five actual NPCs receive shield
  protection; four manual NPC guns damage an allied shield before Roadhog.
  Native pose/filter/priority, all other barriers and original-client gates remain open.
  Manual Assassin1157 now targets and pins Roadhog through the shared scoped
  bridge: actual contact/continuous damage, skill interruption, supporting
  Hook/primary rescue, death/stop/replacement/geometry cleanup and causal clock
  storage have direct checks. Damaging allied melee also releases a live pin.
  Manual reengagement uses the resource-timed release and checks eligibility.
  Full native controller, control priority and navigation remain open.
  See [pin](ASSASSIN_PIN_BRIDGE.txt).
  Secondary node29's embedded25-pellet expiry volley now causes real player and
  five-NPC damage, preserves one-ammo consumption, rejects prior wall/body impact
  emission and matches skipped-tick flight. Callback timing remains reconstructed.
  A real-launch hook consumer now executes30 damage, collision-backed pull and
  recovery against players/all five NPCs, input control,8s cooldown, LOS/range and
  death/replacement cleanup, plus real enemy Whole Hog/Breather interrupts.
  Native filters/defenses/control priority and arbitrary input-loss behavior stay
  open. Shared early-press retry timestamps now preserve their original expiry.
  Actual enemy Flashbang now interrupts Whole Hog without refund/restart and
  Breather healing/reduction with cooldown retained; expiry/fresh input recovers.
  Flashbang on the Hook owner cancels flight and active player/five-NPC pulls
  immediately. NPC pull cleanup also clears stale forced velocity. Other status
  kinds and native immunity/cleanse/competing control semantics remain open.
  Four scoped defensive casts now have actual Roadhog attack evidence: Wraith/
  Fade admit rear hits, Cryo/Transcendence intercept without damage/control;
  primary/slug/fragments/melee/Hook/Whole Hog, in-flight cast, expiry, Mei cancel,
  replacement, Flashbang immunity and passive snapshot checks execute. This is
  reconstructed filter/collision policy, not complete native defenses/cleanse.
  Starting any of the four casts during Whole Hog clears existing external
  motion. The shorter protections allow a fresh impulse from the same
  channel after expiry. Transcendence covers its remaining fire.
  A projected Zarya bubble also clears an existing Whole Hog impulse.
  The breaking pellet applies no damage or force to the recipient.
  Subsequent hits create fresh impulses after depletion or natural expiry.
  See [defense evidence](ROADHOG_DEFENSES.txt).
  The uncommitted worktree adds reconstructed Genji/Sigma projectile and hitscan
  interception, reflected slug fragments/credit/death, melee parry/Hook bypass
  and time-remainder tests, including reflected primary against all five NPCs.
  Reflected Whole Hog reverses its impulse and gives Genji the hit notices.
  A projected barrier on Roadhog absorbs the return before body movement.
  Real shots also verify nearest-contact ordering with a deployed Winston dome,
  Deflect, Kinetic Grasp and Defense Matrix. Whole Hog rays that miss Deflect
  can hit the dome in the same volley as reflected rays.
  Native box/filter/bounce policy, Sigma shield conversion and complete defense
  coverage stay open. Further commits wait for the entire static goal to pass.
  Configured actual0407/02CC Matrix now absorbs Roadhog projectile/ray003s
  using external tags and resource boxes. Selected-body script initialization
  and native-backed ruleset/body-owner helpers allow real graph activation.
  Hook interruption reuses the authored Matrix release cooldown and rejects
  early reopening. Attachment/filter polarity and mech lifecycle/pools
  remain open. Allied Matrix absorbs all four NPC guns; Assassin still pins.
  Enemy Matrix shields all five NPCs from Scrap Gun and Whole Hog, while
  Hook and quick melee still connect. See [Matrix](DVA_MATRIX.txt).
  The worktree also consumes Orisa Fortify's scoped9161 control flag: Hook/
  Flashbang preserve damage but cannot pull/stun/interrupt; actual Q, other
  attacks, in-flight onset, expiry and replacement are tested. Native control
  filters/cleanse/knockback remain open. Enforcer still lacks an established
  controller query; Assassin1460 requests controller message09AC and contains
  no gun volley/direct health state, so a fabricated gun is not a valid repair.
  Breather's live0.5 incoming damage multiplier now affects actual hits/death,
  restores at end/interrupt and applies to in-flight player and actual NPC shots.
  Fortify/Ironclad share the consumer. Combined caps/armor-order fidelity remain open.
  Native spread, full cooldown/status/defense/NPC matrix and complete hero acceptance
  stay open. Original-client acceptance is separate from the static goal.

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
